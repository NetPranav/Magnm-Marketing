#!/usr/bin/env python3
"""Build, sign and describe one Magnm Studio release, from the owner's laptop.

    python3 scripts/release.py 0.2.0 --dry-run     show every step and write sample manifests; builds nothing
    python3 scripts/release.py 0.2.0               build, sign, write latest.json and android.json, print the
                                                   exact `gh release create` command
    python3 scripts/release.py 0.2.0 --publish     the same, then run that command

The version must already be in VERSION (python3 scripts/set_version.py 0.2.0), committed and pushed to main, and the
"Desktop installers" workflow must have finished for that commit (it builds the macOS and Windows bundles).

The signing keys never live in the repository. Their places come from the environment:
  TAURI_SIGNING_PRIVATE_KEY_PATH, TAURI_SIGNING_PRIVATE_KEY_PASSWORD   the Tauri updater key
  MAGNM_ANDROID_KEYSTORE, MAGNM_ANDROID_KEYSTORE_PASSWORD, MAGNM_ANDROID_KEY_ALIAS   the Android release keystore
Nothing is published unless --publish is given.
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import set_version  # noqa: E402

REPO = "NetPranav/Magnm-Marketing"
DOWNLOAD_BASE = "https://github.com/%s/releases/download/v{version}/{name}" % REPO
WORKFLOW = "Desktop installers"

SIGNING_ENV = (
    "TAURI_SIGNING_PRIVATE_KEY_PATH",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
    "MAGNM_ANDROID_KEYSTORE",
    "MAGNM_ANDROID_KEYSTORE_PASSWORD",
    "MAGNM_ANDROID_KEY_ALIAS",
)
# A key made without a password has an empty one; it still has to be stated.
_MAY_BE_EMPTY = ("TAURI_SIGNING_PRIVATE_KEY_PASSWORD",)
_FILE_ENV = ("TAURI_SIGNING_PRIVATE_KEY_PATH", "MAGNM_ANDROID_KEYSTORE")

DRY_SIGNATURE = "DRY-RUN-NOT-A-SIGNATURE"


class ReleaseError(Exception):
    """A plain sentence about what is wrong. Never contains a secret."""


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---- checks ----

def check_version(given, file_version):
    """The version given on the command line must be X.Y.Z and equal the number in VERSION."""
    try:
        set_version.parse(given)
    except ValueError as err:
        raise ReleaseError(str(err))
    if given != file_version:
        raise ReleaseError(
            "VERSION says %s, not %s. Run python3 scripts/set_version.py %s, commit it to main and push first."
            % (file_version, given, given)
        )
    return given


def missing_env(env):
    """Names of the signing variables that are not set (or empty, except a key password)."""
    missing = []
    for name in SIGNING_ENV:
        if name in _MAY_BE_EMPTY:
            if name not in env:
                missing.append(name)
        elif not (env.get(name) or "").strip():
            missing.append(name)
    return missing


def check_key_files(env):
    """The two key files must exist. Only the variable name is named in the error."""
    for name in _FILE_ENV:
        if not os.path.isfile(env.get(name, "")):
            raise ReleaseError("The file that %s points to does not exist." % name)


def pubkey_is_set(root):
    """True when tauri.conf.json holds a real updater public key (not empty, not the placeholder)."""
    path = os.path.join(root, "apps", "desktop", "src-tauri", "tauri.conf.json")
    with open(path, encoding="utf-8") as handle:
        conf = json.load(handle)
    key = str(((conf.get("plugins") or {}).get("updater") or {}).get("pubkey") or "").strip()
    return bool(key) and key != "REPLACE_WITH_TAURI_UPDATER_PUBKEY"


def check_git(git):
    """The working tree must be clean and on main. `git` runs a read-only git command and returns its output."""
    if git(["status", "--porcelain"]).strip():
        raise ReleaseError("There are uncommitted changes. Commit or remove them first, then run the release again.")
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if branch != "main":
        raise ReleaseError("You are on %r. Releases are made from main." % branch)
    return git(["rev-parse", "HEAD"]).strip()


def _default_git(args):
    done = subprocess.run(["git", "-C", _root()] + list(args), capture_output=True, text=True)
    if done.returncode != 0:
        raise ReleaseError("git %s failed." % " ".join(args[:2]))
    return done.stdout


# ---- manifests ----

def asset_url(version, name):
    return DOWNLOAD_BASE.format(version=version, name=name)


def make_latest_json(version, notes, pub_date, platforms, arch_package):
    """latest.json in Tauri v2 format. `platforms` maps a platform key to (signature, file name);
    `arch_package` is (file name, signature) of the Arch package, which goes in `extra.pacman`."""
    entries = {}
    for key, (signature, name) in platforms.items():
        entries[key] = {"signature": signature, "url": asset_url(version, name)}
    arch_name, arch_signature = arch_package
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": entries,
        "extra": {"pacman": {"url": asset_url(version, arch_name), "signature": arch_signature}},
    }


def make_android_json(version, sha256, size, notes):
    """android.json for the phone: version, versionCode, url, sha256, bytes and notes."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise ReleaseError("The APK checksum is not a lower-case SHA-256.")
    if not isinstance(size, int) or size < 1:
        raise ReleaseError("The APK size is not valid.")
    return {
        "version": version,
        "versionCode": set_version.version_code(version),
        "url": asset_url(version, "magnm-studio-%s.apk" % version),
        "sha256": sha256,
        "bytes": size,
        "notes": notes,
    }


def release_command(version, files, notes_file, target=None):
    """The exact `gh release create` command. It is only printed unless --publish is given."""
    cmd = ["gh", "release", "create", "v" + version, "--repo", REPO, "--title", "Magnm Studio " + version,
           "--notes-file", notes_file]
    if target:
        cmd += ["--target", target]
    return cmd + list(files)


def format_command(cmd):
    return " ".join(shlex.quote(part) for part in cmd)


# ---- signing ----

def tauri_cli(root):
    """The Tauri CLI of apps/desktop (installed by pnpm), as a command prefix."""
    local = os.path.join(root, "apps", "desktop", "node_modules", ".bin", "tauri")
    if os.path.exists(local):
        return [local]
    return ["pnpm", "--dir", os.path.join(root, "apps", "desktop"), "exec", "tauri"]


def sign_file(cli, path, version, env):
    """Sign `path` with the updater key (named by TAURI_SIGNING_PRIVATE_KEY_PATH in `env`), bound to `version`.
    Writes and returns `path + '.sig'`. The password travels in the environment, never on the command line."""
    sig = path + ".sig"
    if os.path.exists(sig):
        os.remove(sig)
    done = subprocess.run(
        list(cli) + ["signer", "sign", "--app-version", version, path],
        env=env, capture_output=True, text=True,
    )
    if done.returncode != 0 or not os.path.isfile(sig):
        raise ReleaseError(
            "Signing %s failed. Check TAURI_SIGNING_PRIVATE_KEY_PATH and TAURI_SIGNING_PRIVATE_KEY_PASSWORD."
            % os.path.basename(path)
        )
    return sig


def read_signature(sig_path):
    """The text of a .sig file, as latest.json wants it."""
    with open(sig_path, encoding="utf-8") as handle:
        return handle.read().strip()


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- the steps ----

class Steps:
    """Runs (or, in a dry run, only prints) the commands of a release."""

    def __init__(self, dry_run, say):
        self.dry_run = dry_run
        self.say = say

    def title(self, number, text):
        self.say("")
        self.say("STEP %d: %s" % (number, text))

    def run(self, cmd, cwd=None, env=None):
        where = "  (in %s)" % cwd if cwd else ""
        self.say("  $ %s%s" % (format_command(cmd), where))
        if self.dry_run:
            return
        done = subprocess.run(cmd, cwd=cwd, env=env)
        if done.returncode != 0:
            raise ReleaseError("%s failed (exit %d)." % (os.path.basename(cmd[0]), done.returncode))


def _one(pattern, what):
    """The single file matching `pattern` (`**` also matches folders, as a downloaded artifact has sub-folders)."""
    found = sorted(glob.glob(pattern, recursive=True))
    if len(found) != 1:
        raise ReleaseError("Expected exactly one %s at %s, found %d." % (what, pattern, len(found)))
    return found[0]


def _bundle_dir(root, env):
    target = env.get("CARGO_TARGET_DIR") or os.path.join(root, "apps", "desktop", "src-tauri", "target")
    return os.path.join(target, "release", "bundle")


def _apksigner(env):
    found = shutil.which("apksigner")
    if found:
        return found
    sdk = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT") or ""
    candidates = sorted(glob.glob(os.path.join(sdk, "build-tools", "*", "apksigner")))
    return candidates[-1] if candidates else None


def run_release(version, out, env, steps, notes, sha, run_id, publish):
    """Steps 2 to 7. Returns the list of files to upload."""
    root = _root()
    dry = steps.dry_run
    cli = tauri_cli(root)
    desktop = os.path.join(root, "apps", "desktop")
    mobile = os.path.join(root, "apps", "mobile")
    work = tempfile.mkdtemp(prefix="magnm-release-work-")
    build_env = dict(env, NO_STRIP="true")

    # -- 2. Linux bundles and the Android APK, built here --
    steps.title(2, "Build the Linux AppImage, deb and rpm, and the Android release APK")
    steps.run([sys.executable, os.path.join(root, "scripts", "build_engine.py")], env=build_env)
    steps.run(["pnpm", "install", "--frozen-lockfile"], cwd=desktop, env=build_env)
    steps.run(["pnpm", "tauri", "build", "--bundles", "deb,rpm,appimage", "--config",
               '{"bundle":{"createUpdaterArtifacts":false}}'], cwd=desktop, env=build_env)
    steps.run(["pnpm", "install", "--frozen-lockfile"], cwd=mobile, env=build_env)
    steps.run(["pnpm", "build"], cwd=mobile, env=build_env)
    steps.run(["pnpm", "android:sync"], cwd=mobile, env=build_env)
    steps.run(["./gradlew", "--no-daemon", "assembleRelease"], cwd=os.path.join(mobile, "android"), env=build_env)

    names = {
        "appimage": "magnm-studio_%s_amd64.AppImage" % version,
        "deb": "magnm-studio_%s_amd64.deb" % version,
        "rpm": "magnm-studio-%s-1.x86_64.rpm" % version,
        "nsis": "magnm-studio_%s_x64-setup.exe" % version,
        "msi": "magnm-studio_%s_x64_en-US.msi" % version,
        "mac-arm": "magnm-studio_%s_aarch64.app.tar.gz" % version,
        "mac-intel": "magnm-studio_%s_x64.app.tar.gz" % version,
        "arch": "magnm-studio-%s-1-x86_64.pkg.tar.zst" % version,
        "apk": "magnm-studio-%s.apk" % version,
    }
    apk_name = names["apk"]
    sources = {}
    if not dry:
        bundle = _bundle_dir(root, env)
        sources["appimage"] = _one(os.path.join(bundle, "appimage", "*.AppImage"), "AppImage")
        sources["deb"] = _one(os.path.join(bundle, "deb", "*.deb"), "deb")
        sources["rpm"] = _one(os.path.join(bundle, "rpm", "*.rpm"), "rpm")
        sources["apk"] = _one(
            os.path.join(mobile, "android", "app", "build", "outputs", "apk", "release", "app-release.apk"), "release APK")
        signer = _apksigner(env)
        if signer:
            steps.run([signer, "verify", "--verbose", sources["apk"]])
        else:
            steps.say("  (apksigner was not found, so the APK signature was not double-checked)")

    # -- 3. The Arch package, from the deb --
    steps.title(3, "Build the Arch package from packaging/arch/PKGBUILD (makepkg, nothing is installed)")
    arch_dir = os.path.join(work, "arch")
    pkg_env = dict(env, PKGDEST=os.path.join(arch_dir, "out"), PKGEXT=".pkg.tar.zst")
    if not dry:
        os.makedirs(os.path.join(arch_dir, "out"))
        for name in ("PKGBUILD", "install-kind"):
            shutil.copy(os.path.join(root, "packaging", "arch", name), os.path.join(arch_dir, name))
        shutil.copy(sources["deb"], os.path.join(arch_dir, "magnm-studio_%s_amd64.deb" % version))
    steps.run(["makepkg", "--force", "--cleanbuild", "--nocheck"], cwd=arch_dir, env=pkg_env)
    if not dry:
        sources["arch"] = _one(os.path.join(arch_dir, "out", names["arch"]), "Arch package")

    # -- 4. macOS and Windows bundles, built by the "Desktop installers" workflow for this commit --
    steps.title(4, "Download the macOS and Windows bundles from the \"%s\" workflow run" % WORKFLOW)
    if not run_id:
        list_cmd = ["gh", "run", "list", "--repo", REPO, "--workflow", WORKFLOW, "--commit", sha,
                    "--status", "success", "--json", "databaseId", "--limit", "1"]
        steps.say("  $ %s" % format_command(list_cmd))
        if not dry:
            done = subprocess.run(list_cmd, capture_output=True, text=True)
            try:
                run_id = str(json.loads(done.stdout)[0]["databaseId"])
            except (ValueError, IndexError, KeyError, TypeError):
                raise ReleaseError(
                    "No finished \"%s\" run was found for commit %s. Push main and wait for it, or pass --run-id."
                    % (WORKFLOW, sha[:10]))
    shown_run = run_id or "<run id>"
    artifacts = {
        "magnm-studio-windows-x86_64": os.path.join(work, "windows"),
        "magnm-studio-macos-apple-silicon": os.path.join(work, "mac-arm"),
        "magnm-studio-macos-intel": os.path.join(work, "mac-intel"),
    }
    for artifact, where in artifacts.items():
        steps.run(["gh", "run", "download", shown_run, "--repo", REPO, "-n", artifact, "-D", where])
    if not dry:
        sources["nsis"] = _one(os.path.join(artifacts["magnm-studio-windows-x86_64"], "**", "*.exe"), "Windows installer")
        sources["msi"] = _one(os.path.join(artifacts["magnm-studio-windows-x86_64"], "**", "*.msi"), "Windows MSI")
        sources["mac-arm"] = _one(os.path.join(artifacts["magnm-studio-macos-apple-silicon"], "**", "*.app.tar.gz"), "macOS app archive")
        sources["mac-intel"] = _one(os.path.join(artifacts["magnm-studio-macos-intel"], "**", "*.app.tar.gz"), "macOS app archive")

    # -- 5. Sign every updater file with the updater key --
    steps.title(5, "Sign every updater file with `tauri signer sign` (the key is read from the environment)")
    signed = ["appimage", "deb", "rpm", "nsis", "msi", "mac-arm", "mac-intel", "arch"]
    signatures = {}
    files = []
    os.makedirs(out, exist_ok=True)
    for key in signed:
        steps.say("  $ %s signer sign --app-version %s %s" % (format_command(cli), version, names[key]))
        if dry:
            signatures[key] = DRY_SIGNATURE
            continue
        dest = os.path.join(out, names[key])
        shutil.copy(sources[key], dest)
        signatures[key] = read_signature(sign_file(cli, dest, version, env))
        files += [dest, dest + ".sig"]

    # -- 6. latest.json and android.json --
    steps.title(6, "Write latest.json and android.json")
    platforms = {
        "linux-x86_64": (signatures["appimage"], names["appimage"]),
        "linux-x86_64-appimage": (signatures["appimage"], names["appimage"]),
        "linux-x86_64-deb": (signatures["deb"], names["deb"]),
        "linux-x86_64-rpm": (signatures["rpm"], names["rpm"]),
        "windows-x86_64": (signatures["nsis"], names["nsis"]),
        "windows-x86_64-nsis": (signatures["nsis"], names["nsis"]),
        "windows-x86_64-msi": (signatures["msi"], names["msi"]),
        "darwin-aarch64": (signatures["mac-arm"], names["mac-arm"]),
        "darwin-x86_64": (signatures["mac-intel"], names["mac-intel"]),
    }
    pub_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    latest = make_latest_json(version, notes, pub_date, platforms, (names["arch"], signatures["arch"]))
    if dry:
        apk_sha, apk_size = "0" * 64, 1
    else:
        dest = os.path.join(out, apk_name)
        shutil.copy(sources["apk"], dest)
        apk_sha, apk_size = sha256_of(dest), os.path.getsize(dest)
        files.append(dest)
    android = make_android_json(version, apk_sha, apk_size, notes)
    for name, data in (("latest.json", latest), ("android.json", android)):
        path = os.path.join(out, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        steps.say("  wrote %s" % path)
        files.append(path)
    # The macOS disk images are plain downloads for people (not updater files, so no signature).
    if not dry:
        for artifact, suffix in (("magnm-studio-macos-apple-silicon", "aarch64"), ("magnm-studio-macos-intel", "x64")):
            found = glob.glob(os.path.join(artifacts[artifact], "**", "*.dmg"), recursive=True)
            if found:
                dest = os.path.join(out, "magnm-studio_%s_%s.dmg" % (version, suffix))
                shutil.copy(found[0], dest)
                files.append(dest)
    shutil.rmtree(work, ignore_errors=True)
    return files


def main(argv=None, env=None, git=None):
    parser = argparse.ArgumentParser(description="Build, sign and describe one Magnm Studio release.")
    parser.add_argument("version", help="the version to release, as in VERSION (for example 0.2.0)")
    parser.add_argument("--dry-run", action="store_true", help="show every step and write sample manifests; build nothing")
    parser.add_argument("--publish", action="store_true", help="also run `gh release create` (otherwise it is only printed)")
    parser.add_argument("--out", help="folder for the release files (default dist-release/vX.Y.Z)")
    parser.add_argument("--notes", help="a file with the release notes (default: a one-line note)")
    parser.add_argument("--run-id", help="the id of the finished \"Desktop installers\" run (default: found for this commit)")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    git = git or _default_git
    say = print
    root = _root()

    try:
        if args.dry_run and args.publish:
            raise ReleaseError("--publish cannot be combined with --dry-run.")
        steps = Steps(args.dry_run, say)
        steps.title(1, "Check the version, the signing variables and the working tree")
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as handle:
            file_version = handle.read().strip()
        version = check_version(args.version, file_version)
        drift = set_version.check(root)
        if drift:
            raise ReleaseError("Version files disagree with VERSION (%s). Run python3 scripts/set_version.py." % drift[0])
        missing = missing_env(env)
        if missing:
            raise ReleaseError("Set these environment variables first: %s." % ", ".join(missing))
        if not args.dry_run:
            check_key_files(env)
        if not pubkey_is_set(root):
            message = ("plugins.updater.pubkey in apps/desktop/src-tauri/tauri.conf.json is still the placeholder, so "
                       "installed apps would never accept this release. Put the public key in first (docs/RELEASING.md).")
            if not args.dry_run:
                raise ReleaseError(message)
            say("  WARNING: " + message)
        sha = check_git(git)
        say("  version %s, commit %s, signing variables present, working tree clean on main." % (version, sha[:10]))

        if args.dry_run:
            out = args.out or tempfile.mkdtemp(prefix="magnm-release-dry-")
        else:
            out = args.out or os.path.join(root, "dist-release", "v" + version)
        if os.path.isdir(out) and os.listdir(out):
            raise ReleaseError("The output folder %s is not empty. Choose another with --out." % out)
        notes = "Magnm Studio %s" % version
        if args.notes:
            with open(args.notes, encoding="utf-8") as handle:
                notes = handle.read().strip() or notes
        files = run_release(version, out, env, steps, notes, sha, args.run_id, args.publish)

        steps.title(7, "The release command")
        notes_path = os.path.join(out, "notes.md")
        with open(notes_path, "w", encoding="utf-8") as handle:
            handle.write(notes + "\n")
        command = release_command(version, files, notes_path, target=sha)
        say("  " + format_command(command))
        if args.publish:
            steps.run(command)
            say("Published v%s." % version)
        else:
            say("")
            say("Nothing was published. Check %s, then run the command above (or run this script again with --publish)." % out)
        return 0
    except ReleaseError as err:
        say("ERROR: %s" % err)
        return 1


if __name__ == "__main__":
    sys.exit(main())

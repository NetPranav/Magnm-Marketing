"""T11.9: the one-command release script (dry run, JSON shapes, environment and git checks, real signing)."""

import base64
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TAURI_CLI = os.path.join(REPO_ROOT, "apps", "desktop", "node_modules", ".bin", "tauri")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO_ROOT, "scripts", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = _load("release")
VERSION = open(os.path.join(REPO_ROOT, "VERSION"), encoding="utf-8").read().strip()

FAKE_ENV = {
    "TAURI_SIGNING_PRIVATE_KEY_PATH": "/nonexistent/updater.key",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": "not-a-real-password",
    "MAGNM_ANDROID_KEYSTORE": "/nonexistent/release.jks",
    "MAGNM_ANDROID_KEYSTORE_PASSWORD": "not-a-real-password",
    "MAGNM_ANDROID_KEY_ALIAS": "magnm",
}


def fake_git(dirty="", branch="main", sha="a" * 40):
    """A stand-in for read-only git: returns what the three questions the script asks would answer."""

    def run(args):
        if args[:2] == ["status", "--porcelain"]:
            return dirty
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return branch
        if args[:1] == ["rev-parse"]:
            return sha
        raise AssertionError("unexpected git call %r" % (args,))

    return run


class VersionAndEnvTests(unittest.TestCase):
    def test_version_must_be_plain_and_match_the_version_file(self):
        self.assertEqual(release.check_version("0.1.0", "0.1.0"), "0.1.0")
        for bad in ("v0.1.0", "0.1", "0.1.0-rc1", "", "0.1.0 "):
            with self.assertRaises(release.ReleaseError, msg=bad):
                release.check_version(bad, "0.1.0")
        with self.assertRaises(release.ReleaseError):
            release.check_version("0.2.0", "0.1.0")

    def test_names_every_missing_signing_variable(self):
        self.assertEqual(release.missing_env(FAKE_ENV), [])
        env = dict(FAKE_ENV)
        del env["MAGNM_ANDROID_KEYSTORE"]
        env["TAURI_SIGNING_PRIVATE_KEY_PATH"] = ""
        self.assertEqual(release.missing_env(env), ["TAURI_SIGNING_PRIVATE_KEY_PATH", "MAGNM_ANDROID_KEYSTORE"])
        self.assertEqual(len(release.missing_env({})), 5)

    def test_a_key_password_may_be_empty_but_must_be_set(self):
        env = dict(FAKE_ENV, TAURI_SIGNING_PRIVATE_KEY_PASSWORD="")
        self.assertEqual(release.missing_env(env), [])
        del env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"]
        self.assertEqual(release.missing_env(env), ["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"])

    def test_key_files_must_exist_unless_dry_run(self):
        with self.assertRaises(release.ReleaseError) as ctx:
            release.check_key_files(FAKE_ENV)
        self.assertNotIn("not-a-real-password", str(ctx.exception))

    def test_the_updater_public_key_must_not_be_the_placeholder(self):
        tmp = tempfile.mkdtemp(prefix="magnm-pubkey-")
        self.addCleanup(shutil.rmtree, tmp, True)
        conf_dir = os.path.join(tmp, "apps", "desktop", "src-tauri")
        os.makedirs(conf_dir)
        path = os.path.join(conf_dir, "tauri.conf.json")
        for value, expected in (("REPLACE_WITH_TAURI_UPDATER_PUBKEY", False), ("", False), ("  ", False), ("dW50cnVz", True)):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"plugins": {"updater": {"pubkey": value}}}, handle)
            self.assertEqual(release.pubkey_is_set(tmp), expected, value)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({}, handle)
        self.assertFalse(release.pubkey_is_set(tmp))

    def test_working_tree_must_be_clean_and_on_main(self):
        self.assertEqual(release.check_git(fake_git()), "a" * 40)
        with self.assertRaises(release.ReleaseError) as ctx:
            release.check_git(fake_git(dirty=" M apps/web/src/App.tsx\n"))
        self.assertIn("uncommitted", str(ctx.exception))
        with self.assertRaises(release.ReleaseError) as ctx:
            release.check_git(fake_git(dirty="?? stray.txt\n"))
        self.assertIn("uncommitted", str(ctx.exception))
        with self.assertRaises(release.ReleaseError) as ctx:
            release.check_git(fake_git(branch="feature"))
        self.assertIn("main", str(ctx.exception))


class ManifestShapeTests(unittest.TestCase):
    PLATFORMS = {
        "linux-x86_64": ("sig-appimage", "magnm-studio_0.2.0_amd64.AppImage"),
        "linux-x86_64-appimage": ("sig-appimage", "magnm-studio_0.2.0_amd64.AppImage"),
        "linux-x86_64-deb": ("sig-deb", "magnm-studio_0.2.0_amd64.deb"),
        "linux-x86_64-rpm": ("sig-rpm", "magnm-studio-0.2.0-1.x86_64.rpm"),
        "windows-x86_64": ("sig-nsis", "magnm-studio_0.2.0_x64-setup.exe"),
        "windows-x86_64-nsis": ("sig-nsis", "magnm-studio_0.2.0_x64-setup.exe"),
        "windows-x86_64-msi": ("sig-msi", "magnm-studio_0.2.0_x64_en-US.msi"),
        "darwin-aarch64": ("sig-mac-arm", "magnm-studio_0.2.0_aarch64.app.tar.gz"),
        "darwin-x86_64": ("sig-mac-intel", "magnm-studio_0.2.0_x64.app.tar.gz"),
    }

    def build(self):
        return release.make_latest_json(
            "0.2.0", "Faster Library.", "2026-11-24T10:00:00Z", self.PLATFORMS,
            ("magnm-studio-0.2.0-1-x86_64.pkg.tar.zst", "sig-arch"),
        )

    def test_latest_json_is_tauri_v2_with_every_platform(self):
        data = self.build()
        self.assertEqual(data["version"], "0.2.0")
        self.assertEqual(data["notes"], "Faster Library.")
        self.assertEqual(data["pub_date"], "2026-11-24T10:00:00Z")
        self.assertEqual(
            sorted(data["platforms"]),
            sorted(["linux-x86_64", "linux-x86_64-appimage", "linux-x86_64-deb", "linux-x86_64-rpm", "windows-x86_64",
                    "windows-x86_64-nsis", "windows-x86_64-msi", "darwin-aarch64", "darwin-x86_64"]),
        )
        for key, entry in data["platforms"].items():
            self.assertEqual(sorted(entry), ["signature", "url"], key)
            self.assertTrue(entry["url"].startswith("https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/"), key)
            self.assertNotIn(" ", entry["url"])
        self.assertEqual(data["platforms"]["linux-x86_64-deb"]["signature"], "sig-deb")

    def test_arch_package_is_named_in_extra(self):
        extra = self.build()["extra"]["pacman"]
        self.assertEqual(extra["signature"], "sig-arch")
        self.assertEqual(
            extra["url"],
            "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0-1-x86_64.pkg.tar.zst",
        )

    def test_latest_json_round_trips_as_json(self):
        data = self.build()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_android_json_has_the_agreed_shape(self):
        data = release.make_android_json("0.2.0", "ab" * 32, 12345, "Faster Library.")
        self.assertEqual(sorted(data), ["bytes", "notes", "sha256", "url", "version", "versionCode"])
        self.assertEqual(data["version"], "0.2.0")
        self.assertEqual(data["versionCode"], 200)
        self.assertEqual(data["bytes"], 12345)
        self.assertEqual(data["sha256"], "ab" * 32)
        self.assertEqual(
            data["url"], "https://github.com/NetPranav/Magnm-Marketing/releases/download/v0.2.0/magnm-studio-0.2.0.apk"
        )

    def test_android_json_agrees_with_the_phone_and_the_version_script(self):
        sv = _load("set_version")
        self.assertEqual(release.make_android_json("1.2.3", "0" * 64, 1, "")["versionCode"], sv.version_code("1.2.3"))

    def test_android_json_refuses_a_bad_checksum_or_size(self):
        for sha in ("", "AB" * 32, "zz" * 32, "ab" * 31):
            with self.assertRaises(release.ReleaseError, msg=sha):
                release.make_android_json("0.2.0", sha, 10, "")
        with self.assertRaises(release.ReleaseError):
            release.make_android_json("0.2.0", "ab" * 32, 0, "")

    def test_release_command_lists_every_file_and_is_not_run(self):
        files = ["/out/latest.json", "/out/magnm-studio-0.2.0.apk", "/out/with space.txt"]
        cmd = release.release_command("0.2.0", files, "/out/notes.md")
        self.assertEqual(cmd[:4], ["gh", "release", "create", "v0.2.0"])
        self.assertIn("--title", cmd)
        self.assertIn("--notes-file", cmd)
        for path in files:
            self.assertIn(path, cmd)
        text = release.format_command(cmd)
        self.assertIn("'/out/with space.txt'", text)


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="magnm-release-")
        self.addCleanup(shutil.rmtree, self.out, True)

    def run_main(self, argv, env=None, git=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch("subprocess.run", side_effect=AssertionError("a command was run")), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("a command was started")):
            code = release.main(argv, env=FAKE_ENV if env is None else env, git=git or fake_git())
        return code, buf.getvalue()

    def test_dry_run_runs_nothing_and_prints_every_step_in_order(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out])
        self.assertEqual(code, 0, out)
        steps = re.findall(r"^STEP (\d)\b", out, re.M)
        self.assertEqual(steps, ["1", "2", "3", "4", "5", "6", "7"])
        self.assertIn("gh run download", out)
        self.assertIn("gh release create v%s" % VERSION, out)
        self.assertIn("tauri build", out)
        self.assertIn("makepkg", out)
        self.assertIn("assembleRelease", out)
        self.assertIn("signer sign", out)

    def test_dry_run_never_prints_a_secret(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out])
        self.assertEqual(code, 0)
        self.assertNotIn("not-a-real-password", out)

    def test_dry_run_writes_both_manifests_in_the_right_shape(self):
        code, _ = self.run_main([VERSION, "--dry-run", "--out", self.out])
        self.assertEqual(code, 0)
        latest = json.load(open(os.path.join(self.out, "latest.json"), encoding="utf-8"))
        android = json.load(open(os.path.join(self.out, "android.json"), encoding="utf-8"))
        self.assertEqual(latest["version"], VERSION)
        self.assertIn("linux-x86_64-deb", latest["platforms"])
        self.assertIn("pacman", latest["extra"])
        self.assertEqual(android["version"], VERSION)
        self.assertEqual(sorted(android), ["bytes", "notes", "sha256", "url", "version", "versionCode"])

    def test_dry_run_does_not_publish_even_when_asked_to_print_the_command(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out])
        self.assertEqual(code, 0)
        self.assertIn("Nothing was published", out)

    def test_publish_cannot_be_combined_with_dry_run(self):
        code, out = self.run_main([VERSION, "--dry-run", "--publish", "--out", self.out])
        self.assertNotEqual(code, 0)
        self.assertIn("--publish", out)

    def test_refuses_a_dirty_tree_before_anything_else(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out], git=fake_git(dirty=" M x\n"))
        self.assertNotEqual(code, 0)
        self.assertIn("uncommitted", out)
        self.assertNotIn("STEP 2", out)

    def test_refuses_a_branch_other_than_main(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out], git=fake_git(branch="topic"))
        self.assertNotEqual(code, 0)
        self.assertIn("main", out)

    def test_refuses_a_version_that_is_not_in_the_version_file(self):
        code, out = self.run_main(["9.9.9", "--dry-run", "--out", self.out])
        self.assertNotEqual(code, 0)
        self.assertIn("VERSION", out)

    def test_refuses_missing_signing_variables_and_names_them(self):
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out], env={})
        self.assertNotEqual(code, 0)
        for name in ("TAURI_SIGNING_PRIVATE_KEY_PATH", "MAGNM_ANDROID_KEYSTORE", "MAGNM_ANDROID_KEY_ALIAS"):
            self.assertIn(name, out)

    def test_refuses_an_out_folder_with_old_files(self):
        open(os.path.join(self.out, "old.txt"), "w").close()
        code, out = self.run_main([VERSION, "--dry-run", "--out", self.out])
        self.assertNotEqual(code, 0)
        self.assertIn("not empty", out)


@unittest.skipUnless(os.path.exists(TAURI_CLI), "the Tauri CLI is not installed (pnpm install in apps/desktop)")
class RealSigningTests(unittest.TestCase):
    """Sign a file with a throwaway key made here and deleted afterwards, through the script's own sign function."""

    def test_sign_file_writes_a_version_bound_signature(self):
        tmp = tempfile.mkdtemp(prefix="magnm-sign-")
        self.addCleanup(shutil.rmtree, tmp, True)
        key = os.path.join(tmp, "throwaway.key")
        password = "throwaway-" + os.path.basename(tmp)
        subprocess.run([TAURI_CLI, "signer", "generate", "-w", key, "-p", password, "--ci"],
                       check=True, capture_output=True, timeout=60)
        payload = os.path.join(tmp, "payload.bin")
        with open(payload, "wb") as handle:
            handle.write(b"magnm release script test payload")
        env = dict(os.environ, TAURI_SIGNING_PRIVATE_KEY_PATH=key, TAURI_SIGNING_PRIVATE_KEY_PASSWORD=password)
        sig = release.sign_file([TAURI_CLI], payload, "9.9.9", env)
        self.assertEqual(sig, payload + ".sig")
        text = open(sig, encoding="utf-8").read().strip()
        decoded = base64.b64decode(text).decode("utf-8")
        self.assertTrue(decoded.startswith("untrusted comment:"))
        self.assertIn("version:9.9.9", decoded)
        self.assertEqual(release.read_signature(sig), text)

    def test_a_wrong_password_is_reported_without_printing_it(self):
        tmp = tempfile.mkdtemp(prefix="magnm-sign-")
        self.addCleanup(shutil.rmtree, tmp, True)
        key = os.path.join(tmp, "throwaway.key")
        subprocess.run([TAURI_CLI, "signer", "generate", "-w", key, "-p", "right-pass", "--ci"],
                       check=True, capture_output=True, timeout=60)
        payload = os.path.join(tmp, "payload.bin")
        open(payload, "wb").write(b"x")
        env = dict(os.environ, TAURI_SIGNING_PRIVATE_KEY_PATH=key, TAURI_SIGNING_PRIVATE_KEY_PASSWORD="wrong-pass-xyz")
        with self.assertRaises(release.ReleaseError) as ctx:
            release.sign_file([TAURI_CLI], payload, "9.9.9", env)
        self.assertNotIn("wrong-pass-xyz", str(ctx.exception))
        self.assertFalse(os.path.exists(payload + ".sig"))


class PackagingFilesTests(unittest.TestCase):
    def test_pkgbuild_is_in_step_with_the_version_and_installs_the_marker(self):
        text = open(os.path.join(REPO_ROOT, "packaging", "arch", "PKGBUILD"), encoding="utf-8").read()
        self.assertEqual(re.search(r"^pkgver=(\S+)$", text, re.M).group(1), VERSION)
        self.assertIn("webkit2gtk-4.1", text)
        self.assertIn("gtk3", text)
        self.assertIn("/usr/share/magnm-studio/install-kind", text)
        self.assertIn("!strip", text)
        marker = open(os.path.join(REPO_ROOT, "packaging", "arch", "install-kind"), encoding="utf-8").read()
        self.assertEqual(marker.strip(), "pacman")

    def test_installers_workflow_also_keeps_mac_app_archives(self):
        text = open(os.path.join(REPO_ROOT, ".github", "workflows", "desktop-installers.yml"), encoding="utf-8").read()
        self.assertIn("app.tar.gz", text)
        self.assertIn("AppImage", text)


if __name__ == "__main__":
    unittest.main()

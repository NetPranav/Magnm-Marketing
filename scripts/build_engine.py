#!/usr/bin/env python3
"""Cross-platform PyInstaller build script for the magnm-engine sidecar binary."""

import glob
import os
import shutil
import stat
import subprocess
import sys
import venv


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _venv_python(venv_dir):
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _rustc_host_triple():
    res = subprocess.run(
        ["rustc", "-vV"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in res.stdout.splitlines():
        if line.startswith("host:"):
            triple = line.split(":", 1)[1].strip()
            if triple:
                return triple
    raise RuntimeError(f"Could not parse 'host:' target triple from rustc -vV:\n{res.stdout}")


def _discover_hidden_imports(engine_dir):
    modules = []
    skip_dirs = {"tests", "__pycache__"}
    for root_dir, dirnames, filenames in os.walk(engine_dir):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip_dirs and not d.startswith(".")
        )
        rel_dir = os.path.relpath(root_dir, engine_dir)
        pkg_prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, ".")
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            stem = os.path.splitext(fname)[0]
            if stem == "__init__":
                if pkg_prefix:
                    modules.append(pkg_prefix)
                continue
            mod_name = f"{pkg_prefix}.{stem}" if pkg_prefix else stem
            modules.append(mod_name)
    modules.sort()
    for extra_mod in (
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "cryptography",
        "cryptography.x509",
        "cryptography.hazmat.primitives.ciphers.aead",
        "keyring",
        "keyring.backends",
        "keyring.backends.SecretService",
        "keyring.backends.libsecret",
        "keyring.backends.kwallet",
        "keyring.backends.macOS",
        "keyring.backends.Windows",
        "keyring.backends.chainer",
        "keyring.backends.fail",
        "keyring.backends.null",
    ):
        if extra_mod not in modules:
            modules.append(extra_mod)
    return modules


# Per-user data that must never ship inside the app: users add their own repos.
PER_USER_CONFIG = ("repos.json",)


def stage_config(root, stage_dir):
    """Copy config/ to stage_dir without per-user files, so a developer's own repos never get bundled."""
    src = os.path.join(root, "config")
    if os.path.isdir(stage_dir):
        shutil.rmtree(stage_dir)
    shutil.copytree(src, stage_dir, ignore=shutil.ignore_patterns(*PER_USER_CONFIG))
    return stage_dir


# The local voice (Kokoro). Their native libraries and data files must travel with the sidecar so the desktop app can speak.
VOICE_PACKAGES = ("kokoro_onnx", "onnxruntime", "espeakng_loader", "phonemizer", "language_tags", "segments", "csvw")
SFX_REL = ("video-skills", "magnm-video", "assets", "sfx", "impact", "impactSoft_heavy_003.ogg")


def present_packages(vpy, names):
    """The packages from `names` that the build interpreter can import. A failed probe means none."""
    code = (
        "import importlib.util, sys\n"
        "for n in sys.argv[1:]:\n"
        "    try:\n"
        "        found = importlib.util.find_spec(n) is not None\n"
        "    except Exception:\n"
        "        found = False\n"
        "    print(n) if found else None\n"
    )
    try:
        res = subprocess.run([vpy, "-c", code, *names], capture_output=True, text=True, check=False)
    except OSError:
        return []
    if res.returncode != 0:
        return []
    found = {line.strip() for line in res.stdout.splitlines()}
    return [n for n in names if n in found]


def video_data_specs(root):
    """(source, destination) pairs for the video renderer's transition sound (CC0, Kenney)."""
    sfx = os.path.join(root, *SFX_REL)
    return [(sfx, os.path.join(*SFX_REL[:-1]))]


def build_pyinstaller_command(vpy, engine_dir, dist_dir, work_dir, pyi_root, data_specs, collect_extra=None):
    """Build the PyInstaller CLI argument list including hidden imports and collected packages."""
    hidden_imports = _discover_hidden_imports(engine_dir)
    cmd = [
        vpy,
        "-m",
        "PyInstaller",
        "--onefile",
        "--name",
        "magnm-engine",
        "--noconfirm",
        "--clean",
        "--paths",
        engine_dir,
        "--distpath",
        dist_dir,
        "--workpath",
        work_dir,
        "--specpath",
        pyi_root,
    ]
    for mod in hidden_imports:
        cmd.extend(["--hidden-import", mod])
    for pkg in ("ddgs", "playwright", "keyring", "cryptography", *(collect_extra or ())):
        cmd.extend(["--collect-all", pkg])
    for src, dest in data_specs:
        cmd.extend(["--add-data", f"{src}{os.pathsep}{dest}"])
    cmd.append(os.path.join(engine_dir, "main.py"))
    return cmd


def _ensure_web_dist(root):
    """Build apps/web with pnpm so the bundled UI is never stale.

    Set MAGNM_SKIP_WEB_BUILD=1 to reuse an existing apps/web/dist (for example right after building it).
    """
    web_dist = os.path.join(root, "apps", "web", "dist")
    index_html = os.path.join(web_dist, "index.html")
    if os.environ.get("MAGNM_SKIP_WEB_BUILD") == "1":
        if not os.path.isfile(index_html):
            raise SystemExit("MAGNM_SKIP_WEB_BUILD=1 but apps/web/dist/index.html does not exist.")
        print("Reusing existing apps/web/dist (MAGNM_SKIP_WEB_BUILD=1).")
        return web_dist

    pnpm_bin = shutil.which("pnpm")
    if not pnpm_bin:
        raise SystemExit(
            "'pnpm' is not installed or not in PATH; it is needed to build the web UI. "
            "Install Node 22+ and pnpm, then run this script again."
        )

    web_dir = os.path.join(root, "apps", "web")
    print(f"Building web UI in {web_dir} ...")
    subprocess.run([pnpm_bin, "--dir", web_dir, "install", "--frozen-lockfile"], check=True)
    subprocess.run([pnpm_bin, "--dir", web_dir, "build"], check=True)
    if not os.path.isfile(index_html):
        raise FileNotFoundError(f"Expected built web UI at {index_html}")
    return web_dist


def main():
    if sys.version_info < (3, 10):
        raise SystemExit(f"Python 3.10+ required (got {sys.version})")

    root = _repo_root()
    engine_dir = os.path.join(root, "engine")
    venv_dir = os.path.join(root, ".venv-build")
    req_file = os.path.join(root, "scripts", "build_requirements.txt")
    engine_req_file = os.path.join(root, "engine", "requirements.txt")

    vpy = _venv_python(venv_dir)
    if not os.path.exists(vpy):
        print(f"Creating build virtualenv at {venv_dir} ...")
        venv.create(venv_dir, with_pip=True)

    print(f"Installing build and engine dependencies from {req_file} and {engine_req_file} ...")
    subprocess.run(
        [vpy, "-m", "pip", "install", "-r", req_file, "-r", engine_req_file],
        check=True,
    )

    web_dist = _ensure_web_dist(root)

    pyi_root = os.path.join(venv_dir, "pyinstaller")
    dist_dir = os.path.join(pyi_root, "dist")
    work_dir = os.path.join(pyi_root, "build")
    os.makedirs(pyi_root, exist_ok=True)

    data_specs = [
        (stage_config(root, os.path.join(pyi_root, "staged-config")), "config"),
        (os.path.join(root, "agents"), "agents"),
        (os.path.join(root, "playbooks"), "playbooks"),
        (os.path.join(root, "templates"), "templates"),
        (os.path.join(root, "research", "product-facts.md"), "research"),
        (os.path.join(root, "config.md"), "."),
        (os.path.join(root, "vendor", "magnm-pitch"), os.path.join("vendor", "magnm-pitch")),
        (os.path.join(root, "packages", "magnm-design", "fonts.css"), "pitch-fonts"),
        (os.path.join(root, "packages", "magnm-design", "fonts"), os.path.join("pitch-fonts", "fonts")),
        (web_dist, "web"),
        *video_data_specs(root),
    ]
    for src, _ in data_specs:
        if not os.path.exists(src):
            raise FileNotFoundError(f"Required bundle asset missing: {src}")

    voice_pkgs = present_packages(vpy, VOICE_PACKAGES)
    missing_pkgs = [n for n in VOICE_PACKAGES if n not in voice_pkgs]
    if missing_pkgs:
        print(f"Note: voice packages not installed in the build environment, not bundled: {', '.join(missing_pkgs)}")
    cmd = build_pyinstaller_command(vpy, engine_dir, dist_dir, work_dir, pyi_root, data_specs, collect_extra=voice_pkgs)

    print("Running PyInstaller ...")
    subprocess.run(cmd, cwd=root, check=True)

    triple = _rustc_host_triple()
    is_win = os.name == "nt" or "windows" in triple
    exe_suffix = ".exe" if is_win else ""
    built_bin = os.path.join(dist_dir, f"magnm-engine{exe_suffix}")
    if not os.path.isfile(built_bin):
        raise FileNotFoundError(f"Expected PyInstaller output at {built_bin}")

    out_dir = os.path.join(root, "apps", "desktop", "src-tauri", "binaries")
    os.makedirs(out_dir, exist_ok=True)
    target_bin = os.path.join(out_dir, f"magnm-engine-{triple}{exe_suffix}")
    shutil.copy2(built_bin, target_bin)
    if not is_win:
        mode = os.stat(target_bin).st_mode
        os.chmod(target_bin, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    size_bytes = os.path.getsize(target_bin)
    size_mb = size_bytes / (1024 * 1024)
    print(f"Built sidecar binary: {target_bin} ({size_bytes} bytes, {size_mb:.2f} MiB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Cross-platform development environment setup for Magnm Studio."""

import os
import subprocess
import sys
import venv


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _venv_python(venv_dir):
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def main():
    if sys.version_info < (3, 10):
        raise SystemExit(f"Python 3.10+ required (got {sys.version})")

    root = _repo_root()
    venv_dir = os.path.join(root, ".venv")
    req_file = os.path.join(root, "engine", "requirements.txt")
    vpy = _venv_python(venv_dir)

    if not os.path.exists(vpy):
        print(f"Creating virtual environment at {venv_dir} ...")
        venv.create(venv_dir, with_pip=True)
    else:
        print(f"Using existing virtual environment at {venv_dir}")

    print(f"Installing dependencies from {req_file} ...")
    subprocess.run([vpy, "-m", "pip", "install", "-r", req_file], check=True)

    print("Installing Playwright Chromium browser ...")
    subprocess.run([vpy, "-m", "playwright", "install", "chromium"], check=True)

    print("\nDev setup complete:")
    print(f"  - Virtualenv: {venv_dir}")
    print(f"  - Installed:  {req_file}")
    print("  - Browser:    playwright chromium installed")
    print(f"  - Start UI:   {os.path.relpath(vpy, root)} engine/server.py")


if __name__ == "__main__":
    main()

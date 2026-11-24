#!/usr/bin/env python3
"""Write the number in VERSION into every file that carries the app version, or check that they agree.

Usage:
  python3 scripts/set_version.py 0.2.0     write 0.2.0 everywhere (including VERSION)
  python3 scripts/set_version.py           write the number already in VERSION everywhere
  python3 scripts/set_version.py --check   list files that disagree with VERSION (exit 1 if any)

The Android versionCode is major * 10000 + minor * 100 + patch, so minor and patch must each be 0 to 99.
"""

import json
import os
import re
import sys

TARGET_FILES = (
    "apps/desktop/src-tauri/tauri.conf.json",
    "apps/desktop/src-tauri/Cargo.toml",
    "apps/desktop/src-tauri/Cargo.lock",
    "apps/desktop/package.json",
    "apps/web/package.json",
    "apps/mobile/package.json",
    "packages/magnm-design/package.json",
    "apps/mobile/android/app/build.gradle",
    "packaging/arch/PKGBUILD",
)

_SEMVER = re.compile(r"^(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]?)\.(0|[1-9][0-9]?)$")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(version):
    """Return (major, minor, patch) for X.Y.Z or raise ValueError. Minor and patch are 0 to 99."""
    match = _SEMVER.match(version or "")
    if not match:
        raise ValueError("Version must look like 1.2.3 (minor and patch 0 to 99), got %r." % (version,))
    return tuple(int(part) for part in match.groups())


def version_code(version):
    """The Android versionCode: major * 10000 + minor * 100 + patch."""
    major, minor, patch = parse(version)
    return major * 10000 + minor * 100 + patch


# Each rule: (regex with three groups: before, old value, after; replacement for the value).
def _rules(version):
    code = str(version_code(version))
    json_rule = (re.compile(r'^(  "version": ")([^"]*)(")', re.M), version)
    return {
        "apps/desktop/src-tauri/tauri.conf.json": [json_rule],
        "apps/desktop/package.json": [json_rule],
        "apps/web/package.json": [json_rule],
        "apps/mobile/package.json": [json_rule],
        "packages/magnm-design/package.json": [json_rule],
        "apps/desktop/src-tauri/Cargo.toml": [(re.compile(r'^(version = ")([^"]*)(")', re.M), version)],
        "apps/desktop/src-tauri/Cargo.lock": [
            (re.compile(r'(name = "magnm-studio"\nversion = ")([^"]*)(")'), version),
        ],
        "packaging/arch/PKGBUILD": [(re.compile(r"^(pkgver=)(\S+)()$", re.M), version)],
        "apps/mobile/android/app/build.gradle": [
            (re.compile(r"^(\s*versionCode )(\d+)()$", re.M), code),
            (re.compile(r'^(\s*versionName ")([^"]*)(")$', re.M), version),
        ],
    }


def _read(root, rel):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8") as handle:
        return handle.read()


def check(root=None):
    """Return a list of plain sentences naming each file that does not carry the number in VERSION."""
    root = root or _repo_root()
    version = _read(root, "VERSION").strip()
    parse(version)
    problems = []
    for rel, rules in _rules(version).items():
        text = _read(root, rel)
        for regex, wanted in rules:
            match = regex.search(text)
            if not match:
                problems.append("%s: no version found." % rel)
            elif match.group(2) != wanted:
                problems.append("%s: has %s, VERSION says %s." % (rel, match.group(2), wanted))
        if rel.endswith(".json"):
            json.loads(text)
    return problems


def set_version(root, version):
    """Write version into VERSION and every target file. Nothing is changed if the version is not valid."""
    parse(version)
    new_texts = {}
    for rel, rules in _rules(version).items():
        text = _read(root, rel)
        for regex, wanted in rules:
            text, count = regex.subn(lambda m, w=wanted: m.group(1) + w + m.group(3), text, count=1)
            if count != 1:
                raise ValueError("%s: could not find the version to replace." % rel)
        if rel.endswith(".json"):
            json.loads(text)
        new_texts[rel] = text
    for rel, text in new_texts.items():
        with open(os.path.join(root, *rel.split("/")), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
    with open(os.path.join(root, "VERSION"), "w", encoding="utf-8") as handle:
        handle.write(version + "\n")


def main(argv):
    root = _repo_root()
    if argv[1:] == ["--check"]:
        problems = check(root)
        for line in problems:
            print(line)
        print("All version files agree." if not problems else "%d file(s) disagree." % len(problems))
        return 1 if problems else 0
    if len(argv) > 2:
        print(__doc__)
        return 2
    version = argv[1] if len(argv) == 2 else _read(root, "VERSION").strip()
    try:
        set_version(root, version)
    except ValueError as err:
        print(err)
        return 2
    print("Version set to %s (Android versionCode %d)." % (version, version_code(version)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

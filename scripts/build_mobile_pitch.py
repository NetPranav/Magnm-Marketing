#!/usr/bin/env python3
"""Build apps/mobile/public/pitch/ from pinned vendor files via engine.pitch_host."""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import pitch_host


def main():
    output_dir = os.path.join(REPO_ROOT, "apps", "mobile", "public", "pitch")
    boot_js = os.path.join(REPO_ROOT, "apps", "mobile", "pitch-boot.js")
    res = pitch_host.build_mobile_bundle(output_dir, boot_js_path=boot_js)
    print(f"Built mobile Pitch bundle ({len(res['files'])} files) in {res['output_dir']}")


if __name__ == "__main__":
    main()

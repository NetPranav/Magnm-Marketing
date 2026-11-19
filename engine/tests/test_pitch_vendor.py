"""Tests for vendored Magnm-Pitch files, hashes, and catalog integrity."""

import hashlib
import json
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENDOR_DIR = os.path.join(REPO_ROOT, "vendor", "magnm-pitch")

EXPECTED_GROUPS = [
    "Web & SaaS",
    "Mobile app",
    "Backend",
    "AI & Automation",
    "DevOps & Maintenance",
]


class PitchVendorTests(unittest.TestCase):
    """Verify vendored Pitch snapshot hashes and extracted catalog structure."""

    def test_01_version_manifest_files_exist_and_match_sha256(self):
        version_path = os.path.join(VENDOR_DIR, "PITCH_VERSION.json")
        self.assertTrue(os.path.isfile(version_path), "PITCH_VERSION.json missing")
        with open(version_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        files = manifest.get("files")
        self.assertIsInstance(files, dict)
        self.assertGreaterEqual(len(files), 8)

        for rel_path, expected_sha in files.items():
            full_path = os.path.join(VENDOR_DIR, *rel_path.split("/"))
            self.assertTrue(os.path.isfile(full_path), f"Missing vendored file: {rel_path}")
            with open(full_path, "rb") as fh:
                actual_sha = hashlib.sha256(fh.read()).hexdigest()
            self.assertEqual(
                actual_sha,
                expected_sha,
                f"SHA-256 mismatch for vendored file {rel_path}",
            )

    def test_02_catalog_groups_items_and_dependencies(self):
        catalog_path = os.path.join(VENDOR_DIR, "catalog.json")
        self.assertTrue(os.path.isfile(catalog_path), "catalog.json missing")
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        self.assertEqual(catalog.get("groups"), EXPECTED_GROUPS)
        self.assertIsInstance(catalog.get("standalone_fee"), int)
        self.assertGreaterEqual(catalog.get("standalone_fee"), 0)
        self.assertIsInstance(catalog.get("standalone_label"), str)
        self.assertTrue(catalog["standalone_label"].strip())

        items = catalog.get("items")
        self.assertIsInstance(items, dict)
        self.assertGreater(len(items), 0)

        for item_id, item in items.items():
            for field in ("g", "n", "d", "p", "r"):
                self.assertIn(field, item, f"Item {item_id} missing field {field}")
            self.assertIn(item["g"], EXPECTED_GROUPS, f"Item {item_id} has invalid group {item['g']}")
            self.assertIsInstance(item["n"], str)
            self.assertIsInstance(item["d"], str)
            self.assertIsInstance(item["p"], int)
            self.assertGreaterEqual(item["p"], 0)
            self.assertIsInstance(item["r"], list)
            for dep_id in item["r"]:
                self.assertIn(dep_id, items, f"Item {item_id} dependency {dep_id} not in items")

    def test_03_catalog_presets_exist_and_reference_valid_items(self):
        catalog_path = os.path.join(VENDOR_DIR, "catalog.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        items = catalog["items"]
        presets = catalog.get("presets")
        self.assertIsInstance(presets, dict)
        self.assertIn("general", presets)

        for slug, preset in presets.items():
            self.assertEqual(preset.get("slug"), slug)
            for item_id in preset.get("initialSelected") or []:
                self.assertIn(
                    item_id,
                    items,
                    f"Preset {slug} initialSelected item {item_id} missing from items",
                )
            sections = preset.get("sections") or {}
            for sec_key, sec_ids in sections.items():
                self.assertIsInstance(sec_ids, list)
                for item_id in sec_ids:
                    self.assertIn(
                        item_id,
                        items,
                        f"Preset {slug} section {sec_key} item {item_id} missing from items",
                    )


if __name__ == "__main__":
    unittest.main()

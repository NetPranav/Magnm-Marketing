"""T11.9: one VERSION file, and every place that carries the version agrees with it."""

import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO_ROOT, "scripts", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class VersionAgreementTests(unittest.TestCase):
    """The real repository files all carry the number in VERSION."""

    def setUp(self):
        self.sv = _load_script("set_version")
        self.version = _read("VERSION").strip()

    def test_version_file_is_plain_semver(self):
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+$")

    def test_version_code_formula(self):
        self.assertEqual(self.sv.version_code("0.1.0"), 100)
        self.assertEqual(self.sv.version_code("1.2.3"), 10203)
        self.assertEqual(self.sv.version_code("12.34.56"), 123456)

    def test_version_code_rejects_out_of_range_parts(self):
        for bad in ("1.100.0", "1.0.100", "100000.0.0", "1.2", "v1.2.3", "1.2.3-beta", ""):
            with self.assertRaises(ValueError, msg=bad):
                self.sv.version_code(bad)

    def test_tauri_conf_cargo_and_packages_agree(self):
        self.assertEqual(json.loads(_read("apps", "desktop", "src-tauri", "tauri.conf.json"))["version"], self.version)
        cargo = _read("apps", "desktop", "src-tauri", "Cargo.toml")
        self.assertEqual(re.search(r'^version = "([^"]+)"', cargo, re.M).group(1), self.version)
        lock = _read("apps", "desktop", "src-tauri", "Cargo.lock")
        self.assertIn('name = "magnm-studio"\nversion = "%s"' % self.version, lock)
        for rel in (
            ("apps", "desktop", "package.json"),
            ("apps", "web", "package.json"),
            ("apps", "mobile", "package.json"),
            ("packages", "magnm-design", "package.json"),
        ):
            self.assertEqual(json.loads(_read(*rel))["version"], self.version, "/".join(rel))

    def test_android_gradle_agrees(self):
        gradle = _read("apps", "mobile", "android", "app", "build.gradle")
        self.assertEqual(re.search(r"^\s*versionCode (\d+)\s*$", gradle, re.M).group(1), str(self.sv.version_code(self.version)))
        self.assertEqual(re.search(r'^\s*versionName "([^"]+)"\s*$', gradle, re.M).group(1), self.version)

    def test_check_reports_no_problems(self):
        self.assertEqual(self.sv.check(REPO_ROOT), [])


class SetVersionTests(unittest.TestCase):
    """set_version.py rewrites a copy of the files and check() notices when they drift."""

    def setUp(self):
        self.sv = _load_script("set_version")
        self.tmp = tempfile.mkdtemp(prefix="magnm-version-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for rel in self.sv.TARGET_FILES:
            dest = os.path.join(self.tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(REPO_ROOT, *rel.split("/")), dest)
        with open(os.path.join(self.tmp, "VERSION"), "w", encoding="utf-8") as handle:
            handle.write(_read("VERSION"))

    def test_write_updates_every_file(self):
        self.sv.set_version(self.tmp, "2.5.7")
        self.assertEqual(self.sv.check(self.tmp), [])
        with open(os.path.join(self.tmp, "VERSION"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "2.5.7")
        gradle = open(os.path.join(self.tmp, "apps/mobile/android/app/build.gradle"), encoding="utf-8").read()
        self.assertIn("versionCode 20507", gradle)
        self.assertIn('versionName "2.5.7"', gradle)

    def test_check_names_a_file_that_drifted(self):
        path = os.path.join(self.tmp, "apps/web/package.json")
        data = json.load(open(path, encoding="utf-8"))
        data["version"] = "9.9.9"
        json.dump(data, open(path, "w", encoding="utf-8"))
        problems = self.sv.check(self.tmp)
        self.assertEqual(len(problems), 1)
        self.assertIn("apps/web/package.json", problems[0])

    def test_refuses_a_bad_version_and_changes_nothing(self):
        before = open(os.path.join(self.tmp, "VERSION"), encoding="utf-8").read()
        with self.assertRaises(ValueError):
            self.sv.set_version(self.tmp, "1.2")
        self.assertEqual(open(os.path.join(self.tmp, "VERSION"), encoding="utf-8").read(), before)
        self.assertEqual(self.sv.check(self.tmp), [])

    def test_only_the_version_line_changes(self):
        path = os.path.join(self.tmp, "apps/desktop/src-tauri/Cargo.toml")
        before = open(path, encoding="utf-8").read()
        current = open(os.path.join(self.tmp, "VERSION"), encoding="utf-8").read().strip()
        self.sv.set_version(self.tmp, "3.0.1")
        after = open(path, encoding="utf-8").read()
        self.assertIn(f'version = "{current}"', before)
        self.assertEqual(before.replace(f'version = "{current}"', 'version = "3.0.1"', 1), after)


if __name__ == "__main__":
    unittest.main()

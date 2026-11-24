"""Settings > Video: defaults, checked changes, damaged stored values."""

import importlib
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import db
import paths
import video_settings


class VideoSettingsTests(unittest.TestCase):
    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAGNM_HOME"] = self._tmp.name
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        self._tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(video_settings.get_settings(), {"voice": "am_michael", "speed": 1.0, "captions": True, "flux": False})

    def test_changes_merge_and_persist(self):
        video_settings.update_settings({"voice": "bf_emma"})
        out = video_settings.update_settings({"speed": 1.15, "flux": True})
        self.assertEqual(out, {"voice": "bf_emma", "speed": 1.15, "captions": True, "flux": True})
        self.assertEqual(video_settings.get_settings(), out)

    def test_a_refused_change_changes_nothing(self):
        with self.assertRaises(ValueError):
            video_settings.update_settings({"voice": "af_heart", "speed": 9})
        self.assertEqual(video_settings.get_settings()["voice"], "am_michael")

    def test_bad_values_are_refused(self):
        for bad in ({"speed": True}, {"speed": "fast"}, {"captions": 1}, {"flux": "yes"}, {"voice": "../x"}, {"other": 1}, {}, None):
            with self.assertRaises(ValueError, msg=str(bad)):
                video_settings.update_settings(bad)

    def test_a_damaged_stored_value_falls_back_to_the_default_for_that_field(self):
        db.set_meta(video_settings.SETTINGS_KEY, '{"voice": "nobody", "speed": 2.5, "captions": false}')
        self.assertEqual(video_settings.get_settings(), {"voice": "am_michael", "speed": 1.0, "captions": False, "flux": False})
        db.set_meta(video_settings.SETTINGS_KEY, "not json")
        self.assertEqual(video_settings.get_settings()["voice"], "am_michael")


if __name__ == "__main__":
    unittest.main()

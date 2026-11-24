"""The desktop sidecar must be able to speak: kokoro-onnx and friends are pinned and collected, the SFX is bundled."""

import importlib.util
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")


def _load_build_engine():
    spec = importlib.util.spec_from_file_location("magnm_build_engine_video", os.path.join(SCRIPTS_DIR, "build_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VideoPackagingTests(unittest.TestCase):
    def setUp(self):
        self.be = _load_build_engine()

    def _cmd(self, **kwargs):
        return self.be.build_pyinstaller_command(
            "python", os.path.join(REPO_ROOT, "engine"), "dist", "work", "pyi", [], **kwargs
        )

    def _collected(self, cmd):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == "--collect-all"]

    def test_voice_packages_are_listed(self):
        self.assertEqual(
            set(self.be.VOICE_PACKAGES), {"kokoro_onnx", "onnxruntime", "espeakng_loader", "phonemizer", "language_tags", "segments", "csvw"}
        )

    def test_present_voice_packages_are_collected(self):
        collected = self._collected(self._cmd(collect_extra=["kokoro_onnx", "onnxruntime", "espeakng_loader"]))
        for name in ("kokoro_onnx", "onnxruntime", "espeakng_loader"):
            self.assertIn(name, collected)
        self.assertIn("playwright", collected)

    def test_the_command_still_builds_when_the_voice_packages_are_absent(self):
        collected = self._collected(self._cmd())
        self.assertNotIn("kokoro_onnx", collected)
        self.assertIn("keyring", collected)

    def test_absent_packages_are_left_out(self):
        class Done:
            returncode = 0
            stdout = "kokoro_onnx\nonnxruntime\n"

        with patch.object(subprocess, "run", return_value=Done()) as run:
            found = self.be.present_packages("python", ["kokoro_onnx", "onnxruntime", "espeakng_loader"])
        self.assertEqual(found, ["kokoro_onnx", "onnxruntime"])
        self.assertEqual(run.call_args[0][0][0], "python")

    def test_a_failed_probe_means_no_extra_packages(self):
        class Failed:
            returncode = 1
            stdout = ""

        with patch.object(subprocess, "run", return_value=Failed()):
            self.assertEqual(self.be.present_packages("python", ["kokoro_onnx"]), [])

    def test_the_transition_sound_is_bundled_with_its_folder_layout(self):
        specs = self.be.video_data_specs(REPO_ROOT)
        self.assertEqual(len(specs), 1)
        src, dest = specs[0]
        self.assertTrue(os.path.isfile(src))
        self.assertEqual(dest.replace("\\", "/"), "video-skills/magnm-video/assets/sfx/impact")

    def test_engine_requirements_pin_kokoro_exactly(self):
        with open(os.path.join(REPO_ROOT, "engine", "requirements.txt"), encoding="utf-8") as fh:
            lines = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
        for pin in ("kokoro-onnx==0.4.7", "onnxruntime==1.32.0", "numpy==2.5.5", "espeakng-loader==0.2.4", "phonemizer-fork==3.3.1"):
            self.assertIn(pin, lines)
        self.assertTrue(all("==" in ln for ln in lines), "every requirement must be pinned")
        self.assertNotIn("soundfile", " ".join(lines))

    def test_the_sfx_file_the_renderer_uses_exists_in_the_repo(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
        import video_render

        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, *video_render.SFX_REL)))


if __name__ == "__main__":
    unittest.main()

"""Tests for the local voice-over: model folder, download checks, WAV writing, synthesis with a fake engine."""

import hashlib
import io
import os
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import voiceover


class FakeEngine:
    """Stands in for kokoro_onnx.Kokoro: a quiet sine-free clip whose length follows the text."""

    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        seconds = max(0.5, len(text.split()) * 0.4 / speed)
        return [0.1] * int(24000 * seconds), 24000

    def get_voices(self):
        return ["am_michael", "af_heart"]


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class VoiceoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {"MAGNM_KOKORO_DIR": self.tmp}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        voiceover.reset_engine()

    def _install_fake_models(self):
        for name in voiceover.MODEL_FILES:
            with open(os.path.join(self.tmp, name), "wb") as fh:
                fh.write(b"x" * 16)

    def test_defaults_and_voice_list(self):
        self.assertEqual(voiceover.DEFAULT_VOICE, "am_michael")
        self.assertEqual(voiceover.DEFAULT_SPEED, 1.0)
        ids = [v["id"] for v in voiceover.VOICES]
        self.assertIn("am_michael", ids)
        self.assertEqual(len(ids), len(set(ids)))
        for voice in voiceover.VOICES:
            self.assertTrue(voice["label"])
            self.assertIn(voice["lang"], ("en-us", "en-gb"))

    def test_model_files_pin_the_expected_checksums(self):
        self.assertEqual(
            voiceover.MODEL_FILES["kokoro-v1.0.int8.onnx"]["sha256"],
            "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
        )
        self.assertEqual(
            voiceover.MODEL_FILES["voices-v1.0.bin"]["sha256"],
            "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        )
        for info in voiceover.MODEL_FILES.values():
            self.assertTrue(info["url"].startswith("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"))

    def test_models_dir_follows_the_environment_variable(self):
        self.assertEqual(voiceover.models_dir(), os.path.abspath(self.tmp))
        with patch.dict(os.environ, {"MAGNM_KOKORO_DIR": ""}):
            self.assertTrue(voiceover.models_dir().replace("\\", "/").endswith("models/kokoro"))

    def test_status_reports_missing_and_installed(self):
        st = voiceover.status()
        self.assertFalse(st["installed"])
        self.assertEqual(sorted(st["missing"]), sorted(voiceover.MODEL_FILES))
        self._install_fake_models()
        st = voiceover.status()
        self.assertTrue(st["installed"])
        self.assertEqual(st["missing"], [])
        self.assertEqual(st["default_voice"], "am_michael")

    def test_no_voice_installed_is_a_plain_error_with_the_download_hint(self):
        with self.assertRaises(voiceover.VoiceError) as ctx:
            voiceover.synthesize("Hello there.", os.path.join(self.tmp, "a.wav"))
        self.assertEqual(ctx.exception.code, "no_voice")
        self.assertIn("Download voice", ctx.exception.message)
        self.assertIn("without a voice", ctx.exception.message)

    def test_write_wav_is_a_valid_stdlib_wav(self):
        path = os.path.join(self.tmp, "out.wav")
        voiceover.write_wav(path, [0.0, 0.5, -0.5, 2.0, -2.0] * 2400, 24000)
        with wave.open(path, "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.getnframes(), 12000)
        self.assertAlmostEqual(voiceover.wav_duration(path), 0.5, places=3)

    def test_synthesize_uses_voice_speed_and_language_and_measures_length(self):
        self._install_fake_models()
        engine = FakeEngine()
        out = os.path.join(self.tmp, "clip.wav")
        info = voiceover.synthesize("One two three four five.", out, voice="bf_emma", speed=1.2, engine=engine)
        self.assertEqual(engine.calls[0]["voice"], "bf_emma")
        self.assertEqual(engine.calls[0]["speed"], 1.2)
        self.assertEqual(engine.calls[0]["lang"], "en-gb")
        self.assertAlmostEqual(info["duration_s"], voiceover.wav_duration(out), places=3)
        self.assertGreater(info["duration_s"], 1.0)

    def test_synthesize_defaults_to_the_default_voice_and_us_english(self):
        self._install_fake_models()
        engine = FakeEngine()
        voiceover.synthesize("Hi there friend.", os.path.join(self.tmp, "c.wav"), engine=engine)
        self.assertEqual(engine.calls[0]["voice"], "am_michael")
        self.assertEqual(engine.calls[0]["speed"], 1.0)
        self.assertEqual(engine.calls[0]["lang"], "en-us")

    def test_unknown_voice_and_empty_text_are_refused(self):
        self._install_fake_models()
        with self.assertRaises(voiceover.VoiceError) as ctx:
            voiceover.synthesize("Hi.", os.path.join(self.tmp, "c.wav"), voice="nobody", engine=FakeEngine())
        self.assertEqual(ctx.exception.code, "bad_voice")
        with self.assertRaises(voiceover.VoiceError):
            voiceover.synthesize("   ", os.path.join(self.tmp, "c.wav"), engine=FakeEngine())

    def test_speed_is_kept_in_a_sane_range(self):
        self.assertEqual(voiceover.clean_speed(None), 1.0)
        self.assertEqual(voiceover.clean_speed("1.1"), 1.1)
        self.assertEqual(voiceover.clean_speed(9), voiceover.MAX_SPEED)
        self.assertEqual(voiceover.clean_speed(0.1), voiceover.MIN_SPEED)
        self.assertEqual(voiceover.clean_speed("fast"), 1.0)

    def test_missing_library_is_a_clear_error_not_an_import_crash(self):
        self._install_fake_models()
        with patch.dict(sys.modules, {"kokoro_onnx": None}):
            voiceover.reset_engine()
            with self.assertRaises(voiceover.VoiceError) as ctx:
                voiceover.synthesize("Hi there.", os.path.join(self.tmp, "c.wav"))
        self.assertEqual(ctx.exception.code, "no_library")
        self.assertIn("voice", ctx.exception.message.lower())

    def test_timeline_track_puts_each_clip_at_its_start_and_pads_to_the_total(self):
        a = os.path.join(self.tmp, "a.wav")
        b = os.path.join(self.tmp, "b.wav")
        voiceover.write_wav(a, [0.2] * 24000, 24000)  # 1 s
        voiceover.write_wav(b, [0.2] * 12000, 24000)  # 0.5 s
        out = os.path.join(self.tmp, "track.wav")
        voiceover.write_timeline_track(out, [(0.5, a), (2.0, b)], total_s=4.0)
        self.assertAlmostEqual(voiceover.wav_duration(out), 4.0, places=2)
        with wave.open(out, "rb") as wav:
            raw = wav.readframes(wav.getnframes())
        first_loud = next(i for i in range(0, len(raw), 2) if raw[i:i + 2] != b"\x00\x00") // 2
        self.assertAlmostEqual(first_loud / 24000, 0.5, places=2)

    def test_download_checks_sha256_and_deletes_a_mismatch(self):
        os.environ["MAGNM_KOKORO_DIR"] = ""
        with patch.object(voiceover, "models_dir", return_value=self.tmp):
            data = b"not the model"
            with patch.dict(voiceover.MODEL_FILES["voices-v1.0.bin"], {"sha256": "0" * 64}):
                with self.assertRaises(voiceover.VoiceError) as ctx:
                    voiceover.download(opener=lambda url: FakeResponse(data), only=["voices-v1.0.bin"])
            self.assertEqual(ctx.exception.code, "bad_checksum")
            self.assertIn("deleted", ctx.exception.message.lower())
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "voices-v1.0.bin")))
            self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".part")], [])

    def test_download_reports_progress_and_keeps_a_verified_file(self):
        data = b"model-bytes" * 5000
        digest = hashlib.sha256(data).hexdigest()
        seen = []
        with patch.object(voiceover, "models_dir", return_value=self.tmp):
            with patch.dict(voiceover.MODEL_FILES["voices-v1.0.bin"], {"sha256": digest}):
                voiceover.download(
                    progress=lambda pct, msg: seen.append((pct, msg)),
                    opener=lambda url: FakeResponse(data),
                    only=["voices-v1.0.bin"],
                )
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "voices-v1.0.bin")))
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen[-1][0], 100)
        self.assertEqual(sorted(p for p, _ in seen), [p for p, _ in seen])

    def test_download_can_be_cancelled_and_leaves_no_partial_file(self):
        data = b"z" * 400000
        with patch.object(voiceover, "models_dir", return_value=self.tmp):
            with self.assertRaises(voiceover.VoiceError) as ctx:
                voiceover.download(
                    opener=lambda url: FakeResponse(data), only=["voices-v1.0.bin"], cancel=lambda: True,
                )
        self.assertEqual(ctx.exception.code, "cancelled")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_download_failure_is_plain(self):
        def boom(url):
            raise OSError("network down")

        with patch.object(voiceover, "models_dir", return_value=self.tmp):
            with self.assertRaises(voiceover.VoiceError) as ctx:
                voiceover.download(opener=boom, only=["voices-v1.0.bin"])
        self.assertEqual(ctx.exception.code, "download_failed")
        self.assertIn("internet", ctx.exception.message.lower())

    def test_sample_writes_a_short_wav_for_a_voice(self):
        self._install_fake_models()
        engine = FakeEngine()
        with patch.object(voiceover, "_get_engine", return_value=engine), patch.object(
            voiceover, "cache_dir", return_value=os.path.join(self.tmp, "cache")
        ):
            path = voiceover.sample("af_heart", 1.0)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(engine.calls[0]["voice"], "af_heart")
        self.assertNotIn("Cero", engine.calls[0]["text"])


if __name__ == "__main__":
    unittest.main()

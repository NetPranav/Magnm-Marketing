"""The real toolchain's process handling (cancel, failure, progress) using small Python processes instead of ffmpeg."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import video_render


def _py(code):
    return [sys.executable, "-c", code]


class RealToolchainProcessTests(unittest.TestCase):
    def setUp(self):
        self.tc = video_render.RealToolchain()

    def test_a_cancel_stops_the_process_quickly(self):
        started = time.monotonic()
        with self.assertRaises(video_render.RenderCancelled):
            self.tc.ffmpeg(_py("import time; time.sleep(60)"), cancel=lambda: time.monotonic() - started > 0.4)
        self.assertLess(time.monotonic() - started, 10)

    def test_a_failure_is_a_plain_error_with_the_tail_of_stderr(self):
        with self.assertRaises(video_render.RenderError) as ctx:
            self.tc.ffmpeg(_py("import sys; sys.stderr.write('bad filter graph\\n'); sys.exit(3)"))
        self.assertEqual(ctx.exception.code, "ffmpeg_failed")
        self.assertIn("bad filter graph", ctx.exception.message)

    def test_progress_lines_are_reported_in_seconds(self):
        seen = []
        code = "import time\nfor i in (1, 2):\n    print('out_time_us=%d' % (i * 1500000), flush=True)\n    time.sleep(0.4)\n"
        self.tc.ffmpeg(_py(code), on_progress=seen.append)
        self.assertEqual(seen, [1.5, 3.0])

    def test_stderr_text_is_returned_for_the_loudness_measurement(self):
        text = self.tc.ffmpeg(_py("import sys; sys.stderr.write('hello measurement')"))
        self.assertEqual(text, "hello measurement")

    def test_the_working_folder_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tc.ffmpeg(_py("open('made.txt','w').write('x')"), cwd=tmp)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "made.txt")))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is not installed")
class RealFfmpegTests(unittest.TestCase):
    """Real ffmpeg on a one-second clip: probe and the blank-picture check."""

    def setUp(self):
        self.tc = video_render.RealToolchain()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _clip(self, source, name):
        path = os.path.join(self._tmp.name, name)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source, "-f", "lavfi", "-i", "sine=d=1", "-t", "1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path],
            check=True,
        )
        return path

    def test_probe_reads_size_length_and_audio(self):
        info = self.tc.probe(self._clip("testsrc2=s=320x240:d=1", "a.mp4"))
        self.assertEqual((info["width"], info["height"]), (320, 240))
        self.assertAlmostEqual(info["duration_s"], 1.0, delta=0.2)
        self.assertTrue(info["has_audio"])
        self.assertEqual(info["video_codec"], "h264")
        self.assertAlmostEqual(info["video_duration_s"], 1.0, delta=0.2)
        self.assertAlmostEqual(info["audio_duration_s"], 1.0, delta=0.2)

    def test_a_flat_picture_is_blank_and_a_busy_one_is_not(self):
        flat = self._clip("color=c=0xBCE3FF:s=320x240:d=1", "flat.mp4")
        busy = self._clip("testsrc2=s=320x240:d=1", "busy.mp4")
        self.assertLess(self.tc.frame_spread(flat, 0.3), video_render.BLANK_SPREAD)
        self.assertGreater(self.tc.frame_spread(busy, 0.3), video_render.BLANK_SPREAD)


if __name__ == "__main__":
    unittest.main()

"""Tests for the video renderer with fake TTS, browser and ffmpeg: timing, captions, checks, rules, cancel, files."""

import importlib
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import db
import paths
import voiceover

SCRIPT = (
    "Scene 1 (0s-3s)\n"
    "Visual: Terminal showing a destructive command prompt.\n"
    "On-screen: ASKS FIRST.\n"
    "VO: Most terminal agents run commands before you can blink.\n\n"
    "Scene 2 (3s-10s)\n"
    "Visual: Quillby approval banner highlighting file changes.\n"
    "On-screen: PREVIEW DIFF.\n"
    "VO: Quillby inspects every command and asks for approval first.\n\n"
    "Scene 3 (10s-15s)\n"
    "Visual: End card with repo link.\n"
    "On-screen: TRY QUILLBY.\n"
    "VO: Stay in control of every change."
)

RULES = {
    "version": 1,
    "show_levels": {
        "everything": "Agents may describe and show the product, using approved facts only.",
        "text_only": "Text only.",
        "teasers_only": "Teasers only.",
        "nothing": "Nothing.",
    },
    "products": [
        {"id": "quillby", "name": "Quillby", "status": "released", "show": "everything", "facts_file": None,
         "blocked_terms": [], "confidential": False, "notes": ""},
        {"id": "zorblax", "name": "Zorblax", "status": "in_development", "show": "teasers_only", "facts_file": None,
         "blocked_terms": [], "confidential": True, "notes": ""},
    ],
}


class FakeToolchain:
    """Records every call and writes small placeholder files, so no TTS, browser or ffmpeg runs."""

    def __init__(self):
        self.ffmpeg_calls = []
        self.synth_calls = []
        self.screen_calls = []
        self.cancel_after_synth = None
        self.spread = 40.0
        self.duration_error = 0.0
        self.has_audio = True
        self.video_short = 0.0
        self.screens_fail = False

    def have_ffmpeg(self):
        return True

    def synth(self, text, wav_path, voice, speed):
        self.synth_calls.append({"text": text, "voice": voice, "speed": speed})
        seconds = len(text.split()) * 0.35
        voiceover.write_wav(wav_path, [0.1] * int(24000 * seconds), 24000)
        return {"duration_s": voiceover.wav_duration(wav_path)}

    def screens(self, specs, width, height, cancel=None, on_scene=None):
        self.screen_calls.append({"specs": specs, "size": (width, height)})
        if self.screens_fail:
            from video_render import RenderError
            raise RenderError("The video renderer's browser is not installed.", "no_browser")
        for k, spec in enumerate(specs, 1):
            if on_scene:
                on_scene(k)
            with open(spec["png"], "wb") as fh:
                fh.write(b"\x89PNG fake")

    def ffmpeg(self, args, cwd=None, cancel=None, on_progress=None):
        self.ffmpeg_calls.append({"args": list(args), "cwd": cwd})
        out = args[-1]
        if out != "-" and not str(out).startswith("-"):
            target = out if os.path.isabs(out) else os.path.join(cwd or ".", out)
            with open(target, "wb") as fh:
                fh.write(b"fake media " + os.path.basename(target).encode())
        return ""

    def probe(self, path):
        name = os.path.basename(path)
        total = getattr(self, "expected_total", 15.0) + self.duration_error
        return {
            "duration_s": total,
            "video_duration_s": total - self.video_short,
            "audio_duration_s": total if self.has_audio else None,
            "width": 1920 if "16x9" in name else 1080,
            "height": 1080 if "16x9" in name else 1920,
            "has_video": True,
            "has_audio": self.has_audio,
            "video_codec": "h264",
            "audio_codec": "aac" if self.has_audio else None,
        }

    def frame_spread(self, path, at_s):
        return self.spread


def _ffmpeg_text(call):
    return " ".join(str(a) for a in call["args"])


class VideoRenderBase(unittest.TestCase):
    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_kokoro = os.environ.pop("MAGNM_KOKORO_DIR", None)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAGNM_HOME"] = self._tmp.name
        importlib.reload(paths)
        paths.ensure_data_root()
        with open(paths.data("config", "product_rules.json"), "w", encoding="utf-8") as fh:
            json.dump(RULES, fh)
        import product_rules
        product_rules.clear_cache()
        db.set_db_path(None)
        db.init_db()
        import video_render
        self.vr = importlib.reload(video_render)
        self.fake = FakeToolchain()
        patcher = patch.object(self.vr, "TOOLCHAIN", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        if self._prev_kokoro is not None:
            os.environ["MAGNM_KOKORO_DIR"] = self._prev_kokoro
        importlib.reload(paths)
        self._tmp.cleanup()

    def make_item(self, body=SCRIPT, product_id="quillby", title="Quillby 15s Reel", content_id="cnt_vid_1", session_id=None):
        db.create_content_item(
            content_id=content_id, content_type="script", platform="instagram", title=title, body=body,
            status="draft", product_id=product_id, session_id=session_id,
        )
        return content_id

    def render(self, content_id="cnt_vid_1", **kwargs):
        kwargs.setdefault("voice", False)
        return self.vr.render_video(content_id, **kwargs)


class TimelineTests(VideoRenderBase):
    def test_each_scene_lasts_the_longer_of_planned_and_voice_plus_pause(self):
        scenes = self.vr.parse_scenes(SCRIPT, 15)
        self.assertEqual([s["on_screen"] for s in scenes], ["ASKS FIRST.", "PREVIEW DIFF.", "TRY QUILLBY."])
        timeline, total = self.vr.plan_timeline(scenes, [3.5, 3.0, 2.0])
        durations = [round(t["duration_s"], 2) for t in timeline]
        self.assertEqual(durations[0], 3.9)  # voice 3.5 s + 0.4 s beats the planned 3 s
        self.assertEqual(durations[1], 7.0)  # planned 7 s beats voice + 0.4 s
        self.assertEqual(durations[2], 5.0)
        self.assertTrue(timeline[-1]["end_card"])
        self.assertAlmostEqual(total, sum(t["duration_s"] for t in timeline), places=3)
        starts = [t["start_s"] for t in timeline]
        self.assertEqual(starts[0], 0.0)
        for prev, cur in zip(timeline, timeline[1:]):
            self.assertAlmostEqual(cur["start_s"], prev["start_s"] + prev["duration_s"], places=3)

    def test_without_voice_the_planned_timings_are_kept(self):
        scenes = self.vr.parse_scenes(SCRIPT, 15)
        timeline, _ = self.vr.plan_timeline(scenes, [None, None, None])
        self.assertEqual([round(t["duration_s"], 2) for t in timeline[:3]], [3.0, 7.0, 5.0])

    def test_voice_starts_a_beat_after_the_scene_starts(self):
        scenes = self.vr.parse_scenes(SCRIPT, 15)
        timeline, _ = self.vr.plan_timeline(scenes, [3.0, 3.0, 2.0])
        self.assertAlmostEqual(timeline[1]["vo_start_s"], timeline[1]["start_s"] + self.vr.VO_LEAD_S, places=3)


class CaptionTests(VideoRenderBase):
    def test_lines_are_short_and_at_most_two_per_caption(self):
        text = "Most terminal agents run commands before you can blink, and then they ask forgiveness afterwards."
        cues = self.vr.split_caption_lines(text)
        self.assertGreaterEqual(len(cues), 2)
        for cue in cues:
            lines = cue.split("\n")
            self.assertLessEqual(len(lines), 2)
            for line in lines:
                self.assertLessEqual(len(line), 34, line)
        self.assertEqual(" ".join(c.replace("\n", " ") for c in cues), text)

    def test_a_line_that_just_fits_does_not_leave_a_one_word_caption(self):
        cues = self.vr.split_caption_lines("Quillby inspects every command and asks for your approval first.")
        self.assertEqual(len(cues), 1)
        self.assertEqual(len(cues[0].split("\n")), 2)

    def test_a_word_longer_than_a_line_is_not_cut(self):
        cues = self.vr.split_caption_lines("Supercalifragilisticexpialidocious works.")
        self.assertIn("Supercalifragilisticexpialidocious", " ".join(cues))

    def test_empty_text_gives_no_captions(self):
        self.assertEqual(self.vr.split_caption_lines("   "), [])

    def test_short_sentences_share_a_caption_when_they_fit(self):
        cues = self.vr.split_caption_lines("Stay in control. Try it.")
        self.assertEqual(cues, ["Stay in control.\nTry it."])

    def test_captions_follow_the_real_voice_clip_timing(self):
        scenes = self.vr.parse_scenes(SCRIPT, 15)
        timeline, _ = self.vr.plan_timeline(scenes, [3.5, 3.0, 2.0])
        cues = self.vr.retime_captions(timeline)
        self.assertGreaterEqual(len(cues), 3)
        prev_end = 0.0
        for start, end, text in cues:
            self.assertLess(start, end)
            self.assertGreaterEqual(start, prev_end - 1e-6)
            prev_end = end
        first = timeline[0]
        self.assertAlmostEqual(cues[0][0], first["vo_start_s"], places=2)
        last_scene = timeline[2]
        self.assertAlmostEqual(cues[-1][1], last_scene["vo_start_s"] + 2.0, places=2)

    def test_scenes_without_voice_make_no_caption(self):
        scenes = self.vr.parse_scenes(SCRIPT, 15)
        scenes[1]["voiceover"] = ""
        timeline, _ = self.vr.plan_timeline(scenes, [3.5, None, 2.0])
        for start, end, _ in self.vr.retime_captions(timeline):
            self.assertFalse(timeline[1]["start_s"] <= start < timeline[1]["start_s"] + timeline[1]["duration_s"])

    def test_srt_format(self):
        srt = self.vr.srt_text([(0.0, 1.5, "Hello\nthere"), (2.0, 3.25, "Next one")])
        self.assertEqual(
            srt,
            "1\n00:00:00,000 --> 00:00:01,500\nHello\nthere\n\n2\n00:00:02,000 --> 00:00:03,250\nNext one\n",
        )

    def test_ass_keeps_portrait_captions_inside_the_safe_zone(self):
        ass = self.vr.ass_text([(0.0, 1.5, "Hello\nthere")], 1080, 1920)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.50,Caption,,0,0,0,,Hello\\Nthere", ass)
        style = [ln for ln in ass.splitlines() if ln.startswith("Style: Caption")][0].split(",")
        margin_v = int(style[-2])
        self.assertGreaterEqual(margin_v, int(1920 * 0.2))
        wide = self.vr.ass_text([(0.0, 1.0, "Hi")], 1920, 1080)
        wide_style = [ln for ln in wide.splitlines() if ln.startswith("Style: Caption")][0].split(",")
        self.assertLess(int(wide_style[-2]), 200)


class SceneLayoutTests(VideoRenderBase):
    def test_html_has_text_product_name_fonts_and_escapes_markup(self):
        spec = {"text": "ASKS <b>FIRST</b>.", "kicker": "QUILLBY", "field": "sky", "index": 1, "count": 4, "kind": "scene",
                "image": None, "foot": "magnm-org.vercel.app"}
        html = self.vr.scene_html(spec, 1080, 1920)
        self.assertIn("QUILLBY", html)
        self.assertIn("ASKS &lt;b&gt;FIRST&lt;/b&gt;.", html)
        self.assertNotIn("<b>FIRST", html)
        self.assertIn("anton.ttf", html)
        self.assertIn("#BCE3FF", html)
        self.assertNotIn("gradient", html)
        self.assertNotIn("box-shadow", html)

    def test_end_card_names_the_product_and_site(self):
        spec = {"text": "", "kicker": "QUILLBY", "field": "lime", "index": 4, "count": 4, "kind": "end",
                "image": None, "foot": "magnm-org.vercel.app", "product": "Quillby"}
        html = self.vr.scene_html(spec, 1920, 1080)
        self.assertIn("Quillby", html)
        self.assertIn("magnm-org.vercel.app", html)
        self.assertIn("MAGNM", html)


class RenderFlowTests(VideoRenderBase):
    def test_renders_both_aspects_and_writes_every_file(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        result = self.render()
        out_dir = paths.data("outputs", "video", "cnt_vid_1")
        for name in ("video-9x16.mp4", "video-16x9.mp4", "poster.jpg", "preview-480p.mp4", "captions.srt",
                     "voiceover.txt", "README.md"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)
        self.assertEqual(result["aspects"], ["9:16", "16:9"])
        self.assertEqual(self.fake.screen_calls[0]["size"], (1080, 1920))
        self.assertEqual(self.fake.screen_calls[1]["size"], (1920, 1080))
        encodes = [c for c in self.fake.ffmpeg_calls if "libx264" in _ffmpeg_text(c) and "xfade=" in _ffmpeg_text(c)]
        self.assertEqual(len(encodes), 2)
        for call in encodes:
            text = _ffmpeg_text(call)
            self.assertIn("+faststart", text)
            self.assertIn("aac", text)
            self.assertIn("subtitles=", text)
            self.assertEqual(text.count("xfade="), 3)  # 4 pictures (3 scenes and the end card), 3 transitions

    def test_the_encode_command_sets_the_output_size_and_frame_rate(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render()
        encodes = [c for c in self.fake.ffmpeg_calls if "xfade=" in _ffmpeg_text(c) and "libx264" in _ffmpeg_text(c)]
        self.assertIn("s=1080x1920:fps=30", _ffmpeg_text(encodes[0]))
        self.assertIn("s=1920x1080:fps=30", _ffmpeg_text(encodes[1]))

    def test_each_picture_is_cut_so_the_video_is_exactly_as_long_as_the_timeline(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render(aspects=["9:16"])
        encode = [c for c in self.fake.ffmpeg_calls if "xfade=" in _ffmpeg_text(c) and "libx264" in _ffmpeg_text(c)][0]
        text = _ffmpeg_text(encode)
        frames = [int(m) for m in re.findall(r":d=(\d+):s=", text)]
        x = self.vr.XFADE_S
        self.assertEqual(frames, [90, round((7.0 + x) * 30), round((5.0 + x) * 30), round((self.vr.END_CARD_S + x) * 30)])
        offsets = [float(m) for m in re.findall(r"offset=([0-9.]+)", text)]
        self.assertEqual(offsets, [round(3.0 - x, 3), round(10.0 - x, 3), round(15.0 - x, 3)])
        # The whole chain: first picture + each later picture, minus the overlaps, adds up to the timeline.
        total = frames[0] / 30 + sum(f / 30 - x for f in frames[1:])
        self.assertAlmostEqual(total, 17.5, places=1)

    def test_attaches_video_poster_and_captions_and_replaces_them_on_a_re_render(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render()
        self.render()
        files = db.get_content_item("cnt_vid_1")["files"]
        kinds = sorted(f["kind"] for f in files)
        self.assertEqual(kinds, ["captions", "poster", "video", "video"])
        for entry in files:
            self.assertTrue(entry["path"].replace("\\", "/").startswith("outputs/video/cnt_vid_1/"))

    def test_the_voice_timed_captions_replace_the_planned_ones(self):
        self.make_item()
        db.append_content_files("cnt_vid_1", [{"kind": "captions", "path": "outputs/content/cnt_vid_1/captions.srt"},
                                              {"kind": "draft", "path": "outputs/content/cnt_vid_1/v1.md"}])
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render()
        files = db.get_content_item("cnt_vid_1")["files"]
        caps = [f["path"] for f in files if f["kind"] == "captions"]
        self.assertEqual(caps, ["outputs/video/cnt_vid_1/captions.srt"])
        self.assertIn("outputs/content/cnt_vid_1/v1.md", [f["path"] for f in files])

    def test_one_aspect_only(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        result = self.render(aspects=["16:9"])
        out_dir = paths.data("outputs", "video", "cnt_vid_1")
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "video-16x9.mp4")))
        self.assertFalse(os.path.exists(os.path.join(out_dir, "video-9x16.mp4")))
        self.assertEqual(result["aspects"], ["16:9"])

    def test_bad_aspect_is_refused(self):
        self.make_item()
        with self.assertRaises(self.vr.RenderError):
            self.render(aspects=["4:3"])

    def test_voice_off_uses_planned_timing_and_skips_the_voice(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        result = self.render(voice=False)
        self.assertEqual(self.fake.synth_calls, [])
        self.assertFalse(result["voice"]["used"])
        out_dir = paths.data("outputs", "video", "cnt_vid_1")
        self.assertFalse(os.path.exists(os.path.join(out_dir, "voiceover.wav")))
        with open(os.path.join(out_dir, "voiceover.txt"), encoding="utf-8") as fh:
            self.assertIn("Stay in control", fh.read())

    def test_voice_on_writes_voiceover_and_scene_lengths_follow_the_clips(self):
        self.make_item()
        with patch.object(voiceover, "missing_files", return_value=[]):
            # Clips are 0.35 s per word: scene 1 (9 words) is 3.15 s + 0.4 s, longer than its planned 3 s.
            self.fake.expected_total = 3.55 + 7.0 + 5.0 + self.vr.END_CARD_S
            result = self.render(voice=True)
        timeline = result["timeline"]
        self.assertAlmostEqual(timeline[0]["duration_s"], 3.55, places=2)
        self.assertEqual(len(self.fake.synth_calls), 3)
        self.assertTrue(result["voice"]["used"])
        self.assertEqual(result["voice"]["voice"], "am_michael")
        self.assertTrue(os.path.isfile(paths.data("outputs", "video", "cnt_vid_1", "voiceover.wav")))
        self.assertAlmostEqual(
            voiceover.wav_duration(paths.data("outputs", "video", "cnt_vid_1", "voiceover.wav")), result["total_s"], places=1
        )

    def test_voice_clips_are_cached_so_a_second_render_does_not_speak_again(self):
        self.make_item()
        with patch.object(voiceover, "missing_files", return_value=[]):
            self.fake.expected_total = 3.55 + 7.0 + 5.0 + self.vr.END_CARD_S
            self.render(voice=True)
            first = len(self.fake.synth_calls)
            self.render(voice=True)
        self.assertEqual(len(self.fake.synth_calls), first)

    def test_respelled_names_are_spoken_but_captions_keep_the_real_spelling(self):
        body = SCRIPT.replace("Quillby inspects", "Magnm inspects")
        self.make_item(body=body)
        with patch.object(voiceover, "missing_files", return_value=[]):
            self.fake.expected_total = 3.55 + 7.0 + 5.0 + self.vr.END_CARD_S
            self.render(voice=True)
        spoken = " ".join(c["text"] for c in self.fake.synth_calls)
        self.assertIn("Magnum inspects", spoken)
        with open(paths.data("outputs", "video", "cnt_vid_1", "captions.srt"), encoding="utf-8") as fh:
            self.assertIn("Magnm inspects", fh.read())

    def test_captions_off_burns_nothing_but_still_writes_the_srt(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render(captions=False)
        encodes = [c for c in self.fake.ffmpeg_calls if "libx264" in _ffmpeg_text(c) and "xfade=" in _ffmpeg_text(c)]
        for call in encodes:
            self.assertNotIn("subtitles=", _ffmpeg_text(call))
        self.assertTrue(os.path.isfile(paths.data("outputs", "video", "cnt_vid_1", "captions.srt")))

    def test_audio_is_mixed_with_a_whoosh_and_loudness_normalised(self):
        self.make_item()
        self.fake.expected_total = 3.55 + 7.0 + 5.0 + self.vr.END_CARD_S
        with patch.object(voiceover, "missing_files", return_value=[]):
            self.render(voice=True)
        text = "\n".join(_ffmpeg_text(c) for c in self.fake.ffmpeg_calls)
        self.assertIn("loudnorm", text)
        self.assertIn("adelay", text)
        self.assertIn("video-skills/magnm-video/assets/sfx", text.replace("\\", "/"))
        self.assertNotIn("music", text.lower())

    def test_readme_lists_how_it_was_made_voice_and_licences(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render()
        with open(paths.data("outputs", "video", "cnt_vid_1", "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for needle in ("Kokoro", "CC0", "ffmpeg", "OFL", "Voice", "9:16", "16:9"):
            self.assertIn(needle, readme)

    def test_progress_reports_scenes_and_encoding(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        seen = []
        self.render(progress=lambda pct, msg: seen.append((pct, msg)))
        messages = " | ".join(m for _, m in seen)
        self.assertIn("Scene 1 of", messages)
        self.assertIn("Encoding", messages)
        self.assertEqual(sorted(p for p, _ in seen), [p for p, _ in seen])
        self.assertEqual(seen[-1][0], 100)

    def test_status_reflects_the_last_render(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.assertEqual(self.vr.render_status("cnt_vid_1")["state"], "idle")
        self.render()
        st = self.vr.render_status("cnt_vid_1")
        self.assertEqual(st["state"], "done")
        self.assertEqual(len(st["files"]), 4)

    def test_unknown_content_item(self):
        with self.assertRaises(self.vr.RenderError) as ctx:
            self.render(content_id="cnt_missing")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_an_id_that_could_leave_the_output_folder_is_refused(self):
        for bad in ("../outside", "a/b", "", None, "cnt\\x"):
            with self.assertRaises(self.vr.RenderError) as ctx:
                self.render(content_id=bad)
            self.assertEqual(ctx.exception.code, "not_found", bad)
        self.assertFalse(os.path.exists(os.path.join(paths.data("outputs", "video"), "..", "outside")))


class CheckTests(VideoRenderBase):
    def _checks(self, **over):
        self.fake.expected_total = 20.0
        files = {"9:16": os.path.join(self._tmp.name, "video-9x16.mp4")}
        with open(files["9:16"], "wb") as fh:
            fh.write(b"x")
        args = dict(files=files, expected_s=20.0, voice=True)
        args.update(over)
        return self.vr.check_outputs(**args)

    def test_a_good_render_passes(self):
        self.assertTrue(self._checks()["ok"])

    def test_duration_must_match_the_audio_within_half_a_second(self):
        self.fake.duration_error = 0.9
        with self.assertRaises(self.vr.RenderError) as ctx:
            self._checks()
        self.assertEqual(ctx.exception.code, "check_failed")
        self.assertIn("length", ctx.exception.message.lower())

    def test_a_picture_shorter_than_the_audio_fails_even_though_the_container_is_long_enough(self):
        self.fake.video_short = 0.8
        with self.assertRaises(self.vr.RenderError) as ctx:
            self._checks()
        self.assertIn("length", ctx.exception.message.lower())

    def test_audio_stream_is_required_when_voice_is_on(self):
        self.fake.has_audio = False
        with self.assertRaises(self.vr.RenderError) as ctx:
            self._checks()
        self.assertIn("audio", ctx.exception.message.lower())
        self.fake.has_audio = False
        self.assertTrue(self._checks(voice=False)["ok"])

    def test_blank_picture_fails(self):
        self.fake.spread = 0.2
        with self.assertRaises(self.vr.RenderError) as ctx:
            self._checks()
        self.assertIn("blank", ctx.exception.message.lower())

    def test_missing_file_fails(self):
        with self.assertRaises(self.vr.RenderError):
            self._checks(files={"9:16": os.path.join(self._tmp.name, "nope.mp4")})

    def test_wrong_size_fails(self):
        files = {"16:9": os.path.join(self._tmp.name, "video-9x16.mp4")}
        with open(files["16:9"], "wb") as fh:
            fh.write(b"x")
        with self.assertRaises(self.vr.RenderError):
            self._checks(files=files)

    def test_a_failed_check_fails_the_render_and_keeps_nothing_new(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.fake.duration_error = 2.0
        with self.assertRaises(self.vr.RenderError):
            self.render()
        out_dir = paths.data("outputs", "video", "cnt_vid_1")
        self.assertFalse(os.path.exists(os.path.join(out_dir, "video-9x16.mp4")))
        self.assertEqual(db.get_content_item("cnt_vid_1")["files"], [])


class RulesTests(VideoRenderBase):
    def test_a_script_naming_a_confidential_product_is_refused(self):
        body = SCRIPT.replace("Quillby inspects", "Zorblax inspects")
        self.make_item(body=body)
        with self.assertRaises(self.vr.RenderError) as ctx:
            self.render()
        self.assertEqual(ctx.exception.code, "confidential")
        self.assertNotIn("Zorblax", ctx.exception.message)
        self.assertEqual(self.fake.ffmpeg_calls, [])

    def test_a_confidential_item_renders_locally_without_repo_footage_or_cloud_images(self):
        body = SCRIPT.replace("Quillby inspects", "Zorblax inspects")
        self.make_item(body=body, product_id="zorblax")
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        with patch.object(self.vr, "_flux_image") as flux, patch.object(self.vr, "pick_media") as media:
            result = self.render(flux=True, repo_id="some-repo")
        flux.assert_not_called()
        media.assert_not_called()
        self.assertTrue(result["local_only"])
        specs = self.fake.screen_calls[0]["specs"]
        self.assertTrue(all(not s.get("image") for s in specs))
        # The in-development product's name is never put on screen.
        self.assertTrue(all("ZORBLAX" not in json.dumps(s).upper() for s in specs))

    def test_a_product_that_is_not_shown_never_appears_on_screen(self):
        self.make_item(product_id="zorblax", body=SCRIPT)
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.render()
        specs = self.fake.screen_calls[0]["specs"]
        self.assertEqual({s["kicker"] for s in specs}, {"MAGNM"})

    def test_when_the_rules_cannot_be_read_the_render_is_refused(self):
        self.make_item()
        with patch("confidential_guard.scope") as scope:
            scope.return_value.broken = True
            with self.assertRaises(self.vr.RenderError) as ctx:
                self.render()
        self.assertEqual(ctx.exception.code, "confidential")


class MediaTests(VideoRenderBase):
    def _repo(self, tmp, confidential=False):
        os.makedirs(os.path.join(tmp, "docs"))
        with open(os.path.join(tmp, "docs", "terminal-demo.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        with open(os.path.join(tmp, "docs", "other.png"), "wb") as fh:
            fh.write(b"\x89PNG")
        return {"id": "quillby-app", "name": "Quillby", "kind": "local", "local_path": tmp, "product_id": "quillby",
                "confidential": confidential}

    def test_footage_notes_get_a_real_repo_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            scene = {"visual": "Screen recording of the terminal demo", "footage": "", "on_screen": "X", "voiceover": "y"}
            with patch("repos.get_repo", return_value=repo):
                picked = self.vr.pick_media(scene, repo, used=set())
            self.assertTrue(picked["path"].endswith("terminal-demo.png"))
            self.assertEqual(picked["repo_id"], "quillby-app")

    def test_a_note_that_does_not_ask_for_footage_gets_a_branded_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            scene = {"visual": "Big bold type on a flat colour", "footage": "", "on_screen": "X", "voiceover": "y"}
            self.assertIsNone(self.vr.pick_media(scene, repo, used=set()))

    def test_a_confidential_repo_gives_no_footage(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp, confidential=True)
            scene = {"visual": "Screenshot of the terminal demo", "footage": "", "on_screen": "X", "voiceover": "y"}
            self.assertIsNone(self.vr.pick_media(scene, repo, used=set()))

    def test_a_file_is_not_used_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            scene = {"visual": "Screenshot of the terminal demo", "footage": "", "on_screen": "X", "voiceover": "y"}
            first = self.vr.pick_media(scene, repo, used=set())
            second = self.vr.pick_media(scene, repo, used={first["path"]})
            self.assertNotEqual(first["path"], second["path"])
            self.assertIsNone(self.vr.pick_media(scene, repo, used={first["path"], second["path"]}))

    def test_no_repo_no_footage(self):
        scene = {"visual": "Screenshot of the terminal demo", "footage": "", "on_screen": "X", "voiceover": "y"}
        self.assertIsNone(self.vr.pick_media(scene, None, used=set()))

    def test_flux_is_off_unless_the_person_turned_it_on(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        with patch.object(self.vr, "_flux_image", return_value=None) as flux:
            self.render()
        flux.assert_not_called()


class VoiceMissingTests(VideoRenderBase):
    def test_no_voice_installed_gives_the_plain_error(self):
        self.make_item()
        self.fake.synth = lambda *a, **k: (_ for _ in ()).throw(voiceover.VoiceError("The voice is not installed yet. Download voice", "no_voice"))
        with self.assertRaises(self.vr.RenderError) as ctx:
            self.render(voice=True)
        self.assertEqual(ctx.exception.code, "no_voice")
        self.assertIn("Download voice", ctx.exception.message)
        self.assertEqual(self.fake.ffmpeg_calls, [])

    def test_the_real_check_finds_missing_model_files(self):
        self.make_item()
        with self.assertRaises(self.vr.RenderError) as ctx:
            self.render(voice=True)
        self.assertEqual(ctx.exception.code, "no_voice")

    def test_no_voice_choice_renders_without_it(self):
        self.make_item()
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        self.assertTrue(self.render(voice=False)["files"])


class CancelAndLockTests(VideoRenderBase):
    def test_cancel_stops_the_render_and_cleans_up(self):
        self.make_item()
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 3

        with self.assertRaises(self.vr.RenderCancelled):
            self.render(cancel=cancel)
        self.assertFalse(os.path.exists(paths.data("outputs", "video", "cnt_vid_1", "video-9x16.mp4")))
        self.assertEqual(db.get_content_item("cnt_vid_1")["files"], [])
        work = paths.data("cache", "video")
        leftovers = [n for n in os.listdir(work)] if os.path.isdir(work) else []
        self.assertEqual([n for n in leftovers if n.startswith("render-")], [])
        self.assertEqual(self.vr.render_status("cnt_vid_1")["state"], "cancelled")

    def test_only_one_render_at_a_time(self):
        self.make_item()
        self.make_item(content_id="cnt_vid_2", title="Second")
        self.fake.expected_total = 3.0 + 7.0 + 5.0 + self.vr.END_CARD_S
        started = threading.Event()
        release = threading.Event()
        original = self.fake.screens

        def slow_screens(specs, width, height, cancel=None, on_scene=None):
            started.set()
            release.wait(5)
            return original(specs, width, height, cancel, on_scene)

        self.fake.screens = slow_screens
        errors = []

        def first():
            try:
                self.render()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(started.wait(5))
        with self.assertRaises(self.vr.RenderBusy):
            self.render(content_id="cnt_vid_2", wait=False)
        release.set()
        thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(self.render(content_id="cnt_vid_2")["content_id"], "cnt_vid_2")


class MissingToolTests(VideoRenderBase):
    def test_missing_ffmpeg_is_a_plain_error(self):
        self.make_item()
        with patch.object(self.fake, "have_ffmpeg", return_value=False):
            with self.assertRaises(self.vr.RenderError) as ctx:
                self.render()
        self.assertEqual(ctx.exception.code, "no_ffmpeg")
        self.assertIn("ffmpeg", ctx.exception.message.lower())

    def test_missing_browser_is_a_plain_error(self):
        self.make_item()
        self.fake.screens_fail = True
        with self.assertRaises(self.vr.RenderError) as ctx:
            self.render()
        self.assertEqual(ctx.exception.code, "no_browser")


if __name__ == "__main__":
    unittest.main()


class SafeMarginTests(unittest.TestCase):
    """The slow push or pan crops the picture; the text margin must stay wider than the widest crop."""

    def test_the_margin_survives_the_strongest_pan_in_both_aspects(self):
        import re as _re
        import video_render as vr
        zoom = max(float(x) for x in _re.findall(r"1\.\d+", vr._motion_filter(2, 30, 1920, 1080)))
        for width, height in ((1920, 1080), (1080, 1920)):
            html = vr.scene_html({"kind": "text", "kicker": "QUILLBY", "count": 6, "index": 4, "text": "Every step logged."},
                                 width, height)
            margin = int(_re.search(r"left:(\d+)px;top:\d+px'>QUILLBY", html).group(1))
            crop = width * (1 - 1 / zoom)
            self.assertGreaterEqual(margin - crop, 40, (width, height, margin, crop))

"""A video script is not a caption: platform caption limits never apply to it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import platforms  # noqa: E402


class ScriptLimitTests(unittest.TestCase):
    def test_a_long_script_gets_no_caption_length_warning(self):
        body = "Scene 1 (0s-3s)\nVO: line.\n" * 400
        self.assertGreater(len(body), 2200)
        self.assertEqual(platforms.check_limits("instagram", body, content_type="script"), [])

    def test_a_long_post_still_gets_the_caption_length_warning(self):
        body = "word " * 600
        warnings = platforms.check_limits("instagram", body, content_type="post")
        self.assertTrue(any("exceeds" in w["message"] for w in warnings))


class ScriptAutoReviewTests(unittest.TestCase):
    def test_the_automatic_review_does_not_flag_a_long_script(self):
        import review
        item = {"id": "cnt_x", "type": "script", "platform": "instagram", "title": "Reel",
                "body": "Scene 1 (0s-3s)\nVO: line.\n" * 400}
        codes = [f["code"] for f in review.auto_review(item).get("findings") or []]
        self.assertNotIn("platform_max_chars", codes)

    def test_the_automatic_review_still_flags_a_long_post(self):
        import review
        item = {"id": "cnt_y", "type": "post", "platform": "instagram", "title": "Post", "body": "word " * 600}
        codes = [f["code"] for f in review.auto_review(item).get("findings") or []]
        self.assertIn("platform_max_chars", codes)


class CaptionTimingTests(unittest.TestCase):
    SCRIPT = (
        "Format: 30 seconds, 9:16 vertical video\n\n"
        "## Opening hook (0-3s)\n\n"
        "### Scene 1 \u2014 0:00-0:03\nVisual/footage notes: Repo page.\nOn-screen text: \"What is verifiable?\"\n"
        "VO: \"What can we verify today?\"\n\n"
        "## Main sequence\n\n"
        "### Scene 2 \u2014 0:03-0:08\nVisual/footage notes: Header.\nVO: \"It is in development.\"\n\n"
        "### Scene 3 \u2014 0:08-0:30\nVisual/footage notes: End card.\nVO: \"Follow along.\"\n"
    )

    def test_a_section_heading_with_a_time_range_is_not_a_scene(self):
        import workflows
        scenes, srt = workflows._extract_shotlist_and_srt(self.SCRIPT, duration_s=30)
        self.assertEqual([s["voiceover"] for s in scenes],
                         ["What can we verify today?", "It is in development.", "Follow along."])
        blocks = srt.strip().split("\n\n")
        self.assertEqual(blocks[0].splitlines()[1], "00:00:00,000 --> 00:00:03,000")
        self.assertEqual(blocks[0].splitlines()[2], "What can we verify today?")
        self.assertFalse(any(line.startswith("#") for line in srt.splitlines()))

    def test_a_script_without_voice_over_still_gets_captions(self):
        import workflows
        scenes, srt = workflows._extract_shotlist_and_srt("Scene 1 (0s-5s)\nOn-screen: HELLO\n", duration_s=10)
        self.assertIn("HELLO", srt)


class RealScriptSceneTests(unittest.TestCase):
    """The structure a real video-producer run wrote (names invented): numbered headings without the word scene,
    and bold labels with the colon inside the bold."""
    SCRIPT = (
        "# Quillby in 30 Seconds\n\n**Format:** 9:16 vertical video\n**Duration:** 30 seconds\n\n"
        "## Opening hook\n\n**0-3 seconds:** Quillby is not finished.\n\n## Numbered scenes\n\n"
        "### 1. 0-3s \u2014 The hook\n**Visual/footage:** Real capture of the repo page.\n"
        "**On-screen:** `Quillby: work in progress`\n**VO:** Quillby is not a finished product.\n\n"
        "### 2. 3-7s \u2014 What developers have today\n**Visual/footage:** Pan to the README.\n"
        "**On-screen:** `Public repo | main branch`\n**VO:** It is a public codebase to inspect.\n\n"
        "### 3. 7-12s \u2014 The latest change\n**Visual/footage:** Scroll to the link.\n"
        "**On-screen:** `Latest change: README link`\n**VO**: The latest change adds a link to the README.\n\n"
        "### 4. 12-30s \u2014 Follow along\n**On-screen text:** \"Follow the build\"\n"
        "Voice-over: Watch the repo and follow the build.\n"
    )

    def test_every_numbered_heading_is_a_scene_with_its_own_lines(self):
        import workflows
        scenes, srt = workflows._extract_shotlist_and_srt(self.SCRIPT, duration_s=30)
        self.assertEqual([(s["start_s"], s["end_s"]) for s in scenes], [(0, 3), (3, 7), (7, 12), (12, 30)])
        self.assertEqual([s["on_screen"] for s in scenes],
                         ["Quillby: work in progress", "Public repo | main branch", "Latest change: README link",
                          "Follow the build"])
        self.assertEqual(scenes[0]["voiceover"], "Quillby is not a finished product.")
        self.assertEqual(scenes[2]["voiceover"], "The latest change adds a link to the README.")
        self.assertEqual(scenes[1]["visual"], "Pan to the README.")
        self.assertNotIn("*", srt)
        self.assertNotIn("`", srt)


if __name__ == "__main__":
    unittest.main()

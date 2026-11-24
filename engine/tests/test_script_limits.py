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


if __name__ == "__main__":
    unittest.main()

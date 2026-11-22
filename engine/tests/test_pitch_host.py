"""Tests for engine.pitch_host composition, JSON escaping, allowlist, MIME types, and auth."""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import pitch_host
import server


class _StubGetHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising /pitch/ GET routes without sockets."""

    def __init__(self, path, headers=None):
        self.path = path
        self.command = "GET"
        self.rfile = io.BytesIO(b"")
        self.wfile = io.BytesIO()
        self.headers = dict(headers or {})
        self.status_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self.status_code = code
        self.wfile.write((message or "").encode("utf-8"))

    def body_bytes(self):
        return self.wfile.getvalue()


class PitchHostTests(unittest.TestCase):
    """Verify /pitch/ composed page, escaping, asset allowlist, MIME types, and auth."""

    def setUp(self):
        self._prev_token = server.ENGINE_TOKEN
        server.ENGINE_TOKEN = ""

    def tearDown(self):
        server.ENGINE_TOKEN = self._prev_token

    def test_01_composed_page_replaces_fonts_and_injects_payload_after_app_js(self):
        for path in ("/pitch/", "/pitch/index.html", "/pitch/?mode=present"):
            h = _StubGetHandler(path)
            h.do_GET()
            self.assertEqual(h.status_code, 200, f"Expected 200 for {path}")
            self.assertEqual(h.response_headers.get("Content-Type"), "text/html; charset=utf-8")
            self.assertEqual(h.response_headers.get("Cache-Control"), "no-store")
            html = h.body_bytes().decode("utf-8")

            self.assertNotIn("fonts.googleapis.com", html)
            self.assertNotIn("fonts.gstatic.com", html)
            self.assertEqual(
                len(re.findall(r'<link\s+rel="stylesheet"\s+href="fonts/fonts\.css">', html)),
                1,
            )
            self.assertIn(
                '<script src="app.js"></script><script type="application/json" id="magnm-pitch-data">',
                html,
            )
            self.assertIn('</script><script src="magnm-host.js"></script>', html)

            m = re.search(
                r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
                html,
                re.DOTALL,
            )
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            expected_mode = "present" if "mode=present" in path else "preview"
            self.assertEqual(payload["mode"], expected_mode)
            self.assertIsNone(payload["preset"])
            self.assertIsNone(payload["proposal_id"])
            self.assertEqual(payload["custom_items"], [])
            self.assertIn("w_front", payload["prices"])

    def test_02_payload_json_escapes_script_breakout_characters(self):
        payload = pitch_host.build_payload(
            mode="form",
            proposal={
                "id": "prop-1",
                "preset": {
                    "slug": "test-client",
                    "name": "</script><script>alert(1)</script> & \u2028\u2029",
                },
            },
            price_overrides={"w_front": 7500},
        )
        self.assertEqual(payload["mode"], "form")
        self.assertEqual(payload["prices"]["w_front"], 7500)

        html = pitch_host.compose_index_html(payload)
        m = re.search(
            r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m)
        raw_json = m.group(1)
        self.assertNotIn("</script>", raw_json)
        self.assertNotIn("<", raw_json)
        self.assertNotIn(">", raw_json)
        self.assertNotIn("&", raw_json)
        self.assertNotIn("\u2028", raw_json)
        self.assertNotIn("\u2029", raw_json)
        parsed = json.loads(raw_json)
        self.assertEqual(
            parsed["preset"]["name"],
            "</script><script>alert(1)</script> & \u2028\u2029",
        )

    def test_03_allowlisted_assets_and_mime_types(self):
        expected = [
            ("/pitch/style.css", "text/css; charset=utf-8"),
            ("/pitch/app.js", "application/javascript; charset=utf-8"),
            ("/pitch/presets.js", "application/javascript; charset=utf-8"),
            ("/pitch/logo.png", "image/png"),
            ("/pitch/vendor/jspdf.umd.min.js", "application/javascript; charset=utf-8"),
            ("/pitch/magnm-host.js", "application/javascript; charset=utf-8"),
            ("/pitch/fonts/fonts.css", "text/css; charset=utf-8"),
            ("/pitch/fonts/fonts/instrument-serif-latin-400-normal.woff2", "font/woff2"),
        ]
        for path, mime in expected:
            h = _StubGetHandler(path)
            h.do_GET()
            self.assertEqual(h.status_code, 200, f"Expected 200 for {path}")
            self.assertEqual(h.response_headers.get("Content-Type"), mime)
            self.assertGreater(len(h.body_bytes()), 0)

    def test_04_disallowed_files_and_traversal_return_404(self):
        disallowed = [
            "/pitch/RULES.md",
            "/pitch/catalog.json",
            "/pitch/PITCH_VERSION.json",
            "/pitch/scripts/check-rules.js",
            "/pitch/../README.md",
            "/pitch/%2e%2e/README.md",
            "/pitch/fonts/FONTS.md",
            "/pitch/fonts/OFL.txt",
            "/pitch/fonts/../tokens.css",
            "/pitch/app.js%00.png",
            "/pitch/vendor\\jspdf.umd.min.js",
            "/pitch/does-not-exist.js",
        ]
        for path in disallowed:
            h = _StubGetHandler(path)
            h.do_GET()
            self.assertEqual(h.status_code, 404, f"Expected 404 for {path}")

    def test_05_requires_auth_when_engine_token_is_set(self):
        server.ENGINE_TOKEN = "pitch-secret-token"
        for path in ("/pitch/", "/pitch/style.css", "/pitch/magnm-host.js", "/pitch/logo.png"):
            unauth = _StubGetHandler(path)
            unauth.do_GET()
            self.assertEqual(unauth.status_code, 401, f"Expected 401 for unauthenticated {path}")

        authed = _StubGetHandler("/pitch/", headers={"Cookie": "magnm_token=pitch-secret-token"})
        authed.do_GET()
        self.assertEqual(authed.status_code, 200)

    def test_06_frozen_paths_resolve_pitch_fonts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fonts_dir = os.path.join(tmpdir, "pitch-fonts", "fonts")
            os.makedirs(fonts_dir, exist_ok=True)
            css_file = os.path.join(tmpdir, "pitch-fonts", "fonts.css")
            with open(css_file, "w", encoding="utf-8") as f:
                f.write("/* frozen fonts */")
            with patch.object(sys, "frozen", True, create=True), patch.object(
                pitch_host.paths, "resource", side_effect=lambda *parts: os.path.join(tmpdir, *parts)
            ):
                got_css, got_dir = pitch_host.font_paths()
                self.assertEqual(got_css, css_file)
                self.assertEqual(got_dir, fonts_dir)

    def test_07_host_adapter_supports_sections_live_preview_pdf_and_present_escape(self):
        h = _StubGetHandler("/pitch/magnm-host.js")
        h.do_GET()
        self.assertEqual(h.status_code, 200)
        js = h.body_bytes().decode("utf-8")
        self.assertIn("buildPlannerSections", js)
        self.assertIn("magnm:pitch-preview-update", js)
        self.assertIn("magnm:pitch-pdf-saved", js)
        self.assertIn("magnm:pitch-exit-present", js)

    def test_08_mobile_bundle_has_strict_csp_data_fonts_and_no_network_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            written = pitch_host.build_mobile_bundle(tmpdir)
            files = written["files"]
            self.assertIn("index.html", files)
            self.assertIn("boot.js", files)
            self.assertIn("app.js", files)
            self.assertIn("magnm-host.js", files)
            self.assertIn("fonts/fonts.css", files)

            with open(os.path.join(tmpdir, "index.html"), "r", encoding="utf-8") as f:
                html_text = f.read()
            self.assertIn("http-equiv=\"Content-Security-Policy\"", html_text)
            self.assertIn("connect-src 'none'", html_text)
            self.assertIn("font-src data:", html_text)
            self.assertIn("fonts/fonts.css", html_text)
            self.assertIn("src=\"boot.js\"", html_text)
            self.assertNotIn("fonts.googleapis.com", html_text)
            self.assertNotIn("fonts.gstatic.com", html_text)
            self.assertNotIn("src=\"app.js\"", html_text)

            with open(os.path.join(tmpdir, "fonts", "fonts.css"), "r", encoding="utf-8") as f:
                fonts_css = f.read()
            self.assertIn("data:font/woff2;base64,", fonts_css)
            self.assertNotIn("http://", fonts_css)
            self.assertNotIn("https://", fonts_css)


if __name__ == "__main__":
    unittest.main()

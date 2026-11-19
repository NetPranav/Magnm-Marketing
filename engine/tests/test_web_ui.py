"""Tests for engine.web_ui static serving, path safety, headers, fallback, and auth."""

import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import server
import web_ui


class _StubGetHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising GET routes without sockets."""

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


class WebUiTests(unittest.TestCase):
    """Verify web_ui dist resolution, security checks, headers, fallback, and auth."""

    def setUp(self):
        self._prev_dist = os.environ.get("MAGNM_WEB_DIST")
        self._prev_token = server.ENGINE_TOKEN
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        self.dist_dir = os.path.join(self.tmpdir, "dist")
        self.assets_dir = os.path.join(self.dist_dir, "assets")
        os.makedirs(self.assets_dir, exist_ok=True)
        with open(os.path.join(self.dist_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>Magnm Studio</title><div id='root'></div>")
        with open(os.path.join(self.assets_dir, "app-1234.js"), "w", encoding="utf-8") as f:
            f.write("console.log('magnm');")
        with open(os.path.join(self.assets_dir, "style-1234.css"), "w", encoding="utf-8") as f:
            f.write("body{margin:0}")
        os.environ["MAGNM_WEB_DIST"] = self.dist_dir

    def tearDown(self):
        server.ENGINE_TOKEN = self._prev_token
        if self._prev_dist is None:
            os.environ.pop("MAGNM_WEB_DIST", None)
        else:
            os.environ["MAGNM_WEB_DIST"] = self._prev_dist
        self._tmp_obj.cleanup()

    def test_01_dist_dir_requires_index_html(self):
        """dist_dir() returns None when index.html is missing."""
        self.assertEqual(web_ui.dist_dir(), os.path.realpath(self.dist_dir))
        empty_dir = os.path.join(self.tmpdir, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        os.environ["MAGNM_WEB_DIST"] = empty_dir
        self.assertIsNone(web_ui.dist_dir())

    def test_02_resolve_rejects_traversal_nul_backslashes_and_symlink_escape(self):
        """resolve() rejects path traversal, NUL, backslashes, absolute paths, and symlink escapes."""
        outside_file = os.path.join(self.tmpdir, "secret.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("top secret")

        escape_link = os.path.join(self.dist_dir, "escape.txt")
        os.symlink(outside_file, escape_link)

        inside_link = os.path.join(self.assets_dir, "alias.js")
        os.symlink(os.path.join(self.assets_dir, "app-1234.js"), inside_link)

        bad_paths = [
            "../secret.txt",
            "/../secret.txt",
            "/assets/../../secret.txt",
            "/%2e%2e/secret.txt",
            "/assets/%2e%2e/%2e%2e/secret.txt",
            "/assets/app-1234.js\x00.png",
            "/assets/app-1234.js%00.png",
            "/assets\\app-1234.js",
            "/assets%5capp-1234.js",
            "//etc/passwd",
            "index.html",
            "/C:/Windows/win.ini",
            "/escape.txt",
            "/assets/",
            "/does-not-exist.js",
        ]
        for bad in bad_paths:
            self.assertIsNone(web_ui.resolve(bad), f"Expected None for unsafe path: {bad!r}")

        self.assertEqual(web_ui.resolve("/"), os.path.realpath(os.path.join(self.dist_dir, "index.html")))
        self.assertEqual(
            web_ui.resolve("/index.html"),
            os.path.realpath(os.path.join(self.dist_dir, "index.html")),
        )
        self.assertEqual(
            web_ui.resolve("/assets/app-1234.js"),
            os.path.realpath(os.path.join(self.assets_dir, "app-1234.js")),
        )
        self.assertEqual(
            web_ui.resolve("/assets/alias.js"),
            os.path.realpath(os.path.join(self.assets_dir, "app-1234.js")),
        )

    def test_03_mime_types_and_cache_headers(self):
        """Verify MIME mappings and Cache-Control headers for index.html and assets."""
        expected_mimes = {
            "index.html": "text/html; charset=utf-8",
            "app.js": "application/javascript; charset=utf-8",
            "mod.mjs": "application/javascript; charset=utf-8",
            "app.css": "text/css; charset=utf-8",
            "logo.svg": "image/svg+xml",
            "shot.png": "image/png",
            "favicon.ico": "image/x-icon",
            "manifest.json": "application/json; charset=utf-8",
            "font.woff2": "font/woff2",
            "font.woff": "font/woff",
            "app.js.map": "application/json; charset=utf-8",
            "robots.txt": "text/plain; charset=utf-8",
        }
        for filename, expected in expected_mimes.items():
            self.assertEqual(web_ui.mime_type(filename), expected)

        self.assertEqual(web_ui.cache_control("/"), "no-store")
        self.assertEqual(web_ui.cache_control("/index.html"), "no-store")
        self.assertEqual(
            web_ui.cache_control("/assets/app-1234.js"),
            "public, max-age=31536000, immutable",
        )

        h_index = _StubGetHandler("/")
        h_index.do_GET()
        self.assertEqual(h_index.status_code, 200)
        self.assertEqual(h_index.response_headers.get("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(h_index.response_headers.get("Cache-Control"), "no-store")
        self.assertIn(b"Magnm Studio", h_index.body_bytes())

        h_asset = _StubGetHandler("/assets/app-1234.js")
        h_asset.do_GET()
        self.assertEqual(h_asset.status_code, 200)
        self.assertEqual(
            h_asset.response_headers.get("Content-Type"),
            "application/javascript; charset=utf-8",
        )
        self.assertEqual(
            h_asset.response_headers.get("Cache-Control"),
            "public, max-age=31536000, immutable",
        )
        self.assertIn(b"console.log('magnm')", h_asset.body_bytes())

    def test_04_root_falls_back_to_legacy_dashboard_without_dist_and_legacy_route(self):
        """/ falls back to legacy dashboard when dist is absent; /legacy always serves it."""
        h_legacy = _StubGetHandler("/legacy")
        h_legacy.do_GET()
        self.assertEqual(h_legacy.status_code, 200)
        legacy_html = h_legacy.body_bytes().decode("utf-8")
        self.assertIn("MAGNM // PR &amp; Social Agency Command Center", legacy_html)
        self.assertIn("Try the new Studio", legacy_html)

        missing_dist = os.path.join(self.tmpdir, "missing")
        os.environ["MAGNM_WEB_DIST"] = missing_dist
        h_fallback = _StubGetHandler("/")
        h_fallback.do_GET()
        self.assertEqual(h_fallback.status_code, 200)
        fallback_html = h_fallback.body_bytes().decode("utf-8")
        self.assertIn("MAGNM // PR &amp; Social Agency Command Center", fallback_html)
        self.assertNotIn("Try the new Studio", fallback_html)

    def test_05_static_files_and_root_require_auth_when_token_set(self):
        """When ENGINE_TOKEN is set, /, /legacy, and /assets/* return 401 without valid auth."""
        server.ENGINE_TOKEN = "secret-web-token"

        for path in ("/", "/index.html", "/legacy", "/assets/app-1234.js"):
            unauth = _StubGetHandler(path)
            unauth.do_GET()
            self.assertEqual(unauth.status_code, 401, f"Expected 401 for unauthenticated {path}")
            self.assertEqual(json.loads(unauth.body_bytes().decode("utf-8")), {"error": "unauthorized"})

        authed_cookie = _StubGetHandler("/assets/app-1234.js", headers={"Cookie": "magnm_token=secret-web-token"})
        authed_cookie.do_GET()
        self.assertEqual(authed_cookie.status_code, 200)

        authed_header = _StubGetHandler("/", headers={"X-Magnm-Token": "secret-web-token"})
        authed_header.do_GET()
        self.assertEqual(authed_header.status_code, 200)
        self.assertIn(b"Magnm Studio", authed_header.body_bytes())

        # /api/nonexistent must still 404, never fall through to dist
        with open(os.path.join(self.dist_dir, "api-leak.txt"), "w", encoding="utf-8") as f:
            f.write("should not serve")
        h_api = _StubGetHandler("/api/missing", headers={"Cookie": "magnm_token=secret-web-token"})
        h_api.do_GET()
        self.assertEqual(h_api.status_code, 404)

    def test_06_frozen_mode_uses_resource_web_dir(self):
        """When frozen without MAGNM_WEB_DIST, dist_dir() checks paths.resource('web')."""
        os.environ.pop("MAGNM_WEB_DIST", None)
        frozen_web = os.path.join(self.tmpdir, "frozen_web")
        os.makedirs(frozen_web, exist_ok=True)
        with open(os.path.join(frozen_web, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>Frozen</title>")

        with patch.object(sys, "frozen", True, create=True), patch.object(
            web_ui.paths, "resource", return_value=frozen_web
        ) as mock_res:
            resolved = web_ui.dist_dir()
            mock_res.assert_called_once_with("web")
            self.assertEqual(resolved, os.path.realpath(frozen_web))

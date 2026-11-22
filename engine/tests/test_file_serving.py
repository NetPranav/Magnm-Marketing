"""Tests for serving stored files without letting them run as a page or leave their folder."""

import http.client
from http.server import ThreadingHTTPServer
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_serving
import paths
import server


class FileServingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        root_patch = patch.object(paths, "DATA_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def write(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    def test_active_types_are_downloads(self):
        for mime in ("text/html", "TEXT/HTML; charset=utf-8", "application/xhtml+xml", "application/xml", "text/xml",
                     "text/javascript", "application/rss+xml", "image/unknown+xml", "", None):
            headers = file_serving.safe_headers('a"\r\nX: y.html', mime)
            self.assertEqual(headers["Content-Type"], "application/octet-stream", mime)
            self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
            self.assertNotIn("\n", headers["Content-Disposition"])
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_svg_is_sandboxed_without_scripts(self):
        policy = file_serving.safe_headers("a.svg", "image/svg+xml")["Content-Security-Policy"]
        self.assertTrue(policy.startswith("sandbox;"))
        self.assertNotIn("allow-scripts", policy)

    def test_media_and_text_stay_inline(self):
        for mime in ("image/png", "image/webp", "video/mp4", "application/pdf"):
            headers = file_serving.safe_headers("a", mime)
            self.assertEqual(headers["Content-Type"], mime)
            self.assertNotIn("Content-Disposition", headers)
        self.assertEqual(file_serving.safe_headers("a.md", "text/markdown")["Content-Type"], "text/plain; charset=utf-8")

    def test_private_and_outside_paths_refused(self):
        for parts in ((".env",), ("state", "team_session.json"), ("outputs", "posts", ".env.local")):
            self.assertIsNone(file_serving.servable_path(self.write(*parts)), parts)
        other = self.write("outputs", "secret.txt")
        self.assertIsNone(file_serving.servable_path(other, "outputs", "posts"))
        self.assertIsNone(file_serving.servable_path(os.path.join(self.root, "..", "x")))
        good = self.write("outputs", "posts", "p1", "card.png")
        self.assertEqual(file_serving.servable_path(good, "outputs", "posts"), os.path.realpath(good))

    def test_route_serves_safely_and_refuses_traversal(self):
        self.write("outputs", "posts", "p1", "page.html")
        self.write("outputs", "secret.txt")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

        def get(path, headers):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            return resp

        with patch.object(server, "ENGINE_TOKEN", ""):
            for headers in ({}, {"Range": "bytes=0-0"}):
                resp = get("/api/asset/p1/page.html", headers)
                self.assertEqual(resp.getheader("Content-Type"), "application/octet-stream")
                self.assertTrue(resp.getheader("Content-Disposition").startswith("attachment"))
            self.assertEqual(get("/api/asset/../secret.txt", {}).status, 404)


if __name__ == "__main__":
    unittest.main()

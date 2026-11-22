"""Tests for what agent tools may read and where research may connect."""

import os
import socket
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paths
import private_files
from research import egress, fetch
from tools import core


class PrivateFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)

    def write(self, rel, data=b"x"):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_secret_like_files_are_private(self):
        for rel in (".env", ".env.local", "api/PROD.env", "k/server.pem", ".git/config", ".ssh/config", "id_rsa",
                    ".npmrc", "credentials.json", "a/team_session.json"):
            self.assertTrue(private_files.is_private(self.write(rel), self.root), rel)
        for rel in ("README.md", "src/main.py", ".env.example", "docs/demo.png"):
            self.assertFalse(private_files.is_private(self.write(rel), self.root), rel)

    def test_engine_state_is_private_inside_a_connected_repo(self):
        with patch.object(paths, "DATA_ROOT", self.root):
            self.assertTrue(private_files.is_private(self.write("state/notes.md"), self.root))

    def test_read_repo_file_refuses_secrets_and_links_to_them(self):
        secret = self.write(".env.local", b"FAKE=canary")
        self.write("README.md", b"hello")
        names = [".env.local"]
        if os.name != "nt":
            os.symlink(secret, os.path.join(self.root, "innocent.txt"))
            names.append("innocent.txt")
        repo = {"id": "r", "kind": "local", "local_path": self.root, "name": "R"}
        ctx = types.SimpleNamespace(session_id=None, agent_id="researcher", confidential=False, mark_confidential=lambda: None)
        with patch.object(core.repos, "get_repo", return_value=repo):
            for name in names:
                self.assertNotIn("canary", str(core.read_repo_file(ctx, "r", name)), name)
            self.assertEqual(core.read_repo_file(ctx, "r", "README.md")["content"], "hello")

    def test_only_real_images_count_as_images(self):
        self.assertFalse(private_files.is_image(self.write("a.png", b"SECRET=1")))
        self.assertTrue(private_files.is_image(self.write("b.png", b"\x89PNG\r\n\x1a\n0000")))


class EgressTests(unittest.TestCase):
    def test_non_public_addresses_refused(self):
        for ip in ("127.0.0.1", "10.0.0.1", "100.101.102.103", "169.254.169.254", "0.0.0.0", "::1", "::ffff:127.0.0.1",
                   "64:ff9b::7f00:1", "2002:7f00:1::", "not-an-ip"):
            self.assertTrue(fetch.is_disallowed_address(ip), ip)
        for ip in ("8.8.8.8", "2606:4700:4700::1111"):
            self.assertFalse(fetch.is_disallowed_address(ip), ip)

    def test_connection_checks_the_address_it_uses(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]
        with patch.object(egress.socket, "getaddrinfo", return_value=answers), \
                patch.object(egress.socket, "create_connection") as connect:
            with self.assertRaises(fetch.SSRFError):
                egress.create_public_connection(("rebound.example", 80))
        connect.assert_not_called()

    def test_proxy_refuses_loopback_for_http_and_tunnels(self):
        with egress.GuardProxy() as proxy:
            port = int(proxy.url.rsplit(":", 1)[1])
            for request in (b"GET http://127.0.0.1:5050/api/account HTTP/1.1\r\n\r\n",
                            b"CONNECT 100.101.102.103:8788 HTTP/1.1\r\n\r\n"):
                with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
                    sock.sendall(request)
                    self.assertIn(b"403", sock.recv(200))


if __name__ == "__main__":
    unittest.main()

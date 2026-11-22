"""Tests for the check that keeps other websites away from the local engine."""

from email.message import Message
import http.client
from http.server import ThreadingHTTPServer
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import request_guard
import server

HOST = "127.0.0.1:5050"
OWN = "http://127.0.0.1:5050"
EVIL = "https://attacker.example"


def check(method="GET", target="/api/account", *pairs):
    headers = Message()
    for name, value in pairs:
        headers[name] = value
    return request_guard.is_trusted(method, target, headers)


def navigation(site, dest="document"):
    return (("Host", HOST), ("Sec-Fetch-Site", site), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", dest))


class RequestGuardRuleTests(unittest.TestCase):
    def test_own_page_and_local_tools_pass(self):
        self.assertTrue(check("GET", "/api/account", ("Host", HOST)))
        self.assertTrue(check("POST", "/api/account", ("Host", HOST)))
        self.assertTrue(check("POST", "/api/x"))
        self.assertTrue(check("POST", "/api/x", ("Host", HOST), ("Origin", OWN)))
        self.assertTrue(check("POST", "/api/x", ("Host", HOST), ("Origin", OWN), ("Sec-Fetch-Site", "same-origin")))
        self.assertTrue(check("GET", "/", *navigation("none")))
        for host in ("localhost:8787", "LOCALHOST", "[::1]:5050"):
            self.assertTrue(check("GET", "/", ("Host", host)))

    def test_vite_dev_proxy_passes(self):
        self.assertTrue(check("POST", "/api/x", ("Host", "127.0.0.1:8787"), ("Origin", "http://localhost:5173"),
                              ("Sec-Fetch-Site", "same-origin")))

    def test_rebinding_hosts_refused_even_when_same_origin(self):
        for host in ("attacker.example:5050", "127.0.0.1.attacker.example", "localhost.attacker.example",
                     "evil@127.0.0.1", "0.0.0.0:5050", ""):
            self.assertFalse(check("GET", "/api/x", ("Host", host), ("Sec-Fetch-Site", "same-origin")), host)

    def test_foreign_origins_refused(self):
        for origin in (EVIL, "null", "http://127.0.0.1:3000", "http://localhost:5050", OWN + ".attacker.example", ""):
            self.assertFalse(check("POST", "/api/x", ("Host", HOST), ("Origin", origin)), origin)

    def test_cross_site_and_other_local_ports_refused(self):
        for site in ("cross-site", "same-site", "unexpected"):
            for method in ("GET", "POST", "DELETE"):
                self.assertFalse(check(method, "/api/x", ("Host", HOST), ("Sec-Fetch-Site", site)))

    def test_other_sites_may_only_navigate_to_entry_pages(self):
        for target in ("/", "/?token=abc", "/auth/callback?s=1&code=2"):
            self.assertTrue(check("GET", target, *navigation("cross-site")), target)
        for target in ("/api/account", "/pitch/", "//x/../api/account", "/auth/callback/../../api/x"):
            self.assertFalse(check("GET", target, *navigation("cross-site")), target)
        self.assertFalse(check("GET", "/", *navigation("cross-site", dest="iframe")))
        self.assertFalse(check("POST", "/", *navigation("cross-site")))

    def test_duplicate_headers_refused(self):
        self.assertFalse(check("GET", "/", ("Host", HOST), ("Host", "attacker.example")))
        self.assertFalse(check("POST", "/", ("Host", HOST), ("Origin", OWN), ("Origin", EVIL)))


class RequestGuardServerTests(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=headers)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()

    def test_every_method_refused_before_its_handler_runs(self):
        handler = server.CommandCenterHandler
        with patch.object(handler, "do_GET") as get, patch.object(handler, "do_POST") as post, \
                patch.object(handler, "do_DELETE") as delete:
            for method in ("GET", "POST", "DELETE", "PUT", "OPTIONS"):
                self.assertEqual(self.request(method, "/api/reminders", {"Origin": EVIL}), 403, method)
            self.assertEqual(self.request("GET", "/api/health", {"Host": f"attacker.example:{self.port}"}), 403)
        for mocked in (get, post, delete):
            mocked.assert_not_called()

    def test_own_requests_still_served(self):
        self.assertEqual(self.request("GET", "/api/health", {}), 200)
        self.assertEqual(self.request("GET", "/api/health", {"Origin": f"http://127.0.0.1:{self.port}"}), 200)


if __name__ == "__main__":
    unittest.main()

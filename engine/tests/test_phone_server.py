"""Comprehensive security, pairing, API, confidentiality, and lifecycle tests for engine/phone_server.py and phone_pairing.py."""

import base64
from datetime import datetime, timedelta, timezone
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_chat
import ai_gateway
import db
import events
import main as engine_main
import notifications
import paths
import phone_pairing
import phone_server
import pitch
import pitch_host
import reminders
import results_extract
import server
import team_auth
import weekly_summary


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02"
    b"\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class PhoneServerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

        for mod_patch in (
            patch.object(paths, "DATA_ROOT", self.root),
        ):
            mod_patch.start()
            self.addCleanup(mod_patch.stop)

        paths.ensure_data_root()
        self.db_path = os.path.join(self.root, "state", "magnm.db")
        db.set_db_path(self.db_path)
        db.init_db()
        self.addCleanup(db.close_connection)
        self.addCleanup(db.reset_connections)
        self.addCleanup(lambda: db.set_db_path(None))

        self.clock = _FakeClock()
        self.pairing = phone_pairing.PairingManager(now_fn=self.clock)
        self.manager = phone_server.PhoneServerManager(
            port=0,
            address_resolver=lambda: "127.0.0.1",
            allow_loopback=True,
            pairing_manager=self.pairing,
        )
        # Ensure tests never leak threads.
        self.addCleanup(self._cleanup_threads)

        # Default product rules: add a confidential product alongside TeaserApp & Cero.
        rules = {
            "version": 1,
            "show_levels": {
                "everything": "All features and visuals",
                "text_only": "Text only",
                "teasers_only": "Teasers only",
                "nothing": "Do not mention",
            },
            "products": [
                {
                    "id": "cero",
                    "name": "Cero",
                    "status": "released",
                    "show": "everything",
                    "blocked_terms": [],
                    "confidential": False,
                },
                {
                    "id": "teaserapp",
                    "name": "TeaserApp",
                    "status": "in_development",
                    "show": "teasers_only",
                    "blocked_terms": ["sparkle", "ribbon"],
                    "confidential": False,
                },
                {
                    "id": "secretproj",
                    "name": "ProjectNightfall",
                    "status": "secret",
                    "show": "nothing",
                    "blocked_terms": ["nightfall_internal"],
                    "confidential": True,
                },
            ],
        }
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        self.rules_file = os.path.join(self.root, "config", "product_rules.json")
        with open(self.rules_file, "w", encoding="utf-8") as f:
            json.dump(rules, f)

        # Signed-in user A by default
        self.current_user = {"user_id": "user-alpha", "email": "alpha@example.com", "expires_at": 9999999999}
        sess_patch = patch.object(team_auth, "get_session_info", side_effect=lambda: self.current_user)
        sess_patch.start()
        self.addCleanup(sess_patch.stop)

    def _cleanup_threads(self):
        self.manager.stop(timeout=2.0)
        phone_server.stop(timeout=2.0)
        for th in threading.enumerate():
            if th.name == "magnm-phone-server" and th.is_alive():
                self.fail("Phone server thread outlived test")

    def _start_phone_server(self):
        self.manager.enable()
        host, port = self.manager.bound_address()
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)
        return host, port

    def _phone_req(self, method, path, body=None, headers=None, host_header=None, content_type="application/json"):
        host, port = self.manager.bound_address()
        req_headers = {}
        if host_header is not None:
            req_headers["Host"] = host_header
        else:
            req_headers["Host"] = f"{host}:{port}"
        if headers:
            req_headers.update(headers)
        raw_body = None
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                raw_body = bytes(body)
            else:
                raw_body = json.dumps(body).encode("utf-8")
            if content_type is not None:
                req_headers.setdefault("Content-Type", content_type)
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.putrequest(method, path, skip_host=True)
            for k, v in req_headers.items():
                conn.putheader(k, v)
            if raw_body is not None:
                conn.putheader("Content-Length", str(len(raw_body)))
            elif method == "POST":
                conn.putheader("Content-Length", "0")
            conn.endheaders()
            if raw_body is not None:
                try:
                    conn.send(raw_body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            resp = conn.getresponse()
            data = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.getheaders()}
            return resp.status, resp_headers, data
        finally:
            conn.close()

    def _pair_device(self, device_name="Pixel 9"):
        host, port = self.manager.bound_address()
        started = self.manager.start_pairing()
        parsed = phone_pairing.parse_qr_payload(started["qr_payload"])
        status, _, data = self._phone_req(
            "POST",
            "/phone/v1/pair",
            body={"code": parsed["code"], "device_name": device_name},
        )
        self.assertEqual(status, 200)
        pair_resp = json.loads(data.decode("utf-8"))
        dev = self.pairing.approve_request(pair_resp["request_id"])
        status, _, poll_data = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": pair_resp["request_id"], "poll_secret": pair_resp["poll_secret"]},
        )
        self.assertEqual(status, 200)
        poll_json = json.loads(poll_data.decode("utf-8"))
        return dev["id"], poll_json["device_token"], parsed

    # --- 1. Listener binding, Tailscale, defaults, lifecycle ---

    def test_01_off_by_default_and_never_binds_non_tailscale_address(self):
        self.assertFalse(self.manager.is_enabled())
        self.assertFalse(self.manager.start_if_enabled())
        self.assertFalse(self.manager.is_running())

        for bad_ip in ("0.0.0.0", "192.168.1.10", "10.0.0.5", "172.16.0.1", "8.8.8.8", "127.0.0.1", "::", ""):
            with self.subTest(bad_ip=bad_ip):
                mgr = phone_server.PhoneServerManager(
                    port=0,
                    address_resolver=lambda ip=bad_ip: ip,
                    allow_loopback=False,
                    pairing_manager=self.pairing,
                )
                with self.assertRaises(phone_server.PhoneServerError):
                    mgr.enable()
                self.assertFalse(mgr.is_enabled())
                self.assertFalse(mgr.is_running())

                with self.assertRaises(phone_server.PhoneServerError):
                    phone_server.PhoneHTTPServer((bad_ip or "0.0.0.0", 0), phone_server.PhoneHandler)

        # Tailscale 100.64.0.0/10 validation accepts valid CGNAT addresses
        self.assertEqual(phone_server.validate_bind_ip("100.64.0.1"), "100.64.0.1")
        self.assertEqual(phone_server.validate_bind_ip("100.127.255.254"), "100.127.255.254")

    def test_02_tailscale_resolver_missing_or_valid_uses_list_argv_and_timeout(self):
        with patch.object(subprocess, "run", side_effect=FileNotFoundError("no tailscale")) as m_run:
            with self.assertRaises(phone_server.PhoneServerError) as ctx:
                phone_server.resolve_tailscale_ipv4()
            self.assertEqual(ctx.exception.message, "Install and connect Tailscale on this laptop first.")
            m_run.assert_called_once()

        fake_proc = subprocess.CompletedProcess(
            args=["tailscale", "ip", "-4"],
            returncode=0,
            stdout="100.99.12.34\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=fake_proc) as m_run:
            ip = phone_server.resolve_tailscale_ipv4()
            self.assertEqual(ip, "100.99.12.34")
            args, kwargs = m_run.call_args
            self.assertEqual(args[0], ["tailscale", "ip", "-4"])
            self.assertEqual(kwargs.get("timeout"), 5)

        bad_proc = subprocess.CompletedProcess(
            args=["tailscale", "ip", "-4"],
            returncode=0,
            stdout="192.168.1.50\n",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=bad_proc):
            with self.assertRaises(phone_server.PhoneServerError):
                phone_server.resolve_tailscale_ipv4()

    def test_03_stops_at_once_when_disabled_and_wired_in_main_and_server(self):
        host, port = self._start_phone_server()
        self.assertTrue(self.manager.is_running())
        self.manager.disable()
        self.assertFalse(self.manager.is_enabled())
        self.assertFalse(self.manager.is_running())
        with self.assertRaises(OSError):
            with socket.create_connection((host, port), timeout=1):
                pass

        # Verify main.py and server.py invoke phone_server.start_if_enabled and phone_server.stop
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), "r", encoding="utf-8") as f:
            main_src = f.read()
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server.py"), "r", encoding="utf-8") as f:
            server_src = f.read()
        self.assertIn("phone_server.start_if_enabled()", main_src)
        self.assertIn("phone_server.stop(", main_src)
        self.assertIn("phone_server.start_if_enabled()", server_src)
        self.assertIn("phone_server.stop(", server_src)

    # --- 2. Request envelope, headers, body limits, concurrency, 404 on main API ---

    def test_04_refuses_origin_sec_fetch_wrong_host_outside_ip_bad_content_type_and_oversize(self):
        host, port = self._start_phone_server()
        _, token, _ = self._pair_device()
        auth = {"Authorization": f"Bearer {token}"}

        # Every response has no-store and nosniff, and no CORS
        st, hdrs, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("cache-control"), "no-store")
        self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")
        self.assertFalse(any(k.startswith("access-control-") for k in hdrs))

        # Origin rejected
        st, hdrs, _ = self._phone_req("GET", "/phone/v1/status", headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        self.assertEqual(hdrs.get("cache-control"), "no-store")
        self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")

        # Sec-Fetch-* rejected
        for sec_hdr in ("Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest", "Sec-Fetch-User"):
            st, _, _ = self._phone_req("GET", "/phone/v1/status", headers={**auth, sec_hdr: "same-origin"})
            self.assertEqual(st, 403, sec_hdr)

        # Wrong / missing / duplicate Host rejected
        for bad_host in ("localhost:" + str(port), f"100.64.0.1:{port}", f"{host}:8788", "evil.example"):
            st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth, host_header=bad_host)
            self.assertEqual(st, 403, bad_host)

        # Raw HTTP missing Host or duplicate Host rejected
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(b"GET /phone/v1/status HTTP/1.1\r\nConnection: close\r\n\r\n")
            raw_resp = sock.recv(4096)
            self.assertIn(b"403", raw_resp.split(b"\r\n", 1)[0])
        with socket.create_connection((host, port), timeout=5) as sock:
            req = (
                f"GET /phone/v1/status HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Host: {host}:{port}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("ascii")
            sock.sendall(req)
            raw_resp = sock.recv(4096)
            self.assertIn(b"403", raw_resp.split(b"\r\n", 1)[0])

        # Outside IP refused (bound to 100.64.0.10, client from 192.168.1.50 or 127.0.0.1)
        fake_server = type("S", (), {"bound_ip": "100.64.0.10", "bound_port": 8788})()
        handler = phone_server.PhoneHandler.__new__(phone_server.PhoneHandler)
        handler.server = fake_server
        self.assertTrue(handler._is_allowed_source_ip("100.64.0.55"))
        self.assertTrue(handler._is_allowed_source_ip("100.64.0.10"))
        self.assertFalse(handler._is_allowed_source_ip("192.168.1.50"))
        self.assertFalse(handler._is_allowed_source_ip("127.0.0.1"))
        self.assertFalse(handler._is_allowed_source_ip("10.0.0.1"))

        # Content-Type must be application/json
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/notifications/read",
            body={"all": True},
            headers=auth,
            content_type="text/plain",
        )
        self.assertEqual(st, 415)

        # 1 MB body limit on normal routes, 10 MB on results/extract
        over_1mb = b'{"all":true,"pad":"' + (b"a" * (1024 * 1024 + 50)) + b'"}'
        st, _, _ = self._phone_req("POST", "/phone/v1/notifications/read", body=over_1mb, headers=auth)
        self.assertEqual(st, 413)

        # > 1 MB and <= 10 MB is accepted by /phone/v1/results/extract body parser (not 413)
        mid_2mb = b'{"image_base64":"' + (b"AAAA" * (512 * 1024)) + b'"}'
        st, _, _ = self._phone_req("POST", "/phone/v1/results/extract", body=mid_2mb, headers=auth)
        self.assertEqual(st, 400)

        over_10mb = b'{"image_base64":"' + (b"A" * (10 * 1024 * 1024 + 50)) + b'"}'
        st, _, _ = self._phone_req("POST", "/phone/v1/results/extract", body=over_10mb, headers=auth)
        self.assertEqual(st, 413)

        # Verify socket timeout constant is 15s and max connections is 16
        self.assertEqual(phone_server.SOCKET_TIMEOUT_S, 15.0)
        self.assertEqual(phone_server.MAX_CONCURRENT_CONNECTIONS, 16)

    def test_05_every_main_api_path_on_phone_port_gives_404(self):
        self._start_phone_server()
        _, token, _ = self._pair_device()
        auth = {"Authorization": f"Bearer {token}"}

        forbidden_gets = [
            "/",
            "/index.html",
            "/legacy",
            "/pitch/",
            "/auth/callback",
            "/api/health",
            "/api/status",
            "/api/account",
            "/api/admin/team",
            "/api/coding/runs",
            "/api/coding/mcp",
            "/api/pitch/proposals",
            "/api/product_rules",
            "/api/repos",
            "/api/key_pool",
            "/api/phone",
            "/phone/v1/unknown",
        ]
        for path in forbidden_gets:
            st, _, _ = self._phone_req("GET", path, headers=auth)
            self.assertEqual(st, 404, f"GET {path} should be 404 on phone port")

        forbidden_posts = [
            "/api/save_env",
            "/api/save_env_config",
            "/api/publish",
            "/api/coding/runs",
            "/api/coding/mcp/call",
            "/api/account/invites",
            "/api/phone/enable",
            "/phone/v1/unknown",
        ]
        for path in forbidden_posts:
            st, _, _ = self._phone_req("POST", path, body={}, headers=auth)
            self.assertEqual(st, 404, f"POST {path} should be 404 on phone port")

        # DELETE is never allowed
        st, _, _ = self._phone_req("DELETE", "/phone/v1/drafts/cnt_1", headers=auth)
        self.assertEqual(st, 404)

    def test_06_concurrency_limit_16_connections(self):
        host, port = self._start_phone_server()
        # Hold 16 slots on the server semaphore directly to verify the 17th connection gets 503 immediately.
        httpd = self.manager._server
        for _ in range(16):
            self.assertTrue(httpd._conn_sem.acquire(blocking=False))
        try:
            st, hdrs, _ = self._phone_req("GET", "/phone/v1/status")
            self.assertEqual(st, 503)
            self.assertEqual(hdrs.get("cache-control"), "no-store")
            self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")
        finally:
            for _ in range(16):
                httpd._conn_sem.release()

    # --- 3. Pairing flow, expiry, rate limit, one-time token, sha256 storage, revoke, owner_user_id ---

    def test_07_pairing_qr_single_use_expiry_rate_limit_and_one_time_token(self):
        self._start_phone_server()
        started = self.manager.start_pairing()
        qr_payload = started["qr_payload"]
        self.assertTrue(qr_payload.startswith("magnm-pair:"))
        parsed = phone_pairing.parse_qr_payload(qr_payload)
        self.assertEqual(parsed["v"], 1)
        self.assertEqual(parsed["host"], "127.0.0.1")
        code = parsed["code"]
        raw_code_bytes = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
        self.assertEqual(len(raw_code_bytes), 32)

        # Bad code refused
        st, _, _ = self._phone_req("POST", "/phone/v1/pair", body={"code": "wrong", "device_name": "Phone"})
        self.assertEqual(st, 401)

        # Valid code succeeds and is consumed (single use)
        st, _, data = self._phone_req("POST", "/phone/v1/pair", body={"code": code, "device_name": "Pixel 9"})
        self.assertEqual(st, 200)
        pair_resp = json.loads(data.decode("utf-8"))
        req_id = pair_resp["request_id"]
        poll_secret = pair_resp["poll_secret"]

        # Reusing the same code fails
        st, _, _ = self._phone_req("POST", "/phone/v1/pair", body={"code": code, "device_name": "Clone"})
        self.assertEqual(st, 401)

        # Polling before approval gives NO token
        st, _, data = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": req_id, "poll_secret": poll_secret},
        )
        self.assertEqual(st, 200)
        poll_pending = json.loads(data.decode("utf-8"))
        self.assertEqual(poll_pending["status"], "pending")
        self.assertNotIn("device_token", poll_pending)

        # Wrong poll_secret is refused
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": req_id, "poll_secret": "wrong-secret"},
        )
        self.assertEqual(st, 401)

        # Approve on laptop
        dev = self.pairing.approve_request(req_id)
        self.assertEqual(dev["name"], "Pixel 9")
        self.assertNotIn("token_hash", dev)

        # First poll receives 256-bit token once
        st, _, data = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": req_id, "poll_secret": poll_secret},
        )
        self.assertEqual(st, 200)
        poll_ok = json.loads(data.decode("utf-8"))
        self.assertEqual(poll_ok["status"], "approved")
        token = poll_ok["device_token"]
        raw_tok_bytes = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        self.assertEqual(len(raw_tok_bytes), 32)

        # Second poll CANNOT receive the token again
        st, _, data = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": req_id, "poll_secret": poll_secret},
        )
        self.assertEqual(st, 410)
        self.assertNotIn("device_token", json.loads(data.decode("utf-8")))

        # DB stores ONLY sha256(token), never raw token
        conn = db.connect()
        rows = conn.execute("SELECT * FROM phone_devices").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["token_hash"], phone_pairing.hash_token(token))
        self.assertNotEqual(rows[0]["token_hash"], token)
        self.assertEqual(rows[0]["owner_user_id"], "user-alpha")

        # Expiration: code and pending request both expire after 5 minutes (300s)
        started2 = self.manager.start_pairing()
        code2 = phone_pairing.parse_qr_payload(started2["qr_payload"])["code"]
        st, _, data = self._phone_req("POST", "/phone/v1/pair", body={"code": code2, "device_name": "Late Phone"})
        self.assertEqual(st, 200)
        req2 = json.loads(data.decode("utf-8"))
        self.clock.advance(301)
        with self.assertRaises(phone_pairing.PairingError):
            self.pairing.approve_request(req2["request_id"])
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": req2["request_id"], "poll_secret": req2["poll_secret"]},
        )
        self.assertEqual(st, 404)

        # Rate limit: 5 bad codes per minute then refuse with 429
        self.pairing.reset()
        self.clock.advance(120)
        for _ in range(5):
            st, _, _ = self._phone_req("POST", "/phone/v1/pair", body={"code": "bad", "device_name": "Brute"})
            self.assertEqual(st, 401)
        st, _, _ = self._phone_req("POST", "/phone/v1/pair", body={"code": "bad", "device_name": "Brute"})
        self.assertEqual(st, 429)

    def test_08_revoked_and_other_account_devices_refused_with_401(self):
        self._start_phone_server()
        dev_id, token, _ = self._pair_device("Pixel 9")
        auth = {"Authorization": f"Bearer {token}"}

        # Works for owner_user_id == user-alpha
        st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 200)

        # Signed out -> 401
        self.current_user = None
        st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 401)

        # Another account signed in -> 401
        self.current_user = {"user_id": "user-beta", "email": "beta@example.com"}
        st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 401)

        # Restore owner -> works again until revoked
        self.current_user = {"user_id": "user-alpha", "email": "alpha@example.com"}
        st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 200)

        # Revoke works on the very next request
        phone_pairing.revoke_device(dev_id)
        st, _, _ = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 401)

    # --- 4. Confidential stubs & fail-closed, file traversal, inbox, agents, reminders, notifications, results ---

    def _call_as_phone(self, method, args):
        """Run one real handler and return what it would send to the phone."""
        handler = object.__new__(phone_server.PhoneHandler)
        handler.server = SimpleNamespace(check_agent_rate_limit=lambda _id: True)
        sent = []
        handler._send_json = lambda data, status=200: sent.append(data)
        getattr(handler, method)(*args)
        return json.dumps(sent)

    def test_review_comment_replies_are_filtered(self):
        item = db.create_content_item(content_id="c_replies", content_type="post", platform="linkedin", title="Cero post", body="Cero")
        thread = {"id": "1", "body": "Looks good", "replies": [{"id": "2", "body": "Hold for ProjectNightfall", "replies": []}]}
        payload = {"item": item, "comments": [thread]}
        with patch.object(phone_server.review_studio, "get_review", return_value=payload):
            out = self._call_as_phone("_handle_get_draft_detail", ("c_replies",))
        self.assertNotIn("ProjectNightfall", out)
        self.assertIn("Looks good", out)

    def test_agent_question_and_options_are_filtered(self):
        res = {
            "success": True, "response": "I need one answer.", "waiting_user": True,
            "question": "Which launch?", "options": ["Cero", "ProjectNightfall"],
        }
        with patch.object(agent_chat, "chat_with_agent", return_value=res):
            out = self._call_as_phone(
                "_handle_post_agent_message", ({"id": "d", "name": "Phone"}, "writer", {"text": "Plan a launch"})
            )
        self.assertNotIn("ProjectNightfall", out)

    def test_extracted_screenshot_is_filtered(self):
        res = {
            "proposal": {"id": "p1", "content_id": None, "metrics": {"views": 9}},
            "period": "7 days",
            "notes": "ProjectNightfall teaser",
        }
        params = {"image_base64": base64.b64encode(_PNG_BYTES).decode("ascii")}
        with patch.object(results_extract, "extract_from_image", return_value=res):
            out = self._call_as_phone("_handle_post_results_extract", ({"id": "d", "name": "Phone"}, params))
        self.assertNotIn("ProjectNightfall", out)

    def test_send_to_phone_never_claims_success_when_notifications_are_off(self):
        db.create_content_item(content_id="c_send", content_type="post", platform="linkedin", title="Cero post", body="Cero")
        with patch.object(phone_pairing, "list_devices", return_value=[{"id": "d"}]), \
                patch.object(notifications, "notify", return_value=None):
            with self.assertRaises(phone_server.PhoneApiError):
                phone_server.send_item_to_phone("content", "c_send")

    def test_one_refused_social_request_does_not_block_the_inbox(self):
        refused = phone_server._SocialError("X is disabled", status=403, code="platform_disabled")
        items = [{"client_id": "s1", "kind": "social_request", "platform": "x", "recipe": "search", "argument": "Cero"}]
        with patch.object(phone_server.social_service, "create_run_request", side_effect=refused):
            with self.assertRaises(phone_server.PhoneApiError) as caught:
                self._call_as_phone("_handle_post_inbox", ({"id": "d", "name": "Phone"}, {"items": items}))
        # The phone isolates and marks a bad item only on 400; anything else stops every item behind it.
        self.assertEqual(caught.exception.status, 400)

    def test_phone_never_gets_whatever_window_opened_after_the_check(self):
        registry = SimpleNamespace(current=lambda: None)
        with patch.object(phone_server.social_service, "get_default_service", return_value=SimpleNamespace(live_registry=registry)), \
                patch.object(phone_server.social_service, "get_status", return_value={"login": {"active": False}}), \
                patch.object(phone_server.social_service, "get_live_frame", return_value=b"LOGIN-FRAME") as frame, \
                patch.object(phone_server.social_service, "post_live_input", return_value={"ok": True}) as typed:
            for method, args in (("_handle_get_social_live_frame", ()), ("_handle_post_social_live_input", ({"action": "text", "text": "x"},))):
                with self.assertRaises(phone_server.PhoneApiError):
                    self._call_as_phone(method, args)
        frame.assert_not_called()
        typed.assert_not_called()

    def test_phone_live_calls_use_the_session_that_was_checked(self):
        challenge = {"kind": "challenge", "platform": "x", "run_id": None, "call": lambda cmd, arg: b"CHALLENGE-FRAME"}
        registry = SimpleNamespace(current=lambda: challenge)
        with patch.object(phone_server.social_service, "get_default_service", return_value=SimpleNamespace(live_registry=registry)), \
                patch.object(phone_server.social_service, "get_live_frame", return_value=b"LOGIN-FRAME"):
            out = self._call_as_phone("_handle_get_social_live_frame", ())
        self.assertIn(base64.b64encode(b"CHALLENGE-FRAME").decode("ascii"), out)

    def test_confidential_social_stub_has_no_start_hash(self):
        stub = phone_server._stub_social_run({"id": "r", "approval_hash": "h" * 64})
        self.assertFalse(stub.get("approval_hash"))

    def test_09_confidential_stubs_and_fail_closed_everywhere(self):
        self._start_phone_server()
        _, token, _ = self._pair_device()
        auth = {"Authorization": f"Bearer {token}"}

        # Create public and confidential records
        db.create_content_item(
            "cnt_pub",
            "post",
            "linkedin",
            "Public Cero Launch",
            "Cero is out today.",
            files=[{"kind": "draft", "path": "outputs/content/cnt_pub/v1.md"}],
        )
        os.makedirs(os.path.join(self.root, "outputs", "content", "cnt_pub"), exist_ok=True)
        with open(os.path.join(self.root, "outputs", "content", "cnt_pub", "v1.md"), "w", encoding="utf-8") as f:
            f.write("Public draft")

        db.create_agent_session({
            "id": "sess_conf",
            "agent_id": "writer",
            "status": "done",
            "messages": [{"role": "user", "content": "Secret work"}],
            "confidential": True,
            "created_at": "2026-11-22T00:00:00+00:00",
            "updated_at": "2026-11-22T00:00:00+00:00",
        })
        os.makedirs(os.path.join(self.root, "outputs", "content", "cnt_conf"), exist_ok=True)
        with open(os.path.join(self.root, "outputs", "content", "cnt_conf", "secret.txt"), "w", encoding="utf-8") as f:
            f.write("TOP SECRET")
        db.create_content_item(
            "cnt_conf",
            "post",
            "x",
            "ProjectNightfall Secret Launch",
            "Hidden ProjectNightfall roadmap details",
            product_id="secretproj",
            session_id="sess_conf",
            files=[{"kind": "draft", "path": "outputs/content/cnt_conf/secret.txt"}],
        )

        # Reminders (one public, one confidential)
        future_iso = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        reminders.create_reminder("Post public update", future_iso, content_id="cnt_pub")
        reminders.create_reminder("Secret ProjectNightfall check", future_iso, note="Nightfall details", content_id="cnt_conf")

        # Notifications (one public, one confidential)
        notifications.notify("reminder", "Public reminder", body="Cero post due", route="#/review/cnt_pub")
        notifications.notify("reminder", "ProjectNightfall alert", body="Secret text", route="#/review/cnt_conf")

        # Metric proposals (one public, one confidential)
        db.create_metric_proposal("mprop_pub", content_id="cnt_pub", platform="linkedin", metrics={"views": 100})
        db.create_metric_proposal("mprop_conf", content_id="cnt_conf", platform="x", metrics={"views": 999})

        # Weekly summary referencing confidential content
        db.upsert_weekly_summary(
            week_start="2026-11-16",
            data={"week_start": "2026-11-16", "posts": [{"content_id": "cnt_conf", "title": "ProjectNightfall Secret Launch"}]},
            highlights={"counts": {"posts_posted": 1}},
            narrative="ProjectNightfall did well",
            status="ready",
        )

        # Check drafts list & detail
        st, _, data = self._phone_req("GET", "/phone/v1/drafts?limit=20", headers=auth)
        self.assertEqual(st, 200)
        drafts = {d["id"]: d for d in json.loads(data.decode("utf-8"))["drafts"]}
        self.assertEqual(drafts["cnt_pub"]["title"], "Public Cero Launch")
        self.assertEqual(drafts["cnt_conf"]["title"], "Confidential item")
        self.assertTrue(drafts["cnt_conf"]["confidential"])
        self.assertEqual(drafts["cnt_conf"]["files"], [])
        self.assertNotIn("ProjectNightfall", json.dumps(drafts["cnt_conf"]))

        st, _, data = self._phone_req("GET", "/phone/v1/drafts/cnt_conf", headers=auth)
        self.assertEqual(st, 200)
        detail = json.loads(data.decode("utf-8"))
        self.assertTrue(detail["confidential"])
        self.assertEqual(detail["title"], "Confidential item")
        self.assertEqual(detail["files"], [])
        self.assertNotIn("body", detail)
        self.assertNotIn("ProjectNightfall", json.dumps(detail))

        # Confidential item files, comments, posted, results confirm refused
        st, _, _ = self._phone_req(
            "GET",
            "/phone/v1/files?content_id=cnt_conf&path=outputs/content/cnt_conf/secret.txt",
            headers=auth,
        )
        self.assertEqual(st, 403)
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/drafts/cnt_conf/comments",
            body={"body": "hi", "version": 1},
            headers=auth,
        )
        self.assertEqual(st, 403)
        st, _, _ = self._phone_req("POST", "/phone/v1/drafts/cnt_conf/posted", body={}, headers=auth)
        self.assertEqual(st, 403)
        st, _, _ = self._phone_req("POST", "/phone/v1/results/mprop_conf/confirm", body={}, headers=auth)
        self.assertEqual(st, 403)

        # Reminders stubbed
        st, _, data = self._phone_req("GET", "/phone/v1/reminders", headers=auth)
        self.assertEqual(st, 200)
        rems = json.loads(data.decode("utf-8"))["reminders"]
        self.assertNotIn("ProjectNightfall", json.dumps(rems))
        self.assertTrue(any(r.get("confidential") and r["title"] == "Confidential reminder" for r in rems))

        # Notifications stubbed
        st, _, data = self._phone_req("GET", "/phone/v1/notifications", headers=auth)
        self.assertEqual(st, 200)
        notifs = json.loads(data.decode("utf-8"))["notifications"]
        self.assertNotIn("ProjectNightfall", json.dumps(notifs))
        self.assertTrue(any(n.get("confidential") and n["title"] == "Confidential notification" for n in notifs))

        # Pending proposals stubbed
        st, _, data = self._phone_req("GET", "/phone/v1/results/pending", headers=auth)
        self.assertEqual(st, 200)
        props = {p["id"]: p for p in json.loads(data.decode("utf-8"))["proposals"]}
        self.assertTrue(props["mprop_conf"]["confidential"])
        self.assertNotIn("metrics", props["mprop_conf"])

        # Summary stubbed
        st, _, data = self._phone_req("GET", "/phone/v1/summary", headers=auth)
        self.assertEqual(st, 200)
        summ = json.loads(data.decode("utf-8"))["summary"]
        self.assertTrue(summ["confidential"])
        self.assertNotIn("ProjectNightfall", json.dumps(summ))
        self.assertNotIn("narrative", summ)

        # Confidential agent chat stubbed
        with patch.object(
            agent_chat,
            "chat_with_agent",
            return_value={
                "success": True,
                "waiting_user": False,
                "session_id": "sess_conf",
                "response": "Secret ProjectNightfall plan",
            },
        ):
            st, _, data = self._phone_req(
                "POST",
                "/phone/v1/agents/writer/message",
                body={"text": "Tell me about ProjectNightfall"},
                headers=auth,
            )
            self.assertEqual(st, 200)
            chat_res = json.loads(data.decode("utf-8"))
            self.assertTrue(chat_res["confidential"])
            self.assertNotIn("ProjectNightfall", chat_res["response"])

        # Fail closed when product_rules.json cannot be read
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{corrupt json")
        for path in ("/phone/v1/drafts", "/phone/v1/reminders", "/phone/v1/notifications", "/phone/v1/summary", "/phone/v1/results/pending"):
            st, _, _ = self._phone_req("GET", path, headers=auth)
            self.assertEqual(st, 503, path)

    def test_library_route_lists_assets_without_paths_and_serves_only_registered_files(self):
        import assets_manager
        self._start_phone_server()
        _, token, _ = self._pair_device()
        auth = {"Authorization": f"Bearer {token}"}
        asset = assets_manager.register_asset("logo.png", _PNG_BYTES, category="brand", description="Our mark")
        secret = assets_manager.register_asset("ProjectNightfall.png", _PNG_BYTES + b"x", category="brand")
        st, _, data = self._phone_req("GET", "/phone/v1/library")
        self.assertEqual(st, 401)
        st, _, data = self._phone_req("GET", "/phone/v1/library", headers=auth)
        self.assertEqual(st, 200)
        listing = json.loads(data.decode("utf-8"))["assets"]
        by_id = {a["id"]: a for a in listing}
        self.assertEqual(by_id[asset["id"]]["name"], "logo.png")
        self.assertTrue(by_id[asset["id"]]["fetchable"])
        self.assertEqual(by_id[secret["id"]], {"id": secret["id"], "confidential": True})
        self.assertNotIn("relative_path", data.decode("utf-8"))
        self.assertNotIn(self.root, data.decode("utf-8"))
        self.assertNotIn("Nightfall", data.decode("utf-8"))
        st, headers, body = self._phone_req("GET", f"/phone/v1/files?asset_id={asset['id']}", headers=auth)
        self.assertEqual((st, body), (200, _PNG_BYTES))
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        for bad, want in ((secret["id"], 403), ("asset_nope", 404), ("..%2F..%2Fstate%2Fmagnm.db", 400), ("%2Fetc%2Fpasswd", 400)):
            st, _, _ = self._phone_req("GET", f"/phone/v1/files?asset_id={bad}", headers=auth)
            self.assertEqual(st, want, bad)
        # An asset id never widens what content_id and path may reach.
        st, _, _ = self._phone_req("GET", f"/phone/v1/files?asset_id={asset['id']}&content_id=x&path=state/magnm.db", headers=auth)
        self.assertEqual(st, 400)

    def test_10_file_serving_traversal_private_and_200mb_limit_refused(self):
        self._start_phone_server()
        _, token, _ = self._pair_device()
        auth = {"Authorization": f"Bearer {token}"}

        os.makedirs(os.path.join(self.root, "outputs", "posts", "p1"), exist_ok=True)
        html_path = os.path.join(self.root, "outputs", "posts", "p1", "embed.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write("<script>alert(1)</script>")
        other_path = os.path.join(self.root, "outputs", "other.txt")
        with open(other_path, "w", encoding="utf-8") as f:
            f.write("not listed")
        with open(os.path.join(self.root, "state", "team_session.json"), "w", encoding="utf-8") as f:
            f.write('{"access_token":"secret"}')

        db.create_content_item(
            "cnt_files",
            "post",
            "linkedin",
            "File test",
            "Body",
            files=[
                {"kind": "card", "path": "outputs/posts/p1/embed.html"},
                {"kind": "card", "path": "state/team_session.json"},
            ],
        )

        # Listed HTML file is forced to attachment + nosniff + no-store
        st, hdrs, body = self._phone_req(
            "GET",
            "/phone/v1/files?content_id=cnt_files&path=outputs/posts/p1/embed.html",
            headers=auth,
        )
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("content-type"), "application/octet-stream")
        self.assertTrue(hdrs.get("content-disposition", "").startswith("attachment"))
        self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")
        self.assertEqual(hdrs.get("cache-control"), "no-store")
        self.assertIn(b"alert(1)", body)

        # Unlisted file refused
        st, _, _ = self._phone_req(
            "GET",
            "/phone/v1/files?content_id=cnt_files&path=outputs/other.txt",
            headers=auth,
        )
        self.assertEqual(st, 403)

        # Traversal refused
        st, _, _ = self._phone_req(
            "GET",
            "/phone/v1/files?content_id=cnt_files&path=outputs/posts/p1/../../../state/team_session.json",
            headers=auth,
        )
        self.assertEqual(st, 403)

        # Private state/ file refused even if listed on content item
        st, _, _ = self._phone_req(
            "GET",
            "/phone/v1/files?content_id=cnt_files&path=state/team_session.json",
            headers=auth,
        )
        self.assertEqual(st, 404)

        # > 200 MB file refused with 413
        with patch.object(os.path, "getsize", return_value=200 * 1024 * 1024 + 1):
            st, _, _ = self._phone_req(
                "GET",
                "/phone/v1/files?content_id=cnt_files&path=outputs/posts/p1/embed.html",
                headers=auth,
            )
            self.assertEqual(st, 413)

    def test_11_inbox_idempotent_max50_product_rules_and_reviewer(self):
        self._start_phone_server()
        _, token, _ = self._pair_device("Pixel 9")
        auth = {"Authorization": f"Bearer {token}"}

        # > 50 items refused
        too_many = [
            {"client_id": f"c_{i}", "kind": "idea", "title": f"Idea {i}", "body": "Body"}
            for i in range(51)
        ]
        st, _, _ = self._phone_req("POST", "/phone/v1/inbox", body={"items": too_many}, headers=auth)
        self.assertEqual(st, 400)

        payload = {
            "items": [
                {
                    "client_id": "cid-draft-1",
                    "kind": "draft",
                    "title": "TeaserApp sparkle preview",
                    "body": "Watch our TeaserApp sparkle demo today!",
                    "platform": "linkedin",
                },
                {
                    "client_id": "cid-note-1",
                    "kind": "note",
                    "title": "Competitor note",
                    "body": "Interesting pricing model",
                    "url": "https://example.com/post",
                },
            ]
        }
        st, _, data = self._phone_req("POST", "/phone/v1/inbox", body=payload, headers=auth)
        self.assertEqual(st, 200)
        res1 = json.loads(data.decode("utf-8"))["items"]
        self.assertEqual(len(res1), 2)
        self.assertFalse(res1[0]["deduplicated"])
        # Product rules & Reviewer applied
        self.assertTrue(len(res1[0]["warnings"]) > 0)
        self.assertEqual(res1[0]["review"]["verdict"], "warn")

        # Resending exact same items is idempotent
        st, _, data2 = self._phone_req("POST", "/phone/v1/inbox", body=payload, headers=auth)
        self.assertEqual(st, 200)
        res2 = json.loads(data2.decode("utf-8"))["items"]
        self.assertTrue(res2[0]["deduplicated"])
        self.assertEqual(res2[0]["id"], res1[0]["id"])
        self.assertTrue(res2[1]["deduplicated"])
        self.assertEqual(res2[1]["id"], res1[1]["id"])
        self.assertEqual(len(db.list_content_items(limit=50)), 1)
        self.assertEqual(len(db.list_research_notes(limit=50)), 1)

    def test_12_agent_message_4000_chars_rate_limit_results_and_no_secret_in_logs_events_activity(self):
        self._start_phone_server()
        ev_queue = events.subscribe()
        self.addCleanup(events.unsubscribe, ev_queue)

        _, token, parsed_qr = self._pair_device("Pixel 9")
        auth = {"Authorization": f"Bearer {token}"}

        # 4000-char limit
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/agents/writer/message",
            body={"text": "x" * 4001},
            headers=auth,
        )
        self.assertEqual(st, 400)

        # Agent list hides coding_engine / keys / settings
        st, _, data = self._phone_req("GET", "/phone/v1/agents", headers=auth)
        self.assertEqual(st, 200)
        agents_list = json.loads(data.decode("utf-8"))["agents"]
        self.assertTrue(len(agents_list) > 0)
        self.assertNotIn("coding_engine", agents_list[0])
        self.assertNotIn("model_info", agents_list[0])

        # 20 per 10 minutes per device rate limit
        secret_msg = "SUPER_SECRET_USER_MESSAGE_TEXT"
        with patch.object(
            agent_chat,
            "chat_with_agent",
            return_value={"success": True, "waiting_user": False, "session_id": "sess_1", "response": "OK"},
        ):
            for _ in range(20):
                st, _, _ = self._phone_req(
                    "POST",
                    "/phone/v1/agents/writer/message",
                    body={"text": secret_msg},
                    headers=auth,
                )
                self.assertEqual(st, 200)
            st, _, _ = self._phone_req(
                "POST",
                "/phone/v1/agents/writer/message",
                body={"text": "21st message"},
                headers=auth,
            )
            self.assertEqual(st, 429)

        # Draft comment + posted + reminder done + notifications read + results extract & confirm
        item = db.create_content_item("cnt_ok", "post", "linkedin", "Cero Post", "Hello world")
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/drafts/cnt_ok/comments",
            body={"body": "Looks sharp", "version": 1},
            headers=auth,
        )
        self.assertEqual(st, 200)
        st, _, _ = self._phone_req(
            "POST",
            "/phone/v1/drafts/cnt_ok/posted",
            body={"url": "https://linkedin.com/posts/123"},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self.assertEqual(db.get_content_item(item["id"])["status"], "posted")

        rem = reminders.create_reminder("Follow up", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        st, _, _ = self._phone_req("POST", f"/phone/v1/reminders/{rem['id']}/done", body={}, headers=auth)
        self.assertEqual(st, 200)

        notifications.notify("reminder", "Ping", body="Check")
        st, _, _ = self._phone_req("POST", "/phone/v1/notifications/read", body={"all": True}, headers=auth)
        self.assertEqual(st, 200)

        b64_img = base64.b64encode(_PNG_BYTES).decode("ascii")
        with patch.object(
            ai_gateway,
            "generate_completion_with_meta",
            return_value={
                "text": json.dumps({
                    "platform": "linkedin",
                    "metrics": {"views": 420, "likes": 15},
                    "visible_text_evidence": {"views": "420", "likes": "15"},
                })
            },
        ):
            st, _, data = self._phone_req(
                "POST",
                "/phone/v1/results/extract",
                body={"image_base64": b64_img, "content_id": "cnt_ok", "platform": "linkedin"},
                headers=auth,
            )
            self.assertEqual(st, 200)
            prop = json.loads(data.decode("utf-8"))["proposal"]
            self.assertEqual(prop["status"], "pending")
            self.assertEqual(db.list_metrics(content_id="cnt_ok"), [])

        st, _, data = self._phone_req("POST", f"/phone/v1/results/{prop['id']}/confirm", body={}, headers=auth)
        self.assertEqual(st, 200)
        self.assertEqual(len(db.list_metrics(content_id="cnt_ok")), 1)

        # Verify NO pairing code, NO raw device token, NO message text in events or activity log
        activity_lines = db.get_activity_lines(limit=100)
        activity_blob = "\n".join(activity_lines)
        self.assertIn("phone:Pixel 9", activity_blob)
        self.assertNotIn(token, activity_blob)
        self.assertNotIn(parsed_qr["code"], activity_blob)
        self.assertNotIn(secret_msg, activity_blob)
        self.assertNotIn("Looks sharp", activity_blob)

        collected_events = []
        while not ev_queue.empty():
            collected_events.append(ev_queue.get_nowait())
        events_blob = json.dumps(collected_events)
        self.assertNotIn(token, events_blob)
        self.assertNotIn(parsed_qr["code"], events_blob)
        self.assertNotIn(secret_msg, events_blob)

    def test_13_main_api_phone_endpoints_require_engine_token(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]

        def main_req(method, path, body=None, token=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            hdrs = {}
            if token:
                hdrs["X-Magnm-Token"] = token
            raw = None
            if body is not None:
                raw = json.dumps(body).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()

        with patch.object(server, "ENGINE_TOKEN", "secret-eng-tok"), patch.object(
            phone_server, "get_manager", return_value=self.manager
        ), patch.object(phone_pairing, "get_manager", return_value=self.pairing):
            st, _ = main_req("GET", "/api/phone")
            self.assertEqual(st, 401)
            st, body = main_req("GET", "/api/phone", token="secret-eng-tok")
            self.assertEqual(st, 200)
            self.assertFalse(body["enabled"])

            st, body = main_req("POST", "/api/phone/enable", body={}, token="secret-eng-tok")
            self.assertEqual(st, 200)
            self.assertTrue(body["enabled"])
            self.assertTrue(body["running"])

            st, body = main_req("POST", "/api/phone/pair/start", body={}, token="secret-eng-tok")
            self.assertEqual(st, 200)
            self.assertTrue(body["qr_payload"].startswith("magnm-pair:"))

            st, body = main_req("POST", "/api/phone/pair/cancel", body={}, token="secret-eng-tok")
            self.assertEqual(st, 200)
            self.assertFalse(body["pairing"]["active"])

            st, body = main_req("POST", "/api/phone/disable", body={}, token="secret-eng-tok")
            self.assertEqual(st, 200)
            self.assertFalse(body["enabled"])
            self.assertFalse(body["running"])

    def test_15_pitch_get_parity_confidential_limit_and_fail_closed(self):
        self._start_phone_server()
        _, tok, _ = self._pair_device("Pixel 9")
        auth = {"Authorization": f"Bearer {tok}"}

        defaults = pitch_host.parse_index_defaults()
        db.set_pitch_base_price("w_front", 7777, updated_by="admin")

        def make_preset(slug, title):
            return {
                "slug": slug,
                "name": title,
                "badge": "Web",
                "clientName": "Aria",
                "businessName": "Cero Labs",
                "title": title,
                "lead": "Public proposal",
                "whatWeDo": "Web engineering",
                "portfolio": "cero.example",
                "thisPlan": "Cero Plan",
                "architectureBlock": {
                    "heading": "Architecture",
                    "sub": "Layered system",
                    "roles": [{"label": "Web", "points": ["Landing page"]}],
                },
                "initialSelected": ["w_front", "b_db"],
                "sections": {"p2": ["w_front"], "p3": ["a_base"], "p4": ["b_db"], "p5": ["ai_bot"]},
            }

        pub_prop = db.create_pitch_proposal(
            proposal_id="prop_pub_1",
            slug="cero-pub-1",
            kind="proposal",
            status="draft",
            client_name="Aria",
            business_name="Cero Labs",
            contact={},
            preset=make_preset("cero-pub-1", "Cero Proposal"),
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            share_id="share_pub_1",
            pitch_commit="test-commit",
        )
        form_prop = db.create_pitch_proposal(
            proposal_id="prop_form_1",
            slug="cero-form-1",
            kind="quote_form",
            status="draft",
            client_name="Bea",
            business_name="Cero Studio",
            contact={},
            preset=make_preset("cero-form-1", "Cero Form"),
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            share_id="share_form_1",
            pitch_commit="test-commit",
        )
        final_prop = db.create_pitch_proposal(
            proposal_id="prop_final_1",
            slug="cero-final-1",
            kind="final",
            status="locked",
            client_name="Cy",
            business_name="Cero Ops",
            contact={},
            preset=make_preset("cero-final-1", "Cero Final"),
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            payment_plan=defaults["payment_plan"],
            payment_note=defaults["payment_note"],
            timeline=defaults["timeline"],
            share_id="share_final_1",
            pitch_commit="test-commit",
        )
        conf_record_prop = db.create_pitch_proposal(
            proposal_id="prop_conf_rec",
            slug="cero-conf-rec",
            kind="proposal",
            status="draft",
            client_name="Dan",
            business_name="Cero",
            contact={},
            preset=make_preset("cero-conf-rec", "Clean Name"),
            price_overrides={},
            custom_items=[],
            notes="ProjectNightfall launch brief",
            created_by="user",
            share_id="share_conf_rec",
            pitch_commit="test-commit",
        )

        for idx in range(98):
            db.create_pitch_proposal(
                proposal_id=f"prop_extra_{idx:03d}",
                slug=f"cero-extra-{idx:03d}",
                kind="proposal",
                status="draft",
                client_name=f"Client {idx}",
                business_name="Cero",
                contact={},
                preset=make_preset(f"cero-extra-{idx:03d}", f"Extra {idx}"),
                price_overrides={},
                custom_items=[],
                notes="",
                created_by="user",
                share_id=f"share_extra_{idx:03d}",
                pitch_commit="test-commit",
                created_at=f"2026-11-20T00:{idx // 60:02d}:{idx % 60:02d}+00:00",
            )

        st, _, data = self._phone_req("GET", "/phone/v1/pitch", headers=auth)
        self.assertEqual(st, 200)
        proposals = json.loads(data.decode("utf-8"))["proposals"]
        # Default quotation + max 100 non-default proposals = 101
        self.assertEqual(len(proposals), 101)
        by_id = {p["id"]: p for p in proposals}
        self.assertIn("prop_default", by_id)
        self.assertEqual(by_id[conf_record_prop["id"]], {"id": conf_record_prop["id"], "confidential": True})

        # Payload parity with pitch_host.serve across default, proposal, quote_form, and final
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]

        with patch.object(server, "ENGINE_TOKEN", "secret-eng-tok"):
            for pid in ("prop_default", pub_prop["id"], form_prop["id"], final_prop["id"]):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request(
                        "GET",
                        f"/pitch/?proposal={pid}&mode=present",
                        headers={"X-Magnm-Token": "secret-eng-tok"},
                    )
                    resp = conn.getresponse()
                    html_doc = resp.read().decode("utf-8")
                    self.assertEqual(resp.status, 200)
                finally:
                    conn.close()
                m = re.search(
                    r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
                    html_doc,
                    re.DOTALL,
                )
                self.assertIsNotNone(m, f"Missing embedded payload for {pid}")
                served_payload = json.loads(m.group(1).replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))
                self.assertEqual(by_id[pid]["payload"], served_payload)
                self.assertEqual(by_id[pid]["payload"]["prices"]["w_front"], 7777)

        # Payload-level confidentiality guard redacts even if the stored record seemed public
        orig_builder = pitch_host.build_proposal_payload

        def sneaky_payload(prop, mode="preview", price_overrides=None):
            res = orig_builder(prop, mode=mode, price_overrides=price_overrides)
            if prop.get("id") == pub_prop["id"]:
                res["custom_items"] = [{"g": "Web & SaaS", "n": "ProjectNightfall add-on", "d": "", "p": 100}]
            return res

        with patch.object(pitch_host, "build_proposal_payload", side_effect=sneaky_payload):
            st, _, data = self._phone_req("GET", "/phone/v1/pitch", headers=auth)
            self.assertEqual(st, 200)
            payload_redacted = {p["id"]: p for p in json.loads(data.decode("utf-8"))["proposals"]}
            self.assertEqual(payload_redacted[pub_prop["id"]], {"id": pub_prop["id"], "confidential": True})

        # Fail-closed when product_rules.json cannot be read
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{corrupt")
        st, _, _ = self._phone_req("GET", "/phone/v1/pitch", headers=auth)
        self.assertEqual(st, 503)

    def test_16_send_to_phone_auth_pairing_confidential_and_notifications(self):
        self._start_phone_server()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]

        def main_req(method, path, body=None, token=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            hdrs = {}
            if token:
                hdrs["X-Magnm-Token"] = token
            raw = None
            if body is not None:
                raw = json.dumps(body).encode("utf-8")
                hdrs["Content-Type"] = "application/json"
            try:
                conn.request(method, path, body=raw, headers=hdrs)
                resp = conn.getresponse()
                return resp.status, json.loads(resp.read().decode("utf-8"))
            finally:
                conn.close()

        pub_prop = db.create_pitch_proposal(
            proposal_id="prop_send_pub",
            slug="cero-send-pub",
            kind="proposal",
            status="draft",
            client_name="Aria",
            business_name="Cero Labs",
            contact={},
            preset={
                "slug": "cero-send-pub",
                "name": "Cero Send Pitch",
                "badge": "Web",
                "clientName": "Aria",
                "businessName": "Cero Labs",
                "title": "Cero Send Pitch",
                "lead": "Public",
                "whatWeDo": "Web",
                "portfolio": "cero.example",
                "thisPlan": "Plan",
                "architectureBlock": {"heading": "Arch", "sub": "Sub", "roles": []},
                "initialSelected": ["w_front"],
                "sections": {"p2": ["w_front"], "p3": [], "p4": [], "p5": []},
            },
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            share_id="share_send_pub",
            pitch_commit="test-commit",
        )
        conf_prop = db.create_pitch_proposal(
            proposal_id="prop_send_conf",
            slug="nightfall-send",
            kind="proposal",
            status="draft",
            client_name="Agent",
            business_name="ProjectNightfall",
            contact={},
            preset={"slug": "nightfall-send", "name": "Secret", "initialSelected": ["w_front"]},
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            share_id="share_send_conf",
            pitch_commit="test-commit",
        )
        db.create_content_item("cnt_send_pub", "post", "linkedin", "Cero Post", "Cero launch notes", product_id="cero")
        db.create_content_item("cnt_send_conf", "post", "x", "Nightfall", "Secret notes", product_id="secretproj")

        with patch.object(server, "ENGINE_TOKEN", "secret-eng-tok"):
            st, _ = main_req("POST", "/api/phone/send", body={"kind": "proposal", "id": pub_prop["id"]})
            self.assertEqual(st, 401)

            # Fails without a paired phone
            st, body = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "proposal", "id": pub_prop["id"]},
                token="secret-eng-tok",
            )
            self.assertEqual(st, 400)
            self.assertIn("Pair a phone", body["error"])

            # Pair a device, then test rejections and successes
            _, tok, _ = self._pair_device("Pixel 9")
            auth = {"Authorization": f"Bearer {tok}"}

            st, _ = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "proposal", "id": conf_prop["id"]},
                token="secret-eng-tok",
            )
            self.assertEqual(st, 403)

            st, _ = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "content", "id": "cnt_send_conf"},
                token="secret-eng-tok",
            )
            self.assertEqual(st, 403)

            st, ok_prop = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "proposal", "id": pub_prop["id"]},
                token="secret-eng-tok",
            )
            self.assertEqual(st, 200)
            self.assertEqual(ok_prop["route"], f"#/pitch/{pub_prop['id']}/present")
            self.assertEqual(ok_prop["notification"]["kind"], "sent_to_phone")

            st, ok_cnt = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "content", "id": "cnt_send_pub"},
                token="secret-eng-tok",
            )
            self.assertEqual(st, 200)
            self.assertEqual(ok_cnt["route"], "#/review/cnt_send_pub")
            self.assertEqual(ok_cnt["notification"]["kind"], "sent_to_phone")

        st, _, notif_data = self._phone_req("GET", "/phone/v1/notifications", headers=auth)
        self.assertEqual(st, 200)
        notifs = json.loads(notif_data.decode("utf-8"))["notifications"]
        routes = {n.get("route"): n for n in notifs}
        self.assertIn(f"#/pitch/{pub_prop['id']}/present", routes)
        self.assertIn("#/review/cnt_send_pub", routes)
        self.assertEqual(routes[f"#/pitch/{pub_prop['id']}/present"]["kind"], "sent_to_phone")

        # Fail-closed when product_rules.json cannot be read
        with open(self.rules_file, "w", encoding="utf-8") as f:
            f.write("{corrupt")
        with patch.object(server, "ENGINE_TOKEN", "secret-eng-tok"):
            st, _ = main_req(
                "POST",
                "/api/phone/send",
                body={"kind": "proposal", "id": pub_prop["id"]},
                token="secret-eng-tok",
            )
        self.assertEqual(st, 503)

    def test_14_migration_v22_idempotent(self):
        db._apply_migration_v22(db.connect())
        db._apply_migration_v22(db.connect())
        cols = {r["name"] for r in db.connect().execute("PRAGMA table_info(phone_devices)").fetchall()}
        self.assertEqual(
            cols,
            {"id", "name", "token_hash", "created_at", "last_seen_at", "last_ip", "revoked_at", "owner_user_id"},
        )


class PhoneReviewGapTests(unittest.TestCase):
    """Gaps found in security review."""

    def test_pairing_needs_a_signed_in_account(self):
        mgr = phone_pairing.PairingManager()
        started = mgr.start_pairing("100.64.0.1", 8788)
        code = phone_pairing.parse_qr_payload(started["qr_payload"])["code"]
        req = mgr.submit_pair_request(code, "Phone", "100.64.0.2")
        with patch.object(phone_pairing, "get_current_owner_user_id", return_value=""), \
                patch.object(phone_pairing.db, "create_phone_device") as create:
            with self.assertRaises(phone_pairing.PairingError):
                mgr.approve_request(req["request_id"])
        create.assert_not_called()

    def test_ownerless_device_never_authenticates(self):
        row = {"id": "d", "revoked_at": None, "owner_user_id": None}
        with patch.object(phone_pairing.db, "get_phone_device_by_token_hash", return_value=row), \
                patch.object(phone_pairing, "get_current_owner_user_id", return_value=""):
            self.assertIsNone(phone_pairing.authenticate_bearer("Bearer " + "x" * 43))

    def test_reply_withheld_after_reading_confidential_content(self):
        check = lambda text: "SecretProduct" in text

        def chat(tool):
            return {"messages": [
                {"role": "system", "content": "Rules: SecretProduct is confidential"},
                {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": tool}}]},
                {"role": "tool", "tool_call_id": "1", "content": "SecretProduct details"},
            ]}

        self.assertTrue(phone_server._chat_read_confidential(chat("read_content"), check, []))
        self.assertFalse(phone_server._chat_read_confidential(chat("read_product_facts"), check, []))

    def test_text_file_contents_are_checked(self):
        check = lambda text: "SecretProduct" in text
        with tempfile.TemporaryDirectory() as tmp:
            for body, expected in ((b"about SecretProduct", True), (b"about Cero", False)):
                path = os.path.join(tmp, "v1.md")
                with open(path, "wb") as f:
                    f.write(body)
                self.assertEqual(phone_server._file_is_confidential(path, check, []), expected)

    def test_name_after_a_serialized_newline_is_found(self):
        pattern = phone_server.product_rules._word_pattern("SecretProduct")
        check = lambda text: phone_server.product_rules.any_match([pattern], text)
        once = json.dumps({"notes": "Launch:\nSecretProduct"})
        twice = json.dumps({"tool_result": once})
        for text in (once, twice):
            self.assertTrue(phone_server._text_is_confidential(text, check, []), text)

    def test_one_device_cannot_lock_out_pairing_for_others(self):
        mgr = phone_pairing.PairingManager()
        started = mgr.start_pairing("100.64.0.1", 8788)
        for _ in range(phone_pairing.MAX_BAD_CODES_PER_MIN + 1):
            with self.assertRaises(phone_pairing.PairingError):
                mgr.submit_pair_request("wrong", "Other", "100.64.0.66")
        code = phone_pairing.parse_qr_payload(started["qr_payload"])["code"]
        self.assertTrue(mgr.submit_pair_request(code, "Phone", "100.64.0.2")["request_id"])

    def test_proposal_checked_as_a_whole(self):
        prop = {"id": "p", "content_id": None, "notes": "SecretProduct launch"}
        self.assertTrue(phone_server._proposal_is_confidential(prop, set(), set(), set(), lambda t: "SecretProduct" in t, []))


if __name__ == "__main__":
    unittest.main()

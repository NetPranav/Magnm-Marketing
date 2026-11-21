"""Tests for engine.admin_dashboard and /api/admin/* server routes (mocked HTTP only)."""

import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import admin_dashboard
import paths
import server
import team_auth

ADMIN_ID = "11111111-1111-1111-1111-111111111111"
MEMBER_ID = "22222222-2222-2222-2222-222222222222"
OTHER_ADMIN_ID = "33333333-3333-3333-3333-333333333333"


class _FakeHttpResponse:
    def __init__(self, body_obj=None, status=200):
        self.status = status
        if body_obj is None:
            self._raw = b""
        elif isinstance(body_obj, (bytes, bytearray)):
            self._raw = bytes(body_obj)
        else:
            self._raw = json.dumps(body_obj).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url, status, payload):
    raw = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, "Error", hdrs=None, fp=io.BytesIO(raw))


class AdminDashboardTests(unittest.TestCase):
    """Verify admin auth, presence boundaries, escaping, validation, routes, and error mapping."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        os.makedirs(os.path.join(self.tmpdir, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "state"), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_token = server.ENGINE_TOKEN
        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir

        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)

        team_auth.cancel_login()
        team_auth._set_last_error(None)

    def tearDown(self):
        server.ENGINE_TOKEN = self._prev_token
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        team_auth.cancel_login()
        team_auth._set_last_error(None)
        self._tmp_obj.cleanup()

    def _sign_in(self, user_id=ADMIN_ID, email="admin@example.com"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })

    def _start_server(self):
        server.ENGINE_TOKEN = "test-engine-token"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        return srv, srv.server_address[1]

    def _request_local(self, port, method, path, body=None, token="test-engine-token"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {}
        if token is not None:
            headers["X-Magnm-Token"] = token
        raw_body = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw_body = json.dumps(body).encode("utf-8")
        conn.request(method, path, body=raw_body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, json.loads(raw)

    def _make_urlopen(self, role="admin", active=True, user_id=ADMIN_ID, handler=None):
        calls = []

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            method = req.get_method()
            body = json.loads(req.data.decode("utf-8")) if req.data else None
            entry = {"method": method, "url": url, "body": body, "headers": dict(req.header_items())}
            calls.append(entry)

            if f"/rest/v1/profiles?id=eq.{user_id}&select=id,email,display_name,avatar_url,role,active" in url:
                return _FakeHttpResponse([{
                    "id": user_id,
                    "email": "user@example.com",
                    "display_name": "User",
                    "avatar_url": None,
                    "role": role,
                    "active": active,
                }])
            if handler:
                return handler(entry)
            return _FakeHttpResponse([])

        return calls, fake_urlopen

    def test_01_every_route_refuses_signed_out_member_and_deactivated_admin(self):
        routes = [
            ("GET", "/api/admin/team", None),
            ("GET", "/api/admin/activity", None),
            ("GET", "/api/admin/activity/daily", None),
            ("GET", f"/api/admin/members/{MEMBER_ID}/records", None),
            ("GET", f"/api/admin/members/{MEMBER_ID}/records/content_item/c1", None),
            ("GET", f"/api/admin/members/{MEMBER_ID}/previews", None),
            ("GET", f"/api/admin/members/{MEMBER_ID}/pitch", None),
            ("GET", "/api/admin/storage", None),
            ("GET", "/api/admin/audit", None),
            ("POST", f"/api/admin/members/{MEMBER_ID}/role", {"role": "admin"}),
            ("POST", f"/api/admin/members/{MEMBER_ID}/active", {"active": False}),
        ]

        srv, port = self._start_server()
        try:
            # 1. Signed out -> 401
            for method, path, body in routes:
                status, data = self._request_local(port, method, path, body=body)
                self.assertEqual(status, 401, f"{method} {path} should return 401 when signed out")
                self.assertIn("error", data)

            # 2. Signed-in active member -> 403 "Admins only."
            self._sign_in(MEMBER_ID, "member@example.com")
            _, member_urlopen = self._make_urlopen(role="member", active=True, user_id=MEMBER_ID)
            with patch("team_auth.urllib.request.urlopen", side_effect=member_urlopen):
                for method, path, body in routes:
                    status, data = self._request_local(port, method, path, body=body)
                    self.assertEqual(status, 403, f"{method} {path} should return 403 for member")
                    self.assertEqual(data["error"], "Admins only.")

            # 3. Deactivated admin -> 403 "Admins only."
            self._sign_in(ADMIN_ID, "admin@example.com")
            _, deact_urlopen = self._make_urlopen(role="admin", active=False, user_id=ADMIN_ID)
            with patch("team_auth.urllib.request.urlopen", side_effect=deact_urlopen):
                for method, path, body in routes:
                    status, data = self._request_local(port, method, path, body=body)
                    self.assertEqual(status, 403, f"{method} {path} should return 403 for deactivated admin")
                    self.assertEqual(data["error"], "Admins only.")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_02_presence_rules_at_time_boundaries(self):
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
        iso = lambda delta_s: (now - timedelta(seconds=delta_s)).isoformat()

        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "working", "last_seen_at": iso(180)}], now=now),
            "working",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "working", "last_seen_at": iso(181)}], now=now),
            "idle",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "online", "last_seen_at": iso(180)}], now=now),
            "online",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "online", "last_seen_at": iso(181)}], now=now),
            "idle",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "online", "last_seen_at": iso(1800)}], now=now),
            "idle",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "online", "last_seen_at": iso(1801)}], now=now),
            "offline",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "offline", "last_seen_at": iso(10)}], now=now),
            "offline",
        )
        self.assertEqual(
            admin_dashboard.derive_presence(
                [
                    {"status": "idle", "last_seen_at": iso(60)},
                    {"status": "working", "last_seen_at": iso(90)},
                ],
                now=now,
            ),
            "working",
        )
        self.assertEqual(
            admin_dashboard.derive_presence([{"status": "working", "last_seen_at": iso(5)}], active=False, now=now),
            "offline",
        )

    def test_03_team_join_and_running_agents(self):
        self._sign_in(ADMIN_ID)
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)

        def handler(entry):
            url = entry["url"]
            if "/rest/v1/profiles?" in url:
                return _FakeHttpResponse([
                    {
                        "id": ADMIN_ID,
                        "email": "admin@example.com",
                        "display_name": "Pranav",
                        "avatar_url": "https://example.com/a.png",
                        "role": "admin",
                        "active": True,
                        "created_at": "2026-11-01T00:00:00Z",
                        "last_seen_at": (now - timedelta(minutes=10)).isoformat(),
                    },
                    {
                        "id": MEMBER_ID,
                        "email": "maya@example.com",
                        "display_name": "Maya",
                        "avatar_url": None,
                        "role": "member",
                        "active": True,
                        "created_at": "2026-11-02T00:00:00Z",
                        "last_seen_at": (now - timedelta(minutes=2)).isoformat(),
                    },
                ])
            if "/rest/v1/device_status?" in url:
                return _FakeHttpResponse([
                    {
                        "owner_id": MEMBER_ID,
                        "device_id": "dev_1",
                        "device_kind": "desktop",
                        "status": "working",
                        "running_agents": ["writer", "researcher"],
                        "app_version": "0.1.0",
                        "last_seen_at": (now - timedelta(seconds=30)).isoformat(),
                    },
                    {
                        "owner_id": ADMIN_ID,
                        "device_id": "dev_2",
                        "device_kind": "desktop",
                        "status": "online",
                        "running_agents": ["cmo"],
                        "app_version": "0.1.0",
                        "last_seen_at": (now - timedelta(minutes=15)).isoformat(),
                    },
                ])
            if "/rest/v1/activity_events?" in url:
                return _FakeHttpResponse([
                    {"owner_id": MEMBER_ID, "ts": (now - timedelta(seconds=15)).isoformat()},
                    {"owner_id": ADMIN_ID, "ts": (now - timedelta(hours=1)).isoformat()},
                ])
            return _FakeHttpResponse([])

        _, urlopen = self._make_urlopen(handler=handler)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            res = admin_dashboard.get_team(now=now)

        self.assertEqual(res["current_user_id"], ADMIN_ID)
        by_id = {m["id"]: m for m in res["members"]}
        self.assertEqual(by_id[MEMBER_ID]["presence"], "working")
        self.assertEqual(by_id[MEMBER_ID]["running_agents"], ["writer", "researcher"])
        self.assertEqual(by_id[MEMBER_ID]["app_version"], "0.1.0")
        self.assertEqual(by_id[ADMIN_ID]["presence"], "idle")
        # Stale >3m device should not report running agents as currently running
        self.assertEqual(by_id[ADMIN_ID]["running_agents"], [])

    def test_04_query_escaping_validation_and_limit_capping(self):
        self._sign_in(ADMIN_ID)
        calls, urlopen = self._make_urlopen(handler=lambda _entry: _FakeHttpResponse([]))

        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            # Escaping q with %, _, *, ,, (, ), ../
            admin_dashboard.list_member_records(
                MEMBER_ID,
                kind="content_item",
                q="a%b_c*d,e(f)g/../h",
                limit="999",
            )
            admin_dashboard.get_activity(member=MEMBER_ID, kind="outbox.pitch_1", limit="500")

        rec_calls = [c["url"] for c in calls if "/rest/v1/mirror_records?" in c["url"]]
        self.assertEqual(len(rec_calls), 1)
        rec_url = rec_calls[0]
        self.assertIn("limit=200", rec_url)
        # No data column in list select
        self.assertIn("select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at", rec_url)
        # Ensure special chars in q are escaped and URL-encoded
        self.assertIn("title=ilike.*a%5C%25b%5C_c%5C%2Ad%5C%2Ce%5C%28f%5C%29g%2F..%2Fh*", rec_url)

        act_calls = [c["url"] for c in calls if "/rest/v1/activity_events?" in c["url"]]
        self.assertEqual(len(act_calls), 1)
        self.assertIn("limit=200", act_calls[0])
        self.assertIn("kind=eq.outbox.pitch_1", act_calls[0])

        # Invalid UUID -> 400
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            for bad_uuid in ("not-a-uuid", "../etc", f"{MEMBER_ID},eq.1", "1234"):
                with self.assertRaises(admin_dashboard.AdminDashboardError) as ctx:
                    admin_dashboard.list_member_records(bad_uuid)
                self.assertEqual(ctx.exception.status, 400)
                with self.assertRaises(admin_dashboard.AdminDashboardError) as ctx2:
                    admin_dashboard.get_activity(member=bad_uuid)
                self.assertEqual(ctx2.exception.status, 400)

            # Unsafe kind / local_id / parent with %, ,, ), *, ../ -> 400
            for bad_val in ("a%b", "a,b", "a)b", "a*b", "../etc", "a/b"):
                with self.assertRaises(admin_dashboard.AdminDashboardError) as k_ctx:
                    admin_dashboard.get_activity(kind=bad_val)
                self.assertEqual(k_ctx.exception.status, 400)

                with self.assertRaises(admin_dashboard.AdminDashboardError) as l_ctx:
                    admin_dashboard.get_member_record_detail(MEMBER_ID, "content_item", bad_val)
                self.assertEqual(l_ctx.exception.status, 400)

                with self.assertRaises(admin_dashboard.AdminDashboardError) as p_ctx:
                    admin_dashboard.get_member_previews(MEMBER_ID, parent=bad_val)
                self.assertEqual(p_ctx.exception.status, 400)

    def test_05_daily_activity_and_record_children(self):
        self._sign_in(ADMIN_ID)
        now = datetime(2026, 11, 21, 15, 0, 0, tzinfo=timezone.utc)

        def handler(entry):
            url = entry["url"]
            if "/rest/v1/activity_events?" in url:
                return _FakeHttpResponse([
                    {"owner_id": MEMBER_ID, "ts": "2026-11-21T10:00:00Z"},
                    {"owner_id": MEMBER_ID, "ts": "2026-11-21T11:00:00Z"},
                    {"owner_id": ADMIN_ID, "ts": "2026-11-20T09:00:00Z"},
                ])
            if "/rest/v1/profiles?" in url:
                return _FakeHttpResponse([
                    {"id": ADMIN_ID, "email": "admin@example.com", "display_name": "Pranav", "avatar_url": None},
                    {"id": MEMBER_ID, "email": "maya@example.com", "display_name": "Maya", "avatar_url": None},
                ])
            if "kind=eq.content_item" in url:
                return _FakeHttpResponse([{
                    "id": "rec-1",
                    "owner_id": MEMBER_ID,
                    "kind": "content_item",
                    "local_id": "post-1",
                    "parent_local_id": None,
                    "title": "Launch post",
                    "data": {"type": "post", "platform": "linkedin", "status": "draft"},
                    "local_updated_at": "2026-11-21T10:00:00Z",
                    "synced_at": "2026-11-21T10:01:00Z",
                }])
            if "kind=in.(content_version,review_comment,metric,posted_link)" in url:
                return _FakeHttpResponse([
                    {"id": "c1", "kind": "content_version", "local_id": "v1", "parent_local_id": "post-1", "title": "v1", "local_updated_at": "2026-11-21T09:00:00Z", "synced_at": "2026-11-21T09:01:00Z"},
                    {"id": "c2", "kind": "review_comment", "local_id": "cm1", "parent_local_id": "post-1", "title": "Nice", "local_updated_at": "2026-11-21T09:10:00Z", "synced_at": "2026-11-21T09:11:00Z"},
                    {"id": "c3", "kind": "metric", "local_id": "m1", "parent_local_id": "post-1", "title": "linkedin", "local_updated_at": "2026-11-21T09:20:00Z", "synced_at": "2026-11-21T09:21:00Z"},
                    {"id": "c4", "kind": "posted_link", "local_id": "post-1", "parent_local_id": "post-1", "title": "Launch post", "local_updated_at": "2026-11-21T09:30:00Z", "synced_at": "2026-11-21T09:31:00Z"},
                ])
            if "kind=eq.chat_session" in url and "parent_local_id=eq.sess-1" in url:
                return _FakeHttpResponse([
                    {"id": "sub-1", "kind": "chat_session", "local_id": "sess-sub", "parent_local_id": "sess-1", "title": "Sub chat", "local_updated_at": "2026-11-21T08:30:00Z", "synced_at": "2026-11-21T08:31:00Z"},
                ])
            if "kind=eq.chat_session" in url:
                return _FakeHttpResponse([{
                    "id": "rec-2",
                    "owner_id": MEMBER_ID,
                    "kind": "chat_session",
                    "local_id": "sess-1",
                    "parent_local_id": None,
                    "title": "Main chat",
                    "data": {"agent_id": "writer", "messages": [{"role": "user", "text": "Hi"}]},
                    "local_updated_at": "2026-11-21T08:00:00Z",
                    "synced_at": "2026-11-21T08:01:00Z",
                }])
            return _FakeHttpResponse([])

        calls, urlopen = self._make_urlopen(handler=handler)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            daily = admin_dashboard.get_daily_activity(days=14, now=now)
            content_detail = admin_dashboard.get_member_record_detail(MEMBER_ID, "content_item", "post-1")
            chat_detail = admin_dashboard.get_member_record_detail(MEMBER_ID, "chat_session", "sess-1")

        self.assertEqual(len(daily["series"]), 14)
        by_date = {d["date"]: d for d in daily["series"]}
        self.assertEqual(by_date["2026-11-21"]["total"], 2)
        self.assertEqual(by_date["2026-11-21"]["by_member"][MEMBER_ID], 2)
        self.assertEqual(by_date["2026-11-20"]["by_member"][ADMIN_ID], 1)

        self.assertEqual(content_detail["record"]["data"]["platform"], "linkedin")
        self.assertEqual(
            [c["kind"] for c in content_detail["children"]],
            ["content_version", "review_comment", "metric", "posted_link"],
        )
        self.assertEqual(chat_detail["record"]["data"]["messages"][0]["text"], "Hi")
        self.assertEqual([c["local_id"] for c in chat_detail["children"]], ["sess-sub"])
        # Verify children queries do not select `data`
        child_urls = [c["url"] for c in calls if "parent_local_id=eq." in c["url"]]
        self.assertEqual(len(child_urls), 2)
        for cu in child_urls:
            self.assertIn("select=id,kind,local_id,parent_local_id,title,local_updated_at,synced_at", cu)

    def test_06_previews_signed_and_foreign_paths_skipped(self):
        self._sign_in(ADMIN_ID)

        def handler(entry):
            if "/rest/v1/media_previews?" in entry["url"]:
                return _FakeHttpResponse([
                    {
                        "id": "p-ok",
                        "owner_id": MEMBER_ID,
                        "local_source_id": "content:1:a.png",
                        "source_kind": "content_file",
                        "parent_local_id": "c1",
                        "kind": "thumbnail",
                        "storage_path": f"{MEMBER_ID}/thumbnail/abc.webp",
                        "content_type": "image/webp",
                        "bytes": 1200,
                    },
                    {
                        "id": "p-foreign",
                        "owner_id": MEMBER_ID,
                        "local_source_id": "content:1:evil.png",
                        "source_kind": "content_file",
                        "parent_local_id": "c1",
                        "kind": "thumbnail",
                        "storage_path": f"{ADMIN_ID}/thumbnail/secret.webp",
                        "content_type": "image/webp",
                        "bytes": 1200,
                    },
                    {
                        "id": "p-traversal",
                        "owner_id": MEMBER_ID,
                        "local_source_id": "content:1:bad.png",
                        "source_kind": "content_file",
                        "parent_local_id": "c1",
                        "kind": "thumbnail",
                        "storage_path": f"{MEMBER_ID}/../{ADMIN_ID}/secret.webp",
                        "content_type": "image/webp",
                        "bytes": 1200,
                    },
                ])
            if "/storage/v1/object/sign/previews/" in entry["url"]:
                self.assertEqual(entry["body"], {"expiresIn": 3600})
                return _FakeHttpResponse({"signedURL": f"/object/sign/previews/{MEMBER_ID}/thumbnail/abc.webp?token=sig1"})
            return _FakeHttpResponse([])

        _, urlopen = self._make_urlopen(handler=handler)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            res = admin_dashboard.get_member_previews(MEMBER_ID, parent="c1")

        self.assertEqual(len(res["previews"]), 1)
        self.assertEqual(res["previews"][0]["id"], "p-ok")
        self.assertEqual(
            res["previews"][0]["signed_url"],
            f"https://example.supabase.co/storage/v1/object/sign/previews/{MEMBER_ID}/thumbnail/abc.webp?token=sig1",
        )

    def test_07_pitch_storage_and_audit(self):
        self._sign_in(ADMIN_ID)

        def handler(entry):
            url = entry["url"]
            if "/rest/v1/published_proposals?" in url:
                return _FakeHttpResponse([{
                    "share_id": "SecretShareId1234567890",
                    "local_proposal_id": "prop-1",
                    "kind": "proposal",
                    "status": "live",
                    "payload": {
                        "contact": {"name": "Asha", "business": "Acme Co"},
                        "custom_items": [{"id": "c_audit", "n": "UX Audit"}],
                    },
                    "updated_at": "2026-11-21T10:00:00Z",
                    "created_at": "2026-11-20T10:00:00Z",
                    "expires_at": None,
                }])
            if "/rest/v1/quote_submissions?" in url:
                return _FakeHttpResponse([{
                    "id": "sub-1",
                    "share_id": "SecretShareId1234567890",
                    "choices": {"selected": ["w_front"], "custom_selected": ["c_audit"], "total_shown": 10000},
                    "contact": {"name": "Priya", "business": "Northstar", "phone": "999", "notes": "Call me"},
                    "created_at": "2026-11-21T11:00:00Z",
                    "seen_at": None,
                }])
            if "/rest/v1/rpc/storage_usage" in url:
                return _FakeHttpResponse({
                    "bytes": 900 * 1024 * 1024,
                    "files": 42,
                    "by_kind": {"video_preview": {"bytes": 800 * 1024 * 1024, "files": 12}},
                })
            if "/rest/v1/audit_log?" in url:
                return _FakeHttpResponse([{
                    "id": 7,
                    "actor_id": ADMIN_ID,
                    "action": "invite.created",
                    "target": "new@example.com",
                    "data": {"role": "member"},
                    "created_at": "2026-11-21T12:00:00Z",
                }])
            if "/rest/v1/profiles?" in url:
                return _FakeHttpResponse([
                    {"id": ADMIN_ID, "email": "admin@example.com", "display_name": "Pranav", "avatar_url": None},
                ])
            return _FakeHttpResponse([])

        _, urlopen = self._make_urlopen(handler=handler)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen), \
             patch("admin_dashboard._load_catalog_names", return_value={"w_front": "Web Frontend"}):
            pitch_res = admin_dashboard.get_member_pitch(MEMBER_ID)
            storage_res = admin_dashboard.get_storage()
            audit_res = admin_dashboard.get_audit()

        self.assertEqual(pitch_res["proposals"][0]["share_id"], "Secret")
        self.assertEqual(len(pitch_res["proposals"][0]["share_id"]), 6)
        self.assertEqual(pitch_res["proposals"][0]["client_name"], "Asha")
        self.assertEqual(pitch_res["proposals"][0]["business_name"], "Acme Co")
        self.assertEqual(pitch_res["submissions"][0]["share_id"], "Secret")
        self.assertEqual(pitch_res["submissions"][0]["items"], ["Web Frontend", "UX Audit"])
        self.assertEqual(pitch_res["submissions"][0]["total_shown"], 10000)

        self.assertEqual(storage_res["limit_bytes"], 1073741824)
        self.assertTrue(storage_res["warning"])
        self.assertGreaterEqual(storage_res["percent"], 80.0)

        self.assertEqual(audit_res["entries"][0]["actor_name"], "Pranav")
        self.assertEqual(audit_res["entries"][0]["action"], "invite.created")

    def test_08_role_active_changes_own_refusal_last_admin_and_network_error(self):
        self._sign_in(ADMIN_ID)

        def handler(entry):
            if entry["method"] == "PATCH" and f"id=eq.{OTHER_ADMIN_ID}" in entry["url"]:
                raise _http_error(
                    entry["url"],
                    400,
                    {"message": "Cannot demote or deactivate the last active admin"},
                )
            if entry["method"] == "PATCH" and f"id=eq.{MEMBER_ID}" in entry["url"]:
                return _FakeHttpResponse([{"id": MEMBER_ID, **entry["body"]}])
            return _FakeHttpResponse([])

        _, urlopen = self._make_urlopen(handler=handler)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
            # Own account refusal
            with self.assertRaises(admin_dashboard.AdminDashboardError) as own_role:
                admin_dashboard.update_member_role(ADMIN_ID, "member")
            self.assertEqual(own_role.exception.status, 400)
            self.assertEqual(own_role.exception.message, "Ask another admin to change your own role.")

            with self.assertRaises(admin_dashboard.AdminDashboardError) as own_act:
                admin_dashboard.update_member_active(ADMIN_ID, False)
            self.assertEqual(own_act.exception.status, 400)
            self.assertEqual(own_act.exception.message, "Ask another admin to change your own role.")

            # Last admin error mapped
            with self.assertRaises(admin_dashboard.AdminDashboardError) as last_ctx:
                admin_dashboard.update_member_role(OTHER_ADMIN_ID, "member")
            self.assertEqual(last_ctx.exception.status, 400)
            self.assertEqual(last_ctx.exception.message, "You can't remove the last admin.")

            # Successful role & active updates
            r_res = admin_dashboard.update_member_role(MEMBER_ID, "admin")
            self.assertEqual(r_res["member"]["role"], "admin")
            a_res = admin_dashboard.update_member_active(MEMBER_ID, False)
            self.assertFalse(a_res["member"]["active"])

        # Network error mapped cleanly
        def net_fail(_req, timeout=0):
            raise urllib.error.URLError("DNS failure")

        with patch("team_auth.urllib.request.urlopen", side_effect=net_fail):
            with self.assertRaises(admin_dashboard.AdminDashboardError) as net_ctx:
                admin_dashboard.get_team()
            self.assertEqual(net_ctx.exception.status, 502)
            self.assertEqual(net_ctx.exception.message, "Can't reach the team server. Check your connection.")

    def test_09_http_routes_end_to_end_as_admin(self):
        self._sign_in(ADMIN_ID, "admin@example.com")

        def handler(entry):
            url = entry["url"]
            if "/rest/v1/profiles?" in url and entry["method"] == "GET":
                return _FakeHttpResponse([
                    {
                        "id": ADMIN_ID,
                        "email": "admin@example.com",
                        "display_name": "Pranav",
                        "avatar_url": None,
                        "role": "admin",
                        "active": True,
                        "created_at": "2026-11-01T00:00:00Z",
                        "last_seen_at": "2026-11-21T10:00:00Z",
                    },
                    {
                        "id": MEMBER_ID,
                        "email": "maya@example.com",
                        "display_name": "Maya",
                        "avatar_url": None,
                        "role": "member",
                        "active": True,
                        "created_at": "2026-11-02T00:00:00Z",
                        "last_seen_at": "2026-11-21T10:05:00Z",
                    },
                ])
            if "/rest/v1/profiles?" in url and entry["method"] == "PATCH":
                return _FakeHttpResponse([{"id": MEMBER_ID, **entry["body"]}])
            if "/rest/v1/device_status?" in url:
                return _FakeHttpResponse([])
            if "/rest/v1/activity_events?" in url:
                return _FakeHttpResponse([
                    {"id": 1, "owner_id": MEMBER_ID, "ts": "2026-11-21T10:00:00Z", "kind": "activity", "agent_id": "writer", "summary": "Drafted", "data": {}},
                ])
            if "/rest/v1/mirror_records?" in url and "kind=eq.content_item" in url and "local_id=eq.item-1" in url:
                return _FakeHttpResponse([{
                    "id": "r1",
                    "owner_id": MEMBER_ID,
                    "kind": "content_item",
                    "local_id": "item-1",
                    "parent_local_id": None,
                    "title": "Hello",
                    "data": {"status": "draft"},
                    "local_updated_at": "2026-11-21T10:00:00Z",
                    "synced_at": "2026-11-21T10:01:00Z",
                }])
            if "/rest/v1/mirror_records?" in url:
                return _FakeHttpResponse([])
            if "/rest/v1/media_previews?" in url:
                return _FakeHttpResponse([])
            if "/rest/v1/published_proposals?" in url:
                return _FakeHttpResponse([])
            if "/rest/v1/quote_submissions?" in url:
                return _FakeHttpResponse([])
            if "/rest/v1/rpc/storage_usage" in url:
                return _FakeHttpResponse({"bytes": 1024, "files": 1, "by_kind": {}})
            if "/rest/v1/audit_log?" in url:
                return _FakeHttpResponse([])
            return _FakeHttpResponse([])

        _, urlopen = self._make_urlopen(role="admin", active=True, user_id=ADMIN_ID, handler=handler)
        srv, port = self._start_server()
        try:
            with patch("team_auth.urllib.request.urlopen", side_effect=urlopen):
                for path in (
                    "/api/admin/team",
                    "/api/admin/activity?limit=10",
                    "/api/admin/activity/daily?days=14",
                    f"/api/admin/members/{MEMBER_ID}/records?kind=content_item",
                    f"/api/admin/members/{MEMBER_ID}/records/content_item/item-1",
                    f"/api/admin/members/{MEMBER_ID}/previews",
                    f"/api/admin/members/{MEMBER_ID}/pitch",
                    "/api/admin/storage",
                    "/api/admin/audit",
                ):
                    st, body = self._request_local(port, "GET", path)
                    self.assertEqual(st, 200, f"GET {path} returned {st}: {body}")

                st_bad, _ = self._request_local(port, "GET", "/api/admin/members/not-uuid/records")
                self.assertEqual(st_bad, 400)

                st_role, role_body = self._request_local(port, "POST", f"/api/admin/members/{MEMBER_ID}/role", {"role": "admin"})
                self.assertEqual(st_role, 200)
                self.assertEqual(role_body["member"]["role"], "admin")

                st_act, act_body = self._request_local(port, "POST", f"/api/admin/members/{MEMBER_ID}/active", {"active": False})
                self.assertEqual(st_act, 200)
                self.assertFalse(act_body["member"]["active"])
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()

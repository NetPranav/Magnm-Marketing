"""Tests for engine.notifications, triggers, admin polling cursors, settings, and /api/notifications* routes."""

import io
import json
import os
import queue
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import ai_gateway
import cloud_sync
import db
import events
import key_pool
import notifications
import paths
import pitch
import pitch_links
import server
import team_auth
import team_knowledge


class _FakeHttpResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url, status, payload):
    raw = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, "Error", {}, io.BytesIO(raw))


class _StubHandler(server.CommandCenterHandler):
    def __init__(self, path, method="GET", body_dict=None, headers=None):
        self.path = path
        self.command = method
        raw = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        if body_dict is not None:
            hdrs.setdefault("Content-Length", str(len(raw)))
            hdrs.setdefault("Content-Type", "application/json")
        self.headers = hdrs
        self.status_code = None
        self.response_headers = {}

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        self.response_headers[keyword] = value

    def end_headers(self):
        pass

    def response_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class NotificationsTests(unittest.TestCase):
    """Verify notifications persistence, triggers, admin polling cursors, settings, and HTTP routes."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        for sub in ("config", "state", "research", "agents", "playbooks"):
            os.makedirs(os.path.join(self.tmpdir, sub), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_token = server.ENGINE_TOKEN
        self._prev_disable = os.environ.get("MAGNM_DISABLE_CLOUD_SYNC")
        self._prev_env_path = ai_gateway.ENV_PATH

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        ai_gateway.ENV_PATH = os.path.join(self.tmpdir, ".env")
        key_pool.reset()

        os.symlink(
            os.path.join(self._prev_resource_root, "vendor"),
            os.path.join(self.tmpdir, "vendor"),
        )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.tmpdir, "config", "product_rules.json"),
        )
        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)

        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        team_knowledge.notify_signed_out()
        self._reset_sync_globals()

    def tearDown(self):
        cloud_sync.stop(timeout=0.5)
        team_knowledge.notify_signed_out()
        self._reset_sync_globals()
        server.ENGINE_TOKEN = self._prev_token
        db.reset_connections()
        db.set_db_path(None)
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        ai_gateway.ENV_PATH = self._prev_env_path
        key_pool.reset()
        if self._prev_disable is None:
            os.environ.pop("MAGNM_DISABLE_CLOUD_SYNC", None)
        else:
            os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = self._prev_disable
        self._tmp_obj.cleanup()

    def _reset_sync_globals(self):
        with cloud_sync._lock:
            cloud_sync._running = False
            cloud_sync._last_sync_at = None
            cloud_sync._last_error = None
            cloud_sync._failure_count = 0
            cloud_sync._next_allowed_at = 0.0
            cloud_sync._pending_debounce_until = 0.0
            cloud_sync._immediate_requested = False

    def _sign_in(self, user_id="user-1", role="member", name="Member One"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": f"{user_id}@example.com",
        })
        self._current_role = role
        self._current_name = name

    def _mock_supabase(self, *, profiles=None, posted=None, drafts=None, key_labels=None, storage=None, handler_fn=None):
        profiles_list = list(profiles or [])
        posted_list = list(posted or [])
        drafts_list = list(drafts or [])
        labels_list = list(key_labels or [])
        storage_obj = dict(storage or {"bytes": 100, "files": 1, "by_kind": {}})
        reqs = []

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            method = req.get_method()
            body = json.loads(req.data.decode("utf-8")) if req.data else None
            reqs.append({"url": url, "method": method, "body": body})

            if handler_fn is not None:
                res = handler_fn({"url": url, "method": method, "body": body})
                if res is not None:
                    return res

            parsed = urllib.parse.urlparse(url)
            if "/rest/v1/profiles" in parsed.path:
                sess = team_auth.get_session_info() or {}
                uid = sess.get("user_id") or "user-1"
                if "select=id,email,display_name,avatar_url,role,active" in parsed.query:
                    return _FakeHttpResponse([{
                        "id": uid,
                        "email": f"{uid}@example.com",
                        "display_name": getattr(self, "_current_name", "User"),
                        "avatar_url": None,
                        "role": getattr(self, "_current_role", "member"),
                        "active": True,
                    }])
                return _FakeHttpResponse(profiles_list)

            if "/rest/v1/mirror_records" in parsed.path and method == "GET":
                qs = urllib.parse.parse_qs(parsed.query)
                kind_q = (qs.get("kind") or [""])[0]
                if kind_q == "eq.posted_link":
                    return _FakeHttpResponse(posted_list)
                if kind_q == "eq.content_item":
                    return _FakeHttpResponse(drafts_list)
                return _FakeHttpResponse([])

            if "/rest/v1/rpc/admin_ai_key_labels" in parsed.path:
                return _FakeHttpResponse(labels_list)

            if "/rest/v1/rpc/storage_usage" in parsed.path:
                return _FakeHttpResponse(storage_obj)

            if "/rest/v1/shared_knowledge" in parsed.path or "/rest/v1/pitch_base_prices" in parsed.path or "/rest/v1/team_directory" in parsed.path:
                return _FakeHttpResponse([])

            if "/rest/v1/quote_submissions" in parsed.path and method == "GET":
                return _FakeHttpResponse([])

            return _FakeHttpResponse(None, status=201)

        return reqs, fake_urlopen

    def test_01_notify_stores_publishes_respects_disabled_sanitizes_and_caps_200(self):
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        # Trimming and control characters
        raw_title = "Hello\x00\n\tWorld " + ("A" * 150)
        raw_body = "Line1\r\nLine2\x07 " + ("B" * 350)
        row = notifications.notify("quote_request", raw_title, body=raw_body, route="#/pitch")
        self.assertIsNotNone(row)
        self.assertLessEqual(len(row["title"]), 120)
        self.assertLessEqual(len(row["body"]), 300)
        self.assertNotIn("\n", row["title"])
        self.assertNotIn("\x00", row["title"])
        self.assertNotIn("\r", row["body"])
        self.assertNotIn("\x07", row["body"])

        ev = q.get(timeout=1.0)
        self.assertEqual(ev["type"], "notification")
        self.assertEqual(ev["data"]["id"], row["id"])

        # Disabled kind does not store or publish
        notifications.update_setting("quote_request", False, is_admin=False)
        skipped = notifications.notify("quote_request", "Should skip", body="nope")
        self.assertIsNone(skipped)
        with self.assertRaises(queue.Empty):
            q.get_nowait()

        # Re-enable and verify 200-row cap keeps newest 200
        notifications.update_setting("quote_request", True, is_admin=False)
        for i in range(215):
            notifications.notify(
                "quote_request",
                f"Item {i:03d}",
                created_at=f"2026-11-22T00:{i // 60:02d}:{i % 60:02d}Z",
            )
        listed = notifications.list_notifications(limit=200)
        self.assertEqual(len(listed["notifications"]), 200)
        self.assertEqual(listed["notifications"][0]["title"], "Item 214")
        self.assertEqual(listed["notifications"][-1]["title"], "Item 015")
        self.assertEqual(listed["unread_count"], 200)

    def test_02_quote_request_and_link_failed_triggers(self):
        self._sign_in("u-pitch", role="member", name="Priya")
        prop = pitch.create_proposal({
            "slug": "aero-prop",
            "kind": "proposal",
            "client_name": "Client",
            "business_name": "Aero Proposal",
        })

        # Quote request trigger
        def qr_handler(entry):
            if "/rest/v1/quote_submissions" in entry["url"] and entry["method"] == "GET":
                return _FakeHttpResponse([{
                    "id": "qr-1",
                    "owner_id": "u-pitch",
                    "share_id": prop["share_id"] if prop.get("share_id") else "abcdefghijklmnop",
                    "choices": {"selected": ["w_front"], "total_shown": 6000},
                    "contact": {"name": "Priya", "business": "Northstar", "phone": "555", "notes": ""},
                    "created_at": "2026-11-22T01:00:00Z",
                }])
            return None

        # Publish first so share_id is set and link_status is live
        _, opener = self._mock_supabase(handler_fn=qr_handler)
        with patch("urllib.request.urlopen", side_effect=opener):
            pitch_links.publish(prop["id"])
            count = pitch_links.poll_quote_requests("u-pitch")
        self.assertEqual(count, 1)

        notifs = notifications.list_notifications()["notifications"]
        qr_notifs = [n for n in notifs if n["kind"] == "quote_request"]
        self.assertEqual(len(qr_notifs), 1)
        self.assertEqual(qr_notifs[0]["title"], "New quote request")
        self.assertIn("Priya (Northstar)", qr_notifs[0]["body"])
        self.assertEqual(qr_notifs[0]["route"], "#/pitch")

        # Link failed after 3 consecutive republish failures
        time.sleep(0.01)
        pitch.update_proposal(prop["id"], {"client_name": "Aero Updated"})

        def fail_publish(entry):
            if "/rest/v1/published_proposals" in entry["url"] and entry["method"] == "POST":
                raise _http_error(entry["url"], 503, {"message": "Upstream down"})
            return None

        _, fail_opener = self._mock_supabase(handler_fn=fail_publish)
        with patch("urllib.request.urlopen", side_effect=fail_opener):
            self.assertFalse(cloud_sync.sync_once())
            self.assertFalse(cloud_sync.sync_once())
            # 2 failures -> no link_failed notification yet
            self.assertEqual([n for n in notifications.list_notifications()["notifications"] if n["kind"] == "link_failed"], [])
            # 3rd failure -> notifies
            self.assertFalse(cloud_sync.sync_once())

        link_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "link_failed"]
        self.assertEqual(len(link_notifs), 1)
        self.assertEqual(link_notifs[0]["title"], "A client link could not be updated")
        self.assertEqual(link_notifs[0]["route"], "#/pitch")

        # 4th failure does not duplicate; success resets streak
        with patch("urllib.request.urlopen", side_effect=fail_opener):
            self.assertFalse(cloud_sync.sync_once())
        self.assertEqual(len([n for n in notifications.list_notifications()["notifications"] if n["kind"] == "link_failed"]), 1)

        _, ok_opener = self._mock_supabase()
        with patch("urllib.request.urlopen", side_effect=ok_opener):
            self.assertTrue(cloud_sync.sync_once())

        # Fails 3 more times after recovery -> notifies again
        time.sleep(0.01)
        pitch.update_proposal(prop["id"], {"client_name": "Aero Updated 2"})
        with patch("urllib.request.urlopen", side_effect=fail_opener):
            cloud_sync.sync_once()
            cloud_sync.sync_once()
            cloud_sync.sync_once()
        self.assertEqual(len([n for n in notifications.list_notifications()["notifications"] if n["kind"] == "link_failed"]), 2)

    def test_03_ai_limit_90_and_100_once_per_day_and_proxy_429(self):
        # 90% triggers once per day
        status_90 = {
            "mode": "team",
            "today": {"requests": 180, "prompt_tokens": 0, "completion_tokens": 0},
            "limit": {"daily_requests": 200, "daily_tokens": 400000},
        }
        notifications.evaluate_ai_limit(status_90, today_utc="2026-11-22")
        notifications.evaluate_ai_limit(status_90, today_utc="2026-11-22")
        ai_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "ai_limit"]
        self.assertEqual(len(ai_notifs), 1)
        self.assertEqual(ai_notifs[0]["title"], "AI limit almost reached")
        self.assertEqual(ai_notifs[0]["route"], "#/settings")

        # Token 100% triggers once per day for level 100
        status_100 = {
            "mode": "team",
            "today": {"requests": 50, "prompt_tokens": 300000, "completion_tokens": 100000},
            "limit": {"daily_requests": 200, "daily_tokens": 400000},
        }
        notifications.evaluate_ai_limit(status_100, today_utc="2026-11-22")
        notifications.evaluate_ai_limit(status_100, today_utc="2026-11-22")
        ai_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "ai_limit"]
        self.assertEqual(len(ai_notifs), 2)
        self.assertEqual(ai_notifs[0]["title"], "AI limit reached")

        # Next day notifies again
        notifications.evaluate_ai_limit(status_90, today_utc="2026-11-23")
        ai_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "ai_limit"]
        self.assertEqual(len(ai_notifs), 3)

        # Proxy 429 quota error triggers 100% notification on a fresh day
        self._sign_in("u-ai", role="member")
        with patch("notifications._today_utc", return_value="2026-11-24"), patch(
            "urllib.request.urlopen",
            side_effect=_http_error("https://example.supabase.co/functions/v1/ai-proxy", 429, {
                "error": "Daily request limit reached",
                "reason": "daily_requests_limit",
            }),
        ):
            with self.assertRaises(ai_gateway.AINotConnected):
                ai_gateway._complete_team_proxy_messages([{"role": "user", "content": "hi"}], "test-model")
        ai_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "ai_limit"]
        self.assertEqual(len(ai_notifs), 4)
        self.assertEqual(ai_notifs[0]["title"], "AI limit reached")

        # GET /api/account/ai evaluates usage from Supabase and notifies 90% on a fresh day
        today_iso = time.strftime("%Y-%m-%d", time.gmtime())

        def ai_route_handler(entry):
            if f"/rest/v1/ai_usage_daily?owner_id=eq.u-ai&day=eq.{today_iso}" in entry["url"]:
                return _FakeHttpResponse([{"requests": 185, "prompt_tokens": 1000, "completion_tokens": 500}])
            if "/rest/v1/ai_limits" in entry["url"]:
                return _FakeHttpResponse([{"owner_id": None, "daily_requests": 200, "daily_tokens": 400000}])
            return None

        _, ai_opener = self._mock_supabase(handler_fn=ai_route_handler)
        server.ENGINE_TOKEN = "tok-ai"
        with patch.object(ai_gateway, "has_local_nim_keys", return_value=False), patch(
            "urllib.request.urlopen", side_effect=ai_opener
        ):
            h_ai = _StubHandler("/api/account/ai", method="GET", headers={"X-Magnm-Token": "tok-ai"})
            h_ai.do_GET()
            self.assertEqual(h_ai.status_code, 200)
        ai_notifs2 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "ai_limit"]
        self.assertEqual(len(ai_notifs2), 5)
        self.assertEqual(ai_notifs2[0]["title"], "AI limit almost reached")

    def test_04_team_knowledge_updated_and_sync_paused_triggers(self):
        # team_knowledge.updated for a member
        self._sign_in("member-1", role="member", name="Member")
        remote_kit = [{
            "key": "brand_kit",
            "data": {
                "version": 1,
                "files": {
                    "research/brand-direction.md": "# Brand Direction\nFrom team\n",
                    "research/reference-style.md": "# Reference Style\n",
                },
            },
            "updated_at": "2026-11-22T02:00:00Z",
            "updated_by": "admin-1",
        }]

        def tk_handler(entry):
            if "/rest/v1/shared_knowledge" in entry["url"]:
                return _FakeHttpResponse(remote_kit)
            if "/rest/v1/team_directory" in entry["url"]:
                return _FakeHttpResponse([{"id": "admin-1", "display_name": "Priya"}])
            return None

        _, opener = self._mock_supabase(handler_fn=tk_handler)
        with patch("urllib.request.urlopen", side_effect=opener):
            team_knowledge.sync("member-1", "member")

        tk_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "team_knowledge"]
        self.assertEqual(len(tk_notifs), 1)
        self.assertEqual(tk_notifs[0]["title"], "Brand kit updated by Priya")
        self.assertEqual(tk_notifs[0]["route"], "#/settings")

        # sync_paused after 30 minutes of failure
        notifications.record_sync_failure("Waiting for connection", now_ts=1000.0)
        self.assertEqual([n for n in notifications.list_notifications()["notifications"] if n["kind"] == "sync_paused"], [])
        notifications.record_sync_failure("Waiting for connection", now_ts=1000.0 + 1800.0)
        paused = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "sync_paused"]
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0]["title"], "Team sync is paused")
        self.assertEqual(paused[0]["body"], "Waiting for connection")
        self.assertEqual(paused[0]["route"], "#/settings")

        # Success resets failure tracking; unreadable product rules notify immediately
        notifications.record_sync_success()
        os.remove(os.path.join(self.tmpdir, "config", "product_rules.json"))
        _, ok_opener = self._mock_supabase()
        with patch("urllib.request.urlopen", side_effect=ok_opener):
            self.assertFalse(cloud_sync.sync_once())
        paused2 = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "sync_paused"]
        self.assertEqual(len(paused2), 2)
        self.assertIn("product rules", paused2[0]["body"].lower())

    def test_05_admin_polling_cursors_no_first_run_flood_confidential_key_and_storage(self):
        self._sign_in("admin-1", role="admin", name="Admin")

        old_profiles = [
            {"id": "admin-1", "email": "admin@example.com", "display_name": "Admin", "created_at": "2026-11-20T00:00:00Z"},
            {"id": "mem-1", "email": "mem1@example.com", "display_name": "Asha", "created_at": "2026-11-21T00:00:00Z"},
        ]
        old_posted = [{
            "id": "mr-post-old",
            "owner_id": "mem-1",
            "kind": "posted_link",
            "local_id": "c-old",
            "title": "Old launch post",
            "data": {"platform": "Instagram", "posted_url": "https://ig.example/1"},
            "local_updated_at": "2026-11-21T01:00:00Z",
            "synced_at": "2026-11-21T01:00:00Z",
        }]
        old_drafts = [{
            "id": "mr-draft-old",
            "owner_id": "mem-1",
            "kind": "content_item",
            "local_id": "d-old",
            "title": "Old draft",
            "data": {"status": "draft"},
            "local_updated_at": "2026-11-21T01:00:00Z",
            "synced_at": "2026-11-21T01:00:00Z",
        }]

        # 1st sync: only sets cursors for enabled kinds (member_joined, member_posted); member_draft is off by default
        _, opener1 = self._mock_supabase(
            profiles=old_profiles,
            posted=old_posted,
            drafts=old_drafts,
            key_labels=[{"label": "key_1", "last_status": 200}],
            storage={"bytes": 100, "files": 1, "by_kind": {}},
        )
        with patch("urllib.request.urlopen", side_effect=opener1):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(notifications.list_notifications()["notifications"], [])

        # 2nd sync: new member joins, new posted_link + confidential posted_link appear
        new_profiles = old_profiles + [
            {"id": "mem-2", "email": "rahul@example.com", "display_name": "Rahul", "created_at": "2026-11-22T01:00:00Z"},
        ]
        new_posted = old_posted + [
            {
                "id": "mr-post-new",
                "owner_id": "mem-1",
                "kind": "posted_link",
                "local_id": "c-new",
                "title": "Cero launch",
                "data": {"platform": "Instagram", "posted_url": "https://ig.example/2"},
                "local_updated_at": "2026-11-22T01:05:00Z",
                "synced_at": "2026-11-22T01:05:00Z",
            },
            {
                "id": "mr-post-conf",
                "owner_id": "mem-2",
                "kind": "posted_link",
                "local_id": "c-conf",
                "title": "Secret LazyLayout Teaser",
                "data": {"confidential": True, "platform": "X"},
                "local_updated_at": "2026-11-22T01:06:00Z",
                "synced_at": "2026-11-22T01:06:00Z",
            },
        ]
        _, opener2 = self._mock_supabase(
            profiles=new_profiles,
            posted=new_posted,
            drafts=old_drafts,
            key_labels=[{"label": "key_1", "last_status": 200}],
            storage={"bytes": 100, "files": 1, "by_kind": {}},
        )
        with patch("urllib.request.urlopen", side_effect=opener2):
            self.assertTrue(cloud_sync.sync_once())

        notifs2 = notifications.list_notifications()["notifications"]
        titles2 = [n["title"] for n in notifs2]
        self.assertIn("Rahul joined the team", titles2)
        self.assertIn("Asha posted on Instagram", titles2)
        self.assertIn("Rahul worked on a confidential item", titles2)
        conf_n = [n for n in notifs2 if "confidential item" in n["title"]][0]
        self.assertEqual(conf_n["body"], "")
        self.assertNotIn("Secret", conf_n["title"] + conf_n["body"])

        # Re-running same sync notifies nothing twice
        with patch("urllib.request.urlopen", side_effect=opener2):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(len(notifications.list_notifications()["notifications"]), 3)

        # Enabling member_draft: first run sets cursor only (no flood of old_drafts), next run notifies new draft
        notifications.update_setting("member_draft", True, is_admin=True)
        with patch("urllib.request.urlopen", side_effect=opener2):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual([n for n in notifications.list_notifications()["notifications"] if n["kind"] == "member_draft"], [])

        new_drafts = old_drafts + [{
            "id": "mr-draft-new",
            "owner_id": "mem-2",
            "kind": "content_item",
            "local_id": "d-new",
            "title": "Cero thread",
            "data": {"status": "draft"},
            "local_updated_at": "2026-11-22T02:00:00Z",
            "synced_at": "2026-11-22T02:00:00Z",
        }]
        _, opener3 = self._mock_supabase(
            profiles=new_profiles,
            posted=new_posted,
            drafts=new_drafts,
        )
        with patch("urllib.request.urlopen", side_effect=opener3):
            self.assertTrue(cloud_sync.sync_once())
        draft_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "member_draft"]
        self.assertEqual(len(draft_notifs), 1)
        self.assertEqual(draft_notifs[0]["title"], "Rahul started a draft")

        # key_failing: 401/403 label only, throttled to every 10 minutes
        t0 = time.time() + 5000.0
        _, key_opener = self._mock_supabase(
            key_labels=[{"label": "key_2", "last_status": 401, "last_error": "nvapi-secret-leak"}]
        )
        with patch("urllib.request.urlopen", side_effect=key_opener):
            notifications.poll_key_failing("admin-1", now_ts=t0)
            # Within 10 min -> skipped
            notifications.poll_key_failing("admin-1", now_ts=t0 + 200.0)
            # After 10 min, still 401 -> does not notify twice for same failure
            notifications.poll_key_failing("admin-1", now_ts=t0 + 700.0)
        key_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "key_failing"]
        self.assertEqual(len(key_notifs), 1)
        self.assertEqual(key_notifs[0]["title"], "A shared AI key is failing")
        self.assertEqual(key_notifs[0]["body"], "key_2")
        self.assertNotIn("nvapi-", key_notifs[0]["body"])

        # storage_full: >=80% once per crossing, throttled to every hour
        notifications.poll_storage_full("admin-1", now_ts=t0, storage_fn=lambda: {"percent": 82.0, "warning": True})
        notifications.poll_storage_full("admin-1", now_ts=t0 + 1000.0, storage_fn=lambda: {"percent": 90.0, "warning": True})
        notifications.poll_storage_full("admin-1", now_ts=t0 + 4000.0, storage_fn=lambda: {"percent": 85.0, "warning": True})
        stor_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "storage_full"]
        self.assertEqual(len(stor_notifs), 1)
        self.assertEqual(stor_notifs[0]["title"], "Team storage is 82% full")

        # Drops below 80%, then crosses 80% again after 1 hour -> notifies once more
        notifications.poll_storage_full("admin-1", now_ts=t0 + 8000.0, storage_fn=lambda: {"percent": 60.0, "warning": False})
        notifications.poll_storage_full("admin-1", now_ts=t0 + 12000.0, storage_fn=lambda: {"percent": 80.0, "warning": True})
        stor_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "storage_full"]
        self.assertEqual(len(stor_notifs), 2)
        self.assertEqual(stor_notifs[0]["title"], "Team storage is 80% full")

    def test_06_routes_token_gate_mark_read_and_admin_setting_refusal(self):
        server.ENGINE_TOKEN = "secret-engine-tok"
        self._sign_in("member-1", role="member", name="Member")

        # Unauthenticated request rejected
        h_unauth = _StubHandler("/api/notifications", method="GET")
        h_unauth.do_GET()
        self.assertEqual(h_unauth.status_code, 401)

        auth_headers = {"X-Magnm-Token": "secret-engine-tok"}

        n1 = notifications.notify("quote_request", "Quote 1", created_at="2026-11-22T01:00:00Z")
        n2 = notifications.notify("ai_limit", "AI 90%", created_at="2026-11-22T02:00:00Z")

        # GET /api/notifications?limit=1
        h_list = _StubHandler("/api/notifications?limit=1", method="GET", headers=auth_headers)
        h_list.do_GET()
        self.assertEqual(h_list.status_code, 200)
        body_list = h_list.response_json()
        self.assertEqual(len(body_list["notifications"]), 1)
        self.assertEqual(body_list["notifications"][0]["id"], n2["id"])
        self.assertEqual(body_list["unread_count"], 2)

        # POST /api/notifications/read {ids: [n2.id]}
        h_read1 = _StubHandler(
            "/api/notifications/read",
            method="POST",
            body_dict={"ids": [n2["id"]]},
            headers=auth_headers,
        )
        h_read1.do_POST()
        self.assertEqual(h_read1.status_code, 200)
        self.assertEqual(h_read1.response_json()["unread_count"], 1)

        # POST /api/notifications/read {all: true}
        h_read_all = _StubHandler(
            "/api/notifications/read",
            method="POST",
            body_dict={"all": True},
            headers=auth_headers,
        )
        h_read_all.do_POST()
        self.assertEqual(h_read_all.status_code, 200)
        self.assertEqual(h_read_all.response_json()["unread_count"], 0)

        _, member_opener = self._mock_supabase()
        with patch("urllib.request.urlopen", side_effect=member_opener):
            # GET /api/notifications/settings hides admin kinds for members
            h_set_get = _StubHandler("/api/notifications/settings", method="GET", headers=auth_headers)
            h_set_get.do_GET()
            self.assertEqual(h_set_get.status_code, 200)
            s_data = h_set_get.response_json()
            self.assertFalse(s_data["is_admin"])
            self.assertFalse(any(item["admin_only"] for item in s_data["settings"]))

            # POST /api/notifications/settings refuses admin kind for member with 403
            h_set_forbid = _StubHandler(
                "/api/notifications/settings",
                method="POST",
                body_dict={"kind": "member_joined", "enabled": False},
                headers=auth_headers,
            )
            h_set_forbid.do_POST()
            self.assertEqual(h_set_forbid.status_code, 403)

            # Member can toggle non-admin kind
            h_set_ok = _StubHandler(
                "/api/notifications/settings",
                method="POST",
                body_dict={"kind": "quote_request", "enabled": False},
                headers=auth_headers,
            )
            h_set_ok.do_POST()
            self.assertEqual(h_set_ok.status_code, 200)
            self.assertFalse(notifications.is_kind_enabled("quote_request"))

        # Admin sees and can toggle admin kinds
        self._sign_in("admin-1", role="admin", name="Admin")
        _, admin_opener = self._mock_supabase()
        with patch("urllib.request.urlopen", side_effect=admin_opener):
            h_admin_get = _StubHandler("/api/notifications/settings", method="GET", headers=auth_headers)
            h_admin_get.do_GET()
            self.assertEqual(h_admin_get.status_code, 200)
            a_data = h_admin_get.response_json()
            self.assertTrue(a_data["is_admin"])
            self.assertTrue(any(item["kind"] == "member_draft" for item in a_data["settings"]))

            h_admin_post = _StubHandler(
                "/api/notifications/settings",
                method="POST",
                body_dict={"kind": "member_draft", "enabled": True},
                headers=auth_headers,
            )
            h_admin_post.do_POST()
            self.assertEqual(h_admin_post.status_code, 200)
            self.assertTrue(notifications.is_kind_enabled("member_draft"))


if __name__ == "__main__":
    unittest.main()

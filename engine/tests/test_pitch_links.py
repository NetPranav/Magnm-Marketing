"""Tests for engine.pitch_links, db v15 migrations, background sync integration, and /api/pitch/* link/request routes."""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import db
import events
import paths
import pitch
import pitch_links
import server
import team_auth


class _FakeHttpResponse:
    """Minimal context-manager HTTP response stub for mocked urllib.request.urlopen."""

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


class _StubApiHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising /api/pitch/* routes without sockets."""

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


class PitchLinksTests(unittest.TestCase):
    """Verify Pitch client links, confidentiality guard, auto-republish, quote polling, and routes."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        os.makedirs(os.path.join(self.tmpdir, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "state"), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_token = server.ENGINE_TOKEN
        self._prev_disable = os.environ.get("MAGNM_DISABLE_CLOUD_SYNC")
        self._prev_site_env = os.environ.get("MAGNM_PITCH_SITE_URL")

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        os.environ.pop("MAGNM_PITCH_SITE_URL", None)

        # Copy bundled vendor/magnm-pitch and config/product_rules.json into temp resource root
        os.symlink(
            os.path.join(self._prev_resource_root, "vendor"),
            os.path.join(self.tmpdir, "vendor"),
        )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.tmpdir, "config", "product_rules.json"),
        )
        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({
                "url": "https://example.supabase.co",
                "publishable_key": "sb_pub_test",
                "pitch_site_url": "https://magnm-pitch.vercel.app",
            }, f)

        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        self._reset_sync_globals()

    def tearDown(self):
        cloud_sync.stop(timeout=0.5)
        self._reset_sync_globals()
        server.ENGINE_TOKEN = self._prev_token
        db.reset_connections()
        db.set_db_path(None)
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        if self._prev_disable is None:
            os.environ.pop("MAGNM_DISABLE_CLOUD_SYNC", None)
        else:
            os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = self._prev_disable
        if self._prev_site_env is None:
            os.environ.pop("MAGNM_PITCH_SITE_URL", None)
        else:
            os.environ["MAGNM_PITCH_SITE_URL"] = self._prev_site_env
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

    def _sign_in_user(self, user_id="user-1", email="member@example.com", active=True):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })
        self._active_user = active

    def _mock_supabase_router(self, handler_fn=None):
        requests = []

        def fake_urlopen(req, timeout=0):
            url = req.full_url
            method = req.get_method()
            body = json.loads(req.data.decode("utf-8")) if req.data else None
            headers = {k.lower(): v for k, v in req.header_items()}
            entry = {
                "url": url,
                "method": method,
                "body": body,
                "headers": headers,
                "timeout": timeout,
            }
            requests.append(entry)

            if "/rest/v1/profiles" in url and method == "GET":
                sess = team_auth.get_session_info() or {}
                uid = sess.get("user_id") or "user-1"
                return _FakeHttpResponse([{
                    "id": uid,
                    "email": sess.get("email") or "member@example.com",
                    "display_name": "Member",
                    "avatar_url": None,
                    "role": "member",
                    "active": getattr(self, "_active_user", True),
                }])

            if handler_fn is not None:
                res = handler_fn(entry)
                if res is not None:
                    return res

            return _FakeHttpResponse(None, status=201)

        return requests, fake_urlopen

    def _create_sample_proposal(self, slug="acme-web", kind="proposal", **extra):
        payload = {
            "slug": slug,
            "kind": kind,
            "client_name": "Asha",
            "business_name": "Acme Corp",
            "contact": {
                "name": "Asha",
                "business": "Acme Corp",
                "phone": "+91 9876543210",
                "email": "asha@acme.example",
                "notes": "Private internal contact notes",
            },
            "notes": "Internal proposal notes",
            "preset": {
                "title": "Acme Platform",
                "lead": "Custom web and backend build.",
                "whatWeDo": "Software engineering.",
                "portfolio": "magnm-org.vercel.app",
                "thisPlan": "Acme web build.",
                "initialSelected": ["w_front", "b_db"],
                "sections": {"p2": ["w_front"], "p3": [], "p4": ["b_db"], "p5": []},
            },
        }
        payload.update(extra)
        return pitch.create_proposal(payload)

    def test_01_site_url_and_link_prefixes_and_short_share_id_replacement(self):
        self.assertEqual(pitch_links.site_url(), "https://magnm-pitch.vercel.app")
        with patch.dict(os.environ, {"MAGNM_PITCH_SITE_URL": "https://preview.magnm.test/"}):
            self.assertEqual(pitch_links.site_url(), "https://preview.magnm.test")

        p_prop = self._create_sample_proposal("acme-p", kind="proposal")
        p_qf = self._create_sample_proposal("acme-q", kind="quote_form")
        p_final = self._create_sample_proposal("acme-f", kind="final")
        p_def = pitch.get_default_proposal()

        self.assertEqual(
            pitch_links.link_for(p_prop),
            f"https://magnm-pitch.vercel.app/p/{p_prop['share_id']}",
        )
        self.assertEqual(
            pitch_links.link_for(p_qf),
            f"https://magnm-pitch.vercel.app/q/{p_qf['share_id']}",
        )
        self.assertEqual(
            pitch_links.link_for(p_def),
            f"https://magnm-pitch.vercel.app/q/{p_def['share_id']}",
        )
        self.assertEqual(
            pitch_links.link_for(p_final),
            f"https://magnm-pitch.vercel.app/f/{p_final['share_id']}",
        )

        # Short or missing share_id is replaced with 16+ url-safe token and persisted
        db.set_pitch_share_id(p_prop["id"], "short123")
        reloaded = db.get_pitch_proposal(p_prop["id"])
        self.assertEqual(reloaded["share_id"], "short123")

        new_url = pitch_links.link_for(reloaded)
        self.assertTrue(new_url.startswith("https://magnm-pitch.vercel.app/p/"))
        new_sid = new_url.rsplit("/p/", 1)[1]
        self.assertGreaterEqual(len(new_sid), 16)
        self.assertRegex(new_sid, r"^[A-Za-z0-9_-]{16,64}$")
        self.assertEqual(db.get_pitch_proposal(p_prop["id"])["share_id"], new_sid)

        # Missing share_id also replaced
        reloaded_missing = dict(reloaded, share_id="")
        url2 = pitch_links.link_for(reloaded_missing)
        sid2 = url2.rsplit("/p/", 1)[1]
        self.assertGreaterEqual(len(sid2), 16)
        self.assertEqual(db.get_pitch_proposal(p_prop["id"])["share_id"], sid2)

    def test_02_public_payload_sanitizes_internal_fields_and_blocks_confidential(self):
        p_prop = self._create_sample_proposal("acme-pub", kind="proposal")
        p_qf = self._create_sample_proposal("acme-qf-pub", kind="quote_form")
        p_final = self._create_sample_proposal("acme-fin-pub", kind="final")
        p_def = pitch.get_default_proposal()

        pub_p = pitch_links.public_payload(p_prop)
        self.assertEqual(pub_p["mode"], "present")
        self.assertNotIn("proposal_id", pub_p)
        self.assertNotIn("notes", pub_p)
        self.assertEqual(pub_p["contact"], {"name": "Asha", "business": "Acme Corp"})
        self.assertNotIn("phone", pub_p["contact"])
        self.assertNotIn("email", pub_p["contact"])
        self.assertNotIn("notes", pub_p["contact"])

        pub_q = pitch_links.public_payload(p_qf)
        self.assertEqual(pub_q["mode"], "form")
        self.assertNotIn("proposal_id", pub_q)
        self.assertEqual(pub_q["contact"], {"name": "Asha", "business": "Acme Corp"})

        pub_f = pitch_links.public_payload(p_final)
        self.assertEqual(pub_f["mode"], "present")
        self.assertTrue(pub_f["locked"])
        self.assertNotIn("proposal_id", pub_f)

        pub_d = pitch_links.public_payload(p_def)
        self.assertEqual(pub_d["mode"], "form")
        self.assertEqual(pub_d["kind"], "default")
        self.assertNotIn("proposal_id", pub_d)

        # Confidential product in proposal text is refused (LazyLayout is confidential)
        conf_prop = self._create_sample_proposal(
            "secret-ll",
            kind="proposal",
            preset={
                "title": "LazyLayout Rollout",
                "lead": "Build LazyLayout integration",
                "initialSelected": ["w_front"],
                "sections": {"p2": ["w_front"], "p3": [], "p4": [], "p5": []},
            },
        )
        with self.assertRaises(pitch_links.PitchLinkError) as ctx:
            pitch_links.public_payload(conf_prop)
        self.assertEqual(
            str(ctx.exception),
            "This proposal names a confidential product, so it can't be shared.",
        )

        # Unreadable product rules are refused too
        rules_file = os.path.join(self.tmpdir, "config", "product_rules.json")
        os.remove(rules_file)
        with self.assertRaises(pitch_links.PitchLinkError):
            pitch_links.public_payload(p_prop)

    def test_03_publish_and_withdraw_and_not_signed_in_guard(self):
        prop = self._create_sample_proposal("acme-share", kind="quote_form")

        # Not signed in -> refused
        with self.assertRaises(pitch_links.PitchLinkError) as ctx:
            pitch_links.publish(prop["id"])
        self.assertEqual(str(ctx.exception), "Sign in under Settings, Account to share links.")

        # Deactivated user -> refused
        self._sign_in_user("user-deact", active=False)
        reqs, fake_urlopen = self._mock_supabase_router()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(pitch_links.PitchLinkError) as ctx_deact:
                pitch_links.publish(prop["id"])
        self.assertEqual(str(ctx_deact.exception), "Sign in under Settings, Account to share links.")

        # Active user -> publish succeeds with expected upsert body and headers
        self._sign_in_user("user-1", active=True)
        reqs, fake_urlopen = self._mock_supabase_router()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = pitch_links.publish(prop["id"])

        expected_url = f"https://magnm-pitch.vercel.app/q/{prop['share_id']}"
        self.assertEqual(res["url"], expected_url)
        self.assertTrue(res["published_at"])

        post_reqs = [r for r in reqs if "/rest/v1/published_proposals" in r["url"] and r["method"] == "POST"]
        self.assertEqual(len(post_reqs), 1)
        up = post_reqs[0]
        self.assertIn("on_conflict=share_id", up["url"])
        self.assertEqual(up["headers"].get("prefer"), "resolution=merge-duplicates,return=minimal")
        self.assertEqual(up["body"]["share_id"], prop["share_id"])
        self.assertEqual(up["body"]["owner_id"], "user-1")
        self.assertEqual(up["body"]["local_proposal_id"], prop["id"])
        self.assertEqual(up["body"]["kind"], "proposal")  # quote_form maps to 'proposal'
        self.assertEqual(up["body"]["status"], "live")
        self.assertIsNone(up["body"]["expires_at"])
        self.assertNotIn("proposal_id", up["body"]["payload"])
        self.assertEqual(up["body"]["payload"]["contact"], {"name": "Asha", "business": "Acme Corp"})

        saved = db.get_pitch_proposal(prop["id"])
        self.assertEqual(saved["link_status"], "live")
        self.assertEqual(saved["published_at"], res["published_at"])

        # Withdraw -> PATCH status=withdrawn and local link_status=withdrawn
        reqs.clear()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            wres = pitch_links.withdraw(prop["id"])
        self.assertEqual(wres["link_status"], "withdrawn")
        patch_reqs = [r for r in reqs if "/rest/v1/published_proposals" in r["url"] and r["method"] == "PATCH"]
        self.assertEqual(len(patch_reqs), 1)
        self.assertIn(f"share_id=eq.{prop['share_id']}", patch_reqs[0]["url"])
        self.assertEqual(patch_reqs[0]["body"], {"status": "withdrawn"})
        self.assertEqual(db.get_pitch_proposal(prop["id"])["link_status"], "withdrawn")

        # Default quotation maps to kind='default_quote'
        p_def = pitch.get_default_proposal()
        reqs.clear()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            dres = pitch_links.publish(p_def["id"])
        self.assertTrue(dres["url"].startswith("https://magnm-pitch.vercel.app/q/"))
        def_up = [r for r in reqs if "/rest/v1/published_proposals" in r["url"] and r["method"] == "POST"][0]
        self.assertEqual(def_up["body"]["kind"], "default_quote")

    def test_04_auto_republish_only_for_live_links_newer_than_publish_and_backoff_on_error(self):
        self._sign_in_user("user-sync")
        live_prop = self._create_sample_proposal("live-sync", kind="proposal")
        withdrawn_prop = self._create_sample_proposal("withdrawn-sync", kind="proposal")
        draft_prop = self._create_sample_proposal("draft-sync", kind="proposal")

        reqs, fake_urlopen = self._mock_supabase_router()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            pitch_links.publish(live_prop["id"])
            pitch_links.publish(withdrawn_prop["id"])
            pitch_links.withdraw(withdrawn_prop["id"])

        # Up-to-date live proposal should NOT republish during sync_once
        reqs.clear()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = cloud_sync.sync_once()
        self.assertTrue(ok)
        self.assertEqual(
            [r for r in reqs if "/rest/v1/published_proposals" in r["url"]],
            [],
        )

        # Edit live_prop -> calls cloud_sync.request_sync(), and sync_once republishes ONLY live_prop
        time.sleep(0.01)
        with patch.object(cloud_sync, "request_sync") as m_req_sync:
            pitch.update_proposal(live_prop["id"], {"client_name": "Asha Updated"})
            m_req_sync.assert_called_once()
        with patch.object(cloud_sync, "request_sync") as m_req_sync2:
            pitch.update_proposal(withdrawn_prop["id"], {"client_name": "Withdrawn Updated"})
            pitch.update_proposal(draft_prop["id"], {"client_name": "Draft Updated"})
            m_req_sync2.assert_not_called()

        reqs.clear()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok2 = cloud_sync.sync_once()
        self.assertTrue(ok2)
        upserts = [r for r in reqs if "/rest/v1/published_proposals" in r["url"] and r["method"] == "POST"]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0]["body"]["local_proposal_id"], live_prop["id"])
        self.assertEqual(upserts[0]["body"]["payload"]["contact"]["name"], "Asha Updated")

        # Next cycle does not republish again since published_at >= updated_at
        reqs.clear()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        self.assertEqual(
            [r for r in reqs if "/rest/v1/published_proposals" in r["url"]],
            [],
        )

        # Failure during republish triggers backoff and shows in sync status
        time.sleep(0.01)
        pitch.update_proposal(live_prop["id"], {"client_name": "Asha Retry"})

        def failing_publish(entry):
            if "/rest/v1/published_proposals" in entry["url"] and entry["method"] == "POST":
                raise _http_error(entry["url"], 503, {"message": "Upstream unavailable"})
            return None

        _, fail_urlopen = self._mock_supabase_router(handler_fn=failing_publish)
        with patch("urllib.request.urlopen", side_effect=fail_urlopen):
            ok_fail = cloud_sync.sync_once()
        self.assertFalse(ok_fail)
        st = cloud_sync.status()
        self.assertIsNotNone(st["last_error"])
        self.assertGreater(cloud_sync._failure_count, 0)
        self.assertGreater(cloud_sync._next_allowed_at, 0.0)

    def test_05_quote_requests_polled_totals_computed_mismatch_flagged_and_patch_retry_idempotent(self):
        self._sign_in_user("user-qr")
        prop = self._create_sample_proposal(
            "qr-form",
            kind="quote_form",
            price_overrides={"w_front": 7500},
            custom_items=[{
                "id": "custom_audit",
                "g": "Backend",
                "n": "Security Audit",
                "d": "Pen-test audit",
                "p": 2500,
                "r": [],
            }],
        )

        # Expected local total: w_front (7500) + b_db (catalog 2000) + custom_audit (2500) = 12000
        # Test mismatch when client claims total_shown = 9999
        remote_rows = [
            {
                "id": "req-1",
                "share_id": prop["share_id"],
                "choices": {
                    "selected": ["w_front", "b_db"],
                    "custom_selected": ["custom_audit"],
                    "total_shown": 9999,
                },
                "contact": {
                    "name": "Priya",
                    "business": "Northstar",
                    "phone": "+91 9811122233",
                    "notes": "Need launch by Q2 <script>alert(1)</script>",
                },
                "created_at": "2026-11-21T10:00:00Z",
            },
            {
                "id": "req-2",
                "share_id": prop["share_id"],
                "choices": {
                    "selected": ["w_front"],
                    "custom_selected": [],
                    "total_shown": 7500,
                },
                "contact": {
                    "name": "Kabir",
                    "business": "Kite",
                    "phone": "+91 9822233344",
                    "notes": "",
                },
                "created_at": "2026-11-21T11:00:00Z",
            },
            # Malformed row without id should NOT be stored and NOT included in PATCH seen_at
            {
                "id": "",
                "share_id": prop["share_id"],
                "choices": {},
                "contact": {},
                "created_at": "2026-11-21T12:00:00Z",
            },
        ]

        q = events.subscribe()
        fail_patch = True

        def qr_handler(entry):
            if "/rest/v1/quote_submissions" in entry["url"] and entry["method"] == "GET":
                return _FakeHttpResponse(remote_rows, status=200)
            if "/rest/v1/quote_submissions" in entry["url"] and entry["method"] == "PATCH":
                if fail_patch:
                    raise _http_error(entry["url"], 500, {"message": "PATCH transient failure"})
                return _FakeHttpResponse(None, status=204)
            return None

        try:
            reqs, fake_urlopen = self._mock_supabase_router(handler_fn=qr_handler)
            # Cycle 1: PATCH fails -> requests are STILL stored, events published, activity added
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                ok1 = cloud_sync.sync_once()
            self.assertFalse(ok1)

            listed = pitch_links.list_requests()
            self.assertEqual(listed["unread_count"], 2)
            self.assertEqual([r["id"] for r in listed["requests"]], ["req-2", "req-1"])  # newest first

            r1 = listed["requests"][1]
            self.assertEqual(r1["proposal_title"], "Acme Corp · Asha")
            self.assertEqual(r1["computed_total"], 12000)
            self.assertEqual(r1["total_shown"], 9999)
            self.assertTrue(r1["total_mismatch"])
            self.assertEqual(
                [it["name"] for it in r1["selected_items"]],
                ["Responsive Web Application / Frontend", "Core Database & API Server", "Security Audit"],
            )

            r2 = listed["requests"][0]
            self.assertEqual(r2["computed_total"], 7500)
            self.assertEqual(r2["total_shown"], 7500)
            self.assertFalse(r2["total_mismatch"])

            # Events published once per new request
            published_events = []
            while not q.empty():
                published_events.append(q.get_nowait())
            qr_events = [e for e in published_events if e["type"] == "pitch.quote_request"]
            self.assertEqual(len(qr_events), 2)

            # Activity lines recorded
            act_lines = db.get_activity_lines(limit=10)
            self.assertEqual(len(act_lines), 2)
            self.assertIn("Northstar", act_lines[0])

            # Cycle 2: PATCH succeeds -> retried for stored ids only, NO duplicates or extra events
            fail_patch = False
            reqs.clear()
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                ok2 = cloud_sync.sync_once()
            self.assertTrue(ok2)

            patch_reqs = [r for r in reqs if "/rest/v1/quote_submissions" in r["url"] and r["method"] == "PATCH"]
            self.assertEqual(len(patch_reqs), 1)
            self.assertIn("id=in.(req-1,req-2)", patch_reqs[0]["url"])
            self.assertNotIn("req-3", patch_reqs[0]["url"])
            self.assertIn("seen_at", patch_reqs[0]["body"])

            self.assertEqual(len(pitch_links.list_requests()["requests"]), 2)
            self.assertTrue(q.empty())
            self.assertEqual(len(db.get_activity_lines(limit=10)), 2)
        finally:
            events.unsubscribe(q)

    def test_06_http_routes_require_engine_token_and_handle_publish_withdraw_requests_read(self):
        self._sign_in_user("user-http")
        prop = self._create_sample_proposal("http-prop", kind="proposal")
        db.insert_pitch_quote_request(
            request_id="req-http-1",
            share_id=prop["share_id"],
            local_proposal_id=prop["id"],
            choices={"selected": ["w_front"], "custom_selected": [], "total_shown": 6000},
            contact={"name": "Sara", "business": "Beta", "phone": "9999999999", "notes": "Hi"},
            created_at="2026-11-21T13:00:00Z",
        )

        server.ENGINE_TOKEN = "secret-tok"
        for method, path in (
            ("POST", f"/api/pitch/proposals/{prop['id']}/publish"),
            ("POST", f"/api/pitch/proposals/{prop['id']}/withdraw"),
            ("GET", "/api/pitch/requests"),
            ("POST", "/api/pitch/requests/req-http-1/read"),
        ):
            h_unauth = _StubApiHandler(path, method=method, body_dict={} if method == "POST" else None)
            if method == "GET":
                h_unauth.do_GET()
            else:
                h_unauth.do_POST()
            self.assertEqual(h_unauth.status_code, 401, f"Expected 401 for {method} {path}")

        auth_hdr = {"X-Magnm-Token": "secret-tok"}
        _, fake_urlopen = self._mock_supabase_router()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            h_pub = _StubApiHandler(
                f"/api/pitch/proposals/{prop['id']}/publish",
                method="POST",
                body_dict={},
                headers=auth_hdr,
            )
            h_pub.do_POST()
            self.assertEqual(h_pub.status_code, 200)
            self.assertEqual(
                h_pub.response_json()["url"],
                f"https://magnm-pitch.vercel.app/p/{prop['share_id']}",
            )

            h_wd = _StubApiHandler(
                f"/api/pitch/proposals/{prop['id']}/withdraw",
                method="POST",
                body_dict={},
                headers=auth_hdr,
            )
            h_wd.do_POST()
            self.assertEqual(h_wd.status_code, 200)
            self.assertEqual(h_wd.response_json()["link_status"], "withdrawn")

        h_list = _StubApiHandler("/api/pitch/requests", method="GET", headers=auth_hdr)
        h_list.do_GET()
        self.assertEqual(h_list.status_code, 200)
        self.assertEqual(h_list.response_json()["unread_count"], 1)
        self.assertEqual(len(h_list.response_json()["requests"]), 1)

        h_read = _StubApiHandler(
            "/api/pitch/requests/req-http-1/read",
            method="POST",
            body_dict={},
            headers=auth_hdr,
        )
        h_read.do_POST()
        self.assertEqual(h_read.status_code, 200)
        self.assertEqual(h_read.response_json()["unread_count"], 0)
        self.assertIsNotNone(h_read.response_json()["request"]["read_at"])

    def test_07_admin_only_takes_their_own_quote_requests(self):
        """An admin can read everyone's submissions, but must only fetch, store and mark seen their own."""
        self._sign_in_user("admin-1")
        prop = self._create_sample_proposal("own-form", kind="quote_form")
        rows = [
            {"id": "mine", "owner_id": "admin-1", "share_id": prop["share_id"],
             "choices": {"selected": ["w_front"], "custom_selected": [], "total_shown": 0},
             "contact": {"name": "Mine"}, "created_at": "2026-11-21T10:00:00Z"},
            {"id": "members", "owner_id": "member-9", "share_id": "someoneElsesLink01",
             "choices": {"selected": [], "custom_selected": [], "total_shown": 0},
             "contact": {"name": "Not mine"}, "created_at": "2026-11-21T10:05:00Z"},
        ]

        def handler(entry):
            if "/rest/v1/quote_submissions" in entry["url"] and entry["method"] == "GET":
                return _FakeHttpResponse(rows, status=200)
            if "/rest/v1/quote_submissions" in entry["url"] and entry["method"] == "PATCH":
                return _FakeHttpResponse(None, status=204)
            return None

        reqs, fake_urlopen = self._mock_supabase_router(handler_fn=handler)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()

        gets = [r["url"] for r in reqs if "quote_submissions" in r["url"] and r["method"] == "GET"]
        patches = [r["url"] for r in reqs if "quote_submissions" in r["url"] and r["method"] == "PATCH"]
        self.assertTrue(gets and all("owner_id=eq.admin-1" in u for u in gets))
        self.assertTrue(patches and all("owner_id=eq.admin-1" in u and "members" not in u for u in patches))
        self.assertIsNotNone(db.get_pitch_quote_request("mine"))
        self.assertIsNone(db.get_pitch_quote_request("members"))

if __name__ == "__main__":
    unittest.main()

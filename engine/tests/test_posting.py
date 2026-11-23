"""Tests for Phase 9 T9.1 official-API posting, confirm gate, vault, providers, and bypass blocks."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import io
import json
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.parse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import cmo_runtime
import db
import events
import mcp_tools
import paths
import phone_server
import server
import tools
from posting import PostingError, http_client, instagram, linkedin, service as posting_service_mod, vault as posting_vault_mod, x_provider
from engine.tests.social_test_crypto import requires_cryptography, stub_vault_if_missing


class _FakeClock:
    def __init__(self, mono=1000.0, wall=None):
        self.mono = float(mono)
        self.wall = wall or datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        self.sleeps = []

    def now_mono(self):
        return self.mono

    def now_wall(self):
        return self.wall

    def sleep(self, secs):
        self.sleeps.append(float(secs))
        self.mono += float(secs)
        self.wall += timedelta(seconds=float(secs))

    def advance(self, seconds):
        self.mono += float(seconds)
        self.wall += timedelta(seconds=float(seconds))


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for testing /api/posting/* routes."""

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


def _make_jpeg_bytes(extra=b"0001"):
    return b"\xff\xd8\xff\xe0\x00\x10JFIF" + extra


def _make_png_bytes(extra=b"0001"):
    return b"\x89PNG\r\n\x1a\n" + extra


def _make_mp4_bytes(extra=b"0001"):
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42" + extra


class PostingTests(unittest.TestCase):
    def setUp(self):
        stub_vault_if_missing(self, posting_vault_mod)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)

        p = patch.object(paths, "DATA_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)
        p_res = patch.object(paths, "RESOURCE_ROOT", self.root)
        p_res.start()
        self.addCleanup(p_res.stop)

        paths.ensure_data_root()
        db.set_db_path(os.path.join(self.root, "state", "magnm.db"))
        db.init_db()
        self.addCleanup(db.close_connection)
        self.addCleanup(db.reset_connections)
        self.addCleanup(lambda: db.set_db_path(None))

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
                    "blocked_terms": ["robotics"],
                    "confidential": False,
                },
                {
                    "id": "projectnightfall",
                    "name": "ProjectNightfall",
                    "status": "secret",
                    "show": "nothing",
                    "blocked_terms": [],
                    "confidential": True,
                },
            ],
        }
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        with open(os.path.join(self.root, "config", "product_rules.json"), "w", encoding="utf-8") as fh:
            json.dump(rules, fh)
        with open(os.path.join(self.root, "config", "repos.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "repos": [
                        {"id": "cero-repo", "name": "Cero", "product_id": "cero", "confidential": False},
                        {"id": "nf-repo", "name": "NightfallCore", "product_id": "projectnightfall", "confidential": True},
                    ]
                },
                fh,
            )

        self.clock = _FakeClock()
        self.key_store = posting_vault_mod.InMemoryKeyStore()
        self.prev_default = posting_service_mod._default_service
        self.addCleanup(self._restore_default_service)

    def _restore_default_service(self):
        posting_service_mod.reset_default_service()
        posting_service_mod._default_service = self.prev_default

    def _make_service(self, *, transport=None, media_fetcher=None, oauth_port=0, ig_poll_interval_s=1.0, ig_max_poll_s=300.0):
        svc = posting_service_mod.PostingService(
            data_root=self.root,
            key_store=self.key_store,
            transport=transport,
            media_fetcher=media_fetcher,
            clock=self.clock.now_mono,
            wall_clock=self.clock.now_wall,
            sleep_fn=self.clock.sleep,
            oauth_port=oauth_port,
            ig_poll_interval_s=ig_poll_interval_s,
            ig_max_poll_s=ig_max_poll_s,
        )
        posting_service_mod._default_service = svc
        return svc

    def _create_item(self, cid="cnt-post-1", *, title="Cero launch", body="Cero is live.", platform="linkedin", product_id="cero", session_id=None, files=None, notes=None):
        return db.create_content_item(
            content_id=cid,
            content_type="post",
            platform=platform,
            title=title,
            body=body,
            product_id=product_id,
            session_id=session_id,
            files=files or [],
            notes=notes,
        )

    def test_01_schema_v24_no_secrets_and_never_in_cloud_sync(self):
        """Schema v24 creates posting_accounts and posting_attempts; neither nor vault tokens ever sync."""
        conn = db.connect()
        versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()]
        # Later schema versions (v25 phone_action_receipts) may exist; v24 and everything before it must be there.
        self.assertEqual(versions, list(range(1, db.SCHEMA_VERSION + 1)))
        self.assertGreaterEqual(db.SCHEMA_VERSION, 24)

        cols_acct = {r["name"] for r in conn.execute("PRAGMA table_info(posting_accounts)").fetchall()}
        self.assertFalse(any("token" in c or "secret" in c for c in cols_acct))

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("posting_accounts", tables)
        self.assertIn("posting_attempts", tables)

        def _fake_transport(method, url, headers, body, timeout):
            if url == linkedin.USERINFO_URL:
                return 200, {}, json.dumps({"sub": "member123", "name": "Alex Doe"})
            return 404, {}, "{}"

        svc = self._make_service(transport=_fake_transport)
        svc.connect_linkedin("CANARY_LI_SYNC_TOKEN_999")
        item = self._create_item(cid="cnt-sync-1", body="Cero release update.")
        prep = svc.prepare(item["id"], "linkedin")

        mirror = cloud_sync.build_mirror_records()
        serialized = json.dumps(mirror)
        self.assertNotIn("CANARY_LI_SYNC_TOKEN_999", serialized)
        self.assertNotIn(prep["confirmation_id"], serialized)
        self.assertNotIn(prep["attempt_id"], serialized)
        self.assertNotIn("urn:li:person:member123", serialized)

    @requires_cryptography
    def test_02_vault_aes_gcm_0600_aad_own_service_and_disconnect_deletes_both(self):
        """PostingVault uses AES-256-GCM, 0600 perms, own service/AAD, refuses null keyring, disconnects cleanly."""
        vault = posting_vault_mod.PostingVault(data_root=self.root, key_store=self.key_store)
        tok = {"access_token": "CANARY_SECRET_TOK_ABC", "obtained_at": "2026-11-22T00:00:00Z"}
        vault.save_token("linkedin", tok)
        bpath = vault.blob_path("linkedin")
        self.assertTrue(bpath.startswith(os.path.join(self.root, "state", "posting")))
        self.assertEqual(stat.S_IMODE(os.stat(bpath).st_mode), 0o600)

        with open(bpath, "rb") as fh:
            raw_blob = fh.read()
        self.assertNotIn(b"CANARY_SECRET_TOK_ABC", raw_blob)
        self.assertEqual(vault.load_token("linkedin"), tok)

        # Cross-platform AAD mismatch fails decryption
        other_blob = vault.blob_path("instagram")
        with open(other_blob, "wb") as fh:
            fh.write(raw_blob)
        self.key_store.set_key(vault._service_name(), "instagram", self.key_store.get_key(vault._service_name(), "linkedin"))
        with self.assertRaises(posting_vault_mod.PostingVaultError):
            vault.load_token("instagram")
        os.remove(other_blob)

        # Own service name differs from social vault service name
        from social import vault as social_vault_mod
        sv = social_vault_mod.SocialVault(data_root=self.root, key_store=self.key_store)
        self.assertNotEqual(vault._service_name(), sv._service_name())

        # Null/missing keychain refuses to save with zero fallback
        null_vault = posting_vault_mod.PostingVault(data_root=self.root)
        with self.assertRaises(posting_vault_mod.PostingVaultError) as ctx:
            null_vault.save_token("x", {"access_token": "abc"})
        self.assertEqual(ctx.exception.code, "no_keychain")

        # Disconnect deletes both blob and keychain key
        svc = self._make_service()
        db.upsert_posting_account("linkedin", account_id="urn:li:person:1", display_name="Alex")
        svc.disconnect("linkedin")
        self.assertFalse(os.path.exists(bpath))
        self.assertIsNone(self.key_store.get_key(vault._service_name(), "linkedin"))
        self.assertIsNone(db.get_posting_account("linkedin"))

    def test_03_off_by_default_and_no_agent_mcp_job_workflow_schedule_phone_access(self):
        """Platforms are off until connected, and no agent/MCP/job/workflow/phone route can prepare or confirm."""
        svc = self._make_service()
        st = svc.get_status()
        for plat in ("linkedin", "instagram", "x"):
            self.assertFalse(st["platforms"][plat]["connected"])

        item = self._create_item()
        with self.assertRaises(PostingError) as ctx:
            svc.prepare(item["id"], "linkedin")
        self.assertEqual(ctx.exception.code, "not_connected")

        # No agent tool
        for tool_name in tools.list_registered_tools():
            self.assertNotIn("post", tool_name)
            self.assertNotIn("publish", tool_name)

        # No MCP tool
        mcp_names = [s["name"] for s in mcp_tools.READ_ONLY_TOOL_SPECS + mcp_tools.WRITE_TOOL_SPECS]
        for name in mcp_names:
            self.assertNotIn("posting", name)
            self.assertNotIn("publish", name)

        # No background job
        for jname in cmo_runtime.ALLOWED_JOBS:
            self.assertNotIn("posting", jname)
            self.assertNotIn("publish", jname)

        # No phone server route for posting
        with patch.object(phone_server.db, "get_phone_device_by_token_hash", return_value={"id": "d1", "name": "Phone", "revoked_at": None, "owner_user_id": "u1"}):
            for path in ("/api/posting/status", "/api/posting/prepare", "/api/posting/confirm"):
                h = phone_server.PhoneHandler.__new__(phone_server.PhoneHandler)
                h.path = path
                h.command = "POST" if path != "/api/posting/status" else "GET"
                h.headers = {"Authorization": "Bearer testtok", "Content-Length": "2"}
                h.rfile = io.BytesIO(b"{}")
                h.wfile = io.BytesIO()
                h.status_code = None
                h.send_response = lambda code, msg=None, _h=h: setattr(_h, "status_code", code)
                h.send_header = lambda k, v: None
                h.end_headers = lambda: None
                if h.command == "GET":
                    h.do_GET()
                else:
                    h.do_POST()
                self.assertEqual(h.status_code, 404, path)

        # Laptop HTTP route enforces ENGINE_TOKEN and request_guard
        prev_tok = server.ENGINE_TOKEN
        server.ENGINE_TOKEN = "secret-engine-tok"
        try:
            unauth = _StubHandler("/api/posting/status", "GET")
            unauth.do_GET()
            self.assertEqual(unauth.status_code, 401)
            unauth_post = _StubHandler("/api/posting/confirm", "POST", {"confirmation_id": "x"})
            unauth_post.do_POST()
            self.assertEqual(unauth_post.status_code, 401)
        finally:
            server.ENGINE_TOKEN = prev_tok

    def test_04_linkedin_exact_body_headers_escaping_and_image_upload(self):
        """LinkedIn verifies userinfo, escapes little-text except hashtags, uploads single PNG/JPG to *.linkedin.com, posts exact body, marks posted."""
        calls = []

        def _li_transport(method, url, headers, body, timeout):
            calls.append((method, url, dict(headers), body))
            if url == linkedin.USERINFO_URL:
                return 200, {}, json.dumps({"sub": "abc123", "name": "Jane Doe"})
            if url == linkedin.INITIALIZE_UPLOAD_URL:
                return 200, {}, json.dumps({
                    "value": {
                        "uploadUrl": "https://dms.linkedin.com/upload/img1",
                        "image": "urn:li:image:img99",
                    }
                })
            if url == "https://dms.linkedin.com/upload/img1":
                return 201, {}, b""
            if url == linkedin.POSTS_URL:
                return 201, {"X-RestLi-Id": "urn:li:share:777888"}, b""
            return 404, {}, b""

        svc = self._make_service(transport=_li_transport)
        svc.connect_linkedin("CANARY_LI_TOKEN_1")

        # Write local PNG file
        img_rel = "outputs/posts/cnt-li-1/card.png"
        img_abs = os.path.join(self.root, img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        img_bytes = _make_png_bytes(b"pngpayload")
        with open(img_abs, "wb") as fh:
            fh.write(img_bytes)

        plain_text = "Cero & robotics (beta) @team #Cero #AI_2026 C# # heading ##h | {a} [b] <c> *d* _e_ ~f~ \\path"
        item = self._create_item(
            cid="cnt-li-1",
            body=plain_text,
            platform="linkedin",
            files=[{"kind": "card", "path": img_rel, "alt_text": "Cero card visual"}],
        )

        prep = svc.prepare(item["id"], "linkedin")
        self.assertEqual(prep["final_text"], plain_text)
        self.assertEqual(prep["account"]["account_id"], "urn:li:person:abc123")
        self.assertEqual(prep["media"][0]["bytes"], len(img_bytes))
        self.assertEqual(prep["media"][0]["sha256"], hashlib.sha256(img_bytes).hexdigest())
        # Blocked term warning ("robotics") appears in prepare warnings
        self.assertTrue(any("robotics" in w["message"].lower() for w in prep["warnings"]))

        res = svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
        self.assertTrue(res["ok"])
        self.assertEqual(res["post_id"], "urn:li:share:777888")
        self.assertEqual(res["status"], "posted")

        # Check exact headers and body of POST /rest/posts
        post_calls = [c for c in calls if c[1] == linkedin.POSTS_URL]
        self.assertEqual(len(post_calls), 1)
        _, _, hdrs, raw_body = post_calls[0]
        self.assertEqual(hdrs["Linkedin-Version"], "202611")
        self.assertEqual(hdrs["X-Restli-Protocol-Version"], "2.0.0")
        self.assertEqual(hdrs["Authorization"], "Bearer CANARY_LI_TOKEN_1")
        sent = json.loads(raw_body.decode("utf-8"))
        self.assertEqual(sent["author"], "urn:li:person:abc123")
        self.assertEqual(sent["visibility"], "PUBLIC")
        self.assertEqual(sent["distribution"], {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        })
        self.assertEqual(sent["lifecycleState"], "PUBLISHED")
        self.assertFalse(sent["isReshareDisabledByAuthor"])
        self.assertEqual(sent["content"], {"media": {"id": "urn:li:image:img99", "altText": "Cero card visual"}})
        expected_commentary = (
            "Cero & robotics \\(beta\\) \\@team #Cero #AI_2026 C\\# \\# heading \\#\\#h "
            "\\| \\{a\\} \\[b\\] \\<c\\> \\*d\\* \\_e\\_ \\~f\\~ \\\\path"
        )
        self.assertEqual(sent["commentary"], expected_commentary)

        # LinkedIn upload URL allowlist rejects non-*.linkedin.com
        for bad in (
            "http://dms.linkedin.com/upload",
            "https://linkedin.com.evil.com/upload",
            "https://127.0.0.1/upload",
            "https://user:pass@dms.linkedin.com/upload",
        ):
            with self.assertRaises(PostingError):
                http_client.validate_linkedin_upload_url(bad)

        # Token 7-day expiry warning and expired refusal
        self.clock.advance(54 * 86400)
        st_warn = svc.get_status()["platforms"]["linkedin"]
        self.assertTrue(st_warn["expiring_soon"])
        self.clock.advance(7 * 86400)
        with self.assertRaises(PostingError) as exp_ctx:
            svc.prepare(item["id"], "linkedin", acknowledge_repost=True)
        self.assertEqual(exp_ctx.exception.code, "token_expired")

    def test_05_confirm_gate_expiry_single_use_tamper_race_and_repost_ack(self):
        """Confirm expires in 5m, is single-use, refuses any mutation, refuses race double-confirm, requires repost ack."""
        write_count = {"posts": 0}

        def _li_transport(method, url, headers, body, timeout):
            if url == linkedin.USERINFO_URL:
                return 200, {}, json.dumps({"sub": "u1", "name": "Owner"})
            if url == linkedin.INITIALIZE_UPLOAD_URL:
                return 200, {}, json.dumps({
                    "value": {
                        "uploadUrl": "https://dms.linkedin.com/upload/gate1",
                        "image": "urn:li:image:gate1",
                    }
                })
            if url == "https://dms.linkedin.com/upload/gate1":
                return 201, {}, b""
            if url == linkedin.POSTS_URL:
                write_count["posts"] += 1
                time.sleep(0.05)
                return 201, {"x-restli-id": f"urn:li:share:{write_count['posts']}"}, b""
            return 404, {}, b""

        svc = self._make_service(transport=_li_transport)
        svc.connect_linkedin("CANARY_LI_GATE")

        img_rel = "outputs/posts/cnt-gate-1/pic.jpg"
        img_abs = os.path.join(self.root, img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        with open(img_abs, "wb") as fh:
            fh.write(_make_jpeg_bytes(b"v1"))

        item = self._create_item(cid="cnt-gate-1", body="Cero post v1", files=[{"kind": "image", "path": img_rel}])

        # 1. Expiry after 5 minutes
        p1 = svc.prepare(item["id"], "linkedin")
        self.clock.advance(301)
        with self.assertRaises(PostingError) as ctx:
            svc.confirm(p1["confirmation_id"], expected_hash=p1["payload_hash"])
        self.assertEqual(ctx.exception.code, "confirmation_expired")
        self.assertEqual(write_count["posts"], 0)

        # 2. Body change between prepare and confirm refused
        p2 = svc.prepare(item["id"], "linkedin")
        db.add_content_version(item["id"], "Cero post v2 tampered", created_by="user")
        with self.assertRaises(PostingError) as ctx:
            svc.confirm(p2["confirmation_id"], expected_hash=p2["payload_hash"])
        self.assertEqual(ctx.exception.code, "payload_changed")
        self.assertEqual(write_count["posts"], 0)

        # 3. File bytes change between prepare and confirm refused
        p3 = svc.prepare(item["id"], "linkedin")
        with open(img_abs, "wb") as fh:
            fh.write(_make_jpeg_bytes(b"v2-modified"))
        with self.assertRaises(PostingError) as ctx:
            svc.confirm(p3["confirmation_id"], expected_hash=p3["payload_hash"])
        self.assertEqual(ctx.exception.code, "payload_changed")
        self.assertEqual(write_count["posts"], 0)

        # 4. Parallel confirm race -> ONLY ONE write
        p4 = svc.prepare(item["id"], "linkedin")
        results = []
        errors = []
        barrier = threading.Barrier(2)

        def _worker():
            barrier.wait()
            try:
                results.append(svc.confirm(p4["confirmation_id"], expected_hash=p4["payload_hash"]))
            except PostingError as exc:
                errors.append(exc)
            finally:
                db.close_connection()

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(write_count["posts"], 1)

        # 5. Second confirm after completion refused
        with self.assertRaises(PostingError):
            svc.confirm(p4["confirmation_id"], expected_hash=p4["payload_hash"])
        self.assertEqual(write_count["posts"], 1)

        # 6. Already posted item requires explicit acknowledge_repost=True
        with self.assertRaises(PostingError) as rp_ctx:
            svc.prepare(item["id"], "linkedin")
        self.assertEqual(rp_ctx.exception.code, "already_posted")
        p5 = svc.prepare(item["id"], "linkedin", acknowledge_repost=True)
        with self.assertRaises(PostingError) as rc_ctx:
            svc.confirm(p5["confirmation_id"], expected_hash=p5["payload_hash"], acknowledge_repost=False)
        self.assertEqual(rc_ctx.exception.code, "already_posted")
        svc.confirm(p5["confirmation_id"], expected_hash=p5["payload_hash"], acknowledge_repost=True)
        self.assertEqual(write_count["posts"], 2)

    def test_06_confidential_items_text_files_accounts_refused_and_fail_closed(self):
        """Confidential products, sessions, serialized text, media, accounts, and unreadable rules fail closed."""
        def _li_transport(method, url, headers, body, timeout):
            if url == linkedin.USERINFO_URL:
                return 200, {}, json.dumps({"sub": "u1", "name": "Owner"})
            return 201, {"x-restli-id": "should-never-happen"}, b""

        svc = self._make_service(transport=_li_transport)
        svc.connect_linkedin("CANARY_CONF")

        # 1. Confidential product_id
        c1 = self._create_item(cid="cnt-conf-1", title="Teaser", body="Something new is coming.", product_id="projectnightfall")
        with self.assertRaises(PostingError) as ctx:
            svc.prepare(c1["id"], "linkedin")
        self.assertEqual(ctx.exception.status, 403)

        # 2. Confidential text via JSON/newline split or URL encoding
        c2 = self._create_item(cid="cnt-conf-2", title="Update", body="Line\nProjectNightfall%20launch", product_id="cero")
        with self.assertRaises(PostingError):
            svc.prepare(c2["id"], "linkedin")

        # 3. Confidential session (and child session)
        db.create_agent_session({"id": "sess-parent", "agent_id": "writer", "confidential": True})
        db.create_agent_session({"id": "sess-child", "agent_id": "reviewer", "parent_session_id": "sess-parent", "confidential": False})
        c3 = self._create_item(cid="cnt-conf-3", title="Clean title", body="Cero is great.", product_id="cero", session_id="sess-child")
        with self.assertRaises(PostingError):
            svc.prepare(c3["id"], "linkedin")

        # 4. Private file or confidential filename
        priv_item = self._create_item(cid="cnt-conf-4", title="Clean", body="Cero text", product_id="cero", files=[{"kind": "image", "path": "state/secret.png"}])
        with self.assertRaises(PostingError) as pctx:
            svc.prepare(priv_item["id"], "linkedin")
        self.assertEqual(pctx.exception.status, 403)

        # 5. Item becomes confidential after prepare -> confirm refuses
        clean_item = self._create_item(cid="cnt-conf-5", title="Cero", body="Cero v1 is out.", product_id="cero")
        prep = svc.prepare(clean_item["id"], "linkedin")
        db.add_content_version(clean_item["id"], "Cero and ProjectNightfall together.", created_by="user")
        with self.assertRaises(PostingError):
            svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])

        # 6. Confidential account name refused
        db.upsert_posting_account("linkedin", account_id="urn:li:person:1", display_name="ProjectNightfall Official")
        clean2 = self._create_item(cid="cnt-conf-6", title="Cero", body="Cero post.", product_id="cero")
        with self.assertRaises(PostingError):
            svc.prepare(clean2["id"], "linkedin")
        db.upsert_posting_account("linkedin", account_id="urn:li:person:1", display_name="Owner")

        # 7. Unreadable/corrupt product_rules.json fails closed
        rules_file = os.path.join(self.root, "config", "product_rules.json")
        with open(rules_file, "w", encoding="utf-8") as fh:
            fh.write("{corrupt")
        with self.assertRaises(PostingError) as fctx:
            svc.prepare(clean2["id"], "linkedin")
        self.assertEqual(fctx.exception.status, 503)
        os.remove(rules_file)
        with self.assertRaises(PostingError) as fctx2:
            svc.prepare(clean2["id"], "linkedin")
        self.assertEqual(fctx2.exception.status, 503)

    def test_07_no_auto_retry_unknown_status_and_restart_recovery(self):
        """Timeout, dropped connection, 5xx, and restart during sending mark status 'unknown', never retry."""
        for label, exc_or_status in (
            ("5xx", (503, {}, "upstream down CANARY_BODY_ECHO")),
            ("timeout", TimeoutError("timed out")),
            ("drop", OSError("connection reset by peer")),
        ):
            write_calls = {"n": 0}

            def _failing_transport(method, url, headers, body, timeout, _spec=exc_or_status, _cnt=write_calls):
                if url == linkedin.USERINFO_URL:
                    return 200, {}, json.dumps({"sub": "u1", "name": "Owner"})
                if url == linkedin.POSTS_URL:
                    _cnt["n"] += 1
                    if isinstance(_spec, Exception):
                        raise _spec
                    return _spec
                return 404, {}, b""

            svc = self._make_service(transport=_failing_transport)
            svc.connect_linkedin("CANARY_TOK")
            item = self._create_item(cid=f"cnt-fail-{label}", body=f"Cero {label}")
            prep = svc.prepare(item["id"], "linkedin")
            with self.assertRaises(PostingError) as ctx:
                svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
            self.assertTrue(ctx.exception.uncertain)
            self.assertIn("check the platform before trying again", ctx.exception.message.lower())
            self.assertNotIn("CANARY_BODY_ECHO", ctx.exception.message)
            self.assertEqual(write_calls["n"], 1)

            att = db.get_posting_attempt(prep["attempt_id"])
            self.assertEqual(att["status"], "unknown")
            # Next prepare blocked without acknowledge_unknown=True
            with self.assertRaises(PostingError) as uctx:
                svc.prepare(item["id"], "linkedin")
            self.assertEqual(uctx.exception.code, "previous_unknown")

        # Restart recovery marks 'sending' as 'unknown' and prevents double post
        item_r = self._create_item(cid="cnt-restart-1", body="Cero restart check")
        prep_r = svc.prepare(item_r["id"], "linkedin")
        db.acquire_posting_attempt_atomic(
            prep_r["confirmation_id"],
            prep_r["payload_hash"],
            item_r["id"],
            "linkedin",
            self.clock.now_iso() if hasattr(self.clock, "now_iso") else self.clock.now_wall().isoformat(),
        )
        recovered = posting_service_mod.recover_stale_attempts()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "unknown")
        self.assertIn("check the platform before trying again", recovered[0]["error"].lower())
        with self.assertRaises(PostingError):
            svc.confirm(prep_r["confirmation_id"], expected_hash=prep_r["payload_hash"])

    def test_08_instagram_auth_header_explicit_refresh_egress_rehash_and_poll(self):
        """Instagram uses Authorization header, rejects PERSONAL, refreshes only on explicit tap without logging URL, checks public egress & re-hashes, polls <= 5m."""
        calls = []
        poll_state = {"count": 0}
        media_state = {"bytes": _make_jpeg_bytes(b"img-v1"), "ct": "image/jpeg"}

        def _ig_transport(method, url, headers, body, timeout):
            calls.append((method, url, dict(headers), body))
            if url.startswith(f"{instagram.GRAPH_BASE}/me"):
                self.assertEqual(headers.get("Authorization"), "Bearer CANARY_IG_TOKEN_777")
                self.assertNotIn("access_token", url)
                return 200, {}, json.dumps({"id": "ig-acct-1", "username": "magnm_ig", "account_type": "BUSINESS"})
            if url.startswith(instagram.REFRESH_ENDPOINT):
                return 200, {}, json.dumps({"access_token": "CANARY_IG_REFRESHED_888", "expires_in": 5184000})
            if url == f"{instagram.GRAPH_BASE}/ig-acct-1/media":
                return 200, {}, json.dumps({"id": "cntnr-1"})
            if url == f"{instagram.GRAPH_BASE}/cntnr-1?fields=status_code":
                poll_state["count"] += 1
                sc = "IN_PROGRESS" if poll_state["count"] < 2 else "FINISHED"
                return 200, {}, json.dumps({"status_code": sc})
            if url == f"{instagram.GRAPH_BASE}/ig-acct-1/media_publish":
                return 200, {}, json.dumps({"id": "ig-media-555"})
            return 404, {}, b""

        def _fake_fetcher(url, max_bytes, timeout):
            return media_state["bytes"], media_state["ct"]

        svc = self._make_service(transport=_ig_transport, media_fetcher=_fake_fetcher)

        # Rejects PERSONAL account
        def _personal_transport(method, url, headers, body, timeout):
            return 200, {}, json.dumps({"id": "ig-p", "username": "priv", "account_type": "PERSONAL"})

        svc_p = posting_service_mod.PostingService(data_root=self.root, key_store=self.key_store, transport=_personal_transport)
        with self.assertRaises(PostingError) as pctx:
            svc_p.connect_instagram("tok-p")
        self.assertEqual(pctx.exception.code, "personal_account_unsupported")

        # Connect professional account
        svc.connect_instagram("CANARY_IG_TOKEN_777")
        item = self._create_item(cid="cnt-ig-1", body="Cero Instagram post", platform="instagram")

        # Prepare fetches media, checks sha256, warns host could change it
        prep = svc.prepare(item["id"], "instagram", media_url="https://cdn.example.com/photo.jpg", ai_generated=True)
        self.assertEqual(prep["media"][0]["media_type"], "IMAGE")
        self.assertEqual(prep["media"][0]["sha256"], hashlib.sha256(media_state["bytes"]).hexdigest())
        self.assertTrue(any("could still change" in w["message"].lower() for w in prep["warnings"]))

        # If host mutates the file before confirm, confirm refuses BEFORE creating container!
        media_state["bytes"] = _make_jpeg_bytes(b"img-TAMPERED")
        with self.assertRaises(PostingError) as mctx:
            svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
        self.assertEqual(mctx.exception.code, "payload_changed")
        self.assertFalse(any(c[1].endswith("/media") for c in calls))

        # Now stable MP4 REELS flow
        media_state["bytes"] = _make_mp4_bytes(b"reel-1")
        media_state["ct"] = "video/mp4"
        prep2 = svc.prepare(item["id"], "instagram", media_url="https://cdn.example.com/reel.mp4", ai_generated=True)
        res = svc.confirm(prep2["confirmation_id"], expected_hash=prep2["payload_hash"])
        self.assertEqual(res["post_id"], "ig-media-555")
        media_post = [c for c in calls if c[1] == f"{instagram.GRAPH_BASE}/ig-acct-1/media"][0]
        sent_body = json.loads(media_post[3].decode("utf-8"))
        self.assertEqual(sent_body["media_type"], "REELS")
        self.assertEqual(sent_body["video_url"], "https://cdn.example.com/reel.mp4")
        self.assertTrue(sent_body["is_ai_generated"])

        # Explicit refresh tap does not log token or URL
        log_buf = io.StringIO()
        handler = logging.StreamHandler(log_buf)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            ref = svc.refresh_instagram()
            self.assertTrue(ref["ok"])
        finally:
            root_logger.removeHandler(handler)
        self.assertNotIn("CANARY_IG_TOKEN_777", log_buf.getvalue())
        self.assertNotIn("CANARY_IG_REFRESHED_888", log_buf.getvalue())
        self.assertNotIn("refresh_access_token", log_buf.getvalue())

        # Public-only egress guard blocks http, private IP, localhost, redirect, bad type, >100MB
        for bad_url in ("http://cdn.example.com/a.jpg", "https://127.0.0.1/a.jpg", "https://localhost/a.jpg"):
            with self.assertRaises(PostingError):
                http_client.fetch_public_media(bad_url)
        with self.assertRaises(PostingError):
            http_client.fetch_public_media(
                "https://cdn.example.com/bad.html",
                fetcher=lambda u, m, t: (b"<html>nope</html>", "text/html"),
            )
        with self.assertRaises(PostingError):
            http_client.fetch_public_media(
                "https://cdn.example.com/huge.jpg",
                max_bytes=10,
                fetcher=lambda u, m, t: (_make_jpeg_bytes(b"0" * 50), "image/jpeg"),
            )

    def test_09_x_pkce_oauth_listener_rotation_media_weighted_limit_and_cost(self):
        """X OAuth PKCE listener checks exact Host/path/state once (5m TTL), rotates refresh_token under lock, enforces 280 weighted limit, shows cost, uploads media."""
        x_calls = []
        refresh_counter = {"n": 0}

        def _x_transport(method, url, headers, body, timeout):
            x_calls.append((method, url, dict(headers), body))
            if url == x_provider.OAUTH_TOKEN_URL:
                qs = urllib.parse.parse_qs(body.decode("utf-8"))
                if qs.get("grant_type") == ["authorization_code"]:
                    return 200, {}, json.dumps({
                        "access_token": "CANARY_X_ACC_1",
                        "refresh_token": "CANARY_X_REF_1",
                        "expires_in": 7200,
                    })
                if qs.get("grant_type") == ["refresh_token"]:
                    refresh_counter["n"] += 1
                    n = refresh_counter["n"]
                    return 200, {}, json.dumps({
                        "access_token": f"CANARY_X_ACC_ROT_{n}",
                        "refresh_token": f"CANARY_X_REF_ROT_{n}",
                        "expires_in": 7200,
                    })
            if url == x_provider.USERS_ME_URL:
                return 200, {}, json.dumps({"data": {"id": "x-u-1", "username": "cero_hq", "name": "Cero"}})
            if url == x_provider.MEDIA_INIT_URL:
                return 201, {}, json.dumps({"data": {"id": "x-med-1"}})
            if url == "https://api.x.com/2/media/upload/x-med-1/append":
                return 204, {}, b""
            if url == "https://api.x.com/2/media/upload/x-med-1/finalize":
                return 200, {}, json.dumps({"data": {"id": "x-med-1"}})
            if url == x_provider.TWEETS_URL:
                return 201, {}, json.dumps({"data": {"id": "tw-999", "text": "ok"}})
            return 404, {}, b""

        svc = self._make_service(transport=_x_transport, oauth_port=0)

        # 1. Start OAuth listener (ephemeral port 0)
        oauth = svc.start_x_oauth("x-client-id-pub")
        port = oauth["callback_port"]
        parsed_auth = urllib.parse.urlsplit(oauth["authorize_url"])
        qs = urllib.parse.parse_qs(parsed_auth.query)
        self.assertEqual(f"{parsed_auth.scheme}://{parsed_auth.netloc}{parsed_auth.path}", x_provider.OAUTH_AUTHORIZE_URL)
        self.assertEqual(qs["code_challenge_method"], ["S256"])
        self.assertEqual(qs["scope"], [x_provider.OAUTH_SCOPES])
        state = qs["state"][0]

        # Wrong Host rejected without consuming
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", f"/callback?state={state}&code=c1", skip_host=True)
        conn.putheader("Host", f"evil.example:{port}")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

        # Wrong path -> 404
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/other")
        self.assertEqual(conn.getresponse().status, 404)
        conn.close()

        # Wrong state consumes the one-shot state check and closes listener
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/callback?state=wrong-state&code=c1")
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertFalse(svc._x_oauth.is_active())

        # Fresh OAuth flow succeeds with exact Host, path, state, then closes
        oauth2 = svc.start_x_oauth("x-client-id-pub")
        port2 = oauth2["callback_port"]
        state2 = urllib.parse.parse_qs(urllib.parse.urlsplit(oauth2["authorize_url"]).query)["state"][0]
        conn = http.client.HTTPConnection("127.0.0.1", port2, timeout=5)
        conn.request("GET", f"/callback?state={state2}&code=auth-code-ok")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        conn.close()

        # Listener is now closed (second request fails to connect or returns closed)
        time.sleep(0.05)
        self.assertFalse(svc._x_oauth.is_active())
        self.assertTrue(svc.get_status()["platforms"]["x"]["connected"])

        # 2. Weighted 280 limit & cost estimate
        long_url = "https://example.com/" + ("a" * 300)
        tweet_with_url = f"Cero update {long_url}"
        self.assertLessEqual(x_provider.weighted_length(tweet_with_url), 280)
        too_long = "a" * 281
        item_long = self._create_item(cid="cnt-x-long", body=too_long, platform="x")
        with self.assertRaises(PostingError) as lctx:
            svc.prepare(item_long["id"], "x")
        self.assertEqual(lctx.exception.code, "text_too_long")

        c_nolink = x_provider.estimate_cost("Cero shipped today.")
        self.assertEqual(c_nolink["estimated_usd"], 0.015)
        self.assertIn("prices may change", c_nolink["summary"].lower())
        c_link = x_provider.estimate_cost("Read cero.dev for details", has_media=True)
        self.assertEqual(c_link["estimated_usd"], 0.20)
        self.assertIn("media price unconfirmed", c_link["summary"].lower())

        # 3. Token refresh under lock rotates refresh_token when expired + media upload + tweet body
        self.clock.advance(8000)  # expire access token
        img_rel = "outputs/posts/cnt-x-1/shot.png"
        img_abs = os.path.join(self.root, img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        with open(img_abs, "wb") as fh:
            fh.write(_make_png_bytes(b"x-img"))

        item_x = self._create_item(
            cid="cnt-x-1",
            body=tweet_with_url,
            platform="x",
            files=[{"kind": "image", "path": img_rel}],
        )
        prep_x = svc.prepare(item_x["id"], "x", ai_generated=True)
        self.assertEqual(prep_x["cost"]["estimated_usd"], 0.20)
        res_x = svc.confirm(prep_x["confirmation_id"], expected_hash=prep_x["payload_hash"])
        self.assertEqual(res_x["post_id"], "tw-999")
        self.assertEqual(res_x["post_url"], "https://x.com/cero_hq/status/tw-999")

        # Rotated refresh token was persisted in vault
        saved_tok = svc.vault.load_token("x")
        self.assertEqual(saved_tok["access_token"], "CANARY_X_ACC_ROT_1")
        self.assertEqual(saved_tok["refresh_token"], "CANARY_X_REF_ROT_1")

        # Verify tweet payload
        tw_call = [c for c in x_calls if c[1] == x_provider.TWEETS_URL][0]
        tw_body = json.loads(tw_call[3].decode("utf-8"))
        self.assertEqual(tw_body["text"], tweet_with_url)
        self.assertEqual(tw_body["media"], {"media_ids": ["x-med-1"]})
        self.assertTrue(tw_body["made_with_ai"])

        # Canary check across DB rows
        conn_db = db.connect()
        all_acct = json.dumps([dict(r) for r in conn_db.execute("SELECT * FROM posting_accounts").fetchall()])
        all_att = json.dumps([dict(r) for r in conn_db.execute("SELECT * FROM posting_attempts").fetchall()])
        for canary in ("CANARY_X_ACC_1", "CANARY_X_REF_1", "CANARY_X_ACC_ROT_1", "CANARY_X_REF_ROT_1"):
            self.assertNotIn(canary, all_acct)
            self.assertNotIn(canary, all_att)

    def test_10_http_allowlist_and_no_redirects(self):
        """HTTP client enforces fixed host/path allowlist per provider and blocks redirects."""
        with self.assertRaises(PostingError):
            http_client.validate_provider_url("linkedin", "https://evil.example/v2/userinfo")
        with self.assertRaises(PostingError):
            http_client.validate_provider_url("linkedin", "http://api.linkedin.com/v2/userinfo")
        with self.assertRaises(PostingError):
            http_client.validate_provider_url("instagram", "https://graph.instagram.com/v27.0/me/evil/extra")
        with self.assertRaises(PostingError):
            http_client.validate_provider_url("x", "https://api.x.com/2/dm_conversations")
        with self.assertRaises(PostingError) as rctx:
            http_client.http_request(
                "linkedin",
                "GET",
                linkedin.USERINFO_URL,
                transport=lambda m, u, h, b, t: (302, {"Location": "https://evil.example"}, b""),
            )
        self.assertEqual(rctx.exception.code, "redirect_blocked")


    # --- Review regressions ---

    def _linkedin_service(self, post=None, calls=None):
        def transport(method, url, headers, body, timeout):
            if calls is not None:
                calls.append((method, url, dict(headers)))
            if url == linkedin.USERINFO_URL:
                return 200, {}, json.dumps({"sub": "m1", "name": "Alex"})
            if url == linkedin.INITIALIZE_UPLOAD_URL:
                return 200, {}, json.dumps({"value": {"uploadUrl": "https://www.linkedin.com/dms-uploads/1", "image": "urn:li:image:1"}})
            if method == "PUT":
                return 201, {}, ""
            return post() if post else (201, {"x-restli-id": "urn:li:share:1"}, "")

        svc = self._make_service(transport=transport)
        svc.connect_linkedin("tok-linkedin-1")
        return svc

    def _png_item(self, cid):
        rel = f"outputs/{cid}.png"
        os.makedirs(os.path.join(self.root, "outputs"), exist_ok=True)
        with open(os.path.join(self.root, rel), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        return self._create_item(cid=cid, files=[{"kind": "image", "path": rel}])

    def test_11_unknown_is_not_forgotten_after_many_previews(self):
        svc = self._linkedin_service(post=lambda: (503, {}, ""))
        item = self._create_item(cid="cnt-unk")
        prep = svc.prepare(item["id"], "linkedin")
        with self.assertRaises(PostingError):
            svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
        for _ in range(12):
            self.clock.sleep(1)
            svc.prepare(item["id"], "linkedin", acknowledge_unknown=True)
        with self.assertRaises(PostingError) as caught:
            svc.prepare(item["id"], "linkedin")
        self.assertEqual(caught.exception.code, "previous_unknown")

    def test_12_a_live_post_is_never_offered_again_without_acknowledgement(self):
        svc = self._linkedin_service()
        item = self._create_item(cid="cnt-live")
        prep = svc.prepare(item["id"], "linkedin")
        with patch.object(posting_service_mod.review_studio, "mark_posted", side_effect=RuntimeError("db busy")):
            res = svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
        self.assertTrue(res["ok"])
        with self.assertRaises(PostingError) as caught:
            svc.prepare(item["id"], "linkedin")
        self.assertEqual(caught.exception.code, "already_posted")

    def test_13_lost_answers_to_a_write_are_unknown_not_failed(self):
        class Resp:
            status = 201
            headers = {}

            def read(self, n=-1):
                return b"x" * n

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        opener = type("Opener", (), {"open": lambda self, req, timeout=None: Resp()})()
        with patch.object(http_client.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(PostingError) as caught:
                http_client.http_request("x", "POST", x_provider.TWEETS_URL, body="{}")
        self.assertTrue(caught.exception.uncertain)
        for status in (303, 408):
            with self.assertRaises(PostingError) as caught:
                http_client.http_request("x", "POST", x_provider.TWEETS_URL, body="{}", transport=lambda *a, _s=status: (_s, {}, ""))
            self.assertTrue(caught.exception.uncertain, status)

    def test_14_linkedin_image_upload_carries_the_token(self):
        calls = []
        svc = self._linkedin_service(calls=calls)
        prep = svc.prepare(self._png_item("cnt-img")["id"], "linkedin")
        svc.confirm(prep["confirmation_id"], expected_hash=prep["payload_hash"])
        put = [h for method, _, h in calls if method == "PUT"][0]
        self.assertEqual(put.get("Authorization"), "Bearer tok-linkedin-1")

    def test_15_malformed_token_is_refused_before_any_request(self):
        calls = []
        svc = self._make_service(transport=lambda *a: calls.append(a) or (500, {}, ""))
        for connect in (svc.connect_linkedin, svc.connect_instagram):
            with self.assertRaises(PostingError) as caught:
                connect("SECRET-TOKEN\nsecond-line")
            self.assertNotIn("SECRET-TOKEN", caught.exception.message)
        self.assertEqual(calls, [])

    def test_16_confirm_needs_the_hash_that_was_shown(self):
        svc = self._linkedin_service()
        prep = svc.prepare(self._create_item(cid="cnt-hash")["id"], "linkedin")
        with self.assertRaises(PostingError):
            svc.confirm(prep["confirmation_id"])

    def test_17_image_is_shown_and_confirmation_ids_are_not_listed(self):
        svc = self._linkedin_service()
        prep = svc.prepare(self._png_item("cnt-show")["id"], "linkedin")
        self.assertTrue(prep["media"][0].get("preview_data_uri", "").startswith("data:image/png;base64,"))
        self.assertNotIn(prep["confirmation_id"], json.dumps(svc.list_attempts()))

    def test_18_x_cost_errs_high_for_any_domain(self):
        for text in ("Read cero.tech today", "see magnm.shop", "https://example.com"):
            self.assertEqual(x_provider.estimate_cost(text)["estimated_usd"], 0.20, text)
        self.assertEqual(x_provider.estimate_cost("Cero is live. Try it today.")["estimated_usd"], 0.015)

    def test_19_x_gif_is_refused(self):
        os.makedirs(os.path.join(self.root, "outputs"), exist_ok=True)
        with open(os.path.join(self.root, "outputs", "a.gif"), "wb") as fh:
            fh.write(b"GIF89a" + b"0" * 20)
        with self.assertRaises(PostingError):
            posting_service_mod._resolve_local_media({"files": [{"kind": "image", "path": "outputs/a.gif"}]}, "x")

    def test_20_disconnect_during_refresh_leaves_no_token(self):
        helper = []

        def transport(method, url, headers, body, timeout):
            worker = threading.Thread(target=svc.disconnect, args=("x",))
            worker.start()
            worker.join(0.5)
            helper.append(worker)
            return 200, {}, json.dumps({"access_token": "new", "refresh_token": "r2", "expires_in": 7200})

        svc = self._make_service(transport=transport)
        svc.vault.save_token("x", {"access_token": "old", "refresh_token": "r1", "client_id": "c", "expires_at": "2000-01-01T00:00:00+00:00"})
        x_provider.refresh_token_locked(svc.vault, svc._x_refresh_lock, now_fn=self.clock.now_wall, transport=transport)
        helper[0].join(5)
        self.assertFalse(svc.vault.has_token("x"))

    def test_21_items_confidential_through_their_thread_never_post(self):
        svc = self._linkedin_service()
        item = self._create_item(cid="cnt-inherit")
        with patch.object(cloud_sync, "confidential_scope", return_value=(set(), {item["id"]}, lambda text: False)):
            with self.assertRaises(PostingError) as caught:
                svc.prepare(item["id"], "linkedin")
        self.assertEqual(caught.exception.code, "confidential_item")


if __name__ == "__main__":
    unittest.main()

"""Tests for engine.cloud_sync, db v13 cloud_sync_state, and /api/account/sync routes."""

import io
import json
import os
import sys
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import db
import paths
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


class CloudSyncTests(unittest.TestCase):
    """Verify mirror mapping, privacy rules, batching, cursors, errors, device status, and routes."""

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

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)
        # The real bundled rules: Cero is released, LazyLayout is confidential.
        shutil.copy(os.path.join(self._prev_resource_root, "config", "product_rules.json"),
                    os.path.join(self.tmpdir, "config", "product_rules.json"))

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

    def _sign_in_user(self, user_id="user-1", email="member@example.com"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })

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
                    "active": True,
                }])

            if handler_fn is not None:
                res = handler_fn(entry)
                if res is not None:
                    return res

            return _FakeHttpResponse(None, status=201)

        return requests, fake_urlopen

    # The task board and the automation state travel as their own kinds (tested in test_phone_control.py); these tests
    # are about the records of the workspace itself, so the two derived kinds are left out of the counts.
    DERIVED_KINDS = ("board_item", "automation")

    def _mirror_uploads(self, requests):
        out = []
        for r in requests:
            if "/rest/v1/mirror_records" in r["url"] and r["method"] == "POST":
                out.extend(x for x in (r["body"] or []) if x.get("kind") not in self.DERIVED_KINDS)
        return out

    def test_01_mapping_for_every_kind_and_never_mirrored_excluded(self):
        self._sign_in_user("u-map")

        # 1. chat_session (with system prompt, tool args, tool result, multimodal image)
        db.create_agent_session({
            "id": "sess-1",
            "agent_id": "writer",
            "status": "done",
            "title": "Write launch post",
            "parent_session_id": "sess-parent",
            "messages": [
                {"role": "system", "content": "SECRET_SYSTEM_INSTRUCTIONS"},
                {"role": "user", "content": [
                    {"type": "text", "text": "Draft a post for Cero"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAABBBB"}},
                ]},
                {
                    "role": "assistant",
                    "content": "Checking tools",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "write_draft", "arguments": "{\"secret_arg\": \"DROP_ME\"}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "{\"tool_result_secret\": \"NEVER_COPY\"}"},
                {"role": "assistant", "content": "Here is your draft."},
            ],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:05:00Z",
        })

        # 2. team_message
        db.save_message({
            "id": "msg-1",
            "sender": "CEO",
            "sender_name": "CEO (Founder)",
            "text": "Hey @cmo please prepare the launch update for Monday morning " + ("x" * 60),
            "mentions": ["cmo"],
            "timestamp": "2026-11-21T01:10:00Z",
        })

        # 3. chat_turn (legacy)
        db.save_agent_chat_history("cmo", [
            {"sender": "user", "name": "CEO", "text": "Legacy question", "timestamp": "2026-11-20T10:00:00Z"},
        ])

        # 4 & 5 & 6. content_item + content_version + posted_link
        db.create_content_item(
            "cnt-1",
            "social",
            "linkedin",
            "Cero Launch Post",
            "Body v1",
            notes="Initial draft",
            created_by="writer",
            session_id="sess-1",
            created_at="2026-11-21T01:15:00Z",
        )
        db.mark_posted("cnt-1", url="https://linkedin.com/posts/123", platform="linkedin", posted_at="2026-11-21T02:00:00Z")

        # 7. research_note
        db.save_research_note(
            "note-1",
            "HN Thread",
            "Foundings on HN",
            url="https://news.ycombinator.com/item?id=1",
            source="hackernews",
            agent_id="researcher",
            created_at="2026-11-21T01:20:00Z",
        )

        # 8. review_comment (with anchor geometry that must NOT be mirrored)
        db.create_review_comment(
            "cnt-1",
            1,
            "text",
            {"start": 0, "end": 4, "quote": "Body", "rects": [{"x": 12, "y": 34}]},
            "Tighten the opening line here please",
            author="reviewer",
            comment_id="cmt-1",
            created_at="2026-11-21T01:25:00Z",
        )

        # 9. metric
        db.record_metric(
            "met-1",
            "cnt-1",
            "linkedin",
            {"impressions": 4200, "likes": 88},
            "manual",
            captured_at="2026-11-21T03:00:00Z",
        )

        # 10. brief (workflow_runs with state_json that must NOT be mirrored)
        db.create_workflow_run({
            "id": "wf-1",
            "kind": "social_campaign",
            "status": "running",
            "current_step": 1,
            "inputs": {"topic": "Cero release highlights\nSecond line ignored in title"},
            "state": {"huge_internal_state": "DO_NOT_MIRROR_STATE_JSON"},
            "created_at": "2026-11-21T01:30:00Z",
            "updated_at": "2026-11-21T01:35:00Z",
        })

        # 11. task (jobs)
        db.create_job({
            "id": "job-1",
            "name": "repo_sync",
            "status": "done",
            "progress": 100,
            "message": "Synced 2 repos",
            "created_at": "2026-11-21T01:40:00Z",
            "started_at": "2026-11-21T01:40:01Z",
            "finished_at": "2026-11-21T01:40:05Z",
        })

        # Never-mirrored tables and files
        db.create_agent_memory("writer", "NEVER_MIRROR_MEMORY", source="manual")
        db.set_search_cache("hn", "cero", [{"title": "NEVER_MIRROR_SEARCH_CACHE"}])
        db.log_tool_call("sess-1", "writer", "read_file", json.dumps({"secret": "NEVER_MIRROR_TOOL_CALLS"}), ok=True)
        db.log_run(role="writer", provider="nim", model_used="NEVER_MIRROR_RUNS", ok=True)
        db.create_pitch_proposal(
            proposal_id="prop-1",
            slug="secret-client",
            kind="proposal",
            status="draft",
            client_name="NEVER_MIRROR_PITCH",
            business_name="Secret Biz",
            contact={},
            preset={},
            price_overrides={},
            custom_items=[],
            notes="secret pitch note",
            created_by="user",
            share_id="share-1",
            pitch_commit="abc",
        )
        with open(os.path.join(self.tmpdir, ".env"), "w", encoding="utf-8") as f:
            f.write("NVIDIA_NIM_API_KEY=nvapi-NEVER_MIRROR_ENV_KEY\n")
        with open(os.path.join(self.tmpdir, "secret_disk_file.txt"), "w", encoding="utf-8") as f:
            f.write("NEVER_MIRROR_DISK_FILE\n")

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            ok = cloud_sync.sync_once()
        self.assertTrue(ok)

        uploaded = self._mirror_uploads(requests)
        by_key = {(r["kind"], r["local_id"]): r for r in uploaded}

        expected_kinds = {
            "chat_session",
            "team_message",
            "chat_turn",
            "content_item",
            "content_version",
            "posted_link",
            "research_note",
            "review_comment",
            "metric",
            "brief",
            "task",
        }
        self.assertEqual({r["kind"] for r in uploaded}, expected_kinds)

        # Check chat_session mapping & filtering
        cs = by_key[("chat_session", "sess-1")]
        self.assertEqual(cs["parent_local_id"], "sess-parent")
        self.assertEqual(cs["title"], "Write launch post")
        self.assertEqual(cs["data"]["agent_id"], "writer")
        msgs = cs["data"]["messages"]
        self.assertEqual(msgs[0], {"role": "user", "text": "Draft a post for Cero"})
        self.assertEqual(msgs[1]["role"], "assistant")
        self.assertIn("used tool write_draft", msgs[1]["note"])
        self.assertEqual(msgs[2], {"role": "assistant", "text": "Here is your draft."})

        # Check team_message title <= 80 chars
        tm = by_key[("team_message", "msg-1")]
        self.assertLessEqual(len(tm["title"]), 80)
        self.assertEqual(tm["data"]["mentions"], ["cmo"])

        # Check chat_turn parent_local_id = agent_id
        ct_rows = [r for r in uploaded if r["kind"] == "chat_turn"]
        self.assertEqual(len(ct_rows), 1)
        self.assertEqual(ct_rows[0]["parent_local_id"], "cmo")

        # Check content_version title = v1
        cv_rows = [r for r in uploaded if r["kind"] == "content_version"]
        self.assertEqual(cv_rows[0]["title"], "v1")
        self.assertEqual(cv_rows[0]["parent_local_id"], "cnt-1")

        # Check posted_link
        pl = by_key[("posted_link", "cnt-1")]
        self.assertEqual(pl["parent_local_id"], "cnt-1")
        self.assertEqual(pl["data"]["posted_url"], "https://linkedin.com/posts/123")

        # Check review_comment excludes anchor geometry
        rc = by_key[("review_comment", "cmt-1")]
        self.assertNotIn("anchor", rc["data"])
        self.assertNotIn("anchor_json", rc["data"])
        self.assertNotIn("rects", json.dumps(rc))

        # Check brief title and exclusion of state_json
        br = by_key[("brief", "wf-1")]
        self.assertEqual(br["title"], "social_campaign: Cero release highlights")
        self.assertNotIn("DO_NOT_MIRROR_STATE_JSON", json.dumps(br))

        # Check task
        tk = by_key[("task", "job-1")]
        self.assertEqual(tk["title"], "repo_sync")
        self.assertEqual(tk["data"]["progress"], 100)

        # Ensure never-mirrored data never appears anywhere in any uploaded request
        all_json = json.dumps(requests)
        for forbidden in (
            "SECRET_SYSTEM_INSTRUCTIONS",
            "DROP_ME",
            "NEVER_COPY",
            "AAAABBBB",
            "NEVER_MIRROR_MEMORY",
            "NEVER_MIRROR_SEARCH_CACHE",
            "NEVER_MIRROR_TOOL_CALLS",
            "NEVER_MIRROR_RUNS",
            "NEVER_MIRROR_PITCH",
            "NEVER_MIRROR_ENV_KEY",
            "NEVER_MIRROR_DISK_FILE",
        ):
            self.assertNotIn(forbidden, all_json)

    def test_02_confidential_session_and_content_stubbed_and_retroactive_transition(self):
        self._sign_in_user("u-conf")

        db.create_agent_session({
            "id": "sess-conf",
            "agent_id": "writer",
            "status": "done",
            "title": "Secret LazyLayout Launch Plan",
            "confidential": True,
            "messages": [{"role": "user", "content": "SECRET_PROMPT_LAZYLAYOUT_123"}],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:01:00Z",
        })
        db.create_content_item(
            "cnt-conf",
            "blog",
            "blog",
            "Secret LazyLayout Feature Reveal",
            "SECRET_BODY_DO_NOT_LEAK",
            notes="SECRET_NOTES_DO_NOT_LEAK",
            created_by="writer",
            session_id="sess-conf",
            created_at="2026-11-21T01:02:00Z",
        )
        db.create_review_comment(
            "cnt-conf",
            1,
            "general",
            {},
            "SECRET_COMMENT_DO_NOT_LEAK",
            author="reviewer",
            comment_id="cmt-conf",
        )
        db.record_metric("met-conf", "cnt-conf", "blog", {"views": 99999}, "manual")
        db.mark_posted("cnt-conf", url="https://secret.example.com/leak", platform="blog")

        # Also create a non-confidential session first, sync it, then flip confidential=1
        db.create_agent_session({
            "id": "sess-flip",
            "agent_id": "cmo",
            "status": "running",
            "title": "Normal Chat Title",
            "confidential": False,
            "messages": [{"role": "user", "content": "Initial public message"}],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:00:00Z",
        })

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertTrue(cloud_sync.sync_once())

        uploaded1 = self._mirror_uploads(requests)
        payload1_json = json.dumps(uploaded1)
        for secret in (
            "Secret LazyLayout Launch Plan",
            "SECRET_PROMPT_LAZYLAYOUT_123",
            "Secret LazyLayout Feature Reveal",
            "SECRET_BODY_DO_NOT_LEAK",
            "SECRET_NOTES_DO_NOT_LEAK",
            "SECRET_COMMENT_DO_NOT_LEAK",
            "99999",
            "https://secret.example.com/leak",
        ):
            self.assertNotIn(secret, payload1_json)

        by_key1 = {(r["kind"], r["local_id"]): r for r in uploaded1}
        self.assertEqual(by_key1[("chat_session", "sess-conf")]["title"], "Confidential chat")
        self.assertEqual(by_key1[("chat_session", "sess-conf")]["data"]["confidential"], True)
        self.assertEqual(by_key1[("content_item", "cnt-conf")]["title"], "Confidential item")
        self.assertEqual(by_key1[("review_comment", "cmt-conf")]["title"], "Confidential item")
        self.assertEqual(by_key1[("metric", "met-conf")]["title"], "Confidential item")
        self.assertEqual(by_key1[("posted_link", "cnt-conf")]["title"], "Confidential item")
        self.assertEqual(by_key1[("chat_session", "sess-flip")]["title"], "Normal Chat Title")

        # Now mark sess-flip confidential and verify next sync replaces it with a stub
        db.update_agent_session("sess-flip", confidential=True, updated_at="2026-11-21T01:10:00Z")
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertTrue(cloud_sync.sync_once())

        uploaded2 = self._mirror_uploads(requests)
        self.assertEqual(len(uploaded2), 1)
        self.assertEqual(uploaded2[0]["local_id"], "sess-flip")
        self.assertEqual(uploaded2[0]["title"], "Confidential chat")
        self.assertTrue(uploaded2[0]["data"]["confidential"])
        self.assertNotIn("Initial public message", json.dumps(uploaded2))

    def test_03_change_detection_deletions_and_per_user_isolation(self):
        self._sign_in_user("user-A", "a@example.com")
        db.save_research_note("n1", "Title 1", "Body 1", created_at="2026-11-21T01:00:00Z")
        db.save_research_note("n2", "Title 2", "Body 2", created_at="2026-11-21T01:01:00Z")

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        self.assertEqual(len(self._mirror_uploads(requests)), 2)

        # Second sync with no changes -> zero mirror_records uploads
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        self.assertEqual(len(self._mirror_uploads(requests)), 0)

        # Update n1, delete n2 locally
        conn = db.connect()
        with conn:
            conn.execute("UPDATE research_notes SET body = 'Body 1 updated' WHERE id = 'n1'")
            conn.execute("DELETE FROM research_notes WHERE id = 'n2'")

        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        uploaded = self._mirror_uploads(requests)
        by_id = {r["local_id"]: r for r in uploaded}
        self.assertEqual(set(by_id.keys()), {"n1", "n2"})
        self.assertIsNone(by_id["n1"]["deleted_at"])
        self.assertEqual(by_id["n1"]["data"]["body"], "Body 1 updated")
        self.assertIsNotNone(by_id["n2"]["deleted_at"])

        # Subsequent sync should NOT re-send n2 deletion
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        self.assertEqual(len(self._mirror_uploads(requests)), 0)

        # Different user signing in starts fresh and uploads n1
        self._sign_in_user("user-B", "b@example.com")
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        uploaded_b = self._mirror_uploads(requests)
        self.assertEqual(len(uploaded_b), 1)
        self.assertEqual(uploaded_b[0]["owner_id"], "user-B")
        self.assertEqual(uploaded_b[0]["local_id"], "n1")

    def test_04_batching_by_count_and_bytes_and_truncation_cap(self):
        self._sign_in_user("u-batch")

        # 65 small notes -> batches of 50 and 15
        for i in range(65):
            db.save_research_note(f"bn-{i:03d}", f"Note {i}", f"Short body {i}", created_at="2026-11-21T01:00:00Z")

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        mirror_reqs = [r for r in requests if "/rest/v1/mirror_records" in r["url"]]
        self.assertEqual([len(r["body"]) for r in mirror_reqs], [50, 15])

        # Byte-size batching (>1 MB across 7 large ~190 KB notes) and 200 KB truncation cap
        for i in range(7):
            db.save_research_note(f"big-{i}", f"Big {i}", "A" * 250_000, created_at="2026-11-21T02:00:00Z")

        # Chat session > 200 KB drops oldest messages first
        big_msgs = [
            {"role": "user", "content": f"msg_{idx}_" + ("B" * 30_000)}
            for idx in range(10)
        ]
        db.create_agent_session({
            "id": "sess-big",
            "agent_id": "writer",
            "status": "done",
            "title": "Big Session",
            "messages": big_msgs,
            "created_at": "2026-11-21T02:00:00Z",
            "updated_at": "2026-11-21T02:05:00Z",
        })

        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()

        mirror_reqs2 = [r for r in requests if "/rest/v1/mirror_records" in r["url"]]
        self.assertGreater(len(mirror_reqs2), 1)
        for req in mirror_reqs2:
            raw_bytes = len(json.dumps(req["body"], ensure_ascii=False).encode("utf-8"))
            self.assertLessEqual(raw_bytes, cloud_sync.MAX_BATCH_BYTES)
            for rec in req["body"]:
                data_bytes = len(json.dumps(rec["data"], ensure_ascii=False).encode("utf-8"))
                self.assertLessEqual(data_bytes, cloud_sync.MAX_RECORD_DATA_BYTES)
                if rec["kind"] in self.DERIVED_KINDS:
                    continue  # the small board copy of the big session is not itself big
                self.assertTrue(rec["data"].get("truncated"))

        # Verify chat_session kept newest messages and dropped oldest
        sess_rec = next(
            rec for req in mirror_reqs2 for rec in req["body"] if rec["local_id"] == "sess-big"
        )
        kept_texts = [m["text"] for m in sess_rec["data"]["messages"]]
        self.assertFalse(any(t.startswith("msg_0_") for t in kept_texts))
        self.assertTrue(any(t.startswith("msg_9_") for t in kept_texts))

    def test_05_activity_cursor_and_outbox_sent_at_only_after_success(self):
        self._sign_in_user("u-act")
        db.log_activity("writer", "CEO", "draft_created", "cnt-1", ts="2026-11-21T01:00:00Z")
        db.append_sync_outbox("proposal.created", {"id": "p1", "title": "Proposal 1"}, event_id="evt-1")
        db.append_sync_outbox("proposal.updated", {"big": "X" * 40_000, "title": "Big Proposal"}, event_id="evt-2")

        # First attempt fails on activity_events with 500 -> cursor and sent_at must remain unset
        def fail_activity(entry):
            if "/rest/v1/activity_events" in entry["url"]:
                raise _http_error(entry["url"], 500, {"message": "server error"})
            return None

        requests, fake_urlopen = self._mock_supabase_router(fail_activity)
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            ok = cloud_sync.sync_once()
        self.assertFalse(ok)
        self.assertIsNone(db.get_cloud_sync_value("u-act", cloud_sync.ACTIVITY_CURSOR_KIND, cloud_sync.ACTIVITY_CURSOR_ID))
        self.assertEqual(len(db.list_pending_sync_outbox()), 2)

        # Now succeed -> cursor advances and outbox sent_at is set
        requests2, fake_urlopen2 = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen2):
            ok2 = cloud_sync.sync_once()
        self.assertTrue(ok2)

        self.assertEqual(
            db.get_cloud_sync_value("u-act", cloud_sync.ACTIVITY_CURSOR_KIND, cloud_sync.ACTIVITY_CURSOR_ID),
            "1",
        )
        self.assertEqual(len(db.list_pending_sync_outbox()), 0)

        act_rows = [
            row
            for r in requests2
            if "/rest/v1/activity_events" in r["url"]
            for row in (r["body"] or [])
        ]
        by_kind = {row["kind"]: row for row in act_rows}
        self.assertIn("activity", by_kind)
        self.assertIn("outbox.proposal.created", by_kind)
        self.assertIn("outbox.proposal.updated", by_kind)
        # Oversized outbox payload capped <= 32 KB
        self.assertLessEqual(
            len(json.dumps(by_kind["outbox.proposal.updated"]["data"], ensure_ascii=False).encode("utf-8")),
            32768,
        )
        self.assertTrue(by_kind["outbox.proposal.updated"]["data"].get("truncated"))

        # Next sync does not re-send old activity
        requests2.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen2):
            cloud_sync.sync_once()
        act_again = [r for r in requests2 if "/rest/v1/activity_events" in r["url"]]
        self.assertEqual(act_again, [])

    def test_06_backoff_on_5xx_and_422_per_record_skip_until_changed(self):
        self._sign_in_user("u-err")
        db.save_research_note("good-1", "Good", "Valid", created_at="2026-11-21T01:00:00Z")
        db.save_research_note("bad-422", "Bad", "Will trigger 422", created_at="2026-11-21T01:00:00Z")

        # 503 keeps state unchanged and increments backoff steps
        def fail_503(entry):
            if "/rest/v1/mirror_records" in entry["url"]:
                raise _http_error(entry["url"], 503, {"message": "unavailable"})
            return None

        _, urlopen_503 = self._mock_supabase_router(fail_503)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_503):
            self.assertFalse(cloud_sync.sync_once())
        self.assertEqual(db.get_cloud_sync_state("u-err"), {})
        self.assertEqual(cloud_sync._failure_count, 1)

        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_503):
            self.assertFalse(cloud_sync.sync_once())
        self.assertEqual(cloud_sync._failure_count, 2)

        # 422 on bad-422 in batch -> falls back to individual upload; good-1 succeeds, bad-422 stored with error
        def fail_422_bad(entry):
            if "/rest/v1/mirror_records" in entry["url"]:
                if any(rec["local_id"] == "bad-422" for rec in entry["body"]):
                    raise _http_error(entry["url"], 422, {"message": "invalid payload"})
            return None

        requests, urlopen_422 = self._mock_supabase_router(fail_422_bad)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_422):
            self.assertTrue(cloud_sync.sync_once())

        state = db.get_cloud_sync_state("u-err")
        self.assertIsNone(state[("research_note", "good-1")]["error"])
        self.assertIn("invalid payload", state[("research_note", "bad-422")]["error"])

        # Next cycle skips bad-422 while unchanged
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_422):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(self._mirror_uploads(requests), [])

        # Once bad-422 changes locally, it IS retried
        conn = db.connect()
        with conn:
            conn.execute("UPDATE research_notes SET body = 'Fixed body' WHERE id = 'bad-422'")
        requests_fixed, urlopen_ok = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_ok):
            self.assertTrue(cloud_sync.sync_once())
        uploaded_fixed = self._mirror_uploads(requests_fixed)
        self.assertEqual([r["local_id"] for r in uploaded_fixed], ["bad-422"])
        self.assertIsNone(db.get_cloud_sync_state("u-err")[("research_note", "bad-422")]["error"])

    def test_07_signed_out_no_requests_and_401_refresh(self):
        db.save_research_note("n-out", "Out", "Text", created_at="2026-11-21T01:00:00Z")

        with patch("team_auth.urllib.request.urlopen") as mock_open:
            self.assertFalse(cloud_sync.sync_once())
            mock_open.assert_not_called()

        # Signed in, server returns 401 on first mirror upload -> team_auth refreshes token and retries
        self._sign_in_user("u-401")
        calls = {"mirror": 0}

        def refresh_router(entry):
            if "/auth/v1/token" in entry["url"]:
                return _FakeHttpResponse({
                    "access_token": "tok-refreshed",
                    "refresh_token": "ref-2",
                    "expires_in": 3600,
                    "user": {"id": "u-401", "email": "member@example.com"},
                })
            if "/rest/v1/mirror_records" in entry["url"]:
                calls["mirror"] += 1
                if calls["mirror"] == 1:
                    raise _http_error(entry["url"], 401, {"message": "JWT expired"})
            return None

        requests, urlopen_401 = self._mock_supabase_router(refresh_router)
        with patch("team_auth.urllib.request.urlopen", side_effect=urlopen_401):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(calls["mirror"], 2)
        self.assertTrue(any("/auth/v1/token" in r["url"] for r in requests))

    def test_08_device_status_working_vs_online_and_offline_on_stop(self):
        self._sign_in_user("u-dev")

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        dev_rows = [r["body"][0] for r in requests if "/rest/v1/device_status" in r["url"]]
        self.assertEqual(dev_rows[-1]["status"], "online")
        self.assertEqual(dev_rows[-1]["running_agents"], [])
        self.assertEqual(dev_rows[-1]["device_kind"], "desktop")

        # Running agent session -> status working with running_agents
        db.create_agent_session({
            "id": "sess-run",
            "agent_id": "writer",
            "status": "running",
            "messages": [],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:00:00Z",
        })
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.sync_once()
        dev_rows2 = [r["body"][0] for r in requests if "/rest/v1/device_status" in r["url"]]
        self.assertEqual(dev_rows2[-1]["status"], "working")
        self.assertEqual(dev_rows2[-1]["running_agents"], ["writer"])

        # Stop sends best-effort offline status
        requests.clear()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            cloud_sync.stop(timeout=1.0)
        dev_rows3 = [r["body"][0] for r in requests if "/rest/v1/device_status" in r["url"]]
        self.assertEqual(dev_rows3[-1]["status"], "offline")

    def test_09_account_sync_http_routes_and_token_gate(self):
        server.ENGINE_TOKEN = "sync-secret"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"{base}/api/account/sync", timeout=5)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

            req = urllib.request.Request(
                f"{base}/api/account/sync",
                headers={"X-Magnm-Token": "sync-secret"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                st = json.loads(resp.read().decode("utf-8"))
                self.assertIn("enabled", st)
                self.assertIn("signed_in", st)
                self.assertIn("synced_count", st)
                self.assertIn("pending", st)
                self.assertIn("running", st)

            with patch("cloud_sync.request_sync") as mock_req_sync:
                post_req = urllib.request.Request(
                    f"{base}/api/account/sync",
                    data=b"{}",
                    headers={
                        "X-Magnm-Token": "sync-secret",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(post_req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                mock_req_sync.assert_called_once_with(immediate=True)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_10_mentions_of_confidential_products_are_stubbed_everywhere(self):
        """Records, sub-chats, activity and outbox rows naming a confidential product carry no text."""
        self._sign_in_user("u-mention")
        db.create_agent_session({
            "id": "sess-parent", "agent_id": "cmo", "status": "done", "title": "Plan",
            "confidential": True, "messages": [{"role": "user", "content": "hello"}],
            "created_at": "2026-11-21T01:00:00Z", "updated_at": "2026-11-21T01:00:00Z",
        })
        db.create_agent_session({
            "id": "sess-child", "agent_id": "writer", "status": "done", "title": "Helper",
            "parent_session_id": "sess-parent", "confidential": False,
            "messages": [{"role": "user", "content": "CHILD_SECRET_TEXT"}],
            "created_at": "2026-11-21T01:00:00Z", "updated_at": "2026-11-21T01:00:00Z",
        })
        db.save_message({"id": "msg-lz", "sender": "CEO", "sender_name": "CEO",
                         "text": "Draft the LazyLayout teaser MSG_SECRET", "mentions": [],
                         "timestamp": "2026-11-21T01:10:00Z"})
        db.save_message({"id": "msg-ok", "sender": "CEO", "sender_name": "CEO",
                         "text": "Post about Cero today", "mentions": [], "timestamp": "2026-11-21T01:11:00Z"})
        db.log_activity("writer", "CEO", "LazyLayout ACTIVITY_SECRET", "file.md", ts="2026-11-21T01:12:00Z")
        db.append_sync_outbox("pitch.proposal.updated", {"note": "LazyLayout OUTBOX_SECRET"})

        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertTrue(cloud_sync.sync_once())

        uploaded = json.dumps([r["body"] for r in requests if r["method"] == "POST"])
        for secret in ("CHILD_SECRET_TEXT", "MSG_SECRET", "ACTIVITY_SECRET", "OUTBOX_SECRET", "LazyLayout"):
            self.assertNotIn(secret, uploaded)
        self.assertIn("Post about Cero today", uploaded)
        child = [r for r in self._mirror_uploads(requests) if r["local_id"] == "sess-child"][0]
        self.assertTrue(child["data"].get("confidential"))
        self.assertEqual(child["title"], "Confidential chat")

    def test_11_unreadable_product_rules_pause_sync_without_uploading(self):
        """If the product rules cannot be read, nothing is uploaded and an error is shown."""
        self._sign_in_user("u-norules")
        db.save_message({"id": "msg-1", "sender": "CEO", "sender_name": "CEO", "text": "hello",
                         "mentions": [], "timestamp": "2026-11-21T01:10:00Z"})
        os.remove(os.path.join(self.tmpdir, "config", "product_rules.json"))
        requests, fake_urlopen = self._mock_supabase_router()
        with patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertFalse(cloud_sync.sync_once())
        self.assertEqual([r for r in requests if "/rest/v1/profiles" not in r["url"]], [])
        self.assertIn("product rules", cloud_sync.status()["last_error"])

if __name__ == "__main__":
    unittest.main()

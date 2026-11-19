"""Unit tests for Review Studio engine and HTTP routes (Task T3.5)."""

import importlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import ai_gateway
import db
import events
import jobs
import key_pool
import paths
import posting_kit
import review_studio
import server


class _FakeHTTPResponse:
    """Minimal context-manager mock for urllib.request.urlopen responses."""

    def __init__(self, body_dict, status=200):
        self._raw = json.dumps(body_dict).encode("utf-8")
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _StubHandler(server.CommandCenterHandler):
    """In-memory HTTP handler stub for exercising GET and POST routes without sockets."""

    def __init__(self, path, method="GET", body_dict=None, raw_body=None, headers=None):
        self.path = path
        self.command = method
        if raw_body is not None:
            raw = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
        elif body_dict is not None:
            raw = json.dumps(body_dict).encode("utf-8")
        else:
            raw = b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        if (body_dict is not None or raw_body is not None) and "Content-Length" not in hdrs:
            hdrs["Content-Length"] = str(len(raw))
        self.headers = hdrs
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

    def response_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _wait_until(predicate, timeout=4.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _create_sample_item(
    content_id="cnt_test_1",
    content_type="post",
    platform="linkedin",
    title="Cero terminal release",
    body="First line hook for developers.\nSecond sentence explains Cero speed.\nThird sentence wraps up.",
    product_id="cero",
    files=None,
    notes=None,
):
    return db.create_content_item(
        content_id=content_id,
        content_type=content_type,
        platform=platform,
        title=title,
        body=body,
        notes=notes,
        status="draft",
        product_id=product_id,
        created_by="writer",
        files=files or [],
    )


class ReviewStudioTests(unittest.TestCase):
    """Comprehensive tests for Review Studio engine and HTTP endpoints."""

    def setUp(self):
        jobs.stop_worker(timeout=2.0)
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_token = server.ENGINE_TOKEN
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()
        ai_gateway.ENV_PATH = paths.data(".env")
        key_pool.reset()
        server.ENGINE_TOKEN = ""

    def tearDown(self):
        jobs.stop_worker(timeout=2.0)
        server.ENGINE_TOKEN = self._prev_token
        key_pool.reset()
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        ai_gateway.ENV_PATH = paths.data(".env")
        self._tmpdir_obj.cleanup()

    def _call(self, method, path, body_dict=None, raw_body=None, headers=None):
        h = _StubHandler(path, method=method, body_dict=body_dict, raw_body=raw_body, headers=headers)
        if method == "GET":
            h.do_GET()
        elif method == "POST":
            h.do_POST()
        else:
            raise ValueError(method)
        return h

    def test_01_migration_from_v8_preserves_content_and_versions(self):
        """Upgrading an existing v8 database to v9 keeps all content_items and content_versions intact."""
        legacy_db_path = os.path.join(self.tmpdir, "state", "legacy_v8.db")
        os.makedirs(os.path.dirname(legacy_db_path), exist_ok=True)
        db.reset_connections()
        db.set_db_path(legacy_db_path)

        conn = db.connect()
        with conn:
            conn.execute(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', 'done')")
            for ver, fn in db._MIGRATIONS:
                if ver <= 8:
                    fn(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (ver, "2026-11-18T00:00:00Z"),
                    )
            conn.execute(
                """
                INSERT INTO content_items (
                    id, type, platform, title, status, product_id, created_by,
                    session_id, current_version, created_at, updated_at, files_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "cnt_v8",
                    "post",
                    "x",
                    "Existing v8 post",
                    "draft",
                    "cero",
                    "writer",
                    None,
                    2,
                    "2026-11-18T00:00:00Z",
                    "2026-11-18T01:00:00Z",
                    "[]",
                ),
            )
            conn.execute(
                "INSERT INTO content_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("ver_1", "cnt_v8", 1, "Body v1", "Initial", "writer", "2026-11-18T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO content_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("ver_2", "cnt_v8", 2, "Body v2", "Edited", "writer", "2026-11-18T01:00:00Z"),
            )

        db.reset_connections()
        db.init_db()

        conn2 = db.connect()
        versions = [r["version"] for r in conn2.execute("SELECT version FROM schema_version ORDER BY version").fetchall()]
        self.assertEqual(versions, list(range(1, db.SCHEMA_VERSION + 1)))

        tables = {
            r["name"]
            for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        self.assertTrue({"review_comments", "review_suggestions", "review_checklist"}.issubset(tables))

        item = db.get_content_item("cnt_v8", include_versions=True)
        self.assertIsNotNone(item)
        self.assertEqual(item["current_version"], 2)
        self.assertEqual(item["body"], "Body v2")
        self.assertEqual([v["version"] for v in item["versions"]], [1, 2])

    def test_02_get_review_payload_and_confidential_flag(self):
        """get_review returns complete payload including product, confidential flag, checklist, and limits."""
        _create_sample_item(
            content_id="cnt_cero",
            product_id="cero",
            body="Cero launches faster.",
        )
        _create_sample_item(
            content_id="cnt_lazy",
            product_id="lazylayout",
            title="Teaser",
            body="Something else is coming from LazyLayout.",
        )

        rev_cero = review_studio.get_review("cnt_cero")
        self.assertEqual(rev_cero["item"]["id"], "cnt_cero")
        self.assertEqual(rev_cero["body"], "Cero launches faster.")
        self.assertEqual(rev_cero["current_version"], 1)
        self.assertEqual(rev_cero["versions"][0]["number"], 1)
        self.assertEqual(rev_cero["product"]["id"], "cero")
        self.assertFalse(rev_cero["confidential"])
        self.assertTrue(rev_cero["platform_check"]["ok"])
        self.assertTrue(any(c["source"] == "auto" for c in rev_cero["checklist"]))
        self.assertTrue(any(c["source"] == "platform" for c in rev_cero["checklist"]))

        rev_lazy = review_studio.get_review("cnt_lazy")
        self.assertEqual(rev_lazy["product"]["id"], "lazylayout")
        self.assertTrue(rev_lazy["confidential"])
        self.assertTrue(any(w["code"] == "product_rule" for w in rev_lazy["warnings"]))

    def test_03_comment_anchor_validation_and_crud(self):
        """Validate text/image/video anchors, threading, edit, resolve/reopen, and own-comment delete."""
        img_rel = "outputs/content/cnt_cmt/card.png"
        img_abs = paths.data(img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        with open(img_abs, "wb") as f:
            f.write(b"PNG")

        _create_sample_item(
            content_id="cnt_cmt",
            body="Alpha beta gamma delta.",
            files=[{"kind": "image", "path": img_rel}],
        )

        # Bad text anchors
        with self.assertRaises(review_studio.ValidationError):
            review_studio.create_comment(
                "cnt_cmt",
                kind="text",
                version=1,
                anchor={"start": 0, "end": 5, "quote": "WRONG"},
                body="Fix quote",
            )
        with self.assertRaises(review_studio.ValidationError):
            review_studio.create_comment(
                "cnt_cmt",
                kind="text",
                version=1,
                anchor={"start": 0, "end": 999, "quote": "Alpha"},
                body="Out of bounds",
            )

        # Bad image anchors
        with self.assertRaises(review_studio.ValidationError):
            review_studio.create_comment(
                "cnt_cmt",
                kind="image",
                version=1,
                anchor={"file": img_rel, "x": 1.5, "y": 0.5},
                body="Out of range",
            )
        with self.assertRaises(review_studio.ValidationError):
            review_studio.create_comment(
                "cnt_cmt",
                kind="image",
                version=1,
                anchor={"file": "outputs/other.png", "x": 0.2, "y": 0.3},
                body="Foreign file",
            )

        # Bad video anchor
        with self.assertRaises(review_studio.ValidationError):
            review_studio.create_comment(
                "cnt_cmt",
                kind="video",
                version=1,
                anchor={"file": "clip.mp4", "t": -1},
                body="Negative time",
            )

        # Valid anchors
        c_txt = review_studio.create_comment(
            "cnt_cmt",
            kind="text",
            version=1,
            anchor={"start": 6, "end": 10, "quote": "beta"},
            body="Tighten this word",
        )
        c_img = review_studio.create_comment(
            "cnt_cmt",
            kind="image",
            version=1,
            anchor={"file": img_rel, "x": 0.25, "y": 0.75},
            body="Crop tighter",
        )
        c_vid = review_studio.create_comment(
            "cnt_cmt",
            kind="video",
            version=1,
            anchor={"file": "clip.mp4", "t": 4.5},
            body="Trim intro",
        )
        reply = review_studio.create_comment(
            "cnt_cmt",
            kind="text",
            body="Agreed, updating",
            parent_id=c_txt["id"],
        )

        # Edit, resolve, reopen
        edited = review_studio.update_comment("cnt_cmt", c_txt["id"], body="Tighten this word now")
        self.assertEqual(edited["body"], "Tighten this word now")
        resolved = review_studio.update_comment("cnt_cmt", c_txt["id"], status="resolved")
        self.assertEqual(resolved["status"], "resolved")
        reopened = review_studio.update_comment("cnt_cmt", c_txt["id"], status="open")
        self.assertEqual(reopened["status"], "open")

        # Threaded tree
        rev = review_studio.get_review("cnt_cmt")
        root_ids = [c["id"] for c in rev["comments"]]
        self.assertEqual(root_ids, [c_txt["id"], c_img["id"], c_vid["id"]])
        self.assertEqual([r["id"] for r in rev["comments"][0]["replies"]], [reply["id"]])

        # Agent comment cannot be deleted by user
        c_agent = db.create_review_comment(
            content_id="cnt_cmt",
            version=1,
            kind="general",
            anchor={},
            body="Agent note",
            author="reviewer",
        )
        with self.assertRaises(review_studio.ValidationError):
            review_studio.delete_comment("cnt_cmt", c_agent["id"])

        # Delete own comment removes comment and its replies
        review_studio.delete_comment("cnt_cmt", c_txt["id"])
        self.assertIsNone(db.get_review_comment(c_txt["id"]))
        self.assertIsNone(db.get_review_comment(reply["id"]))

    def test_04_reanchoring_found_moved_and_orphaned(self):
        """Creating a new version re-anchors text comments (nearest match) or marks orphaned."""
        _create_sample_item(
            content_id="cnt_reanchor",
            body="Alpha beta gamma. Beta stays near the end.",
        )
        c_found = review_studio.create_comment(
            "cnt_reanchor",
            kind="text",
            version=1,
            anchor={"start": 0, "end": 5, "quote": "Alpha"},
            body="Keep Alpha",
        )
        c_moved = review_studio.create_comment(
            "cnt_reanchor",
            kind="text",
            version=1,
            anchor={"start": 18, "end": 22, "quote": "Beta"},
            body="Nearest Beta",
        )
        c_orphan = review_studio.create_comment(
            "cnt_reanchor",
            kind="text",
            version=1,
            anchor={"start": 11, "end": 16, "quote": "gamma"},
            body="Check gamma",
        )

        # Create v2 where Alpha moved right, Beta appears twice (at 0 and 30 -> nearest to 18 is 30), gamma removed
        v2 = db.add_content_version(
            "cnt_reanchor",
            body="Beta intro. Prefix Alpha here Beta end.",
            notes="Manual v2",
            created_by="user",
        )
        review_studio._on_new_version("cnt_reanchor", v2["current_version"], v2["body"])

        u_found = db.get_review_comment(c_found["id"])
        self.assertEqual(u_found["version"], 2)
        self.assertEqual(u_found["anchor"]["start"], 19)
        self.assertEqual(u_found["anchor"]["end"], 24)
        self.assertFalse(u_found["anchor"].get("orphaned", False))

        u_moved = db.get_review_comment(c_moved["id"])
        self.assertEqual(u_moved["version"], 2)
        self.assertEqual(u_moved["anchor"]["start"], 30)
        self.assertEqual(u_moved["anchor"]["end"], 34)

        u_orphan = db.get_review_comment(c_orphan["id"])
        self.assertEqual(u_orphan["status"], "open")
        self.assertTrue(u_orphan["anchor"].get("orphaned"))

    def test_05_diff_and_restore_preserves_history(self):
        """Word-level diff returns equal/insert/delete ops; restore appends new version without rewriting old ones."""
        _create_sample_item(content_id="cnt_ver", body="Hello fast world.")
        db.add_content_version("cnt_ver", body="Hello super fast terminal world.", notes="v2", created_by="writer")

        diff = review_studio.diff_versions("cnt_ver", 1, 2)
        ops = diff["ops"]
        self.assertTrue(all(op["op"] in ("equal", "insert", "delete") for op in ops))
        reconstructed_from = "".join(op["text"] for op in ops if op["op"] in ("equal", "delete"))
        reconstructed_to = "".join(op["text"] for op in ops if op["op"] in ("equal", "insert"))
        self.assertEqual(reconstructed_from, "Hello fast world.")
        self.assertEqual(reconstructed_to, "Hello super fast terminal world.")

        restored = review_studio.restore_version("cnt_ver", 1)
        self.assertEqual(restored["current_version"], 3)
        self.assertEqual(restored["body"], "Hello fast world.")
        self.assertEqual(restored["versions"][2]["notes"], "Restored from v1")
        self.assertEqual(len(restored["versions"]), 3)
        self.assertEqual(restored["versions"][1]["body"], "Hello super fast terminal world.")

    def test_06_suggestions_end_to_start_adjacent_overlapping_and_stale(self):
        """Suggestions handle accept one/all, stale original rejection, older version relocation, adjacent & overlapping spans."""
        _create_sample_item(content_id="cnt_sug", body="AAABBBCCC DDD")

        # Adjacent spans [0:3] and [3:6] + overlapping span [4:9] + stale span
        s_adj1 = db.create_review_suggestion("cnt_sug", 1, 0, 3, "AAA", "11111", author="writer")
        s_adj2 = db.create_review_suggestion("cnt_sug", 1, 3, 6, "BBB", "22", author="writer")
        s_overlap = db.create_review_suggestion("cnt_sug", 1, 4, 9, "BBCCC", "XXXX", author="writer")
        s_stale = db.create_review_suggestion("cnt_sug", 1, 10, 13, "ZZZ", "NEW", author="writer")

        res = review_studio.decide_all_suggestions("cnt_sug", "accept")
        self.assertTrue(res["version_created"])
        self.assertEqual(res["version"], 2)
        # "AAA"->"11111", "BBB"->"22", "CCC DDD" untouched -> "1111122CCC DDD"
        self.assertEqual(res["item"]["body"], "1111122CCC DDD")
        self.assertEqual({s["id"] for s in res["accepted"]}, {s_adj1["id"], s_adj2["id"]})
        self.assertEqual({s["id"] for s in res["rejected"]}, {s_overlap["id"], s_stale["id"]})
        self.assertIn("overlap", db.get_review_suggestion(s_overlap["id"])["reason"].lower())
        self.assertIn("no longer matches", db.get_review_suggestion(s_stale["id"])["reason"].lower())

        # Suggestion based on older version (v1) relocated in v2, and missing older suggestion rejected
        s_old_ok = db.create_review_suggestion("cnt_sug", 1, 10, 13, "DDD", "EEE", author="writer")
        s_old_gone = db.create_review_suggestion("cnt_sug", 1, 0, 3, "AAA", "XXX", author="writer")

        r1 = review_studio.decide_suggestion("cnt_sug", s_old_gone["id"], "accept")
        self.assertFalse(r1["version_created"])
        self.assertEqual(db.get_review_suggestion(s_old_gone["id"])["status"], "rejected")

        r2 = review_studio.decide_suggestion("cnt_sug", s_old_ok["id"], "accept")
        self.assertTrue(r2["version_created"])
        self.assertEqual(r2["item"]["body"], "1111122CCC EEE")

        # Reject one / reject all
        s_rej1 = db.create_review_suggestion("cnt_sug", 3, 0, 5, "11111", "000", author="writer")
        review_studio.decide_suggestion("cnt_sug", s_rej1["id"], "reject")
        self.assertEqual(db.get_review_suggestion(s_rej1["id"])["status"], "rejected")

        s_rej2 = db.create_review_suggestion("cnt_sug", 3, 0, 5, "11111", "999", author="writer")
        review_studio.decide_all_suggestions("cnt_sug", "reject")
        self.assertEqual(db.get_review_suggestion(s_rej2["id"])["status"], "rejected")

    def test_07_comment_at_agent_posts_reply_and_suggestion(self):
        """@agent in a comment launches background job, posts reply comment, and creates suggestion for rewrite."""
        _create_sample_item(content_id="cnt_at", body="Cero runs commands quickly.")
        q = events.subscribe()
        try:
            with patch("ai_gateway.chat_with_tools") as mock_chat:
                mock_chat.return_value = {
                    "text": json.dumps({
                        "reply": "Made it more concrete.",
                        "replacement": "executes local workflows in milliseconds",
                    }),
                    "tool_calls": [],
                    "provider": "nvidia_nim",
                    "model_requested": "minimaxai/minimax-m3.1-flash",
                    "model_used": "minimaxai/minimax-m3.1-flash",
                    "fallback_used": False,
                }
                cmt = review_studio.create_comment(
                    "cnt_at",
                    kind="text",
                    version=1,
                    anchor={"start": 5, "end": 26, "quote": "runs commands quickly"},
                    body="@writer make this more specific",
                )
                self.assertIn("job_id", cmt)
                self.assertTrue(_wait_until(lambda: (jobs.get_job(cmt["job_id"]) or {}).get("status") == "done"))

            job = jobs.get_job(cmt["job_id"])
            self.assertEqual(job["result"]["reply_comment"]["author"], "writer")
            self.assertEqual(job["result"]["reply_comment"]["body"], "Made it more concrete.")

            rev = review_studio.get_review("cnt_at")
            self.assertEqual(len(rev["comments"][0]["replies"]), 1)
            self.assertEqual(len(rev["suggestions"]), 1)
            self.assertEqual(rev["suggestions"][0]["replacement"], "executes local workflows in milliseconds")

            seen_events = []
            while not q.empty():
                seen_events.append(q.get_nowait())
            review_whats = [
                e["data"]["what"] for e in seen_events if e.get("type") == "review.updated"
            ]
            self.assertIn("comment", review_whats)
            self.assertIn("suggestion", review_whats)
            self.assertIn("job", review_whats)
        finally:
            events.unsubscribe(q)

    def test_08_apply_all_direct_and_as_suggestions(self):
        """apply_all creates one version and marks comments applied; as_suggestions creates suggestions only."""
        _create_sample_item(
            content_id="cnt_apply",
            body="First sentence. Second sentence.",
        )
        c1 = review_studio.create_comment(
            "cnt_apply",
            kind="text",
            version=1,
            anchor={"start": 0, "end": 15, "quote": "First sentence."},
            body="Sharpen opening",
        )
        c2 = review_studio.create_comment(
            "cnt_apply",
            kind="general",
            version=1,
            anchor={},
            body="Mention Cero",
        )

        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {
                "text": "First sharper sentence. Cero second sentence.",
                "provider": "nvidia_nim",
                "model_used": "minimaxai/minimax-m3.1-flash",
            }
            res_sug = review_studio.apply_all("cnt_apply", as_suggestions=True)
            self.assertTrue(_wait_until(lambda: (jobs.get_job(res_sug["job_id"]) or {}).get("status") == "done"))

        item_after_sug = db.get_content_item("cnt_apply")
        self.assertEqual(item_after_sug["current_version"], 1)
        self.assertEqual(db.get_review_comment(c1["id"])["status"], "open")
        pending = db.list_review_suggestions("cnt_apply", status="pending")
        self.assertGreaterEqual(len(pending), 1)

        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {
                "text": "Cero opens fast. Second sentence updated.",
                "provider": "nvidia_nim",
                "model_used": "minimaxai/minimax-m3.1-flash",
            }
            res_dir = review_studio.apply_all("cnt_apply", as_suggestions=False)
            self.assertTrue(_wait_until(lambda: (jobs.get_job(res_dir["job_id"]) or {}).get("status") == "done"))

        item_after_dir = db.get_content_item("cnt_apply", include_versions=True)
        self.assertEqual(item_after_dir["current_version"], 2)
        self.assertEqual(item_after_dir["body"], "Cero opens fast. Second sentence updated.")
        self.assertEqual(item_after_dir["versions"][1]["notes"], "Applied 2 comments")
        self.assertEqual(db.get_review_comment(c1["id"])["status"], "applied")
        self.assertEqual(db.get_review_comment(c2["id"])["status"], "applied")

    def test_09_ai_actions_text_blog_image_video_and_unknown(self):
        """ai_action supports selection/whole-body suggestions, blog comments, image alt_text, video script, and rejects unknown."""
        img_rel = "outputs/content/cnt_img/shot.png"
        img_abs = paths.data(img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        with open(img_abs, "wb") as f:
            f.write(b"PNG")

        _create_sample_item(
            content_id="cnt_txt",
            platform="x",
            body="This is a very long opening line for Cero.\nSecond line here.",
        )
        _create_sample_item(
            content_id="cnt_blog",
            content_type="blog",
            platform="blog",
            title="Cero architecture",
            body="## Intro\nCero is fast.\n## Design\nLocal-first.\n## Summary\nDone.",
        )
        _create_sample_item(
            content_id="cnt_img",
            content_type="image",
            platform="instagram",
            title="Cero card",
            body="Caption for Cero.",
            files=[{"kind": "image", "path": img_rel}],
        )
        _create_sample_item(
            content_id="cnt_vid",
            content_type="script",
            platform="youtube",
            title="Cero demo script",
            body="0:00 Hook about Cero terminal.\n0:10 Walkthrough.",
        )

        # Unknown action -> ValidationError (400)
        with self.assertRaises(review_studio.ValidationError):
            review_studio.ai_action("cnt_txt", "invent_stuff")

        # Text action WITH selection -> suggestion only
        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {"text": "Cero starts fast."}
            r_sel = review_studio.ai_action(
                "cnt_txt",
                "shorten",
                selection={"start": 0, "end": 42},
            )
            self.assertTrue(_wait_until(lambda: (jobs.get_job(r_sel["job_id"]) or {}).get("status") == "done"))

        self.assertEqual(db.get_content_item("cnt_txt")["body"], "This is a very long opening line for Cero.\nSecond line here.")
        self.assertEqual(len(db.list_review_suggestions("cnt_txt", status="pending")), 1)

        # Text action WITHOUT selection -> suggestion set, body unchanged, auto_review warnings attached
        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {"text": "Cero cuts terminal latency.\nSecond line here. revolutionary"}
            r_all = review_studio.ai_action("cnt_txt", "stronger_hook")
            self.assertTrue(_wait_until(lambda: (jobs.get_job(r_all["job_id"]) or {}).get("status") == "done"))

        job_all = jobs.get_job(r_all["job_id"])
        self.assertEqual(db.get_content_item("cnt_txt")["current_version"], 1)
        self.assertTrue(any(w["code"] == "hype_word" for w in job_all["result"]["warnings"]))

        # Video script text action works on script body
        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {"text": "0:00 Tight Cero terminal hook.\n0:10 Walkthrough."}
            r_vid = review_studio.ai_action("cnt_vid", "rephrase")
            self.assertTrue(_wait_until(lambda: (jobs.get_job(r_vid["job_id"]) or {}).get("status") == "done"))
        self.assertGreaterEqual(len(db.list_review_suggestions("cnt_vid", status="pending")), 1)

        # Blog action -> posted as comment, not body edit
        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {"text": "TL;DR: Cero uses a local-first terminal architecture."}
            r_blog = review_studio.ai_action("cnt_blog", "tldr")
            self.assertTrue(_wait_until(lambda: (jobs.get_job(r_blog["job_id"]) or {}).get("status") == "done"))
        blog_comments = db.list_review_comments("cnt_blog")
        self.assertEqual(len(blog_comments), 1)
        self.assertIn("TL;DR", blog_comments[0]["body"])
        self.assertEqual(db.get_content_item("cnt_blog")["current_version"], 1)

        # Image alt_text -> calls vision model and stores where posting_kit reads alt text
        with patch("ai_gateway.generate_completion_with_meta") as mock_gen:
            mock_gen.return_value = {"text": "Minimalist card showing Cero CLI benchmark.", "model_used": "nvidia/nemotron-3.5-super-vl-120b-a12b"}
            r_img = review_studio.ai_action("cnt_img", "alt_text")
            self.assertTrue(_wait_until(lambda: (jobs.get_job(r_img["job_id"]) or {}).get("status") == "done"))
            _, kwargs = mock_gen.call_args
            self.assertEqual(kwargs.get("tier"), "vision")
            self.assertEqual(kwargs.get("image_paths"), [os.path.realpath(img_abs)])

        updated_img_item = db.get_content_item("cnt_img", include_versions=True)
        self.assertEqual(
            posting_kit._extract_alt_text(updated_img_item, updated_img_item["files"][0]),
            "Minimalist card showing Cero CLI benchmark.",
        )
        kit = review_studio.build_posting_kit_for_item("cnt_img")
        self.assertIn("alt text: Minimalist card showing Cero CLI benchmark.", kit["text"])

    def test_10_confidential_item_routes_only_to_local_provider(self):
        """Confidential (LazyLayout) items must route strictly to local Ollama and never touch cloud providers."""
        _create_sample_item(
            content_id="cnt_secret",
            product_id="lazylayout",
            title="LazyLayout teaser",
            body="Something else is coming soon.",
        )
        with open(paths.data(".env"), "w", encoding="utf-8") as f:
            f.write("NVIDIA_NIM_API_KEY=nvapi-fake-cloud-key\nOPENAI_API_KEY=sk-fake-openai\n")

        seen_urls = []

        def fake_urlopen(req, timeout=0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen_urls.append(url)
            if "11434" in url:
                return _FakeHTTPResponse({"message": {"role": "assistant", "content": "Something new is coming."}})
            raise AssertionError(f"Cloud provider URL called for confidential item: {url}")

        with patch("ai_gateway._is_port_open", return_value=True), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = review_studio.ai_action("cnt_secret", "rephrase")
            self.assertTrue(_wait_until(lambda: (jobs.get_job(res["job_id"]) or {}).get("status") == "done"))

        self.assertTrue(seen_urls)
        self.assertTrue(all("127.0.0.1:11434" in u or "localhost:11434" in u for u in seen_urls))
        self.assertEqual(jobs.get_job(res["job_id"])["status"], "done")

    def test_11_checklist_recompute_keeps_manual_checks(self):
        """Recomputing auto checklist items on a new version preserves manual checks and updates auto checks."""
        _create_sample_item(content_id="cnt_chk", body="Unapproved link https://evil.example.com and revolutionary")
        review_studio.get_review("cnt_chk")

        review_studio.update_checklist(
            "cnt_chk",
            label="Founder signed off on screenshot",
            checked=True,
        )
        review_studio.update_checklist(
            "cnt_chk",
            item_key="platform:see_more",
            checked=True,
        )

        v2 = db.add_content_version("cnt_chk", body="Clean Cero post with zero hype.", notes="Cleaned", created_by="user")
        review_studio._on_new_version("cnt_chk", v2["current_version"], v2["body"])

        checklist = db.list_review_checklist("cnt_chk")
        by_key = {c["item_key"]: c for c in checklist}
        self.assertTrue(by_key["auto:no_hype"]["checked"])
        self.assertTrue(by_key["auto:links_approved"]["checked"])
        self.assertTrue(by_key["platform:see_more"]["checked"])
        manual_items = [c for c in checklist if c["source"] == "manual"]
        self.assertEqual(len(manual_items), 1)
        self.assertEqual(manual_items[0]["label"], "Founder signed off on screenshot")
        self.assertTrue(manual_items[0]["checked"])

    def test_12_ready_posting_kit_and_posted(self):
        """mark_ready warns without blocking; posting_kit returns text+files; mark_posted updates url+platform."""
        _create_sample_item(
            content_id="cnt_pub",
            platform="linkedin",
            body="Cero is revolutionary!",
        )
        ready_res = review_studio.mark_ready("cnt_pub")
        self.assertEqual(ready_res["status"], "ready")
        self.assertTrue(any(w["code"] == "hype_word" for w in ready_res["warnings"]))

        kit_res = review_studio.build_posting_kit_for_item("cnt_pub")
        self.assertIn("Cero is revolutionary!", kit_res["text"])
        self.assertIn(kit_res["path"], kit_res["files"])

        posted_res = review_studio.mark_posted(
            "cnt_pub",
            url="https://linkedin.com/posts/123",
            platform="linkedin",
        )
        self.assertEqual(posted_res["status"], "posted")
        self.assertEqual(posted_res["posted_url"], "https://linkedin.com/posts/123")
        self.assertEqual(posted_res["platform"], "linkedin")

    def test_13_all_http_routes_happy_bad_input_404_and_401(self):
        """Verify all 15 Review Studio HTTP routes for 200, 400, 404, and 401 auth enforcement."""
        _create_sample_item(content_id="cnt_http", body="Alpha beta gamma.")
        db.add_content_version("cnt_http", body="Alpha delta gamma.", notes="v2", created_by="writer")

        # 401 when ENGINE_TOKEN is set and header is missing
        server.ENGINE_TOKEN = "secret-token"
        unauth_routes = [
            ("GET", "/api/review/cnt_http", None),
            ("GET", "/api/review/cnt_http/versions/1", None),
            ("GET", "/api/review/cnt_http/diff?from=1&to=2", None),
            ("POST", "/api/review/cnt_http/versions/1/restore", {}),
            ("POST", "/api/review/cnt_http/comments", {"kind": "general", "body": "hi"}),
            ("POST", "/api/review/cnt_http/comments/c1", {"status": "resolved"}),
            ("POST", "/api/review/cnt_http/comments/c1/delete", {}),
            ("POST", "/api/review/cnt_http/apply_all", {}),
            ("POST", "/api/review/cnt_http/ai_action", {"action": "shorten"}),
            ("POST", "/api/review/cnt_http/suggestions/s1/decide", {"decision": "accept"}),
            ("POST", "/api/review/cnt_http/suggestions/decide_all", {"decision": "reject"}),
            ("POST", "/api/review/cnt_http/checklist", {"label": "x", "checked": True}),
            ("POST", "/api/review/cnt_http/ready", {}),
            ("POST", "/api/review/cnt_http/posting_kit", {}),
            ("POST", "/api/review/cnt_http/posted", {}),
        ]
        for method, path, payload in unauth_routes:
            resp = self._call(method, path, body_dict=payload)
            self.assertEqual(resp.status_code, 401, f"{method} {path} should require auth")

        auth_hdr = {"X-Magnm-Token": "secret-token"}

        # 1. GET /api/review/<id>
        r = self._call("GET", "/api/review/cnt_http", headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["current_version"], 2)
        self.assertEqual(self._call("GET", "/api/review/missing_id", headers=auth_hdr).status_code, 404)

        # 2. GET /api/review/<id>/versions/<n>
        r = self._call("GET", "/api/review/cnt_http/versions/1", headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["body"], "Alpha beta gamma.")
        self.assertEqual(self._call("GET", "/api/review/cnt_http/versions/abc", headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("GET", "/api/review/cnt_http/versions/99", headers=auth_hdr).status_code, 404)

        # 3. GET /api/review/<id>/diff?from=<a>&to=<b>
        r = self._call("GET", "/api/review/cnt_http/diff?from=1&to=2", headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(op["op"] == "insert" for op in r.response_json()["ops"]))
        self.assertEqual(self._call("GET", "/api/review/cnt_http/diff?from=1", headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("GET", "/api/review/cnt_http/diff?from=1&to=99", headers=auth_hdr).status_code, 404)

        # 4. POST /api/review/<id>/versions/<n>/restore
        r = self._call("POST", "/api/review/cnt_http/versions/1/restore", body_dict={}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["current_version"], 3)
        self.assertEqual(self._call("POST", "/api/review/cnt_http/versions/99/restore", body_dict={}, headers=auth_hdr).status_code, 404)

        # 5. POST /api/review/<id>/comments
        r = self._call(
            "POST",
            "/api/review/cnt_http/comments",
            body_dict={"kind": "text", "version": 3, "anchor": {"start": 0, "end": 5, "quote": "Alpha"}, "body": "Fix"},
            headers=auth_hdr,
        )
        self.assertEqual(r.status_code, 200)
        cmt_id = r.response_json()["id"]
        self.assertEqual(
            self._call("POST", "/api/review/cnt_http/comments", body_dict={"kind": "text", "body": ""}, headers=auth_hdr).status_code,
            400,
        )
        self.assertEqual(
            self._call("POST", "/api/review/missing/comments", body_dict={"kind": "general", "body": "hi"}, headers=auth_hdr).status_code,
            404,
        )

        # 6. POST /api/review/<id>/comments/<cid>
        r = self._call("POST", f"/api/review/cnt_http/comments/{cmt_id}", body_dict={"body": "Updated", "status": "resolved"}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["status"], "resolved")
        self.assertEqual(self._call("POST", f"/api/review/cnt_http/comments/{cmt_id}", body_dict={"status": "bad"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/cnt_http/comments/missing", body_dict={"status": "open"}, headers=auth_hdr).status_code, 404)

        # Reopen for apply_all
        self._call("POST", f"/api/review/cnt_http/comments/{cmt_id}", body_dict={"status": "open"}, headers=auth_hdr)

        # 8. POST /api/review/<id>/apply_all
        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": "Alpha updated gamma."}):
            r = self._call("POST", "/api/review/cnt_http/apply_all", body_dict={"as_suggestions": True}, headers=auth_hdr)
            self.assertEqual(r.status_code, 200)
            apply_job_id = r.response_json()["job_id"]
            self.assertTrue(_wait_until(lambda: (jobs.get_job(apply_job_id) or {}).get("status") == "done"))
        self.assertEqual(self._call("POST", "/api/review/cnt_http/apply_all", body_dict={"agent": "nobody"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/missing/apply_all", body_dict={}, headers=auth_hdr).status_code, 404)

        # 9. POST /api/review/<id>/ai_action
        with patch("ai_gateway.generate_completion_with_meta", return_value={"text": "Short Alpha."}):
            r = self._call("POST", "/api/review/cnt_http/ai_action", body_dict={"action": "shorten"}, headers=auth_hdr)
            self.assertEqual(r.status_code, 200)
            ai_job_id = r.response_json()["job_id"]
            self.assertTrue(_wait_until(lambda: (jobs.get_job(ai_job_id) or {}).get("status") == "done"))
        self.assertEqual(self._call("POST", "/api/review/cnt_http/ai_action", body_dict={"action": "unknown_act"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/missing/ai_action", body_dict={"action": "shorten"}, headers=auth_hdr).status_code, 404)

        # 10 & 11. suggestions decide and decide_all
        sugs = db.list_review_suggestions("cnt_http", status="pending")
        self.assertGreaterEqual(len(sugs), 1)
        r = self._call("POST", f"/api/review/cnt_http/suggestions/{sugs[0]['id']}/decide", body_dict={"decision": "accept"}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._call("POST", f"/api/review/cnt_http/suggestions/{sugs[0]['id']}/decide", body_dict={"decision": "bad"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/cnt_http/suggestions/missing/decide", body_dict={"decision": "accept"}, headers=auth_hdr).status_code, 404)

        r = self._call("POST", "/api/review/cnt_http/suggestions/decide_all", body_dict={"decision": "reject"}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._call("POST", "/api/review/cnt_http/suggestions/decide_all", body_dict={"decision": "bad"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/missing/suggestions/decide_all", body_dict={"decision": "reject"}, headers=auth_hdr).status_code, 404)

        # 7. POST /api/review/<id>/comments/<cid>/delete
        r = self._call("POST", f"/api/review/cnt_http/comments/{cmt_id}/delete", body_dict={}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._call("POST", f"/api/review/cnt_http/comments/{cmt_id}/delete", body_dict={}, headers=auth_hdr).status_code, 404)

        # 12. POST /api/review/<id>/checklist
        r = self._call("POST", "/api/review/cnt_http/checklist", body_dict={"label": "Manual check", "checked": True}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._call("POST", "/api/review/cnt_http/checklist", body_dict={"label": "No bool"}, headers=auth_hdr).status_code, 400)
        self.assertEqual(self._call("POST", "/api/review/missing/checklist", body_dict={"label": "x", "checked": True}, headers=auth_hdr).status_code, 404)

        # 13. POST /api/review/<id>/ready
        r = self._call("POST", "/api/review/cnt_http/ready", body_dict={}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["status"], "ready")
        self.assertEqual(self._call("POST", "/api/review/missing/ready", body_dict={}, headers=auth_hdr).status_code, 404)

        # 14. POST /api/review/<id>/posting_kit
        r = self._call("POST", "/api/review/cnt_http/posting_kit", body_dict={}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text", r.response_json())
        self.assertEqual(self._call("POST", "/api/review/missing/posting_kit", body_dict={}, headers=auth_hdr).status_code, 404)

        # 15. POST /api/review/<id>/posted
        r = self._call("POST", "/api/review/cnt_http/posted", body_dict={"url": "https://x.com/p/1", "platform": "x"}, headers=auth_hdr)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.response_json()["status"], "posted")
        self.assertEqual(self._call("POST", "/api/review/missing/posted", body_dict={}, headers=auth_hdr).status_code, 404)

        # Body validation & size limit checks
        bad_json = self._call("POST", "/api/review/cnt_http/ready", raw_body=b"{not-json", headers=auth_hdr)
        self.assertEqual(bad_json.status_code, 400)
        too_big = self._call(
            "POST",
            "/api/review/cnt_http/ready",
            raw_body=b"{}",
            headers={**auth_hdr, "Content-Length": str(server.MAX_REVIEW_BODY_BYTES + 1)},
        )
        self.assertEqual(too_big.status_code, 413)

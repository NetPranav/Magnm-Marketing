"""Tests for engine.team_knowledge, db v16 team_knowledge_state, cloud_sync integration, and /api/team/knowledge routes."""

import io
import json
import os
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

import agents
import cloud_sync
import db
import events
import paths
import pitch
import product_rules
import server
import team_auth
import team_knowledge


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
    """In-memory HTTP handler stub for exercising server routes without sockets."""

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


class TeamKnowledgeTests(unittest.TestCase):
    """Verify snapshots, validation, backups, sync, routes, and cache clearing for shared knowledge."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        self.data_root = os.path.join(self.tmpdir, "data")
        self.res_root = os.path.join(self.tmpdir, "res")
        for base in (self.data_root, self.res_root):
            for sub in ("config", "research", "agents", os.path.join("agents", "skills"), os.path.join("agents", "archive"), "playbooks", "state"):
                os.makedirs(os.path.join(base, sub), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_token = server.ENGINE_TOKEN
        self._prev_disable = os.environ.get("MAGNM_DISABLE_CLOUD_SYNC")

        paths.DATA_ROOT = self.data_root
        paths.RESOURCE_ROOT = self.res_root
        product_rules.clear_cache()
        agents.clear_cache()
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        db.set_db_path(os.path.join(self.data_root, "state", "magnm.db"))
        db.init_db()

        os.symlink(os.path.join(self._prev_resource_root, "vendor"), os.path.join(self.res_root, "vendor"))
        with open(os.path.join(self.res_root, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)

        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.res_root, "config", "product_rules.json"),
        )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.data_root, "config", "product_rules.json"),
        )

        with open(os.path.join(self.res_root, "research", "brand-direction.md"), "w", encoding="utf-8") as f:
            f.write("# Brand Direction\nResource copy\n")
        with open(os.path.join(self.res_root, "research", "reference-style.md"), "w", encoding="utf-8") as f:
            f.write("# Reference Style\nResource copy\n")
        with open(os.path.join(self.data_root, "research", "brand-direction.md"), "w", encoding="utf-8") as f:
            f.write("# Brand Direction\nData override\n")

        with open(os.path.join(self.res_root, "agents", "cmo.md"), "w", encoding="utf-8") as f:
            f.write("# CMO\n")
        with open(os.path.join(self.res_root, "agents", "skills", "strategy.md"), "w", encoding="utf-8") as f:
            f.write("# Strategy Skill\n")
        with open(os.path.join(self.data_root, "agents", "archive", "old.md"), "w", encoding="utf-8") as f:
            f.write("# Archived\n")
        with open(os.path.join(self.data_root, "agents", "notes.txt"), "w", encoding="utf-8") as f:
            f.write("ignored non-md\n")

        with open(os.path.join(self.res_root, "playbooks", "crisis.md"), "w", encoding="utf-8") as f:
            f.write("# Crisis Playbook\n")
        with open(os.path.join(self.res_root, "playbooks", "extra.txt"), "w", encoding="utf-8") as f:
            f.write("ignored\n")

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
        product_rules.clear_cache()
        agents.clear_cache()
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

    def _sign_in(self, user_id="user-1", email="user@example.com"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })

    def _dispatch(self, path, method="GET", body_dict=None, headers=None):
        h = _StubApiHandler(path, method=method, body_dict=body_dict, headers=headers)
        h.command = method
        if method == "GET":
            h.do_GET()
        elif method == "POST":
            h.do_POST()
        return h

    def _mock_supabase(self, *, role="member", user_id="user-1", display_name="User One", shared_rows=None, base_prices=None, directory=None, custom_handler=None):
        requests = []
        shared = list(shared_rows or [])
        prices = list(base_prices or [])
        dir_rows = list(directory if directory is not None else [{"id": user_id, "display_name": display_name}])

        def _fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            method = req.get_method()
            raw_body = req.data.decode("utf-8") if getattr(req, "data", None) else None
            parsed_body = json.loads(raw_body) if raw_body else None
            requests.append({"method": method, "url": url, "body": parsed_body})

            if custom_handler is not None:
                res = custom_handler(method, url, parsed_body)
                if res is not None:
                    return res

            parsed = urllib.parse.urlparse(url)
            if "/rest/v1/profiles" in parsed.path:
                return _FakeHttpResponse([{
                    "id": user_id,
                    "email": f"{user_id}@example.com",
                    "display_name": display_name,
                    "avatar_url": None,
                    "role": role,
                    "active": True,
                }])
            if "/rest/v1/team_directory" in parsed.path:
                return _FakeHttpResponse(dir_rows)
            if "/rest/v1/shared_knowledge" in parsed.path:
                if method == "GET":
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "key" in qs and qs["key"][0].startswith("eq."):
                        want = qs["key"][0][3:]
                        return _FakeHttpResponse([r for r in shared if r["key"] == want])
                    return _FakeHttpResponse(shared)
                if method == "POST":
                    row = dict(parsed_body)
                    row.setdefault("updated_at", "2026-11-22T02:00:00Z")
                    row.setdefault("updated_by", user_id)
                    shared[:] = [r for r in shared if r["key"] != row["key"]] + [row]
                    return _FakeHttpResponse([row])
            if "/rest/v1/pitch_base_prices" in parsed.path:
                if method == "GET":
                    return _FakeHttpResponse(prices)
                if method == "POST":
                    items = parsed_body if isinstance(parsed_body, list) else [parsed_body]
                    by_id = {r["item_id"]: r for r in prices}
                    out = []
                    for item in items:
                        rec = {
                            "item_id": item["item_id"],
                            "price": item["price"],
                            "updated_at": "2026-11-22T02:00:00Z",
                            "updated_by": user_id,
                        }
                        by_id[item["item_id"]] = rec
                        out.append(rec)
                    prices[:] = list(by_id.values())
                    return _FakeHttpResponse(out)
                if method == "DELETE":
                    qs = urllib.parse.parse_qs(parsed.query)
                    filt = (qs.get("item_id") or [""])[0]
                    assert filt.startswith("in.(") and filt.endswith(")"), f"Bare delete not allowed: {url}"
                    ids = set(filt[4:-1].split(","))
                    prices[:] = [r for r in prices if r["item_id"] not in ids]
                    return _FakeHttpResponse(None, status=204)
            if "/rest/v1/mirror_records" in parsed.path or "/rest/v1/activity_events" in parsed.path or "/rest/v1/device_status" in parsed.path or "/rest/v1/published_proposals" in parsed.path or "/rest/v1/quote_submissions" in parsed.path:
                return _FakeHttpResponse([] if method == "GET" else None)
            return _FakeHttpResponse([])

        return requests, _fake_urlopen, shared, prices

    def test_01_local_snapshots_exclude_archive_and_non_md_and_fallback_to_resource(self):
        bk = team_knowledge.local_snapshot("brand_kit")
        self.assertEqual(bk["files"]["research/brand-direction.md"], "# Brand Direction\nData override\n")
        self.assertEqual(bk["files"]["research/reference-style.md"], "# Reference Style\nResource copy\n")

        ai = team_knowledge.local_snapshot("agent_instructions")
        self.assertEqual(set(ai["files"].keys()), {"agents/cmo.md", "agents/skills/strategy.md"})
        self.assertNotIn("agents/archive/old.md", ai["files"])
        self.assertNotIn("agents/notes.txt", ai["files"])

        tpl = team_knowledge.local_snapshot("templates")
        self.assertEqual(set(tpl["files"].keys()), {"playbooks/crisis.md"})

        pr = team_knowledge.local_snapshot("product_rules")
        self.assertIn("products", pr["rules"])

        pitch.set_base_price("w_front", 7700, actor="admin")
        bp = team_knowledge.local_snapshot("pitch_base_prices")
        self.assertEqual(bp, {"prices": {"w_front": 7700}})

        h1 = team_knowledge.local_hash("brand_kit")
        self.assertEqual(len(h1), 64)

    def test_02_limits_unsafe_paths_and_invalid_rules_refused_on_publish_and_apply(self):
        self._sign_in("admin-1", "admin@example.com")
        bad_paths = [
            ("brand_kit", "../x.md"),
            ("brand_kit", "/etc/passwd"),
            ("brand_kit", "research/product-facts.md"),
            ("agent_instructions", "agents/../../x.md"),
            ("agent_instructions", "agents/archive/old.md"),
            ("agent_instructions", "agents/evil.py"),
            ("templates", "playbooks/sub/x.md"),
            ("templates", "/etc/x.md"),
        ]
        for key, bad_path in bad_paths:
            with self.subTest(key=key, bad_path=bad_path):
                with self.assertRaises(team_knowledge.TeamKnowledgeError):
                    team_knowledge.validate_snapshot(key, {"files": {bad_path: "hi"}, "version": 1})

        # Oversize single file (> 200 KB) and oversize total (> 1 MB)
        big_text = "a" * (team_knowledge.MAX_FILE_BYTES + 10)
        with self.assertRaises(team_knowledge.TeamKnowledgeError):
            team_knowledge.validate_snapshot("templates", {"files": {"playbooks/big.md": big_text}, "version": 1})

        # Total > 1 MB across multiple valid files
        chunk = "b" * (190 * 1024)
        many_files = {f"playbooks/p{i}.md": chunk for i in range(6)}
        with self.assertRaises(team_knowledge.TeamKnowledgeError):
            team_knowledge.validate_snapshot("templates", {"files": many_files, "version": 1})

        # Invalid product_rules refused on publish and on apply
        with open(os.path.join(self.data_root, "config", "product_rules.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "show_levels": {}, "products": []}, f)
        _, opener, _, _ = self._mock_supabase(role="admin", user_id="admin-1")
        with patch("urllib.request.urlopen", side_effect=opener):
            with self.assertRaises(team_knowledge.TeamKnowledgeError):
                team_knowledge.publish("product_rules")

        # Invalid remote data on sync is skipped with last_error and never written
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.data_root, "config", "product_rules.json"),
        )
        bad_remote = [
            {
                "key": "agent_instructions",
                "data": {"files": {"agents/../../escape.md": "pwn"}, "version": 1},
                "updated_at": "2026-11-22T03:00:00Z",
                "updated_by": "admin-1",
            },
            {
                "key": "product_rules",
                "data": {"rules": {"version": 1, "show_levels": {"everything": "ok"}, "products": [{"id": "x", "status": "bad", "show": "everything"}]}, "version": 1},
                "updated_at": "2026-11-22T03:00:00Z",
                "updated_by": "admin-1",
            },
        ]
        self._sign_in("member-1", "m@example.com")
        _, opener_m, _, _ = self._mock_supabase(role="member", user_id="member-1", shared_rows=bad_remote)
        with patch("urllib.request.urlopen", side_effect=opener_m):
            cloud_sync.sync_once()
        st = team_knowledge.status(refresh=False)
        by_key = {item["key"]: item for item in st["items"]}
        self.assertIsNotNone(by_key["agent_instructions"]["last_error"])
        self.assertIsNotNone(by_key["product_rules"]["last_error"])
        self.assertFalse(os.path.exists(os.path.join(self.data_root, "escape.md")))
        # Local product rules stayed valid
        self.assertTrue(any(p["id"] == "cero" for p in product_rules.load_rules()["products"]))

    def test_03_member_applies_newer_remote_with_backup_event_and_cache_clear(self):
        self._sign_in("member-1", "m@example.com")
        new_rules = product_rules.load_rules()
        new_rules["products"][0]["confidential"] = True  # Cero becomes confidential!

        shared_rows = [
            {
                "key": "brand_kit",
                "data": {"files": {"research/brand-direction.md": "# Team Brand Direction\n"}, "version": 1},
                "updated_at": "2026-11-22T02:00:00Z",
                "updated_by": "admin-1",
            },
            {
                "key": "product_rules",
                "data": {"rules": new_rules, "version": 1},
                "updated_at": "2026-11-22T02:00:00Z",
                "updated_by": "admin-1",
            },
        ]
        q = events.subscribe()
        self.addCleanup(lambda: events.unsubscribe(q))

        _, opener, _, _ = self._mock_supabase(
            role="member",
            user_id="member-1",
            shared_rows=shared_rows,
            directory=[{"id": "admin-1", "display_name": "Founder"}],
        )
        with patch("urllib.request.urlopen", side_effect=opener):
            ok = cloud_sync.sync_once()
        self.assertTrue(ok)

        # Verify files overwritten atomically
        with open(os.path.join(self.data_root, "research", "brand-direction.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Team Brand Direction\n")

        # Backup created with previous content
        bk_backups = os.listdir(os.path.join(self.data_root, "state", "knowledge_backups", "brand_kit"))
        self.assertEqual(len(bk_backups), 1)
        with open(os.path.join(self.data_root, "state", "knowledge_backups", "brand_kit", bk_backups[0], "research", "brand-direction.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Brand Direction\nData override\n")

        # Cache cleared: cloud_sync.confidential_checker() immediately marks Cero confidential!
        checker = cloud_sync.confidential_checker()
        self.assertTrue(checker("Launch Cero 2.3 today"))

        # Events published
        emitted = []
        while not q.empty():
            emitted.append(q.get_nowait())
        tk_events = [e["data"] for e in emitted if e["type"] == "team_knowledge.updated"]
        self.assertEqual({e["key"] for e in tk_events}, {"brand_kit", "product_rules"})
        self.assertTrue(all(e["updated_by"] == "Founder" for e in tk_events))

        # Second sync with same remote does nothing (no second backup)
        with patch("urllib.request.urlopen", side_effect=opener):
            cloud_sync.sync_once()
        self.assertEqual(len(os.listdir(os.path.join(self.data_root, "state", "knowledge_backups", "brand_kit"))), 1)

        # Older remote timestamp also does nothing
        shared_rows[0]["updated_at"] = "2026-11-22T01:00:00Z"
        shared_rows[0]["data"] = {"files": {"research/brand-direction.md": "# Older\n"}, "version": 1}
        with patch("urllib.request.urlopen", side_effect=opener):
            cloud_sync.sync_once()
        with open(os.path.join(self.data_root, "research", "brand-direction.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Team Brand Direction\n")

    def test_04_admin_not_overwritten_sees_remote_newer_and_can_apply_and_backups_capped_at_10(self):
        self._sign_in("admin-1", "a@example.com")
        shared_rows = [
            {
                "key": "templates",
                "data": {"files": {"playbooks/crisis.md": "# Remote v1\n"}, "version": 1},
                "updated_at": "2026-11-22T02:00:00Z",
                "updated_by": "admin-2",
            }
        ]
        _, opener, _, _ = self._mock_supabase(
            role="admin",
            user_id="admin-1",
            shared_rows=shared_rows,
            directory=[{"id": "admin-2", "display_name": "CoFounder"}],
        )
        with patch("urllib.request.urlopen", side_effect=opener):
            cloud_sync.sync_once()

        # Local resource copy is NOT overwritten automatically for admin
        self.assertFalse(os.path.exists(os.path.join(self.data_root, "playbooks", "crisis.md")))
        st = team_knowledge.status(refresh=False)
        tpl = next(i for i in st["items"] if i["key"] == "templates")
        self.assertTrue(tpl["remote_newer"])
        self.assertEqual(tpl["remote_updated_by"], "CoFounder")

        # Explicit apply backs up and writes team version
        with patch("urllib.request.urlopen", side_effect=opener):
            res = team_knowledge.apply("templates")
        self.assertFalse(res["remote_newer"])
        with open(os.path.join(self.data_root, "playbooks", "crisis.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "# Remote v1\n")

        # Cap backups at 10 per kind without deleting anything else
        extra_keep = os.path.join(self.data_root, "state", "keep_me.txt")
        with open(extra_keep, "w", encoding="utf-8") as f:
            f.write("important")
        for i in range(12):
            shared_rows[0]["updated_at"] = f"2026-11-22T03:{i:02d}:00Z"
            shared_rows[0]["data"] = {"files": {"playbooks/crisis.md": f"# Remote v{i+2}\n"}, "version": 1}
            with patch("urllib.request.urlopen", side_effect=opener):
                team_knowledge.apply("templates")
        bk_dirs = os.listdir(os.path.join(self.data_root, "state", "knowledge_backups", "templates"))
        self.assertEqual(len(bk_dirs), 10)
        self.assertTrue(os.path.exists(extra_keep))

    def test_05_base_prices_publish_filtered_delete_and_member_apply(self):
        # Admin publishes local prices and removes stale remote price using explicit item_id=in.(...)
        self._sign_in("admin-1", "a@example.com")
        pitch.set_base_price("w_front", 7500, actor="admin")
        remote_prices = [
            {"item_id": "w_front", "price": 6200, "updated_at": "2026-11-21T00:00:00Z", "updated_by": "admin-1"},
            {"item_id": "b_db", "price": 2900, "updated_at": "2026-11-21T00:00:00Z", "updated_by": "admin-1"},
        ]
        reqs, opener, _, live_prices = self._mock_supabase(role="admin", user_id="admin-1", base_prices=remote_prices)
        with patch("urllib.request.urlopen", side_effect=opener):
            team_knowledge.publish_base_prices()

        self.assertEqual({r["item_id"]: r["price"] for r in live_prices}, {"w_front": 7500})
        delete_reqs = [r for r in reqs if r["method"] == "DELETE"]
        self.assertEqual(len(delete_reqs), 1)
        self.assertIn("item_id=in.(b_db)", delete_reqs[0]["url"])

        # Member receives remote prices on sync (backup + replace)
        self._sign_in("member-1", "m@example.com")
        pitch.set_base_price("b_db", 3100, actor="local")
        _, opener_m, _, _ = self._mock_supabase(
            role="member",
            user_id="member-1",
            base_prices=[{"item_id": "w_front", "price": 8100, "updated_at": "2026-11-22T05:00:00Z", "updated_by": "admin-1"}],
            directory=[{"id": "admin-1", "display_name": "Founder"}],
        )
        with patch("urllib.request.urlopen", side_effect=opener_m):
            cloud_sync.sync_once()

        self.assertEqual(pitch.get_base_price_overrides(), {"w_front": 8100})
        bp_backups = os.listdir(os.path.join(self.data_root, "state", "knowledge_backups", "pitch_base_prices"))
        self.assertEqual(len(bp_backups), 1)
        with open(os.path.join(self.data_root, "state", "knowledge_backups", "pitch_base_prices", bp_backups[0], "pitch_base_prices.json"), "r", encoding="utf-8") as f:
            backed = json.load(f)
        self.assertIn("b_db", backed["prices"])

    def test_06_routes_auth_member_refusal_and_admin_auto_publish(self):
        # Signed-out users keep today's behavior and make zero HTTP requests
        with patch("urllib.request.urlopen") as mock_open:
            h_get = self._dispatch("/api/team/knowledge", "GET")
            self.assertEqual(h_get.status_code, 200)
            self.assertFalse(h_get.response_json()["signed_in"])

            h_pub = self._dispatch("/api/team/knowledge/brand_kit/publish", "POST", {})
            self.assertEqual(h_pub.status_code, 403)

            h_pr = self._dispatch("/api/product_rules", "POST", {"product_id": "cero", "status": "released", "show": "everything"})
            self.assertEqual(h_pr.status_code, 200)
            h_bp = self._dispatch("/api/pitch/base_prices", "POST", {"item_id": "w_front", "price": 6100})
            self.assertEqual(h_bp.status_code, 200)
            mock_open.assert_not_called()

        # Engine token enforcement on /api/team/knowledge*
        server.ENGINE_TOKEN = "secret-tok"
        h_unauth = self._dispatch("/api/team/knowledge", "GET")
        self.assertEqual(h_unauth.status_code, 401)
        h_auth = self._dispatch("/api/team/knowledge", "GET", headers={"X-Magnm-Token": "secret-tok"})
        self.assertEqual(h_auth.status_code, 200)
        server.ENGINE_TOKEN = ""

        # Signed in as active member: refused on write routes with 403 "Managed by your admin." and publish with 403
        self._sign_in("member-1", "m@example.com")
        _, opener_m, _, _ = self._mock_supabase(role="member", user_id="member-1")
        with patch("urllib.request.urlopen", side_effect=opener_m):
            for path, body in (
                ("/api/product_rules", {"product_id": "cero", "status": "released", "show": "nothing"}),
                ("/api/pitch/base_prices", {"item_id": "w_front", "price": 9999}),
                ("/api/pitch/base_prices/reset", {"item_id": "w_front"}),
            ):
                res = self._dispatch(path, "POST", body)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.response_json()["error"], "Managed by your admin.")

            res_pub = self._dispatch("/api/team/knowledge/brand_kit/publish", "POST", {})
            self.assertEqual(res_pub.status_code, 403)

        # Signed in as admin: save routes succeed and queue auto-publish for background sync
        self._sign_in("admin-1", "a@example.com")
        _, opener_a, shared_a, prices_a = self._mock_supabase(role="admin", user_id="admin-1")
        with patch("urllib.request.urlopen", side_effect=opener_a), patch("cloud_sync.request_sync") as req_sync:
            res_pr = self._dispatch(
                "/api/product_rules",
                "POST",
                {"product_id": "cero", "status": "released", "show": "text_only", "blocked_terms": ["ROS"], "confidential": False},
            )
            self.assertEqual(res_pr.status_code, 200)
            res_bp = self._dispatch("/api/pitch/base_prices", "POST", {"item_id": "w_front", "price": 6800})
            self.assertEqual(res_bp.status_code, 200)
            self.assertGreaterEqual(req_sync.call_count, 2)

        self.assertEqual(team_knowledge.pending_auto_publish_keys(), ["pitch_base_prices", "product_rules"])
        # Not yet published inline
        self.assertEqual(shared_a, [])
        self.assertEqual(prices_a, [])

        # Sync cycle flushes auto-publish
        with patch("urllib.request.urlopen", side_effect=opener_a):
            self.assertTrue(cloud_sync.sync_once())

        self.assertEqual(team_knowledge.pending_auto_publish_keys(), [])
        self.assertEqual({r["key"] for r in shared_a}, {"product_rules"})
        self.assertEqual({r["item_id"]: r["price"] for r in prices_a}, {"w_front": 6800})

        # Admin reset base price also queues auto-publish and deletes remote row on next sync
        with patch("urllib.request.urlopen", side_effect=opener_a), patch("cloud_sync.request_sync"):
            res_rst = self._dispatch("/api/pitch/base_prices/reset", "POST", {"item_id": "w_front"})
            self.assertEqual(res_rst.status_code, 200)
        with patch("urllib.request.urlopen", side_effect=opener_a):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(prices_a, [])


if __name__ == "__main__":
    unittest.main()

"""Tests for Pitch proposals, base prices, rules validation, outbox, agent tools, payload, PDF, and routes."""

import importlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agents
import db
import events
import paths
import pitch
import pitch_host
import server
import tools
from tools import ToolContext


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


def _dispatch(path, method="GET", body_dict=None, raw_body=None, headers=None):
    h = _StubHandler(path, method=method, body_dict=body_dict, raw_body=raw_body, headers=headers)
    if method == "GET":
        h.do_GET()
    elif method == "POST":
        h.do_POST()
    return h


class PitchTests(unittest.TestCase):
    """Comprehensive unit and route tests for Phase 3b.2 Pitch engine."""

    def setUp(self):
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_token = server.ENGINE_TOKEN
        self._tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir_obj.name
        os.environ["MAGNM_HOME"] = self.tmpdir
        server.ENGINE_TOKEN = ""
        importlib.reload(paths)
        paths.ensure_data_root()
        db.set_db_path(None)
        db.init_db()

    def tearDown(self):
        server.ENGINE_TOKEN = self._prev_token
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        importlib.reload(paths)
        self._tmpdir_obj.cleanup()

    def test_01_migration_v9_to_v10_keeps_existing_data(self):
        """Migrating an existing v9 SQLite DB to v10 preserves all existing data and adds Pitch tables."""
        v9_path = os.path.join(self.tmpdir, "v9_upgrade.db")
        conn = sqlite3.connect(v9_path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta (key, value) VALUES ('files_migrated', 'done')")
        for ver, fn in db._MIGRATIONS:
            if ver <= 9:
                fn(conn)
                conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, '2026-11-18T00:00:00Z')", (ver,))
        conn.execute(
            """
            INSERT INTO content_items (
                id, type, platform, title, status, product_id, created_by,
                session_id, current_version, created_at, updated_at, files_json
            ) VALUES ('cnt_keep_1', 'post', 'x', 'Keep Me', 'draft', 'cero', 'writer', NULL, 1, '2026-11-18T00:00:00Z', '2026-11-18T00:00:00Z', '[]')
            """
        )
        conn.commit()
        conn.close()

        db.set_db_path(v9_path)
        db.init_db()

        upgraded = db.connect()
        versions = [r["version"] for r in upgraded.execute("SELECT version FROM schema_version ORDER BY version").fetchall()]
        self.assertEqual(versions, list(range(1, db.SCHEMA_VERSION + 1)))
        kept = upgraded.execute("SELECT title FROM content_items WHERE id = 'cnt_keep_1'").fetchone()
        self.assertIsNotNone(kept)
        self.assertEqual(kept["title"], "Keep Me")
        tables = {
            r["name"]
            for r in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        self.assertTrue({"pitch_proposals", "pitch_base_prices", "sync_outbox"}.issubset(tables))

    def test_02_catalog_merge_base_overrides_and_reset(self):
        """Catalog merges admin base overrides and reset restores Pitch's catalog price."""
        cat0 = pitch.catalog()
        self.assertEqual(cat0["items"]["w_front"]["base_price"], 6000)
        self.assertEqual(cat0["items"]["w_front"]["price"], 6000)
        self.assertFalse(cat0["items"]["w_front"]["overridden"])

        res = pitch.set_base_price("w_front", 7500, actor="admin")
        self.assertTrue(res["admin_only"])
        self.assertEqual(res["old_price"], 6000)
        self.assertEqual(res["new_price"], 7500)

        cat1 = pitch.catalog()
        self.assertEqual(cat1["items"]["w_front"]["base_price"], 6000)
        self.assertEqual(cat1["items"]["w_front"]["price"], 7500)
        self.assertTrue(cat1["items"]["w_front"]["overridden"])

        rst = pitch.reset_base_price("w_front", actor="admin")
        self.assertTrue(rst["admin_only"])
        self.assertTrue(rst["reset"])
        cat2 = pitch.catalog()
        self.assertEqual(cat2["items"]["w_front"]["price"], 6000)
        self.assertFalse(cat2["items"]["w_front"]["overridden"])

    def test_03_rule_violations_individual_and_all_at_once(self):
        """Every Pitch rule violation is rejected and multiple violations are returned together."""
        # 1. Invalid slug format
        for bad_slug in ("A", "Bad-Slug", "has_underscore", "-leading", "a" * 64):
            with self.assertRaises(pitch.ValidationError) as ctx:
                pitch.create_proposal({"slug": bad_slug})
            self.assertTrue(any(e["rule"] == "slug_format" for e in ctx.exception.errors))

        # 2. Reserved slugs (general, admin, clients, built-in presets)
        for reserved in ("general", "admin", "clients", "urban-company"):
            with self.assertRaises(pitch.ValidationError) as ctx:
                pitch.create_proposal({"slug": reserved})
            self.assertTrue(any(e["rule"] == "reserved_slug" for e in ctx.exception.errors))

        # 3. Slug uniqueness
        pitch.create_proposal({"slug": "acme-clinic", "client_name": "Acme"})
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({"slug": "acme-clinic", "client_name": "Acme 2"})
        self.assertTrue(any(e["rule"] == "slug_unique" for e in ctx.exception.errors))

        # 4. Unknown item id in sections
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({"slug": "sec-bad", "preset": {"sections": {"p2": ["no_such_item"]}}})
        self.assertTrue(any(e["rule"] == "unknown_item_id" and "sections" in e["field"] for e in ctx.exception.errors))

        # 5. Unknown item id in initialSelected
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({"slug": "sel-bad", "preset": {"initialSelected": ["no_such_item"]}})
        self.assertTrue(any(e["rule"] == "unknown_item_id" and "initialSelected" in e["field"] for e in ctx.exception.errors))

        # 6. Unknown item id in custom item dependency r
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "dep-bad",
                "custom_items": [{"id": "custom_one", "g": "Backend", "n": "One", "d": "Desc", "p": 500, "r": ["missing_dep"]}],
            })
        self.assertTrue(any(e["rule"] == "unknown_item_id" and "custom_items" in e["field"] for e in ctx.exception.errors))

        # 7. Custom item id missing custom_ prefix
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "cid-bad",
                "custom_items": [{"id": "bad_id", "g": "Backend", "n": "One", "d": "Desc", "p": 500, "r": []}],
            })
        self.assertTrue(any(e["rule"] == "custom_item_id" for e in ctx.exception.errors))

        # 8. Custom item invalid group
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "grp-bad",
                "custom_items": [{"id": "custom_grp", "g": "Marketing", "n": "One", "d": "Desc", "p": 500, "r": []}],
            })
        self.assertTrue(any(e["rule"] == "custom_item_group" for e in ctx.exception.errors))

        # 9. Prices out of range or non-integer
        for bad_price in (-1, 10_000_001, True, 99.5):
            with self.assertRaises(pitch.ValidationError) as ctx:
                pitch.create_proposal({"slug": "price-bad", "price_overrides": {"w_front": bad_price}})
            self.assertTrue(any(e["rule"] == "price_range" for e in ctx.exception.errors))

        # 10. Overlong text field
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({"slug": "len-bad", "client_name": "x" * (pitch.MAX_SHORT_TEXT + 1)})
        self.assertTrue(any(e["rule"] == "text_length" for e in ctx.exception.errors))

        # 11. All-at-once violation collection
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "general",
                "client_name": "y" * (pitch.MAX_SHORT_TEXT + 5),
                "price_overrides": {"w_front": -50},
                "custom_items": [{"id": "not_custom", "g": "InvalidGroup", "n": "N", "d": "D", "p": -1, "r": ["nope"]}],
                "preset": {
                    "initialSelected": ["unknown_sel"],
                    "sections": {"p2": ["unknown_sec"]},
                },
            })
        rules_hit = {e["rule"] for e in ctx.exception.errors}
        self.assertGreaterEqual(len(ctx.exception.errors), 6)
        self.assertTrue({
            "reserved_slug",
            "text_length",
            "custom_item_id",
            "custom_item_group",
            "price_range",
            "unknown_item_id",
        }.issubset(rules_hit))

    def test_04_dependency_auto_add_and_text_trimming(self):
        """Selecting items auto-adds transitive dependencies r and trims text fields."""
        created = pitch.create_proposal({
            "slug": "dep-auto-client",
            "client_name": "  Dr. Rao  ",
            "preset": {
                "title": "  Care Portal  ",
                "initialSelected": ["a_push"],  # requires a_base (-> b_db) and b_notif (-> b_db)
            },
        })
        self.assertEqual(created["client_name"], "Dr. Rao")
        self.assertEqual(created["preset"]["title"], "Care Portal")
        selected = created["preset"]["initialSelected"]
        self.assertEqual(set(selected), {"a_push", "a_base", "b_db", "b_notif"})
        self.assertEqual(set(created["added_dependencies"]), {"a_base", "b_db", "b_notif"})

    def test_05_standalone_fee_rule_matches_pitch(self):
        """Standalone fee (4000 INR) applies iff Mobile app items are selected without Web & SaaS items."""
        # Mobile app only: a_base (6500) + auto-added b_db (2000) + standalone_fee (4000) = 12500
        mobile_only = pitch.create_proposal({
            "slug": "mobile-only-app",
            "preset": {"initialSelected": ["a_base"]},
        })
        ps_mobile = mobile_only["pricing_summary"]
        self.assertEqual(ps_mobile["standalone_fee"], 4000)
        self.assertEqual(ps_mobile["subtotal"], 8500)
        self.assertEqual(ps_mobile["total"], 12500)

        # Mobile + Web: a_base (6500) + b_db (2000) + w_front (6000) + standalone_fee (0) = 14500
        mobile_and_web = pitch.create_proposal({
            "slug": "mobile-and-web",
            "preset": {"initialSelected": ["a_base", "w_front"]},
        })
        ps_both = mobile_and_web["pricing_summary"]
        self.assertEqual(ps_both["standalone_fee"], 0)
        self.assertEqual(ps_both["subtotal"], 14500)
        self.assertEqual(ps_both["total"], 14500)

        # Web only: w_front (6000) + standalone_fee (0) = 6000
        web_only = pitch.create_proposal({
            "slug": "web-only-site",
            "preset": {"initialSelected": ["w_front"]},
        })
        self.assertEqual(web_only["pricing_summary"]["standalone_fee"], 0)
        self.assertEqual(web_only["pricing_summary"]["total"], 6000)

    def test_06_price_precedence_proposal_beats_admin_beats_base(self):
        """Proposal override beats admin override beats Pitch catalog base price."""
        pitch.set_base_price("w_front", 7500, actor="admin")
        pitch.set_base_price("b_db", 2800, actor="admin")

        prop = pitch.create_proposal({
            "slug": "precedence-co",
            "price_overrides": {"w_front": 9000},
            "custom_items": [
                {"id": "custom_sso", "g": "Backend", "n": "Custom SSO", "d": "SAML integration", "p": 1200, "r": []}
            ],
            "preset": {"initialSelected": ["w_front", "b_db", "ops_cloud", "custom_sso"]},
        })

        ps = prop["pricing_summary"]
        by_id = {ln["item_id"]: ln for ln in ps["lines"]}
        self.assertEqual(by_id["w_front"]["base"], 6000)
        self.assertEqual(by_id["w_front"]["effective"], 9000)
        self.assertEqual(by_id["w_front"]["delta"], 3000)
        self.assertEqual(by_id["w_front"]["source"], "proposal")

        self.assertEqual(by_id["b_db"]["base"], 2000)
        self.assertEqual(by_id["b_db"]["effective"], 2800)
        self.assertEqual(by_id["b_db"]["delta"], 800)
        self.assertEqual(by_id["b_db"]["source"], "admin")

        self.assertEqual(by_id["ops_cloud"]["base"], 1500)
        self.assertEqual(by_id["ops_cloud"]["effective"], 1500)
        self.assertEqual(by_id["ops_cloud"]["delta"], 0)
        self.assertEqual(by_id["ops_cloud"]["source"], "base")

        self.assertEqual(len(ps["custom_items"]), 1)
        self.assertEqual(ps["subtotal"], 9000 + 2800 + 1500 + 1200)
        self.assertEqual(ps["total"], ps["subtotal"])

        diff_ids = {d["item_id"]: d for d in ps["differs_from_base"]}
        self.assertEqual(set(diff_ids.keys()), {"w_front", "b_db"})
        self.assertEqual(diff_ids["w_front"]["base_price"], 6000)
        self.assertEqual(diff_ids["w_front"]["effective_price"], 9000)

    def test_07_outbox_records_create_update_delete_and_base_price(self):
        """Outbox captures base_price.updated, proposal.created, proposal.updated, and proposal.deleted."""
        pitch.set_base_price("w_front", 7200, actor="admin")
        prop = pitch.create_proposal({
            "slug": "outbox-client",
            "client_name": "Meera",
            "price_overrides": {"w_front": 8100},
            "preset": {"initialSelected": ["w_front"]},
        }, created_by="user")
        pid = prop["id"]

        pitch.update_proposal(pid, {"client_name": "Meera Shah", "notes": "Updated scope"})
        pitch.delete_proposal(pid, hard=False)
        pitch.delete_proposal(pid, hard=True)

        outbox = pitch.list_outbox(pending_only=True)
        kinds = [ev["kind"] for ev in outbox["events"]]
        self.assertEqual(
            kinds,
            ["proposal.deleted", "proposal.deleted", "proposal.updated", "proposal.created", "base_price.updated"],
        )

        by_kind = {ev["kind"]: ev for ev in reversed(outbox["events"])}
        bp_evt = by_kind["base_price.updated"]["payload"]
        self.assertEqual(bp_evt["old_price"], 6000)
        self.assertEqual(bp_evt["new_price"], 7200)

        created_evt = by_kind["proposal.created"]["payload"]
        self.assertEqual(created_evt["proposal_id"], pid)
        self.assertEqual(created_evt["slug"], "outbox-client")
        self.assertEqual(created_evt["created_by"], "user")
        self.assertIsNone(created_evt["user_label"])
        self.assertEqual(created_evt["pricing_summary"]["total"], 8100)
        self.assertEqual(created_evt["differs_from_base"][0]["item_id"], "w_front")

        # Update event includes changed_fields + new pricing
        upd_evt = next(ev["payload"] for ev in outbox["events"] if ev["kind"] == "proposal.updated")
        self.assertEqual(set(upd_evt["changed_fields"]), {"client_name", "notes"})
        self.assertEqual(upd_evt["pricing_summary"]["total"], 8100)

    def test_08_product_rules_warnings_do_not_block_save(self):
        """Mentioning LazyLayout features emits warnings without blocking proposal creation."""
        prop = pitch.create_proposal({
            "slug": "teaser-warn-test",
            "client_name": "Partner",
            "preset": {
                "lead": "Includes LazyLayout timeline editor and keyframe animations demo.",
                "initialSelected": ["w_front"],
            },
        })
        self.assertTrue(any(w["product"] == "lazylayout" for w in prop["warnings"]))

    def test_09_create_from_preset_duplicate_and_filters(self):
        """Create from built-in preset, duplicate with fresh share_id, and filter proposals."""
        from_uc = pitch.create_proposal({"from_preset": "urban-company", "slug": "home-fix"})
        self.assertEqual(from_uc["preset"]["slug"], "home-fix")
        self.assertIn("a_prov", from_uc["preset"]["initialSelected"])

        qform = pitch.create_proposal({"slug": "open-quote", "kind": "quote_form", "status": "ready"})
        dup = pitch.duplicate_proposal(from_uc["id"], "home-fix-v2")
        self.assertNotEqual(dup["id"], from_uc["id"])
        self.assertNotEqual(dup["share_id"], from_uc["share_id"])
        self.assertEqual(dup["status"], "draft")
        self.assertEqual(dup["preset"]["slug"], "home-fix-v2")

        all_props = pitch.list_proposals()
        self.assertEqual(len(all_props), 4)
        self.assertEqual(len(pitch.list_proposals(kind="proposal")), 2)
        self.assertEqual(len(pitch.list_proposals(kind="quote_form")), 1)
        self.assertEqual(pitch.list_proposals(kind="quote_form")[0]["id"], qform["id"])
        self.assertEqual(len(pitch.list_proposals(kind="default")), 1)
        self.assertEqual(len(pitch.list_proposals(status="ready")), 2)

    def test_10_agent_tools_permissions_prompt_and_price_stripping(self):
        """cmo and writer have catalog/draft tools and prompt rule; prices stripped and unknown ids rejected."""
        all_agents = {a["id"]: a for a in agents.load_agents()}
        for allowed_id in ("cmo", "writer"):
            self.assertIn("draft_proposal", all_agents[allowed_id]["tools"])
            self.assertIn("list_pitch_catalog", all_agents[allowed_id]["tools"])
            sp = agents.build_system_prompt(allowed_id)
            self.assertIn("never set or suggest prices", sp)

        for other_id in ("researcher", "designer", "video-producer", "reviewer", "analyst"):
            self.assertNotIn("draft_proposal", all_agents[other_id]["tools"])
            self.assertNotIn("list_pitch_catalog", all_agents[other_id]["tools"])
            sp = agents.build_system_prompt(other_id)
            self.assertNotIn("never set or suggest prices", sp)

        # Tool schema has no price parameters.
        draft_spec = tools.get_tool("draft_proposal")
        self.assertTrue(draft_spec.side_effects)
        prop_keys = set(draft_spec.parameters_schema["properties"].keys())
        self.assertFalse(any("price" in k for k in prop_keys))

        ctx = ToolContext(session_id="sess_pitch_1", agent_id="writer")
        allowed = all_agents["writer"]["tools"]

        # list_pitch_catalog returns items with no prices.
        cat_res = tools.run_tool("list_pitch_catalog", ctx, {}, allowed)
        self.assertEqual(len(cat_res["groups"]), 5)
        self.assertGreater(len(cat_res["items"]), 0)
        for item in cat_res["items"]:
            self.assertNotIn("p", item)
            self.assertNotIn("price", item)
            self.assertNotIn("base_price", item)

        # Unknown item ids rejected with list of valid ids.
        bad_res = tools.run_tool(
            "draft_proposal",
            ctx,
            {
                "client_name": "Dr. Mehta",
                "business_name": "TeleMed India",
                "headline": "TeleMed Platform",
                "lead": "Telemedicine consultation platform.",
                "this_plan": "Web and mobile telemedicine suite",
                "what_we_do": "Web, Mobile, Cloud",
                "selected_item_ids": ["w_front", "nonexistent_item"],
            },
            allowed,
        )
        self.assertIn("error", bad_res)
        self.assertIn("w_front", bad_res["valid_ids"])
        self.assertIn("nonexistent_item", bad_res["error"])

        # Valid call with injected price fields strips prices and creates draft owned by writer.
        ok_res = tools.run_tool(
            "draft_proposal",
            ctx,
            {
                "client_name": "Dr. Mehta",
                "business_name": "TeleMed India",
                "headline": "TeleMed Platform",
                "lead": "Telemedicine consultation platform.",
                "this_plan": "Web and mobile telemedicine suite",
                "what_we_do": "Web, Mobile, Cloud",
                "selected_item_ids": ["w_book", "a_base"],
                "architecture_roles": [{"label": "Patient", "points": ["Book video consults"]}],
                "notes": "Telemedicine draft",
                "price_overrides": {"w_book": 1},
                "prices": {"a_base": 1},
                "custom_items": [{"id": "custom_x", "g": "Backend", "n": "X", "d": "X", "p": 1, "r": []}],
            },
            allowed,
        )
        self.assertNotIn("error", ok_res)
        self.assertEqual(ok_res["status"], "draft")
        self.assertEqual(ok_res["created_by"], "writer")
        self.assertNotIn("total", ok_res)

        saved = pitch.get_proposal(ok_res["proposal_id"])
        self.assertEqual(saved["price_overrides"], {})
        self.assertEqual(saved["custom_items"], [])
        self.assertIn("w_book", saved["preset"]["sections"]["p2"])
        self.assertIn("w_front", saved["preset"]["initialSelected"])
        self.assertIn("b_db", saved["preset"]["initialSelected"])

    def test_11_pitch_host_payload_for_proposal_and_quote_form(self):
        """GET /pitch/?proposal=<id> injects preset slug, effective prices, custom items, and mode=form for quote_form."""
        pitch.set_base_price("b_db", 2600, actor="admin")
        prop = pitch.create_proposal({
            "slug": "hosted-client",
            "price_overrides": {"w_front": 8800},
            "custom_items": [{"id": "custom_crm", "g": "Backend", "n": "CRM Sync", "d": "HubSpot sync", "p": 1400, "r": []}],
            "preset": {"name": "Hosted Client", "initialSelected": ["w_front", "b_db", "custom_crm"]},
        })

        h = _dispatch(f"/pitch/?proposal={prop['id']}&mode=present")
        self.assertEqual(h.status_code, 200)
        html = h.wfile.getvalue().decode("utf-8")
        m = re.search(r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>', html, re.DOTALL)
        self.assertIsNotNone(m)
        payload = json.loads(m.group(1))
        self.assertEqual(payload["mode"], "present")
        self.assertEqual(payload["proposal_id"], prop["id"])
        self.assertEqual(payload["preset"]["slug"], "hosted-client")
        self.assertEqual(payload["prices"]["w_front"], 8800)
        self.assertEqual(payload["prices"]["b_db"], 2600)
        self.assertEqual(len(payload["custom_items"]), 1)
        self.assertEqual(payload["custom_items"][0]["id"], "custom_crm")

        # quote_form forces mode=form
        qf = pitch.create_proposal({"slug": "client-intake", "kind": "quote_form"})
        h_qf = _dispatch(f"/pitch/?proposal={qf['id']}")
        self.assertEqual(h_qf.status_code, 200)
        m_qf = re.search(
            r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
            h_qf.wfile.getvalue().decode("utf-8"),
            re.DOTALL,
        )
        self.assertEqual(json.loads(m_qf.group(1))["mode"], "form")

        # Unknown proposal returns 404
        h_404 = _dispatch("/pitch/?proposal=prop_missing")
        self.assertEqual(h_404.status_code, 404)

    def test_12_pdf_upload_list_serve_and_validation(self):
        """PDF endpoint validates header/content-type/size, saves under data root, lists, and serves safely."""
        prop = pitch.create_proposal({"slug": "pdf-client"})
        pid = prop["id"]

        # Non-PDF Content-Type
        bad_ct = _dispatch(
            f"/api/pitch/proposals/{pid}/pdf",
            method="POST",
            raw_body=b"%PDF-1.4\nfake",
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(bad_ct.status_code, 400)

        # Missing %PDF- header
        bad_hdr = _dispatch(
            f"/api/pitch/proposals/{pid}/pdf",
            method="POST",
            raw_body=b"<html>not a pdf</html>",
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(bad_hdr.status_code, 400)

        # Oversize (>10 MB)
        oversize = _dispatch(
            f"/api/pitch/proposals/{pid}/pdf",
            method="POST",
            raw_body=b"",
            headers={"Content-Type": "application/pdf", "Content-Length": str(pitch.MAX_PDF_BYTES + 1)},
        )
        self.assertEqual(oversize.status_code, 413)

        # 404 for unknown proposal
        missing = _dispatch(
            "/api/pitch/proposals/prop_nope/pdf",
            method="POST",
            raw_body=b"%PDF-1.4\n%%EOF",
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(missing.status_code, 404)

        # Valid PDF upload
        pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
        ok = _dispatch(
            f"/api/pitch/proposals/{pid}/pdf",
            method="POST",
            raw_body=pdf_bytes,
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(ok.status_code, 200)
        up = ok.response_json()
        self.assertTrue(up["path"].startswith("outputs/pitch/pdf-client/pdf-client-"))
        self.assertTrue(up["path"].endswith(".pdf"))
        self.assertTrue(os.path.isfile(paths.data(up["path"])))

        # List PDFs
        lst = _dispatch(f"/api/pitch/proposals/{pid}/pdfs")
        self.assertEqual(lst.status_code, 200)
        pdfs = lst.response_json()["pdfs"]
        self.assertEqual(len(pdfs), 1)
        self.assertEqual(pdfs[0]["filename"], up["filename"])

        # Serve PDF
        srv = _dispatch(f"/api/pitch/proposals/{pid}/pdf/{up['filename']}")
        self.assertEqual(srv.status_code, 200)
        self.assertEqual(srv.response_headers.get("Content-Type"), "application/pdf")
        self.assertEqual(srv.wfile.getvalue(), pdf_bytes)

        # Reject traversal
        trav = _dispatch(f"/api/pitch/proposals/{pid}/pdf/..%2fsecret.pdf")
        self.assertEqual(trav.status_code, 400)

    def test_13_all_http_routes_happy_bad_404_401_and_sse_events(self):
        """Exercise every /api/pitch/* route for happy path, bad input, 404, 401, and SSE events."""
        q = events.subscribe()
        try:
            # GET /api/pitch/version
            ver = _dispatch("/api/pitch/version")
            self.assertEqual(ver.status_code, 200)
            ver_json = ver.response_json()
            self.assertEqual(ver_json["ref"], "c44b7e2")
            self.assertNotIn("files", ver_json)

            # GET /api/pitch/catalog
            cat = _dispatch("/api/pitch/catalog")
            self.assertEqual(cat.status_code, 200)
            self.assertIn("w_front", cat.response_json()["items"])

            # POST /api/pitch/base_prices (happy, bad, 404)
            bp_ok = _dispatch("/api/pitch/base_prices", method="POST", body_dict={"item_id": "w_front", "price": 6500})
            self.assertEqual(bp_ok.status_code, 200)
            self.assertTrue(bp_ok.response_json()["admin_only"])
            self.assertEqual(_dispatch("/api/pitch/base_prices", method="POST", body_dict={"item_id": "w_front", "price": -1}).status_code, 400)
            self.assertEqual(_dispatch("/api/pitch/base_prices", method="POST", body_dict={"item_id": "missing", "price": 100}).status_code, 404)

            # POST /api/pitch/base_prices/reset (happy, bad, 404)
            self.assertEqual(_dispatch("/api/pitch/base_prices/reset", method="POST", body_dict={"item_id": "w_front"}).status_code, 200)
            self.assertEqual(_dispatch("/api/pitch/base_prices/reset", method="POST", body_dict={}).status_code, 400)
            self.assertEqual(_dispatch("/api/pitch/base_prices/reset", method="POST", body_dict={"item_id": "missing"}).status_code, 404)

            # POST /api/pitch/proposals (happy, bad JSON, rule error)
            cr = _dispatch(
                "/api/pitch/proposals",
                method="POST",
                body_dict={"slug": "alpha-client", "client_name": "Alpha", "preset": {"initialSelected": ["w_front"]}},
            )
            self.assertEqual(cr.status_code, 200)
            pid = cr.response_json()["id"]
            self.assertEqual(_dispatch("/api/pitch/proposals", method="POST", raw_body=b"{bad json").status_code, 400)
            self.assertEqual(_dispatch("/api/pitch/proposals", method="POST", body_dict={"slug": "general"}).status_code, 400)

            # GET /api/pitch/proposals & /api/pitch/proposals/<id>
            lst = _dispatch("/api/pitch/proposals?status=draft&kind=proposal")
            self.assertEqual(lst.status_code, 200)
            self.assertEqual(len(lst.response_json()["proposals"]), 1)
            self.assertEqual(_dispatch("/api/pitch/proposals?status=bogus").status_code, 400)

            got = _dispatch(f"/api/pitch/proposals/{pid}")
            self.assertEqual(got.status_code, 200)
            got_json = got.response_json()
            self.assertIn("pricing_summary", got_json)
            self.assertIn("warnings", got_json)
            self.assertIn("pitch_version", got_json)
            self.assertEqual(_dispatch("/api/pitch/proposals/prop_missing").status_code, 404)

            # POST /api/pitch/proposals/<id> (update)
            upd = _dispatch(f"/api/pitch/proposals/{pid}", method="POST", body_dict={"notes": "Revised"})
            self.assertEqual(upd.status_code, 200)
            self.assertEqual(upd.response_json()["notes"], "Revised")
            self.assertEqual(_dispatch(f"/api/pitch/proposals/{pid}", method="POST", body_dict={"slug": "admin"}).status_code, 400)
            self.assertEqual(_dispatch("/api/pitch/proposals/prop_missing", method="POST", body_dict={"notes": "x"}).status_code, 404)

            # POST /api/pitch/proposals/<id>/duplicate
            dup = _dispatch(f"/api/pitch/proposals/{pid}/duplicate", method="POST", body_dict={"slug": "alpha-copy"})
            self.assertEqual(dup.status_code, 200)
            self.assertEqual(dup.response_json()["slug"], "alpha-copy")
            self.assertEqual(_dispatch(f"/api/pitch/proposals/{pid}/duplicate", method="POST", body_dict={"slug": "admin"}).status_code, 400)
            self.assertEqual(_dispatch("/api/pitch/proposals/prop_missing/duplicate", method="POST", body_dict={"slug": "x-copy"}).status_code, 404)

            # POST /api/pitch/proposals/<id>/delete (soft, hard, 404)
            sdel = _dispatch(f"/api/pitch/proposals/{pid}/delete", method="POST", body_dict={})
            self.assertEqual(sdel.status_code, 200)
            self.assertEqual(sdel.response_json()["status"], "archived")
            hdel = _dispatch(f"/api/pitch/proposals/{pid}/delete", method="POST", body_dict={"hard": True})
            self.assertEqual(hdel.status_code, 200)
            self.assertEqual(hdel.response_json()["status"], "deleted")
            self.assertEqual(_dispatch("/api/pitch/proposals/prop_missing/delete", method="POST", body_dict={}).status_code, 404)

            # GET /api/pitch/outbox?pending=1
            ob = _dispatch("/api/pitch/outbox?pending=1")
            self.assertEqual(ob.status_code, 200)
            self.assertGreater(ob.response_json()["pending_count"], 0)

            # 404 on unknown sub-route
            self.assertEqual(_dispatch("/api/pitch/unknown").status_code, 404)
            self.assertEqual(_dispatch("/api/pitch/unknown", method="POST", body_dict={}).status_code, 404)

            # Verify pitch.updated SSE events emitted
            emitted = []
            while not q.empty():
                emitted.append(q.get_nowait())
            self.assertTrue(any(ev["type"] == "pitch.updated" for ev in emitted))

            # 401 when ENGINE_TOKEN is set
            server.ENGINE_TOKEN = "secret-pitch-token"
            for route, meth in (
                ("/api/pitch/catalog", "GET"),
                ("/api/pitch/version", "GET"),
                ("/api/pitch/outbox?pending=1", "GET"),
                ("/api/pitch/proposals", "GET"),
                (f"/api/pitch/proposals/{dup.response_json()['id']}", "GET"),
                ("/api/pitch/base_prices", "POST"),
                ("/api/pitch/proposals", "POST"),
            ):
                unauth = _dispatch(route, method=meth, body_dict={} if meth == "POST" else None)
                self.assertEqual(unauth.status_code, 401, f"Expected 401 for {meth} {route}")
        finally:
            events.unsubscribe(q)

    def test_14_rejects_angle_brackets_in_pitch_fields_and_agent_tool(self):
        """Reject < and > in all text fields reaching Pitch, including via agent draft_proposal."""
        bad = '<img src=x onerror="alert(1)">'
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "xss-check",
                "client_name": bad,
                "business_name": "Biz > Co",
                "custom_items": [
                    {"id": "custom_xss", "g": "Backend", "n": bad, "d": "Desc <bad>", "p": 500, "r": []},
                ],
                "preset": {
                    "name": bad,
                    "badge": bad,
                    "clientName": bad,
                    "businessName": bad,
                    "title": bad,
                    "lead": bad,
                    "whatWeDo": bad,
                    "portfolio": bad,
                    "thisPlan": bad,
                    "architectureBlock": {
                        "heading": bad,
                        "sub": bad,
                        "roles": [{"label": bad, "points": [bad]}],
                    },
                },
            })

        expected_fields = {
            "client_name",
            "business_name",
            "custom_items[0].n",
            "custom_items[0].d",
            "preset.name",
            "preset.badge",
            "preset.clientName",
            "preset.businessName",
            "preset.title",
            "preset.lead",
            "preset.whatWeDo",
            "preset.portfolio",
            "preset.thisPlan",
            "preset.architectureBlock.heading",
            "preset.architectureBlock.sub",
            "preset.architectureBlock.roles[0].label",
            "preset.architectureBlock.roles[0].points[0]",
        }
        got_by_field = {e["field"]: e["message"] for e in ctx.exception.errors}
        self.assertTrue(expected_fields.issubset(got_by_field.keys()))
        for f in expected_fields:
            self.assertEqual(got_by_field[f], "Text cannot contain < or >")

        # Update path rejects angle brackets too
        clean = pitch.create_proposal({"slug": "clean-prop", "client_name": "Clean"})
        with self.assertRaises(pitch.ValidationError) as upd_ctx:
            pitch.update_proposal(clean["id"], {"preset": {"badge": "Badge <script>"}})
        self.assertEqual(upd_ctx.exception.errors[0]["field"], "preset.badge")
        self.assertEqual(upd_ctx.exception.errors[0]["message"], "Text cannot contain < or >")

        # Agent draft_proposal with <script> in an architecture point is rejected
        all_agents = {a["id"]: a for a in agents.load_agents()}
        tool_ctx = ToolContext(session_id="sess_xss", agent_id="writer")
        before_count = len(pitch.list_proposals())
        res = tools.run_tool(
            "draft_proposal",
            tool_ctx,
            {
                "client_name": "Safe Client",
                "business_name": "Safe Biz",
                "headline": "Safe Headline",
                "lead": "Safe lead",
                "this_plan": "Safe plan",
                "what_we_do": "Web & Mobile",
                "selected_item_ids": ["w_front"],
                "architecture_roles": [
                    {"label": "Customer", "points": ["Safe point", "<script>alert(1)</script>"]},
                ],
            },
            all_agents["writer"]["tools"],
        )
        self.assertIn("error", res)
        self.assertIn("errors", res)
        self.assertTrue(
            any(
                e["field"] == "preset.architectureBlock.roles[0].points[1]"
                and e["message"] == "Text cannot contain < or >"
                for e in res["errors"]
            )
        )
        self.assertEqual(len(pitch.list_proposals()), before_count)

    def test_15_default_quotation_lifecycle_immutability_and_duplication(self):
        """Default quotation auto-creates once, mirrors general preset + admin prices, is immutable, and duplicates."""
        general = pitch.catalog()["presets"]["general"]

        # Auto-created once at migration and idempotent across repeated calls / restarts / re-init.
        d1 = pitch.get_default_proposal()
        d2 = pitch.get_default_proposal()
        db.init_db()
        d3 = pitch.get_default_proposal()
        self.assertEqual(d1["id"], d2["id"])
        self.assertEqual(d1["id"], d3["id"])
        self.assertEqual(d1["share_id"], d3["share_id"])
        self.assertEqual(len(pitch.list_proposals(kind="default")), 1)

        self.assertEqual(d1["kind"], "default")
        self.assertEqual(d1["slug"], "default-quotation")
        self.assertEqual(d1["status"], "ready")
        self.assertEqual(d1["created_by"], "system")
        self.assertTrue(bool(d1["share_id"]))
        self.assertEqual(d1["preset"], general)
        self.assertEqual(d1["price_overrides"], {})
        self.assertEqual(d1["custom_items"], [])

        # Uses admin base price overrides dynamically.
        pitch.set_base_price("w_front", 7700, actor="admin")
        d_updated_price = pitch.get_default_proposal()
        line_map = {ln["item_id"]: ln for ln in d_updated_price["pricing_summary"]["lines"]}
        self.assertEqual(line_map["w_front"]["effective"], 7700)
        self.assertEqual(line_map["w_front"]["source"], "admin")

        # Cannot be created manually, edited, deleted, archived, or have its slug reused.
        with self.assertRaises(pitch.ValidationError):
            pitch.create_proposal({"slug": "other-default", "kind": "default"})
        with self.assertRaises(pitch.ValidationError) as slug_err:
            pitch.create_proposal({"slug": "default-quotation", "kind": "proposal"})
        self.assertTrue(any(e["rule"] == "reserved_slug" for e in slug_err.exception.errors))

        with self.assertRaises(pitch.ValidationError):
            pitch.update_proposal(d1["id"], {"status": "archived"})
        with self.assertRaises(pitch.ValidationError):
            pitch.update_proposal(d1["id"], {"notes": "attempt edit"})
        with self.assertRaises(pitch.ValidationError):
            pitch.delete_proposal(d1["id"], hard=False)
        with self.assertRaises(pitch.ValidationError):
            pitch.delete_proposal(d1["id"], hard=True)

        # Can be duplicated into proposal or quote_form, but not into default.
        dup_prop = pitch.duplicate_proposal(d1["id"], "copy-from-default")
        self.assertEqual(dup_prop["kind"], "proposal")
        self.assertEqual(dup_prop["status"], "draft")
        self.assertNotEqual(dup_prop["share_id"], d1["share_id"])
        self.assertEqual(dup_prop["preset"]["title"], general["title"])
        self.assertEqual(dup_prop["preset"]["slug"], "copy-from-default")

        dup_qf = pitch.duplicate_proposal(d1["id"], "qf-from-default", kind="quote_form")
        self.assertEqual(dup_qf["kind"], "quote_form")
        self.assertEqual(dup_qf["preset"]["initialSelected"], general["initialSelected"])

        with self.assertRaises(pitch.ValidationError):
            pitch.duplicate_proposal(d1["id"], "bad-dup-default", kind="default")

    def test_16_quote_form_blank_template_matches_general_default(self):
        """New blank quote_form equals Pitch's general preset title, thisPlan, and initialSelected."""
        general = pitch.catalog()["presets"]["general"]
        qf = pitch.create_proposal({"slug": "blank-quote-form", "kind": "quote_form"})
        self.assertEqual(qf["preset"]["title"], general["title"])
        self.assertEqual(qf["preset"]["title"], "magnm Software Cost Planner")
        self.assertEqual(qf["preset"]["lead"], general["lead"])
        self.assertEqual(qf["preset"]["whatWeDo"], general["whatWeDo"])
        self.assertEqual(qf["preset"]["thisPlan"], general["thisPlan"])
        self.assertEqual(qf["preset"]["initialSelected"], general["initialSelected"])
        self.assertEqual(qf["preset"]["sections"], general["sections"])
        self.assertEqual(qf["preset"]["architectureBlock"], general["architectureBlock"])

    def test_17_index_html_defaults_parser_and_strict_failure(self):
        """Default plans parse from vendored index.html at runtime and fail loudly if markup changes."""
        defaults = pitch.default_final_plans()
        self.assertEqual(
            defaults["payment_plan"],
            [
                {"percent": 40, "label": "to start"},
                {"percent": 30, "label": "at demo"},
                {"percent": 30, "label": "at launch"},
            ],
        )
        self.assertIn("Third-party infrastructure", defaults["payment_note"])
        self.assertEqual(len(defaults["timeline"]), 4)
        self.assertEqual(defaults["timeline"][0]["title"], "Scope & Cost Estimation")
        self.assertEqual(defaults["timeline"][0]["when"], "Day 1")
        self.assertEqual(defaults["timeline"][3]["when"], "Final Week")
        self.assertEqual(pitch.catalog()["default_plans"], defaults)

        for broken_html in (
            "",
            "<html><body><p>no blocks here</p></body></html>",
            '<div class="block"><h2>How you pay</h2><div class="pay"><div><b>50%</b><span>only</span></div></div></div>',
        ):
            with self.assertRaises(RuntimeError):
                pitch_host.parse_index_defaults(broken_html)

    def test_18_final_quotation_derived_sections_validation_and_angle_brackets(self):
        """Final quotations derive sections, ignore client sections, and validate all plan fields + angle brackets."""
        # Derived sections + ignored client sections + auto dependency expansion.
        final_prop = pitch.create_proposal({
            "slug": "final-derived",
            "kind": "final",
            "preset": {
                "initialSelected": ["a_push"],  # expands to a_push, a_base, b_db, b_notif
                "sections": {"p2": ["w_front", "nonexistent_ignored"], "p3": [], "p4": [], "p5": []},
            },
        })
        self.assertEqual(final_prop["preset"]["sections"]["p2"], [])
        self.assertEqual(set(final_prop["preset"]["sections"]["p3"]), {"a_push", "a_base"})
        self.assertEqual(set(final_prop["preset"]["sections"]["p4"]), {"b_db", "b_notif"})
        self.assertEqual(final_prop["preset"]["sections"]["p5"], [])
        self.assertEqual(set(final_prop["added_dependencies"]), {"a_base", "b_db", "b_notif"})

        # Validation failures for final quotation fields + angle brackets all collected at once.
        bad = "Bad <script> text"
        with self.assertRaises(pitch.ValidationError) as ctx:
            pitch.create_proposal({
                "slug": "final-invalid",
                "kind": "final",
                "contact": {"name": bad, "business": "Biz > Co"},
                "preset": {"initialSelected": []},
                "payment_plan": [
                    {"percent": 0, "label": bad},
                    {"percent": 50, "label": "x" * 41},
                    {"percent": 20, "label": "three"},
                    {"percent": 20, "label": "four"},
                    {"percent": 10, "label": "five"},
                ],
                "payment_note": "<img src=x>" + ("n" * 400),
                "timeline": [
                    {"title": bad, "when": "w" * 31, "detail": "d" * 241},
                ],
                "service_plans": [
                    {"id": "bad_plan", "name": bad, "description": "<bad>", "price": -5, "period": "weekly"},
                    {"id": "plan_ok-one", "name": "Ok", "description": "", "price": 500, "period": "monthly"},
                    {"id": "plan_ok-one", "name": "Dup", "description": "", "price": 500, "period": "monthly"},
                    {"id": "plan_three", "name": "3", "description": "", "price": 100, "period": "yearly"},
                    {"id": "plan_four", "name": "4", "description": "", "price": 100, "period": "quarterly"},
                    {"id": "plan_five", "name": "5", "description": "", "price": 100, "period": "one_time"},
                    {"id": "plan_six", "name": "6", "description": "", "price": 100, "period": "one_time"},
                ],
            })
        err_by_field = {}
        for err in ctx.exception.errors:
            err_by_field.setdefault(err["field"], []).append(err)

        self.assertIn("preset.initialSelected", err_by_field)
        self.assertIn("payment_plan", err_by_field)
        self.assertIn("payment_plan[0].percent", err_by_field)
        self.assertIn("payment_plan[0].label", err_by_field)
        self.assertIn("payment_plan[1].label", err_by_field)
        self.assertIn("payment_note", err_by_field)
        self.assertIn("timeline[0].title", err_by_field)
        self.assertIn("timeline[0].when", err_by_field)
        self.assertIn("timeline[0].detail", err_by_field)
        self.assertIn("service_plans", err_by_field)
        self.assertIn("service_plans[0].id", err_by_field)
        self.assertIn("service_plans[0].name", err_by_field)
        self.assertIn("service_plans[0].description", err_by_field)
        self.assertIn("service_plans[0].price", err_by_field)
        self.assertIn("service_plans[0].period", err_by_field)
        self.assertIn("contact.name", err_by_field)
        self.assertIn("contact.business", err_by_field)

        # Sum != 100, too few payment rows, empty timeline.
        with self.assertRaises(pitch.ValidationError) as ctx2:
            pitch.create_proposal({
                "slug": "final-sum-bad",
                "kind": "final",
                "preset": {"initialSelected": ["w_front"]},
                "payment_plan": [{"percent": 60, "label": "Start"}, {"percent": 30, "label": "End"}],
                "timeline": [],
            })
        rules2 = {e["rule"] for e in ctx2.exception.errors}
        self.assertIn("payment_plan_sum", rules2)
        self.assertIn("timeline_count", rules2)

        with self.assertRaises(pitch.ValidationError) as ctx3:
            pitch.create_proposal({
                "slug": "final-one-row",
                "kind": "final",
                "preset": {"initialSelected": ["w_front"]},
                "payment_plan": [{"percent": 100, "label": "All"}],
            })
        self.assertTrue(any(e["rule"] == "payment_plan_count" for e in ctx3.exception.errors))

    def test_19_final_pricing_summary_recurring_rounding_differs_and_outbox(self):
        """One-time plans add to total, recurring plans stay separate, payment amounts sum exactly, outbox captures plans."""
        final_prop = pitch.create_proposal({
            "slug": "final-pricing",
            "kind": "final",
            "client_name": "Asha Nair",
            "business_name": "Nair Health",
            "price_overrides": {"w_front": 1000},
            "preset": {"initialSelected": ["w_front"]},
            "payment_plan": [
                {"percent": 40, "label": "to start"},
                {"percent": 30, "label": "at demo"},
                {"percent": 30, "label": "at launch"},
            ],
            "service_plans": [
                {"id": "plan_setup", "name": "Onboarding", "description": "Initial setup", "price": 1, "period": "one_time"},
                {"id": "plan_care", "name": "Monthly Care", "description": "Updates", "price": 2500, "period": "monthly"},
                {"id": "plan_audit", "name": "Quarterly Audit", "description": "QA", "price": 4000, "period": "quarterly"},
                {"id": "plan_domain", "name": "Annual Ops", "description": "Renewal", "price": 12000, "period": "yearly"},
            ],
        })

        ps = final_prop["pricing_summary"]
        # One-time total = w_front (1000) + plan_setup (1) = 1001. Recurring excluded from total.
        self.assertEqual(ps["total"], 1001)
        self.assertEqual(len(ps["one_time_plans"]), 1)
        self.assertEqual(len(ps["recurring"]), 3)
        self.assertEqual(ps["recurring_totals"], {"monthly": 2500, "quarterly": 4000, "yearly": 12000})

        # Payment amounts: 400 + 300 + remainder 301 == 1001.
        amounts = [row["amount"] for row in ps["payment_plan"]]
        self.assertEqual(amounts, [400, 300, 301])
        self.assertEqual(sum(amounts), ps["total"])

        diff_ids = {d["item_id"]: d for d in ps["differs_from_base"]}
        self.assertIn("w_front", diff_ids)
        self.assertIn("plan_setup", diff_ids)
        self.assertIn("plan_care", diff_ids)
        self.assertTrue(diff_ids["plan_care"]["recurring"])
        self.assertFalse(diff_ids["plan_setup"]["recurring"])

        # Outbox carries final pricing, plans, and recurring items.
        outbox = pitch.list_outbox(pending_only=True)
        ev = next(e["payload"] for e in outbox["events"] if e["kind"] == "proposal.created")
        self.assertEqual(ev["kind"], "final")
        self.assertEqual(ev["payment_plan"], ps["payment_plan"])
        self.assertEqual(ev["recurring_totals"], ps["recurring_totals"])
        self.assertEqual(len(ev["service_plans"]), 4)
        self.assertEqual({d["item_id"] for d in ev["differs_from_base"]}, set(diff_ids.keys()))

    def test_20_finalize_payload_and_new_http_routes(self):
        """Finalize copies and keeps original, payload includes locked/plans/contact, and new routes pass happy/400/404/401."""
        pitch.set_base_price("b_db", 2500, actor="admin")
        src = pitch.create_proposal({
            "slug": "src-quote",
            "kind": "quote_form",
            "client_name": "Karan",
            "business_name": "Karan Labs",
            "contact": {"phone": "9999999999", "email": "k@example.com"},
            "price_overrides": {"w_front": 7001},
            "custom_items": [{"id": "custom_api", "g": "Backend", "n": "API Bridge", "d": "Sync", "p": 1000, "r": []}],
            "preset": {
                "title": "Karan Custom Planner",
                "lead": "Custom lead",
                "initialSelected": ["w_front", "custom_api"],
                "sections": {"p2": ["w_front", "w_seo"], "p3": [], "p4": ["custom_api"], "p5": []},
            },
            "notes": "Original scope",
        })

        # GET /api/pitch/default
        def_resp = _dispatch("/api/pitch/default")
        self.assertEqual(def_resp.status_code, 200)
        def_json = def_resp.response_json()
        self.assertEqual(def_json["kind"], "default")
        self.assertEqual(def_json["slug"], "default-quotation")

        # Update/delete/archive default via HTTP returns 400
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{def_json['id']}", method="POST", body_dict={"notes": "x"}).status_code, 400)
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{def_json['id']}/delete", method="POST", body_dict={}).status_code, 400)
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{def_json['id']}/delete", method="POST", body_dict={"hard": True}).status_code, 400)

        # POST /api/pitch/proposals/<id>/finalize (happy path)
        fin_resp = _dispatch(f"/api/pitch/proposals/{src['id']}/finalize", method="POST", body_dict={"slug": "karan-final"})
        self.assertEqual(fin_resp.status_code, 200)
        fin = fin_resp.response_json()
        self.assertNotEqual(fin["id"], src["id"])
        self.assertEqual(fin["kind"], "final")
        self.assertEqual(fin["slug"], "karan-final")
        self.assertEqual(fin["client_name"], "Karan")
        self.assertEqual(fin["business_name"], "Karan Labs")
        self.assertEqual(fin["price_overrides"], {"w_front": 7001})
        self.assertEqual(len(fin["custom_items"]), 1)
        self.assertEqual(fin["preset"]["sections"], {"p2": ["w_front"], "p3": [], "p4": ["custom_api"], "p5": []})
        self.assertEqual(len(fin["payment_plan"]), 3)
        self.assertEqual(len(fin["timeline"]), 4)

        # Original kept untouched
        src_after = pitch.get_proposal(src["id"])
        self.assertEqual(src_after["kind"], "quote_form")
        self.assertIn("w_seo", src_after["preset"]["sections"]["p2"])

        # Finalize bad input (400) and missing id (404)
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{src['id']}/finalize", method="POST", body_dict={"slug": "general"}).status_code, 400)
        empty_prop = pitch.create_proposal({"slug": "empty-sel", "preset": {"initialSelected": []}})
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{empty_prop['id']}/finalize", method="POST", body_dict={}).status_code, 400)
        self.assertEqual(_dispatch("/api/pitch/proposals/prop_missing/finalize", method="POST", body_dict={}).status_code, 404)

        # Payload check for final (/pitch/?proposal=<fin_id>)
        h_fin = _dispatch(f"/pitch/?proposal={fin['id']}")
        self.assertEqual(h_fin.status_code, 200)
        m_fin = re.search(
            r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
            h_fin.wfile.getvalue().decode("utf-8"),
            re.DOTALL,
        )
        p_fin = json.loads(m_fin.group(1))
        self.assertEqual(p_fin["kind"], "final")
        self.assertTrue(p_fin["locked"])
        self.assertEqual(p_fin["contact"]["name"], "Karan")
        self.assertEqual(p_fin["contact"]["business"], "Karan Labs")
        self.assertEqual(p_fin["contact"]["phone"], "9999999999")
        self.assertEqual(sum(r["amount"] for r in p_fin["payment_plan"]), 8001)
        self.assertEqual(len(p_fin["timeline"]), 4)
        self.assertEqual(p_fin["service_plans"], [])

        # Payload check for default (/pitch/?proposal=<def_id>)
        h_def = _dispatch(f"/pitch/?proposal={def_json['id']}&mode=present")
        self.assertEqual(h_def.status_code, 200)
        m_def = re.search(
            r'<script type="application/json" id="magnm-pitch-data">(.*?)</script>',
            h_def.wfile.getvalue().decode("utf-8"),
            re.DOTALL,
        )
        p_def = json.loads(m_def.group(1))
        self.assertEqual(p_def["kind"], "default")
        self.assertFalse(p_def["locked"])
        self.assertEqual(p_def["mode"], "form")
        self.assertEqual(p_def["preset"], pitch.catalog()["presets"]["general"])
        self.assertEqual(p_def["prices"]["b_db"], 2500)

        # 401 when ENGINE_TOKEN is set
        server.ENGINE_TOKEN = "secret-token"
        self.assertEqual(_dispatch("/api/pitch/default").status_code, 401)
        self.assertEqual(_dispatch(f"/api/pitch/proposals/{src['id']}/finalize", method="POST", body_dict={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()

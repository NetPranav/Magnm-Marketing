"""Contract tests verifying the real /phone/v1 server responses match apps/mobile/src/fixtures/phone_contract.json."""

import base64
from datetime import datetime, timedelta, timezone
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_chat
import ai_gateway
import db
import notifications
import paths
import phone_pairing
import phone_server
import reminders
import team_auth

_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00\x00\x02"
    b"\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)

_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "apps",
    "mobile",
    "src",
    "fixtures",
    "phone_contract.json",
)


class PhoneContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

        p = patch.object(paths, "DATA_ROOT", self.root)
        p.start()
        self.addCleanup(p.stop)

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
                    "blocked_terms": [],
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
        with open(os.path.join(self.root, "config", "product_rules.json"), "w", encoding="utf-8") as f:
            json.dump(rules, f)

        self.current_user = {
            "user_id": "user-contract",
            "email": "contract@example.com",
            "expires_at": 9_999_999_999,
        }
        sess_patch = patch.object(team_auth, "get_session_info", side_effect=lambda: self.current_user)
        sess_patch.start()
        self.addCleanup(sess_patch.stop)

        self.pairing = phone_pairing.PairingManager()
        self.manager = phone_server.PhoneServerManager(
            port=0,
            address_resolver=lambda: "127.0.0.1",
            allow_loopback=True,
            pairing_manager=self.pairing,
        )
        self.addCleanup(self._cleanup_threads)

        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            self.fixture = json.load(f)

    def _cleanup_threads(self):
        self.manager.stop(timeout=2.0)
        phone_server.stop(timeout=2.0)
        for th in threading.enumerate():
            if th.name == "magnm-phone-server" and th.is_alive():
                self.fail("Phone server thread outlived test")

    def _phone_req(self, method, path, body=None, headers=None):
        host, port = self.manager.bound_address()
        req_headers = {"Host": f"{host}:{port}"}
        if headers:
            req_headers.update(headers)
        raw_body = None
        if body is not None:
            raw_body = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
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
                conn.send(raw_body)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, json.loads(data.decode("utf-8"))
        finally:
            conn.close()

    def _assert_contract_shape(self, actual, expected, path="root"):
        if expected is None:
            self.assertIsNone(actual, f"{path}: expected null, got {actual!r}")
            return
        if isinstance(expected, bool):
            self.assertIs(type(actual), bool, f"{path}: expected bool, got {type(actual).__name__}")
            return
        if isinstance(expected, int):
            self.assertIs(type(actual), int, f"{path}: expected int, got {type(actual).__name__}")
            return
        if isinstance(expected, float):
            self.assertIsInstance(actual, (int, float), f"{path}: expected number, got {type(actual).__name__}")
            self.assertIsNot(type(actual), bool, f"{path}: expected number, got bool")
            return
        if isinstance(expected, str):
            self.assertIs(type(actual), str, f"{path}: expected str, got {type(actual).__name__}")
            return
        if isinstance(expected, list):
            self.assertIsInstance(actual, list, f"{path}: expected list, got {type(actual).__name__}")
            if len(expected) == 0:
                return
            self.assertGreater(len(actual), 0, f"{path}: expected non-empty list")
            for idx, item in enumerate(actual):
                tmpl = expected[idx] if idx < len(expected) else expected[0]
                self._assert_contract_shape(item, tmpl, path=f"{path}[{idx}]")
            return
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, f"{path}: expected dict, got {type(actual).__name__}")
            self.assertEqual(
                set(actual.keys()),
                set(expected.keys()),
                f"{path}: key mismatch actual={sorted(actual.keys())} expected={sorted(expected.keys())}",
            )
            for key in expected:
                self._assert_contract_shape(actual[key], expected[key], path=f"{path}.{key}")
            return
        self.fail(f"{path}: unsupported expected type {type(expected).__name__}")

    def test_all_phone_routes_match_mobile_contract_fixture(self):
        self.manager.enable()

        # 1. Pairing contract
        started = self.manager.start_pairing()
        parsed_qr = phone_pairing.parse_qr_payload(started["qr_payload"])
        st, pair_res = self._phone_req(
            "POST",
            "/phone/v1/pair",
            body={"code": parsed_qr["code"], "device_name": "Pixel 9"},
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(pair_res, self.fixture["pair"], "pair")

        st, poll_pending = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": pair_res["request_id"], "poll_secret": pair_res["poll_secret"]},
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(poll_pending, self.fixture["pair_poll_pending"], "pair_poll_pending")

        self.pairing.approve_request(pair_res["request_id"])
        st, poll_approved = self._phone_req(
            "POST",
            "/phone/v1/pair/poll",
            body={"request_id": pair_res["request_id"], "poll_secret": pair_res["poll_secret"]},
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(poll_approved, self.fixture["pair_poll_approved"], "pair_poll_approved")

        auth = {"Authorization": f"Bearer {poll_approved['device_token']}"}

        # 2. Seed sample records:
        # Running agent session for status
        db.create_agent_session({
            "id": "sess_running",
            "agent_id": "writer",
            "status": "running",
            "messages": [{"role": "user", "content": "Draft Cero post"}],
            "confidential": False,
            "created_at": "2026-11-22T09:00:00+00:00",
            "updated_at": "2026-11-22T09:00:00+00:00",
        })

        # Public draft with two versions (v2 has 6 hashtags to emit a deterministic warning)
        db.create_content_item(
            "cnt_pub",
            "post",
            "linkedin",
            "Public Cero Launch",
            "Cero v1 early draft.",
            notes="Initial draft",
            status="review",
            product_id="cero",
            created_by="writer",
        )
        db.add_content_version(
            "cnt_pub",
            body="Cero v2 release is live for developers today. #cero #ai #devtools #release #shipping #opensource",
            notes="Tightened hook",
            created_by="writer",
        )

        # Comment with nested reply
        root_cmt = db.create_review_comment(
            content_id="cnt_pub",
            version=2,
            kind="general",
            anchor={},
            body="Strong opening line.",
            author="user",
            comment_id="cmt_root",
        )
        db.create_review_comment(
            content_id="cnt_pub",
            version=2,
            kind="general",
            anchor={},
            body="Agreed, trim one hashtag.",
            author="writer",
            parent_id=root_cmt["id"],
            comment_id="cmt_reply",
        )

        # Confidential draft
        db.create_content_item(
            "cnt_conf",
            "post",
            "x",
            "ProjectNightfall Teaser",
            "Secret Nightfall details",
            product_id="secretproj",
        )

        # Open reminder
        future_iso = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        rem = reminders.create_reminder(
            "Add post metrics for Cero",
            future_iso,
            note="Check LinkedIn analytics",
            kind="results_due",
            content_id="cnt_pub",
            created_by="analyst",
        )

        # Unread notification
        notifications.notify(
            "weekly_summary",
            "Weekly summary ready",
            body="2 posts published",
            route="#/today/summary",
        )

        # Weekly summary
        db.upsert_weekly_summary(
            week_start="2026-11-16",
            data={"week_start": "2026-11-16", "posts": []},
            highlights={"counts": {"posts_posted": 2, "posts_with_results": 2, "posts_missing_results": 0}},
            narrative="Published 2 Cero posts this week.",
            status="ready",
        )

        # Pending metric proposal
        prop = db.create_metric_proposal(
            "mprop_contract_1",
            content_id="cnt_pub",
            platform="linkedin",
            metrics={"views": 1200, "likes": 45},
            evidence={"views": "1.2K", "likes": "45"},
            image_path="assets/results/sample.png",
            status="pending",
        )

        # Public and confidential pitch proposals
        db.create_pitch_proposal(
            proposal_id="prop_pub",
            slug="cero-web",
            kind="proposal",
            status="draft",
            client_name="Aria",
            business_name="Cero Labs",
            contact={
                "name": "Aria",
                "business": "Cero Labs",
                "phone": "+91 9800000001",
                "email": "aria@cero.example",
                "notes": "Launch soon",
            },
            preset={
                "slug": "cero-web",
                "name": "Cero Web Launch",
                "badge": "Web",
                "clientName": "Aria",
                "businessName": "Cero Labs",
                "title": "Cero Web Launch",
                "lead": "Public launch proposal",
                "whatWeDo": "Web platform",
                "portfolio": "cero.example",
                "thisPlan": "Cero Web Plan",
                "architectureBlock": {
                    "heading": "Architecture",
                    "sub": "Layered system",
                    "roles": [{"label": "Frontend", "points": ["Marketing site"]}],
                },
                "initialSelected": ["w_front", "b_db"],
                "sections": {
                    "p2": ["w_front"],
                    "p3": ["a_base"],
                    "p4": ["b_db"],
                    "p5": ["ai_bot"],
                },
            },
            price_overrides={},
            custom_items=[],
            notes="",
            created_by="user",
            share_id="share_prop_pub",
            pitch_commit="test-commit",
        )
        db.create_pitch_proposal(
            proposal_id="prop_conf",
            slug="nightfall-secret",
            kind="proposal",
            status="draft",
            client_name="Hidden",
            business_name="ProjectNightfall",
            contact={},
            preset={
                "slug": "nightfall-secret",
                "name": "ProjectNightfall Pitch",
                "badge": "",
                "clientName": "Hidden",
                "businessName": "ProjectNightfall",
                "title": "ProjectNightfall Pitch",
                "lead": "Secret",
                "whatWeDo": "Secret",
                "portfolio": "",
                "thisPlan": "Secret",
                "architectureBlock": {
                    "heading": "Arch",
                    "sub": "Sub",
                    "roles": [{"label": "Web", "points": ["Page"]}],
                },
                "initialSelected": ["w_front"],
                "sections": {"p2": ["w_front"], "p3": [], "p4": [], "p5": []},
            },
            price_overrides={},
            custom_items=[],
            notes="nightfall_internal",
            created_by="user",
            share_id="share_prop_conf",
            pitch_commit="test-commit",
        )

        # 3. Call every route and verify contract match against phone_contract.json
        st, status_res = self._phone_req("GET", "/phone/v1/status", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(status_res, self.fixture["status"], "status")

        st, drafts_res = self._phone_req("GET", "/phone/v1/drafts?limit=30", headers=auth)
        self.assertEqual(st, 200)
        # Order drafts deterministically by id for comparison against fixture templates
        drafts_by_id = {d["id"]: d for d in drafts_res["drafts"]}
        self.assertIn("cnt_pub", drafts_by_id)
        self.assertIn("cnt_conf", drafts_by_id)
        self._assert_contract_shape(drafts_by_id["cnt_pub"], self.fixture["drafts_list"]["drafts"][0], "drafts_list.public")
        self._assert_contract_shape(drafts_by_id["cnt_conf"], self.fixture["drafts_list"]["drafts"][1], "drafts_list.conf")

        st, detail_res = self._phone_req("GET", "/phone/v1/drafts/cnt_pub", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(detail_res, self.fixture["draft_detail"], "draft_detail")

        st, detail_conf_res = self._phone_req("GET", "/phone/v1/drafts/cnt_conf", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(detail_conf_res, self.fixture["draft_detail_confidential"], "draft_detail_confidential")

        st, comment_res = self._phone_req(
            "POST",
            "/phone/v1/drafts/cnt_pub/comments",
            body={"body": "Looks good from phone", "version": 2},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(comment_res, self.fixture["draft_comment"], "draft_comment")

        st, posted_res = self._phone_req(
            "POST",
            "/phone/v1/drafts/cnt_pub/posted",
            body={"url": "https://linkedin.com/posts/123"},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(posted_res, self.fixture["draft_posted"], "draft_posted")

        st, agents_res = self._phone_req("GET", "/phone/v1/agents", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(agents_res, self.fixture["agents"], "agents")

        with patch.object(
            agent_chat,
            "chat_with_agent",
            return_value={
                "success": True,
                "waiting_user": True,
                "session_id": "sess_contract_1",
                "response": "Here is a Cero launch draft.",
                "question": "Which platform should I format first?",
                "options": ["LinkedIn", "X"],
            },
        ):
            st, agent_msg_res = self._phone_req(
                "POST",
                "/phone/v1/agents/writer/message",
                body={"text": "Draft a Cero update"},
                headers=auth,
            )
        self.assertEqual(st, 200)
        self._assert_contract_shape(agent_msg_res, self.fixture["agent_message"], "agent_message")

        st, summary_res = self._phone_req("GET", "/phone/v1/summary", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(summary_res, self.fixture["summary"], "summary")

        st, rems_res = self._phone_req("GET", "/phone/v1/reminders?status=open", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(rems_res, self.fixture["reminders"], "reminders")

        st, rem_done_res = self._phone_req(
            "POST",
            f"/phone/v1/reminders/{rem['id']}/done",
            body={},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(rem_done_res, self.fixture["reminder_done"], "reminder_done")

        st, notifs_res = self._phone_req("GET", "/phone/v1/notifications?limit=50", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(notifs_res, self.fixture["notifications"], "notifications")

        st, notif_read_res = self._phone_req(
            "POST",
            "/phone/v1/notifications/read",
            body={"all": True},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(notif_read_res, self.fixture["notifications_read"], "notifications_read")

        st, pending_res = self._phone_req("GET", "/phone/v1/results/pending", headers=auth)
        self.assertEqual(st, 200)
        self._assert_contract_shape(pending_res, self.fixture["results_pending"], "results_pending")

        b64_img = base64.b64encode(_PNG_BYTES).decode("ascii")
        with patch.object(
            ai_gateway,
            "generate_completion_with_meta",
            return_value={
                "text": json.dumps({
                    "platform": "linkedin",
                    "metrics": {"views": 420, "likes": 15},
                    "visible_text_evidence": {"views": "420", "likes": "15"},
                    "period": "7 days",
                    "notes": "LinkedIn post overview",
                })
            },
        ):
            st, extract_res = self._phone_req(
                "POST",
                "/phone/v1/results/extract",
                body={"image_base64": b64_img, "content_id": "cnt_pub", "platform": "linkedin"},
                headers=auth,
            )
        self.assertEqual(st, 200)
        self._assert_contract_shape(extract_res, self.fixture["results_extract"], "results_extract")

        st, confirm_res = self._phone_req(
            "POST",
            f"/phone/v1/results/{prop['id']}/confirm",
            body={"metrics": {"views": 1250, "likes": 45}, "platform": "linkedin", "content_id": "cnt_pub"},
            headers=auth,
        )
        self.assertEqual(st, 200)
        self._assert_contract_shape(confirm_res, self.fixture["results_confirm"], "results_confirm")

        st = self._phone_req(
            "POST",
            "/phone/v1/inbox",
            body={
                "items": [
                    {
                        "client_id": "cid_draft_1",
                        "kind": "draft",
                        "title": "Cero mobile note",
                        "body": "Cero shipped offline support today.",
                        "platform": "linkedin",
                    },
                    {
                        "client_id": "cid_idea_1",
                        "kind": "idea",
                        "title": "Cero benchmark idea",
                        "body": "Compare cold start latency.",
                    },
                ]
            },
            headers=auth,
        )
        self.assertEqual(st[0], 200)
        self._assert_contract_shape(st[1], self.fixture["inbox"], "inbox")

        st, pitch_res = self._phone_req("GET", "/phone/v1/pitch", headers=auth)
        self.assertEqual(st, 200)
        pitch_by_id = {p["id"]: p for p in pitch_res["proposals"]}
        self.assertIn("prop_default", pitch_by_id)
        self.assertIn("prop_pub", pitch_by_id)
        self.assertIn("prop_conf", pitch_by_id)
        self._assert_contract_shape(pitch_by_id["prop_default"], self.fixture["pitch"]["proposals"][0], "pitch.default")
        self._assert_contract_shape(pitch_by_id["prop_pub"], self.fixture["pitch"]["proposals"][1], "pitch.public")
        self._assert_contract_shape(pitch_by_id["prop_conf"], self.fixture["pitch"]["proposals"][2], "pitch.conf")


if __name__ == "__main__":
    unittest.main()

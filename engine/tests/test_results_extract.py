"""Tests for analytics screenshot extraction, proposal confirmation/discard, and Analyst record_metrics."""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import db
import notifications
import paths
import results_extract
import server
import tools
from tools import ToolContext

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 24


class _StubHandler(server.CommandCenterHandler):
    def __init__(self, path, method="GET", body_dict=None, raw_bytes=None, headers=None):
        self.path = path
        self.command = method
        if raw_bytes is not None:
            raw = raw_bytes
        elif body_dict is not None:
            raw = json.dumps(body_dict).encode("utf-8")
        else:
            raw = b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Length", str(len(raw)))
        if body_dict is not None and raw_bytes is None:
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


def _build_multipart(fields, files):
    boundary = "----MagnmTestBoundary7e12"
    parts = []
    for name, val in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{val}\r\n".encode("utf-8")
        )
    for name, filename, ctype, data in files:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode("utf-8")
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    return body, f"multipart/form-data; boundary={boundary}"


class ResultsExtractTests(unittest.TestCase):
    """Verify screenshot extraction parsing, size/type limits, proposal confirm/discard, and Analyst confirmation."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        for sub in ("config", "state", "outputs", "assets"):
            os.makedirs(os.path.join(self.tmpdir, sub), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_token = server.ENGINE_TOKEN

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        server.ENGINE_TOKEN = ""

        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.tmpdir, "config", "product_rules.json"),
        )
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

    def tearDown(self):
        db.close_connection()
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        db.set_db_path(self._prev_db_path)
        server.ENGINE_TOKEN = self._prev_token
        self._tmp_obj.cleanup()

    def test_01_defensive_parsing_fences_numbers_unknown_keys_and_nulls(self):
        fenced = """```json
        {
          "platform": "LinkedIn",
          "metrics": {
            "views": "1.2K",
            "impressions": "3,400",
            "engagement_rate": "12%",
            "likes": 45,
            "comments": null,
            "shares": -3,
            "invented_metric": 999
          },
          "visible_text_evidence": {
            "views": "1.2K",
            "impressions": "3,400",
            "engagement_rate": "12%",
            "invented_metric": "999"
          },
          "period": "Last 7 days",
          "notes": "Organic reach"
        }
        ```"""
        parsed = results_extract.parse_extraction_response(fenced)
        self.assertEqual(parsed["platform"], "linkedin")
        self.assertEqual(parsed["metrics"]["views"], 1200)
        self.assertEqual(parsed["metrics"]["impressions"], 3400)
        self.assertEqual(parsed["metrics"]["engagement_rate"], 12)
        self.assertEqual(parsed["metrics"]["likes"], 45)
        self.assertNotIn("comments", parsed["metrics"])  # null kept as missing
        self.assertNotIn("shares", parsed["metrics"])  # negative dropped
        self.assertNotIn("invented_metric", parsed["metrics"])  # unknown key dropped
        self.assertNotIn("invented_metric", parsed["evidence"])
        self.assertEqual(parsed["evidence"]["views"], "1.2K")

        for bad in ("not json at all", "[]", "", "```json\n{bad}\n```"):
            with self.assertRaises(results_extract.ResultsExtractError):
                results_extract.parse_extraction_response(bad)

    def test_02_size_and_type_limits(self):
        # Unsupported file type rejected
        h_bad_type = _StubHandler(
            "/api/results/extract",
            method="POST",
            body_dict={"image_base64": base64.b64encode(b"%PDF-1.4 fake").decode("ascii")},
        )
        h_bad_type.do_POST()
        self.assertEqual(h_bad_type.status_code, 400)

        # >10 MB rejected with 413
        too_big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (results_extract.MAX_IMAGE_BYTES + 10)
        body_mp, ctype_mp = _build_multipart({}, [("file", "big.png", "image/png", too_big)])
        h_big = _StubHandler(
            "/api/results/extract",
            method="POST",
            raw_bytes=body_mp,
            headers={"Content-Type": ctype_mp},
        )
        h_big.do_POST()
        self.assertEqual(h_big.status_code, 413)

        # Valid JPEG and WebP accepted (with mocked AI returning JSON)
        fake_ai = {"text": json.dumps({"platform": "x", "metrics": {"views": 500}, "visible_text_evidence": {"views": "500"}})}
        with patch("ai_gateway.generate_completion_with_meta", return_value=fake_ai):
            for img_bytes in (JPEG_BYTES, WEBP_BYTES):
                h_ok = _StubHandler(
                    "/api/results/extract",
                    method="POST",
                    body_dict={"image_base64": base64.b64encode(img_bytes).decode("ascii")},
                )
                h_ok.do_POST()
                self.assertEqual(h_ok.status_code, 200)
                rel = h_ok.response_json()["proposal"]["image_path"]
                self.assertTrue(rel.startswith("assets/results/"))
                self.assertTrue(os.path.isfile(paths.data(rel)))

    def test_03_extract_never_auto_saves_confirm_saves_and_closes_reminders_and_discard(self):
        db.create_content_item(
            content_id="cnt_res_1",
            content_type="post",
            platform="linkedin",
            title="Cero Launch",
            body="Post body",
            status="ready",
        )
        db.mark_posted("cnt_res_1", url="https://linkedin.com/posts/99", platform="linkedin")
        self.assertEqual(len([r for r in db.list_reminders(status="open") if r["content_id"] == "cnt_res_1"]), 2)

        body_mp, ctype_mp = _build_multipart(
            {"content_id": "cnt_res_1", "platform": "linkedin"},
            [("file", "../../evil.png", "image/png", PNG_BYTES)],
        )
        fake_ai = {
            "text": '```json\n{"platform":"linkedin","metrics":{"views":"1.2K","likes":34,"comments":null},"visible_text_evidence":{"views":"1.2K","likes":"34"}}\n```',
            "model_used": "vision-1",
        }
        with patch("ai_gateway.generate_completion_with_meta", return_value=fake_ai) as mock_ai:
            h_ext = _StubHandler(
                "/api/results/extract",
                method="POST",
                raw_bytes=body_mp,
                headers={"Content-Type": ctype_mp},
            )
            h_ext.do_POST()
        self.assertEqual(h_ext.status_code, 200)
        mock_ai.assert_called_once()
        self.assertEqual(mock_ai.call_args.kwargs["model_tier"], "vision")

        prop = h_ext.response_json()["proposal"]
        pid = prop["id"]
        self.assertEqual(prop["status"], "pending")
        self.assertEqual(prop["metrics"], {"views": 1200, "likes": 34})
        self.assertNotIn("evil", prop["image_path"])

        # Proposal is NOT saved to metrics yet
        self.assertEqual(db.list_metrics(content_id="cnt_res_1"), [])

        # One results_pending notification sent
        pending_notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "results_pending"]
        self.assertEqual(len(pending_notifs), 1)
        self.assertEqual(pending_notifs[0]["title"], "Results ready to confirm")

        # Image endpoint serves the screenshot safely
        h_img = _StubHandler(f"/api/results/proposals/{pid}/image", method="GET")
        h_img.do_GET()
        self.assertEqual(h_img.status_code, 200)
        self.assertEqual(h_img.wfile.getvalue(), PNG_BYTES)

        # Confirm with edited metrics
        h_conf = _StubHandler(
            f"/api/results/proposals/{pid}/confirm",
            method="POST",
            body_dict={"content_id": "cnt_res_1", "platform": "linkedin", "metrics": {"views": 1250, "likes": 34, "comments": 5}},
        )
        h_conf.do_POST()
        self.assertEqual(h_conf.status_code, 200)
        conf_data = h_conf.response_json()
        self.assertEqual(conf_data["proposal"]["status"], "saved")
        self.assertEqual(conf_data["metric"]["source"], "screenshot")
        self.assertEqual(conf_data["metric"]["metrics"]["views"], 1250)

        saved_metrics = db.list_metrics(content_id="cnt_res_1")
        self.assertEqual(len(saved_metrics), 1)
        self.assertEqual(saved_metrics[0]["source"], "screenshot")

        # Matching results_due reminders marked done
        open_rems = [r for r in db.list_reminders(status="open") if r["content_id"] == "cnt_res_1"]
        self.assertEqual(len(open_rems), 0)

        # Extract another and discard it
        with patch("ai_gateway.generate_completion_with_meta", return_value=fake_ai):
            h_ext2 = _StubHandler(
                "/api/results/extract",
                method="POST",
                body_dict={"image_base64": base64.b64encode(PNG_BYTES).decode("ascii"), "platform": "x"},
            )
            h_ext2.do_POST()
        pid2 = h_ext2.response_json()["proposal"]["id"]

        h_disc = _StubHandler(f"/api/results/proposals/{pid2}/discard", method="POST", body_dict={})
        h_disc.do_POST()
        self.assertEqual(h_disc.status_code, 200)
        self.assertEqual(h_disc.response_json()["proposal"]["status"], "discarded")
        self.assertEqual(len(db.list_metrics()), 1)

    def test_04_analyst_record_metrics_screenshot_creates_proposal_without_saving(self):
        ctx = ToolContext(session_id="sess_ana", agent_id="analyst")
        allowed = ["record_metrics", "read_metrics"]

        res_ss = tools.run_tool(
            "record_metrics",
            ctx,
            {
                "content_id": "cnt_ana_1",
                "platform": "x",
                "metrics": {"views": "2.4K", "likes": 51},
                "source": "screenshot",
            },
            allowed,
        )
        self.assertEqual(res_ss["status"], "pending")
        self.assertEqual(res_ss["message"], "Waiting for your confirmation in Results")
        self.assertEqual(res_ss["proposal"]["metrics"], {"views": 2400, "likes": 51})

        # Nothing persisted to metrics table yet
        self.assertEqual(db.list_metrics(content_id="cnt_ana_1"), [])
        self.assertEqual(len(db.list_metric_proposals(status="pending")), 1)

        # Manual numbers still save directly
        res_man = tools.run_tool(
            "record_metrics",
            ctx,
            {
                "content_id": "cnt_ana_1",
                "platform": "x",
                "metrics": {"views": 100, "likes": 5},
                "source": "manual",
            },
            allowed,
        )
        self.assertEqual(res_man["source"], "manual")
        self.assertEqual(len(db.list_metrics(content_id="cnt_ana_1")), 1)

    def test_05_manual_label_after_looking_at_an_image_still_needs_confirmation(self):
        """The model picks `source`; if it looked at an image in this chat, 'manual' numbers become a proposal."""
        ctx = ToolContext(session_id="sess_img", agent_id="analyst")
        db.log_tool_call("sess_img", "analyst", "check_image", "{}")
        res = tools.run_tool(
            "record_metrics",
            ctx,
            {"content_id": "cnt_img_1", "platform": "x", "metrics": {"views": 900}, "source": "manual"},
            ["record_metrics"],
        )
        self.assertEqual(res["status"], "pending")
        self.assertEqual(db.list_metrics(content_id="cnt_img_1"), [])

        attached = ToolContext(session_id="sess_att", agent_id="analyst", attachments=["assets/uploads/shot.png"])
        res2 = tools.run_tool(
            "record_metrics",
            attached,
            {"content_id": "cnt_img_2", "platform": "x", "metrics": {"likes": 3}, "source": "manual"},
            ["record_metrics"],
        )
        self.assertEqual(res2["status"], "pending")
        self.assertEqual(db.list_metrics(content_id="cnt_img_2"), [])

if __name__ == "__main__":
    unittest.main()

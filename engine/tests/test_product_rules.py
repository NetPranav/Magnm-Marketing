"""Unit tests for configurable product rules and server endpoints."""

import glob
import io
import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import product_rules
import server


class _StubHandler(server.CommandCenterHandler):
    """Minimal in-memory HTTP handler stub for testing GET/POST routes without sockets."""

    def __init__(self, path, body_bytes=b""):
        self.path = path
        self.rfile = io.BytesIO(body_bytes)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(body_bytes))}
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


class ProductRulesTests(unittest.TestCase):
    """Verify product_rules loading, validation, prompt generation, text checks, and endpoints."""

    def _write_temp_rules(self, payload):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_load_rules_valid_and_get_product(self):
        data = product_rules.load_rules()
        self.assertEqual(data["version"], 1)
        self.assertIn("teasers_only", data["show_levels"])
        cero = product_rules.get_product("cero")
        self.assertIsNotNone(cero)
        self.assertEqual(cero["status"], "released")
        self.assertIsNone(product_rules.get_product("nonexistent"))

    def test_load_rules_rejects_bad_status_or_show(self):
        base = product_rules.load_rules()

        bad_status = json.loads(json.dumps(base))
        bad_status["products"][0]["status"] = "archived"
        p1 = self._write_temp_rules(bad_status)
        with self.assertRaises(ValueError):
            product_rules.load_rules(p1)

        bad_show = json.loads(json.dumps(base))
        bad_show["products"][0]["show"] = "full_public"
        p2 = self._write_temp_rules(bad_show)
        with self.assertRaises(ValueError):
            product_rules.load_rules(p2)

    def test_prompt_block_lists_products_and_show_levels(self):
        block = product_rules.prompt_block()
        rules = product_rules.load_rules()
        for prod in rules["products"]:
            self.assertIn(prod["name"], block)
            self.assertIn(prod["show"], block)
            self.assertIn(prod["status"], block)

    def test_check_text_clean_cero(self):
        self.assertEqual(product_rules.check_text("Try Cero today"), [])

    def test_check_text_blocked_term_ros(self):
        warnings = product_rules.check_text("Cero now supports ROS")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["product"], "cero")
        self.assertEqual(warnings[0]["severity"], "warn")
        self.assertIn("ROS", warnings[0]["message"])
        self.assertIn("Cero", warnings[0]["message"])

    def test_check_text_teaser_normal_vs_feature_warning(self):
        normal = product_rules.check_text("Something else is coming from LazyLayout")
        self.assertEqual(len(normal), 1)
        self.assertEqual(normal[0]["product"], "lazylayout")
        self.assertEqual(normal[0]["severity"], "warn")
        self.assertIn("teaser", normal[0]["message"].lower())
        self.assertNotIn("feature description", normal[0]["message"].lower())

        feature = product_rules.check_text(
            "LazyLayout's new keyframe editor lets you export animations"
        )
        self.assertEqual(len(feature), 1)
        self.assertEqual(feature[0]["product"], "lazylayout")
        self.assertEqual(feature[0]["severity"], "warn")
        self.assertIn("feature description", feature[0]["message"].lower())

    def test_check_text_long_teaser_sentence_warns_stronger(self):
        long_sentence = (
            "We are quietly working behind the scenes on LazyLayout while our small engineering "
            "team builds new internal prototypes every single week this quarter."
        )
        warnings = product_rules.check_text(long_sentence)
        self.assertEqual(len(warnings), 1)
        self.assertIn("feature description", warnings[0]["message"].lower())

    def test_check_text_show_nothing(self):
        cfg = {
            "version": 1,
            "show_levels": product_rules.load_rules()["show_levels"],
            "products": [
                {
                    "id": "stealth",
                    "name": "ProjectX",
                    "status": "secret",
                    "show": "nothing",
                    "facts_file": None,
                    "blocked_terms": [],
                    "confidential": True,
                    "notes": "",
                }
            ],
        }
        path = self._write_temp_rules(cfg)
        warnings = product_rules.check_text("ProjectX is coming soon.", path=path)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["product"], "stealth")
        self.assertIn("must not be mentioned", warnings[0]["message"])
        self.assertEqual(product_rules.check_text("Nothing secret here.", path=path), [])

    def test_confidential_helpers(self):
        self.assertEqual(product_rules.confidential_product_ids(), {"lazylayout"})
        self.assertTrue(product_rules.mentions_confidential("LazyLayout"))
        self.assertFalse(product_rules.mentions_confidential("Cero"))

    def test_no_hardcoded_lazylayout_in_engine_modules(self):
        engine_files = sorted(glob.glob(os.path.join(ENGINE_DIR, "*.py")))
        self.assertGreater(len(engine_files), 0)
        for path in engine_files:
            if os.path.basename(path) == "product_rules.py":
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn(
                "lazylayout",
                content.lower(),
                msg=f"Found hard-coded LazyLayout reference in {path}",
            )

    def test_server_product_rules_and_check_text_endpoints(self):
        get_handler = _StubHandler("/api/product_rules")
        get_handler.do_GET()
        self.assertEqual(get_handler.status_code, 200)
        rules_payload = get_handler.response_json()
        self.assertEqual(rules_payload["version"], 1)
        self.assertTrue(any(p["id"] == "cero" for p in rules_payload["products"]))

        body = json.dumps({"text": "Cero now supports ROS"}).encode("utf-8")
        post_handler = _StubHandler("/api/check_text", body_bytes=body)
        post_handler.do_POST()
        self.assertEqual(post_handler.status_code, 200)
        check_payload = post_handler.response_json()
        self.assertEqual(len(check_payload["warnings"]), 1)
        self.assertEqual(check_payload["warnings"][0]["product"], "cero")

        plat_body = json.dumps({"text": "x" * 290, "platform": "x", "type": "post"}).encode("utf-8")
        plat_handler = _StubHandler("/api/check_text", body_bytes=plat_body)
        plat_handler.do_POST()
        self.assertEqual(plat_handler.status_code, 200)
        plat_payload = plat_handler.response_json()
        self.assertTrue(any("280" in w["message"] for w in plat_payload["warnings"]))

    def test_save_product_rule_and_post_endpoint(self):
        base = product_rules.load_rules()
        temp_path = self._write_temp_rules(base)

        orig_default = product_rules.DEFAULT_RULES_PATH
        product_rules.DEFAULT_RULES_PATH = temp_path
        try:
            updated, full = product_rules.save_product_rule(
                "cero",
                {
                    "status": "released",
                    "show": "text_only",
                    "blocked_terms": ["ROS", "beta"],
                    "confidential": False,
                    "notes": "Updated note",
                },
                path=temp_path,
            )
            self.assertEqual(updated["show"], "text_only")
            self.assertEqual(updated["blocked_terms"], ["ROS", "beta"])
            self.assertEqual(full["products"][0]["notes"], "Updated note")

            post_body = json.dumps({
                "product_id": "cero",
                "status": "released",
                "show": "everything",
                "blocked_terms": ["ROS", "robotics"],
                "confidential": False,
            }).encode("utf-8")
            post_handler = _StubHandler("/api/product_rules", body_bytes=post_body)
            post_handler.do_POST()
            self.assertEqual(post_handler.status_code, 200)
            payload = post_handler.response_json()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["product"]["show"], "everything")

            bad_body = json.dumps({"product_id": "cero", "status": "archived"}).encode("utf-8")
            bad_handler = _StubHandler("/api/product_rules", body_bytes=bad_body)
            bad_handler.do_POST()
            self.assertEqual(bad_handler.status_code, 400)

            missing_body = json.dumps({"product_id": "unknown-prod", "status": "released"}).encode("utf-8")
            missing_handler = _StubHandler("/api/product_rules", body_bytes=missing_body)
            missing_handler.do_POST()
            self.assertEqual(missing_handler.status_code, 404)
        finally:
            product_rules.DEFAULT_RULES_PATH = orig_default


if __name__ == "__main__":
    unittest.main()

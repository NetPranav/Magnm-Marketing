"""Confidential names must be found in plain and serialized text alike."""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud_sync
import product_rules


class ConfidentialMatchingTests(unittest.TestCase):
    def setUp(self):
        rules = {"products": [{"id": "secretproduct", "name": "Secret Product", "confidential": True}]}
        patcher = patch.object(product_rules, "load_rules", return_value=rules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.checker = cloud_sync.confidential_checker()

    def test_names_are_found_after_serialization(self):
        for body in ("Launch:\nSecret Product", "Launch:\r\nSecret Product", "Launch:\tSecret Product", "Secret\nProduct"):
            once = json.dumps({"body": body})
            twice = json.dumps({"tool_result": once})
            for text in (body, once, twice):
                self.assertTrue(self.checker(text), text)

    def test_synced_record_is_stubbed(self):
        rec = {"kind": "content_version", "title": "v1", "data": {"body": "Launch:\nSecret Product"}}
        self.assertEqual(cloud_sync._stub_if_mentions_confidential(rec, self.checker)["data"], {"confidential": True})

    def test_mcp_text_check_finds_confidential_repos(self):
        import mcp_tools

        pattern = product_rules._word_pattern("hidden-repo")
        text = json.dumps({"notes": "Repo:\nhidden-repo"})
        self.assertTrue(mcp_tools._text_is_confidential(text, lambda _text: False, [pattern]))

    def test_ordinary_text_is_not_flagged(self):
        self.assertFalse(self.checker(json.dumps({"body": "Cero\nis released"})))


if __name__ == "__main__":
    unittest.main()

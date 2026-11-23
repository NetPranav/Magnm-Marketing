"""Pitch from the phone without the laptop: proposals are mirrored to the cloud and the phone's edits come back as
phone actions (pitch_create, pitch_update, pitch_finalize, pitch_link) that the laptop applies through the same Pitch
functions and rules as the web app.

The cloud is treated as hostile and the phone's edits as untrusted data. No network: the Supabase endpoint is faked.
"""

import copy
import json
import os
import sys
import unittest
import uuid
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import db
import paths
import phone_pitch
import phone_queue
import pitch
import pitch_links
from engine.tests.test_phone_queue import OTHER, OWNER, _Base as _QueueBase, _http_error, _Resp

PITCH_KINDS = ("pitch_create", "pitch_update", "pitch_finalize", "pitch_link")
SELECTED = ["w_front"]
SECTIONS = {"p2": ["w_front"], "p3": [], "p4": [], "p5": []}


class _PitchBase(_QueueBase):
    """The phone queue fixtures plus the vendored Pitch files and one proposal on the laptop."""

    def setUp(self):
        super().setUp()
        os.symlink(os.path.join(REPO_ROOT, "vendor"), os.path.join(self.tmpdir, "vendor"))
        self.proposal = pitch.create_proposal({
            "slug": "acme-clinic", "client_name": "Acme", "business_name": "Acme Co",
            "contact": {"name": "Acme Owner", "phone": "+91 1"}, "notes": "Internal note",
            "preset": {"initialSelected": list(SELECTED), "sections": copy.deepcopy(SECTIONS)},
        }, created_by="user")
        self.pid = self.proposal["id"]
        self.cloud_posts = []

    def proposal_row(self, pid=None):
        return db.get_pitch_proposal(pid or self.pid)

    def queue_pitch(self, kind, payload, target=None, **kw):
        if target is None:
            target = "new" if kind == "pitch_create" else self.pid
        return self.queue(kind=kind, target=target, payload=payload, **kw)

    def update(self, fields, version=None, **kw):
        base = version if version is not None else self.proposal_row()["updated_at"]
        return self.queue_pitch("pitch_update", {"base_version": base, "fields": fields}, **kw)

    def outcome(self, row):
        return self.result_of(row)["status"], self.result_of(row)["result"]

    def proposals(self):
        return db.list_pitch_proposals(limit=500)

    def names(self):
        return sorted(p["id"] for p in self.proposals())

    def published_calls(self):
        return [r for r in self.cloud.requests if "published_proposals" in r["url"]]


class CreateTests(_PitchBase):
    def test_a_new_proposal_is_made_with_the_clients_details(self):
        row = self.queue_pitch("pitch_create", {
            "kind": "proposal", "client_name": "Meera Shah", "business_name": "Shah Clinics",
            "contact": {"name": "Meera", "phone": "+91 90000 00000"}, "notes": "Warm lead",
        })
        before = self.names()
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        new = [p for p in self.proposals() if p["id"] not in before]
        self.assertEqual(len(new), 1)
        made = new[0]
        self.assertEqual((made["client_name"], made["business_name"], made["notes"]), ("Meera Shah", "Shah Clinics", "Warm lead"))
        self.assertEqual(made["contact"]["phone"], "+91 90000 00000")
        self.assertEqual((made["kind"], made["status"], made["created_by"]), ("proposal", "draft", "phone"))
        self.assertLessEqual(len(self.outcome(row)[1] or ""), 300)

    def test_a_new_proposal_from_a_built_in_preset(self):
        row = self.queue_pitch("pitch_create", {"kind": "quote_form", "from_preset": "urban-company", "client_name": "Urban demo"})
        before = self.names()
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        made = [p for p in self.proposals() if p["id"] not in before][0]
        self.assertEqual(made["kind"], "quote_form")
        self.assertEqual(made["preset"]["slug"], made["slug"])

    def test_an_unknown_preset_is_refused(self):
        row = self.queue_pitch("pitch_create", {"from_preset": "no-such-preset"})
        before = self.names()
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "rejected")
        self.assertEqual(self.names(), before)

    def test_the_price_a_person_sets_on_the_phone_is_kept(self):
        row = self.queue_pitch("pitch_create", {"client_name": "Priced", "price_overrides": {"w_front": 7000}})
        before = self.names()
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        made = [p for p in self.proposals() if p["id"] not in before][0]
        self.assertEqual(made["price_overrides"], {"w_front": 7000})

    def test_creating_twice_makes_one_proposal(self):
        row = self.queue_pitch("pitch_create", {"client_name": "Once only"})
        self.run_sync()
        after_first = self.names()
        # The cloud hands the same action back (a lost finish, a replay): still one proposal.
        cleaned = phone_queue.clean_row(dict(row, status="applying", applied_by_device=self.device), OWNER)
        params = phone_queue.validate_payload("pitch_create", row["payload"])
        guard = phone_queue.phone_server._build_confidential_guard()
        again = phone_pitch.apply("pitch_create", OWNER, cleaned["id"], "new", params, guard)
        self.assertEqual(again[0], "applied")
        self.assertEqual(self.names(), after_first)

    def test_a_crash_after_creating_never_makes_a_second_proposal(self):
        row = self.queue_pitch("pitch_create", {"client_name": "Crashy"})
        real = db.record_phone_action_receipt

        def boom(*a, **k):
            raise RuntimeError("crash before the receipt")

        with patch.object(db, "record_phone_action_receipt", side_effect=boom):
            self.run_sync()
        made = self.names()
        self.assertEqual(len([p for p in self.proposals() if p["client_name"] == "Crashy"]), 1)
        self.run_sync()
        self.assertEqual(len([p for p in self.proposals() if p["client_name"] == "Crashy"]), 1)
        self.assertEqual(self.names(), made)
        self.assertEqual(self.outcome(row)[0], "applied")
        del real


class UpdateTests(_PitchBase):
    def test_edits_are_applied_through_the_pitch_functions(self):
        row = self.update({
            "client_name": "Acme Ltd", "contact": {"email": "owner@acme.example"},
            "notes": "Call on Monday", "hidden_about": ["portfolio"], "price_overrides": {"w_front": 6500},
            "preset": {"title": "Acme plan", "lead": "A plan for Acme.", "initialSelected": ["w_front", "w_anim"]},
        })
        old_version = self.proposal_row()["updated_at"]
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        got = self.proposal_row()
        self.assertEqual(got["client_name"], "Acme Ltd")
        self.assertEqual(got["contact"], {"name": "Acme Owner", "phone": "+91 1", "email": "owner@acme.example"})
        self.assertEqual((got["notes"], got["hidden_about"]), ("Call on Monday", ["portfolio"]))
        self.assertEqual(got["price_overrides"], {"w_front": 6500})
        self.assertEqual((got["preset"]["title"], got["preset"]["lead"]), ("Acme plan", "A plan for Acme."))
        self.assertIn("w_anim", got["preset"]["initialSelected"])
        self.assertNotEqual(got["updated_at"], old_version)
        self.assertEqual(len(self.proposals()), 2)  # the default quotation and this one: no copy

    def test_which_services_each_sheet_shows(self):
        row = self.update({"preset": {"sections": {"p2": ["w_front", "w_anim"], "p3": [], "p4": [], "p5": []}}, "hidden_sheets": ["p3"]})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        got = self.proposal_row()
        self.assertEqual(got["preset"]["sections"]["p2"], ["w_front", "w_anim"])
        self.assertEqual(got["hidden_sheets"], ["p3"])

    def test_a_change_the_pitch_rules_refuse_is_not_applied(self):
        row = self.update({"hidden_sheets": ["p2", "p3", "p4", "p5"]})
        before = self.proposal_row()
        self.run_sync()
        status, result = self.outcome(row)
        self.assertEqual(status, "rejected")
        self.assertIn("hidden_sheets", result)
        self.assertEqual(self.proposal_row(), before)
        self.assertEqual(len(self.proposals()), 2)

    def test_a_price_for_an_unknown_item_or_with_a_bad_value_is_refused(self):
        before = self.proposal_row()
        rows = []
        for bad in ({"no_such_item": 100}, {"w_front": "6000"}, {"w_front": 12.5}, {"w_front": True}, {"w_front": -5},
                    {"w_front": 10_000_001}, {"w_front": None}, [1, 2], "w_front=1"):
            rows.append(self.update({"price_overrides": bad}))
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row)[0], "rejected", row["payload"])
        self.assertEqual(self.proposal_row(), before)

    def test_a_stale_edit_is_kept_as_a_copy_and_the_laptop_version_is_untouched(self):
        stale = self.proposal_row()["updated_at"]
        pitch.update_proposal(self.pid, {"notes": "Changed on the laptop"})
        laptop = self.proposal_row()
        row = self.update({"client_name": "From the phone", "price_overrides": {"w_front": 6200}}, version=stale)
        self.run_sync()
        status, result = self.outcome(row)
        self.assertEqual(status, "applied", result)
        self.assertIn("copy", result.lower())
        self.assertIn("(phone)", result)
        self.assertEqual(self.proposal_row(), laptop)  # never overwritten
        copies = [p for p in self.proposals() if p["id"] not in (self.pid,) and p["kind"] != "default"]
        self.assertEqual(len(copies), 1)
        copy_ = copies[0]
        self.assertTrue(copy_["client_name"].endswith("(phone)"), copy_["client_name"])
        self.assertIn("From the phone", copy_["client_name"])
        self.assertEqual(copy_["price_overrides"], {"w_front": 6200})
        self.assertEqual(copy_["notes"], "Changed on the laptop")  # the copy starts from the laptop's version
        self.assertEqual(copy_["created_by"], "phone")
        self.assertNotEqual(copy_["share_id"], laptop["share_id"])
        self.assertIsNone(copy_["link_status"])

    def test_a_copy_is_made_when_only_a_business_name_exists(self):
        pid2 = pitch.create_proposal({"slug": "only-biz", "business_name": "Biz Only",
                                      "preset": {"initialSelected": SELECTED, "sections": SECTIONS}})["id"]
        stale = db.get_pitch_proposal(pid2)["updated_at"]
        pitch.update_proposal(pid2, {"notes": "laptop"})
        row = self.update({"notes": "phone"}, version=stale, target=pid2)
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied")
        copies = [p for p in self.proposals() if p["slug"].startswith("only-biz-phone")]
        self.assertEqual(len(copies), 1)
        self.assertTrue(pitch_links._proposal_title(copies[0], "x").endswith("(phone)"))

    def test_a_stale_edit_that_already_matches_the_laptop_makes_no_copy(self):
        stale = self.proposal_row()["updated_at"]
        pitch.update_proposal(self.pid, {"client_name": "Same on both"})
        row = self.update({"client_name": "Same on both"}, version=stale)
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied")
        self.assertEqual(len(self.proposals()), 2)

    def test_a_replayed_edit_is_not_turned_into_a_copy(self):
        row = self.update({"client_name": "Applied once"})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied")
        # The version moved on because of this very edit; a replay must not read that as a conflict.
        cleaned = phone_queue.clean_row(dict(row, status="applying", applied_by_device=self.device), OWNER)
        params = phone_queue.validate_payload("pitch_update", row["payload"])
        guard = phone_queue.phone_server._build_confidential_guard()
        outcome = phone_pitch.apply("pitch_update", OWNER, cleaned["id"], self.pid, params, guard)
        self.assertEqual(outcome[0], "applied")
        self.assertEqual(len(self.proposals()), 2)

    def test_two_conflicting_edits_make_two_distinct_copies(self):
        stale = self.proposal_row()["updated_at"]
        pitch.update_proposal(self.pid, {"notes": "laptop moved on"})
        first = self.update({"client_name": "Phone one"}, version=stale)
        second = self.update({"client_name": "Phone two"}, version=stale)
        self.run_sync()
        self.assertEqual(self.outcome(first)[0], "applied")
        self.assertEqual(self.outcome(second)[0], "applied")
        copies = [p for p in self.proposals() if p["kind"] != "default" and p["id"] != self.pid]
        self.assertEqual(len(copies), 2)
        self.assertEqual(len({c["slug"] for c in copies}), 2)

    def test_the_default_quotation_cannot_be_edited(self):
        default = pitch.ensure_default_proposal()
        row = self.update({"notes": "no"}, version=default["updated_at"], target=default["id"])
        before = db.get_pitch_proposal(default["id"])
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "rejected")
        self.assertEqual(db.get_pitch_proposal(default["id"]), before)

    def test_an_edit_for_a_proposal_this_laptop_does_not_have_stays_queued(self):
        row = self.update({"notes": "x"}, version="2026-01-01T00:00:00+00:00", target="prop_not_here")
        self.run_sync()
        self.assertEqual(self.cloud.get_row(row["id"])["status"], "queued")
        self.assertEqual(self.cloud.calls("claim_phone_action"), [])

    def test_a_final_quotation_edit_changes_its_client_but_not_its_agreed_services(self):
        final = pitch.finalize_proposal(self.pid)
        row = self.update({"client_name": "Final client", "price_overrides": {"w_front": 6100}},
                          version=final["updated_at"], target=final["id"])
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        got = db.get_pitch_proposal(final["id"])
        self.assertEqual((got["client_name"], got["kind"]), ("Final client", "final"))
        self.assertEqual(got["price_overrides"], {"w_front": 6100})


class HostileEditTests(_PitchBase):
    def test_angle_brackets_are_refused_in_every_text(self):
        before = self.proposal_row()
        rows = []
        for fields in (
            {"client_name": "<script>alert(1)</script>"}, {"business_name": "Acme > Co"}, {"notes": "a < b"},
            {"contact": {"name": "x<y"}}, {"contact": {"notes": "<b>hi</b>"}}, {"preset": {"lead": "<img src=x>"}},
            {"preset": {"title": "t>"}}, {"preset": {"whatWeDo": "<a>"}}, {"preset": {"portfolio": "<"}},
            {"preset": {"thisPlan": ">"}},
        ):
            rows.append(self.update(fields))
        rows.append(self.queue_pitch("pitch_create", {"client_name": "x<y"}))
        rows.append(self.queue_pitch("pitch_create", {"notes": "x>y"}))
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row)[0], "rejected", row["payload"])
        self.assertEqual(self.proposal_row(), before)
        self.assertEqual(len(self.proposals()), 2)

    def test_fields_a_phone_may_not_set_are_refused_and_nothing_changes(self):
        before = self.proposal_row()
        rows = [self.update({field: value}) for field, value in (
            ("slug", "takeover"), ("status", "sent"), ("kind", "final"), ("created_by", "agent"),
            ("share_id", "AAAAAAAAAAAAAAAAAAAAAA"), ("custom_items", []), ("payment_plan", []), ("link_status", "live"),
            ("id", "prop_other"), ("updated_at", "2099-01-01T00:00:00+00:00"), ("from_preset", "general"),
            ("preset", {"slug": "takeover"}), ("preset", {"architectureBlock": {}}), ("contact", {"whatsapp": "1"}),
            ("hidden_sheets", ["p9"]), ("hidden_about", ["title"]), ("preset", {"sections": {"p9": []}}),
        )]
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row)[0], "rejected", row["payload"])
        self.assertEqual(self.proposal_row(), before)

    def test_malformed_payloads_are_refused(self):
        before = self.proposal_row()
        base = self.proposal_row()["updated_at"]
        rows = [
            self.queue_pitch("pitch_update", payload) for payload in (
                {}, {"fields": {"notes": "x"}}, {"base_version": base}, {"base_version": base, "fields": {}},
                {"base_version": base, "fields": "notes"}, {"base_version": base, "fields": []},
                {"base_version": 7, "fields": {"notes": "x"}}, {"base_version": "", "fields": {"notes": "x"}},
                {"base_version": base, "fields": {"notes": "x"}, "extra": 1}, {"base_version": base, "fields": {"notes": 5}},
                {"base_version": base, "fields": {"client_name": None}}, {"base_version": base, "fields": {"contact": "Meera"}},
                {"base_version": base, "fields": {"notes": "x" * 4001}}, {"base_version": base, "fields": {"notes": "a\u0001b"}},
                {"base_version": "v\u0000", "fields": {"notes": "x"}}, [], "notes",
            )
        ]
        rows.append(self.queue_pitch("pitch_create", {"slug": "mine"}))
        rows.append(self.queue_pitch("pitch_create", {"kind": "final"}))
        rows.append(self.queue_pitch("pitch_create", {"kind": "default"}))
        rows.append(self.queue_pitch("pitch_create", {"duplicate_of": self.pid}))
        rows.append(self.queue_pitch("pitch_create", [1]))
        rows.append(self.queue_pitch("pitch_finalize", {"slug": "x"}))
        rows.append(self.queue_pitch("pitch_link", {"action": "delete"}))
        rows.append(self.queue_pitch("pitch_link", {}))
        rows.append(self.queue_pitch("pitch_link", {"action": "publish", "share_id": "x"}))
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row)[0], "rejected", row["payload"])
        self.assertEqual(self.proposal_row(), before)
        self.assertEqual(len(self.proposals()), 2)
        self.assertEqual(self.published_calls(), [])

    def test_a_row_of_another_owner_is_never_applied(self):
        row = self.update({"client_name": "Not yours", "price_overrides": {"w_front": 1}}, owner=OTHER)
        create = self.queue_pitch("pitch_create", {"client_name": "Not yours either"}, owner=OTHER)
        self.run_sync()
        self.assertEqual(self.cloud.get_row(row["id"])["status"], "queued")
        self.assertEqual(self.cloud.get_row(create["id"])["status"], "queued")
        self.assertEqual(self.proposal_row()["client_name"], "Acme")
        self.assertEqual(len(self.proposals()), 2)

    def test_an_agent_never_sets_a_price(self):
        # The agent path strips every price, and a chat request to an agent cannot edit a proposal or a price.
        made = pitch.create_proposal({"slug": "agent-made", "price_overrides": {"w_front": 1}, "custom_items": [
            {"id": "custom_x", "g": "Backend", "n": "x", "d": "y", "p": 5, "r": []}]}, created_by="agent:writer", strip_prices=True)
        self.assertEqual((made["price_overrides"], made["custom_items"]), ({}, []))
        self.assertEqual(phone_queue.KINDS.count("agent_task"), 1)
        row = self.queue(kind="agent_task", target="writer", payload={"message": "Set the price of w_front to 1 for Acme"})
        before = self.proposal_row()
        with patch("phone_queue.agent_chat.chat_with_agent", return_value={"success": True, "session_id": "s1", "response": "ok"}):
            self.run_sync()
            phone_queue.wait_for_agent_tasks()
        self.assertEqual(self.proposal_row(), before)
        self.assertEqual(self.proposal_row()["price_overrides"], {})
        del row

    def test_only_the_person_prices_come_from_the_validated_whitelist(self):
        # created_by cannot be chosen by the payload, so a phone proposal is always marked as phone-made.
        row = self.queue_pitch("pitch_create", {"client_name": "Phone made", "price_overrides": {"w_front": 7000}})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied")
        made = [p for p in self.proposals() if p["client_name"] == "Phone made"][0]
        self.assertEqual(made["created_by"], "phone")


class ConfidentialTests(_PitchBase):
    def test_a_confidential_proposal_cannot_be_edited_finalized_or_published_from_the_phone(self):
        conf = pitch.create_proposal({"slug": "secret-one", "client_name": "ProjectNightfall Ltd",
                                      "preset": {"initialSelected": SELECTED, "sections": SECTIONS}})
        before = db.get_pitch_proposal(conf["id"])
        rows = [
            self.update({"notes": "harmless"}, version=before["updated_at"], target=conf["id"]),
            self.queue_pitch("pitch_finalize", {}, target=conf["id"]),
            self.queue_pitch("pitch_link", {"action": "publish"}, target=conf["id"]),
            self.queue_pitch("pitch_link", {"action": "withdraw"}, target=conf["id"]),
        ]
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row), ("rejected", phone_queue.CONFIDENTIAL_REASON))
        self.assertEqual(db.get_pitch_proposal(conf["id"]), before)
        self.assertEqual(self.published_calls(), [])
        self.assertEqual(len([p for p in self.proposals() if p["kind"] == "final"]), 0)

    def test_text_naming_a_confidential_product_is_refused_and_not_echoed(self):
        before = self.proposal_row()
        rows = [
            self.update({"notes": "Prepare ProjectNightfall demo"}),
            self.update({"preset": {"lead": "Built on projectnightfall"}}),
            self.update({"contact": {"notes": "ask about ProjectNightfall"}}),
            self.queue_pitch("pitch_create", {"client_name": "ProjectNightfall fan club"}),
        ]
        self.run_sync()
        for row in rows:
            self.assertEqual(self.outcome(row), ("rejected", phone_queue.CONFIDENTIAL_TEXT_REASON))
            self.assertNotIn("Nightfall", json.dumps(self.cloud.get_row(row["id"])["result"]))
        self.assertEqual(self.proposal_row(), before)
        self.assertEqual(len(self.proposals()), 2)

    def test_unreadable_product_rules_pause_the_pitch_actions_too(self):
        row = self.update({"notes": "fine"})
        os.remove(self.rules_path)
        with patch.object(phone_queue.phone_server, "_build_confidential_guard", side_effect=phone_queue.phone_server.PhoneApiError("no rules", status=503)):
            summary = self.run_sync()
        self.assertTrue(summary["paused"])
        self.assertEqual(self.cloud.get_row(row["id"])["status"], "queued")
        self.assertEqual(self.proposal_row()["client_name"], "Acme")


class FinalizeTests(_PitchBase):
    def test_a_proposal_becomes_a_final_quotation_and_the_original_is_kept(self):
        row = self.queue_pitch("pitch_finalize", {})
        original = self.proposal_row()
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        finals = [p for p in self.proposals() if p["kind"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["client_name"], "Acme")
        self.assertEqual(finals[0]["created_by"], "phone")
        self.assertEqual(self.proposal_row(), original)

    def test_finalizing_twice_makes_one_final(self):
        row = self.queue_pitch("pitch_finalize", {})
        self.run_sync()
        cleaned = phone_queue.clean_row(dict(row, status="applying", applied_by_device=self.device), OWNER)
        guard = phone_queue.phone_server._build_confidential_guard()
        again = phone_pitch.apply("pitch_finalize", OWNER, cleaned["id"], self.pid, {}, guard)
        self.assertEqual(again[0], "applied")
        self.assertEqual(len([p for p in self.proposals() if p["kind"] == "final"]), 1)

    def test_a_proposal_with_nothing_agreed_cannot_be_finalized(self):
        empty = pitch.create_proposal({"slug": "empty-plan", "client_name": "Nobody"})
        row = self.queue_pitch("pitch_finalize", {}, target=empty["id"])
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "rejected")
        self.assertEqual(len([p for p in self.proposals() if p["kind"] == "final"]), 0)

    def test_the_default_quotation_cannot_be_finalized_from_the_phone(self):
        default = pitch.ensure_default_proposal()
        row = self.queue_pitch("pitch_finalize", {}, target=default["id"])
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "rejected")


class LinkTests(_PitchBase):
    def test_publish_builds_the_public_payload_on_the_laptop_and_marks_it_live(self):
        row = self.queue_pitch("pitch_link", {"action": "publish"})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        posts = [r for r in self.published_calls() if r["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        body = posts[0]["body"]
        self.assertEqual((body["owner_id"], body["local_proposal_id"], body["status"]), (OWNER, self.pid, "live"))
        self.assertNotIn("notes", body["payload"])
        self.assertNotIn("Internal note", json.dumps(body["payload"]))
        self.assertEqual(self.proposal_row()["link_status"], "live")

    def test_withdraw_closes_the_link(self):
        pitch_links.publish(self.pid)
        row = self.queue_pitch("pitch_link", {"action": "withdraw"})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied", self.outcome(row))
        patches = [r for r in self.published_calls() if r["method"] == "PATCH"]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["body"], {"status": "withdrawn"})
        self.assertEqual(self.proposal_row()["link_status"], "withdrawn")

    def test_a_cloud_that_is_down_is_retried_and_then_given_up_with_a_fixed_reason(self):
        row = self.queue_pitch("pitch_link", {"action": "publish"})
        orig = self.cloud.urlopen

        def flaky(req, timeout=0):
            if "published_proposals" in req.full_url:
                raise _http_error(req.full_url, 502, "bad gateway: internal detail")
            return orig(req, timeout)

        with patch("team_auth.urllib.request.urlopen", side_effect=flaky):
            self.run_sync()
            self.assertEqual(self.cloud.get_row(row["id"])["status"], "applying")  # not rejected on the first failure
            self.run_sync()
            self.run_sync()
        status, result = self.outcome(row)
        self.assertEqual(status, "rejected")
        self.assertEqual(result, phone_queue.FAILED_REASON)
        self.assertNotIn("internal detail", result)

    def test_a_proposal_that_changed_after_it_went_live_is_republished_by_the_normal_cycle(self):
        pitch_links.publish(self.pid)
        before = len([r for r in self.published_calls() if r["method"] == "POST"])
        row = self.update({"client_name": "Renamed on phone"})
        self.run_sync()
        self.assertEqual(self.outcome(row)[0], "applied")
        pitch_links.sync_live_links(OWNER)
        after = len([r for r in self.published_calls() if r["method"] == "POST"])
        self.assertEqual(after, before + 1)


class OlderEngineTests(_PitchBase):
    def test_an_engine_without_pitch_kinds_leaves_them_queued(self):
        rows = [self.queue_pitch("pitch_link", {"action": "publish"}), self.update({"notes": "x"})]
        old = tuple(k for k in phone_queue.KINDS if k not in PITCH_KINDS)
        with patch.object(phone_queue, "KINDS", old):
            self.run_sync()
        for row in rows:
            self.assertEqual(self.cloud.get_row(row["id"])["status"], "queued")
        self.assertEqual(self.cloud.calls("claim_phone_action"), [])

    def test_the_pull_asks_for_the_pitch_kinds(self):
        self.queue_pitch("pitch_link", {"action": "withdraw"})
        self.run_sync()
        gets = [r for r in self.cloud.requests if r["method"] == "GET" and "phone_actions" in r["url"]]
        for kind in PITCH_KINDS:
            self.assertIn(kind, gets[0]["url"])


class ContractTests(_PitchBase):
    def test_every_body_the_phone_sends_is_applied_or_refused_for_a_reason_not_ignored(self):
        path = os.path.join(REPO_ROOT, "apps", "mobile", "src", "fixtures", "phone_pitch_bodies.json")
        with open(path, "r", encoding="utf-8") as f:
            fixture = json.load(f)
        names = [item["name"] for item in fixture["actions"]]
        self.assertEqual(names, ["create", "create_from_preset", "update", "finalize", "publish", "withdraw"])
        version = self.proposal_row()["updated_at"]
        rows = {}
        for item in fixture["actions"]:
            raw = json.dumps(item["body"]).replace("{{PROPOSAL}}", self.pid).replace("{{VERSION}}", version)
            body = json.loads(raw)
            self.assertEqual(sorted(body), ["id", "kind", "payload", "target_id"])
            row = self.queue(kind=body["kind"], target=body["target_id"], payload=body["payload"])
            row["id"] = body["id"]
            rows[item["name"]] = row
        self.run_sync()
        for name, row in rows.items():
            self.assertEqual(self.result_of(row)["status"], "applied", (name, self.result_of(row)["result"]))
        self.assertEqual(self.proposal_row()["client_name"], "Meera S.")
        self.assertEqual(self.proposal_row()["price_overrides"], {"w_front": 6500})


class MirrorTests(_PitchBase):
    def records(self):
        return [r for r in cloud_sync.build_mirror_records() if r["kind"] == "pitch_proposal"]

    def test_every_proposal_is_mirrored_with_what_the_phone_needs_to_edit_and_present(self):
        recs = {r["local_id"]: r for r in self.records()}
        self.assertIn(self.pid, recs)
        rec = recs[self.pid]
        data = rec["data"]
        self.assertEqual(rec["title"], "Acme Co · Acme")
        self.assertEqual(data["version"], self.proposal_row()["updated_at"])
        self.assertEqual(rec["local_updated_at"], self.proposal_row()["updated_at"])
        self.assertEqual((data["client_name"], data["business_name"], data["kind"]), ("Acme", "Acme Co", "proposal"))
        self.assertEqual(data["contact"]["phone"], "+91 1")
        self.assertEqual(data["preset"]["initialSelected"], ["w_front"])
        self.assertIn("lead", data["preset"])
        self.assertEqual(data["payload"]["mode"], "present")
        self.assertIn("w_front", data["payload"]["prices"])
        self.assertIn("w_front", data["catalog"]["items"])
        self.assertEqual(set(data["catalog"]["items"]["w_front"]), {"g", "n", "p"})
        self.assertIn("urban-company", [p["slug"] for p in data["catalog"]["presets"]])
        self.assertIsNone(data.get("link_url"))
        # The default quotation is mirrored too (to present it), but cannot be edited.
        default = [r for r in recs.values() if r["data"].get("kind") == "default"]
        self.assertEqual(len(default), 1)
        self.assertEqual(default[0]["data"]["name"], "Default quotation")

    def test_a_live_link_is_mirrored_with_its_address_and_a_withdrawn_one_without(self):
        pitch_links.publish(self.pid)
        data = {r["local_id"]: r for r in self.records()}[self.pid]["data"]
        self.assertEqual(data["link_status"], "live")
        self.assertTrue(data["link_url"].startswith("https://magnm-pitch.vercel.app/p/"))
        self.assertTrue(data["link_url"].endswith(self.proposal_row()["share_id"]))
        pitch_links.withdraw(self.pid)
        data = {r["local_id"]: r for r in self.records()}[self.pid]["data"]
        self.assertEqual(data["link_status"], "withdrawn")
        self.assertIsNone(data.get("link_url"))

    def test_a_confidential_proposal_is_only_a_stub(self):
        conf = pitch.create_proposal({"slug": "secret-two", "client_name": "ProjectNightfall Ltd", "notes": "roadmap details",
                                      "preset": {"initialSelected": SELECTED, "sections": SECTIONS}})
        rec = {r["local_id"]: r for r in self.records()}[conf["id"]]
        self.assertTrue(rec["data"]["confidential"])
        self.assertEqual(rec["title"], "Confidential item")
        text = json.dumps(rec)
        for leak in ("Nightfall", "roadmap", "secret-two", "payload", "catalog", conf["share_id"]):
            self.assertNotIn(leak, text)

    def test_confidential_text_hiding_in_a_less_obvious_field_stubs_the_proposal(self):
        conf = pitch.create_proposal({"slug": "sneaky", "client_name": "Fine", "contact": {"notes": "wants ProjectNightfall"},
                                      "preset": {"initialSelected": SELECTED, "sections": SECTIONS}})
        rec = {r["local_id"]: r for r in self.records()}[conf["id"]]
        self.assertTrue(rec["data"]["confidential"])
        self.assertNotIn("wants", json.dumps(rec))

    def test_the_record_fits_the_cloud_size_cap(self):
        for rec in self.records():
            self.assertLessEqual(len(json.dumps(rec["data"]).encode("utf-8")), cloud_sync.MAX_RECORD_DATA_BYTES)

    def test_a_deleted_proposal_is_removed_from_the_cloud_mirror(self):
        recs, state = cloud_sync._compute_pending_items(OWNER)
        db.upsert_cloud_sync_state(OWNER, [(k, lid, cloud_sync._hash_record(r), "2026-11-23T00:00:00+00:00", None)
                                           for r in cloud_sync.build_mirror_records() for k, lid in [(r["kind"], r["local_id"])]])
        pitch.delete_proposal(self.pid, hard=True)
        pending, _ = cloud_sync._compute_pending_items(OWNER)
        deleted = [p for p in pending if p[0] == "pitch_proposal" and p[1] == self.pid]
        self.assertEqual(len(deleted), 1)
        self.assertEqual(deleted[0][2], cloud_sync.DELETED_HASH)
        del recs, state

    def test_proposals_that_cannot_be_built_hold_nothing_else_back_and_remove_nothing_in_the_cloud(self):
        db.create_content_item("cnt_x", "post", "linkedin", "Cero launch", "Cero is out today.")
        everything = cloud_sync.build_mirror_records()
        db.upsert_cloud_sync_state(OWNER, [(r["kind"], r["local_id"], cloud_sync._hash_record(r), "2026-11-23T00:00:00+00:00", None)
                                           for r in everything])
        with patch.object(phone_pitch, "mirror_records", side_effect=RuntimeError("pitch files unreadable")):
            records = cloud_sync.build_mirror_records()
            pending, _ = cloud_sync._compute_pending_items(OWNER)
        self.assertFalse([r for r in records if r["kind"] == "pitch_proposal"])
        self.assertTrue([r for r in records if r["kind"] == "content_item"])
        self.assertEqual([p for p in pending if p[0] == "pitch_proposal"], [])
        # Next cycle, when they can be built again, nothing is stuck.
        pending, _ = cloud_sync._compute_pending_items(OWNER)
        self.assertEqual([p for p in pending if p[2] == cloud_sync.DELETED_HASH], [])


if __name__ == "__main__":
    unittest.main()

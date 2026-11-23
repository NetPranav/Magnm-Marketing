"""Personal repositories synced between the laptop and the phone through the cloud (owner only).

No network: the cloud is faked (tests/fake_personal_cloud.py). Names are invented; ProjectNightfall is the made-up
confidential product. Confidential repos must never reach the cloud.
"""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import db
import personal_repo_sync
import repos
import retired_repos
import team_auth
from tests.fake_personal_cloud import FakeCloud
from tests.test_repo_edit import RepoBase, git_init


class SyncBase(RepoBase):
    def setUp(self):
        super().setUp()
        self.cloud = FakeCloud()
        patcher = patch("team_auth.rest", self.cloud.rest)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.no_net = patch.object(repos, "_github_request", side_effect=AssertionError("network call"))
        self.no_net.start()
        self.addCleanup(self.no_net.stop)
        self.sign_in("user-a")

    def sign_in(self, uid):
        team_auth._save_session({
            "access_token": f"tok-{uid}", "refresh_token": f"ref-{uid}",
            "expires_at": int(time.time()) + 3600, "user_id": uid, "email": f"{uid}@example.com",
        })
        self.uid = uid

    def sync(self):
        return personal_repo_sync.sync(self.uid)

    def rows(self, owner=None):
        return sorted(self.cloud.rows_of(owner), key=lambda r: r["id"])

    def row(self, rid):
        return next(r for r in self.rows() if r["id"] == rid)

    def sent(self):
        return json.dumps(self.cloud.requests)

    def local_repo(self, rid, name="Demo Repo", **extra):
        path = git_init(os.path.join(self.home, "code", rid))
        return {"id": rid, "name": name, "product_id": rid, "kind": "local", "local_path": path,
                "confidential": False, "status": "in_development", **extra}

    def past(self, uid, field, minutes=60):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        self.cloud.repos[uid]["field_at"][field] = stamp

    def future(self, uid, field, minutes=60):
        stamp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        self.cloud.repos[uid]["field_at"][field] = stamp


class UploadTests(SyncBase):
    def test_local_repos_are_uploaded_and_mapped(self):
        self.write([self.gh("demo-app", branch="main", marketing_angle="Calm."), self.local_repo("tool")])
        out = self.sync()
        self.assertEqual(sorted(out["uploaded"]), ["demo-app", "tool"])
        row = self.row("demo-app")
        self.assertEqual((row["name"], row["kind"], row["github"], row["branch"], row["marketing_angle"]),
                         ("Demo App", "github", "acme/demo-app", "main", "Calm."))
        tool = self.row("tool")
        self.assertEqual((tool["kind"], tool["path_status"]), ("local", "ok"))
        self.assertTrue(tool["local_path"].endswith(os.path.join("code", "tool")))

    def test_a_second_sync_changes_nothing(self):
        self.write([self.gh("demo-app")])
        self.sync()
        before = len(self.cloud.requests)
        out = self.sync()
        self.assertEqual({k: v for k, v in out.items() if v}, {})
        writes = [r for r in self.cloud.requests[before:] if r["path"].startswith("rpc/")]
        self.assertEqual(writes, [])

    def test_confidential_repos_never_reach_the_cloud(self):
        self.write([
            self.gh("flagged", name="Flagged Repo", confidential=True),
            self.gh("secretproj", name="Quiet Repo"),
            self.gh("named", name="ProjectNightfall companion"),
            self.gh("angle", name="Plain", marketing_angle="Related to ProjectNightfall"),
            self.gh("ok-one"),
        ])
        out = self.sync()
        self.assertEqual(out["uploaded"], ["ok-one"])
        blob = self.sent()
        for word in ("Flagged Repo", "secretproj", "Nightfall", "acme/flagged", "acme/named", "acme/angle"):
            self.assertNotIn(word, blob)
        self.assertEqual([r["id"] for r in self.rows()], ["ok-one"])

    def test_a_folder_named_after_a_confidential_product_is_not_uploaded(self):
        entry = self.local_repo("quiet")
        entry["local_path"] = git_init(os.path.join(self.home, "code", "ProjectNightfall-app"))
        self.write([entry])
        self.sync()
        self.assertEqual(self.rows(), [])
        self.assertNotIn("Nightfall", self.sent())

    def test_a_repo_that_becomes_confidential_is_removed_from_the_cloud_and_stays_off(self):
        self.write([self.gh("demo-app")])
        self.sync()
        repos.update_repo("demo-app", {"confidential": True})
        out = self.sync()
        self.assertEqual(out["purged"], ["demo-app"])
        self.assertEqual(self.rows(), [])
        self.sync()
        self.assertEqual(self.rows(), [])
        self.assertIn("demo-app", self.stored())

    def test_team_repos_are_never_uploaded(self):
        self.write([self.gh("mine")])
        self.cached.return_value = [{"id": "team-app", "github": "acme/team-app", "name": "Team App",
                                     "product_id": "team-app", "kind": "github", "confidential": False,
                                     "status": "in_development", "scope": "global"}]
        self.sync()
        self.assertEqual([r["id"] for r in self.rows()], ["mine"])

    def test_an_existing_cloud_repo_with_the_same_github_is_adopted_not_duplicated(self):
        self.write([self.gh("demo-app")])
        self.cloud.add_repo_row(id="from-phone", name="From Phone", kind="github", github="Acme/Demo-App")
        self.sync()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.stored()), 1)

    def test_at_most_one_hundred_repos_are_uploaded(self):
        self.write([self.gh(f"bulk-{i}") for i in range(103)])
        self.sync()
        self.assertEqual(len(self.rows()), 100)


class PullTests(SyncBase):
    def test_a_repo_added_on_the_phone_appears_on_the_laptop_without_network(self):
        self.cloud.add_repo_row(id="phone-app", name="Phone App", kind="github", github="acme/phone-app",
                                branch="dev", status="released", marketing_angle="Quick wins.")
        out = self.sync()
        self.assertEqual(out["imported"], ["phone-app"])
        entry = self.stored()["phone-app"]
        self.assertEqual((entry["kind"], entry["github"], entry["branch"], entry["status"], entry["product_id"]),
                         ("github", "acme/phone-app", "dev", "released", "phone-app"))
        self.assertFalse(entry["confidential"])
        self.assertEqual(entry["marketing_angle"], "Quick wins.")
        self.sync()
        self.assertEqual(len(self.stored()), 1)

    def test_a_taken_id_gets_a_suffix_and_the_phone_follows(self):
        # A legacy entry the cloud cannot hold (name too long) is not uploaded, but its id is used here.
        self.write([self.gh("phone-app", name="N" * 90)])
        row = self.cloud.add_repo_row(id="phone-app", name="Phone App", kind="github", github="acme/phone-app-x")
        self.sync()
        self.assertEqual(sorted(self.stored()), ["phone-app", "phone-app-2"])
        self.assertEqual(self.cloud.repos[row["uid"]]["id"], "phone-app-2")
        self.assertEqual(self.stored()["phone-app"]["name"], "N" * 90)

    def test_a_phone_repo_that_names_a_confidential_product_stays_on_the_laptop_only(self):
        row = self.cloud.add_repo_row(id="sneaky", name="ProjectNightfall mobile", kind="github", github="acme/sneaky")
        out = self.sync()
        self.assertEqual(out["purged"], ["sneaky"])
        self.assertTrue(self.stored()["sneaky"]["confidential"])
        self.assertNotIn(row["uid"], self.cloud.repos)
        later = len(self.cloud.requests)
        self.sync()
        self.assertNotIn("Nightfall", json.dumps(self.cloud.requests[later:]))
        self.assertEqual(self.rows(), [])

    def test_a_phone_repo_flagged_confidential_is_imported_flagged_and_removed_from_the_cloud(self):
        self.cloud.add_repo_row(id="flag-me", name="Flag Me", kind="github", github="acme/flag-me", confidential=True)
        self.sync()
        self.assertTrue(self.stored()["flag-me"]["confidential"])
        self.assertEqual(self.rows(), [])

    def test_hostile_rows_are_ignored(self):
        now = datetime.now(timezone.utc).isoformat()
        good = {"uid": "00000000-0000-4000-8000-000000000001", "owner_id": "user-a", "id": "good-one", "name": "Good",
                "kind": "github", "github": "acme/good-one", "local_path": None, "path_status": "ok", "path_reason": None,
                "branch": None, "status": "in_development", "marketing_angle": None, "confidential": False,
                "rev": 1, "field_rev": {}, "field_at": {}, "created_at": now, "updated_at": now}

        def mutate(**over):
            return {**good, "uid": f"00000000-0000-4000-8000-{abs(hash(str(over))) % 10**12:012d}", **over}

        bad = [
            mutate(id="../evil", github="acme/evil-1"), mutate(id="UP", github="acme/evil-2"),
            mutate(id="evil-3", github="https://github.com/acme/x"), mutate(id="evil-4", github="a/b; rm -rf"),
            mutate(id="evil-5", name="Line\nbreak", github="acme/evil-5"), mutate(id="evil-6", name="x" * 81, github="acme/evil-6"),
            mutate(id="evil-7", github="acme/evil-7", branch="--upload-pack=x"),
            mutate(id="evil-8", github="acme/evil-8", status="live"), mutate(id="evil-9", github="acme/evil-9", kind="svn"),
            mutate(id="evil-10", github=None), mutate(id="evil-11", kind="local", github=None, local_path=None),
            mutate(id="evil-12", github="acme/evil-12", owner_id="user-b"), mutate(id="evil-13", github="acme/evil-13", uid="not-a-uuid"),
            mutate(id="evil-14", github="acme/evil-14", marketing_angle="x" * 501),
            "a string", 5, None, ["x"],
        ]
        self.cloud.extra_rows = [good] + bad
        self.sync()
        self.assertEqual(sorted(self.stored()), ["good-one"])

    def test_phone_path_tricks_are_rejected_and_never_stored(self):
        tricks = ["/etc", "/", self.home, "~", "../../etc", "~/../..", os.path.join(self.home, ".ssh"),
                  os.path.join(self.data, "state"), "relative/dir", "file:///etc"]
        for i, bad in enumerate(tricks):
            row = self.cloud.add_repo_row(id=f"trick-{i}", name=f"Trick {i}", kind="local", local_path=bad)
            self.cloud.repos[row["uid"]]["path_status"] = "pending"
        self.sync()
        self.assertEqual(self.stored(), {})
        for r in self.rows():
            if ".." in r["local_path"]:
                self.assertEqual(r["path_status"], "pending")  # a row with '..' is dropped unread
                continue
            self.assertEqual(r["path_status"], "rejected", r["local_path"])
            self.assertTrue(r["path_reason"])

    def test_a_good_phone_path_is_applied_and_marked_ok(self):
        folder = git_init(os.path.join(self.home, "code", "fine"))
        row = self.cloud.add_repo_row(id="fine", name="Fine", kind="local", local_path=folder)
        self.cloud.repos[row["uid"]]["path_status"] = "pending"
        self.sync()
        self.assertEqual(self.stored()["fine"]["local_path"], folder)
        self.assertEqual(self.cloud.repos[row["uid"]]["path_status"], "ok")

    def test_another_members_rows_are_never_used(self):
        self.cloud.add_repo_row(owner="user-b", id="theirs", name="Theirs", kind="github", github="acme/theirs")
        self.sync()
        self.assertEqual(self.stored(), {})
        self.assertNotIn("user-b", self.sent())


class MergeTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.write([self.gh("demo-app", branch="main")])
        self.sync()
        self.uid_row = self.row("demo-app")["uid"]

    def test_phone_edit_of_a_field_reaches_the_laptop(self):
        self.cloud.edit_row(self.uid_row, {"name": "Phone Name", "status": "released"})
        out = self.sync()
        self.assertEqual(out["updated_local"], ["demo-app"])
        self.assertEqual((self.stored()["demo-app"]["name"], self.stored()["demo-app"]["status"]), ("Phone Name", "released"))

    def test_laptop_edit_reaches_the_phone(self):
        repos.update_repo("demo-app", {"branch": "release", "marketing_angle": "New angle"})
        out = self.sync()
        self.assertEqual(out["updated_cloud"], ["demo-app"])
        row = self.row("demo-app")
        self.assertEqual((row["branch"], row["marketing_angle"]), ("release", "New angle"))

    def test_edits_of_different_fields_on_both_sides_both_survive(self):
        repos.update_repo("demo-app", {"branch": "release"})
        self.cloud.edit_row(self.uid_row, {"name": "Phone Name"})
        self.sync()
        self.assertEqual((self.stored()["demo-app"]["name"], self.stored()["demo-app"]["branch"]), ("Phone Name", "release"))
        row = self.row("demo-app")
        self.assertEqual((row["name"], row["branch"]), ("Phone Name", "release"))

    def test_the_later_edit_of_the_same_field_wins(self):
        repos.update_repo("demo-app", {"name": "Laptop Name"})
        self.cloud.edit_row(self.uid_row, {"name": "Phone Name"})
        self.future(self.uid_row, "name")
        self.sync()
        self.assertEqual(self.stored()["demo-app"]["name"], "Phone Name")
        self.assertEqual(self.row("demo-app")["name"], "Phone Name")

    def test_the_laptop_wins_when_its_edit_is_later(self):
        self.cloud.edit_row(self.uid_row, {"name": "Phone Name"})
        self.past(self.uid_row, "name")
        repos.update_repo("demo-app", {"name": "Laptop Name"})
        self.sync()
        self.assertEqual(self.stored()["demo-app"]["name"], "Laptop Name")
        self.assertEqual(self.row("demo-app")["name"], "Laptop Name")

    def test_a_rename_on_the_phone_moves_the_laptop_id_and_its_references(self):
        db.save_repo_snapshot("demo-app", "2026-01-01T00:00:00Z", {"version": "1.0", "synced": True})
        db.create_product_fact(product_id="demo-app", text="A fact", source_kind="manual", status="approved")
        self.cloud.edit_row(self.uid_row, {"id": "renamed-app"})
        self.sync()
        self.assertEqual(sorted(self.stored()), ["renamed-app"])
        self.assertEqual(db.get_latest_repo_snapshot("renamed-app")["data"]["version"], "1.0")
        self.assertEqual(len(db.list_product_facts("renamed-app")), 1)
        self.sync()
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(len(self.stored()), 1)

    def test_a_rename_on_the_laptop_keeps_the_same_cloud_row(self):
        repos.update_repo("demo-app", {"id": "renamed-app"})
        self.sync()
        self.assertEqual([(r["uid"], r["id"]) for r in self.rows()], [(self.uid_row, "renamed-app")])

    def test_a_phone_rename_onto_an_id_the_laptop_uses_is_not_applied_and_is_pushed_back(self):
        self.write([*self.stored().values(), self.gh("locked-one", name="N" * 90)])
        self.sync()
        self.cloud.edit_row(self.uid_row, {"id": "locked-one"})
        self.sync()
        self.assertEqual(sorted(self.stored()), ["demo-app", "locked-one"])
        self.assertEqual(self.row("demo-app")["uid"], self.uid_row)

    def test_a_phone_path_edit_is_checked_before_it_is_used(self):
        local = self.local_repo("tool")
        self.write([*self.stored().values(), local])
        self.sync()
        uid = self.row("tool")["uid"]
        good = git_init(os.path.join(self.home, "code", "moved"))
        self.cloud.edit_row(uid, {"local_path": good})
        self.assertEqual(self.row("tool")["path_status"], "pending")
        self.sync()
        self.assertEqual(self.stored()["tool"]["local_path"], good)
        self.assertEqual(self.row("tool")["path_status"], "ok")

    def test_a_rejected_phone_path_keeps_the_old_path_and_is_not_rechecked_every_cycle(self):
        local = self.local_repo("tool")
        self.write([*self.stored().values(), local])
        self.sync()
        uid = self.row("tool")["uid"]
        self.cloud.edit_row(uid, {"local_path": "/etc"})
        self.sync()
        self.assertEqual(self.stored()["tool"]["local_path"], local["local_path"])
        self.assertEqual(self.row("tool")["path_status"], "rejected")
        mark = len(self.cloud.requests)
        self.sync()
        calls = [r for r in self.cloud.requests[mark:] if r["path"] == "rpc/personal_repo_path_result"]
        self.assertEqual(calls, [])
        self.assertEqual(self.stored()["tool"]["local_path"], local["local_path"])

    def test_a_phone_edit_that_names_a_confidential_product_flags_it_and_removes_it_from_the_cloud(self):
        self.cloud.edit_row(self.uid_row, {"marketing_angle": "Works with ProjectNightfall"})
        out = self.sync()
        self.assertEqual(out["purged"], ["demo-app"])
        self.assertTrue(self.stored()["demo-app"]["confidential"])
        self.assertEqual(self.rows(), [])

    def test_a_confidential_flag_set_on_the_phone_is_applied_and_purged(self):
        self.cloud.edit_row(self.uid_row, {"confidential": True})
        self.sync()
        self.assertTrue(self.stored()["demo-app"]["confidential"])
        self.assertEqual(self.rows(), [])

    def test_github_and_kind_changed_in_the_cloud_are_pushed_back(self):
        self.cloud.repos[self.uid_row]["github"] = "evil/other"
        self.sync()
        self.assertEqual(self.stored()["demo-app"]["github"], "acme/demo-app")
        self.assertEqual(self.row("demo-app")["github"], "acme/demo-app")


class RemovalTests(SyncBase):
    def setUp(self):
        super().setUp()
        self.write([self.gh("demo-app"), self.gh("second-app")])
        self.sync()

    def test_removing_on_the_phone_removes_on_the_laptop(self):
        uid = self.row("demo-app")["uid"]
        del self.cloud.repos[uid]
        out = self.sync()
        self.assertEqual(out["removed_local"], ["demo-app"])
        self.assertEqual(sorted(self.stored()), ["second-app"])
        self.sync()
        self.assertEqual(sorted(self.stored()), ["second-app"])
        self.assertEqual([r["id"] for r in self.rows()], ["second-app"])

    def test_removing_on_the_laptop_removes_in_the_cloud(self):
        repos.remove_repo("demo-app")
        out = self.sync()
        self.assertEqual(out["removed_cloud"], ["demo-app"])
        self.assertEqual([r["id"] for r in self.rows()], ["second-app"])
        self.sync()
        self.assertEqual(sorted(self.stored()), ["second-app"])

    def test_a_laptop_removal_is_remembered_until_the_cloud_is_reachable(self):
        repos.remove_repo("demo-app")
        self.cloud.down = True
        with self.assertRaises(personal_repo_sync.PersonalReposError):
            self.sync()
        self.cloud.down = False
        self.sync()
        self.assertEqual([r["id"] for r in self.rows()], ["second-app"])
        self.assertNotIn("demo-app", self.stored())

    def test_a_repo_missing_from_the_laptop_file_without_a_removal_comes_back(self):
        self.write([self.gh("second-app")])  # hand edit or a restore: not a removal through the app
        self.sync()
        self.assertIn("demo-app", self.stored())
        self.assertEqual(len(self.rows()), 2)

    def test_an_empty_cloud_list_does_not_wipe_the_laptop(self):
        self.cloud.repos.clear()
        self.sync()
        self.assertEqual(sorted(self.stored()), ["demo-app", "second-app"])
        self.sync()
        self.assertEqual(len(self.rows()), 2)

    def test_a_cloud_failure_deletes_nothing_locally(self):
        self.cloud.down = True
        with self.assertRaises(personal_repo_sync.PersonalReposError):
            self.sync()
        self.assertEqual(sorted(self.stored()), ["demo-app", "second-app"])

    def test_removing_a_confidential_repo_keeps_its_names_guarded(self):
        repos.update_repo("second-app", {"confidential": True})
        self.sync()
        repos.remove_repo("second-app")
        self.assertIn("second-app", retired_repos.terms())


class AccountTests(SyncBase):
    def test_signing_out_stops_the_sync(self):
        self.write([self.gh("demo-app")])
        with self.assertRaises(personal_repo_sync.SignedOutAbort):
            personal_repo_sync.sync(self.uid, is_signed_out=lambda: True)
        with self.assertRaises(personal_repo_sync.SignedOutAbort):
            personal_repo_sync.sync("someone-else")
        self.assertEqual(self.rows(), [])

    def test_another_account_never_sees_the_first_accounts_cloud_rows(self):
        self.write([self.gh("demo-app")])
        self.sync()
        self.sign_in("user-b")
        self.sync()
        self.assertEqual(len(self.rows("user-a")), 1)
        self.assertEqual(self.cloud.rows_of("user-a")[0]["owner_id"], "user-a")
        # A's mapping never deletes anything of B or A on the switch.
        self.assertEqual(sorted(self.stored()), ["demo-app"])

    def test_the_sync_state_holds_no_secret_text(self):
        self.write([self.gh("demo-app", marketing_angle="Angle")])
        self.sync()
        state = db.get_meta(personal_repo_sync.STATE_META)
        self.assertNotIn("nvapi", state)


class CycleTests(SyncBase):
    def test_a_failing_repo_sync_does_not_stop_the_cycle(self):
        seen = []
        with patch.object(personal_repo_sync, "sync", side_effect=RuntimeError("boom")), \
             patch.object(cloud_sync, "_log_warning", side_effect=seen.append):
            cloud_sync._sync_personal_repos(self.uid, 0)
        self.assertTrue(any("boom" in s for s in seen))

    def test_sign_out_aborts_the_cycle(self):
        with patch.object(personal_repo_sync, "sync", side_effect=personal_repo_sync.SignedOutAbort()):
            with self.assertRaises(cloud_sync._SignedOutAbort):
                cloud_sync._sync_personal_repos(self.uid, 0)


if __name__ == "__main__":
    unittest.main()

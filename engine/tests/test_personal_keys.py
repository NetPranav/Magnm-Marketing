"""Personal NVIDIA keys synced between the laptop and the phone through the cloud (owner only).

No network: the cloud is faked (tests/fake_personal_cloud.py). Every key here is an obviously fake string.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import ai_gateway
import cloud_sync
import db
import key_pool
import paths
import personal_keys
import server
import team_auth
from tests.fake_personal_cloud import FakeCloud, fp

SLOTS = ["NVIDIA_NIM_API_KEY"] + [f"NVIDIA_NIM_API_KEY{i}" for i in range(1, 11)]


def fake_key(n):
    return f"nvapi-FAKEKEYLAPTOP{n:020d}"


class _Handler(server.CommandCenterHandler):
    def __init__(self, path, method="GET", body=None, token=None):
        self.path = path
        self.command = method
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
        if token:
            self.headers["X-Magnm-Token"] = token
        self.status_code = None

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, keyword, value):
        pass

    def end_headers(self):
        pass

    def response_json(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class _Base(unittest.TestCase):
    keychain = False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        os.makedirs(os.path.join(self.tmpdir, "state"))
        prev = (paths.DATA_ROOT, paths.RESOURCE_ROOT, db.get_db_path(), ai_gateway.ENV_PATH, server.ENGINE_TOKEN)
        paths.DATA_ROOT = paths.RESOURCE_ROOT = self.tmpdir
        ai_gateway.ENV_PATH = os.path.join(self.tmpdir, ".env")
        server.ENGINE_TOKEN = ""
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        self.addCleanup(self._restore, prev)
        env = {k: v for k, v in os.environ.items() if not k.startswith("NVIDIA_NIM_API_KEY") and k != "NVIDIA_API_KEY"}
        env.pop("MAGNM_SECRETS_MODE", None)
        if self.keychain:
            env["MAGNM_SECRETS_MODE"] = "keychain"
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        personal_keys.reset_for_tests()
        self.cloud = FakeCloud()
        patcher = patch("team_auth.rest", self.cloud.rest)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sign_in("user-a")

    def _restore(self, prev):
        db.reset_connections()
        paths.DATA_ROOT, paths.RESOURCE_ROOT = prev[0], prev[1]
        db.set_db_path(prev[2])
        ai_gateway.ENV_PATH = prev[3]
        server.ENGINE_TOKEN = prev[4]

    def sign_in(self, uid):
        team_auth._save_session({
            "access_token": f"tok-{uid}", "refresh_token": f"ref-{uid}",
            "expires_at": int(time.time()) + 3600, "user_id": uid, "email": f"{uid}@example.com",
        })
        self.uid = uid

    def sync(self):
        return personal_keys.sync(self.uid)

    def state_text(self):
        return db.get_meta(personal_keys.STATE_META) or ""

    def env_file(self):
        try:
            with open(ai_gateway.ENV_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""


class SyncTests(_Base):
    def test_local_keys_are_uploaded_and_the_state_holds_no_key(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        os.environ["NVIDIA_NIM_API_KEY3"] = fake_key(3)
        out = self.sync()
        self.assertEqual(sorted(out["uploaded"]), ["NVIDIA_NIM_API_KEY", "NVIDIA_NIM_API_KEY3"])
        self.assertEqual(self.cloud.keys_of(), {"NVIDIA_NIM_API_KEY": fake_key(1), "NVIDIA_NIM_API_KEY3": fake_key(3)})
        self.assertNotIn("FAKEKEY", self.state_text())
        self.assertIn(fp(fake_key(1)), self.state_text())

    def test_cloud_keys_are_downloaded_to_the_local_store(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(7))
        out = self.sync()
        self.assertEqual(out["downloaded"], ["NVIDIA_NIM_API_KEY"])
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(7))
        self.assertIn(fake_key(7), self.env_file())
        pool = key_pool.KeyPool()
        pool.sync_from_env()
        self.assertEqual(pool.first_pool_key(), fake_key(7))

    def test_a_second_sync_changes_nothing(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        before = len(self.cloud.requests)
        out = self.sync()
        self.assertEqual((out["uploaded"], out["downloaded"], out["removed_local"], out["removed_cloud"]), ([], [], [], []))
        writes = [r for r in self.cloud.requests[before:] if r["path"] in ("rpc/personal_key_set", "rpc/personal_key_delete")]
        self.assertEqual(writes, [])

    def test_removing_on_the_phone_removes_it_on_the_laptop(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        self.cloud.drop_key("NVIDIA_NIM_API_KEY")
        out = self.sync()
        self.assertEqual(out["removed_local"], ["NVIDIA_NIM_API_KEY"])
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        self.assertNotIn(fake_key(1), self.env_file())
        self.assertEqual(self.cloud.keys_of(), {})
        # And it stays gone: the next sync does not upload or download it again.
        self.sync()
        self.assertEqual(self.cloud.keys_of(), {})

    def test_removing_on_the_laptop_removes_it_in_the_cloud(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        del os.environ["NVIDIA_NIM_API_KEY"]
        personal_keys.forget("NVIDIA_NIM_API_KEY")
        out = self.sync()
        self.assertEqual(out["removed_cloud"], ["NVIDIA_NIM_API_KEY"])
        self.assertEqual(self.cloud.keys_of(), {})
        self.sync()
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)

    def test_forget_is_remembered_until_the_cloud_is_reachable(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        del os.environ["NVIDIA_NIM_API_KEY"]
        personal_keys.forget("NVIDIA_NIM_API_KEY")
        self.cloud.down = True
        with self.assertRaises(personal_keys.PersonalKeysError):
            self.sync()
        self.cloud.down = False
        self.sync()
        self.assertEqual(self.cloud.keys_of(), {})
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)

    def test_a_key_missing_locally_without_a_removal_is_downloaded_not_deleted(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        del os.environ["NVIDIA_NIM_API_KEY"]  # a locked or broken keychain looks like this
        self.sync()
        self.assertEqual(self.cloud.keys_of(), {"NVIDIA_NIM_API_KEY": fake_key(1)})
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(1))

    def test_a_key_changed_on_the_laptop_replaces_the_cloud_copy(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(2)
        out = self.sync()
        self.assertEqual(out["uploaded"], ["NVIDIA_NIM_API_KEY"])
        self.assertEqual(self.cloud.keys_of()["NVIDIA_NIM_API_KEY"], fake_key(2))

    def test_a_key_changed_on_the_phone_replaces_the_laptop_copy(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(2))
        out = self.sync()
        self.assertEqual(out["downloaded"], ["NVIDIA_NIM_API_KEY"])
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(2))
        self.assertEqual(self.cloud.keys_of()["NVIDIA_NIM_API_KEY"], fake_key(2))

    def test_two_different_keys_in_one_slot_keep_both(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(2))
        self.sync()
        local = {k: v for k, v in os.environ.items() if k.startswith("NVIDIA_NIM_API_KEY")}
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(2))
        self.assertEqual(sorted(local.values()), sorted([fake_key(1), fake_key(2)]))
        self.assertEqual(sorted(self.cloud.keys_of().values()), sorted([fake_key(1), fake_key(2)]))
        self.assertEqual(local, self.cloud.keys_of())

    def test_the_same_key_in_another_cloud_slot_is_not_uploaded_twice(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY2", fake_key(5))
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(5)
        self.sync()
        self.assertEqual(list(self.cloud.keys_of().values()), [fake_key(5)])

    def test_malformed_local_keys_are_never_uploaded(self):
        os.environ["NVIDIA_NIM_API_KEY"] = "nvapi-short"
        os.environ["NVIDIA_NIM_API_KEY1"] = "sk-FAKEKEYLAPTOP0000000000000000000000"
        os.environ["NVIDIA_NIM_API_KEY2"] = fake_key(2) + "\n"
        self.sync()
        self.assertEqual(self.cloud.keys_of(), {})

    def test_hostile_cloud_answers_are_not_applied(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(1))
        real_rpc = self.cloud._rpc

        def hostile(owner, name, body):
            out = real_rpc(owner, name, body)
            if name == "personal_key_list":
                out = out + [
                    {"name": "PATH", "last4": "abcd", "fingerprint": fp("x"), "version": 1},
                    {"name": "OPENAI_API_KEY", "last4": "abcd", "fingerprint": fp("y"), "version": 1},
                    {"name": "NVIDIA_NIM_API_KEY4", "last4": "abcd", "fingerprint": "not-a-hash", "version": 1},
                ]
            if name == "personal_key_get":
                out = out + [{"name": "PATH", "api_key": "/evil"}, {"name": "NVIDIA_NIM_API_KEY1", "api_key": "rm -rf"}]
                for row in out:
                    if row["name"] == "NVIDIA_NIM_API_KEY":
                        row["api_key"] = fake_key(9)  # not the key whose fingerprint was listed
            return out

        self.cloud._rpc = hostile
        before_path = os.environ.get("PATH")
        self.sync()
        self.assertEqual(os.environ.get("PATH"), before_path)
        self.assertNotIn("OPENAI_API_KEY", os.environ)
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        self.assertNotIn("NVIDIA_NIM_API_KEY1", os.environ)
        self.assertNotIn("evil", self.env_file())

    def test_another_members_keys_are_never_read(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(8), owner="user-b")
        out = self.sync()
        self.assertEqual(out["downloaded"], [])
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        for req in self.cloud.requests:
            self.assertNotIn("user-b", json.dumps(req))

    def test_a_new_account_never_receives_the_previous_accounts_keys(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        self.sign_in("user-b")
        out = self.sync()
        self.assertEqual(out["uploaded"], [])
        self.assertEqual(self.cloud.keys_of("user-b"), {})
        self.assertEqual(self.cloud.keys_of("user-a"), {"NVIDIA_NIM_API_KEY": fake_key(1)})
        # B's own key in the same slot replaces the old one locally; the old one is not pushed anywhere.
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(2), owner="user-b")
        self.sync()
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(2))
        self.assertEqual(list(self.cloud.keys_of("user-b").values()), [fake_key(2)])

    def test_a_key_typed_new_after_an_account_change_is_uploaded(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        self.sign_in("user-b")
        self.sync()
        os.environ["NVIDIA_NIM_API_KEY1"] = fake_key(4)
        self.sync()
        self.assertEqual(self.cloud.keys_of("user-b"), {"NVIDIA_NIM_API_KEY1": fake_key(4)})

    def test_signing_out_during_sync_stops_it(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        with self.assertRaises(personal_keys.SignedOutAbort):
            personal_keys.sync(self.uid, is_signed_out=lambda: True)
        with self.assertRaises(personal_keys.SignedOutAbort):
            personal_keys.sync("user-other")
        self.assertEqual(self.cloud.keys_of(), {})

    def test_cloud_errors_never_leak_a_key_into_output_or_state(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)

        def echo(owner, name, body):
            if name == "personal_key_set":
                raise team_auth.TeamAuthError(f"bad key {body['p_key']} refused", status=400)
            return FakeCloud._rpc(self.cloud, owner, name, body)

        self.cloud._rpc = echo
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                self.sync()
            except Exception as exc:
                self.assertNotIn(fake_key(1), str(exc))
        self.assertNotIn(fake_key(1), out.getvalue() + err.getvalue())
        self.assertNotIn("FAKEKEY", self.state_text())

    def test_status_shows_labels_and_never_a_key(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.cloud.put_key("NVIDIA_NIM_API_KEY2", fake_key(2))
        self.sync()
        rows = personal_keys.status()
        self.assertEqual([r["name"] for r in rows], ["NVIDIA_NIM_API_KEY", "NVIDIA_NIM_API_KEY2"])
        self.assertTrue(all(r["synced"] for r in rows))
        self.assertEqual(rows[0]["label"], f"...{fake_key(1)[-4:]}")
        self.assertNotIn("FAKEKEY", json.dumps(rows))

    def test_shared_team_keys_stay_separate(self):
        # The team pool is a different mechanism: nothing here reads or writes team keys.
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(1))
        self.sync()
        self.assertFalse([r for r in self.cloud.requests if "ai_get_keys" in r["path"] or "admin_" in r["path"]])


class KeychainModeTests(_Base):
    keychain = True

    def test_downloads_wait_for_the_desktop_shell_and_are_applied_on_ack(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(1))
        out = self.sync()
        self.assertEqual(out["downloaded"], ["NVIDIA_NIM_API_KEY"])
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        self.assertEqual(self.env_file(), "")
        ops = personal_keys.pending_ops()
        self.assertEqual(ops, [{"op": "set", "name": "NVIDIA_NIM_API_KEY", "value": fake_key(1)}])
        personal_keys.acknowledge(["NVIDIA_NIM_API_KEY"])
        self.assertEqual(os.environ["NVIDIA_NIM_API_KEY"], fake_key(1))
        self.assertEqual(personal_keys.pending_ops(), [])
        self.assertIn(fp(fake_key(1)), self.state_text())
        self.assertNotIn("FAKEKEY", self.state_text())
        self.assertEqual(self.env_file(), "")

    def test_pending_ops_are_rederived_after_a_restart(self):
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(1))
        self.sync()
        personal_keys.reset_for_tests()  # the engine restarted before the shell applied anything
        self.sync()
        self.assertEqual(len(personal_keys.pending_ops()), 1)

    def test_a_pending_removal_is_not_undone_by_the_next_sync(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        self.cloud.drop_key("NVIDIA_NIM_API_KEY")
        self.sync()
        self.assertEqual(personal_keys.pending_ops(), [{"op": "delete", "name": "NVIDIA_NIM_API_KEY"}])
        self.sync()  # still not applied: it must not be uploaded again
        self.assertEqual(self.cloud.keys_of(), {})
        personal_keys.acknowledge(["NVIDIA_NIM_API_KEY"])
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        self.sync()
        self.assertEqual(self.cloud.keys_of(), {})

    def test_acknowledging_a_name_that_is_not_pending_is_ignored(self):
        os.environ["PATH_BEFORE"] = "x"
        personal_keys.acknowledge(["PATH", "NVIDIA_NIM_API_KEY", "../x", 5, None])
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)
        self.assertEqual(os.environ["PATH_BEFORE"], "x")

    def test_a_conflict_in_keychain_mode_keeps_the_local_key_in_a_free_slot(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.cloud.put_key("NVIDIA_NIM_API_KEY", fake_key(2))
        self.sync()
        ops = {o["name"]: o["value"] for o in personal_keys.pending_ops()}
        self.assertEqual(ops["NVIDIA_NIM_API_KEY"], fake_key(2))
        self.assertIn(fake_key(1), ops.values())
        self.assertEqual(sorted(self.cloud.keys_of().values()), sorted([fake_key(1), fake_key(2)]))


class RouteTests(_Base):
    def call(self, method, path, body=None, token=None):
        handler = _Handler(path, method, body, token)
        getattr(handler, f"do_{method}")()
        return handler.status_code, handler.response_json()

    def test_routes_are_closed_without_the_engine_token(self):
        server.ENGINE_TOKEN = "tok"
        for method, path in (("GET", "/api/account/keys"), ("POST", "/api/account/keys/pending"),
                             ("POST", "/api/account/keys/applied"), ("POST", "/api/account/keys/forget")):
            status, _ = self.call(method, path, {} if method == "POST" else None)
            self.assertEqual(status, 401, path)

    def test_status_route_lists_labels_only(self):
        os.environ["NVIDIA_NIM_API_KEY"] = fake_key(1)
        self.sync()
        status, body = self.call("GET", "/api/account/keys")
        self.assertEqual(status, 200)
        self.assertNotIn("FAKEKEY", json.dumps(body))
        self.assertEqual(body["keys"][0]["name"], "NVIDIA_NIM_API_KEY")

    def test_forget_route_checks_the_slot_name(self):
        for bad in ("PATH", "OPENAI_API_KEY", "../x", "", None, 5, "NVIDIA_NIM_API_KEY11"):
            status, _ = self.call("POST", "/api/account/keys/forget", {"name": bad})
            self.assertEqual(status, 400, bad)
        status, body = self.call("POST", "/api/account/keys/forget", {"name": "NVIDIA_NIM_API_KEY"})
        self.assertEqual(status, 200)

    def test_pending_and_applied_routes(self):
        status, body = self.call("POST", "/api/account/keys/pending", {})
        self.assertEqual((status, body), (200, {"ops": []}))
        status, _ = self.call("POST", "/api/account/keys/applied", {"names": "x"})
        self.assertEqual(status, 400)
        status, _ = self.call("POST", "/api/account/keys/applied", {"names": ["NVIDIA_NIM_API_KEY"]})
        self.assertEqual(status, 200)

    def test_the_old_env_routes_still_refuse_secrets_in_keychain_mode(self):
        with patch.dict(os.environ, {"MAGNM_SECRETS_MODE": "keychain"}):
            status, body = self.call("POST", "/api/save_env", {"key_name": "NVIDIA_NIM_API_KEY", "key_val": fake_key(1)})
        self.assertNotIn("FAKEKEY", json.dumps(body))
        self.assertNotIn("NVIDIA_NIM_API_KEY", os.environ)


class CycleTests(_Base):
    def test_a_failing_key_sync_does_not_stop_the_other_sync_work(self):
        calls = []
        with patch.object(personal_keys, "sync", side_effect=RuntimeError("boom")), \
             patch.object(cloud_sync, "_log_warning", side_effect=calls.append):
            cloud_sync._sync_personal_keys(self.uid, 0)
        self.assertTrue(any("boom" in c for c in calls))

    def test_sign_out_in_the_key_sync_aborts_the_cycle(self):
        with patch.object(personal_keys, "sync", side_effect=personal_keys.SignedOutAbort()):
            with self.assertRaises(cloud_sync._SignedOutAbort):
                cloud_sync._sync_personal_keys(self.uid, 0)


if __name__ == "__main__":
    unittest.main()

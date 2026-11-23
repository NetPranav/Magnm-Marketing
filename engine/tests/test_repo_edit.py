"""Editing a personal repository on the laptop: field checks, folder path checks and moving an id's references.

No network. Repos are invented names; the confidential product is the made-up ProjectNightfall.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import coding_agents
import db
import paths
import repos
import retired_repos
import server
import team_repos

RULES = {
    "version": 1,
    "show_levels": {
        "everything": "All features and visuals",
        "text_only": "Text only",
        "teasers_only": "Teasers only",
        "nothing": "Do not mention",
    },
    "products": [
        {"id": "cero", "name": "Cero", "status": "released", "show": "everything", "blocked_terms": [],
         "confidential": False},
        {"id": "secretproj", "name": "ProjectNightfall", "status": "secret", "show": "nothing",
         "blocked_terms": [], "confidential": True},
    ],
}


def git_init(path):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


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


class RepoBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.data = os.path.join(self.root, "data")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.data, "config"))
        os.makedirs(os.path.join(self.data, "state"))
        os.makedirs(self.home)
        prev = (paths.DATA_ROOT, paths.RESOURCE_ROOT, db.get_db_path(), server.ENGINE_TOKEN)
        paths.DATA_ROOT = paths.RESOURCE_ROOT = self.data
        server.ENGINE_TOKEN = ""
        db.set_db_path(os.path.join(self.data, "state", "magnm.db"))
        db.init_db()
        self.addCleanup(self._restore, prev)
        patcher = patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        with open(os.path.join(self.data, "config", "product_rules.json"), "w", encoding="utf-8") as f:
            json.dump(RULES, f)
        patcher = patch.object(team_repos, "cached", return_value=[])
        self.cached = patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self, prev):
        db.reset_connections()
        paths.DATA_ROOT, paths.RESOURCE_ROOT = prev[0], prev[1]
        db.set_db_path(prev[2])
        server.ENGINE_TOKEN = prev[3]

    def write(self, entries):
        with open(os.path.join(self.data, "config", "repos.json"), "w", encoding="utf-8") as f:
            json.dump({"repositories": entries}, f)

    def stored(self):
        try:
            with open(os.path.join(self.data, "config", "repos.json"), "r", encoding="utf-8") as f:
                return {r["id"]: r for r in json.load(f)["repositories"]}
        except FileNotFoundError:
            return {}

    def gh(self, rid="demo-app", **extra):
        return {"id": rid, "name": "Demo App", "product_id": rid, "kind": "github", "github": f"acme/{rid}",
                "confidential": False, "status": "in_development", **extra}


class FieldTests(RepoBase):
    def test_edits_name_branch_status_angle_and_confidential(self):
        self.write([self.gh()])
        out = repos.update_repo("demo-app", {
            "name": "Demo App Pro", "branch": "release", "status": "released",
            "marketing_angle": "Calm and fast.\nTwo lines.", "confidential": True,
        })
        self.assertEqual(out["name"], "Demo App Pro")
        row = self.stored()["demo-app"]
        self.assertEqual((row["branch"], row["status"], row["confidential"]), ("release", "released", True))
        self.assertEqual(row["marketing_angle"], "Calm and fast.\nTwo lines.")

    def test_empty_branch_and_angle_clear_the_field(self):
        self.write([self.gh(branch="main", marketing_angle="x")])
        repos.update_repo("demo-app", {"branch": "", "marketing_angle": ""})
        row = self.stored()["demo-app"]
        self.assertNotIn("branch", row)
        self.assertNotIn("marketing_angle", row)

    def test_unknown_missing_and_wrong_type_fields_are_refused(self):
        self.write([self.gh()])
        for bad in ({"github": "evil/x"}, {"kind": "local"}, {"product_id": "other"}, {"scope": "global"},
                    {"local_path": "/tmp"}, {"__proto__": 1}, {}):
            with self.assertRaises(ValueError, msg=str(bad)):
                repos.update_repo("demo-app", bad)
        for bad in ({"name": 5}, {"name": ["x"]}, {"confidential": "yes"}, {"confidential": 1}, {"branch": 3},
                    {"status": None}, {"marketing_angle": {"a": 1}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                repos.update_repo("demo-app", bad)
        with self.assertRaises(ValueError):
            repos.update_repo("nope", {"name": "X"})
        self.assertEqual(self.stored()["demo-app"]["name"], "Demo App")

    def test_hostile_values_are_refused(self):
        self.write([self.gh()])
        bad = [
            {"name": ""}, {"name": "x" * 81}, {"name": "Line\nbreak"}, {"name": "Bell\x07"},
            {"branch": "--upload-pack=x"}, {"branch": "../main"}, {"branch": "a..b"}, {"branch": "feat/"},
            {"branch": "x.lock"}, {"branch": "with space"}, {"branch": "b" * 101}, {"branch": "-x"},
            {"status": "live"}, {"status": "released; rm -rf"}, {"marketing_angle": "x" * 501},
            {"marketing_angle": "bad\x07bell"}, {"marketing_angle": "nul\x00byte"},
            {"id": "Upper"}, {"id": "-lead"}, {"id": "has space"}, {"id": "a/b"}, {"id": "../x"}, {"id": ""},
            {"id": "a" * 64}, {"id": "x;y"}, {"id": "x\n"},
        ]
        for fields in bad:
            with self.assertRaises(ValueError, msg=repr(fields)):
                repos.update_repo("demo-app", fields)
        self.assertEqual(self.stored()["demo-app"], self.gh())

    def test_id_must_be_free(self):
        self.write([self.gh("one"), self.gh("two")])
        with self.assertRaises(ValueError):
            repos.update_repo("one", {"id": "two"})
        with self.assertRaises(ValueError):
            repos.update_repo("one", {"id": "TWO".lower()})
        self.cached.return_value = [{"id": "team-app", "github": "acme/team-app", "name": "Team", "product_id": "team-app",
                                     "kind": "github", "confidential": False, "status": "in_development", "scope": "global"}]
        with self.assertRaises(ValueError):
            repos.update_repo("one", {"id": "team-app"})
        self.assertEqual(sorted(self.stored()), ["one", "two"])

    def test_a_team_repo_cannot_be_edited(self):
        self.write([])
        self.cached.return_value = [{"id": "team-app", "github": "acme/team-app", "name": "Team", "product_id": "team-app",
                                     "kind": "github", "confidential": False, "status": "in_development", "scope": "global"}]
        with self.assertRaises(ValueError):
            repos.update_repo("team-app", {"name": "Mine"})
        self.assertEqual(self.stored(), {})

    def test_a_confidential_product_cannot_be_unflagged(self):
        self.write([self.gh("secretproj", name="Nightfall Repo", confidential=True)])
        with self.assertRaises(ValueError):
            repos.update_repo("secretproj", {"confidential": False})
        self.assertTrue(self.stored()["secretproj"]["confidential"])
        self.write([self.gh("plain", confidential=True)])
        repos.update_repo("plain", {"confidential": False})
        self.assertFalse(self.stored()["plain"]["confidential"])

    def test_naming_a_confidential_product_forces_the_flag(self):
        self.write([self.gh()])
        out = repos.update_repo("demo-app", {"name": "ProjectNightfall mobile"})
        self.assertTrue(out["confidential"])
        self.assertTrue(self.stored()["demo-app"]["confidential"])
        with self.assertRaises(ValueError):
            repos.update_repo("demo-app", {"confidential": False})


class IdMoveTests(RepoBase):
    def seed(self):
        self.write([self.gh("old-app"), self.gh("other-app")])
        db.save_repo_snapshot("old-app", "2026-01-01T00:00:00Z", {"version": "1.0", "synced": True})
        db.save_repo_snapshot("other-app", "2026-01-01T00:00:00Z", {"version": "9.9", "synced": True})
        db.create_product_fact(product_id="old-app", text="Fact one", source_kind="manual", status="approved")
        db.create_product_fact(product_id="other-app", text="Fact two", source_kind="manual", status="approved")
        self.item = db.create_content_item("c-old", "post", "x", "Title", "Body", product_id="old-app")
        self.other_item = db.create_content_item("c-other", "post", "x", "Other", "Body", product_id="other-app")

    def test_snapshots_facts_and_the_product_follow_the_new_id(self):
        self.seed()
        out = repos.update_repo("old-app", {"id": "new-app"})
        self.assertEqual((out["id"], out["product_id"]), ("new-app", "new-app"))
        self.assertIsNone(db.get_latest_repo_snapshot("old-app"))
        self.assertEqual(db.get_latest_repo_snapshot("new-app")["data"]["version"], "1.0")
        self.assertEqual(db.get_latest_repo_snapshot("other-app")["data"]["version"], "9.9")
        self.assertEqual([f["text"] for f in db.list_product_facts("new-app")], ["Fact one"])
        self.assertEqual(db.list_product_facts("old-app"), [])
        self.assertEqual([f["text"] for f in db.list_product_facts("other-app")], ["Fact two"])
        self.assertNotIn("old-app", self.stored())
        conn = db.connect()
        prods = {r["id"]: r["product_id"] for r in conn.execute("SELECT id, product_id FROM content_items")}
        self.assertEqual(prods[self.item["id"]], "new-app")
        self.assertEqual(prods[self.other_item["id"]], "other-app")

    def test_a_product_id_that_is_not_the_old_id_stays(self):
        self.write([self.gh("old-app", product_id="shared-product")])
        db.create_product_fact(product_id="shared-product", text="Keep", source_kind="manual", status="approved")
        out = repos.update_repo("old-app", {"id": "new-app"})
        self.assertEqual(out["product_id"], "shared-product")
        self.assertEqual(len(db.list_product_facts("shared-product")), 1)

    def test_a_product_used_by_another_repo_stays(self):
        self.write([self.gh("old-app"), self.gh("sibling", product_id="old-app")])
        db.create_product_fact(product_id="old-app", text="Shared", source_kind="manual", status="approved")
        out = repos.update_repo("old-app", {"id": "new-app"})
        self.assertEqual(out["product_id"], "old-app")
        self.assertEqual(len(db.list_product_facts("old-app")), 1)

    def test_coding_runs_and_workflow_runs_follow_and_approvals_stay_valid(self):
        self.seed()
        conn = db.connect()
        h_old = coding_agents.compute_approval_hash("claude", "read_only", ["old-app", "other-app"], "Look")
        h_other = coding_agents.compute_approval_hash("claude", "read_only", ["old-app"], "Different prompt hash")
        with conn:
            for rid, repo_ids, h in (("run-1", ["old-app", "other-app"], h_old), ("run-2", ["old-app"], "stale-hash")):
                conn.execute(
                    "INSERT INTO coding_runs (id, engine, mode, prompt, repo_ids_json, status, workspace, created_at, approval_hash) "
                    "VALUES (?, 'claude', 'read_only', ?, ?, 'awaiting_approval', 'w', '2026-01-01', ?)",
                    (rid, "Look" if rid == "run-1" else "x", json.dumps(repo_ids), h),
                )
            conn.execute(
                "INSERT INTO workflow_runs (id, kind, status, current_step, inputs_json, state_json, created_at, updated_at) "
                "VALUES ('wf-1', 'post', 'running', 0, ?, '{}', '2026-01-01', '2026-01-01')",
                (json.dumps({"repo_id": "old-app", "topic": "t"}),),
            )
        repos.update_repo("old-app", {"id": "new-app"})
        r1 = db.get_coding_run("run-1")
        self.assertEqual(r1["repo_ids"], ["new-app", "other-app"])
        self.assertEqual(r1["approval_hash"], coding_agents.compute_approval_hash("claude", "read_only", ["new-app", "other-app"], "Look"))
        r2 = db.get_coding_run("run-2")
        self.assertEqual(r2["repo_ids"], ["new-app"])
        self.assertEqual(r2["approval_hash"], "stale-hash")  # an approval that did not match is never made valid
        wf = conn.execute("SELECT inputs_json FROM workflow_runs WHERE id='wf-1'").fetchone()
        self.assertEqual(json.loads(wf["inputs_json"]), {"repo_id": "new-app", "topic": "t"})
        self.assertNotEqual(h_other, "")

    def test_a_confidential_repo_keeps_its_old_id_guarded(self):
        self.write([self.gh("old-conf", confidential=True)])
        repos.update_repo("old-conf", {"id": "new-conf"})
        self.assertIn("old-conf", retired_repos.terms())

    def test_a_failed_config_write_leaves_the_database_alone(self):
        self.seed()
        with patch.object(repos, "_write_repos_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                repos.update_repo("old-app", {"id": "new-app"})
        self.assertIsNotNone(db.get_latest_repo_snapshot("old-app"))
        self.assertIsNone(db.get_latest_repo_snapshot("new-app"))
        self.assertEqual(len(db.list_product_facts("old-app")), 1)

    def test_a_failed_database_move_restores_the_config(self):
        self.seed()
        with patch.object(repos, "_move_references", side_effect=RuntimeError("db locked")):
            with self.assertRaises(RuntimeError):
                repos.update_repo("old-app", {"id": "new-app"})
        self.assertIn("old-app", self.stored())
        self.assertNotIn("new-app", self.stored())


class PathTests(RepoBase):
    def setUp(self):
        super().setUp()
        self.repo_dir = git_init(os.path.join(self.home, "code", "demo-app"))
        self.write([{"id": "demo-app", "name": "Demo App", "product_id": "demo-app", "kind": "local",
                     "local_path": self.repo_dir, "confidential": False, "status": "in_development"}])

    def edit(self, path):
        return repos.update_repo("demo-app", {"local_path": path})

    def test_a_real_git_folder_is_accepted_and_stored_resolved(self):
        other = git_init(os.path.join(self.home, "code", "second"))
        link = os.path.join(self.home, "code", "link-to-second")
        os.symlink(other, link)
        out = self.edit(link)
        self.assertEqual(out["local_path"], os.path.realpath(other))
        self.assertEqual(self.stored()["demo-app"]["local_path"], os.path.realpath(other))

    def test_home_expansion_works_inside_home(self):
        git_init(os.path.join(self.home, "code", "third"))
        out = self.edit("~/code/third")
        self.assertEqual(out["local_path"], os.path.join(self.home, "code", "third"))

    def test_path_tricks_are_refused(self):
        file_path = os.path.join(self.home, "afile")
        open(file_path, "w").close()
        plain = os.path.join(self.home, "plain-folder")
        os.makedirs(plain)
        secret = git_init(os.path.join(self.home, ".ssh", "keys-repo"))
        cfg = git_init(os.path.join(self.home, ".config", "magnm", "x"))
        inside_state = git_init(os.path.join(self.data, "state", "sneaky"))
        link_home = os.path.join(self.root, "link-home")
        os.symlink(self.home, link_home)
        cases = {
            "empty": "", "blank": "   ", "none": None, "number": 5, "list": ["/tmp"],
            "relative": "code/demo-app", "dotdot": os.path.join(self.home, "code", "..", ".ssh"),
            "dotdot only": "..", "tilde dotdot": "~/../..", "root": "/", "home itself": self.home,
            "home via ~": "~", "home via symlink": link_home, "parent of home": os.path.dirname(self.home),
            "missing": os.path.join(self.home, "nope"), "a file": file_path, "not git": plain,
            "ssh dir": secret, "config dir": cfg, "engine state": inside_state,
            "newline": self.repo_dir + "\nx", "nul": self.repo_dir + "\x00x", "tab": self.repo_dir + "\tx",
            "url": "file://" + self.repo_dir, "https": "https://github.com/acme/demo-app",
            "unc": "\\\\server\\share\\repo", "too long": "/" + "a" * 600,
            "backslash dotdot": self.home + "\\..\\..",
        }
        for label, value in cases.items():
            with self.assertRaises(ValueError, msg=label):
                self.edit(value)
        self.assertEqual(self.stored()["demo-app"]["local_path"], self.repo_dir)

    def test_refusal_messages_do_not_echo_hostile_text(self):
        for value in ("/etc/passwd\nINJECT", "x\x00y"):
            with self.assertRaises(ValueError) as ctx:
                self.edit(value)
            self.assertNotIn("INJECT", str(ctx.exception))

    def test_the_same_folder_cannot_belong_to_two_repos(self):
        other = git_init(os.path.join(self.home, "code", "fourth"))
        entries = list(self.stored().values()) + [{"id": "fourth", "name": "Fourth", "product_id": "fourth", "kind": "local",
                                                   "local_path": other, "confidential": False, "status": "in_development"}]
        self.write(entries)
        with self.assertRaises(ValueError):
            self.edit(other)

    def test_a_github_repo_has_no_folder_to_edit(self):
        self.write([self.gh()])
        with self.assertRaises(ValueError):
            repos.update_repo("demo-app", {"local_path": self.repo_dir})

    def test_the_path_checker_is_one_function_for_every_caller(self):
        self.assertEqual(repos.validate_local_path(self.repo_dir, exclude_id="demo-app"), os.path.realpath(self.repo_dir))
        with self.assertRaises(ValueError):
            repos.validate_local_path("/etc")


class RouteTests(RepoBase):
    def call(self, path, body, token=None):
        handler = _Handler(path, "POST", body, token)
        handler.do_POST()
        return handler.status_code, handler.response_json()

    def test_update_route(self):
        self.write([self.gh()])
        status, body = self.call("/api/repos/demo-app/update", {"name": "Renamed", "status": "released"})
        self.assertEqual(status, 200)
        self.assertEqual(body["repo"]["name"], "Renamed")
        self.assertEqual(self.stored()["demo-app"]["name"], "Renamed")

    def test_update_route_errors(self):
        self.write([self.gh()])
        self.assertEqual(self.call("/api/repos/demo-app/update", {"branch": "--x"})[0], 400)
        self.assertEqual(self.call("/api/repos/demo-app/update", {})[0], 400)
        self.assertEqual(self.call("/api/repos/missing/update", {"name": "X"})[0], 404)
        self.cached.return_value = [{"id": "team-app", "github": "acme/team-app", "name": "Team", "product_id": "team-app",
                                     "kind": "github", "confidential": False, "status": "in_development", "scope": "global"}]
        status, body = self.call("/api/repos/team-app/update", {"name": "Mine"})
        self.assertEqual(status, 403)
        server.ENGINE_TOKEN = "tok"
        self.assertEqual(self.call("/api/repos/demo-app/update", {"name": "X"})[0], 401)
        self.assertEqual(self.call("/api/repos/demo-app/update", {"name": "X"}, token="tok")[0], 200)

    def test_update_route_is_not_reachable_with_a_hostile_id(self):
        self.write([self.gh()])
        status, _ = self.call("/api/repos/..%2F..%2Fx/update", {"name": "X"})
        self.assertIn(status, (400, 404))


if __name__ == "__main__":
    unittest.main()

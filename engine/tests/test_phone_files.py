"""Get on phone: full-quality copies of finished draft files, kept 14 days, never confidential.

No network: the cloud (REST) is faked and the storage backend is replaced by an in-memory one. Files are tiny fakes
with the right first bytes, in a temporary data folder.
"""

import http.client
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import cloud_sync
import db
import paths
import phone_files
import server
import team_auth

UID = "11111111-2222-4333-8444-555555555501"
OTHER = "11111111-2222-4333-8444-555555555502"

RULES = {
    "version": 1,
    "show_levels": {"everything": "All", "text_only": "Text", "teasers_only": "Teasers", "nothing": "None"},
    "products": [
        {"id": "cero", "name": "Cero", "status": "released", "show": "everything", "blocked_terms": [], "confidential": False},
        {"id": "secretproj", "name": "ProjectNightfall", "status": "secret", "show": "nothing",
         "blocked_terms": [], "confidential": True},
    ],
}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
PDF = b"%PDF-1.4\n1 0 obj\n<< /Title (Weekly plan) >>\nendobj\n%%EOF\n"


class _Resp:
    def __init__(self, body=None, status=200):
        self.status = status
        self._raw = b"" if body is None else json.dumps(body).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, status, message="failed"):
    return urllib.error.HTTPError(url, status, "err", None, io.BytesIO(json.dumps({"message": message}).encode("utf-8")))


class _FakeBackend:
    """An in-memory private bucket that records every call and enforces the storage policies: an object can be stored
    only while an unexpired copy row points at it, and seen (so deleted) only while its row is unexpired or marked
    for removal. An object that cannot be seen is silently left behind, as in the real storage API."""

    def __init__(self, cloud=None):
        self.cloud = cloud
        self.log = []
        self.objects = {}
        self.uploads = []
        self.deletes = []
        self.fail_upload = None
        self.fail_delete = None
        self.attempts = 0
        self.invisible = []

    def _row_for(self, storage_path):
        for r in (self.cloud.rows if self.cloud else []):
            if r.get("storage_path") == storage_path and r.get("owner_id") == self.cloud.uid:
                return r
        return None

    @staticmethod
    def _live(row):
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)

    def upload(self, storage_path, data_bytes, content_type):
        self.attempts += 1
        if self.fail_upload is not None:
            raise self.fail_upload
        if self.cloud is not None:
            row = self._row_for(storage_path)
            if row is None or not self._live(row):
                raise team_auth.TeamAuthError("new row violates row-level security policy", status=403)
        self.log.append(("upload", storage_path))
        self.cloud.order.append("object") if self.cloud is not None else None
        self.uploads.append((storage_path, len(data_bytes), content_type))
        self.objects[storage_path] = bytes(data_bytes)

    def delete(self, storage_paths):
        if self.fail_delete is not None:
            raise self.fail_delete
        for p in storage_paths:
            self.deletes.append(p)
            if self.cloud is not None:
                row = self._row_for(p)
                if row is None or not (self._live(row) or row.get("removing")):
                    self.invisible.append(p)
                    continue
            self.log.append(("delete", p))
            self.objects.pop(p, None)

    def sign(self, storage_path, expires_in=3600):
        raise AssertionError("the laptop never signs these copies")


class _FakeCloud:
    """phone_files REST table, storage_usage and profiles, as the server defines them."""

    def __init__(self, uid=UID):
        self.uid = uid
        self.rows = []
        self.requests = []
        self.usage_bytes = 0
        self.table_missing = False
        self.ignore_owner_filter = False
        self.insert_error = None
        self.status_all = None
        self.order = []

    def calls(self, method, part):
        return [r for r in self.requests if r["method"] == method and part in r["url"]]

    def urlopen(self, req, timeout=0):
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data.decode("utf-8")) if req.data and req.headers.get("Content-type", "").startswith("application/json") else None
        self.requests.append({"url": url, "method": method, "body": body})
        if self.status_all:
            raise _http_error(url, self.status_all)
        if "/rest/v1/profiles" in url:
            return _Resp([{"id": self.uid, "email": "m@example.com", "display_name": "P", "avatar_url": None,
                           "role": "member", "active": True}])
        if url.endswith("rpc/storage_usage"):
            raise AssertionError("the budget must come from the project-wide total, not the caller's own meter")
        if url.endswith("rpc/team_storage_used"):
            return _Resp(self.usage_bytes + sum(r.get("bytes", 0) if isinstance(r.get("bytes", 0), int) else 0 for r in self.rows))
        if "/rest/v1/phone_files" in url:
            if self.table_missing:
                raise _http_error(url, 404, 'relation "public.phone_files" does not exist')
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            if method == "GET":
                rows = self.rows
                owner = (qs.get("owner_id") or [""])[0]
                if owner.startswith("eq.") and not self.ignore_owner_filter:
                    rows = [r for r in rows if r.get("owner_id") == owner[3:]]
                return _Resp(rows)
            if method == "POST":
                self.order.append("row")
                if self.insert_error:
                    raise _http_error(url, *self.insert_error)
                for item in body if isinstance(body, list) else [body]:
                    row = dict(item, id=str(uuid.uuid4()), owner_id=self.uid)
                    # The server alone sets the dates; a client that sends them is refused (no column privilege).
                    if "expires_at" in item or "created_at" in item:
                        raise _http_error(url, 403, "permission denied for table phone_files")
                    row["created_at"] = datetime.now(timezone.utc).isoformat()
                    row["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
                    self.rows.append(row)
                return _Resp(None, status=201)
            if method == "PATCH":
                target = (qs.get("id") or [""])[0][3:]
                owner = (qs.get("owner_id") or [""])[0][3:]
                if "expires_at" in body or "created_at" in body:
                    raise _http_error(url, 403, "permission denied for table phone_files")
                if body.get("removing"):
                    target_row = next((r for r in self.rows if r["id"] == target), None)
                    if target_row is not None and datetime.fromisoformat(target_row["expires_at"]) > datetime.now(timezone.utc):
                        raise _http_error(url, 400, "Only an expired copy can be marked for removal")
                for row in self.rows:
                    if row["id"] == target and (self.ignore_owner_filter or row.get("owner_id") == owner):
                        if body.get("storage_path", row.get("storage_path")) != row.get("storage_path"):
                            row["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
                            row["removing"] = False
                        row.update(body)
                return _Resp(None, status=204)
            if method == "DELETE":
                ids = (qs.get("id") or [""])[0]
                owner = (qs.get("owner_id") or [""])[0][3:]
                path = (qs.get("storage_path") or [""])[0][3:]
                wanted = set(ids[4:-1].split(",")) if ids.startswith("in.(") else {ids[3:]}
                self.rows = [
                    r for r in self.rows
                    if not ((r["id"] in wanted or (path and r.get("storage_path") == path))
                            and (self.ignore_owner_filter or r.get("owner_id") == owner))
                ]
                return _Resp(None, status=204)
        return _Resp(None, status=201)


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        for d in ("config", "state", "outputs"):
            os.makedirs(os.path.join(self.tmpdir, d))
        prev = (paths.DATA_ROOT, paths.RESOURCE_ROOT, db.get_db_path(), server.ENGINE_TOKEN,
                os.environ.get("MAGNM_DISABLE_CLOUD_SYNC"))
        paths.DATA_ROOT = paths.RESOURCE_ROOT = self.tmpdir
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        server.ENGINE_TOKEN = ""
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        self.addCleanup(self._restore, prev)
        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test"}, f)
        with open(os.path.join(self.tmpdir, "config", "product_rules.json"), "w", encoding="utf-8") as f:
            json.dump(RULES, f)
        self.cloud = _FakeCloud()
        patcher = patch("team_auth.urllib.request.urlopen", side_effect=self.cloud.urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = _FakeBackend(self.cloud)
        self.sign_in(UID)
        phone_files.notify_signed_out()
        self.addCleanup(phone_files.notify_signed_out)
        with cloud_sync._lock:
            cloud_sync._failure_count = 0
            cloud_sync._next_allowed_at = 0.0
            cloud_sync._last_error = None

    def _restore(self, prev):
        db.reset_connections()
        paths.DATA_ROOT, paths.RESOURCE_ROOT = prev[0], prev[1]
        db.set_db_path(prev[2])
        server.ENGINE_TOKEN = prev[3]
        if prev[4] is None:
            os.environ.pop("MAGNM_DISABLE_CLOUD_SYNC", None)
        else:
            os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = prev[4]

    def sign_in(self, uid):
        team_auth._save_session({
            "access_token": f"tok-{uid}", "refresh_token": f"ref-{uid}",
            "expires_at": int(time.time()) + 3600, "user_id": uid, "email": f"{uid}@example.com",
        })

    def write(self, rel, data=PNG):
        full = os.path.join(self.tmpdir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    def draft(self, cid="cnt_1", files=("outputs/card.png",), status="ready", title="Cero launch card", **kw):
        db.create_content_item(cid, "post", "linkedin", title, "Cero is out today.", status=status,
                               files=[{"path": p, "kind": "image"} if isinstance(p, str) else p for p in files], **kw)
        return cid

    def sync(self, **kw):
        kw.setdefault("backend", self.backend)
        return phone_files.sync(UID, **kw)

    def object_names(self):
        return sorted(self.backend.objects)


class UploadTests(_Base):
    def test_a_finished_draft_gets_a_full_quality_copy_of_each_allowed_file(self):
        files = {"outputs/a.png": PNG, "outputs/b.jpg": JPG, "outputs/c.webp": WEBP, "outputs/e.mp4": MP4,
                 "outputs/f.webm": WEBM, "outputs/g.m4v": MP4, "outputs/h.pdf": PDF, "outputs/i.jpeg": JPG}
        for rel, data in files.items():
            self.write(rel, data)
        self.draft(files=list(files))
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 3)  # three per cycle
        self.sync()
        self.sync()
        self.assertEqual(len(self.cloud.rows), len(files))
        types = {os.path.basename(r["file_path"]): r["content_type"] for r in self.cloud.rows}
        self.assertEqual(types, {
            "a.png": "image/png", "b.jpg": "image/jpeg", "c.webp": "image/webp", "e.mp4": "video/mp4",
            "f.webm": "video/webm", "g.m4v": "video/mp4", "h.pdf": "application/pdf", "i.jpeg": "image/jpeg",
        })
        for row in self.cloud.rows:
            self.assertRegex(row["storage_path"], rf"^{UID}/[0-9a-f]{{64}}\.(png|jpg|webp|mp4|webm|pdf)$")
            self.assertRegex(row["source_hash"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["content_id"], "cnt_1")
            self.assertEqual(self.backend.objects[row["storage_path"]], files[row["file_path"]])
            self.assertEqual(row["bytes"], len(files[row["file_path"]]))
            self.assertEqual(row["file_name"], os.path.basename(row["file_path"]))
        for rel in files:
            self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, rel)), "the local file stays")

    def test_the_copy_expires_in_14_days_and_the_laptop_never_sends_dates(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        sent = self.cloud.calls("POST", "phone_files")[0]["body"]
        self.assertNotIn("expires_at", sent)
        self.assertNotIn("created_at", sent)
        stamp = datetime.fromisoformat(self.cloud.rows[0]["expires_at"].replace("Z", "+00:00"))
        left = stamp - datetime.now(timezone.utc)
        self.assertGreater(left, timedelta(days=13, hours=23))
        self.assertLessEqual(left, timedelta(days=14, minutes=1))

    def test_only_finished_drafts_get_copies(self):
        self.write("outputs/a.png")
        self.write("outputs/b.png")
        self.write("outputs/c.png")
        self.draft("cnt_draft", ["outputs/a.png"], status="draft")
        self.draft("cnt_ready", ["outputs/b.png"], status="ready")
        self.draft("cnt_posted", ["outputs/c.png"], status="posted")
        self.sync()
        self.assertEqual(sorted(r["content_id"] for r in self.cloud.rows), ["cnt_posted", "cnt_ready"])

    def test_an_unchanged_file_is_never_uploaded_twice(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.assertEqual(len(self.backend.uploads), 1)
        self.sync()
        self.sync()
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(len(self.cloud.calls("POST", "phone_files")), 1)

    def test_a_changed_file_replaces_the_copy_and_removes_the_old_object(self):
        path = self.write("outputs/card.png", PNG)
        self.draft()
        self.sync()
        old = self.cloud.rows[0]["storage_path"]
        with open(path, "wb") as f:
            f.write(PNG + b"more pixels")
        os.utime(path, (time.time() + 5, time.time() + 5))
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(len(self.cloud.rows), 1)
        new = self.cloud.rows[0]["storage_path"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.object_names(), [new])
        self.assertEqual(self.backend.objects[new], PNG + b"more pixels")
        self.assertEqual(self.cloud.calls("PATCH", "phone_files"), [], "a replacement is a remove then an add")
        self.assertEqual(len(self.cloud.calls("POST", "phone_files")), 2)

    def test_at_most_three_files_per_cycle(self):
        for i in range(6):
            self.write(f"outputs/{i}.png")
        self.draft(files=[f"outputs/{i}.png" for i in range(6)])
        self.assertEqual(self.sync()["uploaded"], 3)
        self.assertEqual(self.sync()["uploaded"], 3)
        self.assertEqual(self.sync()["uploaded"], 0)

    def test_the_byte_budget_of_one_cycle_is_bounded(self):
        for i in range(3):
            self.write(f"outputs/{i}.png")
        self.draft(files=[f"outputs/{i}.png" for i in range(3)])
        with patch.object(phone_files, "MAX_BYTES_PER_CYCLE", len(PNG) * 2):
            self.assertEqual(self.sync()["uploaded"], 2)

    def test_the_file_size_limit_is_50_mb(self):
        self.assertEqual(phone_files.MAX_FILE_BYTES, 50 * 1024 * 1024)
        self.write("outputs/ok.png", PNG + b"x" * 936)
        self.write("outputs/big.png", PNG + b"x" * 937)
        self.draft(files=["outputs/ok.png", "outputs/big.png"])
        with patch.object(phone_files, "MAX_FILE_BYTES", len(PNG) + 936):
            self.sync()
        self.assertEqual([r["file_path"] for r in self.cloud.rows], ["outputs/ok.png"])

    def test_a_huge_sparse_file_is_skipped(self):
        full = self.write("outputs/huge.png", PNG)
        with open(full, "r+b") as f:
            f.truncate(phone_files.MAX_FILE_BYTES + 1)
        self.draft(files=["outputs/huge.png"])
        self.assertEqual(self.sync()["uploaded"], 0)
        self.assertEqual(self.backend.uploads, [])

    def test_the_same_file_on_two_drafts_has_two_copies_with_two_names(self):
        self.write("outputs/shared.png")
        self.draft("cnt_a", ["outputs/shared.png"])
        self.draft("cnt_b", ["outputs/shared.png"])
        self.sync()
        self.assertEqual(len(self.cloud.rows), 2)
        self.assertEqual(len({r["storage_path"] for r in self.cloud.rows}), 2)

    def test_a_file_entry_may_be_a_plain_string(self):
        self.write("outputs/card.png")
        db.create_content_item("cnt_s", "post", "x", "Cero tip", "body", status="ready", files=["outputs/card.png"])
        self.sync()
        self.assertEqual(len(self.cloud.rows), 1)


class CrashRecoveryTests(_Base):
    def test_a_crash_between_the_row_and_the_upload_heals_on_the_next_cycle(self):
        self.write("outputs/card.png")
        self.draft()

        class _Crash(BaseException):
            pass

        self.backend.fail_upload = _Crash()
        with self.assertRaises(_Crash):
            self.sync()
        self.assertEqual(len(self.cloud.rows), 1)
        self.assertEqual(self.object_names(), [])
        self.backend.fail_upload = None
        self.sync()
        self.assertEqual(self.object_names(), [self.cloud.rows[0]["storage_path"]])
        uploads = len(self.backend.uploads)
        self.sync()
        self.assertEqual(len(self.backend.uploads), uploads, "a healed copy is not uploaded again")


class SafetyTests(_Base):
    def assertNothingUploaded(self):
        self.sync()
        self.assertEqual(self.backend.uploads, [])
        self.assertEqual(self.cloud.rows, [])

    def test_a_confidential_product_draft_is_never_copied(self):
        self.write("outputs/card.png")
        self.draft(product_id="secretproj")
        self.assertNothingUploaded()

    def test_a_draft_that_names_a_confidential_product_is_never_copied(self):
        self.write("outputs/card.png")
        self.draft(title="ProjectNightfall teaser")
        self.assertNothingUploaded()

    def test_a_file_whose_name_or_folder_names_one_is_never_copied(self):
        self.write("outputs/ProjectNightfall-hero.png")
        self.write("outputs/ProjectNightfall_hero.png")
        self.write("outputs/ProjectNightfall/hero.png")
        self.draft("cnt_a", ["outputs/ProjectNightfall-hero.png"])
        self.draft("cnt_b", ["outputs/ProjectNightfall_hero.png", "outputs/ProjectNightfall/hero.png"])
        self.assertNothingUploaded()

    def test_a_draft_whose_version_text_names_one_is_never_copied(self):
        self.write("outputs/card.png")
        self.draft()
        db.add_content_version("cnt_1", "Coming soon: ProjectNightfall", created_by="writer")
        self.assertNothingUploaded()

    def test_a_confidential_session_draft_is_never_copied(self):
        self.write("outputs/card.png")
        session_id = "sess_conf"
        db.create_agent_session({
            "id": session_id, "agent_id": "writer", "status": "done", "confidential": True, "title": "ProjectNightfall plan",
            "messages": [{"role": "user", "content": "ProjectNightfall plan"}],
            "created_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        self.draft(session_id=session_id)
        self.assertNothingUploaded()

    def test_a_pdf_that_names_one_in_plain_text_is_never_copied(self):
        self.write("outputs/plan.pdf", PDF + b"\n(ProjectNightfall roadmap)\n")
        self.draft(files=["outputs/plan.pdf"])
        self.assertNothingUploaded()

    def test_path_tricks_never_leave_the_data_folder(self):
        secret = os.path.join(os.path.dirname(self.tmpdir), "outside-secret.png")
        with open(secret, "wb") as f:
            f.write(PNG)
        self.addCleanup(lambda: os.path.exists(secret) and os.remove(secret))
        self.write("state/inside-state.png")
        self.write(".git/objects/x.png")
        self.write("outputs/.env.png")
        os.symlink(secret, os.path.join(self.tmpdir, "outputs", "link-out.png"))
        os.symlink(os.path.join(self.tmpdir, "state", "inside-state.png"), os.path.join(self.tmpdir, "outputs", "link-state.png"))
        os.symlink(self.tmpdir, os.path.join(self.tmpdir, "outputs", "dirlink"))
        entries = [
            "../outside-secret.png", "outputs/../../outside-secret.png", "/etc/passwd", secret, "state/inside-state.png",
            "outputs/../state/inside-state.png", ".git/objects/x.png", "outputs/.env.png", "outputs/link-out.png",
            "outputs/link-state.png", "outputs/dirlink/state/inside-state.png", "outputs\\..\\state\\inside-state.png",
            "C:\\Windows\\win.png", "outputs/missing.png", "", "outputs", "outputs/",
        ]
        self.draft(files=entries)
        self.assertNothingUploaded()

    def test_a_link_shows_the_drafts_name_and_cannot_change_the_file_type(self):
        self.write("outputs/v3-final-hero.png")
        self.write("outputs/real.pdf", PDF)
        os.symlink(os.path.join(self.tmpdir, "outputs", "v3-final-hero.png"), os.path.join(self.tmpdir, "outputs", "latest.png"))
        os.symlink(os.path.join(self.tmpdir, "outputs", "real.pdf"), os.path.join(self.tmpdir, "outputs", "fake.png"))
        self.draft(files=["outputs/latest.png", "outputs/fake.png"])
        self.sync()
        self.assertEqual([(r["file_path"], r["file_name"]) for r in self.cloud.rows], [("outputs/latest.png", "latest.png")])

    def test_a_file_that_is_not_what_its_name_says_is_never_copied(self):
        self.write("outputs/notes.png", b"password=hunter2\nCERO api key")
        self.write("outputs/page.pdf", b"<html><script>alert(1)</script>")
        self.write("outputs/clip.mp4", b"not a video at all, just text")
        self.write("outputs/empty.png", b"")
        self.draft(files=["outputs/notes.png", "outputs/page.pdf", "outputs/clip.mp4", "outputs/empty.png"])
        self.assertNothingUploaded()

    def test_other_file_types_are_never_copied(self):
        names = ("vector.svg", "page.html", "run.sh", "data.json", "notes.txt", "movie.mkv", "archive.zip", "x.exe", "anim.gif", "clip.mov")
        for name in names:
            self.write(f"outputs/{name}", PNG)
        self.draft(files=[f"outputs/{n}" for n in names])
        self.assertNothingUploaded()

    def test_unreadable_product_rules_copy_and_delete_nothing(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.assertEqual(len(self.cloud.rows), 1)
        os.remove(os.path.join(self.tmpdir, "config", "product_rules.json"))
        self.write("outputs/two.png")
        db.set_content_files("cnt_1", [{"path": "outputs/card.png"}, {"path": "outputs/two.png"}])
        summary = self.sync()
        self.assertTrue(summary["paused"])
        self.assertEqual(len(self.backend.uploads), 1)
        self.assertEqual(self.backend.deletes, [])
        self.assertEqual(len(self.cloud.rows), 1)


class RemovalTests(_Base):
    def test_a_draft_that_becomes_confidential_loses_its_copy_but_not_its_local_file(self):
        local = self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.assertEqual(len(self.cloud.rows), 1)
        db.add_content_version("cnt_1", "Now about ProjectNightfall", created_by="writer")
        summary = self.sync()
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})
        self.assertTrue(os.path.isfile(local))

    def test_a_file_taken_off_the_draft_loses_its_copy(self):
        self.write("outputs/a.png")
        self.write("outputs/b.png")
        self.draft(files=["outputs/a.png", "outputs/b.png"])
        self.sync()
        db.set_content_files("cnt_1", [{"path": "outputs/a.png"}])
        self.sync()
        self.assertEqual([r["file_path"] for r in self.cloud.rows], ["outputs/a.png"])
        self.assertEqual(len(self.backend.objects), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, "outputs", "b.png")))

    def test_a_draft_that_is_no_longer_finished_loses_its_copy(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        conn = db.connect()
        with conn:
            conn.execute("UPDATE content_items SET status = 'draft' WHERE id = 'cnt_1'")
        self.sync()
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})

    def test_a_deleted_local_file_removes_its_copy(self):
        local = self.write("outputs/card.png")
        self.draft()
        self.sync()
        os.remove(local)
        self.sync()
        self.assertEqual(self.cloud.rows, [])

    def test_a_deleted_draft_removes_its_copies(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        conn = db.connect()
        with conn:
            conn.execute("DELETE FROM content_items WHERE id = 'cnt_1'")
        self.sync()
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})

    def test_after_14_days_the_copy_goes_and_the_same_version_is_not_copied_again(self):
        local = self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.cloud.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        summary = self.sync()
        self.assertEqual(summary["removed"], 1)
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})
        self.assertTrue(os.path.isfile(local))
        uploads = len(self.backend.uploads)
        self.sync()
        self.sync()
        self.assertEqual(len(self.backend.uploads), uploads, "an expired copy must not come back by itself")
        with open(local, "wb") as f:
            f.write(PNG + b"edited")
        os.utime(local, (time.time() + 5, time.time() + 5))
        self.sync()
        self.assertEqual(len(self.cloud.rows), 1, "an edited file gets a fresh copy")

    def test_the_owner_can_remove_every_copy_and_it_stays_removed(self):
        local = self.write("outputs/card.png")
        self.write("outputs/two.png")
        self.draft(files=["outputs/card.png", "outputs/two.png"])
        self.sync()
        self.assertEqual(len(self.cloud.rows), 2)
        out = phone_files.remove(UID, remove_all=True, backend=self.backend)
        self.assertEqual(out["removed"], 2)
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})
        self.assertTrue(os.path.isfile(local))
        self.sync()
        self.assertEqual(self.cloud.rows, [])

    def test_the_owner_can_remove_one_copy(self):
        self.write("outputs/a.png")
        self.write("outputs/b.png")
        self.draft(files=["outputs/a.png", "outputs/b.png"])
        self.sync()
        out = phone_files.remove(UID, content_id="cnt_1", file_path="outputs/a.png", backend=self.backend)
        self.assertEqual(out["removed"], 1)
        self.assertEqual([r["file_path"] for r in self.cloud.rows], ["outputs/b.png"])

    def test_a_failed_object_delete_keeps_the_row_so_it_is_retried(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.cloud.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.backend.fail_delete = team_auth.TeamAuthError("boom", status=500)
        summary = self.sync()
        self.assertEqual(len(self.cloud.rows), 1)
        self.assertGreaterEqual(summary["errors"], 1)
        self.backend.fail_delete = None
        self.sync()
        self.assertEqual(self.cloud.rows, [])
        self.assertEqual(self.backend.objects, {})


class HostileCloudTests(_Base):
    def test_rows_of_another_owner_are_never_touched(self):
        self.cloud.ignore_owner_filter = True
        theirs = {"id": str(uuid.uuid4()), "owner_id": OTHER, "content_id": "x", "file_path": "o/x.png", "file_name": "x.png",
                  "kind": "image", "content_type": "image/png", "bytes": 5, "source_hash": "a" * 64,
                  "storage_path": f"{OTHER}/{'a' * 64}.png",
                  "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                  "created_at": datetime.now(timezone.utc).isoformat()}
        self.cloud.rows.append(theirs)
        self.backend.objects[theirs["storage_path"]] = b"theirs"
        self.sync()
        phone_files.remove(UID, remove_all=True, backend=self.backend)
        self.assertEqual(self.cloud.rows, [theirs])
        self.assertEqual(self.backend.deletes, [])

    def test_every_write_names_the_signed_in_owner(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.cloud.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.sync()
        for req in self.cloud.calls("DELETE", "phone_files") + self.cloud.calls("PATCH", "phone_files") + self.cloud.calls("GET", "phone_files"):
            self.assertIn(f"owner_id=eq.{UID}", req["url"])

    def test_a_row_pointing_at_someone_elses_or_a_forged_path_is_never_deleted_from_storage(self):
        for forged in (f"{OTHER}/{'b' * 64}.png", f"{UID}/../{OTHER}/{'b' * 64}.png", "/etc/passwd", f"{UID}/sub/{'b' * 64}.png",
                       f"{UID}/evil.html", ""):
            self.cloud.rows.append({
                "id": str(uuid.uuid4()), "owner_id": UID, "content_id": "z", "file_path": "o/z.png", "file_name": "z.png",
                "kind": "image", "content_type": "image/png", "bytes": 5, "source_hash": "b" * 64, "storage_path": forged,
                "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        self.sync()
        self.assertEqual(self.backend.deletes, [])

    def test_garbage_rows_do_not_crash_the_cycle(self):
        self.cloud.rows += [{"id": 5}, {"owner_id": UID}, {"id": "x", "owner_id": UID, "storage_path": 7, "expires_at": "tomorrow"}]
        self.write("outputs/card.png")
        self.draft()
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 1)

    def test_a_server_that_returns_a_non_list_is_an_error_not_a_crash(self):
        with patch.object(phone_files, "_rest", return_value={"oops": True}):
            with self.assertRaises(phone_files.PhoneFilesError):
                self.sync()

    def test_an_old_server_without_the_table_is_not_an_error(self):
        self.cloud.table_missing = True
        self.write("outputs/card.png")
        self.draft()
        summary = self.sync()
        self.assertTrue(summary["unavailable"])
        self.assertEqual(self.backend.uploads, [])


class BudgetAndFailureTests(_Base):
    def test_a_full_budget_skips_the_oldest_files_and_says_so_instead_of_failing(self):
        for i in range(4):
            self.write(f"outputs/{i}.png", PNG + b"x" * 1000)
            db.create_content_item(f"cnt_{i}", "post", "x", f"Cero card {i}", "body", status="ready",
                                   files=[{"path": f"outputs/{i}.png"}],
                                   created_at=f"2026-11-0{i + 1}T10:00:00+00:00")
        size = len(PNG) + 1000
        self.cloud.usage_bytes = phone_files.BUDGET_BYTES - size * 2 - 10
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 2)
        self.assertEqual(summary["skipped_budget"], 2)
        self.assertEqual(sorted(r["content_id"] for r in self.cloud.rows), ["cnt_2", "cnt_3"], "the newest drafts win")
        st = phone_files.status()
        self.assertEqual(st["skipped_budget"], 2)
        self.assertIn("storage", st["message"].lower())

    def test_the_budget_counts_the_previews_the_meter_already_holds(self):
        self.write("outputs/card.png")
        self.draft()
        self.cloud.usage_bytes = phone_files.BUDGET_BYTES - 1
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 0)
        self.assertEqual(summary["skipped_budget"], 1)
        self.assertEqual(self.backend.uploads, [])

    def test_a_server_that_refuses_the_row_over_budget_stops_before_any_upload(self):
        self.write("outputs/card.png")
        self.draft()
        self.cloud.insert_error = (400, "Cloud storage budget (1 GB) is full")
        summary = self.sync()
        self.assertEqual(self.backend.uploads, [], "the row is asked for first, so nothing is uploaded or left behind")
        self.assertEqual(self.backend.objects, {})
        self.assertEqual(summary["skipped_budget"], 1)
        self.assertEqual(summary["uploaded"], 0)

    def test_a_failed_upload_leaves_nothing_marked_done_and_is_retried(self):
        self.write("outputs/card.png")
        self.draft()
        self.backend.fail_upload = team_auth.TeamAuthError("Waiting for connection", status=0)
        summary = self.sync()
        self.assertGreaterEqual(summary["errors"], 1)
        self.assertEqual(self.cloud.rows, [])
        self.backend.fail_upload = None
        phone_files._reset_backoff()
        self.assertEqual(self.sync()["uploaded"], 1)

    def test_a_file_the_cloud_always_refuses_stops_being_retried_every_cycle(self):
        self.write("outputs/card.png")
        self.draft()
        self.backend.fail_upload = team_auth.TeamAuthError("Mime type not supported", status=415)
        for _ in range(phone_files.MAX_FAILED_ATTEMPTS + 3):
            phone_files._reset_backoff()
            self.sync()
        self.assertEqual(self.backend.uploads, [])
        self.assertEqual(self.backend.attempts, phone_files.MAX_FAILED_ATTEMPTS, "tried a few times, then left alone")
        self.backend.fail_upload = None
        self.sync()
        self.assertEqual(self.cloud.rows, [], "still left alone until the file changes")

    def test_signed_out_does_nothing(self):
        self.write("outputs/card.png")
        self.draft()
        with self.assertRaises(phone_files.SignedOutAbort):
            self.sync(is_signed_out=lambda: True)
        self.assertEqual(self.cloud.requests, [])
        self.assertEqual(self.backend.uploads, [])

    def test_the_account_changing_stops_the_cycle(self):
        self.write("outputs/a.png")
        self.write("outputs/b.png")
        self.draft(files=["outputs/a.png", "outputs/b.png"])
        real = self.backend.upload

        def switch(path, data, ctype):
            real(path, data, ctype)
            self.sign_in(OTHER)

        self.backend.upload = switch
        with self.assertRaises(phone_files.SignedOutAbort):
            self.sync()
        self.assertEqual(len(self.backend.uploads), 1)

    def test_unauthorised_means_signed_out(self):
        self.cloud.status_all = 401
        with self.assertRaises(phone_files.SignedOutAbort):
            self.sync()


class OrderAndPolicyTests(_Base):
    """The storage policies need a live copy row to store or see an object, so the laptop adds a copy row first,
    uploads second, and for an expired copy marks it, deletes the object, then the row."""

    def test_the_row_comes_before_the_object(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.assertEqual(self.cloud.order, ["row", "object"])
        self.assertEqual(len(self.backend.objects), 1)

    def test_a_failed_upload_takes_the_row_back_so_it_is_retried(self):
        self.write("outputs/card.png")
        self.draft()
        self.backend.fail_upload = team_auth.TeamAuthError("Mime type not supported", status=415)
        self.sync()
        self.assertEqual(self.cloud.rows, [], "no row pointing at a missing object")
        self.backend.fail_upload = None
        self.assertEqual(self.sync()["uploaded"], 1, "after one refusal it is simply retried")
        self.assertEqual(len(self.cloud.rows), 1)

    def test_the_budget_is_the_project_wide_total(self):
        self.write("outputs/card.png")
        self.draft()
        self.cloud.usage_bytes = phone_files.BUDGET_BYTES - 10  # other members use almost everything
        summary = self.sync()
        self.assertEqual(summary["uploaded"], 0)
        self.assertEqual(summary["skipped_budget"], 1)
        self.assertTrue(self.cloud.calls("POST", "rpc/team_storage_used"))
        self.assertEqual(self.cloud.calls("POST", "rpc/storage_usage"), [])

    def test_an_expired_copy_is_marked_then_deleted_then_its_row_dropped(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.cloud.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.sync()
        patches = self.cloud.calls("PATCH", "phone_files")
        self.assertEqual([p["body"] for p in patches], [{"removing": True}])
        self.assertEqual(self.backend.invisible, [], "the laptop never tried to read an object it could not see")
        self.assertEqual(self.backend.objects, {})
        self.assertEqual(self.cloud.rows, [])

    def test_an_unexpired_copy_is_deleted_without_the_marker(self):
        self.write("outputs/card.png")
        self.draft(product_id=None)
        self.sync()
        db.add_content_version("cnt_1", "Now about ProjectNightfall", created_by="writer")
        self.sync()
        self.assertEqual(self.cloud.calls("PATCH", "phone_files"), [])
        self.assertEqual(self.backend.objects, {})
        self.assertEqual(self.backend.invisible, [])

    def test_a_replaced_file_removes_the_old_copy_first_then_adds_the_new_one(self):
        path = self.write("outputs/card.png", PNG)
        self.draft()
        self.sync()
        old = self.cloud.rows[0]["storage_path"]
        with open(path, "wb") as f:
            f.write(PNG + b"more")
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.cloud.order.clear()
        self.backend.log.clear()
        self.sync()
        new = self.cloud.rows[0]["storage_path"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.backend.log, [("delete", old), ("upload", new)])
        self.assertEqual(self.cloud.order, ["row", "object"])
        self.assertEqual(self.backend.invisible, [])
        self.assertEqual(sorted(self.backend.objects), [new])

    def test_owner_removal_of_an_expired_copy_also_marks_it_first(self):
        self.write("outputs/card.png")
        self.draft()
        self.sync()
        self.cloud.rows[0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        phone_files.remove(UID, remove_all=True, backend=self.backend)
        self.assertEqual(self.backend.objects, {})
        self.assertEqual(self.backend.invisible, [])


class SyncCycleTests(_Base):
    def test_the_normal_sync_cycle_makes_the_copies(self):
        self.write("outputs/card.png")
        self.draft()
        with patch("phone_files.get_backend", return_value=self.backend), \
                patch("media_previews.ffmpeg_available", return_value=False):
            self.assertTrue(cloud_sync.sync_once())
        self.assertEqual(len(self.cloud.rows), 1)

    def test_a_failing_copy_step_does_not_stop_the_rest_of_the_sync(self):
        with patch("phone_files.sync", side_effect=RuntimeError("boom")):
            self.assertTrue(cloud_sync.sync_once())
        self.assertTrue(self.cloud.calls("POST", "mirror_records") or self.cloud.requests)

    def test_signing_out_clears_the_status(self):
        phone_files._set_status({"uploaded": 3})
        cloud_sync.notify_signed_out()
        self.assertEqual(phone_files.status()["uploaded"], 0)


class RouteTests(_Base):
    def _serve(self):
        server.ENGINE_TOKEN = "local-tok"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def _call(self, port, method, path, body=None, token="local-tok"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Magnm-Token"] = token
        conn.request(method, path, body=json.dumps(body).encode("utf-8") if body is not None else None, headers=headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        conn.close()
        return resp.status, (json.loads(raw) if raw else None)

    def test_status_and_owner_removal_routes(self):
        port = self._serve()
        self.write("outputs/card.png")
        self.draft()
        with patch("phone_files.get_backend", return_value=self.backend):
            phone_files.sync(UID, backend=self.backend)
            status, body = self._call(port, "GET", "/api/account/phone-files")
            self.assertEqual(status, 200)
            self.assertIn("uploaded", body)
            self.assertIn("message", body)
            status, body = self._call(port, "POST", "/api/account/phone-files/remove", {"all": True})
            self.assertEqual((status, body["removed"]), (200, 1))
        self.assertEqual(self.cloud.rows, [])
        self.assertTrue(os.path.isfile(os.path.join(self.tmpdir, "outputs", "card.png")))

    def test_routes_need_the_local_token(self):
        port = self._serve()
        self.assertIn(self._call(port, "GET", "/api/account/phone-files", token=None)[0], (401, 403))
        self.assertIn(self._call(port, "POST", "/api/account/phone-files/remove", {"all": True}, token="wrong")[0], (401, 403))

    def test_removal_needs_an_explicit_target(self):
        port = self._serve()
        with patch("phone_files.get_backend", return_value=self.backend):
            status, _ = self._call(port, "POST", "/api/account/phone-files/remove", {})
            self.assertEqual(status, 400)
            status, _ = self._call(port, "POST", "/api/account/phone-files/remove", {"content_id": ["x"], "file_path": 5})
            self.assertEqual(status, 400)


class StorageBackendTests(unittest.TestCase):
    def test_the_backend_talks_only_to_the_phone_files_bucket(self):
        backend = phone_files.PhoneFilesStorage()
        self.assertEqual(backend.BUCKET, "phone-files")
        import media_previews
        self.assertEqual(media_previews.SupabaseStorageBackend.BUCKET, "previews")

    def test_only_exact_object_names_are_accepted_for_delete(self):
        self.assertTrue(phone_files.is_own_object(UID, f"{UID}/{'a' * 64}.png"))
        for bad in (f"{OTHER}/{'a' * 64}.png", f"{UID}/{'a' * 64}.html", f"{UID}/{'a' * 64}.gif", f"{UID}/{'a' * 64}.mov", f"{UID}/../{'a' * 64}.png", f"{UID}/{'a' * 63}.png",
                    f"{UID}/{'A' * 64}.png", f"{UID}/x/{'a' * 64}.png", "", None, 5, f"{UID}/{'a' * 64}.png\n"):
            self.assertFalse(phone_files.is_own_object(UID, bad), bad)


if __name__ == "__main__":
    unittest.main()

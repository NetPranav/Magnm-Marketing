"""Library on the phone: files uploaded from the phone reach the owner's Library on the laptop through the private
phone-uploads bucket and a library_upload phone action; laptop assets are mirrored to the cloud (names and sizes only).

Uploads are untrusted: the type is checked by the first bytes, the size is capped, nothing is executed, confidential
text is refused and another user's rows are never used. No network: the Supabase endpoint and storage are faked.
"""

import json
import os
import sys
import unittest
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import assets_manager
import cloud_sync
import db
import paths
import phone_library
import phone_queue
import phone_server
import team_auth
from engine.tests.test_phone_queue import OTHER, OWNER, _Base as _QueueBase, _http_error, _Resp

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
HTML = b"<html><script>alert(1)</script></html>"


def _hex(n=1):
    return (f"{n:x}" * 64)[:64]


class _FakeStorage:
    """Stands in for the phone-uploads bucket: objects by name."""

    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.fail_download = False

    def download(self, storage_path):
        if self.fail_download:
            raise team_auth.TeamAuthError("storage down", status=503)
        if storage_path not in self.objects:
            raise team_auth.TeamAuthError("not found", status=404)
        return self.objects[storage_path]

    def delete(self, storage_paths):
        for p in storage_paths:
            self.deleted.append(p)
            self.objects.pop(p, None)


class _Base(_QueueBase):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.tmpdir, "assets", "uploads"), exist_ok=True)
        # The module-level asset folder was fixed when assets_manager was imported: point it at this test's folder.
        patcher = patch.object(assets_manager, "ASSETS_DIR", os.path.join(self.tmpdir, "assets", "uploads"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.storage = _FakeStorage()
        patcher = patch.object(phone_library, "get_backend", return_value=self.storage)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.uploads = {}  # id -> cloud row
        self.cloud_calls = []
        real = self.cloud.urlopen

        def urlopen(req, timeout=0):
            url, method = req.full_url, req.get_method()
            if "/rest/v1/phone_uploads" in url:
                self.cloud_calls.append((method, url))
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                want = (qs.get("id") or [""])[0].removeprefix("eq.")
                owner = (qs.get("owner_id") or [""])[0].removeprefix("eq.")
                if method == "GET":
                    rows = [r for r in self.uploads.values() if (not want or r["id"] == want) and (not owner or r["owner_id"] == owner)]
                    return _Resp(rows)
                if method == "DELETE":
                    if want in self.uploads and (not owner or self.uploads[want]["owner_id"] == owner):
                        del self.uploads[want]
                    return _Resp(None, status=204)
                if method == "PATCH":
                    if want in self.uploads:
                        self.uploads[want]["removing"] = True
                    return _Resp(None, status=204)
            return real(req, timeout)

        patcher = patch("team_auth.urllib.request.urlopen", side_effect=urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self, data=PNG, name="logo.png", ext="png", owner=OWNER, **over):
        """A file in the bucket and its row, as the phone leaves them."""
        uid = str(uuid.uuid4())
        path = f"{owner}/{_hex(len(self.uploads) + 1)}.{ext}"
        kind = {"png": "image", "jpg": "image", "webp": "image", "mp4": "video", "webm": "video", "pdf": "pdf"}.get(ext, "image")
        ctype = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf", "mp4": "video/mp4"}.get(ext, "image/png")
        row = {
            "id": uid, "owner_id": owner, "file_name": name, "kind": kind, "content_type": ctype,
            "bytes": len(data), "storage_path": path,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "removing": False,
        }
        row.update(over)
        self.uploads[uid] = row
        self.storage.objects[row["storage_path"]] = data
        return row

    def queue_upload(self, row, category="brand", description=None, target=None):
        payload = {"category": category}
        if description is not None:
            payload["description"] = description
        return self.queue(kind="library_upload", target=target or row["id"], payload=payload)

    def outcome(self, action):
        res = self.result_of(action)
        return res["status"], res["result"]

    def assets(self):
        return assets_manager.get_assets()


class ImportTests(_Base):
    def test_an_uploaded_image_joins_the_library(self):
        row = self.upload(PNG, "Launch logo.png")
        action = self.queue_upload(row, "brand", "The new mark")
        self.run_sync()
        self.assertEqual(self.outcome(action)[0], "applied", self.outcome(action))
        assets = self.assets()
        self.assertEqual(len(assets), 1)
        asset = assets[0]
        self.assertEqual((asset["category"], asset["description"], asset["file_group"]), ("brand", "The new mark", "image"))
        self.assertTrue(asset["name"].endswith(".png"))
        saved = os.path.join(self.tmpdir, asset["relative_path"])
        with open(saved, "rb") as f:
            self.assertEqual(f.read(), PNG)
        self.assertEqual(os.path.dirname(os.path.realpath(saved)), os.path.realpath(os.path.join(self.tmpdir, "assets", "uploads")))
        self.assertFalse(os.stat(saved).st_mode & 0o111, "a stored upload must never be executable")
        self.assertLessEqual(len(self.outcome(action)[1] or ""), 300)

    def test_every_allowed_type_is_taken(self):
        for data, name, ext in ((JPEG, "a.jpg", "jpg"), (PDF, "b.pdf", "pdf"), (MP4, "c.mp4", "mp4")):
            row = self.upload(data, name, ext)
            action = self.queue_upload(row, "document")
            self.run_sync()
            self.assertEqual(self.outcome(action)[0], "applied", (name, self.outcome(action)))
        self.assertEqual(len(self.assets()), 3)

    def test_the_cloud_copy_is_removed_after_the_import_so_the_budget_is_freed(self):
        row = self.upload(PNG)
        self.queue_upload(row)
        self.run_sync()
        self.assertNotIn(row["storage_path"], self.storage.objects)
        self.assertIn(row["storage_path"], self.storage.deleted)
        self.assertNotIn(row["id"], self.uploads)

    def test_importing_twice_adds_one_asset(self):
        row = self.upload(PNG)
        action = self.queue_upload(row)
        self.run_sync()
        # The cloud hands the same action back (a lost finish): the laptop's receipt answers, nothing more is added.
        self.cloud.get_row(action["id"]).update(status="queued", applied_by_device=None, result=None)
        self.run_sync()
        self.assertEqual(len(self.assets()), 1)

    def test_the_same_file_added_twice_at_once_is_one_asset_and_neither_action_fails(self):
        first, second = self.upload(PNG, "a.png"), self.upload(PNG, "b.png")
        a1, a2 = self.queue_upload(first), self.queue_upload(second)
        self.run_sync()
        self.assertEqual({self.outcome(a1)[0], self.outcome(a2)[0]}, {"applied"})
        self.assertLessEqual(len(self.assets()), 2)

    def test_a_crash_after_saving_never_adds_a_second_asset(self):
        row = self.upload(PNG)
        self.queue_upload(row)
        with patch.object(db, "record_phone_action_receipt", side_effect=RuntimeError("crash before the receipt")):
            self.run_sync()
        self.assertEqual(len(self.assets()), 1)
        # The cloud row and object may already be gone: the retry must still not add another.
        self.uploads.setdefault(row["id"], row)
        self.storage.objects.setdefault(row["storage_path"], PNG)
        self.run_sync()
        self.run_sync()
        self.assertEqual(len(self.assets()), 1)

    def test_an_older_engine_would_leave_the_new_kind_queued(self):
        self.assertIn("library_upload", phone_queue.KINDS)
        row = self.upload(PNG)
        self.queue_upload(row)
        self.run_sync()
        pulled = [c for c in self.cloud.requests if "/rest/v1/phone_actions" in c["url"] and c["method"] == "GET"]
        self.assertIn("library_upload", pulled[0]["url"])


class HostileUploadTests(_Base):
    def rejected(self, action, containing=None):
        self.run_sync()
        status, result = self.outcome(action)
        self.assertEqual(status, "rejected", (status, result))
        if containing:
            self.assertIn(containing, result or "")
        self.assertEqual(self.assets(), [])
        return result

    def test_bytes_that_are_not_what_the_name_says_are_refused(self):
        for data, ext in ((HTML, "png"), (HTML, "pdf"), (PNG, "pdf"), (b"MZ\x90\x00" + b"\x00" * 60, "jpg"), (b"GIF89a" + b"\x00" * 40, "png")):
            self.cloud.rows.clear()
            row = self.upload(data, f"x.{ext}", ext)
            self.rejected(self.queue_upload(row))

    def test_an_empty_or_oversize_file_is_refused(self):
        self.rejected(self.queue_upload(self.upload(b"", "e.png")))
        with patch.object(phone_library, "MAX_FILE_BYTES", 100):
            self.cloud.rows.clear()
            self.rejected(self.queue_upload(self.upload(PNG + b"\x00" * 200, "big.png")))

    def test_a_file_whose_size_differs_from_what_the_phone_declared_is_refused_and_removed(self):
        # The cloud budget counts the declared size, so a phone that lies about it is refused and its file removed.
        for declared in (len(PNG) - 1, len(PNG) + 1, 1):
            self.cloud.rows.clear()
            row = self.upload(PNG, "logo.png", bytes=declared)
            self.rejected(self.queue_upload(row))
            self.assertIn(row["storage_path"], self.storage.deleted, declared)

    def test_a_refused_file_of_the_owner_is_removed_from_the_cloud(self):
        cases = ((HTML, "x.png", "png"), (PNG, "ProjectNightfall mockup.png", "png"),
                 (PDF + b"<< /JavaScript (x) >>", "doc.pdf", "pdf"), (PDF + b"/Title (ProjectNightfall)\n", "p.pdf", "pdf"))
        for data, name, ext in cases:
            self.cloud.rows.clear()
            row = self.upload(data, name, ext)
            self.rejected(self.queue_upload(row))
            self.assertIn(row["storage_path"], self.storage.deleted, name)
            self.assertNotIn(row["storage_path"], self.storage.objects, name)

    def test_a_pdf_with_active_content_is_refused(self):
        for marker in (b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile", b"/OpenAction"):
            self.cloud.rows.clear()
            row = self.upload(PDF + b"<< " + marker + b" (x) >>", "doc.pdf", "pdf")
            self.rejected(self.queue_upload(row), "active content")

    def test_another_users_upload_row_is_never_used(self):
        row = self.upload(PNG, owner=OTHER)
        action = self.queue_upload(row)
        self.run_sync()
        self.assertEqual(self.outcome(action)[0], "rejected")
        self.assertEqual(self.assets(), [])
        self.assertIn(row["storage_path"], self.storage.objects, "another user's object must not even be deleted")

    def test_a_row_pointing_into_another_users_folder_is_refused(self):
        row = self.upload(PNG)
        row["storage_path"] = f"{OTHER}/{_hex(9)}.png"
        self.storage.objects[row["storage_path"]] = PNG
        self.rejected(self.queue_upload(row))
        self.assertIn(row["storage_path"], self.storage.objects)

    def test_path_tricks_in_the_storage_path_are_refused(self):
        for bad in (f"{OWNER}/../{OTHER}/{_hex(3)}.png", f"{OWNER}/{_hex(3)}.png/../../x", f"{OWNER}/short.png",
                    f"/{OWNER}/{_hex(3)}.png", f"{OWNER}/{_hex(3)}.sh", f"{OWNER}/{_hex(3)}.png\n"):
            self.cloud.rows.clear()
            row = self.upload(PNG)
            row["storage_path"] = bad
            self.rejected(self.queue_upload(row))

    def test_a_hostile_file_name_cannot_leave_the_library_folder(self):
        for name in ("../../../etc/cron.d/x.png", "..\\..\\evil.png", "/abs/evil.png", ".env", "x.png\x00.sh", "a\nb.png"):
            self.cloud.rows.clear()
            row = self.upload(PNG + name.encode("latin-1", "replace"), name)
            action = self.queue_upload(row)
            self.run_sync()
            status = self.outcome(action)[0]
            self.assertIn(status, ("applied", "rejected"), name)
        folder = os.path.realpath(os.path.join(self.tmpdir, "assets", "uploads"))
        for asset in self.assets():
            real = os.path.realpath(os.path.join(self.tmpdir, asset["relative_path"]))
            self.assertEqual(os.path.dirname(real), folder)
            self.assertTrue(os.path.splitext(real)[1] in (".png", ".jpg", ".pdf", ".mp4", ".webp", ".webm"))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "etc")))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "evil.png")))

    def test_a_name_or_note_naming_a_confidential_product_stays_on_the_phone(self):
        row = self.upload(PNG, "ProjectNightfall mockup.png")
        self.rejected(self.queue_upload(row), "confidential")
        self.cloud.rows.clear()
        row = self.upload(PNG, "fine.png")
        self.rejected(self.queue_upload(row, description="Screens from ProjectNightfall"), "confidential")

    def test_a_pdf_whose_text_names_a_confidential_product_is_refused(self):
        row = self.upload(PDF + b"/Title (ProjectNightfall roadmap)\n", "plan.pdf", "pdf")
        self.rejected(self.queue_upload(row), "confidential")

    def test_the_refusal_never_echoes_the_hostile_text(self):
        row = self.upload(PNG, "ProjectNightfall.png")
        result = self.rejected(self.queue_upload(row))
        self.assertNotIn("ProjectNightfall", result)

    def test_an_expired_or_missing_upload_is_refused(self):
        row = self.upload(PNG, expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.rejected(self.queue_upload(row), "expired")
        self.cloud.rows.clear()
        self.rejected(self.queue_upload(self.upload(PNG), target=str(uuid.uuid4())), "not found")

    def test_a_table_row_that_lies_about_its_shape_is_refused(self):
        row = self.upload(PNG)
        row["kind"] = "script"
        self.rejected(self.queue_upload(row))
        self.cloud.rows.clear()
        row2 = self.upload(PNG)
        row2["content_type"] = "text/html"
        self.rejected(self.queue_upload(row2))

    def test_a_storage_outage_leaves_the_action_to_retry_and_adds_nothing(self):
        row = self.upload(PNG)
        action = self.queue_upload(row)
        self.storage.fail_download = True
        self.run_sync()
        self.assertEqual(self.assets(), [])
        self.assertNotEqual(self.cloud.get_row(action["id"])["status"], "rejected")

    def test_bad_payloads_are_rejected_and_nothing_is_written(self):
        row = self.upload(PNG)
        for payload in ({}, {"category": "weapons"}, {"category": "brand", "path": "/etc/passwd"},
                        {"category": "brand", "description": "x\x07"}, {"category": 5}, [], {"category": "brand", "description": "y" * 301}):
            self.cloud.rows.clear()
            action = self.queue(kind="library_upload", target=row["id"], payload=payload)
            self.run_sync()
            self.assertEqual(self.outcome(action)[0], "rejected", payload)
        self.assertEqual(self.assets(), [])


class ExpiredCleanupTests(_Base):
    def test_only_the_owners_expired_uploads_are_removed(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        mine_old = self.upload(PNG, expires_at=old)
        mine_new = self.upload(PNG)
        theirs = self.upload(PNG, owner=OTHER, expires_at=old)
        out = phone_library.sync_expired(OWNER)
        self.assertEqual(out["removed"], 1)
        self.assertNotIn(mine_old["storage_path"], self.storage.objects)
        self.assertNotIn(mine_old["id"], self.uploads)
        self.assertIn(mine_new["id"], self.uploads)
        self.assertIn(theirs["id"], self.uploads)
        self.assertIn(theirs["storage_path"], self.storage.objects)
        self.assertTrue(any(m == "PATCH" for m, _ in self.cloud_calls), "an expired row is marked for removal first")

    def test_a_storage_failure_keeps_the_row_so_it_is_tried_again(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        row = self.upload(PNG, expires_at=old)
        with patch.object(self.storage, "delete", side_effect=team_auth.TeamAuthError("down", status=503)):
            phone_library.sync_expired(OWNER)
        self.assertIn(row["id"], self.uploads)


class MirrorTests(_Base):
    def make(self, kind, local_id, parent, title, data, ts):
        return {"kind": kind, "local_id": local_id, "parent_local_id": parent, "title": title, "data": data, "local_updated_at": ts}

    def register(self, name="logo.png", data=PNG, **kw):
        return assets_manager.register_asset(name, data, **kw)

    def records(self):
        guard = phone_server._build_confidential_guard()
        return phone_library.mirror_records(self.make, "2026-11-24T00:00:00+00:00", guard)

    def test_assets_are_listed_by_name_and_size_never_by_path(self):
        self.register("logo.png", PNG, category="brand", description="Our mark")
        recs = self.records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["kind"], "asset")
        data = rec["data"]
        self.assertEqual((data["name"], data["category"], data["file_group"], data["size_bytes"]), ("logo.png", "brand", "image", len(PNG)))
        self.assertNotIn("relative_path", data)
        self.assertNotIn("saved_filename", data)
        self.assertNotIn(self.tmpdir, json.dumps(rec))

    def test_a_confidential_asset_is_a_bare_stub(self):
        self.register("ProjectNightfall.png", PNG, category="brand")
        self.register("ok.png", PNG + b"x", description="Notes on ProjectNightfall")
        recs = self.records()
        self.assertEqual(len(recs), 2)
        for rec in recs:
            self.assertEqual(rec["data"].get("confidential"), True)
            self.assertEqual(rec["title"], "Confidential item")
            self.assertNotIn("Nightfall", json.dumps(rec))

    def test_at_most_a_hundred_recent_assets_are_mirrored(self):
        reg = [{"id": f"asset_{i}", "name": f"n{i}.png", "relative_path": f"assets/uploads/n{i}.png", "file_group": "image",
                "mime_type": "image/png", "size_bytes": 1, "category": "brand", "description": "", "tags": [],
                "uploaded_at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00"} for i in range(130)]
        db.save_assets(reg)
        self.assertEqual(len(self.records()), 100)

    def test_a_broken_asset_row_does_not_stop_the_others(self):
        db.save_assets([{"id": "asset_bad", "name": None, "size_bytes": "huge"}, {"id": "asset_ok", "name": "ok.png", "file_group": "image", "size_bytes": 5, "category": "brand"}])
        ids = [r["local_id"] for r in self.records()]
        self.assertIn("asset_ok", ids)


class LiveRouteTests(_Base):
    def guard(self):
        return phone_server._build_confidential_guard()

    def test_the_list_has_no_paths(self):
        asset = assets_manager.register_asset("logo.png", PNG, category="brand")
        listing = phone_library.list_assets(self.guard())
        self.assertEqual([a["id"] for a in listing], [asset["id"]])
        self.assertNotIn("relative_path", listing[0])

    def test_a_file_is_served_only_by_a_registered_asset_id(self):
        asset = assets_manager.register_asset("logo.png", PNG, category="brand")
        found = phone_library.resolve_asset_file(asset["id"], self.guard())
        self.assertEqual(os.path.realpath(found[0]), os.path.realpath(os.path.join(self.tmpdir, asset["relative_path"])))
        for bad in ("../../state/magnm.db", "asset_nope", "", "/etc/passwd", "asset_1/../../x", asset["id"] + "/.."):
            with self.assertRaises(phone_server.PhoneApiError):
                phone_library.resolve_asset_file(bad, self.guard())

    def test_a_registry_row_pointing_outside_the_library_is_refused(self):
        secret = os.path.join(self.tmpdir, "state", "secret.png")
        with open(secret, "wb") as f:
            f.write(PNG)
        db.save_assets([{"id": "asset_evil", "name": "x.png", "relative_path": "state/secret.png", "file_group": "image"},
                        {"id": "asset_evil2", "name": "x.png", "relative_path": "../outside.png", "file_group": "image"}])
        for aid in ("asset_evil", "asset_evil2"):
            with self.assertRaises(phone_server.PhoneApiError) as ctx:
                phone_library.resolve_asset_file(aid, self.guard())
            self.assertIn(ctx.exception.status, (403, 404))

    def test_a_confidential_asset_is_refused_on_the_live_route_too(self):
        asset = assets_manager.register_asset("ProjectNightfall.png", PNG, category="brand")
        with self.assertRaises(phone_server.PhoneApiError) as ctx:
            phone_library.resolve_asset_file(asset["id"], self.guard())
        self.assertEqual(ctx.exception.status, 403)
        self.assertEqual(phone_library.list_assets(self.guard())[0].get("confidential"), True)


if __name__ == "__main__":
    unittest.main()

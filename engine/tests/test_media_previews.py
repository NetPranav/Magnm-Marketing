"""Tests for engine.media_previews, db v14 media_upload_queue, and /api/account/media* routes."""

import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import assets_manager
import cloud_sync
import db
import media_previews
import paths
import server
import team_auth


class _FakeHttpResponse:
    def __init__(self, body_obj=None, status=200):
        self.status = status
        if body_obj is None:
            self._raw = b""
        elif isinstance(body_obj, (bytes, bytearray)):
            self._raw = bytes(body_obj)
        else:
            self._raw = json.dumps(body_obj).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(url, status, payload):
    raw = json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(url, status, "Error", hdrs=None, fp=io.BytesIO(raw))


class MediaPreviewsTests(unittest.TestCase):
    """Verify media preview discovery, privacy, ffmpeg args, queue, clean-up, routes, and real ffmpeg output."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        os.makedirs(os.path.join(self.tmpdir, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "state"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "assets", "uploads"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "outputs"), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_token = server.ENGINE_TOKEN
        self._prev_disable = os.environ.get("MAGNM_DISABLE_CLOUD_SYNC")

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = "0"
        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()

        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "url": "https://example.supabase.co",
                    "publishable_key": "sb_pub_test",
                    "storage_backend": "supabase",
                },
                f,
            )
        shutil.copy(
            os.path.join(self._prev_resource_root, "config", "product_rules.json"),
            os.path.join(self.tmpdir, "config", "product_rules.json"),
        )
        media_previews.notify_signed_out()

    def tearDown(self):
        media_previews.notify_signed_out()
        server.ENGINE_TOKEN = self._prev_token
        db.reset_connections()
        db.set_db_path(None)
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        if self._prev_disable is None:
            os.environ.pop("MAGNM_DISABLE_CLOUD_SYNC", None)
        else:
            os.environ["MAGNM_DISABLE_CLOUD_SYNC"] = self._prev_disable
        self._tmp_obj.cleanup()

    def _sign_in_user(self, user_id="user-1", email="member@example.com"):
        team_auth._save_session({
            "access_token": f"tok-{user_id}",
            "refresh_token": f"ref-{user_id}",
            "expires_at": int(time.time()) + 3600,
            "user_id": user_id,
            "email": email,
        })

    def _write_rel_file(self, rel_path, content=b"fake-media-bytes"):
        full = os.path.join(self.tmpdir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return full

    def _mock_router(self, custom_handler=None):
        requests = []

        def fake_urlopen(req, timeout=None):
            method = req.get_method()
            url = req.full_url
            headers = {k.lower(): v for k, v in req.header_items()}
            raw = req.data or b""
            body = None
            if raw and headers.get("content-type", "").startswith("application/json"):
                body = json.loads(raw.decode("utf-8"))
            entry = {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body,
                "raw": raw,
                "timeout": timeout,
            }
            requests.append(entry)
            if custom_handler:
                resp = custom_handler(entry)
                if resp is not None:
                    return resp
            if "/rest/v1/profiles" in url and method == "GET":
                sess = team_auth.get_session_info() or {}
                uid = sess.get("user_id") or "user-1"
                return _FakeHttpResponse([{
                    "id": uid,
                    "email": sess.get("email") or "member@example.com",
                    "display_name": "Member",
                    "avatar_url": None,
                    "role": "member",
                    "active": True,
                }])
            if url.endswith("/storage/v1/object/sign/previews/user-1/thumbnail/abc.webp"):
                return _FakeHttpResponse({"signedURL": "/object/sign/previews/user-1/thumbnail/abc.webp?token=sig123"})
            if "/storage/v1/object/sign/" in url:
                return _FakeHttpResponse({"signedURL": "/object/sign/previews/x?token=tok"})
            if "/rest/v1/rpc/storage_usage" in url:
                return _FakeHttpResponse({"bytes": 0, "files": 0, "by_kind": {}})
            if "/rest/v1/media_previews" in url and method == "GET":
                return _FakeHttpResponse([])
            return _FakeHttpResponse([])

        return requests, fake_urlopen

    def _fake_subprocess_factory(self, probe_map=None, record_cmds=None):
        def fake_run(cmd, **kwargs):
            if record_cmds is not None:
                record_cmds.append((cmd, kwargs))
            exe = os.path.basename(cmd[0])
            if exe == "ffprobe":
                target = cmd[-1]
                info = (probe_map or {}).get(target)
                if info is None:
                    if target.endswith(".mp4"):
                        info = {
                            "streams": [
                                {"codec_type": "video", "width": 1280, "height": 720, "duration": "12.5"},
                                {"codec_type": "audio"},
                            ],
                            "format": {"duration": "12.5"},
                        }
                    else:
                        info = {"streams": [{"codec_type": "video", "width": 800, "height": 600}], "format": {}}
                info = json.loads(json.dumps(info))
                info.setdefault("format", {}).setdefault(
                    "format_name", "mov,mp4,m4a,3gp,3g2,mj2" if target.endswith(".mp4") else "image2"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(info), stderr="")
            if exe == "ffmpeg":
                dst = cmd[-1]
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(b"RIFFwebp" if dst.endswith(".webp") else b"\x00\x00\x00\x18ftypmp42")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"Unexpected command: {cmd}")

        return fake_run

    def test_01_ffmpeg_argument_lists(self):
        """Verify ffmpeg arguments for thumbnail, image_preview (no upscale), and video_preview."""
        img_thumb = media_previews.build_ffmpeg_args("in.png", "out.webp", "thumbnail", "image")
        self.assertEqual(img_thumb[:3], ["ffmpeg", "-nostdin", "-y"])
        self.assertIn("-frames:v", img_thumb)
        self.assertIn("libwebp", img_thumb)
        self.assertIn("70", img_thumb)
        self.assertIn("min(320,iw)", " ".join(img_thumb))

        img_prev = media_previews.build_ffmpeg_args("in.gif", "out.webp", "image_preview", "image")
        self.assertIn("-frames:v", img_prev)
        self.assertIn("80", img_prev)
        self.assertIn("min(1080,iw)", " ".join(img_prev))
        self.assertIn("force_original_aspect_ratio=decrease", " ".join(img_prev))

        vid_thumb_long = media_previews.build_ffmpeg_args(
            "in.mp4", "t.webp", "thumbnail", "video", {"duration_s": 5.0}
        )
        self.assertEqual(vid_thumb_long[3:5], ["-ss", "1"])

        vid_thumb_short = media_previews.build_ffmpeg_args(
            "in.mp4", "t.webp", "thumbnail", "video", {"duration_s": 0.4}
        )
        self.assertNotIn("-ss", vid_thumb_short)

        vid_audio = media_previews.build_ffmpeg_args(
            "in.mp4", "p.mp4", "video_preview", "video", {"has_audio": True}
        )
        self.assertIn("-t", vid_audio)
        self.assertEqual(vid_audio[vid_audio.index("-t") + 1], "30")
        self.assertIn("scale=-2:'min(480,trunc(ih/2)*2)'", vid_audio)
        self.assertIn("libx264", vid_audio)
        self.assertIn("veryfast", vid_audio)
        self.assertIn("30", vid_audio)
        self.assertIn("+faststart", vid_audio)
        self.assertIn("aac", vid_audio)
        self.assertIn("64k", vid_audio)

        vid_no_audio = media_previews.build_ffmpeg_args(
            "in.mp4", "p.mp4", "video_preview", "video", {"has_audio": False}
        )
        self.assertIn("-an", vid_no_audio)
        self.assertNotIn("aac", vid_no_audio)

    def test_02_path_traversal_refused(self):
        """Paths escaping DATA_ROOT via .., absolute paths, or symlinks are refused."""
        with tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret.png")
            with open(secret, "wb") as f:
                f.write(b"secret")
            sym_path = os.path.join(self.tmpdir, "outputs", "link.png")
            if hasattr(os, "symlink"):
                os.symlink(secret, sym_path)

            db.create_content_item(
                "item-trav",
                "post",
                "x",
                "Traversal Test",
                "body",
                files=[
                    {"path": "../outside/secret.png"},
                    {"path": secret},
                    {"path": "outputs/link.png"},
                    {"path": "outputs/missing.png"},
                ],
            )
            sources = media_previews._collect_sources()
            self.assertEqual(sources, [])

    def test_03_confidential_media_and_assets_never_uploaded(self):
        """Confidential sessions, confidential content items, and confidential product assets are skipped."""
        self._sign_in_user("user-1")
        self._write_rel_file("outputs/secret1.png")
        self._write_rel_file("outputs/secret2.png")
        self._write_rel_file("outputs/secret3.png")
        self._write_rel_file("outputs/public.png")

        db.create_agent_session({
            "id": "sess-conf",
            "agent_id": "writer",
            "status": "done",
            "title": "Secret",
            "confidential": True,
            "messages": [],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:00:00Z",
        })
        db.create_agent_session({
            "id": "sess-child",
            "agent_id": "designer",
            "status": "done",
            "title": "Child",
            "parent_session_id": "sess-conf",
            "confidential": False,
            "messages": [],
            "created_at": "2026-11-21T01:00:00Z",
            "updated_at": "2026-11-21T01:00:00Z",
        })
        db.create_content_item(
            "c-conf", "post", "x", "Public Title", "body",
            session_id="sess-child", files=[{"path": "outputs/secret1.png"}],
        )
        db.create_content_item(
            "c-mention", "post", "x", "LazyLayout launch", "body",
            files=[{"path": "outputs/secret2.png"}],
        )
        db.create_content_item(
            "c-ver-mention", "post", "x", "Generic Title", "Showing LazyLayout demo",
            files=[{"path": "outputs/secret3.png"}],
        )
        db.create_content_item(
            "c-ok", "post", "x", "Cero release post", "Cero is live",
            files=[{"path": "outputs/public.png"}],
        )

        # Confidential assets by filename, description, or tags
        assets_manager.register_asset("lazylayout_mockup.png", b"img1", description="hero")
        assets_manager.register_asset("card.png", b"img2", description="Early LazyLayout screen")
        assets_manager.register_asset("clip.png", b"img3", description="ok", tags=["LazyLayout"])
        ok_asset = assets_manager.register_asset("cero_card.png", b"img4", description="Cero card", tags=["cero"])

        requests, fake_urlopen = self._mock_router()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        meta_posts = [r["body"][0] for r in requests if "/rest/v1/media_previews" in r["url"] and r["method"] == "POST"]
        uploaded_ids = {m["local_source_id"] for m in meta_posts}
        self.assertEqual(
            uploaded_ids,
            {"content:c-ok:outputs/public.png", f"asset:{ok_asset['id']}"},
        )

    def test_04_unreadable_product_rules_upload_nothing(self):
        """If product rules cannot be read, zero previews are generated or uploaded."""
        self._sign_in_user("user-1")
        self._write_rel_file("outputs/public.png")
        db.create_content_item("c-ok", "post", "x", "Cero", "Cero", files=[{"path": "outputs/public.png"}])
        os.remove(os.path.join(self.tmpdir, "config", "product_rules.json"))

        requests, fake_urlopen = self._mock_router()
        cmds = []
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory(record_cmds=cmds)),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertFalse(media_previews.sync_previews("user-1"))
            # status() checks for ffmpeg itself, so read it while ffmpeg is still faked (CI has no ffmpeg).
            last_error = media_previews.status()["last_error"]

        self.assertEqual(requests, [])
        self.assertEqual(cmds, [])
        self.assertIn("product rules", last_error)

    def test_05_unchanged_not_reprocessed_and_changed_deletes_old_after_success(self):
        """Unchanged files skip ffmpeg/upload; changed files re-upload and delete the old object after success."""
        self._sign_in_user("user-1")
        full = self._write_rel_file("outputs/card.png", b"version-1")
        db.create_content_item("c1", "post", "x", "Cero", "body", files=[{"path": "outputs/card.png"}])

        requests, fake_urlopen = self._mock_router()
        cmds1 = []
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory(record_cmds=cmds1)),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        first_uploads = [r["url"] for r in requests if "/storage/v1/object/previews/" in r["url"] and r["method"] == "POST"]
        self.assertEqual(len(first_uploads), 2)
        old_paths = [u.split("/storage/v1/object/previews/")[1] for u in first_uploads]

        # Second run with unchanged file -> zero ffmpeg or upload calls.
        requests.clear()
        cmds2 = []
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory(record_cmds=cmds2)),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        self.assertEqual(cmds2, [])
        self.assertEqual([r for r in requests if r["method"] == "POST"], [])

        # Modify file -> new upload + old object deleted after success.
        with open(full, "wb") as f:
            f.write(b"version-2-much-longer")
        os.utime(full, (time.time() + 10, time.time() + 10))
        requests.clear()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        new_uploads = [r for r in requests if "/storage/v1/object/previews/" in r["url"] and r["method"] == "POST"]
        deletes = [r for r in requests if r["url"].endswith("/storage/v1/object/previews") and r["method"] == "DELETE"]
        self.assertEqual(len(new_uploads), 2)
        deleted_prefixes = [p for d in deletes for p in d["body"]["prefixes"]]
        self.assertEqual(set(deleted_prefixes), set(old_paths))
        # Each old object is deleted only after its replacement upload and metadata upsert succeed.
        for old_p in old_paths:
            kind_seg = old_p.split("/")[1]
            upsert_idx = next(
                i for i, r in enumerate(requests)
                if "/rest/v1/media_previews" in r["url"] and r["method"] == "POST" and r["body"][0]["kind"] == kind_seg
            )
            del_idx = next(
                i for i, r in enumerate(requests)
                if r["method"] == "DELETE" and old_p in r["body"]["prefixes"]
            )
            self.assertGreater(del_idx, upsert_idx)

    def test_06_per_cycle_limits_and_expiry_by_kind(self):
        """At most 5 sources per cycle, byte cap respected, and expires_at set only for video_preview."""
        self._sign_in_user("user-1")
        entries = []
        for idx in range(7):
            rel = f"outputs/img_{idx}.png" if idx < 6 else "outputs/vid_6.mp4"
            self._write_rel_file(rel, b"x")
            entries.append({"path": rel})
        db.create_content_item("c-batch", "post", "x", "Cero batch", "body", files=entries)

        requests, fake_urlopen = self._mock_router()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        st1 = media_previews.status()
        self.assertEqual(st1["uploaded"], 10)  # 5 sources * 2 kinds
        self.assertEqual(st1["waiting"], 4)    # 2 remaining sources * 2 kinds

        # Check expires_at is None for images.
        meta1 = [r["body"][0] for r in requests if "/rest/v1/media_previews" in r["url"] and r["method"] == "POST"]
        self.assertTrue(all(m["expires_at"] is None for m in meta1))

        # Second cycle finishes remaining 2 sources including video_preview.
        requests.clear()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(media_previews.sync_previews("user-1"))

        meta2 = [r["body"][0] for r in requests if "/rest/v1/media_previews" in r["url"] and r["method"] == "POST"]
        vid_rows = [m for m in meta2 if m["kind"] == "video_preview"]
        self.assertEqual(len(vid_rows), 1)
        exp = datetime.fromisoformat(vid_rows[0]["expires_at"])
        delta_days = (exp - datetime.now(timezone.utc)).total_seconds() / 86400
        self.assertGreater(delta_days, 59.0)
        self.assertLess(delta_days, 61.0)

        # Also check byte limit stops cycle before exceeding MAX_UPLOAD_BYTES_PER_CYCLE.
        self._write_rel_file("outputs/big_a.png", b"a")
        self._write_rel_file("outputs/big_b.png", b"b")
        db.create_content_item("c-bytes", "post", "x", "Cero big", "body", files=[
            {"path": "outputs/big_a.png"},
            {"path": "outputs/big_b.png"},
        ])
        with (
            patch.object(media_previews, "MAX_UPLOAD_BYTES_PER_CYCLE", 16),
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            media_previews.sync_previews("user-1")
        st_bytes = media_previews.status()
        self.assertEqual(st_bytes["waiting"], 2)  # big_b waited for next cycle

    def test_07_backoff_on_5xx_and_signed_out_makes_no_requests(self):
        """5xx triggers backoff on queue row; signed-out user makes zero requests."""
        self._write_rel_file("outputs/one.png")
        db.create_content_item("c1", "post", "x", "Cero", "body", files=[{"path": "outputs/one.png"}])

        requests, fake_urlopen = self._mock_router()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertFalse(media_previews.sync_previews())
        self.assertEqual(requests, [])

        self._sign_in_user("user-1")

        def fail_503(entry):
            if "/storage/v1/object/previews/" in entry["url"] and entry["method"] == "POST":
                raise _http_error(entry["url"], 503, {"message": "Storage unavailable"})
            return None

        req_err, fake_err = self._mock_router(fail_503)
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_err),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_err),
        ):
            self.assertFalse(media_previews.sync_previews("user-1"))

        q = db.get_media_upload_queue("user-1")
        thumb_row = q[("content:c1:outputs/one.png", "thumbnail")]
        self.assertEqual(thumb_row["state"], "failed")
        self.assertEqual(thumb_row["attempts"], 1)
        self.assertGreater(thumb_row["next_attempt_at"], time.time() + 30)

        # Immediate re-run respects backoff and does not hit storage again.
        req_err.clear()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory()),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_err),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_err),
        ):
            media_previews.sync_previews("user-1")
        self.assertEqual([r for r in req_err if "/storage/v1/object/" in r["url"]], [])

    def test_08_daily_cleanup_deletes_expired_video_previews_and_old_cache(self):
        """Daily clean-up deletes expired video_preview objects/rows and removes >7d uploaded cache files."""
        self._sign_in_user("user-1")
        expired_rows = [
            {"id": "uuid-1", "storage_path": "user-1/video_preview/v1.mp4"},
            {"id": "uuid-2", "storage_path": "user-1/video_preview/v2.mp4"},
        ]

        def cleanup_handler(entry):
            if "/rest/v1/media_previews" in entry["url"] and entry["method"] == "GET":
                self.assertIn("kind=eq.video_preview", entry["url"])
                self.assertIn("owner_id=eq.user-1", entry["url"])
                return _FakeHttpResponse(expired_rows)
            return None

        # Create an uploaded queue entry + old cache file (> 7 days) and a pending cache file (> 7 days)
        db.upsert_media_upload_queue("user-1", [
            ("asset:a1", "thumbnail", "hash111111111111", "user-1/thumbnail/a1.webp", "uploaded", 0, 0.0, None, "2026-11-01T00:00:00Z"),
            ("asset:a2", "thumbnail", "hash222222222222", "", "pending", 0, 0.0, None, "2026-11-01T00:00:00Z"),
        ])
        cdir = os.path.join(self.tmpdir, "cache", "previews")
        os.makedirs(cdir, exist_ok=True)
        old_uploaded_cache = os.path.join(cdir, media_previews._cache_filename("asset:a1", "thumbnail", "hash111111111111"))
        old_pending_cache = os.path.join(cdir, media_previews._cache_filename("asset:a2", "thumbnail", "hash222222222222"))
        for p in (old_uploaded_cache, old_pending_cache):
            with open(p, "wb") as f:
                f.write(b"cached")
            old_ts = time.time() - (8 * 86400)
            os.utime(p, (old_ts, old_ts))

        requests, fake_urlopen = self._mock_router(cleanup_handler)
        with (
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            deleted = media_previews.run_daily_cleanup("user-1")

        self.assertEqual(deleted, 2)
        del_reqs = [r for r in requests if r["method"] == "DELETE" and r["url"].endswith("/storage/v1/object/previews")]
        self.assertEqual(len(del_reqs), 1)
        self.assertEqual(del_reqs[0]["body"], {"prefixes": ["user-1/video_preview/v1.mp4", "user-1/video_preview/v2.mp4"]})

        patch_reqs = [r for r in requests if r["method"] == "PATCH" and "/rest/v1/media_previews" in r["url"]]
        self.assertEqual(len(patch_reqs), 1)
        self.assertIn("deleted_at", patch_reqs[0]["body"])
        self.assertFalse(os.path.exists(old_uploaded_cache))
        self.assertTrue(os.path.exists(old_pending_cache))

    def test_09_missing_ffmpeg_reported_without_crash(self):
        """Missing ffmpeg/ffprobe skips processing and reports 'FFmpeg is not installed'."""
        self._sign_in_user("user-1")
        self._write_rel_file("outputs/one.png")
        db.create_content_item("c1", "post", "x", "Cero", "body", files=[{"path": "outputs/one.png"}])

        with patch("media_previews.ffmpeg_available", return_value=False):
            ok = media_previews.sync_previews("user-1")
            st = media_previews.status()

        self.assertFalse(ok)
        self.assertFalse(st["ffmpeg"])
        self.assertEqual(st["last_error"], "FFmpeg is not installed")
        self.assertEqual(st["waiting"], 2)

    def test_10_routes_signed_url_and_storage_usage(self):
        """HTTP routes /api/account/media, /api/account/storage, and /api/account/media/sign work."""
        self._sign_in_user("user-1")
        server.ENGINE_TOKEN = "local-tok"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        port = httpd.server_address[1]

        def storage_handler(entry):
            if "/rest/v1/rpc/storage_usage" in entry["url"]:
                return _FakeHttpResponse({"bytes": 900 * 1024 * 1024, "files": 42, "by_kind": {"video_preview": {"bytes": 800 * 1024 * 1024, "files": 12}}})
            return None

        _, fake_urlopen = self._mock_router(storage_handler)
        try:
            with (
                patch("media_previews.ffmpeg_available", return_value=True),
                patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
                patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/account/media", headers={"X-Magnm-Token": "local-tok"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                media_body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(media_body["ffmpeg"])
                self.assertEqual(media_body["uploaded"], 0)

                conn.request("GET", "/api/account/storage", headers={"X-Magnm-Token": "local-tok"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                storage_body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(storage_body["limit_bytes"], 1073741824)
                self.assertTrue(storage_body["warning"])
                self.assertGreater(storage_body["percent"], 80.0)

                conn.request(
                    "POST",
                    "/api/account/media/sign",
                    body=json.dumps({"storage_path": "user-1/thumbnail/abc.webp"}).encode("utf-8"),
                    headers={"X-Magnm-Token": "local-tok", "Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                sign_body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(
                    sign_body["url"],
                    "https://example.supabase.co/storage/v1/object/sign/previews/user-1/thumbnail/abc.webp?token=sig123",
                )
                conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()

    @unittest.skipUnless(media_previews.ffmpeg_available(), "ffmpeg/ffprobe not installed")
    def test_11_real_ffmpeg_generation_png_and_video(self):
        """Real ffmpeg test: generates WebP thumbnail/preview from PNG and thumbnail/MP4 preview from 2s video."""
        png_path = os.path.join(self.tmpdir, "outputs", "tiny.png")
        mp4_path = os.path.join(self.tmpdir, "outputs", "tiny.mp4")

        # 200x100 PNG (smaller than 320 and 1080 -> must NOT upscale)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=red:s=200x100:d=0.1", "-frames:v", "1", png_path],
            check=True,
            capture_output=True,
        )
        # 2s 1280x720 video with audio -> thumbnail at 1s (320x180 WebP) and 480p MP4 (854x480)
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-c:a", "aac", mp4_path,
            ],
            check=True,
            capture_output=True,
        )

        cache_dir = os.path.join(self.tmpdir, "cache", "previews")
        png_thumb = os.path.join(cache_dir, "png_thumb.webp")
        png_prev = os.path.join(cache_dir, "png_prev.webp")
        vid_thumb = os.path.join(cache_dir, "vid_thumb.webp")
        vid_prev = os.path.join(cache_dir, "vid_prev.mp4")

        png_info = media_previews.probe_media(png_path)
        media_previews.generate_preview(png_path, png_thumb, "thumbnail", "image", png_info)
        media_previews.generate_preview(png_path, png_prev, "image_preview", "image", png_info)

        vid_info = media_previews.probe_media(mp4_path)
        self.assertTrue(vid_info["has_audio"])
        media_previews.generate_preview(mp4_path, vid_thumb, "thumbnail", "video", vid_info)
        media_previews.generate_preview(mp4_path, vid_prev, "video_preview", "video", vid_info)

        self.assertGreater(os.path.getsize(png_thumb), 0)
        self.assertGreater(os.path.getsize(png_prev), 0)
        self.assertGreater(os.path.getsize(vid_thumb), 0)
        self.assertGreater(os.path.getsize(vid_prev), 0)

        out_png_prev = media_previews.probe_media(png_prev)
        self.assertEqual((out_png_prev["width"], out_png_prev["height"]), (200, 100))

        out_vid_thumb = media_previews.probe_media(vid_thumb)
        self.assertEqual((out_vid_thumb["width"], out_vid_thumb["height"]), (320, 180))

        out_vid_prev = media_previews.probe_media(vid_prev)
        self.assertEqual(out_vid_prev["height"], 480)
        self.assertEqual(out_vid_prev["width"] % 2, 0)
        self.assertTrue(out_vid_prev["has_audio"])

    def test_12_subprocess_flags_backend_switch_and_cloud_sync_integration(self):
        """Verify subprocess timeout/shell/preexec_fn, storage_backend switch, and cloud_sync hook."""
        self._sign_in_user("user-1")
        self._write_rel_file("outputs/hook.png")
        db.create_content_item("c-hook", "post", "x", "Cero hook", "body", files=[{"path": "outputs/hook.png"}])

        cmds = []
        requests, fake_urlopen = self._mock_router()
        with (
            patch("media_previews.ffmpeg_available", return_value=True),
            patch("media_previews.subprocess.run", side_effect=self._fake_subprocess_factory(record_cmds=cmds)),
            patch("media_previews.urllib.request.urlopen", side_effect=fake_urlopen),
            patch("team_auth.urllib.request.urlopen", side_effect=fake_urlopen),
        ):
            self.assertTrue(cloud_sync.sync_once())

        self.assertGreaterEqual(len(cmds), 2)
        for cmd, kwargs in cmds:
            self.assertFalse(kwargs.get("shell"))
            self.assertEqual(kwargs.get("timeout"), 120)
            if os.name == "posix":
                self.assertIsNotNone(kwargs.get("preexec_fn"))

        storage_posts = [r for r in requests if "/storage/v1/object/previews/" in r["url"] and r["method"] == "POST"]
        self.assertEqual(len(storage_posts), 2)

        self.assertIsInstance(media_previews.get_storage_backend(), media_previews.SupabaseStorageBackend)
        with open(os.path.join(self.tmpdir, "config", "supabase.json"), "w", encoding="utf-8") as f:
            json.dump({"url": "https://example.supabase.co", "publishable_key": "sb_pub_test", "storage_backend": "b2"}, f)
        with self.assertRaises(RuntimeError):
            media_previews.get_storage_backend()

    @unittest.skipUnless(media_previews.ffmpeg_available(), "ffmpeg/ffprobe not installed")
    def test_13_concat_list_disguised_as_video_is_refused(self):
        """A concat list renamed to .mp4, pulling in another media file, is rejected by the format allowlist."""
        folder = os.path.join(self.tmpdir, "outputs")
        os.makedirs(folder, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
             "-c:v", "libx264", "-f", "mpegts", os.path.join(folder, "private_clip.ts")],
            check=True, timeout=60,
        )
        fake = os.path.join(folder, "evil.mp4")
        with open(fake, "w", encoding="utf-8") as f:
            f.write("ffconcat version 1.0\nfile 'private_clip.ts'\n")
        with self.assertRaisesRegex(RuntimeError, "Unsupported media format"):
            media_previews.probe_media(fake)

if __name__ == "__main__":
    unittest.main()

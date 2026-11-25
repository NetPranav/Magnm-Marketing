"""Tests for the video_script workflow steps, the render_video tool, the video routes, jobs and the posting kit."""

import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import agent_runtime
import agents
import cmo_runtime
import db
import jobs
import paths
import posting_kit
import tools
import video_render
import voiceover
import workflows
from engine.tests.test_video_render import RULES, SCRIPT, FakeToolchain
from engine.tests.test_workflows import _StubHandler, _create_fake_session, _wait_until
from tools import ToolContext

END = video_render.END_CARD_S


class _Base(unittest.TestCase):
    def setUp(self):
        jobs.stop_worker(timeout=2.0)
        self._prev_home = os.environ.get("MAGNM_HOME")
        self._prev_kokoro = os.environ.pop("MAGNM_KOKORO_DIR", None)
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAGNM_HOME"] = self._tmp.name
        importlib.reload(paths)
        paths.ensure_data_root()
        with open(paths.data("config", "product_rules.json"), "w", encoding="utf-8") as fh:
            json.dump(RULES, fh)
        import product_rules
        product_rules.clear_cache()
        db.set_db_path(None)
        db.init_db()
        video_render._STATUS.clear()
        self.fake = FakeToolchain()
        self.fake.expected_total = 15.0 + END
        patcher = patch.object(video_render, "TOOLCHAIN", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        jobs.stop_worker(timeout=2.0)
        db.reset_connections()
        db.set_db_path(None)
        if self._prev_home is None:
            os.environ.pop("MAGNM_HOME", None)
        else:
            os.environ["MAGNM_HOME"] = self._prev_home
        if self._prev_kokoro is not None:
            os.environ["MAGNM_KOKORO_DIR"] = self._prev_kokoro
        importlib.reload(paths)
        self._tmp.cleanup()

    def make_item(self, content_id="cnt_vid_1"):
        db.create_content_item(
            content_id=content_id, content_type="script", platform="instagram", title="Quillby 15s Reel", body=SCRIPT,
            status="draft", product_id="quillby",
        )
        return content_id


def _fake_session_factory():
    def fake_start_session(agent_id, user_message, **kwargs):
        sess = _create_fake_session(agent_id, title=kwargs.get("title") or "")
        ctx = ToolContext(session_id=sess["id"], agent_id=agent_id)
        if agent_id == "video-producer":
            tools.run_tool(
                "write_draft", ctx,
                {"type": "script", "platform": "instagram", "title": "Quillby 15s Reel", "body": SCRIPT, "product_id": "quillby"},
                ["write_draft"],
            )
        elif agent_id == "reviewer":
            m = re.search(r"Review content item (cnt_[a-zA-Z0-9_]+)", user_message)
            tools.run_tool(
                "write_review", ctx, {"content_id": m.group(1), "verdict": "ok", "notes": "Fine."}, ["write_review"],
            )
        return sess

    return fake_start_session


class WorkflowInputTests(_Base):
    def test_defaults_render_voice_and_both_aspects(self):
        data = workflows.VideoScriptWorkflow().normalize_inputs({"topic": "x"})
        self.assertTrue(data["render"])
        self.assertTrue(data["voice"])
        self.assertEqual(data["aspects"], ["9:16", "16:9"])

    def test_choices_are_kept_and_bad_aspects_fall_back(self):
        wf = workflows.VideoScriptWorkflow()
        data = wf.normalize_inputs({"topic": "x", "render": False, "voice": False, "aspects": ["16:9"]})
        self.assertFalse(data["render"])
        self.assertFalse(data["voice"])
        self.assertEqual(data["aspects"], ["16:9"])
        self.assertEqual(wf.normalize_inputs({"topic": "x", "aspects": ["4:3"]})["aspects"], ["9:16", "16:9"])

    def test_step_order(self):
        names = [n for n, _ in workflows.VideoScriptWorkflow().steps]
        self.assertEqual(
            names, ["write_script", "review_script", "build_captions", "voice_over", "render_video", "finalize"],
        )

    def test_a_run_saved_before_rendering_existed_skips_the_new_steps(self):
        wf = workflows.VideoScriptWorkflow()
        old_run = {"id": "run_old", "inputs": {"topic": "x", "duration_s": 15, "aspect": "9:16"}}
        with patch.object(video_render, "render_video", side_effect=AssertionError("must not render")), patch.object(
            video_render, "voice_over", side_effect=AssertionError("must not speak")
        ):
            self.assertEqual(wf.step_voice_over(old_run, {"script_content_id": "x"}).action, "next")
            self.assertEqual(wf.step_render_video(old_run, {"script_content_id": "x"}).action, "next")


class WorkflowRunTests(_Base):
    def _run(self, **inputs):
        base = {"topic": "Quillby command safety", "duration_s": 15, "aspect": "9:16"}
        base.update(inputs)
        with patch.object(agent_runtime, "start_session", side_effect=_fake_session_factory()):
            run = workflows.start("video_script", base)
            self.assertTrue(_wait_until(lambda: (workflows.get(run["id"]) or {}).get("status") in ("done", "failed"), 20))
        return workflows.get(run["id"])

    def test_the_rendered_files_are_attached_to_the_script(self):
        done = self._run(voice=False)
        self.assertEqual(done["status"], "done", done.get("error"))
        script_id = done["state"]["script_content_id"]
        files = db.get_content_item(script_id)["files"]
        kinds = sorted(f["kind"] for f in files if f["kind"] in ("video", "poster", "captions"))
        # One captions file: the one timed to the voice replaces the planned one, so it matches the video.
        self.assertEqual(kinds, ["captions", "poster", "video", "video"])
        caps = [f for f in files if f["kind"] == "captions"]
        self.assertTrue(caps[0]["path"].replace("\\", "/").startswith("outputs/video/"), caps)
        self.assertTrue(os.path.isfile(paths.data("outputs", "video", script_id, "video-9x16.mp4")))
        self.assertEqual(done["state"]["rendered"]["aspects"], ["9:16", "16:9"])
        self.assertEqual(db.get_content_item(script_id)["status"], "ready")
        self.assertIn("outputs/video/%s/video-9x16.mp4" % script_id, done["state"]["output_files"])

    def test_render_false_keeps_the_old_behaviour(self):
        done = self._run(render=False)
        self.assertEqual(done["status"], "done")
        self.assertEqual(self.fake.ffmpeg_calls, [])
        files = db.get_content_item(done["state"]["script_content_id"])["files"]
        self.assertEqual([f["kind"] for f in files if f["kind"] in ("video", "poster")], [])

    def test_a_missing_voice_is_recorded_plainly_and_the_script_is_still_kept(self):
        done = self._run(voice=True)
        self.assertEqual(done["status"], "done")
        err = done["state"]["render_error"]
        self.assertEqual(err["code"], "no_voice")
        self.assertIn("Download voice", err["message"])
        self.assertEqual(self.fake.ffmpeg_calls, [])
        script_id = done["state"]["script_content_id"]
        self.assertEqual(db.get_content_item(script_id)["status"], "ready")

    def test_a_failed_check_does_not_fail_the_run(self):
        self.fake.duration_error = 3.0
        done = self._run(voice=False)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["state"]["render_error"]["code"], "check_failed")
        files = db.get_content_item(done["state"]["script_content_id"])["files"]
        self.assertEqual([f for f in files if f["kind"] == "video"], [])

    def test_the_voice_step_speaks_once_and_the_render_step_reuses_the_clips(self):
        with patch.object(voiceover, "missing_files", return_value=[]):
            self.fake.expected_total = 3.55 + 7.0 + 5.0 + END
            done = self._run(voice=True)
        self.assertEqual(done["status"], "done", done.get("error"))
        self.assertEqual(done["state"]["voice_info"]["clips"], 3)
        self.assertEqual(len(self.fake.synth_calls), 3)  # not 6: the render step used the cache


class ToolTests(_Base):
    def test_the_tool_is_registered_and_only_the_video_producer_has_it(self):
        self.assertIsNotNone(tools.get_tool("render_video"))
        self.assertIn("render_video", agents.get_agent("video-producer")["tools"])
        for other in ("writer", "designer", "reviewer", "researcher", "analyst", "cmo"):
            self.assertNotIn("render_video", agents.get_agent(other)["tools"])

    def test_the_tool_renders_and_returns_the_files(self):
        cid = self.make_item()
        ctx = ToolContext(session_id="sess_t", agent_id="video-producer")
        res = tools.run_tool("render_video", ctx, {"content_id": cid, "voice": False}, ["render_video"])
        self.assertNotIn("error", res, res)
        self.assertEqual(res["aspects"], ["9:16", "16:9"])
        self.assertTrue(any(p.endswith("video-9x16.mp4") for p in res["files"]))

    def test_the_tool_gives_a_plain_error_when_the_voice_is_missing(self):
        cid = self.make_item()
        ctx = ToolContext(session_id="sess_t", agent_id="video-producer")
        res = tools.run_tool("render_video", ctx, {"content_id": cid}, ["render_video"])
        self.assertEqual(res["code"], "no_voice")
        self.assertIn("Download voice", res["error"])

    def test_the_tool_does_not_wait_when_another_render_runs(self):
        cid = self.make_item()
        ctx = ToolContext(session_id="sess_t", agent_id="video-producer")
        self.assertTrue(video_render._RENDER_LOCK.acquire(blocking=False))
        try:
            res = tools.run_tool("render_video", ctx, {"content_id": cid, "voice": False}, ["render_video"])
        finally:
            video_render._RENDER_LOCK.release()
        self.assertEqual(res["code"], "busy")

    def test_the_tool_is_refused_for_an_agent_without_it(self):
        cid = self.make_item()
        ctx = ToolContext(session_id="sess_t", agent_id="writer")
        res = tools.run_tool("render_video", ctx, {"content_id": cid}, ["write_draft"])
        self.assertIn("error", res)


class RouteTests(_Base):
    def _get(self, path):
        h = _StubHandler(path)
        h.do_GET()
        return h

    def _post(self, path, body):
        h = _StubHandler(path, "POST", body)
        h.do_POST()
        return h

    def setUp(self):
        super().setUp()
        patcher = patch.object(jobs, "start_worker")
        self.start_worker = patcher.start()
        self.addCleanup(patcher.stop)

    def test_status_reports_voice_settings_and_tools(self):
        h = self._get("/api/video/status")
        self.assertEqual(h.status_code, 200)
        data = h.response_json()
        self.assertFalse(data["voice"]["installed"])
        self.assertEqual(data["settings"]["voice"], "am_michael")
        self.assertTrue(data["ffmpeg"])
        self.assertEqual(data["rendering"], [])

    def test_settings_are_saved_and_checked(self):
        h = self._post("/api/video/settings", {"voice": "af_heart", "speed": 1.1, "captions": False})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.response_json()["settings"], {"voice": "af_heart", "speed": 1.1, "captions": False, "flux": False})
        self.assertEqual(self._get("/api/video/status").response_json()["settings"]["voice"], "af_heart")
        for bad in ({"voice": "nobody"}, {"speed": 5}, {"captions": "yes"}, {"colour": "red"}, {}):
            self.assertEqual(self._post("/api/video/settings", bad).status_code, 400, bad)
        self.assertEqual(self._get("/api/video/status").response_json()["settings"]["speed"], 1.1)

    def test_render_needs_a_known_script(self):
        h = self._post("/api/video/render", {"content_id": "cnt_nope", "voice": False})
        self.assertEqual(h.status_code, 404)

    def test_render_with_voice_but_no_voice_installed_offers_the_download(self):
        cid = self.make_item()
        h = self._post("/api/video/render", {"content_id": cid})
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.response_json()["code"], "no_voice")
        self.assertIn("Download voice", h.response_json()["error"])
        self.assertEqual(db.list_jobs(limit=5), [])

    def test_render_without_voice_queues_a_job_and_shows_waiting(self):
        cid = self.make_item()
        h = self._post("/api/video/render", {"content_id": cid, "voice": False, "aspects": ["9:16"]})
        self.assertEqual(h.status_code, 200)
        job = h.response_json()["job"]
        self.assertEqual(job["name"], "render_video")
        self.assertEqual(job["params"], {"content_id": cid, "aspects": ["9:16"], "voice": False})
        status = self._get(f"/api/video/render/{cid}").response_json()
        self.assertEqual(status["state"], "queued")
        self.assertEqual(self._get("/api/video/status").response_json()["rendering"], [cid])
        again = self._post("/api/video/render", {"content_id": cid, "voice": False})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.response_json()["code"], "busy")

    def test_cancelling_a_queued_render(self):
        cid = self.make_item()
        self._post("/api/video/render", {"content_id": cid, "voice": False})
        h = self._post("/api/video/render/cancel", {"content_id": cid})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(self._get(f"/api/video/render/{cid}").response_json()["state"], "cancelled")
        self.assertEqual(self._post("/api/video/render/cancel", {"content_id": cid}).status_code, 409)

    def test_bad_render_input(self):
        cid = self.make_item()
        self.assertEqual(self._post("/api/video/render", {"content_id": cid, "voice": "no"}).status_code, 400)
        self.assertEqual(self._post("/api/video/render", {"content_id": cid, "voice": False, "aspects": ["4:3"]}).status_code, 409)

    def test_the_voice_download_is_a_job(self):
        h = self._post("/api/video/voice/download", {})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.response_json()["job"]["name"], "download_voice")
        with patch.object(voiceover, "missing_files", return_value=[]):
            again = self._post("/api/video/voice/download", {})
        self.assertTrue(again.response_json()["installed"])

    def test_the_sample_is_served_as_audio(self):
        wav = os.path.join(self._tmp.name, "s.wav")
        voiceover.write_wav(wav, [0.1] * 2400, 24000)
        served = []
        with patch.object(voiceover, "sample", return_value=wav) as sample, patch.object(
            _StubHandler, "_serve_file_with_range", lambda self, path, mime: served.append((path, mime))
        ):
            self._get("/api/video/voice/sample?voice=af_heart&speed=1.1")
        sample.assert_called_once_with("af_heart", "1.1")
        self.assertEqual(served, [(wav, "audio/wav")])

    def test_the_sample_without_a_voice_is_a_plain_409(self):
        h = self._get("/api/video/voice/sample?voice=am_michael")
        self.assertEqual(h.status_code, 409)
        self.assertIn("Download voice", h.response_json()["error"])

    def test_the_rendered_file_is_served_through_the_content_file_route(self):
        cid = self.make_item()
        video_render.render_video(cid, voice=False)
        entry = [f for f in db.get_content_item(cid)["files"] if f["kind"] == "video"][0]
        served = []
        with patch.object(_StubHandler, "_serve_file_with_range", lambda self, path, mime: served.append(mime)):
            self._get(f"/api/content/{cid}/file?path={entry['path']}")
        self.assertEqual(served, ["video/mp4"])


class JobTests(_Base):
    def test_the_render_job_body_renders_and_records_a_result(self):
        cid = self.make_item()
        out = cmo_runtime.run_job_body(
            "render_video", job_id="render_video_t1", log_file=paths.data("logs", "jobs", "t1.log"),
            params={"content_id": cid, "voice": False},
        )
        self.assertIn("Rendered", out)
        self.assertTrue(os.path.isfile(paths.data("outputs", "video", cid, "video-16x9.mp4")))

    def test_a_cancelled_render_job_is_a_cancelled_job(self):
        cid = self.make_item()
        with patch.object(jobs, "is_cancel_requested", return_value=True):
            with self.assertRaises(jobs.JobCancelled):
                cmo_runtime.run_job_body(
                    "render_video", job_id="render_video_t2", log_file=paths.data("logs", "jobs", "t2.log"),
                    params={"content_id": cid, "voice": False},
                )

    def test_the_voice_download_job_runs_the_download(self):
        with patch.object(voiceover, "download") as dl:
            out = cmo_runtime.run_job_body(
                "download_voice", job_id="download_voice_t1", log_file=paths.data("logs", "jobs", "t3.log"),
            )
        dl.assert_called_once()
        self.assertIn("installed", out)

    def test_both_jobs_are_allowed_names(self):
        self.assertIn("render_video", cmo_runtime.ALLOWED_JOBS)
        self.assertIn("download_voice", cmo_runtime.ALLOWED_JOBS)


class PostingKitTests(_Base):
    def test_the_kit_lists_the_rendered_video_poster_and_captions(self):
        cid = self.make_item()
        video_render.render_video(cid, voice=False)
        rel = posting_kit.build_posting_kit([cid])
        with open(paths.data(rel), encoding="utf-8") as fh:
            kit = fh.read()
        self.assertIn("### Video", kit)
        self.assertIn(f"outputs/video/{cid}/video-9x16.mp4", kit)
        self.assertIn(f"outputs/video/{cid}/video-16x9.mp4", kit)
        self.assertIn("poster", kit)

    def test_an_item_without_a_video_has_no_video_section(self):
        cid = self.make_item()
        rel = posting_kit.build_posting_kit([cid])
        with open(paths.data(rel), encoding="utf-8") as fh:
            self.assertNotIn("### Video", fh.read())


if __name__ == "__main__":
    unittest.main()

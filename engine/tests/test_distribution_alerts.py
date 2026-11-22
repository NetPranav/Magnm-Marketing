"""Unit tests for Phase 10 alerts, catalog hardening, build discovery, and smoke test helpers."""

from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

import ai_gateway
import db
import key_pool
import model_registry
import notifications
import paths


def _load_script_module(name, filename):
    path = os.path.join(SCRIPTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeHTTPResponse:
    def __init__(self, payload, status=200):
        self.status = status
        if isinstance(payload, (bytes, bytearray)):
            self._raw = bytes(payload)
        elif isinstance(payload, str):
            self._raw = payload.encode("utf-8")
        else:
            self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DistributionAlertsTests(unittest.TestCase):
    """Tests for local key alerts, NIM catalog alerts, monitor lifecycle, and packaging."""

    def setUp(self):
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp_obj.name
        for sub in ("config", "state"):
            os.makedirs(os.path.join(self.tmpdir, sub), exist_ok=True)

        self._prev_data_root = paths.DATA_ROOT
        self._prev_resource_root = paths.RESOURCE_ROOT
        self._prev_db_path = db.get_db_path()
        self._prev_env_path = ai_gateway.ENV_PATH
        self._prev_models_cfg = model_registry.MODELS_CONFIG_PATH
        self._prev_catalog_cache = model_registry.CATALOG_CACHE_PATH

        paths.DATA_ROOT = self.tmpdir
        paths.RESOURCE_ROOT = self.tmpdir
        ai_gateway.ENV_PATH = os.path.join(self.tmpdir, ".env")
        model_registry.MODELS_CONFIG_PATH = os.path.join(REPO_ROOT, "config", "models.json")
        model_registry.CATALOG_CACHE_PATH = os.path.join(self.tmpdir, "state", "model_catalog.json")

        db.set_db_path(os.path.join(self.tmpdir, "state", "magnm.db"))
        db.init_db()
        key_pool.reset()
        model_registry.stop_catalog_monitor(timeout=1.0)

    def tearDown(self):
        model_registry.stop_catalog_monitor(timeout=1.0)
        key_pool.reset()
        db.reset_connections()
        db.set_db_path(self._prev_db_path)
        paths.DATA_ROOT = self._prev_data_root
        paths.RESOURCE_ROOT = self._prev_resource_root
        ai_gateway.ENV_PATH = self._prev_env_path
        model_registry.MODELS_CONFIG_PATH = self._prev_models_cfg
        model_registry.CATALOG_CACHE_PATH = self._prev_catalog_cache
        self._tmp_obj.cleanup()

    def test_01_notification_settings_include_new_kinds(self):
        """key_rejected, keys_exhausted, and models_changed appear in settings and respect toggles."""
        settings_resp = notifications.get_settings(is_admin=False)
        by_kind = {item["kind"]: item for item in settings_resp["settings"]}
        for kind in ("key_rejected", "keys_exhausted", "models_changed"):
            self.assertIn(kind, by_kind)
            self.assertFalse(by_kind[kind]["admin_only"])
            self.assertTrue(by_kind[kind]["enabled"])

        notifications.update_setting("keys_exhausted", False, is_admin=False)
        self.assertIsNone(notifications.notify_local_keys_exhausted(now_ts=1000.0))

    def test_02_key_rejected_once_per_key_per_day_safe_text_and_lock_free(self):
        """key_rejected alerts once per key/day, survives restarts, never leaks key, releases lock."""
        raw_key1 = "nvapi-fake-alpha-999"
        raw_key2 = "nvapi-fake-beta-888"
        env_map = {
            "NVIDIA_NIM_API_KEY1": raw_key1,
            "NVIDIA_NIM_API_KEY2": raw_key2,
        }
        pool = key_pool.KeyPool(env_loader=lambda: {}, environ=env_map)
        pool.sync_from_env()

        lock_states = []
        orig_notify = notifications.notify

        def spy_notify(*args, **kwargs):
            acquired = pool._lock.acquire(blocking=False)
            lock_states.append(acquired)
            if acquired:
                pool._lock.release()
            return orig_notify(*args, **kwargs)

        with patch.object(notifications, "notify", side_effect=spy_notify):
            pool.report("NVIDIA_NIM_API_KEY1", "auth_failed")
            pool.report("NVIDIA_NIM_API_KEY1", "auth_failed")

        self.assertEqual(lock_states, [True])
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "key_rejected"]
        self.assertEqual(len(notifs), 1)
        n1_text = json.dumps(notifs[0])
        self.assertIn("NVIDIA_NIM_API_KEY1", n1_text)
        self.assertNotIn(raw_key1, n1_text)
        self.assertNotIn("nvapi-", n1_text)
        self.assertNotIn("all local", notifs[0]["title"].lower())

        # Simulate restart with a brand new KeyPool against the same SQLite DB
        pool2 = key_pool.KeyPool(env_loader=lambda: {}, environ=env_map)
        pool2.sync_from_env()
        pool2.report("NVIDIA_NIM_API_KEY1", "auth_failed")
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "key_rejected"]
        self.assertEqual(len(notifs), 1)

        # Rejecting the remaining key reports that all local keys are rejected
        pool2.report("NVIDIA_NIM_API_KEY2", "auth_failed")
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "key_rejected"]
        self.assertEqual(len(notifs), 2)
        self.assertIn("all local", notifs[0]["title"].lower())
        self.assertIn("NVIDIA_NIM_API_KEY2", notifs[0]["body"])
        self.assertNotIn(raw_key2, json.dumps(notifs[0]))

        # Next day resets per-day deduplication
        notifications.notify_local_key_rejected("NVIDIA_NIM_API_KEY1", all_rejected=False, today_utc="2099-01-02")
        notifs = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "key_rejected"]
        self.assertEqual(len(notifs), 3)

    def test_03_keys_exhausted_hourly_cap_and_lock_free(self):
        """keys_exhausted fires when all local keys are cooling, at most once/hour, lock-free."""
        now = [100.0]
        wall_ts = [5000.0]
        pool = key_pool.KeyPool(
            clock=lambda: now[0],
            sleep=lambda s: None,
            env_loader=lambda: {},
            environ={
                "NVIDIA_NIM_API_KEY1": "nvapi-cool-111111111",
                "NVIDIA_NIM_API_KEY2": "nvapi-cool-222222222",
            },
            acquire_timeout_s=0.0,
        )
        pool.sync_from_env()
        pool.report("NVIDIA_NIM_API_KEY1", "rate_limited", retry_after=60.0)

        # Only 1 of 2 keys cooling -> acquire succeeds with no keys_exhausted alert
        _, lbl = pool.acquire()
        self.assertEqual(lbl, "NVIDIA_NIM_API_KEY2")
        self.assertEqual(
            [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "keys_exhausted"],
            [],
        )

        # Now both keys cooling -> acquire raises AINotConnected and notifies outside lock
        pool.report("NVIDIA_NIM_API_KEY2", "rate_limited", retry_after=60.0)
        lock_states = []
        orig_notify = notifications.notify

        def spy_notify(*args, **kwargs):
            acquired = pool._lock.acquire(blocking=False)
            lock_states.append(acquired)
            if acquired:
                pool._lock.release()
            return orig_notify(*args, **kwargs)

        with patch.object(notifications, "notify", side_effect=spy_notify), \
             patch("notifications.time.time", side_effect=lambda: wall_ts[0]):
            with self.assertRaises(key_pool.AINotConnected):
                pool.acquire()
            # Within 1 hour -> suppressed
            wall_ts[0] += 1800.0
            with self.assertRaises(key_pool.AINotConnected):
                pool.acquire()
            # After > 1 hour -> notifies again
            wall_ts[0] += 2000.0
            with self.assertRaises(key_pool.AINotConnected):
                pool.acquire()

        self.assertEqual(lock_states, [True, True])
        exhausted = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "keys_exhausted"]
        self.assertEqual(len(exhausted), 2)

    def test_04_models_changed_baseline_left_returned_and_role_override(self):
        """models_changed baselines quietly, alerts once on leave/return, and watches role overrides."""
        cfg = model_registry.load_models_config()
        watched = notifications._watched_nim_models(models_cfg=cfg, role_overrides={"cmo": "custom/cmo-model"})
        full_catalog = set(watched) | {f"extra/model-{i}" for i in range(15)}

        # First sight: all healthy -> 0 alerts
        res0 = notifications.evaluate_nim_catalog_changes(
            full_catalog, models_cfg=cfg, role_overrides={"cmo": "custom/cmo-model"}
        )
        self.assertEqual(res0, [])

        # Remove tier model and role override model -> 2 alerts
        missing_target = cfg["tiers"]["writing"]["primary"]
        reduced = full_catalog - {missing_target, "custom/cmo-model"}
        res1 = notifications.evaluate_nim_catalog_changes(
            reduced, models_cfg=cfg, role_overrides={"cmo": "custom/cmo-model"}
        )
        self.assertEqual(len(res1), 2)
        titles1 = {n["title"] for n in res1}
        self.assertTrue(any(missing_target in t for t in titles1))
        self.assertTrue(any("custom/cmo-model" in t for t in titles1))

        # Repeat with same reduced catalog -> 0 alerts
        res2 = notifications.evaluate_nim_catalog_changes(
            reduced, models_cfg=cfg, role_overrides={"cmo": "custom/cmo-model"}
        )
        self.assertEqual(res2, [])

        # Bring one model back -> 1 alert
        restored = reduced | {"custom/cmo-model"}
        res3 = notifications.evaluate_nim_catalog_changes(
            restored, models_cfg=cfg, role_overrides={"cmo": "custom/cmo-model"}
        )
        self.assertEqual(len(res3), 1)
        self.assertIn("custom/cmo-model", res3[0]["title"])
        self.assertIn("returned", res3[0]["title"].lower())

    def test_05_bad_or_tiny_catalog_never_overwrites_cache_or_mass_alerts(self):
        """Empty, failed, malformed, or tiny catalog responses preserve good cache and never mass-alert."""
        cfg = model_registry.load_models_config()
        watched = notifications._watched_nim_models(models_cfg=cfg, role_overrides={})
        good_catalog = set(watched) | {f"extra/model-{i}" for i in range(12)}
        notifications.evaluate_nim_catalog_changes(good_catalog, models_cfg=cfg, role_overrides={})

        cache_file = model_registry.CATALOG_CACHE_PATH
        stale_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": stale_ts, "models": sorted(good_catalog)}, f)

        bad_responses = [
            _FakeHTTPResponse({"data": []}),
            _FakeHTTPResponse({"error": "bad gateway"}),
            _FakeHTTPResponse("not-valid-json"),
            _FakeHTTPResponse({"data": [{"id": "tiny/only-1"}, {"id": "tiny/only-2"}]}),
        ]
        for resp in bad_responses:
            with patch("urllib.request.urlopen", return_value=resp):
                cat = model_registry.get_catalog("nvapi-test", "https://integrate.api.nvidia.com/v1", cache_path=cache_file)
            self.assertEqual(cat, good_catalog)
            self.assertIsNotNone(model_registry.get_last_catalog_error())
            with open(cache_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(set(saved["models"]), good_catalog)

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            cat_err = model_registry.get_catalog("nvapi-test", "https://integrate.api.nvidia.com/v1", cache_path=cache_file)
        self.assertEqual(cat_err, good_catalog)

        # Tiny catalog passed directly to evaluator must not trigger mass removed alerts
        tiny_alerts = notifications.evaluate_nim_catalog_changes(
            {"tiny/only-1"}, models_cfg=cfg, role_overrides={}
        )
        self.assertEqual(tiny_alerts, [])
        all_mc = [n for n in notifications.list_notifications()["notifications"] if n["kind"] == "models_changed"]
        self.assertEqual(all_mc, [])

    def test_06_catalog_monitor_daily_cadence_local_key_and_disable_flag(self):
        """Monitor skips without local key or with MAGNM_DISABLE_SCHEDULER=1, and runs at most daily."""
        cfg = model_registry.load_models_config()
        watched = notifications._watched_nim_models(models_cfg=cfg, role_overrides={})
        healthy = set(watched) | {f"extra/m-{i}" for i in range(10)}

        calls = []

        def fake_get_catalog(**kwargs):
            calls.append(kwargs)
            return healthy

        # Disabled when MAGNM_DISABLE_SCHEDULER=1
        with patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "1", "NVIDIA_NIM_API_KEY": "nvapi-test-1111"}):
            self.assertIsNone(model_registry.start_catalog_monitor(initial_delay_s=0.01, poll_interval_s=1.0))
            self.assertIsNone(model_registry.check_catalog_once(now_ts=1000.0, catalog_fn=lambda: healthy))
        self.assertEqual(calls, [])

        # No local key -> skipped
        with patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "0"}, clear=False):
            os.environ.pop("NVIDIA_NIM_API_KEY", None)
            with patch.object(ai_gateway, "load_env", return_value={}):
                key_pool.reset()
                self.assertIsNone(model_registry.check_catalog_once(now_ts=1000.0, catalog_fn=lambda: healthy))

        # Local key present -> runs at most once per 24h
        with patch.dict(os.environ, {"MAGNM_DISABLE_SCHEDULER": "0", "NVIDIA_NIM_API_KEY": "nvapi-local-12345"}), \
             patch.object(ai_gateway, "load_env", return_value={}), \
             patch.object(model_registry, "get_catalog", side_effect=fake_get_catalog):
            key_pool.reset()
            res1 = model_registry.check_catalog_once(now_ts=10000.0)
            self.assertEqual(res1, [])
            self.assertEqual(len(calls), 1)

            # Same day -> skipped
            res2 = model_registry.check_catalog_once(now_ts=10000.0 + 3600.0)
            self.assertIsNone(res2)
            self.assertEqual(len(calls), 1)

            # Next day -> runs again
            res3 = model_registry.check_catalog_once(now_ts=10000.0 + 90000.0)
            self.assertEqual(res3, [])
            self.assertEqual(len(calls), 2)

            # Thread starts and stops cleanly
            t = model_registry.start_catalog_monitor(initial_delay_s=60.0, poll_interval_s=60.0)
            self.assertIsNotNone(t)
            self.assertTrue(t.is_alive())
            model_registry.stop_catalog_monitor(timeout=1.0)
            self.assertFalse(t.is_alive())

    def test_07_entrypoints_wire_catalog_monitor_start_and_stop(self):
        """Both engine/main.py and server.main() start and stop model_registry catalog monitor."""
        for rel in ("engine/main.py", "engine/server.py"):
            with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIn("start_catalog_monitor", src, rel)
            self.assertIn("stop_catalog_monitor", src, rel)

    def test_08_build_engine_discovers_all_engine_modules_and_subpackages(self):
        """build_engine._discover_hidden_imports finds every engine module and subpackage (excluding tests)."""
        build_engine = _load_script_module("magnm_build_engine", "build_engine.py")
        discovered = set(build_engine._discover_hidden_imports(ENGINE_DIR))

        expected = set()
        for root_dir, dirnames, filenames in os.walk(ENGINE_DIR):
            dirnames[:] = [d for d in dirnames if d not in ("tests", "__pycache__") and not d.startswith(".")]
            rel_dir = os.path.relpath(root_dir, ENGINE_DIR)
            prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, ".")
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                stem = os.path.splitext(fname)[0]
                if stem == "__init__":
                    if prefix:
                        expected.add(prefix)
                else:
                    expected.add(f"{prefix}.{stem}" if prefix else stem)

        missing = expected - discovered
        self.assertEqual(missing, set(), f"Missing engine modules in build_engine: {sorted(missing)}")
        for subpkg in ("posting", "posting.service", "social", "social.service", "research", "research.search", "tools", "tools.core"):
            self.assertIn(subpkg, discovered)
        self.assertFalse(any(m == "tests" or m.startswith("tests.") for m in discovered))

    def test_09_smoke_test_env_stripping_and_failure_exit(self):
        """smoke_test strips secrets from env and exits non-zero with failing check on bad binary."""
        smoke_test = _load_script_module("magnm_smoke_test", "smoke_test.py")
        with patch.dict(
            os.environ,
            {
                "NVIDIA_NIM_API_KEY": "nvapi-should-strip",
                "MAGNM_ENGINE_TOKEN": "tok-should-strip",
                "GITHUB_TOKEN": "ghp_should_strip",
                "OPENAI_API_KEY": "sk-should-strip",
            },
        ):
            clean = smoke_test._build_clean_env("/tmp/fake-smoke-dir")
        self.assertNotIn("NVIDIA_NIM_API_KEY", clean)
        self.assertNotIn("MAGNM_ENGINE_TOKEN", clean)
        self.assertNotIn("GITHUB_TOKEN", clean)
        self.assertNotIn("OPENAI_API_KEY", clean)
        self.assertEqual(clean["MAGNM_HOME"], "/tmp/fake-smoke-dir")
        self.assertEqual(clean["MAGNM_DISABLE_SCHEDULER"], "1")
        self.assertEqual(clean["MAGNM_DISABLE_CLOUD_SYNC"], "1")
        self.assertEqual(clean["PYTHON_KEYRING_BACKEND"], "keyring.backends.null.Keyring")

        err_buf = io.StringIO()
        with patch.object(sys, "stderr", err_buf):
            rc = smoke_test.main(["--engine-bin", os.path.join(self.tmpdir, "does-not-exist")])
        self.assertEqual(rc, 1)
        self.assertIn("FAILED check=setup", err_buf.getvalue())


if __name__ == "__main__":
    unittest.main()

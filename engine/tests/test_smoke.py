"""Smoke tests for the moved engine modules."""

import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_DIR = os.path.join(REPO_ROOT, "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

ENGINE_MODULES = [
    "agent_chat",
    "ai_gateway",
    "assets_manager",
    "cmo_runtime",
    "repo_sync",
    "research_scanner",
    "scheduler",
    "server",
    "team_room",
]


class EngineSmokeTests(unittest.TestCase):
    """Verify engine imports, ROOT resolution, and scheduler unit output."""

    def test_import_all_engine_modules(self):
        """Every engine module imports cleanly with engine/ on sys.path."""
        for name in ENGINE_MODULES:
            with self.subTest(module=name):
                mod = importlib.import_module(name)
                self.assertIsNotNone(mod)

    def test_cmo_runtime_root_equals_repo_root(self):
        """cmo_runtime.ROOT resolves to the repository root."""
        import cmo_runtime

        self.assertEqual(os.path.abspath(cmo_runtime.ROOT), os.path.abspath(REPO_ROOT))

    def test_scheduler_execstart_points_to_engine(self):
        """scheduler.generate_systemd_units writes ExecStart referencing /engine/cmo_runtime.py."""
        import scheduler

        sample_schedules = [
            {
                "id": "smoke_test",
                "name": "Smoke Test",
                "command": "daily_cycle",
                "interval_hours": 6,
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(scheduler, "SYSTEMD_DIR", tmpdir), patch.object(
                scheduler, "load_schedules", return_value=sample_schedules
            ), contextlib.redirect_stdout(io.StringIO()):
                scheduler.generate_systemd_units()

            service_path = os.path.join(tmpdir, "magnm-smoke_test.service")
            self.assertTrue(os.path.exists(service_path))
            with open(service_path, "r", encoding="utf-8") as f:
                content = f.read()

        exec_lines = [line for line in content.splitlines() if line.startswith("ExecStart=")]
        self.assertEqual(len(exec_lines), 1)
        self.assertIn("/engine/cmo_runtime.py", exec_lines[0])


if __name__ == "__main__":
    unittest.main()

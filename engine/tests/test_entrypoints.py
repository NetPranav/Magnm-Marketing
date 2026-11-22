"""engine/server.py must run what Phase 5 promises: due reminders and the Monday summary."""

from datetime import datetime, timezone
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import db
import scheduler_loop
import server


class _Listener:
    daemon_threads = True
    server_address = ("127.0.0.1", 5050)

    def __init__(self, *args):
        pass

    def serve_forever(self):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


class NoTimezoneDatabaseTests(unittest.TestCase):
    def test_engine_imports_without_a_timezone_database(self):
        """Windows has none: the packaged engine crashed on import there."""
        code = (
            "import zoneinfo\n"
            "def none(*a, **k): raise zoneinfo.ZoneInfoNotFoundError('no database')\n"
            "zoneinfo.ZoneInfo = none\n"
            "import sys; sys.path.insert(0, sys.argv[1]); import server, scheduler_loop, weekly_summary, workflows, reminders"
        )
        engine_dir = os.path.dirname(os.path.abspath(server.__file__))
        res = subprocess.run([sys.executable, "-c", code, engine_dir], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)


class ServerEntrypointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous = db.get_db_path()
        db.set_db_path(os.path.join(tmp.name, "magnm.db"))
        self.addCleanup(db.set_db_path, previous)
        self.addCleanup(db.reset_connections)
        db.init_db()

    def test_server_main_starts_and_stops_reminders_and_weekly_summary_only(self):
        with patch.object(server, "ThreadingHTTPServer", _Listener), \
                patch.object(server, "write_engine_endpoint"), \
                patch.object(server, "remove_engine_endpoint"), \
                patch.object(server.jobs, "start_worker"), \
                patch.object(server.phone_server, "start_if_enabled"), \
                patch.object(server.signal, "signal"), \
                patch("builtins.print"), \
                patch.object(scheduler_loop, "start") as start, \
                patch.object(scheduler_loop, "stop") as stop:
            server.main()
        start.assert_called_once_with(commands=("weekly_summary",))
        stop.assert_called_once()

    def test_limited_loop_never_enables_other_automatic_jobs(self):
        db.save_schedules([
            {"id": "cmo_daily", "command": "daily_cycle", "cron": "46 8 * * *", "enabled": True},
            {"id": "repo_sync", "command": "repo_sync", "interval_hours": 2, "enabled": True},
        ])
        only = ("weekly_summary",)
        scheduler_loop.ensure_default_schedules(commands=only)
        self.assertEqual({s["id"] for s in db.load_schedules()}, {"cmo_daily", "repo_sync", "weekly_summary"})
        with patch.object(scheduler_loop.reminders, "check_due_reminders") as due, \
                patch.object(scheduler_loop.jobs, "enqueue", return_value={}) as enqueue:
            scheduler_loop.tick(now=datetime(2026, 11, 24, 12, tzinfo=timezone.utc), commands=only)
        due.assert_called_once()
        self.assertEqual([c.args[0] for c in enqueue.call_args_list], ["weekly_summary"])


if __name__ == "__main__":
    unittest.main()

"""Engine test package.

Engines started by tests must never run scheduled jobs: those make real network calls
(GitHub, Hacker News, ...) and kept test runs hanging. Child processes inherit this.
"""
import os

os.environ["MAGNM_DISABLE_SCHEDULER"] = "1"

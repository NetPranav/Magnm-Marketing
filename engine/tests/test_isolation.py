"""Tests for the isolation that engine/tests/__init__.py sets up."""

import os
import socket
import sqlite3
import sys
import unittest

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.realpath(os.path.dirname(ENGINE))
sys.path.insert(0, ENGINE)

import paths


class IsolationTests(unittest.TestCase):
    def test_default_data_folder_is_not_the_repo(self):
        self.assertNotEqual(os.path.realpath(paths.DATA_ROOT), REPO)
        self.assertNotEqual(os.path.realpath(os.environ["MAGNM_HOME"]), REPO)

    def test_real_private_files_are_refused(self):
        for parts in ((".env",), ("state", "team_session.json")):
            with self.assertRaises(PermissionError):
                open(os.path.join(REPO, *parts), "r").close()
        real_db = os.path.join(REPO, "state", "magnm.db")
        for target in (real_db, f"file:{real_db}?mode=ro"):
            with self.assertRaises(PermissionError):
                sqlite3.connect(target, uri=target.startswith("file:"))

    def test_internet_is_refused(self):
        with self.assertRaises(socket.gaierror):
            socket.getaddrinfo("supabase.co", 443)
        with socket.socket() as sock, self.assertRaises(socket.gaierror):
            sock.connect(("192.0.2.1", 443))


if __name__ == "__main__":
    unittest.main()

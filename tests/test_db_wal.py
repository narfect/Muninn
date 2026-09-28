"""PRIORITY 2 (#2) — bounded write-ahead log.

``connect()`` caps ``wal_autocheckpoint`` to a sane page count while KEEPING WAL mode (so
the ``-wal`` file can't grow unbounded during a long-running demo), and a clean shutdown
runs ``PRAGMA wal_checkpoint(TRUNCATE)`` to leave the DB compact. Offline, temp SQLite file.
No schema change is asserted here beyond the pragmas.
"""
import os
import tempfile
import unittest

from backend.db import WAL_AUTOCHECKPOINT_PAGES, Repository, connect
from backend.models import Service


class TestWalPragmas(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "wal.db")

    def test_autocheckpoint_is_bounded_and_wal_is_kept(self):
        with connect(self.db) as conn:
            pages = conn.execute("PRAGMA wal_autocheckpoint;").fetchone()[0]
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(pages, WAL_AUTOCHECKPOINT_PAGES)   # not SQLite's 1000-page default
        self.assertLess(pages, 1000)
        self.assertEqual(mode.lower(), "wal")               # WAL itself stays on

    def test_checkpoint_truncates_without_error(self):
        repo = Repository(self.db)
        repo.upsert_service(Service(name="checkout-api", tier=1))  # give the WAL something
        repo.checkpoint()                                          # must not raise
        with connect(self.db) as conn:
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()

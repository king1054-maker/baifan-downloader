"""Review-only regression tests for the two improvements taken from 0.10.104.

These are meant to be applied on top of GitHub main (commit ba15b86), not
copied together with the rest of the local 0.10.104 tree.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from app import integrity
from app.api_server import ApiContext


class PausedCompletedTests(unittest.TestCase):
    def test_paused_task_with_full_bytes_is_verifiable(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "done.bin"
            f.write_bytes(b"content")
            ctx = ApiContext.__new__(ApiContext)
            ctx.rpc = Mock()
            ctx.rpc.call.return_value = {
                "status": "paused", "seeder": "false",
                "completedLength": "7", "totalLength": "7",
                "files": [{"path": str(f), "length": "7",
                           "completedLength": "7", "selected": "true"}],
                "dir": td,
            }
            ctx.verify_cache = {}
            ctx._lock = threading.Lock()
            self.assertTrue(ctx.verify_gid("paused-1")["ok"])

    def test_paused_incomplete_task_still_fails(self):
        ctx = ApiContext.__new__(ApiContext)
        ctx.rpc = Mock()
        ctx.rpc.call.return_value = {
            "status": "paused", "seeder": "false",
            "completedLength": "3", "totalLength": "7",
            "files": [], "dir": "",
        }
        ctx.verify_cache = {}
        ctx._lock = threading.Lock()
        self.assertFalse(ctx.verify_gid("paused-2")["ok"])


class MoovWarningTests(unittest.TestCase):
    def test_missing_moov_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample.mp4"
            p.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00")
            c = integrity.check_file(p, expected=p.stat().st_size)
            self.assertTrue(c.ok, c.problems)
            self.assertTrue(any("moov" in n for n in c.notes), c.notes)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for aria2 BT seeding and selected-file integrity checks.

No network, downloaded files, API tokens, or GUI are required.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app import integrity
from app.api_server import ApiContext, _Handler


class SelectedFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def file(self, name, contents, selected="true"):
        path = self.root / name
        path.write_bytes(contents)
        return {
            "path": str(path), "length": str(len(contents)),
            "completedLength": str(len(contents)), "selected": selected,
        }

    def test_unselected_missing_file_is_skipped(self):
        selected = self.file("downloaded.bin", b"ready")
        excluded = {
            "path": str(self.root / "ignored.bin"), "length": "200",
            "completedLength": "0", "selected": "false",
        }
        report = integrity.check_task([selected, excluded], str(self.root))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["file_count"], 1)
        self.assertEqual(report["skipped_count"], 1)

    def test_selected_short_file_still_fails(self):
        selected = self.file("short.bin", b"abcd")
        selected["length"] = "10"
        report = integrity.check_task([selected], str(self.root))
        self.assertFalse(report["ok"])
        self.assertEqual(report["bad_count"], 1)
        self.assertIn("未下载完", report["files"][0]["problems"][0])

    def test_missing_selected_field_defaults_to_selected(self):
        file = self.file("http-download.bin", b"abc")
        file.pop("selected")
        self.assertTrue(integrity.check_task([file], str(self.root))["ok"])

    def test_all_unselected_is_not_success(self):
        excluded = {
            "path": str(self.root / "ignored.bin"), "length": "500",
            "completedLength": "0", "selected": "false",
        }
        result = integrity.check_task([excluded], str(self.root))
        self.assertFalse(result["ok"])
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("error", result)

    def test_empty_file_list_is_not_success(self):
        self.assertFalse(integrity.check_task([], str(self.root))["ok"])


class ApiSeedingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        target = self.root / "movie.bin"
        target.write_bytes(b"content")
        self.file = {
            "path": str(target), "length": "7",
            "completedLength": "7", "selected": "true",
        }

    def context(self, status, seeder="false"):
        ctx = ApiContext.__new__(ApiContext)
        ctx.rpc = Mock()
        ctx.rpc.call.return_value = {
            "status": status, "seeder": seeder,
            "files": [self.file], "dir": str(self.root), "errorCode": "0",
        }
        ctx.verify_cache = {}
        ctx._lock = threading.Lock()
        return ctx

    def test_active_seeder_is_verifiable(self):
        ctx = self.context("active", "true")
        result = ctx.verify_gid("a")
        self.assertTrue(result["ok"], result)
        self.assertIn("a", ctx.verify_cache)
        self.assertIn("seeder", ctx.rpc.call.call_args.args[1][1])

    def test_active_nonseeder_remains_incomplete(self):
        ctx = self.context("active", "false")
        report = ctx.verify_gid("b")
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "not_ready")
        self.assertNotIn("b", ctx.verify_cache)

    def test_completed_http_download(self):
        self.assertTrue(self.context("complete").verify_gid("c")["ok"])



class ApiWaitTests(unittest.TestCase):
    def test_wait_returns_immediately_for_active_seeder(self):
        ctx = Mock()
        ctx.rpc.call.return_value = {
            "gid": "bt-1", "status": "active", "seeder": "true",
            "infoHash": "a" * 40, "totalLength": "7",
            "completedLength": "7", "dir": "/tmp", "files": [],
        }
        ctx.store.get.return_value = {}
        ctx.verify_gid.return_value = {"ok": True, "file_count": 1}
        handler = _Handler.__new__(_Handler)
        handler.server = SimpleNamespace(ctx=ctx)
        handler._send = Mock()
        handler._wait("bt-1", {"timeout": ["600"], "verify": ["1"]})
        result = handler._send.call_args.args[0]
        self.assertTrue(result["done"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["task"]["download_complete"])
        self.assertTrue(result["task"]["verify"]["ok"])
        ctx.verify_gid.assert_called_once_with("bt-1")


if __name__ == "__main__":
    unittest.main()

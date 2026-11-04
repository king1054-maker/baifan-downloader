"""动态改选纯控制流测试（不依赖 Qt）：暂停确认、成功恢复、失败回滚、暂停超时补偿。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.rpc import Aria2Error
from app.torrent_selection import change_selection


class FakeRpc:
    def __init__(self, status="paused", fail_change=False, pause_stuck=False):
        self.status = status
        self.fail_change = fail_change
        self.pause_stuck = pause_stuck
        self.calls = []
        self.files = [
            {"index": str(i), "path": "E:/dl/f%d.mkv" % i,
             "length": "1000", "completedLength": "0",
             "selected": "true"}
            for i in range(1, 11)
        ]

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "aria2.tellStatus":
            fields = params[1] if params and len(params) > 1 else []
            if fields and "files" not in fields:
                return {"status": self.status}
            return {"status": self.status, "seeder": "false",
                    "files": [dict(f) for f in self.files],
                    "dir": "E:/dl"}
        if method == "aria2.forcePause":
            if not self.pause_stuck:
                self.status = "paused"
            return "OK"
        if method == "aria2.changeOption":
            if self.fail_change:
                raise Aria2Error("RPC 拒绝")
            spec = params[1].get("select-file", "")
            wanted = set()
            for part in spec.split(","):
                if "-" in part:
                    a, b = part.split("-")
                    wanted.update(range(int(a), int(b) + 1))
                elif part:
                    wanted.add(int(part))
            for f in self.files:
                f["selected"] = "true" if int(f["index"]) in wanted else "false"
            return "OK"
        if method == "aria2.getFiles":
            return [dict(f) for f in self.files]
        if method == "aria2.unpause":
            self.status = "active"
            return "OK"
        raise AssertionError(method)


class ChangeSelectionTests(unittest.TestCase):
    def test_active_task_pauses_waits_changes_verifies_and_resumes(self):
        rpc = FakeRpc("active")
        res = change_selection(rpc, "g", "1-8")
        self.assertTrue(res.ok, res.error)
        methods = [m for m, _ in rpc.calls]
        self.assertIn("aria2.forcePause", methods)
        self.assertIn("aria2.changeOption", methods)
        self.assertIn("aria2.getFiles", methods)
        self.assertIn("aria2.unpause", methods)
        self.assertEqual(sum(f["selected"] == "true" for f in rpc.files), 8)

    def test_paused_task_changes_without_pausing_or_resuming(self):
        rpc = FakeRpc("paused")
        self.assertTrue(change_selection(rpc, "g", "1-9").ok)
        methods = [m for m, _ in rpc.calls]
        self.assertNotIn("aria2.forcePause", methods)
        self.assertNotIn("aria2.unpause", methods)

    def test_rpc_failure_restores_old_selection(self):
        rpc = FakeRpc("paused", fail_change=True)
        res = change_selection(rpc, "g", "1-8")
        self.assertFalse(res.ok)
        self.assertIn("失败", res.error)
        changes = [c for c in rpc.calls if c[0] == "aria2.changeOption"]
        self.assertGreaterEqual(len(changes), 2)
        self.assertTrue(any(c[1][1].get("select-file") == "1-10" for c in changes))

    def test_pause_timeout_compensates_by_resuming(self):
        rpc = FakeRpc("active", pause_stuck=True)
        with patch("app.torrent_selection.wait_status", return_value="active"):
            res = change_selection(rpc, "g", "1-8")
        self.assertFalse(res.ok)
        self.assertIn("没有确认暂停", res.error)
        methods = [m for m, _ in rpc.calls]
        self.assertIn("aria2.unpause", methods)
        self.assertNotIn("aria2.changeOption", methods)


if __name__ == "__main__":
    unittest.main()

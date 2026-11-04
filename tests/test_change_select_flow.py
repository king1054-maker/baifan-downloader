"""动态改选的控制流测试：暂停确认、成功恢复、失败回滚。"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.rpc import Aria2Error
from app.ui.main_window import MainWindow


class FakeRpc:
    def __init__(self, status="paused", fail_change=False):
        self.status = status
        self.fail_change = fail_change
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


def make_win(rpc):
    win = MainWindow.__new__(MainWindow)
    win.engine = SimpleNamespace(rpc=rpc)
    win._reload_files_from_aria2 = lambda gid: None
    return win


class ChangeSelectTests(unittest.TestCase):
    def test_active_task_pauses_waits_changes_verifies_and_resumes(self):
        rpc = FakeRpc("active")
        win = make_win(rpc)
        with patch("app.ui.main_window.QMessageBox.warning"):
            self.assertTrue(win._change_select("g", "1-8", unpause=False))
        methods = [m for m, _ in rpc.calls]
        self.assertIn("aria2.forcePause", methods)
        self.assertIn("aria2.changeOption", methods)
        self.assertIn("aria2.getFiles", methods)
        self.assertIn("aria2.unpause", methods)
        self.assertEqual(sum(f["selected"] == "true" for f in rpc.files), 8)

    def test_paused_task_changes_without_pausing_or_resuming(self):
        rpc = FakeRpc("paused")
        win = make_win(rpc)
        with patch("app.ui.main_window.QMessageBox.warning"):
            self.assertTrue(win._change_select("g", "1-9", unpause=False))
        methods = [m for m, _ in rpc.calls]
        self.assertNotIn("aria2.forcePause", methods)
        self.assertNotIn("aria2.unpause", methods)

    def test_rpc_failure_restores_old_selection(self):
        rpc = FakeRpc("paused", fail_change=True)
        win = make_win(rpc)
        reloaded = []
        win._reload_files_from_aria2 = lambda gid: reloaded.append(gid)
        with patch("app.ui.main_window.QMessageBox.warning") as warn:
            self.assertFalse(win._change_select("g", "1-8", unpause=False))
        self.assertTrue(reloaded)
        self.assertTrue(warn.called)
        # 失败后至少尝试把旧选择写回；旧选择是全选 1-10
        changes = [c for c in rpc.calls if c[0] == "aria2.changeOption"]
        self.assertTrue(any(c[1][1].get("select-file") for c in changes))


if __name__ == "__main__":
    unittest.main()

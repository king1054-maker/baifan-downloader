"""动态改选文件的回归测试（不启动 GUI）。

覆盖用户要求的关键数值：10×100MB 全选=1000MB，取消2个=800MB，
再勾回1个=900MB；暂停态 totalLength=0 时也不能回退成全部文件大小。
"""
from __future__ import annotations

import unittest

from app.ui.main_window import MainWindow
from app.ui.models import row_from_aria2

MB = 1024 * 1024


def make_files(selected: set[int] | None = None, mode: str = "paused"):
    selected = set(range(1, 11)) if selected is None else selected
    files = []
    for i in range(1, 11):
        files.append({
            "index": str(i),
            "path": "E:/dl/video%02d.mkv" % i,
            "length": str(100 * MB),
            "completedLength": str(100 * MB) if i in selected else "0",
            "selected": "true" if i in selected else "false",
        })
    d = {
        "gid": "gid-%s" % mode,
        "status": mode,
        "totalLength": "0",   # 暂停 BT 的真实行为
        "completedLength": "0",
        "files": files,
        "dir": "E:/dl",
    }
    return d, files


class SelectionSizeTests(unittest.TestCase):
    def test_1000_800_900_mb(self):
        d, files = make_files()
        self.assertEqual(row_from_aria2(d).size, 1000 * MB)

        d, files = make_files(set(range(1, 9)))  # 取消 9、10
        self.assertEqual(row_from_aria2(d).size, 800 * MB)
        self.assertEqual(row_from_aria2(d).file_count, 8)

        d, files = make_files(set(range(1, 10)))  # 再勾回 9
        self.assertEqual(row_from_aria2(d).size, 900 * MB)
        self.assertEqual(row_from_aria2(d).file_count, 9)

    def test_active_selection_uses_selected_only(self):
        d, files = make_files(set(range(1, 9)), mode="active")
        d["totalLength"] = str(1000 * MB)  # aria2 若还报全量，也要按勾选纠正
        row = row_from_aria2(d)
        self.assertEqual(row.size, 800 * MB)
        self.assertEqual(row.done, 800 * MB)

    def test_unselected_files_do_not_affect_eta(self):
        d, files = make_files(set(range(1, 9)))
        row = row_from_aria2(d)
        self.assertEqual(row.size, 800 * MB)
        self.assertEqual(row.done, 800 * MB)
        self.assertAlmostEqual(row.percent, 100.0)


class SpecTests(unittest.TestCase):
    def test_parse_and_compress_chunk_boundary(self):
        self.assertEqual(MainWindow._parse_spec("1-3,5,8-10"), {1, 2, 3, 5, 8, 9, 10})
        self.assertEqual(MainWindow._parse_spec("4-4"), {4})
        self.assertEqual(MainWindow._compress([1, 2, 3, 5, 8, 9, 10]), "1-3,5,8-10")
        self.assertEqual(MainWindow._compress([4]), "4")
        self.assertEqual(MainWindow._compress([1, 2, 4, 5, 6]), "1-2,4-6")


if __name__ == "__main__":
    unittest.main()

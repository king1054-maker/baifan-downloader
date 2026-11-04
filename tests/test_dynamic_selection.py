"""动态改选文件的纯逻辑回归测试（不依赖 Qt）。

覆盖 10×100MB 全选=1000MB，取消2个=800MB，再勾回1个=900MB；
以及分块边界解析。
"""
from __future__ import annotations

import unittest

from app.torrent_selection import compress_ids, parse_spec, selection_totals

MB = 1024 * 1024


def make_files(selected: set[int] | None = None):
    selected = set(range(1, 11)) if selected is None else selected
    return [{
        "index": str(i),
        "path": "E:/dl/video%02d.mkv" % i,
        "length": str(100 * MB),
        "completedLength": str(100 * MB) if i in selected else "0",
        "selected": "true" if i in selected else "false",
    } for i in range(1, 11)]


class SelectionSizeTests(unittest.TestCase):
    def test_1000_800_900_mb(self):
        count, total, done = selection_totals(make_files(), 1000 * MB, 1000 * MB)
        self.assertEqual((count, total, done), (10, 1000 * MB, 1000 * MB))

        count, total, done = selection_totals(make_files(set(range(1, 9))), 1000 * MB, 1000 * MB)
        self.assertEqual((count, total, done), (8, 800 * MB, 800 * MB))

        count, total, done = selection_totals(make_files(set(range(1, 10))), 1000 * MB, 1000 * MB)
        self.assertEqual((count, total, done), (9, 900 * MB, 900 * MB))

    def test_unselected_files_do_not_affect_totals(self):
        count, total, done = selection_totals(make_files(set(range(1, 9))))
        self.assertEqual(count, 8)
        self.assertEqual(total, 800 * MB)
        self.assertEqual(done, 800 * MB)


class SpecTests(unittest.TestCase):
    def test_parse_and_compress_chunk_boundary(self):
        self.assertEqual(parse_spec("1-3,5,8-10"), {1, 2, 3, 5, 8, 9, 10})
        self.assertEqual(parse_spec("4-4"), {4})
        self.assertEqual(compress_ids([1, 2, 3, 5, 8, 9, 10]), "1-3,5,8-10")
        self.assertEqual(compress_ids([4]), "4")
        self.assertEqual(compress_ids([1, 2, 4, 5, 6]), "1-2,4-6")


if __name__ == "__main__":
    unittest.main()

"""Qt 层的最小导入测试；Ubuntu 无 PySide6 时跳过，Windows CI 会实际执行。"""
from __future__ import annotations

import unittest

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from app.ui.main_window import MainWindow
except Exception:  # pragma: no cover - 仅用于无 Qt 的 Ubuntu 源检查
    MainWindow = None


@unittest.skipUnless(MainWindow is not None, "需要 PySide6/Qt（Windows CI 安装）")
class QtSelectionImportTests(unittest.TestCase):
    def test_spec_helpers_are_available(self):
        self.assertEqual(MainWindow._parse_spec("1-3,5"), {1, 2, 3, 5})
        self.assertEqual(MainWindow._compress([1, 2, 4]), "1-2,4")

    def test_main_window_has_async_worker_hook(self):
        self.assertTrue(hasattr(MainWindow, "_start_change_select"))


if __name__ == "__main__":
    unittest.main()

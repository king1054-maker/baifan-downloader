# -*- coding: utf-8 -*-
"""离屏出图：关于对话框。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from app import config  # noqa: E402
from app.engine import Aria2Engine  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    settings = config.load()
    engine = Aria2Engine(settings)
    engine.start()
    win = MainWindow(engine, settings)
    win.show()

    out = ROOT / "docs" / "about.png"

    def shoot():
        dlg = app.activeModalWidget()
        if isinstance(dlg, QDialog):
            dlg.grab().save(str(out))
            print("saved", out)
            dlg.accept()
        else:
            print("no dialog", dlg)
        QTimer.singleShot(300, app.quit)

    QTimer.singleShot(900, win._about)
    QTimer.singleShot(1900, shoot)
    code = app.exec()
    win.worker.stop()
    win.worker.wait(1500)
    engine.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())

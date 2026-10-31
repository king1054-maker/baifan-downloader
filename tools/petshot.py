# -*- coding: utf-8 -*-
"""桌宠三种状态与气泡面板出图。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui import theme  # noqa: E402
from app.ui.pet import PetWindow  # noqa: E402
from tools.shot import fake_rows  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    out = ROOT / "docs"
    out.mkdir(exist_ok=True)

    rows = fake_rows()
    stat = {"downloadSpeed": "19200000", "uploadSpeed": "2100000"}

    eating = PetWindow()
    eating.move(120, 120)
    eating.show()
    eating.update_data(rows, stat)
    eating._toast = ""
    eating._relayout()

    full = PetWindow()
    full.move(340, 120)
    full.show()
    full.update_data([r for r in rows if r.status_code == "complete"][:1], {})
    full._toast = ""
    full._relayout()

    idle = PetWindow()
    idle.move(560, 120)
    idle.show()
    idle.update_data([], {})
    idle._toast = ""
    idle._relayout()

    def step1():
        for pet, name in ((eating, "pet.png"), (full, "pet-full.png"), (idle, "pet-idle.png")):
            pet.repaint()
            pet.grab().save(str(out / name))
            print("saved", name)
        eating._toggle_panel()
        eating.panel.set_mode(0)
        eating.panel.refresh(rows, stat)
        eating.panel.repaint()
        QTimer.singleShot(250, step2)

    def step2():
        eating.panel.grab().save(str(out / "pet-panel-simple.png"))
        print("saved pet-panel-simple.png")
        eating.panel.set_mode(1)
        eating.panel.refresh(rows, stat)
        eating.panel.repaint()
        QTimer.singleShot(250, step3)

    def step3():
        eating.panel.grab().save(str(out / "pet-panel-detail.png"))
        print("saved pet-panel-detail.png")
        for p in (eating, full, idle):
            p.close()
        app.exit(0)

    QTimer.singleShot(700, step1)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

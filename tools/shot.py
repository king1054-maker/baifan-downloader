# -*- coding: utf-8 -*-
"""离屏出图：给界面做视觉验收用。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, paths  # noqa: E402
from app.engine import Aria2Engine  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.models import TaskRow  # noqa: E402


def fake_rows() -> list[TaskRow]:
    import time
    now = time.time()
    paths.DL_DIR.mkdir(parents=True, exist_ok=True)
    for n in ("Blender 4.5 LTS Windows x64.zip",
              "[Nekomoe kissaten] 药屋少女的呢喃 07 [1080p].mkv"):
        f = paths.DL_DIR / n
        if not f.exists():
            f.write_bytes(b"")
    return [
        TaskRow(gid="a1", name="Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.mkv",
                size=1_382_000_000, done=734_000_000, dl=11_400_000, ul=48_000,
                seeders=38, conns=64, status_code="active", dir=r"E:\Download\动画", added=now - 900,
                memo="《沙丘 2》4K 原盘 中字"),
        TaskRow(gid="a2", name="Ubuntu 24.04.1 Desktop amd64.iso",
                size=5_900_000_000, done=2_010_000_000, dl=7_800_000, ul=0,
                seeders=0, conns=16, status_code="active", dir=r"E:\Download", added=now - 300),
        TaskRow(gid="a3", name="半导体行业研究报告_2026Q3.pdf",
                size=24_600_000, done=9_100_000, dl=0, ul=0,
                seeders=0, conns=0, status_code="waiting", dir=r"E:\Download", added=now - 120),
        TaskRow(gid="a4", name="[VCB-Studio] 葬送的芙莉莲 01 [Ma10p_1080p].mkv",
                size=2_460_000_000, done=2_460_000_000, ul=2_100_000,
                seeders=12, conns=9, status_code="active", is_seeder=True, memo="芙莉莲 第 1 集 1080p",
                dir=r"E:\Download\动画", added=now - 7200),
        TaskRow(gid="a5", name="Blender 4.5 LTS Windows x64.zip",
                size=312_000_000, done=312_000_000, ul=0,
                seeders=0, conns=0, status_code="complete", dir=r"E:\Download",
                added=now - 86400, finished=now - 86000, verify_ok=True,
                memo="Blender 4.5 安装包（建模用）"),
        TaskRow(gid="a8", name="[Nekomoe kissaten] 药屋少女的呢喃 07 [1080p].mkv",
                size=1_100_000_000, done=1_100_000_000, ul=0,
                seeders=0, conns=0, status_code="complete", dir=r"E:\Download\动画",
                added=now - 4300, finished=now - 4200, verify_ok=False,
                memo="药屋少女 第 7 集（校验没过）"),
        TaskRow(gid="a6", name="[Nekomoe kissaten] 药屋少女的呢喃 07 [1080p].mkv",
                size=1_100_000_000, done=42_000_000, dl=0, ul=0,
                seeders=0, conns=0, status_code="paused", dir=r"E:\Download\动画", added=now - 3600),
        TaskRow(gid="a7", name="Fedora-Workstation-Live-x86_64-42.iso",
                size=2_100_000_000, done=8_000_000, ul=0,
                seeders=0, conns=0, status_code="error", error="连接超时",
                dir=r"E:\Download", added=now - 600),
    ]


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    settings = config.load()
    engine = Aria2Engine(settings)
    engine.start()

    win = MainWindow(engine, settings)
    win.resize(1360, 820)
    win.show()

    def shoot():
        try:
            win.worker.snapshot.disconnect()
        except TypeError:
            pass
        win.worker.stop()
        win.worker.wait(2000)
        rows = fake_rows()
        win.all_rows = rows
        win.search.setText("")
        win._apply_filter()
        win.table.selectRow(0)
        win.lb_state.setText("打饭中 3 碗 · 已开饭 3 碗 · 共 8 碗  ·  AI 接口 127.0.0.1:6811")
        win.lb_speed.setText("↓ 19.2 MB/s　↑ 2.1 MB/s")
        win.lb_conn.setText("连接 89")
        win.lb_limit.setText("限速 关")
        out = ROOT / "docs" / "ui-main.png"
        out.parent.mkdir(exist_ok=True)

        def snap_main():
            win.grab().save(str(out))
            print("saved", out)
            # 再来一张「正在写备注」的：让截图本身就说明怎么用
            from app.ui.models import COL_INDEX
            win.table.selectRow(0)
            win.table.setFocus()
            win.table.edit(win.model.index(0, COL_INDEX["memo"]))
            QTimer.singleShot(350, snap_memo)

        def snap_memo():
            out2 = ROOT / "docs" / "memo-edit.png"
            win.grab().save(str(out2))
            print("saved", out2)
            app.exit(0)

        QTimer.singleShot(400, snap_main)

    QTimer.singleShot(1200, shoot)
    code = app.exec()
    engine.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())


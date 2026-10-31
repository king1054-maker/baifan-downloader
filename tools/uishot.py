# -*- coding: utf-8 -*-
"""离屏渲染：选项-通知页、选文件对话框、完成气泡。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config  # noqa: E402
from app.notify import CompleteBubble  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.file_select import FileSelectDialog  # noqa: E402
from app.ui.options_dialog import OptionsDialog  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    settings = config.load()

    dlg = OptionsDialog(settings)
    dlg.tabs.setCurrentIndex(4)
    dlg.show()

    files = [
        {"index": 1, "path": r"E:/Download/葬送的芙莉莲/[VCB-Studio] Frieren 01 [Ma10p_1080p].mkv",
         "length": 2_460_000_000, "completedLength": 0, "selected": "true"},
        {"index": 2, "path": r"E:/Download/葬送的芙莉莲/[VCB-Studio] Frieren 02 [Ma10p_1080p].mkv",
         "length": 2_380_000_000, "completedLength": 0, "selected": "true"},
        {"index": 3, "path": r"E:/Download/葬送的芙莉莲/[VCB-Studio] Frieren 03 [Ma10p_1080p].mkv",
         "length": 2_410_000_000, "completedLength": 0, "selected": "false"},
        {"index": 4, "path": r"E:/Download/葬送的芙莉莲/CDs/OP.flac",
         "length": 42_000_000, "completedLength": 0, "selected": "false"},
        {"index": 5, "path": r"E:/Download/葬送的芙莉莲/readme.txt",
         "length": 3_200, "completedLength": 0, "selected": "false"},
    ]
    fd = FileSelectDialog(files, r"E:/Download", parent=None)
    fd.show()

    bubble = CompleteBubble("第 3 碗，开饭了！",
                            "Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HDR.mkv",
                            "吃干净了 · 1.29 GB")
    bubble.show()

    def snap():
        dlg.grab().save(str(out / "options-notify.png"))
        fd.grab().save(str(out / "file-select.png"))
        bubble.grab().save(str(out / "notify-bubble.png"))
        print("saved")
        app.exit(0)

    QTimer.singleShot(500, snap)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())


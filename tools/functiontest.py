# -*- coding: utf-8 -*-
"""端到端：拖入种子 + 粘贴磁力文本 + 打开方式枚举。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import config, intents, openwith, paths  # noqa: E402
from app.engine import Aria2Engine  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import QSS  # noqa: E402
from testselect import make_torrent  # noqa: E402


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    settings = config.load()
    eng = Aria2Engine(settings)
    eng.start()
    win = MainWindow(eng, settings)

    tp = ROOT / ".cache" / "demo.torrent"
    tp.parent.mkdir(exist_ok=True)
    tp.write_bytes(make_torrent())

    n = win.import_external([
        str(tp),
        "复制打开抖音 https://example.com/a.zip 看看",
        "magnet:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567&dn=Demo",
    ])
    print("imported:", n)
    time.sleep(1.5)
    snap = eng.rpc.tell_all()
    for bucket, items in snap.items():
        for it in items:
            print(" %-9s %-10s %s" % (bucket, it.get("status"),
                                      (it.get("bittorrent") or {}).get("info", {}).get("name")
                                      or it.get("files", [{}])[0].get("path", "")))
    print("tray:", win.tray is not None, "| notifier:", win.notifier is not None,
          "| pet:", win.pet is not None)
    f = paths.DL_DIR / "Blender 4.5 LTS Windows x64.zip"
    f.write_bytes(b"")
    print("handlers for .zip:", [(h.label, h.is_default) for h in openwith.handlers_for(f)][:5])
    print("handlers for .mkv:", [h.label for h in openwith.handlers_for("a.mkv")][:5])
    print("compress:", win._compress([1, 2, 3, 5, 8, 9, 10]))

    for bucket, items in snap.items():
        for it in items:
            gid = it.get("gid")
            try:
                eng.rpc.call("aria2.forceRemove", [gid])
            except Exception:
                pass
            try:
                eng.rpc.call("aria2.removeDownloadResult", [gid])
            except Exception:
                pass
    f.unlink(missing_ok=True)
    tp.unlink(missing_ok=True)
    win._really_quit = True
    win.close()
    print("done")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Capture real PySide6 UI widgets on a Windows CI runner without aria2.

These are *interface previews* populated with example data, not proof that
downloads or online AI search work. This module does not contact AI services,
download content, or access real user settings.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEST = ROOT / "docs" / "screenshots"

from PySide6.QtWidgets import QApplication
from app import background, config, title_lookup, title_parse
from app.engine import Aria2Engine
from app.ui import theme
from app.ui.title_lookup_dialog import TitleLookupDialog
from app.ui.options_dialog import OptionsDialog
from app.ui.main_window import MainWindow


def capture(widget, name: str) -> dict:
    """Capture actual widget pixels; reject empty/near-monochrome output."""
    DEST.mkdir(parents=True, exist_ok=True)
    widget.show()
    for _ in range(5):
        QApplication.processEvents()
    widget.repaint()
    im = widget.grab()
    if im.isNull():
        raise RuntimeError(f"Qt returned a null image for {name}")
    image = im.toImage()
    w, h = image.width(), image.height()
    if w < 400 or h < 300:
        raise RuntimeError(f"Abnormally small image {name}: {w}x{h}")
    colors = {image.pixelColor(x, y).rgba()
              for x in range(0, w, max(1, w // 24))
              for y in range(0, h, max(1, h // 20))}
    if len(colors) < 12:
        raise RuntimeError(f"Screenshot appears blank ({len(colors)} colors): {name}")
    path = DEST / name
    if not im.save(str(path), "PNG") or path.stat().st_size < 10000:
        raise RuntimeError(f"Screenshot not saved or implausibly small: {name}")
    result = {"file": name, "width": w, "height": h,
              "bytes": path.stat().st_size, "sample_colors": len(colors)}
    print("SCREENSHOT", json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> int:
    app = QApplication.instance() or QApplication(["BaiFan CI screenshots"])
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    settings = dict(config.DEFAULTS)
    settings.update({"show_pet": False, "autostart": False, "bg_timer_on": False,
                     "bg_random": False})
    results: list[dict] = []
    errors: dict[str, str] = {}
    windows = []

    # No network calls, no real AI key: all labels below are demonstrative.
    try:
        demo_name = "[Demo] Example.Series.S01E05.1080p.WEB-DL.mkv"
        parsed = title_parse.parse(demo_name)
        with patch.object(TitleLookupDialog, "_start_search", lambda self: None), \
             patch.object(title_lookup, "cached", lambda *a, **kw: None):
            d = TitleLookupDialog(None, parsed, raw_name=demo_name, settings=settings)
        windows.append(d)
        d.setFixedSize(700, 720)
        regular = SimpleNamespace(title="示例动画 第05集", original="Example Series",
                                  source="演示候选", url="")
        ai = SimpleNamespace(title="示例动画 · 第05集 [1080p]",
                             original="Example Series", source="超智能鲸鱼娘最佳", url="")
        d._on_done([regular])
        d._add_ai([ai])
        d.lb_status.setText("界面演示：以下为示例候选，未调用联网搜索")
        results.append(capture(d, "ai-rename.png"))
        d.close()
    except Exception:
        errors["ai-rename"] = traceback.format_exc()
        print(errors["ai-rename"], flush=True)

    try:
        # Render the real settings dialog twice: interface and pet customization.
        opts = OptionsDialog(settings)
        windows.append(opts)
        opts.resize(900, 740)
        opts.tabs.setCurrentIndex(5)
        results.append(capture(opts, "settings-background.png"))
        opts.tabs.setCurrentIndex(6)
        results.append(capture(opts, "settings-pet.png"))
        opts.close()
    except Exception:
        errors["settings"] = traceback.format_exc()
        print(errors["settings"], flush=True)

    try:
        # Main window with a fake in-memory task table. No aria2 executable is
        # present or launched; polling, API, tray and notifications are disabled.
        def noop(self, *a, **kw):
            return None
        methods = ("_start_polling", "_start_api", "_init_pet", "_init_tray",
                   "_schedule_tray_promote", "_init_notifier")
        with patch.multiple(MainWindow, **{m: noop for m in methods}):
            win = MainWindow(Aria2Engine(settings), settings)
        windows.append(win)
        win.resize(1360, 820)
        from tools.shot import fake_rows
        win.all_rows = fake_rows()
        win._apply_filter()
        win.lb_state.setText("界面预览 · 示例任务数据 · 未执行真实下载")
        results.append(capture(win, "main-window.png"))
        win.close()
    except Exception:
        errors["main-window"] = traceback.format_exc()
        print(errors["main-window"], flush=True)

    report = {"platform": sys.platform, "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
              "screenshots": results, "errors": errors,
              "disclaimer": "Actual PySide6 widgets, but demo tasks and candidate titles."}
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "capture-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("SUMMARY", json.dumps(report, ensure_ascii=False), flush=True)
    app.quit()
    required = {"ai-rename.png", "settings-background.png"}
    got = {r["file"] for r in results}
    if not required.issubset(got):
        print("FAIL Required UI captures did not complete.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

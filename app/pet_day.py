# -*- coding: utf-8 -*-
"""「今天下完了几碗」—— 桌宠用它决定要不要躺沙发。

为什么要落盘：桌宠原来只在内存里数（`_done_total`），程序一关就归零，
用户看到的就会是「刚才还躺着，重启又站起来了」。小 json 一句话就能记住。

过了零点自动归零（按本地日期比字符串），不需要定时器。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import paths

FILE = paths.DATA_DIR / "pet_day.json"


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def load() -> dict:
    """读今天的记录；文件坏了 / 是昨天的，都当成「今天 0 碗」。"""
    blank = {"date": _today(), "done": 0}
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return blank
    if not isinstance(d, dict) or d.get("date") != _today():
        return blank
    try:
        d["done"] = max(0, int(d.get("done", 0)))
    except (TypeError, ValueError):
        d["done"] = 0
    return d


def count() -> int:
    """今天已经下完几个。"""
    return load()["done"]


def bump(n: int = 1) -> int:
    """记一碗，返回记完之后今天的总数。写不进去也不抛，桌宠不该因此罢工。"""
    d = load()
    d["done"] += max(0, int(n))
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(FILE) + ".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(FILE)
    except OSError:
        pass
    return d["done"]

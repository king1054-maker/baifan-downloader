# -*- coding: utf-8 -*-
"""查重：这个是不是以前下过？

下载器的老毛病：一个月前下过的东西，昨天又贴一遍，它照下不误，
用户只能自己在硬盘里翻。这里给一条"以前下过，文件还在那儿"的提示。

认人的顺序（从最可靠到最松）：
  1. infohash（磁力/种子的唯一指纹）
  2. 原始链接完全一样
  3. 名字完全一样（兜底，只给有记录的已完成任务用）

只提示，不拦：用户说"再下一份"就继续下，不跟他犟。
"""
from __future__ import annotations

import base64
import re
import time

_BTIH = re.compile(r"btih:([0-9a-zA-Z]+)", re.I)


def magnet_hash(uri: str) -> str:
    """从磁力里取出 infohash（统一成 40 位小写 hex）。取不到返回空串。"""
    m = _BTIH.search(uri or "")
    if not m:
        return ""
    h = m.group(1)
    if len(h) == 32:                       # base32 形式
        try:
            return base64.b32decode(h.upper()).hex()
        except Exception:  # noqa: BLE001
            return h.lower()
    return h.lower()


def find_previous(metas: dict, ihash: str = "", uri: str = "",
                  name: str = "") -> tuple[str, dict] | None:
    """在历史记录里找同一个东西，返回 (gid, meta) 或 None。"""
    ih = (ihash or "").lower()
    for gid, meta in (metas or {}).items():
        if not isinstance(meta, dict):
            continue
        if ih and (meta.get("ihash") or "").lower() == ih:
            return gid, meta
        if uri and meta.get("uri") == uri:
            return gid, meta
    if name:
        for gid, meta in (metas or {}).items():
            if (meta.get("name") or "") == name and meta.get("finished_at"):
                return gid, meta
    return None


def describe(meta: dict) -> str:
    """一句话说明"以前那次是什么时候、文件在哪"。"""
    when = meta.get("finished_at") or meta.get("added_at") or 0
    day = time.strftime("%Y年%m月%d日", time.localtime(when)) if when else "以前"
    name = meta.get("name") or "（没记名字）"
    d = meta.get("dir") or ""
    path = ("%s\\%s" % (d, name)) if d else name
    return "%s 下载完成过：%s" % (day, path)

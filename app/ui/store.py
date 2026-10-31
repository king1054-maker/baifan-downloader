# -*- coding: utf-8 -*-
"""本地任务元数据（aria2 自身不存"添加时间"这类信息）。

data/tasks.json 结构：
    { "<gid>": {"added_at": 1750000000.0, "uri": "magnet:?...", "name": "...",
                "memo": "这是啥（用户自己写的备注）"} }
只做增量维护，不参与任务控制。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .. import paths

_MAX = 2000
MEMO_MAX = 120        # 备注只占一行，太长没意义；也避免把 tasks.json 撑胖


def normalize_memo(text: str) -> str:
    """备注统一成「一行、去首尾空白、最多 MEMO_MAX 字」。

    粘贴进来的换行/制表符会被压成空格：表格里只有一行，留着换行也显示不出来。
    """
    s = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    return s[:MEMO_MAX]


class TaskMetaStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else paths.TASKS_FILE
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        f = self._path
        if not f.exists():
            return
        try:
            with open(f, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict):
                self._data = {k: v for k, v in obj.items() if isinstance(v, dict)}
        except Exception:  # noqa: BLE001
            self._data = {}

    def save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        try:
            if len(self._data) > _MAX:
                keep = sorted(self._data.items(), key=lambda kv: kv[1].get("added_at", 0))[-_MAX:]
                self._data = dict(keep)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False)
            os.replace(tmp, self._path)
            self._dirty = False
        except OSError:
            pass

    def set_origin(self, gid: str, name: str, dir: str = "") -> None:
        """永远记住第一次见到的真实文件名；后续 AI/磁盘改名不能覆盖它。"""
        if not gid or not name:
            return
        rec = self._data.setdefault(gid, {})
        if not rec.get("orig_name"):
            rec["orig_name"] = name
            self._dirty = True
        if dir and not rec.get("orig_dir"):
            rec["orig_dir"] = dir
            self._dirty = True

    def get_origin(self, gid: str) -> str:
        rec = self._data.get(gid, {})
        return str(rec.get("orig_name") or rec.get("name") or "")

    def note(self, gid: str, uri: str = "", name: str = "",
             ihash: str = "", dir: str = "") -> None:
        if not gid:
            return
        rec = self._data.setdefault(gid, {})
        rec.setdefault("added_at", time.time())
        if uri:
            rec["uri"] = uri
        if name:
            rec["name"] = name
        if ihash:
            rec["ihash"] = ihash.lower()
        if dir:
            rec["dir"] = dir
        self._dirty = True

    def note_finished(self, gid: str, size: int = 0) -> float:
        rec = self._data.setdefault(gid, {})
        if not rec.get("finished_at"):
            rec["finished_at"] = time.time()
            self._dirty = True
        if size and not rec.get("size"):
            rec["size"] = int(size)
            self._dirty = True
        return float(rec["finished_at"])

    def set_size(self, gid: str, size: int) -> None:
        """记住任务总大小：暂停后的 BT 任务 aria2 会报 0，靠这份记录补回来。"""
        if not gid or int(size or 0) <= 0:
            return
        rec = self._data.setdefault(gid, {})
        if rec.get("size") != int(size):
            rec["size"] = int(size)
            self._dirty = True

    def get(self, gid: str) -> dict[str, Any]:
        return self._data.get(gid, {})

    # ---------- 备注 ----------
    def set_memo(self, gid: str, text: str) -> str:
        """写备注。返回真正存下来的文本（已规范化）。

        备注是「人话」，只存在本机 data/tasks.json，不碰 aria2，
        也不参与下载：任务删了备注跟着删，任务还在就一直留着。
        """
        if not gid:
            return ""
        val = normalize_memo(text)
        rec = self._data.setdefault(gid, {})
        rec.setdefault("added_at", time.time())
        if val:
            rec["memo"] = val
        else:
            rec.pop("memo", None)          # 清空就真删掉，别在 json 里留空串
        self._dirty = True
        return val

    def get_memo(self, gid: str) -> str:
        return str(self._data.get(gid, {}).get("memo") or "")

    # ---------- 自动核心名（只改列表显示，不动磁盘） ----------
    def set_title(self, gid: str, text: str) -> str:
        if not gid:
            return ""
        val = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())[:90]
        rec = self._data.setdefault(gid, {})
        rec.setdefault("added_at", time.time())
        if val:
            rec["title"] = val
        else:
            rec.pop("title", None)
        self._dirty = True
        return val

    def get_title(self, gid: str) -> str:
        return str(self._data.get(gid, {}).get("title") or "")

    def all(self) -> dict[str, dict[str, Any]]:
        return self._data

    def forget(self, gids: list[str]) -> None:
        for g in gids:
            if self._data.pop(g, None) is not None:
                self._dirty = True

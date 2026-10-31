# -*- coding: utf-8 -*-
"""后台轮询线程：把 aria2 状态搬到 UI 线程。

所有 RPC 调用都在这里发生，UI 线程永远不做网络请求。
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..rpc import Aria2Error, Aria2Rpc
from .models import TaskRow, row_from_aria2
from .store import TaskMetaStore


# 「文件还在不在」的缓存：每 8 秒才 stat 一次，别每轮轮询都去碰磁盘。
# 下完的任务才会查 —— 正在下的文件当然在，查了也没意义。
_MISSING_TTL = 8.0
_missing: dict[str, tuple[bool, float]] = {}


def file_gone(r: TaskRow) -> bool:
    """下完之后文件还在原处吗。

    用户常见的操作是"看完就删"，但删除不会通知下载器 ——
    任务永远停在"已完成"，看起来一切正常。这里补一次 stat，
    让界面能标出"文件其实已经没了"（比特彗星那个黄色感叹号）。
    """
    if not r.dir or not r.name:
        return False
    now = time.time()
    hit = _missing.get(r.gid)
    if hit and now - hit[1] < _MISSING_TTL:
        return hit[0]
    try:
        gone = not (Path(r.dir) / r.name).exists()
    except OSError:
        gone = False
    _missing[r.gid] = (gone, now)
    return gone


class PollWorker(QThread):
    snapshot = Signal(list, dict)   # rows, globalStat
    failed = Signal(str)

    def __init__(self, rpc: Aria2Rpc, store: TaskMetaStore, parent=None):
        super().__init__(parent)
        self.rpc = rpc
        self.store = store
        self._running = True
        self._fast = False
        self._wake = False

    def set_fast(self, fast: bool) -> None:
        """有活跃任务时提高刷新频率。"""
        self._fast = fast

    def poke(self) -> None:
        """外部动作后立刻刷新一次。"""
        self._wake = True

    def stop(self) -> None:
        self._running = False
        self._wake = True

    def run(self) -> None:  # noqa: D102
        while self._running:
            started = time.time()
            try:
                snap = self.rpc.tell_all()
                stat = self.rpc.global_stat()
                meta = self.store.all()
                rows: list[TaskRow] = []
                for bucket in ("active", "waiting", "stopped"):
                    for d in snap.get(bucket, []):
                        r = row_from_aria2(d, meta)
                        if r.is_finished:
                            r.missing = file_gone(r)
                        rows.append(r)
                self.snapshot.emit(rows, stat)
            except Aria2Error as exc:
                self.failed.emit(str(exc))
            except Exception as exc:  # noqa: BLE001
                self.failed.emit("刷新失败: %s" % exc)

            target = 0.45 if self._fast else 1.2
            spent = time.time() - started
            wait = max(0.1, target - spent)
            # 分段睡，poke 能立刻被响应
            deadline = time.time() + wait
            while self._running and not self._wake and time.time() < deadline:
                time.sleep(0.05)
            self._wake = False

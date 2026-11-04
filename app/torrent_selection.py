# -*- coding: utf-8 -*-
"""BT 文件选择的纯逻辑层（不依赖 Qt）。

主窗口、后台线程和 CI 的 Ubuntu 纯逻辑测试共用这一层：
- 解析/压缩 aria2 的 select-file 序号；
- 按 selected=true 统计文件大小/完成量；
- 动态改选的状态机：暂停确认 → changeOption → getFiles 复核 → 恢复。

这里没有 QMessageBox / QWidget，所有 RPC 都是同步的，调用方负责放到
工作线程（见 app/ui/workers.py 的 FileSelectWorker）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .rpc import Aria2Error


@dataclass
class ChangeResult:
    ok: bool
    error: str = ""
    status: str = ""
    files: list[dict] = field(default_factory=list)
    rollback_ok: bool | None = None


def is_selected(f: dict) -> bool:
    return str(f.get("selected", "true")).strip().lower() not in ("false", "0", "no")


def selected_files(files: list[dict]) -> list[dict]:
    return [f for f in files if is_selected(f)]


def selected_ids(files: list[dict]) -> set[int]:
    out: set[int] = set()
    for i, f in enumerate(files):
        if not is_selected(f):
            continue
        out.add(int(f.get("index") or (i + 1)))
    return out


def parse_spec(spec: str) -> set[int]:
    out: set[int] = set()
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            out.update(range(lo, hi + 1))
        else:
            try:
                out.add(int(part))
            except ValueError:
                pass
    return out


def compress_ids(ids: list[int]) -> str:
    ids = sorted(set(ids))
    out: list[str] = []
    i = 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        if j - i >= 1:
            out.append("%d-%d" % (ids[i], ids[j]))
        else:
            out.append(str(ids[i]))
        i = j + 1
    return ",".join(out)


def selection_totals(files: list[dict], fallback_total: int = 0,
                     fallback_done: int = 0) -> tuple[int, int, int]:
    """返回 (选中文件数, 选中总大小, 选中已完成量)。

    若 files 里没有 selected 字段（HTTP 任务/旧数据），按全部选中处理。
    """
    chosen = selected_files(files) if files else []
    if not chosen:
        return (0, int(fallback_total or 0), int(fallback_done or 0))
    total = sum(int(f.get("length") or 0) for f in chosen)
    done = sum(int(f.get("completedLength") or 0) for f in chosen)
    return (len(chosen), total, done)


def wait_status(rpc, gid: str, want: tuple[str, ...],
                timeout: float = 3.0, interval: float = 0.08) -> str:
    """等 aria2 进入目标状态。只允许在工作线程里调用。"""
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        try:
            st = rpc.call("aria2.tellStatus", [gid, ["status"]])
            last = str(st.get("status") or "")
        except Aria2Error:
            return last
        if last in want:
            return last
        time.sleep(interval)
    return last


def change_selection(rpc, gid: str, spec: str,
                     unpause: bool = False) -> ChangeResult:
    """改选文件并复核；不删除任务、不删除文件、不重建种子。

    暂停超时或 RPC 失败时必须尝试恢复原来的运行状态，并返回错误。
    """
    want = parse_spec(spec)
    if not want:
        return ChangeResult(False, "至少勾一个文件")

    try:
        snapshot = rpc.call("aria2.tellStatus",
                            [gid, ["files", "dir", "status", "seeder"]])
    except Aria2Error as exc:
        return ChangeResult(False, "读不到任务状态：%s" % exc)

    status = str(snapshot.get("status") or "")
    files = snapshot.get("files") or []
    if not files:
        return ChangeResult(False, "磁力任务还在取种子元数据，等文件列表出来再改。")
    if status == "complete":
        return ChangeResult(False, "这个任务已经完成，不能再改文件选择。")
    if status == "active" and str(snapshot.get("seeder") or "").lower() == "true":
        return ChangeResult(False, "任务已经下完并在做种，不能改文件选择。")

    old_spec = compress_ids(sorted(selected_ids(files)))
    was_active = status == "active"

    if was_active:
        try:
            rpc.call("aria2.forcePause", [gid])
        except Aria2Error as exc:
            return ChangeResult(False, "改选前暂停任务失败：%s" % exc)
        now = wait_status(rpc, gid, ("paused", "waiting"), 3.0)
        if now not in ("paused", "waiting"):
            # 补偿：forcePause 可能已经生效但状态回报迟滞，或任务卡在 active。
            # 无论如何不能把原本在跑的任务丢在暂停/半暂停状态。
            restored = False
            try:
                rpc.call("aria2.unpause", [gid])
                restored = wait_status(rpc, gid, ("active", "waiting"), 2.0) in (
                    "active", "waiting")
            except Aria2Error:
                pass
            return ChangeResult(
                False,
                "任务没有确认暂停（当前 %s），已%s恢复原运行状态" %
                (now, "" if restored else "尝试"),
                status=now)
    elif status not in ("paused", "waiting"):
        return ChangeResult(False, "当前任务状态不支持改选：%s" % status)

    try:
        rpc.call("aria2.changeOption", [gid, {"select-file": spec}])
        actual = rpc.call("aria2.getFiles", [gid]) or []
        if selected_ids(actual) != want:
            raise Aria2Error("aria2 没有接受新的 select-file（实际 %s）"
                             % compress_ids(sorted(selected_ids(actual))))
    except (Aria2Error, RuntimeError) as exc:
        rollback = True
        try:
            if old_spec:
                rpc.call("aria2.changeOption", [gid, {"select-file": old_spec}])
        except Aria2Error:
            rollback = False
        if was_active or unpause:
            try:
                rpc.call("aria2.unpause", [gid])
            except Aria2Error:
                rollback = False
        return ChangeResult(False, "改选失败，已恢复原选择：%s" % exc,
                            rollback_ok=rollback)

    if was_active or unpause:
        try:
            rpc.call("aria2.unpause", [gid])
        except Aria2Error as exc:
            return ChangeResult(False, "文件选择已改，但恢复任务失败：%s" % exc,
                                status=status)
    return ChangeResult(True, status=status)

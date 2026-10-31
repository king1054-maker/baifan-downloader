# -*- coding: utf-8 -*-
"""任务表的数据模型与格式化。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QPoint, QPointF, QRectF, Qt)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QStyledItemDelegate, QStyle,
    QStyleOptionViewItem,
)

from . import theme
from .store import MEMO_MAX, normalize_memo
from .. import brand, intents


# ---------- 格式化 ----------
def fmt_size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return ("%.0f %s" if i == 0 else "%.2f %s") % (n, units[i])


def fmt_speed(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "—"
    if v < 1:
        return "0 B/s"
    return fmt_size(v) + "/s"


def fmt_eta(done: float, total: float, speed: float) -> str:
    if speed <= 1 or total <= 0:
        return "—"
    left = total - done
    if left <= 0:
        return "—"
    sec = int(left / speed)
    if sec < 60:
        return "%d秒" % sec
    if sec < 3600:
        return "%d分%d秒" % (sec // 60, sec % 60)
    if sec < 86400:
        return "%d时%d分" % (sec // 3600, sec % 3600 // 60)
    return "%d天" % (sec // 86400)


def fmt_clock(ts: float) -> str:
    if not ts:
        return "—"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


# ---------- 行 ----------
@dataclass
class TaskRow:
    gid: str = ""
    name: str = ""
    size: int = 0
    done: int = 0
    dl: int = 0
    ul: int = 0
    seeders: int = 0
    conns: int = 0
    status_code: str = ""      # aria2 原始状态
    is_seeder: bool = False
    dir: str = ""
    added: float = 0.0
    error: str = ""
    is_bt: bool = False
    verify: bool = False
    ul_done: int = 0
    file_count: int = 1
    finished: float = 0.0
    verify_ok: bool | None = None
    memo: str = ""             # 用户自己写的一句备注（存在 tasks.json，不在 aria2）
    is_meta: bool = False      # 磁力链接「先下种子」那一步（.torrent 元数据），不是真文件
    missing: bool = False      # 下完了，但文件已经不在原处（用户删了/挪走了）
    title: str = ""            # 自动生成的"核心内容"显示名（只改列表，不动磁盘文件）
    file_paths: list[str] = field(default_factory=list)   # 种子内文件路径（起名用）
    uri: str = ""              # 原始链接/磁力（种子库留档、查重用）
    is_history: bool = False   # 从 tasks.json/磁盘补回来的已完成记录（不是 aria2 当前任务）

    @property
    def state(self) -> str:
        # 磁力任务第一步只是把种子元数据抓下来，几十 KB，一瞬间就"完成"。
        # 那一步不该喊"吃饱了"，也不该弹通知 —— 它连饭都还不是。
        if self.is_meta and not self.error:
            return "种子菜单"
        if self.error:
            return "错误"
        if self.status_code == "active":
            return "做种中" if self.is_seeder else "下载中"
        # 已经下完的任务被暂停（比如停掉做种）时，aria2 只报 paused；
        # 但本机 tasks.json 里记过 finished_at，仍然应该算「已完成」，
        # 不能掉进「放下筷子」——这是下完的文件，不是没下完。
        if self.finished and self.status_code in ("paused", "waiting"):
            return "已完成"
        if self.status_code == "waiting":
            return "等待中"
        if self.status_code == "paused":
            return "已暂停"
        if self.status_code == "complete":
            return "已完成"
        if self.status_code == "removed":
            return "已移除"
        return self.status_code or "—"

    @property
    def dot_kind(self) -> str:
        """状态灯是哪一种：down / done / seeding / error / missing / idle。

        顺序有讲究：错误最优先，其次"文件没了"，然后才看下载状态。
        下完但文件被删了的任务，状态还是 complete，光看状态是看不出来的。
        """
        if self.error:
            return "error"
        if self.missing and (self.status_code == "complete" or self.is_seeder):
            return "missing"
        if self.status_code == "active":
            return "seeding" if self.is_seeder else "down"
        if self.finished and self.status_code in ("paused", "waiting"):
            return "done"
        if self.status_code == "complete":
            return "done"
        if self.status_code in ("waiting", "paused"):
            return "idle"
        return "other"

    @property
    def dot_text(self) -> str:
        k = self.dot_kind
        if k == "error":
            return "出错了：%s" % (self.error or "未知原因")
        if k == "missing":
            return ("文件不在了 —— 下完之后被删掉/挪走了。\n"
                    "下载器这边还记着它，但磁盘上已经没有了。")
        if k == "down":
            return "正在下载"
        if k == "seeding":
            return "已经下完，正在上传（做种）"
        if k == "done":
            return "已下完，没在上传"
        if k == "idle":
            return "等待中 / 已暂停"
        return "状态未知"

    @property
    def is_finished(self) -> bool:
        """真的下完了吗。

        BT 任务下完之后还会做种，这段时间 aria2 报的仍然是 `active`，
        只是 `seeder=true`。所以「完成」不能只看 status_code == "complete" ——
        那要等做种结束（默认一小时）才算，通知和桌宠就都晚了一小时。
        """
        if self.is_meta:
            return False
        if self.finished:
            return True
        if self.status_code == "complete":
            return True
        return self.status_code == "active" and self.is_seeder

    @property
    def ratio(self) -> float:
        return (self.ul_done / self.size) if self.size else 0.0

    @property
    def display_name(self) -> str:
        """列表里显示的名字：自动核心名优先，没有就用原文件名。"""
        return self.title or self.name

    @property
    def percent(self) -> float:
        if self.size <= 0:
            return 100.0 if self.finished else 0.0
        return max(0.0, min(100.0, self.done * 100.0 / self.size))


def _basename(p: str) -> str:
    return os.path.basename(p.replace("\\", "/").rstrip("/")) or p


def target_complete(dir_path: str, name: str, expected: int,
                    file_count: int = 1) -> bool:
    """磁盘上的文件是不是已经达到种子声明的总大小。

    用来给"暂停后 aria2 报 totalLength=0"的任务做自愈：单文件比大小，
    多文件比目录里所有文件的和。只做本地 stat，不扫网络。
    """
    if not dir_path or not name or int(expected or 0) <= 0:
        return False
    p = Path(dir_path) / name
    try:
        if int(file_count or 1) <= 1:
            return p.is_file() and p.stat().st_size == int(expected)
        if p.is_dir():
            total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return total == int(expected)
    except OSError:
        pass
    return False


def matches_text(row: TaskRow, text: str) -> bool:
    """筛选框的判定：名称或备注里有这几个字就算命中（不区分大小写）。

    备注也参与搜索，是因为备注存在的意义就是「替看不懂的文件名说话」——
    搜「芙莉莲」应该搜得到那串日文名。
    """
    t = (text or "").strip().lower()
    if not t:
        return True
    return (t in row.name.lower() or t in row.memo.lower()
            or t in (row.title or "").lower())


def row_from_aria2(d: dict[str, Any], meta: dict[str, dict] | None = None) -> TaskRow:
    gid = d.get("gid", "")
    files = d.get("files") or []
    bt = d.get("bittorrent") or {}
    name = ""
    if bt.get("info", {}).get("name"):
        name = bt["info"]["name"]
    elif files:
        name = _basename(files[0].get("path", "")) or files[0].get("path", "")
    if not name:
        name = "（等待元数据）"

    err = ""
    if d.get("errorCode") and d["errorCode"] != "0":
        err = d.get("errorMessage") or ("错误码 %s" % d["errorCode"])

    m = (meta or {}).get(gid, {})
    finished_ts = float(m.get("finished_at") or 0)
    # 暂停中的 BT 任务 aria2 会报 totalLength=0；先用 files 的长度和，
    # 再退回 tasks.json 里记过的大小，别让"下完的文件"显示成 0 字节。
    total = int(d.get("totalLength") or 0)
    if total <= 0:
        total = sum(int(f.get("length") or 0) for f in files)
    if total <= 0:
        total = int(m.get("size") or 0)
    completed = int(d.get("completedLength") or 0)
    if finished_ts and total and completed <= 0:
        completed = total
    # 磁力链接的「先取种子」阶段：aria2 会先建一个只有元数据的小任务，
    # 下完 .torrent 后自动 create 出新任务，并把新 gid 写进 followedBy。
    # 这个任务从头到尾都不是用户想要的文件，界面上要跟真任务分开。
    #   * 取种子中：有 bittorrent 但还没有 info.name（元数据还没齐）
    #   * 取到了  ：followedBy 里已经挂上真正的下载任务
    is_meta = bool(d.get("followedBy")) or bool(bt) and not (bt.get("info") or {}).get("name")
    return TaskRow(
        gid=gid,
        name=name,
        size=total,
        done=completed,
        dl=int(d.get("downloadSpeed") or 0),
        ul=int(d.get("uploadSpeed") or 0),
        seeders=int(d.get("numSeeders") or 0),
        conns=int(d.get("connections") or 0),
        status_code=d.get("status", ""),
        is_seeder=(d.get("seeder") == "true"),
        dir=d.get("dir", ""),
        added=float(m.get("added_at") or 0),
        error=err,
        is_bt=bool(bt) or d.get("infoHash") is not None,
        verify=bool(d.get("verifyIntegrityPending") == "true"),
        ul_done=int(d.get("uploadLength") or 0),
        file_count=len(files) or 1,
        finished=finished_ts,
        memo=str(m.get("memo") or ""),
        is_meta=is_meta,
        title=str(m.get("title") or ""),
        file_paths=[str(f.get("path") or "") for f in files if f.get("path")],
        uri=str(m.get("uri") or ""),
    )


# ---------- 列 ----------
COLS = [
    ("dot", "", 28),            # 状态灯：圆点/三角，跟比特彗星那套对应
    ("name", "名称", 284),
    ("ai_name", "AI改名", 56),  # 联网查准确名字（只改显示名）
    ("size", "大小", 70),
    ("percent", "进度", 100),
    ("speed", "速度 ↓ / ↑", 176),
    ("peers", "种子/连接", 62),
    ("eta", "剩余时间", 76),
    ("status", "状态", 92),
    ("finished", "完成时间", 90),
    ("memo", "备注", 112),      # 刚好放下空备注时的灰色提示「＋ 双击这里写备注」
    ("act_dir", "打开文件夹", 84),
    ("act_open", "打开方式", 94),
    ("dir", "保存路径", 88),
    ("added", "添加时间", 70),
]
COL_INDEX = {key: i for i, (key, _, _) in enumerate(COLS)}
ACTION_KEYS = ("act_dir", "act_open")
ARROW_ZONE = 16     # 「打开方式」单元格右侧下拉箭头的点击宽度


class TaskTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[TaskRow] = []
        # 备注写完之后往哪儿存：由主窗口接上 TaskMetaStore.set_memo
        self.memo_sink = None

    # -- Qt 接口 --
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLS)

    def flags(self, index):  # noqa: N802
        f = super().flags(index)
        if index.isValid() and COLS[index.column()][0] == "memo":
            # 只有备注列能编辑；其它列双击是「打开文件」。
            f |= Qt.ItemFlag.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):  # noqa: N802
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        if COLS[index.column()][0] != "memo":
            return False
        row = self.row_at(index.row())
        if row is None:
            return False
        text = normalize_memo(value)
        if text == row.memo:
            return False
        row.memo = text
        self.dataChanged.emit(index, index,
                              [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole])
        if callable(self.memo_sink):
            self.memo_sink(row.gid, text)
        return True

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        r = self.rows[index.row()]
        key = COLS[index.column()][0]

        if key == "dot":
            if role == Qt.ItemDataRole.DisplayRole:
                return ""                      # 由 DotDelegate 画成圆点/三角
            if role == Qt.ItemDataRole.ToolTipRole:
                return r.dot_text
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
        if key == "ai_name":
            if role == Qt.ItemDataRole.DisplayRole:
                return ""
            if role == Qt.ItemDataRole.ToolTipRole:
                return "联网查准确名字（只改显示名，不动磁盘文件）"
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            return ""
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            if key == "name":
                return r.display_name
            if key == "size":
                return fmt_size(r.size)
            if key == "percent":
                return "%5.1f%%" % r.percent
            if key == "speed":
                # 下载、上传放一栏：分两列看的时候，上传那一列常年是 0，白占位置
                if r.status_code != "active":
                    return "—"
                if r.is_seeder:
                    return "↑ %s" % fmt_speed(r.ul)
                return "↓ %s  ↑ %s" % (fmt_speed(r.dl), fmt_speed(r.ul))
            if key == "peers":
                return "%d / %d" % (r.seeders, r.conns) if r.is_bt else "%d" % r.conns
            if key == "eta":
                return fmt_eta(r.done, r.size, r.dl) if r.status_code == "active" else "—"
            if key == "status":
                if r.error:
                    return brand.WORD_ERROR
                if r.verify_ok is False:
                    return brand.WORD_DONE + "（校验失败）"
                if r.status_code == "complete" and r.verify_ok is not None:
                    return brand.status_text("已完成", r.verify_ok)
                return brand.status_text(r.state)
            if key == "dir":
                return r.dir
            if key == "added":
                return fmt_clock(r.added)
            if key == "finished":
                return fmt_clock(r.finished) if r.finished else "—"
            if key == "memo":
                return r.memo
            if key in ACTION_KEYS:
                return ""          # 由 ActionDelegate 画成图标按钮
            return ""

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if key in ("size", "percent", "speed", "peers", "eta", "added",
                       "finished", "status"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if key == "status":
                if r.error or r.verify_ok is False:
                    return QColor(theme.RED)
                if r.state == "下载中":
                    return QColor(theme.ACCENT)
                if r.state in ("已完成", "做种中"):
                    return QColor(theme.GREEN)
                if r.state == "等待中":
                    return QColor(theme.AMBER)
                return QColor(theme.GREY)
            if key == "percent":
                return QColor(theme.TEXT_DIM)
            if key == "speed":
                return QColor(theme.TEXT) if (r.dl or r.ul) else QColor(theme.TEXT_DIM)
            if key in ("eta", "added", "finished", "dir", "size", "peers"):
                return QColor(theme.TEXT_DIM) if key != "size" else QColor(theme.TEXT)
            return None

        if role == Qt.ItemDataRole.FontRole and key == "name":
            f = QFont()
            f.setWeight(QFont.Weight.DemiBold)
            return f

        if role == Qt.ItemDataRole.ToolTipRole:
            if key == "name":
                if r.title and r.title != r.name:
                    return "自动核心名：%s\n原文件名：%s\nGID: %s" % (r.title, r.name, r.gid)
                return "%s\nGID: %s" % (r.name, r.gid)
            if key == "dir":
                return r.dir
            if key == "status":
                if r.error:
                    return "噎住了 = 出错了：%s" % r.error
                if r.verify_ok is False:
                    return ("校验没通过：文件可能没下完整、被移动或被删除。\n"
                            "点任务详情/自检查看具体原因。")
                plain = {
                    "下载中": "正在下载",
                    "做种中": "给鱼群分饭 = 已经下完，正在上传给别人",
                    "等待中": "排队中，还没轮到它",
                    "已暂停": "放下筷子 = 任务暂停了，点「开始」继续",
                    "已完成": "已经下完（没在上传）",
                    "已移除": "任务已经取消",
                }.get(r.state)
                return plain
            if key == "act_dir":
                return "在资源管理器中打开所在文件夹"
            if key == "act_open":
                return "用默认程序打开；右侧箭头可选其他程序"
            if key == "memo":
                if r.memo:
                    return "备注：%s\n双击这一格可以改；清空后回车就是删掉。" % r.memo
                return "双击这一格，写一句「这到底是什么」。\n文件名看不懂的时候特别有用；备注只存在本机。"
        return None

    # -- 刷新 --
    def set_rows(self, rows: list[TaskRow]) -> None:
        """行数不变时只发 dataChanged，保留滚动与选中。"""
        if len(rows) == len(self.rows):
            self.rows = rows
            if rows:
                self.dataChanged.emit(
                    self.index(0, 0), self.index(len(rows) - 1, len(COLS) - 1))
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def row_at(self, row: int) -> TaskRow | None:
        if 0 <= row < len(self.rows):
            return self.rows[row]
        return None


DOT_COLORS = {
    "down": theme.ACCENT,      # 下载中：蓝
    "done": theme.GREEN,       # 下完、没上传：绿
    "seeding": theme.GREEN,    # 下完、正在上传：绿 + 外圈
    "error": theme.RED,        # 出错：红
    "missing": "#EAB308",      # 文件没了：琥珀黄三角
    "idle": "#CBD5E1",         # 等待 / 暂停：灰
    "other": "#E5E7EB",
}


class DotDelegate(QStyledItemDelegate):
    """状态灯：一个圆点 / 一个黄色感叹号三角。

    为什么单独一列而不是给状态文字染色：一眼扫一列圆点比逐行读字快得多，
    这也是比特彗星那套的做法。文字状态仍然保留在后面。
    """

    def paint(self, painter, option, index):  # noqa: N802
        row = index.model().row_at(index.row())
        kind = row.dot_kind if row is not None else "other"
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect
        cx = rect.center().x()
        cy = rect.center().y()

        if kind == "missing":
            # 三角 + 感叹号：借用"文件失效"的通用符号
            size = 8.0
            path = QPainterPath()
            path.moveTo(cx, cy - size)
            path.lineTo(cx + size, cy + size * 0.85)
            path.lineTo(cx - size, cy + size * 0.85)
            path.closeSubpath()
            painter.setPen(QPen(QColor("#B45309"), 1.2))
            painter.setBrush(QColor(DOT_COLORS["missing"]))
            painter.drawPath(path)
            painter.setPen(QColor("#7C2D12"))
            f = QFont()
            f.setPointSizeF(6.5)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "!")
        else:
            color = QColor(DOT_COLORS.get(kind, DOT_COLORS["other"]))
            if kind == "seeding":
                # 外圈表示"还在往外吐"
                ring = QColor(color)
                ring.setAlpha(70)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ring)
                painter.drawEllipse(QPointF(rect.center()), 7.0, 7.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(rect.center()), 4.5, 4.5)
        painter.restore()


class ProgressDelegate(QStyledItemDelegate):
    """进度列：圆角细条 + 百分比文字。

    以前让 Fusion 的 QStyleOptionProgressBar 画，结果填充只剩底边一条蓝线，
    62% 和 5% 看起来一模一样；这里按 percent 自己画填充宽度。
    """

    def paint(self, painter, option, index):  # noqa: N802
        row = index.model().row_at(index.row())
        if row is None:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(option.rect.adjusted(8, 0, -64, 0))
        track.setHeight(8)
        track.moveTop(option.rect.center().y() - 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E4E8F0"))
        painter.drawRoundedRect(track, 4, 4)
        color = QColor(theme.ACCENT)
        if row.error:
            color = QColor(theme.RED)
        elif row.status_code == "paused":
            color = QColor(theme.GREY)
        elif row.percent >= 100:
            color = QColor(theme.GREEN)
        pct = max(0.0, min(100.0, row.percent))
        fill = track.width() * pct / 100.0
        if fill > 0:
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(track.x(), track.y(), fill, track.height()), 4, 4)

        painter.setPen(QColor(theme.TEXT_DIM))
        f = painter.font()
        f.setPointSizeF(8.2)
        painter.setFont(f)
        painter.drawText(
            option.rect.adjusted(0, 0, -10, 0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            "%.1f%%" % row.percent,
        )
        painter.restore()


class AIRenameDelegate(QStyledItemDelegate):
    """AI改名列：画一个橙色小星，不依赖 emoji 字体（避免白框/空档）。"""

    def paint(self, painter, option, index):  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = option.widget.style() if option.widget else None
        if style:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter,
                              option.widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = option.rect.center()
        cx, cy = c.x(), c.y()
        outer, inner = 8.0, 3.2
        path = QPolygonF()
        for i in range(8):
            import math
            a = math.radians(-90 + i * 45)
            r = outer if i % 2 == 0 else inner
            path.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
        color = QColor("#F97316") if option.state & QStyle.StateFlag.State_MouseOver \
            else QColor("#F59E0B")
        painter.setPen(QPen(color.darker(110), 1))
        painter.setBrush(color)
        painter.drawPolygon(path)
        painter.restore()


class MemoDelegate(QStyledItemDelegate):
    """备注列：双击（或点一下已选中的这一格）就地在行里写一句人话。

    文件名是「[VCB-Studio] Sousou no Frieren [08][Ma10p_2160p]」这种东西的时候，
    顺手写「芙莉莲 第 8 集」比改文件名安全，也不会动到做种用的原始文件名。
    """

    def createEditor(self, parent, option, index):  # noqa: N802
        ed = QLineEdit(parent)
        ed.setPlaceholderText("写一句这是什么，例如：芙莉莲 第 8 集 4K")
        ed.setMaxLength(MEMO_MAX)
        ed.setClearButtonEnabled(True)
        ed.setFrame(False)
        ed.setStyleSheet(
            "QLineEdit{background:%s;border:1px solid %s;border-radius:4px;"
            "padding:0 5px;selection-background-color:#C9D4FF;}"
            % (theme.BG, theme.ACCENT))
        return ed

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        editor.selectAll()          # 直接重打就整条替换，不用先删

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        editor.setGeometry(option.rect.adjusted(4, 4, -4, -4))

    def paint(self, painter, option, index):  # noqa: N802
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""               # 文字自己画，位置要压住左侧的竖线
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        inner = option.rect.adjusted(7, 0, -6, 0)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if text:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(inner.left(), int(inner.center().y() - 7), 2, 14, 1, 1)
            f = QFont(option.font)
            f.setPointSizeF(8.8)
            painter.setFont(f)
            painter.setPen(QColor(theme.TEXT))
            r = inner.adjusted(9, 0, 0, 0)
            fm = painter.fontMetrics()
            painter.drawText(
                r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, r.width())))
        elif hover or selected:
            # 空备注平时不打扰；鼠标停在这一行（或选中）才提示可以写。
            # 光写「＋ 备注」等于没说，直接把操作写出来：双击。
            f = QFont(option.font)
            f.setPointSizeF(8.5)
            f.setItalic(True)
            painter.setFont(f)
            painter.setPen(QColor("#AEB6C2"))
            painter.drawText(inner, int(Qt.AlignmentFlag.AlignLeft
                                        | Qt.AlignmentFlag.AlignVCenter),
                             "＋ 双击这里写备注")
        painter.restore()


class ActionDelegate(QStyledItemDelegate):
    """「打开文件夹 / 打开方式」两列：画成小胶囊按钮，右侧带下拉箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        st = QApplication.style()
        self.ic_folder = st.standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        self.ic_video = st.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self.ic_image = st.standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self.ic_audio = st.standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
        self.ic_file = st.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def _icon(self, row: TaskRow, key: str):
        if key == "act_dir":
            return self.ic_folder
        kind = intents.kind_of(row.name) if row.name else "other"
        if kind == "video":
            return self.ic_video
        if kind == "image":
            return self.ic_image
        if kind == "audio":
            return self.ic_audio
        return self.ic_file

    @staticmethod
    def _text(row: TaskRow, key: str) -> str:
        if key == "act_dir":
            return "打开"
        kind = intents.kind_of(row.name) if row.name else "other"
        if kind in ("video", "audio"):
            return "播放"
        if kind == "image":
            return "查看"
        return "打开"

    def paint(self, painter: QPainter, option, index):  # noqa: N802
        row = index.model().row_at(index.row())
        if row is None:
            return super().paint(painter, option, index)
        key = COLS[index.column()][0]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect.adjusted(6, 5, -6, -5)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E8EDFB") if hover else QColor("#F5F7FA"))
        painter.drawRoundedRect(rect, 6, 6)

        ready = row.status_code == "complete" or bool(row.dir)
        icon = self._icon(row, key)
        pm = icon.pixmap(15, 15)
        dpr = pm.devicePixelRatio() or 1.0
        ph = pm.height() / dpr
        x = rect.x() + 7
        painter.drawPixmap(QPoint(x, int(rect.center().y() - ph / 2 + 1)), pm)

        text = self._text(row, key)
        tx = x + 15 + 6
        right = rect.right() - (ARROW_ZONE if key == "act_open" else 7)
        f = QFont(option.font)
        f.setPointSizeF(8.6)
        painter.setFont(f)
        painter.setPen(QPen(QColor(theme.ACCENT if ready else theme.GREY)))
        fm = painter.fontMetrics()
        painter.drawText(
            int(tx), int(rect.center().y() + fm.ascent() / 2 - 1),
            fm.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(right - tx))))

        if key == "act_open":
            # 下拉箭头
            cx = rect.right() - 10
            cy = rect.center().y()
            painter.setPen(QPen(QColor(theme.TEXT_DIM), 1.4, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(int(cx - 3), int(cy - 1), int(cx), int(cy + 2))
            painter.drawLine(int(cx), int(cy + 2), int(cx + 3), int(cy - 1))
        painter.restore()

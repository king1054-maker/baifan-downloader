# -*- coding: utf-8 -*-
"""桌面宠物：白饭下载器的大肥鱼。

形象是自绘的蓝发女仆 Q 版（AI 出图）。姿势按"现在发生什么"来分，不是随机：

顺序是固定的，就按「一顿饭」的流程走，不随机：

  ciallo     待命               —— 没任务，或者刚下完那 12 秒过去了
  idle_bowl  等饭               —— 任务在排队 / 磁力还在取种子，饭还没端上来
  half       下载中             —— **整个下载过程就这一张**：半张脸凑近了盯着你
  tease      刚下完 12 秒       —— 指着你："你这吃白饭的蓝色大肥鱼。没吃饱喵！"
                                  12 秒一到就回待命（每天前 3 碗都是这样）
  idle_lying 吃撑了             —— 待命时，如果**今天已经下完 3 个以上**，就躺沙发上
  sassy      翻白眼 + 台词气泡   —— 正在下的名字命中通用编号/域名等结构时，当场调侃 8 秒
                                  （图是整张原画：气泡、字、情绪线都在画面里；
                                   纯玩笑，不评价你下什么，也不保存文件名）

贴边隐藏：
  拖到屏幕边缘松手 → 收成一条发色蓝边（告诉你「我在这儿」）；
  鼠标移到蓝边上 → 自动展开；展开后**至少留 EXPAND_GRACE 毫秒缓冲**再允许自动收。
  这个缓冲是用户点名要的：「从蓝边唤出它，到我把手移到头像上点下去」中间
  得有反应时间；以前鼠标一离开蓝边 700ms 就收回去，手还没到头像上就没了。
  缓冲过了、鼠标也确实不在身上，才重新收起来；拖离边缘 → 恢复悬浮，不再自动收。

全屏检测：前台窗口铺满整个屏幕（游戏 / 视频全屏）时，连那条蓝边也藏起来，
**连单击弹出的进度气泡一起藏**；离开全屏自己回来。

进度气泡：单击弹出，9 秒没人管自己收，鼠标移开后 3 秒收；拖去贴边时一起收。

交互：
  * 把下载链接拖到它头上        → 立刻在默认目录建任务
  * 双击                        → 读剪贴板里的链接，静默建任务
  * 单击                        → 弹出简洁进度气泡；
                                  再单击一次（不是双击速度）→ 收起气泡、呼出主窗口。
                                  以前第二下是切「详细」，没任务时两页内容一模一样，
                                  用户看着就是「又弹了一个一样的框」，所以改成呼主窗。
右键：打开主窗口 / 暂停全部 / 贴边 / 隐藏 / 退出整个程序。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import math
import os
import re
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QToolButton, QVBoxLayout, QWidget

from .. import brand, paths, pet_day, pet_skin
from . import theme
from .models import TaskRow, fmt_size, fmt_speed

SIZE = 168
SPRITE_BOX = 152
PANEL_W = 320

CIALLO = "ciallo"            # 待命：没任务（或刚下完那 12 秒过去了）
BOWL = "idle_bowl"            # 等饭：任务在排队 / 磁力还在取种子
HALF = "half"                 # 下载中：半张脸，整个下载过程就这一张
TEASE = "tease"               # 刚下完那 12 秒：指着你「没吃饱喵！」（整图带字）
LYING = "idle_lying"          # 吃撑了：待命时，今天已经下完 3 个以上
SASSY = "sassy"               # 名字踩中调侃词，凑过来翻个白眼（8 秒）

# 「今天下完几个」算吃撑。用户定的线：3 个及以上躺沙发。
STUFFED_BOWLS = 3

DONE_HOLD = 12.0              # 刚下完那张（没吃饱）保持秒数
SASSY_HOLD = 8.0              # 吐槽那张保持秒数（每个任务只闪一次）
IDLE_FADE = 0.45              # 姿势切换的淡入淡出时长

PANEL_AUTO_HIDE = 9000        # 气泡没人管，9 秒自动收
PANEL_HOVER_GRACE = 3000      # 鼠标刚移开，再给 3 秒

# 下载器彩蛋：只看文件名里的“编号结构/域名/视频后缀”等通用特征，
# 不保存文件名、不判断内容类型，也不维护任何人物或站点名单。
_CODE_SEP = re.compile(
    r"(?<![a-z0-9])([a-z]{2,5})[-_ ](\d{2,5})(?![a-z0-9])", re.I)
_CODE_COMPACT = re.compile(
    r"(?<![a-z0-9])([a-z]{3,5})(\d{3,5})(?![a-z0-9])", re.I)
_DOMAIN = re.compile(
    r"(?:[a-z0-9-]+\.)+(?:com|net|cc|org|xyz|tv|me|info|club|site|top|vip|app|link)(?![a-z])",
    re.I)
_VIDEO_EXT = re.compile(r"\.(?:mkv|mp4|avi|wmv|mov|ts|m2ts|flv|rmvb|webm)(?![a-z0-9])",
                        re.I)
_EPISODE = re.compile(
    r"(?:^|[\s._-])(?:s\d{1,2}e\d{1,3}|ep?\s*\d{1,4}|第\s*\d+\s*[集话話])", re.I)
_GROUP_TAG = re.compile(r"^\s*\[[^\]]{1,40}\]")

# 贴边隐藏
EDGE_SNAP = 26            # 离屏幕边缘多近算"贴上"
BAR_W = 7                 # 收起后那条蓝边的粗细
BAR_H = 96                # 收起后那条蓝边的长度
BAR_COLOR = "#2C4A96"     # 发色蓝
COLLAPSE_DELAY = 700      # 鼠标移开后多久收起（毫秒）
EXPAND_GRACE = 4000       # 从蓝边展开后至少留这么久再允许自动收回（毫秒）
                          # 用户原话「三五秒的缓冲」：唤出 → 手移到头像 → 点下去要来得及

# 全屏检测
FS_POLL = 1500            # 多久查一次前台是不是全屏（毫秒）
FS_COVER = 0.995          # 窗口要盖住显示器多少才算全屏


def _sprite_path(name: str) -> Path:
    """用户换过就用 data/pet 里那张，否则用内置的。"""
    return pet_skin.sprite_path(name)


def looks_spicy(*texts: str, size: int = 0) -> bool:
    """文件名里出现通用编号结构，再凑够 3 个辅助特征，就触发一次吐槽。

    编号结构是硬门槛，避免把普通番剧/电影误判；辅助特征只看域名、视频后缀、
    文件大小和有没有集数/字幕组标签。所有判断只在内存里做，不记录内容。
    """
    blob = " ".join(t for t in texts if t).lower()
    if not (_CODE_SEP.search(blob) or _CODE_COMPACT.search(blob)):
        return False
    score = 1
    if _DOMAIN.search(blob):
        score += 1
    if _VIDEO_EXT.search(blob):
        score += 1
    if int(size or 0) >= 300 * 1024 * 1024:
        score += 1
    first = (texts[0] if texts else "") or ""
    if not _EPISODE.search(blob) and not _GROUP_TAG.search(first):
        score += 1
    return score >= 3


_user32 = ctypes.windll.user32


def _window_is_fullscreen(hwnd: int) -> bool:
    """这个窗口是不是铺满了它所在的显示器。

    判据：窗口矩形和显示器的 rcMonitor 几乎重合（宽高都 >= 99.5%）。
    这样最大化的普通窗口（底下还留着任务栏）不会被误判成全屏。
    """
    if not hwnd:
        return False
    rect = wt.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False

    MONITOR_DEFAULTTONEAREST = 2

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                    ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    mon = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon or not _user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False

    mw = mi.rcMonitor.right - mi.rcMonitor.left
    mh = mi.rcMonitor.bottom - mi.rcMonitor.top
    ww = rect.right - rect.left
    wh = rect.bottom - rect.top
    if mw <= 0 or mh <= 0:
        return False
    return (ww >= mw * FS_COVER and wh >= mh * FS_COVER
            and abs(rect.left - mi.rcMonitor.left) <= 3
            and abs(rect.top - mi.rcMonitor.top) <= 3)


def _foreground_fullscreen(own_pid: int) -> bool:
    """前台窗口是不是全屏（游戏 / 视频全屏）。自己的窗口不算。"""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == own_pid:
        return False

    # 桌面 / 任务栏 / 输入法候选窗不算
    buf = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return False
    return _window_is_fullscreen(hwnd)


class PetWindow(QWidget):
    quick_add = Signal(str)
    show_main = Signal()
    toggle_all = Signal()
    quit_app = Signal()          # 右键「退出整个程序」→ 主窗口负责真正结束进程

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setToolTip("单击看干饭进度 · 双击下载剪贴板链接 · 把链接拖到我头上也行")

        self.rows: list[TaskRow] = []
        self.stat: dict = {}
        self.downloading = False
        self.error = False
        self.mood = CIALLO
        self._pix: dict[str, QPixmap] = {}
        self._drag_pos: QPoint | None = None
        self._press_pos: QPoint | None = None
        self._moved = False
        self._phase = 0.0
        self._toast = ""
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)

        self._seen_done: set[str] = set()
        self._done_until = 0.0           # 「刚下完」那张（没吃饱）保持到这个时刻
        self._sassy_until = 0.0          # 吐槽那张保持到这个时刻
        self._sassy_gid = ""             # 已经吐槽过的任务，同一个不闪第二次
        self._day_done = 0               # 今天下完几碗（缓存，别每帧读文件）
        self._day_ts = 0.0
        self._first_poll = True          # 还没拿到第一份任务快照

        # 姿势：mood 直接就是素材名，省掉一层映射
        self.mood = CIALLO
        self._prev_key = ""
        self._fade_at = 0.0

        # 贴边隐藏
        self._dock: str | None = None        # None / "left" / "right" / "top" / "bottom"
        self._collapsed = False
        self._dock_center = None
        self._expand_until = 0.0             # 展开后的缓冲截止时刻（这之前不许自动收）
        self._want_visible = True            # 用户主动隐藏时置 False，全屏结束后别硬把它弹出来
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._collapse)

        # 全屏检测：全屏时不打扰
        self._hidden_by_fullscreen = False
        self._fs_timer = QTimer(self)
        self._fs_timer.setInterval(FS_POLL)
        self._fs_timer.timeout.connect(self._watch_fullscreen)

        self.panel = PetPanel(self)
        self.panel.hide()
        self._relayout()

        self._anim = QTimer(self)
        self._anim.setInterval(50)
        self._anim.timeout.connect(self._tick)
        self._anim.start()

        self._place_bottom_right()
        self._fs_timer.start()

    # ---------- 布局 ----------
    def _relayout(self) -> None:
        # 顶部永远留出气泡的位置，避免出气泡时桌宠整体跳动
        if self._collapsed:
            return
        self.setFixedSize(SIZE, SIZE + 28)

    def _place_bottom_right(self) -> None:
        scr = QApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - SIZE - 36, g.bottom() - self.height() - 36)

    def _visible_screen(self):
        """优先用窗口中心所在屏幕；窗口已经跑出屏幕时退到鼠标/主屏。"""
        scr = QApplication.screenAt(self.frameGeometry().center())
        if scr is None:
            scr = QApplication.screenAt(QCursor.pos())
        if scr is None:
            scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry() if scr else None

    def ensure_visible(self) -> None:
        """把窗口夹回某块屏幕的可见区域，召唤/换屏后都用它兜底。"""
        g = self._visible_screen()
        if g is None:
            return
        fg = self.frameGeometry()
        margin = 8
        x = min(max(fg.left(), g.left() + margin),
                max(g.left() + margin, g.right() - fg.width() - margin))
        y = min(max(fg.top(), g.top() + margin),
                max(g.top() + margin, g.bottom() - fg.height() - margin))
        if x != fg.left() or y != fg.top():
            self.move(x, y)

    # ---------- 贴边隐藏 ----------
    def _screen_geo(self):
        scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry() if scr else None

    def _dock_candidate(self) -> str | None:
        """当前贴着哪条边（离得够近才算）。"""
        g = self._screen_geo()
        if not g:
            return None
        fg = self.frameGeometry()
        if fg.left() - g.left() <= EDGE_SNAP:
            return "left"
        if g.right() - fg.right() <= EDGE_SNAP:
            return "right"
        if fg.top() - g.top() <= EDGE_SNAP:
            return "top"
        if g.bottom() - fg.bottom() <= EDGE_SNAP:
            return "bottom"
        return None

    def _dock_half(self) -> int:
        """收起状态下沿边方向的一半长度（用来算中心点）。"""
        return BAR_H // 2

    def _collapse(self, force: bool = False) -> None:
        """收成一条发色蓝边。

        force=True 只给「明确动作」用（右键「收成一条边」/ 拖到边上松手）：
        那两种是用户主动要收，不该被刚展开的缓冲拦住。
        普通自动收则要同时满足：缓冲期已过、鼠标不在身上、气泡没开着。
        """
        if self._collapsed or not self._dock:
            return
        if self.panel.isVisible() and not force:
            return
        if not force:
            # 刚展开的几秒是留给用户反应/移动鼠标的，别一移开就收
            if time.time() < self._expand_until:
                self._schedule_collapse()
                return
            if self._cursor_on_pet():
                self._collapse_timer.start(COLLAPSE_DELAY)
                return
        # 收边之前先把自己的气泡收掉，否则白框会孤零零挂在屏幕边上
        self._hide_panel()
        self._collapsed = True
        self._toast = ""
        self._toast_timer.stop()
        g = self._screen_geo()
        fg = self.frameGeometry()
        self._dock_center = fg.center()
        cx, cy = self._dock_center.x(), self._dock_center.y()
        if self._dock in ("left", "right"):
            self.setFixedSize(BAR_W, BAR_H)
            x = g.left() if self._dock == "left" else g.right() - BAR_W + 1
            y = max(g.top(), min(g.bottom() - BAR_H, cy - BAR_H // 2))
            self.move(x, y)
        else:
            self.setFixedSize(BAR_H, BAR_W)
            x = max(g.left(), min(g.right() - BAR_H, cx - BAR_H // 2))
            y = g.top() if self._dock == "top" else g.bottom() - BAR_W + 1
            self.move(x, y)
        self.update()

    def _schedule_collapse(self) -> None:
        """按「缓冲剩余时间」排一次自动收边，而不是固定 700ms 后一刀切。"""
        if not self._dock or self._collapsed or self.panel.isVisible():
            return
        remain = (self._expand_until - time.time()) * 1000.0
        self._collapse_timer.start(int(max(COLLAPSE_DELAY, remain)))

    def _cursor_on_pet(self) -> bool:
        """鼠标是不是还搭在身上。

        用全局坐标兜底：窗口在鼠标底下移动/缩放时 underMouse() 偶尔会漏，
        只看它会误判成"已经移开"，然后当着用户的面把桌宠收走。
        """
        return self.underMouse() or self.frameGeometry().contains(QCursor.pos())

    def _expand(self) -> None:
        """从蓝边还原成正常大小，贴回原来那条边。"""
        if not self._collapsed:
            return
        self._collapse_timer.stop()
        self._collapsed = False
        self._expand_until = time.time() + EXPAND_GRACE / 1000.0
        g = self._screen_geo()
        self._relayout()
        w, h = self.width(), self.height()
        c = getattr(self, "_dock_center", None)
        if c is None:
            self._place_bottom_right()
            self.update()
            return
        if self._dock == "left":
            self.move(g.left() + 2, max(g.top(), min(g.bottom() - h, c.y() - h // 2)))
        elif self._dock == "right":
            self.move(g.right() - w + 1, max(g.top(), min(g.bottom() - h, c.y() - h // 2)))
        elif self._dock == "top":
            self.move(max(g.left(), min(g.right() - w, c.x() - w // 2)), g.top() + 2)
        elif self._dock == "bottom":
            self.move(max(g.left(), min(g.right() - w, c.x() - w // 2)), g.bottom() - h + 1)
        self.update()

    def _undock(self) -> None:
        self._dock = None
        self._collapsed = False
        self._expand_until = 0.0
        self._collapse_timer.stop()
        self._relayout()

    def _maybe_dock(self) -> None:
        """松手之后判断要不要贴边。用户刚拖到边上，是明确动作，强制收。"""
        edge = self._dock_candidate()
        if edge:
            self._dock = edge
            self._collapse(force=True)
        else:
            self._undock()

    # ---------- 全屏检测 ----------
    def _watch_fullscreen(self) -> None:
        fs = _foreground_fullscreen(os.getpid())
        if fs and not self._hidden_by_fullscreen:
            self._hidden_by_fullscreen = True
            self._collapse_timer.stop()
            # 气泡也得一起藏。只藏桌宠不藏白框，等于没藏。
            self._hide_panel()
            super().hide()
        elif not fs and self._hidden_by_fullscreen:
            self._hidden_by_fullscreen = False
            if self._want_visible:
                self.show()

    def _hide_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        # 面板关了就把全局监听撤掉：别让一堆已销毁的桌宠继续收 App 级事件
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _watch_outside(self, on: bool) -> None:
        """只在面板开着时监听全局点击；面板一关就撤，避免销毁后的窗口还收事件。"""
        app = QApplication.instance()
        if app is None:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def _panel_outside_pressed(self, gp: QPoint) -> bool:
        """面板开着时点了面板和桌宠以外的地方 → 立刻收面板。

        返回 True 表示这次点击被当成"点空白处"处理了。
        桌宠本身不跟着消失：只收白框，贴边收起仍按缓冲那套走。
        """
        if not self.panel.isVisible():
            return False
        if self.panel.frameGeometry().contains(gp) or self.frameGeometry().contains(gp):
            return False
        self._panel_closed()
        return True

    def eventFilter(self, obj, event):  # noqa: N802
        try:
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and self.panel.isVisible():
                try:
                    gp = event.globalPosition().toPoint()
                except AttributeError:      # 不是 QMouseEvent（例如 QEnterEvent）
                    gp = QCursor.pos()
                self._panel_outside_pressed(gp)
            elif et == QEvent.Type.ApplicationDeactivate and self.panel.isVisible():
                # 点到别的程序/桌面：应用失活，同样把白框收掉
                self._panel_closed()
        except RuntimeError:
            # 窗口/面板已经被 Qt 销毁，别再碰
            return False
        return super().eventFilter(obj, event)

    def _panel_closed(self) -> None:
        """气泡自己收起来了（自动超时 / 用户按 Esc / 点了别处）。"""
        self._hide_panel()
        self._schedule_collapse()

    # ---------- 数据 ----------
    def update_data(self, rows: list[TaskRow], stat: dict) -> None:
        self.rows = rows
        self.stat = stat or {}
        # 「在做种」不算在下载 —— BT 下完之后还会挂一小时做种，
        # 这段时间任务仍是 active，把它当"还在下"就会一直摆着没吃饱的脸。
        active = [r for r in rows
                  if r.status_code == "active" and not r.is_seeder and not r.is_meta]
        self.downloading = bool(active)
        self.error = any(r.error for r in rows)

        for r in rows:
            # 磁力「先下种子」那一步不算一碗饭
            if r.is_meta:
                continue
            if self._first_poll:
                # 启动后的第一次快照：列表里已经下完的（含还在做种的）都不是"刚刚下完"，
                # 记下来别再庆祝，否则一重启就把今天下过的全补数一遍。
                if r.is_finished:
                    self._seen_done.add(r.gid)
                continue
            if r.is_finished and r.gid not in self._seen_done:
                self._seen_done.add(r.gid)
                self._celebrate(r)
        self._first_poll = False

        self._maybe_sassy()
        self._pick_mood()
        if self.panel.isVisible():
            self.panel.refresh(rows, self.stat)
        self.update()

    def silence(self, gid: str) -> None:
        """把一个任务记成"已经看过"：重新分饭时新挂的任务不要庆祝、不要加碗数。"""
        if gid:
            self._seen_done.add(gid)

    def _celebrate(self, r: TaskRow) -> None:
        """一碗下完：这张「没吃饱喵」挂 12 秒，之后回待命。"""
        self._done_until = time.time() + DONE_HOLD
        try:
            total = pet_day.bump(1)          # 记进"今天第几碗"，重启也认
        except Exception:                    # noqa: BLE001
            total = 0
        self.toast("第 %d 碗下完了，没吃饱喵！" % total if total else "下完了，没吃饱喵！")

    def _bowls(self) -> int:
        """现在桌上有几碗在吃（真正在下的任务数）。做种、取种子都不算。"""
        return sum(1 for r in self.rows
                   if r.status_code == "active" and not r.is_seeder and not r.is_meta)

    def _waiting(self) -> bool:
        """饭还在路上：有排队 / 暂停的，或者磁力还在取种子。"""
        for r in self.rows:
            if r.is_meta and not r.error:
                return True
            if r.status_code in ("waiting", "paused") and not r.finished:
                return True
        return False

    def _maybe_sassy(self) -> None:
        """名字踩词的调侃：每个任务只闪一次，8 秒就收回半张脸。"""
        if self._sassy_gid:
            return
        for r in self.rows:
            if r.status_code not in ("active", "waiting", "paused") or r.is_meta:
                continue
            if looks_spicy(r.name, r.dir, r.uri, size=r.size):
                self._sassy_gid = r.gid
                self._sassy_until = time.time() + SASSY_HOLD
                return

    def _day_count(self) -> int:
        """今天下完几碗。文件读太勤没必要，3 秒缓存一次（也能顺带跨过零点）。"""
        now = time.time()
        if now - self._day_ts > 3.0:
            self._day_ts = now
            try:
                self._day_done = pet_day.count()
            except Exception:            # noqa: BLE001
                pass
        return self._day_done

    def _pick_mood(self) -> None:
        """定这一帧画哪张图。顺序就是一顿饭的流程。"""
        old = self.mood
        now = time.time()
        if self.downloading:
            # 下载全程都是这张半张脸；只有撞词那 8 秒格外插一下
            if now < self._sassy_until:
                self.mood = SASSY
            else:
                self.mood = HALF
        elif now < self._done_until:
            # 刚端下桌的 12 秒：指着你说没吃饱
            self.mood = TEASE
        elif self._waiting():
            # 饭还没上来：端着碗等
            self.mood = BOWL
        elif self._day_count() >= STUFFED_BOWLS:
            # 今天已经干完 3 碗了，待命时就躺着 —— 吃撑了
            self.mood = LYING
        else:
            self.mood = CIALLO
        if self.mood != old:
            self._prev_key = old
            self._fade_at = time.time()

    def _sprite_name(self) -> str:
        """mood 本身就是素材名，直接画。"""
        return self.mood

    def _tick(self) -> None:
        self._phase += 0.08
        # 每一帧都重算一次 —— "刚下完 12 秒" 和 "吐槽 8 秒" 都是到点就该换的，
        # 不能等下一次轮询数据才换（那样任务下完后会一直挂着没吃饱的脸）。
        self._pick_mood()
        fading = self._prev_key and (time.time() - self._fade_at) < IDLE_FADE
        if fading:
            self.update()
            return
        if self._prev_key:
            self._prev_key = ""      # 淡出结束，之后就不用再画旧图了
        if self.downloading or self.mood != CIALLO or self._toast:
            self.update()
        elif int(self._phase * 10) % 6 == 0:
            self.update()          # 待机也慢慢呼吸

    def _clear_toast(self) -> None:
        self._toast = ""
        self._relayout()
        self.update()

    def toast(self, text: str) -> None:
        self._toast = text
        self._toast_timer.start(2800)
        self._relayout()
        self.update()

    # ---------- 素材 ----------
    def reload_skins(self) -> None:
        """用户在设置里换了图：扔掉缓存，立刻按新图重画。"""
        self._pix.clear()
        self._prev_key = ""
        self.update()

    def _pixmap(self, name: str) -> QPixmap:
        pm = self._pix.get(name)
        if pm is None:
            path = _sprite_path(name)
            pm = QPixmap(str(path)) if path.exists() else QPixmap()
            if not pm.isNull():
                dpr = self.devicePixelRatioF()
                pm = pm.scaled(int(SPRITE_BOX * dpr), int(SPRITE_BOX * dpr),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
            self._pix[name] = pm
        return pm

    # ---------- 绘制 ----------
    def _paint_bar(self, p: QPainter) -> None:
        """收起状态：一条很浅的发色蓝边，意思是「我在这儿」。"""
        w, h = self.width(), self.height()
        vertical = h > w
        radius = min(w, h) / 2.0
        if vertical:
            box = QRectF(0.0, 0.0, w, h)
            hi = QRectF(w * 0.22, 2, w * 0.56, h - 4)
        else:
            box = QRectF(0.0, 0.0, w, h)
            hi = QRectF(2, h * 0.22, w - 4, h * 0.56)
        p.setPen(Qt.PenStyle.NoPen)
        # 主体：发色蓝，压得比较淡，别在桌面上太扎眼
        p.setBrush(QColor(58, 92, 168, 120))
        p.drawRoundedRect(box, radius, radius)
        # 中间一道更亮的芯，像头发的高光
        p.setBrush(QColor(132, 168, 240, 170))
        p.drawRoundedRect(hi, radius * 0.7, radius * 0.7)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._collapsed:
            self._paint_bar(p)
            return

        bob = math.sin(self._phase * 1.5) * 2.2
        if self.mood == LYING:
            bob = math.sin(self._phase * 0.55) * 1.0    # 躺平了，只剩肚子在动
        elif self.mood == TEASE:
            bob = math.sin(self._phase * 0.9) * 1.4

        name = self._sprite_name()
        pm = self._pixmap(name)
        base_y = self.height() - 16
        if pm.isNull():
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "缺素材")
            return
        pw = pm.width() / pm.devicePixelRatio()
        ph = pm.height() / pm.devicePixelRatio()
        left = (self.width() - pw) / 2
        top = base_y - ph + bob

        # 换空闲姿势时做一次淡入淡出：旧图淡出、新图淡入，避免生硬跳变
        if self._prev_key:
            old = self._pixmap(self._prev_key)
            if old.isNull():
                self._prev_key = ""
            else:
                t = max(0.0, min(1.0, (time.time() - self._fade_at) / IDLE_FADE))
                ow = old.width() / old.devicePixelRatio()
                oh = old.height() / old.devicePixelRatio()
                ox, oy = (self.width() - ow) / 2, base_y - oh + bob
                p.setOpacity(1.0 - t)
                p.drawPixmap(QPoint(int(ox), int(oy)), old)
                p.setOpacity(t)

        # 原图自带落地阴影，这里不再画，避免出现"两层影子"
        p.drawPixmap(QPoint(int(left), int(top)), pm)
        p.setOpacity(1.0)

        # 干饭进度条（只要还在下就画，不管当前是哪张脸）
        if self.downloading:
            pct = self._overall_percent()
            bar = QRectF(20, self.height() - 11, self.width() - 40, 5)
            p.setBrush(QColor(28, 48, 96, 55))
            p.drawRoundedRect(bar, 2.5, 2.5)
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(),
                                     max(4.0, bar.width() * pct / 100), bar.height()), 2.5, 2.5)
            if self.mood == BOWL:
                self._steam(p, left, top, pw, ph)

        # 碗数角标
        n = self._bowls()
        if n:
            badge = QRectF(left + pw - 34, top + ph - 34, 30, 23)
            p.setBrush(QColor(255, 255, 255, 245))
            p.setPen(QPen(QColor(theme.ACCENT), 1.6))
            p.drawRoundedRect(badge, 11, 11)
            p.setPen(QColor(theme.ACCENT))
            f = QFont("Microsoft YaHei UI", 9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(badge, int(Qt.AlignmentFlag.AlignCenter),
                       str(n) if n < 10 else "9+")

        if self._toast:
            self._paint_toast(p)

    def _overall_percent(self) -> float:
        # 只算"正在下载"的：做种任务在 aria2 里也是 active、而且进度 100%，
        # 混进来会把底部进度条一下拉到接近满格（用户 2026-10-21 看到 8% 却像 70%）。
        act = [r for r in self.rows
               if r.status_code == "active" and not r.is_seeder and not r.is_meta]
        if not act:
            return 0.0
        tot = sum(r.size for r in act)
        if tot <= 0:
            return 0.0
        done = sum(r.done for r in act)
        return max(0.0, min(100.0, done * 100.0 / tot))

    def _steam(self, p: QPainter, left: float, top: float, pw: float, ph: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        base_x = left + pw * 0.52
        base_y = top + ph * 0.60
        for i in range(3):
            t = (self._phase * 1.4 + i * 0.9) % 3.0
            alpha = int(120 * max(0.0, 1 - t / 3.0))
            p.setBrush(QColor(255, 255, 255, alpha))
            r = 3.2 - i * 0.4
            p.drawEllipse(QRectF(base_x + i * 5.0 + math.sin(t * 2) * 2.5,
                                 base_y - t * 13, r * 2, r * 2))

    def _paint_toast(self, p: QPainter) -> None:
        f = QFont("Microsoft YaHei UI", 9)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = min(SIZE - 8, fm.horizontalAdvance(self._toast) + 22)
        box = QRectF((SIZE - tw) / 2, 2, tw, 22)
        path = QPainterPath()
        path.addRoundedRect(box, 11, 11)
        p.setPen(QPen(QColor(210, 222, 245), 1))
        p.setBrush(QColor(255, 255, 255, 246))
        p.drawPath(path)
        p.setPen(QColor(theme.TEXT))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), fm.elidedText(
            self._toast, Qt.TextElideMode.ElideRight, int(tw - 10)))

    # ---------- 拖放 ----------
    def dragEnterEvent(self, event):  # noqa: N802
        md = event.mimeData()
        if md.hasText() or md.hasUrls():
            event.acceptProposedAction()
            self.toast("闻到了饭味，松手～")
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        md = event.mimeData()
        text = ""
        if md.hasUrls():
            text = "\n".join(u.toString() for u in md.urls())
        elif md.hasText():
            text = md.text()
        if text.strip():
            self.quick_add.emit(text.strip())
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---------- 鼠标 ----------
    def enterEvent(self, event):  # noqa: N802
        # 鼠标碰到那条蓝边就把自己召唤出来
        if self._collapsed:
            self._collapse_timer.stop()
            self._expand()
        elif self._dock:
            # 已经展开：鼠标回来了，取消原来排好的自动收
            self._collapse_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        # 贴边状态下，鼠标走开先排定时器；缓冲没到、或鼠标又回来了都不会真收
        self._schedule_collapse()
        super().leaveEvent(event)

    def showEvent(self, event):  # noqa: N802
        self._want_visible = True
        super().showEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._collapsed:
                self._expand()
            self._collapse_timer.stop()
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = self._press_pos - self.frameGeometry().topLeft()
            self._moved = False
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            cur = event.globalPosition().toPoint()
            if (cur - self._press_pos).manhattanLength() > 4:
                self._moved = True
            self.move(cur - self._drag_pos)
            if self.panel.isVisible():
                self._position_panel()
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                self._toggle_panel()
            else:
                # 拖到边上就收起来；拖到中间就恢复正常悬浮
                self._maybe_dock()
        self._drag_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            text = QApplication.clipboard().text().strip()
            if text:
                self.quick_add.emit(text)
                self.toast("开饭！")
            else:
                self.toast("剪贴板里没有链接")
            event.accept()

    def _build_menu(self):
        """右键菜单。单独拆出来，测试才能不 exec() 直接检查菜单项。"""
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("打开主窗口", self.show_main.emit)
        m.addAction("暂停 / 继续全部", self.toggle_all.emit)
        m.addSeparator()
        if self._dock:
            m.addAction("取消贴边（恢复正常悬浮）", self._undock)
        else:
            m.addAction("收成一条边（贴右边）", lambda: self._dock_to("right"))
        m.addAction("隐藏桌宠", self._hide_by_user)
        m.addSeparator()
        m.addAction("退出整个程序", self.quit_app.emit)
        return m

    def contextMenuEvent(self, event):  # noqa: N802
        m = self._build_menu()
        m.exec(event.globalPos())
        m.deleteLater()

    def _dock_to(self, edge: str) -> None:
        self._dock = edge
        self._collapse(force=True)

    def _hide_by_user(self) -> None:
        self._want_visible = False
        self.hide()

    # ---------- 面板 ----------
    def _toggle_panel(self) -> None:
        if not self.panel.isVisible():
            if self._collapsed:
                self._expand()
            self._collapse_timer.stop()
            self.panel.refresh(self.rows, self.stat)
            self.panel.set_mode(0)
            self._position_panel()
            self.panel.show()
            self.panel.raise_()
            self._watch_outside(True)
        else:
            # 第二下单击只收起气泡；打开主界面走气泡里的按钮或右键菜单，
            # 不能把主窗口绑定在"随手再点一下桌宠"上。
            self._hide_panel()
            self._schedule_collapse()

    def _position_panel(self) -> None:
        g = self._screen_geo()
        h = self.panel.sizeHint().height()
        y = self.y() - h - 8
        if y < 0:
            y = self.y() + self.height() + 8
        x = self.x() + SIZE // 2 - PANEL_W // 2
        if g:
            # 贴边收起后，气泡不能被推出屏幕
            x = max(g.left() + 8, min(g.right() - PANEL_W - 8, x))
            y = max(g.top() + 8, min(g.bottom() - h - 8, y))
        self.panel.move(x, y)


class PetPanel(QWidget):
    """桌宠气泡：0=简洁 1=详细。"""

    def __init__(self, pet: PetWindow):
        super().__init__(None)
        self.pet = pet
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_W)
        self.mode = 0
        self.rows: list[TaskRow] = []
        self.stat: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.inner = _PanelBody(self)
        lay.addWidget(self.inner)

        # 气泡里给一个明确的「打开主界面」按钮；左键只负责开关气泡。
        self.btn_open_main = QToolButton(self.inner)
        self.btn_open_main.setText("打开主界面")
        self.btn_open_main.setAutoRaise(True)
        self.btn_open_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_main.setToolTip("打开主窗口；也可以右键桌宠 → 打开主窗口")
        self.btn_open_main.setStyleSheet(
            "QToolButton{color:#3A7BD5;font-size:11px;border:1px solid #DCE6F5;"
            "border-radius:6px;padding:2px 8px;background:#F5F9FF;}"
            "QToolButton:hover{background:#E8F1FF;}")
        self.btn_open_main.clicked.connect(self.pet.show_main.emit)

        # 自动收起：没人管它就自己关，不然全屏看片时白框一直赖在屏幕上
        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.setInterval(PANEL_AUTO_HIDE)
        self._auto.timeout.connect(self._auto_hide)

    def _auto_hide(self) -> None:
        if self.underMouse():
            self._auto.start(PANEL_HOVER_GRACE)   # 鼠标还搭在上面，缓缓
            return
        self.pet._panel_closed()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._auto.start(PANEL_AUTO_HIDE)

    def hideEvent(self, event):  # noqa: N802
        self._auto.stop()
        try:
            self.pet._watch_outside(False)
        except RuntimeError:
            pass
        super().hideEvent(event)

    def enterEvent(self, event):  # noqa: N802
        self._auto.stop()          # 鼠标进来了就是在看，别关
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._auto.start(PANEL_HOVER_GRACE)
        super().leaveEvent(event)

    def set_mode(self, mode: int) -> None:
        self.mode = mode
        self.inner.mode = mode
        self.refresh(self.rows, self.stat)
        self.adjustSize()

    def refresh(self, rows: list[TaskRow], stat: dict) -> None:
        self.rows = rows
        self.stat = stat
        self.inner.rows = rows
        self.inner.stat = stat
        active = [r for r in rows if r.status_code == "active"]
        show = min(len(active), 5 if self.mode == 0 else 12)
        row_h = 26 if self.mode == 0 else 44
        extra = 24 if len(active) > show else 0
        h = 58 + max(1, show) * row_h + extra + 10 + 28
        self.inner.setFixedHeight(int(h))
        self.btn_open_main.setGeometry(int(PANEL_W - 104), int(h - 27), 94, 21)
        self.inner.update()
        self.adjustSize()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        # 左键双击不再打开主窗口；主窗口只走气泡按钮/右键菜单。
        event.ignore()


def panel_state_text(rows: list[TaskRow]) -> str:
    """气泡顶部状态：下载 + 做种分开说，做种不再显示成"在等饭"。"""
    down = [r for r in rows
            if r.status_code == "active" and not r.is_seeder and not r.is_meta]
    seed = [r for r in rows
            if r.status_code == "active" and r.is_seeder and not r.is_meta]
    parts = []
    if down:
        parts.append("打饭中 %d 碗" % len(down))
    if seed:
        parts.append("给鱼群分饭 %d 个" % len(seed))
    return " · ".join(parts) if parts else "在等饭"


class _PanelBody(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[TaskRow] = []
        self.stat: dict = {}
        self.mode = 0
        self.setMinimumHeight(108)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(255, 255, 255, 250))
        p.drawRoundedRect(rect, 12, 12)

        pad = 13
        y = 10
        ds = int(self.stat.get("downloadSpeed") or 0)
        us = int(self.stat.get("uploadSpeed") or 0)
        # 下载中 / 做种分开：做种也是 active，但界面上是"在上传"，不是"还在下"
        down = [r for r in self.rows
                if r.status_code == "active" and not r.is_seeder and not r.is_meta]
        seed = [r for r in self.rows
                if r.status_code == "active" and r.is_seeder and not r.is_meta]
        work = down + seed
        done_total = sum(1 for r in self.rows if r.is_finished)
        done_bytes = sum(r.size for r in self.rows if r.is_finished)

        f = QFont("Microsoft YaHei UI", 9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(pad, y + 14, brand.APP_NAME)
        p.setFont(QFont("Microsoft YaHei UI", 8))
        p.setPen(QColor(theme.ACCENT) if work else QColor(theme.TEXT_DIM))
        state = panel_state_text(self.rows)
        p.drawText(pad, y + 31, "%s   ↓ %s   ↑ %s" % (state, fmt_speed(ds), fmt_speed(us)))
        y += 44

        if not work:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(pad, y + 12, "大肥鱼已经吃掉 %d 碗（%s）" % (done_total, fmt_size(done_bytes)))
            p.setPen(QColor(theme.GREY))
            p.drawText(pad, y + 32, "双击我：直接下载剪贴板里的链接")
            return

        shown = work[:5] if self.mode == 0 else work[:12]
        for idx, r in enumerate(shown, 1):
            name = r.name if len(r.name) <= (24 if self.mode == 0 else 32) else r.name[:22] + "…"
            p.setFont(QFont("Microsoft YaHei UI", 8))
            if r.is_seeder:
                # 做种：不画下载进度条，直接报上传速度和已上传量
                if self.mode == 0:
                    p.setPen(QColor(theme.TEXT))
                    p.drawText(pad, y + 10, "%d. %s   ↑ %s" % (idx, name, fmt_speed(r.ul)))
                    y += 26
                else:
                    p.setPen(QColor(theme.TEXT))
                    p.drawText(pad, y + 10, "%d. %s" % (idx, name))
                    p.setPen(QColor(theme.ACCENT))
                    p.drawText(pad, y + 36,
                               "↑ %s  已传 %s  分享率 %.2f"
                               % (fmt_speed(r.ul), fmt_size(r.ul_done), r.ratio))
                    y += 44
                continue

            p.setPen(QColor(theme.TEXT))
            p.drawText(pad, y + 10, "%d. %s" % (idx, name))

            bar = QRectF(pad, y + 15, self.width() - pad * 2, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#EDEFF3"))
            p.drawRoundedRect(bar, 2.5, 2.5)
            p.setBrush(QColor(theme.ACCENT))
            w = max(3.0, bar.width() * r.percent / 100.0)
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), w, bar.height()), 2.5, 2.5)

            if self.mode == 1:
                p.setPen(QColor(theme.TEXT_DIM))
                if r.percent >= 99.9:
                    tail = "这碗见底了"
                elif r.dl > 1024:
                    tail = "还剩 %d 分钟" % max(1, (r.size - r.done) // max(1, r.dl) // 60)
                else:
                    tail = "等上菜"
                p.drawText(pad, y + 36, "%.1f%%   ↓ %s   %s" % (r.percent, fmt_speed(r.dl), tail))
                y += 44
            else:
                y += 26

        if len(work) > len(shown):
            p.setPen(QColor(theme.GREY))
            p.drawText(pad, y + 12, "还有 %d 个在排队/分饭…" % (len(work) - len(shown)))

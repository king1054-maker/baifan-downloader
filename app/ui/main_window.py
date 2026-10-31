# -*- coding: utf-8 -*-
"""主窗口。"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import (QEvent, QItemSelectionModel, QSize, Qt, QTimer,
                          Signal, QByteArray, QRect, QPoint)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QFileIconProvider,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QScrollArea, QSlider, QSplitter, QStatusBar,
    QSystemTrayIcon,
    QTabWidget, QTableView, QTableWidget, QTableWidgetItem, QToolBar, QToolButton,
    QToolTip,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import (background, brand, config, dedupe, intents, openwith, paths, pet_skin,
                selfcheck, shell_integration, title_parse, title_prefetch,
                titlegen, torrent_keep)
from ..api_server import ApiServer
from ..engine import Aria2Engine
from ..notify import Notifier
from ..rpc import Aria2Error, add_checked
from . import theme
from .icons import filter_icon
from .add_dialog import AddDialog, TaskRequest
from .file_select import FileSelectDialog
from .models import (
    ACTION_KEYS, ARROW_ZONE, COL_INDEX, COLS, ActionDelegate, AIRenameDelegate,
    DotDelegate, MemoDelegate,
    ProgressDelegate, TaskRow, TaskTableModel, fmt_size, fmt_speed, matches_text,
    target_complete,
)
from .options_dialog import OptionsDialog
from .pet import PetWindow
from .store import MEMO_MAX, TaskMetaStore
from .table import GapTableView
from .title_lookup_dialog import TitleLookupDialog
from .torrent_library import TorrentLibraryDialog
from .workers import PollWorker

class _Backdrop(QWidget):
    """主界面底图：把用户图等比铺满，再盖一层很淡的白纱保证文字可读。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = None
        self._veil = 0.5
        self._anchor = (0.5, 0.5)
        self._crop = None
        self.setAutoFillBackground(False)

    def set_image(self, path: Path | None, anchor: tuple[float, float] | None = None,
                  crop: list[float] | tuple[float, ...] | None = None) -> None:
        pm = QPixmap(str(path)) if path else QPixmap()
        self._pm = None if pm.isNull() else pm
        if anchor is not None:
            self._anchor = (max(0.0, min(1.0, float(anchor[0]))),
                            max(0.0, min(1.0, float(anchor[1]))))
        if crop and len(crop) == 4:
            try:
                x, y, w, h = (max(0.0, min(1.0, float(v))) for v in crop)
                self._crop = (x, y, max(0.02, min(1.0 - x, w)),
                              max(0.02, min(1.0 - y, h)))
            except (TypeError, ValueError):
                self._crop = None
        else:
            self._crop = None
        self.update()

    def set_veil(self, value: float) -> None:
        self._veil = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        if self._pm is not None:
            w, h = self.width(), self.height()
            if self._crop is not None:
                cx, cy, cw, ch = self._crop
                src = QRect(int(cx * self._pm.width()), int(cy * self._pm.height()),
                            max(1, int(cw * self._pm.width())),
                            max(1, int(ch * self._pm.height())))
                p.drawPixmap(self.rect(), self._pm, src)
            else:
                scaled = self._pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                         Qt.TransformationMode.SmoothTransformation)
                # 宽/窄图用锚点决定裁哪一块；0.5,0.5 = 居中
                x = int(max(0, scaled.width() - w) * self._anchor[0])
                y = int(max(0, scaled.height() - h) * self._anchor[1])
                p.drawPixmap(0, 0, scaled, x, y, w, h)
            # 白纱浓度由用户调；0 = 完全看原图，100 = 接近纯白
            p.fillRect(self.rect(),
                       QColor(255, 255, 255, int(self._veil * 210)))
        p.end()


class _SpeedLimitPanel(QFrame):
    """工具栏限速小面板：10 档标度滑块，禁止单独一个按钮。"""

    changed = Signal(int)                 # 字节/秒；0 = 不限速
    VALUES = [1, 5, 10, 20, 30, 50, 100, 200, 500, 0]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QFrame{background:#FFFFFF;border:1px solid #DCE2EC;border-radius:10px;}")
        self.setFixedWidth(430)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)
        top = QHBoxLayout()
        lb = QLabel("全局下载限速")
        lb.setStyleSheet("font-weight:600;color:#1F2328;")
        top.addWidget(lb)
        top.addStretch(1)
        self.lb_cur = QLabel("")
        self.lb_cur.setStyleSheet("color:#2F5BEA;font-weight:600;")
        top.addWidget(self.lb_cur)
        lay.addLayout(top)

        self.sl = QSlider(Qt.Orientation.Horizontal)
        self.sl.setRange(0, len(self.VALUES) - 1)
        self.sl.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sl.setTickInterval(1)
        self.sl.setPageStep(1)
        self.sl.valueChanged.connect(self._on_slide)
        lay.addWidget(self.sl)

        marks = QHBoxLayout()
        marks.setContentsMargins(0, 0, 0, 0)
        for t in ("1", "5", "10", "20", "30", "50", "100", "200", "500", "不限"):
            x = QLabel(t)
            x.setAlignment(Qt.AlignmentFlag.AlignCenter)
            x.setStyleSheet("color:#7A8494;font-size:10px;border:none;background:transparent;")
            marks.addWidget(x, 1)
        lay.addLayout(marks)

        bot = QHBoxLayout()
        self.btn_forbid = QToolButton()
        self.btn_forbid.setText("禁止")
        self.btn_forbid.setCheckable(True)
        self.btn_forbid.setToolTip("完全禁止传输（1 B/s），任务不暂停")
        self.btn_forbid.clicked.connect(self._on_forbid)
        bot.addWidget(self.btn_forbid)
        tip = QLabel("拖动即时生效，最右为不限速")
        tip.setStyleSheet("color:#9AA3AF;font-size:10px;border:none;background:transparent;")
        bot.addWidget(tip, 1)
        lay.addLayout(bot)

    def _mb(self, idx: int) -> int:
        mb = self.VALUES[idx] if 0 <= idx < len(self.VALUES) else 0
        return mb * 1024 * 1024

    def set_limit(self, value: int) -> None:
        value = int(value or 0)
        forbidden = 0 < value <= 1024
        self.btn_forbid.blockSignals(True)
        self.btn_forbid.setChecked(forbidden)
        self.btn_forbid.blockSignals(False)
        if forbidden:
            idx = 0
            self.lb_cur.setText("禁止")
        elif value == 0:
            idx = len(self.VALUES) - 1
            self.lb_cur.setText("不限速")
        else:
            mb = max(1, round(value / (1024 * 1024)))
            idx = min(range(len(self.VALUES) - 1),
                      key=lambda i: abs(self.VALUES[i] - mb))
            self.lb_cur.setText("%d MB/s" % self.VALUES[idx])
        self.sl.blockSignals(True)
        self.sl.setValue(idx)
        self.sl.blockSignals(False)

    def _on_slide(self, idx: int) -> None:
        self.btn_forbid.setChecked(False)
        mb = self.VALUES[idx] if 0 <= idx < len(self.VALUES) else 0
        self.lb_cur.setText("不限速" if mb == 0 else "%d MB/s" % mb)
        self.changed.emit(self._mb(idx))

    def _on_forbid(self, on: bool) -> None:
        if on:
            self.lb_cur.setText("禁止")
            self.changed.emit(1)
        else:
            self._on_slide(self.sl.value())


FILTERS = [
    ("all", "全部饭碗", (),
     lambda r: True),
    ("down", "打饭中", (),
     lambda r: r.status_code == "active" and not r.is_seeder and not r.error),
    ("wait", "排队等饭", (),
     lambda r: r.status_code == "waiting" and not r.error),
    # 放下筷子往上提：暂停的任务还是"没下完"，放在开饭了前面更顺
    ("pause", "放下筷子", (),
     lambda r: r.status_code == "paused" and not r.is_finished),
    ("done", "开饭了！！", (),
     lambda r: r.is_finished and not r.is_meta),
    ("seed", "给鱼群分饭", (),
     lambda r: r.status_code == "active" and r.is_seeder and not r.error),
    ("err", "噎住了", (),
     lambda r: bool(r.error)),
]

# 饭桌用语 → 人话。鼠标停在筛选列表上就能看到，不用猜。
FILTER_HINTS = {
    "all": "所有任务，不管什么状态",
    "down": "正在下载的任务（大肥鱼在打饭）",
    "wait": "排队中、还没轮到的任务",
    "pause": "放下筷子 = 任务被暂停了。选中后点「开始」继续",
    "done": "已经下完的任务（开饭了！！）。正在做种上传的也在这里，\n"
            "同时会出现在「给鱼群分饭」——那一栏是叠加筛选，不互斥。",
    "seed": "给鱼群分饭 = 已经开饭、正在上传给别人。\n"
            "这一栏只是从「开饭了」里筛出正在上传的那部分，\n"
            "任务仍然同时留在「开饭了」，不是搬过去。\n"
            "下面「种子库」是磁力/种子文件的备案，不是下载回来的文件。\n"
            "没在传？右键任意一条 →「给鱼群分饭」重新校验并开始上传。",
    "err": "噎住了 = 下载出错了。详情里能看到具体原因",
}

STATE_ORDER = {"下载中": 0, "做种中": 1, "等待中": 2, "已暂停": 3, "错误": 4,
               "已完成": 5, "已移除": 6}


class PieceMap(QWidget):
    """分块完成图：绿色 = 已有，蓝色 = 下载中，灰色 = 缺失。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self._bits: list[int] = []

    def set_bitfield(self, hex_str: str, num_pieces: int) -> None:
        bits: list[int] = []
        if hex_str:
            for ch in hex_str:
                try:
                    v = int(ch, 16)
                except ValueError:
                    continue
                for shift in (3, 2, 1, 0):
                    bits.append((v >> shift) & 1)
        if num_pieces > 0:
            bits = bits[:num_pieces]
        self._bits = bits
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self._bits:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "暂无分块信息（仅 BitTorrent 可用）")
            return
        n = len(self._bits)
        w = self.width()
        cell = 7
        gap = 1
        cols = max(1, (w - 16) // (cell + gap))
        rows = (n + cols - 1) // cols
        cell_h = max(5.0, min(9.0, (self.height() - 20) / max(1, rows)))
        for i, b in enumerate(self._bits):
            x = 8 + (i % cols) * (cell + gap)
            y = 10 + (i // cols) * (cell_h + gap)
            p.fillRect(int(x), int(y), cell, int(cell_h),
                       QColor(theme.GREEN if b else "#E9ECF1"))
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(8, self.height() - 4, "共 %d 块 · 已有 %d 块" % (n, sum(self._bits)))


class MainWindow(QMainWindow):
    verify_done = Signal(str, dict)

    def __init__(self, engine: Aria2Engine, settings: dict[str, Any]):
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.store = TaskMetaStore()
        self._last_dupes = 0        # 上一次添加里有几个是「之前就加过了」
        # 排序：按顺序生效，第一个是主键。默认跟以前一样——状态分组、组内按添加时间
        self._sort_keys: list[tuple[str, bool]] = [("status", False), ("added", False)]
        self._sort_hint_shown = False
        self.all_rows: list[TaskRow] = []
        self.visible_rows: list[TaskRow] = []
        self.current_filter = "all"
        self._last_detail = 0.0
        self._selected_gid = ""
        self.api: ApiServer | None = None
        self.api_port = 0
        self._verified: set[str] = set()
        self.verify_map: dict[str, dict] = {}
        self.pet: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self.notifier: Notifier | None = None
        self._really_quit = False
        self._started_at = time.time()
        self._completed_seen: set[str] = set()
        self._active_seen: set[str] = set()
        self._seen_errors: set[str] = set()
        self._history_cache: list[TaskRow] | None = None
        self._title_prefetch = title_prefetch.TitlePrefetcher()
        self._cleaned: set[str] = set()
        self._title_done: set[str] = set()      # 已经起过核心名的任务
        self._magnet_named: set[str] = set()    # 种子库里已经更新过显示名的磁力
        self._verify_attempts: dict[str, int] = {}   # 校验失败先重试一次，别急着判"没吃干净"
        self._recover_tried: set[str] = set()        # 暂停任务的大小/完成状态自愈只试一次
        self._notify_pending: set[str] = set()
        self._bowls = 0
        self._await_pick: dict[str, dict] = {}
        self._files_gid = ""
        self._files_rows = 0
        self._files_dirty = False
        self._icons = QFileIconProvider()
        self._icon_cache: dict[str, QIcon] = {}

        self.setAcceptDrops(True)
        self.setWindowTitle("%s  ·  %s" % (brand.APP_NAME, brand.TAGLINE))
        ico = paths.ASSET_DIR / "icons" / "whale.ico"
        if ico.exists():
            self.setWindowIcon(QIcon(str(ico)))
        self.resize(1360, 820)
        self.setMinimumSize(1024, 600)
        self._window_was_maximized = False
        self._apply_maximized_on_show = False
        self._restore_saved_geometry()

        self.model = TaskTableModel(self)
        self.model.memo_sink = self._on_memo_committed
        self._filter_pending = False
        # 详情页备注框当前装的是哪个任务的备注 —— 失焦提交时按它存，
        # 不能按"当前选中行"（点别的行时焦点先走、选中后变，会串行）
        self._memo_edit_gid = ""
        self._build_ui()
        self._build_actions()
        self._build_menubar()
        self._build_toolbar()
        self._build_statusbar()
        self._sync_speed_combo()
        self._bg_path = None
        self._bg_timer = QTimer(self)
        self._bg_timer.timeout.connect(self._next_background)
        try:
            if not self.settings.get("bg_defaults_seeded"):
                background.seed_defaults()
                self.settings["bg_defaults_seeded"] = True
                config.save(self.settings)
        except Exception:  # noqa: BLE001
            pass
        self._init_background()
        self.verify_done.connect(self._on_verify_done)
        self._init_tray()
        self._schedule_tray_promote()
        self._start_polling()
        self._start_api()
        self._init_pet()
        self._init_notifier()

        # 桌宠素材库：把随包的八张图铺到 data/pet/图库（只补缺，不覆盖）。
        # 放在 init 之后是因为这一步失败也不该挡住启动。
        try:
            pet_skin.ensure_library()
        except Exception:  # noqa: BLE001
            pass

    # ================= UI 构建 =================
    def _build_ui(self) -> None:
        # 左边那列筛选宽度是写死的，没有可拖的必要 —— 用普通水平布局就够。
        # 以前这里套了个 QSplitter，QSplitter 按 tree 的 sizeHint(256) 分栏，
        # 而 tree 自己又被 setFixedWidth(150) 卡死在 150，多出来的 106px 就成了
        # 树和表格之间一条莫名其妙的白色空带（用户 2026-10-20 报的那个"白区域"）。
        holder = _Backdrop()
        self._backdrop = holder
        hb = QHBoxLayout(holder)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # 150 的时候「给鱼群分饭 (2)」会被省略号吃掉，加宽一点
        self.tree.setFixedWidth(190)
        self.tree.setIndentation(10)          # 「种子库」挂在「给鱼群分饭」下面，要一点缩进
        self.tree.setIconSize(QSize(40, 40))   # 用户给的是整张立绘，给足地方
        self.tree.setStyleSheet(
            "QTreeWidget{background:%s;border:none;border-right:1px solid %s;"
            "outline:0;padding:8px 6px;}"
            "QTreeWidget::item{height:46px;border-radius:6px;padding-left:6px;}"
            "QTreeWidget::item:hover{background:#EEF1F6;}"
            "QTreeWidget::item:selected{background:%s;color:%s;font-weight:600;}"
            % (theme.BG_ALT, theme.BORDER, theme.ACCENT_SOFT, theme.ACCENT))
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for key, label, _t, _f in FILTERS:
            it = QTreeWidgetItem([label])
            it.setIcon(0, filter_icon(key))
            it.setSizeHint(0, QSize(0, 46))
            it.setData(0, Qt.ItemDataRole.UserRole, key)
            it.setToolTip(0, "%s —— %s" % (label, FILTER_HINTS.get(key, "")))
            self.tree.addTopLevelItem(it)
        # 「给鱼群分饭」下面挂「种子库」：历史 .torrent/磁力单独放这里，
        # 不再混在任务列表里装成"已经下好的文件"（用户对标比特彗星的种子存档）。
        self._archive_item = QTreeWidgetItem(["种子库  (0)"])
        self._archive_item.setData(0, Qt.ItemDataRole.UserRole, "archive")
        # 图标用原来「噎住了」那张立绘（用户指定）；「噎住了」本身换了新图
        self._archive_item.setIcon(0, filter_icon("archive"))
        # 只给 Enabled、不给 Selectable：点它只弹种子库，不抢当前项，
        # 否则 Qt 会在这一行画一个带四个黑角的焦点框（用户截图报的怪框）。
        self._archive_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self._archive_item.setSizeHint(0, QSize(0, 38))
        self._archive_item.setToolTip(
            0, "历史种子库 —— 用过的 .torrent / 磁力都存在这里，可以重新分饭")
        seed_index = next((i for i, f in enumerate(FILTERS) if f[0] == "seed"), -1)
        if seed_index >= 0:
            seed_item = self.tree.topLevelItem(seed_index)
            seed_item.addChild(self._archive_item)
            seed_item.setExpanded(True)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.currentItemChanged.connect(self._on_filter_changed)
        hb.addWidget(self.tree)

        right = QSplitter(Qt.Orientation.Vertical)

        self.table = GapTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.drag_exclude_columns = {
            COL_INDEX["ai_name"], COL_INDEX["act_dir"], COL_INDEX["act_open"],
            COL_INDEX["memo"],
        }
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setItemDelegateForColumn(COL_INDEX["dot"], DotDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_INDEX["ai_name"], AIRenameDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_INDEX["percent"], ProgressDelegate(self.table))
        self._memo_delegate = MemoDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_INDEX["memo"], self._memo_delegate)
        # 备注列：双击改；已经是选中行的备注格，单击一下就进编辑（少按一次）
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked)
        self._memo_delegate.closeEditor.connect(self._on_memo_editor_closed)
        self._action_delegate = ActionDelegate(self.table)
        for key in ACTION_KEYS:
            self.table.setItemDelegateForColumn(COL_INDEX[key], self._action_delegate)
        self.table.setMouseTracking(True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_row_menu)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, (key, _label, w) in enumerate(COLS):
            hh.resizeSection(i, w)
        hh.setStretchLastSection(False)
        # 「名称」以前是 Stretch，看着能自适应，但一拖别的列的分隔线就露馅：
        # Qt 会去压缩这个 Stretch 列，而不是把右边的列往右推 ——
        # 用户拖「大小」的右边线，结果变窄的却是左边的「名称」。
        # 双击表头分隔线，Qt 会"自动适配宽度"——在用户眼里就是"我拖好的宽度突然被复原"。
        # 这里直接吃掉这个事件，宽度只认手动拖的。
        hh.viewport().installEventFilter(self)
        hh.installEventFilter(self)
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)
        hh.sectionClicked.connect(self._on_header_clicked)
        self.table.setColumnHidden(COL_INDEX["added"], True)
        self.table.setColumnHidden(COL_INDEX["dir"], True)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.clicked.connect(self._on_table_clicked)
        right.addWidget(self.table)

        self.detail = QTabWidget()
        self.detail.setDocumentMode(True)
        # 不自定义 QTabBar 的 tab 背景：之前那一版把每个标签画成白块，比透图更丑。
        # 用一层实底 host 托住整个 QTabWidget，标签保持主题原本的透明样式。
        self.detail_host = QWidget()
        self.detail_host.setStyleSheet("background:%s;" % theme.BG)
        _hl = QVBoxLayout(self.detail_host)
        _hl.setContentsMargins(0, 0, 0, 0)
        _hl.setSpacing(0)
        _hl.addWidget(self.detail)
        # QTabBar 默认会在标签底下画一条 base 线；在 documentMode + 透明标签下
        # 这条线会被标签切成一段一段，看着像"无数次中断"。直接不要 base 线。
        self.detail.tabBar().setDrawBase(False)
        self.detail.tabBar().setExpanding(False)
        self.detail.setStyleSheet("QTabWidget::pane{border-top:0px;}")
        # 以前这里 setFixedHeight(246)，QSplitter 就再也拖不动它了：
        # 往下拖很大一截只会触发"整个收起"，没法停在 20%~30% 这种高度。
        # 改成只留一个最小高度，剩下的交给用户拖。
        self.detail.setMinimumHeight(32)
        self.btn_detail_toggle = QToolButton()
        self.btn_detail_toggle.setText("▾")
        self.btn_detail_toggle.setAutoRaise(True)
        self.btn_detail_toggle.setFixedSize(22, 22)
        self.btn_detail_toggle.setToolTip("收起下面的详情面板，多看几行任务")
        self.btn_detail_toggle.setStyleSheet(
            "QToolButton{background:transparent;border:none;color:#6B7280;"
            "font-size:12px;padding:0;}")
        self.btn_detail_toggle.clicked.connect(self._toggle_detail)
        # 放左上角：桌宠常驻右下角，会把右上角的按钮挡住，用户根本点不到
        self.detail.setCornerWidget(self.btn_detail_toggle,
                                    Qt.Corner.TopLeftCorner)

        self.tab_info = QWidget()
        self.info_form = QFormLayout(self.tab_info)
        self.info_form.setContentsMargins(16, 10, 16, 10)
        self.info_form.setHorizontalSpacing(18)
        self.info_form.setVerticalSpacing(4)
        # 出错原因横幅：以前只在状态后面拼长英文，用户根本注意不到。
        self.lb_error = QLabel("")
        self.lb_error.setWordWrap(True)
        self.lb_error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lb_error.setStyleSheet(
            "color:#B42318;background:#FFF3F1;border:1px solid #FECACA;"
            "border-radius:6px;padding:6px 9px;")
        self.lb_error.hide()
        self.info_form.addRow(self.lb_error)
        self._info_labels: dict[str, QLabel] = {}
        for key, label in (("name", "名称"), ("status", "状态"), ("size", "大小"),
                           ("done", "已下载"), ("speed", "速度"), ("peers", "种子/连接"),
                           ("hash", "InfoHash"), ("dir", "保存路径"), ("gid", "GID")):
            lb = QLabel("—")
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lb.setWordWrap(key in ("name", "dir"))
            self._info_labels[key] = lb
            self.info_form.addRow("%s：" % label, lb)

        # 备注单独一行，且直接可写：表格那列窄，长备注看不全，这里敲字更舒服
        self.memo_edit = QLineEdit()
        self.memo_edit.setPlaceholderText("点这里写一句这是什么，例如：芙莉莲 第 8 集 4K")
        self.memo_edit.setMaxLength(MEMO_MAX)
        self.memo_edit.setClearButtonEnabled(True)
        self.memo_edit.setFrame(False)
        self.memo_edit.setStyleSheet(
            "QLineEdit{background:%s;border:1px solid %s;border-radius:5px;"
            "padding:2px 7px;color:%s;}"
            "QLineEdit:focus{background:#FFFFFF;border-color:%s;}"
            % (theme.BG_ALT, theme.BORDER, theme.TEXT, theme.ACCENT))
        self.memo_edit.setToolTip("在这里写/改备注，回车或点到别处就保存；清空后回车等于删掉")
        self.memo_edit.editingFinished.connect(self._commit_detail_memo)
        self.info_form.addRow("备注：", self.memo_edit)
        # 详情面板会被拖得很矮，常规页必须能滚动，不能把 10 行字硬挤在一起
        info_scroll = QScrollArea()
        info_scroll.setWidgetResizable(True)
        info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        info_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        info_scroll.setWidget(self.tab_info)
        self.detail.addTab(info_scroll, "常规")

        files_page = QWidget()
        fp = QVBoxLayout(files_page)
        fp.setContentsMargins(12, 8, 12, 8)
        fp.setSpacing(6)
        fbar = QHBoxLayout()
        fbar.setSpacing(6)
        for text, fn in (("全选", lambda: self._check_all_files(True)),
                         ("全不选", lambda: self._check_all_files(False)),
                         ("仅选视频", self._check_video_files),
                         ("反选", self._invert_files)):
            b = QToolButton()
            b.setText(text)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            fbar.addWidget(b)
        fbar.addSpacing(10)
        self.btn_apply_files = QToolButton()
        self.btn_apply_files.setText("应用勾选")
        self.btn_apply_files.setToolTip("只下载勾上的文件（任务会短暂暂停再继续）")
        self.btn_apply_files.setEnabled(False)
        self.btn_apply_files.clicked.connect(self._apply_file_selection)
        fbar.addWidget(self.btn_apply_files)
        fbar.addStretch(1)
        self.lb_files = QLabel("")
        self.lb_files.setStyleSheet("color:#6B7280;")
        fbar.addWidget(self.lb_files)
        fp.addLayout(fbar)

        self.tab_files = QTableWidget(0, 3)
        self.tab_files.setHorizontalHeaderLabels(["文件", "大小", "进度"])
        self.tab_files.verticalHeader().setVisible(False)
        self.tab_files.setShowGrid(False)
        self.tab_files.setAlternatingRowColors(True)
        self.tab_files.horizontalHeader().setStretchLastSection(False)
        self.tab_files.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tab_files.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tab_files.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.tab_files.setColumnWidth(1, 110)
        self.tab_files.setColumnWidth(2, 120)
        self.tab_files.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tab_files.itemChanged.connect(self._on_file_item_changed)
        fp.addWidget(self.tab_files, 1)
        self.detail.addTab(files_page, "文件")

        self.tab_peers = QTableWidget(0, 5)
        self.tab_peers.setHorizontalHeaderLabels(["地址", "下载", "上传", "类型", "进度"])
        self.tab_peers.verticalHeader().setVisible(False)
        self.tab_peers.setShowGrid(False)
        self.tab_peers.setAlternatingRowColors(True)
        self.tab_peers.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3, 4):
            self.tab_peers.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.tab_peers.setColumnWidth(c, 100)
        self.tab_peers.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail.addTab(self.tab_peers, "Peers")

        self.piece_map = PieceMap()
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(self.piece_map)
        self.detail.addTab(wrap, "分块")

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_box.setStyleSheet(
            "QPlainTextEdit{background:#FBFCFD;border:none;font-family:Consolas,monospace;font-size:8.5pt;}")
        self.detail.addTab(self.log_box, "日志")
        self.detail.currentChanged.connect(lambda _i: self._refresh_detail(force=True))

        right.addWidget(self.detail_host)
        self._right_splitter = right
        right.setHandleWidth(6)
        # 任务表格底部本来就有横向滚动条；splitter 的 6px handle 再画一条灰杠，
        # 看起来像"两道杠"。handle 透明，拖动功能还在，但不再多一条线。
        right.setStyleSheet("QSplitter::handle{background:transparent;}")
        right.setCollapsible(0, False)
        # 详情不靠"拖没了"来收，拖到底只到最小高度；真要全收用左上角 ▾
        right.setCollapsible(1, False)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 0)
        right.setSizes([620, 246])
        hb.addWidget(right, 1)

        self.setCentralWidget(holder)
        self._adapt_columns()

        self._detail_timer = QTimer(self)
        self._detail_timer.setInterval(900)
        self._detail_timer.timeout.connect(self._refresh_detail)
        self._detail_timer.start()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._adapt_columns()

    def eventFilter(self, obj, event):  # noqa: N802
        """吃掉表头上的双击，别让它把用户拖好的列宽"自动适配"回去。"""
        if event.type() == QEvent.Type.MouseButtonDblClick:
            hh = getattr(self, "table", None) and self.table.horizontalHeader()
            if obj is hh or (hh is not None and obj is hh.viewport()):
                return True
        return super().eventFilter(obj, event)

    def _adapt_columns(self) -> None:
        """窗口窄的时候先收起「上传速度 / 种子连接」，把位置让给文件名和备注。

        这两项在下方详情的常规页里仍然能看到，不会丢信息。
        阈值 1320 是按「备注列 180 + 其余固定列」算出来的：低于这个宽度
        再留着这两列，文件名就只剩不到 200px，全是省略号。
        """
        if not hasattr(self, "table"):
            return
        narrow = self.table.width() < 1320
        # 文档写的是收起「上传速度 / 种子连接」，这里之前只收起了 peers，
        # 导致窄窗口列宽总和超出可视区、底部冒出横向滚动条。
        for key in ("speed", "peers"):
            idx = COL_INDEX[key]
            if self.table.isColumnHidden(idx) != narrow:
                self.table.setColumnHidden(idx, narrow)

    def _build_actions(self) -> None:
        self.act_new = QAction("新建任务", self)
        self.act_add_torrent = QAction("打开种子文件…", self)
        self.act_paste = QAction("从剪贴板导入", self)

        self.act_start = QAction("开始", self)
        self.act_pause = QAction("暂停", self)
        # 工具栏 / 任务菜单用全局操作：日常真正需要的是"全员暂停/继续"
        self.act_start_all = QAction("开始全部任务", self)
        self.act_stop_all = QAction("停止全部任务", self)
        self.act_start_all.setToolTip("恢复所有暂停的任务（全局继续）")
        self.act_stop_all.setToolTip("立刻暂停所有任务，紧急让出网速")
        self.act_start_all.triggered.connect(lambda: self._apply_global("unpause"))
        self.act_stop_all.triggered.connect(lambda: self._apply_global("pause"))
        self.act_remove = QAction("删除任务", self)
        self.act_remove_file = QAction("删除任务和文件", self)

        self.act_up = QAction("上移", self)
        self.act_down = QAction("下移", self)
        self.act_top = QAction("置顶", self)
        self.act_bottom = QAction("置底", self)

        self.act_open_dir = QAction("打开目录", self)
        self.act_copy_link = QAction("复制链接", self)
        self.act_api = QAction("AI 调用接口…", self)
        # 工具栏上显示不带省略号的短名（按钮本身点开就有窗口，省略号反而啰嗦）
        self.act_api.setIconText("AI 调用接口")
        self.act_api.setToolTip("把这套接口的完整说明一键复制，粘给别的 AI 就能用")
        _ai_ico = paths.ASSET_DIR / "icons" / "ai_api.png"
        if _ai_ico.exists():
            self.act_api.setIcon(QIcon(str(_ai_ico)))
        self.act_api.triggered.connect(self._show_api_info)
        self.act_recheck = QAction("重新校验", self)
        self.act_stop_seed = QAction("停止分饭", self)
        self.act_stop_seed.setToolTip("立刻停止上传（文件保留）；想再传就「再分一轮饭」")
        self.act_stop_seed.triggered.connect(lambda: self._apply_to_selected("pause"))
        self.act_reseed = QAction("给鱼群分饭", self)
        self.act_reseed.setToolTip("让已经下完的任务重新校验一遍文件并开始上传（做种）")
        self.act_reseed.triggered.connect(self._reseed_selected)
        self.act_library = QAction("种子库…", self)
        self.act_library.setToolTip("历史种子（.torrent / 磁力）单独存这里，可以重新分饭")
        self.act_library.triggered.connect(self._open_torrent_library)
        self.act_memo = QAction("写备注", self)
        self.act_memo.setShortcut("F2")
        self.act_memo.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.act_memo.setToolTip("给这碗饭写一句人话备注（F2）")
        self.act_quit = QAction("退出", self)
        self.act_about = QAction("关于", self)

        self.act_start.triggered.connect(lambda: self._apply_to_selected("unpause"))
        self.act_pause.triggered.connect(lambda: self._apply_to_selected("pause"))
        self.act_remove.triggered.connect(lambda: self._remove_selected(delete_files=False))
        self.act_remove_file.triggered.connect(lambda: self._remove_selected(delete_files=True))
        self.act_up.triggered.connect(lambda: self._reorder(-1))
        self.act_down.triggered.connect(lambda: self._reorder(1))
        self.act_top.triggered.connect(lambda: self._reorder(-9999))
        self.act_bottom.triggered.connect(lambda: self._reorder(9999))
        self.act_open_dir.triggered.connect(self._open_dir)
        self.act_copy_link.triggered.connect(self._copy_link)
        self.act_memo.triggered.connect(self._edit_memo_of_selection)
        self.act_new.triggered.connect(lambda: self._on_new_task())
        self.act_add_torrent.triggered.connect(lambda: self._on_new_task())
        self.act_paste.triggered.connect(self._on_paste_new)
        self.act_about.triggered.connect(self._about)
        self.act_quit.triggered.connect(self._quit_app)   # 以前只加进菜单、没接槽，点了没反应
        self._update_actions()

    def _build_menubar(self) -> None:
        mb = self.menuBar()
        m_file = mb.addMenu("文件")
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_add_torrent)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_task = mb.addMenu("任务")
        m_task.addAction(self.act_start_all)
        m_task.addAction(self.act_stop_all)
        m_task.addSeparator()
        m_task.addAction(self.act_remove)
        m_task.addAction(self.act_remove_file)
        m_task.addSeparator()
        m_task.addAction(self.act_open_dir)
        m_task.addAction(self.act_copy_link)
        m_task.addAction(self.act_api)          # AI 调用接口：紧跟在复制链接后面
        m_task.addAction(self.act_recheck)
        m_task.addAction(self.act_memo)
        m_task.addSeparator()
        m_task.addAction(self.act_reseed)
        m_task.addAction(self.act_library)
        m_task.addSeparator()
        m_task.addAction("打开方式…", self._open_with_dialog_for_selection)

        m_view = mb.addMenu("视图")
        m_view.addAction("刷新", self._poke)
        m_view.addAction("显示 / 隐藏桌宠", self._toggle_pet)
        m_view.addAction("打开日志文件", self._open_log)

        m_tools = mb.addMenu("工具")
        m_tools.addAction("自检…", self._show_selfcheck)
        m_tools.addAction("任务记录自检…", self._show_task_audit)
        m_tools.addAction("选项…", self._open_options)
        m_tools.addSeparator()
        m_tools.addAction("打开下载目录", self._open_download_dir)

        m_help = mb.addMenu("帮助")
        m_help.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        # 保持用户熟悉的那"一排"：只把开始/暂停换成全局操作、限速挪到它们后面、
        # 原来限速的位置换成召唤鲸鱼娘，其他按钮顺序尽量不动。
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(24, 24))
        tb.setStyleSheet("QToolBar{padding:2px;} QToolButton{font-size:12px;padding:1px 4px;}")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)

        btn_new = QToolButton()
        btn_new.setText("新建任务")
        btn_new.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        # 按本体 = 直接开新建窗口；只有右边的箭头才展开菜单。
        # MenuButtonPopup 下如果没有默认 action，点本体是「什么都不发生」——
        # 这里显式接 clicked，别让人以为按钮坏了。
        btn_new.clicked.connect(self._on_new_task)
        m = QMenu(btn_new)
        m.addAction("磁力 / 链接…", lambda: self._on_new_task())
        m.addAction(self.act_add_torrent)
        m.addAction(self.act_paste)
        btn_new.setMenu(m)
        tb.addWidget(btn_new)
        tb.addSeparator()

        for a in (self.act_start_all, self.act_stop_all):
            tb.addAction(a)
        # 工具栏按钮文字分两行，给左栏这一排省横向空间
        for a, two in ((self.act_start_all, "开始\n全部任务"),
                       (self.act_stop_all, "停止\n全部任务")):
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton):
                w.setText(two)
        # 限速紧跟在"停止全部任务"后面：紧急暂停网速时下一个动作就是限速
        self.btn_speed = QToolButton()
        self.btn_speed.setToolTip("点开调整全局下载限速")
        self.btn_speed.clicked.connect(self._show_speed_panel)
        tb.addWidget(self.btn_speed)
        self.speed_panel = _SpeedLimitPanel(self)
        self.speed_panel.changed.connect(self._apply_speed_limit)
        tb.addSeparator()

        for a in (self.act_up, self.act_down):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_open_dir, self.act_copy_link):
            tb.addAction(a)
        # 用户要的是**工具栏上**「复制链接」后面单独一个按钮，不是藏在菜单里
        tb.addSeparator()
        tb.addAction(self.act_api)
        _api_w = tb.widgetForAction(self.act_api)
        if isinstance(_api_w, QToolButton):
            _api_w.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            _api_w.setIconSize(QSize(50, 50))
            # 文字分两行：横向省空间，图标才能放大
            _api_w.setText("外部AI\n调用接口")

        # 「大肥鱼干饭手册」= 直接打开设置。
        # 带图、带文字，比别的按钮显眼 —— 目的就是让人愿意点进去把软件调成自己的。
        btn_book = QToolButton()
        btn_book.setText("大肥鱼\n干饭手册")
        _ico = paths.ASSET_DIR / "icons" / "handbook.png"
        if _ico.exists():
            btn_book.setIcon(QIcon(str(_ico)))
            btn_book.setIconSize(QSize(50, 50))
            btn_book.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn_book.setToolTip("打开设置：下载目录、限速、分饭时长、提示音、桌宠换图…")
        btn_book.clicked.connect(self._open_options)
        tb.addWidget(btn_book)

        w = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(w)

        # 原来的限速位置换成"召唤鲸鱼娘"：桌宠被藏起来时一键叫回来
        self.btn_summon = QToolButton()
        self.btn_summon.setText("召唤\n鲸鱼娘")
        _sum_ico = paths.ASSET_DIR / "icons" / "summon_whale.png"
        if _sum_ico.exists():
            self.btn_summon.setIcon(QIcon(str(_sum_ico)))
            self.btn_summon.setIconSize(QSize(50, 50))
            self.btn_summon.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_summon.setToolTip("把藏起来的桌宠叫回来并置顶")
        self.btn_summon.clicked.connect(self._summon_pet)
        tb.addWidget(self.btn_summon)

        tb.addWidget(QLabel("  筛选："))
        self.search = QLineEdit()
        self.search.setPlaceholderText("按名称 / 备注筛选")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(140)
        self.search.textChanged.connect(self._apply_filter)
        tb.addWidget(self.search)

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lb_state = QLabel("引擎启动中…")
        self.lb_speed = QLabel("↓ 0 B/s　↑ 0 B/s")
        self.lb_conn = QLabel("连接 0")
        self.lb_limit = QLabel("限速 关")
        sb.addWidget(self.lb_state)
        sb.addPermanentWidget(self.lb_conn)
        sb.addPermanentWidget(self.lb_speed)
        sb.addPermanentWidget(self.lb_limit)

    # ================= 轮询 =================
    def _start_polling(self) -> None:
        assert self.engine.rpc is not None
        self.worker = PollWorker(self.engine.rpc, self.store, self)
        self.worker.snapshot.connect(self._on_snapshot)
        self.worker.failed.connect(self._on_poll_failed)
        self.worker.start()

    def _poke(self) -> None:
        if hasattr(self, "worker"):
            self.worker.poke()

    # ================= AI 接口 / 桌宠 =================
    def _start_api(self) -> None:
        try:
            self.api = ApiServer(self.engine.rpc, self.store, self.settings)  # type: ignore[arg-type]
            self.api_port = self.api.start()
        except Exception as exc:  # noqa: BLE001
            self.api = None
            self.lb_state.setText("AI 接口未启动：%s" % str(exc)[:50])

    def _init_pet(self) -> None:
        self.pet = PetWindow()
        self.pet.quick_add.connect(self._quick_add_text)
        self.pet.show_main.connect(self._restore_window)
        self.pet.toggle_all.connect(self._toggle_all)
        self.pet.quit_app.connect(self._quit_app)
        if self.settings.get("show_pet", True):
            self.pet.show()

    # ================= 托盘 / 通知 / 选项 =================
    def _init_tray(self) -> None:
        ico = paths.ASSET_DIR / "icons" / "whale.ico"
        self.tray = QSystemTrayIcon(QIcon(str(ico)) if ico.exists() else self.windowIcon(), self)
        self.tray.setToolTip("%s · %s" % (brand.APP_NAME, brand.TAGLINE))
        m = QMenu()
        m.addAction("显示主窗口", self._restore_window)
        m.addAction("新建任务…", lambda: self._on_new_task())
        m.addAction("从剪贴板添加", self._on_paste_new)
        m.addSeparator()
        m.addAction("全部开始 / 暂停", self._toggle_all)
        m.addSeparator()
        m.addAction("选项…", self._open_options)
        m.addAction("退出", self._quit_app)
        # Qt 不接管菜单所有权：不存一份引用，Python 一回收，托盘右键就空了
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _toggle_detail(self) -> None:
        """详情面板一键收起/展开：收起后只留一排标签，再点恢复。"""
        sp = getattr(self, "_right_splitter", None)
        if sp is None:
            return
        sizes = sp.sizes()
        total = sum(sizes) or 1
        cur = sizes[1] if len(sizes) > 1 else 0
        if cur > 80:
            self._detail_saved_height = cur
            sp.setSizes([max(1, total - 36), 36])
            self.btn_detail_toggle.setText("▴")
            self.btn_detail_toggle.setToolTip("展开下面的详情面板")
        else:
            h = max(140, min(getattr(self, "_detail_saved_height", 246), total - 120))
            sp.setSizes([max(1, total - h), h])
            self.btn_detail_toggle.setText("▾")
            self.btn_detail_toggle.setToolTip("收起下面的详情面板，多看几行任务")

    def _schedule_tray_promote(self) -> None:
        """安装版启动后，尽量把托盘图标从 Win11 的「^」溢出区钉出来。"""
        if not self.settings.get("tray_promote", True):
            return
        if not shell_integration.is_tray_promotable():
            return
        self._tray_promote_tries = 0
        QTimer.singleShot(1500, self._try_promote_tray)

    def _try_promote_tray(self) -> None:
        self._tray_promote_tries = getattr(self, "_tray_promote_tries", 0) + 1
        if shell_integration.promote_tray_icon():
            return
        # Explorer 还没把图标登记进 NotifyIconSettings 时，过两秒再试
        if self._tray_promote_tries < 6:
            QTimer.singleShot(2000, self._try_promote_tray)

    def _init_notifier(self) -> None:
        self.notifier = Notifier(self.settings, self)
        self.notifier.set_tray(self.tray)

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self._restore_window()

    def _quit_app(self) -> None:
        """真正退出程序：文件菜单 / 托盘菜单 / 桌宠右键三处都走这里。"""
        self._really_quit = True
        try:
            self.lb_state.setText("正在退出…")
            QApplication.processEvents()      # 先把这句画出来，别让人以为点了没反应
        except Exception:                     # noqa: BLE001
            pass
        if self.tray is not None:
            self.tray.hide()
        self.close()
        # 兜底：桌宠/托盘都是 Tool 窗口，个别情况下"最后一个窗口关闭"
        # 不会让事件循环退出（项目里踩过 app.quit() 不返回的坑），显式再收一次尾。
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def _open_options(self) -> None:
        dlg = OptionsDialog(self.settings, self, on_apply=self._apply_settings,
                            on_pet_changed=self._reload_pet_skins)
        dlg.exec()

    def _reload_pet_skins(self) -> None:
        """选项里换了桌宠图：立刻按新图重画，不用重启。"""
        if self.pet:
            self.pet.reload_skins()

    def _apply_settings(self) -> None:
        # 用户点了「确定」才动注册表：开机自启开关
        try:
            want = bool(self.settings.get("autostart", True))
            if want != shell_integration.is_autostart():
                shell_integration.set_autostart(want)
        except Exception:  # noqa: BLE001
            pass
        config.save(self.settings)
        try:
            self.engine.settings.update(self.settings)
            self.engine.apply_hot()
        except Exception:  # noqa: BLE001
            pass
        if self.pet:
            self.pet.setVisible(bool(self.settings.get("show_pet", True)))
        self._sync_speed_combo()
        self._reload_background()

    def _apply_bg_veil(self) -> None:
        """背景白纱浓度：同时调底图和任务表格的半透明白底。"""
        try:
            veil = max(0, min(100, int(self.settings.get("bg_veil", 50)))) / 100.0
        except (TypeError, ValueError):
            veil = 0.5
        panel = 0.28 + veil * 0.67
        alt = max(0.20, panel - 0.05)
        self._backdrop.set_veil(veil * 0.82)
        self.table.setStyleSheet(
            "QTableView{background:rgba(255,255,255,%.3f);border:none;"
            "gridline-color:transparent;selection-background-color:rgba(237,241,255,0.96);"
            "selection-color:%s;alternate-background-color:rgba(251,252,253,%.3f);outline:0;}"
            "QTableView::item{padding:0 8px;border:none;}"
            % (panel, theme.TEXT, alt))

    def _init_background(self) -> None:
        self._bg_path = None
        if self.settings.get("bg_blank"):
            self._backdrop.set_image(None)
        else:
            self._next_background()
        self._apply_bg_veil()
        self._sync_bg_timer()

    def _set_blank_background(self, blank: bool) -> None:
        self.settings["bg_blank"] = bool(blank)
        if blank:
            self._bg_path = None
            self._backdrop.set_image(None)
            self._bg_timer.stop()
        else:
            self._reload_background()

    def _sync_bg_timer(self) -> None:
        if self.settings.get("bg_blank"):
            self._bg_timer.stop()
            return
        if not self.settings.get("bg_timer_on", True):
            self._bg_timer.stop()
            return
        try:
            mins = max(1, int(self.settings.get("bg_interval_min", 3) or 3))
        except (TypeError, ValueError):
            mins = 3
        self._bg_timer.start(mins * 60000)

    def _bg_anchor(self, path) -> tuple[float, float]:
        anchors = self.settings.get("bg_anchors") or {}
        a = anchors.get(Path(path).name) or [0.5, 0.5]
        try:
            return max(0.0, min(1.0, float(a[0]))), max(0.0, min(1.0, float(a[1])))
        except (TypeError, ValueError, IndexError):
            return 0.5, 0.5

    def _bg_crop(self, path) -> list[float] | None:
        crops = self.settings.get("bg_crops") or {}
        c = crops.get(Path(path).name)
        if isinstance(c, (list, tuple)) and len(c) == 4:
            try:
                return [max(0.0, min(1.0, float(v))) for v in c]
            except (TypeError, ValueError):
                return None
        return None

    def _apply_background_path(self, path) -> None:
        """设置页里“设为当前背景”按钮：立刻切到这张图。"""
        self._bg_path = Path(path)
        self.settings["bg_current"] = self._bg_path.name
        self._backdrop.set_image(self._bg_path, self._bg_anchor(self._bg_path),
                                 self._bg_crop(self._bg_path))

    def _next_background(self) -> None:
        if self.settings.get("bg_blank"):
            self._bg_path = None
            self._backdrop.set_image(None)
            return
        imgs = background.images()
        if not imgs:
            self._bg_path = None
            self._backdrop.set_image(None)
            return
        if self.settings.get("bg_random", True) or self._bg_path is None:
            p = background.pick(exclude=self._bg_path)
        else:
            p = imgs[0]
        if p is not None:
            self._apply_background_path(p)

    def _reload_background(self) -> None:
        """设置页关掉后立即生效：优先保持用户刚选的当前背景。

        以前这里无条件重新随机，导致“设为当前背景”只闪一下又跳回旧图。
        """
        cur = str(self.settings.get("bg_current") or "")
        if self.settings.get("bg_blank"):
            self._bg_path = None
            self._backdrop.set_image(None)
            self._apply_bg_veil()
            self._sync_bg_timer()
            return
        cur_path = background.folder() / cur if cur else None
        if cur_path is not None and cur_path.is_file():
            self._apply_background_path(cur_path)
        else:
            self._bg_path = None
            self._next_background()
        self._apply_bg_veil()
        self._sync_bg_timer()

    def _show_speed_panel(self) -> None:
        if self.speed_panel.isVisible():
            self.speed_panel.hide()
            return
        self._sync_speed_combo()
        p = self.btn_speed.mapToGlobal(QPoint(0, self.btn_speed.height() + 4))
        scr = QApplication.screenAt(p) or self.screen()
        if scr:
            g = scr.availableGeometry()
            p.setX(max(g.left() + 4, min(p.x(), g.right() - self.speed_panel.width() - 4)))
            p.setY(min(p.y(), g.bottom() - self.speed_panel.height() - 4))
        self.speed_panel.move(p)
        self.speed_panel.show()
        self.speed_panel.raise_()

    def _apply_speed_limit(self, value: int) -> None:
        self.settings["max_dl"] = int(value or 0)
        config.save(self.settings)
        if self.engine.rpc:
            try:
                self.engine.rpc.change_global_option(
                    {"max-overall-download-limit": str(self.settings["max_dl"])})
            except Aria2Error:
                pass
        self._sync_speed_combo()

    def _sync_speed_combo(self) -> None:
        v = int(self.settings.get("max_dl", 0) or 0)
        if hasattr(self, "speed_panel"):
            self.speed_panel.set_limit(v)
        if not hasattr(self, "btn_speed"):
            return
        if v == 0:
            text = "限速 ▾\n不限"
            state = "关"
        elif v <= 1024:
            text = "限速 ▾\n禁止"
            state = "禁止"
        else:
            mb = max(1, round(v / (1024 * 1024)))
            text = "限速 ▾\n%d MB/s" % mb
            state = "%d MB/s" % mb
        self.btn_speed.setText(text)
        self.lb_limit.setText("限速 %s" % state)

    def _toggle_pet(self) -> None:
        if not self.pet:
            return
        if self.pet.isVisible():
            self.pet.hide()
            self.settings["show_pet"] = False
        else:
            self.pet.show()
            self.settings["show_pet"] = True
        config.save(self.settings)

    def _summon_pet(self) -> None:
        """把藏起来/收成一条边的桌宠唤回正常悬浮状态。"""
        if not self.pet:
            return
        # 顺序必须是先展开、再取消贴边：_undock() 会清掉 _collapsed，
        # 先 undock 的话 _expand() 会被跳过，窗口就放大在屏幕边缘看不见。
        if getattr(self.pet, "_collapsed", False):
            self.pet._expand()
        if getattr(self.pet, "_dock", None):
            self.pet._undock()
        self.pet._want_visible = True
        self.pet._hidden_by_fullscreen = False
        self.pet.show()
        self.pet.raise_()
        self.pet.activateWindow()
        # 双屏/鼠标跨屏后窗口可能停在虚拟桌面外，强制夹回可见屏幕
        self.pet.ensure_visible()
        self.pet._expand_until = time.time() + 4.0
        self.pet._collapse_timer.stop()
        self.settings["show_pet"] = True
        config.save(self.settings)

    def _restore_saved_geometry(self) -> None:
        """恢复上次退出时的窗口大小/位置/最大化状态。"""
        raw = str(self.settings.get("window_geometry") or "")
        if raw:
            try:
                self.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
            except Exception:  # noqa: BLE001
                pass
        self._window_was_maximized = bool(self.settings.get("window_maximized", False))
        if self._window_was_maximized:
            self._apply_maximized_on_show = True

    def _save_window_state(self) -> None:
        try:
            self.settings["window_geometry"] = bytes(
                self.saveGeometry().toBase64()).decode("ascii")
            self.settings["window_maximized"] = bool(
                self.isMaximized() or self._window_was_maximized)
            config.save(self.settings)
        except Exception:  # noqa: BLE001
            pass

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_apply_maximized_on_show", False):
            self._apply_maximized_on_show = False
            QTimer.singleShot(0, self.showMaximized)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._window_was_maximized = self.isMaximized()

    def _restore_window(self) -> None:
        if getattr(self, "_window_was_maximized", False):
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def _is_current_task_text(self, text: str) -> bool:
        """剪贴板里的东西是不是已经在当前列表里（下载中/排队/做种都算）。"""
        t = (text or "").strip()
        if not t:
            return False
        ih = dedupe.magnet_hash(t)
        for r in getattr(self, "all_rows", []):
            m = self.store.get(r.gid) or {}
            # 本地 tasks.json 可能没记 ihash（磁力任务尤其常见），
            # 依次从 uri / 任务行 / aria2 的 infoHash 算，剪贴板只差 tracker 也能认出来
            cands = {str(m.get("ihash") or "").lower(),
                     (dedupe.magnet_hash(str(m.get("uri") or "")) or "").lower(),
                     (dedupe.magnet_hash(str(getattr(r, "uri", "") or "")) or "").lower()}
            if ih and ih in cands:
                return True
            try:
                st = self.engine.rpc.call("aria2.tellStatus", [r.gid, ["infoHash"]])
                if isinstance(st, dict) and st.get("infoHash"):
                    cands.add(str(st["infoHash"]).lower())
            except Aria2Error:
                pass
            if ih and ih in cands:
                return True
            if t == str(m.get("uri") or "") or t == str(getattr(r, "uri", "") or "") \
                    or t == r.name:
                return True
        return False

    def _quick_add_text(self, text: str) -> None:
        """桌宠收到链接/种子：不弹窗、不打扰，直接投递到默认目录。"""
        # 已经在列表里的链接不要再弹「这个好像下过」的中断框；
        # 桌宠双击时直接把主窗口叫出来，小白用户不会卡在确认弹窗里。
        if self._is_current_task_text(text):
            self._restore_window()
            if self.pet:
                self.pet.toast("这碗已经在桌上了")
            return
        n = self.import_external([text])
        if self.pet:
            if n:
                self.pet.toast("接到 %d 碗" % n)
            elif getattr(self, "_last_dupes", 0):
                self.pet.toast("这碗已经在桌上了")
            else:
                self.pet.toast("没认出能下的东西")

    # ---------- 外部导入（拖放 / 剪贴板 / 命令行 / 单实例转发） ----------
    def _default_opts(self) -> dict[str, str]:
        s = self.settings
        opts: dict[str, str] = {"dir": s.get("download_dir") or str(paths.DL_DIR)}
        if int(s.get("per_task_ul") or 0) > 0:
            opts["max-upload-limit"] = str(int(s["per_task_ul"]))
        if int(s.get("per_task_dl") or 0) > 0:
            opts["max-download-limit"] = str(int(s["per_task_dl"]))
        return opts

    def import_external(self, items: list[str]) -> int:
        """把一堆「可能是路径、也可能是整段分享文案」的东西塞进引擎。"""
        rpc = self.engine.rpc
        if not rpc:
            return 0
        opts = self._default_opts()
        torrents: list[Path] = []
        links: list[intents.Link] = []
        seen: set[str] = set()

        for raw in items:
            it = (raw or "").strip()
            if not it:
                continue
            if it.lower().endswith(".torrent"):
                p = Path(it)
                if p.exists() and p not in torrents:
                    torrents.append(p)
                    continue
            for link in intents.extract_links(it):
                key = link.value.lower()
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)

        items = [{"uri": lk.value, "ihash": dedupe.magnet_hash(lk.value)}
                 for lk in links if lk.kind != "torrent"]
        for path in torrents:
            try:
                items.append({"uri": "(torrent) %s" % path.name, "name": path.name,
                              "ihash": torrent_keep.infohash_of(path.read_bytes())})
            except OSError:
                pass
        if not self._check_dupes(items):
            self.lb_state.setText("已取消：这个已经在列表里或以前下过")
            return 0

        n = 0
        dupes = 0
        for path in torrents:
            try:
                b64 = base64.b64encode(path.read_bytes()).decode("ascii")
                gid, why = add_checked(rpc, "aria2.addTorrent", [b64, [], opts])
                if not gid:
                    dupes += 1
                    continue
                torrent_keep.save(gid, b64, source=str(path))   # 进历史种子库
                self.store.note(gid, uri="(torrent) %s" % path.name, name=path.name,
                                ihash=torrent_keep.infohash_of(path.read_bytes()),
                                dir=opts.get("dir", ""))
                n += 1
            except (OSError, Aria2Error):
                pass
        for link in links:
            try:
                if link.kind == "torrent":
                    p = Path(link.value)
                    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                    gid, why = add_checked(rpc, "aria2.addTorrent", [b64, [], opts])
                    if not gid:
                        dupes += 1
                        continue
                    torrent_keep.save(gid, b64, source=str(p))
                    self.store.note(gid, uri="(torrent) %s" % p.name, name=p.name,
                                    ihash=torrent_keep.infohash_of(p.read_bytes()),
                                    dir=opts.get("dir", ""))
                else:
                    gid, why = add_checked(rpc, "aria2.addUri", [[link.value], opts])
                    if not gid:
                        dupes += 1
                        continue
                    self.store.note(gid, uri=link.value,
                                    ihash=dedupe.magnet_hash(link.value),
                                    dir=opts.get("dir", ""))
                    if link.value.lower().startswith("magnet:"):
                        torrent_keep.save_magnet(link.value, gid=gid,
                                                 dir_=opts.get("dir", ""))
                n += 1
            except (OSError, Aria2Error):
                pass
        self.store.save()
        self._last_dupes = dupes
        if n:
            self._poke()
        return n

    def handle_external(self, args: list[str]) -> None:
        """第二个实例（双击 .torrent / 命令行）把参数转进来。"""
        if not args:
            return
        n = self.import_external(args)
        if n:
            self._restore_window()
            self.lb_state.setText("收到 %d 个新任务" % n)

    # ---------- 拖放 ----------
    def dragEnterEvent(self, event):  # noqa: N802
        md = event.mimeData()
        if md.hasUrls() or md.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):  # noqa: N802
        md = event.mimeData()
        items: list[str] = []
        if md.hasUrls():
            for u in md.urls():
                items.append(u.toLocalFile() if u.isLocalFile() else u.toString())
        if md.hasText():
            items.append(md.text())
        n = self.import_external(items)
        self.lb_state.setText("收到 %d 个新任务" % n if n else "没认出能下的东西")
        if self.pet:
            self.pet.toast("接到 %d 碗" % n if n else "没认出能下的东西")
        event.acceptProposedAction()

    def _toggle_all(self) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        actives = [r for r in self.all_rows if r.status_code == "active"]
        try:
            if actives:
                for r in actives:
                    rpc.call("aria2.forcePause", [r.gid])
            else:
                for r in self.all_rows:
                    if r.status_code in ("paused", "waiting"):
                        rpc.call("aria2.unpause", [r.gid])
        except Aria2Error:
            pass
        self._poke()

    def _show_selfcheck(self) -> None:
        """工具 → 自检：把安装/路径/token/端口/引擎/说明渲染都过一遍。"""
        guide = self._api_guide_text() if self.api else ""
        rep = selfcheck.run_runtime(api_port=self.api_port, guide_text=guide,
                                    engine=self.engine)
        box = QMessageBox(self)
        box.setWindowTitle("程序自检")
        box.setText("全部通过 ✓" if rep["ok"] else "发现需要处理的问题 ✗")
        box.setDetailedText(rep["report"])
        btn_copy = box.addButton("复制自检报告", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is btn_copy:
            QApplication.clipboard().setText(rep["report"])
            self.lb_state.setText("自检报告已复制")

    def _show_task_audit(self) -> None:
        """任务记录自检：确认每条历史记录都还能在列表里看到。"""
        hist = self._build_history_rows()
        self._history_cache = hist
        active = {r.gid for r in self.all_rows if not r.is_history}
        listed = active | {h.gid for h in hist}
        metas = self.store.all() or {}
        unseen = [g for g in metas if g not in listed]
        missing = [h for h in hist if h.missing]
        lines = ["当前列表任务：%d 条" % len(self.all_rows),
                 "历史记录补回：%d 条" % len(hist),
                 "磁盘文件找不到：%d 条" % len(missing),
                 "存在记录但未显示：%d 条" % len(unseen)]
        if missing:
            lines.append("\n文件不在了（记录仍保留）：")
            lines += ["· %s" % h.name for h in missing[:8]]
        if unseen:
            lines.append("\n未显示记录 gid：")
            lines += ["· %s" % g for g in unseen[:8]]
        report = "\n".join(lines)
        box = QMessageBox(self)
        box.setWindowTitle("任务记录自检")
        box.setIcon(QMessageBox.Icon.Information if not (missing or unseen)
                    else QMessageBox.Icon.Warning)
        box.setText("所有任务记录都能在列表里看到 ✓" if not (missing or unseen)
                    else "有记录需要留意")
        box.setDetailedText(report)
        btn_copy = box.addButton("复制自检报告", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is btn_copy:
            QApplication.clipboard().setText(report)
            self.lb_state.setText("任务记录自检报告已复制")
        self._poke()

    def _show_api_info(self) -> None:
        if not self.api:
            QMessageBox.warning(self, "AI 接口", "接口没有启动，请检查端口占用（默认 6811）")
            return
        tok = self.api.ctx.token
        txt = (
            "本机接口已就绪，任何 AI / 脚本都能直接调用：\n\n"
            "地址：http://127.0.0.1:%d\n"
            "令牌：%s\n"
            "令牌文件：%s\n\n"
            "投递一个磁力：\n"
            "curl -X POST http://127.0.0.1:%d/api/v1/tasks \\\n"
            "  -H \"X-Auth-Token: %s\" -H \"Content-Type: application/json\" \\\n"
            "  -d '{\"urls\":[\"magnet:?xt=urn:btih:...\"]}'\n\n"
            "等它真正下完并校验：\n"
            "curl \"http://127.0.0.1:%d/api/v1/wait/<gid>?timeout=1800&token=%s\"\n\n"
            "接口自述：GET /api/v1/describe"
        ) % (self.api_port, tok, paths.API_TOKEN_FILE, self.api_port,
             tok, self.api_port, tok)
        box = QMessageBox(self)
        box.setWindowTitle("AI 调用接口")
        box.setText(txt)
        box.setDetailedText(txt)
        btn_copy = box.addButton("一键复制内容发给其他AI",
                                 QMessageBox.ButtonRole.ActionRole)
        btn_copy.setToolTip("把整份说明（含本机地址、令牌、下载目录、用法、字段表）"
                            "复制到剪贴板，直接粘给别的 AI")
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is btn_copy:
            guide = self._api_guide_text()
            # 复制这一刻按当前安装目录重新自检：换目录重装后 token/端口/下载目录
            # 都会变，不能把上一份安装的旧值粘给别的 AI。
            rep = selfcheck.run_runtime(api_port=self.api_port, guide_text=guide,
                                        engine=self.engine)
            if not rep["ok"]:
                guide += ("\n\n---\n## 复制时自检警告（这台机器）\n"
                          + rep["report"] + "\n")
                self.lb_state.setText("接口说明已复制，但自检有警告（见文末）")
            else:
                self.lb_state.setText("接口说明已复制，自检通过")
            QApplication.clipboard().setText(guide)

    def _api_guide_text(self) -> str:
        """整份给 AI 看的说明（Markdown），占位符换成这台机器的真实值。"""
        f = paths.ASSET_DIR / "ai-guide.md"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            text = "# %s AI 调用说明\n\nPOST /api/v1/tasks {\"urls\":[...]}\n" % brand.APP_NAME
        return (text.replace("@PORT@", str(self.api_port))
                    .replace("@TOKEN_FILE@", str(paths.API_TOKEN_FILE))
                    .replace("@DOWNLOAD_DIR@", str(paths.DL_DIR))
                    .replace("@VERSION@", brand.APP_VERSION))

    # ================= 关于 =================
    def _about(self) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QLabel
        dlg = QDialog(self)
        dlg.setWindowTitle("关于 " + brand.APP_NAME)
        dlg.setFixedWidth(420)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(8)

        art = QLabel()
        # 以前这里写的是 full.png —— 那张图 v0.9.4 按用户要求删了，于是"关于"
        # 页顶上一直空着。改成招牌那张 ciallo，找不到再退到头像。
        for _name in ("ciallo", "face"):
            _p = pet_skin.sprite_path(_name)
            if _p.exists():
                _pm = QPixmap(str(_p))
                if not _pm.isNull():
                    art.setPixmap(_pm.scaled(
                        200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                    break
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(art)

        title = QLabel("<span style='font-size:17pt;font-weight:600'>%s</span><br>"
                       "<span style='color:#6B7280'>%s</span>" % (brand.APP_NAME, brand.TAGLINE))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        try:
            aria_ver = self.engine.version() if hasattr(self.engine, "version") else ""
        except Exception:  # noqa: BLE001
            aria_ver = ""
        info = QLabel(
            "版本 %s　·　aria2 %s<br>"
            "引擎 127.0.0.1:%d　·　AI 接口 127.0.0.1:%s<br><br>"
            "<span style='color:#9AA3AF;font-size:8.5pt'>"
            "作者 B站天才俱乐部　·　仅作个人使用"
            % (brand.APP_VERSION, aria_ver or "1.37.0", paths.RPC_PORT,
               str(self.api_port or "未启动")))
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        lay.addWidget(info)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("开饭")
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    # ================= 新建任务 =================
    def _on_new_task(self, preset: str = "") -> None:
        dlg = AddDialog(self.settings, self, preset)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.request:
            self._submit(dlg.request)

    def _on_paste_new(self) -> None:
        from PySide6.QtWidgets import QApplication
        self._on_new_task(QApplication.clipboard().text().strip())

    def _check_dupes(self, items: list[dict]) -> bool:
        """加任务前问一句：这个以前是不是下过？

        items 每项：{"uri": 链接, "ihash": infohash, "name": 文件名}
        返回 True = 继续加；False = 用户点了取消。
        只提示、不拦：硬盘上那份可能已经被删了，也可能他就想再存一份。
        注意：正在列表里的任务（包括还在解析的 METADATA）不算“以前下过”；
        只有真正下载完成、记过 finished_at 的历史才弹这个框。
        """
        for it in items:
            uri = str(it.get("uri") or "")
            if uri and self._is_current_task_text(uri):
                self.lb_state.setText("这个已经在列表里，没重复添加")
                return False
        metas = {gid: m for gid, m in (self.store.all() or {}).items()
                 if isinstance(m, dict) and m.get("finished_at")}
        hits = []
        for it in items:
            hit = dedupe.find_previous(metas, ihash=it.get("ihash", ""),
                                       uri=it.get("uri", ""), name=it.get("name", ""))
            if hit and hit[0] not in [h[0] for h in hits]:
                hits.append(hit)
        if not hits:
            return True
        lines = ["%s" % dedupe.describe(m) for _g, m in hits[:4]]
        if len(hits) > 4:
            lines.append("…… 另外还有 %d 个也下过" % (len(hits) - 4))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("以前下载完成过")
        box.setText("这些以前已经下载完成过：\n\n" + "\n".join(lines))
        box.setInformativeText("文件如果还在硬盘上，就不要再下一遍；\n"
                               "想再存一份、或者原来的已经删了，也可以继续。")
        btn_yes = box.addButton("还是再下一份", QMessageBox.ButtonRole.AcceptRole)
        btn_no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_no)
        box.exec()
        return box.clickedButton() is btn_yes

    def _submit(self, req: TaskRequest) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        opts: dict[str, str] = {"dir": req.dir}
        if req.per_dl:
            opts["max-download-limit"] = str(req.per_dl)
        if req.per_ul:
            opts["max-upload-limit"] = str(req.per_ul)
        if req.paused:
            opts["pause"] = "true"

        hot: dict[str, str] = {}
        if req.max_concurrent:
            hot["max-concurrent-downloads"] = str(req.max_concurrent)
            self.settings["max_concurrent"] = req.max_concurrent
        if req.max_conn:
            hot["max-connection-per-server"] = str(req.max_conn)
            self.settings["max_conn_per_server"] = req.max_conn
        if hot:
            try:
                rpc.change_global_option(hot)
            except Aria2Error:
                pass

        # 查重：磁力/种子用 infohash 认，普通链接用原文认
        items = [{"uri": u, "ihash": dedupe.magnet_hash(u)} for u in req.uris]
        for name, b64 in req.torrents:
            items.append({"uri": "(torrent) %s" % name, "name": name,
                          "ihash": torrent_keep.infohash_of(base64.b64decode(b64))})
        if not self._check_dupes(items):
            self.lb_state.setText("已取消：这个已经在列表里或以前下过")
            return

        n = 0
        dupes = 0
        err = ""
        for name, b64 in req.torrents:
            try:
                gid, why = add_checked(rpc, "aria2.addTorrent", [b64, [], opts])
                if not gid:
                    dupes += 1
                    continue
                torrent_keep.save(gid, b64, source="种子文件")
                self.store.note(gid, uri="(torrent) %s" % name, name=name,
                                ihash=torrent_keep.infohash_of(base64.b64decode(b64)),
                                dir=req.dir)
                n += 1
                if req.choose_files:
                    self._pick_files_soon(gid, unpause=not req.paused)
            except Aria2Error as exc:
                err = str(exc)
        for u in req.uris:
            extra = dict(opts)
            is_magnet = u.lower().startswith("magnet:")
            if is_magnet and (req.pause_metadata or req.choose_files):
                # 要选文件就必须先停住，否则元数据一到就开始下全量了
                extra["pause-metadata"] = "true"
                extra["bt-save-metadata"] = "true"
            try:
                gid, why = add_checked(rpc, "aria2.addUri", [[u], extra])
                if not gid:
                    dupes += 1
                    continue
                self.store.note(gid, uri=u)
                n += 1
                if is_magnet:
                    torrent_keep.save_magnet(u, gid=gid, dir_=req.dir)
                if req.choose_files and is_magnet:
                    # 磁力要先等元数据，snapshot 里再弹选文件
                    self._await_pick[gid] = {"unpause": not req.paused}
            except Aria2Error as exc:
                err = str(exc)
        self.store.save()
        self._last_dupes = dupes
        if err:
            QMessageBox.warning(self, "部分任务添加失败", err)
        if n:
            self.lb_state.setText("已添加 %d 个任务" % n
                                  + ("（%d 个之前加过了）" % dupes if dupes else ""))
        elif dupes:
            self.lb_state.setText("%d 个之前就加过了，没重复添加" % dupes)
        else:
            self.lb_state.setText("没有任务被添加")
        self._poke()

    # ================= 种子内选文件 =================
    def _pick_files_soon(self, gid: str, unpause: bool) -> None:
        QTimer.singleShot(500, lambda: self._open_file_picker(gid, unpause))

    def _open_file_picker(self, gid: str, unpause: bool = False) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        try:
            st = rpc.call("aria2.tellStatus",
                          [gid, ["files", "dir", "status", "totalLength", "bittorrent"]])
        except Aria2Error:
            return
        files = st.get("files") or []
        if len(files) <= 1:
            if unpause:
                try:
                    rpc.call("aria2.unpause", [gid])
                except Aria2Error:
                    pass
            return
        dlg = FileSelectDialog(files, st.get("dir", ""), parent=self,
                               note="这碗里共 %d 个文件。勾上的才下载，没勾的不占空间。"
                                    % len(files))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        spec = ",".join(str(i) for i in dlg.selected())
        self._change_select(gid, spec, unpause)

    def _change_select(self, gid: str, spec: str, unpause: bool) -> bool:
        rpc = self.engine.rpc
        if not rpc:
            return False
        try:
            st = rpc.call("aria2.tellStatus", [gid, ["status"]])
            status = st.get("status", "")
            need_pause = status == "active"
            if need_pause:
                rpc.call("aria2.forcePause", [gid])
            rpc.call("aria2.changeOption", [gid, {"select-file": spec}])
            if need_pause or unpause:
                rpc.call("aria2.unpause", [gid])
            return True
        except Aria2Error as exc:
            QMessageBox.warning(self, "改不了文件选择", str(exc))
            return False

    def _on_table_clicked(self, index) -> None:
        row = self.model.row_at(index.row())
        if row is None:
            return
        key = COLS[index.column()][0]
        if key == "memo":
            # 单击备注格只是选中，不弹任何东西；编辑交给双击 / 再点一次
            return
        if key == "ai_name":
            self._lookup_title_for(row)
            return
        if key == "act_dir":
            self._open_dir_of(row)
        elif key == "act_open":
            rect = self.table.visualRect(index)
            pos = self.table.viewport().mapFromGlobal(QCursor.pos())
            if pos.x() >= rect.right() - ARROW_ZONE:
                self._show_open_menu(row, self.table.viewport().mapToGlobal(
                    rect.bottomLeft()))
            else:
                self._open_file_of(row)

    # ================= 打开 / 打开方式 =================
    def _lookup_title_for(self, row: TaskRow) -> None:
        if not row.gid:
            return
        meta = self.store.get(row.gid) or {}
        ihash = str(meta.get("ihash") or "") or dedupe.magnet_hash(row.uri)
        orig_name = str(meta.get("orig_name") or row.name)
        parsed = title_parse.parse(orig_name, row.file_paths)
        can_rename = bool(row.is_finished and not row.error and row.size > 0)
        dlg = TitleLookupDialog(self, parsed, row.name,
                                allow_disk_rename=can_rename, ihash=ihash,
                                settings=self.settings, original_name=orig_name,
                                task_key=row.gid)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.restore_original:
            keep = "" if row.name == orig_name else orig_name
            self.store.set_title(row.gid, keep)
            row.title = keep
            self.store.save()
            self.model.layoutChanged.emit()
            self._apply_filter()
            QMessageBox.information(self, "已还原",
                                    "已恢复原始显示名：\n%s" % orig_name)
            return
        if not dlg.chosen:
            return
        self.store.set_title(row.gid, dlg.chosen)
        row.title = dlg.chosen
        self.store.save()
        self.model.layoutChanged.emit()
        self._apply_filter()
        self.lb_state.setText("已更新显示名：%s" % dlg.chosen)
        if dlg.rename_disk:
            self._rename_task_disk(row, dlg.chosen)
        self._poke()

    def _safe_disk_name(self, title: str, old: Path) -> str:
        name = re.sub(r'[<>:"/\\|?*]', " ", str(title or ""))
        name = re.sub(r"\s+", " ", name).strip(" .")
        if len(name) > 180:
            name = name[:180].rstrip(" .")
        if old.is_file() and old.suffix and not name.lower().endswith(old.suffix.lower()):
            name += old.suffix
        return name or old.stem

    def _rename_task_disk(self, row: TaskRow, title: str) -> bool:
        """把已完成任务的文件/文件夹真改名；改名后停止做种，不再上传。"""
        old = Path(row.dir) / row.name
        if not old.exists():
            QMessageBox.warning(self, "改不了真实文件名", "原文件已经不在：\n%s" % old)
            return False
        if not row.is_finished:
            QMessageBox.warning(self, "还没下完", "任务还没彻底下载完成，先不要改真实文件名。")
            return False
        try:
            actual = (old.stat().st_size if old.is_file()
                      else sum(f.stat().st_size for f in old.rglob("*") if f.is_file()))
        except OSError as exc:
            QMessageBox.warning(self, "检查失败", str(exc))
            return False
        if row.size <= 0 or actual != int(row.size):
            QMessageBox.warning(
                self, "校验没通过",
                "文件/文件夹总大小和任务记录对不上，可能还没收尾或内容不完整，先不改名。\n"
                "期望 %s，实际 %s" % (fmt_size(row.size), fmt_size(actual)))
            return False

        new_name = self._safe_disk_name(title, old)
        new = old.with_name(new_name)
        if new.exists() and new != old:
            stem, suffix = new.stem, new.suffix
            i = 1
            while new.exists():
                new = old.with_name("%s (%d)%s" % (stem, i, suffix))
                i += 1
        # 先停掉做种/旧任务，避免 aria2 继续指向旧路径
        rpc = self.engine.rpc
        if rpc and row.gid:
            try:
                if row.status_code in ("active", "waiting", "paused"):
                    rpc.call("aria2.forcePause", [row.gid])
                    time.sleep(0.3)
                rpc.call("aria2.removeDownloadResult", [row.gid])
            except Aria2Error:
                pass
        try:
            old.rename(new)
        except OSError as exc:
            QMessageBox.warning(self, "改名失败", str(exc))
            return False
        row.name = new.name
        row.title = title
        row.status_code = "complete"
        row.missing = False
        self.store.note(row.gid, name=new.name, dir=row.dir,
                        uri=row.uri, ihash=dedupe.magnet_hash(row.uri))
        self.store.set_title(row.gid, title)
        self.store.save()
        self._history_cache = None
        self.model.layoutChanged.emit()
        self._apply_filter()
        self.lb_state.setText("已改真实文件名，并停止该任务上传：%s" % new.name)
        return True

    def _error_hint(self, msg: str) -> str:
        """把 aria2 的英文错误翻译成用户能照做的处理建议。"""
        low = (msg or "").lower()
        if "control file" in low and "does not exist" in low:
            return ("噎住了：目标位置已经有同名文件，但没有 aria2 的 .aria2 控制文件，"
                    "aria2 为防止把文件覆盖成 0 字节而停下了。\n"
                    "处理：① 先打开所在目录，看看这个文件是不是已经完整；"
                    "② 如果完整可以直接播放，不需要重下；"
                    "③ 如果不完整或不需要，删掉它，再右键「开始」重下；"
                    "④ 也可以换一个保存目录。")
        if "no space" in low or "not enough" in low:
            return "噎住了：磁盘空间不够。清理一些空间后右键「开始」重试。"
        return "噎住了：" + (msg or "任务出错，但没有返回具体原因。")

    def _unique_output_name(self, folder: str, name: str, file_count: int) -> str:
        """给「原文件已存在」的任务找一个不冲突的新名字。"""
        p = Path(name)
        if int(file_count or 1) <= 1:
            stem, suffix = p.stem, p.suffix
            cand = name
            i = 1
            while (Path(folder) / cand).exists():
                cand = "%s (%d)%s" % (stem, i, suffix)
                i += 1
            return cand
        cand = name
        i = 1
        while (Path(folder) / cand).exists():
            cand = "%s (%d)" % (name, i)
            i += 1
        return cand

    def _retry_with_rename(self, row: TaskRow | None = None) -> None:
        """出错任务：保留旧文件，给新下载自动加 (1) 后缀继续跑。"""
        if row is None:
            rows = [r for r in self._selected_rows() if r.error]
            row = rows[0] if rows else None
        if row is None or not row.gid:
            return
        rpc = self.engine.rpc
        if not rpc:
            return
        meta = self.store.get(row.gid) or {}
        folder = row.dir or str(meta.get("dir") or paths.DL_DIR)
        new_name = self._unique_output_name(folder, row.name, row.file_count)
        if QMessageBox.question(
                self, "自动改名继续下载",
                "原文件不会被删除，新下载会保存为：\n\n%s\n\n继续开始吗？" % new_name
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            st = rpc.call("aria2.tellStatus", [row.gid, ["infoHash"]])
            ihash = str((st or {}).get("infoHash") or meta.get("ihash") or "")
        except Aria2Error:
            ihash = str(meta.get("ihash") or "")
        opts = {"dir": folder, "out": new_name}
        gid2 = ""
        try:
            b64 = torrent_keep.find_by_infohash(ihash) if ihash else ""
            if b64:
                gid2, why = add_checked(rpc, "aria2.addTorrent", [b64, [], opts])
            else:
                uri = str(meta.get("uri") or row.uri or "")
                if not uri:
                    QMessageBox.warning(self, "无法重试",
                                        "这个任务没有留下磁力/种子来源，无法自动重建。")
                    return
                gid2, why = add_checked(rpc, "aria2.addUri", [[uri], opts])
        except Aria2Error as exc:
            QMessageBox.warning(self, "重试失败", str(exc))
            return
        if not gid2:
            QMessageBox.warning(self, "重试失败", "aria2 没有返回新任务 id。")
            return
        self.store.note(gid2, uri=str(meta.get("uri") or row.uri or ""),
                        name=row.name, ihash=ihash, dir=folder)
        if meta.get("memo"):
            self.store.set_memo(gid2, str(meta["memo"]))
        if meta.get("title"):
            self.store.set_title(gid2, str(meta["title"]))
        try:
            rpc.call("aria2.removeDownloadResult", [row.gid])
        except Aria2Error:
            pass
        self.store.forget([row.gid])
        self.store.save()
        self._seen_errors.discard(row.gid)
        self.lb_state.setText("已改名继续下载：%s" % new_name)
        self._poke()

    def _show_error_reason(self) -> None:
        rows = [r for r in self._selected_rows() if r.error]
        if not rows:
            return
        r = rows[0]
        box = QMessageBox(self)
        box.setWindowTitle("任务出错原因")
        box.setText("任务：%s" % r.name)
        box.setInformativeText(self._error_hint(r.error))
        box.setDetailedText(r.error)
        btn_open = box.addButton("打开所在目录", QMessageBox.ButtonRole.ActionRole)
        btn_rename = box.addButton("自动改名继续下载", QMessageBox.ButtonRole.ActionRole)
        btn_copy = box.addButton("复制错误", QMessageBox.ButtonRole.ActionRole)
        box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is btn_rename:
            self._retry_with_rename(r)
        elif box.clickedButton() is btn_open:
            target = self._target_of(r)
            if target is not None:
                intents.reveal(target)
            elif r.dir:
                intents.open_path(Path(r.dir), 1)
        elif box.clickedButton() is btn_copy:
            QApplication.clipboard().setText(r.error)
            self.lb_state.setText("错误信息已复制")

    def _toast(self, text: str) -> None:
        """小气泡提示：状态栏 + 鼠标旁的小气泡 + 桌宠气泡，不弹模态框。"""
        self.lb_state.setText(text)
        try:
            QToolTip.showText(QCursor.pos(), text, self.table, self.table.rect(), 2500)
        except Exception:  # noqa: BLE001
            pass
        if self.pet is not None:
            try:
                self.pet.toast(text)
            except Exception:  # noqa: BLE001
                pass

    def _open_guard(self, row: TaskRow) -> bool:
        """能不能打开这个任务的文件。

        下载中的文件是不完整的，有些播放器直接报错/花屏；
        用户要的是：没下完就别让他打开，弹个小气泡说「尚未下载完成」。
        """
        if row.is_meta:
            self._toast("还在取种子信息，等真正的下载任务出来再打开")
            return False
        if row.error:
            self._toast("这个任务出错了，先看详情里的原因")
            return False
        if not row.is_finished:
            pct = row.percent
            self._toast("尚未下载完成（%.0f%%），下完再打开" % pct
                        if pct > 0 else "尚未下载完成，下完再打开")
            return False
        if row.missing:
            self._toast("文件已经不在原处了（可能被删除或挪走了）")
            return False
        return True

    def _target_of(self, row: TaskRow) -> Path | None:
        name = row.name if row.file_count <= 1 else ""
        return intents.resolve_target(row.dir, name, row.file_count)

    def _open_dir_of(self, row: TaskRow) -> None:
        target = self._target_of(row)
        if target is None:
            d = Path(row.dir) if row.dir else None
            if d and d.exists():
                intents.reveal(d)
            return
        intents.reveal(target)

    def _open_file_of(self, row: TaskRow) -> None:
        if not self._open_guard(row):
            return
        target = self._target_of(row)
        if target is None:
            self._toast("文件还没落盘，等一会儿再试")
            return
        if target.is_dir():
            intents.reveal(target)
            return
        if intents.open_default(target):
            self.lb_state.setText("已用默认程序打开 %s" % target.name)
        else:
            self.lb_state.setText("打开失败，可能没有关联的程序")

    def _icon_for_exe(self, exe: str) -> QIcon:
        if not exe:
            return QIcon()
        if exe in self._icon_cache:
            return self._icon_cache[exe]
        ico = self._icons.icon(QFileIconProvider.IconType.File) if not exe else None
        try:
            from PySide6.QtCore import QFileInfo
            ico = self._icons.icon(QFileInfo(exe))
        except Exception:  # noqa: BLE001
            ico = QIcon()
        self._icon_cache[exe] = ico
        return ico

    def _custom_openers(self, ext: str) -> list[str]:
        data = self.settings.get("custom_openers") or {}
        return [str(x) for x in (data.get(ext) or []) if Path(str(x)).exists()]

    def _show_open_menu(self, row: TaskRow, global_pos) -> None:
        if not self._open_guard(row):
            return
        target = self._target_of(row)
        if target is None:
            self._toast("文件还没落盘，等一会儿再试")
            return
        ext = intents.ext_of(target)
        handlers = openwith.handlers_for(target, self._custom_openers(ext))
        m = QMenu(self)
        for h in handlers:
            label = h.label + ("（默认）" if h.is_default else "")
            act = m.addAction(self._icon_for_exe(h.icon_path), label)
            act.triggered.connect(lambda _c=False, hh=h: self._run_handler(target, hh))
        if handlers:
            m.addSeparator()
        m.addAction(self._icon_for_exe(""), "用系统默认程序打开",
                    lambda: self._open_file_of(row))
        m.addAction("选择其他程序…", lambda: openwith.show_open_with_dialog(target))
        m.addSeparator()
        m.addAction("打开所在文件夹", lambda: self._open_dir_of(row))
        m.exec(global_pos)

    def _run_handler(self, target: Path, handler) -> None:
        ok, err = openwith.open_with(target, handler)
        if ok:
            self.lb_state.setText("已用 %s 打开" % handler.label)
        else:
            self.lb_state.setText("打开失败：%s" % err[:60])

    def _open_with_dialog_for_selection(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if not self._open_guard(rows[0]):
            return
        target = self._target_of(rows[0])
        if target is None:
            self._toast("文件还没落盘")
            return
        self._show_open_menu(rows[0], QCursor.pos())

    # ================= 双击打开 =================
    def _on_double_click(self, index) -> None:
        r = self.model.row_at(index.row())
        if not r:
            return
        if COLS[index.column()][0] == "memo":
            # 双击备注格是「写备注」，不是「打开文件」——别把电影放起来
            self._edit_memo(r)
            return
        if COLS[index.column()][0] == "ai_name":
            self._lookup_title_for(r)
            return
        # 只有"正在下载"的任务双击才是暂停；其他（暂停/完成/做种）都按打开处理
        if (r.status_code == "active" and not r.is_seeder
                and not r.is_meta and not r.error):
            try:
                self.engine.rpc.call("aria2.forcePause", [r.gid])
                self.lb_state.setText("已暂停：%s" % r.name)
            except Aria2Error as exc:
                self.lb_state.setText("暂停失败：%s" % str(exc)[:60])
            self._poke()
            return
        if not self._open_guard(r):
            return
        name = r.name if r.file_count <= 1 else ""
        target = intents.resolve_target(r.dir, name, r.file_count)
        msg = intents.open_path(target, r.file_count)
        self.lb_state.setText(msg)

    # ================= 备注 =================
    def _on_memo_committed(self, gid: str, text: str) -> None:
        """备注落盘。写的是 tasks.json，不动 aria2，也不动文件名。"""
        saved = self.store.set_memo(gid, text)
        self.store.save()
        for r in self.all_rows:
            if r.gid == gid:
                r.memo = saved
        for i, r in enumerate(self.visible_rows):
            if r.gid == gid:
                idx = self.model.index(i, COL_INDEX["memo"])
                self.model.dataChanged.emit(idx, idx)
        self.lb_state.setText("备注已保存" if saved else "备注已清空")
        # 搜索框在用的话，备注也要参与匹配，改完立刻重算
        if hasattr(self, "search") and self.search.text().strip():
            self._apply_filter()

    def _commit_detail_memo(self) -> None:
        """详情页那一行「备注」里的编辑框，回车 / 失焦时保存。"""
        gid = self._memo_edit_gid
        if not gid:
            return
        row = next((r for r in self.all_rows if r.gid == gid), None)
        if row is not None and self.memo_edit.text() == row.memo:
            return
        self._on_memo_committed(gid, self.memo_edit.text())

    def _on_memo_editor_closed(self, *_a) -> None:
        if self._filter_pending:
            self._filter_pending = False
            QTimer.singleShot(0, self._apply_filter)

    def _edit_memo(self, row: TaskRow) -> None:
        for i, r in enumerate(self.visible_rows):
            if r.gid == row.gid:
                idx = self.model.index(i, COL_INDEX["memo"])
                self.table.selectRow(i)
                self.table.scrollTo(idx, QAbstractItemView.ScrollHint.EnsureVisible)
                self.table.setFocus(Qt.FocusReason.OtherFocusReason)
                QTimer.singleShot(0, lambda x=idx: self.table.edit(x))
                return

    def _edit_memo_of_selection(self) -> None:
        rows = self._selected_rows()
        if rows:
            self._edit_memo(rows[0])

    def _clear_memo_of_selection(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for r in rows:
            self._on_memo_committed(r.gid, "")
        self._poke()

    # ================= 完成通知 =================
    def _task_identity(self, r: TaskRow) -> str:
        meta = self.store.get(r.gid) or {}
        ih = str(meta.get("ihash") or "") or dedupe.magnet_hash(r.uri)
        if ih:
            return "h:" + ih.lower()
        if r.name:
            return "n:" + str(Path(r.dir) / r.name).lower()
        return "g:" + r.gid

    def _meta_identity(self, gid: str, meta: dict) -> str:
        ih = str(meta.get("ihash") or "").strip()
        if ih:
            return "h:" + ih.lower()
        name = str(meta.get("name") or "").strip()
        if name:
            return "n:" + str(Path(str(meta.get("dir") or "")) / name).lower()
        return "g:" + str(gid)

    @staticmethod
    def _history_rank(r: TaskRow) -> tuple:
        return (1 if r.status_code == "complete" else 0,
                1 if r.title else 0, r.finished or 0.0,
                r.added or 0.0, r.size or 0)

    def _dedupe_rows(self, rows: list[TaskRow]) -> list[TaskRow]:
        """同一个 infohash/文件只留一行：防止删除一个 gid 后另一个幽灵行还留着。"""
        out: list[TaskRow] = []
        where: dict[str, int] = {}
        for r in rows:
            key = self._task_identity(r)
            idx = where.get(key)
            if idx is None:
                where[key] = len(out)
                out.append(r)
                continue
            old = out[idx]
            if old.is_history and not r.is_history:
                out[idx] = r
            elif old.is_history == r.is_history and self._history_rank(r) > self._history_rank(old):
                out[idx] = r
        return out

    def _build_history_rows(self) -> list[TaskRow]:
        """从 tasks.json 里补回 aria2 已经不记得的已完成任务。

        aria2 默认不把已完成任务写回 session（force-save=false），
        程序重启后这些任务会从列表消失，但文件和 tasks.json 还在。
        这里按 infohash 找种子名，再 stat 磁盘确认文件真的存在。
        """
        out: list[TaskRow] = []
        for gid, meta in (self.store.all() or {}).items():
            if not gid or meta.get("removed"):
                continue
            name = str(meta.get("name") or "").strip()
            ihash = str(meta.get("ihash") or "").strip()
            tsize = 0
            if ihash:
                # 种子库索引里通常已经记了目标名/大小（磁力解析后写入）；
                # 没有 .torrent 文件时 find_by_infohash() 是空的，不能只看它。
                entry = torrent_keep.get(ihash) or {}
                if entry.get("name"):
                    name = name or str(entry.get("name") or "")
                tsize = int(entry.get("size") or 0)
                b64 = torrent_keep.find_by_infohash(ihash)
                if b64:
                    try:
                        tname, tsize = torrent_keep.meta_of(base64.b64decode(b64))
                        name = name or str(tname or "")
                    except Exception:  # noqa: BLE001
                        pass
            if not name:
                name = str(meta.get("title") or "").strip()
            if not name:
                continue
            folder = Path(str(meta.get("dir") or paths.DL_DIR))
            target = folder / name
            exists = False
            size = 0
            count = 1
            try:
                if target.is_file():
                    exists = True
                    size = target.stat().st_size
                elif target.is_dir():
                    exists = True
                    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
                    count = 2
            except OSError:
                exists = False
            expected = int(meta.get("size") or tsize or 0)
            finished_at = float(meta.get("finished_at") or 0)
            completed = bool(finished_at) or (
                exists and expected > 0 and size >= expected)
            if not exists and not finished_at and not expected:
                continue
            display_size = expected if expected > 0 else size
            done = display_size if completed else min(size, display_size or size)
            status = "complete" if completed else ("paused" if exists else "removed")
            out.append(TaskRow(
                gid=gid, name=name, size=display_size, done=done,
                status_code=status, dir=str(folder), added=float(meta.get("added_at") or 0),
                finished=finished_at or (time.time() if completed else 0.0),
                is_bt=bool(ihash), file_count=count, title=str(meta.get("title") or ""),
                uri=str(meta.get("uri") or ""), memo=str(meta.get("memo") or ""),
                missing=not exists, is_history=True))
        return self._dedupe_rows(out)

    def _scan_completion(self, rows: list[TaskRow]) -> None:
        for r in rows:
            # 磁力链接的「先下种子」那一步：拿到 .torrent 就算"完成"，
            # 但它连饭都不是。静默清掉这条记录，等真正的下载任务自己出现。
            # 用户要的是「贴进去、等一会儿、看见它在下」，中间不该有两次提醒。
            if r.is_meta:
                if r.status_code == "complete" and r.gid not in self._cleaned:
                    self._cleaned.add(r.gid)
                    self._drop_meta_task(r)
                continue
            if r.is_history:
                continue
            if r.status_code == "active" and not r.is_seeder:
                self._active_seen.add(r.gid)
            # 秒下完的小文件可能一次都没被轮询成 active，所以清理控制文件
            # 单独用一个集合记，不跟「发通知」的条件绑在一起。
            # 用 is_finished：BT 下完会继续做种一小时，那段时间 aria2 还报 active，
            # 拿 status_code == "complete" 当完成通知会整整晚一小时。
            if r.is_finished and r.gid not in self._cleaned:
                self._cleaned.add(r.gid)
                self._drop_control_file(r)
            if not r.is_finished or r.gid in self._completed_seen:
                continue
            self._completed_seen.add(r.gid)
            if r.gid not in self._active_seen:
                continue          # 启动前就下完的旧任务，不补通知
            if time.time() - self._started_at < 3:
                continue
            if self.api:
                self._notify_pending.add(r.gid)   # 等校验结论一起说
            else:
                self._notify_complete(r, None)

    def _drop_meta_task(self, row: TaskRow) -> None:
        """把「先下种子」那条临时任务从列表里抹掉，并把种子收进历史种子库。

        磁力/`.torrent` 链接都会先下出一个几十 KB 的"种子菜单"，
        aria2 随后另起真任务。这条临时任务如果留着，就会在「吃饱了」里
        装成一个"已经下好的文件"——用户对标比特彗星，说这个不该存在。

        以前这里取 `self.api.rpc`（ApiServer 没有这个属性）→ 静默返回，
        所以临时任务一直清不掉；现在直接用 `self.engine.rpc`。
        """
        rpc = self.engine.rpc
        if rpc is None:
            return
        try:
            uri = (row.uri or "").strip()
            lower = uri.lower()
            if lower.startswith(("http://", "https://", "ftp://")) \
                    and (row.name or "").lower().endswith(".torrent"):
                # 下载回来的 .torrent 本身：留进种子库，临时文件删掉
                p = Path(row.dir or "") / row.name
                if p.exists():
                    raw = p.read_bytes()
                    torrent_keep.save(row.gid, base64.b64encode(raw).decode("ascii"),
                                      source=uri)
                    try:
                        p.unlink()
                    except OSError:
                        pass
            elif lower.startswith("magnet:"):
                torrent_keep.save_magnet(uri, gid=row.gid, dir_=row.dir or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            rpc.call("aria2.removeDownloadResult", [row.gid])
        except Exception:  # noqa: BLE001
            pass

    def _drop_control_file(self, row: TaskRow) -> None:
        """删掉 aria2 留下的 .aria2 控制文件。

        下完的普通文件旁边会多出一个「xxx.mkv.aria2」，看着脏。
        已经 complete 了就不需要它了（配置里 force-save 已改成 false，
        这里再兜一层，防止老配置文件还没迁移）。
        """
        try:
            target = self._target_of(row)
        except Exception:  # noqa: BLE001
            return
        if not target:
            return
        p = Path(target)
        cands = [Path(str(p) + ".aria2")]
        if p.parent.exists():
            cands.append(p.parent / (p.name + ".aria2"))
        for c in cands:
            try:
                if c.is_file():
                    c.unlink()
            except OSError:
                pass

    def _notify_complete(self, row: TaskRow, rep: dict | None) -> None:
        if self.notifier is None:
            return
        if self.settings.get("notify_only_when_hidden") and self.isVisible() \
                and self.isActiveWindow():
            return
        self._bowls += 1
        target = self._target_of(row)
        ok = None if rep is None else bool(rep.get("ok"))
        if ok is True:
            detail = "吃干净了 · %s" % fmt_size(row.size)
        elif ok is False:
            detail = "没吃干净，可能不完整 · %s" % fmt_size(row.size)
        else:
            detail = fmt_size(row.size)
        can_open = target is not None and target.is_file()
        self.notifier.notify(
            "第 %d 碗，开饭了！" % self._bowls, row.name, detail, can_open,
            on_dir=(lambda t=target: intents.reveal(t)) if target is not None else None,
            on_file=(lambda t=target: intents.open_default(t)) if can_open else None,
        )

    def _scan_pending_picks(self, rows: list[TaskRow]) -> None:
        if not self._await_pick:
            return
        by_gid = {r.gid: r for r in rows}
        for gid, info in list(self._await_pick.items()):
            r = by_gid.get(gid)
            if r is None:
                continue
            if r.error:
                self._await_pick.pop(gid, None)
                continue
            # 暂停中的 BT 任务 totalLength 会是 0，只能用文件数判断元数据是否到位
            if r.file_count > 1:
                self._await_pick.pop(gid, None)
                QTimer.singleShot(
                    0, lambda g=gid, u=bool(info.get("unpause")): self._open_file_picker(g, u))
            elif r.file_count == 1 and not r.is_meta:
                # 单文件磁力没什么可选的：不自动开始会一直卡在「拿种子后选文件」
                # 的暂停状态（用户 2026-10-22 遇到：新建磁力后 0% 一直不动）。
                self._await_pick.pop(gid, None)
                if info.get("unpause"):
                    try:
                        self.engine.rpc.call("aria2.unpause", [gid])
                    except Aria2Error:
                        pass

    # ================= 完整性校验 =================
    def _maybe_verify(self, rows: list[TaskRow]) -> None:
        if not self.api:
            return
        for r in rows:
            # 「取种子」那条本来就没有文件，校验它只会得出"没吃干净"，没意义
            if r.is_meta or r.is_history:
                continue
            if r.is_finished and r.gid not in self._verified:
                self._verified.add(r.gid)
                threading.Thread(target=self._verify_bg, args=(r.gid,), daemon=True).start()

    def _verify_bg(self, gid: str) -> None:
        try:
            rep = self.api.ctx.verify_gid(gid) if self.api else {"ok": False, "error": "接口未启动"}
        except Exception as exc:  # noqa: BLE001
            rep = {"ok": False, "error": str(exc)}
        self.verify_done.emit(gid, rep)

    def _on_verify_done(self, gid: str, rep: dict) -> None:
        # 校验失败先别急着标红：aria2 刚报 complete 的那一瞬间，文件可能还在收尾
        # （尤其带 check-integrity 重新挂上的任务），过 4 秒再验一次，
        # 避免把完整文件误判成「没吃干净 ✗」。
        if not rep.get("ok") and self._verify_attempts.get(gid, 0) < 1:
            still = any(r.gid == gid and r.is_finished for r in self.all_rows)
            if still:
                self._verify_attempts[gid] = 1
                QTimer.singleShot(4000, lambda g=gid: self._retry_verify(g))
                return
        self.verify_map[gid] = rep
        for r in self.all_rows:
            if r.gid == gid:
                r.verify_ok = bool(rep.get("ok"))
        self._apply_filter()
        if not rep.get("ok") and rep.get("bad_count"):
            self.lb_state.setText("⚠ 有任务校验未通过（%d 个文件），详情里可查看" % rep["bad_count"])
        if self.pet:
            self.pet.update_data(self.all_rows, {})
        if gid in self._notify_pending:
            self._notify_pending.discard(gid)
            for r in self.all_rows:
                if r.gid == gid:
                    self._notify_complete(r, rep)
                    break

    def _retry_verify(self, gid: str) -> None:
        """第一次校验失败后的补验（文件刚收尾 / 刚校验完的边角情况）。"""
        if self.api is None:
            return
        row = next((r for r in self.all_rows if r.gid == gid), None)
        if row is None or not row.is_finished:
            return
        threading.Thread(target=self._verify_bg, args=(gid,), daemon=True).start()

    def _on_poll_failed(self, msg: str) -> None:
        self.lb_state.setText("⚠ %s" % msg[:60])

    def _recover_task_size(self, r: TaskRow) -> bool:
        """暂停/完成任务的"大小 + 是否真的下完"自愈。

        aria2 对暂停中的 BT 任务会报 totalLength=0、completedLength=0，
        界面就会显示 0% 并掉进「放下筷子」。这里按 infohash 从种子库/下载目录
        找到种子声明的总大小，再和磁盘上的文件比对：
          * 一样大 → 认定已经下完，补记 finished_at/size；
          * 不一样 → 保持原样（真的没下完）。
        每个 gid 只试一次，不在轮询里反复 stat/RPC。
        """
        if (r.is_meta or not r.gid or r.gid in self._recover_tried
                or r.status_code not in ("paused", "complete")):
            return False
        if r.size > 0 and (r.is_finished or r.status_code != "paused"):
            return False
        self._recover_tried.add(r.gid)
        rpc = self.engine.rpc
        if rpc is None:
            return False
        ihash = ""
        try:
            st = rpc.call("aria2.tellStatus", [r.gid, ["infoHash"]])
            if isinstance(st, dict):
                ihash = str(st.get("infoHash") or "")
        except Aria2Error:
            pass
        expected = int(r.size or 0)
        if expected <= 0 and ihash:
            b64 = torrent_keep.find_by_infohash(ihash)
            if b64:
                try:
                    expected = int(torrent_keep.meta_of(base64.b64decode(b64))[1])
                except Exception:  # noqa: BLE001
                    expected = 0
        if expected <= 0:
            return False
        if target_complete(r.dir, r.name, expected, r.file_count):
            r.size = expected
            r.done = expected
            r.finished = self.store.note_finished(r.gid, expected)
            return True
        if r.is_finished:
            r.size = expected
            r.done = expected
            return True
        return False

    def _on_snapshot(self, rows: list[TaskRow], stat: dict) -> None:
        if self._history_cache is None:
            self._history_cache = self._build_history_rows()
        if self._history_cache:
            have = {r.gid for r in rows}
            names = {r.name for r in rows}
            rows = rows + [h for h in self._history_cache
                           if h.gid not in have and h.name not in names
                           and self.store.get(h.gid) is not None]
        rows = self._dedupe_rows(rows)
        for r in rows:
            if r.name and not r.is_meta:
                self.store.set_origin(r.gid, r.name, r.dir)
            if not r.is_history:
                meta = self.store.get(r.gid) or {}
                ih = str(meta.get("ihash") or "").strip() or dedupe.magnet_hash(r.uri)
                nm = "" if r.is_meta else (r.name if r.name and not r.name.startswith("（") else "")
                self.store.note(r.gid, uri=r.uri, name=nm, ihash=ih, dir=r.dir)
                if nm:
                    self.store.set_origin(r.gid, nm, r.dir)
                if r.size:
                    self.store.set_size(r.gid, r.size)
            if r.added == 0:
                self.store.note(r.gid)
            if self._recover_task_size(r):
                self.store.save()
            if r.is_finished and not r.finished:
                r.finished = self.store.note_finished(r.gid, r.size)
            if r.is_finished and r.size and not (self.store.get(r.gid) or {}).get("size"):
                self.store.set_size(r.gid, r.size)
            rep = self.verify_map.get(r.gid)
            if rep is not None:
                r.verify_ok = bool(rep.get("ok"))
        self._auto_title(rows)
        self._archive_magnet_names(rows)
        for r in rows:
            if r.gid and not r.is_meta and r.name:
                _meta = self.store.get(r.gid) or {}
                _ih = str(_meta.get("ihash") or "") or dedupe.magnet_hash(r.uri)
                self._title_prefetch.submit(r.name, r.file_paths, _ih)
        self.all_rows = rows
        self._apply_filter()
        self._scan_completion(rows)
        self._scan_pending_picks(rows)
        self._maybe_verify(rows)
        if self.pet:
            self.pet.update_data(rows, stat)

        ds = int(stat.get("downloadSpeed") or 0)
        us = int(stat.get("uploadSpeed") or 0)
        n_active = int(stat.get("numActive") or 0)
        n_wait = int(stat.get("numWaiting") or 0)
        n_stop = int(stat.get("numStopped") or 0)

        self.lb_speed.setText("↓ %s　↑ %s" % (fmt_speed(ds), fmt_speed(us)))
        self.lb_conn.setText("连接 %d" % (n_active + n_wait))
        tail = ("  ·  AI 接口 127.0.0.1:%d" % self.api_port) if self.api_port else ""
        # 干饭中只数"真在下载"的；做种的单独说一句，别混在一起
        n_eat = sum(1 for r in rows
                    if r.status_code == "active" and not r.is_seeder and not r.error)
        n_seed = sum(1 for r in rows if r.status_code == "active" and r.is_seeder)
        n_done = sum(1 for r in rows if r.is_finished and not r.is_meta)
        n_err = sum(1 for r in rows if r.error)
        seed_txt = ("　·　分饭中 %d 个" % n_seed) if n_seed else ""
        state_line = ("打饭中 %d 碗 · 已开饭 %d 碗%s · 共 %d 碗%s"
                      % (n_eat, n_done, seed_txt, len(rows), tail))
        if n_err:
            state_line = "⚠ 噎住了 %d 个（右键可看原因）　·　%s" % (n_err, state_line)
        self.lb_state.setText(state_line)

        new_errors = [r for r in rows if r.error and r.gid not in self._seen_errors]
        for r in new_errors:
            self._seen_errors.add(r.gid)
        if new_errors:
            r = new_errors[0]
            if self.pet:
                self.pet.toast("噎住了：%s（详情里有原因）" % r.name[:16])

        self._update_counts()

        if hasattr(self, "worker"):
            self.worker.set_fast(n_active > 0)

    def _auto_title(self, rows: list[TaskRow]) -> None:
        """给 BT 任务整理显示名（只改列表，不动磁盘文件）。

        按发布名结构拆：字幕组 / 片名 / 集数 / 画质 / 编码 / 语言；
        旧版「2026年10月 一堆词」会在下一次刷新时重新整理。
        """
        if not self.settings.get("auto_title", True):
            return
        changed = False
        old_style = re.compile(r"^\d{4}年\d{2}月\s")
        for r in rows:
            if not r.gid or r.is_meta:
                continue
            # 用户自己写过/手动改过的标题不要动；只整理空的或旧版年月标题
            if r.title and not old_style.match(r.title):
                self._title_done.add(r.gid)
                continue
            title = titlegen.make_title(r.name, r.file_paths or [], r.added)
            if not title or title == r.name:
                continue
            if title == r.title:
                self._title_done.add(r.gid)
                continue
            self.store.set_title(r.gid, title)
            r.title = title
            self._title_done.add(r.gid)
            changed = True
        if changed:
            self.store.save()

    def _archive_magnet_names(self, rows: list[TaskRow]) -> None:
        """磁力任务拿到元数据后，把种子库备案里的名字从「磁力 · hash」换成真名。"""
        for r in rows:
            if r.is_meta or not r.gid or r.gid in self._magnet_named:
                continue
            if not (r.uri or "").lower().startswith("magnet:"):
                continue
            if not r.name or r.name.startswith("（"):
                continue
            torrent_keep.save_magnet(r.uri, gid=r.gid, name=r.name,
                                     dir_=r.dir, size=r.size, files=r.file_count)
            self._magnet_named.add(r.gid)

    def _update_counts(self) -> None:
        rows = self.all_rows
        counts = {
            "all": len(rows),
            "down": sum(1 for r in rows if r.status_code == "active" and not r.is_seeder and not r.error),
            "wait": sum(1 for r in rows if r.status_code == "waiting"),
            "done": sum(1 for r in rows if r.is_finished and not r.is_meta),
            "seed": sum(1 for r in rows if r.status_code == "active" and r.is_seeder),
            "pause": sum(1 for r in rows if r.status_code == "paused" and not r.is_finished),
            "err": sum(1 for r in rows if r.error),
        }
        for i, (key, label, _t, _f) in enumerate(FILTERS):
            item = self.tree.topLevelItem(i)
            if item:
                item.setText(0, "%s  (%d)" % (label, counts.get(key, 0)))
        if getattr(self, "_archive_item", None) is not None:
            self._archive_item.setText(0, "种子库  (%d)" % torrent_keep.count())

    # ================= 筛选 / 表格 =================
    def _on_tree_item_clicked(self, item, _col) -> None:
        """「种子库」不是任务筛选，点它直接开历史种子列表。

        它故意不可选中（没有焦点框），所以走 itemClicked 而不是 currentItemChanged。
        """
        if item is getattr(self, "_archive_item", None):
            self._open_torrent_library()
            self._update_counts()

    def _on_filter_changed(self, cur, _prev) -> None:
        if cur is not None:
            key = cur.data(0, Qt.ItemDataRole.UserRole) or "all"
            if key == "archive":
                # 兜底：万一当前项被程序设成了 archive
                self._open_torrent_library()
                seed_index = next((i for i, f in enumerate(FILTERS) if f[0] == "seed"), -1)
                if seed_index >= 0:
                    self.tree.setCurrentItem(self.tree.topLevelItem(seed_index))
                return
            self.current_filter = key
            self._apply_filter()

    # ---------- 排序 ----------
    # 单击表头 = 换主键；按住 Shift = 叠加第二/第三键。
    # 为什么要叠加：用户的原话是"先按类型排，再按大小排，
    # 这样就能在某一个类型里找到特别大的那个"——单键排序做不到。
    SORT_MAX_KEYS = 4

    def _sort_value(self, r: TaskRow, key: str):
        if key == "name":
            return (r.display_name or "").lower()
        if key == "memo":
            return (r.memo or "").lower()
        if key == "size":
            return r.size
        if key == "percent":
            return r.percent
        if key == "speed":
            return r.dl + r.ul
        if key == "peers":
            return r.seeders * 1000 + r.conns
        if key == "eta":
            if r.status_code == "active" and r.dl > 0:
                return (r.size - r.done) / r.dl
            return 10 ** 12
        if key == "status":
            return (STATE_ORDER.get(r.state, 9), r.dot_kind)
        if key == "finished":
            return r.finished
        if key == "added":
            return r.added
        return 0

    def _sort_rows(self, rows: list[TaskRow]) -> list[TaskRow]:
        out = list(rows)
        # 从最后一个键往前排，前面的键优先级更高（Python 的 sort 是稳定的）
        for key, desc in reversed(self._sort_keys):
            out.sort(key=lambda r, k=key: self._sort_value(r, k), reverse=desc)
        return out

    def _on_header_clicked(self, idx: int) -> None:
        key = COLS[idx][0]
        if key in ("dot", "act_dir", "act_open"):
            return          # 这仨没有排序意义
        shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        # 数字类默认"大的在前"，文字类默认 A→Z
        numeric = key in ("size", "percent", "speed", "peers", "eta",
                          "finished", "added")
        exists = [i for i, (k, _d) in enumerate(self._sort_keys) if k == key]
        if shift:
            if exists:
                i = exists[0]
                self._sort_keys[i] = (key, not self._sort_keys[i][1])
            else:
                self._sort_keys.append((key, numeric))
                del self._sort_keys[:max(0, len(self._sort_keys) - self.SORT_MAX_KEYS)]
        else:
            if len(self._sort_keys) == 1 and self._sort_keys[0][0] == key:
                self._sort_keys = [(key, not self._sort_keys[0][1])]
            else:
                self._sort_keys = [(key, numeric)]
            if not self._sort_hint_shown:
                self._sort_hint_shown = True
                QToolTip.showText(
                    QCursor.pos(),
                    "按住 Shift 再点一列，可以叠加排序\n（例如先按状态，再按大小）",
                    self.table.horizontalHeader())
        self._show_sort_indicator()
        self._apply_filter()

    def _show_sort_indicator(self) -> None:
        hh = self.table.horizontalHeader()
        key, desc = self._sort_keys[0]
        idx = next((i for i, (k, _l, _w) in enumerate(COLS) if k == key), -1)
        if idx >= 0:
            hh.setSortIndicator(idx, Qt.SortOrder.DescendingOrder if desc
                                 else Qt.SortOrder.AscendingOrder)
        parts = []
        for k, d in self._sort_keys:
            label = next((l or k for kk, l, _w in COLS if kk == k), k)
            parts.append("%s %s" % (label or k, "↓" if d else "↑"))
        hh.setToolTip("当前排序：" + "，".join(parts)
                      + "\n单击表头排序；按住 Shift 再点一列可以叠加（最多 %d 列）"
                      % self.SORT_MAX_KEYS)

    def _apply_filter(self) -> None:
        # 正在就地编辑备注时不重排行：编辑框绑的是行号，行一换，
        # 回车就会把备注写到别的任务上（先挂起，编辑结束再刷）。
        if hasattr(self, "table") and self.table.state() == QAbstractItemView.State.EditingState:
            self._filter_pending = True
            return
        pred = dict((k, f) for k, _l, _t, f in FILTERS).get(self.current_filter, lambda r: True)
        text = self.search.text().strip().lower() if hasattr(self, "search") else ""
        rows = [r for r in self.all_rows if pred(r)]
        if text:
            rows = [r for r in rows if matches_text(r, text)]
        rows = self._sort_rows(rows)
        self.visible_rows = rows

        keep_gids = [r.gid for r in self._selected_rows()]
        if not keep_gids and self._selected_gid:
            keep_gids = [self._selected_gid]
        self.model.set_rows(rows)
        if keep_gids:
            sm = self.table.selectionModel()
            if sm is not None:
                sm.clearSelection()
                wanted = set(keep_gids)
                for i, r in enumerate(rows):
                    if r.gid in wanted:
                        sm.select(self.model.index(i, 0),
                                  QItemSelectionModel.SelectionFlag.Rows
                                  | QItemSelectionModel.SelectionFlag.Select)
        self._update_counts()
        self._update_actions()

    def _selected_rows(self) -> list[TaskRow]:
        idxs = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        out = []
        for idx in idxs:
            r = self.model.row_at(idx.row())
            if r:
                out.append(r)
        return out

    def _on_selection_changed(self, *_a) -> None:
        sel = self._selected_rows()
        self._selected_gid = sel[0].gid if sel else ""
        if len(sel) > 1:
            self.lb_state.setText("已选中 %d 项，可一起删除/暂停/复制" % len(sel))
        # 换行之前把详情里没提交的备注存回**原来那一条**（_memo_edit_gid 记的就是它）
        if hasattr(self, "memo_edit") and self.memo_edit.hasFocus():
            self.memo_edit.clearFocus()
        self._update_actions()
        self._refresh_detail(force=True)

    def _update_actions(self) -> None:
        sel = self._selected_rows() if hasattr(self, "model") else []
        has = bool(sel)
        for a in (self.act_start, self.act_pause, self.act_remove, self.act_remove_file,
                  self.act_up, self.act_down, self.act_top, self.act_bottom,
                  self.act_open_dir, self.act_copy_link, self.act_recheck, self.act_memo):
            a.setEnabled(has)
        self.act_stop_seed.setEnabled(any(r.is_seeder for r in sel))
        self.act_reseed.setEnabled(any(r.is_finished for r in sel))

    # ================= 任务操作 =================
    def _gids(self) -> list[str]:
        return [r.gid for r in self._selected_rows()]

    def _reseed_selected(self) -> None:
        """菜单/右键「给鱼群分饭」：让已下完的任务重新校验并开始上传。"""
        self._seed_rows(self._selected_rows())

    def _seed_rows(self, rows: list[TaskRow]) -> None:
        """把已经下完的任务重新喂给 aria2：check-integrity → 直接进入做种。

        aria2 没有"凭空开始做种"的接口，必须再喂一次种子：
          * 优先用种子库/下载目录里按 infohash 找到的 .torrent；
          * 磁力任务退回复贴磁力；
          * 都没有才说"找不到来源"。
        这样就算任务是从命令行/接口直接加进来的、没走 UI 留档，也能重新分饭。
        """
        rpc = self.engine.rpc
        if not rpc:
            return
        seed = int(self.settings.get("seed_time", 60))
        ratio = float(self.settings.get("seed_ratio", 0.0))
        n_ok = 0
        failed = 0
        for r in rows:
            if not r.is_finished:
                continue
            opts = {"dir": r.dir or str(paths.DL_DIR),
                    "check-integrity": "true",
                    "seed-time": str(seed if seed > 0 else 60),
                    "seed-ratio": str(ratio)}
            meta = self.store.get(r.gid) or {}
            uri = str(meta.get("uri") or "")
            b64 = torrent_keep.load_b64(r.gid)
            ihash = ""
            if not b64 and not uri.lower().startswith("magnet:"):
                try:
                    st = rpc.call("aria2.tellStatus", [r.gid, ["infoHash"]])
                    if isinstance(st, dict):
                        ihash = str(st.get("infoHash") or "")
                except Aria2Error:
                    pass
                if ihash:
                    b64 = torrent_keep.find_by_infohash(ihash)
            try:
                if b64:
                    gid = rpc.call("aria2.addTorrent", [b64, [], opts])
                    torrent_keep.save(gid, b64, source="重新分饭")
                    note_uri = "(torrent) %s" % (r.name or "")
                elif uri.lower().startswith("magnet:"):
                    gid = rpc.call("aria2.addUri", [[uri], opts])
                    note_uri = uri
                else:
                    failed += 1
                    continue
            except Aria2Error:
                failed += 1
                continue
            self.store.note(gid, uri=note_uri, name=r.name,
                            dir=opts["dir"], ihash=ihash)
            # 重新分饭 = 把旧的那条完成记录换掉，不是"又下完一个"：
            # 1) 新任务静音（不弹完成、不加碗数、不响提示音）
            self._completed_seen.add(gid)
            self._active_seen.add(gid)
            self._cleaned.add(gid)
            self._notify_pending.discard(gid)
            if self.pet is not None:
                self.pet.silence(gid)
            # 2) 把旧任务从列表里删掉，并把备注/核心名搬过去
            old_meta = dict(self.store.get(r.gid) or {})
            if old_meta.get("memo"):
                self.store.set_memo(gid, str(old_meta["memo"]))
            if old_meta.get("title"):
                self.store.set_title(gid, str(old_meta["title"]))
            self.store.forget([r.gid])
            try:
                rpc.call("aria2.removeDownloadResult", [r.gid])
            except Aria2Error:
                pass
            self._verify_attempts.pop(r.gid, None)
            self.verify_map.pop(r.gid, None)
            n_ok += 1
        self.store.save()
        if n_ok:
            self.lb_state.setText(
                "已开始给鱼群分饭：%d 个任务在校验文件，通过后就开始上传" % n_ok)
            self._poke()
        if failed:
            self.lb_state.setText(
                "有 %d 个任务没找到可用的种子/磁力来源，分不了饭" % failed)
            if self.pet is not None:
                self.pet.toast("有 %d 碗找不到种子，分不了饭" % failed)

    def _open_torrent_library(self) -> None:
        """种子库：历史磁力/.torrent 的备案，只存来源，不存下载回来的文件。"""
        dlg = TorrentLibraryDialog(self)
        dlg.redownload.connect(self._redownload_archive)
        dlg.exec()
        self._update_counts()

    def _redownload_archive(self, entry: dict) -> None:
        """按种子库里的来源重新建一个下载任务（不是做种校验）。"""
        rpc = self.engine.rpc
        if not rpc:
            return
        opts = {"dir": entry.get("dir") or self.settings.get("download_dir") or str(paths.DL_DIR)}
        try:
            b64 = torrent_keep.load_b64(str(entry.get("ihash") or ""))
            uri = str(entry.get("uri") or "")
            if b64:
                gid = rpc.call("aria2.addTorrent", [b64, [], opts])
                note_uri = "(torrent) %s" % (entry.get("name") or "")
            elif uri.lower().startswith("magnet:"):
                gid = rpc.call("aria2.addUri", [[uri], opts])
                note_uri = uri
            else:
                self._toast("这条来源没有可用的磁力/.torrent，重新下载不了")
                return
        except Aria2Error as exc:
            self._toast("重新下载失败：%s" % str(exc)[:60])
            return
        self.store.note(gid, uri=note_uri, name=str(entry.get("name") or ""),
                        dir=opts["dir"])
        self.store.save()
        self.lb_state.setText("已按种子库的来源重新加入下载队列")
        self._poke()

    def _apply_global(self, what: str) -> None:
        """全局暂停/继续：紧急让出网速用，不依赖当前选中了哪一行。"""
        rpc = self.engine.rpc
        if not rpc:
            return
        try:
            rpc.call("aria2.pauseAll" if what == "pause" else "aria2.unpauseAll")
            self.lb_state.setText("已停止全部任务" if what == "pause"
                                  else "已开始全部任务")
        except Aria2Error as exc:
            self.lb_state.setText("全局操作失败：%s" % str(exc)[:60])
        self._poke()

    def _apply_to_selected(self, what: str) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        gids = set(self._gids())
        rows = [r for r in self.visible_rows if r.gid in gids]
        history = [r for r in rows if r.is_history]
        if history:
            rows = [r for r in rows if not r.is_history]
            self.lb_state.setText("这是历史记录，文件已经在磁盘；要重新下载请重新添加任务")
        if not rows:
            return
        if what == "unpause":
            # 「开始」对已经下完的任务 = 开始做种（给鱼群分饭），不能只 unpause
            to_seed = [r for r in rows if r.is_finished and not r.is_seeder]
            if to_seed:
                self._seed_rows(to_seed)
            seeded = {r.gid for r in to_seed}
            rows = [r for r in rows if r.gid not in seeded]
        for r in rows:
            gid = r.gid
            try:
                if what == "unpause":
                    if r.error:
                        rpc.call("aria2.removeDownloadResult", [gid])
                        continue
                    if r.is_seeder:
                        continue
                    rpc.call("aria2.unpause", [gid])
                elif what == "pause":
                    rpc.call("aria2.forcePause", [gid])
                elif what == "stop":
                    rpc.call("aria2.forceRemove", [gid])
            except Aria2Error as exc:
                self.lb_state.setText("操作失败：%s" % str(exc)[:60])
        self._poke()

    def _remove_selected(self, delete_files: bool) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        rows = self._selected_rows()
        if not rows:
            return
        if len(rows) == 1:
            r = rows[0]
            txt = "要把「%s」这碗端走吗？" % r.name
        else:
            txt = "要把选中的 %d 碗一起端走吗？" % len(rows)
        if delete_files:
            txt += "\n\n⚠ 连同已经下好的文件一起删除，无法恢复。"
        box = QMessageBox(self)
        box.setWindowTitle("删除任务")
        box.setText(txt)
        box.setIcon(QMessageBox.Icon.Warning if delete_files else QMessageBox.Icon.Question)
        yes = box.addButton("确定", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return

        keys = {self._task_identity(r) for r in rows}
        all_gids: list[str] = [r.gid for r in rows]
        for gid, meta in (self.store.all() or {}).items():
            if gid not in all_gids and self._meta_identity(gid, meta) in keys:
                all_gids.append(gid)
        for gid in all_gids:
            try:
                rpc.call("aria2.forceRemove", [gid])
            except Aria2Error:
                pass
            try:
                rpc.call("aria2.removeDownloadResult", [gid])
            except Aria2Error:
                pass
        self.store.forget(all_gids)
        self.store.save()
        self._history_cache = None
        if delete_files:
            for r in rows:
                self._delete_task_files(r)
        self._selected_gid = ""
        self._poke()

    def _delete_task_files(self, r: TaskRow) -> None:
        # 交给回收站，避免误删不可恢复
        target = Path(r.dir) / r.name if r.name else None
        if not target or not target.exists():
            return
        try:
            import ctypes
            from ctypes import wintypes

            class SHFILEOPSTRUCTW(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR),
                ]

            FO_DELETE = 3
            FOF_ALLOWUNDO = 0x0040
            FOF_NOCONFIRMATION = 0x0010
            FOF_SILENT = 0x0004
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            op.pFrom = str(target) + "\0\0"
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
            ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception:  # noqa: BLE001
            pass

    def _reorder(self, delta: int) -> None:
        rpc = self.engine.rpc
        if not rpc:
            return
        for gid in self._gids():
            try:
                if delta in (-9999, 9999):
                    rpc.call("aria2.changePosition", [gid, delta, "POS_SET"])
                else:
                    rpc.call("aria2.changePosition", [gid, delta, "POS_CUR"])
            except Aria2Error:
                pass
        self._poke()

    def _open_dir(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        r = rows[0]
        target = Path(r.dir) / r.name
        try:
            if target.exists() and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                d = Path(r.dir)
                if r.name and (d / r.name).exists():
                    subprocess.Popen(["explorer", str(d / r.name)])
                elif d.exists():
                    subprocess.Popen(["explorer", str(d)])
        except OSError:
            pass

    def _copy_link(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        uri = self.store.get(rows[0].gid).get("uri", "")
        if not uri:
            info = ""
            try:
                info = (self.engine.rpc.call("aria2.getOption", [rows[0].gid]) or {}).get("gid", "")
            except Aria2Error:
                pass
            uri = info
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(uri or rows[0].name)

    def _on_speed_changed(self, idx: int) -> None:
        # 兼容旧接口：下拉框已换成标度滑块
        vals = [0, 1, 5, 10, 20, 30, 50, 100, 200, 500]
        if 0 <= idx < len(vals):
            self._apply_speed_limit(vals[idx] * 1024 * 1024)

    # ================= 右键菜单 =================
    def _show_row_menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if idx.isValid() and idx.row() not in [i.row() for i in self.table.selectionModel().selectedRows()]:
            self.table.selectRow(idx.row())
        m = QMenu(self)
        sel = self._selected_rows()
        if sel:
            row0 = sel[0]
            if row0.error:
                m.addAction("查看错误原因…", self._show_error_reason)
                m.addAction("自动改名继续下载",
                            lambda r=row0: self._retry_with_rename(r))
                m.addSeparator()
            target = self._target_of(row0)
            if target is not None and target.is_file():
                m.addAction("用默认程序打开", lambda: self._open_file_of(row0))
                ow = m.addMenu("打开方式")
                handlers = openwith.handlers_for(
                    target, self._custom_openers(intents.ext_of(target)))
                for h in handlers:
                    a = ow.addAction(self._icon_for_exe(h.icon_path),
                                     h.label + ("（默认）" if h.is_default else ""))
                    a.triggered.connect(
                        lambda _c=False, hh=h, t=target: self._run_handler(t, hh))
                if handlers:
                    ow.addSeparator()
                ow.addAction("选择其他程序…",
                             lambda t=target: openwith.show_open_with_dialog(t))
            if row0.is_bt or row0.status_code in ("waiting", "paused", "active"):
                m.addAction("勾选要下的文件…",
                            lambda g=row0.gid: self._open_file_picker(g, False))
            m.addSeparator()
            # 分饭（上传）：正在分 → 可以停；下完了 → 可以再来一轮
            if any(r.is_seeder for r in sel):
                m.addAction(self.act_stop_seed)
            if any(r.is_finished for r in sel):
                m.addAction(self.act_reseed)
            m.addAction(self.act_library)
            m.addSeparator()
            m.addAction("改备注…" if row0.memo else "写备注…（F2）",
                        lambda r=row0: self._edit_memo(r))
            if row0.memo:
                m.addAction("清除备注", lambda g=row0.gid: self._on_memo_committed(g, ""))
            m.addSeparator()
        m.addAction(self.act_start)
        m.addAction(self.act_pause)
        m.addSeparator()
        m.addAction(self.act_open_dir)
        m.addAction(self.act_copy_link)
        m.addSeparator()
        m.addAction(self.act_up)
        m.addAction(self.act_down)
        m.addAction(self.act_top)
        m.addAction(self.act_bottom)
        m.addSeparator()
        m.addAction(self.act_remove)
        m.addAction(self.act_remove_file)
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ================= 详情 =================
    # ================= 文件表 =================
    def _fill_files(self, gid: str, files: list[dict], base_dir: str) -> None:
        rebuild = (gid != self._files_gid) or (len(files) != self._files_rows)
        self._files_gid = gid
        self._files_rows = len(files)

        if rebuild:
            self.tab_files.blockSignals(True)
            self.tab_files.setRowCount(len(files))
            for i, f in enumerate(files):
                p = f.get("path", "")
                rel = p
                if base_dir:
                    try:
                        rel = os.path.relpath(p, base_dir)
                    except ValueError:
                        rel = p
                rel = rel.replace("\\", "/")
                total = int(f.get("length") or 0)
                it = QTableWidgetItem(rel)
                it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                            | Qt.ItemFlag.ItemIsSelectable)
                it.setCheckState(Qt.CheckState.Checked if f.get("selected", "true") == "true"
                                 else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, int(f.get("index") or (i + 1)))
                it.setData(Qt.ItemDataRole.UserRole + 1, total)
                self.tab_files.setItem(i, 0, it)

                sz = QTableWidgetItem(fmt_size(total))
                sz.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter))
                sz.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.tab_files.setItem(i, 1, sz)

                pc = QTableWidgetItem("0.0%")
                pc.setTextAlignment(int(Qt.AlignmentFlag.AlignRight
                                        | Qt.AlignmentFlag.AlignVCenter))
                pc.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.tab_files.setItem(i, 2, pc)
            self.tab_files.blockSignals(False)
            self._files_dirty = False
            self.btn_apply_files.setEnabled(False)

        self.tab_files.blockSignals(True)
        for i, f in enumerate(files):
            if i >= self.tab_files.rowCount():
                break
            total = int(f.get("length") or 0)
            done = int(f.get("completedLength") or 0)
            pct = (done * 100.0 / total) if total else 0.0
            pc = self.tab_files.item(i, 2)
            if pc is not None:
                pc.setText("%.1f%%" % pct)
            it = self.tab_files.item(i, 0)
            if it is not None and not self._files_dirty:
                want = (Qt.CheckState.Checked if f.get("selected", "true") == "true"
                        else Qt.CheckState.Unchecked)
                if it.checkState() != want:
                    it.setCheckState(want)
        self.tab_files.blockSignals(False)
        self._update_files_sum()

    def _update_files_sum(self) -> None:
        n = self.tab_files.rowCount()
        if n == 0:
            self.lb_files.setText("")
            return
        if n == 1:
            self.lb_files.setText("只有 1 个文件，不用挑")
            return
        sel = checked = 0
        for i in range(n):
            it = self.tab_files.item(i, 0)
            if it is None:
                continue
            size = int(it.data(Qt.ItemDataRole.UserRole + 1) or 0)
            if it.checkState() == Qt.CheckState.Checked:
                checked += 1
                sel += size
        self.lb_files.setText("已勾 %d / %d 个 · %s" % (checked, n, fmt_size(sel)))

    def _on_file_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        self._files_dirty = True
        self.btn_apply_files.setEnabled(True)
        self._update_files_sum()

    def _check_all_files(self, on: bool) -> None:
        self.tab_files.blockSignals(True)
        for i in range(self.tab_files.rowCount()):
            it = self.tab_files.item(i, 0)
            if it is not None:
                it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tab_files.blockSignals(False)
        self._files_dirty = True
        self.btn_apply_files.setEnabled(True)
        self._update_files_sum()

    def _check_video_files(self) -> None:
        self.tab_files.blockSignals(True)
        for i in range(self.tab_files.rowCount()):
            it = self.tab_files.item(i, 0)
            if it is None:
                continue
            ok = intents.ext_of(it.text()) in intents.VIDEO_EXT
            it.setCheckState(Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked)
        self.tab_files.blockSignals(False)
        self._files_dirty = True
        self.btn_apply_files.setEnabled(True)
        self._update_files_sum()

    def _invert_files(self) -> None:
        self.tab_files.blockSignals(True)
        for i in range(self.tab_files.rowCount()):
            it = self.tab_files.item(i, 0)
            if it is None:
                continue
            it.setCheckState(Qt.CheckState.Unchecked
                             if it.checkState() == Qt.CheckState.Checked
                             else Qt.CheckState.Checked)
        self.tab_files.blockSignals(False)
        self._files_dirty = True
        self.btn_apply_files.setEnabled(True)
        self._update_files_sum()

    @staticmethod
    def _compress(ids: list[int]) -> str:
        ids = sorted(set(ids))
        out: list[str] = []
        i = 0
        while i < len(ids):
            j = i
            while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
                j += 1
            if j - i >= 2:
                out.append("%d-%d" % (ids[i], ids[j]))
            else:
                out.extend(str(x) for x in ids[i:j + 1])
            i = j + 1
        return ",".join(out)

    def _apply_file_selection(self) -> None:
        gid = self._files_gid
        if not gid:
            return
        ids: list[int] = []
        for i in range(self.tab_files.rowCount()):
            it = self.tab_files.item(i, 0)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                ids.append(int(it.data(Qt.ItemDataRole.UserRole) or (i + 1)))
        if not ids:
            QMessageBox.information(self, "一个都没勾", "至少勾一个文件，不然就没得吃了。")
            return
        row = next((r for r in self.all_rows if r.gid == gid), None)
        if row is not None and row.status_code == "complete":
            QMessageBox.information(
                self, "已经下完了",
                "这个任务已经完成，不能再改文件选择。\n要重新挑，请删掉任务再添一次。")
            return
        spec = self._compress(ids)
        was_active = bool(row and row.status_code == "active")
        if self._change_select(gid, spec, unpause=was_active):
            self._files_dirty = False
            self.btn_apply_files.setEnabled(False)
            sel = sum(int(self.tab_files.item(i, 0).data(Qt.ItemDataRole.UserRole + 1) or 0)
                      for i in range(self.tab_files.rowCount())
                      if self.tab_files.item(i, 0) is not None
                      and self.tab_files.item(i, 0).checkState() == Qt.CheckState.Checked)
            self.lb_state.setText("已更新：只下 %d 个文件，共 %s" %
                                  (len(ids), fmt_size(sel)))
            self._poke()
            QTimer.singleShot(600, lambda: self._refresh_detail(force=True))

    def _refresh_detail(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_detail < 0.7:
            return
        self._last_detail = now

        row = next((r for r in self.visible_rows if r.gid == self._selected_gid), None)
        if row is None:
            for k, lb in self._info_labels.items():
                lb.setText("—")
            self.lb_error.hide()
            self.lb_error.clear()
            self.memo_edit.clear()
            self.memo_edit.setEnabled(False)
            self._memo_edit_gid = ""
            self.tab_files.setRowCount(0)
            self._files_gid = ""
            self._files_rows = 0
            self.lb_files.setText("")
            self.btn_apply_files.setEnabled(False)
            self.tab_peers.setRowCount(0)
            self.piece_map.set_bitfield("", 0)
            return

        L = self._info_labels
        L["name"].setText(row.name)
        self.memo_edit.setEnabled(True)
        # 只在「没在编辑」时换内容 / 换绑 gid；正在敲字时不动它，
        # 否则轮询一刷就把你写了一半的备注冲掉，或者存到别人头上
        if not self.memo_edit.hasFocus():
            self._memo_edit_gid = row.gid
            if self.memo_edit.text() != row.memo:
                self.memo_edit.setText(row.memo)
        L["status"].setText(brand.status_text(row.state, row.verify_ok))
        self.lb_error.setVisible(bool(row.error))
        if row.error:
            self.lb_error.setText(self._error_hint(row.error))
        else:
            self.lb_error.clear()
        L["size"].setText(fmt_size(row.size))
        L["done"].setText("%s / %s" % (fmt_size(row.done), fmt_size(row.size)))
        L["speed"].setText("↓ %s　↑ %s" % (fmt_speed(row.dl), fmt_speed(row.ul)))
        L["peers"].setText("%d 个种子 / %d 个连接" % (row.seeders, row.conns))
        L["dir"].setText(row.dir)
        L["gid"].setText(row.gid)

        rpc = self.engine.rpc
        if not rpc or not row.gid:
            return

        try:
            st = rpc.call("aria2.tellStatus", [row.gid,
                                               ["files", "infoHash", "bitfield", "numPieces",
                                                "pieceLength", "bittorrent"]])
        except Aria2Error:
            return
        L["hash"].setText(st.get("infoHash") or "—（HTTP 任务无哈希）")

        files = st.get("files") or []
        self._fill_files(row.gid, files, st.get("dir", ""))

        self.piece_map.set_bitfield(st.get("bitfield") or "", int(st.get("numPieces") or 0))

        if self.detail.currentWidget() is self.tab_peers:
            try:
                peers = rpc.call("aria2.getPeers", [row.gid]) or []
            except Aria2Error:
                peers = []
            self.tab_peers.setRowCount(len(peers))
            for i, pr in enumerate(peers):
                addr = "%s:%s" % (pr.get("ip", ""), pr.get("port", ""))
                kind = "做种" if pr.get("seeder") == "true" else "下载"
                self.tab_peers.setItem(i, 0, QTableWidgetItem(addr))
                self.tab_peers.setItem(i, 1, QTableWidgetItem(fmt_speed(pr.get("downloadSpeed"))))
                self.tab_peers.setItem(i, 2, QTableWidgetItem(fmt_speed(pr.get("uploadSpeed"))))
                self.tab_peers.setItem(i, 3, QTableWidgetItem(kind))
                self.tab_peers.setItem(i, 4, QTableWidgetItem("%s%%" % pr.get("peerProgress", "")))

        if self.detail.tabText(self.detail.currentIndex()) == "日志":
            self._tail_log()

    def _tail_log(self) -> None:
        try:
            if paths.ENGINE_LOG.exists() and paths.ENGINE_LOG.stat().st_size:
                with open(paths.ENGINE_LOG, "rb") as fh:
                    fh.seek(max(0, paths.ENGINE_LOG.stat().st_size - 48_000))
                    data = fh.read().decode("utf-8", "replace")
                lines = data.splitlines()[-200:]
                self.log_box.setPlainText("\n".join(lines))
                sb = self.log_box.verticalScrollBar()
                sb.setValue(sb.maximum())
        except OSError:
            pass

    def _open_log(self) -> None:
        try:
            subprocess.Popen(["notepad", str(paths.ENGINE_LOG)])
        except OSError:
            pass

    def _open_download_dir(self) -> None:
        d = Path(self.settings.get("download_dir") or paths.DL_DIR)
        d.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["explorer", str(d)])
        except OSError:
            pass

    # ================= 关闭 =================
    def changeEvent(self, event):  # noqa: N802
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized() \
                and self.settings.get("minimize_to_tray"):
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event):  # noqa: N802
        self._save_window_state()
        if not self._really_quit and self.settings.get("close_to_tray", True):
            event.ignore()
            self.hide()
            if self.tray is not None and self.tray.supportsMessages():
                self.tray.showMessage(
                    brand.APP_NAME,
                    "还在后台干饭，双击托盘图标回来。",
                    QSystemTrayIcon.MessageIcon.Information, 3000)
            return
        if hasattr(self, "worker"):
            self.worker.stop()
            self.worker.wait(2500)
        self.store.save(force=True)
        if self.api:
            try:
                self.api.stop()
            except Exception:  # noqa: BLE001
                pass
        if self.pet:
            self.pet.close()
        if self.tray:
            self.tray.hide()
        self.engine.shutdown()
        event.accept()
        if self._really_quit:
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)


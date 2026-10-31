# -*- coding: utf-8 -*-
"""选项：常规 / 连接 / 限速 / BitTorrent / 通知 / 界面 / 桌宠。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl, QStandardPaths, QSize, QRectF, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from .. import background, config, notify, paths, pet_skin, shell_integration


def _kb_spin(value: int) -> QSpinBox:
    sp = QSpinBox()
    sp.setRange(0, 1_000_000)
    sp.setSingleStep(256)
    sp.setSuffix(" KB/s")
    sp.setSpecialValueText("不限")
    sp.setValue(max(0, int(value) // 1024))
    return sp


def _hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("color:#9AA3AF;")
    lb.setWordWrap(True)
    return lb


class CropCanvas(QWidget):
    """拖动裁切框：框内就是主界面会显示的区域，框外压暗。"""

    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 300)
        self.setMouseTracking(True)
        self._pm = None
        self._crop = (0.0, 0.0, 1.0, 1.0)
        self._disp = QRectF()
        self._drag = None

    def set_image(self, path: str | None, crop=None) -> None:
        pm = QPixmap(path) if path else QPixmap()
        self._pm = None if pm.isNull() else pm
        if self._pm is None:
            self._crop = (0.0, 0.0, 1.0, 1.0)
        elif crop and len(crop) == 4:
            try:
                self._crop = tuple(max(0.0, min(1.0, float(v))) for v in crop)
            except (TypeError, ValueError):
                self._crop = self._default_crop()
        else:
            self._crop = self._default_crop()
        self._recalc()
        self.update()

    def crop(self) -> list[float]:
        return list(self._crop)

    def _default_crop(self) -> tuple[float, float, float, float]:
        if self._pm is None or self._pm.width() <= 0 or self._pm.height() <= 0:
            return (0.0, 0.0, 1.0, 1.0)
        img_aspect = self._pm.width() / self._pm.height()
        target = 16 / 9
        if img_aspect > target:
            w = target / img_aspect
            h = 1.0
        else:
            w = 1.0
            h = img_aspect / target
        return ((1 - w) / 2, (1 - h) / 2, w, h)

    def _recalc(self) -> None:
        if self._pm is None:
            self._disp = QRectF()
            return
        m = 6
        aw, ah = max(1, self.width() - m * 2), max(1, self.height() - m * 2)
        iw, ih = self._pm.width(), self._pm.height()
        scale = min(aw / iw, ah / ih)
        dw, dh = iw * scale, ih * scale
        self._disp = QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)

    def _crop_rect(self) -> QRectF:
        if self._disp.isEmpty():
            return QRectF()
        x, y, w, h = self._crop
        return QRectF(self._disp.x() + x * self._disp.width(),
                      self._disp.y() + y * self._disp.height(),
                      w * self._disp.width(), h * self._disp.height())

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1F2328"))
        if self._pm is None:
            p.setPen(QColor("#9AA3AF"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "选一张图，拖动框选择主界面显示区域")
            p.end()
            return
        p.drawPixmap(self._disp, self._pm, QRectF(self._pm.rect()))
        r = self._crop_rect()
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(self._disp.x(), self._disp.y(),
                          self._disp.width(), r.top() - self._disp.y()), shade)
        p.fillRect(QRectF(self._disp.x(), r.bottom(),
                          self._disp.width(), self._disp.bottom() - r.bottom()), shade)
        p.fillRect(QRectF(self._disp.x(), r.top(),
                          r.left() - self._disp.x(), r.height()), shade)
        p.fillRect(QRectF(r.right(), r.top(),
                          self._disp.right() - r.right(), r.height()), shade)
        p.setPen(QPen(QColor("#22B8E6"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        p.end()

    def resizeEvent(self, event):  # noqa: N802
        self._recalc()
        super().resizeEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pm is not None:
            r = self._crop_rect()
            if r.contains(event.position()):
                self._drag = (event.position().x() - r.x(), event.position().y() - r.y())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag is not None and self._disp.width() > 0 and self._disp.height() > 0:
            ox, oy = self._drag
            r = self._crop_rect()
            x = event.position().x() - ox
            y = event.position().y() - oy
            x = max(self._disp.x(), min(self._disp.right() - r.width(), x))
            y = max(self._disp.y(), min(self._disp.bottom() - r.height(), y))
            nx = (x - self._disp.x()) / self._disp.width()
            ny = (y - self._disp.y()) / self._disp.height()
            w, h = self._crop[2], self._crop[3]
            if (nx, ny) != (self._crop[0], self._crop[1]):
                self._crop = (nx, ny, w, h)
                self.changed.emit(self.crop())
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)


class BackgroundPickerDialog(QDialog):
    """背景轮换库管理窗口。

    左边就是「当前会参与轮换的全部图片」；右边预览；
    顶部三个大按钮：添加图片 / 从文件夹添加 / 删除选中。
    以前只列一个文件夹、把添加藏在“换文件夹”里，用户根本看不出
    自己到底有几张图在轮换，也不知道怎么删。
    """

    def __init__(self, _start_dir: Path | None = None, parent=None,
                 settings: dict | None = None):
        super().__init__(parent)
        self.settings = settings if settings is not None else {}
        self.chosen = ""
        self.setWindowTitle("背景轮换库（这些图会随机/定时切换）")
        self.resize(940, 660)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        self.lb_count = QLabel("")
        self.lb_count.setStyleSheet("font-weight:600;color:#1F2328;")
        outer.addWidget(self.lb_count)

        bar = QHBoxLayout()
        b_add = QPushButton("添加图片…")
        b_add.setToolTip("从任意文件夹选一张或多张图，复制进轮换库")
        b_add.setStyleSheet(
            "QPushButton{background:#4D6BFE;color:#fff;border:none;border-radius:6px;"
            "padding:7px 16px;font-weight:600;} QPushButton:hover{background:#3B5AF5;}")
        b_add.clicked.connect(self._add_files)
        b_folder = QPushButton("从文件夹添加…")
        b_folder.setToolTip("先选一个文件夹，再勾选其中要加入轮换的图片")
        b_folder.clicked.connect(self._add_from_folder)
        b_del = QPushButton("删除选中")
        b_del.setToolTip("从轮换库删除选中的图（只删程序里的副本，不动原文件）")
        b_del.clicked.connect(self._delete_selected)
        bar.addWidget(b_add)
        bar.addWidget(b_folder)
        bar.addWidget(b_del)
        bar.addStretch(1)
        outer.addLayout(bar)

        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(150, 150))
        self.list.setGridSize(QSize(170, 196))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setWordWrap(True)
        self.list.setStyleSheet(
            "QListWidget{background:#FFFFFF;border:1px solid #E3E8F0;border-radius:8px;"
            "color:#1F2328;}"
            "QListWidget::item{padding:6px;border-radius:6px;}"
            "QListWidget::item:selected{background:#EAF1FF;color:#1F2328;}"
            "QListWidget::item:hover{background:#F3F7FF;}")
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemDoubleClicked.connect(lambda _i: self._apply_current())
        body.addWidget(self.list, 1)

        self.crop = CropCanvas()
        self.crop.changed.connect(self._on_crop_changed)
        right = QVBoxLayout()
        right.addWidget(self.crop, 1)
        shint = QLabel("拖动蓝色框：框内就是主界面显示的裁切区域，框外会压暗")
        shint.setStyleSheet("color:#6B7280;")
        shint.setWordWrap(True)
        right.addWidget(shint)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        bottom = QHBoxLayout()
        tip = QLabel("轮换库里的图会按设置随机/定时切换；支持 PNG / JPG / WebP / BMP / GIF")
        tip.setStyleSheet("color:#9AA3AF;")
        bottom.addWidget(tip, 1)
        b_close = QPushButton("完成")
        b_close.clicked.connect(self.accept)
        b_apply = QPushButton("设为当前背景")
        b_apply.setStyleSheet(
            "QPushButton{background:#4D6BFE;color:#fff;border:none;border-radius:6px;"
            "padding:7px 16px;font-weight:600;} QPushButton:hover{background:#3B5AF5;}")
        b_apply.clicked.connect(self._apply_current)
        bottom.addWidget(b_close)
        bottom.addWidget(b_apply)
        outer.addLayout(bottom)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        self.crop.set_image(None)
        imgs = background.images()
        self.lb_count.setText("当前轮换库：%d 张图片" % len(imgs))
        for p in imgs:
            item = QListWidgetItem(QIcon(str(p)), p.name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip("%s\n%d KB" % (p, max(1, p.stat().st_size // 1024)))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _crop_for(self, name: str):
        c = (self.settings.get("bg_crops") or {}).get(name)
        if isinstance(c, (list, tuple)) and len(c) == 4:
            try:
                return [float(v) for v in c]
            except (TypeError, ValueError):
                return None
        return None

    def _on_crop_changed(self, crop) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not path:
            return
        self.settings.setdefault("bg_crops", {})[Path(path).name] = list(crop)

    def _on_current(self, current, _previous) -> None:
        path = current.data(Qt.ItemDataRole.UserRole) if current else ""
        if not path:
            self.crop.set_image(None)
            return
        self.crop.set_image(str(path), self._crop_for(Path(str(path)).name))

    def _apply_current(self) -> None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.information(self, "没有选中", "先在左边选中一张图。")
            return
        self.chosen = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if self.chosen:
            self.accept()

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "添加图片到轮换库", str(background.folder()),
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if files and background.add(files):
            self._refresh()

    def _add_from_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择图片所在文件夹",
                                             str(background.folder()))
        if not d:
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "勾选要加入轮换的图片", d,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if files and background.add(files):
            self._refresh()

    def _delete_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            QMessageBox.information(self, "没有选中", "先在左边选中要删除的图。")
            return
        if QMessageBox.question(
                self, "删除图片",
                "从轮换库删除这 %d 张图？\n（只删 data/background 里的副本，不动原文件）"
                % len(items)) != QMessageBox.StandardButton.Yes:
            return
        n = 0
        for it in items:
            p = Path(str(it.data(Qt.ItemDataRole.UserRole) or ""))
            try:
                if p.is_file() and p.parent == background.folder():
                    p.unlink()
                    n += 1
            except OSError:
                pass
        self._refresh()
        if n:
            self.lb_count.setText(self.lb_count.text() + "（已删除 %d 张）" % n)

    def selected_paths(self) -> list[str]:
        return [i.data(Qt.ItemDataRole.UserRole)
                for i in self.list.selectedItems()
                if i.data(Qt.ItemDataRole.UserRole)]

    def accept(self) -> None:
        super().accept()


def _num_row(spin: QWidget, hint: str, tip: str = "") -> QWidget:
    """数字框 + 右边一行灰字：给不知道自己该填多少的人看。"""
    box = QWidget()
    h = QHBoxLayout(box)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    spin.setMinimumWidth(104)
    spin.setMaximumWidth(160)
    h.addWidget(spin)
    lb = _hint(hint)
    h.addWidget(lb, 1)
    if tip:
        for w in (spin, lb):
            w.setToolTip(tip)
    return box


class OptionsDialog(QDialog):
    def __init__(self, settings: dict[str, Any], parent=None,
                 on_apply: Callable[[], None] | None = None,
                 on_pet_changed: Callable[[], None] | None = None):
        super().__init__(parent)
        self.settings = settings
        self.on_apply = on_apply
        self.on_pet_changed = on_pet_changed
        self._pet_rows: dict[str, dict[str, Any]] = {}
        # 换图是即时写盘的，记下进来时每张原来是什么，"取消"才好退回去
        self._pet_orig: dict[str, bytes | None] = {}
        for _k in pet_skin.KEYS:
            try:
                self._pet_orig[_k] = (pet_skin.user_path(_k).read_bytes()
                                      if pet_skin.has_custom(_k) else None)
            except OSError:
                self._pet_orig[_k] = None
        self.setWindowTitle("选项")
        self.resize(720, 600)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._page_general(), "常规")
        self.tabs.addTab(self._page_conn(), "连接")
        self.tabs.addTab(self._page_speed(), "限速")
        self.tabs.addTab(self._page_bt(), "BitTorrent")
        self.tabs.addTab(self._page_notify(), "通知")
        self.tabs.addTab(self._page_ui(), "界面")
        self.tabs.addTab(self._page_pet(), "桌宠")
        lay.addWidget(self.tabs, 1)

        row = QHBoxLayout()
        reset = QPushButton("恢复默认")
        reset.setAutoDefault(False)
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        apply = QPushButton("应用")
        apply.setAutoDefault(False)
        apply.clicked.connect(self._apply_only)
        ok = QPushButton("确定")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        for b in (cancel, apply, ok):
            row.addWidget(b)
        lay.addLayout(row)

    # ---------- 页 ----------
    def _page_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(18, 16, 18, 14)
        form.setVerticalSpacing(10)

        self.cb_dir = QComboBox()
        self.cb_dir.setEditable(True)
        seen: list[str] = []
        for d in [self.settings.get("download_dir", "")] + list(self.settings.get("recent_dirs") or []):
            if d and d not in seen and Path(d).exists():
                seen.append(d)
        self.cb_dir.addItems(seen)
        self.cb_dir.setCurrentText(self.settings.get("download_dir") or str(paths.DL_DIR))
        b = QPushButton("浏览…")
        b.setAutoDefault(False)
        b.clicked.connect(self._pick_dir)
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(self.cb_dir, 1)
        h.addWidget(b)
        form.addRow("默认下载目录：", box)

        self.chk_close_tray = QCheckBox("关闭主窗口时缩到托盘，继续下载")
        self.chk_close_tray.setChecked(bool(self.settings.get("close_to_tray", True)))
        form.addRow("", self.chk_close_tray)

        self.chk_autostart = QCheckBox("开机自动启动（登录 Windows 就常驻托盘）")
        self.chk_autostart.setChecked(bool(self.settings.get("autostart", True)))
        form.addRow("", self.chk_autostart)

        self.chk_min_tray = QCheckBox("最小化时缩到托盘")
        self.chk_min_tray.setChecked(bool(self.settings.get("minimize_to_tray", True)))
        form.addRow("", self.chk_min_tray)

        self.chk_tray_promote = QCheckBox(
            "托盘图标常显（Windows 11：装好后不用点「^」去找）")
        self.chk_tray_promote.setToolTip(
            "Windows 11 默认把新托盘图标塞进溢出区，微软没有公开 API 强制钉出来。\n"
            "这里会写 HKCU\\Control Panel\\NotifyIconSettings 里本程序的 IsPromoted=1，\n"
            "等价于你手动把图标拖出来；只对安装版 exe 有效（源码版共用 pythonw.exe，不能动）。\n"
            "如果当前图标还没出现在系统托盘，程序会启动后多试几次；\n"
            "个别系统要重启资源管理器/重新登录才立刻生效。")
        self.chk_tray_promote.setChecked(bool(self.settings.get("tray_promote", True)))
        form.addRow("", self.chk_tray_promote)

        self.chk_assoc = QCheckBox("把 .torrent 文件关联到本程序（双击种子直接添任务）")
        self.chk_assoc.setChecked(self._assoc_checked())
        form.addRow("", self.chk_assoc)

        self.chk_auto_title = QCheckBox(
            "自动给已下载的种子任务改名为核心内容（从文件名提关键词）")
        self.chk_auto_title.setToolTip(
            "从种子里的文件名提取最多 10 个关键词，加年月，作为列表里显示的名字。\n"
            "只改显示名，不动磁盘上的文件/文件夹 —— 改磁盘名会破坏做种校验。")
        self.chk_auto_title.setChecked(bool(self.settings.get("auto_title", True)))
        form.addRow("", self.chk_auto_title)

        note = QLabel("下载目录只影响新建任务的默认值，已有任务不受影响。")
        note.setStyleSheet("color:#9AA3AF;")
        note.setWordWrap(True)
        form.addRow("", note)
        return w

    def _page_conn(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(18, 16, 18, 14)
        form.setVerticalSpacing(12)

        head = QLabel("不知道填多少就用推荐值。右边括号里是可填范围，超了引擎会不认。")
        head.setStyleSheet("color:#6B7280;")
        head.setWordWrap(True)
        form.addRow("", head)

        self.sp_conc = QSpinBox()
        self.sp_conc.setRange(1, 20)
        self.sp_conc.setValue(int(self.settings.get("max_concurrent", 5)))
        form.addRow("同时下载任务数：", _num_row(
            self.sp_conc, "推荐 5（范围 1–20）",
            "同时几个任务在跑。开太多会互相抢带宽和硬盘，总速度不会更快。"))

        self.sp_conn = QSpinBox()
        self.sp_conn.setRange(1, 16)          # aria2 硬上限就是 16，填 17 引擎直接起不来
        self.sp_conn.setValue(int(self.settings.get("max_conn_per_server", 16)))
        form.addRow("单服务器最大连接：", _num_row(
            self.sp_conn, "推荐 16（范围 1–16）",
            "对同一个网站同时开几条连接。aria2 上限就是 16，"
            "填更大它会拒绝启动，所以这里只让填到 16。"))

        self.sp_split = QSpinBox()
        self.sp_split.setRange(1, 32)
        self.sp_split.setValue(int(self.settings.get("split", 16)))
        form.addRow("单任务分片数：", _num_row(
            self.sp_split, "推荐 16（范围 1–32）",
            "把一个文件切成几段分别下。一般和「单服务器最大连接」持平最合适，"
            "再多就是白占内存。有多个镜像源时才需要调高。"))

        self.sp_peers = QSpinBox()
        self.sp_peers.setRange(0, 2000)
        self.sp_peers.setSpecialValueText("不限")
        self.sp_peers.setValue(int(self.settings.get("bt_max_peers", 300)))
        form.addRow("BT 单任务最大 Peer：", _num_row(
            self.sp_peers, "推荐 300（范围 0–2000，0 = 不限）",
            "一个种子同时连多少个别人。家用 200–500 足够，"
            "开太大反而会让连接调度变卡。"))

        self.chk_dht = QCheckBox("启用 DHT（无 Tracker 也能找到人）")
        self.chk_dht.setChecked(bool(self.settings.get("enable_dht", True)))
        form.addRow("", self.chk_dht)

        note = QLabel("并发、连接数、分片即时生效；DHT 需要重启程序。")
        note.setStyleSheet("color:#9AA3AF;")
        note.setWordWrap(True)
        form.addRow("", note)
        return w

    def _page_speed(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(18, 16, 18, 14)
        form.setVerticalSpacing(12)

        head = QLabel("点一下选档位：不限速 = 不封顶，不是 0；要限就选一个比实际带宽小的值。")
        head.setStyleSheet("color:#6B7280;")
        head.setWordWrap(True)
        form.addRow("", head)

        # 全局下载和工具栏的限速是同一个设置，档位必须一致
        dl_global = [(0, "不限速"), (1024, "1 MB/s"), (5120, "5 MB/s"),
                     (10240, "10 MB/s"), (20480, "20 MB/s"), (30720, "30 MB/s"),
                     (51200, "50 MB/s"), (102400, "100 MB/s"),
                     (204800, "200 MB/s"), (512000, "500 MB/s")]
        # 单任务下载是给单个任务掐小水管，保留 KB 档，和全局不是一回事
        dl_task = [(0, "不限速"), (100, "100 KB/s"), (256, "256 KB/s"),
                   (512, "512 KB/s"), (1024, "1 MB/s"), (2048, "2 MB/s"),
                   (5120, "5 MB/s"), (10240, "10 MB/s"), (30720, "30 MB/s")]
        upload = [(0, "不限速"), (50, "50 KB/s"), (100, "100 KB/s"),
                  (256, "256 KB/s"), (512, "512 KB/s"),
                  (1024, "1 MB/s"), (2048, "2 MB/s")]

        def speed_combo(choices, value):
            cb = QComboBox()
            for kb, label in choices:
                cb.addItem(label, kb)
            cur = max(0, round(int(value or 0) / 1024))
            idx = cb.findData(cur)
            if idx < 0:
                cb.addItem("当前 %d KB/s" % cur, cur)
                idx = cb.count() - 1
            cb.setCurrentIndex(idx)
            cb.setFixedWidth(150)
            return cb

        self.cb_dl = speed_combo(dl_global, self.settings.get("max_dl", 0))
        form.addRow("全局下载限速：", _num_row(
            self.cb_dl, "和工具栏「限速」同一个值",
            "所有任务加起来的总封顶；默认不限速。"))
        self.cb_ul = speed_combo(upload, self.settings.get("max_ul", 0))
        form.addRow("全局上传限速：", _num_row(
            self.cb_ul, "默认 100 KB/s，别掐到 0",
            "所有任务合计的上传上限；太低别人不愿意连你，下载反而更慢。"))
        self.cb_pdl = speed_combo(dl_task, self.settings.get("per_task_dl", 0))
        form.addRow("单任务下载限速：", _num_row(
            self.cb_pdl, "只限制单个任务，默认不限速",
            "这是给某一个任务单独掐速度用的，和上面的全局总速度不是一回事。"))
        self.cb_pul = speed_combo(upload, self.settings.get("per_task_ul", 0))
        form.addRow("单任务上传限速：", _num_row(
            self.cb_pul, "只限制单个任务，默认 100 KB/s",
            "同上，一般不用动。"))

        lay_line = QFrame()
        lay_line.setFrameShape(QFrame.Shape.HLine)
        lay_line.setStyleSheet("color:#EEF0F4;")
        form.addRow("", lay_line)

        seed_head = QLabel("给鱼群分饭（下完之后继续上传给别人）")
        seed_head.setStyleSheet("color:#3A7BD5;font-weight:600;")
        form.addRow("", seed_head)

        self.cb_seed = QComboBox()
        self.cb_seed.addItem("60 分钟", 60)
        self.cb_seed.addItem("6 小时", 360)
        self.cb_seed.addItem("1 天", 1440)
        self.cb_seed.addItem("1 周", 10080)
        self.cb_seed.addItem("长期", 525600)
        self.cb_seed.addItem("不分饭（下完就停）", 0)
        self.cb_seed.setFixedWidth(150)
        _cur_seed = int(self.settings.get("seed_time", 60) or 60)
        _seed_idx = self.cb_seed.findData(_cur_seed)
        self.cb_seed.setCurrentIndex(_seed_idx if _seed_idx >= 0 else 0)
        _seed_row = QWidget()
        _seed_h = QHBoxLayout(_seed_row)
        _seed_h.setContentsMargins(0, 0, 0, 0)
        _seed_h.setSpacing(10)
        _seed_h.addWidget(self.cb_seed)
        _seed_h.addWidget(_hint("默认 60 分钟；点一下选，不用敲数字。多留一会儿，后面的人就能少等一会儿"))
        _seed_h.addStretch(1)
        form.addRow("分饭时长：", _seed_row)

        seed_note = QLabel("谢谢你愿意做种。BT 不是网盘，你这次能下到，也是因为前面有人多留了一会儿。\n"
                           "哪怕只留 60 分钟，也可能正好帮一个人把饭吃完；"
                           "改完重启程序生效。已经下完的任务可以在列表里右键「再分一轮饭」。")
        seed_note.setStyleSheet("color:#9AA3AF;")
        seed_note.setWordWrap(True)
        form.addRow("", seed_note)
        seed_note.setStyleSheet("color:#9AA3AF;")
        seed_note.setWordWrap(True)
        form.addRow("", seed_note)

        note = QLabel("保存后立即生效，不用重启。工具栏上的限速下拉改的也是这里。")
        note.setStyleSheet("color:#9AA3AF;")
        note.setWordWrap(True)
        form.addRow("", note)
        return w

    def _page_bt(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(18, 16, 18, 14)
        form.setVerticalSpacing(10)

        port = QLabel("127.0.0.1:%d（RPC，仅本机）" % paths.RPC_PORT)
        port.setStyleSheet("color:#6B7280;")
        form.addRow("引擎端口：", port)

        note = QLabel("分饭（上传）的时长挪到「限速」页了 —— "
                      "它本来就是上传的事，放一块更好找。")
        note.setStyleSheet("color:#9AA3AF;")
        note.setWordWrap(True)
        form.addRow("", note)
        return w

    def _page_notify(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)

        head = QLabel("下载完成时用哪些方式叫你（可以同时勾）")
        head.setStyleSheet("color:#6B7280;")
        lay.addWidget(head)

        self.chk_n_sound = QCheckBox("声音提示")
        self.chk_n_sound.setChecked(bool(self.settings.get("notify_sound", True)))
        self.chk_n_tray = QCheckBox("通知栏通知（右下角系统气泡）")
        self.chk_n_tray.setChecked(bool(self.settings.get("notify_tray", True)))
        self.chk_n_bubble = QCheckBox("大肥鱼气泡卡片（带「打开文件夹 / 直接打开」按钮）")
        self.chk_n_bubble.setChecked(bool(self.settings.get("notify_bubble", True)))
        for c in (self.chk_n_sound, self.chk_n_tray, self.chk_n_bubble):
            lay.addWidget(c)

        lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("提示音："))
        self.cb_sound = QComboBox()
        for label, fn in notify.BUILTIN_SOUNDS:
            self.cb_sound.addItem(label, fn)
        cur = self.settings.get("notify_sound_file") or ""
        self._custom_sound = ""
        if cur and not any(fn == cur for _l, fn in notify.BUILTIN_SOUNDS):
            self._custom_sound = cur
            self.cb_sound.addItem("自定义 · %s" % Path(cur).stem, cur)
            self.cb_sound.setCurrentIndex(self.cb_sound.count() - 1)
        self.cb_sound.addItem("选择其他文件…", "__pick__")
        self.cb_sound.currentIndexChanged.connect(self._on_sound_changed)
        row.addWidget(self.cb_sound, 1)
        b_try = QPushButton("试听")
        b_try.setAutoDefault(False)
        b_try.clicked.connect(self._preview)
        row.addWidget(b_try)
        lay.addLayout(row)

        # 音量：ciallo 那声本身挺好听，但原文件太响，默认就压到 60%
        vrow = QHBoxLayout()
        vrow.setSpacing(8)
        vrow.addWidget(QLabel("音量："))
        self.sl_vol = QSlider(Qt.Orientation.Horizontal)
        self.sl_vol.setRange(0, 100)
        self.sl_vol.setSingleStep(5)
        self.sl_vol.setPageStep(10)
        self.sl_vol.setValue(int(self.settings.get("notify_volume", notify.DEFAULT_VOLUME)))
        self.sl_vol.setFixedWidth(240)
        vrow.addWidget(self.sl_vol)
        self.lb_vol = QLabel("%d%%" % self.sl_vol.value())
        self.lb_vol.setFixedWidth(42)
        self.lb_vol.setStyleSheet("color:#3A7BD5;font-weight:600;")
        vrow.addWidget(self.lb_vol)
        tip_vol = QLabel("拖动试听一下，嫌吵就往左")
        tip_vol.setStyleSheet("color:#9AA3AF;")
        vrow.addWidget(tip_vol, 1)
        self.sl_vol.valueChanged.connect(self._on_vol_changed)
        lay.addLayout(vrow)

        self.chk_only_hidden = QCheckBox("只在主窗口不在前台时通知（正在看窗口就不吵）")
        self.chk_only_hidden.setChecked(bool(self.settings.get("notify_only_when_hidden", False)))
        lay.addWidget(self.chk_only_hidden)

        note = QLabel("提示音支持 .wav（推荐）和常见的 .mp3；默认是内置的 Ciallo～。")
        note.setStyleSheet("color:#9AA3AF;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _page_ui(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(18, 16, 18, 14)
        form.setVerticalSpacing(10)

        cb = QComboBox()
        cb.addItem("浅色（默认）", "light")
        cb.setCurrentIndex(0)
        form.addRow("主题：", cb)

        form.addRow(self._build_bg_group())

        note = QLabel("窗口大小、列宽会自动记住。")
        note.setStyleSheet("color:#9AA3AF;")
        form.addRow("", note)
        return w

    # ---------- 桌宠形象 ----------
    def _page_pet(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 14, 18, 12)
        outer.setSpacing(8)

        self.chk_pet = QCheckBox("显示桌面宠物（蓝色大肥鱼）")
        self.chk_pet.setChecked(bool(self.settings.get("show_pet", True)))
        self.chk_pet2 = self.chk_pet
        outer.addWidget(self.chk_pet)

        tip = QLabel(
            "六张图各管一段状态，换哪张都行。PNG / JPG / WebP 都能用；"
            "程序会白底自动抠、主体居中缩进 512 方框，原图不会被改。\n"
            "作者已经在图库里放好了多张现成图，直接挑就行，也可以自己往里丢图再「换张图…」。\n"
            "推荐正方形、长边不超过 4096px、文件不超过 10MB"
            "（程序硬上限 24MB / 约 8000×8000，超过会自动缩小处理，太大的图会占内存、可能卡顿）。\n"
            "没有合适的图可以用 AI 生成：把「打开图片目录」里的图作为参考素材一起发给 AI，"
            "让它按同一角色、正面半身、纯白或透明背景、正方形来画，再换进来。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#6B7280;")
        outer.addWidget(tip)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 4, 0)
        rows.setSpacing(4)
        for key, name, note in pet_skin.STATES:
            rows.addWidget(self._pet_row(key, name, note))
        rows.addStretch(1)
        area.setWidget(holder)
        outer.addWidget(area, 1)

        foot = QHBoxLayout()
        self.lb_pet_tip = QLabel("")
        self.lb_pet_tip.setStyleSheet("color:#3A7BD5;")
        foot.addWidget(self.lb_pet_tip, 1)

        btn_dir = QPushButton("打开图片目录")
        btn_dir.setAutoDefault(False)
        btn_dir.setToolTip("现成的图都在这个目录里，可以直接挑；\n"
                           "也可以自己往这里丢图，再点「换张图…」选它；\n"
                           "想用 AI 生成新姿势，就把这里的图当参考素材一起发给 AI。")
        btn_dir.clicked.connect(self._pet_open_lib)
        btn_all = QPushButton("全部还原")
        btn_all.setAutoDefault(False)
        btn_all.clicked.connect(self._pet_reset_all)
        foot.addWidget(btn_dir)
        foot.addWidget(btn_all)
        outer.addLayout(foot)
        return w

    def _build_bg_group(self):
        """界面页里的主界面背景设置。"""
        from PySide6.QtWidgets import QGroupBox
        bgbox = QGroupBox("主界面背景（静态图，可选）")
        self.bg_box = bgbox
        bgl = QVBoxLayout(bgbox)
        bgl.setSpacing(4)
        _pic = QLabel("图片背景（推荐：从图库轮换）")
        _pic.setStyleSheet("color:#3A7BD5;font-weight:600;")
        bgl.addWidget(_pic)
        self.lb_bg_count = QLabel("")
        self.lb_bg_count.setStyleSheet("color:#6B7280;")
        bgl.addWidget(self.lb_bg_count)
        bgrow = QHBoxLayout()
        btn_bg = QPushButton("选择背景图…")
        btn_bg.setToolTip("打开带预览的选图窗口；可以一次选多张，复制到 data/background")
        btn_bg.clicked.connect(self._bg_pick)
        bgrow.addWidget(btn_bg)
        bgrow.addStretch(1)
        bgl.addLayout(bgrow)
        self.chk_bg_random = QCheckBox("每次启动随机换一张")
        self.chk_bg_random.setChecked(bool(self.settings.get("bg_random", True)))
        bgl.addWidget(self.chk_bg_random)
        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("白纱浓度："))
        self.sl_bg_veil = QSlider(Qt.Orientation.Horizontal)
        self.sl_bg_veil.setRange(0, 100)
        self.sl_bg_veil.setValue(int(self.settings.get("bg_veil", 50)))
        self.sl_bg_veil.setFixedWidth(220)
        self.lb_bg_veil = QLabel("%d%%" % self.sl_bg_veil.value())
        self.lb_bg_veil.setFixedWidth(42)
        self.lb_bg_veil.setStyleSheet("color:#3A7BD5;font-weight:600;")
        self.sl_bg_veil.valueChanged.connect(
            lambda v: self.lb_bg_veil.setText("%d%%" % v))
        vrow.addWidget(self.sl_bg_veil)
        vrow.addWidget(self.lb_bg_veil)
        vrow.addWidget(QLabel("（0 = 原图，100 = 接近白底）"))
        vrow.addStretch(1)
        bgl.addLayout(vrow)
        self.chk_bg_timer = QCheckBox("使用时自动切换")
        self.chk_bg_timer.setChecked(bool(self.settings.get("bg_timer_on", True)))
        bgl.addWidget(self.chk_bg_timer)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("　　每"))
        self.sp_bg_min = QSpinBox()
        self.sp_bg_min.setRange(1, 240)
        self.sp_bg_min.setSuffix(" 分钟")
        mins = int(self.settings.get("bg_interval_min", 3) or 3)
        self.sp_bg_min.setValue(max(1, mins))
        self.sp_bg_min.setEnabled(self.chk_bg_timer.isChecked())
        self.chk_bg_timer.toggled.connect(self.sp_bg_min.setEnabled)
        brow.addWidget(self.sp_bg_min)
        brow.addWidget(QLabel("自动换一张"))
        brow.addStretch(1)
        bgl.addLayout(brow)
        bgl.addSpacing(6)
        _blank_tip = QLabel("如果暂时不想看图片，可以勾选下面这项；图库会保留，取消后还能继续轮换。")
        _blank_tip.setStyleSheet("color:#9AA3AF;")
        _blank_tip.setWordWrap(True)
        bgl.addWidget(_blank_tip)
        self.chk_bg_blank = QCheckBox("使用空白背景（不显示图片）")
        self.chk_bg_blank.setToolTip("勾选后上面的换图/套图/定时切换区域禁用，主界面回到纯色背景")
        self.chk_bg_blank.setChecked(bool(self.settings.get("bg_blank", False)))
        bgl.addWidget(self.chk_bg_blank)
        self.chk_bg_blank.toggled.connect(self._toggle_bg_blank)
        self._toggle_bg_blank(self.chk_bg_blank.isChecked())
        self._refresh_bg_count()
        return bgbox

    def _toggle_bg_blank(self, blank: bool) -> None:
        """空白背景是排它选项：勾上后整块换图设置变灰不可用。"""
        for w in self.bg_box.findChildren(QWidget):
            if w is self.chk_bg_blank:
                continue
            w.setEnabled(not blank)
        if not blank:
            self.sp_bg_min.setEnabled(self.chk_bg_timer.isChecked())

    def _refresh_bg_count(self) -> None:
        imgs = background.images()
        self.lb_bg_count.setText(
            "图库里共 %d 张，保存在 %s" % (len(imgs), background.folder()))

    def _bg_pick(self) -> None:
        # 窗口内部自己负责添加/删除轮换库、裁切位置；这里只负责打开 + 回到设置页刷新
        dlg = BackgroundPickerDialog(background.folder(), self, settings=self.settings)
        dlg.exec()
        self._refresh_bg_count()
        if getattr(dlg, "chosen", ""):
            apply_now = getattr(self.parent(), "_apply_background_path", None)
            if callable(apply_now):
                apply_now(dlg.chosen)

    def _bg_clear(self) -> None:
        if QMessageBox.question(self, "清空背景",
                                "清空 data/background 里由程序保存的背景图？") \
                != QMessageBox.StandardButton.Yes:
            return
        background.clear()
        self._refresh_bg_count()

    def _pet_open_lib(self) -> None:
        pet_skin.ensure_library()
        d = pet_skin.library_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        self.lb_pet_tip.setText("已打开 %s" % d)

    def _pet_row(self, key: str, name: str, note: str) -> QWidget:
        box = QFrame()
        box.setObjectName("petRow")
        box.setFixedHeight(56)
        box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        box.setStyleSheet(
            "QFrame#petRow{background:#F7F9FC;border:1px solid #E6EAF2;border-radius:8px;}")
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(10)

        thumb = QLabel()
        thumb.setFixedSize(46, 46)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("border:none;color:#B0B7C3;")
        h.addWidget(thumb)

        col = QVBoxLayout()
        col.setSpacing(1)
        lb_name = QLabel(name)
        lb_name.setStyleSheet("border:none;font-weight:600;")
        lb_note = QLabel(note)
        lb_note.setStyleSheet("border:none;color:#8A93A0;")
        # 说明这行再长也不能把卡片撑宽（否则横向出滚动条），让它自己缩
        lb_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        col.addWidget(lb_name)
        col.addWidget(lb_note)
        h.addLayout(col, 1)

        src = QLabel()
        src.setFixedWidth(70)
        src.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        src.setStyleSheet("border:none;color:#9AA3AF;")
        h.addWidget(src)

        pick = QPushButton("换张图…")
        pick.setAutoDefault(False)
        pick.setFixedWidth(84)
        pick.clicked.connect(lambda _=False, k=key: self._pet_pick(k))
        back = QPushButton("还原")
        back.setAutoDefault(False)
        back.setFixedWidth(58)
        back.clicked.connect(lambda _=False, k=key: self._pet_reset(k))
        h.addWidget(pick)
        h.addWidget(back)

        self._pet_rows[key] = {"thumb": thumb, "src": src, "reset": back}
        self._pet_refresh(key)
        return box

    def _pet_refresh(self, key: str) -> None:
        r = self._pet_rows.get(key)
        if not r:
            return
        pm = pet_skin.thumb(key, 46)
        if pm.isNull():
            r["thumb"].setText("缺图")
        else:
            r["thumb"].setText("")
            r["thumb"].setPixmap(pm)
        custom = pet_skin.has_custom(key)
        r["src"].setText("我自己换的" if custom else "内置")
        r["src"].setStyleSheet("border:none;color:%s;" % ("#3A7BD5" if custom else "#9AA3AF"))
        r["reset"].setEnabled(custom)

    def _pet_pick(self, key: str) -> None:
        pet_skin.ensure_library()
        start = pet_skin.library_dir()
        p, _ = QFileDialog.getOpenFileName(
            self, "选一张图当「%s」" % pet_skin.LABELS[key],
            str(start) if start.exists() else "",
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;所有文件 (*.*)")
        if not p:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, msg = pet_skin.import_image(p, key)
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            QMessageBox.warning(self, "没换成", msg)
            return
        self._pet_refresh(key)
        self.lb_pet_tip.setText(msg)
        if self.on_pet_changed:
            self.on_pet_changed()

    def _pet_reset(self, key: str) -> None:
        pet_skin.reset(key)
        self._pet_refresh(key)
        self.lb_pet_tip.setText("「%s」已还原成内置图" % pet_skin.LABELS[key])
        if self.on_pet_changed:
            self.on_pet_changed()

    def _pet_reset_all(self) -> None:
        n = pet_skin.custom_count()
        if not n:
            self.lb_pet_tip.setText("六张都还是内置的，没什么可还原")
            return
        r = QMessageBox.question(
            self, "全部还原",
            "把你换过的 %d 张全删掉、退回内置图？" % n,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return
        n = pet_skin.reset_all()
        for k in self._pet_rows:
            self._pet_refresh(k)
        self.lb_pet_tip.setText("已还原 %d 张" % n)
        if self.on_pet_changed:
            self.on_pet_changed()

    # ---------- 事件 ----------
    def _assoc_checked(self) -> bool:
        try:
            return shell_integration.is_registered()
        except Exception:  # noqa: BLE001
            return False

    def _pick_dir(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "选择下载目录", self.cb_dir.currentText())
        if p:
            self.cb_dir.setCurrentText(p)

    def _on_sound_changed(self, _i: int) -> None:
        if self.cb_sound.currentData() == "__pick__":
            p, _ = QFileDialog.getOpenFileName(
                self, "选择提示音", "", "音频文件 (*.wav *.mp3 *.m4a *.ogg);;所有文件 (*.*)")
            if p:
                self._custom_sound = p
                self.cb_sound.blockSignals(True)
                self.cb_sound.insertItem(self.cb_sound.count() - 1,
                                         "自定义 · %s" % Path(p).stem, p)
                self.cb_sound.setCurrentIndex(self.cb_sound.count() - 2)
                self.cb_sound.blockSignals(False)
            else:
                self.cb_sound.blockSignals(True)
                self.cb_sound.setCurrentIndex(0)
                self.cb_sound.blockSignals(False)

    def _on_vol_changed(self, v: int) -> None:
        self.lb_vol.setText("%d%%" % v)
        if v == 0:
            self.lb_vol.setStyleSheet("color:#9CA3AF;font-weight:600;")
        else:
            self.lb_vol.setStyleSheet("color:#3A7BD5;font-weight:600;")

    def _cur_sound(self) -> str:
        data = self.cb_sound.currentData()
        return "" if data in (None, "__pick__") else str(data)

    def _preview(self) -> None:
        # 按当前滑块音量播 —— 否则"试听"跟实际响度对不上，等于没试
        notify.play_sound(self._cur_sound(), self.sl_vol.value())

    def _collect(self) -> None:
        s = self.settings
        d = self.cb_dir.currentText().strip()
        if d:
            s["download_dir"] = d
            recent = [x for x in (s.get("recent_dirs") or []) if x != d]
            s["recent_dirs"] = [d] + recent[:7]
        s["close_to_tray"] = self.chk_close_tray.isChecked()
        s["minimize_to_tray"] = self.chk_min_tray.isChecked()
        s["tray_promote"] = self.chk_tray_promote.isChecked()
        # 注册表不在这里改：_collect 在测试里也会被调，不该伸手动系统。
        # 真正写键在 MainWindow._apply_settings（用户点了确定才生效）。
        s["autostart"] = self.chk_autostart.isChecked()
        s["show_pet"] = self.chk_pet.isChecked() and self.chk_pet2.isChecked()
        s["auto_title"] = self.chk_auto_title.isChecked()
        s["max_concurrent"] = self.sp_conc.value()
        s["max_conn_per_server"] = self.sp_conn.value()
        s["split"] = self.sp_split.value()
        s["bt_max_peers"] = self.sp_peers.value()
        s["enable_dht"] = self.chk_dht.isChecked()
        s["max_dl"] = int(self.cb_dl.currentData() or 0) * 1024
        s["max_ul"] = int(self.cb_ul.currentData() or 0) * 1024
        s["per_task_dl"] = int(self.cb_pdl.currentData() or 0) * 1024
        s["per_task_ul"] = int(self.cb_pul.currentData() or 0) * 1024
        s["seed_time"] = int(self.cb_seed.currentData() or 60)
        s["seed_ratio"] = 0.0          # 只用时长，不再用分享率重复管上传
        s["notify_sound"] = self.chk_n_sound.isChecked()
        s["notify_tray"] = self.chk_n_tray.isChecked()
        s["notify_bubble"] = self.chk_n_bubble.isChecked()
        s["notify_only_when_hidden"] = self.chk_only_hidden.isChecked()
        data = self.cb_sound.currentData()
        if data == "__pick__":
            data = self._custom_sound or ""
        s["notify_sound_file"] = "" if data in (None, "__pick__") else str(data)
        s["notify_volume"] = int(self.sl_vol.value())
        s["bg_random"] = bool(self.chk_bg_random.isChecked())
        s["bg_blank"] = bool(self.chk_bg_blank.isChecked())
        s["bg_timer_on"] = bool(self.chk_bg_timer.isChecked())
        s["bg_interval_min"] = max(1, int(self.sp_bg_min.value()))
        s["bg_veil"] = int(self.sl_bg_veil.value())

        want = self.chk_assoc.isChecked()
        try:
            if want and not shell_integration.is_registered():
                shell_integration.register()
            elif not want and shell_integration.is_registered():
                shell_integration.unregister()
            s["torrent_assoc"] = want
        except OSError as exc:
            QMessageBox.warning(self, "关联失败", "写注册表失败：%s" % exc)

    def _reset(self) -> None:
        if QMessageBox.question(self, "恢复默认", "把所有选项恢复成初始值？") \
                != QMessageBox.StandardButton.Yes:
            return
        for k, v in config.DEFAULTS.items():
            if k not in ("recent_dirs",):
                self.settings[k] = v
        if not self.settings.get("download_dir"):
            self.settings["download_dir"] = str(paths.DL_DIR)
        self.cb_dir.setCurrentText(self.settings["download_dir"])
        self.chk_close_tray.setChecked(bool(self.settings["close_to_tray"]))
        self.chk_min_tray.setChecked(bool(self.settings["minimize_to_tray"]))
        self.chk_tray_promote.setChecked(bool(self.settings["tray_promote"]))
        self.chk_pet.setChecked(bool(self.settings["show_pet"]))
        self.chk_pet2.setChecked(bool(self.settings["show_pet"]))
        self.chk_bg_random.setChecked(bool(self.settings["bg_random"]))
        self.chk_bg_blank.setChecked(bool(self.settings.get("bg_blank", False)))
        self._toggle_bg_blank(self.chk_bg_blank.isChecked())
        self.chk_bg_timer.setChecked(bool(self.settings.get("bg_timer_on", True)))
        self.sp_bg_min.setValue(max(1, int(self.settings.get("bg_interval_min", 3))))
        self.sp_bg_min.setEnabled(self.chk_bg_timer.isChecked())
        self.sl_bg_veil.setValue(int(self.settings["bg_veil"]))
        self.chk_auto_title.setChecked(bool(self.settings["auto_title"]))
        self.sp_conc.setValue(int(self.settings["max_concurrent"]))
        self.sp_conn.setValue(int(self.settings["max_conn_per_server"]))
        self.sp_split.setValue(int(self.settings["split"]))
        self.sp_peers.setValue(int(self.settings["bt_max_peers"]))
        self.chk_dht.setChecked(bool(self.settings["enable_dht"]))
        for cb, key in ((self.cb_dl, "max_dl"), (self.cb_ul, "max_ul"),
                        (self.cb_pdl, "per_task_dl"), (self.cb_pul, "per_task_ul")):
            kb = max(0, round(int(self.settings[key] or 0) / 1024))
            i = cb.findData(kb)
            if i < 0:
                cb.addItem("当前 %d KB/s" % kb, kb)
                i = cb.count() - 1
            cb.setCurrentIndex(i)
        _i = self.cb_seed.findData(int(self.settings["seed_time"]))
        self.cb_seed.setCurrentIndex(_i if _i >= 0 else 0)
        self.chk_n_sound.setChecked(True)
        self.chk_n_tray.setChecked(True)
        self.chk_n_bubble.setChecked(True)
        self.cb_sound.setCurrentIndex(0)

    def _apply_only(self) -> None:
        self._collect()
        config.save(self.settings)
        if self.on_apply:
            self.on_apply()

    def reject(self) -> None:
        """取消：把这轮换掉的桌宠图退回去——换图是即时写盘的。"""
        if self._restore_pet_snapshot() and self.on_pet_changed:
            self.on_pet_changed()
        super().reject()

    def _restore_pet_snapshot(self) -> bool:
        changed = False
        for k, data in self._pet_orig.items():
            cur = pet_skin.user_path(k)
            try:
                now = cur.read_bytes() if cur.exists() else None
            except OSError:
                now = None
            if now == data:
                continue
            changed = True
            try:
                if data is None:
                    pet_skin.reset(k)
                else:
                    pet_skin.SKIN_DIR.mkdir(parents=True, exist_ok=True)
                    cur.write_bytes(data)
            except OSError:
                pass
        return changed

    def _accept(self) -> None:
        self._apply_only()
        self.accept()

# -*- coding: utf-8 -*-
"""新建任务对话框：磁力、种子文件、普通链接都从这里进。"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from .. import config, intents, paths

SPEED_CHOICES = [
    ("不限速", 0),
    ("50 KB/s", 50),
    ("100 KB/s", 100),
    ("200 KB/s", 200),
    ("512 KB/s", 512),
    ("1 MB/s", 1024),
    ("3 MB/s", 3072),
    ("5 MB/s", 5120),
    ("10 MB/s", 10240),
    ("20 MB/s", 20480),
    ("50 MB/s", 51200),
]


def _kb(v: int) -> int:
    return v * 1024


@dataclass
class TaskRequest:
    uris: list[str] = field(default_factory=list)
    torrents: list[tuple[str, str]] = field(default_factory=list)   # (文件名, base64)
    dir: str = ""
    paused: bool = False
    per_dl: int = 0
    per_ul: int = 0
    max_concurrent: int | None = None
    max_conn: int | None = None
    choose_files: bool = False
    pause_metadata: bool = False


class AddDialog(QDialog):
    def __init__(self, settings: dict, parent=None, preset_text: str = ""):
        super().__init__(parent)
        self.settings = settings
        self.torrent_paths: list[Path] = []
        self.request: TaskRequest | None = None
        self.setWindowTitle("添一碗饭 · 新建任务")
        self.setMinimumWidth(640)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(9)

        tip = QLabel("批量：链接之间用一个空格或换行隔开就行（粘一整段分享文案也能自动挑出链接）。\n"
                     "支持磁力、种子文件、http/https/ftp。")
        tip.setStyleSheet("color:#6B7280;")
        lay.addWidget(tip)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "magnet:?xt=urn:btih:...\nhttps://example.com/file.zip")
        self.text.setMinimumHeight(116)
        if preset_text:
            self.text.setPlainText(preset_text)
        self.text.textChanged.connect(self._update_detect)
        lay.addWidget(self.text)

        row = QHBoxLayout()
        row.setSpacing(8)
        b_paste = QPushButton("粘贴剪贴板")
        b_paste.clicked.connect(self._paste)
        b_file = QPushButton("打开种子文件…")
        b_file.clicked.connect(self._pick_torrent)
        b_clear = QPushButton("清空")
        b_clear.clicked.connect(self.text.clear)
        for b in (b_paste, b_file, b_clear):
            b.setAutoDefault(False)
            row.addWidget(b)
        row.addStretch(1)
        self.lb_detect = QLabel("")
        self.lb_detect.setStyleSheet("color:#4D6BFE;")
        row.addWidget(self.lb_detect)
        lay.addLayout(row)

        self.lb_torrent = QLabel("")
        self.lb_torrent.setStyleSheet("color:#4D6BFE;")
        self.lb_torrent.setWordWrap(True)
        self.lb_torrent.hide()
        lay.addWidget(self.lb_torrent)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        self.dir_combo = QComboBox()
        self.dir_combo.setEditable(True)
        self._fill_dirs()
        b_browse = QPushButton("浏览…")
        b_browse.setAutoDefault(False)
        b_browse.clicked.connect(self._browse_dir)
        dw = QWidget()
        dl = QHBoxLayout(dw)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(6)
        dl.addWidget(self.dir_combo, 1)
        dl.addWidget(b_browse)
        form.addRow("保存到：", dw)
        lay.addLayout(form)

        # 常用目录做成一排按钮：点一下就直接成为保存目录，不用再进下拉里挑
        quick = QHBoxLayout()
        quick.setSpacing(6)
        lb_quick = QLabel("常用：")
        lb_quick.setStyleSheet("color:#6B7280;")
        quick.addWidget(lb_quick)
        self._quick_group = QButtonGroup(self)
        self._quick_group.setExclusive(True)
        for label, getter, tip_text in self._quick_specs():
            b = QPushButton(label)
            b.setAutoDefault(False)
            b.setCheckable(True)
            path = getter()
            if path:
                b.setToolTip("%s\n%s" % (path, tip_text) if tip_text else path)
                b.clicked.connect(lambda _c=0, p=path, bb=b: self._use_quick(p, bb))
            else:
                b.setEnabled(False)
                b.setToolTip(tip_text or "不可用")
            self._quick_group.addButton(b)
            quick.addWidget(b)
        quick.addStretch(1)
        lay.addLayout(quick)

        sep = QLabel("速度与连接（本次生效）")
        sep.setStyleSheet("color:#6B7280; margin-top:4px;")
        lay.addWidget(sep)

        grid = QHBoxLayout()
        grid.setSpacing(10)
        self.cb_dl = QComboBox()
        self.cb_ul = QComboBox()
        for label, kb in SPEED_CHOICES:
            self.cb_dl.addItem(label, kb)
            self.cb_ul.addItem(label, kb)
        self.cb_dl.setCurrentIndex(0)
        default_ul = int(self.settings.get("per_task_ul", 0)) // 1024
        for i, (_l, kb) in enumerate(SPEED_CHOICES):
            if kb == default_ul:
                self.cb_ul.setCurrentIndex(i)
                break

        self.sp_conc = QSpinBox()
        self.sp_conc.setRange(1, 64)
        self.sp_conc.setValue(int(self.settings.get("max_concurrent", 5)))
        self.sp_conn = QSpinBox()
        self.sp_conn.setRange(1, 32)
        self.sp_conn.setValue(int(self.settings.get("max_conn_per_server", 16)))

        for label, w in (("下载限速", self.cb_dl), ("上传限速", self.cb_ul),
                         ("并发任务", self.sp_conc), ("单服务器连接", self.sp_conn)):
            box = QVBoxLayout()
            box.setSpacing(3)
            lb = QLabel(label)
            lb.setStyleSheet("color:#6B7280;")
            box.addWidget(lb)
            box.addWidget(w)
            cw = QWidget()
            cw.setLayout(box)
            grid.addWidget(cw)
        lay.addLayout(grid)

        opts = QVBoxLayout()
        opts.setSpacing(5)
        line1 = QHBoxLayout()
        self.chk_start = QCheckBox("立即开始")
        self.chk_start.setChecked(True)
        line1.addWidget(self.chk_start)
        line1.addSpacing(14)
        self.chk_pick = QCheckBox("拿到种子后让我选文件（只要其中几集/几个文件）")
        self.chk_pick.setChecked(True)
        line1.addWidget(self.chk_pick)
        line1.addStretch(1)
        opts.addLayout(line1)
        line2 = QHBoxLayout()
        self.chk_meta = QCheckBox("磁力先只取种子信息、不下内容")
        line2.addWidget(self.chk_meta)
        line2.addStretch(1)
        opts.addLayout(line2)
        lay.addLayout(opts)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self._update_detect()
        self.dir_combo.currentTextChanged.connect(lambda _t: self._sync_quick())
        self._sync_quick()

    # ---------- 内部 ----------
    def _quick_specs(self) -> list[tuple[str, object, str]]:
        """常用目录按钮：文案、取路径、不可用时的说明。"""
        home = Path.home()
        desktop = next((home / n for n in ("Desktop", "桌面", "OneDrive/Desktop")
                        if (home / n).exists()), None)

        def drive_d() -> str:
            for c in (Path("D:/Download"), Path("D:/Downloads"), Path("D:/")):
                if c.exists():
                    return str(c)
            return ""

        def last() -> str:
            p = str(self.settings.get("last_dir") or "")
            if p and Path(p).exists():
                return p
            # 还没用过就退到最近目录里的第一个（排除程序自带目录）
            cur_dir = str(self.settings.get("download_dir") or paths.DL_DIR)
            pool = list(self.settings.get("recent_dirs") or []) or config.default_recent_dirs()
            for d in pool:
                if d and str(d) != cur_dir and Path(str(d)).exists():
                    return str(d)
            return ""

        cur = str(self.settings.get("download_dir") or paths.DL_DIR)
        return [
            ("程序目录", lambda: cur, "程序文件夹里的 Download"),
            ("桌面", (lambda: str(desktop)) if desktop else (lambda: ""), "没有桌面目录"),
            ("D 盘", drive_d, "这台机器没有 D 盘"),
            ("上次保存", last, "还没有用过的目录"),
        ]

    def _use_quick(self, path: str, _btn=None) -> None:
        """点一下就切过去；目录不存在就顺手建出来。"""
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "目录不可用", str(exc))
            return
        idx = self.dir_combo.findText(path)
        if idx >= 0:
            self.dir_combo.setCurrentIndex(idx)
        else:
            self.dir_combo.addItem(path)
            self.dir_combo.setCurrentText(path)
        # 把刚点的按钮传下去：几个按钮指向同一个目录时（"上次保存"常常就是
        # 程序目录里的 Download），只按文字匹配会点亮错的那个。
        self._sync_quick(prefer=_btn)

    def _sync_quick(self, prefer=None) -> None:
        """下拉里当前目录和哪个按钮一致，就把哪个按钮点亮。"""
        cur = self.dir_combo.currentText().strip().rstrip("\\/").lower()

        def tip_of(b) -> str:
            return (b.toolTip() or "").splitlines()[0].strip().rstrip("\\/").lower()

        chosen = None
        if prefer is not None and prefer.isEnabled() and tip_of(prefer) == cur:
            chosen = prefer
        else:
            for b in self._quick_group.buttons():
                if tip_of(b) == cur:
                    chosen = b
                    break
        for b in self._quick_group.buttons():
            if b is not chosen and b.isChecked():
                b.setChecked(False)
        if chosen is not None:
            chosen.setChecked(True)

    def _fill_dirs(self) -> None:
        cur = self.settings.get("download_dir") or str(paths.DL_DIR)
        seen: list[str] = []
        for d in [cur] + list(self.settings.get("recent_dirs") or []) + config.default_recent_dirs():
            d = str(d)
            if d and d not in seen and Path(d).exists():
                seen.append(d)
        self.dir_combo.addItems(seen)
        self.dir_combo.setCurrentText(cur)

    def _update_detect(self) -> None:
        links = intents.extract_links(self.text.toPlainText())
        n_mag = sum(1 for l in links if l.kind == "magnet")
        n_tor = sum(1 for l in links if l.kind == "torrent")
        n_url = sum(1 for l in links if l.kind == "url")
        if links:
            self.lb_detect.setText("识别到 磁力 %d · 种子 %d · 链接 %d" % (n_mag, n_tor, n_url))
        else:
            self.lb_detect.setText("")

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication
        txt = QApplication.clipboard().text().strip()
        if not txt:
            QMessageBox.information(self, "提示", "剪贴板是空的")
            return
        existing = self.text.toPlainText().strip()
        self.text.setPlainText((existing + "\n" + txt).strip() if existing else txt)

    def _pick_torrent(self) -> None:
        ps, _ = QFileDialog.getOpenFileNames(self, "选择种子文件", "",
                                            "种子文件 (*.torrent);;所有文件 (*.*)")
        if not ps:
            return
        for p in ps:
            path = Path(p)
            if path not in self.torrent_paths:
                self.torrent_paths.append(path)
        names = "、".join(p.name for p in self.torrent_paths[:3])
        more = " 等 %d 个" % len(self.torrent_paths) if len(self.torrent_paths) > 3 else ""
        self.lb_torrent.setText("已选种子：%s%s" % (names, more))
        self.lb_torrent.show()

    def _browse_dir(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_combo.currentText())
        if p:
            self.dir_combo.setCurrentText(p)
            recent = list(self.settings.get("recent_dirs") or [])
            if p not in recent:
                recent.insert(0, p)
                self.settings["recent_dirs"] = recent[:8]
                config.save(self.settings)
                self.dir_combo.clear()
                self._fill_dirs()
                self.dir_combo.setCurrentText(p)
        self._sync_quick()

    def _accept(self) -> None:
        req = self.build()
        if req is None:
            return
        self.request = req
        self.accept()

    def build(self) -> TaskRequest | None:
        d = self.dir_combo.currentText().strip()
        if not d:
            QMessageBox.warning(self, "缺少目录", "请选择保存目录")
            return None
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "目录不可用", str(exc))
            return None

        # 记住这次用的目录，下次「上次保存」按钮就能一键回去
        if str(self.settings.get("last_dir") or "") != d:
            self.settings["last_dir"] = d
            config.save(self.settings)

        raw = self.text.toPlainText()
        links = intents.extract_links(raw)
        unsupported = intents.unsupported_links(raw)

        if not links and not self.torrent_paths:
            if unsupported:
                QMessageBox.warning(self, "这个下不了",
                                    "本程序基于 aria2，不支持：\n· " + "\n· ".join(unsupported))
            else:
                QMessageBox.warning(self, "没有链接",
                                    "请输入磁力 / 链接，或选择一个 .torrent 种子文件")
            return None

        if unsupported:
            QMessageBox.information(
                self, "跳过不支持的链接",
                "以下内容会被跳过（aria2 不支持）：\n· " + "\n· ".join(unsupported))

        req = TaskRequest()
        req.dir = d
        req.paused = not self.chk_start.isChecked()
        req.choose_files = self.chk_pick.isChecked()
        req.pause_metadata = self.chk_meta.isChecked()
        req.per_dl = _kb(int(self.cb_dl.currentData() or 0))
        req.per_ul = _kb(int(self.cb_ul.currentData() or 0))
        req.max_concurrent = self.sp_conc.value()
        req.max_conn = self.sp_conn.value()

        for p in self.torrent_paths:
            try:
                req.torrents.append(
                    (p.name, base64.b64encode(p.read_bytes()).decode("ascii")))
            except OSError as exc:
                QMessageBox.warning(self, "无法读取种子", "%s\n%s" % (p.name, exc))
                return None

        # 直接粘进来的本地种子路径也照收
        for link in links:
            if link.kind != "torrent":
                continue
            p = Path(link.value)
            if any(name == p.name for name, _b in req.torrents):
                continue
            try:
                req.torrents.append(
                    (p.name, base64.b64encode(p.read_bytes()).decode("ascii")))
            except OSError:
                pass

        req.uris = [l.value for l in links if l.kind in ("magnet", "url")]

        self.settings["per_task_dl"] = req.per_dl
        self.settings["per_task_ul"] = req.per_ul
        self.settings["max_concurrent"] = req.max_concurrent
        self.settings["max_conn_per_server"] = req.max_conn
        config.save(self.settings)
        return req

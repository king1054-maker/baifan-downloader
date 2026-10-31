# -*- coding: utf-8 -*-
"""种子库：历史用过的磁力链接 / .torrent 源文件，单独备案。

注意：这里**只存"来源"**（磁力链接、.torrent 文件），不存下载回来的视频/文件。
下载好的东西在「开饭了」里；种子库是给用户二次查找、重新下载用的备案，
跟比特彗星的"种子存档"一样。

入口在左栏「给鱼群分饭」下面；选中一条 →「重新下载」会按原来源重新建任务。
"""
from __future__ import annotations

import subprocess
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .. import torrent_keep

ROLE_ENTRY = Qt.ItemDataRole.UserRole


class TorrentLibraryDialog(QDialog):
    redownload = Signal(dict)      # 参数是 torrent_keep 里的一条来源索引

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("种子库 · 下载来源备案")
        self.setModal(True)
        self.resize(780, 520)
        self.entries: list[dict] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        tip = QLabel("这里只存**磁力链接和 .torrent 源文件**（备案），"
                     "不是下载回来的文件。选中一条可以按原来源重新下载。")
        tip.setStyleSheet("color:#6B7280;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "类型", "添加时间", "来源"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 240)
        self.table.doubleClicked.connect(lambda _i: self._redownload())
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.lb_count = QLabel("")
        self.lb_count.setStyleSheet("color:#4D6BFE;")
        row.addWidget(self.lb_count)
        row.addStretch(1)
        b_open = QPushButton("打开种子文件夹")
        b_del = QPushButton("删除备案")
        b_close = QPushButton("关闭")
        b_go = QPushButton("重新下载")
        b_go.setProperty("accent", True)
        for b in (b_open, b_del, b_close, b_go):
            b.setAutoDefault(False)
        b_open.clicked.connect(self._open_folder)
        b_del.clicked.connect(self._remove)
        b_close.clicked.connect(self.reject)
        b_go.clicked.connect(self._redownload)
        row.addWidget(b_open)
        row.addWidget(b_del)
        row.addWidget(b_close)
        row.addWidget(b_go)
        b_go.setDefault(True)
        lay.addLayout(row)

        self.reload()

    # ---------- 数据 ----------
    def reload(self) -> None:
        self.entries = torrent_keep.entries()
        self.table.setRowCount(0)
        for e in self.entries:
            i = self.table.rowCount()
            self.table.insertRow(i)
            raw_name = str(e.get("name") or "（未命名来源）")
            unresolved = raw_name.startswith("磁力 · ")
            name_text = ("（未解析目标）" + raw_name) if unresolved else raw_name
            name = QTableWidgetItem(name_text)
            name.setData(ROLE_ENTRY, e)
            tip = e.get("uri") or e.get("source") or ""
            if unresolved:
                tip = ("磁力已留档，但还没拿到目标文件名；重新下载时会再解析。\n" + tip)
            if e.get("size"):
                tip += "\n目标大小：%.2f GB" % (int(e["size"]) / (1024 ** 3))
            if e.get("files"):
                tip += "\n文件数：%d" % int(e["files"])
            name.setToolTip(tip)
            self.table.setItem(i, 0, name)
            kind = "磁力" if e.get("kind") == "magnet" else "种子文件"
            self.table.setItem(i, 1, QTableWidgetItem(kind))
            ts = float(e.get("added_at") or 0)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"
            self.table.setItem(i, 2, QTableWidgetItem(when))
            src = e.get("uri") or e.get("source") or ""
            if e.get("kind") == "magnet":
                src = src or "磁力链接"
            else:
                src = "本地 .torrent 源文件"
            it = QTableWidgetItem(src)
            it.setToolTip(src)
            self.table.setItem(i, 3, it)
        self.lb_count.setText("共 %d 条来源备案　·　%s"
                              % (len(self.entries), str(torrent_keep.DIR)))
        if self.entries:
            self.table.selectRow(0)

    def selected_entry(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        it = self.table.item(row, 0)
        return it.data(ROLE_ENTRY) if it else None

    # ---------- 动作 ----------
    def _redownload(self) -> None:
        e = self.selected_entry()
        if not e:
            QMessageBox.information(self, "还没选", "先在上面选一条来源。")
            return
        self.redownload.emit(e)
        self.accept()

    def _open_folder(self) -> None:
        try:
            torrent_keep.DIR.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(torrent_keep.DIR)])
        except OSError:
            pass

    def _remove(self) -> None:
        e = self.selected_entry()
        if not e:
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除备案")
        box.setText("从种子库里删掉这条来源备案「%s」？\n\n"
                    "只删磁力/.torrent 备案，已经下载的文件不会动。"
                    % (e.get("name") or "这条来源"))
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            torrent_keep.remove(str(e.get("ihash") or ""))
            self.reload()

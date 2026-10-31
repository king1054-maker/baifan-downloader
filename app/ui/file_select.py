# -*- coding: utf-8 -*-
"""种子内文件勾选：按文件夹层次显示，可以整目录勾选。

为什么不用平铺列表（v0.10.18 之前的做法）：
  多文件夹种子（例如 `2026年08月合集/CHS/xxx.mp4`）平铺出来就是一长排，
  用户看不出「哪个文件夹是哪个版本」，只能逐个文件去猜。
  比特彗星的种子内容是一棵树：文件夹整行可勾，勾父文件夹 = 勾里面全部文件，
  子文件取消到一半时父文件夹显示半选。

对外仍然只有一个出口：`selected()` 返回选中的文件在 aria2 里的 1-based 序号。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .. import intents
from .models import fmt_size

COL_NAME, COL_SIZE, COL_PCT = 0, 1, 2
ROLE_NODE = Qt.ItemDataRole.UserRole


class _Node:
    """树节点：目录或文件。目录的 size/done/files 是子树聚合出来的。"""

    __slots__ = ("name", "is_dir", "index", "size", "done", "selected",
                 "children", "parent", "item", "files")

    def __init__(self, name: str, is_dir: bool = False, index: int = 0,
                 size: int = 0, done: int = 0, selected: bool = True):
        self.name = name
        self.is_dir = is_dir
        self.index = index
        self.size = size
        self.done = done
        self.selected = selected
        self.children: dict[str, _Node] = {}
        self.parent: _Node | None = None
        self.item: QTreeWidgetItem | None = None
        self.files: list[_Node] = []      # 目录下所有文件节点（叶子）

    def child(self, node: "_Node") -> "_Node":
        node.parent = self
        self.children[node.name] = node
        return node


def _rel_parts(files: list[dict], base_dir: str) -> list[tuple[dict, list[str]]]:
    """把 aria2 的绝对路径变成相对路径，并去掉共同的种子根目录名。

    例：`C:/dl/2026年08月合集/CHS/a.mp4` → `["CHS", "a.mp4"]`
    （`2026年08月合集` 只作为种子名，不再在树里多套一层；跟比特彗星一致）。
    """
    out: list[tuple[dict, list[str]]] = []
    for f in files:
        p = str(f.get("path") or "")
        rel = p
        if base_dir:
            try:
                rel = os.path.relpath(p, base_dir)
            except (ValueError, OSError):
                rel = p
        parts = [x for x in rel.replace("\\", "/").split("/") if x and x != "."]
        if not parts:
            parts = [str(f.get("index") or "?")]
        out.append((f, parts))
    if out and all(len(parts) >= 2 for _f, parts in out):
        roots = {parts[0] for _f, parts in out}
        if len(roots) == 1:                     # 所有文件都在同一个种子根目录下
            out = [(f, parts[1:]) for f, parts in out]
    return out


def build_tree(files: list[dict], base_dir: str = "") -> _Node:
    """把 aria2 的 files 数组建成目录树。"""
    root = _Node("", is_dir=True)
    for f, parts in _rel_parts(files, base_dir):
        node = root
        for part in parts[:-1]:
            child = node.children.get(part)
            if child is None or not child.is_dir:
                # 同名文件先占了这个名字：理论上不会发生，真遇到就改成目录
                child = _Node(part, is_dir=True)
                node.child(child)
            node = child
        leaf = _Node(parts[-1], is_dir=False,
                     index=int(f.get("index") or 0),
                     size=int(f.get("length") or 0),
                     done=int(f.get("completedLength") or 0),
                     selected=str(f.get("selected", "true")).lower() == "true")
        node.child(leaf)

    def aggregate(n: _Node) -> None:
        if not n.is_dir:
            n.files = [n]
            return
        n.files = []
        for c in n.children.values():
            aggregate(c)
            n.size += c.size
            n.done += c.done
            n.files.extend(c.files)

    aggregate(root)
    return root


class FileSelectDialog(QDialog):
    """返回选中文件在 aria2 里的 1-based 序号。"""

    def __init__(self, files: list[dict], base_dir: str = "",
                 title: str = "这碗里要哪几样？", parent=None,
                 note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(860, 640)
        self._files = files
        self._base = base_dir
        self._root = build_tree(files, base_dir)
        self._file_nodes: list[_Node] = []
        self._dir_nodes: list[_Node] = []
        self._sort_by_size = False
        self._updating = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)

        tip = QLabel(note or "勾上的才会下载；没勾的不会占空间。文件夹可以整行勾。")
        tip.setStyleSheet("color:#6B7280;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.btn_all = QPushButton("全选")
        for w in (self.btn_all,):
            w.setAutoDefault(False)
        self.btn_all.clicked.connect(self._all)
        bar.addWidget(self.btn_all)
        for text, fn in (("全不选", self._none),
                         ("仅选视频", self._video_only), ("反选", self._invert),
                         ("按大小排序", self._toggle_sort)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch(1)
        self.lb_sum = QLabel("")
        self.lb_sum.setStyleSheet("color:#4D6BFE;")
        bar.addWidget(self.lb_sum)
        lay.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["文件名称", "大小", "进度"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(False)
        hh = self.tree.header()
        hh.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(COL_PCT, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(COL_SIZE, 100)
        self.tree.setColumnWidth(COL_PCT, 90)
        self.tree.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.tree, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("就吃这些")
        ok.setProperty("accent", True)
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

        self._fill()

    # ---------- 构建 ----------
    def _make_item(self, parent, node: _Node) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent)
        it.setText(COL_NAME, node.name)
        it.setText(COL_SIZE, fmt_size(node.size))
        pct = (node.done * 100.0 / node.size) if node.size else 0.0
        it.setText(COL_PCT, "%.1f%%" % pct)
        for col in (COL_SIZE, COL_PCT):
            it.setTextAlignment(col, int(Qt.AlignmentFlag.AlignRight
                                         | Qt.AlignmentFlag.AlignVCenter))
        it.setData(0, ROLE_NODE, node)
        it.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable)
        it.setIcon(0, self.style().standardIcon(
            QStyle.StandardPixmap.SP_DirIcon if node.is_dir
            else QStyle.StandardPixmap.SP_FileIcon))
        node.item = it
        if node.is_dir:
            self._dir_nodes.append(node)
            children = list(node.children.values())
            if self._sort_by_size:
                children.sort(key=lambda c: (-c.size, c.name.lower()))
            else:
                children.sort(key=lambda c: (0 if c.is_dir else 1, c.name.lower()))
            for c in children:
                self._make_item(it, c)
        else:
            self._file_nodes.append(node)
            it.setCheckState(0, Qt.CheckState.Checked if node.selected
                             else Qt.CheckState.Unchecked)
        return it

    def _fill(self, preserve: bool = False) -> None:
        """重建整棵树。preserve=True 时按文件序号保留勾选状态（排序用）。"""
        keep: set[int] | None = None
        if preserve:
            keep = set(self.selected())
        self.tree.blockSignals(True)
        self._updating = True
        try:
            self.tree.clear()
            self._file_nodes = []
            self._dir_nodes = []
            children = list(self._root.children.values())
            if self._sort_by_size:
                children.sort(key=lambda c: (-c.size, c.name.lower()))
            else:
                children.sort(key=lambda c: (0 if c.is_dir else 1, c.name.lower()))
            for c in children:
                self._make_item(self.tree, c)
        finally:
            self._updating = False
            self.tree.blockSignals(False)
        if keep is not None:
            self._updating = True
            try:
                for n in self._file_nodes:
                    if n.item is not None:
                        n.item.setCheckState(
                            0, Qt.CheckState.Checked if n.index in keep
                            else Qt.CheckState.Unchecked)
            finally:
                self._updating = False
        self._sync_all_dirs()
        self.tree.expandAll()
        self.btn_all.setText("全选(%d)" % len(self._file_nodes))
        self._refresh_sum()

    # ---------- 勾选联动 ----------
    def _on_item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if self._updating or col != COL_NAME:
            return
        node = item.data(0, ROLE_NODE)
        if node is None:
            return
        old_block = self.tree.blockSignals(True)
        self._updating = True
        try:
            if node.is_dir:
                st = item.checkState(0)
                if st == Qt.CheckState.PartiallyChecked:
                    return
                for f in node.files:
                    if f.item is not None:
                        f.item.setCheckState(0, st)
            self._update_ancestors(item)
            self._refresh_sum()
        finally:
            self._updating = False
            self.tree.blockSignals(old_block)

    def _update_ancestors(self, item: QTreeWidgetItem) -> None:
        self._updating = True
        old_block = self.tree.blockSignals(True)
        try:
            p = item.parent()
            root = self.tree.invisibleRootItem()
            while p is not None and p is not root:
                states = [p.child(i).checkState(0) for i in range(p.childCount())]
                if states and all(s == Qt.CheckState.Checked for s in states):
                    p.setCheckState(0, Qt.CheckState.Checked)
                elif states and all(s == Qt.CheckState.Unchecked for s in states):
                    p.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    p.setCheckState(0, Qt.CheckState.PartiallyChecked)
                p = p.parent()
        finally:
            self.tree.blockSignals(old_block)
            self._updating = False

    def _sync_all_dirs(self) -> None:
        """从叶子往上重算所有目录的勾选态（批量操作之后用）。"""
        self._updating = True
        try:
            def walk(it: QTreeWidgetItem) -> None:
                node = it.data(0, ROLE_NODE)
                if node is None or not node.is_dir:
                    return
                for i in range(it.childCount()):
                    walk(it.child(i))
                states = [it.child(i).checkState(0) for i in range(it.childCount())]
                if states and all(s == Qt.CheckState.Checked for s in states):
                    it.setCheckState(0, Qt.CheckState.Checked)
                elif states and all(s == Qt.CheckState.Unchecked for s in states):
                    it.setCheckState(0, Qt.CheckState.Unchecked)
                else:
                    it.setCheckState(0, Qt.CheckState.PartiallyChecked)

            for i in range(self.tree.topLevelItemCount()):
                walk(self.tree.topLevelItem(i))
        finally:
            self._updating = False

    # ---------- 批量勾选 ----------
    def _set_files(self, pred) -> None:
        self._updating = True
        try:
            for n in self._file_nodes:
                if n.item is not None:
                    n.item.setCheckState(
                        0, Qt.CheckState.Checked if pred(n) else Qt.CheckState.Unchecked)
        finally:
            self._updating = False
        self._sync_all_dirs()
        self._refresh_sum()

    def _all(self) -> None:
        self._set_files(lambda _n: True)

    def _none(self) -> None:
        self._set_files(lambda _n: False)

    def _invert(self) -> None:
        self._set_files(lambda n: n.item is not None
                        and n.item.checkState(0) != Qt.CheckState.Checked)

    def _video_only(self) -> None:
        self._set_files(lambda n: intents.ext_of(n.name) in intents.VIDEO_EXT)

    def _toggle_sort(self) -> None:
        self._sort_by_size = not self._sort_by_size
        self._fill(preserve=True)

    # ---------- 汇总 ----------
    def _refresh_sum(self) -> None:
        total = sel = 0
        n = 0
        for f in self._file_nodes:
            total += f.size
            if f.item is not None and f.item.checkState(0) == Qt.CheckState.Checked:
                sel += f.size
                n += 1
        self.lb_sum.setText("已选 %d / %d 个　·　%s / %s"
                            % (n, len(self._file_nodes), fmt_size(sel), fmt_size(total)))

    def selected(self) -> list[int]:
        out = [f.index for f in self._file_nodes
               if f.item is not None and f.item.checkState(0) == Qt.CheckState.Checked]
        return sorted(out)

    def _accept(self) -> None:
        if not self.selected():
            QMessageBox.warning(self, "一个都没选",
                                "至少勾一个文件，不然这碗端上来是空的。")
            return
        self.accept()

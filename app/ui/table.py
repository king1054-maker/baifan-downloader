# -*- coding: utf-8 -*-
"""表格右端的空白区：不许露黑底。

列宽加起来比可视区窄的时候（用户把几列拖窄了、或者手拖「名称」变窄了），
右边会剩下一条区域。这块 Qt 用的是调色板底色 —— 系统是深色主题时就是
**一整块纯黑**，在这套浅色界面里像程序坏了。

所以自己画：从蓝到紫的淡渐变 + 几点星屑。渐变取主色系的两个邻居，
很浅、不抢内容；星屑只是让这块"死白"看起来有点生气。
"""
from __future__ import annotations

import random

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QRubberBand, QTableView

# 渐变三站：主色蓝 → 淡紫 → 偏粉的紫，都很浅，不抢内容
GAP_COLORS = ("#EEF2FF", "#F1ECFF", "#FAEFFF")
SPARK_SEED = 20261021
MIN_GAP = 6          # 窄于这个就不画了，省得看着像一条杂色缝


def make_sparks(w: int, h: int) -> list[tuple[float, float, float, int]]:
    """按尺寸生成星屑：(x 比例, y 比例, 半径, 透明度)。

    固定种子 —— 同一尺寸每次生成的位置一样，不然窗口一动星点就乱跳。
    """
    rnd = random.Random(SPARK_SEED)
    n = max(5, min(24, max(w, 1) // 26))
    return [(rnd.random(), rnd.random(), rnd.uniform(0.8, 2.1),
             rnd.randint(80, 200)) for _ in range(n)]


def paint_gap(p: QPainter, rect: QRectF,
              sparks: list[tuple[float, float, float, int]]) -> None:
    """把那条空白画成渐变 + 星屑。单独拿出来，方便测试直接画到 QImage 上验颜色。"""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    grad = QLinearGradient(rect.left(), 0, rect.right(), 0)
    grad.setColorAt(0.0, QColor(GAP_COLORS[0]))
    grad.setColorAt(0.5, QColor(GAP_COLORS[1]))
    grad.setColorAt(1.0, QColor(GAP_COLORS[2]))
    p.fillRect(rect, QBrush(grad))

    p.setPen(Qt.PenStyle.NoPen)
    for fx, fy, r, alpha in sparks:
        p.setBrush(QColor(255, 255, 255, alpha))
        cx, cy = rect.left() + fx * rect.width(), fy * rect.height()
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))


class GapTableView(QTableView):
    """把「列没铺满」的那条空白画成渐变星屑。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sparks: list[tuple[float, float, float, int]] = []
        self._spark_key: tuple[int, int] = (0, 0)
        self._band_origin: QPoint | None = None
        self._band_moved = False
        self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        # 这些列是按钮/编辑入口，拖拽从它们身上开始时不要抢点击
        self.drag_exclude_columns: set[int] = set()
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def _select_rows_in_rect(self, rect: QRect) -> None:
        model = self.model()
        if model is None or self.selectionModel() is None:
            return
        rows: list[int] = []
        for row in range(model.rowCount()):
            r = self.visualRect(model.index(row, 0))
            if r.isValid() and rect.intersects(
                    QRect(r.left(), r.top(), self.viewport().width(), r.height())):
                rows.append(row)
        self.selectionModel().clearSelection()
        if not rows:
            return
        top, bottom = min(rows), max(rows)
        sel = QItemSelection(
            model.index(top, 0),
            model.index(bottom, model.columnCount() - 1))
        self.selectionModel().select(
            sel, QItemSelectionModel.SelectionFlag.Rows
            | QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def mousePressEvent(self, event):  # noqa: N802
        self._band_origin = None
        self._band_moved = False
        if event.button() == Qt.MouseButton.LeftButton \
                and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            idx = self.indexAt(event.position().toPoint())
            if not idx.isValid() or idx.column() not in self.drag_exclude_columns:
                self._band_origin = event.position().toPoint()
                self._band.setGeometry(QRect(self._band_origin, self._band_origin))
                self._band.show()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._band_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            rect = QRect(self._band_origin, pos).normalized()
            if rect.width() > 3 or rect.height() > 3:
                self._band_moved = True
            self._band.setGeometry(rect)
            self._select_rows_in_rect(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        moved = self._band_moved
        if self._band_origin is not None:
            self._band.hide()
            self._band_origin = None
            self._band_moved = False
            if moved:
                # 拖拽结束后不要再把多选压回“单击一行”
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _ensure_sparks(self, w: int, h: int) -> None:
        if self._spark_key != (w, h):
            self._sparks = make_sparks(w, h)
            self._spark_key = (w, h)

    def gap_rect(self) -> QRectF | None:
        """当前有没有空白区；有就返回它的矩形（视口坐标）。"""
        vw = self.viewport().width()
        left = self.horizontalHeader().length()
        if vw - left < MIN_GAP:
            return None
        return QRectF(left, 0, vw - left, self.viewport().height())

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        rect = self.gap_rect()
        if rect is None:
            return
        self._ensure_sparks(int(rect.width()), int(rect.height()))
        p = QPainter(self.viewport())
        paint_gap(p, rect, self._sparks)
        p.end()

# -*- coding: utf-8 -*-
"""左栏筛选用的小图标。

两种来源，优先级从上到下：
  1. `assets/icons/filters/<key>.png` —— 用户给的那套大肥鱼表情图
     （每个状态举一个牌子：时钟=等待、下箭头=下载、叠碗=吃饱、暂停、感叹号=出错）
  2. 没图的时候退回代码画的线稿图标（这样删了图也不会变成空白）

图片统一缩到 176px，树里按 40px 显示 —— 立绘缩到 20px 就只剩一团蓝色，
所以行高也一起撑到 46px。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from .. import paths
from . import theme

BOX = 18          # 逻辑尺寸
DPR = 2           # 画两倍再缩，任何缩放下都清楚


def _canvas() -> tuple[QPixmap, QPainter]:
    pm = QPixmap(BOX * DPR, BOX * DPR)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(DPR)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pm, p


def _bowl(p: QPainter, color: QColor) -> None:
    """一只碗（上沿一条线 + 半圆碗身），「全部饭碗」用。"""
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(3.5, 7.2), QPointF(14.5, 7.2))
    path = QPainterPath()
    path.moveTo(3.8, 7.6)
    path.quadTo(4.4, 14.2, 9.0, 14.2)
    path.quadTo(13.6, 14.2, 14.2, 7.6)
    p.drawPath(path)


def _arrow_down(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(9, 3.6), QPointF(9, 13.4))
    p.drawLine(QPointF(5.4, 10), QPointF(9, 13.6))
    p.drawLine(QPointF(12.6, 10), QPointF(9, 13.6))


def _arrow_up(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(9, 14.4), QPointF(9, 4.6))
    p.drawLine(QPointF(5.4, 8.2), QPointF(9, 4.6))
    p.drawLine(QPointF(12.6, 8.2), QPointF(9, 4.6))


def _clock(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(3.2, 3.2, 11.6, 11.6))
    p.drawLine(QPointF(9, 6.2), QPointF(9, 9.4))
    p.drawLine(QPointF(9, 9.4), QPointF(11.4, 10.6))


def _check(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(3.2, 3.2, 11.6, 11.6))
    path = QPainterPath()
    path.moveTo(6.0, 9.2)
    path.lineTo(8.2, 11.4)
    path.lineTo(12.2, 6.9)
    p.drawPath(path)


def _pause(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(7.0, 4.6), QPointF(7.0, 13.4))
    p.drawLine(QPointF(11.6, 4.6), QPointF(11.6, 13.4))


def _warn(p: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(9.0, 3.2)
    path.lineTo(15.2, 14.4)
    path.lineTo(2.8, 14.4)
    path.closeSubpath()
    pen = QPen(color, 1.4)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    pen2 = QPen(color, 1.6)
    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen2)
    p.drawLine(QPointF(9, 7.4), QPointF(9, 10.4))
    p.drawPoint(QPointF(9, 12.3))


_DRAWERS = {
    "all": (_bowl, QColor(theme.TEXT_DIM)),
    "down": (_arrow_down, QColor(theme.ACCENT)),
    "wait": (_clock, QColor(theme.AMBER)),
    "done": (_check, QColor(theme.GREEN)),
    "seed": (_arrow_up, QColor("#14B8A6")),
    "pause": (_pause, QColor(theme.GREY)),
    "err": (_warn, QColor(theme.RED)),
}

_cache: dict[str, QIcon] = {}
IMG_DIR = paths.ASSET_DIR / "icons" / "filters"


def _from_image(key: str) -> QIcon | None:
    f = IMG_DIR / ("%s.png" % key)
    if not f.exists():
        return None
    icon = QIcon(str(f))
    return None if icon.isNull() else icon


def filter_icon(key: str) -> QIcon:
    """按筛选键取一个图标（用户给的表情图优先，没有就画一个）。"""
    hit = _cache.get(key)
    if hit is not None:
        return hit
    icon = _from_image(key)
    if icon is not None:
        _cache[key] = icon
        return icon
    drawer, color = _DRAWERS.get(key, _DRAWERS["all"])
    pm, p = _canvas()
    try:
        drawer(p, color)
    finally:
        p.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon

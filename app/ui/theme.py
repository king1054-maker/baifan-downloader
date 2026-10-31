# -*- coding: utf-8 -*-
"""浅色现代主题（Qt 样式表）。

主色取蓝 #4D6BFE —— 和蓝发女仆大肥鱼的形象统一。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle

ACCENT = "#4D6BFE"
ACCENT_DARK = "#3B5AF5"
ACCENT_SOFT = "#EDF1FF"

BG = "#FFFFFF"
BG_ALT = "#F7F8FA"
BORDER = "#E4E7EC"
TEXT = "#1F2328"
TEXT_DIM = "#6B7280"

GREEN = "#16A34A"
AMBER = "#F59E0B"
RED = "#EF4444"
GREY = "#9CA3AF"

FONT_FAMILY = '"Microsoft YaHei UI", "Segoe UI", sans-serif'

QSS = f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 9pt;
    color: {TEXT};
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* ---------- 菜单栏 ---------- */
QMenuBar {{
    background: {BG};
    border-bottom: 1px solid {BORDER};
    padding: 2px 6px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px; 
}}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

/* ---------- 工具栏 ---------- */
QToolBar {{
    background: {BG}; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; spacing: 4px;
}}
QToolBar::separator {{ width: 1px; background: {BORDER}; margin: 4px 8px; }}
QToolButton {{
    background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 5px 9px; color: {TEXT};
}}
QToolButton:hover {{ background: {ACCENT_SOFT}; border-color: #DCE3FF; color: {ACCENT}; }}
QToolButton:pressed {{ background: #DCE3FF; }}
QToolButton:disabled {{ color: {GREY}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---------- 左侧树 ---------- */
QTreeWidget {{
    background: {BG_ALT}; border: none; border-right: 1px solid {BORDER};
    outline: 0; padding: 8px 6px;
}}
QTreeWidget::item {{
    height: 30px; border-radius: 6px; padding-left: 6px;
}}
QTreeWidget::item:hover {{ background: #EEF1F6; }}
QTreeWidget::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}

/* ---------- 表格 ---------- */
QTableView {{
    background: {BG}; border: none; gridline-color: transparent;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    alternate-background-color: #FBFCFD;
    outline: 0;
}}
QTableView::item {{ padding: 0 8px; border-bottom: 1px solid #F1F2F5; }}
QTableView::item:selected {{ background: {ACCENT_SOFT}; }}
/* 表头这一条本身也要给底色：分栏没铺满时，右边那段会用调色板的 Window 色，
   深色系统主题下就是一块纯黑（用户截图里那块）。 */
QHeaderView {{ background: {BG_ALT}; border: none; }}
QHeaderView::section {{
    background: {BG_ALT}; color: {TEXT_DIM};
    padding: 7px 8px; border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}
QHeaderView::section:hover {{ background: #EFF1F5; }}
QTableCornerButton::section {{ background: {BG_ALT}; border: none; }}

/* ---------- 详情 Tab ---------- */
QTabWidget::pane {{
    border: none; border-top: 1px solid {BORDER}; background: {BG};
}}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 7px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {ACCENT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}

/* ---------- 状态栏 ---------- */
QStatusBar {{
    background: {BG_ALT}; border-top: 1px solid {BORDER};
    color: {TEXT_DIM}; padding: 2px 8px;
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; padding: 0 8px; }}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: #EDEFF3; border: none; border-radius: 3px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

/* ---------- 滑块 ---------- */
/* 不写这几条，Qt 默认滑轨是系统深色，在浅色面板里像一根黑棍子 */
QSlider::groove:horizontal {{
    height: 4px; background: {BORDER}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT}; border-radius: 2px;
}}
QSlider::add-page:horizontal {{
    background: {BORDER}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 13px; margin: -6px 0; border-radius: 7px;
    background: #FFFFFF; border: 1px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_SOFT};
}}
QSlider::handle:horizontal:pressed {{
    background: {ACCENT}; border-color: {ACCENT_DARK};
}}

/* ---------- 滚动区 ---------- */
/* 不写这几条，viewport 会用系统调色板的底色 —— 深色主题下就是一块黑，
   内容之间还会露出黑缝（选项→桌宠那页踩过）。*/
QScrollArea {{ background: {BG}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {ACCENT_SOFT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT};
    outline: 0; padding: 4px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 16px; color: {TEXT};
}}
QPushButton:hover {{ border-color: #C9D0DC; background: {BG_ALT}; }}
QPushButton:pressed {{ background: #EEF1F6; }}
QPushButton:disabled {{ color: {GREY}; background: {BG_ALT}; }}
QPushButton[accent="true"] {{
    background: {ACCENT}; border: 1px solid {ACCENT}; color: #FFFFFF; font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; }}
/* 可勾选的胶囊按钮（新建任务的「常用目录」）：选中就整颗变蓝 */
QPushButton:checked {{
    background: {ACCENT_SOFT}; border: 1px solid {ACCENT};
    color: {ACCENT_DARK}; font-weight: 600;
}}
QPushButton:checked:hover {{ background: {ACCENT_SOFT}; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CDD2DA; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #B4BAC5; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #CDD2DA; border-radius: 5px; min-width: 30px; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QToolTip {{
    background: #2C3138; color: #FFFFFF; border: none;
    border-radius: 4px; padding: 4px 8px;
}}
"""


# ============================ 自绘「勾选框」 ============================
# 为什么不用 QSS 的 image: QSS 只能贴一张固定像素的图，125%/150% 缩放下一糊；
# 而完全不管又会被系统主题接管 —— Win11 上是深蓝底 + 深色对号，糊成一片。
# 所以用 QProxyStyle 自己画：圆角方框 + 白色对号，任何缩放下都是锐的。
class CheckStyle(QProxyStyle):
    # 基础样式按**名字**传：QProxyStyle("Fusion") 自己造一份，
    # 如果传 app.style() 那个对象，setStyle 换掉之后它就被销毁了，proxy 拿着野指针（直接崩）。
    def __init__(self):
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        e = QStyle.PrimitiveElement
        if element == e.PE_IndicatorCheckBox:
            self._draw(option, painter, radio=False)
            return
        if element == e.PE_IndicatorRadioButton:
            self._draw(option, painter, radio=True)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw(option, painter, radio: bool) -> None:  # noqa: C901
        st = option.state
        S = QStyle.StateFlag
        on = bool(st & S.State_On)
        partial = bool(st & S.State_NoChange)
        enabled = bool(st & S.State_Enabled)
        hover = bool(st & S.State_MouseOver)

        r = option.rect
        side = min(r.width(), r.height()) or 15
        box = QRectF(0, 0, side - 1.6, side - 1.6)
        box.moveCenter(QPointF(r.center()))

        accent = QColor(ACCENT if enabled else "#C7CDD8")
        border = QColor(BORDER if enabled else "#E5E8EE")
        if hover and enabled:
            border = QColor(ACCENT)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if radio:
            painter.setPen(Qt.PenStyle.NoPen if on else QPen(border, 1.4))
            painter.setBrush(accent if on else QColor("#FFFFFF" if enabled else "#F5F6F8"))
            painter.drawEllipse(box)
            if on:
                inner = QRectF(0, 0, box.width() * 0.42, box.height() * 0.42)
                inner.moveCenter(box.center())
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawEllipse(inner)
        else:
            if on or partial:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent)
                painter.drawRoundedRect(box, 4, 4)
            else:
                painter.setPen(QPen(border, 1.4))
                painter.setBrush(QColor("#FFFFFF" if enabled else "#F5F6F8"))
                painter.drawRoundedRect(box, 4, 4)
            if on or partial:
                pen = QPen(QColor("#FFFFFF"), max(1.7, box.width() * 0.15))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                path = QPainterPath()
                x, y, w, h = box.left(), box.top(), box.width(), box.height()
                if partial:
                    path.moveTo(x + w * 0.26, y + h * 0.5)
                    path.lineTo(x + w * 0.74, y + h * 0.5)
                else:
                    path.moveTo(x + w * 0.22, y + h * 0.52)
                    path.lineTo(x + w * 0.42, y + h * 0.72)
                    path.lineTo(x + w * 0.78, y + h * 0.28)
                painter.drawPath(path)
        painter.restore()


_STYLE = None      # 必须留引用：setStyle 不接管 Python 对象的生命周期，
                   # 一被回收，app.style() 就退回到基类（QCommonStyle）


def apply_style(app) -> None:
    """装到 QApplication 上：拦住复选框/单选钮，别的照旧交给 Fusion。"""
    global _STYLE
    _STYLE = CheckStyle()
    app.setStyle(_STYLE)

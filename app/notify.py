# -*- coding: utf-8 -*-
"""完成通知：声音 / 通知栏 / 气泡，三路可任意组合。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from . import paths

SOUND_DIR = paths.ASSET_DIR / "sounds"

# 音量（0–100）。100 是"文件本身有多大就多大声"。
# 默认 60：ciallo 那声本身已经压到峰值 55%，再乘 0.6 刚好不吵。
DEFAULT_VOLUME = 60

# 默认提示音：ciallo 有文件就用它，没有才退回叮咚。
# 用户明确要的是 ciallo 那一声，不是原来那个"叮咚"，太吵。
DEFAULT_SOUND = "ciallo.wav"

# 候选音效。只列**文件真的在**的那些 —— 界面上摆一个点了没声音的选项，
# 比没有这个选项更让人困惑。
_SOUND_CANDIDATES = [
    ("Ciallo～（默认）", "ciallo.wav"),
    ("Ciallo～（另一个版本）", "ciallo_alt2.wav"),
    ("叮咚", "ding.wav"),
    ("咬一口", "bite.wav"),
    ("打个饱嗝", "burp.wav"),
]
BUILTIN_SOUNDS = [(label, fn) for label, fn in _SOUND_CANDIDATES
                  if (SOUND_DIR / fn).exists()]


def _default_path() -> Path:
    p = SOUND_DIR / DEFAULT_SOUND
    return p if p.exists() else SOUND_DIR / "ding.wav"


def sound_path(value: str = "") -> Path:
    """value 为空 → 内置默认；为文件名 → 内置；为绝对路径 → 自定义。"""
    if value:
        p = Path(value)
        if p.exists():
            return p
        builtin = SOUND_DIR / value
        if builtin.exists():
            return builtin
    return _default_path()


def sound_label(value: str = "") -> str:
    p = sound_path(value)
    for label, fn in BUILTIN_SOUNDS:
        if fn == p.name:
            return label
    return p.stem


_qt_player = None   # 必须留引用，否则 QMediaPlayer 会被回收
_qt_effect = None   # wav 走 QSoundEffect：应用级音量，不跟系统音量混在一起

# ---------- 播放（带音量） ----------
# winsound 没有音量参数；MCI 的 volume 在这台机器上实测不生效。
# 所以 wav 优先 QSoundEffect，非 wav 用 QMediaPlayer，两者都能独立调音量。
MCI_ALIAS = "baifan_prompt"
_mci_ok = True


def _play_wav_qt(path: Path, volume: int) -> bool:
    global _qt_effect
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect
    if _qt_effect is None:
        _qt_effect = QSoundEffect()
    _qt_effect.setSource(QUrl.fromLocalFile(str(path)))
    _qt_effect.setVolume(max(0, min(100, int(volume))) / 100.0)
    _qt_effect.play()
    return True


def _mci(cmd: str) -> int:
    import ctypes
    return int(ctypes.windll.winmm.mciSendStringW(cmd, None, 0, None))


def _play_mci(path: Path, volume: int) -> bool:
    global _mci_ok
    if not _mci_ok:
        return False
    try:
        _mci("close %s" % MCI_ALIAS)          # 上一次还没关门就先关掉
        if _mci('open "%s" type waveaudio alias %s' % (path, MCI_ALIAS)) != 0:
            return False
        _mci("setaudio %s volume to %d" % (MCI_ALIAS,
                                           max(0, min(100, int(volume))) * 10))
        if _mci("play %s" % MCI_ALIAS) != 0:
            return False
        return True
    except Exception:  # noqa: BLE001
        _mci_ok = False                        # 这台机器就别再试了
        return False


def play_sound(value: str = "", volume: int | None = None) -> None:
    """播提示音。volume 0–100，None 用 DEFAULT_VOLUME，0 等于静音。"""
    global _qt_player
    vol = DEFAULT_VOLUME if volume is None else int(volume)
    if vol <= 0:
        return
    p = sound_path(value)
    if not p.exists():
        return
    if p.suffix.lower() == ".wav":
        try:
            _play_wav_qt(p, vol)
            return
        except Exception:  # noqa: BLE001
            pass
    if p.suffix.lower() == ".wav" and _play_mci(p, vol):
        return
    if p.suffix.lower() == ".wav":
        try:
            import winsound
            winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        if _qt_player is None:
            _qt_player = QMediaPlayer()
            _qt_player.setAudioOutput(QAudioOutput())
        out = _qt_player.audioOutput()
        if out is not None:
            out.setVolume(vol / 100.0)
        _qt_player.setSource(p.as_uri())
        _qt_player.play()
    except Exception:  # noqa: BLE001
        pass


class CompleteBubble(QWidget):
    """右下角的气泡卡片：文件名 + 三个按钮。"""

    open_dir = Signal()
    open_file = Signal()
    dismissed = Signal()

    def __init__(self, title: str, name: str, detail: str = "",
                 can_open: bool = True):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(392)

        card = QWidget(self)
        card.setObjectName("card")
        card.setStyleSheet("""
            QWidget#card{background:#FFFFFF;border:1px solid #E4E7EC;border-radius:12px;}
            QLabel#t{color:#1F2328;font-weight:600;}
            QLabel#n{color:#4B5563;}
            QLabel#d{color:#9AA3AF;font-size:8.5pt;}
            QPushButton{border:none;background:#F3F5F9;color:#374151;border-radius:6px;
                        padding:5px 10px;}
            QPushButton:hover{background:#E8EDFB;color:#3B5AF5;}
            QPushButton#acc{background:#4D6BFE;color:#FFFFFF;}
            QPushButton#acc:hover{background:#3B5AF5;}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(30, 50, 110, 60))
        card.setGraphicsEffect(shadow)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        art = QLabel()
        pm = QPixmap(str(paths.ASSET_DIR / "pet" / "face.png"))
        if not pm.isNull():
            art.setPixmap(pm.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation))
        art.setFixedWidth(72)
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(art)

        col = QVBoxLayout()
        col.setSpacing(2)
        lb_t = QLabel(title)
        lb_t.setObjectName("t")
        lb_n = QLabel(name)
        lb_n.setObjectName("n")
        lb_n.setWordWrap(False)
        lb_n.setText(self._elide(name, 34))
        lb_d = QLabel(detail)
        lb_d.setObjectName("d")
        col.addWidget(lb_t)
        col.addWidget(lb_n)
        col.addWidget(lb_d)
        col.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(6)
        b_dir = QPushButton("打开文件夹")
        b_dir.clicked.connect(self._do_dir)
        row.addWidget(b_dir)
        if can_open:
            b_open = QPushButton("直接打开")
            b_open.setObjectName("acc")
            b_open.clicked.connect(self._do_file)
            row.addWidget(b_open)
        row.addStretch(1)
        b_no = QPushButton("知道了")
        b_no.clicked.connect(self._dismiss)
        row.addWidget(b_no)
        col.addLayout(row)
        lay.addLayout(col, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(14000)
        self._timer.timeout.connect(self._dismiss)
        self._timer.start()

    @staticmethod
    def _elide(text: str, n: int) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"

    def _do_dir(self) -> None:
        self.open_dir.emit()
        self._dismiss()

    def _do_file(self) -> None:
        self.open_file.emit()
        self._dismiss()

    def _dismiss(self) -> None:
        self.dismissed.emit()
        self.close()

    def enterEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._timer.start()
        super().leaveEvent(event)


class Notifier:
    """按设置把完成事件分发到三个通道。"""

    def __init__(self, settings: dict, parent=None):
        self.settings = settings
        self.parent = parent
        self.tray = None
        self._bubble: CompleteBubble | None = None

    def set_tray(self, tray) -> None:
        self.tray = tray

    @property
    def volume(self) -> int:
        try:
            return max(0, min(100, int(self.settings.get("notify_volume", DEFAULT_VOLUME))))
        except (TypeError, ValueError):
            return DEFAULT_VOLUME

    @property
    def sound_on(self) -> bool:
        return bool(self.settings.get("notify_sound", True))

    @property
    def tray_on(self) -> bool:
        return bool(self.settings.get("notify_tray", True))

    @property
    def bubble_on(self) -> bool:
        return bool(self.settings.get("notify_bubble", True))

    def notify(self, title: str, name: str, detail: str = "",
               can_open: bool = True, on_dir=None, on_file=None) -> None:
        if self.sound_on:
            play_sound(self.settings.get("notify_sound_file") or "",
                       self.volume)
        if self.tray_on and self.tray is not None:
            try:
                self.tray.showMessage(title, "%s\n%s" % (name, detail) if detail else name,
                                      QIcon(), 7000)
            except Exception:  # noqa: BLE001
                pass
        if self.bubble_on:
            if self._bubble is not None:
                self._bubble.close()
            b = CompleteBubble(title, name, detail, can_open)
            if on_dir:
                b.open_dir.connect(on_dir)
            if on_file:
                b.open_file.connect(on_file)
            b.dismissed.connect(lambda: setattr(self, "_bubble", None))
            self._bubble = b
            b.adjustSize()
            b.show()
            self._place(b)

    def _place(self, b: CompleteBubble) -> None:
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        b.adjustSize()
        x = g.right() - b.width() - 18
        y = g.bottom() - b.height() - 18
        # 桌宠在右下角时往上让一点，别盖住它
        pet = getattr(self.parent, "pet", None) if self.parent is not None else None
        if pet is not None and pet.isVisible():
            pr = pet.geometry()
            if abs(pr.right() - g.right()) < 260 and pr.bottom() > g.bottom() - 320:
                y = pr.top() - b.height() - 10
        b.move(QPoint(x, max(g.top() + 8, y)))

    def preview(self) -> None:
        play_sound(self.settings.get("notify_sound_file") or "", self.volume)

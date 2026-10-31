# -*- coding: utf-8 -*-
"""桌宠形象：让用户自己换那几张图。

契约：一个状态对应一个文件名，就这六个（顺序就是一顿饭的顺序）——

    ciallo      待命：没任务（或刚下完那 12 秒过去了）
    idle_bowl   等饭：任务在排队 / 磁力还在取种子
    half        下载中：整个下载过程就这一张
    tease       刚下完那 12 秒：指着你「没吃饱喵」
    idle_lying  吃撑了：待命时，今天已经下完 3 个以上
    sassy       文件名踩中调侃词（停 8 秒就收回）

（原来的 eat「馋嘴」那张按用户要求删了：他说那只鱼一脸谄媚，不要了。）

内置图在 assets/pet/（只读，装完在 _internal 里，升级会被覆盖）。
用户换的图存 data/pet/（可写、绿色版能整个文件夹带走），
查图时先看 data/pet/，没有再回退内置——这就是"单张覆盖"的全部机制。

用户给的图什么尺寸、什么格式都可能，甚至可能是白底 JPEG，
所以 import_image() 统一规范化：抠白底 → 裁到内容 → 摆进 512 方画布。
"""
from __future__ import annotations

import shutil
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap

from . import paths

OUT = 512            # 规范后输出的边长
MAX_SRC = 1024       # 先缩到这个尺寸以内再逐像素处理，纯 Python 才跑得动
WHITE = 238          # 近白判定阈值（每通道）
CHROMA = 14          # 三通道差值上限，防止浅灰蓝被误当白
ALPHA_KEEP = 8       # alpha 低于这个值算透明
MIN_KEEP = 0.03      # 抠完只剩不到 3% 不透明 → 判定抠错了，退回不抠

# (键, 显示名, 什么时候用) —— 顺序 = 一顿饭的顺序，界面照这个排
STATES: tuple[tuple[str, str, str], ...] = (
    ("ciallo",     "待命",    "没有任务的时候"),
    ("idle_bowl",  "等饭",    "任务在排队 / 磁力还在取种子时"),
    ("half",       "下载中",  "有任务正在下的时候（整个下载过程都是这张）"),
    ("tease",      "没吃饱",  "任务下完后的 12 秒"),
    ("idle_lying", "吃撑了",  "待命时，今天已经下完 3 个以上：躺沙发上"),
    ("sassy",      "吐槽",    "文件名踩中那些词时，停 8 秒就收回半张脸"),
)

KEYS = tuple(s[0] for s in STATES)
LABELS = {k: name for k, name, _ in STATES}
NOTES = {k: note for k, _, note in STATES}

SKIN_DIR = paths.DATA_DIR / "pet"

# 随包分发的素材库：现成的七张图，用户可以直接从这里挑。
# 源在 assets/pet/library（只读、升级会覆盖），首次启动铺一份到 data/pet/图库。
LIB_SRC = paths.ASSET_DIR / "pet" / "library"


def library_dir() -> Path:
    return SKIN_DIR / "图库"


def ensure_library() -> int:
    """把随包素材库铺到 data/pet/图库：缺什么补什么，不覆盖用户改过的。

    返回这次补了几张（0 表示之前已经铺全了）。
    """
    if not LIB_SRC.is_dir():
        return 0
    dst_dir = library_dir()
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    n = 0
    for src in sorted(LIB_SRC.glob("*.png")):
        dst = dst_dir / src.name
        if dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            n += 1
        except OSError:
            pass
    return n


def user_path(name: str) -> Path:
    return SKIN_DIR / ("%s.png" % name)


def builtin_path(name: str) -> Path:
    return paths.ASSET_DIR / "pet" / ("%s.png" % name)


def sprite_path(name: str) -> Path:
    """实际要画的那张：用户换过就用用户的，否则用内置的。"""
    p = user_path(name)
    if p.exists():
        return p
    return builtin_path(name)


def has_custom(name: str) -> bool:
    return user_path(name).exists()


def custom_count() -> int:
    return sum(1 for k in KEYS if has_custom(k))


def reset(name: str) -> bool:
    """还原成内置图。返回是否真的删掉了东西。"""
    p = user_path(name)
    try:
        if p.exists():
            p.unlink()
            return True
    except OSError:
        pass
    return False


def reset_all() -> int:
    return sum(1 for k in KEYS if reset(k))


def thumb(name: str, box: int = 64) -> QPixmap:
    """界面里那一小块预览，用的就是当前生效的那张。"""
    pm = QPixmap(str(sprite_path(name)))
    if pm.isNull():
        return pm
    return pm.scaled(box, box, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)


# ---------------------------------------------------------------- 图片处理

def _near_white(r: int, g: int, b: int) -> bool:
    return (r >= WHITE and g >= WHITE and b >= WHITE
            and max(r, g, b) - min(r, g, b) <= CHROMA)


def _cut_white_bg(buf: bytearray, w: int, h: int, bpl: int) -> int:
    """从四条边开始洪水填充，把连通的近白像素抠成透明。

    只抠"跟边缘连着的"白，这样图里纯白的衣服/眼球不会被吃出洞。
    返回抠掉的像素数。
    """
    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def take(x: int, y: int) -> None:
        i = y * w + x
        if seen[i]:
            return
        o = y * bpl + x * 4
        if buf[o + 3] == 0:            # 本来就透明，当作可通行
            seen[i] = 1
            q.append((x, y))
            return
        if _near_white(buf[o], buf[o + 1], buf[o + 2]):
            seen[i] = 1
            buf[o + 3] = 0
            q.append((x, y))

    for x in range(w):
        take(x, 0)
        take(x, h - 1)
    for y in range(h):
        take(0, y)
        take(w - 1, y)

    while q:
        x, y = q.popleft()
        if x > 0:
            take(x - 1, y)
        if x + 1 < w:
            take(x + 1, y)
        if y > 0:
            take(x, y - 1)
        if y + 1 < h:
            take(x, y + 1)

    return sum(1 for i in range(0, len(buf), 4) if buf[i + 3] == 0)


def _bbox(buf: bytearray, w: int, h: int, bpl: int) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * bpl
        for x in range(w):
            if buf[row + x * 4 + 3] >= ALPHA_KEEP:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    if x1 < 0:
        return None
    return x0, y0, x1 + 1, y1 + 1


def _opaque_ratio(buf: bytearray) -> float:
    n = len(buf) // 4
    if not n:
        return 0.0
    return sum(1 for i in range(3, len(buf), 4) if buf[i] >= ALPHA_KEEP) / n


def normalize(img: QImage) -> QImage:
    """任意图 → 512×512 透明底的 PNG 画布。"""
    if img.width() > MAX_SRC or img.height() > MAX_SRC:
        img = img.scaled(MAX_SRC, MAX_SRC, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    # PySide6 QImage.bits() already exposes a sized memoryview.
    # Unlike PyQt6/sip.voidptr it does not support ptr.setsize().
    buf = bytearray(img.bits()[:img.sizeInBytes()])

    # 要不要抠白底：没 alpha 通道就抠；有 alpha 但四个角都是不透明的白，也抠
    need = not img.hasAlphaChannel()
    if not need:
        corners = []
        for cx, cy in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)):
            o = cy * bpl + cx * 4
            corners.append((buf[o + 3], buf[o], buf[o + 1], buf[o + 2]))
        need = all(a == 255 and _near_white(r, g, b) for a, r, g, b in corners)

    if need:
        _cut_white_bg(buf, w, h, bpl)
        if _opaque_ratio(buf) < MIN_KEEP:
            # 抠完什么都不剩，说明这张图本身就是一整片浅色。
            # 与其给用户一张白板，不如直接说清楚。
            raise ValueError("这张图几乎全是白的，抠完什么都不剩——换张有主体的")

    fixed = QImage(bytes(buf), w, h, bpl, QImage.Format.Format_RGBA8888).copy()
    box = _bbox(buf, w, h, bpl)
    if box is None:
        raise ValueError("这张图看起来是空的（全透明或全白）")
    x0, y0, x1, y1 = box
    crop = fixed.copy(x0, y0, x1 - x0, y1 - y0)

    canvas = QImage(OUT, OUT, QImage.Format.Format_RGBA8888)
    canvas.fill(0)
    inner = int(OUT * 0.96)
    scaled = crop.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.drawImage((OUT - scaled.width()) // 2, (OUT - scaled.height()) // 2, scaled)
    p.end()
    return canvas


def import_image(src: str | Path, name: str) -> tuple[bool, str]:
    """把用户选的图装成某个状态的立绘。返回 (成功, 给用户看的话)。"""
    if name not in KEYS:
        return False, "没有这个状态：%s" % name
    src = Path(src)
    if not src.exists():
        return False, "文件不存在"
    if src.stat().st_size > 24 * 1024 * 1024:
        return False, "图太大了（超过 24MB），换张小点的"

    # 先只读尺寸、不整张解码：20~30MB 的图如果像素巨大（比如 10000×10000），
    # 直接 QImage 会先吃几百 MB 内存，老机器/32 位环境容易崩。
    reader = QImageReader(str(src))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        if (size.width() > 8000 or size.height() > 8000
                or size.width() * size.height() > 40_000_000):
            return False, ("图片像素太大（%d×%d），先缩到 4096 左右再换，"
                           "不然光解码就会占很多内存" % (size.width(), size.height()))
        if size.width() > MAX_SRC or size.height() > MAX_SRC:
            # 直接按 1024 解码，别先整张大图进内存
            reader.setScaledSize(size.scaled(MAX_SRC, MAX_SRC,
                                             Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        img = QImage(str(src))
    if img.isNull():
        return False, "这个格式读不进来，换成 PNG / JPG / WebP 试试"
    try:
        out = normalize(img)
    except ValueError as e:
        return False, str(e)

    SKIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = user_path(name)
    tmp = dst.with_suffix(".tmp.png")
    if not out.save(str(tmp), "PNG"):
        return False, "写文件失败，检查一下程序目录是不是只读"
    tmp.replace(dst)
    return True, "已换成「%s」：%s" % (LABELS[name], src.name)


def clear_cache() -> None:
    """兼容用：data/pet 整个删掉（测试/排错时用）。"""
    if SKIN_DIR.exists():
        shutil.rmtree(SKIN_DIR, ignore_errors=True)

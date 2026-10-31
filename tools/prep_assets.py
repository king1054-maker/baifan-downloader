# -*- coding: utf-8 -*-
"""从原画里切出桌宠与图标素材。

原图是白底 RGB，没有 alpha。这里用「从边框泛洪」的方式抠图：
只删掉与图片边缘连通的近白像素，角色内部的白围裙/头饰不会被误删。
抠完再把残留的台词小块（"打滚~"之类）按连通块删掉。
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "opus"
OUT_PET = ROOT / "assets" / "pet"
OUT_ICON = ROOT / "assets" / "icons"


def flood_bg(rgb: np.ndarray) -> np.ndarray:
    """返回背景掩码：与四条边连通的、接近白/浅灰的像素。"""
    h, w, _ = rgb.shape
    mx = rgb.max(axis=2).astype(np.int16)
    mn = rgb.min(axis=2).astype(np.int16)
    near_white = ((mx >= 236) & ((mx - mn) <= 18)).astype(np.uint8)

    mask = np.zeros((h, w), dtype=np.uint8)
    q: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if near_white[y, x] and not mask[y, x]:
                mask[y, x] = 1
                q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if near_white[y, x] and not mask[y, x]:
                mask[y, x] = 1
                q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and near_white[ny, nx] and not mask[ny, nx]:
                mask[ny, nx] = 1
                q.append((ny, nx))
    return mask


def _components(alpha: np.ndarray, thr: int = 24) -> list[list[tuple[int, int]]]:
    h, w = alpha.shape
    seen = np.zeros((h, w), bool)
    out: list[list[tuple[int, int]]] = []
    for y in range(h):
        for x in range(w):
            if alpha[y, x] > thr and not seen[y, x]:
                q = deque([(y, x)])
                seen[y, x] = True
                pts: list[tuple[int, int]] = []
                while q:
                    cy, cx = q.popleft()
                    pts.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and alpha[ny, nx] > thr and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                out.append(pts)
    return out


def erase_specks(im: Image.Image, zones: list[tuple[float, float, float, float]],
                 max_px: int = 8000) -> Image.Image:
    """干掉落在 zones（相对坐标）里、且面积很小的连通块 —— 也就是残留台词。"""
    a = np.array(im.getchannel("A"))
    h, w = a.shape
    for pts in _components(a):
        if len(pts) > max_px:
            continue
        ys = [p[0] for p in pts]
        xs = [p[1] for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        cx, cy = (x0 + x1) / 2 / w, (y0 + y1) / 2 / h
        if any(zx0 <= cx <= zx1 and zy0 <= cy <= zy1 for zx0, zy0, zx1, zy1 in zones):
            for y, x in pts:
                a[y, x] = 0
    out = im.copy()
    out.putalpha(Image.fromarray(a))
    return out


def erase_rect(im: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    """硬擦一小块残留（坐标是相对值）。只用在确认是背景的位置。"""
    a = np.array(im.getchannel("A"))
    h, w = a.shape
    x0, y0, x1, y1 = (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
    a[max(0, y0):y1, max(0, x0):x1] = 0
    out = im.copy()
    out.putalpha(Image.fromarray(a))
    return out


def cutout(path: Path, box: tuple[float, float, float, float] | None = None,
           feather: float = 1.1) -> Image.Image:
    im = Image.open(path).convert("RGB")
    if box:
        w, h = im.size
        im = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    rgb = np.array(im)
    bg = flood_bg(rgb)
    alpha = Image.fromarray(((1 - bg) * 255).astype(np.uint8), "L")
    alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    alpha = alpha.point(lambda v: 0 if v < 40 else (255 if v > 215 else int(v * 1.35)))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    bbox = out.getbbox()
    if bbox:
        out = out.crop(bbox)
    return out


def fit_square(im: Image.Image, size: int, pad: float = 0.03) -> Image.Image:
    inner = int(size * (1 - pad * 2))
    scale = inner / max(im.width, im.height)
    im2 = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                    Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(im2, ((size - im2.width) // 2, (size - im2.height) // 2))
    return canvas


def rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def make_icon(head: Image.Image, size: int = 512) -> Image.Image:
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, size))
    d = ImageDraw.Draw(grad)
    top, bot = (20, 40, 104), (64, 108, 246)
    for y in range(size):
        t = y / max(1, size - 1)
        d.point((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    base.paste(grad.resize((size, size)), (0, 0))

    gloss = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse(
        [-size * 0.35, -size * 0.72, size * 1.05, size * 0.42], fill=(255, 255, 255, 28))
    base.alpha_composite(gloss)

    tw = int(size * 0.94)
    th = int(tw * head.height / head.width)
    if th > size * 0.94:
        th = int(size * 0.94)
        tw = int(th * head.width / head.height)
    ch = head.resize((tw, th), Image.LANCZOS)
    base.alpha_composite(ch, ((size - tw) // 2, size - th - int(size * 0.02)))

    base.putalpha(rounded_mask(size, int(size * 0.22)))
    return base


def main() -> int:
    OUT_PET.mkdir(parents=True, exist_ok=True)
    OUT_ICON.mkdir(parents=True, exist_ok=True)
    raw_dir = ROOT / ".cache"
    raw_dir.mkdir(exist_ok=True)

    # 1) 趴着舔碗 —— 没活干、等饭
    idle = cutout(SRC / "img09.png", box=(0.02, 0.40, 0.99, 1.0))
    idle = erase_specks(idle, [(0.00, 0.08, 0.17, 0.31), (0.55, 0.00, 0.70, 0.22),
                              (0.85, 0.00, 0.95, 0.08)])
    idle.save(raw_dir / "idle-raw.png")
    fit_square(idle, 640).save(OUT_PET / "idle.png")

    # 2) 开心蹦跳 —— 正在干饭
    eat = cutout(SRC / "img33.png", box=(0.0, 0.0, 0.50, 1.0))
    eat = erase_specks(eat, [])
    eat.save(raw_dir / "eat-raw.png")
    fit_square(eat, 640).save(OUT_PET / "eat.png")

    # 3) 靠着饭碗坐 —— 吃饱了
    full = cutout(SRC / "img27.png")
    full.save(raw_dir / "full-raw.png")
    fit_square(full, 640).save(OUT_PET / "full.png")

    # 4) 图标：取第 3 张的头像
    head = cutout(SRC / "img27.png", box=(0.24, 0.04, 0.70, 0.48))
    head.save(raw_dir / "head-raw.png")
    icon512 = make_icon(head, 512)
    icon512.save(OUT_ICON / "whale-512.png")
    for s in (256, 128, 64, 48, 32, 24, 16):
        icon512.resize((s, s), Image.LANCZOS).save(OUT_ICON / ("whale-%d.png" % s))
    icon512.resize((256, 256), Image.LANCZOS).save(
        OUT_ICON / "whale.ico",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    print("ok idle", idle.size, "eat", eat.size, "full", full.size, "head", head.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())

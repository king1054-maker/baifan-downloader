# -*- coding: utf-8 -*-
"""生成程序图标：圆角深蓝底 + 大头特写。

以前是手画的白色胖鲸剪影，用户说太抽象；现在直接用立绘里的脸。
输出 assets/icons/whale-<size>.png 与 whale.ico（16/24/32/48/64/128/256），
文件名保持不变，免得改一堆引用。

跑法：python tools/makeicon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icons"
FACE = ROOT / "assets" / "pet" / "face.png"

SIZES = [16, 24, 32, 48, 64, 128, 256]
ICO_SIZES = [(s, s) for s in SIZES]

TOP = (20, 34, 58)        # 底色渐变：上深下浅，跟主界面的深蓝是一套
BOTTOM = (32, 58, 110)
INSET = 0.80              # 脸占画布的比例：留出圆角，别把耳朵和发饰啃掉


def _face_square() -> Image.Image:
    im = Image.open(FACE).convert("RGBA")
    w, h = im.size
    side = min(w, h)
    cx, cy = w // 2, h // 2             # 取正中，别把发饰和耳朵切掉
    left = max(0, min(w - side, cx - side // 2))
    top = max(0, min(h - side, cy - side // 2))
    return im.crop((left, top, left + side, top + side))


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        k = y / max(1, size - 1)
        d.line([(0, y), (size, y)],
               fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * k) for i in range(3)) + (255,))
    return img


def render(size: int, face: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=max(2, int(size * 0.20)), fill=255)
    canvas.paste(_gradient(size), (0, 0), mask)

    inner = max(8, int(size * INSET))
    f = face.resize((inner, inner), Image.LANCZOS)
    canvas.alpha_composite(f, ((size - inner) // 2, (size - inner) // 2))
    # 压回圆角，避免脸的四角盖出圆角之外
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    return out


def main() -> int:
    if not FACE.exists():
        print("× 没有 assets/pet/face.png，先跑 python tools/build_pet.py")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    face = _face_square()
    for s in SIZES:
        render(s, face).save(OUT / ("whale-%d.png" % s))
        print("png whale-%d.png" % s)
    render(SIZES[-1], face).save(OUT / "whale.ico", format="ICO", sizes=ICO_SIZES)
    print("ico whale.ico")
    return 0


if __name__ == "__main__":
    sys.exit(main())

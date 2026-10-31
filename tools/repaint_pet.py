# -*- coding: utf-8 -*-
"""把用户给的三张参考图做成桌面宠物的三态立绘。

做的事：
  1. 先把台词气泡 / 指向的手指 / 碗 / 「已思考」卡片 / 大字幕这些"多余的"区域
     涂成纯白（涂之前它们是带黑边的图形，泛洪填不掉，必须显式抹掉）；
  2. 再从四条边泛洪删近白像素 → 透明背景；
  3. 高斯羽化 1px，去掉锯齿；
  4. 裁到内容外框，等比缩到 512×512。

跑法：python tools/repaint_pet.py
原图从 .cache/pet_src 读，输出到 assets/pet/{idle,eat,full}.png
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "pet_src"
OUT = ROOT / "assets" / "pet"

WHITE_MIN = 232      # 三个通道都 >= 这个值才算"白"
CHROMA_MAX = 20      # 最大最小通道差 <= 这个值才算"白"（排除浅蓝浅粉）

# 每张图的处理参数：抹白矩形 + 可选裁剪框
JOBS = {
    "idle": {
        # 站立叉腰那张：去掉气泡、手指、字幕、碗
        "src": "stand.png",
        "erase": [
            (0, 0, 202, 188),         # 左边缘整片（气泡、尾巴、留白）
            (0, 18, 288, 152),        # 气泡主体
            (198, 144, 262, 200),     # 气泡的尾巴（贴着耳朵尖，只能这么小一块）
            (0, 186, 214, 330),       # 指向的手和袖子
            (58, 330, 262, 414),      # 「没吃饱喵！」和它的描边
            (92, 412, 276, 566),      # 碗、碟子和阴影
        ],
        # 气泡和手指贴在一起，矩形擦会啃到耳朵；这一小块只擦肤色像素
        "skin": (150, 185, 232, 262),
        "crop": None,
    },
    "eat": {
        # 大头特写：去掉「正在思考....」气泡和它下面两个小圈
        "src": "think.png",
        "erase": [
            (0, 0, 182, 150),         # 气泡整块（含云朵描边和尾巴）
            (0, 0, 200, 124),         # 气泡右上那个鼓包
            (96, 126, 140, 175),      # 下面两个小圈
        ],
        "skin": None,
        "ink": (150, 55, 252, 130),   # 压在头发上的气泡描边：只擦近黑的线
        "crop": None,
    },
    "full": {
        # 从「已思考」卡片后面探身那张：裁到卡片上沿，卡片和字幕全在框外
        "src": "peek.png",
        "erase": [],
        "skin": None,
        "crop": (24, 0, 834, 502),
    },
}


def erase_ink(img: Image.Image, box: tuple[int, int, int, int],
              fill: tuple[int, int, int]) -> Image.Image:
    """只擦"深灰/黑的线条"，不碰有颜色的头发——用来去掉压在角色上的气泡描边。"""
    px = img.load()
    x0, y0, x1, y1 = box
    for y in range(max(0, y0), min(img.height, y1)):
        for x in range(max(0, x0), min(img.width, x1)):
            r, g, b = px[x, y][:3]
            if max(r, g, b) < 158 and (max(r, g, b) - min(r, g, b)) < 46:
                px[x, y] = fill
    return img

def drop_specks(img: Image.Image, min_area: int = 45) -> Image.Image:
    """删掉抠图后残留的小碎点（气泡描边的断点、压缩噪点）。"""
    a = img.split()[-1]
    w, h = img.size
    px = a.load()
    seen = bytearray(w * h)
    for y0 in range(h):
        for x0 in range(w):
            i = y0 * w + x0
            if seen[i] or px[x0, y0] <= 8:
                continue
            comp = []
            q = deque([(x0, y0)])
            seen[i] = 1
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if not seen[j] and px[nx, ny] > 8:
                            seen[j] = 1
                            q.append((nx, ny))
            if len(comp) < min_area:
                for x, y in comp:
                    px[x, y] = 0
    out = img.copy()
    out.putalpha(a)
    return out


def bg_color(img: Image.Image) -> tuple[int, int, int]:
    """取四角的中位数当背景色——有的图白，有的图是暖灰。"""
    w, h = img.size
    pts = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3),
           (w // 2, 2), (w // 2, h - 3)]
    px = [img.getpixel(p) for p in pts]
    return tuple(sorted(c[i] for c in px)[len(px) // 2] for i in range(3))  # type: ignore[return-value]


def erase(img: Image.Image, rects: list[tuple[int, int, int, int]],
          fill: tuple[int, int, int]) -> Image.Image:
    d = ImageDraw.Draw(img)
    for r in rects:
        d.rectangle(r, fill=fill)
    return img


def erase_skin(img: Image.Image, box: tuple[int, int, int, int],
               fill: tuple[int, int, int]) -> Image.Image:
    """在指定框里只擦"皮肤色"，用来抠掉伸进来的手指而不伤到深蓝耳朵。"""
    px = img.load()
    x0, y0, x1, y1 = box
    for y in range(max(0, y0), min(img.height, y1)):
        for x in range(max(0, x0), min(img.width, x1)):
            r, g, b = px[x, y][:3]
            if r > 185 and 135 < g < 235 and 125 < b < 225 and r >= g >= b - 6:
                px[x, y] = fill
    return img


def is_bg(px: tuple[int, int, int], bg: tuple[int, int, int], tol: int = 26) -> bool:
    r, g, b = px[:3]
    # 跟背景色足够接近就算背景（有的图背景是暖灰 222,217,214，不是纯白）
    if (abs(r - bg[0]) <= tol and abs(g - bg[1]) <= tol and abs(b - bg[2]) <= tol):
        return True
    return min(r, g, b) >= WHITE_MIN and (max(r, g, b) - min(r, g, b)) <= CHROMA_MAX


def cutout(img: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """从四条边泛洪，把连通的背景色变透明（只删外部，不伤角色内部浅色）。"""
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    alpha = Image.new("L", (w, h), 255)
    a = alpha.load()
    seen = bytearray(w * h)
    q: deque[tuple[int, int]] = deque()

    def seed(x: int, y: int) -> None:
        if not seen[y * w + x] and is_bg(px[x, y], bg):
            seen[y * w + x] = 1
            q.append((x, y))

    for x in range(w):
        seed(x, 0)
        seed(x, h - 1)
    for y in range(h):
        seed(0, y)
        seed(w - 1, y)

    while q:
        x, y = q.popleft()
        a[x, y] = 0
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                if is_bg(px[nx, ny], bg):
                    seen[ny * w + nx] = 1
                    q.append((nx, ny))

    alpha = alpha.filter(ImageFilter.GaussianBlur(0.8))
    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def fit_square(img: Image.Image, size: int = 512) -> Image.Image:
    box = img.split()[-1].getbbox()
    if box:
        img = img.crop(box)
    w, h = img.size
    scale = (size - 12) / max(w, h)
    img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return canvas


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, job in JOBS.items():
        src = SRC / job["src"]
        if not src.exists():
            print("缺原图：", src)
            return 1
        img = Image.open(src).convert("RGB")
        if job["crop"]:
            img = img.crop(job["crop"])
        bg = bg_color(img)
        if job["erase"]:
            img = erase(img, job["erase"], bg)
        if job.get("skin"):
            img = erase_skin(img, job["skin"], bg)
        if job.get("ink"):
            img = erase_ink(img, job["ink"], bg)
        out = drop_specks(cutout(img, bg))
        out = fit_square(out)
        dst = OUT / ("%s.png" % name)
        out.save(dst, "PNG", optimize=True)
        print("%-6s %s  %d KB" % (name, dst.name, dst.stat().st_size // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())

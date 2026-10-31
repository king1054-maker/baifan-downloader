# -*- coding: utf-8 -*-
"""把用户新给的 5 张图整理成桌宠素材。

五张都是现成的 1024×1024 透明 PNG，不用抠背景，麻烦的是**构图不统一**：
  lie   躺着看剧（全身、横构图）
  bowl  端碗叉腰（全身、竖构图）
  panic 慌张摆手（半身）
  drool 流口水（胸像）
  face  大头特写（只有一个脑袋）
如果一律「按内容外框缩进 512」，五个姿势的脑袋会差出一倍，来回切换像换了个角色。
所以每种姿势给了独立的缩放系数，目标是把「头部大小」对齐。

另有一张 sassy（翻白眼 + 台词气泡），是**整图**：气泡和情绪线都在画面里，
不做任何裁剪或涂抹，只做等比缩放（fill=1.00，把方框占满，字才勉强看得清）。

输出：
  assets/pet/idle_lying.png  吃撑了（躺着看剧）
  assets/pet/idle_bowl.png   等饭（端碗叉腰）
  assets/pet/sassy.png       吐槽（今天下的东西有点意思时调侃一句）
  assets/pet/face.png        头像（托盘图标 / 气泡 / 关于页）

  （已按用户要求弃用两张，脚本会顺手删掉：
    full.png「慌张仰头、一脸谄媚」—— 用于「刚下完」，用户说不要了；
    eat.png 「馋嘴凑过来」      —— 用户说这只鱼一脸谄媚，也删了。）

跑法：python tools/build_pet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / ".cache" / "pet2"
OUT = ROOT / "assets" / "pet"
CANVAS = 512

# src 文件名, 输出名, 内容占画布的最大边长比例, 底部留白比例
JOBS = [
    ("lie",   "idle_lying", 1.00, 0.02),
    ("bowl2", "idle_bowl",  0.88, 0.02),
    ("face",  "face",       0.92, 0.02),
    # 这张是「凑过来吐槽」的整图：左上角的台词气泡、两个小圆圈、两道情绪线
    # 都**属于画面本身**，原图就是透明背景 + 原色气泡，不能拆、不能盖。
    # 它是别人看了会心一笑的调侃，不是对下载内容的评价，所以整张照搬。
    ("sassy", "sassy",      1.00, 0.01),
    # 下面是 2026-10-20 追加的两张：
    #   half   半张脸特写（无字）—— 下载中凑近盯着你看，占满画布，脸大才像"凑过来"
    #   ciallo 打招呼带字       —— 空闲待命 / 下载中 1/3 概率的彩蛋
    # 两张都带文字或构图撑满，不做裁字处理；ciallo 的字在画面里，裁了就不完整。
    ("half",   "half",   1.00, 0.00),
    ("ciallo", "ciallo", 1.00, 0.01),
    # 2026-10-20 再追加：tease 调侃图（手指着 +「没吃饱喵」+ 空碗）
    # 整图照搬，一个字都不裁：气泡和手都在这张画里。
    ("tease",  "tease",  1.00, 0.01),
]


def build(src_name: str, out_name: str, fill: float, margin: float) -> Image.Image:
    im = Image.open(SRC / f"{src_name}.png").convert("RGBA")
    bbox = im.getbbox()
    if not bbox:
        raise SystemExit(f"× {src_name}.png 整张是透明的")
    im = im.crop(bbox)

    # 等比缩放到「最长边 = fill × 画布」
    room = CANVAS * fill
    k = min(room / im.width, room / im.height)
    w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
    im = im.resize((w, h), Image.LANCZOS)

    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    x = (CANVAS - w) // 2
    y = CANVAS - h - round(CANVAS * margin)
    canvas.paste(im, (x, max(0, y)))
    return canvas


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for src, out, fill, margin in JOBS:
        img = build(src, out, fill, margin)
        path = OUT / f"{out}.png"
        img.save(path)
        print(f"√ {src}.png → {path.name}  {path.stat().st_size // 1024}KB")

    # 老的三态图已经被替换，删掉免得被打进安装包误导
    for junk in ("idle.png", "full.png", "eat.png"):
        p = OUT / junk
        if p.exists():
            p.unlink()
            print(f"- 删掉旧素材 {junk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

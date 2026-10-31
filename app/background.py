# -*- coding: utf-8 -*-
"""主界面背景图：用户往 data/background 里放图，程序随机/定时切换。"""
from __future__ import annotations

import random
import shutil
from pathlib import Path

from . import paths

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
DEFAULT_DIR = paths.ASSET_DIR / "backgrounds"


def seed_defaults() -> int:
    """首次运行把随包的横图/竖图示例放进 data/background（只补缺，不覆盖）。"""
    d = folder()
    n = 0
    try:
        srcs = sorted(p for p in DEFAULT_DIR.iterdir()
                      if p.is_file() and p.suffix.lower() in SUPPORTED)
    except OSError:
        return 0
    for src in srcs:
        dst = d / src.name
        if dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            n += 1
        except OSError:
            continue
    return n


def folder() -> Path:
    d = paths.DATA_DIR / "background"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def images() -> list[Path]:
    d = folder()
    try:
        return sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in SUPPORTED)
    except OSError:
        return []


def add(paths_in: list[str]) -> int:
    """把用户选的图复制进 data/background，返回成功数量。"""
    d = folder()
    n = 0
    for raw in paths_in:
        src = Path(raw)
        if not src.is_file() or src.suffix.lower() not in SUPPORTED:
            continue
        dst = d / src.name
        i = 2
        while dst.exists():
            dst = d / ("%s_%d%s" % (src.stem, i, src.suffix))
            i += 1
        try:
            shutil.copy2(src, dst)
            n += 1
        except OSError:
            continue
    return n


def clear() -> int:
    """清空背景目录（只删程序复制进来的图）。"""
    d = folder()
    n = 0
    for p in list(d.iterdir()) if d.is_dir() else []:
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def pick(exclude: Path | None = None) -> Path | None:
    imgs = images()
    if exclude is not None:
        imgs = [p for p in imgs if p != exclude] or imgs
    return random.choice(imgs) if imgs else None

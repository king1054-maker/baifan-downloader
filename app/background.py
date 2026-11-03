# -*- coding: utf-8 -*-
"""主界面背景图：用户往 data/background 里放图，程序随机/定时切换。"""
from __future__ import annotations

import os
import random
import shutil
import stat
from pathlib import Path

from . import paths

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
DEFAULT_DIR = paths.ASSET_DIR / "backgrounds"


def ensure_defaults_once(settings: dict) -> int:
    """第一次运行时铺示例图；已经铺过返回 -1（调用方不用再存盘）。

    settings 里的 bg_defaults_seeded 必须能被 config.load() 读回来
    （所以它得在 config.DEFAULTS 里）。以前不在，load 会把这个键过滤掉，
    每次启动都当"没铺过"重新复制 —— 用户删掉的示例图下次启动又回来了。
    """
    if settings.get("bg_defaults_seeded"):
        return -1
    n = seed_defaults()
    settings["bg_defaults_seeded"] = True
    return n


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


def _same_folder(p: Path, d: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(p.parent))) == \
            os.path.normcase(os.path.abspath(str(d)))
    except OSError:
        return False


def remove(items: list[str]) -> tuple[int, str]:
    """从轮换库里删掉选中的图，返回 (成功数, 失败原因)。

    只删 data/background 里的副本；用户选图时的原文件不动。
    以前这里是 `p.unlink()` 外面套一个 `except OSError: pass`，
    删不掉也一声不吭（比如文件还带只读属性），用户下次启动看到图还在，
    会以为"删除没生效"。现在先清只读属性再删，删不掉把原因带回去。
    """
    d = folder()
    n = 0
    err = ""
    for raw in items:
        p = Path(raw)
        try:
            if not p.is_file() or not _same_folder(p, d):
                continue
        except OSError:
            continue
        try:
            if not os.access(p, os.W_OK):
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        try:
            p.unlink()
            n += 1
        except OSError as exc:
            err = "%s：%s" % (p.name, exc)
    return n, err


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

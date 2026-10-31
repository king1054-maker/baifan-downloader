# -*- coding: utf-8 -*-
"""应用设置，持久化到 data/settings.json。

只放「用户意图」，不放由引擎/系统推导出来的东西（如实际端口）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    # 下载目录
    "download_dir": "",                 # 空 = 程序目录/Download
    "recent_dirs": [],                  # 最近用过的目录，新建任务下拉用
    # 连接
    "max_concurrent": 5,
    "max_conn_per_server": 16,
    "split": 16,
    # 全局限速（字节/秒，0 = 不限）
    "max_dl": 0,
    "max_ul": 100 * 1024,               # 默认全局上传限速 100 KB/s（用户 2026-10-21 定的）
    # 单任务默认限速（新建任务时可覆盖）
    "per_task_dl": 0,
    "per_task_ul": 100 * 1024,          # 默认单任务上传限速 100 KB/s
    # BT
    "enable_dht": True,
    "enable_dht6": True,
    "bt_max_peers": 300,
    "seed_time": 60,                    # 分钟；aria2 里 0 = 下完立刻停做种，不是「不限」
    "seed_ratio": 0.0,
    "ai_key_enc": "",                   # 超智能鲸鱼娘的 DeepSeek Key（DPAPI 密文）
    "ai_enabled": False,
    # 界面
    "close_to_tray": True,
    "autostart": True,             # 默认开机自启
    "minimize_to_tray": True,     # 默认最小化就缩托盘（用户的默认期望）
    "tray_promote": True,         # 安装版启动时尝试让托盘图标常显（Win11）
    "notify_on_complete": True,
    # 完成通知三通道（可同时开）
    "notify_sound": True,
    "notify_tray": True,
    "notify_bubble": True,
    "notify_sound_file": "",       # 空 = 内置 ciallo.wav；可填绝对路径
    "notify_volume": 60,           # 提示音音量 0–100（0 = 静音）
    "notify_only_when_hidden": False,
    # 打开方式里用户自己挑的程序（按扩展名记）
    "custom_openers": {},
    "start_minimized": False,
    "window_geometry": "",         # 主窗口上次的位置/大小（saveGeometry 的 base64）
    "window_maximized": False,     # 上次关闭时是不是最大化
    "bg_random": True,             # 每次启动从 data/background 随机选一张
    "bg_blank": False,             # 使用空白背景时，整个换图/切换区都禁用
    "bg_timer_on": True,           # 使用过程中定时自动切换
    "bg_interval_min": 3,          # 默认 3 分钟换一张
    "bg_veil": 50,                 # 背景上方白纱浓度 0–100（0=原图，100=接近白底）
    "bg_current": "",              # 当前正在显示的文件名（设为当前背景后不要被设置页随机掉）
    "bg_crops": {},                # 每张图的裁切框 {文件名: [x,y,w,h]}（0–1）
    "bg_anchors": {},              # 旧版裁切锚点，保留兼容
    "theme": "light",
    "torrent_assoc": False,
    "show_pet": True,
    # 自动给种子任务起"核心内容"显示名（从文件名提关键词，只改列表，不动磁盘）
    "auto_title": True,
}


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    f = paths.SETTINGS_FILE
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if isinstance(saved, dict):
                data.update({k: v for k, v in saved.items() if k in DEFAULTS})
        except Exception:  # noqa: BLE001 - 坏文件不阻塞启动
            pass
    if not data["download_dir"]:
        data["download_dir"] = str(paths.DL_DIR)
    return data


def save(data: dict[str, Any]) -> None:
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = paths.SETTINGS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, paths.SETTINGS_FILE)


def default_recent_dirs() -> list[str]:
    """新建任务里「备选目录」的初始值：桌面、D 盘、系统下载。"""
    home = Path(os.path.expanduser("~"))
    cands = [str(paths.DL_DIR), str(home / "Desktop"), str(home / "Downloads")]
    for letter in ("D:/", "E:/"):
        p = Path(letter)
        if p.exists():
            cands.append(str(p / "Downloads") if (p / "Downloads").exists() else str(p))
    out: list[str] = []
    for c in cands:
        if c not in out and Path(c).exists():
            out.append(c)
    return out

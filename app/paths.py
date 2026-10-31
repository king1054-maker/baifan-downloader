# -*- coding: utf-8 -*-
"""路径解析。

开发态与打包态（PyInstaller onedir）的根目录不同，全部集中在这里判断，
其它模块禁止自己拼 sys.executable / __file__。

约定：
  * 可写数据（conf / data / logs / Download / session / dht）永远在【程序目录】
    —— 这是"整个文件夹拷走就能用"的前提。
  * 只读资源（图标、内置 aria2.exe、conf 模板）打包后在 _MEIPASS 下。
"""
from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _app_root() -> Path:
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _res_root() -> Path:
    if FROZEN:
        # onedir 模式下 _MEIPASS 指向 <程序目录>/_internal
        return Path(getattr(sys, "_MEIPASS", _app_root()))
    return Path(__file__).resolve().parent.parent


APP_ROOT: Path = _app_root()
RES_ROOT: Path = _res_root()

BIN_DIR = RES_ROOT / "bin"
ASSET_DIR = RES_ROOT / "assets"
ARIA2_EXE = BIN_DIR / "aria2c.exe"

CONF_DIR = APP_ROOT / "conf"
DATA_DIR = APP_ROOT / "data"
LOG_DIR = APP_ROOT / "logs"
DL_DIR = APP_ROOT / "Download"

CONF_TEMPLATE = ASSET_DIR / "conf" / "aria2.conf.tpl"
CONF_FILE = CONF_DIR / "aria2.conf"
SETTINGS_FILE = DATA_DIR / "settings.json"
TASKS_FILE = DATA_DIR / "tasks.json"
SECRET_FILE = CONF_DIR / "rpc-secret.txt"
API_TOKEN_FILE = CONF_DIR / "api-token.txt"
SESSION_FILE = CONF_DIR / "session"
DHT_FILE = CONF_DIR / "dht.dat"
DHT6_FILE = CONF_DIR / "dht6.dat"
ENGINE_LOG = LOG_DIR / "aria2.log"
APP_LOG = LOG_DIR / "app.log"

RPC_PORT = 6810
API_PORT = 6811

for _d in (CONF_DIR, DATA_DIR, LOG_DIR, DL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

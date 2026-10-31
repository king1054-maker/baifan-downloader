# -*- coding: utf-8 -*-
"""把 .torrent 文件关联到本程序（只写 HKCU，不需要管理员权限）。"""
from __future__ import annotations

import os
import sys
import winreg
from pathlib import Path

from . import paths

PROGID = "BaiFan.Torrent"
EXT_KEY = r"Software\Classes\.torrent"
PROGID_KEY = r"Software\Classes\%s" % PROGID
COMMAND_KEY = PROGID_KEY + r"\shell\open\command"


def launch_command() -> str:
    """双击 .torrent 时执行的命令（带 %1 占位）。"""
    if getattr(sys, "frozen", False):
        return '"%s" "%%1"' % sys.executable
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    runner = paths.APP_ROOT / "run.py"
    return '"%s" "%s" "%%1"' % (pyw if pyw.exists() else exe, runner)


def is_registered() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, EXT_KEY) as k:
            v = winreg.QueryValueEx(k, "")[0]
        if v != PROGID:
            return False
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, COMMAND_KEY) as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except OSError:
        return False


def register() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, EXT_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, PROGID)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PROGID_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "BitTorrent 种子文件")
        winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, "种子文件")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, PROGID_KEY + r"\DefaultIcon",
                            0, winreg.KEY_WRITE) as k:
        ico = paths.ASSET_DIR / "icons" / "whale.ico"
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(ico) if ico.exists() else "")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, COMMAND_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command())
    _notify_shell()


def unregister() -> None:
    for key in (COMMAND_KEY, PROGID_KEY, EXT_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except OSError:
            pass
    _notify_shell()


def _notify_shell() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:  # noqa: BLE001
        pass


def tooltip() -> str:
    return os.path.basename(launch_command())


# ============================ 开机自启 ============================
# 写法：HKCU\Software\Microsoft\Windows\CurrentVersion\Run 下一条值。
# 只写当前用户，不需要管理员，也不碰系统级键。
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "BaiFanDownloader"


def startup_command() -> str:
    """开机时执行的命令（没有 %1，路径都加引号）。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")     # 后台启动，不要黑框
    runner = paths.APP_ROOT / "run.py"
    return '"%s" "%s"' % (pyw if pyw.exists() else exe, runner)


def is_autostart() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v = winreg.QueryValueEx(k, RUN_VALUE)[0]
        return os.path.normcase(str(v)).strip() == os.path.normcase(
            startup_command()).strip()
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    """开/关开机自启；返回最终状态（写不下去就是 False，不抛异常）。"""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return is_autostart() if on else not is_autostart()
    except OSError:
        return False


# ====================== Windows 11 托盘图标常显 ======================
# Win11 默认把新托盘图标塞进「^」溢出区，微软没有公开 API 让程序自己钉出来。
# 但系统把「用户是否把这个图标拖出来」记在
#   HKCU\Control Panel\NotifyIconSettings\<hash>
# 的 IsPromoted (DWORD) 里。把这个值改成 1，等价于用户手动拖出来。
# 只对**打包后的 exe**有意义：源码版托盘进程是 pythonw.exe，
# 改 pythonw 的项会把别的 Python 托盘程序一起钉出来，不能干。
NOTIFY_ICONS_KEY = r"Control Panel\NotifyIconSettings"


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
            os.path.abspath(str(b)))
    except OSError:
        return False


def find_tray_key(executable: str | None = None) -> str | None:
    """在 NotifyIconSettings 里找这个 exe 对应的子键名；找不到返回 None。"""
    exe = executable or sys.executable
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, NOTIFY_ICONS_KEY) as root:
            n = winreg.QueryInfoKey(root)[0]
            for i in range(n):
                name = winreg.EnumKey(root, i)
                try:
                    with winreg.OpenKey(root, name) as sub:
                        path = str(winreg.QueryValueEx(sub, "ExecutablePath")[0])
                except OSError:
                    continue
                if _same_path(path, exe):
                    return name
    except OSError:
        return None
    return None


def promote_tray_icon(executable: str | None = None) -> bool:
    """把本程序的托盘图标标记为常显（等价于用户把它拖出溢出区）。

    找不到系统登记项（图标还没被 Explorer 记录）就返回 False，
    调用方可过几秒再试。只写 HKCU，不需要管理员。
    """
    exe = executable or sys.executable
    key = find_tray_key(exe)
    if not key:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            NOTIFY_ICONS_KEY + "\\" + key,
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 1)
        return True
    except OSError:
        return False


def is_tray_promotable() -> bool:
    """只有打包成 exe 的版本才值得做托盘常显（源码版是共享的 pythonw.exe）。"""
    return bool(getattr(sys, "frozen", False))

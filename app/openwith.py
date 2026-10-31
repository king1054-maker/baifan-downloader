# -*- coding: utf-8 -*-
"""Windows「打开方式」：枚举某个扩展名可用的程序，并按指定程序打开。

只读注册表，不写任何全局项；UWP（照片 / Clipchamp 之类）没有可用的命令行，
会被自动跳过，改用系统的「打开方式」对话框兜底。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import winreg
from dataclasses import dataclass
from pathlib import Path

from . import intents

# 常见播放器的可执行文件名：App Paths 里能查到就直接列出来
KNOWN_EXES = [
    "vlc.exe", "PotPlayerMini64.exe", "PotPlayerMini.exe", "mpc-hc64.exe",
    "mpc-be64.exe", "mpv.exe", "QQPlayer.exe", "wmplayer.exe", "mspaint.exe",
    "notepad.exe", "MPCVR.exe",
]

FRIENDLY = {
    "vlc.exe": "VLC media player",
    "potplayermini64.exe": "PotPlayer 64",
    "potplayermini.exe": "PotPlayer",
    "mpc-hc64.exe": "MPC-HC 64",
    "mpc-be64.exe": "MPC-BE 64",
    "mpv.exe": "mpv",
    "wmplayer.exe": "旧版 Windows Media Player",
    "mspaint.exe": "画图",
    "notepad.exe": "记事本",
}


@dataclass
class Handler:
    label: str
    command: str = ""
    exe: str = ""
    progid: str = ""
    is_default: bool = False
    kind: str = "exe"          # exe / default / dialog / reveal

    @property
    def icon_path(self) -> str:
        return self.exe if self.exe and os.path.exists(self.exe) else ""


# ---------- 注册表小工具 ----------
def _q(root, path: str, name: str | None = None) -> str:
    try:
        with winreg.OpenKey(root, path) as k:
            v, _t = winreg.QueryValueEx(k, name if name is not None else "")
        return v if isinstance(v, str) else ""
    except OSError:
        return ""


def _subkeys(root, path: str) -> list[str]:
    out: list[str] = []
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    out.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def _values(root, path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        with winreg.OpenKey(root, path) as k:
            i = 0
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, i)
                    out.append((n, v if isinstance(v, str) else ""))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass
    return out


def _indirect(s: str) -> str:
    """把 @dll,-123 / @{包?ms-resource:...} 解析成人类可读文本。"""
    if not s or not s.startswith("@"):
        return s
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shlwapi.SHLoadIndirectString(s, buf, 1024, None) == 0:
            return buf.value or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _clean_label(s: str) -> str:
    s = (s or "").strip()
    if not s or s.startswith("@"):
        return ""
    return s.replace("&", "")[:40]


def _exe_from_command(cmd: str) -> str:
    if not cmd:
        return ""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        exe = cmd[1:end] if end > 0 else cmd[1:]
    else:
        exe = cmd.split(" ")[0]
    exe = os.path.expandvars(exe)
    if not exe.lower().endswith(".exe"):
        return ""
    return exe


def _command_for_progid(progid: str) -> str:
    if not progid:
        return ""
    for root, base in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"),
                       (winreg.HKEY_CLASSES_ROOT, "")):
        p = (base + "\\" + progid + r"\shell\open\command").strip("\\")
        cmd = _q(root, p)
        if cmd:
            return cmd
    return ""


def _command_for_exe(exe: str) -> str:
    for root, base in ((winreg.HKEY_CLASSES_ROOT, r"Applications\%s" % exe),
                       (winreg.HKEY_CURRENT_USER, r"Software\Classes\Applications\%s" % exe)):
        p = base + r"\shell\open\command"
        cmd = _q(root, p)
        if cmd:
            return cmd
    return ""


def _label_for_exe(exe: str) -> str:
    name = os.path.basename(exe).lower()
    for root, p in ((winreg.HKEY_CLASSES_ROOT, r"Applications\%s" % name),):
        lab = _clean_label(_q(root, p))
        if not lab:
            lab = _clean_label(_indirect(_q(root, p, "FriendlyAppName")))
        if lab:
            return lab
    return FRIENDLY.get(name) or os.path.splitext(os.path.basename(exe))[0]


def _label_for_progid(progid: str) -> str:
    for root, base in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"),
                       (winreg.HKEY_CLASSES_ROOT, "")):
        p = (base + "\\" + progid).strip("\\")
        lab = _clean_label(_q(root, p))
        if lab:
            return lab
        lab = _clean_label(_indirect(_q(root, p, "FriendlyAppName")))
        if lab:
            return lab
        lab = _clean_label(_indirect(_q(root, p + r"\Application", "ApplicationName")))
        if lab:
            return lab
    return ""


def default_progid(ext: str) -> str:
    p = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\%s\UserChoice" % ext
    pid = _q(winreg.HKEY_CURRENT_USER, p, "ProgId")
    if pid:
        return pid
    return _q(winreg.HKEY_CLASSES_ROOT, ext)


def _mru_exes(ext: str) -> list[str]:
    p = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\%s\OpenWithList" % ext
    pairs = dict(_values(winreg.HKEY_CURRENT_USER, p))
    mru = pairs.get("MRUList", "")
    out: list[str] = []
    for ch in mru:
        v = pairs.get(ch, "")
        if v and not v.lower().startswith("appx"):
            out.append(v)
    for name, v in pairs.items():
        if name != "MRUList" and v and not v.lower().startswith("appx") and v not in out:
            out.append(v)
    return out


def _open_with_progids(ext: str) -> list[str]:
    out: list[str] = []
    for root, base in ((winreg.HKEY_CURRENT_USER, r"Software\Classes"),
                       (winreg.HKEY_CLASSES_ROOT, "")):
        p = (base + "\\" + ext + r"\OpenWithProgids").strip("\\")
        out.extend(n for n, _v in _values(root, p))
    return out


def _app_path(exe: str) -> str:
    p = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        v = _q(root, p)
        if v and os.path.exists(os.path.expandvars(v)):
            return os.path.expandvars(v)
    return ""


def _player_candidates() -> list[str]:
    found: list[str] = []
    for exe in KNOWN_EXES:
        p = _app_path(exe)
        if p:
            found.append(p)
    # App Paths 里查不到时，兜底查常见安装目录（VLC 装 D 盘也能认出来）
    quick = [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        r"D:\Program Files\VLC\vlc.exe",
        r"E:\Program Files\VLC\vlc.exe",
        r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
        r"C:\Program Files\mpv\mpv.exe",
    ]
    for p in quick:
        if os.path.exists(p) and p not in found:
            found.append(p)
    return found


def handlers_for(path: str | Path, custom_openers: list[str] | None = None) -> list[Handler]:
    """列出一份像 Windows「打开方式」的菜单项，默认程序排第一。"""
    ext = intents.ext_of(path).lower()
    if not ext:
        return []
    out: list[Handler] = []
    seen: set[str] = set()

    def push(h: Handler) -> None:
        if not h.command and h.kind == "exe":
            return
        key = (h.exe or h.progid or h.label).lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(h)

    pid = default_progid(ext)
    if pid:
        cmd = _command_for_progid(pid)
        if cmd:
            exe = _exe_from_command(cmd)
            label = (_label_for_exe(os.path.basename(exe)) if exe else "") \
                or _label_for_progid(pid) or pid
            push(Handler(label, cmd, exe, pid, is_default=True))
        else:
            # 默认程序是 UWP（照片 / 媒体播放器），命令行拉不起来：
            # 给一个「系统默认」入口，走 ShellExecute 仍然能正常打开。
            push(Handler("系统默认程序", "", "", pid, is_default=True, kind="default"))
    if not out:
        out.append(Handler("系统默认程序", "", "", "", is_default=True, kind="default"))

    for exe_name in _mru_exes(ext):
        cmd = _command_for_exe(exe_name)
        exe = _exe_from_command(cmd) or _app_path(exe_name)
        if cmd:
            push(Handler(_label_for_exe(exe_name), cmd, exe))
        elif exe:
            push(Handler(_label_for_exe(exe_name), '"%s" "%%1"' % exe, exe))

    for p in _open_with_progids(ext):
        cmd = _command_for_progid(p)
        if not cmd:
            continue          # UWP：只有 DelegateExecute，没法用命令行拉起
        exe = _exe_from_command(cmd)
        label = (_label_for_exe(os.path.basename(exe)) if exe else "") \
            or _label_for_progid(p)
        push(Handler(label, cmd, exe, p))

    for exe in list(custom_openers or []) + _player_candidates():
        if not os.path.exists(exe):
            continue
        cmd = _command_for_exe(os.path.basename(exe)) or '"%s" "%%1"' % exe
        push(Handler(_label_for_exe(os.path.basename(exe)), cmd, exe))

    # 找到默认程序后，把它排到最前
    out.sort(key=lambda h: 0 if h.is_default else 1)
    return out[:16]


def open_with(path: str | Path, handler: Handler) -> tuple[bool, str]:
    p = Path(path)
    if not p.exists():
        return False, "文件不存在"
    if handler.kind == "default" or not handler.command:
        try:
            os.startfile(str(p))  # noqa: S606
            return True, ""
        except OSError as exc:
            return False, str(exc)
    q = '"%s"' % str(p)
    cmd = handler.command
    for token in ('"%1"', "%1", '"%L"', "%L"):
        cmd = cmd.replace(token, q)
    cmd = cmd.replace("%*", "").strip()
    try:
        subprocess.Popen(cmd, cwd=str(p.parent))
        return True, ""
    except OSError as exc:
        return False, str(exc)


def show_open_with_dialog(path: str | Path) -> None:
    """系统的「打开方式」对话框（UWP 应用只能走这里）。"""
    p = str(path)
    try:
        r = ctypes.windll.shell32.ShellExecuteW(None, "openas", p, None, None, 1)
        if r > 32:
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", p])
    except OSError:
        pass

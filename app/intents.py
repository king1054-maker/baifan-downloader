# -*- coding: utf-8 -*-
"""链接识别、文件类型判定与「双击打开」策略。

链接识别要能吞下用户从各种地方复制来的整段文字：
    「7.43 复制打开抖音…」这类分享文案里夹着 URL、
    「magnet:?xt=urn:btih:xxxx」直接贴、
    资源站那种「复制磁力」按钮给出的裸 40 位 hash。

打开策略（按用户要求）：
  * 单个视频 / 图片 / 音频  → 用系统默认程序直接打开
  * 目录 / 多文件任务 / exe / 压缩包 → 打开所在文件夹
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXT = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m2ts", ".mts",
    ".webm", ".rmvb", ".rm", ".mpg", ".mpeg", ".m4v", ".3gp", ".vob", ".asf",
}
IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".heic", ".tif", ".tiff",
}
AUDIO_EXT = {
    ".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".ape",
}
EXEC_EXT = {".exe", ".msi", ".bat", ".cmd", ".com", ".ps1", ".jar", ".lnk"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz"}

OPEN_DIRECT = VIDEO_EXT | IMAGE_EXT | AUDIO_EXT

# ---------- 链接正则 ----------
MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.I)
BTIH_RE = re.compile(r"xt=urn:btih:([0-9a-zA-Z]{32,40})", re.I)
URL_RE = re.compile(r"(?:https?|ftp)://[^\s\"'<>]+", re.I)
FILE_URL_RE = re.compile(r"file:///[^\s\"'<>]+", re.I)
WIN_TORRENT_RE = re.compile(r"[A-Za-z]:[\\/][^\r\n\"'<>|?*]*?\.torrent", re.I)
HASH_RE = re.compile(r"^[0-9a-fA-F]{40}$")
THUNDER_RE = re.compile(r"thunder://", re.I)
ED2K_RE = re.compile(r"ed2k://", re.I)

TRAILING = " \t\r\n\"'<>`，。；：、！？）》】」”’,.;:!?)]}"


@dataclass(frozen=True)
class Link:
    kind: str          # magnet / torrent / url
    value: str         # magnet 链接、http 链接或本地 .torrent 绝对路径

    @property
    def label(self) -> str:
        if self.kind == "magnet":
            h = magnet_hash(self.value)
            return "磁力 · %s" % (h[:12] + "…" if h else "BT")
        if self.kind == "torrent":
            return "种子 · " + os.path.basename(self.value)
        return "链接 · " + urllib.parse.urlparse(self.value).netloc


def _clean(u: str) -> str:
    return u.strip().strip(TRAILING).rstrip(TRAILING)


def magnet_hash(uri: str) -> str:
    m = BTIH_RE.search(uri)
    return (m.group(1) if m else "")


def is_magnet(s: str) -> bool:
    return bool(BTIH_RE.search(s or ""))


def hash_to_magnet(h: str) -> str:
    return "magnet:?xt=urn:btih:%s" % h.strip()


def extract_links(text: str) -> list[Link]:
    """从任意文本里捞出可下载的东西，按出现顺序去重。"""
    if not text:
        return []
    out: list[Link] = []
    seen: set[str] = set()

    def add(kind: str, value: str) -> None:
        v = _clean(value)
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(Link(kind, v))

    # 1) 磁力（先抓，避免里面的 & 参数被 URL 正则切走）
    for m in MAGNET_RE.finditer(text):
        v = m.group(0)
        if is_magnet(v):
            add("magnet", v)
    rest = MAGNET_RE.sub(" ", text)

    # 2) http / ftp 链接（.torrent 结尾的仍走普通 URL，aria2 自己会处理）
    for m in URL_RE.finditer(rest):
        add("url", m.group(0))

    # 3) 本地种子文件：file:/// 形式 与 盘符路径
    for m in FILE_URL_RE.finditer(rest):
        raw = m.group(0)[len("file:///"):]
        p = urllib.parse.unquote(raw).replace("/", "\\")
        if p.lower().endswith(".torrent") and Path(p).exists():
            add("torrent", p)
    for m in WIN_TORRENT_RE.finditer(rest):
        p = _clean(m.group(0))
        if Path(p).exists():
            add("torrent", p)

    # 4) 整段只有一个裸 hash（资源站常见）
    only = text.strip()
    if HASH_RE.match(only):
        add("magnet", hash_to_magnet(only))

    return out


def unsupported_links(text: str) -> list[str]:
    """aria2 无能为力的协议，单独提示，不要默默失败。"""
    out = []
    if THUNDER_RE.search(text or ""):
        out.append("迅雷专用链接（thunder://）")
    if ED2K_RE.search(text or ""):
        out.append("电驴链接（ed2k://）")
    return out


# ---------- 文件类型 ----------
def ext_of(p: str | Path) -> str:
    return os.path.splitext(str(p))[1].lower()


def kind_of(p: str | Path) -> str:
    """返回 video / image / audio / exec / archive / torrent / folder / other。"""
    path = Path(p)
    if path.is_dir():
        return "folder"
    e = ext_of(path)
    if e in VIDEO_EXT:
        return "video"
    if e in IMAGE_EXT:
        return "image"
    if e in AUDIO_EXT:
        return "audio"
    if e in EXEC_EXT:
        return "exec"
    if e == ".torrent":
        return "torrent"
    if e in ARCHIVE_EXT:
        return "archive"
    return "other"


def resolve_target(dir_path: str, name: str, file_count: int = 1) -> Path | None:
    """从 aria2 的 dir + name 还原出真实路径。

    多文件任务（BT 常见）name 是文件夹名，直接返回文件夹。
    """
    base = Path(dir_path)
    if name:
        cand = base / name
        if cand.exists():
            return cand
    if base.exists():
        return base
    return None


def reveal(path: Path | None) -> None:
    """在资源管理器中定位（不运行）。"""
    if path is None:
        return
    try:
        if path.is_dir():
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["explorer", "/select,", str(path)])
    except OSError:
        pass


def open_default(path: Path | None) -> bool:
    """用系统默认程序打开（视频/图片/音频点一下就能播）。"""
    if path is None or not path.exists():
        return False
    try:
        os.startfile(str(path))  # noqa: S606 - 用户明确要求直接打开
        return True
    except OSError:
        return False


def open_path(path: Path | None, file_count: int = 1) -> str:
    """执行「双击任务」的动作，返回做了什么（用于状态栏提示）。"""
    if path is None:
        return "目标不存在"
    if path.is_file():
        kind = kind_of(path)
        if kind in ("video", "image", "audio") and file_count <= 1:
            return "已用默认程序打开" if open_default(path) else "打开失败"
        reveal(path)
        return "已在资源管理器中定位"
    if path.is_dir():
        reveal(path)
        return "已打开文件夹"
    return "无法处理的路径"

# -*- coding: utf-8 -*-
"""下载完整性校验。

要解决的问题：文件"看起来下完了、体积也对"，打开却跳帧/卡帧/解压报错。

三层检查，从便宜到贵：
  1. 存在性 + 字节数（对 aria2 上报的 length 逐个比对）
  2. 容器结构（MP4 必须能找到 moov，MKV 必须有 EBML 头且尾部能读到 Cluster，
     ZIP 必须有中央目录结尾，PDF 必须有 %%EOF……）—— 这一步能抓出绝大多数"体积对但内容截断"
  3. BitTorrent 哈希复核（可选，耗时；由 aria2 重新校验整块，失败说明分片损坏）

不依赖 ffmpeg：只读文件头尾，几毫秒完成，几 GB 的视频也不卡。
"""
from __future__ import annotations

import os
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .intents import kind_of

TAIL = 2 * 1024 * 1024     # 读尾部 2MB：慢速启动的 MP4 的 moov 常常在末尾
HEAD = 64 * 1024


@dataclass
class Check:
    path: str
    ok: bool = True
    size: int = 0
    expected: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.problems.append(msg)

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "ok": self.ok,
            "size": self.size,
            "expected": self.expected,
            "problems": self.problems,
            "notes": self.notes,
        }


def _read(path: Path, offset: int, count: int) -> bytes:
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(count)


def _check_mp4(p: Path, c: Check) -> None:
    head = _read(p, 0, HEAD)
    if len(head) < 12 or head[4:8] != b"ftyp":
        c.fail("不是有效的 MP4/MOV（缺少 ftyp 头）")
        return
    brand = head[8:12]
    c.notes.append("容器品牌 %s" % brand.decode("latin-1", "replace"))
    # moov 可能在文件头（faststart）也可能在尾部
    if b"moov" in head:
        c.notes.append("moov 位于文件头")
        return
    size = c.size
    tail_start = max(0, size - TAIL)
    tail = _read(p, tail_start, min(TAIL, size))
    pos = tail.rfind(b"moov")
    if pos < 0:
        c.fail("找不到 moov 索引块：文件被截断，或仍在写入")
        return
    # moov box 的 size 字段在 "moov" 前 4 字节
    if pos >= 4:
        (box_size,) = struct.unpack(">I", tail[pos - 4:pos])
        if box_size and pos - 4 + box_size > len(tail) + (size - tail_start - len(tail)):
            c.fail("moov 索引块长度越界：索引损坏")
            return
    c.notes.append("moov 位于文件尾，已确认存在")


def _check_mkv(p: Path, c: Check) -> None:
    head = _read(p, 0, 8)
    if head[:4] != b"\x1a\x45\xdf\xa3":
        c.fail("不是有效的 Matroska/WebM（缺少 EBML 头）")
        return
    tail = _read(p, max(0, c.size - 65536), 65536)
    # Cluster ID 0x1F43B675；截断文件常见于最后一个 Cluster 不完整
    if b"\x1f\x43\xb6\x75" not in tail:
        c.notes.append("尾部 64KB 未找到 Cluster 边界（可能截断，但仍可能可播放）")
    c.notes.append("EBML 头正常")


def _check_avi(p: Path, c: Check) -> None:
    head = _read(p, 0, 12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"AVI ":
        c.fail("不是有效的 AVI")
        return
    (riff_size,) = struct.unpack("<I", head[4:8])
    if riff_size + 8 > c.size + 1024:
        c.fail("RIFF 声明长度 %d 超过实际文件大小 %d：文件被截断" % (riff_size + 8, c.size))


def _check_flv(p: Path, c: Check) -> None:
    if _read(p, 0, 3) != b"FLV":
        c.fail("不是有效的 FLV")


def _check_ts(p: Path, c: Check) -> None:
    head = _read(p, 0, 188)
    if len(head) < 188 or head[0] != 0x47:
        c.fail("不是有效的 MPEG-TS（同步字节错误）")


def _check_zip(p: Path, c: Check) -> None:
    try:
        with zipfile.ZipFile(p) as zf:
            bad = zf.testzip()
            if bad:
                c.fail("ZIP 内文件损坏: %s" % bad)
            else:
                c.notes.append("ZIP 中央目录与 CRC 均正常")
    except zipfile.BadZipFile as exc:
        c.fail("ZIP 结构损坏: %s" % exc)
    except OSError as exc:
        c.fail("读取失败: %s" % exc)


def _check_rar7z(p: Path, c: Check) -> None:
    head = _read(p, 0, 8)
    if head[:4] == b"Rar!":
        c.notes.append("RAR 头正常（完整解压需外部工具校验）")
    elif head[:6] == b"7z\xbc\xaf\x27\x1c":
        c.notes.append("7z 头正常（完整解压需外部工具校验）")
    else:
        c.fail("压缩包头部标识不正确")


def _check_pdf(p: Path, c: Check) -> None:
    if not _read(p, 0, 5).startswith(b"%PDF"):
        c.fail("不是有效的 PDF")
        return
    if b"%%EOF" not in _read(p, max(0, c.size - 2048), 2048):
        c.fail("PDF 缺少 %%EOF 结尾：文件被截断")


def _check_image(p: Path, c: Check) -> None:
    head = _read(p, 0, 32)
    ok = (
        head[:3] == b"\xff\xd8\xff"                      # jpg
        or head[:8] == b"\x89PNG\r\n\x1a\n"              # png
        or head[:6] in (b"GIF87a", b"GIF89a")            # gif
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or head[:2] == b"BM"                             # bmp
    )
    if not ok:
        c.fail("图片头部标识无法识别")
    if p.suffix.lower() in (".jpg", ".jpeg"):
        tail = _read(p, max(0, c.size - 4), 4)
        if len(tail) < 2 or tail[-2:] != b"\xff\xd9":
            c.fail("JPEG 缺少 FFD9 结束标记：文件被截断")


_CHECKERS = {
    "video": None,   # 按扩展名细分
    "image": _check_image,
}


def check_file(path: str | Path, expected: int = 0) -> Check:
    p = Path(path)
    c = Check(path=str(p), expected=int(expected or 0))
    if not p.exists():
        c.fail("文件不存在")
        return c
    c.size = p.stat().st_size
    if expected and c.size != expected:
        c.fail("大小不符：实际 %d 字节，应为 %d 字节" % (c.size, expected))
    if p.is_dir():
        c.notes.append("目录（多文件任务，逐个文件校验）")
        return c
    if c.size == 0:
        c.fail("文件大小为 0")
        return c

    e = p.suffix.lower()
    try:
        if e in (".mp4", ".m4v", ".mov", ".3gp"):
            _check_mp4(p, c)
        elif e in (".mkv", ".webm"):
            _check_mkv(p, c)
        elif e in (".avi", ".wmv", ".asf"):
            _check_avi(p, c)
        elif e == ".flv":
            _check_flv(p, c)
        elif e in (".ts", ".m2ts", ".mts"):
            _check_ts(p, c)
        elif e == ".zip":
            _check_zip(p, c)
        elif e in (".rar", ".7z"):
            _check_rar7z(p, c)
        elif e == ".pdf":
            _check_pdf(p, c)
        elif kind_of(p) == "image":
            _check_image(p, c)
        else:
            c.notes.append("该类型仅做大小校验")
    except (OSError, struct.error) as exc:
        c.fail("读取失败: %s" % exc)
    return c


def check_task(files: list[dict], base_dir: str) -> dict:
    """校验 aria2 实际选择下载的文件，而不误判未选 BT 文件。

    aria2 的 selected 一般为字符串 "true" / "false"。
    若缺少 selected 字段（旧数据或 HTTP 任务），按已选择处理。
    该函数只做文件大小和部分格式结构检查，不等于 BT 分片哈希复核。
    """
    checks: list[Check] = []
    skipped = 0
    for f in files:
        selected = f.get("selected", True)
        if selected is False or str(selected).strip().lower() in ("false", "0", "no"):
            skipped += 1
            continue
        raw = f.get("path", "")
        expected = int(f.get("length") or 0)
        done = int(f.get("completedLength") or 0)
        if expected and done != expected:
            c = Check(path=raw, expected=expected, size=done, ok=False,
                      problems=["未下载完：%d/%d 字节" % (done, expected)])
            checks.append(c)
            continue
        checks.append(check_file(raw, expected))

    # 无文件详情、或全部未勾选时，不能把空检查列表认作完整。
    ok = bool(checks) and all(c.ok for c in checks)
    return {
        "ok": ok,
        "file_count": len(checks),
        "total_file_count": len(files),
        "skipped_count": skipped,
        "bad_count": sum(1 for c in checks if not c.ok),
        "files": [c.as_dict() for c in checks if not c.ok][:20],
        "dir": base_dir,
        **({"error": "没有可校验的已选择文件"} if not checks else {}),
    }


def is_complete_enough(path: Path) -> bool:
    return check_file(path).ok

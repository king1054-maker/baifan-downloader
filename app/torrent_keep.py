# -*- coding: utf-8 -*-
"""历史种子库：把用过的 .torrent 留一份，并在 data/torrents/index.json 记一份索引。

为什么要留：
  * BT 下完还能继续上传（做种），但做种停了之后想重新开始，
    必须**有种子文件**才能让 aria2 重新校验 + 开始上传；
  * 磁力链接可以重新贴一次（aria2 会自己再取元数据），但本地 .torrent
    加的任务如果不留一份，就再也找不到那个种子了；
  * 比特彗星有一个"种子存档/种子库"，用户要的就是这个：
    种子文件单独存，不要当成"已经下好的文件"混在任务列表里。

文件布局：
  data/torrents/<infohash>.torrent   —— 真正的种子文件（几十 KB 一个）
  data/torrents/index.json           —— 名称/大小/gid/目录/来源/添加时间

兼容旧版：旧版文件名是 <gid>.torrent，没有索引；entries() 会扫到并补进索引。
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

from . import paths

DIR = paths.DATA_DIR / "torrents"
INDEX = DIR / "index.json"


def path_of(key: str) -> Path:
    return DIR / ("%s.torrent" % key)


# ---------- bencode：只为了从种子里读出 name / 总大小 ----------
def _bdecode(buf: bytes, i: int = 0):
    c = buf[i:i + 1]
    if c == b"i":
        j = buf.index(b"e", i)
        return int(buf[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while buf[i:i + 1] != b"e":
            v, i = _bdecode(buf, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while buf[i:i + 1] != b"e":
            k, i = _bdecode(buf, i)
            v, i = _bdecode(buf, i)
            out[k] = v
        return out, i + 1
    if c.isdigit():
        colon = buf.index(b":", i)
        n = int(buf[i:colon])
        return buf[colon + 1:colon + 1 + n], colon + 1 + n
    raise ValueError("不是 bencode")


def meta_of(raw: bytes) -> tuple[str, int]:
    """种子里的显示名和总大小；读不出来返回 ("", 0)。"""
    try:
        d, _ = _bdecode(raw, 0)
        info = d.get(b"info") or {}
        name = info.get(b"name", b"")
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        if b"files" in info:
            size = sum(int(f.get(b"length", 0)) for f in info[b"files"])
        else:
            size = int(info.get(b"length", 0))
        return name, size
    except Exception:  # noqa: BLE001
        return "", 0


def _magnet_hash(uri: str) -> str:
    m = re.search(r"btih:([0-9a-zA-Z]+)", uri or "")
    if not m:
        return ""
    h = m.group(1)
    if len(h) == 32:
        try:
            return base64.b32decode(h.upper()).hex()
        except Exception:  # noqa: BLE001
            return h.lower()
    return h.lower()


# ---------- 索引 ----------
def _load_index() -> dict:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_index(idx: dict) -> None:
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(INDEX)
    except OSError:
        pass


def _entry(ihash: str, **kw) -> dict:
    return {
        "ihash": ihash,
        "name": kw.get("name") or "",
        "size": int(kw.get("size") or 0),
        "gid": kw.get("gid") or "",
        "dir": kw.get("dir") or "",
        "source": kw.get("source") or "",
        "uri": kw.get("uri") or "",
        "kind": kw.get("kind") or "torrent",
        "files": int(kw.get("files") or 0),
        "added_at": float(kw.get("added_at") or time.time()),
        "file": kw["file"] if "file" in kw else ("%s.torrent" % ihash),
    }


def save(gid: str, b64: str, name: str = "", size: int = 0,
         source: str = "", uri: str = "") -> str:
    """存一份种子文件并登记索引，返回 infohash（失败返回空串）。"""
    if not b64:
        return ""
    try:
        raw = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return ""
    ihash = infohash_of(raw) or gid
    if not ihash:
        return ""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        fname = "%s.torrent" % ihash
        path_of(ihash).write_bytes(raw)
    except OSError:
        return ""
    mname, msize = meta_of(raw)
    try:
        d, _ = _bdecode(raw, 0)
        info = d.get(b"info") or {}
        fcount = len(info.get(b"files") or []) or 1
    except Exception:  # noqa: BLE001
        fcount = 1
    idx = _load_index()
    old = idx.get(ihash) or {}
    idx[ihash] = _entry(ihash,
                        name=name or mname or old.get("name", ""),
                        size=size or msize or old.get("size", 0),
                        files=fcount or old.get("files", 0),
                        gid=gid or old.get("gid", ""),
                        dir=old.get("dir", ""),
                        source=source or old.get("source", ""),
                        uri=uri or old.get("uri", ""),
                        added_at=old.get("added_at") or time.time())
    _save_index(idx)
    return ihash


def save_magnet(uri: str, gid: str = "", name: str = "",
                dir_: str = "", size: int = 0, files: int = 0) -> str:
    """磁力没有 .torrent 文件，只登记一条索引（重分饭时重新贴磁力）。"""
    ihash = _magnet_hash(uri)
    if not uri:
        return ""
    key = ihash or ("magnet_" + str(abs(hash(uri))))
    idx = _load_index()
    old = idx.get(key) or {}
    idx[key] = _entry(key, name=name or old.get("name") or ("磁力 · %s" % (ihash[:12] or "BT")),
                      size=size or old.get("size", 0),
                      files=files or old.get("files", 0),
                      gid=gid or old.get("gid", ""),
                      dir=dir_ or old.get("dir", ""), source=old.get("source", "磁力"),
                      uri=uri, kind="magnet",
                      added_at=old.get("added_at") or time.time(),
                      file="")
    _save_index(idx)
    return key


def entries() -> list[dict]:
    """历史种子列表（新→旧）。顺手把旧版只有文件、没有索引的种子补登记。"""
    idx = _load_index()
    changed = False
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        for p in DIR.glob("*.torrent"):
            key = p.stem
            if key in idx:
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            ihash = infohash_of(raw) or key
            name, size = meta_of(raw)
            idx[ihash] = _entry(ihash, name=name, size=size,
                                added_at=p.stat().st_mtime,
                                file=p.name)
            changed = True
    except OSError:
        pass
    if changed:
        _save_index(idx)
    out = list(idx.values())
    out.sort(key=lambda e: float(e.get("added_at") or 0), reverse=True)
    return out


def count() -> int:
    return len(entries())


def remove(ihash: str) -> bool:
    idx = _load_index()
    e = idx.pop(ihash, None)
    if e is None:
        return False
    f = DIR / (e.get("file") or "")
    try:
        if f.name and f.exists():
            f.unlink()
    except OSError:
        pass
    _save_index(idx)
    return True


def get(ihash: str) -> dict | None:
    return _load_index().get(ihash)


def load_b64(key: str) -> str:
    """按 infohash / gid 取回 base64；没有就返回空串。"""
    p = path_of(key)
    if not p.exists():
        idx = _load_index()
        e = idx.get(key)
        if e is None:
            # 老接口按 gid 取（data/torrents/<gid>.torrent 或索引里的 gid 字段）
            e = next((v for v in idx.values() if v.get("gid") == key), None)
        if e and e.get("file"):
            p = DIR / e["file"]
    try:
        if p.exists() and p.is_file():
            return base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        pass
    return ""


def find_by_infohash(ihash: str) -> str:
    """按 infohash 找种子：先查种子库，再扫下载目录里遗留的 .torrent。

    为什么扫下载目录：有些任务是命令行/接口直接加进来的，没走 UI 的留档，
    但 aria2 的 `bt-save-metadata` 会把 .torrent 落在下载目录里；
    「给鱼群分饭」要能从这些文件里把种子捞回来，而不是一句"没留种子"。
    """
    ihash = (ihash or "").lower()
    if not ihash:
        return ""
    hit = load_b64(ihash)
    if hit:
        return hit
    seen_dirs = [DIR]
    try:
        seen_dirs.append(paths.DL_DIR)
    except Exception:  # noqa: BLE001
        pass
    for d in seen_dirs:
        try:
            files = list(d.glob("*.torrent"))
        except OSError:
            continue
        for p in files:
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if infohash_of(raw) == ihash:
                return base64.b64encode(raw).decode("ascii")
    return ""


# ---------- 算 infohash（用来查重 / 认人） ----------
def _skip(buf: bytes, i: int) -> int:
    """跳过 bencode 的一个值，返回下一个字节位置。"""
    c = buf[i:i + 1]
    if c == b"i":
        return buf.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while buf[i:i + 1] != b"e":
            i = _skip(buf, i)
        return i + 1
    if c.isdigit():
        colon = buf.index(b":", i)
        n = int(buf[i:colon])
        return colon + 1 + n
    raise ValueError("不是 bencode")


def info_span(raw: bytes) -> tuple[int, int] | None:
    """找出顶层的 info 字典在原始字节里的区间（infohash 就是对它做 SHA1）。"""
    if raw[:1] != b"d":
        return None
    i = 1
    while i < len(raw) and raw[i:i + 1] != b"e":
        colon = raw.index(b":", i)
        klen = int(raw[i:colon])
        key = raw[colon + 1:colon + 1 + klen]
        vstart = colon + 1 + klen
        vend = _skip(raw, vstart)
        if key == b"info":
            return vstart, vend
        i = vend
    return None


def infohash_of(raw: bytes) -> str:
    """种子的 infohash（40 位 hex）。算不出来就返回空串。"""
    try:
        span = info_span(raw)
        if not span:
            return ""
        import hashlib
        return hashlib.sha1(raw[span[0]:span[1]]).hexdigest()
    except Exception:  # noqa: BLE001
        return ""

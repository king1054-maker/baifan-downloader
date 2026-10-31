# -*- coding: utf-8 -*-
"""验证 addTorrent + 多文件 + select-file 改变勾选。"""
import base64
import hashlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, paths
from app.engine import Aria2Engine


def bencode(o):
    if isinstance(o, int):
        return b"i%de" % o
    if isinstance(o, bytes):
        return b"%d:%s" % (len(o), o)
    if isinstance(o, str):
        return bencode(o.encode())
    if isinstance(o, list):
        return b"l" + b"".join(bencode(x) for x in o) + b"e"
    if isinstance(o, dict):
        out = b"d"
        for k in sorted(o):
            out += bencode(k) + bencode(o[k])
        return out + b"e"
    raise TypeError(type(o))


def make_torrent(name="Demo.Show.S01", files=None, piece_len=16384):
    files = files or [("01.mkv", 40_000), ("02.mkv", 50_000),
                      ("readme.txt", 1200), ("poster.jpg", 8_000)]
    total = sum(sz for _n, sz in files)
    pieces = b"".join(hashlib.sha1(b"\0" * min(piece_len, total)).digest()
                      for _ in range((total + piece_len - 1) // piece_len))
    info = {
        "name": name,
        "piece length": piece_len,
        "pieces": pieces,
        "files": [{"length": sz, "path": [n]} for n, sz in files],
    }
    return bencode({"announce": "http://127.0.0.1:9/announce", "info": info})


def main():
    eng = Aria2Engine(config.load())
    eng.start()
    data = make_torrent()
    b64 = base64.b64encode(data).decode()
    gid = eng.rpc.call("aria2.addTorrent", [b64, [], {
        "dir": str(paths.DL_DIR), "pause": "true"}])
    print("gid", gid)
    time.sleep(1.2)
    st = eng.rpc.call("aria2.tellStatus", [gid, ["files", "status", "totalLength",
                                                 "bittorrent", "dir"]])
    files = st.get("files") or []
    print("status", st.get("status"), "| 文件数", len(files), "| 总大小", st.get("totalLength"))
    for f in files:
        print("  idx=%s selected=%s %8d %s" % (f.get("index"), f.get("selected"),
                                               int(f.get("length") or 0), f.get("path")))
    print("改成只下 1,3 ...")
    eng.rpc.call("aria2.changeOption", [gid, {"select-file": "1,3"}])
    time.sleep(0.5)
    st2 = eng.rpc.call("aria2.tellStatus", [gid, ["files", "totalLength"]])
    for f in st2.get("files") or []:
        print("  idx=%s selected=%s %s" % (f.get("index"), f.get("selected"), f.get("path")))
    print("恢复全选 ...")
    eng.rpc.call("aria2.changeOption", [gid, {"select-file": "1-4"}])
    time.sleep(0.3)
    eng.rpc.call("aria2.removeDownloadResult", [gid])
    eng.rpc.call("aria2.remove", [gid])
    eng.shutdown()
    print("ok")


if __name__ == "__main__":
    main()

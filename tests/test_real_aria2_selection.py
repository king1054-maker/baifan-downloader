"""真实 aria2 动态改选测试（无需外网、无需真实下载）。

构造 10 个 100KB 文件的临时种子，启动本机 aria2 RPC，暂停添加后
用 changeOption 改选 8/9 个文件，再用 getFiles 复核 selected 状态。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARIA2 = ROOT / "bin" / "aria2c.exe"
FILE_SIZE = 1024 * 1024
PIECE = 1024 * 1024


def bencode(obj) -> bytes:
    if isinstance(obj, int):
        return b"i%de" % obj
    if isinstance(obj, bytes):
        return b"%d:%s" % (len(obj), obj)
    if isinstance(obj, str):
        return bencode(obj.encode("utf-8"))
    if isinstance(obj, list):
        return b"l" + b"".join(bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        out = b"d"
        for k in sorted(obj):
            out += bencode(k) + bencode(obj[k])
        return out + b"e"
    raise TypeError(type(obj))


def make_torrent() -> bytes:
    files = []
    pieces = b""
    for i in range(1, 11):
        data = b"\0" * FILE_SIZE
        files.append({"length": FILE_SIZE, "path": ["f%02d.bin" % i]})
        for off in range(0, len(data), PIECE):
            pieces += hashlib.sha1(data[off:off + PIECE]).digest()
    info = {
        "name": "baifan-selection-test",
        "piece length": PIECE,
        "pieces": pieces,
        "files": files,
        "private": 1,
    }
    return bencode({"announce": "http://127.0.0.1:9/announce", "info": info})


class Aria2:
    def __init__(self, exe: Path, work: Path):
        self.port = self._free_port()
        self.proc = subprocess.Popen([
            str(exe), "--enable-rpc", "--rpc-listen-all=false",
            "--rpc-listen-port=%d" % self.port, "--rpc-secret=test",
            "--dir=%s" % work, "--seed-time=0", "--bt-save-metadata=false",
            "--console-log-level=error", "--summary-interval=0",
            "--stop-with-process=%d" % os.getpid(),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.base = "http://127.0.0.1:%d/jsonrpc" % self.port
        self._wait()

    @staticmethod
    def _free_port() -> int:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def _wait(self) -> None:
        end = time.time() + 10
        while time.time() < end:
            try:
                self.call("aria2.getGlobalStat")
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("aria2 RPC 未启动")

    def call(self, method: str, params=None):
        req = {"jsonrpc": "2.0", "id": "t", "method": method,
               "params": ["token:test"] + (params or [])}
        body = json.dumps(req).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            self.base, body, {"Content-Type": "application/json"}), timeout=8)
        data = json.loads(r.read().decode())
        if "error" in data:
            raise RuntimeError(data["error"])
        return data.get("result")

    def close(self) -> None:
        try:
            self.call("aria2.shutdown")
        except Exception:
            pass
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()


class RealSelectionTests(unittest.TestCase):
    @unittest.skipUnless(ARIA2.exists(), "aria2c.exe 不在 bin/")
    def test_real_aria2_select_8_then_9(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            torrent = make_torrent()
            a = Aria2(ARIA2, wd / "dl")
            try:
                gid = a.call("aria2.addTorrent", [
                    base64.b64encode(torrent).decode(), [],
                    {"pause": "true", "dir": str(wd / "dl")}])
                self.assertTrue(gid)
                files = a.call("aria2.getFiles", [gid])
                self.assertEqual(len(files), 10)

                a.call("aria2.changeOption", [gid, {"select-file": "1-8"}])
                files = a.call("aria2.getFiles", [gid])
                selected = [f for f in files if f.get("selected") == "true"]
                self.assertEqual(len(selected), 8)
                self.assertEqual(sum(int(f["length"]) for f in selected),
                                 8 * 1024 * 1024)

                a.call("aria2.changeOption", [gid, {"select-file": "1-9"}])
                files = a.call("aria2.getFiles", [gid])
                selected = [f for f in files if f.get("selected") == "true"]
                self.assertEqual(len(selected), 9)
                self.assertEqual(sum(int(f["length"]) for f in selected),
                                 9 * 1024 * 1024)
            finally:
                a.close()

    @unittest.skipUnless(ARIA2.exists(), "aria2c.exe 不在 bin/")
    def test_real_aria2_active_pause_change_resume(self):
        """真实 active 状态：强制暂停 → 确认 paused → 改选 → unpause。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            torrent = make_torrent()
            a = Aria2(ARIA2, wd / "dl")
            try:
                gid = a.call("aria2.addTorrent", [
                    base64.b64encode(torrent).decode(), [],
                    {"pause": "false", "dir": str(wd / "dl")}])
                self.assertTrue(gid)
                end = time.time() + 10
                status = ""
                while time.time() < end:
                    status = a.call("aria2.tellStatus", [gid, ["status"]])["status"]
                    if status in ("active", "waiting"):
                        break
                    time.sleep(0.1)
                self.assertIn(status, ("active", "waiting"), status)
                a.call("aria2.forcePause", [gid])
                end = time.time() + 5
                while time.time() < end:
                    status = a.call("aria2.tellStatus", [gid, ["status"]])["status"]
                    if status == "paused":
                        break
                    time.sleep(0.1)
                self.assertEqual(status, "paused")
                a.call("aria2.changeOption", [gid, {"select-file": "1-8"}])
                files = a.call("aria2.getFiles", [gid])
                self.assertEqual(sum(f.get("selected") == "true" for f in files), 8)
                a.call("aria2.unpause", [gid])
                self.assertIn(a.call("aria2.tellStatus", [gid, ["status"]])["status"],
                              ("active", "waiting"))
            finally:
                a.close()


if __name__ == "__main__":
    unittest.main()

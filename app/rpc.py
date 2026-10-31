# -*- coding: utf-8 -*-
"""aria2 JSON-RPC 同步客户端。

只连 127.0.0.1，token 在进程内注入，不落日志、不上命令行。
"""
from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request
from typing import Any


class Aria2Error(RuntimeError):
    """RPC 返回的 error 对象，或传输层失败。"""


# aria2 对「同一个种子再下一次」不报错，而是给你一个**新 gid**，
# 然后那个任务立刻变成 error："InfoHash xxx is already registered."
# 不拦的话，列表里就会多出一行红色的「错误」，看着像下载失败。
DUP_HINT = "already registered"


def add_checked(rpc: Aria2Rpc, method: str, params: list[Any]) -> tuple[str | None, str]:
    """建任务，并挡掉 aria2 的"重复添加"。

    返回 (gid, "") 表示建成功；
    返回 (None, "duplicate") 表示这个种子/磁力已经在列表里了（错误行已清掉）。
    RPC 自身的异常照旧往外抛。
    """
    gid = rpc.call(method, params)
    try:
        st = rpc.call("aria2.tellStatus", [gid, ["status", "errorMessage"]])
    except Aria2Error:
        return gid, ""
    if st.get("status") == "error" and DUP_HINT in (st.get("errorMessage") or ""):
        try:
            rpc.call("aria2.removeDownloadResult", [gid])
        except Aria2Error:
            pass
        return None, "duplicate"
    return gid, ""


class Aria2Rpc:
    def __init__(self, port: int, secret: str = "", host: str = "127.0.0.1", timeout: float = 20.0):
        self.url = "http://%s:%d/jsonrpc" % (host, port)
        self.secret = secret
        self.timeout = timeout
        self._ids = itertools.count(1)

    # ---------- 底层 ----------
    def _payload(self, method: str, params: list[Any] | None) -> bytes:
        p = list(params or [])
        if self.secret:
            p = ["token:" + self.secret] + p
        return json.dumps(
            {"jsonrpc": "2.0", "id": "bfg-%d" % next(self._ids), "method": method, "params": p}
        ).encode("utf-8")

    def _post(self, body: bytes) -> dict:
        req = urllib.request.Request(self.url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        raw = b""
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # aria2 对出错的请求仍返回 JSON 错误体，必须读出来才能给出真正原因
            raw = exc.read()
            if not raw:
                raise Aria2Error("RPC HTTP %s" % exc.code) from exc
        except urllib.error.URLError as exc:
            raise Aria2Error("无法连接 aria2 RPC: %s" % exc.reason) from exc
        except OSError as exc:
            raise Aria2Error("RPC 传输失败: %s" % exc) from exc
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise Aria2Error("RPC 返回非 JSON: %r" % raw[:200]) from exc
        if isinstance(obj, dict) and obj.get("error"):
            err = obj["error"]
            raise Aria2Error("%s (code %s)" % (err.get("message", "unknown"), err.get("code")))
        return obj

    # ---------- 公共 API ----------
    def call(self, method: str, params: list[Any] | None = None) -> Any:
        return self._post(self._payload(method, params)).get("result")

    def multicall(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        """System.multicall：一次网络往返拿多份数据。

        返回与 calls 等长的列表；单个子调用失败时对应位置是 {"__err": msg}，
        不会因为一条失败就整批报废。
        """
        # aria2 的 system.multicall 只接受一个参数（methods 数组），
        # token 必须注入到【每个子调用】的 params 首位，而不是外层。
        ops = []
        for m, p in calls:
            pp = list(p or [])
            if self.secret:
                pp = ["token:" + self.secret] + pp
            ops.append({"methodName": m, "params": pp})
        body = json.dumps({"jsonrpc": "2.0", "id": "bfg-mc-%d" % next(self._ids),
                           "method": "system.multicall", "params": [ops]}).encode("utf-8")
        obj = self._post(body)
        if obj.get("error"):
            err = obj["error"]
            raise Aria2Error("%s (code %s)" % (err.get("message", "unknown"), err.get("code")))
        out: list[Any] = []
        for item in obj.get("result") or []:
            if isinstance(item, list) and item:
                out.append(item[0])
            elif isinstance(item, dict) and item.get("faultCode"):
                out.append({"__err": item.get("faultString", "fault")})
            else:
                out.append(item)
        return out

    # ---------- 便捷封装 ----------
    def version(self) -> dict:
        return self.call("aria2.getVersion") or {}

    def global_stat(self) -> dict:
        return self.call("aria2.getGlobalStat") or {}

    def global_option(self) -> dict:
        return self.call("aria2.getGlobalOption") or {}

    def change_global_option(self, opts: dict[str, str]) -> str:
        return self.call("aria2.changeGlobalOption", [opts])

    def ping(self, timeout: float = 1.0) -> bool:
        old, self.timeout = self.timeout, timeout
        try:
            self.version()
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            self.timeout = old

    def tell_all(self) -> dict[str, list[dict]]:
        """active / waiting / stopped 三份快照，用一次 multicall 取回。"""
        keys = [["gid", "status", "totalLength", "completedLength", "uploadLength",
                 "downloadSpeed", "uploadSpeed", "connections", "numSeeders", "seeder",
                 "errorCode", "errorMessage", "dir", "files", "bittorrent",
                 "followedBy", "following", "belongsTo", "verifiedLength",
                 "verifyIntegrityPending", "infoHash"]]
        res = self.multicall([
            ("aria2.tellActive", keys),
            ("aria2.tellWaiting", [0, 1000] + keys),
            ("aria2.tellStopped", [0, 500] + keys),
        ])
        names = ("active", "waiting", "stopped")
        out: dict[str, list[dict]] = {}
        for name, val in zip(names, res):
            out[name] = val if isinstance(val, list) else []
        return out

# -*- coding: utf-8 -*-
"""aria2c 进程托管。

职责：生成配置 → 选端口 → 拉起子进程 → 等 RPC 就绪 → 退出时优雅收尾。
UI 层只和 Aria2Engine.rpc 说话，不直接碰 subprocess。
"""
from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import time
from typing import Any

from . import config, paths
from .rpc import DUP_HINT, Aria2Error, Aria2Rpc

CREATE_NO_WINDOW = 0x08000000


class EngineError(RuntimeError):
    pass


def _clamp(value: Any, lo: int, hi: int) -> int:
    """把用户填的数卡进 aria2 认的范围。

    aria2 对越界值是**拒绝启动**（`--max-connection-per-server=17` 会直接
    exit：must be between 1 and 16），所以哪怕界面已经限了范围，这里也
    要再兜一次——老版本的 settings.json 里可能还留着越界值。
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


class Aria2Engine:
    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or config.load()
        self.port: int = paths.RPC_PORT
        self.secret: str = ""
        self.proc: subprocess.Popen | None = None
        self.rpc: Aria2Rpc | None = None
        self.session_deduped: int = 0    # 启动时从 session 里清掉了几条重复

    # ---------- 准备 ----------
    def _ensure_secret(self) -> str:
        p = paths.SECRET_FILE
        try:
            if p.exists():
                s = p.read_text(encoding="utf-8").strip()
                if len(s) >= 32:
                    return s
        except OSError:
            pass
        s = secrets.token_hex(32)
        p.write_text(s, encoding="utf-8")
        return s

    @staticmethod
    def _port_free(port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _pick_port(self, start: int = paths.RPC_PORT, tries: int = 20) -> int:
        for p in range(start, start + tries):
            if self._port_free(p):
                return p
        raise EngineError("端口 %d-%d 全部被占用，无法启动 aria2 RPC" % (start, start + tries - 1))

    def _render_conf(self) -> None:
        if not paths.CONF_TEMPLATE.exists():
            raise EngineError("缺少配置模板: %s" % paths.CONF_TEMPLATE)
        s = self.settings
        tpl = paths.CONF_TEMPLATE.read_text(encoding="utf-8")
        repl = {
            "DOWNLOAD_DIR": s["download_dir"],
            # 这四个直接决定 aria2 能不能起来，必须卡范围
            "MAX_CONN": _clamp(s["max_conn_per_server"], 1, 16),
            "SPLIT": _clamp(s["split"], 1, 64),
            "MAX_CONC": _clamp(s["max_concurrent"], 1, 20),
            "MAX_DL": s["max_dl"],
            "MAX_UL": s["max_ul"],
            "PER_DL": s["per_task_dl"],
            "PER_UL": s["per_task_ul"],
            "ENABLE_DHT": "true" if s["enable_dht"] else "false",
            "ENABLE_DHT6": "true" if s["enable_dht6"] else "false",
            "BT_MAX_PEERS": _clamp(s["bt_max_peers"], 0, 2000),
            "SEED_TIME": _clamp(s["seed_time"], 0, 525600),
            "SEED_RATIO": max(0.0, float(s["seed_ratio"])),
            "LOG_FILE": str(paths.ENGINE_LOG),
            "DHT_FILE": str(paths.DHT_FILE),
            "DHT6_FILE": str(paths.DHT6_FILE),
            "RPC_PORT": self.port,
            "RPC_SECRET": self.secret,
            "SESSION_FILE": str(paths.SESSION_FILE),
        }
        for k, v in repl.items():
            tpl = tpl.replace("@%s@" % k, str(v))
        if "@" in tpl:
            left = [ln for ln in tpl.splitlines() if "@" in ln and not ln.startswith("#")]
            if left:
                raise EngineError("配置模板有未替换的占位符: %s" % left[0])
        paths.CONF_FILE.write_text(tpl, encoding="utf-8")
        paths.SESSION_FILE.touch(exist_ok=True)

    def _dedupe_session(self) -> int:
        """把 session 文件里重复的条目去掉，返回删掉几条。

        aria2 的 save-session 是**只追加不清**：同一个种子加过几次，就把同一个
        magnet 存几遍。下次启动 aria2 逐条读，第一条正常、后面几条全是
        "InfoHash ... is already registered." 的红色错误 —— 而且这些错误又会被
        存回 session，于是**每次启动都重现一遍，删都删不干净**。

        这里在启动前按"同一个种子只留一条"清一遍。判定：磁力取 btih，
        其他按整行小写。
        """
        p = paths.SESSION_FILE
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        if not raw.strip():
            return 0

        blocks: list[list[str]] = []
        cur: list[str] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            if not line.startswith(" ") and cur:
                blocks.append(cur)
                cur = []
            cur.append(line)
        if cur:
            blocks.append(cur)

        def key_of(block: list[str]) -> str:
            u = block[0].strip()
            low = u.lower()
            if low.startswith("magnet:"):
                m = re.search(r"btih:([0-9a-z]{32,40})", low)
                if m:
                    return "btih:" + m.group(1)
            return low

        seen: set[str] = set()
        kept: list[list[str]] = []
        for b in blocks:
            k = key_of(b)
            if k in seen:
                continue
            seen.add(k)
            kept.append(b)
        dropped = len(blocks) - len(kept)
        if dropped <= 0:
            return 0
        text = "\n".join("\n".join(b) for b in kept) + "\n"
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            return 0
        return dropped

    def _drop_dup_errors(self) -> None:
        """把「这个种子已经在列表里」的红色错误行清掉（历史 session 带进来的）。"""
        if self.rpc is None:
            return
        try:
            stopped = self.rpc.call("aria2.tellStopped",
                                    [0, 200, ["gid", "status", "errorMessage"]])
        except Exception:  # noqa: BLE001
            return
        for t in stopped:
            if t.get("status") == "error" and DUP_HINT in (t.get("errorMessage") or ""):
                try:
                    self.rpc.call("aria2.removeDownloadResult", [t["gid"]])
                except Exception:  # noqa: BLE001
                    pass

    # ---------- 生命周期 ----------
    def start(self, wait: float = 25.0) -> int:
        if not paths.ARIA2_EXE.exists():
            raise EngineError("内置 aria2c.exe 缺失: %s" % paths.ARIA2_EXE)
        self.secret = self._ensure_secret()
        self.port = self._pick_port()
        self._render_conf()
        self.session_deduped = self._dedupe_session()

        log = open(paths.ENGINE_LOG, "ab", buffering=0)  # noqa: SIM115 - 交给子进程持有
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        self.proc = subprocess.Popen(
            [str(paths.ARIA2_EXE), "--conf-path=%s" % paths.CONF_FILE],
            cwd=str(paths.APP_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=CREATE_NO_WINDOW,
            startupinfo=si,
            close_fds=True,
        )
        self.rpc = Aria2Rpc(self.port, self.secret)

        deadline = time.time() + wait
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise EngineError(
                    "aria2 启动即退出（退出码 %s）。常见原因：配置项重复或非法，详见 logs/aria2.log"
                    % self.proc.returncode
                )
            if self.rpc.ping():
                # 历史 session 里带进来的「已经在列表里」错误行，起来之后清掉
                self._drop_dup_errors()
                return self.port
            time.sleep(0.15)
        raise EngineError("aria2 RPC 在 %.0f 秒内未就绪（端口 %d）" % (wait, self.port))

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def shutdown(self, timeout: float = 8.0) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None and self.rpc is not None:
            try:
                self.rpc.call("aria2.shutdown")
            except Exception:  # noqa: BLE001
                pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            self.proc.terminate()
            self.proc.wait(3)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 设置热更新 ----------
    HOT_KEYS = {
        "max_concurrent": "max-concurrent-downloads",
        "max_conn_per_server": "max-connection-per-server",
        "split": "split",
        "bt_max_peers": "bt-max-peers",
    }

    def apply_hot(self) -> None:
        """把可热改的设置推到运行中的 aria2（无需重启）。"""
        if not self.rpc:
            return
        opts = {eng: str(self.settings[key]) for key, eng in self.HOT_KEYS.items()}
        opts["max-overall-download-limit"] = self._limit(self.settings["max_dl"])
        opts["max-overall-upload-limit"] = self._limit(self.settings["max_ul"])
        opts["max-download-limit"] = self._limit(self.settings["per_task_dl"])
        opts["max-upload-limit"] = self._limit(self.settings["per_task_ul"])
        try:
            self.rpc.change_global_option(opts)
        except Aria2Error:
            pass

    @staticmethod
    def _limit(v: Any) -> str:
        v = int(v or 0)
        return "0" if v <= 0 else str(v)

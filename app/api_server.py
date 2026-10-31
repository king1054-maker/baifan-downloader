# -*- coding: utf-8 -*-
"""本机 AI 接口（HTTP）。

为什么要它：让任何 AI / 脚本 / 自动化流程都能在**一条 HTTP 请求**里完成
"投递任务 → 等它真下完 → 拿到完整性结论"，不必自己去装 aria2、解析 RPC、
也不必模拟鼠标点比特彗星。

约定：
  * 只绑 127.0.0.1，端口 6811
  * 所有请求带 X-Auth-Token（值在 conf/api-token.txt），也支持 ?token= 与 Bearer
  * 统一返回 JSON，失败一律 {"ok": false, "error": {...}}
  * GET /api/v1/describe 返回自述文档，AI 读一次就知道怎么用
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import brand, integrity, intents, paths
from . import dedupe, torrent_keep
from .rpc import Aria2Error, Aria2Rpc, add_checked

VERSION = brand.APP_VERSION
DEFAULT_PORT = paths.API_PORT


def ensure_token() -> str:
    p = paths.API_TOKEN_FILE
    try:
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if len(t) >= 24:
                return t
    except OSError:
        pass
    t = secrets.token_hex(24)
    p.write_text(t, encoding="utf-8")
    return t


# ---------------- 序列化 ----------------
def _name_of(d: dict) -> str:
    bt = d.get("bittorrent") or {}
    if bt.get("info", {}).get("name"):
        return bt["info"]["name"]
    files = d.get("files") or []
    if files:
        p = files[0].get("path", "")
        base = os.path.basename(p.replace("\\", "/").rstrip("/"))
        return base or p
    return ""


def task_json(d: dict, meta: dict, verify: dict | None = None) -> dict:
    files = d.get("files") or []
    total = int(d.get("totalLength") or 0)
    done = int(d.get("completedLength") or 0)
    status = d.get("status", "")
    name = _name_of(d)
    base_dir = d.get("dir", "")

    single = len(files) == 1
    if single and files:
        main_path = files[0].get("path", "")
    else:
        main_path = str(Path(base_dir) / name) if name else base_dir

    kind = intents.kind_of(main_path) if main_path else "other"
    if not single:
        kind = "folder"

    dl = int(d.get("downloadSpeed") or 0)
    eta = None
    if status == "active" and dl > 0 and total > done:
        eta = int((total - done) / dl)

    err = ""
    if d.get("errorCode") and d["errorCode"] != "0":
        err = d.get("errorMessage") or "错误码 %s" % d["errorCode"]

    out = {
        "gid": d.get("gid"),
        "name": name,
        "status": status if not err else "error",
        "raw_status": status,
        "size": total,
        "completed": done,
        "percent": round(done * 100.0 / total, 2) if total else 0.0,
        "down_speed": dl,
        "up_speed": int(d.get("uploadSpeed") or 0),
        "seeders": int(d.get("numSeeders") or 0),
        "connections": int(d.get("connections") or 0),
        "eta_seconds": eta,
        "is_bittorrent": bool(d.get("infoHash")),
        # 磁力的「先下种子」阶段：不是真任务，别当下载进度看
        "is_metadata": bool(d.get("followedBy"))
        or bool(d.get("bittorrent")) and not ((d.get("bittorrent") or {}).get("info") or {}).get("name"),
        "info_hash": d.get("infoHash"),
        "dir": base_dir,
        "path": main_path,
        "kind": kind,
        "file_count": len(files),
        "files": [
            {
                "path": f.get("path", ""),
                "length": int(f.get("length") or 0),
                "completed": int(f.get("completedLength") or 0),
                "selected": f.get("selected") == "true",
            }
            for f in files[:200]
        ],
        "error": err,
        "added_at": (meta or {}).get("added_at"),
        "uri": (meta or {}).get("uri", ""),
        "memo": (meta or {}).get("memo", ""),
    }
    if verify is not None:
        out["verify"] = verify
    return out


# ---------------- 服务 ----------------
class ApiContext:
    def __init__(self, rpc: Aria2Rpc, store, settings: dict):
        self.rpc = rpc
        self.store = store
        self.settings = settings
        self.token = ensure_token()
        self.verify_cache: dict[str, dict] = {}
        self.started_at = time.time()
        self._lock = threading.Lock()

    # 供 UI 复用：任务完成时后台校验
    def verify_gid(self, gid: str) -> dict:
        try:
            st = self.rpc.call("aria2.tellStatus", [gid, ["files", "dir", "status", "errorCode"]])
        except Aria2Error as exc:
            return {"ok": False, "error": "无法读取任务: %s" % exc}
        if st.get("status") != "complete":
            return {"ok": False, "error": "任务尚未完成（%s）" % st.get("status")}
        report = integrity.check_task(st.get("files") or [], st.get("dir", ""))
        report["checked_at"] = time.time()
        with self._lock:
            self.verify_cache[gid] = report
        return report

    def cached_verify(self, gid: str) -> dict | None:
        with self._lock:
            return self.verify_cache.get(gid)


class _Handler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (brand.APP_ID, VERSION)
    protocol_version = "HTTP/1.1"

    # ---- 基础 ----
    def log_message(self, fmt, *args):  # 静默
        pass

    @property
    def ctx(self) -> ApiContext:
        return self.server.ctx  # type: ignore[attr-defined]

    def _send(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: str, message: str, status: int = 400) -> None:
        self._send({"ok": False, "error": {"code": code, "message": message}}, status)

    def _authed(self, qs: dict) -> bool:
        tok = (self.headers.get("X-Auth-Token") or "").strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
        if not tok:
            tok = (qs.get("token") or [""])[0]
        return bool(tok) and tok == self.ctx.token

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    # ---- 路由 ----
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_DELETE(self):  # noqa: N802
        self._route("DELETE")

    def _route(self, method: str) -> None:
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        qs = parse_qs(u.query)
        parts = [unquote(p) for p in path.split("/") if p]

        if path in ("/", "/api", "/api/v1/describe"):
            return self._describe()
        if path == "/openapi.json":
            return self._openapi()
        if path == "/api/v1/guide":
            return self._guide()
        if path == "/health":
            return self._send({"ok": True, "version": VERSION,
                               "uptime": int(time.time() - self.ctx.started_at)})

        if not self._authed(qs):
            return self._err("unauthorized", "缺少或错误的 X-Auth-Token，值见 conf/api-token.txt", 401)

        try:
            if path == "/api/v1/status":
                return self._status()
            if path == "/api/v1/tasks" and method == "GET":
                return self._list_tasks(qs)
            if path == "/api/v1/tasks" and method == "POST":
                return self._add_task()
            if len(parts) == 4 and parts[:3] == ["api", "v1", "tasks"]:
                return self._task_action(method, parts[3], qs)
            if len(parts) == 5 and parts[:3] == ["api", "v1", "tasks"]:
                return self._task_action(method, parts[3], qs, action=parts[4])
            if len(parts) == 4 and parts[:3] == ["api", "v1", "wait"]:
                return self._wait(parts[3], qs)
            if path == "/api/v1/limit" and method == "POST":
                return self._limit()
            return self._err("not_found", "没有这个接口: %s" % path, 404)
        except Aria2Error as exc:
            return self._err("aria2_error", str(exc), 502)
        except Exception as exc:  # noqa: BLE001
            return self._err("internal", "%s: %s" % (type(exc).__name__, exc), 500)

    # ---- 各接口 ----
    def _describe(self) -> None:
        self._send({
            "ok": True,
            "name": brand.APP_NAME,
            "version": VERSION,
            "auth": {
                "header": "X-Auth-Token",
                "token_file": str(paths.API_TOKEN_FILE),
                "also_accepts": "?token=... 或 Authorization: Bearer <token>",
            },
            "usage_for_ai": [
                "1. POST /api/v1/tasks  {\"urls\":[\"magnet:?xt=...\"]}  → 返回 gid（批量见 batch）",
                "2. GET  /api/v1/wait/<gid>?timeout=1800  → 阻塞直到下完，返回 verify 结果",
                "3. 若 verify.ok 为 false，任务文件不完整，不要交付给用户",
                "4. 想看完整人话版说明：GET /api/v1/guide",
            ],
            "batch": {
                "一句话": "要一次下多个文件，就一个请求塞多个链接，不要循环发请求。",
                "推荐写法": {
                    "POST /api/v1/tasks": {
                        "urls": ["http://example.com/a.mkv",
                                 "http://example.com/b.zip",
                                 "magnet:?xt=urn:btih:..."],
                        "dir": str(paths.DL_DIR),
                    }
                },
                "文本写法": {
                    "endpoint": "POST /api/v1/tasks",
                    "body": {"text": "链接1 链接2\n链接3", "dir": str(paths.DL_DIR)},
                    "规则": "服务端把 text 按空白切开（1 个及以上空格、换行、制表符都算），"
                            "再逐条识别磁力/http/ftp/本地种子路径，能认多少建多少",
                },
                "返回": "gids 是本次建出来的全部任务；gid 是第一个；count 是数量",
                "注意": [
                    "urls 里每个字符串也会再按空白切一次，所以 \"a b\" 等于两个任务",
                    "重复链接会自动去重",
                    "全部链接会共用同一个 dir / 限速 / 暂停 等参数",
                ],
            },
            "endpoints": {
                "GET  /health": "存活检查，无需 token",
                "GET  /api/v1/status": "引擎版本、全局速度、任务计数",
                "GET  /api/v1/tasks": "任务列表 ?status=all|active|waiting|stopped&verify=1",
                "POST /api/v1/tasks": "新建任务 {urls|text|torrent_base64, dir, pause, download_limit, upload_limit, memo}——可批量",
                "GET  /api/v1/guide": "给 AI 看的完整说明（Markdown 纯文本）",
                "GET  /api/v1/tasks/<gid>": "单任务详情",
                "POST /api/v1/tasks/<gid>/pause": "暂停",
                "POST /api/v1/tasks/<gid>/resume": "继续",
                "POST /api/v1/tasks/<gid>/verify": "强制完整性校验",
                "POST /api/v1/tasks/<gid>/memo": "写备注 {text}（空串=清除），会显示在任务行里",
                "DELETE /api/v1/tasks/<gid>?files=1": "删除任务（files=1 同时删文件，进回收站）",
                "GET  /api/v1/wait/<gid>?timeout=600&verify=1": "等待完成并校验",
                "POST /api/v1/limit": "全局限速 {download_kb, upload_kb}",
            },
            "verify": {
                "meaning": "下载完成 ≠ 文件完整。verify.ok 才是唯一可信结论。",
                "checks": ["字节数与 aria2 上报一致", "视频容器结构（MP4 moov / MKV EBML）",
                           "压缩包与 PDF 尾部完整性", "图片文件结束标记"],
            },
            "notes": [
                "所有速度单位为 Byte/s；percent 为 0-100",
                "status: active / waiting / paused / complete / error / removed",
                "kind: video / image / audio / folder / exec / archive / other",
                "memo: 一句人话备注（文件名看不懂时用），不动文件本身；POST /api/v1/tasks/<gid>/memo 可写",
                "多文件任务（kind=folder）的 path 指向任务根目录",
            ],
        })

    def _guide(self) -> None:
        """给 AI 读的完整说明书（Markdown）。内容存在 assets/ai-guide.md，改文案不用改代码。"""
        f = paths.ASSET_DIR / "ai-guide.md"
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            text = "# %s API\n\nPOST /api/v1/tasks {\"urls\":[...]}\n" % brand.APP_NAME
        # 把占位符换成本机真实值，AI 直接照抄就能跑
        text = (text.replace("@PORT@", str(self.server.server_address[1]))
                    .replace("@TOKEN_FILE@", str(paths.API_TOKEN_FILE))
                    .replace("@DOWNLOAD_DIR@", str(paths.DL_DIR))
                    .replace("@VERSION@", VERSION))
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _openapi(self) -> None:
        self._send({
            "openapi": "3.0.0",
            "info": {"title": brand.APP_NAME + " API", "version": VERSION},
            "servers": [{"url": "http://127.0.0.1:%d" % self.server.server_address[1]}],
            "paths": {
                "/api/v1/tasks": {
                    "get": {"summary": "任务列表"},
                    "post": {"summary": "新建任务",
                             "requestBody": {"content": {"application/json": {"schema": {
                                 "type": "object",
                                 "properties": {
                                     "urls": {"type": "array", "items": {"type": "string"}},
                                     "dir": {"type": "string"},
                                     "pause": {"type": "boolean"},
                                     "download_limit": {"type": "integer", "description": "Byte/s，0=不限"},
                                     "upload_limit": {"type": "integer"},
                                     "memo": {"type": "string", "description": "备注（仅单任务时生效）"},
                                 }}}}}},
                },
                "/api/v1/tasks/{gid}": {"get": {"summary": "任务详情"}},
                "/api/v1/tasks/{gid}/pause": {"post": {"summary": "暂停"}},
                "/api/v1/tasks/{gid}/resume": {"post": {"summary": "继续"}},
                "/api/v1/tasks/{gid}/verify": {"post": {"summary": "完整性校验"}},
                "/api/v1/tasks/{gid}/memo": {"post": {"summary": "写备注（text，空串=清除）"}},
                "/api/v1/wait/{gid}": {"get": {"summary": "等待任务完成"}},
            },
            "components": {"securitySchemes": {"token": {"type": "apiKey", "in": "header",
                                                         "name": "X-Auth-Token"}}},
        })

    def _status(self) -> None:
        stat = self.ctx.rpc.global_stat()
        ver = self.ctx.rpc.version()
        self._send({
            "ok": True,
            "version": VERSION,
            "aria2": ver.get("version"),
            "download_speed": int(stat.get("downloadSpeed") or 0),
            "upload_speed": int(stat.get("uploadSpeed") or 0),
            "num_active": int(stat.get("numActive") or 0),
            "num_waiting": int(stat.get("numWaiting") or 0),
            "num_stopped": int(stat.get("numStopped") or 0),
            "default_dir": self.ctx.settings.get("download_dir"),
        })

    def _list_tasks(self, qs: dict) -> None:
        snap = self.ctx.rpc.tell_all()
        want = (qs.get("status") or ["all"])[0]
        meta = self.ctx.store.all()
        items = []
        for bucket in ("active", "waiting", "stopped"):
            if want not in ("all", bucket):
                continue
            for d in snap.get(bucket, []):
                gid = d.get("gid", "")
                v = self.ctx.cached_verify(gid) if (qs.get("verify") or ["0"])[0] in ("1", "true") else None
                items.append(task_json(d, meta.get(gid, {}), v))
        self._send({"ok": True, "count": len(items), "tasks": items})

    def _add_task(self) -> None:
        body = self._body()
        urls = body.get("urls") or body.get("url") or []
        if isinstance(urls, str):
            urls = [urls]
        # 「至少一个空格」也算分隔符：一条字符串里塞好几个链接也能拆开
        flat: list[str] = []
        for u in urls:
            if isinstance(u, str):
                flat.extend(u.split())
        urls = [u for u in dict.fromkeys(flat) if u]

        # text：整段分享文案 / 空格分隔的链接，交给同一套识别器
        torrents: list[str] = []
        text = body.get("text")
        if isinstance(text, str) and text.strip():
            for ln in intents.extract_links(text):
                if ln.kind == "torrent":
                    try:
                        torrents.append(base64.b64encode(
                            Path(ln.value).read_bytes()).decode())
                    except OSError:
                        pass
                elif ln.value not in urls:
                    urls.append(ln.value)

        torrent = body.get("torrent_base64") or body.get("torrent")
        if torrent:
            torrents.insert(0, str(torrent))
        if not urls and not torrents:
            return self._err("bad_request", "需要 urls 数组、text 文本 或 torrent_base64")

        s = self.ctx.settings
        d = body.get("dir") or s.get("download_dir") or str(paths.DL_DIR)
        Path(d).mkdir(parents=True, exist_ok=True)

        opts: dict[str, str] = {"dir": d}
        if body.get("pause"):
            opts["pause"] = "true"
        dl = body.get("download_limit", s.get("per_task_dl", 0))
        ul = body.get("upload_limit", s.get("per_task_ul", 0))
        if int(dl or 0) > 0:
            opts["max-download-limit"] = str(int(dl))
        if int(ul or 0) > 0:
            opts["max-upload-limit"] = str(int(ul))
        extra = body.get("options") or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
                    opts[k] = str(v).lower() if isinstance(v, bool) else str(v)

        gids: list[str] = []
        dupes = 0
        # 给 AI 的查重提示：以前下过就顺手告诉它一声，不拦（AI 可能在补下载）
        history: list[str] = []
        metas = self.ctx.store.all()
        for u in urls:
            hit = dedupe.find_previous(metas, ihash=dedupe.magnet_hash(u), uri=u)
            if hit:
                history.append(dedupe.describe(hit[1]))
        # 一条链接 = 一个任务。不能像 addUri 默认那样把多条当成同一个文件的镜像，
        # 那样批量就没有意义了（3 个链接只会下出来 1 个文件）。
        # add_checked 顺手挡掉「同一个种子再下一次」——那种情况 aria2 会返回一个
        # 立刻报错的新 gid，列表里就多出一行红字，看着像下载失败。
        for u in urls:
            try:
                gid, why = add_checked(self.ctx.rpc, "aria2.addUri", [[u], opts])
            except Aria2Error:
                continue
            if not gid:
                dupes += 1
                continue
            self.ctx.store.note(gid, uri=u)
            gids.append(gid)
        for b64 in torrents:
            try:
                gid, why = add_checked(self.ctx.rpc, "aria2.addTorrent", [b64, [], opts])
            except Aria2Error:
                continue
            if not gid:
                dupes += 1
                continue
            torrent_keep.save(gid, b64)      # 留档，之后才能"再分一轮饭"
            self.ctx.store.note(gid, uri="(torrent)", name=body.get("name", ""))
            gids.append(gid)
        if not gids:
            if dupes:
                return self._err("duplicate",
                                 "这个任务已经在列表里了，不用重复添加", 409)
            return self._err("add_failed", "所有链接都没能建出任务", 502)
        # 建任务时顺手带备注（只在建出 1 个任务时生效；批量下每碗饭不一样，别硬套）
        if len(gids) == 1 and str(body.get("memo") or "").strip():
            self.ctx.store.set_memo(gids[0], str(body["memo"]))
        self.ctx.store.save()

        gid = gids[0]
        try:
            st = self.ctx.rpc.call("aria2.tellStatus", [gid])
        except Aria2Error:
            st = {"gid": gid}
        self._send({"ok": True, "gid": gid, "gids": gids,
                    "count": len(gids), "duplicates": dupes,
                    "already_downloaded": history,
                    "task": task_json(st, self.ctx.store.get(gid))}, 201)

    def _task_action(self, method: str, gid: str, qs: dict, action: str = "") -> None:
        rpc = self.ctx.rpc
        if method == "GET" and not action:
            st = rpc.call("aria2.tellStatus", [gid])
            return self._send({"ok": True,
                               "task": task_json(st, self.ctx.store.get(gid),
                                                 self.ctx.cached_verify(gid))})

        if method == "POST" and action == "pause":
            rpc.call("aria2.forcePause", [gid])
            return self._send({"ok": True, "gid": gid, "status": "paused"})
        if method == "POST" and action == "resume":
            rpc.call("aria2.unpause", [gid])
            return self._send({"ok": True, "gid": gid, "status": "active"})
        if method == "POST" and action == "verify":
            return self._send({"ok": True, "gid": gid, "verify": self.ctx.verify_gid(gid)})
        if method == "POST" and action == "memo":
            # 备注：给「看不懂文件名」的任务写一句人话。空字符串 = 清除。
            body = self._body()
            text = body.get("text", body.get("memo", ""))
            saved = self.ctx.store.set_memo(gid, str(text or ""))
            self.ctx.store.save()
            return self._send({"ok": True, "gid": gid, "memo": saved})
        if method == "DELETE" and not action:
            delete_files = (qs.get("files") or ["0"])[0] in ("1", "true")
            st = rpc.call("aria2.tellStatus", [gid, ["files", "dir", "status"]])
            files = [f.get("path") for f in (st.get("files") or [])]
            try:
                if st.get("status") in ("active", "waiting", "paused"):
                    rpc.call("aria2.forceRemove", [gid])
                rpc.call("aria2.removeDownloadResult", [gid])
            except Aria2Error:
                pass
            if delete_files:
                _recycle(files)
            self.ctx.store.forget([gid])
            self.ctx.store.save()
            return self._send({"ok": True, "gid": gid, "deleted_files": delete_files})
        return self._err("bad_action", "不支持的操作: %s %s" % (method, action), 405)

    def _wait(self, gid: str, qs: dict) -> None:
        timeout = min(3600, max(1, int((qs.get("timeout") or ["600"])[0])))
        do_verify = (qs.get("verify") or ["1"])[0] in ("1", "true")
        interval = float((qs.get("interval") or ["1"])[0])
        deadline = time.time() + timeout

        while time.time() < deadline:
            st = self.ctx.rpc.call("aria2.tellStatus", [gid])
            status = st.get("status")
            if status in ("complete", "error", "removed"):
                task = task_json(st, self.ctx.store.get(gid))
                if status == "complete" and do_verify:
                    task["verify"] = self.ctx.verify_gid(gid)
                return self._send({"ok": status == "complete", "done": True, "task": task})
            time.sleep(interval)

        st = self.ctx.rpc.call("aria2.tellStatus", [gid])
        return self._send({"ok": False, "done": False, "timeout": timeout,
                           "task": task_json(st, self.ctx.store.get(gid))}, 202)

    def _limit(self) -> None:
        body = self._body()
        opts = {}
        if "download_kb" in body:
            opts["max-overall-download-limit"] = str(int(body["download_kb"]) * 1024)
        if "upload_kb" in body:
            opts["max-overall-upload-limit"] = str(int(body["upload_kb"]) * 1024)
        if not opts:
            return self._err("bad_request", "需要 download_kb 或 upload_kb")
        self.ctx.rpc.change_global_option(opts)
        self._send({"ok": True, "applied": opts})


def _recycle(paths_: list[str]) -> None:
    """删除到回收站，不做永久删除。"""
    if not paths_:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = SHFILEOPSTRUCTW()
        op.wFunc = 3  # FO_DELETE
        op.pFrom = "\0".join(paths_) + "\0\0"
        op.fFlags = 0x0040 | 0x0010 | 0x0004  # ALLOWUNDO | NOCONFIRMATION | SILENT
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:  # noqa: BLE001
        pass


class ApiServer:
    def __init__(self, rpc: Aria2Rpc, store, settings: dict, port: int = DEFAULT_PORT):
        self.ctx = ApiContext(rpc, store, settings)
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> int:
        last: Exception | None = None
        for p in range(self.port, self.port + 12):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", p), _Handler)
                httpd.daemon_threads = True
                httpd.ctx = self.ctx  # type: ignore[attr-defined]
                self.httpd = httpd
                self.port = p
                self.thread = threading.Thread(target=httpd.serve_forever,
                                               name="bfg-api", daemon=True)
                self.thread.start()
                return p
            except OSError as exc:
                last = exc
        raise RuntimeError("API 端口绑定失败: %s" % last)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

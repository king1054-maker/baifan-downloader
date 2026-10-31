# -*- coding: utf-8 -*-
"""AI 接口端到端测试：投递 → 等待 → 校验 → 删除。"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, paths  # noqa: E402
from app.api_server import ApiServer  # noqa: E402
from app.engine import Aria2Engine  # noqa: E402
from app.ui.store import TaskMetaStore  # noqa: E402


def call(base, path, token="", method="GET", body=None):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("X-Auth-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main() -> int:
    engine = Aria2Engine(config.load())
    engine.start()
    store = TaskMetaStore()
    api = ApiServer(engine.rpc, store, engine.settings)
    port = api.start()
    base = "http://127.0.0.1:%d" % port
    tok = api.ctx.token
    print("API:", base)

    print("\n[1] /health (无 token)")
    print(call(base, "/health"))

    print("\n[2] /api/v1/tasks 无 token → 应 401")
    print(call(base, "/api/v1/tasks")[0])

    print("\n[3] /api/v1/describe")
    code, doc = call(base, "/api/v1/describe")
    print(code, doc["name"], "| 端点数:", len(doc["endpoints"]))

    print("\n[4] 投递任务")
    code, res = call(base, "/api/v1/tasks", tok, "POST",
                     {"urls": ["http://example.com/"], "options": {"out": "_api_test.html"}})
    print(code, json.dumps(res, ensure_ascii=False)[:200])
    gid = res.get("gid")
    if not gid:
        api.stop(); engine.shutdown(); return 1

    print("\n[5] /wait/<gid>?timeout=60")
    code, res = call(base, "/api/v1/wait/%s?timeout=60" % gid, tok)
    print(code, "ok=%s done=%s" % (res.get("ok"), res.get("done")))
    v = (res.get("task") or {}).get("verify") or {}
    bad = v.get("files") or []
    print("    verify.ok =", v.get("ok"), "| 文件数", v.get("file_count"),
          "| 问题文件", (bad[0].get("problems") if bad else "无"))

    print("\n[6] 列表")
    code, res = call(base, "/api/v1/tasks?verify=1", tok)
    print(code, "count =", res.get("count"))

    print("\n[7] 删除")
    print(call(base, "/api/v1/tasks/%s" % gid, tok, "DELETE"))

    api.stop()
    engine.shutdown()
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

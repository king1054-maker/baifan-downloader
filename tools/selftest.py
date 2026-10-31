# -*- coding: utf-8 -*-
"""引擎层自检：启动 aria2 → 取版本 → 下一个小文件 → 清理 → 退出。

用法: python tools/selftest.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, paths  # noqa: E402
from app.engine import Aria2Engine  # noqa: E402


def main() -> int:
    print("程序目录:", paths.APP_ROOT)
    print("内置 aria2:", paths.ARIA2_EXE, "存在" if paths.ARIA2_EXE.exists() else "缺失")

    s = config.load()
    eng = Aria2Engine(s)
    eng.start()
    print("RPC 端口:", eng.port)
    ver = eng.rpc.version()
    print("aria2 版本:", ver.get("version"), "| 已启用功能:", ",".join(ver.get("enabledFeatures", [])[:6]))

    gid = eng.rpc.call(
        "aria2.addUri",
        [["http://example.com/"],
         {"dir": str(paths.DL_DIR), "out": "_selftest.html", "max-download-limit": "20K"}],
    )
    print("已投递任务 GID:", gid)
    time.sleep(3)

    snap = eng.rpc.tell_all()
    print("globalStat:", eng.rpc.global_stat())
    print("bucket sizes:", {k: len(v) for k, v in snap.items()})
    for bucket, items in snap.items():
        for it in items:
            name = (it.get("files") or [{}])[0].get("path", "?")
            print("  [%s] %s  %s/%s  %s B/s" % (
                bucket, Path(name).name, it.get("completedLength"),
                it.get("totalLength"), it.get("downloadSpeed")))

    try:
        eng.rpc.call("aria2.forceRemove", [gid])
    except Exception as exc:  # noqa: BLE001
        print("清理任务失败(可忽略):", exc)

    eng.shutdown()
    print("引擎已关闭。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

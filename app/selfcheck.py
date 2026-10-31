# -*- coding: utf-8 -*-
"""程序自身完整性自检。

用户提的问题：装到另一台电脑上，程序能不能确定是"齐的"。
这里在启动时验证：内置 aria2c 可执行、配置模板存在、图标资源存在、
目录可写。任何一项缺失都明确报出来，而不是等下载时才莫名失败。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import paths


def _can_exec(p: Path) -> tuple[bool, str]:
    if not p.exists():
        return False, "文件缺失"
    try:
        r = subprocess.run([str(p), "--version"], capture_output=True, timeout=10,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode != 0:
            return False, "退出码 %s" % r.returncode
        return True, (r.stdout or b"").decode("utf-8", "replace").splitlines()[0] if r.stdout else "ok"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def run() -> dict:
    problems: list[str] = []
    info: list[str] = []

    ok, detail = _can_exec(paths.ARIA2_EXE)
    if ok:
        info.append("aria2c: %s" % detail)
    else:
        problems.append("内置 aria2c.exe 不可用（%s）：%s" % (detail, paths.ARIA2_EXE))

    for label, p in (
        ("配置模板", paths.CONF_TEMPLATE),
        ("程序图标", paths.ASSET_DIR / "icons" / "whale.ico"),
        # 立绘三张是必用的：日常招呼 / 下载中半张脸 / 刚下完没吃饱
        ("桌宠立绘（日常）", paths.ASSET_DIR / "pet" / "ciallo.png"),
        ("桌宠立绘（下载中）", paths.ASSET_DIR / "pet" / "half.png"),
        ("桌宠立绘（刚下完）", paths.ASSET_DIR / "pet" / "tease.png"),
    ):
        if not p.exists():
            problems.append("%s缺失：%s" % (label, p))

    # 提示音：默认优先自制 Ciallo 声音，缺少时退回 ding.wav
    sounds = sorted((paths.ASSET_DIR / "sounds").glob("*.*"))
    if not sounds:
        problems.append("提示音全部缺失：%s" % (paths.ASSET_DIR / "sounds"))

    for label, d in (("配置目录", paths.CONF_DIR), ("数据目录", paths.DATA_DIR),
                     ("日志目录", paths.LOG_DIR), ("下载目录", paths.DL_DIR)):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
        except OSError as exc:
            problems.append("%s不可写（%s）：%s" % (label, exc, d))
            continue
        # 删不掉不等于不能写：杀毒软件、沙箱、同步盘都可能短暂锁住刚写的文件。
        # 这里失败只留下一个 2 字节的空壳，不影响下载，不该报「目录不可写」。
        try:
            probe.unlink()
        except OSError:
            pass

    info.append("提示音: %d 个" % len(sounds))

    return {"ok": not problems, "problems": problems, "info": info,
            "root": str(paths.APP_ROOT), "python": sys.version.split()[0]}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))


def run_runtime(api_port: int | None = None, guide_text: str = "",
                engine=None, token_path: Path | None = None,
                download_dir: Path | None = None) -> dict:
    """运行期自检：不只查文件在不在，还查"本机真实值"有没有接上。"""
    token_path = token_path or paths.API_TOKEN_FILE
    download_dir = download_dir or paths.DL_DIR
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    base = run()
    add("安装文件齐全", base["ok"],
        "；".join(base["problems"]) if base["problems"] else "aria2c / 图标 / 配置模板 / 目录都可写")

    guide = paths.ASSET_DIR / "ai-guide.md"
    add("AI 说明文件存在", guide.exists(), str(guide))
    if guide_text:
        left = re.findall(r"@[A-Z_]+@", guide_text)
        add("说明占位符全部替换", not left, "残留 %s" % left if left else "端口/令牌/下载目录/版本都换成当前值")
        bad = [s for s in ("C:\\Users\\", "C:/Users/", "D:/Download") if s in guide_text]
        add("说明没有写死别的机器路径", not bad, "命中 %s" % bad if bad else "无本机专属硬编码")
        add("说明里带当前下载目录", str(download_dir) in guide_text, str(download_dir))
    else:
        add("AI 说明已渲染", False, "拿不到渲染后的说明")

    try:
        tok = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        tok = ""
        add("API 令牌文件可读", False, "%s (%s)" % (token_path, exc))
    else:
        add("API 令牌文件可读", bool(tok), "长度 %d" % len(tok))
        add("API 令牌长度足够", len(tok) >= 24, "%d 位" % len(tok))

    add("API 端口已分配", bool(api_port and 1 <= int(api_port) <= 65535), str(api_port))

    try:
        download_dir.mkdir(parents=True, exist_ok=True)
        add("下载目录存在/可创建", download_dir.is_dir(), str(download_dir))
    except OSError as exc:
        add("下载目录存在/可创建", False, "%s (%s)" % (download_dir, exc))

    tasks = paths.DATA_DIR / "tasks.json"
    if tasks.exists():
        try:
            json.loads(tasks.read_text(encoding="utf-8"))
            add("任务记录可解析", True, str(tasks))
        except (OSError, ValueError) as exc:
            add("任务记录可解析", False, str(exc))
    else:
        add("任务记录（首次启动可不建）", True, "尚未创建")

    if engine is not None:
        alive = bool(engine.alive())
        add("aria2 引擎进程在跑", alive, "pid=%s" % getattr(engine.proc, "pid", "?"))
        try:
            ver = engine.rpc.call("aria2.getVersion") if engine.rpc else {}
            add("aria2 RPC 可响应", isinstance(ver, dict) and bool(ver.get("version")),
                str(ver.get("version") if isinstance(ver, dict) else ver))
        except Exception as exc:  # noqa: BLE001
            add("aria2 RPC 可响应", False, str(exc))

    # 换目录重装后，旧的开机自启可能还指向旧 exe —— 这是最隐蔽的"绑定旧机器/旧目录"
    try:
        import winreg

        from . import shell_integration as si
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, si.RUN_KEY) as k:
                target = str(winreg.QueryValueEx(k, si.RUN_VALUE)[0])
        except FileNotFoundError:
            target = ""
        want = si.startup_command()
        same = (not target) or os.path.normcase(target).strip() == os.path.normcase(want).strip()
        add("开机自启指向当前安装", same,
            target if target else "未设置（不会自启）")
    except Exception as exc:  # noqa: BLE001
        add("开机自启指向当前安装", False, str(exc))

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks, "report": format_report(checks)}


def format_report(checks: list[dict]) -> str:
    lines = []
    for c in checks:
        lines.append("%s %s  %s" % ("✓" if c["ok"] else "✗", c["name"], c["detail"]))
    return "\n".join(lines)

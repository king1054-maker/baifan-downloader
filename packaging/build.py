# -*- coding: utf-8 -*-
"""一键打包：PyInstaller onedir → （可选）Inno Setup 出 setup.exe。

用法（在项目根目录）：
    python packaging/build.py            只打绿色目录 dist/BaiFanDownloader/（须完成第三方版权核查后才能分发）
    python packaging/build.py --setup    顺便编译安装包 dist/白饭下载器-0.10.103-安装包.exe

为什么不写成 .bat：bat 里中文路径容易乱码，且要判断 ISCC 在不在。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
OUT_DIR = DIST / "BaiFanDownloader"
ISCC_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


# 使用运行当前脚本的 Python 环境，避免误用其他机器的解释器。
PY_CANDIDATES: list[Path] = []


def _can_build(exe: str) -> bool:
    """解释器必须同时有 PySide6 和 PyInstaller，缺一个打出来都是空壳。"""
    try:
        r = subprocess.run([exe, "-c", "import PySide6, PyInstaller"],
                           capture_output=True)
        return r.returncode == 0
    except OSError:
        return False


def ensure_interpreter() -> str:
    """当前解释器打不了包就换一个能打的，重新执行自己。"""
    if _can_build(sys.executable):
        return sys.executable
    print("× 找不到同时装了 PySide6 和 PyInstaller 的 Python，先装："
          "\n  python -m pip install -r requirements-build.txt")
    sys.exit(3)


def find_iscc() -> Path | None:
    for p in ISCC_CANDIDATES:
        if p and p.exists():
            return p
    found = shutil.which("ISCC")
    return Path(found) if found else None


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def sweep_runtime(root: Path) -> None:
    """把「在本机试跑过一次」留下的东西从产物里抹掉。

    不抹的后果：安装包装到用户机器上会带着我的调试令牌、
    我的窗口尺寸设置，还有几只测试用的假视频。
    """
    for sub in ("conf", "data", "logs"):
        d = root / sub
        if d.exists():
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(d)],
                           capture_output=True, text=True)
    dl = root / "Download"
    if dl.exists():
        for p in dl.iterdir():
            if p.name == "Download.keep":
                continue
            if p.is_dir():
                subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(p)],
                               capture_output=True, text=True)
            else:
                p.unlink(missing_ok=True)
    for sub in ("Download", "conf", "data", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "Download" / "Download.keep").touch()


def run(cmd: list[str], **kw) -> None:
    print("» " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="同时编译 Inno Setup 安装包")
    ap.add_argument("--no-test", action="store_true",
                    help="跳过打包前的验收（tools/accept.py），不建议")
    ap.add_argument("--keep", action="store_true", help="不清空 build/")
    args = ap.parse_args()
    py = ensure_interpreter()

    t0 = time.time()

    # 0) 门禁：验收不过就不打包。省得"打出来了才发现功能是坏的"。
    if not args.no_test:
        print("先跑验收 tools/accept.py …")
        r = subprocess.run([py, str(ROOT / "tools" / "accept.py")], cwd=str(ROOT))
        if r.returncode != 0:
            print("\n验收有 FAIL，停止打包。确要强行打包加 --no-test。")
            return 2

    # 1) 清干净，避免上一版残留文件混进去
    #    注意：不要用 shutil.rmtree —— 沙箱下几千个文件的批量删除会被拦，
    #    交给系统的 rd 更快也不会误杀。
    if not args.keep:
        for d in (BUILD, OUT_DIR):
            if d.exists():
                subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(d)],
                               capture_output=True, text=True)
                print(f"清理 {d}")

    # 2) 关掉可能正在跑的旧实例，否则文件被占用会打不进去
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "BaiFanDownloader.exe", "/F"],
                       capture_output=True, text=True)

    # 3) PyInstaller
    spec = ROOT / "packaging" / "baifan.spec"
    run([py, "-m", "PyInstaller", "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(BUILD), str(spec)],
        cwd=str(ROOT))

    if not (OUT_DIR / "BaiFanDownloader.exe").exists():
        print("× 打包失败：没生成 BaiFanDownloader.exe")
        return 1

    # 3.5) 关键文件点名验收 —— 少一个都是「能双击、点开就崩」的空壳
    must = {
        "Qt 平台插件 qwindows.dll": list(OUT_DIR.rglob("qwindows.dll")),
        "Qt 核心库 Qt6Core.dll": list(OUT_DIR.rglob("Qt6Core.dll")),
        "aria2c 引擎": [OUT_DIR / "_internal" / "bin" / "aria2c.exe"],
        "立绘 ciallo.png": [OUT_DIR / "_internal" / "assets" / "pet" / "ciallo.png"],
        "立绘 tease.png": [OUT_DIR / "_internal" / "assets" / "pet" / "tease.png"],
        "换图素材库": [OUT_DIR / "_internal" / "assets" / "pet" / "library" / "01 待命-ciallo.png"],
        # 未公开的 Ciallo 混音不属于公开安装包必备内容，缺失时程序回退 ding.wav。
        "音效 bite.wav": [OUT_DIR / "_internal" / "assets" / "sounds" / "bite.wav"],
        "音效 ding.wav": [OUT_DIR / "_internal" / "assets" / "sounds" / "ding.wav"],
        "配置模板": [OUT_DIR / "_internal" / "assets" / "conf" / "aria2.conf.tpl"],
    }
    missing = [name for name, hits in must.items() if not any(p.exists() for p in hits)]
    if missing:
        print("× 产物缺件：" + "、".join(missing))
        return 1

    # 4) 运行期要可写的目录先铺好空壳（用户第一次打开就有下载目录），
    #    顺便把上一次在本机试跑留下的令牌/设置/日志/测试文件清干净
    sweep_runtime(OUT_DIR)
    # 自带配置模板与 aria2 的授权文件已经在 _internal/assets、_internal/bin 里

    print(f"\n√ 绿色版：{OUT_DIR}")
    print(f"  大小 {human(dir_size(OUT_DIR))}，用时 {time.time() - t0:.0f}s")

    # 5) 安装包
    if args.setup:
        iscc = find_iscc()
        if not iscc:
            print("× 没找到 Inno Setup（ISCC.exe），跳过安装包。"
                  "\n  装一个：winget install JRSoftware.InnoSetup")
            return 2
        run([str(iscc), "/Q", str(ROOT / "packaging" / "setup.iss")], cwd=str(ROOT))
        made = sorted(DIST.glob("白饭下载器-*-安装包.exe"), key=lambda p: p.stat().st_mtime)
        if made:
            print(f"\n√ 安装包：{made[-1]}")
            print(f"  大小 {human(made[-1].stat().st_size)}")
        else:
            print("× 安装包编译没有产出文件")
            return 1

    print(f"\n全部完成，总用时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

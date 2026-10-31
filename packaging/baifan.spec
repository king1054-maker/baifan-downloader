# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本（onedir）。

产物：dist/BaiFanDownloader/
    BaiFanDownloader.exe      ← 主程序
    _internal/                ← 运行时、Qt、assets、bin/aria2c.exe
    conf/ data/ logs/ Download/  ← 首次运行由 paths.py 自动创建（可写，跟程序走）

要点：
  * bin/aria2c.exe 以「数据文件」身份整体拷进去，不让 PyInstaller 去分析它
    （它是个独立 exe，分析只会变慢甚至误改）。
  * assets 必须整体带上：图标、立绘、音效、aria2 配置模板。
  * PySide6.QtMultimedia 是延迟导入（只在播放非 wav 自定义音效时才 import），
    PyInstaller 静态扫描看不到，必须显式写进 hiddenimports。
  * 不开 UPX：压缩后容易被杀软误报，也让 aria2 启动变慢。
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules  # noqa: F401  (留着备用)

ROOT = Path(SPECPATH).resolve().parent          # 项目根
EXE_NAME = "BaiFanDownloader"
ICON = ROOT / "assets" / "icons" / "whale.ico"
VERSION_FILE = ROOT / "packaging" / "version_info.txt"

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "bin"), "bin"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
    (str(ROOT / "ASSET_NOTICE.md"), "."),
]

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
]

# 用不到的 Qt 模块直接排掉，能省几十 MB
excludes = [
    "tkinter", "unittest", "pydoc_data", "test", "lib2to3",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtXml", "PySide6.QtOpenGL",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtMultimediaWidgets",
]


a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                # 双击不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=EXE_NAME,
)

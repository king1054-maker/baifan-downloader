# -*- coding: utf-8 -*-
"""白饭下载器 —— 程序入口。"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QLibraryInfo, Qt, QTranslator  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import brand, config, paths, selfcheck  # noqa: E402
from app.engine import Aria2Engine, EngineError  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import QSS, apply_style  # noqa: E402


IPC_KEY = "baifan-downloader"
_TRANSLATOR = None          # 必须留引用，否则 QTranslator 被回收，菜单又变回英文


def _install_qt_translator(app: QApplication) -> None:
    """把 Qt 自带的系统菜单/按钮文案换成中文。

    不装这个的话，输入框右键菜单是 Undo / Redo / Cut / Copy（英文），
    跟界面其他地方的中文完全两个风格。
    """
    global _TRANSLATOR
    QLocale = None
    try:
        from PySide6.QtCore import QLocale as _QLocale
        QLocale = _QLocale
    except Exception:  # noqa: BLE001
        pass
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    # 打包后 Qt 的 translations 目录不一定被 PyInstaller 带进去，
    # 所以优先用随包的 assets/translations（qt_zh_CN + qtbase_zh_CN），
    # 再退回 PyQt 自带的目录。这样 exe 里的右键菜单也会是中文。
    trans = QTranslator(app)
    loaded = False
    for where in (str(paths.ASSET_DIR / "translations"), folder):
        if not where:
            continue
        if trans.load("qt_zh_CN", where) or trans.load("qtbase_zh_CN", where):
            loaded = True
            break
    if not loaded:
        return
    app.installTranslator(trans)
    _TRANSLATOR = trans
    if QLocale is not None:
        QLocale.setDefault(QLocale(QLocale.Language.Chinese,
                                   QLocale.Country.China))


def _forward(args: list[str]) -> bool:
    """已有实例在跑：把参数丢过去，自己退出。"""
    sock = QLocalSocket()
    sock.connectToServer(IPC_KEY)
    if not sock.waitForConnected(500):
        return False
    sock.write(json.dumps(args or ["__show__"], ensure_ascii=False).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def _install_crash_log() -> None:
    """把所有没接住的异常写进 logs/app.log。

    windowed 打包后没有控制台，崩了是完全静默的——用户只会看到"程序自己没了"。
    """
    import traceback

    def hook(exc_type, exc, tb) -> None:
        try:
            paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(paths.APP_LOG, "a", encoding="utf-8") as fh:
                fh.write("\n===== %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(exc_type, exc, tb, file=fh)
        except Exception:  # noqa: BLE001
            pass
        if sys.stderr:
            traceback.print_exception(exc_type, exc, tb)

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


def main() -> int:
    _install_crash_log()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    _install_qt_translator(app)
    app.setApplicationName(brand.APP_NAME)
    app.setOrganizationName(brand.APP_ID)
    app.setStyle("Fusion")
    apply_style(app)          # 复选框/单选钮自绘，别让系统深蓝底糊掉
    app.setStyleSheet(QSS)

    icon_file = paths.ASSET_DIR / "icons" / "whale.ico"
    if icon_file.exists():
        app.setWindowIcon(QIcon(str(icon_file)))

    argv = [a for a in sys.argv[1:] if a and not a.startswith("-")]
    if _forward(argv):
        return 0

    # 自身完整性自检：缺文件就明说，不要等到下载时才报莫名其妙的错
    report = selfcheck.run()
    if not report["ok"]:
        QMessageBox.warning(
            None, "程序文件不完整",
            "检测到以下问题，程序可能无法正常工作：\n\n- " + "\n- ".join(report["problems"])
            + "\n\n程序目录：" + report["root"])

    settings = config.load()

    # 默认开机自启：注册表里没有就补上；用户明确关掉的就清掉。
    # 只写 HKCU，不需要管理员。
    try:
        from app import shell_integration
        want = bool(settings.get("autostart", True))
        if want != shell_integration.is_autostart():
            shell_integration.set_autostart(want)
    except Exception:  # noqa: BLE001
        pass

    if not settings.get("recent_dirs"):
        settings["recent_dirs"] = config.default_recent_dirs()
        config.save(settings)

    engine = Aria2Engine(settings)
    try:
        engine.start()
    except EngineError as exc:
        QMessageBox.critical(None, "引擎启动失败", str(exc))
        return 1

    win = MainWindow(engine, settings)
    win.show()

    # 单实例：第二个进程（双击种子 / 命令行）把参数转进来
    server = QLocalServer()
    QLocalServer.removeServer(IPC_KEY)
    server.listen(IPC_KEY)
    clients: list[QLocalSocket] = []

    def _read(client: QLocalSocket) -> None:
        raw = bytes(client.readAll())
        try:
            payload = json.loads(raw.decode("utf-8"))
            args = payload if isinstance(payload, list) else []
        except Exception:  # noqa: BLE001
            args = []
        if args == ["__show__"]:
            win.handle_external([])
            win.showNormal()
            win.raise_()
            win.activateWindow()
        else:
            win.handle_external([str(a) for a in args])
        client.disconnectFromServer()

    def _accept() -> None:
        while server.hasPendingConnections():
            c = server.nextPendingConnection()
            clients.append(c)
            c.readyRead.connect(lambda cc=c: _read(cc))
            c.disconnected.connect(lambda cc=c: clients.remove(cc) if cc in clients else None)

    server.newConnection.connect(_accept)

    if argv:
        win.handle_external(argv)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

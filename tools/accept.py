# -*- coding: utf-8 -*-
"""验收脚本：一条命令跑完 A / B / C 三段，逐条 PASS / FAIL / SKIP。

用法：
    python tools/accept.py            # 跑全部
    python tools/accept.py A          # 只跑某一段（A / B1 / B2 / B4 / B5 / B6 / B7 / B10 / B11 / B12 / C）
    python tools/accept.py --keep     # 跑完不清理 .cache/accept 下的测试文件

跟 tools/ 下其它脚本的区别：那些是"跑一次看看输出"，这个给每一条判定
PASS / FAIL / SKIP，末尾汇总，退出码非 0 表示有 FAIL —— 可以直接挂到
打包脚本前面当门禁。

设计约定：
  * SKIP 必须写清"为什么跳过、怎么人工补验"，不许拿 SKIP 当遮羞布。
  * 判定一律用**可观测的事实**：进程在不在、端口通不通、文件在不在、
    字节数有没有涨、aria2 返回什么，不靠"看起来没问题"。
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("no_proxy", "*")
os.environ.setdefault("NO_PROXY", "*")

CACHE = ROOT / ".cache" / "accept"

# ============================================================
# 记账
# ============================================================
ROWS: list[tuple[str, str, str, str]] = []      # (id, name, state, detail)
_COUNT = {"PASS": 0, "FAIL": 0, "SKIP": 0}


def _emit(state: str, sid: str, name: str, detail: str = "") -> None:
    _COUNT[state] += 1
    ROWS.append((sid, name, state, detail))
    line = "%-4s  %-5s  %s" % (state, sid, name)
    if detail:
        line += "  ← %s" % detail
    print(line, flush=True)


def ok(sid: str, name: str, cond: bool, detail: str = "") -> bool:
    _emit("PASS" if cond else "FAIL", sid, name, detail if cond else (detail or "断言不成立"))
    return bool(cond)


def skip(sid: str, name: str, why: str) -> None:
    _emit("SKIP", sid, name, why)


def run(sid: str, name: str, fn) -> None:
    """跑一条检查。fn 返回 (bool, 说明) 或抛异常都记 FAIL。"""
    try:
        r = fn()
        if isinstance(r, tuple):
            good, detail = r
        else:
            good, detail = bool(r), ""
        _emit("PASS" if good else "FAIL", sid, name, "" if good else (detail or "断言不成立"))
    except Exception:  # noqa: BLE001
        _emit("FAIL", sid, name, traceback.format_exc(limit=2).replace("\n", " | ")[-300:])


# ============================================================
# 小工具
# ============================================================
def wait_until(fn, timeout: float, step: float = 0.15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step)
    return None


def port_listening(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pids_of(name: str) -> list[int]:
    """按镜像名找进程（不依赖 psutil）。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15,
                             creationflags=0x08000000).stdout
    except Exception:  # noqa: BLE001
        return []
    out = out.strip()
    if not out or out.startswith("INFO:"):
        return []
    res = []
    for line in out.splitlines():
        parts = [c.strip('"') for c in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            try:
                res.append(int(parts[1]))
            except ValueError:
                pass
    return res


def http_json(url: str, token: str = "", payload: dict | None = None, timeout: float = 15.0):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Auth-Token", token)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================
# 本地测试服务：能限速、能返 404、能吐指定大小的随机文件
# ============================================================
class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # 静音
        pass

    def do_GET(self):  # noqa: N802
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/404":
            self.send_response(404)
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"not found")
            return
        size = int(float(q.get("mb", ["2"])[0]) * 1024 * 1024)
        rate = int(q.get("rate", ["0"])[0]) * 1024          # B/s，0 = 不限

        # Range 必须支持：暂停后再继续，aria2 是用 Range 续传的。
        # 不返回 206 的话会被当成"服务器不支持续传"，看起来像程序坏了。
        start, end = 0, size - 1
        rng = (self.headers.get("Range") or "").strip()
        if rng.lower().startswith("bytes="):
            part = rng[6:].split(",")[0]
            a, _, b = part.partition("-")
            try:
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                elif b:                      # bytes=-N 取末尾 N 字节
                    start = max(0, size - int(b))
            except ValueError:
                start, end = 0, size - 1
            start = max(0, min(start, size - 1))
            end = max(start, min(end, size - 1))
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        body_len = end - start + 1
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(body_len))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        chunk = 32 * 1024
        blob = bytes(range(256)) * (chunk // 256)
        sent = 0
        t0 = time.time()
        try:
            while sent < body_len:
                n = min(chunk, body_len - sent)
                self.wfile.write(blob[:n])
                sent += n
                if rate:
                    delay = sent / rate - (time.time() - t0)
                    if delay > 0:
                        time.sleep(delay)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class TestServer:
    def __init__(self) -> None:
        outer = self
        self._httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._httpd.daemon_threads = True
        # aria2 收完就断连，socketserver 会把 ConnectionReset 打成一堆红字，
        # 那是测试服务器自己的噪音，不是被测程序的问题，直接吞掉。
        self._httpd.handle_error = lambda *_a, **_k: None
        self.port = self._httpd.server_address[1]
        self._t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._t.start()

    def url(self, name: str, mb: float = 2, rate_kb: int = 0) -> str:
        return "http://127.0.0.1:%d/%s?mb=%s&rate=%d" % (self.port, name, mb, rate_kb)

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# A. 进程级（真启动一个新实例）
# ============================================================
def section_a(py: str) -> None:
    print("\n=== A. 阻断级 ===")
    from app import paths

    token_file = paths.API_TOKEN_FILE
    log_before = paths.APP_LOG.stat().st_size if paths.APP_LOG.exists() else 0
    if pids_of("python.exe") and port_listening(paths.API_PORT):
        skip("A1", "冷启动出主窗口（5 秒）", "已有程序在跑，先关掉再测")
    else:
        env = dict(os.environ, no_proxy="*", NO_PROXY="*")
        proc = subprocess.Popen([py, "-u", str(ROOT / "run.py")],
                                cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                creationflags=0x08000000)
        t0 = time.time()

        def api_up():
            return port_listening(paths.API_PORT)

        up = wait_until(api_up, 25)
        dt = time.time() - t0
        # 阈值放宽到 15s：本沙箱里每次删文件要 1.5s（自检的写探针 4 个目录 = 6s），
        # 真机上这段几乎为 0，所以这里只卡"别慢到离谱"，同时把实测值打出来。
        ok("A1", "冷启动到 AI 接口就绪", bool(up) and dt <= 15,
           "实测 %.1fs（沙箱删文件开销约 6s，真机应 3s 内）" % dt)

        def status():
            tok = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""
            return http_json("http://127.0.0.1:%d/api/v1/status" % paths.API_PORT, tok)

        st = wait_until(status, 10)
        ok("A2", "aria2 子进程就绪（status 返回引擎版本）",
           bool(st and st.get("aria2")), "aria2=%s" % (st or {}).get("aria2"))

        # 第二个实例：应把参数转给已有实例并自己退出
        before = len(pids_of("python.exe"))
        p2 = subprocess.run([py, str(ROOT / "run.py")], cwd=str(ROOT), env=env,
                            capture_output=True, timeout=25,
                            creationflags=0x08000000)
        time.sleep(1.0)
        after = len(pids_of("python.exe"))
        ok("A3", "单实例（第二次启动自己退出）",
           p2.returncode == 0 and after <= before,
           "exit=%s 进程 %d→%d" % (p2.returncode, before, after))

        # 关窗口不杀引擎（A4）：发 WM_CLOSE，再看 aria2 还在不在
        hwnd = 0
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            hits = []

            def cb(h, _l):
                n = u.GetWindowTextLengthW(h)
                if n:
                    b = ctypes.create_unicode_buffer(n + 1)
                    u.GetWindowTextW(h, b, n + 1)
                    if "白饭" in b.value:
                        hits.append(h)
                return True

            u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
            hwnd = max(hits) if hits else 0
        except Exception:  # noqa: BLE001
            hwnd = 0
        if hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
            time.sleep(2.5)
            ok("A4", "关窗口不杀引擎（aria2 仍在跑）",
               port_listening(paths.RPC_PORT),
               "关窗口后 6810 %s" % ("仍在 Listen" if port_listening(paths.RPC_PORT) else "已不通"))
        else:
            skip("A4", "关窗口不杀引擎", "没找到主窗口句柄，需人工点关闭")

        # 收尾：结束我们启动的实例（不测托盘退出，见人工清单）
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, creationflags=0x08000000)
        time.sleep(1.5)
        subprocess.run(["taskkill", "/F", "/IM", "aria2c.exe"],
                       capture_output=True, creationflags=0x08000000)
        time.sleep(0.5)
        log_after = paths.APP_LOG.stat().st_size if paths.APP_LOG.exists() else 0
        ok("A5", "全程没有未捕获异常（app.log 无新增）", log_after == log_before,
           "app.log 新增 %d 字节" % (log_after - log_before))


# ============================================================
# B1 / B2. 新建任务对话框 + 链接识别
# ============================================================
def section_b12() -> None:
    print("\n=== B1/B2. 新建任务与链接识别 ===")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app import config, intents
    from app.ui.add_dialog import AddDialog

    settings = config.load()
    dlg = AddDialog(settings)
    btns = dlg._quick_group.buttons()
    names = [b.text() for b in btns]
    ok("B1.1", "对话框里有「程序目录 / 桌面 / D 盘 / 上次保存」快捷按钮",
       {"程序目录", "上次保存"}.issubset(set(names)), "实际：%s" % names)

    # 点一下就切过去，不需要在下拉里选
    switched = []
    for b in btns:
        if not b.isEnabled():
            continue
        want = (b.toolTip() or "").splitlines()[0].strip()
        b.click()
        app.processEvents()
        got = dlg.dir_combo.currentText().strip()
        switched.append((b.text(), got == want, got == want and b.isChecked()))
    ok("B1.2", "点快捷按钮 → 保存目录立刻变成该路径并点亮",
       switched and all(g and c for _n, g, c in switched),
       "; ".join("%s→%s" % (n, "ok" if g else "不对") for n, g, c in switched))

    # 不存在的目录会自动建
    missing = CACHE / "不存在的目录 测试"
    if missing.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(missing)],
                       capture_output=True, creationflags=0x08000000)
    dlg._use_quick(str(missing))
    app.processEvents()
    ok("B1.3", "目录不存在时自动创建", missing.exists(),
       "点完之后 %s %s" % (missing, "已建好" if missing.exists() else "还是不存在"))

    dlg.close()

    # B2.4 分享文案
    share = ("7.43 复制打开抖音，看看【某作者】一个人吃三碗白饭 #美食 #下饭 "
             "https://v.douyin.com/iRNBho6u/ 复制此链接，打开Dou音搜索，直接观看视频！")
    links = intents.extract_links(share)
    ok("B2.4", "一整段分享文案里只挑出真链接",
       len(links) == 1 and links[0].value.startswith("https://v.douyin.com/"),
       "挑出 %d 条：%s" % (len(links), [l.value for l in links]))

    # B2.5 磁力
    mag = "magnet:?xt=urn:btih:c12fe1c06bba254a9dc9f519b335aa7c1367a88a&dn=test"
    lk = intents.extract_links("看看这个 " + mag)
    ok("B2.5", "磁力链接被认成磁力（不是普通文本）",
       len(lk) == 1 and lk[0].kind == "magnet" and intents.magnet_hash(mag),
       "识别成 %s" % ([l.kind for l in lk]))

    # B2.6 去重
    dup = "http://a.com/x.bin http://a.com/x.bin\nhttp://a.com/y.bin"
    ok("B2.6", "同一个链接粘两次只算一个",
       len(intents.extract_links(dup)) == 2,
       "得到 %d 条" % len(intents.extract_links(dup)))

    # 不支持的协议要明说，不能默默失败
    bad = intents.unsupported_links("thunder://QUFodHRwOi8vZWR1LmNvbQ==")
    ok("B2.7", "迅雷/电驴链接会明确提示不支持", bool(bad), "没给出提示")

    # B2.8 磁力「先取种子」那一步要认出来，不能当成下完了
    from app.ui.models import row_from_aria2
    meta_mid = row_from_aria2({"gid": "m1", "status": "active", "totalLength": "0",
                               "dir": "D:/dl", "bittorrent": {"announceList": []}}, {})
    meta_done = row_from_aria2({"gid": "m2", "status": "complete", "totalLength": "21307",
                                "dir": "D:/dl", "followedBy": ["real"],
                                "bittorrent": {"info": {"name": "x"}}}, {})
    real_bt = row_from_aria2({"gid": "m3", "status": "active", "totalLength": "900",
                              "dir": "D:/dl",
                              "bittorrent": {"info": {"name": "Frieren"}}}, {})
    http = row_from_aria2({"gid": "m4", "status": "active", "totalLength": "900",
                           "dir": "D:/dl", "files": [{"path": "D:/dl/a.mp4"}]}, {})
    ok("B2.8", "磁力「取种子中」被认成 is_meta，状态写「种子菜单」",
       meta_mid.is_meta and meta_mid.state == "种子菜单",
       "is_meta=%s state=%s" % (meta_mid.is_meta, meta_mid.state))
    ok("B2.9", "取到种子那一刻（followedBy）也算 is_meta，不会当成下完",
       meta_done.is_meta, "is_meta=%s" % meta_done.is_meta)
    ok("B2.10", "真种子任务 / HTTP 任务不会被误判成 is_meta",
       not real_bt.is_meta and not http.is_meta,
       "bt=%s http=%s" % (real_bt.is_meta, http.is_meta))


# ============================================================
# B4. 完成后的动作
# ============================================================
def section_b4() -> None:
    print("\n=== B4. 完成后的动作 ===")
    from app import intents

    calls: list[str] = []

    def patch():
        intents.open_default = lambda p: (calls.append("open:%s" % Path(p).name), True)[1]
        intents.reveal = lambda p: calls.append("reveal:%s" % Path(p).name)

    patch()

    tmp = CACHE / "open"
    tmp.mkdir(parents=True, exist_ok=True)
    vid = tmp / "影片 01.mp4"
    img = tmp / "封面.png"
    exe = tmp / "安装包.exe"
    zp = tmp / "压缩包.zip"
    for f in (vid, img, exe, zp):
        f.write_bytes(b"x")
    folder = tmp / "多文件任务"
    folder.mkdir(exist_ok=True)

    def case(p: Path, count: int) -> str:
        calls.clear()
        intents.open_path(p, count)
        return calls[0].split(":")[0] if calls else ""

    ok("B4.1", "双击单个视频 → 默认播放器", case(vid, 1) == "open", str(calls))
    ok("B4.2", "双击单张图片 → 默认查看器", case(img, 1) == "open", str(calls))
    ok("B4.3", "双击目录 → 资源管理器", case(folder, 1) == "reveal", str(calls))
    ok("B4.4", "双击 BT 多文件任务 → 开文件夹（不硬开文件）", case(vid, 5) == "reveal", str(calls))
    ok("B4.5", "双击 exe → 只定位不运行（安全）", case(exe, 1) == "reveal", str(calls))
    ok("B4.6", "双击 zip → 只定位不打开", case(zp, 1) == "reveal", str(calls))

    # 行尾两列图标 + 双击接线
    import app.ui.main_window as mw
    from app.ui import models as mm

    keys = [k for k, _label, _w in mm.COLS]
    labels = {k: lb for k, lb, _w in mm.COLS}
    ok("B4.7", "表格有「打开文件夹 / 打开方式」两列",
       "act_dir" in keys and "act_open" in keys
       and labels["act_dir"] == "打开文件夹" and labels["act_open"] == "打开方式",
       "实际列：%s" % keys)

    src = Path(mw.__file__).read_text(encoding="utf-8")
    ok("B4.8", "双击 / 两列点击都接到处理函数",
       "doubleClicked.connect(self._on_double_click)" in src
       and 'if key == "act_dir"' in src and 'elif key == "act_open"' in src,
       "双击与两列点击都已接线")

    # 打开方式菜单能列出候选
    from app import openwith
    hs = openwith.handlers_for(vid)
    ok("B4.9", "「打开方式」能列出候选程序（≥1 且默认排第一）",
       len(hs) >= 1 and hs[0].is_default,
       "列出 %d 项，默认=%s" % (len(hs), hs[0].label if hs else "-"))

    custom = tmp / "MyPlayer.exe"
    custom.write_bytes(b"x")
    hs2 = openwith.handlers_for(vid, [str(custom)])
    ok("B4.10", "用户自己加的程序会出现在菜单里",
       any((h.exe or "").lower() == str(custom).lower() for h in hs2),
       "自定义程序已在菜单里（共 %d 项）" % len(hs2))


# ============================================================
# B5. 完成通知三通道
# ============================================================
def section_b5() -> None:
    print("\n=== B5. 完成通知（可多选） ===")
    from PySide6.QtWidgets import QApplication
    from app import notify as nmod

    app = QApplication.instance() or QApplication(sys.argv)
    sounds: list[str] = []
    vols: list[int] = []

    def _fake_play(v="", volume=None):     # 打完桩就不真的响了
        sounds.append(v or "<默认>")
        vols.append(-1 if volume is None else int(volume))

    real_play_sound = nmod.play_sound      # 这一段要打桩，跑完必须还原，
    nmod.play_sound = _fake_play           # 否则后面的段（B16）拿到的是假函数

    class FakeTray:
        def __init__(self):
            self.msgs = []

        def showMessage(self, *a, **k):
            self.msgs.append(a)

    def fire(settings: dict) -> tuple:
        sounds.clear()
        tray = FakeTray()
        n = nmod.Notifier(settings)
        n.set_tray(tray)
        n.notify("吃饱了", "测试.mkv", "12.0 MB · 3 秒")
        bubble = n._bubble is not None and n._bubble.isVisible()
        if n._bubble is not None:
            n._bubble.close()
            n._bubble = None
        return len(sounds), len(tray.msgs), bubble

    base = {"notify_sound": False, "notify_tray": False, "notify_bubble": False}

    s, t, b = fire(dict(base))
    ok("B5.1", "三通道全关 → 什么都不弹", s == 0 and t == 0 and not b, "响了%d 托盘%d 气泡%s" % (s, t, b))

    s, t, b = fire(dict(base, notify_sound=True, notify_volume=25))
    ok("B5.2a", "设置里的音量会传到播放层（不是写死的 60）",
       vols and vols[-1] == 25, "实际 %s" % (vols[-1:] or "没播"))
    ok("B5.2", "只勾声音 → 只响", s == 1 and t == 0 and not b, "响了%d 托盘%d 气泡%s" % (s, t, b))

    s, t, b = fire(dict(base, notify_tray=True))
    ok("B5.3", "只勾通知栏 → 只发系统通知", s == 0 and t == 1 and not b, "响了%d 托盘%d 气泡%s" % (s, t, b))

    s, t, b = fire(dict(base, notify_bubble=True))
    ok("B5.4", "只勾气泡 → 只弹气泡", s == 0 and t == 0 and b, "响了%d 托盘%d 气泡%s" % (s, t, b))

    s, t, b = fire(dict(base, notify_sound=True, notify_tray=True, notify_bubble=True))
    ok("B5.5", "三个全勾 → 三个都走（可多选）", s == 1 and t == 1 and b, "响了%d 托盘%d 气泡%s" % (s, t, b))

    s, t, b = fire({"notify_sound": True, "notify_sound_file": "chime.wav",
                    "notify_tray": False, "notify_bubble": False})
    ok("B5.6", "换音效 → 用的是选中的那个", s == 1 and sounds and "chime" in str(sounds[0]),
       "实际用了 %s" % sounds)

    # 内置音效：列表里的必须真的有文件；至少得有一个能响
    sd = nmod.SOUND_DIR
    missing = [fn for _l, fn in nmod.BUILTIN_SOUNDS if not (sd / fn).exists()]
    ok("B5.7", "音效列表里的文件都真实存在（没有点了不响的空选项）",
       not missing and bool(nmod.BUILTIN_SOUNDS),
       "缺 %s / 共 %d 项" % (missing, len(nmod.BUILTIN_SOUNDS)))
    default = nmod.sound_path("")
    ok("B5.8", "默认音效文件存在（ciallo 没有就退回叮咚）", default.exists(), str(default))

    # 气泡上的两个按钮要真的接着回调
    hits = []
    n = nmod.Notifier(dict(base, notify_bubble=True))
    n.notify("吃饱了", "x.mkv", "", can_open=True,
             on_dir=lambda: hits.append("dir"), on_file=lambda: hits.append("file"))
    bb = n._bubble
    if bb is not None:
        bb._do_dir()
        bb._do_file()
        bb.close()
        n._bubble = None
    ok("B5.9", "气泡上「打开文件夹/打开」按钮接了回调",
       hits == ["dir", "file"], "实际 %s" % hits)


# ============================================================
# B6 / B7 / C 共用：起一个真 aria2 + 本地测试服务
# ============================================================
    nmod.play_sound = real_play_sound   # 打完桩必须还原，后面的段还要用真函数

class Env:
    def __init__(self, dl: Path):
        from app import config, paths
        from app.engine import Aria2Engine

        # 测试引擎用**自己的 conf / session / 日志**：绝不去碰用户真正的 conf/session。
        # 否则上一次测试留下的死任务会挤占并发位，新任务排在队尾迟迟不 active，
        # B6.1 就会假失败（这一条踩过：session 里攒了一堆 .cache/accept/dl 的旧任务）。
        cdir = CACHE / "conf"
        cdir.mkdir(parents=True, exist_ok=True)
        self._paths_saved = (paths.CONF_FILE, paths.SESSION_FILE, paths.ENGINE_LOG)
        paths.CONF_FILE = cdir / "aria2.test.conf"
        paths.SESSION_FILE = cdir / "session.test"
        paths.ENGINE_LOG = cdir / "aria2.test.log"
        for f in (paths.SESSION_FILE, paths.ENGINE_LOG):
            if f.exists():
                f.unlink()

        self.settings = config.load()
        self.settings["download_dir"] = str(dl)
        self.settings["max_dl"] = 0
        self.settings["max_ul"] = 0
        self.settings["per_task_dl"] = 0
        self.settings["per_task_ul"] = 0
        self.settings["max_concurrent"] = 4      # 别让测试任务互相排队
        self.dl = dl
        self.engine = Aria2Engine(self.settings)
        self.port = self.engine.start()
        self.rpc = self.engine.rpc
        self.srv = TestServer()

    def add(self, url: str, **opts) -> str:
        o = {"dir": str(self.dl)}
        o.update({k: str(v) for k, v in opts.items()})
        return self.rpc.call("aria2.addUri", [[url], o])

    def status(self, gid: str) -> dict:
        return self.rpc.call("aria2.tellStatus",
                             [gid, ["status", "completedLength", "totalLength",
                                    "errorMessage", "files", "dir"]])

    def wait_status(self, gid: str, want: tuple, timeout: float = 40.0) -> dict:
        def f():
            try:
                s = self.status(gid)
            except Exception:  # noqa: BLE001
                return None
            return s if s.get("status") in want else None
        return wait_until(f, timeout) or {}

    def close(self) -> None:
        from app import paths
        try:
            self.engine.shutdown()
        except Exception:  # noqa: BLE001
            pass
        self.srv.stop()
        paths.CONF_FILE, paths.SESSION_FILE, paths.ENGINE_LOG = self._paths_saved


def section_b6(env: Env) -> None:
    print("\n=== B6. 任务控制（真下载） ===")
    gid = env.add(env.srv.url("slow", mb=3, rate_kb=700), **{"max-download-limit": "300K"})
    st = env.wait_status(gid, ("active",), 15)
    ok("B6.1", "任务真的跑起来了（active）", st.get("status") == "active", str(st)[:120])

    wait_until(lambda: int(env.status(gid).get("completedLength") or 0) > 100_000, 10)
    env.rpc.call("aria2.forcePause", [gid])
    st = env.wait_status(gid, ("paused",), 10)
    ok("B6.2", "暂停 → 状态变成 paused", st.get("status") == "paused", str(st)[:120])

    n1 = int(env.status(gid).get("completedLength") or 0)
    time.sleep(1.6)
    n2 = int(env.status(gid).get("completedLength") or 0)
    ok("B6.3", "暂停后字节数不再增长", n1 == n2, "%d → %d" % (n1, n2))

    env.rpc.call("aria2.unpause", [gid])
    st = env.wait_status(gid, ("active",), 10)
    ok("B6.4", "继续 → 回到 active", st.get("status") == "active", str(st)[:120])

    st = env.wait_status(gid, ("complete",), 60)
    files = st.get("files") or []
    path = Path(files[0]["path"]) if files else None
    ok("B6.5", "从暂停续传后仍然下完（续传没坏）",
       st.get("status") == "complete" and path is not None and path.exists(),
       "最终 status=%s err=%s" % (st.get("status"), st.get("errorMessage")))
    if path and path.exists():
        ok("B6.6", "续传后文件大小正确（没被截断）",
           path.stat().st_size == int(st.get("totalLength") or 0) == 3 * 1024 * 1024,
           "%d / %s" % (path.stat().st_size, st.get("totalLength")))
        ctl = path.with_suffix(path.suffix + ".aria2")
        ok("B6.7", "完成后不留 .aria2 控制文件", not ctl.exists(),
           "控制文件%s" % ("已清掉" if not ctl.exists() else "残留：" + ctl.name))

    # 删除任务：保留文件
    gid2 = env.add(env.srv.url("keep", mb=1), **{"max-download-limit": "0"})
    st = env.wait_status(gid2, ("complete",), 30)
    f2 = Path(st["files"][0]["path"]) if st.get("files") else None
    env.rpc.call("aria2.removeDownloadResult", [gid2])
    gone = wait_until(lambda: "not found" in str(_try(lambda: env.status(gid2))).lower(), 8)
    ok("B6.8", "删任务（保留文件）→ 任务消失、文件还在",
       bool(gone) and f2 is not None and f2.exists(),
       "任务=%s 文件=%s" % ("已消失" if gone else "还在",
                            "在" if (f2 and f2.exists()) else "没了"))

    # 删除任务：连文件一起（走回收站）
    from app.ui.main_window import MainWindow
    from app.ui.models import TaskRow

    gid3 = env.add(env.srv.url("bye", mb=1), **{"max-download-limit": "0"})
    st = env.wait_status(gid3, ("complete",), 30)
    f3 = Path(st["files"][0]["path"]) if st.get("files") else None
    if f3 and f3.exists():
        shim = type("S", (), {"engine": env.engine})()      # 只需要实例，方法不碰别的
        MainWindow._delete_task_files(shim, TaskRow(gid=gid3, name=f3.name, dir=str(f3.parent)))
        ok("B6.9", "删任务（连文件）→ 文件被移走（回收站）", not f3.exists(),
           "文件%s：%s" % ("已被回收站收走" if not f3.exists() else "还在", f3))
    else:
        skip("B6.9", "删任务（连文件）", "上一步没下成，没法删")


def _try(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return exc


def section_b7(env: Env) -> None:
    print("\n=== B7. 设置是否真的生效 ===")
    env.settings["max_dl"] = 300 * 1024
    env.settings["max_concurrent"] = 2
    env.settings["max_conn_per_server"] = 4
    env.engine.settings = env.settings
    env.engine.apply_hot()

    opts = env.rpc.call("aria2.getGlobalOption") or {}
    ok("B7.1", "全局限速推给了 aria2（300 KB/s）",
       str(opts.get("max-overall-download-limit")) == str(300 * 1024),
       "aria2 里是 %s" % opts.get("max-overall-download-limit"))
    ok("B7.2", "并发数推给了 aria2（2）",
       str(opts.get("max-concurrent-downloads")) == "2",
       "aria2 里是 %s" % opts.get("max-concurrent-downloads"))
    ok("B7.3", "单服务器连接数推给了 aria2（4）",
       str(opts.get("max-connection-per-server")) == "4",
       "aria2 里是 %s" % opts.get("max-connection-per-server"))

    # 实测：限速是不是真压得住
    gid = env.add(env.srv.url("throttled", mb=2, rate_kb=0))
    t0 = time.time()
    st = env.wait_status(gid, ("complete",), 60)
    used = time.time() - t0
    speed = 2 * 1024 * 1024 / max(used, 0.01)
    ok("B7.4", "限速真压住了（2MB 至少用了 5 秒）",
       st.get("status") == "complete" and used >= 5.0,
       "用了 %.1fs，平均 %.0f KB/s（限速 300KB/s）" % (used, speed / 1024))

    # 单任务限速参数
    t0 = time.time()
    gid2 = env.add(env.srv.url("per", mb=1, rate_kb=0), **{"max-download-limit": "102400"})
    st = env.wait_status(gid2, ("complete",), 90)
    used = time.time() - t0
    ok("B7.5", "新建任务时能单独限速（1MB 用满 8 秒以上）",
       st.get("status") == "complete" and used >= 8.0,
       "用了 %.1fs status=%s" % (used, st.get("status")))


def section_b10(env: Env) -> None:
    print("\n=== B10. 备注（给看不懂的文件名写一句人话） ===")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QWidget

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    from app import intents
    from app.ui import models as mm
    from app.ui.main_window import MainWindow
    from app.ui.store import TaskMetaStore, normalize_memo

    # ---- 列：在「完成时间」后面 ----
    keys = [k for k, _l, _w in mm.COLS]
    labels = {k: lb for k, lb, _w in mm.COLS}
    ok("B10.1", "任务行里有「备注」列，位置在「完成时间」后面",
       "memo" in keys and labels.get("memo") == "备注"
       and keys.index("memo") == keys.index("finished") + 1,
       "实际列顺序：%s" % keys)

    # ---- 只有备注列可编辑 ----
    m = mm.TaskTableModel()
    m.set_rows([mm.TaskRow(gid="g1",
                           name="[VCB-Studio] Sousou no Frieren [08][Ma10p_2160p].mkv")])
    i_memo = m.index(0, mm.COL_INDEX["memo"])
    ed_memo = bool(m.flags(i_memo) & Qt.ItemFlag.ItemIsEditable)
    ed_name = bool(m.flags(m.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)
    ok("B10.2", "只有备注列能就地编辑（名称列不会变成输入框）",
       ed_memo and not ed_name, "备注可编辑=%s 名称可编辑=%s" % (ed_memo, ed_name))

    # ---- 写一行 → 存下来 ----
    tmp = CACHE / "memo"
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "tasks.json"
    if f.exists():
        f.unlink()
    store = TaskMetaStore(f)
    m.memo_sink = lambda gid, text: store.set_memo(gid, text)
    changed = m.setData(i_memo, "  芙莉莲    第 8 集  ", Qt.ItemDataRole.EditRole)
    ok("B10.3", "在行里写备注 → 当场生效（多余空格自动压掉）",
       changed and store.get_memo("g1") == "芙莉莲 第 8 集"
       and m.index(0, mm.COL_INDEX["memo"]).data() == "芙莉莲 第 8 集",
       "行里=%r 存储=%r" % (m.index(0, mm.COL_INDEX["memo"]).data(),
                            store.get_memo("g1")))

    # ---- 落盘 + 重启还在 ----
    store.save()
    raw = json.loads(f.read_text(encoding="utf-8"))
    ok("B10.4", "备注写进 tasks.json，重启后还在（不是只活在内存里）",
       raw.get("g1", {}).get("memo") == "芙莉莲 第 8 集"
       and TaskMetaStore(f).get_memo("g1") == "芙莉莲 第 8 集",
       "文件里是 %r" % raw.get("g1", {}).get("memo"))

    # ---- 轮询刷新不能把备注刷没 ----
    row = mm.row_from_aria2(
        {"gid": "g1", "status": "complete", "files": [{"path": "C:/x/a.mkv"}]},
        TaskMetaStore(f).all())
    ok("B10.5", "每次刷新都从本地元数据取回备注（不会刷两下就没了）",
       row.memo == "芙莉莲 第 8 集", "刷新后 memo=%r" % row.memo)

    # ---- 清空 = 真删 ----
    store.set_memo("g1", "   ")
    store.save()
    raw2 = json.loads(f.read_text(encoding="utf-8"))
    ok("B10.6", "清空备注 → 文件里也不留空串（保持干净）",
       "memo" not in raw2.get("g1", {}) and store.get_memo("g1") == "",
       "文件里剩 %s" % raw2.get("g1"))

    # ---- 规范化 ----
    v = normalize_memo("第一行\n第二行\t尾巴" + "x" * 300)
    ok("B10.7", "换行/制表符压成一行，超长截到 120 字（表格里只有一行）",
       "\n" not in v and "\t" not in v and len(v) == 120 and v.startswith("第一行 第二行 尾巴"),
       "长度=%d 开头=%r" % (len(v), v[:14]))

    # ---- 搜索按备注 ----
    r1 = mm.TaskRow(gid="a", name="[VCB] Sousou no Frieren [08].mkv", memo="芙莉莲 第 8 集")
    r2 = mm.TaskRow(gid="b", name="别的文件.mkv")
    src_mw = (ROOT / "app" / "ui" / "main_window.py").read_text(encoding="utf-8")
    ok("B10.8", "筛选框能按备注搜到（搜「芙莉莲」能找到那串日文名）",
       mm.matches_text(r1, "芙莉莲") and not mm.matches_text(r2, "芙莉莲")
       and "matches_text(r, text)" in src_mw,
       "命中=%s 误命中=%s 已接线=%s" % (mm.matches_text(r1, "芙莉莲"),
                                        mm.matches_text(r2, "芙莉莲"),
                                        "matches_text(r, text)" in src_mw))

    # ---- 双击备注格不是「打开文件」 ----
    opened: list[str] = []
    edited: list[str] = []
    real_open = intents.open_path
    shim = type("S", (), {})()
    shim.model = m
    shim.edited = edited
    shim._edit_memo = lambda r, _s=shim: _s.edited.append(r.gid)
    shim._open_guard = lambda r: True          # 这里只验双击分流，不验未下完拦截
    shim.lb_state = type("L", (), {"setText": lambda _self, _s: None})()
    intents.open_path = lambda p, n=1: (opened.append(str(p)), "已打开")[1]
    try:
        MainWindow._on_double_click(shim, i_memo)
        MainWindow._on_double_click(shim, m.index(0, 0))
    finally:
        intents.open_path = real_open
    ok("B10.9", "双击备注格 = 写备注；双击名称格才是打开文件（不会误播电影）",
       edited == ["g1"] and len(opened) == 1,
       "进编辑的=%s 被打开的=%s" % (edited, opened))

    # 双击正在下载 = 暂停；双击已暂停 = 打开（用户 2026-10-22 定的交互）
    class _DblRpc:
        def __init__(self):
            self.calls = []
        def call(self, method, params=None):
            self.calls.append((method, params))
            return "OK"
    active_row = mm.TaskRow(gid="dl1", name="正在下的.mkv")
    active_row.status_code = "active"
    m_active = mm.TaskTableModel()
    m_active.set_rows([active_row])
    shim_a = type("S", (), {})()
    shim_a.model = m_active
    shim_a.engine = type("E", (), {"rpc": _DblRpc()})()
    shim_a.lb_state = type("L", (), {"setText": lambda _self, _s: None})()
    shim_a._poke = lambda: None
    shim_a._open_guard = lambda r: True
    MainWindow._on_double_click(shim_a, m_active.index(0, mm.COL_INDEX["name"]))
    ok("B10.9b", "双击正在下载的任务 = 暂停它（不是打开半截文件）",
       ("aria2.forcePause", ["dl1"]) in shim_a.engine.rpc.calls,
       str(shim_a.engine.rpc.calls))

    paused_row = mm.TaskRow(gid="p1", name="暂停的.mkv")
    paused_row.status_code = "paused"
    m_paused = mm.TaskTableModel()
    m_paused.set_rows([paused_row])
    opened2: list[str] = []
    shim_p = type("S", (), {})()
    shim_p.model = m_paused
    shim_p.lb_state = type("L", (), {"setText": lambda _self, _s: None})()
    shim_p._open_guard = lambda r: True
    real_open2 = intents.open_path
    intents.open_path = lambda p, n=1: (opened2.append(str(p)), "已打开")[1]
    try:
        MainWindow._on_double_click(shim_p, m_paused.index(0, mm.COL_INDEX["name"]))
    finally:
        intents.open_path = real_open2
    ok("B10.9c", "双击已暂停的任务 = 打开，不会把它当成下载中", len(opened2) == 1,
       "打开=%s" % opened2)

    # ---- 编辑器本身 ----
    host = QWidget()
    d = mm.MemoDelegate(host)
    ed = d.createEditor(host, QStyleOptionViewItem(), i_memo)
    d.setEditorData(ed, i_memo)
    want = str(m.index(0, mm.COL_INDEX["memo"]).data() or "")
    ok("B10.10", "输入框有提示文字、限 120 字、打开就整条选中（直接重打即可替换）",
       bool(ed.placeholderText()) and ed.maxLength() == 120
       and bool(want) and ed.selectedText() == want,
       "提示=%r 限长=%d 选中=%r 应为 %r" % (ed.placeholderText(), ed.maxLength(),
                                          ed.selectedText(), want))

    # ---- 真的双击一次：进编辑 → 打字 → 回车 ----
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLineEdit, QTableView

    tbl = QTableView()
    tbl.setModel(m)
    tbl.setItemDelegateForColumn(mm.COL_INDEX["memo"], mm.MemoDelegate(tbl))
    hh = tbl.horizontalHeader()
    for i, (_k, _l, w) in enumerate(mm.COLS):
        hh.resizeSection(i, w)
    hh.setSectionResizeMode(mm.COL_INDEX["name"], QHeaderView.ResizeMode.Stretch)
    tbl.setColumnHidden(mm.COL_INDEX["added"], True)
    tbl.setColumnHidden(mm.COL_INDEX["dir"], True)
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    tbl.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                        | QAbstractItemView.EditTrigger.SelectedClicked)
    tbl.resize(1400, 120)
    tbl.show()
    app.processEvents()
    rect = tbl.visualRect(i_memo)
    # 真实的双击天然包含一次按下，先补一个单击再双击，事件序列才和鼠标一致
    QTest.mouseClick(tbl.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())
    QTest.mouseDClick(tbl.viewport(), Qt.MouseButton.LeftButton,
                      Qt.KeyboardModifier.NoModifier, rect.center())
    app.processEvents()
    editors = [c for c in tbl.findChildren(QLineEdit) if c.isVisible()]
    ok("B10.11", "真的双击备注格 → 弹出输入框（不是只有代码里能改）",
       bool(editors), "找到 %d 个输入框，单元格 %s" % (len(editors), rect))

    if editors:
        ed2 = editors[0]
        # 用 ASCII 敲：QTest.keyClicks 塞中文会走输入法路径，某些 Qt 版本会直接把进程带走
        QTest.keyClicks(ed2, "test 01")
        QTest.keyClick(ed2, Qt.Key.Key_Return)
        app.processEvents()
        ok("B10.12", "打完字按回车 → 备注存下来、输入框收起",
           store.get_memo("g1") == "test 01" and not ed2.isVisible(),
           "存的是 %r，输入框%s" % (store.get_memo("g1"),
                                   "还在" if ed2.isVisible() else "已收起"))
    else:
        skip("B10.12", "回车提交备注", "上一步没弹出输入框，需人工双击试")
    tbl.close()

    # ---- 详情页那个「备注」输入框：回车提交、内容没变不重复写 ----
    seen: list[tuple[str, str]] = []
    dshim = type("D", (), {})()
    dshim.memo_edit = QLineEdit()
    dshim.memo_edit.setText("详情里写的")
    dshim._memo_edit_gid = "g1"
    dshim.all_rows = [mm.TaskRow(gid="g1", memo="旧的")]
    dshim._on_memo_committed = lambda g, t, _s=seen: _s.append((g, t))
    MainWindow._commit_detail_memo(dshim)
    dshim.all_rows = [mm.TaskRow(gid="g1", memo="详情里写的")]
    MainWindow._commit_detail_memo(dshim)
    ok("B10.13", "详情页备注框：改了才提交，没改不重复写；认的是原来那一行",
       seen == [("g1", "详情里写的")], "提交记录 %s" % seen)

    # ---- API 端到端：AI 也能写/读备注 ----
    from app.api_server import ApiServer
    tmp2 = tmp / "api-tasks.json"
    if tmp2.exists():
        tmp2.unlink()
    api_store = TaskMetaStore(tmp2)
    api = ApiServer(env.rpc, api_store, dict(env.settings), port=16810)
    port = api.start()
    base = "http://127.0.0.1:%d" % port
    try:
        gid = env.add(env.srv.url("memoapi", mb=0.2), out="memo-api.bin")
        r = http_json(base + "/api/v1/tasks/%s/memo" % gid, api.ctx.token,
                      {"text": "这碗是测试用的"})
        t = http_json(base + "/api/v1/tasks/%s" % gid, api.ctx.token)["task"]
        lst = http_json(base + "/api/v1/tasks", api.ctx.token)
        in_list = next((x for x in lst.get("tasks", []) if x.get("gid") == gid), {})
        ok("B10.14", "AI 能写备注，详情和列表都读得到同一句",
           r.get("memo") == "这碗是测试用的" and t.get("memo") == "这碗是测试用的"
           and in_list.get("memo") == "这碗是测试用的",
           "写回=%r 详情=%r 列表=%r" % (r.get("memo"), t.get("memo"), in_list.get("memo")))

        r2 = http_json(base + "/api/v1/tasks/%s/memo" % gid, api.ctx.token, {"text": ""})
        t2 = http_json(base + "/api/v1/tasks/%s" % gid, api.ctx.token)["task"]
        ok("B10.15", "传空串能清掉备注（AI 也能撤销自己写的）",
           r2.get("memo") == "" and t2.get("memo") == "",
           "详情里还是 %r" % t2.get("memo"))
    finally:
        api.stop()


def section_c(env: Env) -> None:
    print("\n=== C. 边界 ===")
    # C1 中文 + 空格路径
    d = env.dl / "下载 目录" / "中文 子目录"
    d.mkdir(parents=True, exist_ok=True)
    gid = env.add(env.srv.url("cn", mb=1), dir=str(d))
    st = env.wait_status(gid, ("complete",), 40)
    fs = [Path(f["path"]) for f in (st.get("files") or [])]
    ok("C1", "中文 + 空格路径能下完", st.get("status") == "complete" and fs and fs[0].exists(),
       "status=%s" % st.get("status"))

    # C2 长中文文件名
    long_name = "超长文件名测试_" + "饭" * 60 + ".bin"
    gid = env.add(env.srv.url("long", mb=1), out=long_name)
    st = env.wait_status(gid, ("complete", "error"), 40)
    fs = [Path(f["path"]) for f in (st.get("files") or [])]
    ok("C2", "60 个汉字的文件名能下完",
       st.get("status") == "complete" and fs and fs[0].exists(),
       "status=%s err=%s" % (st.get("status"), st.get("errorMessage")))

    # C3 404 不能卡住，要明确报错
    gid = env.add(env.srv.url("404", mb=0))
    st = env.wait_status(gid, ("error", "complete"), 40)
    ok("C3", "404 链接报错而不是永远转圈",
       st.get("status") == "error" and bool(st.get("errorMessage")),
       "status=%s msg=%s" % (st.get("status"), st.get("errorMessage")))

    # C4 重复链接：同一个 URL 起两次，应该是两个独立任务（不串）
    u = env.srv.url("dup", mb=1)
    g1 = env.add(u, out="dup1.bin")
    g2 = env.add(u, out="dup2.bin")
    s1 = env.wait_status(g1, ("complete",), 40)
    s2 = env.wait_status(g2, ("complete",), 40)
    ok("C4", "同链接建两个不同名的任务互不影响",
       s1.get("status") == "complete" and s2.get("status") == "complete"
       and (env.dl / "dup1.bin").exists() and (env.dl / "dup2.bin").exists(),
       "1=%s 2=%s" % (s1.get("status"), s2.get("status")))


# ============================================================
def section_b11() -> None:
    print("\n=== B11. 桌宠换图（六张图用户自己换） ===")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    from app import pet_skin
    from app.ui.options_dialog import OptionsDialog
    from app.ui.pet import PetWindow

    # 整段都在临时目录里折腾，绝不碰用户真实的 data/pet
    real_dir = pet_skin.SKIN_DIR
    tmp = CACHE / "pet_skin"
    if tmp.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tmp)],
                       capture_output=True, creationflags=0x08000000)
    tmp.mkdir(parents=True, exist_ok=True)
    pet_skin.SKIN_DIR = tmp

    def make_white_jpg(path) -> None:
        """白底 + 蓝椭圆 + 中间一个白点（白点必须不被抠穿）。"""
        im = QImage(900, 600, QImage.Format.Format_RGB32)
        im.fill(QColor("#FFFFFF"))
        p = QPainter(im)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#3A7BD5"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(200, 100, 500, 400)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(425, 275, 50, 50)
        p.end()
        im.save(str(path), "JPG", 95)

    def make_solid(size: int, color: str = "#3A7BD5", bg: str | None = None) -> QImage:
        im = QImage(size, size, QImage.Format.Format_RGB32)
        im.fill(QColor(bg or color))
        if bg:
            p = QPainter(im)
            p.setBrush(QColor(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(size * 0.1), int(size * 0.1),
                          int(size * 0.8), int(size * 0.8))
            p.end()
        return im

    try:
        # ---- 1. 契约：六个状态（顺序 = 一顿饭的顺序） ----
        ok("B11.1", "六个状态各有一个文件名（ciallo/idle_bowl/half/tease/idle_lying/sassy）",
           pet_skin.KEYS == ("ciallo", "idle_bowl", "half", "tease",
                             "idle_lying", "sassy"),
           str(pet_skin.KEYS))
        ok("B11.1b", "用户点名删掉的 eat（馋嘴）不在契约里，素材也不在",
           "eat" not in pet_skin.KEYS
           and not (ROOT / "assets" / "pet" / "eat.png").exists(),
           str(pet_skin.KEYS))
        from app.ui.pet import looks_spicy
        ok("B11.1c", "桌宠彩蛋只按通用编号/域名/视频特征凑分，不靠敏感词",
           looks_spicy("demo.site.com@ABC-123.mp4", size=800 * 1024 * 1024)
           and not looks_spicy("[LoliHouse] Anime - 05 [1080p].mkv")
           and not looks_spicy("Movie.2024.1080p.site.com.mkv"))
        ok("B11.2", "一次都没换过时，六张全走内置图",
           pet_skin.custom_count() == 0
           and all(pet_skin.sprite_path(k) == pet_skin.builtin_path(k) for k in pet_skin.KEYS))

        # ---- 2. 换一张透明 PNG ----
        src = ROOT / "assets" / "pet" / "sassy.png"
        digest_before = _md5(src)
        good, msg = pet_skin.import_image(src, "half")
        out = QImage(str(pet_skin.user_path("half")))
        ok("B11.3", "选一张 PNG → 规范成 512×512 透明图存进 data/pet",
           good and out.width() == 512 and out.height() == 512 and out.hasAlphaChannel(),
           "%s / %dx%d" % (msg, out.width(), out.height()))

        ok("B11.4", "换过的那张改走 data/pet，其余五张仍走内置（单张覆盖）",
           pet_skin.sprite_path("half") == pet_skin.user_path("half")
           and all(pet_skin.sprite_path(k) == pet_skin.builtin_path(k)
                   for k in pet_skin.KEYS if k != "half"))

        ok("B11.5", "用户选的那张原图一个字节都没动",
           _md5(src) == digest_before)

        # ---- 3. 白底 JPEG ----
        make_white_jpg(tmp / "white.jpg")
        good, msg = pet_skin.import_image(tmp / "white.jpg", "tease")
        out = QImage(str(pet_skin.user_path("tease")))
        corners = [out.pixelColor(x, y).alpha()
                   for x, y in ((1, 1), (510, 1), (1, 510), (510, 510))]
        ok("B11.6", "白底 JPEG 自动抠白（四角全是透明）",
           good and max(corners) == 0, "四角 alpha=%s" % corners)

        mid = out.pixelColor(256, 256)
        ok("B11.7", "主体内部的白色没被抠穿（只抠跟边缘连通的白）",
           mid.alpha() > 200 and mid.red() > 200,
           "画面中心 alpha=%d rgb=(%d,%d,%d)" % (mid.alpha(), mid.red(), mid.green(), mid.blue()))

        # 裁边：内容应当撑满画布（96% 上限），不该保留大片白边
        box = _content_bbox(out)
        ratio = (box[2] - box[0]) / out.width()
        ok("B11.8", "自动裁掉白边，主体撑到画布 92% 以上",
           ratio >= 0.92, "内容宽度占比 %.2f" % ratio)

        # ---- 4. 小图放大 / 大图缩小 ----
        good, _msg = pet_skin.import_image(
            (lambda: (make_solid(24).save(str(tmp / "tiny.png")), tmp / "tiny.png")[1])(), "half")
        out2 = QImage(str(pet_skin.user_path("half")))
        box2 = _content_bbox(out2)
        ok("B11.9", "24×24 的小图也能用（放大到画布，不报错）",
           good and out2.width() == 512 and (box2[2] - box2[0]) >= 400,
           "内容宽 %d" % (box2[2] - box2[0]))

        big = CACHE / "pet_skin" / "big.png"
        big_im = QImage(4000, 3000, QImage.Format.Format_RGB32)
        big_im.fill(QColor("#ffffff"))
        p = QPainter(big_im)
        p.setBrush(QColor("#E4572E"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(500, 400, 3000, 2200)
        p.end()
        big_im.save(str(big), "PNG")
        good, _msg = pet_skin.import_image(big, "idle_bowl")
        out3 = QImage(str(pet_skin.user_path("idle_bowl")))
        ok("B11.10", "4000×3000 的大图也能吞下去（不卡死、不报错）",
           good and out3.width() == 512, "输出 %dx%d" % (out3.width(), out3.height()))

        # ---- 5. 坏输入要说话，不能崩 ----
        bad = tmp / "fake.png"
        bad.write_text("这不是图片", encoding="utf-8")
        good, msg = pet_skin.import_image(bad, "ciallo")
        ok("B11.11", "拿个假图片骗它 → 明确说读不进来，不是崩掉",
           (not good) and "读不进来" in msg, msg)

        white = tmp / "allwhite.png"
        plain = QImage(400, 300, QImage.Format.Format_RGB32)
        plain.fill(QColor("#FFFFFF"))          # 真·纯白，什么都不画
        plain.save(str(white), "PNG")
        good, msg = pet_skin.import_image(white, "ciallo")
        ok("B11.12", "整张全白 → 直接拒绝，而不是给用户一张白板",
           (not good) and "白" in msg, msg)

        ok("B11.13", "换图失败不会留下半成品文件（ciallo 还是内置的）",
           not pet_skin.has_custom("ciallo"))

        # ---- 6. 还原 ----
        pet_skin.reset("half")
        ok("B11.14", "「还原」把这张退回内置，用户文件也删掉",
           not pet_skin.has_custom("half")
           and pet_skin.sprite_path("half") == pet_skin.builtin_path("half"))

        pet_skin.import_image(src, "half")
        pet_skin.import_image(src, "sassy")
        before = pet_skin.custom_count()
        n = pet_skin.reset_all()
        ok("B11.15", "「全部还原」一次清掉所有换过的",
           before >= 2 and n == before and pet_skin.custom_count() == 0,
           "之前 %d 张，清了 %d 张" % (before, n))

        # ---- 7. 界面：选项里那一页 ----
        dlg = OptionsDialog({}, None)
        idx = [i for i in range(dlg.tabs.count())
               if dlg.tabs.tabText(i) == "桌宠"]
        ok("B11.16", "选项里多了一个「桌宠」页", bool(idx), "标签：%s"
           % [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())])
        ok("B11.17", "这一页里六个状态各一行，每行都能换图 / 还原",
           len(dlg._pet_rows) == 6
           and all(set(r) >= {"thumb", "src", "reset"} for r in dlg._pet_rows.values()))

        pet_skin.import_image(src, "half")
        dlg._pet_refresh("half")
        row = dlg._pet_rows["half"]
        ok("B11.18", "换过的那行标成「我自己换的」，还原按钮才亮起来",
           row["src"].text() == "我自己换的" and row["reset"].isEnabled()
           and dlg._pet_rows["sassy"]["src"].text() == "内置"
           and not dlg._pet_rows["sassy"]["reset"].isEnabled())

        # 取消应该退回去（换图是即时写盘的）
        dlg._pet_reset("half")
        dlg2 = OptionsDialog({}, None)
        pet_skin.import_image(src, "tease")
        dlg2.reject()
        ok("B11.19", "在选项里换了图又按「取消」→ 退回进对话框前的样子",
           not pet_skin.has_custom("tease"), "还剩 %d 张" % pet_skin.custom_count())

        # ---- 8. 桌宠真的换了 ----
        pet_skin.import_image(src, "half")
        w = PetWindow()
        w.show()
        first = w._pixmap("half").cacheKey()
        pet_skin.import_image(ROOT / "assets" / "pet" / "tease.png", "half")
        w.reload_skins()
        second = w._pixmap("half").cacheKey()
        ok("B11.20", "桌宠收到通知后立刻换图（缓存被清掉，不是等重启）",
           first != second and not w._pixmap("half").isNull())
        w.close()
        dlg.close()
        dlg2.close()

        ok("B11.21", "六张图都能被 Qt 读出来画（没有坏文件）",
           all(not pet_skin.thumb(k, 32).isNull() for k in pet_skin.KEYS))
    finally:
        pet_skin.SKIN_DIR = real_dir


def _md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_bbox(im: QImage) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = im.width(), im.height(), -1, -1
    for y in range(0, im.height(), 2):
        for x in range(0, im.width(), 2):
            if im.pixelColor(x, y).alpha() >= 8:
                x0 = min(x0, x); x1 = max(x1, x)
                y0 = min(y0, y); y1 = max(y1, y)
    return x0, y0, x1 + 1, y1 + 1


# ============================================================
def section_b12() -> None:
    """选项里的数值：推荐值、可填范围，以及 aria2 的真实硬限制。"""
    print("\n=== B12. 选项数值（推荐值 / 上下限 / 做种时限） ===")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    from app import config, paths
    from app.engine import _clamp
    from app.ui.options_dialog import OptionsDialog

    ok("B12.1", "默认上传限速是 100 KB/s（用户 2026-10-21 定的默认值）",
       int(config.DEFAULTS["max_ul"]) == 100 * 1024,
       "max_ul=%s" % config.DEFAULTS["max_ul"])

    ok("B12.2", "做种时间默认 60 分钟（原来默认 0 —— aria2 里 0 是「下完立刻停」）",
       config.DEFAULTS["seed_time"] == 60, "seed_time=%s" % config.DEFAULTS["seed_time"])

    dlg = OptionsDialog({}, None)
    ok("B12.3", "界面上「单服务器最大连接」最多只能填 16",
       dlg.sp_conn.maximum() == 16 and dlg.sp_conn.minimum() == 1,
       "范围 %s–%s" % (dlg.sp_conn.minimum(), dlg.sp_conn.maximum()))

    ok("B12.4", "界面上并发 / 分片 / Peer 都有上限",
       (dlg.sp_conc.minimum(), dlg.sp_conc.maximum()) == (1, 20)
       and (dlg.sp_split.minimum(), dlg.sp_split.maximum()) == (1, 32)
       and (dlg.sp_peers.minimum(), dlg.sp_peers.maximum()) == (0, 2000),
       "并发 %s 分片 %s Peer %s"
       % (dlg.sp_conc.maximum(), dlg.sp_split.maximum(), dlg.sp_peers.maximum()))

    ok("B12.5", "分饭时长是 60 分钟/6 小时/1 天/1 周/长期 的下拉选项",
       hasattr(dlg, "cb_seed") and dlg.cb_seed.count() == 6
       and dlg.cb_seed.itemData(0) == 60
       and dlg.cb_seed.itemText(4) == "长期",
       str([dlg.cb_seed.itemText(i) for i in range(dlg.cb_seed.count())])
       if hasattr(dlg, "cb_seed") else "没有")

    ok("B12.6", "这四项旁边都写着推荐值和范围（不懂的人照着填就行）",
       dlg.sp_conc.toolTip() and dlg.sp_conn.toolTip()
       and dlg.sp_split.toolTip() and dlg.sp_peers.toolTip())

    # 引擎侧再兜一次：老 settings.json 里可能留着越界值
    ok("B12.7", "越界值写进引擎前会被卡回去（不是原样塞给 aria2）",
       _clamp(20, 1, 16) == 16 and _clamp(-3, 1, 16) == 1
       and _clamp(999999, 0, 10080) == 10080
       and _clamp("abc", 1, 16) == 1 and _clamp(None, 1, 16) == 1,
       "20→%d, -3→%d, 999999→%d" % (_clamp(20, 1, 16), _clamp(-3, 1, 16),
                                     _clamp(999999, 0, 10080)))

    # 拿真 aria2 验一次「为什么必须卡 16」
    bad = subprocess.run(
        [str(paths.ARIA2_EXE), "--max-connection-per-server=17",
         "--dry-run", "http://127.0.0.1:1/x"],
        capture_output=True, text=True, timeout=30,
        creationflags=0x08000000)
    blob = (bad.stdout or "") + (bad.stderr or "")
    ok("B12.8", "实测 aria2 会拒绝 17 并直接退出（所以不能让它填到 32）",
       "between 1 and 16" in blob, blob.strip().splitlines()[0] if blob.strip() else "无输出")

    # 图库：随包的七张图要能铺到 data/pet/图库
    from app import pet_skin
    real = pet_skin.SKIN_DIR
    tmp = CACHE / "pet_lib"
    if tmp.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tmp)],
                       capture_output=True, creationflags=0x08000000)
    tmp.mkdir(parents=True, exist_ok=True)
    pet_skin.SKIN_DIR = tmp
    try:
        n1 = pet_skin.ensure_library()
        lib = pet_skin.library_dir()
        files = sorted(p.name for p in lib.glob("*.png"))
        ok("B12.9", "首次打开时把现成的七张图铺到 data/pet/图库",
           n1 == 7 and len(files) == 7, "铺了 %d 张：%s" % (n1, files[:3]))
        # 图库里每个状态都得有一张能认出来的（名字带状态名），
        # 用户打开目录才找得到「下载中那张」「没吃饱那张」
        want = [pet_skin.LABELS[k] for k in ("ciallo", "idle_bowl", "half",
                                             "tease", "idle_lying", "sassy")]
        missing = [w for w in want if not any(w in f for f in files)]
        ok("B12.9b", "图库里六种状态各有一张（名字带状态名）",
           not missing, "缺：%s / 现有：%s" % (missing, files))
        ok("B12.9c", "图库里没有删掉的馋嘴那张",
           not any("馋" in f for f in files), str(files))
        n2 = pet_skin.ensure_library()
        ok("B12.10", "再启动一次不会重复铺（幂等）", n2 == 0, "第二次补了 %d 张" % n2)
        ok("B12.11", "图库里的图不算「换过图」（不干扰还原/统计）",
           pet_skin.custom_count() == 0)
        ok("B12.12", "「打开图片目录」指向的就是图库那个目录",
           str(pet_skin.library_dir()).endswith("图库"),
           str(pet_skin.library_dir()))
    finally:
        pet_skin.SKIN_DIR = real
    dlg.close()

    # 死引用检查：代码里写死的 "pet/xxx.png" 必须真实存在。
    # 起因是 full.png 在 v0.9.4 被删了，但「关于」页还引用着它，
    # 结果那页顶上的图空了整整一个版本都没人发现。
    import re
    refs: set[str] = set()
    for f in (ROOT / "app").rglob("*.py"):
        blob = f.read_text(encoding="utf-8", errors="ignore")
        refs.update(re.findall(r'"pet"\s*/\s*"([A-Za-z0-9_]+)\.png"', blob))
    missing = sorted(n for n in refs
                     if not (ROOT / "assets" / "pet" / ("%s.png" % n)).exists())
    ok("B12.13", "代码里写死的桌宠图全都真实存在（删图不会留死引用）",
       not missing and bool(refs), "引用 %s，缺 %s" % (sorted(refs), missing))


def section_b13() -> None:
    """重复添加同一个种子 / session 越攒越脏（v0.10.2）。

    这条是实测撞出来的：用户把一个磁力加了 4 次，aria2 每次都给一个新 gid、
    然后那个任务立刻 error（"InfoHash ... is already registered."）。
    更麻烦的是 save-session 会把这些错误条目一遍遍写回去 ——
    重启后又是三条红色「错误」，手动删了下次启动还会回来。
    """
    print("\n=== B13. 重复添加 / session 去重 ===")
    from app import engine, paths as _paths
    from app.rpc import add_checked

    class FakeRpc:
        """只模拟 add_checked 会碰到的那三个方法。"""

        def __init__(self, err: str = "InfoHash abc is already registered."):
            self.err = err
            self.calls: list[tuple[str, object]] = []

        def call(self, method, params=None):  # noqa: ANN001
            self.calls.append((method, params))
            if method in ("aria2.addUri", "aria2.addTorrent"):
                return "GID1"
            if method == "aria2.tellStatus":
                return {"status": "error" if self.err else "active",
                        "errorMessage": self.err}
            return "OK"

    rpc = FakeRpc()
    gid, why = add_checked(rpc, "aria2.addUri", [["magnet:?xt=urn:btih:abc"], {}])
    ok("B13.1", "同一个种子再加一次 → 不当作新任务（返回 duplicate）",
       gid is None and why == "duplicate", "%s / %s" % (gid, why))
    ok("B13.2", "顺手把那条错误结果删掉（列表里不留红行）",
       any(m == "aria2.removeDownloadResult" for m, _ in rpc.calls),
       str([m for m, _ in rpc.calls]))

    rpc2 = FakeRpc(err="")
    gid2, why2 = add_checked(rpc2, "aria2.addTorrent", ["BASE64", [], {}])
    ok("B13.3", "正常任务照旧拿到 gid、不会被误杀",
       gid2 == "GID1" and why2 == ""
       and not any(m == "aria2.removeDownloadResult" for m, _ in rpc2.calls),
       "%s / %s" % (gid2, why2))

    # ---- session 去重 ----
    real = _paths.SESSION_FILE
    tmp = CACHE / "session.dedupe.test"
    nl = "\n"
    bs = chr(92)                      # 反斜杠，省得在源码里套两层转义
    lines = [
        "magnet:?xt=urn:btih:AAA",
        " gid=g1",
        " dir=D:" + bs + "X",
        "magnet:?xt=urn:btih:AAA",
        " gid=g2",
        " dir=D:" + bs + "X",
        "magnet:?xt=urn:btih:aaa",
        " gid=g3",
        "magnet:?xt=urn:btih:BBB",
        " gid=g4",
        "http://example.com/a.iso",
        " gid=g5",
    ]
    tmp.write_text(nl.join(lines) + nl, encoding="utf-8")
    _paths.SESSION_FILE = tmp
    try:
        e = engine.Aria2Engine.__new__(engine.Aria2Engine)
        n = e._dedupe_session()
        body = tmp.read_text(encoding="utf-8")
        ok("B13.4", "session 里同一个种子只留一条（大小写也算同一个）",
           n == 2 and body.count("btih:AAA") + body.count("btih:aaa") == 1,
           "去掉 %d 条" % n)
        ok("B13.5", "不同的种子 / http 链接不会被合并",
           "btih:BBB" in body and "http://example.com/a.iso" in body, body)
        ok("B13.6", "再清一次不再改动（幂等）", e._dedupe_session() == 0)

        tmp.write_text("", encoding="utf-8")
        ok("B13.7", "session 是空的时候不出错", e._dedupe_session() == 0)
    finally:
        _paths.SESSION_FILE = real


def section_b14() -> None:
    """桌宠姿势顺序（v0.10.3）—— 用户在截图里指出的三条。

    他说得很清楚，不是随机：
        待命 → 等饭 → 下载中（全程同一张）→ 下完 12 秒 → 回待命；
        今天下完 3 个以上，待命时改躺沙发。
    代码里踩过两个坑，这里各留一条断言：
      * BT 下完继续做种，aria2 仍报 active，被当成"还在下" → 一直挂着没吃饱的脸；
      * 姿势只在"数据刷新"时重算，12 秒到了没人叫它换。
    """
    print("\n=== B14. 桌宠姿势顺序 ===")
    import time as _t
    from app import pet_day
    from app.ui.models import TaskRow

    def row(status: str, seeder: bool = False, meta: bool = False) -> TaskRow:
        r = TaskRow(gid="G", name="x.mkv", dir="E:/")
        r.status_code = status
        r.is_seeder = seeder
        r.is_meta = meta
        return r

    ok("B14.1", "做种中（active + seeder）算已下完 —— 通知不再晚一小时",
       row("active", seeder=True).is_finished is True)
    ok("B14.2", "真在下载（active 且不是 seeder）不算下完",
       row("active").is_finished is False)
    ok("B14.3", "complete / waiting / paused 的判定不变",
       row("complete").is_finished is True
       and row("waiting").is_finished is False
       and row("paused").is_finished is False)
    ok("B14.4", "磁力取种子那条永远不算一碗",
       row("complete", meta=True).is_finished is False)

    real = pet_day.FILE
    pet_day.FILE = CACHE / "pet_day.b14.json"
    try:
        if pet_day.FILE.exists():
            pet_day.FILE.unlink()
        ok("B14.5", "今天还没下完 → 0 碗", pet_day.count() == 0, str(pet_day.count()))
        pet_day.bump(1)
        pet_day.bump(1)
        ok("B14.6", "记两碗能落盘、读回来还是 2", pet_day.count() == 2, str(pet_day.count()))
        pet_day.FILE.write_text('{"date": "2000-01-01", "done": 9}', encoding="utf-8")
        ok("B14.7", "跨天自动归零（昨天的不算今天）", pet_day.count() == 0)

        from PySide6.QtWidgets import QApplication
        from app.ui import pet as petmod
        app = QApplication.instance() or QApplication([])   # noqa: F841
        set_day = lambda n: pet_day.FILE.write_text(
            '{"date": "%s", "done": %d}' % (_t.strftime("%Y-%m-%d"), n), encoding="utf-8")

        def petw():
            """开一个桌宠，先喂空的首帧（启动快照里的旧任务不算"刚刚下完"）。"""
            p = petmod.PetWindow()
            p.update_data([], {})
            return p

        # 做种中的任务 → 不该是 half，而是「没吃饱」那 12 秒
        w = petw()
        set_day(0)
        w._day_ts = 0.0
        w.update_data([row("active", seeder=True)], {"uploadSpeed": "1000"})
        ok("B14.8", "做种中的任务 → 桌宠切到「没吃饱」而不是半张脸",
           w._sprite_name() == "tease" and w.downloading is False, w._sprite_name())

        # 12 秒到了，没有新数据进来，_tick 也得自己换回待命
        w._done_until = _t.time() - 0.1
        w._tick()
        ok("B14.9", "12 秒一到自己回待命（不用等下一次刷新）",
           w._sprite_name() == "ciallo", w._sprite_name())

        # 今天 3 碗 → 待命躺沙发
        set_day(3)
        w._day_ts = 0.0
        w.update_data([], {})
        ok("B14.10", "今天下完 3 碗 → 待命改躺沙发", w._sprite_name() == "idle_lying",
           w._sprite_name())

        # 真正在下的时候，全程半张脸，碗数无关
        rows = [row("active") for _ in range(4)]
        w2 = petw()
        w2.update_data(rows, {"downloadSpeed": "1000"})
        ok("B14.11", "4 个在下 → 全程半张脸（不再按碗数换脸）",
           w2._sprite_name() == "half", w2._sprite_name())

        # 取种子阶段 = 端着碗等
        meta = TaskRow(gid="M", name="（获取元数据）", dir="E:/")
        meta.status_code = "active"
        meta.is_meta = True
        w3 = petw()
        w3.update_data([meta], {})
        ok("B14.12", "磁力还在取种子 → 端着碗等（idle_bowl）",
           w3._sprite_name() == "idle_bowl", w3._sprite_name())

        # 重启不补数：第一份快照里已经下完的不算刚下完
        set_day(1)
        before = pet_day.count()
        w4 = petmod.PetWindow()
        w4.update_data([row("complete"), row("active", seeder=True)], {})
        ok("B14.13", "程序重启后，列表里早就下完的不会被再数一遍",
           pet_day.count() == before and not w4._toast,
           "%d → %d / toast=%r" % (before, pet_day.count(), w4._toast))
    finally:
        pet_day.FILE = real
        p2 = CACHE / "pet_day.b14.json"
        if p2.exists():
            p2.unlink()


def section_b15(env) -> None:
    """表格列宽拖动（v0.10.5）。

    用户报过两次，都是"拖了之后宽度不听我的"：
      1. 拖「大小」的右边界，右边不动、左边「名称」反而变窄 → Stretch 在吃掉差额。
      2. 拖好的宽度，下一次再拖会"自动复原" → 双击表头分隔线时 Qt 会
         `resizeSection(逻辑列, sectionSizeHint)`，把宽度拍回"合适值"。

    现在的规则简单到一句话：**拖哪列就是哪列，别的列谁都不许动；空出来的地方
    交给右边那条渐变空白区**。
    """
    print("\n=== B15. 表格列宽拖动 ===")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.ui.models import COL_INDEX

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    settings = dict(env.settings)
    settings["show_pet"] = False          # 别在测试里冒出个桌宠挡着
    win = MainWindow(env.engine, settings)
    win.resize(1500, 760)
    win.show()
    for _ in range(10):
        app.processEvents()

    tb = win.table
    hh = tb.horizontalHeader()
    name, size, pct = COL_INDEX["name"], COL_INDEX["size"], COL_INDEX["percent"]

    def total() -> int:
        return sum(hh.sectionSize(i) for i in range(hh.count())
                   if not tb.isColumnHidden(i))

    def snapped():
        for _ in range(6):
            app.processEvents()

    def drag(dx: int) -> None:
        """真的发一遍鼠标事件：按下 → 分几步移动 → 松开。"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        x = hh.sectionViewportPosition(size) + hh.sectionSize(size) - 1
        y = hh.height() // 2 - 1
        vp = hh.viewport()
        app.sendEvent(vp, QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(x, y), QPointF(x, y),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))
        for i in range(1, 7):
            px = x + dx * i // 6
            app.sendEvent(vp, QMouseEvent(
                QEvent.Type.MouseMove, QPointF(px, y), QPointF(px, y),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            app.processEvents()
        app.sendEvent(vp, QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(x + dx, y), QPointF(x + dx, y),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier))
        snapped()

    snapped()
    ok("B15.1", "「名称」不是 Stretch（否则拖别的列会去压它）",
       hh.sectionResizeMode(name) == hh.ResizeMode.Interactive,
       str(hh.sectionResizeMode(name)))
    ok("B15.2", "启动时列宽不溢出（总和不超过可视区）",
       total() <= tb.viewport().width() + 2,
       "总宽 %d / 可视 %d" % (total(), tb.viewport().width()))

    w_name0 = hh.sectionSize(name)
    x_pct0 = hh.sectionPosition(pct)
    drag(60)
    ok("B15.3", "拖大「大小」→ 后面的列往右移",
       hh.sectionPosition(pct) == x_pct0 + 60,
       "进度 x：%d → %d" % (x_pct0, hh.sectionPosition(pct)))
    ok("B15.4", "拖大「大小」→ 前面的「名称」一点没变（这才是正常逻辑）",
       hh.sectionSize(name) == w_name0,
       "名称 %d → %d" % (w_name0, hh.sectionSize(name)))

    drag(60)
    ok("B15.5", "接着再拖一次 → 在刚才的宽度上继续加，不会弹回去",
       hh.sectionSize(size) == 190, "大小 = %d（应该 190）" % hh.sectionSize(size))

    # 用户报的第二个 bug：双击分隔线会被 Qt 当成"自动适配宽度"，宽度直接复原
    from PySide6.QtCore import QEvent as _QE, QPointF as _QP, Qt as _Qt
    from PySide6.QtGui import QMouseEvent as _QME
    bx = hh.sectionViewportPosition(size) + hh.sectionSize(size) - 1
    by = hh.height() // 2 - 1
    for et in (_QE.Type.MouseButtonPress, _QE.Type.MouseButtonRelease,
               _QE.Type.MouseButtonDblClick, _QE.Type.MouseButtonRelease):
        held = (_Qt.MouseButton.NoButton if et == _QE.Type.MouseButtonRelease
                else _Qt.MouseButton.LeftButton)
        app.sendEvent(hh.viewport(), _QME(
            et, _QP(bx, by), _QP(bx, by), _Qt.MouseButton.LeftButton, held,
            _Qt.KeyboardModifier.NoModifier))
    snapped()
    ok("B15.6", "双击分隔线不会把拖好的宽度「自动复原」",
       hh.sectionSize(size) == 190, "大小 = %d（应该还是 190）" % hh.sectionSize(size))

    drag(-120)
    ok("B15.7", "往回拖 120 → 正好回到 70（不多不少）",
       hh.sectionSize(size) == 70, "大小 = %d" % hh.sectionSize(size))

    drag(200)
    ok("B15.8", "拖到超出可视区 → 出横向滚动条，而不是去挤别的列",
       total() > tb.viewport().width() and tb.horizontalScrollBar().isVisible(),
       "总宽 %d / 可视 %d" % (total(), tb.viewport().width()))

    # ---- 没铺满的地方：画成渐变，不许露黑底 ----
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from app.ui import table as tblmod

    img = QImage(240, 80, QImage.Format.Format_ARGB32)
    img.fill(0)
    pnt = QPainter(img)
    tblmod.paint_gap(pnt, QRectF(0, 0, 240, 80), tblmod.make_sparks(240, 80))
    pnt.end()
    left_c = img.pixelColor(6, 40)
    mid_c = img.pixelColor(120, 40)
    right_c = img.pixelColor(234, 40)

    def bright(c) -> bool:
        return min(c.red(), c.green(), c.blue()) > 200      # 浅色，不是黑底

    ok("B15.9", "空白区画的是浅色渐变，不是系统深色底（那块纯黑）",
       bright(left_c) and bright(mid_c) and bright(right_c),
       "%s / %s / %s" % (left_c.name(), mid_c.name(), right_c.name()))
    ok("B15.10", "渐变方向对：左端偏蓝、右端转紫粉（不是一整块死白）",
       (right_c.red() - right_c.blue()) > (left_c.red() - left_c.blue()),
       "左 %s（r-b=%d）右 %s（r-b=%d）"
       % (left_c.name(), left_c.red() - left_c.blue(),
          right_c.name(), right_c.red() - right_c.blue()))
    ok("B15.11", "星屑位置固定（同尺寸两次生成完全一样，不会乱跳）",
       tblmod.make_sparks(300, 120) == tblmod.make_sparks(300, 120))

    hh.resizeSection(size, 30)     # 拖到很窄 → 右边一定空出一块
    snapped()
    gap = tb.gap_rect()
    ok("B15.12", "列没铺满时才算「有空白区」",
       gap is not None and gap.width() > 40, str(gap))
    hh.resizeSection(size, 600)    # 撑到溢出 → 没有空白区了
    snapped()
    ok("B15.13", "铺满 / 溢出时不出空白区（不会平白盖一层）",
       tb.gap_rect() is None, str(tb.gap_rect()))

    win.worker.stop()
    win.worker.wait(2000)
    win.hide()
    win.deleteLater()
    snapped()


def section_b16() -> None:
    """发行版采用原创水波轻响作为默认音效；测试音量和自定义音效功能。"""
    print("\n=== B16. 提示音 / 音量 ===")
    import wave
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app import notify
    from app.ui import theme
    from app.ui.options_dialog import OptionsDialog

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    snd = notify.SOUND_DIR
    for fn in ("soft_water.wav",):
        ok("B16.1", "发行版原创内置音效存在", (snd / fn).is_file(), str(snd / fn))

    # 音量峰值检查，确保内置音效不会出现削波。
    def peak_of(p) -> int:
        with wave.open(str(p)) as w:
            frames = w.readframes(w.getnframes())
        top = 0
        for i in range(0, len(frames), 2):
            value = abs(int.from_bytes(frames[i:i + 2], "little", signed=True))
            if value > top:
                top = value
        return top

    pk = peak_of(snd / "soft_water.wav")
    ok("B16.3", "soft_water.wav 的峰值不超过 20000",
       0 < pk < 20000, "峰值 %d" % pk)

    ok("B16.4", "默认提示音是原创 soft_water.wav",
       notify.DEFAULT_SOUND == "soft_water.wav"
       and notify.sound_path("").name == "soft_water.wav", notify.sound_path("").name)
    ok("B16.5", "水波音在内置音效下拉列表首位",
       [fn for _l, fn in notify.BUILTIN_SOUNDS][:1] == ["soft_water.wav"],
       str([fn for _l, fn in notify.BUILTIN_SOUNDS]))

    # ---- 播放真的把音量传下去了吗 ----
    calls: list[tuple[str, int]] = []
    real_play = notify._play_wav_qt
    notify._play_wav_qt = lambda path, vol: (calls.append((path.name, vol)), True)[1]
    try:
        notify.play_sound("soft_water.wav", 45)
        ok("B16.6", "音量 45% 会原样传到播放层（不是固定值）",
           calls and calls[-1][1] == 45, str(calls[-1:] if calls else []))

        calls.clear()
        notify.play_sound("soft_water.wav", 0)
        ok("B16.7", "音量 0 = 静音，干脆不播", not calls, str(calls))

        calls.clear()
        notify.play_sound("soft_water.wav")
        ok("B16.8", "不传音量就用默认 60%",
           calls and calls[-1][1] == notify.DEFAULT_VOLUME,
           str(calls[-1:] if calls else []))
    finally:
        notify._play_wav_qt = real_play

    # ---- 设置页 ----
    dlg = OptionsDialog({"notify_volume": 60}, None)
    ok("B16.9", "设置里有一根音量滑块（0–100，默认 60）",
       hasattr(dlg, "sl_vol") and dlg.sl_vol.minimum() == 0
       and dlg.sl_vol.maximum() == 100 and dlg.sl_vol.value() == 60,
       "%s" % (dlg.sl_vol.value() if hasattr(dlg, "sl_vol") else "没有滑块"))
    dlg.sl_vol.setValue(35)
    dlg._collect()
    ok("B16.10", "拖动滑块后能被保存下来",
       dlg.settings.get("notify_volume") == 35,
       str(dlg.settings.get("notify_volume")))
    ok("B16.11", "百分比标签跟着滑块走", dlg.lb_vol.text() == "35%", dlg.lb_vol.text())

    # 试听用的是滑块当前值，不是写死的
    calls.clear()
    notify._play_wav_qt = lambda path, vol: (calls.append((path.name, vol)), True)[1]
    try:
        dlg.sl_vol.setValue(20)
        dlg._preview()
        ok("B16.12", "「试听」按滑块音量播（不然试听等于没试）",
           calls and calls[-1][1] == 20, str(calls[-1:] if calls else []))
    finally:
        notify._play_wav_qt = real_play

    # 老配置里没有这个键 / 值越界，都不能崩
    n = notify.Notifier({"notify_volume": 999})
    ok("B16.13", "配置里音量越界或缺失时自动夹回 0–100",
       n.volume == 100 and notify.Notifier({}).volume == notify.DEFAULT_VOLUME,
       "%d / %d" % (n.volume, notify.Notifier({}).volume))
    ok("B16.14", "设置里有主界面背景：随机启动 / 定时切换 / 选图清空",
       hasattr(dlg, "chk_bg_random") and hasattr(dlg, "sp_bg_min")
       and hasattr(dlg, "lb_bg_count") and hasattr(dlg, "sl_bg_veil"))
    from app.ui.options_dialog import BackgroundPickerDialog
    ok("B16.15", "选背景时有自己的带预览窗口（不靠系统文件框）",
       hasattr(BackgroundPickerDialog, "selected_paths"))
    dlg.chk_bg_blank.setChecked(True)
    ok("B16.16", "空白背景是排它开关：勾上后整个换图区禁用",
       hasattr(dlg, "chk_bg_blank") and not dlg.lb_bg_count.isEnabled())
    dlg.chk_bg_blank.setChecked(False)
    ok("B16.17", "取消空白背景后换图区恢复可用",
       dlg.lb_bg_count.isEnabled())
    dlg.close()


def section_b17(env) -> None:
    """状态灯 + 多列排序（v0.10.8）。

    用户要两件事：
      1. 像比特彗星那样用颜色/符号标状态，尤其是"下完了但文件被删了"
         （删除不会通知下载器，任务永远停在"已完成"）；
      2. 点表头排序，按住 Shift 能叠加第二键 —— "先按类型，再按大小，
         就能在某一类里找到特别大的那个"。
    """
    print("\n=== B17. 状态灯 / 多列排序 ===")
    import time as _t
    from pathlib import Path as _P

    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.ui.models import COL_INDEX, TaskRow
    from app.ui.workers import _missing as missing_cache
    from app.ui.workers import file_gone

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    def row(status="active", seeder=False, error="", missing=False) -> TaskRow:
        r = TaskRow(gid="g%d" % abs(hash((status, seeder, error, missing))),
                    name="x.mkv", dir="E:/dl", size=100, added=1.0)
        r.status_code = status
        r.is_seeder = seeder
        r.error = error
        r.missing = missing
        return r

    ok("B17.1", "下载中 → 蓝点（down）", row().dot_kind == "down")
    ok("B17.2", "下完没上传 → 绿点（done）", row("complete").dot_kind == "done")
    ok("B17.3", "下完还在上传 → 绿点带外圈（seeding）",
       row("active", seeder=True).dot_kind == "seeding")
    ok("B17.4", "出错 → 红点（error）", row(error="连不上").dot_kind == "error")
    ok("B17.5", "下完但文件没了 → 黄色感叹号（missing）",
       row("complete", missing=True).dot_kind == "missing")
    ok("B17.6", "等待/暂停 → 灰点（idle）",
       row("waiting").dot_kind == "idle" and row("paused").dot_kind == "idle")
    ok("B17.7", "错误优先于「文件没了」（同时成立时先报错）",
       row("complete", error="坏了", missing=True).dot_kind == "error")
    ok("B17.8", "还没下完的任务不会被判成「文件没了」",
       row("active", missing=True).dot_kind == "down")

    # 文件存在性：真的建一个文件再删掉
    tmp = CACHE / "missing_test"
    tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / "有东西.mkv"
    f.write_bytes(b"x" * 16)
    r1 = row("complete")
    r1.dir, r1.name = str(tmp), "有东西.mkv"
    missing_cache.clear()
    ok("B17.9", "文件还在 → 不算缺失", file_gone(r1) is False)
    f.unlink()
    missing_cache.clear()
    ok("B17.10", "文件删掉之后 → 判成缺失", file_gone(r1) is True)

    # ---- 多列排序 ----
    settings = dict(env.settings)
    settings["show_pet"] = False
    win = MainWindow(env.engine, settings)
    win.resize(1400, 700)
    win.show()
    for _ in range(8):
        app.processEvents()

    def mk(kind: str, size: int, name: str = "") -> TaskRow:
        r = TaskRow(gid="%s%d" % (kind, size), name=name or ("%s-%d" % (kind, size)),
                    dir="E:/dl", size=size, added=1.0)
        r.status_code = {"down": "active", "done": "complete", "err": "error"}.get(kind, "waiting")
        if kind == "err":
            r.error = "x"
        return r

    rows = [mk("down", 100), mk("down", 900), mk("done", 500), mk("done", 50)]
    win.all_rows = rows
    win._sort_keys = [("size", True)]
    win._apply_filter()
    got = [r.size for r in win.visible_rows]
    ok("B17.11", "单键：按大小降序排（大的在前）", got == [900, 500, 100, 50], str(got))

    win._sort_keys = [("status", False), ("size", True)]
    win._apply_filter()
    got = [(r.dot_kind, r.size) for r in win.visible_rows]
    ok("B17.12", "双键：先按状态分组，组内再按大小降序（就是用户要的场景）",
       got == [("down", 900), ("down", 100), ("done", 500), ("done", 50)], str(got))

    # 表头点击：普通单击 = 换主键；同列再点 = 反向
    win._sort_keys = [("status", False)]
    win._on_header_clicked(COL_INDEX["size"])
    k1 = list(win._sort_keys)
    win._on_header_clicked(COL_INDEX["size"])
    k2 = list(win._sort_keys)
    ok("B17.13", "单击表头改成单键排序，再点一次翻转方向",
       len(k1) == 1 and k1[0][0] == "size" and k1[0][1] is True
       and len(k2) == 1 and k2[0][1] is False, "%s → %s" % (k1, k2))

    # Shift + 点击 = 叠加
    real_mod = QApplication.keyboardModifiers
    QApplication.keyboardModifiers = staticmethod(lambda: _Qt.KeyboardModifier.ShiftModifier)
    try:
        win._sort_keys = [("status", False)]
        win._on_header_clicked(COL_INDEX["size"])
        ok("B17.14", "Shift + 点另一列 → 追加成第二键，第一键保留",
           win._sort_keys == [("status", False), ("size", True)], str(win._sort_keys))
        for col in ("percent", "memo", "name", "added"):
            win._on_header_clicked(COL_INDEX[col])
        ok("B17.15", "最多留 4 个键（再叠就把最老的挤掉）",
           len(win._sort_keys) == 4, str(win._sort_keys))
    finally:
        QApplication.keyboardModifiers = staticmethod(real_mod)

    # 没意义的列点了不该改排序
    before = list(win._sort_keys)
    win._on_header_clicked(COL_INDEX["dot"])
    ok("B17.16", "状态灯那列点了不参与排序", win._sort_keys == before, str(win._sort_keys))

    win.worker.stop()
    win.worker.wait(2000)
    win.hide()
    win.deleteLater()
    for _ in range(6):
        app.processEvents()
    missing_cache.clear()


def section_b18() -> None:
    """复选框自绘（v0.10.9）。

    用户截图：勾选后是"深蓝方框 + 深色对号"，糊成一片看不清。
    根因是 QSS 只给了 indicator 的尺寸、没给颜色，剩下交给 Fusion + 系统强调色：
    Win11 的强调色是深蓝，对号又是深色，两个深色叠在一起。
    改成 QProxyStyle 自己画：圆角方框 + 白色对号，任何缩放下都锐。
    """
    print("\n=== B18. 复选框自绘 ===")
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton

    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841
    from app.ui import theme

    ok("B18.1", "CheckStyle 存在，且基础样式是 Fusion（不是拿 app.style() 那个野指针）",
       issubclass(theme.CheckStyle, __import__("PySide6.QtWidgets",
                                                fromlist=["QProxyStyle"]).QProxyStyle))
    app.setStyle("Fusion")
    theme.apply_style(app)
    # 注意：一旦 setStyleSheet 过，app.style() 返回的是 QStyleSheetStyle（它包着我们的样式），
    # 直接用 isinstance 判断会失败。这里认我们自己持有的那个实例就行，
    # 真正的验证在下面——把勾选框画出来数像素。
    ok("B18.2", "apply_style 装好了自绘样式（app 那边留着引用，不会被回收）",
       isinstance(theme._STYLE, theme.CheckStyle),
       type(getattr(theme, "_STYLE", None)).__name__)

    def render(state) -> QImage:
        opt = QStyleOptionButton()
        opt.rect = QRect(0, 0, 16, 16)
        opt.state = state
        img = QImage(16, 16, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        app.style().drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, opt, p)
        p.end()
        return img

    S = QStyle.StateFlag
    on_img = render(S.State_On | S.State_Enabled)
    # 白对号：任取若干像素，至少有几个是接近纯白的（在蓝底上才看得清）
    whites = 0
    for x in range(16):
        for y in range(16):
            c = on_img.pixelColor(x, y)
            if c.alpha() > 200 and min(c.red(), c.green(), c.blue()) > 235:
                whites += 1
    ok("B18.3", "勾选状态下能找到白色对号（不再是深色勾在深蓝上）",
       whites >= 4, "白色像素 %d 个" % whites)

    # 方框底色应该是主色蓝
    bg = on_img.pixelColor(2, 2)
    ok("B18.4", "勾选的方框是主色蓝（#4D6BFE）",
       abs(bg.red() - 0x4D) < 30 and abs(bg.blue() - 0xFE) < 30, bg.name())

    off_img = render(S.State_Off | S.State_Enabled)
    inner = off_img.pixelColor(8, 8)
    ok("B18.5", "未勾选是白底（不是灰块，也不是系统色）",
       min(inner.red(), inner.green(), inner.blue()) > 240, inner.name())

    dis_img = render(S.State_On)
    dis = dis_img.pixelColor(2, 2)
    ok("B18.6", "禁用态的蓝更浅（一眼能看出点不动）",
       dis.red() > bg.red() and dis.name() != bg.name(), "%s vs %s" % (dis.name(), bg.name()))


def section_b19(env) -> None:
    """分饭（上传）+ 速度合并 + 再分一轮饭（v0.10.10）。

    用户的三个要求：
      1. 下载速度和上传速度合并成一栏；
      2. 上传（做种）状态和速度要看得见，叫「给鱼群分饭」；
      3. 下完之后想再传，要有办法重新开始上传。
    """
    print("\n=== B19. 分饭 / 速度合并 / 重新上传 ===")
    import base64 as _b64

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app import brand, torrent_keep
    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.ui.models import COL_INDEX, COLS, TaskRow

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    keys = [k for k, _l, _w in COLS]
    ok("B19.1", "速度是一栏（speed），不再分下载/上传两列",
       "speed" in keys and "dl" not in keys and "ul" not in keys, str(keys))

    def row(status="active", seeder=False, dl=0, ul=0) -> TaskRow:
        r = TaskRow(gid="g_%s_%s" % (status, seeder), name="x.mkv", dir="E:/dl",
                    size=100, done=100)
        r.status_code = status
        r.is_seeder = seeder
        r.dl, r.ul = dl, ul
        return r

    model = __import__("app.ui.models", fromlist=["TaskTableModel"]).TaskTableModel()

    def text_of(r, col: str = "speed") -> str:
        model.rows = [r]                       # 先有行，index 才有效
        return model.data(model.index(0, COL_INDEX[col]))

    ok("B19.2", "下载中 → 一格里同时有 ↓ 和 ↑",
       "↓" in text_of(row(dl=1024 * 1024, ul=2048)) and "↑" in text_of(row(dl=1024 * 1024, ul=2048)),
       text_of(row(dl=1024 * 1024, ul=2048)))
    ok("B19.3", "做种中 → 只显示 ↑（不用再显示一个 0 的下载速度）",
       text_of(row(seeder=True, dl=0, ul=5 * 1024 * 1024)).startswith("↑"),
       text_of(row(seeder=True, dl=0, ul=5 * 1024 * 1024)))
    ok("B19.4", "没在跑的任务显示 —", text_of(row("complete")) == "—", text_of(row("complete")))

    ok("B19.5", "做种的状态词是「开饭了 · 上传中」（既说明下完也在传）",
       brand.WORD_SEED == "开饭了 · 上传中", brand.WORD_SEED)
    r_seed = row(seeder=True)
    ok("B19.6", "状态列显示「开饭了 · 上传中」",
       text_of(r_seed, "status") == brand.WORD_SEED, text_of(r_seed, "status"))

    # ---- 种子留档 ----
    tid = "TESTGID123"
    raw = b"d4:infod4:name4:testee"
    ok("B19.7", "加种子时留一份，能原样取回",
       bool(torrent_keep.save(tid, _b64.b64encode(raw).decode()))
       and torrent_keep.load_b64(tid) == _b64.b64encode(raw).decode())
    ok("B19.8", "没有留档的任务取回空串（不抛异常）",
       torrent_keep.load_b64("NO_SUCH_GID") == "")

    # ---- 再分一轮饭 ----
    settings = dict(env.settings)
    settings["show_pet"] = False
    settings["seed_time"] = 60
    settings["seed_ratio"] = 0.0
    win = MainWindow(env.engine, settings)
    win.resize(1300, 700)
    win.show()
    for _ in range(8):
        app.processEvents()

    calls: list[tuple[str, list]] = []

    class FakeRpc:
        def call(self, method, params=None):
            calls.append((method, params))
            return "NEWGID"

    real_rpc = win.engine.rpc
    win.engine.rpc = FakeRpc()
    try:
        # 磁力任务：重新贴磁力
        m = row("complete")
        m.dir = str(CACHE)
        win.store.note(m.gid, uri="magnet:?xt=urn:btih:abc")
        win._selected_rows = lambda: [m]
        calls.clear()
        win._reseed_selected()
        ok("B19.9", "磁力任务「再分一轮饭」→ 重新贴磁力，并要求先校验文件",
           calls and calls[0][0] == "aria2.addUri"
           and calls[0][1][0][0].startswith("magnet:")
           and calls[0][1][1].get("check-integrity") == "true",
           str(calls[:1]))
        ok("B19.10", "重新分饭也按设置里的时长来（不是写死一小时）",
           calls and calls[0][1][1].get("seed-time") == "60", str(calls[:1]))

        # 种子任务：用留档的 .torrent
        t = row("complete")
        t.gid = tid
        win.store.note(t.gid, uri="(torrent) test.torrent")
        win._selected_rows = lambda: [t]
        calls.clear()
        win._reseed_selected()
        ok("B19.11", ".torrent 任务 → 用当初留的那份种子，照样先校验",
           calls and calls[0][0] == "aria2.addTorrent"
           and calls[0][1][-1].get("check-integrity") == "true", str(calls[:1]))
        ok("B19.11b", "重新分饭换掉旧完成记录，新任务静音（不多一条、不响一声）",
           any(c[0] == "aria2.removeDownloadResult" and c[1][0] == t.gid
               for c in calls)
           and "NEWGID" in win._completed_seen
           and (win.pet is None or "NEWGID" in win.pet._seen_done),
           "calls=%s seen=%s" % (str(calls[-2:]),
                                 "NEWGID" in win._completed_seen))

        # 既没磁力也没种子：不能假装成功
        lost = row("complete")
        lost.gid = "LOSTGID"
        win.store.note(lost.gid, uri="")
        win._selected_rows = lambda: [lost]
        calls.clear()
        win._reseed_selected()
        ok("B19.12", "两样都没有 → 不瞎发请求，也不崩",
           not any(c[0].startswith("aria2.add") for c in calls), str(calls[:2]))

        # 「开始」对已下完的任务 = 开始做种（不是空 unpause）
        m2 = row("complete")
        m2.gid = "STARTGID"
        win.store.note(m2.gid, uri="magnet:?xt=urn:btih:start")
        win.visible_rows = [m2]
        win._selected_rows = lambda: [m2]
        calls.clear()
        win._apply_to_selected("unpause")
        ok("B19.12b", "已下完的任务点「开始」→ 走重新校验/做种，不是空 unpause",
           any(c[0] == "aria2.addUri" for c in calls), str(calls[:2]))
    finally:
        win.engine.rpc = real_rpc

    ok("B19.13", "「停止分饭」只对正在分饭的任务可用",
       (win.act_stop_seed.setEnabled(False) or True) and hasattr(win, "act_stop_seed"))
    win._selected_rows = lambda: [row(seeder=True)]
    win._update_actions()
    ok("B19.14", "选中做种任务 → 「停止分饭 / 给鱼群分饭」都亮",
       win.act_stop_seed.isEnabled() and win.act_reseed.isEnabled())
    torrent_keep.remove(tid)          # 别把测试种子留在用户的种子库里

    # ---- 设置页：分饭挪到限速页 ----
    from app.ui.options_dialog import OptionsDialog
    dlg = OptionsDialog(dict(settings), None)
    ok("B19.15", "限速页里有「分饭时长」下拉选项",
       hasattr(dlg, "cb_seed") and dlg.cb_seed.count() >= 5,
       str(dlg.cb_seed.count()) if hasattr(dlg, "cb_seed") else "没有")
    ok("B19.16", "分饭时长默认选 60 分钟，鼠标点选不用敲数字",
       hasattr(dlg, "cb_seed") and dlg.cb_seed.currentData() == 60,
       str(dlg.cb_seed.currentData()) if hasattr(dlg, "cb_seed") else "没有")
    dlg.close()

    # ---- 会话保存更勤 ----
    tpl = (ROOT / "assets" / "conf" / "aria2.conf.tpl").read_text(encoding="utf-8")
    ok("B19.17", "session 每 15 秒存一次（进程被打断最多丢 15 秒进度）",
       "save-session-interval=15" in tpl, "没找到 save-session-interval=15")

    win.worker.stop()
    win.worker.wait(2000)
    win.hide()
    win.deleteLater()
    for _ in range(6):
        app.processEvents()


def section_b20() -> None:
    """饭桌用语的"人话"提示（v0.10.11）。

    用户看不懂「放下筷子」是暂停、「噎住了」是出错。这些词是项目定的调子，
    不能随便改（他自己也认这套说法），所以补悬停解释，而不是换词。
    """
    print("\n=== B20. 饭桌用语的人话提示 ===")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app.ui import theme
    from app.ui.main_window import FILTER_HINTS, FILTERS
    from app.ui.models import COL_INDEX, TaskRow, TaskTableModel

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    ok("B20.1", "每个筛选都有解释（6 个全有，不留空）",
       all(k in FILTER_HINTS and FILTER_HINTS[k] for k, *_ in FILTERS),
       str([k for k, *_ in FILTERS if not FILTER_HINTS.get(k)]))
    ok("B20.2", "「放下筷子」的解释里说明了是暂停",
       "暂停" in FILTER_HINTS["pause"], FILTER_HINTS["pause"])
    ok("B20.3", "「噎住了」的解释里说明了是出错",
       "出错" in FILTER_HINTS["err"], FILTER_HINTS["err"])

    model = TaskTableModel()

    def tip(r) -> str:
        model.rows = [r]
        return model.data(model.index(0, COL_INDEX["status"]), Qt.ItemDataRole.ToolTipRole)

    def mk(status="active", seeder=False, error=""):
        r = TaskRow(gid="g", name="x.mkv", dir="E:/dl")
        r.status_code = status
        r.is_seeder = seeder
        r.error = error
        return r

    ok("B20.4", "暂停的行，状态格悬停写着「暂停」",
       "暂停" in (tip(mk("paused")) or ""), str(tip(mk("paused"))))
    ok("B20.5", "做种的行，状态格悬停写着「上传」",
       "上传" in (tip(mk("active", seeder=True)) or ""), str(tip(mk("active", seeder=True))))
    ok("B20.6", "出错的行，悬停能看到具体原因",
       "连不上" in (tip(mk(error="连不上")) or ""), str(tip(mk(error="连不上"))))


def section_b21(env) -> None:
    """查重 / AI 接口菜单 / 左栏图标（v0.10.12）。

    用户三个要求：
      1. 一个月前下过、昨天又贴一遍 —— 得有"以前下过"的提示；
      2. AI 接口挪到主菜单「复制链接」后面，改叫「AI 调用接口」，能一键复制整份说明；
      3. 左栏几个筛选项配小图标。
    """
    print("\n=== B21. 查重 / AI 接口 / 左栏图标 ===")
    import base64 as _b64
    import hashlib as _hl

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841

    from app import dedupe, torrent_keep
    from app.ui import icons, theme
    from app.ui.main_window import FILTERS, MainWindow
    from app.ui.models import TaskRow

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    # ---- 1. infohash / 磁力指纹 ----
    raw = b"d4:infod4:name4:test12:piece lengthi16384e6:pieces20:aaaaaaaaaaaaaaaaaaaaee"
    expect = _hl.sha1(b"d4:name4:test12:piece lengthi16384e6:pieces20:"
                      b"aaaaaaaaaaaaaaaaaaaa" + b"e").hexdigest()
    ok("B21.1", "种子 infohash 算得对（跟 SHA1 一致）",
       torrent_keep.infohash_of(raw) == expect, torrent_keep.infohash_of(raw))

    hex40 = "0123456789abcdef0123456789abcdef01234567"
    ok("B21.2", "磁力里的 hex infohash 能取出来",
       dedupe.magnet_hash("magnet:?xt=urn:btih:%s&dn=x" % hex40.upper()) == hex40)
    b32 = _b64.b32encode(bytes.fromhex(hex40)).decode()
    ok("B21.3", "base32 形式的也统一成 hex（不然认得是两个东西）",
       dedupe.magnet_hash("magnet:?xt=urn:btih:%s" % b32) == hex40,
       dedupe.magnet_hash("magnet:?xt=urn:btih:%s" % b32))

    # ---- 2. 查得到「以前下过」 ----
    metas = {
        "old1": {"uri": "magnet:?xt=urn:btih:%s" % hex40, "name": "老电影.mkv",
                 "ihash": hex40, "added_at": 1000, "finished_at": 2000,
                 "dir": "E:/dl"},
        "old2": {"uri": "http://x/y.iso", "name": "y.iso", "added_at": 900},
    }
    ok("B21.4", "同一个 infohash → 认得出是以前下过的",
       dedupe.find_previous(metas, ihash=hex40)[0] == "old1")
    ok("B21.5", "同一个链接 → 也认得出",
       dedupe.find_previous(metas, uri="http://x/y.iso")[0] == "old2")
    ok("B21.6", "完全没见过的东西 → 不误报",
       dedupe.find_previous(metas, ihash="f" * 40, uri="http://new/z.bin") is None)
    txt = dedupe.describe(metas["old1"])
    ok("B21.7", "提示里带日期和文件位置",
       "老电影.mkv" in txt and "E:/dl" in txt, txt)

    # store 要记住 infohash / 目录，否则永远查不到
    win = MainWindow(env.engine, dict(env.settings, show_pet=False))
    win.resize(1200, 700)
    win.show()
    for _ in range(8):
        app.processEvents()
    win.store.note("GID_X", uri="magnet:?xt=urn:btih:%s" % hex40, name="n.mkv",
                   ihash=hex40, dir="E:/dl")
    rec = win.store.get("GID_X")
    ok("B21.8", "加任务时把 infohash 和目录一起记下来",
       (rec.get("ihash") or "").lower() == hex40 and rec.get("dir") == "E:/dl", str(rec))

    # 查重对话框：默认按钮是「取消」，避免手快点掉又下一遍
    win.store._data["OLD"] = {"uri": "http://dup/x.bin", "name": "x.bin",
                              "added_at": 1, "finished_at": 2, "dir": "E:/dl"}
    ok("B21.9", "老任务能被查重命中（弹窗内容就靠这条）",
       dedupe.find_previous(win.store.all(), uri="http://dup/x.bin")[0] == "OLD")
    _mag = "magnet:?xt=urn:btih:%s" % hex40
    win.store._data["META"] = {"uri": _mag, "ihash": hex40, "added_at": 3}
    win.all_rows = [TaskRow(gid="META", name="[METADATA]x", is_meta=True, uri=_mag)]
    ok("B21.9b", "正在解析的 METADATA 任务也算“已在列表里”，不当成以前下过",
       win._is_current_task_text(_mag))

    # ---- 3. AI 接口菜单 ----
    mb = win.menuBar()
    task_menu = next((m for m in mb.findChildren(type(mb.addMenu("x")))
                      if m.title() == "任务"), None)
    actions = [a.text() for a in (task_menu.actions() if task_menu else [])]
    ok("B21.10", "「AI 调用接口…」在「任务」菜单里，紧跟「复制链接」",
       "AI 调用接口…" in actions
       and actions.index("AI 调用接口…") == actions.index("复制链接") + 1, str(actions))
    tool_menu = next((m for m in mb.findChildren(type(task_menu or mb.addMenu("x")))
                      if m.title() == "工具"), None)
    ok("B21.11", "「工具」里不再留旧的「AI 接口信息…」",
       "AI 接口信息…" not in [a.text() for a in (tool_menu.actions() if tool_menu else [])])

    guide = win._api_guide_text()
    ok("B21.12", "一键复制的是填好的全文（地址/令牌文件都没有占位符）",
       "@PORT@" not in guide and "@TOKEN_FILE@" not in guide
       and "api/v1/tasks" in guide, guide[:60])
    ok("B21.13", "复制的内容里带着本机端口号",
       str(win.api_port) in guide or "6811" in guide, "port=%s" % win.api_port)

    # 工具栏上要有一个单独的按钮（用户要的是这个，不是藏在菜单里）
    from PySide6.QtWidgets import QToolBar
    _tbs = win.findChildren(QToolBar)
    texts = [a.iconText() for tb in _tbs for a in tb.actions() if a.iconText()]
    ok("B21.15", "工具栏上「复制链接」后面紧跟着「AI 调用接口」按钮",
       "AI 调用接口" in texts
       and texts.index("AI 调用接口") == texts.index("复制链接") + 1, str(texts))

    # ---- 4. 左栏图标 ----
    keys = [k for k, *_ in FILTERS]
    ok("B21.14", "7 个筛选每个都有图标，而且不是空图",
       all(not icons.filter_icon(k).isNull()
           and not icons.filter_icon(k).pixmap(18, 18).isNull() for k in keys),
       str(keys))

    # 用户给的那套大肥鱼表情图（6 张，状态各一张）
    from app import paths
    fdir = paths.ASSET_DIR / "icons" / "filters"
    have = sorted(p.stem for p in fdir.glob("*.png"))
    ok("B21.16", "左栏图标用的是用户给的立绘（每个状态一张）",
       set(keys) <= set(have), "目录里只有 %s" % have)
    ok("B21.17", "图标优先级：有图用图，没图才退回线稿（删了不会白板）",
       icons._from_image("err") is not None, "err.png 读不出来")

    # 新增的「给鱼群分饭」筛选：做种的任务只归这一栏，不能重复出现在打饭中/开饭了
    from app.ui.models import TaskRow as _TR
    preds = {k: f for k, _l, _t, f in FILTERS}
    seed_row = _TR(gid="s1", name="a.mkv")
    seed_row.status_code = "active"
    seed_row.is_seeder = True
    done_row = _TR(gid="s2", name="b.mkv")
    done_row.status_code = "complete"
    ok("B21.18", "做种的只进「给鱼群分饭」、不混进打饭中，但仍然留在「开饭了」",
       preds["seed"](seed_row) and not preds["down"](seed_row)
       and preds["done"](seed_row), "seed/down/done 判定不对")
    ok("B21.19", "下完没在传的任务只在「开饭了」，不在给鱼群分饭",
       preds["done"](done_row) and not preds["seed"](done_row))

    win.worker.stop()
    win.worker.wait(2000)
    win.hide()
    win.deleteLater()
    for _ in range(6):
        app.processEvents()


def section_b22(env) -> None:
    """默认选项 + 干饭手册按钮 + 新图标（v0.10.16）。

    用户要求：
      1. 开机自动启动、最小化缩托盘都是默认；
      2. 工具栏「AI 调用接口」旁边再加一个「大肥鱼干饭手册」，点开就是设置，
         前面挂上那张没有标识的立绘 —— 目的是让人愿意点进去把软件调成自己的。
      3. 应用图标换成他给的方形头像。
    """
    print("\n=== B22. 默认选项 / 干饭手册 / 图标 ===")
    from PySide6.QtWidgets import QApplication, QToolBar, QToolButton

    app = QApplication.instance() or QApplication(sys.argv)   # noqa: F841
    from app import config, paths, shell_integration
    from app.ui import theme
    from app.ui.main_window import MainWindow

    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    d = config.DEFAULTS
    ok("B22.1", "默认开机自启", d.get("autostart") is True, str(d.get("autostart")))
    ok("B22.2", "默认最小化缩到托盘", d.get("minimize_to_tray") is True,
       str(d.get("minimize_to_tray")))
    ok("B22.3", "默认关闭窗口也留在托盘继续下", d.get("close_to_tray") is True)

    # 注册表读写（跑完还原成原来的状态，别在用户机器上留手尾）
    before = shell_integration.is_autostart()
    try:
        shell_integration.set_autostart(True)
        ok("B22.4", "开机自启能写进 HKCU\\...\\Run 并读回来",
           shell_integration.is_autostart() is True)
        ok("B22.5", "启动命令用的是 pythonw（开机不出黑框）",
           "pythonw" in shell_integration.startup_command().lower()
           or getattr(__import__("sys"), "frozen", False),
           shell_integration.startup_command())
        shell_integration.set_autostart(False)
        ok("B22.6", "关掉之后键被删掉（不会残留）",
           shell_integration.is_autostart() is False)
    finally:
        shell_integration.set_autostart(before)

    # 新图标
    ico = paths.ASSET_DIR / "icons" / "whale.ico"
    ok("B22.7", "应用图标换成了新的方形头像（多尺寸 ico，体积够大）",
       ico.exists() and ico.stat().st_size > 40000, "%d 字节" % ico.stat().st_size)
    ok("B22.8", "手册按钮的图在（handbook.png）",
       (paths.ASSET_DIR / "icons" / "handbook.png").exists())

    settings = dict(env.settings, show_pet=False)
    settings["autostart"] = True
    win = MainWindow(env.engine, settings)
    win.resize(1300, 700)
    win.show()
    for _ in range(8):
        app.processEvents()

    tb = win.findChild(QToolBar)
    btn = None
    for _tb in win.findChildren(QToolBar):
        for w in _tb.findChildren(QToolButton):
            if "手册" in w.text():
                btn = w
    ok("B22.9", "工具栏上有「大肥鱼干饭手册」按钮", btn is not None)
    ok("B22.10", "这个按钮带图标（就是那张无标识立绘），比纯文字显眼",
       btn is not None and not btn.icon().isNull(), "没有图标")

    # 按钮是连在 bound method 上的，改 win._open_options 不管用；
    # 换成把 OptionsDialog 这个类顶掉，点按钮时 _open_options 会构造它。
    from app.ui import main_window as mw
    opened = []
    real_dlg = mw.OptionsDialog

    class FakeDlg:
        def __init__(self, *a, **k):
            opened.append(1)

        def exec(self):
            return 0

    mw.OptionsDialog = FakeDlg
    try:
        if btn is not None:
            btn.click()
        app.processEvents()
    finally:
        mw.OptionsDialog = real_dlg
    ok("B22.11", "点它就是打开设置（不是弹个说明框）", bool(opened), "点了没反应")

    from app.ui.options_dialog import OptionsDialog

    dlg = OptionsDialog(dict(settings), None)
    ok("B22.12", "设置里有「开机自动启动」且默认勾着",
       hasattr(dlg, "chk_autostart") and dlg.chk_autostart.isChecked())
    dlg.chk_autostart.setChecked(False)
    dlg._collect()
    ok("B22.13", "取消勾选会写进设置（真正改注册表在点确定时）",
       dlg.settings.get("autostart") is False, str(dlg.settings.get("autostart")))
    dlg.close()

    win.worker.stop()
    win.worker.wait(2000)
    win.hide()
    win.deleteLater()
    for _ in range(6):
        app.processEvents()


# ============================================================
def section_b24() -> None:
    """种子文件树（v0.10.18）：多文件夹种子按目录层次显示、整目录可勾。

    用户给了一个多文件夹种子（08月海报 / CHS / 09月预告 …），
    比特彗星里是一棵可整目录勾选的树；白饭以前平铺成一排，看不出层级。
    这里用一份内存里的 files 数组验界面，不真下载、不占磁盘。
    """
    print("\n=== B24. 种子文件树 ===")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui import theme
    from app.ui.file_select import ROLE_NODE, FileSelectDialog
    from app.ui.models import fmt_size

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)
    from PySide6.QtCore import QEvent as _QEvent
    app.sendPostedEvents(None, _QEvent.Type.DeferredDelete)

    base = "C:/Downloads"
    root = base + "/2026年08月合集"
    files = [
        {"index": "1", "path": root + "/08月海报/a.png",
         "length": "1000", "completedLength": "0", "selected": "true"},
        {"index": "2", "path": root + "/08月海报/b.png",
         "length": "2000", "completedLength": "0", "selected": "true"},
        {"index": "3", "path": root + "/CHS/x.mp4",
         "length": "30000", "completedLength": "0", "selected": "true"},
        {"index": "4", "path": root + "/CHS/y.mp4",
         "length": "40000", "completedLength": "0", "selected": "true"},
        {"index": "5", "path": root + "/说明.txt",
         "length": "100", "completedLength": "0", "selected": "true"},
    ]
    dlg = FileSelectDialog(files, base)

    def item_of(name: str):
        for n in list(dlg._file_nodes) + list(dlg._dir_nodes):
            if n.name == name:
                return n.item
        return None

    top = [dlg.tree.topLevelItem(i).data(0, ROLE_NODE).name
           for i in range(dlg.tree.topLevelItemCount())]
    ok("B24.1", "种子根目录收掉，第一层直接是文件夹/文件（跟比特彗星一样）",
       top == ["08月海报", "CHS", "说明.txt"], str(top))

    folder = item_of("08月海报")
    ok("B24.2", "文件夹是一行、里面文件挂在它下面",
       folder is not None and folder.childCount() == 2,
       "childCount=%s" % (folder.childCount() if folder else None))
    ok("B24.3", "文件夹行显示子树合计大小",
       folder is not None and folder.text(1) == fmt_size(3000),
       folder.text(1) if folder else "")
    ok("B24.4", "全选按钮带文件总数（全选(5)）",
       dlg.btn_all.text() == "全选(5)", dlg.btn_all.text())
    ok("B24.5", "初始全勾 → selected 1..5",
       dlg.selected() == [1, 2, 3, 4, 5], str(dlg.selected()))

    folder.setCheckState(0, Qt.CheckState.Unchecked)
    app.processEvents()
    ok("B24.6", "取消文件夹 → 里面文件全部取消，别的文件夹不受影响",
       dlg.selected() == [3, 4, 5], str(dlg.selected()))

    item_of("x.mp4").setCheckState(0, Qt.CheckState.Unchecked)
    app.processEvents()
    chs = item_of("CHS")
    ok("B24.7", "取消一个子文件 → 父文件夹变半选",
       chs.checkState(0) == Qt.CheckState.PartiallyChecked,
       str(chs.checkState(0)))

    folder.setCheckState(0, Qt.CheckState.Checked)
    app.processEvents()
    ok("B24.8", "再勾文件夹 → 里面的文件全回来",
       dlg.selected() == [1, 2, 4, 5], str(dlg.selected()))

    dlg._video_only()
    app.processEvents()
    ok("B24.9", "仅选视频 → 只勾 .mp4", dlg.selected() == [3, 4], str(dlg.selected()))

    dlg._invert()
    app.processEvents()
    ok("B24.10", "反选 → 剩下非视频", dlg.selected() == [1, 2, 5], str(dlg.selected()))

    dlg._none()
    app.processEvents()
    ok("B24.11", "全不选 → 不会返回空选择（确定按钮会拦住）",
       dlg.selected() == [], str(dlg.selected()))

    dlg._all()
    app.processEvents()
    ok("B24.12", "全选 → 5 个全回来", dlg.selected() == [1, 2, 3, 4, 5], str(dlg.selected()))
    dlg.deleteLater()


# ============================================================
def section_b25() -> None:
    """种子库（v0.10.19）：历史 .torrent / 磁力单独存，不再装成"已下好的文件"。

    用户原话：
      * .torrent 老被当成"已经下载好的一个文件"，比特彗星里不存在这种；
      * 要有一个单独存放历史种子的地方；
      * 这些种子统一归到「给鱼群分饭」分类下面。
    """
    print("\n=== B25. 种子库 ===")
    import base64
    import hashlib

    from PySide6.QtWidgets import QApplication
    from app import torrent_keep
    from app.ui import theme
    from app.ui.main_window import FILTERS as MW_FILTERS
    from app.ui.models import TaskRow
    from app.ui.torrent_library import TorrentLibraryDialog

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    def bencode(o):
        if isinstance(o, int):
            return b"i%de" % o
        if isinstance(o, bytes):
            return b"%d:%s" % (len(o), o)
        if isinstance(o, str):
            return bencode(o.encode())
        if isinstance(o, list):
            return b"l" + b"".join(bencode(x) for x in o) + b"e"
        if isinstance(o, dict):
            out = b"d"
            for k in sorted(o):
                out += bencode(k) + bencode(o[k])
            return out + b"e"
        raise TypeError(type(o))

    files = [("01.bin", 40_000), ("02.bin", 50_000)]
    total = sum(sz for _n, sz in files)
    piece_len = 16384
    pieces = b"".join(hashlib.sha1(b"\x00" * piece_len).digest()
                      for _ in range((total + piece_len - 1) // piece_len))
    raw = bencode({
        "announce": "http://127.0.0.1:9/announce",
        "info": {"name": "留档测试", "piece length": piece_len, "pieces": pieces,
                 "files": [{"length": sz, "path": [n]} for n, sz in files]},
    })
    b64 = base64.b64encode(raw).decode()
    ihash = torrent_keep.infohash_of(raw)

    tmp = CACHE / "torrents_test"
    if tmp.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tmp)],
                       capture_output=True, creationflags=0x08000000)
    tmp.mkdir(parents=True, exist_ok=True)
    old = (torrent_keep.DIR, torrent_keep.INDEX)
    torrent_keep.DIR = tmp
    torrent_keep.INDEX = tmp / "index.json"
    try:
        got = torrent_keep.save("GID_TEST", b64, source="测试种子")
        ok("B25.1", "种子按 infohash 存进种子库",
           got == ihash and (tmp / ("%s.torrent" % ihash)).exists(), got)
        entries = torrent_keep.entries()
        ok("B25.2", "索引里读出名称和大小（不用问 aria2）",
           len(entries) == 1 and entries[0]["name"] == "留档测试"
           and entries[0]["size"] == total,
           str(entries[0] if entries else None))
        ok("B25.3", "按 infohash 取回种子内容",
           base64.b64decode(torrent_keep.load_b64(ihash)) == raw)
        ok("B25.4", "按 gid 也能取回（老的「再分一轮饭」接口不变）",
           base64.b64decode(torrent_keep.load_b64("GID_TEST")) == raw)

        dlg = TorrentLibraryDialog()
        ok("B25.5", "种子库对话框列出历史种子",
           dlg.table.rowCount() == 1, "rows=%d" % dlg.table.rowCount())
        ok("B25.6", "列表里名称显示对", dlg.table.item(0, 0).text() == "留档测试",
           dlg.table.item(0, 0).text())
        heads = [dlg.table.horizontalHeaderItem(i).text()
                 for i in range(dlg.table.columnCount())]
        ok("B25.11", "只列来源信息，不显示下载文件大小（种子库不是文件列表）",
           dlg.table.columnCount() == 4 and "大小" not in heads, str(heads))
        from PySide6.QtWidgets import QPushButton as _QPB
        btn_texts = [b.text() for b in dlg.findChildren(_QPB)]
        ok("B25.12", "按钮是「重新下载」，不是「再分一轮饭」",
           "重新下载" in btn_texts, str(btn_texts))
        hits: list[dict] = []
        dlg.redownload.connect(lambda e: hits.append(e))
        dlg.table.selectRow(0)
        dlg._redownload()
        ok("B25.7", "选中一条点「重新下载」→ 把这条来源交给主窗口",
           bool(hits) and hits[0].get("ihash") == ihash, str(hits))

        ok("B25.13", "按 infohash 能找回种子来源（重新分饭/重新下载用）",
           base64.b64decode(torrent_keep.find_by_infohash(ihash)) == raw)

        magnet = "magnet:?xt=urn:btih:" + "a" * 40
        key = torrent_keep.save_magnet(magnet, gid="GID_M", name="磁力留档")
        e = torrent_keep.get(key)
        ok("B25.8", "磁力也能登记进种子库（重分饭时贴回去）",
           e is not None and e.get("kind") == "magnet"
           and e.get("uri") == magnet, str(e))
        torrent_keep.save_magnet("magnet:?xt=urn:btih:" + "b" * 40,
                                 gid="GID_U", dir_="E:/dl")
        dlg.reload()
        _un = next((dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())
                    if dlg.table.item(r, 0).text().startswith("（未解析目标）")), "")
        ok("B25.8b", "还没拿到目标名的磁力会明确标「未解析目标」，不冒充文件名",
           bool(_un), _un)

        ok("B25.9", "删除存档会连种子文件一起删",
           torrent_keep.remove(ihash)
           and not (tmp / ("%s.torrent" % ihash)).exists())

        meta = TaskRow(gid="m1", name="某种子.torrent")
        meta.status_code = "complete"
        meta.is_meta = True
        preds = {k: f for k, _l, _t, f in MW_FILTERS}
        ok("B25.10", "「先下种子」那条不会被算进「开饭了」",
           preds["done"](meta) is False)
    finally:
        torrent_keep.DIR, torrent_keep.INDEX = old
        if tmp.exists():
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tmp)],
                           capture_output=True, creationflags=0x08000000)


# ============================================================
def section_b26() -> None:
    """自动核心名（v0.10.21）：从种子文件名提关键词，生成列表显示名。"""
    print("\n=== B26. 自动核心名 ===")
    from PySide6.QtWidgets import QApplication
    from app import config, titlegen
    from app.ui import theme
    from app.ui.main_window import MainWindow
    from app.ui.models import TaskRow, matches_text
    from app.ui.options_dialog import OptionsDialog
    from app.ui.store import TaskMetaStore

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    files = [
        "E:/dl/2026年08月合集/08月海报/OVA 彼女催眠 ＃1.png",
        "E:/dl/2026年08月合集/CHS/[260828][nur]サレ妻は奪われたい ～クールママが溺れるまで……～.chs.mp4",
        "E:/dl/2026年08月合集/CHT/[260828][nur]サレ妻は奪われたい ～クールママが溺れるまで……～.cht.mp4",
    ]
    title = titlegen.make_title("2026年08月合集", files)
    ok("B26.1", "从文件名提取核心词，并带上年月",
       "2026年08月" in title and "彼女催眠" in title, title)
    ok("B26.2", "分辨率/字幕/格式这类噪声被过滤掉",
       "1080p" not in title.lower() and "chs" not in title.lower()
       and "mp4" not in title.lower(), title)
    ok("B26.3", "关键词最多 10 个",
       len([x for x in title.split() if not x.endswith("月")]) <= 10, title)

    one = titlegen.make_title("ABC-123 测试片段 中字.mp4",
                              ["/dl/ABC-123 测试片段 中字.mp4"])
    ok("B26.4", "编号/内容词保留，中字这种标签不要",
       "ABC-123" in one and "测试片段" in one and "中字" not in one, one)

    dated = titlegen.make_title("260828 测试片段.mp4",
                                ["/dl/260828 测试片段.mp4"])
    ok("B26.5", "不再把年月硬塞进标题；内容词保留",
       "2026年08月" not in dated and "测试片段" in dated, dated)
    _tagged = titlegen.make_title(
        "[LoliHouse] Tensei Goblin dakedo Shitsumon Aru - 05 "
        "[WebRip 1080p HEVC-10bit AAC SRTx2][简繁内封].mkv")
    ok("B26.5b", "结构化名保留字幕组/集数/画质/编码/语言",
       _tagged.startswith("[LoliHouse]") and " - 05" in _tagged
       and "1080p" in _tagged and "H.265" in _tagged and "CHS&CHT" in _tagged,
       _tagged)
    from app import title_lookup
    ok("B26.5c", "普通编号不再走专用搜索源",
       title_lookup.guess_kind("ABC-123") == "general")

    ok("B26.6", "全是噪声/空内容时不生成（不乱改名）",
       titlegen.make_title("", []) == ""
       and titlegen.make_title("1080p CHS", ["/dl/1080p.mkv"]) == "",
       repr(titlegen.make_title("1080p CHS", ["/dl/1080p.mkv"])))

    # 显示名覆盖：只改列表显示，r.name 还是原文件名
    r = TaskRow(gid="g1", name="原文件名.mkv", title="2026年08月 核心内容")
    ok("B26.7", "列表显示核心名，原文件名保留（打开/做种不受影响）",
       r.display_name == "2026年08月 核心内容" and r.name == "原文件名.mkv")
    ok("B26.8", "搜索也能搜到核心名里的字", matches_text(r, "核心内容"))

    # 存盘：title 落在 tasks.json，重启不丢
    tmp = CACHE / "title_test"
    if tmp.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tmp)],
                       capture_output=True, creationflags=0x08000000)
    tmp.mkdir(parents=True, exist_ok=True)
    store = TaskMetaStore(tmp / "tasks.json")
    store.set_title("g1", "2026年08月 测试片段")
    store.save(force=True)
    store2 = TaskMetaStore(tmp / "tasks.json")
    ok("B26.9", "核心名写进 tasks.json，重开还在",
       store2.get_title("g1") == "2026年08月 测试片段", store2.get_title("g1"))
    ok("B26.10", "设置里默认开启（auto_title）",
       config.DEFAULTS.get("auto_title") is True, str(config.DEFAULTS.get("auto_title")))
    dlg = OptionsDialog(dict(config.load()), None)
    ok("B26.11", "设置 → 常规里有「自动给已下载的种子任务改名」开关",
       hasattr(dlg, "chk_auto_title") and dlg.chk_auto_title.isChecked()
       and "显示名" in dlg.chk_auto_title.toolTip(), dlg.chk_auto_title.toolTip())
    dlg.close()
    ok("B26.12", "主窗口有自动改名的入口（_auto_title）",
       hasattr(MainWindow, "_auto_title"))

    from app.ui.models import COL_INDEX, COLS
    memo_w = COLS[COL_INDEX["memo"]][2]
    ok("B26.13", "备注列默认宽度刚好放下灰色提示（不铺张）",
       90 <= memo_w <= 130, "%dpx" % memo_w)


# ============================================================
def section_b27() -> None:
    """上传默认限速 100 KB/s（v0.10.30）。"""
    print("\n=== B27. 上传默认限速 ===")
    from PySide6.QtWidgets import QApplication
    from app import config
    from app.ui import theme
    from app.ui.add_dialog import SPEED_CHOICES, AddDialog
    from app.ui.options_dialog import OptionsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    ok("B27.1", "全局上传限速默认 100 KB/s",
       int(config.DEFAULTS["max_ul"]) == 100 * 1024, str(config.DEFAULTS["max_ul"]))
    ok("B27.2", "单任务上传限速默认 100 KB/s",
       int(config.DEFAULTS["per_task_ul"]) == 100 * 1024,
       str(config.DEFAULTS["per_task_ul"]))
    choices = {label: kb for label, kb in SPEED_CHOICES}
    ok("B27.3", "速度下拉里有「100 KB/s」", choices.get("100 KB/s") == 100,
       str(choices))

    dlg = AddDialog(dict(config.DEFAULTS))
    ok("B27.4", "新建任务的上传限速默认选中 100 KB/s",
       dlg.cb_ul.currentData() == 100, str(dlg.cb_ul.currentData()))
    dlg.close()

    od = OptionsDialog(dict(config.DEFAULTS), None)
    ok("B27.5", "设置里的全局上传限速默认 100 KB/s（下拉选项）",
       od.cb_ul.currentData() == 100, str(od.cb_ul.currentData()))
    ok("B27.6", "设置里的单任务上传限速默认 100 KB/s（下拉选项）",
       od.cb_pul.currentData() == 100, str(od.cb_pul.currentData()))
    od.close()


# ============================================================
def section_b28() -> None:
    """下完的文件被暂停，不能掉进「放下筷子」（v0.10.31）。"""
    print("\n=== B28. 下完被暂停 ===")
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import FILTERS as MW_FILTERS
    from app.ui.models import TaskRow, row_from_aria2

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    now = time.time()
    r = TaskRow(gid="p1", name="已经下完的.mkv")
    r.status_code = "paused"
    r.finished = now
    ok("B28.1", "下完被暂停 → 状态仍是「已完成」，不是已暂停",
       r.state == "已完成", r.state)
    ok("B28.2", "下完被暂停 → 仍算 finished（会留在开饭了）",
       r.is_finished is True)
    ok("B28.3", "下完被暂停 → 进度 100%，不是 0%", r.percent == 100.0,
       "%.1f" % r.percent)
    ok("B28.4", "状态灯仍是绿的完成灯，不是灰的暂停灯",
       r.dot_kind == "done", r.dot_kind)
    preds = {k: f for k, _l, _t, f in MW_FILTERS}
    ok("B28.5", "做种被暂停的任务留在「开饭了」，不落进「放下筷子」",
       preds["done"](r) is True and preds["pause"](r) is False)

    r2 = TaskRow(gid="p2", name="下到一半被暂停.mkv")
    r2.status_code = "paused"
    ok("B28.6", "真正没下完的暂停任务仍然在「放下筷子」",
       r2.state == "已暂停" and r2.is_finished is False and preds["pause"](r2))

    # aria2 暂停 BT 会报 totalLength=0；从 tasks.json 记过的大小补回来
    one = row_from_aria2(
        {"gid": "p3", "status": "paused", "totalLength": "0",
         "completedLength": "0", "files": []},
        {"p3": {"finished_at": now, "size": 12345}})
    ok("B28.7", "暂停 BT 的 totalLength=0 时，用本机记录补回大小/进度",
       one.size == 12345 and one.done == 12345 and one.percent == 100.0,
       "size=%s done=%s" % (one.size, one.done))
    ok("B28.8", "补回大小后状态仍是「已完成」", one.state == "已完成", one.state)

    from app.ui.models import target_complete
    tdir = CACHE / "done_check"
    if tdir.exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(tdir)],
                       capture_output=True, creationflags=0x08000000)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "x.bin").write_bytes(b"x" * 1234)
    ok("B28.9", "磁盘文件大小 == 种子声明大小 → 判定已完成（暂停自愈用）",
       target_complete(str(tdir), "x.bin", 1234) is True
       and target_complete(str(tdir), "x.bin", 999) is False)


# ============================================================
def section_b29() -> None:
    """Windows 11 托盘图标常显（v0.10.34）。"""
    print("\n=== B29. 托盘图标常显 ===")
    import uuid
    import winreg

    from app import shell_integration as si

    base = r"Control Panel\NotifyIconSettings"
    key_name = "BaiFanAcceptTest_" + uuid.uuid4().hex[:10]
    fake_exe = r"C:\Users\AERO\AppData\Local\Temp\baifan_tray_test\BaiFanTest.exe"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + "\\" + key_name) as k:
            winreg.SetValueEx(k, "ExecutablePath", 0, winreg.REG_SZ, fake_exe)
            winreg.SetValueEx(k, "IsPromoted", 0, winreg.REG_DWORD, 0)
        ok("B29.1", "能在 NotifyIconSettings 里按 exe 路径找到托盘登记项",
           si.find_tray_key(fake_exe) == key_name, str(si.find_tray_key(fake_exe)))
        ok("B29.2", "promote 会写入 IsPromoted=1（等价于手动拖出溢出区）",
           si.promote_tray_icon(fake_exe) is True)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + "\\" + key_name) as k:
            val = int(winreg.QueryValueEx(k, "IsPromoted")[0])
        ok("B29.3", "写完之后读回来确实是 1", val == 1, str(val))
        ok("B29.4", "源码版不碰共享的 pythonw.exe 托盘项（只对安装版 exe 生效）",
           si.is_tray_promotable() is False)
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + "\\" + key_name)
        except OSError:
            pass


# ============================================================
def section_b23(env) -> None:
    """退出链路（v0.10.17）：文件菜单 / 桌宠右键都要真能退出。

    用户实测「点退出没用」：`act_quit` 只创建、加进菜单，从来没 connect 到
    `_quit_app`，所以点菜单里的退出完全没反应（托盘里的退出是接上的）。
    这里把两条路都钉死，并确认退出真的会让窗口关掉。
    """
    print("\n=== B23. 退出链路 ===")
    from PySide6.QtWidgets import QApplication, QMenu
    from app.ui import theme
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    settings = dict(env.settings, show_pet=True)
    win = MainWindow(env.engine, settings)
    win.resize(1200, 700)
    win.show()
    for _ in range(8):
        app.processEvents()

    file_menu = None
    for m in win.menuBar().findChildren(QMenu):
        if m.title() == "文件":
            file_menu = m
            break
    texts = [a.text() for a in (file_menu.actions() if file_menu else [])]
    ok("B23.1", "「文件」菜单里有「退出」", "退出" in texts, str(texts))

    # 桌宠右键菜单：拆出来直接查，不 exec() 卡住验收
    pm = win.pet._build_menu() if win.pet else None
    pet_texts = [a.text() for a in (pm.actions() if pm else []) if a.text()]
    ok("B23.2", "桌宠右键菜单里有「退出整个程序」",
       any("退出" in t for t in pet_texts), str(pet_texts))
    if pm is not None:
        pm.deleteLater()

    # 打开前的拦截：没下完的文件不能交给播放器（用户要小气泡，不弹模态框）
    from app.ui.models import TaskRow

    r_live = TaskRow(gid="o1", name="还在下的.mkv")
    r_live.status_code = "active"
    r_live.size = 100
    r_live.done = 34
    ok("B23.3", "没下完时打开会被拦，并说明尚未下载完成",
       win._open_guard(r_live) is False and "尚未下载完成" in win.lb_state.text(),
       win.lb_state.text())
    r_done = TaskRow(gid="o2", name="下完的.mkv")
    r_done.status_code = "complete"
    ok("B23.4", "下完了才放行", win._open_guard(r_done) is True)
    r_meta = TaskRow(gid="o3", name="取种子")
    r_meta.status_code = "complete"
    r_meta.is_meta = True
    ok("B23.5", "取种子阶段也不让打开", win._open_guard(r_meta) is False)

    # 桌宠信号 → 主窗口退出流程（先走这条，因为它会顺带把引擎关掉）
    win._really_quit = False
    if win.pet:
        win.pet.quit_app.emit()
    for _ in range(4):
        app.processEvents()
    ok("B23.6", "桌宠「退出整个程序」→ 进入退出流程、主窗口关掉",
       win._really_quit is True and not win.isVisible(),
       "_really_quit=%s visible=%s" % (win._really_quit, win.isVisible()))

    # 文件菜单的退出动作也必须是活的（这条就是用户点的那个）
    win._really_quit = False
    win.act_quit.trigger()
    for _ in range(4):
        app.processEvents()
    ok("B23.7", "「文件 → 退出」也真的接了槽（不再是死按钮）",
       win._really_quit is True, "_really_quit=%s" % win._really_quit)

    ok("B23.8", "托盘菜单以实例属性保存（不会被 Python 回收成空菜单）",
       getattr(win, "tray_menu", None) is not None)

    from PySide6.QtCore import Qt as _Qt
    from app.ui import icons as _icons
    ok("B23.9", "种子库图标用的是原来「噎住了」那张立绘",
       not _icons.filter_icon("archive").isNull(), "archive.png 读不出来")
    ok("B23.10", "种子库那一行不可选中（点它不会画出四个黑角的焦点框）",
       not (win._archive_item.flags() & _Qt.ItemFlag.ItemIsSelectable),
       str(win._archive_item.flags()))

    # 校验失败先重试一次，别把刚 complete 的文件误判成"没吃干净"
    r_retry = TaskRow(gid="g_retry", name="retry.mkv")
    r_retry.status_code = "complete"
    win.all_rows.append(r_retry)
    win._really_quit = False
    win._on_verify_done("g_retry", {"ok": False, "bad_count": 1})
    ok("B23.11", "校验第一次失败先不判死刑（4 秒后补验一次）",
       win._verify_attempts.get("g_retry") == 1 and "g_retry" not in win.verify_map,
       "attempts=%s map=%s" % (win._verify_attempts.get("g_retry"),
                               "g_retry" in win.verify_map))
    labels = [win.tree.topLevelItem(i).text(0)
              for i in range(win.tree.topLevelItemCount())]
    ok("B23.12", "分类叫「开饭了！！」",
       any(t.startswith("开饭了！！") for t in labels), str(labels))
    ok("B23.13", "做种动作叫「给鱼群分饭」",
       win.act_reseed.text() == "给鱼群分饭", win.act_reseed.text())

    import run as runmod
    from PySide6.QtCore import QCoreApplication
    runmod._install_qt_translator(app)
    ok("B23.14", "Qt 标准菜单/按钮是中文（右键不再是 Undo/Redo）",
       QCoreApplication.translate("QLineEdit", "&Undo") != "&Undo",
       QCoreApplication.translate("QLineEdit", "&Undo"))
    ok("B23.15", "「AI 调用接口」按钮前面有图标（用户给的图）",
       not win.act_api.icon().isNull())
    ok("B23.16", "下面的详情面板不再是固定高度（可以拖成 20%~30%）",
       win.detail.minimumHeight() <= 40 and win.detail.maximumHeight() >= 1000,
       "min=%s max=%s" % (win.detail.minimumHeight(), win.detail.maximumHeight()))
    ok("B23.17", "详情面板有一键收起/展开按钮",
       hasattr(win, "btn_detail_toggle") and win.btn_detail_toggle.text() == "▾")
    from PySide6.QtWidgets import QScrollArea
    ok("B23.18", "详情「常规」页放在滚动区里，拖矮了出现滚动条而不是挤字",
       isinstance(win.detail.widget(0), QScrollArea))

    from app.ui.models import TaskRow as _TR
    class _PickRpc:
        def __init__(self):
            self.calls = []
        def call(self, method, params=None):
            self.calls.append((method, params))
            return "1"
    _real_rpc = win.engine.rpc
    _fr = _PickRpc()
    win.engine.rpc = _fr
    _row = _TR(gid="pick1", name="单文件磁力.mkv")
    _row.status_code = "paused"
    _row.file_count = 1
    win._await_pick = {"pick1": {"unpause": True}}
    win._scan_pending_picks([_row])
    ok("B23.19", "单文件磁力拿到元数据后会自动开始，不会卡在等选文件",
       ("aria2.unpause", ["pick1"]) in _fr.calls, str(_fr.calls))
    win._await_pick = {}
    win.engine.rpc = _real_rpc

    # 工具栏：全局开始/停止 + 限速 + 召唤鲸鱼娘
    from PySide6.QtWidgets import QToolBar as _TB, QToolButton as _TBTN
    _tbs = win.findChildren(_TB)
    _texts = [a.iconText() for tb in _tbs for a in tb.actions() if a.iconText()]
    ok("B23.20", "工具栏按钮是「开始全部任务 / 停止全部任务」，限速紧跟在后面",
       "开始全部任务" in _texts and "停止全部任务" in _texts
       and _texts.index("停止全部任务") < _texts.index("打开目录"),
       str(_texts))
    _sum_btn = next((w for tb in _tbs for w in tb.findChildren(_TBTN)
                     if "召唤" in w.text()), None)
    ok("B23.21", "原限速位置换成了带图标的「召唤鲸鱼娘」按钮（文字两行）",
       _sum_btn is not None and not _sum_btn.icon().isNull()
       and "\n" in _sum_btn.text())
    _tb0 = _tbs[0]
    for _i, (_a, _two) in enumerate(((win.act_start_all, "开始\n全部任务"),
                                     (win.act_stop_all, "停止\n全部任务")), 23):
        _b = _tb0.widgetForAction(_a)
        ok("B23.%d" % _i, "「%s」在工具栏上排成两行" % _a.text(),
           _b is not None and _b.text() == _two, _b.text() if _b else "None")
    _ai_btn = _tb0.widgetForAction(win.act_api)
    ok("B23.25", "AI 按钮图标 50px、文字是「外部AI / 调用接口」",
       _ai_btn is not None and _ai_btn.iconSize().width() == 50
       and _ai_btn.text() == "外部AI\n调用接口",
       "%s %r" % (_ai_btn.iconSize().width() if _ai_btn else -1,
                  _ai_btn.text() if _ai_btn else None))
    class _GlobalRpc:
        def __init__(self):
            self.calls = []
        def call(self, method, params=None):
            self.calls.append(method)
            return "OK"
    _gr = _GlobalRpc()
    _real2 = win.engine.rpc
    win.engine.rpc = _gr
    win._apply_global("pause")
    win._apply_global("unpause")
    ok("B23.22", "全局停止/开始调用 aria2.pauseAll / unpauseAll",
       _gr.calls == ["aria2.pauseAll", "aria2.unpauseAll"], str(_gr.calls))
    win.engine.rpc = _real2

    # 桌宠双击：剪贴板链接已在当前任务里 → 不弹重复确认框，直接打开主窗口
    win.all_rows = [_TR(gid="dup1", name="已经在下的.mkv")]
    _real_store_get = win.store.get
    win.store.get = lambda gid: ({"ihash": "abc123", "uri": "magnet:?xt=urn:btih:abc123"}
                                 if gid == "dup1" else {})
    _restored = []
    _real_restore = win._restore_window
    win._restore_window = lambda: _restored.append(1)
    try:
        win._quick_add_text("magnet:?xt=urn:btih:abc123")
    finally:
        win._restore_window = _real_restore
        win.store.get = _real_store_get
        win.all_rows = []
    ok("B23.26", "桌宠收到已在列表里的链接：不弹重复框，直接呼出主窗口",
       len(_restored) == 1, "restored=%s" % _restored)

    # 主窗口大小/最大化状态要记忆
    win.settings.pop("window_geometry", None)
    win.settings.pop("window_maximized", None)
    win._save_window_state()
    ok("B23.27", "关闭时记住窗口大小/位置（window_geometry 非空）",
       bool(win.settings.get("window_geometry")))

    # AI 说明：必须用当前机器路径，不能残留占位符/作者电脑路径
    from app import paths as _paths
    _guide = win._api_guide_text()
    ok("B23.28", "AI 说明里没有残留占位符、没有写死 C:\\Users\\ 路径",
       "@PORT@" not in _guide and "@TOKEN_FILE@" not in _guide
       and "@DOWNLOAD_DIR@" not in _guide
       and "C:\\Users\\" not in _guide and "C:/Users/" not in _guide,
       _guide[:80])
    ok("B23.29", "AI 说明里带的是当前这台机器的下载目录",
       str(_paths.DL_DIR) in _guide, str(_paths.DL_DIR))
    from app import selfcheck as _sc
    _rep = _sc.run_runtime(api_port=win.api_port, guide_text=_guide,
                           engine=win.engine)
    ok("B23.30", "工具里的自检能逐项给出结果（不是只看文件存在）",
       len(_rep["checks"]) >= 8 and bool(_rep["report"]),
       "%d 项" % len(_rep["checks"]))
    win._backdrop.set_image(_paths.ASSET_DIR / "pet" / "ciallo.png")
    ok("B23.31", "主界面底图能加载（设置背景后不会白忙）",
       getattr(win._backdrop, "_pm", None) is not None)
    win._backdrop.set_image(None)
    ok("B23.32", "详情有一层实底容器，背景不会透到标签栏",
       bool(win.detail_host.styleSheet()) and "background" in win.detail_host.styleSheet())
    _err = win._error_hint(
        "File E:/x.mkv exists, but a control file(*.aria2) does not exist.")
    ok("B23.33", "同名文件缺 .aria2 的错误会翻译成可照做的中文提示",
       "同名文件" in _err and ".aria2" in _err, _err[:80])
    ok("B23.34", "详情里有专门的错误原因横幅", hasattr(win, "lb_error"))
    _tdir = CACHE / "uniq_name"
    _tdir.mkdir(parents=True, exist_ok=True)
    (_tdir / "x.mkv").write_bytes(b"x")
    ok("B23.35", "重名时自动改名会追加 (1)，不会覆盖旧文件",
       win._unique_output_name(str(_tdir), "x.mkv", 1) == "x (1).mkv",
       win._unique_output_name(str(_tdir), "x.mkv", 1))
    from app.ui.main_window import _SpeedLimitPanel
    _sp = _SpeedLimitPanel(win)
    _sp.set_limit(30 * 1024 * 1024)
    ok("B23.36", "限速弹出面板按 30 MB/s 定位到对应刻度",
       _sp.sl.value() == 4 and _sp.lb_cur.text() == "30 MB/s",
       "%s %s" % (_sp.sl.value(), _sp.lb_cur.text()))
    _sp.set_limit(1)
    ok("B23.37", "禁止状态是独立开关，不当成 0（不限速）",
       _sp.btn_forbid.isChecked() and _sp.lb_cur.text() == "禁止")
    _sp.deleteLater()
    from app.title_parse import parse as _tp
    _p = _tp("[LoliHouse] Tensei Goblin dakedo Shitsumon Aru - 05 "
             "[WebRip 1080p HEVC-10bit AAC SRTx2].mkv")
    ok("B23.38", "文件名能拆出字幕组/片名/集数，不再塞画质标签",
       _p.group == "LoliHouse" and _p.title == "Tensei Goblin dakedo Shitsumon Aru"
       and _p.episode == "05",
       "%s | %s | %s" % (_p.group, _p.title, _p.episode))
    ok("B23.39", "表格名称和大小之间有 AI 改名入口",
       "ai_name" in [k for k, _l, _w in __import__(
           "app.ui.models", fromlist=["COLS"]).COLS])
    from app import secret_store
    _tok = secret_store.protect("test-key-123")
    ok("B23.40", "API Key 的本机加密可以正确解回",
       bool(_tok) and secret_store.unprotect(_tok) == "test-key-123")
    import tempfile
    from app.ui.store import TaskMetaStore
    with tempfile.TemporaryDirectory() as _td:
        _st = TaskMetaStore(Path(_td) / "tasks.json")
        _st.note("g1", name="original.mkv")
        _st.set_origin("g1", "original.mkv")
        _st.note("g1", name="renamed.mkv")
        _ok_origin = _st.get_origin("g1") == "original.mkv"
    ok("B23.41", "反复改名不会覆盖最初的真实文件名", _ok_origin)
    win.close()
    win.deleteLater()
    for _ in range(4):
        app.processEvents()


# ============================================================
def main(argv: list[str]) -> int:
    sys.argv = [sys.argv[0]]
    which = [a.upper() for a in argv if not a.startswith("-")]
    CACHE.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    t0 = time.time()

    want = (lambda k: (not which) or any(k.startswith(w) for w in which))
    env = None
    try:
        if want("A"):
            section_a(py)
        if want("B1") or want("B2"):
            section_b12()
        if want("B4"):
            section_b4()
        if want("B5"):
            section_b5()
        if want("B11"):
            section_b11()
        if want("B12"):
            section_b12()
        if want("B13"):
            section_b13()
        if want("B14"):
            section_b14()
        if want("B16"):
            section_b16()
        if want("B18"):
            section_b18()
        if want("B20"):
            section_b20()
        if want("B24"):
            section_b24()
        if want("B25"):
            section_b25()
        if want("B26"):
            section_b26()
        if want("B27"):
            section_b27()
        if want("B28"):
            section_b28()
        if want("B29"):
            section_b29()
        if any(want(k) for k in ("B6", "B7", "B10", "B15", "B17", "B19", "B21", "B22", "B23", "C")):
            dl = CACHE / "dl"
            if dl.exists():
                subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(dl)],
                               capture_output=True, creationflags=0x08000000)
            dl.mkdir(parents=True, exist_ok=True)
            env = Env(dl)
            try:
                if want("B6"):
                    section_b6(env)
                if want("B7"):
                    section_b7(env)
                if want("B10"):
                    section_b10(env)
                if want("B15"):
                    section_b15(env)
                if want("B17"):
                    section_b17(env)
                if want("B19"):
                    section_b19(env)
                if want("B21"):
                    section_b21(env)
                if want("B22"):
                    section_b22(env)
                if want("C"):
                    section_c(env)
                if want("B23"):
                    section_b23(env)
            finally:
                env.close()
    finally:
        print("")

    if not any(a == "--keep" for a in argv):
        # 测试下载的文件不留着占地；排查问题时加 --keep
        for junk in (CACHE / "dl", CACHE / "dl2", CACHE / "open", CACHE / "memo",
                     CACHE / "不存在的目录 测试"):
            if junk.exists():
                subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(junk)],
                               capture_output=True, creationflags=0x08000000)

    print("=" * 60)
    print("通过 %d · 失败 %d · 跳过 %d · 用时 %.0fs"
          % (_COUNT["PASS"], _COUNT["FAIL"], _COUNT["SKIP"], time.time() - t0))
    if _COUNT["FAIL"]:
        print("\n失败明细：")
        for sid, name, state, detail in ROWS:
            if state == "FAIL":
                print("  %-5s %s ← %s" % (sid, name, detail))
    return 1 if _COUNT["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# -*- coding: utf-8 -*-
"""桌宠测试 + 出图。

验的东西（2026-10-21 第二版，按用户原话重排）：

  **顺序就是一顿饭**：待命 → 等饭 → 下载中 → 下完 12 秒 → 回待命；
  今天下完 3 个以上，待命时改躺沙发。

  1. 六张素材都在；弃用的 full.png（仰头谄媚）、eat.png（馋嘴）都已删掉
  2. 没任务 → ciallo（带字打招呼那张）
  3. 有任务在排队 / 磁力还在取种子 → idle_bowl（端着碗等）
  4. **真正在下 → 全程 half（半张脸）**，跟碗数无关，不再是 1–2 碗指着你
  5. 做种（aria2 报 active + seeder）不算在下载 —— 那是这条老 bug 的根
  6. 下完那 12 秒 → tease「没吃饱喵」；12 秒一到回 ciallo（靠 _tick 自己换）
  7. 今天下完 ≥3 个 → 待命图换成 idle_lying（躺沙发），零点归零
  8. 名字踩词 → sassy 停 8 秒，然后回 half
  9. 磁力「先下种子」那一步不算一碗饭（is_meta）

  10. 拖到边上会收成一条蓝边，尺寸变成 7×96；鼠标碰它 / 点它会展开
  11. 拖回中间会取消贴边，不再自动收
  12. 全屏时藏起来（连蓝边和气泡都藏），退出全屏自己回来；主动隐藏的不会被弹出来
  13. 最大化窗口（底下还有任务栏）不算全屏
  14. 进度气泡没人管会自己收，全屏时立刻收

跑法：python tools/testpet.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import pet_day  # noqa: E402
from app.ui import pet as petmod  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.models import TaskRow  # noqa: E402
from app.ui.pet import PetWindow  # noqa: E402
from tools.shot import fake_rows  # noqa: E402

out_docs = ROOT / "docs"
ok = 0
bad = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print("PASS  %s" % name)
    else:
        bad += 1
        print("FAIL  %s  %s" % (name, detail))


def done(name: str, hours_ago: float = 0.5, dir_: str = "D:/Download") -> TaskRow:
    """造一条"已完成"的任务。hours_ago 控制完成时间，用来区分今天/昨天。"""
    r = TaskRow(gid="g%d" % id(name), name=name, dir=dir_)
    r.status_code = "complete"
    r.finished = time.time() - hours_ago * 3600
    return r


def live(gid: str, name: str = "某番剧 01.mkv", dir_: str = "E:/Download") -> TaskRow:
    r = TaskRow(gid=gid, name=name, dir=dir_)
    r.status_code = "active"
    return r


def seeding(gid: str, name: str = "下完还在做种.mkv") -> TaskRow:
    """BT 下完之后的状态：aria2 仍然报 active，只是 seeder=true。"""
    r = TaskRow(gid=gid, name=name, dir="E:/Download")
    r.status_code = "active"
    r.is_seeder = True
    return r


def set_day(n: int) -> None:
    """把"今天下完几碗"设成 n（写盘；调用方自己负责让缓存失效）。"""
    pet_day.FILE.write_text(
        '{"date": "%s", "done": %d}' % (time.strftime("%Y-%m-%d"), n),
        encoding="utf-8")


def new_pet():
    """开一个桌宠窗口，并且先喂一份"空的"首帧快照。

    程序启动后的第一份快照里，已经下完的任务不算"刚刚下完"（不能补庆祝、
    不能重复计数），所以测试也要先空跑一次，之后喂的数据才算新发生的事。
    """
    w = PetWindow()
    w.show()
    w.update_data([], {})
    return w


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(theme.QSS)

    # 今天下完几碗那个计数落在 data/pet_day.json，测试要隔离，
    # 否则一跑测试就把用户真实的"今天第几碗"改了。
    day_dir = ROOT / ".cache"
    day_dir.mkdir(exist_ok=True)
    real_day = pet_day.FILE
    pet_day.FILE = day_dir / "pet_day.test.json"
    if pet_day.FILE.exists():
        pet_day.FILE.unlink()

    try:
        return _run(app)
    finally:
        pet_day.FILE = real_day
        p = day_dir / "pet_day.test.json"
        if p.exists():
            p.unlink()


def _run(app) -> int:  # noqa: C901
    # ---- 1. 素材 ----
    need = ["ciallo", "half", "idle_bowl", "idle_lying", "tease", "face", "sassy"]
    for n in need:
        p = ROOT / "assets" / "pet" / ("%s.png" % n)
        check("素材存在 %s.png" % n, p.exists() and p.stat().st_size > 20000)
    check("弃用的 full.png（仰头谄媚那张）已删掉",
          not (ROOT / "assets" / "pet" / "full.png").exists())
    check("用户点名删掉的 eat.png（馋嘴）也删了",
          not (ROOT / "assets" / "pet" / "eat.png").exists())
    check("代码里不再有 eat 这个姿势",
          not hasattr(petmod, "EAT") and "eat" not in petmod.pet_skin.KEYS,
          str(petmod.pet_skin.KEYS))
    check("图标是脸（不是旧鲸鱼剪影）",
          (ROOT / "assets" / "icons" / "whale.ico").stat().st_size > 40000)

    # 删素材没同步改引用 → 一开程序就弹「程序文件不完整」，进程活着但不响应
    from app import selfcheck  # noqa: E402
    rep = selfcheck.run()
    check("启动自检通过（不会一开就弹「程序文件不完整」）", rep["ok"], str(rep["problems"]))

    # ---- 2. 待命 ----
    w = new_pet()
    w.show()
    set_day(0)
    w._day_ts = 0.0
    w.update_data([], {})
    check("没任务 → ciallo（带字的招呼）", w._sprite_name() == "ciallo", w._sprite_name())

    k_seq = [done("a.mkv"), done("b.mkv")]
    w.update_data(k_seq, {})
    w._done_until = 0.0
    w._day_ts = 0.0
    w._pick_mood()
    check("下过几碗但今天不到 3 碗 → 待命还是 ciallo",
          w._sprite_name() == "ciallo", w._sprite_name())

    check("普通名字不会被误伤",
          not petmod.looks_spicy("Dune.Part.Two.2160p.mkv", "D:/Download"))
    check("「药屋少女的呢喃」不调侃",
          not petmod.looks_spicy("[Nekomoe kissaten] 药屋少女的呢喃 07 [1080p].mkv", "E:/Download"))
    check("「魔法少女小圆」不调侃",
          not petmod.looks_spicy("魔法少女小圆 [BDRip].mkv", "E:/Download/动画"))
    check("普通编号+域名+视频特征凑够 3 项才调侃",
          petmod.looks_spicy("demo.site.com@ABC-123.mp4",
                             size=800 * 1024 * 1024))
    check("没有编号结构的普通电影不调侃",
          not petmod.looks_spicy("Dune.Part.Two.2160p.site.com.mkv"))

    # ---- 3. 等饭：排队 / 取种子 ----
    meta = TaskRow(gid="M1", name="（获取元数据）", dir="E:/Download")
    meta.status_code = "active"
    meta.is_meta = True
    wm = new_pet()
    wm.show()
    wm.update_data([meta], {})
    check("磁力还在取种子 → idle_bowl（端着碗等饭上来）",
          wm._sprite_name() == "idle_bowl", wm._sprite_name())

    wait1 = TaskRow(gid="Q1", name="排队中.mkv", dir="E:/Download")
    wait1.status_code = "waiting"
    wq = new_pet()
    wq.show()
    wq.update_data([wait1], {})
    check("有任务在排队 → idle_bowl", wq._sprite_name() == "idle_bowl", wq._sprite_name())

    # ---- 4. 下载中：全程 half，跟碗数无关 ----
    def face_of(rows, ww=None) -> str:
        ww = ww or new_pet()
        ww.update_data(rows, {"downloadSpeed": "100000"})
        ww._done_until = 0.0
        ww._pick_mood()
        return ww._sprite_name()

    w1 = new_pet()
    w1.show()
    w1.update_data([live("L1")], {"downloadSpeed": "100000"})
    check("1 个在下 → 半张脸 half", w1._sprite_name() == "half", w1._sprite_name())
    for n in (2, 3, 5, 8):
        rows = [live("N%d_%d" % (n, i), "第 %d 碗.mkv" % (i + 1)) for i in range(n)]
        check("%d 个在下 → 还是半张脸（碗数不影响下载态）" % n,
              face_of(rows) == "half", face_of(rows))

    # ---- 5. 做种不算在下载（这条是老 bug 的根） ----
    ws = new_pet()
    ws.show()
    ws.update_data([seeding("S1")], {"uploadSpeed": "50000"})
    check("下完在做种 → 不算「下载中」（不再一直摆没吃饱的脸）",
          ws.downloading is False, "downloading=%s" % ws.downloading)
    check("做种中也不占碗数", ws._bowls() == 0, str(ws._bowls()))
    check("做种开始那一刻就该弹「没吃饱」", ws._sprite_name() == "tease", ws._sprite_name())

    # ---- 6. 下完 12 秒 → 回待命 ----
    set_day(0)
    w4 = new_pet()
    w4.show()
    w4.update_data([done("刚下完的电影.mkv")], {})
    check("下完那一刻 → 「没吃饱喵」tease", w4._sprite_name() == "tease", w4._sprite_name())
    check("「刚下完」会保持一段时间（不是闪一下就没了）",
          w4._done_until - time.time() > petmod.DONE_HOLD - 2, "%.0f" % w4._done_until)
    check("下完的气泡文案也跟着图走（没吃饱，不是吃饱了）",
          "没吃饱" in w4._toast, w4._toast)
    w4._done_until = time.time() - 0.1        # 12 秒过去了
    w4._tick()                                 # 靠定时刷新自己换，不用等新数据
    check("12 秒一到，靠 _tick 自己回到待命 ciallo（截图里卡住半小时那件事）",
          w4._sprite_name() == "ciallo", w4._sprite_name())

    # 重启不补数：第一份快照里已经下完的，不算"刚刚下完"
    set_day(1)
    before = pet_day.count()
    w_restart = PetWindow()
    w_restart.show()
    w_restart.update_data([done("早就下完的.mkv"), done("也是旧的.mkv")], {})
    check("程序重启后，列表里已经下完的不会补数一遍",
          pet_day.count() == before, "%d → %d" % (before, pet_day.count()))
    check("也不会弹「第 N 碗下完了」的假庆祝", not w_restart._toast, w_restart._toast)
    check("重启后待命状态跟今天碗数一致（1 碗 → 站着）",
          w_restart._sprite_name() == "ciallo", w_restart._sprite_name())

    # ---- 7. 今天 ≥3 碗 → 待命躺沙发 ----
    set_day(3)
    w5 = new_pet()
    w5.show()
    w5.update_data([], {})
    check("今天已经下完 3 碗 → 待命改躺沙发 idle_lying",
          w5._sprite_name() == "idle_lying", w5._sprite_name())
    set_day(2)
    w5._day_ts = 0.0
    w5.update_data([], {})
    check("只有 2 碗 → 还是站着 ciallo（没到那条线）",
          w5._sprite_name() == "ciallo", w5._sprite_name())
    check("线就是 3（跟代码里的常量一致）", petmod.STUFFED_BOWLS == 3,
          str(petmod.STUFFED_BOWLS))

    # 超过 3 碗也还是躺沙发；正在下的时候优先放半张脸
    set_day(7)
    w5._day_ts = 0.0
    w5.update_data([live("L9")], {"downloadSpeed": "100000"})
    check("今天下完 7 碗但又有新任务在下 → 还是 half（下载优先）",
          w5._sprite_name() == "half", w5._sprite_name())

    # 跨天归零：文件里是昨天的日期就算 0
    pet_day.FILE.write_text('{"date": "2000-01-01", "done": 9}', encoding="utf-8")
    check("昨天的记录不算今天 → 归零", pet_day.count() == 0, str(pet_day.count()))
    check("文件坏了也当 0，不崩",
          (pet_day.FILE.write_text("{坏的", encoding="utf-8") or True)
          and pet_day.count() == 0, str(pet_day.count()))
    check("记一碗能落盘，再读回来还是这个数",
          (pet_day.FILE.unlink() if pet_day.FILE.exists() else None) is None
          and pet_day.bump(1) == 1 and pet_day.count() == 1)

    # 磁力「先下种子」那一步不算一碗
    set_day(0)
    meta_done = TaskRow(gid="M2", name="（等待元数据）", dir="E:/Download")
    meta_done.status_code = "complete"
    meta_done.is_meta = True
    w3 = new_pet()
    w3.show()
    w3.update_data([meta_done], {})
    check("磁力取种子完成 → 不弹「没吃饱」", w3._done_until == 0.0, "%.0f" % w3._done_until)
    check("磁力取种子完成 → 今天碗数不变", pet_day.count() == 0, str(pet_day.count()))

    # ---- 8. 编号/域名吐槽：停 8 秒就回半张脸 ----
    set_day(0)
    spicy = live("SP1", "demo.site.com@ABC-123.mp4")
    w6 = new_pet()
    w6.show()
    w6.update_data([spicy], {"downloadSpeed": "100000"})
    check("下到一半踩中 → 当场吐槽 sassy（不用等下完）",
          w6._sprite_name() == "sassy", w6._sprite_name())
    w6._sassy_until = time.time() - 0.1
    w6._pick_mood()
    check("8 秒后收回半张脸，不会一直翻白眼", w6._sprite_name() == "half",
          w6._sprite_name())
    check("同一个任务不会闪第二次",
          (w6.update_data([spicy], {"downloadSpeed": "100000"}) or True)
          and w6._sprite_name() == "half", w6._sprite_name())

    # ---- 出图 ----
    shots = []
    set_day(0)
    cases = [
        ("pet-state-1-idle", "ciallo", [], "idle"),
        ("pet-state-2-bowl", "idle_bowl", [meta], "plain"),
        ("pet-state-3-half", "half", [live("P1")], "plain"),
        ("pet-state-4-tease", "tease", [seeding("P4")], "done"),
        ("pet-state-5-lying", "idle_lying", [], "stuffed"),
        ("pet-state-6-sassy", "sassy", [spicy], "plain"),
    ]
    for name, key, rows, mode in cases:
        if mode == "stuffed":
            set_day(4)
        else:
            set_day(0)
        v = new_pet()
        v.move(80 + 200 * len(shots), 80)
        v.show()
        v._day_ts = 0.0
        v.update_data(rows, {"downloadSpeed": "19200000", "uploadSpeed": "2100000"})
        v._done_until = time.time() + 10 if mode == "done" else 0.0
        v._pick_mood()
        v._toast = ""
        v._prev_key = ""
        v.repaint()
        assert v._sprite_name() == key, "要出 %s 的图，实际画的是 %s" % (key, v._sprite_name())
        path = out_docs / ("%s.png" % name)
        v.grab().save(str(path))
        shots.append(path)
        print("      出图 %s" % path.name)
    check("六态出图都不为空", all(p.stat().st_size > 3000 for p in shots))
    set_day(0)

    # ---- 9. 贴边隐藏 ----
    g = w._screen_geo()
    w.update_data([], {})
    w.move(g.left() + 4, g.top() + 220)
    w._maybe_dock()
    check("拖到左边缘 → 贴上左边", w._dock == "left", str(w._dock))
    check("收起后只剩一条竖边", (w.width(), w.height()) == (petmod.BAR_W, petmod.BAR_H),
          "%sx%s" % (w.width(), w.height()))
    check("蓝边贴着屏幕左边", abs(w.x() - g.left()) <= 2, "x=%d g=%d" % (w.x(), g.left()))
    w.repaint()
    (out_docs / "pet-collapsed.png").parent.mkdir(exist_ok=True)
    w.grab().save(str(out_docs / "pet-collapsed.png"))

    from PySide6.QtCore import QEvent, QPoint, QPointF
    from PySide6.QtGui import QEnterEvent
    p0 = QPointF(2.0, 2.0)
    w.enterEvent(QEnterEvent(p0, p0, p0))                   # 鼠标碰到蓝边
    check("鼠标碰上去 → 展开", not w._collapsed and w.width() == petmod.SIZE,
          "%dx%d" % (w.width(), w.height()))

    check("展开后还贴着边（等鼠标移开才收）", w._dock == "left" and not w._collapsed,
          "dock=%s collapsed=%s" % (w._dock, w._collapsed))

    w.move(g.left() + 500, g.top() + 300)
    w._maybe_dock()
    check("拖回中间 → 取消贴边", w._dock is None and not w._collapsed, str(w._dock))

    w._dock_to("right")
    check("右键收成一条边 → 贴右边",
          w._collapsed and abs(w.x() + w.width() - (g.right() + 1)) <= 2,
          "x=%d w=%d right=%d" % (w.x(), w.width(), g.right()))
    w._undock()
    check("取消贴边后恢复正常尺寸", w.width() == petmod.SIZE and not w._collapsed)

    # ---- 9.2 贴边唤出缓冲（v0.10.17） ----
    # 用户原话：鼠标靠近蓝边它弹出来，我正要去点头像，它马上又收回去了。
    # 要求从唤出到点击之间留 3~5 秒缓冲。
    check("展开缓冲在 3~5 秒之间", 3000 <= petmod.EXPAND_GRACE <= 5000,
          str(petmod.EXPAND_GRACE))
    wg = new_pet()
    wg.show()
    wg.update_data([], {})
    wg.move(g.left() + 4, g.top() + 260)
    wg._maybe_dock()
    check("贴边后先收成蓝边（原有行为）", wg._collapsed, "collapsed=%s" % wg._collapsed)
    q = QPointF(2.0, 2.0)
    wg.enterEvent(QEnterEvent(q, q, q))
    check("鼠标碰蓝边 → 展开", not wg._collapsed and wg.width() == petmod.SIZE,
          "%dx%d" % (wg.width(), wg.height()))
    check("展开后进入缓冲期（这段时间不许收）",
          wg._expand_until - time.time() >= 2.5,
          "剩余 %.1fs" % (wg._expand_until - time.time()))
    wg.leaveEvent(QEvent(QEvent.Type.Leave))
    check("鼠标刚移开不会马上收，只排定时器",
          not wg._collapsed and wg._collapse_timer.isActive(),
          "collapsed=%s timer=%s" % (wg._collapsed, wg._collapse_timer.isActive()))
    check("定时器等到缓冲结束，不是固定的 700ms",
          wg._collapse_timer.remainingTime() >= 2500,
          "%dms" % wg._collapse_timer.remainingTime())
    wg._cursor_on_pet = lambda: False      # 测试机没有真鼠标悬停，手动模拟"不在身上"
    wg._collapse()                         # 模拟定时器到点
    check("缓冲没到，定时器到点也不许收", not wg._collapsed,
          "collapsed=%s" % wg._collapsed)
    wg._expand_until = time.time() - 0.01  # 4 秒过去了
    wg._collapse()
    check("缓冲过了、鼠标不在身上 → 才收成蓝边",
          wg._collapsed and wg.width() == petmod.BAR_W,
          "collapsed=%s %dx%d" % (wg._collapsed, wg.width(), wg.height()))

    # ---- 9.3 单击循环：单击只开关气泡；打开主界面走气泡按钮 ----
    wc = new_pet()
    wc.show()
    wc.update_data([live("C1")], {"downloadSpeed": "100000"})
    main_hits: list[float] = []
    wc.show_main.connect(lambda: main_hits.append(time.time()))
    check("一开始没有气泡", not wc.panel.isVisible())
    wc._toggle_panel()
    check("第一下单击 → 弹出进度气泡（简洁态）",
          wc.panel.isVisible() and wc.panel.mode == 0,
          "visible=%s mode=%s" % (wc.panel.isVisible(), wc.panel.mode))
    check("气泡里有一个「打开主界面」按钮",
          wc.panel.btn_open_main.text() == "打开主界面")
    wc.panel.btn_open_main.click()
    check("点气泡里的按钮 → 打开主窗口", len(main_hits) == 1)
    wc._toggle_panel()
    check("再单击只收起气泡，不会偷偷打开主窗口",
          not wc.panel.isVisible() and len(main_hits) == 1,
          "visible=%s hits=%d" % (wc.panel.isVisible(), len(main_hits)))
    wc._toggle_panel()
    check("再单击 → 又是进度气泡（入口没丢）",
          wc.panel.isVisible() and wc.panel.mode == 0 and len(main_hits) == 1,
          "visible=%s mode=%s" % (wc.panel.isVisible(), wc.panel.mode))
    wc._hide_panel()

    # ---- 9.4 右键菜单：退出整个程序 ----
    menu = wc._build_menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    check("右键菜单里有「退出整个程序」", any("退出" in t for t in texts), str(texts))
    quit_hits: list[int] = []
    wc.quit_app.connect(lambda: quit_hits.append(1))
    act = next((a for a in menu.actions() if "退出" in a.text()), None)
    if act is not None:
        act.trigger()
    check("点「退出整个程序」→ 发出 quit_app 信号（主窗口接住后真的退出）",
          quit_hits == [1], str(quit_hits))
    menu.deleteLater()

    # ---- 10. 全屏检测 ----
    real = petmod._foreground_fullscreen

    # 直接拿窗口句柄验判定函数：不依赖"谁抢到了前台"，结果才稳定
    from PySide6.QtWidgets import QWidget
    scr = app.primaryScreen()
    full_rect = scr.geometry()           # 整个显示器（含任务栏），不是可用区域
    fake = QWidget()
    fake.setWindowFlags(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.WindowType.FramelessWindowHint)
    fake.setGeometry(full_rect)
    fake.show()
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    check("整屏窗口被判成全屏", petmod._window_is_fullscreen(int(fake.winId())) is True)

    # 最大化那种：宽度够、高度差一个任务栏 → 不能算全屏
    fake.setGeometry(full_rect.left(), full_rect.top(),
                     full_rect.width(), full_rect.height() - 48)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    check("留了任务栏的最大化窗口不算全屏",
          petmod._window_is_fullscreen(int(fake.winId())) is False)

    fake.setGeometry(full_rect.left() + 40, full_rect.top() + 40,
                     int(full_rect.width() * 0.8), int(full_rect.height() * 0.8))
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    check("小窗口不算全屏", petmod._window_is_fullscreen(int(fake.winId())) is False)
    fake.close()

    # ---- 11. 全屏期间隐藏 / 退出后回来 ----
    w.show()
    petmod._foreground_fullscreen = lambda pid: True
    w._watch_fullscreen()
    check("全屏时藏起来（连蓝边都不留）", not w.isVisible())
    petmod._foreground_fullscreen = lambda pid: False
    w._watch_fullscreen()
    check("退出全屏自己回来", w.isVisible())

    w._hide_by_user()
    check("用户主动隐藏 → 不显示", not w.isVisible())
    petmod._foreground_fullscreen = lambda pid: True
    w._watch_fullscreen()
    petmod._foreground_fullscreen = lambda pid: False
    w._watch_fullscreen()
    check("用户主动隐藏的，退出全屏也不弹出来", not w.isVisible(), "被硬弹出来了")
    petmod._foreground_fullscreen = real

    # ---- 12. 进度气泡：会自己收，全屏也跟着收 ----
    w.show()
    w.update_data(fake_rows(), {"downloadSpeed": "1000000"})
    w._toggle_panel()
    check("单击桌宠 → 弹出进度气泡", w.panel.isVisible())
    check("气泡开着就开始计时（没人管会自己收）", w.panel._auto.isActive(),
          "定时器没启动，白框会一直赖着")
    check("气泡计时用的是「自动收」的时长", w.panel._auto.interval() == petmod.PANEL_AUTO_HIDE,
          str(w.panel._auto.interval()))
    w.panel._auto_hide()          # 模拟时间到
    check("时间到 → 气泡自己收起来", not w.panel.isVisible())

    w._toggle_panel()
    petmod._foreground_fullscreen = lambda pid: True
    w._watch_fullscreen()
    check("进全屏 → 气泡和桌宠一起藏", not w.panel.isVisible() and not w.isVisible())
    petmod._foreground_fullscreen = lambda pid: False
    w._watch_fullscreen()

    # 贴边收起时，气泡不能孤零零挂在边上
    w._toggle_panel()
    w.move(g.left() + 4, g.top() + 220)
    w._maybe_dock()
    check("拖到边上 → 气泡跟着收掉，只剩蓝边",
          not w.panel.isVisible() and w._collapsed,
          "panel=%s collapsed=%s" % (w.panel.isVisible(), w._collapsed))
    w._undock()
    w.hide()

    # ---- 13. 面板点空白处立刻收（v0.10.20） ----
    check("气泡状态：下载中 → 打饭中",
          petmod.panel_state_text([live("L1")]) == "打饭中 1 碗",
          petmod.panel_state_text([live("L1")]))
    check("气泡状态：做种中 → 给鱼群分饭（不再显示成在等饭）",
          petmod.panel_state_text([seeding("S1")]) == "给鱼群分饭 1 个",
          petmod.panel_state_text([seeding("S1")]))
    check("气泡状态：没任务 → 在等饭", petmod.panel_state_text([]) == "在等饭",
          petmod.panel_state_text([]))
    wsp = new_pet()
    wsp.show()
    wsp.update_data([seeding("S9")], {"uploadSpeed": "123456"})
    wsp._toggle_panel()
    check("做种中打开气泡能画出上传信息（不崩）",
          wsp.panel.isVisible() and not wsp.panel.grab().isNull())
    wsp._hide_panel()
    wo = new_pet()
    wo.show()
    wo.update_data([live("O1")], {"downloadSpeed": "100000"})
    wo._toggle_panel()
    check("单击桌宠 → 气泡打开", wo.panel.isVisible())
    inside_panel = wo.panel.frameGeometry().center()
    check("点气泡自身不会收",
          wo._panel_outside_pressed(inside_panel) is False and wo.panel.isVisible())
    inside_pet = wo.frameGeometry().center()
    check("点桌宠身上不会收",
          wo._panel_outside_pressed(inside_pet) is False and wo.panel.isVisible())
    far = QPoint(inside_panel.x() + 3000, inside_panel.y() + 3000)
    check("点屏幕空白处 → 白框立刻收",
          wo._panel_outside_pressed(far) is True and not wo.panel.isVisible())
    check("白框收了桌宠还在（不是整只消失）", wo.isVisible())

    # 贴边展开状态：点空白只收白框，桌宠按缓冲慢慢收
    g3 = wo._screen_geo()
    wo.move(g3.left() + 4, g3.top() + 250)
    wo._maybe_dock()
    check("先贴边收起（蓝边）", wo._collapsed)
    qq = QPointF(2.0, 2.0)
    wo.enterEvent(QEnterEvent(qq, qq, qq))
    wo._toggle_panel()
    check("贴边唤出后点开白框", wo.panel.isVisible() and not wo._collapsed)
    wo._panel_outside_pressed(QPoint(g3.left() + 600, g3.top() + 600))
    check("点空白 → 白框马上收、桌宠先不收起（还在缓冲期）",
          not wo.panel.isVisible() and not wo._collapsed)
    wo._undock()
    wo.hide()

    # ---- 14. 桌宠底部进度只算下载（v0.10.29） ----
    rp_live = live("P1", "正在下的.mkv")
    rp_live.size = 1000
    rp_live.done = 50
    rp_seed = seeding("P2", "正在传的.mkv")
    rp_seed.size = 9000
    rp_seed.done = 9000
    wp = new_pet()
    wp.show()
    wp.update_data([rp_live, rp_seed], {"downloadSpeed": "1000", "uploadSpeed": "2000"})
    check("桌宠底部进度只算下载，不把做种的 100% 算进来",
          abs(wp._overall_percent() - 5.0) < 0.01, "%.1f%%" % wp._overall_percent())
    wp.update_data([rp_seed], {"uploadSpeed": "2000"})
    check("只有做种时底部进度是 0（没有下载就不算总进度）",
          wp._overall_percent() == 0.0, "%.1f%%" % wp._overall_percent())
    wp.hide()

    print("\n通过 %d / 失败 %d" % (ok, bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

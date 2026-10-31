# -*- coding: utf-8 -*-
"""AI/联网改名的小面板：查询候选，用户点选后只改显示名。"""
from __future__ import annotations

import threading

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from .. import ai_lookup, paths, secret_store, title_lookup, title_parse
from .ai_key_dialog import AIKeyDialog


class TitleLookupDialog(QDialog):
    def __init__(self, parent, parsed: title_parse.ParsedName, raw_name: str = "",
                 allow_disk_rename: bool = False, ihash: str = "",
                 settings: dict | None = None, original_name: str = "",
                 task_key: str = ""):
        super().__init__(parent)
        self.setWindowTitle("AI 智能改名")
        self.resize(640, 620)
        self.parsed = parsed
        self.settings = settings if settings is not None else {}
        self.chosen = ""
        self.restore_original = False
        self.rename_disk = False
        self._allow_disk_rename = allow_disk_rename
        self._ihash = ihash or ""
        self._task_key = task_key or ""
        self._original_name = original_name or raw_name or parsed.raw
        self._stop = None
        self._ai_done = False
        self._ai_candidates: list = []
        self._ai_error = ""
        self._ai_timer: QTimer | None = None
        self._finished = False
        self._timer: QTimer | None = None
        self._q = ""
        self._kind = ""

        self.resize(700, 720)
        _avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(700, _avail.width() - 30),
                    min(720, _avail.height() - 50))

        def card(qss: str):
            f = QFrame()
            f.setObjectName("aiCard")
            f.setStyleSheet("QFrame#aiCard{%s}" % qss)
            return f

        def badge(text: str, color: str):
            b = QLabel(text)
            b.setStyleSheet(
                "QLabel{background:%s;color:#fff;border-radius:9px;"
                "padding:2px 8px;font-size:12px;font-weight:700;}" % color)
            b.setFixedHeight(22)
            return b

        def number(n: str):
            b = QLabel(n)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setFixedSize(30, 30)
            b.setStyleSheet(
                "QLabel{background:#E8F1FF;color:#2563EB;border-radius:15px;"
                "font-size:15px;font-weight:700;}")
            return b

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget()
        lay = QVBoxLayout(self._content)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.setSpacing(6)

        # 顶部标题
        head = card("background:#F4F8FF;border:1px solid #DCE8FF;border-radius:10px;")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 7, 10, 7)
        _ico = paths.ASSET_DIR / "icons" / "ai_whale.png"
        ico = QLabel()
        if _ico.exists():
            ico.setPixmap(QIcon(str(_ico)).pixmap(QSize(42, 42)))
        hl.addWidget(ico)
        box = QVBoxLayout()
        box.setSpacing(1)
        t = QLabel("AI 智能改名")
        t.setStyleSheet("font-size:20px;font-weight:800;color:#16233D;")
        box.addWidget(t)
        sub = QLabel("为下载的文件智能识别并生成更准确、更易识别的显示文件名")
        sub.setStyleSheet("color:#5B6B84;font-size:12px;")
        box.addWidget(sub)
        hl.addLayout(box, 1)
        lay.addWidget(head)

        # 原文件名
        orig = card("background:#F8FAFC;border:1px solid #E4EAF2;border-radius:8px;")
        ol = QHBoxLayout(orig)
        ol.setContentsMargins(10, 5, 10, 5)
        _ol = QLabel("原文件名")
        _ol.setStyleSheet("font-size:12px;color:#64748B;")
        ol.addWidget(_ol)
        self.lb_raw = QLabel(raw_name or parsed.raw)
        self.lb_raw.setStyleSheet("color:#64748B;font-size:12px;")
        self.lb_raw.setWordWrap(True)
        ol.addWidget(self.lb_raw, 1)
        ol.addWidget(badge("视频文件", "#8BA3C7"))
        lay.addWidget(orig)

        info = QLabel("默认只改显示名；要同步本地文件，请勾选下面的“同时改真实文件名”。")
        info.setWordWrap(True)
        info.setStyleSheet("background:#EAF3FF;color:#2B5B9E;border-radius:6px;"
                           "padding:5px 8px;font-size:12px;")
        lay.addWidget(info)

        # 1 常规匹配
        sec1 = card("background:#fff;border:1px solid #E2E8F0;border-radius:10px;")
        s1 = QVBoxLayout(sec1)
        s1.setContentsMargins(10, 7, 10, 7)
        s1.setSpacing(4)
        h1 = QHBoxLayout()
        h1.addWidget(number("1"))
        ct = QLabel("常规匹配搜索")
        ct.setStyleSheet("font-size:17px;font-weight:800;color:#1F2A44;")
        h1.addWidget(ct)
        h1.addWidget(badge("免费", "#3B82F6"))
        h1.addStretch(1)
        b_retry = QPushButton("重新搜索")
        b_retry.setFixedHeight(26)
        b_retry.setToolTip("清掉这一条的旧缓存，重新联网查")
        b_retry.clicked.connect(self._restart_search)
        h1.addWidget(b_retry)
        s1.addLayout(h1)
        d1 = QLabel("从文件名提取关键词，在分享站/资料站匹配；准确率有限。")
        d1.setWordWrap(False)
        d1.setFixedHeight(18)
        d1.setStyleSheet("color:#64748B;font-size:12px;")
        s1.addWidget(d1)
        self.lb_status = QLabel("正在同时查询 爱恋动漫 / Bangumi / Wikipedia …")
        self.lb_status.setWordWrap(False)
        self.lb_status.setFixedHeight(18)
        self.lb_status.setStyleSheet("color:#3A7BD5;font-size:12px;")
        s1.addWidget(self.lb_status)
        self.list = QListWidget()
        self.list.setMinimumHeight(100)
        self.list.setMaximumHeight(170)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setWordWrap(False)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet(
            "QListWidget{background:#FBFDFF;border:1px solid #E3E8F0;border-radius:6px;}"
            "QListWidget::item{padding:4px 6px;border-bottom:1px solid #EEF2F7;}"
            "QListWidget::item:selected{background:#E8F1FF;color:#1D4ED8;}")
        self.list.itemDoubleClicked.connect(lambda _i: self._apply())
        s1.addWidget(self.list)
        lay.addWidget(sec1)

        # 2 AI API 搜索
        sec2 = card("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                    "stop:0 #F2F8FF,stop:1 #FFF9E8);"
                    "border:1px solid #8DB8FF;border-radius:10px;")
        s2 = QVBoxLayout(sec2)
        s2.setContentsMargins(10, 7, 10, 7)
        s2.setSpacing(4)
        h2 = QHBoxLayout()
        h2.addWidget(number("2"))
        at = QLabel("AI API 搜索")
        at.setStyleSheet("font-size:17px;font-weight:800;color:#1F2A44;")
        h2.addWidget(at)
        h2.addWidget(badge("推荐", "#EF4444"))
        h2.addStretch(1)
        b_api = QPushButton("⚙ API 设置")
        b_api.setFixedHeight(26)
        b_api.setToolTip("更换/删除 DeepSeek API Key；Key 失效时也可以在这里重新填")
        b_api.clicked.connect(self._open_api_settings)
        h2.addWidget(b_api)
        s2.addLayout(h2)
        d2 = QLabel("DeepSeek 联网搜索 + AI 提取，准确率最高；需要填写一次 API Key。")
        d2.setWordWrap(True)
        d2.setStyleSheet("color:#64748B;font-size:12px;")
        s2.addWidget(d2)
        mid = QHBoxLayout()
        pic = QLabel()
        if _ico.exists():
            pic.setPixmap(QIcon(str(_ico)).pixmap(QSize(88, 56)))
            pic.setFixedWidth(94)
        mid.addWidget(pic)
        feat = QLabel("更强语义理解 / 更高准确率 /\n自动生成规范易读的名字")
        feat.setStyleSheet("color:#27538F;font-weight:600;font-size:12px;")
        mid.addWidget(feat, 1)
        self.btn_ai = QPushButton("使用 AI 智能搜索")
        self.btn_ai.setMinimumHeight(40)
        self.btn_ai.setMinimumWidth(190)
        self.btn_ai.setMaximumWidth(215)
        self.btn_ai.setStyleSheet(
            "QPushButton{background:#2E6BFF;color:#fff;border:none;border-radius:8px;"
            "font-size:15px;font-weight:800;padding:4px 12px;}"
            "QPushButton:hover{background:#1D5BEA;}"
            "QPushButton:disabled{background:#9DB8E8;}")
        self.btn_ai.clicked.connect(self._ask_ai)
        mid.addWidget(self.btn_ai)
        s2.addLayout(mid)
        secu = QLabel("已使用 Windows DPAPI（AES）纯本地加密保存。准确率极高。"
                      "注意不要把 API 发给其他人，并通过 DeepSeek 官网及时维护。")
        secu.setWordWrap(True)
        secu.setStyleSheet("color:#8A4B00;font-size:11px;")
        s2.addWidget(secu)
        lay.addWidget(sec2)

        # 3 手动填写
        sec3 = card("background:#fff;border:1px solid #F0D6D6;border-radius:10px;")
        s3 = QVBoxLayout(sec3)
        s3.setContentsMargins(10, 7, 10, 7)
        s3.setSpacing(4)
        h3 = QHBoxLayout()
        h3.addWidget(number("3"))
        mt = QLabel("手动填写")
        mt.setStyleSheet("font-size:17px;font-weight:800;color:#1F2A44;")
        h3.addWidget(mt)
        h3.addStretch(1)
        s3.addLayout(h3)
        d3 = QLabel("找不到便手动填，原样作为显示名（可自己带集数/画质）。")
        d3.setStyleSheet("color:#B42318;font-weight:700;font-size:12px;")
        d3.setWordWrap(True)
        s3.addWidget(d3)
        self.ed_manual = QLineEdit()
        self.ed_manual.setPlaceholderText("输入中文名（原样作为显示名，可自己带集数/画质）")
        self.ed_manual.textChanged.connect(
            lambda t: self.b_apply.setEnabled(
                bool(t.strip()) or self.list.count() > 0)
            if hasattr(self, "b_apply") else None)
        s3.addWidget(self.ed_manual)
        self.chk_rename = QCheckBox("同时改真实文件名（仅限已下载完成，会停止做种）")
        self.chk_rename.setEnabled(allow_disk_rename)
        _rename_tip = ("仅能对已下载完成且校验通过的文件生效；下载中的任务不可勾选，"
                       "这是正常限制，不是程序 bug。勾选后会停止做种，之后不能再上传。")
        self.chk_rename.setToolTip(_rename_tip)
        self.chk_rename.setStatusTip(_rename_tip)
        lay.addWidget(sec3)

        # 底部选项/按钮；真实文件名勾选对上面三种方式都生效
        foot = QHBoxLayout()
        foot.addWidget(self.chk_rename)
        foot.addStretch(1)
        b_restore = QPushButton("还原原显示名")
        b_restore.setToolTip("清掉自动/手动标题，恢复显示原始文件名")
        b_restore.clicked.connect(self._restore)
        b_close = QPushButton("取消")
        b_close.clicked.connect(self.reject)
        self.b_apply = QPushButton("使用选中的名字")
        self.b_apply.setProperty("accent", True)
        self.b_apply.setEnabled(False)
        self.b_apply.clicked.connect(self._apply)
        self.ed_manual.returnPressed.connect(self._apply)
        self.ed_manual.textChanged.connect(self._manual_changed)
        foot.addStretch(1)
        foot.addWidget(b_restore)
        foot.addWidget(b_close)
        foot.addWidget(self.b_apply)
        lay.addLayout(foot)
        scroll.setWidget(self._content)
        root.addWidget(scroll)

        q = parsed.title or parsed.raw
        self._q = q
        self._kind = title_lookup.guess_kind(raw_name or parsed.raw, parsed)
        ai_hit = ai_lookup.load_cached(self._ihash, self._original_name,
                                       self._task_key)
        if ai_hit:
            self._add_ai(ai_hit)
            self.lb_status.setText("上次的鲸鱼娘 AI 结果还在，已排在最前。")
            self.lb_status.setStyleSheet("color:#8A4B00;font-weight:600;")
        hit = title_lookup.cached(q, parsed.year, parsed.episode, self._kind,
                                  self._ihash)
        if hit:
            self._on_done(hit)
        else:
            self._start_search()

    def _manual_changed(self, text: str) -> None:
        has = bool((text or "").strip())
        self.b_apply.setEnabled(has or self.list.count() > 0)
        self.b_apply.setText("使用手填的名字" if has else "使用选中的名字")
        if has:
            self.list.clearSelection()
            self.list.setCurrentRow(-1)

    def _open_api_settings(self) -> None:
        dlg = AIKeyDialog(self.settings, self)
        dlg.exec()

    def _ask_ai(self) -> None:
        key = secret_store.get_key(self.settings)
        if not key:
            dlg = AIKeyDialog(self.settings, self)
            dlg.exec()
            key = secret_store.get_key(self.settings)
        if not key:
            return
        self.btn_ai.setEnabled(False)
        self.lb_status.setText("🐟 超智能鲸鱼娘正在联网搜索…")
        self.lb_status.setStyleSheet("color:#B45309;font-weight:600;")
        self._ai_done = False
        self._ai_candidates = []
        self._ai_error = ""

        def work() -> None:
            try:
                self._ai_candidates, self._ai_error = ai_lookup.query_name(
                    self._original_name, self.parsed, key)
                if self._ai_candidates:
                    ai_lookup.save_cached(self._ihash, self._original_name,
                                          self._ai_candidates, self._task_key)
            except Exception as exc:  # noqa: BLE001
                self._ai_error = str(exc)[:120]
            self._ai_done = True

        threading.Thread(target=work, name="whale-ai", daemon=True).start()
        if self._ai_timer is None:
            self._ai_timer = QTimer(self)
            self._ai_timer.setInterval(500)
            self._ai_timer.timeout.connect(self._poll_ai)
        self._ai_timer.start()

    def _poll_ai(self) -> None:
        if not self._ai_done:
            return
        if self._ai_timer is not None:
            self._ai_timer.stop()
        self.btn_ai.setEnabled(True)
        if self._ai_candidates:
            self._add_ai(self._ai_candidates)
            self.lb_status.setText(
                "🐟 找到 %d 个 AI 候选，已排在最前。" % len(self._ai_candidates))
            self.lb_status.setToolTip("超智能鲸鱼娘（DeepSeek 联网）返回的候选已标为金色")
            self.lb_status.setStyleSheet("color:#8A4B00;font-weight:700;")
        else:
            err = self._ai_error or "无结果"
            self.lb_status.setToolTip(err)
            if any(k in err for k in ("401", "403", "Unauthorized", "Forbidden",
                                      "authentication", "无效", "Key")):
                self.lb_status.setText("🐟 API Key 失效，点「⚙ API 设置」更换。")
            else:
                self.lb_status.setText("🐟 AI 没找到，点「⚙ API 设置」检查或手填。")
            self.lb_status.setStyleSheet("color:#C9372C;font-weight:700;")

    def _add_ai(self, candidates: list) -> None:
        existing = {self.list.item(i).text() for i in range(self.list.count())}
        for c in reversed(candidates):
            tag = "AI最佳" if "最佳" in str(getattr(c, "source", "")) else "AI备选"
            text = "🐟 %s · %s" % (tag, c.title)
            if text in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, c)
            item.setToolTip("来源：%s\n%s" % (c.source, c.url or ""))
            item.setBackground(QColor("#FFF4DC"))
            item.setForeground(QColor("#8A4B00"))
            f = item.font()
            f.setBold(True)
            item.setFont(f)
            self.list.insertItem(0, item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.b_apply.setEnabled(True)

    def _clear_regular(self) -> None:
        for i in range(self.list.count() - 1, -1, -1):
            item = self.list.item(i)
            c = item.data(Qt.ItemDataRole.UserRole)
            if str(getattr(c, "source", "")).startswith("超智能"):
                continue
            self.list.takeItem(i)

    def _start_search(self) -> None:
        if self._stop is not None:
            self._stop.set()
        self._clear_regular()
        self._finished = False
        self.b_apply.setEnabled(bool(self.ed_manual.text().strip())
                                or self.list.count() > 0)
        self.lb_status.setText("正在搜索，回来一个先显示一个…")
        self.lb_status.setStyleSheet("color:#3A7BD5;")
        self._stop = threading.Event()
        threading.Thread(target=self._search_bg, name="title-dialog",
                         daemon=True).start()
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _restart_search(self) -> None:
        title_lookup.clear(self._q, self.parsed.year, self.parsed.episode,
                           self._kind, self._ihash)
        self._start_search()

    def _search_bg(self) -> None:
        try:
            title_lookup.lookup(self._q, self.parsed.year, self.parsed.episode,
                                kind=self._kind, ihash=self._ihash,
                                stop_event=self._stop)
        except Exception:  # noqa: BLE001
            pass
        self._finished = True

    def _poll(self) -> None:
        hit = title_lookup.cached(self._q, self.parsed.year,
                                  self.parsed.episode, self._kind, self._ihash)
        if hit:
            self._on_done(hit)
            if self._timer is not None:
                self._timer.stop()
            return
        if self._finished:
            if self._timer is not None:
                self._timer.stop()
            self._on_done([])

    def _on_done(self, candidates: list) -> None:
        if not candidates:
            if self.list.count() == 0:
                hint = title_lookup.LAST_ERROR or "没有查到可靠结果"
                self.lb_status.setText("没找到，请手动填写，或点“还原原显示名”。")
                self.lb_status.setToolTip(hint[:300])
                self.lb_status.setStyleSheet("color:#C9372C;font-weight:700;")
                self.b_apply.setEnabled(bool(self.ed_manual.text().strip()))
                self.ed_manual.setFocus()
            return
        existing = {self.list.item(i).text() for i in range(self.list.count())}
        for c in candidates:
            text = c.title
            if text in existing:
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, c)
            item.setToolTip("来源：%s\n原名：%s\n%s"
                            % (c.source, c.original or "", c.url or ""))
            self.list.addItem(item)
            existing.add(text)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self.lb_status.setText("常规匹配：已收到 %d 个候选（原名看提示）。"
                               % self.list.count())
        self.lb_status.setStyleSheet("color:#3E8E5A;")
        self.b_apply.setEnabled(True)

    def _apply(self) -> None:
        manual = self.ed_manual.text().strip()
        zh = manual
        used_ai = False
        if not zh:
            item = self.list.currentItem()
            if item is not None:
                cand = item.data(Qt.ItemDataRole.UserRole)
                zh = str(getattr(cand, "title", "") or "")
                used_ai = str(getattr(cand, "source", "")).startswith("超智能")
        if not zh:
            return
        if self._stop is not None:
            self._stop.set()          # 用户选定了，剩下的源不用再等
        if self._timer is not None:
            self._timer.stop()
        # 手填/常规结果覆盖掉旧 AI 结果时，把那条过期 AI 缓存清掉，免得下次又跳出来
        if not used_ai:
            ai_lookup.clear_cached(self._ihash, self._original_name,
                                   self._task_key)
        # 手填就是用户最终决定，原样使用；候选名才自动补字幕组/集数/画质
        self.chosen = zh if manual else title_parse.compose(self.parsed, zh)
        self.rename_disk = bool(self.chk_rename.isChecked()) and self._allow_disk_rename
        self.accept()

    def _restore(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._timer is not None:
            self._timer.stop()
        self.restore_original = True
        self.accept()

    def reject(self) -> None:  # noqa: N802
        if self._stop is not None:
            self._stop.set()
        if self._timer is not None:
            self._timer.stop()
        super().reject()

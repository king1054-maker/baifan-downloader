# -*- coding: utf-8 -*-
"""超智能鲸鱼娘的 DeepSeek API Key 设置窗。"""
from __future__ import annotations

import urllib.request

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .. import ai_lookup, config, paths, secret_store

KEY_URL = "https://platform.deepseek.com/api_keys"
DOC_URL = "https://api-docs.deepseek.com/zh-cn/guides/search/"


class AIKeyDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.removed = False
        self.setWindowTitle("超智能鲸鱼娘 · 连接 DeepSeek")
        self.resize(620, 470)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        ico = QLabel()
        p = paths.ASSET_DIR / "icons" / "ai_whale.png"
        if p.exists():
            ico.setPixmap(QIcon(str(p)).pixmap(QSize(64, 64)))
        head.addWidget(ico)
        title = QLabel("超智能鲸鱼娘")
        title.setStyleSheet("font-size:20px;font-weight:700;color:#1F2328;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        tip = QLabel(
            "让鲸鱼娘直接调用 <b>DeepSeek 官方联网搜索</b>，搜到网页后再用 AI 提取准确中文名。"
            "不经过本机模型，也不会上传视频文件。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#4B5563;")
        lay.addWidget(tip)

        steps = QLabel(
            "第一次使用，按下面三步：<br>"
            "1. 打开 <a href='%s'>%s</a> ，登录 DeepSeek 开放平台；<br>"
            "2. 点「创建 API Key」，复制 <code>sk-...</code>；<br>"
            "3. 粘贴到下面，点「测试并保存」。<br><br>"
            "<span style='color:#C9372C;'>Key 只保存在本机，用 Windows DPAPI 加密，"
            "换用户/换电脑都解不开；不要把它发给别人。"
            "搜索按成功请求约 5 元/1000 次计费，AI 对话 token 另计。</span><br><br>"
            "DeepSeek 联网搜索官方说明文档："
            "<a href='%s'>%s</a>（本功能依赖该 Search API 提供网页搜索支持）。"
            % (KEY_URL, KEY_URL, DOC_URL, DOC_URL))
        steps.setOpenExternalLinks(True)
        steps.setWordWrap(True)
        steps.setStyleSheet("color:#374151;background:#F6F8FC;border-radius:8px;padding:10px;")
        lay.addWidget(steps)

        self.lb_saved = QLabel("")
        self.lb_saved.setStyleSheet("color:#3E8E5A;")
        if secret_store.get_key(settings):
            self.lb_saved.setText("本机已保存一个 API Key（出于安全不显示）。")
        lay.addWidget(self.lb_saved)

        self.ed = QLineEdit()
        self.ed.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed.setPlaceholderText("粘贴 DeepSeek API Key（sk-...）")
        lay.addWidget(self.ed)
        self.chk_show = QCheckBox("显示 Key")
        self.chk_show.toggled.connect(
            lambda on: self.ed.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        lay.addWidget(self.chk_show)

        foot = QHBoxLayout()
        b_link = QPushButton("打开 DeepSeek 创建 Key")
        b_link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(KEY_URL)))
        b_remove = QPushButton("解除 API")
        b_remove.setEnabled(bool(secret_store.get_key(settings)))
        b_remove.clicked.connect(self._remove)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        b_save = QPushButton("测试并保存")
        b_save.setProperty("accent", True)
        b_save.clicked.connect(self._save)
        foot.addWidget(b_link)
        foot.addWidget(b_remove)
        foot.addStretch(1)
        foot.addWidget(b_cancel)
        foot.addWidget(b_save)
        lay.addLayout(foot)

    def _save(self) -> None:
        key = self.ed.text().strip()
        if not key:
            QMessageBox.information(self, "还没填 Key", "请先粘贴 DeepSeek API Key。")
            return
        if not key.startswith("sk-") or len(key) < 20 or " " in key:
            QMessageBox.warning(self, "Key 格式不对",
                                "DeepSeek API Key 通常以 sk- 开头，请检查是否复制完整。")
            return
        self.lb_saved.setText("正在测试 Key…")
        self.lb_saved.repaint()
        try:
            req = urllib.request.Request(
                ai_lookup.CHAT_URL,
                data=('{"model":"deepseek-flash","messages":[{"role":"user",'
                      '"content":"只回复 OK"}],"max_tokens":5}').encode("utf-8"),
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=25) as r:
                r.read(1000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "连接失败",
                                "这个 Key 没通过测试：\n%s" % str(exc)[:160])
            self.lb_saved.setText("")
            return
        if not secret_store.set_key(self.settings, key):
            QMessageBox.warning(self, "保存失败", "Windows 加密 API 不可用，Key 没有保存。")
            return
        self.settings["ai_enabled"] = True
        config.save(self.settings)
        self.lb_saved.setText("已保存，Key 已用本机 DPAPI 加密。")
        self.accept()

    def _remove(self) -> None:
        secret_store.clear_key(self.settings)
        self.settings["ai_enabled"] = False
        config.save(self.settings)
        self.removed = True
        QMessageBox.information(
            self, "已解除",
            "本机保存的 Key 已删除。\n如果这个 Key 曾经暴露，请到 DeepSeek 开放平台作废它。")
        self.accept()

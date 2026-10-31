# -*- coding: utf-8 -*-
"""品牌与文案集中处。

整个程序把「下载一个任务」叫做「给大肥鱼喂一碗饭」：
任务在跑 = 打饭中，下载完 = 开饭了，校验失败 = 没吃干净。
所有对外露出的名字都从这里取，改名只改这一处。
"""
from __future__ import annotations

APP_NAME = "白饭下载器"
APP_NAME_EN = "BaiFan Downloader"
APP_ID = "BaiFanDownloader"
APP_VERSION = "0.10.103"
TAGLINE = "一碗一碗，喂饱大肥鱼"

# 状态词：键与 models.TaskRow.state 一致
WORD_ERROR = "噎住了"
WORD_SEED = "开饭了 · 上传中"
WORD_ACTIVE = "打饭中"
WORD_WAITING = "排队等饭"
WORD_PAUSED = "放下筷子"
WORD_DONE = "开饭了"
WORD_REMOVED = "已退席"


def status_text(state: str, verify_ok: bool | None = None) -> str:
    """把状态翻成饭桌用语；校验结论优先。"""
    if state == "错误":
        return WORD_ERROR
    if state == "做种中":
        return WORD_SEED
    if state == "下载中":
        return WORD_ACTIVE
    if state == "等待中":
        return WORD_WAITING
    if state == "已暂停":
        return WORD_PAUSED
    if state == "已完成":
        if verify_ok is True:
            return WORD_DONE + " ✓"
        if verify_ok is False:
            return "没吃干净 ✗"
        return WORD_DONE
    if state == "已移除":
        return WORD_REMOVED
    return state or "—"

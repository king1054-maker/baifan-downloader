# -*- coding: utf-8 -*-
"""自动给种子任务起一个"核心内容"显示名。

用户的需求（2026-10-21）：
  磁力/种子下回来的东西，文件名经常是 `[260828][group]Some.Show.Name.1080p.mkv`
  这种，列表里一眼扫不出是什么。希望自动从文件里取约 10 个关键词，
  再补一个简单时间标志（哪年哪月），变成「2026年08月 Some Show Name」，
  扫列表就知道是什么。带编号的发布文件也同理：保留编号和内容词 + 时间。

**只生成"显示名"**，不碰磁盘上的文件和文件夹：
  * 改磁盘名会破坏 aria2 的做种路径（种子按原路径校验），文件会变成"找不到"；
  * 显示名存在 data/tasks.json 的 title 字段，随时能改回 / 关掉。

算法（纯本地、无网络、可解释）：
  1. 把种子名 + 每个文件的 basename 拆成 token：
     - 去掉扩展名、方括号/圆括号；
     - 保留英文/数字串和连续中文/日文块（`[\u3040-\u30ff\u4e00-\u9fff]`）；
     - 丢掉分辨率、编码、字幕、合集这类噪声词。
  2. 统计词频：种子名里的词权重更高；跨多个文件重复出现的词优先，
     这样「系列名/主标题」会排在「某一集独有的标题」前面。
  3. 找时间标志：`2026年08月`、`202608`、`260828` 都能认，取出现最多的那个；
     找不到就用任务添加时间所在的年月。
  4. 时间 + 最多 10 个关键词，去重、压到 90 字以内。
"""
from __future__ import annotations

import os
import re
import time

# 分辨率 / 编码 / 字幕 / 打包格式 —— 这些词对"是什么内容"没有帮助
STOP_WORDS = {
    "1080p", "720p", "2160p", "480p", "4k", "8k", "uhd", "hdr", "hdr10",
    "x264", "x265", "h264", "h265", "hevc", "avc", "aac", "ac3", "dts", "flac",
    "10bit", "8bit", "ass", "assx", "assx2",
    "web", "webdl", "webrip", "bluray", "bdrip", "dvdrip", "hdtv", "remux",
    "chs", "cht", "chinese", "japanese", "eng", "raw", "sub", "subs", "subtitle",
    "mp4", "mkv", "avi", "wmv", "mov", "flv", "ts", "m2ts", "zip", "rar", "7z",
    "合集", "全集", "完结合集", "中字", "简繁", "简中", "繁中", "字幕", "内嵌",
    "外挂", "修复", "补档", "更新", "整理", "打包", "高清", "无修",
}

BIG_BRACKET_RE = re.compile(r"[\[\(【（<]([^\]\)】）>]{0,30})[\]\)】）>]")
EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
DATE_CN_RE = re.compile(r"(20\d{2})\s*[年.\-/]\s*(0?[1-9]|1[0-2])\s*月?")
DATE_YMD_RE = re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
DATE_YYM_RE = re.compile(r"(?<!\d)(2[0-9])(0[1-9]|1[0-2])([0-2][0-9]|3[01])(?!\d)")
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.\-]{1,24}|[\u3040-\u30ff\u4e00-\u9fff]{2,40}")

# 只要词里带这些片段，就不是"内容词"
STOP_PARTS = ("合集", "全集", "中字", "简繁", "字幕", "高清", "无修", "修复",
              "补档", "整理", "打包", "内嵌", "外挂", "生肉", "熟肉")

MAX_KEYWORDS = 10
MAX_TITLE = 90


def _clean_text(s: str) -> str:
    s = BIG_BRACKET_RE.sub(lambda m: " %s " % m.group(1), s or "")
    for ch in "[]()【】（）<>《》「」『』~～_":
        s = s.replace(ch, " ")
    s = EXT_RE.sub(" ", s)
    return s


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in TOKEN_RE.finditer(_clean_text(text)):
        tok = m.group(0).strip(".-_")
        if len(tok) < 2 or tok.isdigit():
            continue
        low = tok.lower()
        if low in STOP_WORDS:
            continue
        parts = [p for p in re.split(r"[-+_.]", low) if p]
        if any(p in STOP_WORDS for p in parts):
            continue
        if any(part in tok for part in STOP_PARTS):
            continue
        if re.fullmatch(r"[0-9a-f]{8,}", low):     # 纯 hash / 长 hex
            continue
        if re.fullmatch(r"(19|20)\d{2}", tok):     # 单独年份不算核心词
            continue
        out.append(tok)
    return out


def _pick_date(texts: list[str]) -> str:
    found: list[str] = []
    for t in texts:
        for m in DATE_CN_RE.finditer(t or ""):
            y, mo = m.group(1), int(m.group(2))
            if 1 <= mo <= 12:
                found.append("%s年%02d月" % (y, mo))
        for m in DATE_YMD_RE.finditer(t or ""):
            y, mo = m.group(1), int(m.group(2))
            if 1 <= mo <= 12:
                found.append("%s年%02d月" % (y, mo))
        for m in DATE_YYM_RE.finditer(t or ""):
            y, mo = "20" + m.group(1), int(m.group(2))
            if 1 <= mo <= 12:
                found.append("%s年%02d月" % (y, mo))
    if not found:
        return ""
    # 取出现最多的；一样多取最后出现的（文件名里的时间通常更具体）
    best = max(set(found), key=lambda x: (found.count(x), found[::-1].index(x)))
    return best


def make_title(name: str, file_paths: list[str] | None = None, ts: float = 0.0) -> str:
    """从种子名 + 文件名生成显示名；生成不了返回空串。"""
    # 先按发布名结构解析：字幕组 / 片名 / 集数 / 画质 / 编码 / 语言。
    # 不再把年月塞进标题，也不再用词频把 LoliHouse、SRTx2 当核心词。
    from .title_parse import parse, compose
    parsed = parse(name, file_paths)
    if parsed.title and parsed.confidence >= 40:
        out = compose(parsed, parsed.title)
        if out:
            return out[:MAX_TITLE]

    files = [os.path.basename(str(p).replace("\\", "/")) for p in (file_paths or [])]
    texts = [name or ""] + files[:60]
    date = _pick_date(texts)
    # 时间已经单独拎出来了，正文里别再让 "月合集/260828" 混进关键词
    clean_texts = [DATE_CN_RE.sub(" ", t) for t in texts]
    clean_texts = [DATE_YMD_RE.sub(" ", t) for t in clean_texts]
    clean_texts = [DATE_YYM_RE.sub(" ", t) for t in clean_texts]

    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for idx, text in enumerate(clean_texts):
        weight = 2 if idx == 0 else 1        # 种子名里的词更重要
        for tok in _tokens(text):
            counts[tok] = counts.get(tok, 0) + weight
            order.setdefault(tok, len(order))

    if not counts:
        return ""

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    picked: list[str] = []
    for tok, _cnt in ranked:
        if tok in picked or tok in (date or ""):
            continue
        # 已经有一个更长的词包含它，就别再重复（"彼女催眠" 之后的 "催眠"）
        if any(tok in p for p in picked):
            continue
        picked.append(tok)
        if len(picked) >= MAX_KEYWORDS:
            break

    if not date:
        when = ts or time.time()
        date = time.strftime("%Y年%m月", time.localtime(when))

    core = " ".join(picked)
    title = re.sub(r"\s+", " ", ("%s %s" % (date, core)).strip())
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE - 1].rstrip() + "…"
    # 只剩一个年月/一两个词的，不如不改
    if len(picked) == 0:
        return ""
    return title

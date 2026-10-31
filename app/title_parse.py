# -*- coding: utf-8 -*-
"""从下载文件名里拆出「字幕组 / 片名 / 集数 / 年份」。

只给显示名用，不碰磁盘文件。解析失败返回尽量多的字段，调用方可以回退旧逻辑。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
GROUP_RE = re.compile(r"^\s*[\[\(【（<]([^\]]{1,24})[\]\)】）>]")
END_GROUP_RE = re.compile(r"[-_ ]([A-Za-z][A-Za-z0-9&.+ ]{1,24})\s*$")
EP_RE = re.compile(
    r"(?:^|[\s._\-\[\(])(?:EP?|EPISODE)?\s*0?(\d{1,4})(?:v\d)?(?:[\s._\-\]\).]|$)",
    re.I,
)
SEASON_EP_RE = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.I)
SEASON_RE = re.compile(r"\bS(\d{1,2})\b", re.I)
RANGE_RE = re.compile(r"\b(\d{1,2})\s*[-~]\s*(\d{1,2})\b")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
BAD_SEG = re.compile(
    r"(web-?dl|web-?rip|blu-?ray|bd-?rip|dvd-?rip|hdtv|remux|1080p|720p|2160p|"
    r"4k|8k|hevc|avc|h\.?264|h\.?265|x264|x265|10bit|8bit|aac|ac3|flac|"
    r"assx?2?|srtx?2?|chs|cht|简繁|中字|字幕|内嵌|外挂|合集|全集|"
    r"eng|english|jpn|japanese|chs|cht|简中|繁体)",
    re.I,
)


@dataclass
class ParsedName:
    raw: str = ""
    group: str = ""
    title: str = ""
    episode: str = ""
    season: str = ""
    year: str = ""
    quality: str = ""
    resolution: str = ""
    codec: str = ""
    languages: str = ""
    confidence: int = 0
    notes: list[str] = field(default_factory=list)

    def tags(self) -> str:
        bits = [x for x in (self.resolution, self.codec, self.languages) if x]
        return "[%s]" % " ".join(bits) if bits else ""


def _clean(s: str) -> str:
    s = EXT_RE.sub("", s or "")
    s = re.sub(r"[\[\]【】（）()<>《》「」『』]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _fmt_ep(v: str) -> str:
    try:
        n = int(str(v).lstrip("0") or "0")
        return "%02d" % n if 0 < n < 100 else str(n)
    except (TypeError, ValueError):
        return str(v)


def parse(name: str, file_paths: list[str] | None = None) -> ParsedName:
    files = [os.path.basename(str(p).replace("\\", "/")) for p in (file_paths or [])]
    raw = name or (files[0] if files else "")
    p = ParsedName(raw=raw)
    work = EXT_RE.sub("", raw or "")

    m = GROUP_RE.search(work)
    if m:
        p.group = m.group(1).strip()
        work = work[m.end():].strip()

    # 后续方括号通常是在画质/字幕/集数标签，先拆出来，别让它们污染片名
    chunks: list[str] = []

    def _take(mt):
        chunks.append(mt.group(1).strip())
        return " "

    work = re.sub(r"[\[\(【（<]([^\]]{1,40})[\]\)】）>]", _take, work)
    for c in chunks:
        if re.fullmatch(r"0?\d{1,4}", c):
            p.episode = _fmt_ep(c)
        elif BAD_SEG.search(c):
            p.quality = (p.quality + " " + c).strip()
    work = re.sub(r"[\[\]【】（）()<>《》「」『』]", " ", work)
    work = work.replace(".", " ").replace("_", " ")
    work = re.sub(r"\s+", " ", work).strip()

    ym = YEAR_RE.search(work)
    if ym:
        p.year = ym.group(1)
        work = (work[:ym.start()] + " " + work[ym.end():]).strip()

    sm = SEASON_EP_RE.search(work)
    if sm:
        p.season = sm.group(1)
        p.episode = _fmt_ep(sm.group(2))
        work = (work[:sm.start()] + " " + work[sm.end():]).strip()
    elif not p.season:
        ssm = SEASON_RE.search(work)
        if ssm:
            p.season = ssm.group(1)
            work = (work[:ssm.start()] + " " + work[ssm.end():]).strip()

    # 去掉明显的质量/字幕标签，留片名
    kept: list[str] = []
    for word in work.split():
        if BAD_SEG.search(word):
            p.quality = (p.quality + " " + word).strip()
        else:
            kept.append(word)
    work = " ".join(kept).strip()

    if not p.episode:
        # 优先 ` - 05` 这种集数，避免把 1080 之类的数字当集数
        em = re.search(r"(?:^|[\s._\-])0?(\d{1,4})(?:v\d)?\s*$", work)
        if em:
            p.episode = _fmt_ep(em.group(1))
            work = work[:em.start()].strip()
        else:
            rm = RANGE_RE.search(work)
            if rm:
                p.episode = "%s-%s" % (_fmt_ep(rm.group(1)), _fmt_ep(rm.group(2)))
                work = (work[:rm.start()] + " " + work[rm.end():]).strip()

    work = re.sub(r"^[\s\-_.]+|[\s\-_.]+$", "", work)
    work = re.sub(r"\s+", " ", work).strip()
    if work and not work.lower().startswith(("unknown", "untitled")):
        p.title = work

    # 画质 / 编码 / 语言：这些是选文件时真正有用的标签，显示名里保留
    low = (raw or "").lower()
    if re.search(r"(2160p|4k|3840x2160)", low):
        p.resolution = "4K"
    elif re.search(r"(1080p|1920x1080)", low):
        p.resolution = "1080p"
    elif re.search(r"(720p|1280x720)", low):
        p.resolution = "720p"
    if re.search(r"(hevc|x265|h\.?265)", low):
        p.codec = "H.265"
    elif re.search(r"(avc|x264|h\.?264)", low):
        p.codec = "H.264"
    elif "av1" in low:
        p.codec = "AV1"
    langs: list[str] = []
    if re.search(r"(chs|sc|gb|简中|简体|简日|简繁)", low):
        langs.append("CHS")
    if re.search(r"(cht|tc|big5|繁中|繁體|繁日|简繁)", low):
        langs.append("CHT")
    if re.search(r"(eng|english|\ben\b|\b英\b)", low):
        langs.append("ENG")
    if re.search(r"(jpn|japanese|\bjp\b|\b日\b|日语)", low):
        langs.append("JPN")
    p.languages = "&".join(dict.fromkeys(langs))

    if p.title:
        p.confidence += 40
    if p.episode:
        p.confidence += 25
    if p.group:
        p.confidence += 20
    if p.year:
        p.confidence += 10
    return p


def compose(parsed: ParsedName, zh_title: str = "", include_tags: bool = True) -> str:
    """把查询到的中文名和本地结构拼成显示名（保留画质/编码/语言）。"""
    name = (zh_title or parsed.title or "").strip()
    if not name:
        return ""
    prefix = "[%s] " % parsed.group if parsed.group else ""
    ep = (" - %s" % parsed.episode) if parsed.episode else ""
    if ep and ep.strip("- ") in name:
        ep = ""
    year = ""
    if not parsed.episode and parsed.year and parsed.year not in name:
        year = " (%s)" % parsed.year
    tags = (" " + parsed.tags()) if include_tags and parsed.tags() else ""
    return (prefix + name + year + ep + tags).strip()

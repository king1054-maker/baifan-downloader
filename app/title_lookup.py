# -*- coding: utf-8 -*-
"""联网查片名：只用免费、无需 API Key 的公开库。

动画优先 Bangumi；电影/剧集用 Wikipedia。任何一路失败都静默跳过，
调用方拿不到结果就保留英文/原名。只发送用户点名的片名，不发文件内容。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import partial
from html import unescape

from . import paths

UA = "BaiFanDownloader/0.10.75 (title lookup; contact: local user)"
CACHE_FILE = paths.DATA_DIR / "title_lookup_cache.json"
CACHE_TTL = 7 * 24 * 3600
CACHE_VERSION = 3       # 换版本就忽略旧缓存（坏结果不能再用）
LAST_ERROR = ""


@dataclass
class Candidate:
    title: str
    original: str = ""
    source: str = ""
    year: str = ""
    episode: str = ""
    score: int = 0
    url: str = ""

    def label(self) -> str:
        bits = [self.title]
        if self.original and self.original != self.title:
            bits.append("原名：%s" % self.original)
        meta = " / ".join(x for x in (self.source, self.year, self.episode) if x)
        if meta:
            bits.append(meta)
        return " · ".join(bits)


def _load_cache() -> dict:
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(d: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except OSError:
        pass


def _get_json(url: str, data: bytes | None = None, timeout: float = 8.0) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get_text(url: str, timeout: float = 7.0) -> str:
    text, _final = _get_text_final(url, timeout)
    return text


def _get_text_final(url: str, timeout: float = 7.0) -> tuple[str, str]:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.kisssub.org/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(2_000_000)
        return raw.decode("utf-8", "replace"), str(r.geturl())


def _relevant(query: str, title: str) -> bool:
    qt = [t.lower() for t in re.findall(r"[A-Za-z0-9]{4,}", query or "")]
    if not qt:
        return True
    low = (title or "").lower()
    return any(t in low for t in qt)


def _clean_html_title(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def _bangumi(query: str) -> list[Candidate]:
    body = json.dumps({"keyword": query, "filter": {"type": [2]}}).encode("utf-8")
    d = _get_json("https://api.bgm.tv/v0/search/subjects", data=body)
    out: list[Candidate] = []
    for x in (d.get("data") or [])[:6]:
        cn = (x.get("name_cn") or "").strip()
        orig = (x.get("name") or "").strip()
        title = cn or orig
        if not title:
            continue
        if not (_relevant(query, orig) or _relevant(query, title)):
            continue
        out.append(Candidate(title=title, original=orig, source="Bangumi",
                             year=str(x.get("date") or "")[:4],
                             episode=str(x.get("eps") or "")))
    return out


def _wikipedia(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    url = ("https://zh.wikipedia.org/w/api.php?action=query&list=search&"
           "srsearch=%s&format=json&utf8=1&srlimit=6" % q)
    d = _get_json(url)
    out: list[Candidate] = []
    for x in ((d.get("query") or {}).get("search") or [])[:6]:
        title = re.sub(r"\s*\([^)]*\)\s*$", "", str(x.get("title") or "")).strip()
        if title and _relevant(query, title):
            out.append(Candidate(title=title, source="Wikipedia"))
    return out


_RSS_CACHE: dict = {"ts": 0.0, "items": []}


def _kisssub(query: str) -> list[Candidate]:
    """爱恋动漫（kisssub）RSS：国内通常可访问，标题里常带中文名。"""
    now = time.time()
    if now - float(_RSS_CACHE.get("ts") or 0) > 600:
        req = urllib.request.Request("https://www.kisssub.org/rss.xml",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            root = ET.fromstring(r.read())
        items = []
        for it in root.findall(".//item"):
            t = (it.findtext("title") or "").strip()
            desc = (it.findtext("description") or "").strip()
            if t:
                items.append((t, desc))
        _RSS_CACHE.update({"ts": now, "items": items})
    items = _RSS_CACHE.get("items") or []
    qt = {t.lower() for t in re.findall(r"[A-Za-z0-9]{3,}", query or "")}
    if not qt:
        return []
    out: list[Candidate] = []
    for title, desc in items:
        blob = (title + " " + desc).lower()
        if sum(1 for t in qt if t in blob) < max(1, min(2, len(qt))):
            continue
        raw = re.sub(r"^\s*[\[\(【（<][^\]]{1,30}[\]\)】）>]\s*", "", title)
        raw = re.split(r"\s*/\s*", raw)[0]
        raw = re.sub(r"[\[\(【（<][^\]]*[\]\)】）>]", " ", raw)
        raw = re.sub(r"\s*[-–]\s*0?\d{1,4}(?:v\d)?\s*$", "", raw).strip()
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw or not re.search(r"[\u4e00-\u9fff]", raw):
            continue
        out.append(Candidate(title=raw, original=title, source="爱恋动漫"))
    return out


def _kisssub_hash(ihash: str) -> list[Candidate]:
    """爱恋的 show 页面就是 show-<infohash>.html，有 infohash 可直达。"""
    ihash = (ihash or "").lower().strip()
    if len(ihash) != 40 or not re.fullmatch(r"[0-9a-f]{40}", ihash):
        return []
    text, final = _get_text_final("https://www.kisssub.org/show-%s.html" % ihash)
    if "/public/html/start/" in final or ("show-%s" % ihash) not in final:
        return []
    raw = ""
    for pat in (r"<title[^>]*>(.*?)</title>",
                r"<h1[^>]*>(.*?)</h1>",
                r"<h3[^>]*>(.*?)</h3>"):
        m = re.search(pat, text, re.S | re.I)
        if m:
            raw = _clean_html_title(m.group(1))
            if raw:
                break
    raw = re.sub(r"\s*[-_|]\s*(爱恋动漫|kisssub).*$", "", raw, flags=re.I)
    if raw and re.search(r"[\u4e00-\u9fff]", raw):
        return [Candidate(title=raw, original=raw, source="爱恋动漫")]
    return []


def _mikan(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    req = urllib.request.Request(
        "https://mikanani.me/RSS/Search?searchstr=%s" % q,
        headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=7) as r:
        root = ET.fromstring(r.read(2_000_000))
    out: list[Candidate] = []
    for it in root.findall(".//item")[:12]:
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        raw = re.sub(r"^\s*[\[\(【（<][^\]]{1,30}[\]\)】）>]\s*", "", title)
        raw = re.split(r"\s*/\s*", raw)[0]
        raw = re.sub(r"[\[\(【（<][^\]]*[\]\)】）>]", " ", raw)
        raw = re.sub(r"\s*[-–]\s*0?\d{1,4}(?:v\d)?\s*$", "", raw).strip()
        if raw and re.search(r"[\u4e00-\u9fff]", raw) and _relevant(query, title):
            out.append(Candidate(title=raw, original=title, source="Mikan"))
    return out


def _cn_from_release(title: str) -> str:
    raw = re.sub(r"^\s*[\[\(【（<][^\]]{1,30}[\]\)】）>]\s*", "", title or "")
    raw = re.split(r"\s*/\s*", raw)[0]
    raw = re.sub(r"[\[\(【（<][^\]]*[\]\)】）>]", " ", raw)
    raw = re.sub(r"\s*[-–]\s*0?\d{1,4}(?:v\d)?(?:\s*\[[^\]]*\])?\s*$", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw if re.search(r"[\u4e00-\u9fff]", raw) else ""


def _html_results(query: str, url: str, href_part: str, source: str) -> list[Candidate]:
    text, final = _get_text_final(url)
    # 搜索页被重定向到首页时直接放弃，别把首页推荐当成搜索结果
    qmark = urllib.parse.quote(query)
    if "?" not in final and qmark.lower() not in final.lower() and "/search" not in final.lower():
        if final.rstrip("/").count("/") <= 3:
            return []
    out: list[Candidate] = []
    pattern = r'<a[^>]+href="([^"]*%s[^"]*)"[^>]*>(.*?)</a>' % re.escape(href_part)
    for m in re.finditer(pattern, text, re.S | re.I):
        title = _clean_html_title(m.group(2))
        cn = _cn_from_release(title)
        if not cn:
            continue
        if not _relevant(query, title):
            continue
        out.append(Candidate(title=cn, source=source, original=title))
    return out


def _mikan_html(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    return _html_results(q, "https://mikanani.me/Home/Search?searchstr=%s" % q,
                         "/Home/Episode/", "Mikan")


def _dmhy(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    return _html_results(q, "https://share.dmhy.org/topics/list?keyword=%s" % q,
                         "/topics/view/", "动漫花园")


def _acgrip(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    return _html_results(q, "https://acg.rip/?term=%s" % q,
                         "/t/", "ACG.RIP")


def _tvmaze(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    d = _get_json("https://api.tvmaze.com/search/shows?q=%s" % q)
    out: list[Candidate] = []
    for x in (d or [])[:5]:
        show = (x or {}).get("show") or {}
        name = (show.get("name") or "").strip()
        if name:
            out.append(Candidate(title=name, source="TVmaze",
                                 year=str(show.get("premiered") or "")[:4]))
    return out


def _bing(query: str) -> list[Candidate]:
    q = urllib.parse.quote("%s 中文名" % query)
    text = _get_text("https://cn.bing.com/search?q=%s&setlang=zh-hans&cc=cn" % q)
    out: list[Candidate] = []
    for m in re.finditer(r"<h2[^>]*>.*?<a[^>]*>(.*?)</a>", text, re.S):
        title = _clean_html_title(m.group(1))
        if title and re.search(r"[\u4e00-\u9fff]", title):
            out.append(Candidate(title=title, source="Bing"))
    return out


def _douban(query: str) -> list[Candidate]:
    q = urllib.parse.quote(query)
    text = _get_text("https://www.douban.com/search?q=%s" % q)
    out: list[Candidate] = []
    for m in re.finditer(r'<a[^>]+title="([^"]{1,120})"', text):
        title = _clean_html_title(m.group(1))
        if title and re.search(r"[\u4e00-\u9fff]", title):
            out.append(Candidate(title=title, source="豆瓣"))
    return out


def _cache_key(query: str, year: str, episode: str, kind: str,
               ihash: str = "") -> str:
    return ("v%d|%s|%s|%s|%s|%s" % (CACHE_VERSION, query.lower(), year,
                                    episode, kind, (ihash or "").lower()))[:360]


def clear(query: str, year: str = "", episode: str = "", kind: str = "",
          ihash: str = "") -> None:
    """让用户点“重新搜索”时清掉这一条旧结果。"""
    key = _cache_key((query or "").strip(), year, episode, kind, ihash)
    cache = _load_cache()
    if cache.pop(key, None) is not None:
        _save_cache(cache)


def cached(query: str, year: str = "", episode: str = "", kind: str = "",
           ihash: str = ""):
    """只读缓存；没有就返回 None（不联网）。"""
    key = _cache_key((query or "").strip(), year, episode, kind, ihash)
    hit = _load_cache().get(key)
    if isinstance(hit, dict):
        return [Candidate(**c) for c in hit.get("items") or [] if isinstance(c, dict)]
    return None


def _detect_kind(query: str, kind: str = "") -> str:
    return kind or "general"


def guess_kind(query: str, parsed=None) -> str:
    """返回 anime / general，供后台预取和对话框使用同一缓存键。"""
    if parsed is not None and (getattr(parsed, "group", "") or getattr(parsed, "episode", "")):
        return "anime"
    return "general"


def _rank(items: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for c in items:
        k = c.title.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        c.score = 80 if re.search(r"[\u4e00-\u9fff]", c.title) else 45
        out.append(c)
    out.sort(key=lambda c: (-c.score, len(c.title)))
    return out[:12]


def lookup(query: str, year: str = "", episode: str = "",
           kind: str = "", ihash: str = "", on_partial=None,
           stop_event=None) -> list[Candidate]:
    """并发查询；每回来一个源就通过 on_partial 追加结果。

    用户选定候选后把 stop_event 置位，下一轮立即取消还没回来的源。
    """
    global LAST_ERROR
    LAST_ERROR = ""
    query = (query or "").strip()
    if not query:
        return []
    kind = _detect_kind(query, kind)
    key = _cache_key(query, year, episode, kind, ihash)
    cache = _load_cache()
    hit = cache.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts") or 0) < CACHE_TTL:
        out = [Candidate(**c) for c in hit.get("items") or [] if isinstance(c, dict)]
        if on_partial and out:
            on_partial(out)
        return out

    if kind == "anime":
        providers = (_kisssub, _mikan, _mikan_html, _dmhy, _acgrip,
                     _bangumi, _wikipedia)
    else:
        # 豆瓣会跳安全验证，Bing 噪声太大；先不用这两个
        providers = (_tvmaze, _wikipedia)
    if ihash:
        def _hash_provider(_query: str, _hash: str = ihash):
            return _kisssub_hash(_hash)
        providers = (_hash_provider,) + providers

    items: list[Candidate] = []
    errors: list[str] = []

    def publish(final: bool = False) -> None:
        out = _rank(items)
        if out:
            cache[key] = {"ts": time.time(),
                          "items": [c.__dict__ for c in out]}
            _save_cache(cache)
            if on_partial:
                on_partial(out)
        elif final:
            cache[key] = {"ts": time.time() - CACHE_TTL + 300, "items": []}
            _save_cache(cache)

    ex = ThreadPoolExecutor(max_workers=len(providers), thread_name_prefix="title-lookup")
    names = {_kisssub: "爱恋动漫", _mikan: "Mikan", _mikan_html: "Mikan",
             _dmhy: "动漫花园", _acgrip: "ACG.RIP",
             _bangumi: "Bangumi", _wikipedia: "Wikipedia",
             _douban: "豆瓣", _bing: "Bing", _tvmaze: "TVmaze"}
    futs = {ex.submit(fn, query): names.get(fn, "") for fn in providers}
    deadline = time.time() + 6.0
    try:
        for fut in as_completed(futs, timeout=max(0.1, deadline - time.time())):
            try:
                items.extend(fut.result(timeout=0.2) or [])
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc)[:80])
            publish()
            if stop_event is not None and stop_event.is_set():
                break
    except Exception:  # noqa: BLE001
        errors.append("部分站点超时")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if not items:
        LAST_ERROR = "；".join(errors) or "没有找到匹配条目"
    publish(final=True)
    return _rank(items)

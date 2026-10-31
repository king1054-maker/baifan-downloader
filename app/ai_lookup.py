# -*- coding: utf-8 -*-
"""超智能鲸鱼娘：DeepSeek Search + deepseek-flash 提取中文名。

只发送原文件名/解析出的片名和搜索得到的标题摘要，不发送文件内容。
先调用 DeepSeek 官方 Search API 联网搜索，再让 deepseek-flash 只根据
搜索结果输出 JSON；AI 结果永远由用户点选，不自动覆盖。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from . import paths
from .title_lookup import Candidate

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-flash"
SOURCE = "超智能鲸鱼娘（DeepSeek 联网）"
_CACHE_FILE = paths.DATA_DIR / "ai_title_cache.json"
_CACHE_TTL = 30 * 24 * 3600


def _cache_key(ihash: str, original_name: str, task_key: str = "") -> str:
    key = "v2|%s|%s|%s" % (task_key or "", ihash or "",
                           original_name or "")
    return key.strip().lower()[:360]


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict):
                return obj
    except Exception:  # noqa: BLE001
        pass
    return {}


def load_cached(ihash: str, original_name: str,
                task_key: str = "") -> list[Candidate]:
    key = _cache_key(ihash, original_name, task_key)
    if not key:
        return []
    hit = _load_cache().get(key)
    if not isinstance(hit, dict):
        return []
    if time.time() - float(hit.get("ts") or 0) > _CACHE_TTL:
        return []
    return [Candidate(**x) for x in hit.get("items") or [] if isinstance(x, dict)]


def save_cached(ihash: str, original_name: str,
                candidates: list[Candidate], task_key: str = "") -> None:
    key = _cache_key(ihash, original_name, task_key)
    if not key or not candidates:
        return
    cache = _load_cache()
    cache[key] = {"ts": time.time(),
                  "items": [c.__dict__ for c in candidates[:8]]}
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        tmp.replace(_CACHE_FILE)
    except OSError:
        pass


def clear_cached(ihash: str, original_name: str, task_key: str = "") -> None:
    key = _cache_key(ihash, original_name, task_key)
    if not key:
        return
    cache = _load_cache()
    if cache.pop(key, None) is not None:
        try:
            _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False),
                                   encoding="utf-8")
        except OSError:
            pass


def _post(url: str, payload: dict, key: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _clean_title(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = re.sub(r"\s*[-_|]\s*(维基百科|百度百科|豆瓣|Anibase|Bangumi).*$", "",
               s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -_|")


def _fallback(results: list[dict]) -> list[Candidate]:
    out: list[Candidate] = []
    for r in results[:5]:
        t = _clean_title(str(r.get("title") or ""))
        if t and re.search(r"[\u4e00-\u9fff]", t):
            out.append(Candidate(title=t, original=str(r.get("title") or ""),
                                 source=SOURCE, url=str(r.get("url") or ""),
                                 score=95))
    return out


def query_name(filename: str, parsed, key: str) -> tuple[list[Candidate], str]:
    """返回 (候选, 错误说明)。网络/权限失败时候选为空。"""
    key = (key or "").strip()
    if not key:
        return [], "还没有填写 DeepSeek API Key"
    q = (getattr(parsed, "title", "") or filename or "").strip()
    if not q:
        return [], "文件名里没有可搜索的片名"
    try:
        s = _post(SEARCH_URL, {
            "query": q + " 中文名",
            "ori_question": "下载文件名：%s，请找它的官方中文名和集数。" % filename,
            "count": 6,
            "need_content": False,
        }, key)
    except Exception as exc:  # noqa: BLE001
        return [], "DeepSeek 搜索失败：%s" % str(exc)[:120]
    results = s.get("results") or []
    if not results:
        return [], "DeepSeek 没有搜到网页结果"
    material = [{"title": r.get("title"), "summary": r.get("summary"),
                 "url": r.get("url")} for r in results[:5]]
    prompt = (
        "你在帮下载器识别文件名。原始文件名：%s\n"
        "解析：片名=%s，集数=%s，字幕组=%s。\n"
        "下面是联网搜索结果，请只根据这些资料判断最可能的简体中文名。\n"
        "只返回 JSON（不要 Markdown），格式："
        '{"best":"...","alternatives":["...","..."],'
        '"episode":"...","source":"..."}。\n'
        "如果资料不足以确认，best 返回空字符串。资料：%s"
        % (filename, q, getattr(parsed, "episode", ""),
           getattr(parsed, "group", ""),
           json.dumps(material, ensure_ascii=False)[:9000]))
    try:
        c = _post(CHAT_URL, {
            "model": MODEL,
            "messages": [
                {"role": "system",
                 "content": "你是精确的影视/动画命名助手，只使用用户提供的资料，禁止编造。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 700,
        }, key)
        text = (c.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except Exception as exc:  # noqa: BLE001
        fb = _fallback(results)
        return fb, "" if fb else "DeepSeek 对话失败：%s" % str(exc)[:100]

    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    m = re.search(r"\{.*\}", text, re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            data = {}
    best = _clean_title(str(data.get("best") or ""))
    alts = data.get("alternatives") or []
    if not isinstance(alts, list):
        alts = []
    names: list[str] = []
    if best:
        names.append(best)
    for a in alts[:4]:
        a = _clean_title(str(a))
        if a and a not in names:
            names.append(a)
    if not names:
        fb = _fallback(results)
        return fb, "" if fb else "AI 没有从搜索结果里确定名字"
    src_url = str((results[0] or {}).get("url") or "")
    out: list[Candidate] = []
    for i, name in enumerate(names):
        out.append(Candidate(
            title=name,
            original=_clean_title(str((results[0] or {}).get("title") or "")),
            source=SOURCE + (" · 最佳" if i == 0 else " · 备选"),
            episode=str(data.get("episode") or getattr(parsed, "episode", "")),
            url=src_url,
            score=100 - i,
        ))
    return out, ""

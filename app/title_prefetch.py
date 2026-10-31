# -*- coding: utf-8 -*-
"""后台慢慢查名字：任务一出现就排队，查到直接写本地缓存。

不阻塞界面、不弹窗；用户点 ✨ 时如果缓存已经有结果就直接显示。
每次只查一条，查完歇 2 秒，避免把网络和对方站点打爆。
"""
from __future__ import annotations

import threading
import time

from . import title_lookup, title_parse


class TitlePrefetcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: list[tuple[str, list[str]]] = []
        self._seen: set[str] = set()
        self._thread: threading.Thread | None = None

    def submit(self, name: str, file_paths: list[str] | None = None,
               ihash: str = "") -> None:
        name = (name or "").strip()
        if not name:
            return
        with self._lock:
            if name in self._seen or len(self._seen) >= 40:
                return
            self._seen.add(name)
            self._queue.append((name, list(file_paths or []), ihash or ""))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="title-prefetch", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._thread = None
                    return
                name, files, ihash = self._queue.pop(0)
            try:
                parsed = title_parse.parse(name, files)
                q = parsed.title or name
                kind = title_lookup.guess_kind(name, parsed)
                if title_lookup.cached(q, parsed.year, parsed.episode, kind,
                                       ihash) is not None:
                    continue
                title_lookup.lookup(q, parsed.year, parsed.episode,
                                    kind=kind, ihash=ihash)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2.0)

# -*- coding: utf-8 -*-
"""本机小保险箱：用 Windows DPAPI 保护 API Key。

DPAPI 的密钥绑定当前 Windows 用户，别的用户/拷走 settings.json 都解不开；
密文外层再做 base64，避免明文出现在 JSON/日志里。不把 Key 写进源码、备份或日志。
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _bytes(blob: _DATA_BLOB) -> bytes:
    if not blob.pbData:
        return b""
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect(text: str) -> str:
    if not text:
        return ""
    try:
        src = _blob(text.encode("utf-8"))
        out = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(src), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return ""
        try:
            return "dpapi:v1:" + base64.b64encode(_bytes(out)).decode("ascii")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return ""


def unprotect(token: str) -> str:
    token = str(token or "")
    if not token.startswith("dpapi:v1:"):
        return ""
    try:
        raw = base64.b64decode(token[9:])
        src = _blob(raw)
        out = _DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            return ""
        try:
            return _bytes(out).decode("utf-8", "replace")
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)
    except Exception:  # noqa: BLE001
        return ""


def get_key(settings: dict) -> str:
    return unprotect(str(settings.get("ai_key_enc") or ""))


def set_key(settings: dict, key: str) -> bool:
    key = (key or "").strip()
    enc = protect(key) if key else ""
    if key and not enc:
        return False
    settings["ai_key_enc"] = enc
    return True


def clear_key(settings: dict) -> None:
    settings.pop("ai_key_enc", None)

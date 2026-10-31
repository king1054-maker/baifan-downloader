# -*- coding: utf-8 -*-
"""按窗口标题抓图（不受其它窗口遮挡影响）。

用 PrintWindow + PW_RENDERFULLCONTENT，比全屏截图靠谱：
WorkBuddy 自己挡在前面也能抓到目标窗口的真实画面。

用法：python .cache/winshot.py "白饭下载器" docs/xxx.png
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys

from PySide6.QtGui import QGuiApplication, QImage

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
        ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG),
        ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


def find_window(prefix: str) -> int:
    hits: list[tuple[int, str]] = []

    def cb(h, _l):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, buf, n + 1)
                if buf.value.startswith(prefix):
                    hits.append((h, buf.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)(cb), 0)
    if not hits:
        return 0
    # 标题最长的一般是主窗口
    return max(hits, key=lambda it: len(it[1]))[0]


def grab(hwnd: int) -> QImage:
    rect = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = width
    bi.bmiHeader.biHeight = -height          # 负数 = 从上到下
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bi), 0)

    img = QImage(buf.raw, width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return img


def main() -> int:
    prefix, out = sys.argv[1], sys.argv[2]
    app = QGuiApplication([])  # noqa: F841  QImage 需要
    hwnd = find_window(prefix)
    if not hwnd:
        print("没找到窗口：", prefix)
        return 1
    img = grab(hwnd)
    if not img.save(out):
        print("保存失败")
        return 1
    print(f"ok {img.width()}x{img.height()} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

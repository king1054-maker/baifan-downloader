# -*- coding: utf-8 -*-
"""生成三枚内置提示音（纯标准库，不依赖任何素材）。

音色走「软、短、不刺耳」：正弦 + 少量二次谐波 + 指数衰减，
在笔记本小喇叭上也不会爆音。
"""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

RATE = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def _write(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(1e-6, max(abs(s) for s in samples))
    scale = 0.82 / peak
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * scale)) * 32767))
                    for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)


def _env(i: int, n: int, attack: float = 0.008, decay: float = 4.2) -> float:
    t = i / n
    a = min(1.0, (i / RATE) / attack) if attack > 0 else 1.0
    return a * math.exp(-decay * t)


def _bell(freq: float, dur: float, amp: float = 1.0) -> list[float]:
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        e = _env(i, n, 0.006, 4.6)
        v = (math.sin(2 * math.pi * freq * t)
             + 0.28 * math.sin(2 * math.pi * freq * 2.01 * t)
             + 0.10 * math.sin(2 * math.pi * freq * 3.02 * t))
        out.append(amp * v * e)
    return out


def _tone(f0: float, f1: float, dur: float, amp: float = 1.0,
          vib: float = 0.0, decay: float = 3.2) -> list[float]:
    n = int(RATE * dur)
    phase = 0.0
    out = []
    for i in range(n):
        t = i / n
        f = f0 + (f1 - f0) * t
        if vib:
            f *= 1.0 + vib * math.sin(2 * math.pi * 6.5 * (i / RATE))
        phase += 2 * math.pi * f / RATE
        e = _env(i, n, 0.010, decay)
        out.append(amp * math.sin(phase) * e)
    return out


def _mix(*tracks: list[float]) -> list[float]:
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    return out


def _concat(*tracks: list[float]) -> list[float]:
    out: list[float] = []
    for t in tracks:
        out.extend(t)
    return out


def _silence(dur: float) -> list[float]:
    return [0.0] * int(RATE * dur)


def ding() -> list[float]:
    """叮——咚：两声清亮的小铃。"""
    return _concat(
        _bell(1046.5, 0.30, 0.75),
        _silence(0.045),
        _bell(1568.0, 0.52, 0.65),
    )


def bite() -> list[float]:
    """咬一口：短促上扬 + 一点脆响。"""
    rnd = random.Random(7)
    n = int(RATE * 0.20)
    crunch = []
    for i in range(n):
        e = math.exp(-18.0 * i / n)
        crunch.append(rnd.uniform(-1, 1) * e * 0.22)
    return _mix(_tone(320, 880, 0.20, 0.9, vib=0.02, decay=5.0), crunch)


def burp() -> list[float]:
    """打个饱嗝：低频下滑 + 轻微颤音。"""
    return _mix(
        _tone(240, 110, 0.34, 0.85, vib=0.10, decay=3.0),
        _tone(120, 58, 0.34, 0.45, vib=0.08, decay=2.6),
    )


def soft_water() -> list[float]:
    """原创短促水波音：合成波形，不使用第三方语音采样。"""
    rnd = random.Random(21)
    n = int(RATE * 0.42)
    out = []
    for i in range(n):
        t = i / RATE
        env = (1 - math.exp(-t * 160)) * math.exp(-t * 14)
        v = (0.21 * math.sin(2 * math.pi * (430 * t - 300 * t * t)) * env
             + 0.06 * math.sin(2 * math.pi * 620 * t) * env)
        v += 0.012 * rnd.uniform(-1, 1) * math.exp(-t * 60)
        out.append(v)
    return out


def main() -> None:
    for name, fn in (("ding", ding), ("bite", bite), ("burp", burp), ("soft_water", soft_water)):
        p = OUT / ("%s.wav" % name)
        _write(p, fn())
        print("%-6s %6.1f KB" % (p.name, p.stat().st_size / 1024))


if __name__ == "__main__":
    main()

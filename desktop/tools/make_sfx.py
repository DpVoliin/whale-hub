#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成挂件音效（纯标准库，无依赖）：鸭子"呱" + 可爱"啵"。

为什么要自己合成：现成音效素材要么带版权、要么授权不明；自己算波形最干净。
自己算出来的波形没有任何版权问题，而且体积小（各 ~10KB）。

用法： python3 tools/make_sfx.py  → 写到 assets/sfx_duck.wav / assets/sfx_pop.wav
"""
import math
import os
import random
import struct
import sys
import wave

SR = 22050
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
random.seed(20260918)


def write_wav(path, samples):
    peak = max(1e-6, max(abs(s) for s in samples))
    scale = 0.86 / peak                       # 归一化到 -0.86（留 headroom 不削波）
    data = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * scale)) * 32767))
                    for s in samples)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)
    return len(samples) / float(SR)


def duck():
    """鸭子的一声"呱"：频率快速下滑 + 奇偶谐波堆出蜂鸣感 + 中段二次突起（像"呱啊"两截）。"""
    dur = 0.22
    n = int(SR * dur)
    out = []
    ph = 0.0
    for i in range(n):
        t = i / SR
        f = 168 + 430 * math.exp(-11.0 * t)            # 起音高、迅速掉下来
        f *= 1.0 + 0.035 * math.sin(2 * math.pi * 42 * t)   # 一点点抖动，别像电子音
        ph += 2 * math.pi * f / SR
        s = (1.00 * math.sin(ph) + 0.52 * math.sin(2 * ph)
             + 0.38 * math.sin(3 * ph) + 0.20 * math.sin(5 * ph)
             + 0.10 * math.sin(7 * ph))
        s = math.tanh(s * 1.5)                          # 软削波 → 更像嗓子的粗糙感
        if t < 0.016:                                   # 起音的"嘎"擦音
            s += random.uniform(-1, 1) * 0.55 * (1 - t / 0.016)
        env = min(1.0, t / 0.010) * math.exp(-7.2 * t)
        if 0.055 < t < 0.145:                           # 二次突起：一声里有两个小峰
            env *= 1.0 + 0.55 * math.sin((t - 0.055) / 0.090 * math.pi)
        out.append(s * env * 0.5)
    return out


def pop():
    """可爱的一声"啵"：短促正弦上滑 + 快速衰减（点击时的"Q弹"感）。"""
    dur = 0.13
    n = int(SR * dur)
    out = []
    ph = 0.0
    for i in range(n):
        t = i / SR
        f = 560 + 900 * math.exp(-24.0 * t)             # 上滑（"啵"往上挑）
        ph += 2 * math.pi * f / SR
        s = math.sin(ph) + 0.25 * math.sin(2 * ph)
        env = min(1.0, t / 0.004) * math.exp(-26.0 * t)
        out.append(s * env * 0.5)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, gen in (("sfx_duck.wav", duck), ("sfx_pop.wav", pop)):
        p = os.path.join(OUT, name)
        dur = write_wav(p, gen())
        sz = os.path.getsize(p)
        # 读回自检：采样率/声道/时长/峰值
        with wave.open(p, "rb") as w:
            fr = w.getframerate(); ch = w.getnchannels(); nf = w.getnframes()
            raw = w.readframes(nf)
        vals = struct.unpack("<%dh" % nf, raw)
        print("  %-14s %.3f s  %d Hz  %d ch  %d bytes  峰值 %d  RMS %.1f  静音段 %d"
              % (name, nf / float(fr), fr, ch, sz, max(abs(v) for v in vals),
                 math.sqrt(sum(v * v for v in vals) / max(1, nf)),
                 sum(1 for v in vals if abs(v) < 20)))
    print("  写到 %s" % OUT)
    sys.exit(0)


if __name__ == "__main__":
    main()

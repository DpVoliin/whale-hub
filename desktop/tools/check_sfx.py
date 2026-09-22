#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音效自检：过零率（≈基频）随时间怎么走 —— 确认合成的是想要的那种音型。"""
import struct
import sys
import wave


def zcr(path, a, b):
    with wave.open(path, "rb") as w:
        fr = w.getframerate()
        n = w.getnframes()
        v = struct.unpack("<%dh" % n, w.readframes(n))
    seg = v[int(a * fr):int(b * fr)]
    if len(seg) < 4:
        return 0.0
    z = sum(1 for i in range(1, len(seg)) if (seg[i - 1] < 0) != (seg[i] < 0))
    return z / 2.0 / (len(seg) / float(fr))


for p, lab in (("assets/sfx_duck.wav", "鸭叫"), ("assets/sfx_pop.wav", "啵")):
    print("  %-4s 起音 %5.0f Hz → 中段 %5.0f Hz → 尾段 %5.0f Hz"
          % (lab, zcr(p, 0.0, 0.03), zcr(p, 0.04, 0.09), zcr(p, 0.6 * 0.22, 0.22)))
print("  （鸭叫要从高往低掉才有'呱'的滑音；尾部已很弱，过零率会失真，属正常）")
sys.exit(0)

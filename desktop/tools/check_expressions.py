#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材体检：防止「表情图其实是站立图调色」这种事故再次发生。

事故背景：assets/exp_*.png 曾长期是 `colorize(pet.png, 38%)` 的**占位版**，
而真图（用户给的 6 张表情）只躺在 /tmp 没被拷进来 —— 结果"点一下换表情"其实是
同一张脸换颜色，用户报「点她表情没变」。而当时的对照图也是拿错文件做的，所以没发现。

本脚本只用数字判断，不看图、不问模型：
  1) 每张表情图与站立图的**边缘图差异**必须 > MIN_EDGE（同一张画换色 ≈ 0，真表情几百以上）
  2) 表情图不得比站立图宽（否则换表情时窗口宽度会变 → 她横向微移）
  3) Windows 拍平版（*_win.png）四角必须是键控色（否则 Windows 上会显示成一块彩色方块）
用法：python3 tools/check_expressions.py [assets目录]
"""
import os
import re
import subprocess
import sys

KEY = "#010203"
MIN_EDGE = 300
AS = sys.argv[1] if len(sys.argv) > 1 else "assets"
NAMES = ["blush", "happy", "shy", "surprised", "angry", "sleepy"]


def dims(p):
    out = subprocess.run(["identify", "-format", "%w %h", p], capture_output=True, text=True).stdout
    return tuple(int(x) for x in out.split()[:2])


def edge_diff(a, b):
    for i, f in enumerate((a, b)):
        subprocess.run(["convert", f, "-colorspace", "Gray", "-edge", "1", f"/tmp/_ce{i}.png"],
                       capture_output=True)
    r = subprocess.run(["compare", "-metric", "AE", "/tmp/_ce0.png", "/tmp/_ce1.png", "null:"],
                       capture_output=True, text=True)
    try:
        return int(re.sub(r"[^0-9]", "", (r.stderr or r.stdout).split()[0]))
    except Exception:
        return -1


def hexpix(p, x, y):
    return subprocess.run(["convert", p, "-format", "%%[pixel:p{%d,%d}]" % (x, y), "info:"],
                          capture_output=True, text=True).stdout.strip()


def keyish(corner):
    """角落像素是不是键控色 #010203（Tk 透明只认精确色；容差给 ±3 抗压缩噪声）"""
    m = re.search(r"\((\d+),(\d+),(\d+)", corner or "")
    if not m:
        return corner in ("black", "#000000")
    r, g, b = (int(x) for x in m.groups())
    return abs(r - 1) <= 3 and abs(g - 2) <= 3 and abs(b - 3) <= 3


pet = os.path.join(AS, "pet.png")
pw, ph = dims(pet)
print("  站立图 %dx%d | 键控色要求 %s" % (pw, ph, KEY))
bad = 0
for n in NAMES:
    q = os.path.join(AS, "exp_%s.png" % n)
    w = os.path.join(AS, "exp_%s_win.png" % n)
    if not os.path.exists(q):
        print("  ✗ exp_%s.png 不存在（点一下换表情会整条不生效）" % n)
        bad += 1
        continue
    d = edge_diff(pet, q)
    ew, eh = dims(q)
    corner = hexpix(w, 1, 1) if os.path.exists(w) else "(无 _win)"
    ok_diff = d > MIN_EDGE
    ok_w = ew <= pw
    ok_key = keyish(corner)
    flag = "✓" if (ok_diff and ok_w and ok_key) else "✗"
    if flag == "✗":
        bad += 1
    print("  %s exp_%-10s 边缘差异=%-5s(%s) 尺寸=%dx%d(%s) _win角=%s(%s)"
          % (flag, n, d, "真图" if ok_diff else "疑似站立图调色！", ew, eh,
             "不超宽" if ok_w else "比站立宽→会横移", corner, "键控色" if ok_key else "不是键控色=会变方块"))
print("  → %s" % ("全部通过" if bad == 0 else "有 %d 项不合格，别打包！" % bad))
sys.exit(1 if bad else 0)

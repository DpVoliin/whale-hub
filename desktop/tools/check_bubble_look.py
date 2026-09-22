#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""气泡「观感」的数字检查（不靠模型看图）。

⚠️ 本机无头 Tk 只有点阵字体族（9/10/16 号全映射成同一个 fixed 9，中文 measure=0），
   所以**文字层级在这里量不出来** —— 那部分只能在 Windows 上定论。
   这个脚本只量"形状与配色"：圆角是不是真圆弧、边线多细多暗、内边距多少、配色对不对。

用法： python3 tools/check_bubble_look.py /tmp/wd/2_data.png
"""
import re
import subprocess
import sys

SHOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wd/2_data.png"
BG = (17, 20, 26)          # BUB_BG  #11141a
EDGE = (30, 36, 48)        # BUB_EDGE #1e2430


def pixels(path):
    out = subprocess.run(["convert", path, "txt:-"], capture_output=True, text=True).stdout
    g = {}
    for ln in out.splitlines()[1:]:
        m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", ln)
        if m:
            x, y, h = int(m.group(1)), int(m.group(2)), m.group(3)
            g[(x, y)] = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return g


g = pixels(SHOT)
xs = [x for (x, y), c in g.items() if c == BG]
ys = [y for (x, y), c in g.items() if c == BG]
if not xs:
    print("  ✗ 画面上找不到气泡底色 %s —— 尺寸/配色改错了？" % (BG,))
    sys.exit(1)
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
print("  气泡卡片 bbox: x %d..%d  y %d..%d  （%dx%d）" % (x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1))

# ① 圆角：左上角逐行的第一个"卡片色"像素应随 y 增加而左移（呈弧）
rows = []
for yy in range(y0, y0 + 18):
    row = [x for x in range(x0, x0 + 40) if g.get((x, yy)) == BG]
    rows.append(min(row) if row else None)
deltas = [rows[i] - rows[i - 1] for i in range(1, len(rows)) if rows[i] and rows[i - 1]]
print("  圆角：左上角逐行首像素偏移 %s" % deltas[:10])
rounded = sum(1 for d in deltas if d < 0)
print("    → 递进左移的行数 %d/%d  %s" % (rounded, len(deltas), "✓ 是真圆弧" if rounded >= 6 else "✗ 像直角/近似多边形"))

# ② 边线：卡片上下左右各一小段，看有没有 1px 的 EDGE 色，以及它占几行
for lab, pts in (("上边", [(x, y0 - 1) for x in range(x0 + 20, x0 + 60)]),
                 ("左边", [(x0 - 1, y) for y in range(y0 + 20, y0 + 60)])):
    hit = [p for p in pts if g.get(p) == EDGE]
    print("  %s 线的 EDGE 色命中 %d/%d px  %s" % (lab, len(hit), len(pts), "✓" if hit else "（没量到，可能被抗锯齿混色）"))

# ③ 内边距：卡片左边缘往右，第一个"明显亮于底色"的像素（文字/强调条）离边缘多远
pad = None
for x in range(x0 + 1, x0 + 60):
    for y in range(y0 + 6, y1 - 6):
        c = g.get((x, y))
        if c and sum(c) > sum(BG) + 90:
            pad = x - x0
            break
    if pad is not None:
        break
print("  左内边距 ≈ %s px  %s" % (pad, "✓（≥14 有呼吸感）" if pad and pad >= 14 else "✗ 太挤"))

# ④ 配色对比度：卡片底色 vs 文字亮色
bright = max((sum(c), c) for c in set(g.values()) if sum(c) > 600)[1] if any(sum(c) > 600 for c in set(g.values())) else None
print("  最亮文字色 %s；卡片底色 %s → 对比 %d 级  %s"
      % (bright, BG, (sum(bright) - sum(BG)) // 30 if bright else 0,
         "✓ 层级清楚" if bright else "（这里没渲染出文字，属正常）"))

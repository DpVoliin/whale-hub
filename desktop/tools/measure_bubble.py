#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量气泡卡片的真实几何：先取最大同色连通块（=卡片本身），再相对它量圆角/边线/内边距。"""
import re
import subprocess
import sys
from collections import deque

SHOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wd/2_data.png"
BG = (17, 20, 26)
EDGE = (30, 36, 48)

out = subprocess.run(["convert", SHOT, "txt:-"], capture_output=True, text=True).stdout
g = {}
for ln in out.splitlines()[1:]:
    m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", ln)
    if m:
        x, y, h = int(m.group(1)), int(m.group(2)), m.group(3)
        g[(x, y)] = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

mask = {p for p, c in g.items() if c == BG}
seen, best = set(), None
for p in mask:
    if p in seen:
        continue
    q = deque([p]); seen.add(p); comp = [p]
    while q:
        x, y = q.popleft()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n in mask and n not in seen:
                seen.add(n); q.append(n); comp.append(n)
    if best is None or len(comp) > len(best):
        best = comp

xs = [p[0] for p in best]; ys = [p[1] for p in best]
x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
bs = set(best)
print("  卡片（最大同色连通块）%d px  bbox x %d..%d y %d..%d = %dx%d"
      % (len(best), x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1))

print("\n  左上角 24x16 字符图（# = 卡片色，. = 非卡片）：")
for y in range(y0, y0 + 16):
    print("   " + "".join("#" if (x, y) in bs else "." for x in range(x0, x0 + 24)))

d = []
for y in range(y0, y0 + 20):
    row = [x for x in range(x0, x0 + 40) if (x, y) in bs]
    if row:
        d.append(min(row) - x0)
desc = sum(1 for i in range(1, len(d)) if d[i] < d[i - 1])
print("\n  左上角逐行首像素偏移：%s" % d)
print("    → 连续递减 %d 步 %s" % (desc, "✓ 是真圆弧" if desc >= 6 else "✗ 像直角/近似多边形"))

print("\n  卡片外侧 1px 的颜色（边上应是边线色 #1e2430）：")
print("   上边:", [g.get((x, y0 - 1)) for x in range(x0 + 20, x0 + 26)])
print("   左边:", [g.get((x0 - 1, y)) for y in range(y0 + 20, y0 + 26)])
n_up = sum(1 for x in range(x0 + 20, x0 + 80) if g.get((x, y0 - 1)) == EDGE)
n_lf = sum(1 for y in range(y0 + 20, y0 + 80) if g.get((x0 - 1, y)) == EDGE)
print("   边线命中：上 %d/60  左 %d/60" % (n_up, n_lf))

pad = None
for x in range(x0 + 1, x0 + 50):
    hit = [c for c in (g.get((x, y)) for y in range(y0 + 8, y1 - 8))
           if c and sum(c) > sum(BG) + 120]
    if len(hit) > 3:
        pad = x - x0
        break
print("\n  左内边距 ≈ %s px  %s"
      % (pad, "✓ 有呼吸感" if pad and pad >= 14 else "✗ 太挤（或本行没有文字）"))
print("  卡片高度 %d px（旧版 140；新版按行类型算 = 14+42+28+24+14 = 122）" % (y1 - y0 + 1))

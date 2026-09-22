#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量菜单里各行的「文字左边缘」是否齐平。

要点：
  · **不能**按固定行距去猜行位置 —— 菜单里有标题行(26px)和分隔行(9px)，会整体漂移
  · **不能**把绿色"开"状态小条当成文字 —— 按颜色区分（绿条是 #4f9e80，文字是灰白）
"""
import re
import subprocess
import sys

SHOT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wd/menushot/01_menu.png"
X0, Y0, W, H = 121, 151, 236, 476          # 菜单区域（左上角=点击点）

out = subprocess.run(["convert", SHOT, "txt:-"], capture_output=True, text=True).stdout
pix = {}
for ln in out.splitlines()[1:]:
    m = re.match(r"(\d+),(\d+):.*?#([0-9A-Fa-f]{6})", ln)
    if m:
        x, y, h = int(m.group(1)), int(m.group(2)), m.group(3)
        pix[(x, y)] = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def is_text(c):
    r, g, b = c
    return sum(c) > 380 and (max(c) - min(c)) < 40      # 亮且接近中性灰 = 文字

# ⚠️ 必须先确认「这一行在卡片内部」：菜单外的浅灰壁纸（#dfe6ee 一带）亮度和中性度
#    跟文字重合，会被误判成文字 → 量出一堆假错位。
def in_card(y):
    c = pix.get((X0 + W // 2, y))
    return bool(c) and sum(c) < 150          # 卡片底色 #11141a 很暗

bands = []
for y in range(Y0, Y0 + H):
    if not in_card(y):
        bands.append((y, None))
        continue
    xs = [x for x in range(X0 + 6, X0 + W - 6) if is_text(pix.get((x, y), (0, 0, 0)))]
    bands.append((y, min(xs) if xs else None))

rows = []
cur = None
for y, mx in bands:
    if mx is None:
        if cur:
            rows.append(cur); cur = None
        continue
    if cur is None:
        cur = [y, y, mx]
    else:
        cur[1] = y; cur[2] = min(cur[2], mx)
if cur:
    rows.append(cur)

# 忽略贴着区域上下边缘的行带：菜单四角的圆弧处会**露出浅色壁纸**，会被误判成文字
rows = [r for r in rows if (r[1] - r[0]) >= 6 and r[0] - Y0 > 8 and r[1] - Y0 < H - 8]

print("  文字行带（y 范围 → 最左字像素相对菜单左缘）")
edges = []
for y0, y1, mx in rows:
    rel = mx - X0
    edges.append(rel)
    print("    y %4d..%-4d  相对 x = %d" % (y0 - Y0, y1 - Y0, rel))
if edges:
    print("  → 左边缘：最小 %d / 最大 %d / 差 %d px %s"
          % (min(edges), max(edges), max(edges) - min(edges),
             "✓ 齐平" if max(edges) - min(edges) <= 2 else "✗ 有错位"))

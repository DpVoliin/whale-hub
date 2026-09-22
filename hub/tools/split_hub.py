#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 hub.py 按章节机械切成 src/whalehub/NN_*.py 片段（切片，不重写、不重排）。

切点来自文件里已有的章节注释，保证：
  - 每个片段单独是合法 Python（片段内不跨文件引用）
  - **按序合并回去与原 hub.py 逐字节一致**（这就是等价性的证明）
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB = os.path.join(ROOT, "hub.py")
SRC = os.path.join(ROOT, "src", "whalehub")

lines = open(HUB, encoding="utf-8").read().splitlines(keepends=True)

# 章节标题行 → 片段名（顺序即合并顺序）
MARKS = [
    ("# ----------------------------------------------------------------- 配置 / 存储", "10_core"),
    ("# ----------------------------------------------------------------- 课表 / 日程", "20_timetable"),
    ("# ----------------------------------------------------------------- 规则引擎", "30_rules"),
    ("# ----------------------------------------------------------------- 脱敏（给 AI 之前）", "40_privacy"),
    ("# ---- 天气：主用中国天气网", "50_weather"),
    ("# --------------------------------------------------------------- 个人基线 / 惊讶度", "60_analysis"),
    ("# ----------------------------------------------------------------- 主动关心", "70_care"),
    ("# ------------------------------------------------------------- 人设包（personas/）", "80_persona"),
    ("# ------------------------------------------------------------- 外挂扩展（ext/）", "90_ext"),
    ("# ----------------------------------------------------------------- 调度线程", "95_scheduler"),
    ("# ----------------------------------------------------------------- HTTP", "97_http"),
]

cuts = []
for pat, name in MARKS:
    idx = next((i for i, l in enumerate(lines) if l.startswith(pat)), None)
    assert idx is not None, "找不到章节: %s" % pat
    cuts.append((idx, name))

# 入口（main + __main__ 块）从 main() 开始切到最后
i_main = next(i for i, l in enumerate(lines) if l.startswith("def main("))
cuts.append((i_main, "99_main"))

os.makedirs(SRC, exist_ok=True)
bounds = [0] + [c[0] for c in cuts] + [len(lines)]
names = ["00_header"] + [c[1] for c in cuts]
total = 0
for k in range(len(names)):
    chunk = lines[bounds[k]:bounds[k + 1]]
    txt = "".join(chunk)
    open(os.path.join(SRC, names[k] + ".py"), "w", encoding="utf-8").write(txt)
    total += len(txt)
    print("  %-14s %5d 行" % (names[k] + ".py", len(chunk)))

orig = "".join(lines)
print("  切片总字节 %d / 原文件 %d / 一致: %s" % (total, len(orig), total == len(orig)))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追踪连点的每一拍：气泡 idx / 渲染出的行 / 窗口几何 / 形象屏幕 x。
用户报"点第三下信息会乱跳"，这里要把"跳"变成数字。"""
import importlib.util
import os
import sys
import time

ROOT = "/home/ubuntu/whale-desktop"
os.environ["DISPLAY"] = sys.argv[1] if len(sys.argv) > 1 else ":97"
spec = importlib.util.spec_from_file_location("wd", os.path.join(ROOT, "whale_desk.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

cfg = m.load_cfg(os.path.join(ROOT, "whale_desk.json"))
cfg.setdefault("sound", {})["enabled"] = False
cfg.setdefault("pet", {})["pos"] = [40, 40]
w = m.Widget(cfg, os.path.join(ROOT, "whale_desk.json"))
w.root.geometry("420x420+40+30")
w.root.update()
w.root.after(m.TICK_MS, w.animate)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        w.root.update()
        time.sleep(0.006)


def snap(tag):
    try:
        rows = w._bubble_rows()
    except Exception as e:
        rows = [("ERR", str(e))]
    txt = " | ".join((r[1] if isinstance(r, (list, tuple)) and len(r) > 1 else str(r))[:26]
                     for r in rows)
    print("  %-8s idx=%-3s state=%-5s 窗口=%-14s 形象屏幕x=%-5s"
          % (tag, w.bubble_idx, w.state,
             "%dx%d+%d+%d" % (w.root.winfo_width(), w.root.winfo_height(),
                              w.root.winfo_x(), w.root.winfo_y()),
             w.pet_xy[0] if w.pet_xy else "-"))
    print("           行：%s" % txt[:150])


pump(0.5)
snap("初始")
q = list((cfg.get("bubbles") or {}).get("queue") or [])
print("  队列共 %d 条：%s" % (len(q), " ／ ".join(str(x)[:20] for x in q)))
for i in (1, 2, 3, 4):
    w.on_click()
    pump(0.5)
    snap("第%d下" % i)
# 再看"刷新一次"会不会把队列/内容换掉（刷新是每分钟一次的，可能正是"乱跳"来源）
print("\n  —— 模拟一次数据刷新（60 秒那次）——")
before = [str(r) for r in w._bubble_rows()]
w.refresh()
pump(0.4)
after = [str(r) for r in w._bubble_rows()]
print("  刷新后 idx=%s，内容是%s" % (w.bubble_idx, "换了 ✗ 会乱跳" if before != after else "没变 ✓"))
w.root.destroy()

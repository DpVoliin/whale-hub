#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截一张「新右键菜单」的实际样子 + 一张「拉伸不被裁」的对照，给用户看效果。

⚠️ 截屏用外部 import 命令，但只能在主循环之外的直线代码里调（老坑）。
"""
import importlib.util
import os
import subprocess
import sys
import time

ROOT = "/home/ubuntu/whale-desktop"
os.environ["DISPLAY"] = sys.argv[1] if len(sys.argv) > 1 else ":97"
OUT = "/tmp/wd"
spec = importlib.util.spec_from_file_location("wd", os.path.join(ROOT, "whale_desk.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

cfg = m.load_cfg(os.path.join(ROOT, "whale_desk.json"))
cfg.setdefault("sound", {})["enabled"] = False          # 截图时别出声
w = m.Widget(cfg, os.path.join(ROOT, "whale_desk.json"))
w.root.geometry("420x420+40+30")
w.root.update()
w.root.after(m.TICK_MS, w.animate)                      # ★ 驱动脚本必须自己挂主循环


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        w.root.update()
        time.sleep(0.006)


# 一张"点一下拉伸到峰值"的对照：左=常态，右=拉伸峰值（看右边有没有被裁）
pump(0.4)
import_ = lambda name: subprocess.run(["import", "-window", "root", os.path.join(OUT, name)],
                                      capture_output=True)
import_("menu_shot_0_pet.png")
w.sq["amp"], w.sq["ph"] = m.STRETCH_AMP_MAX, 1.55     # sin≈1 → 峰值
pump(0.02)
import_("menu_shot_1_stretch.png")
w.sq["amp"], w.sq["ph"] = 0.0, 0.0
pump(0.3)

# 菜单：开出来再截
class _Ev(object):
    x_root = 520
    y_root = 60


w.menu(_Ev())
pump(0.4)
print("  菜单 geometry = %s" % (getattr(w, "_menu_win", None).winfo_geometry()
                              if getattr(w, "_menu_win", None) else "-"))
import_("menu_shot_2_menu.png")
w._close_menu()
pump(0.2)
w.root.destroy()
print("  截图：menu_shot_0_pet.png / menu_shot_1_stretch.png / menu_shot_2_menu.png")

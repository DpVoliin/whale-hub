#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包成 Windows exe（用 wine 里的 Windows Python 跑 PyInstaller）。
!!!!! 所有 print 必须纯 ASCII —— Windows Python 的 stdout 默认 cp936，
      打印中文会直接 UnicodeEncodeError 崩掉（踩过）。注释中文没事。!!!!!

用法（在 wine 里跑）：
    wine $HOME/winbuild/pywin/python/python.exe build_exe.py
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PC_SRC = "/opt/whale/whale-pc/whale_pc.py"      # 采集模块源码（唯一一份，不拷贝）
SEP = os.pathsep                     # Windows 下是 ';'
NAME = "WhaleDesk"
CLI = "WhaleDesk-CLI"
ICON = os.path.join(ROOT, "assets", "whaledesk.ico")


def build(kind):
    work = os.path.join(ROOT, "build", kind)
    shutil.rmtree(work, ignore_errors=True)
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--noupx",
        "--name", CLI if kind == "cli" else NAME,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", work,
        "--specpath", work,
        # ⚠️ 这里必须是**绝对路径**：用了 --specpath 之后，相对路径会按 spec 所在目录解，
        #    于是报 "Unable to find ...\build\cli\assets"（踩过）。
        "--add-data", "%s%sassets" % (os.path.join(ROOT, "assets"), SEP),
        # ⚠️ PyInstaller 不会自动收 ctypes 的子模块 —— 采集模块 import ctypes.wintypes，
        #    漏了这行：本机裸跑正常、打包后报 "No module named 'ctypes.wintypes'"
        "--hidden-import", "ctypes.wintypes",
        "--hidden-import", "ctypes",
        # 采集模块（源码只有一份，在 ~/whale-pc/）：打进包内，运行时会从 _MEIPASS 加载
        "--add-data", "%s%s%s" % (PC_SRC, SEP, "."),
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.font",
        "--hidden-import", "tkinter.messagebox",
    ]
    if kind == "cli":
        args.append("--console")
    else:
        args.append("--windowed")
    if os.path.exists(ICON):
        args += ["--icon", ICON]
    args.append(os.path.join(ROOT, "whale_desk.py"))
    print("[build] %s ..." % kind)
    r = subprocess.run(args, capture_output=True, text=True)
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    print(tail.encode("ascii", "replace").decode("ascii"))
    return r.returncode


rc1 = build("cli")
rc2 = build("gui")
d = os.path.join(ROOT, "dist")
for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
    p = os.path.join(d, f)
    print("  %-22s %10d bytes" % (f, os.path.getsize(p)))
print("exit codes: cli=%d gui=%d" % (rc1, rc2))
sys.exit(0 if (rc1 == 0 and rc2 == 0) else 1)

#!/bin/bash
# 起一个无头 X 屏（本机没有真实桌面，只有靠 Xvfb 才能真跑真截）
# 用法：bash tools/start_xvfb.sh [display] [WxH]
set -u
D="${1:-:97}"
G="${2:-900x640x24}"
ps -eo pid,cmd | grep "Xvfb $D " | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null
sleep 0.5
setsid Xvfb "$D" -screen 0 "$G" </dev/null >/dev/null 2>&1 &
sleep 1.5
ps -eo pid,cmd | grep "Xvfb $D " | grep -v grep | awk '{print "  Xvfb 已起 pid="$1" 屏="$2}' | head -1

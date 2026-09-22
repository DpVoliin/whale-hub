#!/bin/bash
# 守护"会拿主意的嘴"：cron 每分钟 + 开机各跑一次，没在跑就拉起来（幂等）
#
# ⚠️ 判活只认"解释器 + 脚本"的精确形态：否则调用它的命令行自己也会被 pgrep 命中 → 永远不启动。
S="/opt/whale/.hermes/scripts"
P="/usr/bin/python3 ${S}/whale""_speaker.py"

[ -f "$S/whale_speaker.py" ] || exit 0
pgrep -f "^${P}$" >/dev/null 2>&1 && exit 0
cd "$S" || exit 0
setsid nohup $P >> "$S/whale_speaker.log" 2>&1 </dev/null &

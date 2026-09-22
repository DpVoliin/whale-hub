#!/bin/bash
# 无头自检：起 Xvfb + 铺背景 → 驱动挂件各状态 → 截图 + 断言
set -u
D="${1:-:97}"
OUT="${2:-/tmp/wd}"
cd "$(dirname "$0")/.."
mkdir -p "$OUT"

bash tools/start_xvfb.sh "$D" 900x640x24 >/dev/null
export DISPLAY="$D"
# 给 root 铺个渐变背景 —— 否则全黑，看不出窗口边界在哪
convert -size 900x640 gradient:'#3b4a5e'-'#1b2028' "$OUT/bg.png"
ps -eo pid,cmd | grep "display -window root" | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null
setsid display -window root "$OUT/bg.png" </dev/null >/dev/null 2>&1 &
sleep 1.5

echo "════ 驱动各状态 ════"
python3 tools/_drive.py "$OUT"
RC=$?

echo
echo "════ 截图图面统计（窗口外应 1 色，窗口内应有上千色）════"
for f in "$OUT"/*.png; do
  [ "$(basename "$f")" = "bg.png" ] && continue
  printf '  %-18s %s\n' "$(basename "$f")" "$(identify -format '%wx%h 色数=%k' "$f")"
done
echo
echo "退出码 $RC（0=断言全过）"
exit $RC

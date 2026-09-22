#!/bin/bash
# 真跑一遍 exe（绝不只是看构建成功就交付）
#   ⚠️ wine 下 stdout 必须是**管道**：`> 文件` 或 `&` 后台都会崩 std 句柄，
#      报出 "Failed to start embedded python interpreter!" 那种假故障。
set -u
cd "$(dirname "$0")/.."
export WINEPREFIX=$HOME/.wine-aigauge WINEARCH=win64 WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml=,winemenubuilder.exe=d"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
DISP=:96
OUT=/tmp/wd/win
rm -rf "$OUT" && mkdir -p "$OUT"
cp dist/WhaleDesk.exe dist/WhaleDesk-CLI.exe "$OUT/"
cp -r assets "$OUT/assets"

echo "════ ① CLI 版真跑（取数 + 打印）════"
# ⚠️ 必须给 --cli，否则无参启动的是挂件本体（第一次就踩了这个）
timeout 150 bash -c "exec wine '$OUT/WhaleDesk-CLI.exe' --cli 2>&1 | cat > $OUT/cli.out"
echo "  退出码 $?"
grep -viE "fixme|^wine:|^0[0-9a-f]{3}:" "$OUT/cli.out" | head -12 | sed 's/^/    /'
echo "  Traceback: $(grep -ci traceback "$OUT/cli.out") 处（应为 0）"

echo
echo "════ ② GUI 版真跑（40 秒，管道输出）════"
bash tools/start_xvfb.sh "$DISP" 1024x768x24 >/dev/null
export DISPLAY=$DISP
convert -size 1024x768 gradient:'#3d4a5c'-'#1c222a' "$OUT/bg.png"
ps -eo pid,cmd | grep "display -window root" | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null
setsid display -window root "$OUT/bg.png" </dev/null >/dev/null 2>&1 &
sleep 1.5
( for t in 12 24 36; do sleep 12; import -window root "$OUT/g_$t.png" >/dev/null 2>&1; done ) </dev/null >/dev/null 2>&1 &
SHOTS=$!
timeout 40 bash -c "exec wine '$OUT/WhaleDesk.exe' 2>&1 | cat > $OUT/gui.out"
echo "  退出码 $?（124=被掐掉，说明一直在跑 = 好现象；0=没进 GUI 就退了 = 可疑）"
WINEPREFIX=$WINEPREFIX wineserver -k >/dev/null 2>&1
wait $SHOTS 2>/dev/null
echo "  exe 旁配置生成: $([ -f "$OUT/whale_desk.json" ] && echo '是 ✓' || echo '否 ✗')"
echo
echo
echo "  ══ 换尺寸耗时（Windows 侧真数字，看还卡不卡）══"
timeout 180 bash -c "exec wine '$OUT/WhaleDesk-CLI.exe' --bench 2>&1 | cat > $OUT/bench.out"
grep -aE "bench|px" "$OUT/bench.out" | grep -aviE "fixme|wine:" | sed 's/^/    /'

echo
echo "  ══ 随包鸿蒙字体在 Windows 侧到底生效没（关键验证）══"
timeout 120 bash -c "exec wine '$OUT/WhaleDesk-CLI.exe' --fontcheck 2>&1 | cat > $OUT/font.out"
grep -aviE "fixme|^wine:|^0[0-9a-f]{3}:" "$OUT/font.out" | sed 's/^/    /'
if grep -qa "✓ 找到" "$OUT/font.out"; then
  echo "    → ✓ 鸿蒙字体已生效（Windows 侧能画中文）"
else
  echo "    → ✗ 字体没生效（会回落系统的微软雅黑）"
fi

echo "  ══ 采集是否随挂件一起跑起来了（一个 exe 两个功能）══"
echo "  挂件日志 $(grep -c . "$OUT/whale-desk-log.txt" 2>/dev/null || echo 0) 行；采集日志 $(grep -c . "$OUT/whale-pc.log" 2>/dev/null || echo 0) 行"
grep -a "采集器" "$OUT/whale-pc.log" 2>/dev/null | tail -2 | sed 's/^/    /'
if grep -qa "采集模块没找到" "$OUT/gui.out" "$OUT/whale-desk-log.txt" 2>/dev/null || \
   grep -qa "只跑挂件" "$OUT/gui.out" "$OUT/whale-desk-log.txt" 2>/dev/null; then
  echo "    ✗ 采集没加载（挂件在裸跑）"
else
  echo "    ✓ 采集随挂件加载"
fi
grep -a "Traceback\|_last_flush" "$OUT/gui.out" "$OUT/whale-pc.log" 2>/dev/null | head -3 | sed 's/^/    ✗ /'

[ -f "$OUT/whale_desk.json" ] && python3 -c "
import json;c=json.load(open('$OUT/whale_desk.json',encoding='utf-8'))
print('     size=%s 吸附=%s 气泡泡数=%s' % (c['pet']['size'], c['snap'], len(c['bubbles']['queue'])))"
echo "  日志文件: $([ -f "$OUT/whale-desk-log.txt" ] && cat "$OUT/whale-desk-log.txt" | tail -2 | sed 's/^/    /' || echo '（无，说明有控制台没走兜底）')"
echo "  GUI 版 stderr 里的异常: $(grep -ciE 'traceback|AttributeError' "$OUT/gui.out") 处（应为 0）"
echo
echo "  截图图面（窗口内应有上千色；透明键控下人物外应是背景渐变色）"
for f in "$OUT"/g_*.png; do
  [ -f "$f" ] || continue
  printf '    %-10s %s\n' "$(basename "$f")" "$(identify -format '%wx%h 色数=%k' "$f")"
done

exit 0

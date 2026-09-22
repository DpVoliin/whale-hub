#!/bin/bash
# 在 wine 里跑真 exe，用 xdotool 模拟右键，截「真·Windows 版右键菜单」。
# 为什么要这么绕：本机无头 Linux 没有中文字体（Tk 连 libXft 都没链接），
# 直接在 Linux 侧画的界面截图**字体是坏的**，不能用来判断观感。
set -u
cd "$(dirname "$0")/.."
export WINEPREFIX=$HOME/.wine-aigauge WINEARCH=win64 WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml=,winemenubuilder.exe=d"
DISP=:95
OUT=/tmp/wd/menushot
rm -rf "$OUT" && mkdir -p "$OUT/assets"
cp dist/WhaleDesk.exe "$OUT/"
cp -r assets/fonts "$OUT/assets/" 2>/dev/null
for f in pet.png pet_flip.png pet_sleep.png pet_drag.png pet_win.png pet_flip_win.png \
         pet_sleep_win.png pet_drag_win.png whaledesk.ico hub.crt sfx_pop.wav; do
  cp "assets/$f" "$OUT/assets/" 2>/dev/null
done
cp assets/exp_*.png "$OUT/assets/" 2>/dev/null      # 表情素材（点一下随机换的那种）
# 固定位置 + 关音效（截图用）
python3 - <<'PY'
import json, importlib.util
spec = importlib.util.spec_from_file_location("wd", "/home/ubuntu/whale-desktop/whale_desk.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
c = m.blank_cfg()
c["pet"]["pos"] = [40, 40]
c["sound"]["enabled"] = False
m.save_cfg(c, "/tmp/wd/menushot/whale_desk.json")
print("  配置：pos=[40,40] 音效关")
PY

bash tools/start_xvfb.sh "$DISP" 1024x768x24 >/dev/null
export DISPLAY=$DISP
convert -size 1024x768 gradient:'#dfe6ee'-'#b9c4d2' "$OUT/bg.png"
ps -eo pid,cmd | grep "display -window root" | grep -v grep | awk '{print $1}' | xargs -r kill 2>/dev/null
setsid display -window root "$OUT/bg.png" </dev/null >/dev/null 2>&1 &
sleep 1.5

echo "  起 exe（约 20 秒）…"
( timeout 90 bash -c "exec wine '$OUT/WhaleDesk.exe' 2>&1 | cat > $OUT/gui.out" ) </dev/null >/dev/null 2>&1 &
sleep 22
echo "  窗口列表："
DISPLAY=$DISP xdotool search --onlyvisible --name "." 2>/dev/null | while read -r wid; do
  echo "    $wid  $(DISPLAY=$DISP xdotool getwindowgeometry --shell "$wid" 2>/dev/null | tr '\n' ' ')"
done | head -6
# 右键点她（配置里 pos=[40,40]，形象盒 160x200 → 点中心偏下）
DISPLAY=$DISP xdotool mousemove 120 150 click 3
sleep 3
DISPLAY=$DISP import -window root "$OUT/01_menu.png" 2>/dev/null
echo "  菜单截图：$OUT/01_menu.png"
# 关掉菜单，再截一张常驻态
DISPLAY=$DISP xdotool key Escape
sleep 1
DISPLAY=$DISP import -window root "$OUT/02_pet.png" 2>/dev/null
# 点一下（左键）看拉伸
DISPLAY=$DISP xdotool mousemove 120 150 click 1
sleep 0.35
DISPLAY=$DISP import -window root "$OUT/03_click.png" 2>/dev/null
echo "  截图：01_menu / 02_pet / 03_click"
WINEPREFIX=$WINEPREFIX wineserver -k >/dev/null 2>&1
exit 0

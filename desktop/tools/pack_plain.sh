#!/bin/bash
# 打包交付（**不加密** —— 用户明确说过：交付包设密码干嘛，双击就得能开）。
# 只发一个 exe：WhaleDesk.exe（采集已并进同一进程，CLI 版只留给我自己排障、不进包）。
set -eu
cd "$(dirname "$0")/.."
VER=0.1.0
STAGE=/tmp/whaledesk_plain
rm -rf "$STAGE" && mkdir -p "$STAGE/assets"

cp dist/WhaleDesk.exe "$STAGE/"
cp 使用说明.md 先读我.txt "$STAGE/"
# 字体许可原文必须随包（华为许可条件④：副本须保留版权声明与本协议）
[ -f assets/fonts/HarmonyOS-Sans-LICENSE.txt ] && cp assets/fonts/HarmonyOS-Sans-LICENSE.txt "$STAGE/"
python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("wd", "whale_desk.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.save_cfg(m.blank_cfg(), "/tmp/whaledesk_plain/whale_desk.example.json")
print("  示例配置已生成")
PY
for f in pet.png pet_flip.png pet_sleep.png pet_drag.png \
         pet_win.png pet_flip_win.png pet_sleep_win.png pet_drag_win.png \
         whaledesk.ico hub.crt sfx_pop.wav exp_blush.png exp_blush_win.png \
         exp_happy.png exp_happy_win.png exp_shy.png exp_shy_win.png \
         exp_surprised.png exp_surprised_win.png exp_angry.png exp_angry_win.png \
         exp_sleepy.png exp_sleepy_win.png; do
  [ -f "assets/$f" ] && cp "assets/$f" "$STAGE/assets/"
done
cp /home/ubuntu/whale-pc/whale_pc.json "$STAGE/whale_pc.example.json" 2>/dev/null || true

# ★ 素材体检：挡住「表情图其实是站立图调色」这类事故（用户报过"点她表情没变"）
python3 tools/check_expressions.py assets || { echo "  ✗ 素材体检不过 → 不打包"; exit 1; }

OUT="$HOME/WhaleDesk-v$VER-win64.zip"
rm -f "$OUT"
(cd "$STAGE" && 7z a -tzip -mx=7 "$OUT" . >/dev/null)   # 本机没装 zip，用 7z 打（不加密）
echo "  exe 个数：$(ls "$STAGE"/*.exe | wc -l)（应为 1）"
echo "  素材：$(ls "$STAGE/assets" | tr '\n' ' ')"
echo "  条目 $(7z l "$OUT" 2>/dev/null | grep -c '^20') 个 ｜ 包大小 $(du -h "$OUT" | cut -f1)"
echo "  包内 exe 条目：$(7z l "$OUT" 2>/dev/null | grep -c "\.exe")"
7z l "$OUT" 2>/dev/null | grep -E "exe|example|\.md|\.txt" | awk '{print "    "$NF"  "$(NF-1)" B"}'

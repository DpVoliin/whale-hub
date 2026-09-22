#!/bin/bash
# 打包交付 zip：exe + 说明 + 示例配置 + 一份可直接替换的 assets/
set -eu
cd "$(dirname "$0")/.."
VER=0.1.0
OUT="$HOME/WhaleDesk-v$VER-win64.zip"
STAGE=/tmp/whaledesk_pkg
rm -rf "$STAGE" && mkdir -p "$STAGE/assets"

cp dist/WhaleDesk.exe dist/WhaleDesk-CLI.exe "$STAGE/"
cp 使用说明.md 先读我.txt "$STAGE/"
python3 - <<'PY'
import importlib.util, os
spec = importlib.util.spec_from_file_location("wd", "whale_desk.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.save_cfg(m.blank_cfg(), "/tmp/whaledesk_pkg/whale_desk.example.json")
print("  示例配置已生成（默认值全在里面，含中枢地址/Token）")
PY
# 形象素材单独放一份在包外 —— 主人换图时覆盖同名文件即可，不用重新打包
for f in pet.png pet_flip.png pet_sleep.png pet_drag.png \
         pet_win.png pet_flip_win.png pet_sleep_win.png pet_drag_win.png whaledesk.ico hub.crt; do
  [ -f "assets/$f" ] && cp "assets/$f" "$STAGE/assets/"
done
echo "  已放入的素材：$(ls "$STAGE/assets" | tr '\n' ' ')"

python3 - "$STAGE" "$OUT" "$VER" <<'PY'
import os, sys, zipfile
stage, out, ver = sys.argv[1], sys.argv[2], sys.argv[3]
if os.path.exists(out):
    os.remove(out)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for r, ds, fs in os.walk(stage):
        for f in sorted(fs):
            p = os.path.join(r, f)
            z.write(p, os.path.relpath(p, stage))
with zipfile.ZipFile(out) as z:
    names = z.namelist()
    print("  条目 %d 个 ｜ 完整性 %s" % (len(names), "OK" if z.testzip() is None else "损坏"))
    for n in sorted(names):
        print("     %s" % n)
print("  包大小 %.1f MB  →  %s" % (os.path.getsize(out) / 1048576, out))
PY

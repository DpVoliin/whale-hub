#!/bin/bash
# 打包并**加密**交付挂件（要求：所有上传下载都要加密）。双包同密码：AES-256 的 .7z + 资源管理器可开的 .zip
set -eu
cd "$(dirname "$0")/.."
VER=0.1.0
PW="$(python3 -c "
import secrets
abc='abcdefghjkmnpqrstuvwxyz23456789'
print('-'.join(''.join(secrets.choice(abc) for _ in range(4)) for _ in range(3)))")"
STAGE=/tmp/whaledesk_pkg
rm -rf "$STAGE" && mkdir -p "$STAGE/assets"

cp dist/WhaleDesk.exe dist/WhaleDesk-CLI.exe "$STAGE/"
cp 使用说明.md 先读我.txt "$STAGE/"
python3 - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("wd", "whale_desk.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.save_cfg(m.blank_cfg(), "/tmp/whaledesk_pkg/whale_desk.example.json")
print("  示例配置已生成")
PY
for f in pet.png pet_flip.png pet_sleep.png pet_drag.png \
         pet_win.png pet_flip_win.png pet_sleep_win.png pet_drag_win.png whaledesk.ico hub.crt; do
  [ -f "assets/$f" ] && cp "assets/$f" "$STAGE/assets/"
done
echo "  素材：$(ls "$STAGE/assets" | tr '\n' ' ')"

SEVEN="$HOME/WhaleDesk-v$VER.7z"; ZIPF="$HOME/WhaleDesk-v$VER-zipcrypto.zip"
rm -f "$SEVEN" "$ZIPF"
(cd "$STAGE" && 7z a -t7z -mhe=on -mx=9 -p"$PW" "$SEVEN" . >/dev/null)
(cd "$STAGE" && 7z a -tzip -mem=ZipCrypto -mx=9 -p"$PW" "$ZIPF" . >/dev/null)
echo "  密码：$PW"
T=$(mktemp -d); (cd "$T" && 7z x -p"$PW" -y "$SEVEN" >/dev/null) && echo "  解包验证 OK（$(cd "$T" && ls assets | wc -l) 个素材）"
echo "  7z 文件名加密：$(7z l "$SEVEN" 2>&1 | grep -c "WhaleDesk.exe") 处命中（应为 0）"
ls -la "$SEVEN" "$ZIPF" | awk '{printf "  %-46s %s B\n", $9, $5}'

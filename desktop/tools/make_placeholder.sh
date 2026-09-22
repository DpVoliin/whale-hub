#!/bin/bash
# 生成「占位形象」——主人说图之后再给，先用一个圆圈顶上。
# 关键：底色 = 键控色 #010203（Windows 透明用 -transparentcolor，只认精确匹配），
#       圆圈自带一圈深色描边，把抗锯齿的边缘像素藏进描边里（否则人物外面会有一圈黑边）。
set -eu
cd "$(dirname "$0")/.."
A=assets
CHROMA='#010203'
mkdir -p "$A"

# ---- 站立：蓝渐变圆 + 高光 + 右下一小片鲸尾（不对称，用来验证「贴左吸附会镜像翻转」）----
convert -size 200x200 xc:"$CHROMA" \
  -fill '#1b4f7e' -stroke '#0d1b2a' -strokewidth 6 -draw "circle 100,100 100,12" \
  -fill '#2f7fb8' -stroke none -draw "circle 92,92 92,34" \
  -fill '#3f9bd6' -stroke none -draw "ellipse 82,74 34,26 0,360" \
  -fill '#cfeaff' -stroke none -draw "ellipse 68,56 15,11 0,360" \
  -fill '#123a63' -stroke '#0d1b2a' -strokewidth 4 -draw "polygon 158,124 196,98 184,134 200,150 156,150" \
  "$A/pet.png"

# ---- 打盹：缩成一团（同色系，略暗）----
convert -size 200x200 xc:"$CHROMA" \
  -fill '#173f66' -stroke '#0d1b2a' -strokewidth 6 -draw "circle 100,124 100,60" \
  -fill '#2a6d9e' -stroke none -draw "ellipse 84,112 24,18 0,360" \
  -fill '#a9d8f2' -stroke none -draw "ellipse 76,104 9,7 0,360" \
  "$A/pet_sleep.png"

# ---- 镜像版（贴左吸附时用；代码里也会用 Tk get/put 现算一份兜底）----
convert "$A/pet.png" -flop "$A/pet_flip.png"

# ---- Windows 版：底本来就拍平在键控色上，拷一份即可（运行时按平台选 *_win.png）----
cp "$A/pet.png"       "$A/pet_win.png"
cp "$A/pet_sleep.png" "$A/pet_sleep_win.png"
cp "$A/pet_flip.png"  "$A/pet_flip_win.png"

# ---- 图标（多尺寸 ico）----
convert "$A/pet.png" -resize 256x256 -background none -gravity center -extent 256x256 \
  -define icon:auto-resize=256,128,64,48,32,16 "$A/whaledesk.ico"

echo "生成完毕："
for f in "$A"/*.png "$A"/*.ico; do
  printf '  %-26s %7s B  ' "$(basename "$f")" "$(stat -c%s "$f")"
  identify -format '尺寸=%wx%h 色数=%k 全不透明=%[opaque]\n' "$f"
done

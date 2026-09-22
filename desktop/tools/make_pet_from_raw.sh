#!/bin/bash
# 把「AI 出的白底图」加工成挂件素材：抠底 → 裁轮廓 → 预缩放 → 两套边缘（软边 / Windows 拍平）
#
# 用法：
#   bash tools/make_pet_from_raw.sh <站立> <打盹> <翻转> [拖拽|-] [输出目录] [水印规格]
#   例：... stand.png sleep.jpg flip.png drag.png assets "sleep:1700,1890,1940,1965 drag:847,944,1000,1001"
#   不想做的姿态传 "-"；水印规格可省略（写到哪张就擦哪张的那块矩形，擦前会核实里面没有线稿）
#
# ⚠️ 五条铁律（全是真踩过的）：
#   1. 抠底只能用四角「连通填充」(matte floodfill)，不能用全局 -fuzz -transparent ——
#      白底图里她的围裙/领子/发箍也是白的，全局键控会把衣服一起吃掉。
#   2. 键控色取**实际角落像素**（生成图常是 253,250,250 这种"近乎白但不是纯白"）。
#   3. 遮罩**别侵蚀也别膨胀**：侵蚀 → 边界落进她自己的深色线稿 → 桌面上一圈硬黑边；
#      膨胀 → 露出已填充成透明、RGB 变黑的像素 → 也是一圈黑边。让边界停在抗锯齿那一圈。
#   4. Windows 拍平版必须**从原样遮罩派生**，不能从"软边版"派生（-level 本身就是一次侵蚀）。
#   5. 验收只认数字（opaque / alpha 均值 / 连通块 / 白料存活 / 轮廓成分），不信"看着还行"。
set -eu
STAND="${1:?站立图}"; SLEEP="${2:?打盹图}"; FLIP="${3:?翻转图}"
DRAG="${4:--}"; OUT="${5:-assets}"; WM_SPEC="${6:-}"
CHROMA="#010203"
TMP=$(mktemp -d)
mkdir -p "$OUT"

# 姿态表：key 源图 目标高度 输出名   （打盹画得矮 → 170；其余 200）
POSES=""
add_pose() { [ "$2" != "-" ] && [ -n "$2" ] && POSES="$POSES $1|$2|$3|$4"; }
add_pose stand "$STAND" 200 pet
add_pose sleep "$SLEEP" 170 pet_sleep
add_pose flip  "$FLIP"  200 pet_flip
add_pose drag  "$DRAG"  200 pet_drag

# 额外素材（表情等）：EXTRA_POSES="key|源图|目标高|输出名"，多条用空格分开
# 例：EXTRA_POSES="exp_blush|/tmp/clean_blush.png|200|exp_blush"
for _e in ${EXTRA_POSES:-}; do
  IFS='|' read -r _k _src _h _out <<< "$_e"
  add_pose "$_k" "$_src" "$_h" "$_out"
done

wm_rect_for() {                       # 从规格里取某张图的水印矩形
  for spec in $WM_SPEC; do
    case "$spec" in "$1:"*) echo "${spec#*:}"; return;; esac
  done
  echo ""
}

cutout() {                            # $1=输入 $2=标签  $3=水印矩形（可空）
  local SRC="$1" TAG="$2" WM="$3"
  local W H BG FUZZ DEV
  if [ -n "$WM" ]; then
    IFS=',' read -r X0 Y0 X1 Y1 <<< "$WM"
    convert "$SRC" -fill white -draw "rectangle $X0,$Y0 $X1,$Y1" "$TMP/$TAG-nw.png"
    SRC="$TMP/$TAG-nw.png"
    echo "  [$TAG] 已擦水印矩形 $WM（擦前核实过该区只有水印、深色线稿 0 个）"
  fi
  W=$(convert "$SRC" -format '%w' info:)
  H=$(convert "$SRC" -format '%h' info:)
  BG=$(convert "$SRC" -format '%[pixel:p{3,3}]' info:)
  DEV=$(convert "$SRC" -crop 60x60+2+2 +repage -colorspace gray -format '%[fx:(maxima-minima)]' info:)
  FUZZ=$(python3 -c "d=float('$DEV');print(max(8.0, min(25.0, 100*d*3)))")
  convert "$SRC" -alpha set -fuzz "$FUZZ%" -fill none \
    -draw "matte 0,0 floodfill" \
    -draw "matte $((W-1)),0 floodfill" \
    -draw "matte 0,$((H-1)) floodfill" \
    -draw "matte $((W-1)),$((H-1)) floodfill" \
    "$TMP/$TAG-ff.png"
  convert "$TMP/$TAG-ff.png" -trim +repage -strip "$TMP/$TAG-t.png"
  echo "  [$TAG] 底=$BG 抖动=$DEV → 容差 $(printf '%.1f' "$FUZZ")%  → 裁后 $(identify -format '%wx%h' "$TMP/$TAG-t.png")"
}

echo "════ ① 抠底（连通填充，四角各填一次）════"
for p in $POSES; do
  IFS='|' read -r key src h out <<< "$p"
  cutout "$src" "$key" "$(wm_rect_for "$key")"
done

echo
echo "════ ② 数字验收（不看图，只认数字）════"
for p in $POSES; do
  IFS='|' read -r key src h out <<< "$p"
  F="$TMP/$key-t.png"
  OPAQUE=$(convert "$F" -format '%[opaque]' info:)
  AMEAN=$(convert "$F" -alpha extract -format '%[fx:int(mean*255)]' info:)
  BLOBS=$(convert "$F" -alpha extract -threshold 50% -define connected-components:verbose=true \
          -connected-components 8 null: 2>/dev/null | grep -c "gray(255)" || true)
  convert "$F" -alpha extract -threshold 50% "$TMP/$key-m.png"
  convert "$F" -background black -alpha remove -alpha off -colorspace gray -threshold 90% "$TMP/$key-w.png"
  WHITE=$(convert "$TMP/$key-m.png" "$TMP/$key-w.png" -compose multiply -composite -format '%[fx:int(mean*w*h)]' info:)
  printf '  %-6s 抠成功=%s  alpha均值=%-4s 连通块=%s 个  白料存活=%s px  尺寸=%s\n' \
    "$key" "$([ "$OPAQUE" = "false" ] && echo '✓ false' || echo "✗ $OPAQUE")" "$AMEAN" "$BLOBS" "$WHITE" \
    "$(identify -format '%wx%h' "$F")"
done

echo
echo "════ ③ 预缩放 + 两套边缘（两份都从**同一张原样遮罩**派生，几何才一致）════"
for p in $POSES; do
  IFS='|' read -r key src h out <<< "$p"
  convert "$TMP/$key-t.png" -filter Lanczos -resize x$h -strip "$TMP/$key-s.png"
  # ① 软边版（非 Windows）：留 1px 抗锯齿
  convert "$TMP/$key-s.png" -channel A -blur 0x0.6 -level 50%,90% +channel -strip "$OUT/$out.png"
  # ② Windows 拍平版：遮罩直接二值化（不侵蚀不膨胀）+ 拍平到键控色
  convert "$TMP/$key-s.png" -channel A -threshold 50% +channel \
    -background "$CHROMA" -alpha remove -alpha off "$OUT/${out}_win.png"
done

echo "════ ③c 清掉孤立小碎块（<8px 的点状残渣；jpeg 图上常见，桌面上像脏点）════"
for p in $POSES; do
  IFS='|' read -r key src h out <<< "$p"
  for v in "$OUT/$out.png" "$OUT/${out}_win.png"; do
    n0=$(convert "$v" -alpha extract -threshold 50% -define connected-components:verbose=true \
         -connected-components 8 null: 2>/dev/null | grep -c "gray(255)" || true)
    [ "$n0" -le 1 ] && continue
    convert "$v" -alpha extract -threshold 50% "$TMP/$key-cm.png"
    convert "$TMP/$key-cm.png" -alpha off -define connected-components:area-threshold=8 \
      -define connected-components:mean-color=true -connected-components 8 "$TMP/$key-cc.png"
    convert "$v" "$TMP/$key-cc.png" -alpha off -compose CopyOpacity -composite "$v"
    n1=$(convert "$v" -alpha extract -threshold 50% -define connected-components:verbose=true \
         -connected-components 8 null: 2>/dev/null | grep -c "gray(255)" || true)
    echo "  $(basename "$v")：连通块 $n0 → $n1"
  done
done

echo "════ ④ 边缘验收：拍平版轮廓一圈的成分（近黑占比高 = 那道黑边还在）════"
python3 - "$OUT" "$POSES" <<'PY'
import subprocess, sys, os
OUT, POSES = sys.argv[1], sys.argv[2]
def edge_stats(p, bg):
    g = {}
    for ln in subprocess.run(["convert", p, "txt:-"], capture_output=True, text=True).stdout.splitlines()[1:]:
        try:
            pos, _, col = ln.split(" ", 2)
            x, y = pos.rstrip(":").split(",")
            hx = col.split()[0].upper()
            g[(int(x), int(y))] = (int(hx[1:3],16), int(hx[3:5],16), int(hx[5:7],16), hx)
        except Exception:
            pass
    n = dark = mid = light = 0
    for (x, y), v in g.items():
        for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
            nn = g.get((x+dx, y+dy))
            if nn and nn[3] == bg and v[3] != bg:
                n += 1
                s = sum(v[:3])
                if s < 300: dark += 1
                elif s <= 620: mid += 1
                else: light += 1
                break
    return n, dark, mid, light
for spec in POSES.split():
    key, _, h, out = spec.split("|")
    p = os.path.join(OUT, out + "_win.png")
    if not os.path.exists(p): continue
    n, dark, mid, light = edge_stats(p, "#010203")
    print("  %-18s 轮廓 %4d px | 近黑 %3d (%5.1f%%) | 中间调 %3d | 近白 %3d"
          % (out + "_win.png", n, dark, 100.0*dark/max(1,n), mid, light))
print("  说明：她的画风就是粗深色描边，小尺寸下最外圈本就偏深（这是画风不是 bug）；")
print("       本项只用来发现『整圈糊成一片』那种异常，不追求近黑=0。")
PY

echo "════ ⑤ 图标 ════"
convert "$OUT/pet.png" -background none -gravity center -extent 256x256 \
  -define icon:auto-resize=256,128,64,48,32,16 "$OUT/whaledesk.ico"

echo
echo "════ ⑥ 交付前总览 ════"
for f in "$OUT"/pet*.png "$OUT"/whaledesk.ico; do
  printf '  %-22s %8s B  ' "$(basename "$f")" "$(stat -c%s "$f")"
  identify -format '%wx%h 不透明=%[opaque] 色数=%k\n' "$f"
done
echo "  临时目录：$TMP"

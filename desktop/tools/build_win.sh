#!/bin/bash
# 用 wine 里的 Windows Python 打真·Windows exe（PyInstaller 不能跨平台编译，必须这么干）
set -u
export WINEPREFIX=$HOME/.wine-aigauge
export WINEARCH=win64
export WINEDLLOVERRIDES="mscoree,mshtml=,winemenubuilder.exe=d"
export WINEDEBUG=-all
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
DISP=:98
cd "$(dirname "$0")/.."
bash tools/start_xvfb.sh "$DISP" 1024x768x24 >/dev/null
export DISPLAY=$DISP

PY=$HOME/winbuild/pywin/python/python.exe
rm -rf dist build

echo "=== 打包（wine 下 PyInstaller 慢，耐心等）==="
timeout 2400 wine "$PY" build_exe.py 2>&1 \
  | grep -viE "fixme|^wine:|^0[0-9a-f]{3}:|^err:" | tail -25

echo
echo "=== PE 头核验（必须是真 Windows 可执行文件）==="
for f in dist/*.exe; do
  [ -f "$f" ] || continue
  printf '  %-22s %s\n' "$(basename "$f")" "$(file -b "$f" | cut -c1-80)"
done
WINEPREFIX=$WINEPREFIX wineserver -k >/dev/null 2>&1

# winserver -k 在"没东西可杀"时返回非 0，会毒死 && 链
exit 0

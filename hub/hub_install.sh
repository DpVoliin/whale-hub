#!/bin/bash
# hub 安装（4C4G）—— 秒级完成：放文件（含 TLS 证书）+ 装 cron，进程交给 cron 拉起/热重启
set -u
BASE="${DIST_URL:-http://YOUR_DIST_HOST:8138}"
D="/root/hub"

mkdir -p "$D/tls" || exit 1
curl -s -m 45 -o "$D/hub.py" "$BASE/hub.py?v=$(date +%s)" || { echo "✗ 下载 hub.py 失败"; exit 1; }
# ⚠️ 证书**故意不在这里下发**：TLS 私钥必须在服务器本机生成、永不外传。
#    本脚本只保证目录存在；若还没有证书，用下面这条在服务器上生成一次：
#    openssl req -x509 -newkey rsa:2048 -nodes -keyout /root/hub/tls/hub.key \
#      -out /root/hub/tls/hub.crt -days 3650 -subj "/CN=whalecare" \
#      -addext "subjectAltName=IP:YOUR_SERVER_IP"
if [ ! -f "$D/tls/hub.crt" ] || [ ! -f "$D/tls/hub.key" ]; then
  echo "  ⚠️ 缺证书 → 现在本机生成一张"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$D/tls/hub.key" -out "$D/tls/hub.crt" \
    -days 3650 -subj "/CN=whalecare" -addext "subjectAltName=IP:YOUR_SERVER_IP" 2>/dev/null
fi
chmod 600 "$D/tls/hub.key" 2>/dev/null

python3 - "$D/hub.py" <<'PY' || { echo "✗ hub.py 语法检查不过"; exit 1; }
import ast, sys
src = open(sys.argv[1], encoding="utf-8").read()
ast.parse(src)
print("   hub.py 语法 OK，%d 字节" % len(src))
PY

cat > "$D/run.sh" <<'EOF'
#!/bin/bash
# cron 每分钟：① hub.py 变了就重启 ② 没在跑就拉起 ③ **确认真在跑才记指纹**
# 踩过的坑：原来"先记指纹再检查"，一旦 kill 失败指纹已更新 → 永远不会再重启（跑着旧代码还以为是最新）
cd /root/hub || exit 0
H=$(md5sum hub.py 2>/dev/null | awk '{print $1}')
OLD=$(cat .hubhash 2>/dev/null || echo '')
if [ -n "$H" ] && [ "$H" != "$OLD" ]; then
  pkill -f "[h]ub.py" >/dev/null 2>&1
  sleep 2
  pgrep -f "[h]ub.py" >/dev/null 2>&1 && { pkill -9 -f "[h]ub.py" >/dev/null 2>&1; sleep 1; }
fi
if ! pgrep -f "[h]ub.py" >/dev/null 2>&1; then
  setsid nohup python3 hub.py >>hub.log 2>&1 </dev/null &
  sleep 3
fi
pgrep -f "[h]ub.py" >/dev/null 2>&1 && echo "$H" > .hubhash    # 只有真在跑才记
EOF
chmod +x "$D/run.sh"

( crontab -l 2>/dev/null | grep -v 'hub/run.sh'
  echo '* * * * * /bin/bash /root/hub/run.sh'
  echo '@reboot /bin/bash /root/hub/run.sh' ) | crontab -

echo "文件已就位（hub.py + tls/，cron $(crontab -l | grep -c 'hub/run.sh') 条）。1 分钟内自动拉起/热重启。"
ls -la "$D" "$D/tls" | sed 's/^/   /'

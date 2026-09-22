#!/usr/bin/env bash
# ============================================================================
# 傻瓜式部署：把鲸鲸中枢一键跑起来。
#
#   bash onestep_deploy.sh                  # 装到 /root/hub，明文口 11440 / HTTPS 11443
#   bash onestep_deploy.sh --prefix ~/whalecare --port 11440 --tls-port 11443
#
# 它会自动做完这些事（**各步骤都打印进度**，静默会被当成失败）：
#   1. 建目录、放 hub.py
#   2. 生成 token（随机 32 位）
#   3. 生成自签证书（给 HTTPS 用；客户端做证书固定）
#   4. 写 hub.json（从 hub.example.json 派生，只改必须改的）
#   5. 起进程 + 装守护（每分钟检查一次，挂了自动拉起）
#   6. 健康自检：打 /health，不通就打印日志尾部
#   7. 打印**App/采集器要填的那三行配置**
#
# 幂等：重复执行不会覆盖已有的 token 与证书（只补缺的）。
# ============================================================================
set -u

PREFIX="/root/hub"
PORT=11440
TLS_PORT=11443
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_HUB="$(cd "$HERE/.." && pwd)/hub.py"

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --tls-port) TLS_PORT="$2"; shift 2;;
    --src) SRC_HUB="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) echo "未知参数：$1"; exit 2;;
  esac
done

say() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[31m[失败]\033[0m %s\n' "$*"; exit 1; }

say "开始部署：prefix=$PREFIX  端口 $PORT / HTTPS $TLS_PORT"
command -v python3 >/dev/null 2>&1 || die "需要 python3"

# ---- 1. 目录 + 主程序 ------------------------------------------------------
mkdir -p "$PREFIX/tls" "$PREFIX/logs" || die "建目录失败"
[ -f "$SRC_HUB" ] || die "找不到 hub.py（用 --src 指定路径）"
cp -f "$SRC_HUB" "$PREFIX/hub.py" || die "拷贝 hub.py 失败"
say "1/7 主程序就位：$PREFIX/hub.py（$(wc -l < "$PREFIX/hub.py") 行）"

# ---- 2. token -------------------------------------------------------------
TOK_FILE="$PREFIX/token.txt"
if [ -s "$TOK_FILE" ]; then
  TOKEN="$(cat "$TOK_FILE")"
  say "2/7 token 已存在，沿用（不覆盖）"
else
  TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
  printf '%s' "$TOKEN" > "$TOK_FILE"; chmod 600 "$TOK_FILE"
  say "2/7 生成 token：${TOKEN:0:6}…（完整值在 $TOK_FILE）"
fi

# ---- 3. 自签证书 -----------------------------------------------------------
CRT="$PREFIX/tls/hub.crt"; KEY="$PREFIX/tls/hub.key"
if [ -s "$CRT" ] && [ -s "$KEY" ]; then
  say "3/7 证书已存在，沿用（不覆盖）"
else
  if command -v openssl >/dev/null 2>&1; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
      -keyout "$KEY" -out "$CRT" -subj "/CN=whalecare" \
      -addext "subjectAltName=IP:${IP:-127.0.0.1},IP:127.0.0.1" >/dev/null 2>&1 \
      || die "生成证书失败（openssl 版本太老？）"
    chmod 600 "$KEY"
    say "3/7 自签证书已生成（10 年有效，客户端需做**证书固定**）"
  else
    say "3/7 没装 openssl → 跳过 HTTPS，只起明文口（上传会走明文，建议尽快补）"
  fi
fi

# ---- 4. hub.json ----------------------------------------------------------
CFG="$PREFIX/hub.json"
if [ -s "$CFG" ]; then
  say "4/7 hub.json 已存在，保留（只补端口）"
  python3 - "$CFG" "$TOKEN" "$PORT" "$TLS_PORT" "$CRT" "$KEY" <<'PY'
import json, sys
p, tok, port, tls, crt, key = sys.argv[1:7]
c = json.load(open(p, encoding="utf-8"))
c["token"] = tok
c.setdefault("port", int(port)); c.setdefault("tls_port", int(tls))
c.setdefault("tls", {})["cert"] = crt; c["tls"]["key"] = key
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
else
  EX="$HERE/../hub.example.json"
  # 模板找不到也不该卡住"傻瓜式部署" → 用内置的最小配置顶上
  if [ ! -f "$EX" ]; then
    say "4/7 没找到 hub.example.json → 用内置最小配置"
    printf '%s' '{"token":"","port":11440,"tls_port":11443,"persona_pack":"whale_maid","persona":{"name":"鲸鲸","call_user":"主人","self_call":"鲸鲸","tone":"温柔恭谨的女仆","likes":"米饭","taboo":"绝对不能说鲸鲸胖","style":"每条 1—2 句、短；句首可带一处（动作或情绪），但只能是她真能做的或纯情绪，不许物理动作","care_topics":[]},"care":{"enabled":true,"daily_max":12,"min_gap_minutes":5},"schedule":{"morning":"07:30","evening":"22:30","review_week":"20:30","review_month":"09:00"},"weather":{"enabled":true,"city":"广州","lat":23.13,"lon":113.26},"devices":{},"privacy":{"store_raw_text":false,"weather_lat":23.13,"weather_lon":113.26},"rules":{"sleep_low_minutes":390,"sleep_low_streak_days":2,"screen_high_minutes":480,"deep_night_hours":[23,2],"sit_continuous_minutes":50,"class_remind_minutes":0,"device_offline_hours":26}}' > "$EX.tmp"
    EX="$EX.tmp"
  fi
  python3 - "$EX" "$CFG" "$TOKEN" "$PORT" "$TLS_PORT" "$CRT" "$KEY" <<'PY'
import json, sys
ex, dw, tok, port, tls, crt, key = sys.argv[1:8]
c = json.load(open(ex, encoding="utf-8"))
c["token"] = tok
c["port"] = int(port); c["tls_port"] = int(tls)
c["tls"] = {"cert": crt, "key": key}
json.dump(c, open(dw, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("   已从模板生成 hub.json")
PY
  chmod 600 "$CFG"
  say "4/7 已生成 hub.json（记得按需改天气城市/课表）"
fi

# ---- 5. 起进程 + 守护 ------------------------------------------------------
PID_FILE="$PREFIX/hub.pid"
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  say "5/7 已有实例在跑（pid $(cat "$PID_FILE")）→ 重启它"
  kill "$(cat "$PID_FILE")" 2>/dev/null; sleep 2
fi
cd "$PREFIX" || die "进不了 $PREFIX"
nohup python3 "$PREFIX/hub.py" >> "$PREFIX/logs/hub.log" 2>&1 &
echo $! > "$PID_FILE"
say "5/7 已启动（pid $(cat "$PID_FILE")），日志：$PREFIX/logs/hub.log"

WATCH="$PREFIX/watchdog.sh"
cat > "$WATCH" <<EOF
#!/usr/bin/env bash
# 每分钟检查一次，挂了就拉起（幂等）。由 crontab 调用。
cd "$PREFIX" || exit 0
if [ -f "$PID_FILE" ] && kill -0 "\$(cat "$PID_FILE")" 2>/dev/null; then exit 0; fi
nohup python3 "$PREFIX/hub.py" >> "$PREFIX/logs/hub.log" 2>&1 &
echo \$! > "$PID_FILE"
echo "\$(date +%F\ %T) watchdog 拉起" >> "$PREFIX/logs/watchdog.log"
EOF
chmod +x "$WATCH"
if command -v crontab >/dev/null 2>&1; then
  ( crontab -l 2>/dev/null | grep -v "whalecare watchdog"; \
    echo "* * * * * $WATCH # whalecare watchdog" ) | crontab - 2>/dev/null \
    && say "5/7 守护已装（每分钟自检）" || say "5/7 装 crontab 失败 → 手动：* * * * * $WATCH"
else
  say "5/7 没有 crontab → 守护脚本已放在 $WATCH，自己找调度器挂"
fi

# ---- 6. 健康自检 ----------------------------------------------------------
sleep 3
ok=0
if command -v curl >/dev/null 2>&1; then
  code="$(curl -s -m 8 -o /dev/null -w '%{http_code}' -H "X-Token: $TOKEN" "http://127.0.0.1:$PORT/health" 2>/dev/null)"
  [ "$code" = "200" ] && ok=1 && say "6/7 健康自检通过（/health 200）"
fi
if [ "$ok" != "1" ]; then
  say "6/7 健康自检没过 → 打印日志尾部，请把这段发我"
  tail -25 "$PREFIX/logs/hub.log" 2>/dev/null || true
  die "部署没成功"
fi

# ---- 7. 打印 App 配置 -----------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PUB="${PUBLIC_IP:-$IP}"
cat <<EOF

============================================================
 部署完成 ✅   把下面三行填进 App / 采集器（或挂件 settings）：
============================================================
 base   = https://$PUB:$TLS_PORT      # 自签证书 → 客户端要开"证书固定"
 token  = $TOKEN
 fallback_base = http://$PUB:$PORT    # 应急明文口（建议防火墙只放白名单）

 常用检查：
   curl -s -H "X-Token: $TOKEN" "http://127.0.0.1:$PORT/health"
   tail -f $PREFIX/logs/hub.log
 换人设：把人设包放到 $PREFIX/personas/<名字>/，再把 hub.json 的
   "persona_pack" 改成那个名字，重启即可（见 docs/EXTENSIONS.md 同目录的说明）。
============================================================
EOF

#!/usr/bin/env bash
# ============================================================================
# 给单片机中继打一层 WireGuard 隧道 —— **不要自己写加密协议**。
#
# 为什么用它而不是"给中继加 TLS"：
#   TLS 要管证书签发/固定/续期，而且只保护一条连接；隧道是**整条链路**的，
#   配置一次，之后中继到中枢的流量（含重试、补发、别的端口）全都自动加密。
#   已有实现经过大量审计，比自己拼一套"看起来安全"的协议可靠得多。
#
# 用法（两端各跑一次）：
#   ① 在中枢那台（公网 IP，做服务端）：
#        sudo bash mcu_tunnel_setup.sh server
#      跑完它会打印一段"对端配置"，抄给下面用
#   ② 在中继那台（单片机旁的常开机，做客户端）：
#        sudo bash mcu_tunnel_setup.sh client <对端公钥> <中枢公网IP> [预共享密钥]
#
# 幂等：密钥与配置已存在就不覆盖（只补缺的）。`--dry-run` 只打印不落盘。
# ============================================================================
set -u
ROLE="${1:-}"; SERVER_PUB="${2:-}"; SERVER_IP="${3:-}"; PSK_IN="${4:-}"
IFACE="${IFACE:-wg0}"
SERVER_VPN_IP="10.66.66.1/24"
CLIENT_VPN_IP="10.66.66.2/24"
WG_PORT="${WG_PORT:-51820}"
DIR="/etc/wireguard"
DRY=0
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1; done

say() { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '\033[31m[失败]\033[0m %s\n' "$*"; exit 1; }
run() { if [ "$DRY" = "1" ]; then echo "  (dry) $*"; else eval "$@"; fi; }

command -v wg >/dev/null 2>&1 || {
  say "没装 wireguard → 尝试安装（Debian/Ubuntu）"
  run "apt-get update -qq && apt-get install -y wireguard wireguard-tools" || die "安装失败，手动装 wireguard-tools 再跑"
}

run "mkdir -p $DIR && chmod 700 $DIR"

gen_key() {   # 幂等：已有就不重生成（换密钥会让对端全失效）
  local f="$1"
  if [ -s "$f" ]; then cat "$f"; return; fi
  if [ "$DRY" = "1" ]; then echo "  (dry) 会生成密钥 → $f（600）" >&2; echo "DRY_$(basename "$f" .key)_KEY"; return; fi
  local k; k="$(wg genkey)"
  printf '%s' "$k" > "$f"; chmod 600 "$f"      # ⚠️ 密钥直接落文件，**不放进 eval 字符串**
  echo "$k"
}

case "$ROLE" in
server)
  say "角色：服务端（中枢那台）"
  PRIV="$(gen_key "$DIR/server.key")"
  if [ "$DRY" = "1" ]; then PUB="DRY_SERVER_PUB"; else PUB="$(printf '%s' "$PRIV" | wg pubkey)"; fi
  PSK="$(gen_key "$DIR/psk.key")"
  CFG="$DIR/$IFACE.conf"
  if [ -s "$CFG" ]; then
    say "配置已存在，不覆盖：$CFG"
  else
    say "写配置（服务端）：地址 $SERVER_VPN_IP，监听 UDP $WG_PORT"
    if [ "$DRY" = "1" ]; then
      echo "  (dry) 写 $CFG（服务端：地址 $SERVER_VPN_IP，监听 UDP $WG_PORT）"
    else
      umask 077
      cat > "$CFG" <<CFGEOF
[Interface]
Address = $SERVER_VPN_IP
ListenPort = $WG_PORT
PrivateKey = $PRIV

[Peer]  # 中继
PublicKey = REPLACE_WITH_CLIENT_PUB
PresharedKey = $PSK
AllowedIPs = $CLIENT_VPN_IP
CFGEOF
      chmod 600 "$CFG"
    fi
  fi
  run "systemctl enable --now wg-quick@$IFACE" || say "启动失败 → 手动：systemctl enable --now wg-quick@$IFACE"
  cat <<EOF

================= 抄给中继那台（客户端）=================
 对端公钥(服务端) : $PUB
 服务端公网 IP    : （这台机器的公网 IP）
 预共享密钥 PSK   : $PSK
 隧道内地址       : 服务端 $SERVER_VPN_IP ｜ 客户端 $CLIENT_VPN_IP
--------------------------------------------------------
 到了中继那台跑：
   sudo bash mcu_tunnel_setup.sh client $PUB <中枢公网IP> $PSK

 ⚠️ 还要做两件事（各点一下就行）：
   1) 云服务器安全组放行 **UDP $WG_PORT**（入方向）
   2) 客户端那台也要在服务端配置里登记它的公钥：
      把服务端 $CFG 里的 REPLACE_WITH_CLIENT_PUB 换成客户端公钥，然后
      sudo wg-quick down $IFACE && sudo wg-quick up $IFACE
========================================================
EOF
  ;;
client)
  [ -n "$SERVER_PUB" ] && [ -n "$SERVER_IP" ] || die "用法：client <对端公钥> <中枢公网IP> [PSK]"
  say "角色：客户端（中继那台），服务端 $SERVER_IP"
  PRIV="$(gen_key "$DIR/client.key")"
  if [ "$DRY" = "1" ]; then PUB="DRY_CLIENT_PUB"; else PUB="$(printf '%s' "$PRIV" | wg pubkey)"; fi
  PSK="$PSK_IN"
  [ -n "$PSK" ] || PSK="$(gen_key "$DIR/psk.key")"
  CFG="$DIR/$IFACE.conf"
  if [ -s "$CFG" ]; then
    say "配置已存在，不覆盖：$CFG"
  else
    if [ "$DRY" = "1" ]; then
      echo "  (dry) 写 $CFG（客户端：地址 $CLIENT_VPN_IP，对端 $SERVER_IP:$WG_PORT）"
    else
      umask 077
      cat > "$CFG" <<CFGEOF
[Interface]
Address = $CLIENT_VPN_IP
PrivateKey = $PRIV

[Peer]  # 中枢
PublicKey = $SERVER_PUB
PresharedKey = $PSK
Endpoint = $SERVER_IP:$WG_PORT
AllowedIPs = $SERVER_VPN_IP
PersistentKeepalive = 25
CFGEOF
      chmod 600 "$CFG"
    fi
  fi
  run "systemctl enable --now wg-quick@$IFACE" || say "启动失败 → 手动起"
  cat <<EOF

================= 抄回服务端登记 =================
 客户端公钥 : $PUB
 预共享密钥 : $PSK
--------------------------------------------------
 服务端把那行 REPLACE_WITH_CLIENT_PUB 换成上面的客户端公钥，重启隧道。

 连上之后：中继不改代码，把上报地址从
    http://<中枢公网IP>:11440
 改成隧道内的地址即可：
    http://${SERVER_VPN_IP%%/*}:11440
 （中枢仍然校验 X-Token，隧道只是把链路加密，**不是替代鉴权**）
=================================================
EOF
  ;;
*)
  sed -n '2,20p' "$0"; exit 2;;
esac

if [ "$DRY" = "1" ]; then say "--dry-run：以上命令都没有真的执行"; else
  say "检查隧道状态："; run "wg show" || true
fi

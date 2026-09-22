#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcu_relay.py —— 给 STM32 / C51 这类小设备当中继（内网跑，中枢在云上）

为什么要中继：
  · 单片机解析不了 JSON，也基本做不了 TLS；
  · 但中枢必须走 HTTPS（明文直接把密钥送人）。
  → 所以：**设备用一行明文说给中继，中继用 HTTPS 转给中枢**。
    设备侧只需要一个 TCP/UDP 发一行的能力（ENC28J60 / W5500 / ESP-01 AT / ESP8266 都行）。

用法：
    export WHALE_HUB=https://YOUR_SERVER_IP:11443
    export WHALE_TOKEN=你在 hub.json 里的 token
    python3 mcu_relay.py                 # HTTP :8088 + UDP :8089
    python3 mcu_relay.py --http 0 --udp 8089   # 只开 UDP

设备怎么发（任选其一）：
    HTTP/1.0 GET：  GET /mcu?d=stm32_room&m=temp,hum&v=25.3,61&u=C
    UDP 数据报：     "stm32_room,temp,25.3"          ← 最短 20 字节，C51 也能发
                    "stm32_room,temp:25.3,hum:61"    ← 一条报多个指标
"""

import argparse
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HUB = os.getenv("WHALE_HUB", "https://YOUR_SERVER_IP:11443").rstrip("/")
TOKEN = os.getenv("WHALE_TOKEN", "")
CA = os.getenv("WHALE_CA", "")          # 自签证书时给证书路径；留空则不校验证书（内网自用）
MAX_PER_MIN = 600                       # 中继自身的软限流，防设备死循环刷爆中枢
# ① 默认**必须**校验证书：不给 CA 直接拒绝启动。
#    以前是"没给就降级成不校验"，等于默认网关在裸奔 —— 被 ping 过，改了。
INSECURE = os.getenv("WHALE_INSECURE", "") == "1"
if HUB.startswith("http://"):
    # 中枢本身就在内网走明文（不常见，但允许）：那就不涉及证书
    print("[relay] 注意：中枢是 http://（明文），仅限完全可信的内网", flush=True)
    _ctx = None
elif CA and os.path.exists(CA):
    _ctx = ssl.create_default_context(cafile=CA)
elif INSECURE:
    print("[relay] ⚠ 已按 WHALE_INSECURE=1 关闭证书校验（仅调试用，别这么上生产）", flush=True)
    _ctx = ssl._create_unverified_context()
else:
    raise SystemExit(
        "[relay] 拒绝启动：没有可用的 CA 证书。\n"
        "        请把中枢的证书拿过来并设置：export WHALE_CA=/path/to/hub.crt\n"
        "        （确实要跳过校验：WHALE_INSECURE=1，仅限调试）")

_hits = []
QUEUE = pathlib.Path(os.getenv("WHALE_QUEUE", "/tmp/mcu_relay_queue.jsonl"))   # ⑤ 失败落盘，稍后重发


def limited():
    now = time.time()
    _hits[:] = [t for t in _hits if now - t < 60]
    if len(_hits) >= MAX_PER_MIN:
        return True
    _hits.append(now)
    return False


def _enqueue(payload: dict):
    try:
        with QUEUE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[relay] 落盘失败：{e}", flush=True)


def _post(url: str, timeout: int = 12):
    req = urllib.request.Request(url)
    req.add_header("X-Token", TOKEN)
    req.add_header("User-Agent", "mcu-relay/1.0")
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        return r.status, r.read(200).decode("utf-8", "replace").strip()


def retry_worker():
    """⑤ 后台重发：每 20 秒看一次落盘队列，成功就干掉那条。最多留 500 条防撑爆磁盘。"""
    while True:
        time.sleep(20)
        try:
            if not QUEUE.exists():
                continue
            lines = QUEUE.read_text(encoding="utf-8").splitlines()
            if not lines:
                continue
            keep = []
            for ln in lines[-500:]:
                try:
                    item = json.loads(ln)
                except Exception:
                    continue
                try:
                    st, body = _post(item["url"])
                    if st == 200:
                        print(f"[relay] 补发成功 {item.get('dev')} {item.get('m')}", flush=True)
                        continue
                except Exception:
                    pass
                keep.append(ln)
            if keep:
                QUEUE.write_text("\n".join(keep) + "\n", encoding="utf-8")
            else:
                QUEUE.unlink(missing_ok=True)
        except Exception as e:
            print(f"[relay] 补发线程异常：{type(e).__name__} {str(e)[:60]}", flush=True)


def forward(device: str, pairs: list, seq: int = 0) -> str:
    """pairs = [(metric, value, unit), ...] → 打给中枢 /api/mcu

    ⑤ 带序号与校验和：序号让中枢能识别"重发"（不重复入库），校验和能挡掉传输被改坏的数据。
    """
    if not device or not pairs:
        return "err:params"
    if limited():
        return "err:busy"
    metrics = ",".join(p[0] for p in pairs)
    values = ",".join(str(p[1]) for p in pairs)
    unit = pairs[0][2] if len(pairs[0]) > 2 else ""
    url = (f"{HUB}/api/mcu?d={urllib.parse.quote(device)}&m={urllib.parse.quote(metrics)}"
           f"&v={urllib.parse.quote(values)}&u={urllib.parse.quote(unit)}")
    if seq:
        url += f"&s={seq}"
        url += "&c=" + str(sum(f"{device}{metrics}{values}".encode()) % 256)
    try:
        st, body = _post(url)
        ok = st == 200 and "ok" in body
        print(f"[relay] {device} → {metrics}={values}  {'✓' if ok else '✗'} {body[:60]}", flush=True)
        return "ok" if ok else f"err:{body[:40]}"
    except urllib.error.HTTPError as e:
        msg = e.read(120).decode("utf-8", "replace")
        print(f"[relay] {device} 被拒 HTTP {e.code}: {msg}", flush=True)
        if e.code in (500, 502, 503, 504):
            _enqueue({"url": url, "dev": device, "m": metrics})      # 服务端问题 → 稍后补
        return f"err:http{e.code}"
    except Exception as e:
        print(f"[relay] {device} 转发失败 {type(e).__name__}: {str(e)[:80]}（已落盘，稍后补发）", flush=True)
        _enqueue({"url": url, "dev": device, "m": metrics})
        return "err:net"


# ---------------------------------------------------------------- HTTP 侧
def parse_http(q: dict):
    """从 query 里解析：d / m(可逗号) / v(可逗号) / u"""
    g = lambda k: (q.get(k) or [""])[0]
    dev = g("d").strip()
    ms = [x.strip() for x in g("m").split(",") if x.strip()]
    vs = [x.strip() for x in g("v").split(",")]
    u = g("u").strip()
    return dev, [(m, vs[i] if i < len(vs) else (vs[0] if vs else ""), u) for i, m in enumerate(ms)]


def parse_udp(text: str):
    """UDP 一行：
       'dev,metric,value'            （单指标，最短写法）
       'dev,metric:value,metric2:value2'
       'dev,temp=25.3,hum=61'        （容忍 = 号写法）
    """
    text = text.strip().strip("\x00")
    if not text:
        return "", []
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < 2:
        return "", []
    dev, rest = parts[0], parts[1:]
    no_sep = [x for x in rest if ":" not in x and "=" not in x]
    if len(rest) == 1 and len(no_sep) == 1:
        return dev, [("value", rest[0], "")]
    if len(rest) == 2 and len(no_sep) == 2:
        # 最常见的短写法："dev,metric,value"
        return dev, [(rest[0], rest[1], "")]
    pairs, meta = [], {}
    for item in rest:
        for sep in (":", "="):
            if sep in item:
                k, v = item.split(sep, 1)
                if k.strip() in ("seq", "s", "crc", "c"):
                    meta[k.strip()] = v.strip()
                else:
                    pairs.append((k.strip(), v.strip(), ""))
                break
        else:
            pairs.append((item, "", ""))
    return dev, pairs, meta


def http_server(port: int):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        server_version = "mcu-relay"

        def _reply(self, text):
            data = (text + "\n").encode()
            self.send_response(200 if text.startswith("ok") else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path not in ("/mcu", "/", "/ingest"):
                return self._reply("err:path")
            q = urllib.parse.parse_qs(u.query)
            dev, pairs = parse_http(q)
            seq = int((q.get("s") or ["0"])[0] or 0)
            self._reply(forward(dev, pairs, seq))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(min(n, 4096)).decode("utf-8", "replace")
            q = urllib.parse.parse_qs(raw.strip())
            if not q:
                dev, pairs, meta = parse_udp(raw)    # 也容忍纯文本行
            else:
                dev, pairs, meta = parse_http(q), {}
                dev, pairs = dev
            seq = int((meta or {}).get("seq", 0) or 0)
            self._reply(forward(dev, pairs, seq))

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"[relay] HTTP 在 :{port}（设备发 GET /mcu?d=..&m=..&v=..）", flush=True)
    srv.serve_forever()


# ---------------------------------------------------------------- UDP 侧
def udp_server(port: int):
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sk.bind(("0.0.0.0", port))
    print(f"[relay] UDP 在 :{port}（设备发一行 'dev,metric,value'）", flush=True)
    while True:
        try:
            data, addr = sk.recvfrom(1024)
            text = data.decode("utf-8", "replace")
            dev, pairs, meta = parse_udp(text)
            seq = int(meta.get("seq") or 0)
            crc = meta.get("crc")
            if crc:                                     # 设备带了校验和就先验：不对不往中枢送
                metrics = ",".join(x[0] for x in pairs)
                values = ",".join(str(x[1]) for x in pairs)
                try:
                    if int(crc) != sum(f"{dev}{metrics}{values}".encode()) % 256:
                        sk.sendto(b"err:crc\n", addr)
                        continue
                except ValueError:
                    sk.sendto(b"err:crc\n", addr)
                    continue
            res = forward(dev, pairs, seq)
            sk.sendto((res + "\n").encode(), addr)      # 回一行确认
        except Exception as e:
            print(f"[relay] UDP 异常 {type(e).__name__}: {str(e)[:80]}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="MCU 中继：内网明文 ↔ 云上中枢 HTTPS")
    ap.add_argument("--http", type=int, default=8088)
    ap.add_argument("--udp", type=int, default=8089)
    a = ap.parse_args()
    if not TOKEN:
        print("[relay] 警告：没设 WHALE_TOKEN，中枢会拒收（err:token）", flush=True)
    print(f"[relay] 中枢 {HUB}", flush=True)
    if a.http:
        threading.Thread(target=http_server, args=(a.http,), daemon=True).start()
    if a.udp:
        threading.Thread(target=udp_server, args=(a.udp,), daemon=True).start()
    threading.Thread(target=retry_worker, daemon=True).start()      # ⑤ 补发线程
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[relay] 停了", flush=True)


if __name__ == "__main__":
    main()

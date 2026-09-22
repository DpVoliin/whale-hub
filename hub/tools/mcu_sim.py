#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcu_sim.py —— 假装自己是一块 STM32 小屏（不用买元件就能验整条链）

它会像真设备一样轮询中枢，把"屏幕上会显示什么、喇叭会念什么"打印出来，
顺便让你确认两件事：① 动作标注确实被剥掉了 ② gb2312 编码 TTS 模块能直接用。

用法：
    export WHALE_HUB="https://你的中枢:11443"
    export WHALE_TOKEN="hub.json 里的 mcu.token（设备专用，别用主 token）"
    export WHALE_CA="/path/to/hub.crt"          # 自签证书必须给
    python3 hub/tools/mcu_sim.py --device stm32_desk --enc gb2312 --interval 5

    # 只想看一眼队列里有什么、不消费：--peek
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

def _from_hubjson(key):
    """从 hub.json 里自动读（优先设备 token）—— 免得让使用者手抄一串乱七八糟的字符。"""
    import pathlib as _p
    cands = []
    if os.getenv("WHALE_HOME"):
        cands.append(_p.Path(os.environ["WHALE_HOME"]) / "hub.json")
    here = _p.Path(__file__).resolve()
    cands += [here.parents[1] / "hub.json", here.parents[2] / "hub.json", _p.Path("/root/hub/hub.json")]
    for f in cands:
        try:
            if f.is_file():
                d = json.loads(f.read_text(encoding="utf-8"))
                if key == "token":
                    v = (d.get("mcu") or {}).get("token") or d.get("token") or ""
                else:
                    v = d.get("port", 11440)
                    return "http://127.0.0.1:%s" % v
                if v:
                    print(f"[sim] 从 {f} 读到 {key}（{'设备' if key == 'token' else ''}）", flush=True)
                    return v
        except Exception:
            continue
    return ""


HUB = (os.getenv("WHALE_HUB") or _from_hubjson("hub")).rstrip("/")
TOKEN = os.getenv("WHALE_TOKEN") or _from_hubjson("token")
CA = os.getenv("WHALE_CA", "")


def _ctx():
    if HUB.startswith("http://"):
        return None
    if CA and os.path.exists(CA):
        return ssl.create_default_context(cafile=CA)
    print("[sim] 拒绝启动：HTTPS 中枢必须给 CA（export WHALE_CA=...）；调试可用 WHALE_INSECURE=1", flush=True)
    if os.getenv("WHALE_INSECURE") == "1":
        return ssl._create_unverified_context()
    sys.exit(2)


def get(path: str, raw: bool = False):
    req = urllib.request.Request(HUB + path)
    req.add_header("X-Token", TOKEN)
    req.add_header("User-Agent", "mcu-sim/1.0")
    with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
        data = r.read()
    return data if raw else data.decode("utf-8", "replace").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="假装自己是一块 STM32 小屏")
    ap.add_argument("--device", default="mcu_sim")
    ap.add_argument("--enc", default="utf8", choices=["utf8", "gb2312"])
    ap.add_argument("--interval", type=int, default=5)
    ap.add_argument("--peek", action="store_true", help="只看不消费（调试用）")
    ap.add_argument("--once", action="store_true", help="只轮询一次")
    a = ap.parse_args()
    if not HUB or not TOKEN:
        print("  要先设 WHALE_HUB 与 WHALE_TOKEN（设备 token）", flush=True)
        return 2

    print(f"  ✓ 模拟设备已启动：{a.device} · 中枢 {HUB} · 编码 {a.enc} · 每 {a.interval}s 轮询", flush=True)
    n = 0
    while True:
        q = urllib.parse.urlencode({"d": a.device, "t": TOKEN, "enc": a.enc, **({"peek": "1"} if a.peek else {})})
        try:
            raw = get("/mcu/inbox?" + q, raw=True)          # 一律先拿原始字节
            codec = "gb2312" if a.enc == "gb2312" else "utf-8"
            try:
                line = raw.decode(codec).strip()            # 按**实际请求的编码**解码
            except Exception:
                line = raw.decode("utf-8", "replace").strip()
        except urllib.error.HTTPError as e:
            print(f"  [sim] HTTP {e.code}：{e.read(60).decode('utf-8', 'replace')}", flush=True)
            if a.once:
                return 1
            time.sleep(a.interval)
            continue
        except Exception as e:
            print(f"  [sim] 连不上：{type(e).__name__} {str(e)[:60]}", flush=True)
            if a.once:
                return 1
            time.sleep(a.interval)
            continue

        if line.startswith("none"):
            print(f"  [sim] {time.strftime('%H:%M:%S')} 队列空 → 屏幕显示待机表情，不发声", flush=True)
        elif line.startswith("ok|"):
            _, rid, text = line.split("|", 2)
            n += 1
            print(f"  [sim] {time.strftime('%H:%M:%S')} ← 收到提醒 #{rid}", flush=True)
            print(f"         屏幕显示：{text}", flush=True)
            print(f"         喇叭朗读：{text}", flush=True)
            if raw:
                # 验证 gb2312 编码确实能用（真 TTS 模块就吃这个）
                ok = True
                try:
                    raw.split(b"|", 2)[2].decode("gb2312")
                except Exception as ex:
                    ok = False
                    print(f"         ✗ gb2312 解码失败：{ex}", flush=True)
                print(f"         gb2312 编码自检：{'✓ 可用（TTS 模块能直接读）' if ok else '✗'}", flush=True)
            if not a.peek:
                print(f"         回执：{get('/mcu/ack?' + urllib.parse.urlencode({'d': a.device, 't': TOKEN, 'id': rid}))}", flush=True)
        else:
            print(f"  [sim] 非预期响应：{line[:80]}", flush=True)

        if a.once:
            print(f"  轮询一次完成，共取到 {n} 条，退出（--once）", flush=True)
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("  已停止", flush=True)

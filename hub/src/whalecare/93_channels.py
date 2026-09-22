# ---------------------------------------------------------------- 出口（直发通道）
# 为什么要有它：主出口是"说话层 → Hermes 网关 webhook → 微信"，那条链依赖网关活着。
# 这里给中枢自己开**直发**通道，一台 4C4G 的机器 + 一个 webhook 就能把话说出去：
#   · 企业微信群机器人（官方接口、无限流）—— 填 channels.wecom_webhook 即可，不需要 corpid/secret
#   · 通用 webhook —— 任何接受 POST {"text": "..."} 的地址（自建小服务、Slack/Discord 中转等）
#   · ntfy —— 极简自托管推送：把文本 POST 到 https://ntfy.sh/<你的主题> 即达（可自建服务端）
#   · Bark —— iOS 极简推送：https://api.day.app/<你的key>/<内容>（可自建服务端）
# 刻意**不**自动发：自动发会和说话层重复推送。它是个"通道"，由调用方（人 / cron / 扩展）决定何时用。
CHANNEL_KEYS = ("wecom_webhook", "generic_webhook", "wecom_corpid", "wecom_secret", "wecom_agentid",
                "wecom_touser", "ntfy_url", "ntfy_token", "bark_url", "bark_sound")


def channels_status(cfg=None):
    """各出口配没配（给人看的；**不打印 webhook 地址本身**，它带密钥）。"""
    ch = (cfg or CFG).get("channels") or {}
    return {
        "wecom_bot": "已配置" if ch.get("wecom_webhook") else "空",
        "generic_webhook": "已配置" if ch.get("generic_webhook") else "空",
        "wecom_app": "已配置" if all(ch.get(k) for k in ("wecom_corpid", "wecom_secret", "wecom_agentid"))
                     else "空（需要 corpid + secret + agentid）",
        "ntfy": "已配置" if ch.get("ntfy_url") else "空（填 https://ntfy.sh/你的主题）",
        "bark": "已配置" if ch.get("bark_url") else "空（填 https://api.day.app/你的key）",
        "note": "主出口仍是说话层→网关；这里是中枢**直发**通道，不自动使用",
    }


def _post_json(url, payload, timeout=10):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), r.read(200).decode("utf-8", "replace")


def channel_send(text, cfg=None):
    """把一条消息发到所有已配置的直发出口。返回 {出口: "ok"/错误}。

    企业微信群机器人要求的 payload 是 {"msgtype":"text","text":{"content": ...}}；
    通用出口只要求 {"text": ...}（够简单，自己搭个转发服务就能接）。
    """
    ch = (cfg or CFG).get("channels") or {}
    text = str(text or "").strip()
    out = {}
    if not text:
        return {"error": "内容为空"}
    url = (ch.get("wecom_webhook") or "").strip()
    if url:
        try:
            st, body = _post_json(url, {"msgtype": "text", "text": {"content": text[:1800]}})
            out["wecom_bot"] = "ok" if st == 200 else "HTTP %s" % st
            if st == 200 and '"errcode":0' not in body.replace(" ", ""):
                out["wecom_bot"] = "被拒：%s" % body[:80]
        except Exception as e:
            out["wecom_bot"] = "%s: %s" % (type(e).__name__, str(e)[:60])
    url2 = (ch.get("generic_webhook") or "").strip()
    if url2:
        try:
            st, _ = _post_json(url2, {"text": text[:1800]})
            out["generic"] = "ok" if st == 200 else "HTTP %s" % st
        except Exception as e:
            out["generic"] = "%s: %s" % (type(e).__name__, str(e)[:60])
    # ── ntfy：把文本**原样**POST 到主题地址（不是 JSON！这是它自己的协议）──
    ntfy = (ch.get("ntfy_url") or "").strip()
    if ntfy:
        try:
            import urllib.request
            hdr = {"Title": ("whalecare".encode()).decode(),
                   "Tags": "whale", "Content-Type": "text/plain; charset=utf-8"}
            tok = (ch.get("ntfy_token") or "").strip()
            if tok:
                hdr["Authorization"] = "Bearer " + tok
            req = urllib.request.Request(ntfy, data=text[:1800].encode("utf-8"), headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=10) as r:
                out["ntfy"] = "ok" if r.getcode() == 200 else "HTTP %s" % r.getcode()
        except Exception as e:
            out["ntfy"] = "%s: %s" % (type(e).__name__, str(e)[:60])

    # ── Bark：经典路径式（https://api.day.app/<key>/<标题>/<内容>?sound=xxx）──
    bark = (ch.get("bark_url") or "").strip()
    if bark:
        try:
            import urllib.parse
            import urllib.request
            base = bark.rstrip("/")
            title = "whalecare"
            seg = "%s/%s/%s" % (base, urllib.parse.quote(title), urllib.parse.quote(text[:900]))
            snd = (ch.get("bark_sound") or "").strip()
            if snd:
                seg += "?sound=" + urllib.parse.quote(snd)
            with urllib.request.urlopen(seg, timeout=10) as r:
                body = r.read(200).decode("utf-8", "replace")
                ok = r.getcode() == 200 and '"code":200' in body.replace(" ", "")
                out["bark"] = "ok" if ok else ("被拒：%s" % body[:60])
        except Exception as e:
            out["bark"] = "%s: %s" % (type(e).__name__, str(e)[:60])

    if not out:
        out["error"] = ("没有配置任何直发出口（channels.wecom_webhook / generic_webhook / "
                        "ntfy_url / bark_url）")
    try:
        audit("channel_send", target=",".join(sorted(out)), result="ok" if "ok" in str(list(out.values())) else "error",
              note="直发一条（%d 字）" % len(text))
    except Exception:
        pass
    return out


def channel_test(cfg=None):
    """发一条测试消息（配出口时用它验收，别等真有事才发现不通）。"""
    return channel_send("（测试）whalecare 直发通道已连通 —— 收到即说明配置生效。", cfg)

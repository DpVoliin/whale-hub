# ---------------------------------------------------------------- 出口（直发通道）
# 为什么要有它：主出口是"说话层 → Hermes 网关 webhook → 微信"，那条链依赖网关活着。
# 这里给中枢自己开**直发**通道，一台 4C4G 的机器 + 一个 webhook 就能把话说出去：
#   · 企业微信群机器人（官方接口、无限流）—— 填 channels.wecom_webhook 即可，不需要 corpid/secret
#   · 通用 webhook —— 任何接受 POST {"text": "..."} 的地址（自建小服务、Slack/Discord 中转等）
# 刻意**不**自动发：自动发会和说话层重复推送。它是个"通道"，由调用方（人 / cron / 扩展）决定何时用。
CHANNEL_KEYS = ("wecom_webhook", "generic_webhook", "wecom_corpid", "wecom_secret", "wecom_agentid", "wecom_touser")


def channels_status(cfg=None):
    """各出口配没配（给人看的；**不打印 webhook 地址本身**，它带密钥）。"""
    ch = (cfg or CFG).get("channels") or {}
    return {
        "wecom_bot": "已配置" if ch.get("wecom_webhook") else "空",
        "generic_webhook": "已配置" if ch.get("generic_webhook") else "空",
        "wecom_app": "已配置" if all(ch.get(k) for k in ("wecom_corpid", "wecom_secret", "wecom_agentid"))
                     else "空（需要 corpid + secret + agentid）",
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
    if not out:
        out["error"] = "没有配置任何直发出口（channels.wecom_webhook / generic_webhook）"
    try:
        audit("channel_send", target=",".join(sorted(out)), result="ok" if "ok" in str(list(out.values())) else "error",
              note="直发一条（%d 字）" % len(text))
    except Exception:
        pass
    return out


def channel_test(cfg=None):
    """发一条测试消息（配出口时用它验收，别等真有事才发现不通）。"""
    return channel_send("（测试）whalecare 直发通道已连通 —— 收到即说明配置生效。", cfg)

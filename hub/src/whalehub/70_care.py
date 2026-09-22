# ----------------------------------------------------------------- 主动关心
def _in_quiet_hours(now=None):
    now = now or datetime.now(TZ)
    a, b = CFG["care"]["quiet_hours"]
    h = now.hour
    return (a <= h <= 23 or 0 <= h < b) if a > b else (a <= h < b)


def _pushed_count(day=None):
    day = day or today_str()
    with db() as c:
        n = c.execute("SELECT COUNT(*) n FROM reminders WHERE day=? AND kind IN "
                      "('care','alert') AND status IN ('new','delivered')", (day,)).fetchone()["n"]
    return int(n)


def _last_push_at():
    with db() as c:
        row = c.execute("SELECT MAX(created_at) t FROM reminders WHERE kind IN ('care','alert')").fetchone()
    if not row or not row["t"]:
        return None
    try:
        return datetime.fromisoformat(row["t"])
    except Exception:
        return None


def _allow_proactive(level="info", now=None):
    """频率闸：免打扰 + 每日上限 + 最小间隔。不满足就攒着（返回 False）。"""
    now = now or datetime.now(TZ)
    if not CFG["care"].get("enabled", True):
        return False
    if _in_quiet_hours(now) and level != "urgent":
        return False
    if _pushed_count(now.strftime("%Y-%m-%d")) >= int(CFG["care"]["daily_max"]):
        return False
    last = _last_push_at()
    if last and (now - last).total_seconds() < int(CFG["care"]["min_gap_minutes"]) * 60:
        return False
    return True


def push_wecom(text):
    """推到企业微信：优先群机器人 webhook；配了自建应用三件套则走应用消息。"""
    ch = CFG.get("channels", {})
    ok = False
    hook = (ch.get("wecom_webhook") or "").strip()
    if hook:
        try:
            import urllib.request as _u
            body = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode()
            req = _u.Request(hook, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            resp = json.loads(_u.urlopen(req, timeout=8).read() or b"{}")
            ok = resp.get("errcode") == 0
            print(f"[wecom] webhook {'ok' if ok else resp}", flush=True)
        except Exception as e:
            print(f"[wecom] webhook 失败：{str(e)[:70]}", flush=True)
    if not ok and ch.get("wecom_corpid") and ch.get("wecom_secret") and ch.get("wecom_agentid"):
        try:
            import urllib.request as _u
            t = json.loads(_u.urlopen(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=%s&corpsecret=%s"
                % (ch["wecom_corpid"], ch["wecom_secret"]), timeout=8).read())
            token_ = t.get("access_token")
            if token_:
                body = json.dumps({"touser": ch.get("wecom_touser", "@all"), "msgtype": "text",
                                   "agentid": int(ch["wecom_agentid"]),
                                   "text": {"content": text}}, ensure_ascii=False).encode()
                req = _u.Request("https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=" + token_,
                                 data=body, method="POST")
                req.add_header("Content-Type", "application/json")
                r = json.loads(_u.urlopen(req, timeout=8).read() or b"{}")
                ok = r.get("errcode") == 0
                print(f"[wecom] app {'ok' if ok else r}", flush=True)
        except Exception as e:
            print(f"[wecom] app 失败：{str(e)[:70]}", flush=True)
    return ok


def push_terminals(rid, text, level="info", kind="care"):
    """主动推送：把提醒 POST 给注册过 webhook 的终端（拉模式的终端忽略即可）。"""
    import urllib.request as _u
    payload = json.dumps({"id": rid, "kind": kind, "level": level, "text": text,
                          "persona": CFG["persona"], "at": now_iso()}, ensure_ascii=False).encode()
    with db() as c:
        rows = c.execute("SELECT name, note FROM terminals WHERE note LIKE 'http%'").fetchall()
    for r in rows:
        try:
            req = _u.Request(r["note"], data=payload, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("X-Token", CFG["token"])
            _u.urlopen(req, timeout=6).read()
            print(f"[push] → {r['name']} ok", flush=True)
        except Exception as e:
            print(f"[push] → {r['name']} 失败：{str(e)[:60]}", flush=True)


def say(text, level="info", kind="care", key=None, now=None, force=False):
    """主动说一句：过频率闸才落库 + 推送；key 用于去重（同一件事当天只说一次）。

    force=True（**健康异常专用**）：绕过免打扰与每日限额，立即说 —— 但**去重仍然生效**，
    同一件异常一天只提一次，避免连续刷屏。"""
    # ★ 外挂钩子：让她开口前可以被用户自己的规则拦一下或改写（ext/hooks.py）
    #   约定：返回非空字符串=替换内容；返回空字符串=这次不说；返回 None=照原样。
    _h = call_hook("before_say", text, level, kind, key)
    if isinstance(_h, str):
        if not _h.strip():
            return None
        text = _h
    now = now or datetime.now(TZ)
    # ★ 情节记忆：她说过的话自动留痕（脱敏后只有正文，没有设备名/原文）
    episode_add("said:" + str(kind), text)
    if key:
        with db() as c:
            if c.execute("SELECT key FROM fired WHERE key=?", (key,)).fetchone():
                return None
    if not force and not _allow_proactive(level, now):
        return None
    with db() as c:
        cur = c.execute("INSERT INTO reminders(day, kind, level, text, created_at, status) "
                        "VALUES (?,?,?,?,?,'new')", (now.strftime("%Y-%m-%d"), kind, level, text, now_iso()))
        rid = cur.lastrowid
        if key:
            c.execute("INSERT OR REPLACE INTO fired(key, at) VALUES (?,?)", (key, now_iso()))
    push_terminals(rid, text, level, kind)
    push_wecom(text)
    print(f"[care] #{rid} {level} {text[:40]}", flush=True)
    return rid


_weather_cache = {"day": "", "text": ""}


def weather_line():
    """今天的天气一句（免费 Open-Meteo，无需 key；失败就静默跳过，绝不影响主流程）。"""
    if not CFG["care"].get("weather", True):
        return ""
    day = today_str()
    if _weather_cache["day"] == day:
        return _weather_cache["text"]
    c = CFG["care"]["city"]
    try:
        import urllib.request as _u
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={c['lat']}&longitude={c['lon']}"
               "&daily=precipitation_probability_max,temperature_2m_max,temperature_2m_min"
               "&timezone=Asia%2FShanghai&forecast_days=1")
        with _u.urlopen(url, timeout=8) as r:
            d = json.loads(r.read())["daily"]
        p = (d.get("precipitation_probability_max") or [0])[0]
        hi = (d.get("temperature_2m_max") or [0])[0]
        lo = (d.get("temperature_2m_min") or [0])[0]
        text = f"{c['name']}今天 {int(lo)}~{int(hi)}℃"
        text += "，有雨，记得带伞" if (p or 0) >= 50 else ("，可能下雨，伞备着" if (p or 0) >= 30 else "")
        text += "。"
        _weather_cache.update({"day": day, "text": text})
        return text
    except Exception as e:
        print(f"[weather] 取不到：{str(e)[:60]}", flush=True)
        return ""



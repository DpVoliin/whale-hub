# ---------------------------------------------------------------- Web 管理台（标准库拼 HTML）
# 设计取舍：
#   · **不引前端框架**（vue/react/alpine 都不引）—— 一旦引了，"零依赖"这条卖点就没了，
#     而且静态资源要单独分发。这里就用 <form> + 一点内联 CSS，够用。
#   · **鉴权与 API 完全同一套 token**：登录页把 token 换成一个 HttpOnly 会话 cookie
#     （SameSite=Strict，抗 CSRF），API 侧仍然只认 X-Token 头，不因为"是网页"就放松。
#   · 能改的只有"开关与人设"这类**可逆**配置；删除/导出这类破坏性动作仍然只在 CLI（hubctl）。
import hashlib
import hmac

_COOKIE_SEED = b"whale-admin-session-v1"


def admin_session():
    """管理台会话值 = HMAC(token)。好处：**轮换 token 会顺带废掉所有旧会话**。"""
    return hmac.new(str(CFG.get("token") or "").encode(), _COOKIE_SEED, hashlib.sha256).hexdigest()[:32]


def _e(x):
    import html as _h
    return _h.escape(str(x if x is not None else "—"))


_CSS = """
:root{color-scheme:dark}
body{background:#0e1116;color:#dfe6ee;font:14px/1.6 -apple-system,"Segoe UI","PingFang SC",sans-serif;margin:0;padding:20px 22px 60px}
h1{font-size:17px;margin:0 0 2px}h2{font-size:14px;margin:26px 0 8px;color:#8ea1b5;font-weight:600}
a{color:#6cb6ff;text-decoration:none}a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%}td,th{padding:6px 10px;border-bottom:1px solid #1c2431;text-align:left;font-size:13px}
th{color:#8ea1b5;font-weight:600}.dim{color:#7c8b9c}.warn{color:#f0a35e}.bad{color:#ef6f6f}.ok{color:#5fd38a}
ul{margin:0;padding-left:18px}li{margin:2px 0}
.tag{display:inline-block;padding:1px 7px;border:1px solid #2b3646;border-radius:9px;color:#8ea1b5;font-size:12px}
.card{border:1px solid #1c2431;border-radius:10px;padding:12px 14px;margin:8px 0;background:#12161d}
label{display:inline-block;min-width:150px;color:#a9b7c6}
input,select{background:#0b0e13;border:1px solid #2b3646;color:#dfe6ee;border-radius:6px;padding:5px 8px;font:13px/1.4 inherit}
input[type=submit]{background:#1d4e86;border-color:#2a6bb0;cursor:pointer;padding:6px 14px}
input[type=submit]:hover{background:#245da0}
.row{margin:6px 0}
.flash{border-left:3px solid #5fd38a;background:#12201a;padding:8px 12px;border-radius:6px;margin:10px 0}
.flash.bad{border-color:#ef6f6f;background:#201414}
.grid{display:flex;flex-wrap:wrap;gap:14px}.grid>.card{flex:1 1 320px}
"""


def login_html(name="鲸鲸", err=""):
    return ("""<!doctype html><html lang=zh><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>登录 · __NAME__ 中枢</title><style>__CSS__</style>
<h1>__NAME__ · 中枢管理台</h1>
<div class=dim>这个页面能看到你的数据，所以要先验证 —— 口令就是 hub.json 里的 token。</div>
__ERR__
<form class=card method=post action=/login>
<div class=row><label>token</label><input type=password name=token autofocus size=34></div>
<div class=row><input type=submit value=登录></div>
</form>
<p class=dim>命令行里也能看：<code>hubctl status</code> · <code>hubctl token</code>。浏览器登录后会种一个 HttpOnly cookie，
换 token 即失效；API 侧仍然只认 X-Token 头。</p>
"""
            .replace("__CSS__", _CSS)
            .replace("__NAME__", _e(name))
            .replace("__ERR__", ('<div class="flash bad">%s</div>' % _e(err)) if err else ""))


def admin_html(flash="", ok=True):
    # ---- 概览
    try:
        with db() as c:
            n_metrics = c.execute("SELECT COUNT(*) n FROM metrics").fetchone()["n"]
            n_rem = c.execute("SELECT COUNT(*) n FROM reminders").fetchone()["n"]
            n_dev = c.execute("SELECT COUNT(DISTINCT device) n FROM metrics").fetchone()["n"]
    except Exception:
        n_metrics = n_rem = n_dev = 0
    head = ("<h1>%s · 中枢管理台 <span class=tag>v%s</span></h1>"
            "<div class=dim>%s ｜ 代码指纹 %s ｜ 数据点 %s ｜ 设备 %s ｜ 提醒 %s</div>"
            % (_e(CFG["persona"]["name"]), _e(VERSION), _e(now_iso()),
               _e(code_fingerprint()), n_metrics, n_dev, n_rem))

    # ---- 数据源健康度
    rows = []
    try:
        for s, v in source_health().items():
            cls = "ok" if v["verdict"] == "ok" else "warn"
            rows.append("<tr><td>%s</td><td class=%s>%s</td><td>%s</td><td class=dim>%s</td></tr>"
                        % (_e(s), cls, _e(v["verdict"]), _e(v.get("today_n")), _e(v.get("note"))))
    except Exception as e:
        rows.append("<tr><td colspan=4>健康度算不出：%s</td></tr>" % _e(str(e)[:80]))
    health = ("<h2>数据源健康度</h2><table><tr><th>源</th><th>状态</th><th>今天</th><th>说明</th></tr>%s</table>"
              % ("".join(rows) or "<tr><td colspan=4 class=dim>还没有数据源</td></tr>"))

    # ---- 开关（care / privacy / rules）
    care = CFG.get("care") or {}
    priv = CFG.get("privacy") or {}
    rules = CFG.get("rules") or {}
    qh = care.get("quiet_hours") or [23, 7]

    def yn(v):
        return "是" if v else "否"
    switches = """<h2>开关</h2>
<form class=card method=post action=/admin><input type=hidden name=section value=care>
<div class=row><label>主动关心总开关</label><select name=enabled><option value=1 __CE__>开</option><option value=0 __CD__>关</option></select></div>
<div class=row><label>天气关心</label><select name=weather><option value=1 __WE__>开</option><option value=0 __WD__>关</option></select></div>
<div class=row><label>免打扰起（时）</label><input name=quiet_from type=number min=0 max=23 value="__QF__"></div>
<div class=row><label>免打扰止（时）</label><input name=quiet_to type=number min=0 max=23 value="__QT__"></div>
<div class=row><label>每天主动上限（条）</label><input name=daily_max type=number min=0 max=50 value="__DM__"></div>
<div class=row><label>两条最小间隔（分钟）</label><input name=min_gap type=number min=1 max=600 value="__MG__"></div>
<div class=row><input type=submit value="保存开关"></div></form>

<form class=card method=post action=/admin><input type=hidden name=section value=privacy>
<div class=row><label>脱敏总开关</label><select name=priv_enabled><option value=1 __PE__>开</option><option value=0 __PD__>关</option></select></div>
<div class=row><label>保存通知原文（排障用）</label><select name=store_raw><option value=1 __RE__>开</option><option value=0 __RD__>关</option></select></div>
<div class=row><label>保留天数（0=永久）</label><input name=retention type=number min=0 max=3650 value="__RT__"></div>
<div class=row><input type=submit value="保存隐私设置"></div></form>

<form class=card method=post action=/admin><input type=hidden name=section value=rules>
<div class=row><label>连续活跃提醒（分钟）</label><input name=sit type=number min=10 max=300 value="__SIT__"></div>
<div class=row><label>屏幕过高阈值（分钟）</label><input name=screen type=number min=60 max=1440 value="__SCR__"></div>
<div class=row><label>睡眠不足阈值（分钟）</label><input name=sleep type=number min=60 max=900 value="__SLP__"></div>
<div class=row><label>设备离线提醒（小时）</label><input name=offline type=number min=1 max=240 value="__OFF__"></div>
<div class=row><label>课前提醒（分钟，0=关）</label><input name=cls type=number min=0 max=120 value="__CLS__"></div>
<div class=row><input type=submit value="保存规则"></div></form>
""".replace("__CE__", "selected" if care.get("enabled") else "").replace("__CD__", "" if care.get("enabled") else "selected") \
   .replace("__WE__", "selected" if care.get("weather") else "").replace("__WD__", "" if care.get("weather") else "selected") \
   .replace("__QF__", _e(qh[0])).replace("__QT__", _e(qh[1])) \
   .replace("__DM__", _e(care.get("daily_max"))).replace("__MG__", _e(care.get("min_gap_minutes"))) \
   .replace("__PE__", "selected" if priv.get("enabled", True) else "").replace("__PD__", "" if priv.get("enabled", True) else "selected") \
   .replace("__RE__", "selected" if priv.get("store_raw_text") else "").replace("__RD__", "" if priv.get("store_raw_text") else "selected") \
   .replace("__RT__", _e(priv.get("retention_days"))) \
   .replace("__SIT__", _e(rules.get("sit_continuous_minutes"))).replace("__SCR__", _e(rules.get("screen_high_minutes"))) \
   .replace("__SLP__", _e(rules.get("sleep_low_minutes"))).replace("__OFF__", _e(rules.get("device_offline_hours"))) \
   .replace("__CLS__", _e(rules.get("class_remind_minutes")))

    # ---- 人设
    p = CFG["persona"]
    persona = """<h2>人设（改这里 = 三端同步）</h2>
<form class=card method=post action=/admin><input type=hidden name=section value=persona>
<div class=row><label>名字</label><input name=name size=20 value="__N__"></div>
<div class=row><label>自称</label><input name=self_call size=20 value="__SC__"></div>
<div class=row><label>怎么称呼你</label><input name=call_user size=20 value="__CU__"></div>
<div class=row><label>喜欢</label><input name=likes size=30 value="__LK__"></div>
<div class=row><label>禁忌</label><input name=taboo size=30 value="__TB__"></div>
<div class=row><label>语气</label><input name=tone size=60 value="__TN__"></div>
<div class=row><label>文体要求</label><input name=style size=60 value="__ST__"></div>
<div class=row><input type=submit value="保存人设"></div></form>
""".replace("__N__", _e(p.get("name"))).replace("__SC__", _e(p.get("self_call"))) \
   .replace("__CU__", _e(p.get("call_user"))).replace("__LK__", _e(p.get("likes"))) \
   .replace("__TB__", _e(p.get("taboo"))).replace("__TN__", _e(p.get("tone"))) \
   .replace("__ST__", _e(p.get("style")))

    # ---- 配对码
    pairs = []
    try:
        for c_ in pair_list(8):
            state = "已用" if c_["used_at"] else ("已过期" if c_["expires_at"] < now_iso() else "待用")
            pairs.append("<tr><td>%s</td><td>%s</td><td>%s</td><td class=dim>%s</td></tr>"
                         % (_e(c_["code"]), _e(c_["device"] or "(任意)"), _e(state), _e(c_["created_at"][:16])))
    except Exception:
        pass
    pair_block = ("<h2>设备配对（一次性码）</h2>"
                  "<form class=card method=post action=/admin><input type=hidden name=section value=pair>"
                  "<div class=row><label>设备名（可留空）</label><input name=device size=20 placeholder=stm32_room></div>"
                  "<div class=row><input type=submit value=\"生成配对码\"></div></form>"
                  "<table><tr><th>码</th><th>设备</th><th>状态</th><th>生成</th></tr>%s</table>"
                  % ("".join(pairs) or "<tr><td colspan=4 class=dim>还没有配对码</td></tr>"))

    # ---- 外挂扩展 / 决策 / 审计
    ext = "".join("<li>%s → 上次入库 %s 条%s</li>"
                  % (_e(s["name"]), _e(s["last_n"]),
                     (" ｜ <span class=warn>%s</span>" % _e(s["last_err"])) if s["last_err"] else "")
                  for s in EXT["sources"])
    ext_block = ("<h2>外挂扩展</h2><ul>%s</ul>"
                 % (ext or "<li class=dim>（没装扩展）</li>"))

    dec = []
    try:
        with db() as c:
            for d in c.execute("SELECT ts, band, gap_sec, reason FROM decisions "
                               "ORDER BY id DESC LIMIT 10").fetchall():
                dec.append("<li><span class=dim>%s</span> %s ｜ %s 分钟 ｜ %s</li>"
                           % (_e(d["ts"][11:16]), _e(d["band"]), _e(round((d["gap_sec"] or 0) / 60.0)),
                              _e(d["reason"])))
    except Exception:
        pass
    dec_block = ("<h2>决策日志（她为什么这么频繁）</h2><ul>%s</ul>"
                 % ("".join(dec) or "<li class=dim>（还没有）</li>"))

    au = []
    try:
        for a in audit_recent(30):
            cls = {"denied": "warn", "error": "bad"}.get(a["result"], "dim")
            au.append("<tr><td class=dim>%s</td><td>%s</td><td>%s</td><td class=%s>%s</td><td class=dim>%s</td></tr>"
                      % (_e(a["ts"][5:16]), _e(a["action"]), _e(a["target"]), cls,
                         _e(a["result"]), _e(a["note"])))
    except Exception:
        pass
    try:
        st = audit_stats()
        au_stat = "近 7 天共 %s 条 ｜ 鉴权失败 %s ｜ 配置修改 %s" % (st["total"], st["auth_fail"], st["config_change"])
    except Exception:
        au_stat = ""
    audit_block = ("<h2>审计日志 <span class=tag>只记动作，不记内容</span></h2>"
                   "<div class=dim>%s</div>"
                   "<table><tr><th>时间</th><th>动作</th><th>对象</th><th>结果</th><th>说明</th></tr>%s</table>"
                   % (_e(au_stat), "".join(au) or "<tr><td colspan=5 class=dim>（还没有）</td></tr>"))

    links = ("<h2>其他</h2><div class=dim>"
             "<a href=/dash>只读数据页 /dash</a> ｜ "
             "<a href=/llm-preview>看会发给模型的内容 /llm-preview</a> ｜ "
             "<a href=/audit>审计 JSON /audit</a> ｜ "
             "<a href=/health>健康 /health</a> ｜ <a href=/logout>退出</a></div>"
             "<div class=dim style='margin-top:6px'>破坏性动作（导出/删除/备份还原）只在命令行："
             "<code>hubctl dump | prune | backup | restore</code> —— 网页端刻意不做，少一处被误触的面。</div>")

    return ("""<!doctype html><html lang=zh><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>__NAME__ · 管理台</title><style>__CSS__</style>
__HEAD____FLASH__
<div class=grid><div>__HEALTH____DEC__</div><div>__SW____PAIR__</div></div>
__PERSONA____EXT____AUDIT____LINKS__
""".replace("__CSS__", _CSS).replace("__NAME__", _e(CFG["persona"]["name"])).replace("__HEAD__", head)
       .replace("__FLASH__", ('<div class="flash%s">%s</div>' % ("" if ok else " bad", _e(flash))) if flash else "")
       .replace("__HEALTH__", health).replace("__DEC__", dec_block)
       .replace("__SW__", switches).replace("__PAIR__", pair_block)
       .replace("__PERSONA__", persona).replace("__EXT__", ext_block)
       .replace("__AUDIT__", audit_block).replace("__LINKS__", links))


def _num(form, key, cast=int, default=None, lo=None, hi=None):
    try:
        v = cast(form.get(key))
    except Exception:
        return default
    if lo is not None and v < lo:
        return default
    if hi is not None and v > hi:
        return default
    return v


def _flag(form, key):
    return str(form.get(key) or "").strip() in ("1", "on", "true", "yes")


def admin_apply(form, actor=""):
    """执行管理台提交。返回 (ok, 人话说明)。**只动可逆配置**，改完立刻写 hub.json。"""
    if not isinstance(form, dict) or not form:
        return False, "空提交"
    sec = str(form.get("section") or "")
    if sec == "persona":
        p = CFG.setdefault("persona", {})
        for k in ("name", "self_call", "call_user", "likes", "taboo", "tone", "style"):
            if k in form and str(form[k]).strip():
                p[k] = str(form[k]).strip()[:400]
        _save_cfg()
        return True, "人设已保存（三端共用同一份）"
    if sec == "care":
        c_ = CFG.setdefault("care", {})
        c_["enabled"] = _flag(form, "enabled")
        c_["weather"] = _flag(form, "weather")
        qf, qt = _num(form, "quiet_from", lo=0, hi=23), _num(form, "quiet_to", lo=0, hi=23)
        if qf is not None and qt is not None:
            c_["quiet_hours"] = [qf, qt]
        dm = _num(form, "daily_max", lo=0, hi=50)
        mg = _num(form, "min_gap", lo=1, hi=600)
        if dm is not None:
            c_["daily_max"] = dm
        if mg is not None:
            c_["min_gap_minutes"] = mg
        _save_cfg()
        return True, "开关已保存"
    if sec == "privacy":
        pv = CFG.setdefault("privacy", {})
        pv["enabled"] = _flag(form, "priv_enabled")
        pv["store_raw_text"] = _flag(form, "store_raw")
        rt = _num(form, "retention", lo=0, hi=3650)
        if rt is not None:
            pv["retention_days"] = rt
        _save_cfg()
        return True, "隐私设置已保存"
    if sec == "rules":
        r_ = CFG.setdefault("rules", {})
        for key, name, lo, hi in (("sit", "sit_continuous_minutes", 10, 300),
                                  ("screen", "screen_high_minutes", 60, 1440),
                                  ("sleep", "sleep_low_minutes", 60, 900),
                                  ("offline", "device_offline_hours", 1, 240),
                                  ("cls", "class_remind_minutes", 0, 120)):
            v = _num(form, key, lo=lo, hi=hi)
            if v is not None:
                r_[name] = v
        _save_cfg()
        return True, "规则已保存"
    if sec == "pair":
        info = pair_new(str(form.get("device") or "").strip())
        return True, ("配对码 %s（%d 分钟内有效，**只能用一次**）—— 设备侧："
                      "curl -sk 'https://<中枢>:11443/api/pair?c=%s&d=<设备名>'" % (info["code"], info["ttl_minutes"], info["code"]))
    return False, "不认识的 section：%s" % sec


def _save_cfg():
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(CFG, f, ensure_ascii=False, indent=2)

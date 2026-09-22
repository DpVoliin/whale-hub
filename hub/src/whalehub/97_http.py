# ----------------------------------------------------------------- HTTP
# ---- 简易防护（pentest 之后补的）----
MAX_BODY = 1024 * 1024        # 单请求最大 1MB（原来不限，3MB 也照收）
RATE_WINDOW = 60              # 秒
RATE_MAX = 240               # 每个来源每分钟 240 次；正常用量远低于此
_hits = {}


def rate_limited(ip):
    """够用就好的简易限流：只防"被刷"，不追求精确。"""
    now = time.time()
    arr = _hits.setdefault(ip, [])
    arr[:] = [t for t in arr if now - t < RATE_WINDOW]
    if len(arr) >= RATE_MAX:
        return True
    arr.append(now)
    return False


def ingest_items(body):
    """★ 唯一的入库闸口：HTTP /ingest、单片机 /api/mcu、外挂扩展**都走这里**。

    这样"去重 / 不落原文 / 单位口径"只有一处实现，新数据源不可能绕过规则。
    返回 (ok, skipped)。
    """
    items = body if isinstance(body, list) else [body]
    ok = 0
    skipped = 0
    with db() as c:
        # ★ P0 事件级幂等：网络重试必然导致重复投递。采集端带 event_id 时按 id 去重
        #   （比"值相同 + 60 秒窗口"更严：两个不同事件值恰好相同时不会互相吃掉）。
        #   没带 event_id 的旧客户端 → 自动退回下面的旧规则，向后兼容。
        def _seen(eid):
            if not eid:
                return False
            try:
                if c.execute("SELECT 1 FROM seen_events WHERE event_id=?", (str(eid),)).fetchone():
                    return True
                c.execute("INSERT OR REPLACE INTO seen_events(event_id, ts) VALUES (?,?)",
                          (str(eid)[:80], now_iso()))
                return False
            except Exception:
                return False
        for it in items:
            if not isinstance(it, dict) or not it.get("device") or not it.get("metric"):
                continue
            if it.get("v") is not None:            # 报文版本：记进 meta，以后改字段能判断对面哪一版
                _m = dict(it.get("meta") or {})
                _m["_v"] = it["v"]
                it["meta"] = _m
            if _seen(it.get("event_id") or it.get("eid")):
                skipped += 1
                continue
            ts = it.get("ts") or now_iso()
            try:
                day = datetime.fromisoformat(ts).astimezone(TZ).strftime("%Y-%m-%d")
            except Exception:
                day = today_str()

            # 去重：设备会重发未确认的批次、也可能同一轮上报两次
            #       → 同一设备/指标/数值在 60 秒内只留一条
            is_dup = False
            try:
                recent = c.execute(
                    "SELECT ts, value, meta FROM metrics WHERE device=? AND metric=? AND day=? "
                    "ORDER BY ts DESC LIMIT 3",
                    (it["device"], it["metric"], day)).fetchall()
                for row in recent:
                    # 事件类指标（订单/快递/签到…）值常常都是 1 → 必须把 meta 一起比，
                    # 否则"同一秒的三种不同事件"会被当成重复丢掉（实测被吃掉两笔订单）
                    same_meta = (json.loads(row["meta"] or "{}") == (it.get("meta") or {}))
                    same_val = (row["value"] == it.get("value")) and same_meta
                    try:
                        gap = abs((datetime.fromisoformat(ts)
                                   - datetime.fromisoformat(row["ts"])).total_seconds())
                    except Exception:
                        gap = 999
                    if same_val and gap < 60:
                        is_dup = True
                        break
            except Exception:
                is_dup = False
            if is_dup:
                skipped += 1
                continue

            # ② 默认**不落原文**：通知原文这类内容不进库（要排障时把 store_raw_text 打开）
            _meta = dict(it.get("meta") or {})
            if not CFG.get("privacy", {}).get("store_raw_text", False):
                _meta.pop("raw", None)
                _meta.pop("text", None)
            try:
                c.execute(
                    "INSERT OR REPLACE INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, day, it["device"], it["metric"], it.get("value"), it.get("unit", ""),
                     it.get("source", ""), float(it.get("confidence", 1.0)),
                     json.dumps(_meta, ensure_ascii=False)))
                ok += 1
            except Exception as e:
                print(f"[ingest] 跳过：{e}", flush=True)
    return ok, skipped


class Handler(BaseHTTPRequestHandler):
    server_version = f"hub/{VERSION}"

    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    # ---- 工具
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, q):
        # 只认 header 里的 X-Token：?token= 会进服务器日志、也会留在浏览器历史/代理记录里
        tok = self.headers.get("X-Token") or ""
        if not tok and (q.get("token") or [""])[0]:
            print(f"[warn] {self.client_address[0]} 试图用 query 里的 token（已拒绝）", flush=True)
        if tok != CFG["token"]:
            self._send(401, {"error": "token 不对"})   # ⚠️ 别把服务端路径写进报错（安全自测抓到：路径泄露）
            return False
        return True

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _touch(self, name):
        if not name:
            return
        with db() as c:
            c.execute("INSERT INTO terminals(name, last_seen) VALUES (?,?) "
                      "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen", (name, now_iso()))

    # ---- 路由
    def _guard(self):
        """统一防线：限流在最前面 —— 不然 /health 这类免鉴权接口会被拿来刷。"""
        if rate_limited(self.client_address[0]):
            self._send(429, {"error": "太频繁了，缓一下"})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        # 单片机极简口：/api/mcu?d=dev&m=temp&v=25.3&u=C&t=TOKEN
        #   多个指标可以逗号并列：m=temp,hum&v=25.3,60（省一次往返）
        #   回一行纯文本 ok / err:xxx —— 单片机不用解析 JSON
        if self.path.startswith("/api/mcu"):
            return self._mcu(urlparse(self.path).query)

        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        if path in ("/", "/index.html"):
            return self._page()
        if path == "/health":
            return self._send(200, self._health())
        if not self._auth(q):
            return
        if path == "/ext":
            return self._send(200, {
                "dir": EXT_DIR,
                "loaded": EXT["loaded"],
                "errors": EXT["errors"],
                "sources": [{"file": s["file"], "name": s["name"], "device": s["device"],
                             "interval_minutes": s["interval"], "last_n": s["last_n"],
                             "last_err": s["last_err"]} for s in EXT["sources"]],
                "hooks": sorted(EXT["hooks"].keys()),
                "note": "外挂扩展：加一个文件就多一个数据源，中枢核心不需要改；扩展报错不影响主流程",
            })
        if path == "/today":
            return self._today(q)
        if path == "/feedback":
            lim = min(50, int((q.get("limit") or ["20"])[0] or 20))
            consume = (q.get("consume") or ["0"])[0] == "1"
            with db() as c:
                rows = c.execute("SELECT id, ts, verdict, band, note FROM feedback "
                                 "WHERE consumed=0 ORDER BY id ASC LIMIT ?", (lim,)).fetchall()
                if consume and rows:
                    c.execute("UPDATE feedback SET consumed=1 WHERE id IN (%s)"
                              % ",".join("?" * len(rows)), [r["id"] for r in rows])
            return self._send(200, {"count": len(rows), "items": [dict(r) for r in rows]})
        if path in ("/dash", "/看数据"):
            return self._send(200, dash_html(), "text/html; charset=utf-8")
        if path == "/decisions":
            lim = min(500, int((q.get("limit") or ["100"])[0] or 100))
            with db() as c:
                rows = c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (lim,)).fetchall()
            return self._send(200, {"count": len(rows), "items": [dict(r) for r in rows]})
        if path == "/memory":
            qq = (q.get("q") or [""])[0]
            return self._send(200, {"q": qq, "items": episode_search(qq) if qq else episodes_recent()})
        if path == "/question":
            qn = question_now()
            return self._send(200, {"has_question": bool(qn), "question": qn or {},
                                    "note": "一天最多一个；有数据支撑才问；问过记情节不重复"})
        if path == "/review":
            k = (q.get("kind") or ["week"])[0]
            if k not in ("week", "month"):
                return self._send(400, {"ok": False, "error": "kind 只能是 week / month"})
            return self._send(200, review(k))
        if path == "/personas":
            return self._send(200, {"active": active_pack(), "dir": PERSONA_DIR,
                                    "packs": persona_packs(),
                                    "note": "换人设＝换目录：personas/<id>/{persona.json,card.json}"})
        if path == "/persona/card":
            pk = (q.get("pack") or [""])[0]
            return self._send(200, load_persona_card(pk or None) or {"error": "没有角色卡"})
        if path == "/persona":
            pk = (q.get("pack") or [""])[0]
            if pk:
                p = persona_pack_path(pk, "persona.json")
                if not p:
                    return self._send(404, {"ok": False, "error": f"没有人设包 {pk}"})
                return self._send(200, {"pack": pk, "persona": json.load(open(p, encoding="utf-8"))})
            return self._send(200, CFG["persona"])
        if path == "/pending":
            return self._pending(q)
        if path == "/devices":
            return self._send(200, self._devices())
        if path == "/remind":
            with db() as c:
                rows = c.execute("SELECT * FROM scheduled WHERE fired_at IS NULL "
                                 "ORDER BY at_iso ASC").fetchall()
            return self._send(200, {"items": [dict(r) for r in rows]})
        if path == "/llm-preview":
            ctx, dropped = llm_context()
            return self._send(200, {
                "privacy_enabled": bool(CFG["privacy"].get("enabled", True)),
                "would_send_to_model": ctx,
                "what_model_never_sees": dropped,
            })
        if path == "/timetable":
            tt = _timetable()
            return self._send(200, tt or {"error": "还没收到课表（在岛课表里导出备份后 POST /timetable）"})
        if path == "/timetable/today":
            tt = _timetable()
            today = datetime.now(TZ).date()
            return self._send(200, {"date": today.strftime("%Y-%m-%d"),
                                    "week": _week_of(tt, today) if tt else None,
                                    "courses": courses_on(today, tt)})
        if path == "/timetable/next":
            return self._send(200, {"next": next_course_from()})
        if path == "/metrics":
            return self._metrics(q)
        return self._send(404, {"error": "没有这个接口"})

    def do_POST(self):
        if not self._guard():
            return
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        # 体积上限：先掐掉超大请求（防"一个 100MB 的包把内存吃光"）
        try:
            _n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            _n = 0
        if _n > MAX_BODY:
            return self._send(413, {"error": f"请求太大，上限 {MAX_BODY // 1024}KB"})
        if not self._auth(q):
            return
        if path == "/ingest":
            return self._ingest(self._body())
        if path == "/timetable":
            body = self._body()
            raw = body.get("timetable") if isinstance(body, dict) and "timetable" in body else body
            if not isinstance(raw, dict) or "courses" not in raw:
                return self._send(400, {"error": "要传岛课表导出的那份 JSON（含 courses / periods）"})
            with db() as c:
                c.execute("INSERT INTO timetable(id, raw, source, updated_at) VALUES (1,?,?,?) "
                          "ON CONFLICT(id) DO UPDATE SET raw=excluded.raw, source=excluded.source, "
                          "updated_at=excluded.updated_at",
                          (json.dumps(raw, ensure_ascii=False), raw.get("source", ""), now_iso()))
            return self._send(200, {"ok": True, "courses": len(raw.get("courses", [])),
                                    "term_start": raw.get("termStartDate", "")})
        if path == "/feedback":
            b = self._body() or {}
            v = str(b.get("verdict") or "").strip().lower()
            if v not in ("good", "bad"):
                return self._send(400, {"ok": False, "error": "verdict 只能是 good / bad"})
            with db() as c:
                c.execute("INSERT INTO feedback(ts, verdict, band, note) VALUES (?,?,?,?)",
                          (now_iso(), v, str(b.get("band") or "")[:24], str(b.get("note") or "")[:200]))
            print(f"[feedback] {v}", flush=True)
            return self._send(200, {"ok": True, "verdict": v})
        if path == "/persona":
            body = self._body()
            if isinstance(body, dict) and body:
                CFG["persona"].update(body)
                with open(CFG_PATH, "w", encoding="utf-8") as f:
                    json.dump(CFG, f, ensure_ascii=False, indent=2)
                return self._send(200, {"ok": True, "persona": CFG["persona"]})
            return self._send(400, {"error": "body 要是一个对象"})
        if path == "/brief":
            kind = (q.get("kind") or ["brief_evening"])[0]
            rid, text = compose_brief(kind)
            return self._send(200, {"ok": True, "id": rid, "kind": kind, "text": text})
        if path == "/ack":
            body = self._body()
            body = body if isinstance(body, dict) else {}
            ids = body.get("ids") if body.get("ids") is not None else (
                [body["id"]] if body.get("id") is not None else [])
            ids = [int(i) for i in (ids or [])]
            term = (q.get("for") or [body.get("for") or "weixin"])[0]
            with db() as c:
                c.executemany("UPDATE reminders SET status='delivered', delivered_to=? WHERE id=?",
                              [(term, i) for i in ids])
            return self._send(200, {"ok": True, "acked": len(ids), "for": term})
        if path == "/channels":
            body = self._body()
            ch = CFG.setdefault("channels", {})
            for k in ("wecom_webhook", "wecom_corpid", "wecom_secret", "wecom_agentid", "wecom_touser"):
                if k in body:
                    ch[k] = str(body[k]).strip()
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(CFG, f, ensure_ascii=False, indent=2)
            return self._send(200, {"ok": True, "channels": {k: ("已设置" if v else "空") for k, v in ch.items()}})
        if path == "/push/register":
            body = self._body()
            name, url = (body.get("name") or "").strip(), (body.get("url") or "").strip()
            if not name or not url.startswith("http"):
                return self._send(400, {"error": "要 {name, url}"})
            with db() as c:
                c.execute("INSERT INTO terminals(name, last_seen, note) VALUES (?,?,?) "
                          "ON CONFLICT(name) DO UPDATE SET note=excluded.note, last_seen=excluded.last_seen",
                          (name, now_iso(), url))
            return self._send(200, {"ok": True, "note": "有主动提醒就 POST 到这个地址，请求头带 X-Token"})
        if path == "/ack":
            body = self._body()
            with db() as c:
                c.execute("UPDATE reminders SET status=? WHERE id=?", (body.get("status", "done"), body.get("id")))
            return self._send(200, {"ok": True})
        if path == "/remind":
            body = self._body()
            body = body if isinstance(body, dict) else {}
            text = str(body.get("text") or "").strip()
            at = str(body.get("at") or "").strip()
            daily = 1 if body.get("daily") else 0
            if not text or not at:
                return self._send(400, {"error": "需要 text 和 at（at 可为 HH:MM 或 ISO 时间）"})
            now = datetime.now(TZ)
            try:
                if len(at) <= 5:                       # HH:MM → 今天该时刻；已过则顺延明天
                    hh, mm = [int(x) for x in at.split(":")]
                    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if t <= now:
                        t = t + timedelta(days=1)
                else:
                    t = datetime.fromisoformat(at)
            except Exception as e:
                return self._send(400, {"error": f"时间格式不对：{at}（{e}）"})
            with db() as c:
                cur = c.execute("INSERT INTO scheduled(at_iso, text, daily, created_at) VALUES (?,?,?,?)",
                                (t.isoformat(), text, daily, now_iso()))
                rid = cur.lastrowid
            return self._send(200, {"ok": True, "id": rid, "at": t.isoformat(),
                                    "daily": bool(daily), "text": text})
        if path == "/chat":
            return self._chat(self._body())
        return self._send(404, {"error": "没有这个接口"})

    def _send_text(self, code, text):
        data = (text + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _mcu(self, query):
        """单片机上报口。参数：d 设备 / m 指标(可逗号) / v 值(可逗号) / u 单位 / t token。"""
        q = parse_qs(query)
        g = lambda k, i=0: ((q.get(k) or [""])[0] if i == 0 else (q.get(k) or [""])[i])
        tok = g("t") or self.headers.get("X-Token") or ""
        mcu_tok = (CFG.get("mcu") or {}).get("token") or CFG["token"]
        if tok not in (CFG["token"], mcu_tok):
            print(f"[mcu] {self.client_address[0]} token 不对", flush=True)
            return self._send_text(401, "err:token")
        dev = (g("d") or "").strip()
        # ⑤ 可选的校验和与序号（单片机稳一点）：
        #    c = 各字符 ASCII 之和 mod 256（C 里一行 for 就能算，比 CRC16 省事）
        #    s = 递增序号（中继/中枢用它去重，防丢包重发造成的重复）
        csum = (g("c") or "").strip()
        seq = (g("s") or "").strip()
        if csum:
            raw_line = f"{g('d')}{g('m')}{g('v')}"
            calc = sum(raw_line.encode()) % 256
            try:
                if int(csum) != calc:
                    print(f"[mcu] {self.client_address[0]} 校验和不符（给的 {csum}，算的 {calc}）", flush=True)
                    return self._send_text(400, "err:crc")
            except ValueError:
                return self._send_text(400, "err:crc")
        metrics = [x.strip() for x in (g("m") or "").split(",") if x.strip()]
        vals = [x.strip() for x in (g("v") or "").split(",")]
        unit = (g("u") or "").strip()
        if not dev or not metrics or not vals:
            return self._send_text(400, "err:params")
        items = []
        for i, m in enumerate(metrics[:8]):
            raw = vals[i] if i < len(vals) else vals[0]
            try:
                num = float(raw)
            except ValueError:
                num = raw          # 非数值也收（比如状态字符串），存 meta
            _m = {"seq": seq} if seq else {}
            items.append({"device": dev, "metric": m, "value": num if isinstance(num, (int, float)) else None,
                          "unit": unit, "source": "mcu", "meta": _m})
        print(f"[mcu] {dev} ← " + " ".join(f"{m}={v}" for m, v in zip(metrics, vals)), flush=True)
        # 直接复用 /ingest 的入库逻辑（它自己会回响应 —— 单片机只看 HTTP 200 就够了）
        return self._ingest(items)

    # ---- 各接口实现
    def _health(self):
        with db() as c:
            m = c.execute("SELECT COUNT(*) n FROM metrics").fetchone()["n"]
            r = c.execute("SELECT COUNT(*) n FROM reminders").fetchone()["n"]
        _r = CFG.get("rules") or {}
        return {"ok": True, "version": VERSION, "now": now_iso(), "metrics": m, "reminders": r,
                "code": code_fingerprint(),
                "rules": {"class_remind_minutes": _r.get("class_remind_minutes"),
                          "sit_continuous_minutes": _r.get("sit_continuous_minutes")},
                "uptime_note": "hub 在跑", "endpoints": ["/ingest", "/today", "/pending", "/persona",
                                                         "/devices", "/metrics", "/ack", "/chat", "/health"]}

    def _ingest(self, body):
        ok, skipped = ingest_items(body)
        return self._send(200, {"ok": True, "accepted": ok, "skipped": skipped,
                                "total": len(body if isinstance(body, list) else [body])})

    def _today(self, q):
        term = (q.get("terminal") or [""])[0]
        self._touch(term)
        with db() as c:
            rows = c.execute("SELECT * FROM reminders WHERE day=? ORDER BY id DESC LIMIT 20", (today_str(),)).fetchall()
            rem = [dict(r) for r in rows]
            devs = self._devices()
            sleep = _latest_metric(c, "sleep.total_minutes")
            screen, _ = _peak_metric(c, today_str(), "screen.active_minutes")
        return self._send(200, {
            "date": today_str(), "now": now_iso(), "terminal": term,
            "persona": CFG["persona"],
            "greeting": persona_line("info") + ("今天还没什么要注意的。" if not rem else "今天的提醒在下面。"),
            "reminders": rem,
            "latest": {"sleep_minutes": (sleep or {}).get("value"), "screen_minutes_today": screen},
            "digest": daily_digest(),
            "classes_today": courses_on(datetime.now(TZ).date()),
            "next_class": with_day_hint(next_course_from()),
            "calendar_today": calendar_today(),
            "care": care_now(),          # ★ 新增数据源（天气/在听/电量/闹钟/快递/温湿度/游戏）
            "devices": devs,
        })

    def _pending(self, q):
        term = (q.get("for") or q.get("terminal") or [""])[0]
        self._touch(term)
        try:
            limit = max(1, min(20, int((q.get("limit") or ["20"])[0])))
        except Exception:
            limit = 20
        peek = (q.get("peek") or ["0"])[0].lower() not in ("0", "", "false", "no")
        with db() as c:
            rows = c.execute("SELECT * FROM reminders WHERE status='new' ORDER BY id ASC LIMIT ?",
                             (limit,)).fetchall()
            if rows and term and not peek:
                ids = [r["id"] for r in rows]
                c.executemany("UPDATE reminders SET status='delivered', delivered_to=? WHERE id=?",
                              [(term, i) for i in ids])
        return self._send(200, {"terminal": term, "count": len(rows), "items": [dict(r) for r in rows]})

    def _devices(self):
        with db() as c:
            rows = c.execute(
                "SELECT device, COUNT(*) n, MAX(ts) last, GROUP_CONCAT(DISTINCT metric) metrics "
                "FROM metrics GROUP BY device ORDER BY last DESC").fetchall()
            terms = c.execute("SELECT name, last_seen FROM terminals ORDER BY last_seen DESC").fetchall()
        return {"data_sources": [dict(r) for r in rows], "terminals": [dict(r) for r in terms]}

    def _metrics(self, q):
        where, args = ["1=1"], []
        for key, col in (("device", "device"), ("metric", "metric"), ("day", "day")):
            v = (q.get(key) or [""])[0]
            if v:
                where.append(f"{col}=?")
                args.append(v)
        since = (q.get("since") or [""])[0]
        if since:
            where.append("ts>=?")
            args.append(since)
        limit = min(int((q.get("limit") or ["200"])[0]), 2000)
        with db() as c:
            rows = c.execute(f"SELECT * FROM metrics WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?",
                             (*args, limit)).fetchall()
        return self._send(200, {"count": len(rows), "items": [dict(r) for r in rows]})

    def _chat(self, body):
        """终端发来的对话。v0.1 走确定性回答；接上 LLM 后在此换成模型（人设从 /persona 取）。"""
        text = (body.get("text") or "").strip()
        term = body.get("terminal") or ""
        self._touch(term)
        if not text:
            return self._send(400, {"error": "text 为空"})
        with db() as c:
            c.execute("INSERT INTO chats(ts, terminal, role, text) VALUES (?,?,?,?)", (now_iso(), term, "user", text))
        p = CFG["persona"]
        ctx, _dropped = llm_context()          # ★ 给模型看的只有脱敏版
        items = analyze()
        lines = "；".join(i["text"] for i in items[:3]) or "没什么要提醒的"
        reply = f"{p['self_call']}在。{lines}。"
        if re.search(r"睡|作息", text):
            s = None
            with db() as c:
                s = _latest_metric(c, "sleep.total_minutes")
            reply = (f"你昨晚睡了 {int(s['value']) // 60} 小时 {int(s['value']) % 60} 分。"
                     if s and s.get("value") else "还没拿到你的睡眠数据呢。")
        elif re.search(r"今天|干啥|要做什么", text):
            reply = "今天要做：" + (lines if lines != "没什么要提醒的" else "暂时没有记录的待办。")
        with db() as c:
            c.execute("INSERT INTO chats(ts, terminal, role, text) VALUES (?,?,?,?)", (now_iso(), term, "persona", reply))
        return self._send(200, {"ok": True, "reply": reply, "persona": p})

    def _page(self):
        """极简状态页：浏览器打开就能看今天（也算是又一个终端）。"""
        try:
            with db() as c:
                rem = c.execute("SELECT * FROM reminders WHERE day=? ORDER BY id DESC LIMIT 10",
                                (today_str(),)).fetchall()
                devs = c.execute("SELECT device, MAX(ts) last, COUNT(*) n FROM metrics GROUP BY device").fetchall()
        except Exception:
            rem, devs = [], []
        p = CFG["persona"]
        try:
            _c = courses_on(datetime.now(TZ).date())
        except Exception:
            _c = []
        cls = "".join(f"<li>{c['start']}–{c['end']} <b>{c['name']}</b> {c['room']}</li>" for c in _c) \
            or "<li class=muted>当天没有课（或还没同步课表）</li>"
        rows = "".join(
            f"<li><b>{r['level']}</b> {r['text'].replace(chr(10), '<br>')}</li>" for r in rem) or "<li>今天还没有提醒</li>"
        dvs = "".join(f"<tr><td>{d['device']}</td><td>{d['last']}</td><td>{d['n']}</td></tr>" for d in devs) \
            or "<tr><td colspan=3>还没有任何设备上报</td></tr>"
        html = f"""<!doctype html><meta charset=utf-8><title>hub · {p['name']}</title>
<style>body{{font:14px/1.7 -apple-system,"PingFang SC",sans-serif;background:#0d0e10;color:#e6e6e6;padding:24px;max-width:760px;margin:auto}}
h1{{font-size:18px}} .muted{{color:#8b9099}} li{{margin:6px 0}} table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #2a2e34;padding:6px 4px;text-align:left;font-size:13px}}</style>
<h1>{p['name']} · 中枢状态</h1>
<p class=muted>v{VERSION} · {now_iso()} · 数据只存在这台服务器上</p>
<h3>今天课程</h3><ul>{cls}</ul>
<h3>今天的提醒</h3><ul>{rows}</ul>
<h3>数据源（各设备最近上报）</h3><table><tr><th>设备</th><th>最近</th><th>条数</th></tr>{dvs}</table>
<p class=muted>给 AI 的数据已脱敏（<a href="/llm-preview">看会发给模型的内容</a>（需带 X-Token 请求头访问））：不含通知原文 / 日程标题 / App 名 / 分钟级时间</p>
<p class=muted>接口：POST /ingest ｜ GET /today ｜ GET /pending ｜ GET /persona ｜ GET /devices（都要 token）</p>"""
        return self._send(200, html.replace("", CFG["token"]), "text/html; charset=utf-8")



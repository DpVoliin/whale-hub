# ----------------------------------------------------------------- 配置 / 存储
def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))          # 深拷贝默认值
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"[cfg] 读取失败，用默认值：{e}", flush=True)
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(18)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


CFG = load_cfg()
STATE_PATH = os.path.join(BASE, "state.json")     # ⑥ 调度状态落盘（重启不重复/不漏发）


def ensure_mcu_token():
    """⑧ 单片机用独立 token（别再和主钥匙共用一把）。
    没有就自动生成一个，写回 hub.json —— 固件被抄走也不会泄露主 token。"""
    mcu = CFG.setdefault("mcu", {})
    if mcu.get("token"):
        return mcu["token"]
    tok = secrets.token_urlsafe(18)
    mcu["token"] = tok
    mcu["note"] = "设备 → 内网中继 → 中枢(HTTPS)；别把明文端口裸在公网"
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(CFG, f, ensure_ascii=False, indent=2)
        print(f"[mcu] 已生成独立单片机 token：{tok[:6]}…{tok[-4:]}（在 hub.json 的 mcu.token）", flush=True)
    except Exception as e:
        print(f"[mcu] token 写盘失败：{e}", flush=True)
    return tok


def load_state():
    """⑥ 调度状态落盘：重启后不会把今天已经发过的简报再发一遍。"""
    try:
        return json.loads(pathlib.Path(STATE_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {"morning": "", "evening": "", "bed": "", "weather_at": None}


def save_state(st):
    try:
        pathlib.Path(STATE_PATH).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


BACKUP_KEEP = 7          # 备份滚存天数
LOG_ROTATE_MB = 50       # 单文件超过多少 MB 就轮转


def backups_dir():
    d = os.path.join(BASE, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def dash_html():
    """★ 只读数据页：看数据，不改配置（配置仍走 CLI/文件 —— 刻意不做管理后台）。

    为什么只读：管理后台意味着"浏览器里能改中枢状态"，那要另建一套权限与 CSRF 防线；
    而这里的数据本来就都在你自己机器上，**看的价值 > 改的价值**。
    鉴权与 API 完全一致（同一个 X-Token），不因为"是个网页"就放松。
    """
    import html as _h
    def esc(x):
        return _h.escape(str(x if x is not None else "—"))
    rows = []
    try:
        h = source_health()
        for s, v in h.items():
            cls = "" if v["verdict"] == "ok" else "warn"
            rows.append("<tr class=%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                        % (cls, esc(s), esc(v["verdict"]), esc(v.get("today_n")), esc(v.get("note"))))
    except Exception as e:
        rows.append("<tr><td colspan=4>健康度算不出：%s</td></tr>" % esc(str(e)[:80]))
    eps = []
    try:
        for e2 in episodes_recent(days=7, limit=12):
            eps.append("<li><span class=dim>%s</span> %s</li>" % (esc(e2["day"][5:]), esc(e2["summary"])))
    except Exception:
        pass
    ext = []
    for s in EXT["sources"]:
        ext.append("<li>%s → 上次入库 %s 条%s</li>"
                   % (esc(s["name"]), esc(s["last_n"]),
                      (" ｜ <span class=warn>%s</span>" % esc(s["last_err"])) if s["last_err"] else ""))
    dec = []
    try:
        with db() as c:
            for d in c.execute("SELECT ts, band, gap_sec, reason FROM decisions "
                               "ORDER BY id DESC LIMIT 8").fetchall():
                dec.append("<li><span class=dim>%s</span> %s ｜ %s 分钟 ｜ %s</li>"
                           % (esc(d["ts"][11:16]), esc(d["band"]),
                              esc(round((d["gap_sec"] or 0) / 60.0)), esc(d["reason"])))
    except Exception:
        pass
    page = """<!doctype html><html lang=zh><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>鲸鲸 · 看数据</title><style>
body{background:#0e1116;color:#dfe6ee;font:14px/1.6 -apple-system,"Segoe UI",sans-serif;margin:0;padding:24px}
h1{font-size:17px;margin:0 0 4px}h2{font-size:14px;margin:22px 0 8px;color:#8ea1b5;font-weight:600}
table{border-collapse:collapse;width:100%}td,th{padding:6px 10px;border-bottom:1px solid #1c2431;text-align:left}
th{color:#8ea1b5;font-weight:600}.dim{color:#7c8b9c}.warn{color:#f0a35e}
ul{margin:0;padding-left:18px}li{margin:2px 0}
.tag{display:inline-block;padding:1px 7px;border:1px solid #2b3646;border-radius:9px;color:#8ea1b5;font-size:12px}
</style><h1>鲸鲸 · 看数据 <span class=tag>只读</span></h1>
<div class=dim>__DATE__ ｜ 中枢 __CODE__ ｜ 这个页面只看不改（配置走 CLI/文件）</div>
<h2>数据源健康度</h2><table><tr><th>源</th><th>状态</th><th>今天条数</th><th>说明</th></tr>__ROWS__</table>
<h2>她最近说过什么（情节记忆）</h2><ul>__EPS__</ul>
<h2>外挂扩展</h2><ul>__EXT__</ul>
<h2>决策日志（为什么这么频繁）</h2><ul>__DEC__</ul>
"""
    # ⚠️ 别用 %-格式化：CSS 里全是 % 和 {}，会撞成 "unsupported format character"
    return (page.replace("__DATE__", esc(today_str()))
                .replace("__CODE__", esc(code_fingerprint()))
                .replace("__ROWS__", "".join(rows))
                .replace("__EPS__", "".join(eps) or "<li class=dim>（还没有）</li>")
                .replace("__EXT__", "".join(ext) or "<li class=dim>（没装扩展）</li>")
                .replace("__DEC__", "".join(dec) or "<li class=dim>（还没有）</li>"))
    """把 hub.db + hub.json 打包成一份带日期的快照（私人系统没有 DBA，唯一的保险就是它）。"""
def make_backup(tag=None):
    import tarfile
    tag = tag or datetime.now(TZ).strftime("%Y%m%d")
    out = os.path.join(backups_dir(), "hub-%s.tgz" % tag)
    try:
        with tarfile.open(out, "w:gz") as tf:
            for f in ("hub.db", "hub.json"):
                p = os.path.join(BASE, f)
                if os.path.isfile(p):
                    tf.add(p, arcname=f)
        # 滚存：只留最近 N 份
        fs = sorted(f for f in os.listdir(backups_dir()) if f.startswith("hub-") and f.endswith(".tgz"))
        for old in fs[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(backups_dir(), old))
            except Exception:
                pass
        try:
            audit("backup", target=os.path.basename(out),
                  note="%.1f MB" % (os.path.getsize(out) / 1048576.0))
        except Exception:
            pass
        return out
    except Exception as e:
        print(f"[backup] 失败：{str(e)[:80]}", flush=True)
        return None


def rotate_log(path=None):
    """日志轮转：超过阈值就压缩归档并清空 —— 否则会涨到几百 MB（本机真发生过 209MB）。"""
    import gzip
    import shutil
    hours = [os.path.join(BASE, "hub.log"), os.path.join(BASE, "logs", "hub.log")]
    done = []
    for h in hours:
        try:
            if not os.path.isfile(h) or os.path.getsize(h) < LOG_ROTATE_MB * 1024 * 1024:
                continue
            arc = h + "." + datetime.now(TZ).strftime("%Y%m%d%H%M") + ".gz"
            with open(h, "rb") as fi, gzip.open(arc, "wb") as fo:
                shutil.copyfileobj(fi, fo)
            open(h, "w").close()                      # 截断（进程还在写同一个 fd，用 truncate 不影响）
            done.append(os.path.basename(arc))
        except Exception as e:
            print(f"[rotate] {h}: {str(e)[:60]}", flush=True)
    return done


def selfcheck(silent_hours=24):
    """自检：数据源还活着吗 / 她多久没说话了。
    **沉默故障是最难发现的** —— 所以自检的结论要走推送，让人能看见。"""
    out = {"ok": True, "issues": []}
    try:
        h = source_health()
        bad = [s for s, v in h.items() if v["verdict"] in ("missing", "stale")]
        if bad:
            out["ok"] = False
            out["issues"].append("数据源异常：" + "、".join(bad))
    except Exception as e:
        out["issues"].append("健康度算不出：" + str(e)[:40])
    try:
        with db() as c:
            r = c.execute("SELECT MAX(created_at) m FROM reminders").fetchone()
        last = r["m"] if r else None
        if last:
            hrs = (datetime.now(TZ) - datetime.fromisoformat(last).astimezone(TZ)).total_seconds() / 3600.0
            out["last_said_hours"] = round(hrs, 1)
            if hrs > silent_hours:
                out["ok"] = False
                out["issues"].append(f"已经 {hrs:.0f} 小时没说过话了")
    except Exception as e:
        out["issues"].append("查不到最近发言：" + str(e)[:40])
    return out


def episode_add(kind, summary, sig=None, day=None):
    """记一条**情节**（她说过什么 / 发生过什么）。只落本地库。

    为什么这是"记忆"而不是日志：它不是给排障用的，是给**她**用的 ——
    以后她能说"你上周说想早睡"，靠的就是这里。
    """
    try:
        with db() as c:
            ts = now_iso()
            dy = day or today_str()
            c.execute("INSERT INTO episodes(ts, day, kind, summary, sig) VALUES (?,?,?,?,?)",
                      (ts, dy, str(kind)[:24], str(summary or "")[:200],
                       json.dumps(sig or {}, ensure_ascii=False)))
            try:      # 同步进 FTS5 索引（失败也不影响主流程：索引可重建）
                c.execute("INSERT INTO episodes_fts(summary, kind, day, ts) VALUES (?,?,?,?)",
                          (str(summary or "")[:200], str(kind)[:24], dy, ts))
            except Exception:
                pass
    except Exception as e:
        print(f"[epi] 写入失败：{str(e)[:60]}", flush=True)


def ensure_fts():
    """索引是可重建的**派生数据**：分词器变了就重建（不动原始 episodes）。
    这样以后想换检索方案（甚至换向量）随时能重算，原始存档永远不动。"""
    try:
        with db() as c:
            row = c.execute("SELECT sql FROM sqlite_master WHERE name='episodes_fts'").fetchone()
            if row and "trigram" not in (row["sql"] or ""):
                c.execute("DROP TABLE episodes_fts")
                print("[fts] 分词器变了 → 重建索引", flush=True)
                row = None
            if not row:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5("
                          "summary, kind UNINDEXED, day UNINDEXED, ts UNINDEXED, tokenize='trigram')")
            n = c.execute("SELECT COUNT(*) n FROM episodes_fts").fetchone()["n"]
            m = c.execute("SELECT COUNT(*) n FROM episodes").fetchone()["n"]
            if n < m:      # 补齐（含历史情节）
                c.execute("INSERT INTO episodes_fts(summary, kind, day, ts) "
                          "SELECT summary, kind, day, ts FROM episodes WHERE id > "
                          "(SELECT COALESCE(MAX(1),0) FROM episodes_fts)".replace("1", "(SELECT COUNT(*) FROM episodes_fts)"), ())
                print(f"[fts] 索引补齐 {m - n} 条", flush=True)
    except Exception as e:
        print(f"[fts] 建索引失败（不影响其他功能）：{str(e)[:70]}", flush=True)


def episode_search(q, limit=8):
    """★ FTS5 + BM25 检索情节（按需检索那条路；`episodes_recent` 仍是"最近几条"那条路）。"""
    if not (q or "").strip():
        return []
    q = q.strip()
    try:
        with db() as c:
            # ⚠️ trigram 分词器的硬限制：**查询词少于 3 个字符匹配不到**（中文两字词全废）。
            #    所以：≥3 字走 BM25 排序，<3 字退回 LIKE 子串匹配（我们只有几千条，够快）。
            if len(q) >= 3:
                rows = c.execute(
                    "SELECT summary, kind, day, bm25(episodes_fts) AS rank FROM episodes_fts "
                    "WHERE episodes_fts MATCH ? ORDER BY rank LIMIT ?",
                    (q, int(limit))).fetchall()
            else:
                rows = c.execute(
                    "SELECT summary, kind, day, 0.0 AS rank FROM episodes "
                    "WHERE summary LIKE ? ORDER BY id DESC LIMIT ?",
                    ("%" + q + "%", int(limit))).fetchall()
        return [{"summary": r["summary"], "kind": r["kind"], "day": r["day"]} for r in rows]
    except Exception as e:
        print(f"[fts] 检索失败（不影响其他功能）：{str(e)[:60]}", flush=True)
        return []


def decision_log(kind, gap_sec=None, reason="", material=None, said=None, band=None):
    """★ 结构化决策日志：把"为什么这么决定"落成**可回放的字段**，而不是只写一行中文理由。

    为什么必须有（外部评审点出来的真问题）：只记文本理由 = 一个月后完全回放不了，
    调参就永远停在"改几个阈值 → 用几天 → 感觉不对 → 再改"。
    """
    try:
        with db() as c:
            c.execute("INSERT INTO decisions(ts, day, kind, gap_sec, reason, material, said, band) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (now_iso(), today_str(), str(kind)[:24],
                       int(gap_sec) if gap_sec is not None else None,
                       str(reason or "")[:200],
                       int(material) if material is not None else None,
                       int(said) if said is not None else None,
                       str(band or "")[:24]))
    except Exception as e:
        print(f"[decision] 写入失败：{str(e)[:60]}", flush=True)


def episodes_recent(days=14, limit=8, kind=None):
    """最近的情节（新→旧）。**去掉措辞高度重复的**，避免喂给模型一堆一样的话。"""
    start = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    try:
        with db() as c:
            sql = "SELECT ts, day, kind, summary, sig FROM episodes WHERE day >= ?"
            args = [start]
            if kind:
                sql += " AND kind=?"
                args.append(kind)
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(int(limit) * 3)
            rows = c.execute(sql, args).fetchall()
    except Exception:
        return []
    seen = []
    for r in rows:
        s = r["summary"] or ""
        if not s:
            continue
        # 用字符 3-gram 去重（措辞不同但"同一件事"的，只留最新一条）
        if any(_same_fact(s, x) for x in seen):
            continue
        seen.append(s)
        out.append({"ts": r["ts"], "day": r["day"], "kind": r["kind"], "summary": s,
                    "sig": r["sig"]})     # ★ 必须带上：复盘靠它读"上期结论"
        if len(out) >= limit:
            break
    return out


def _same_fact(a, b):
    """两条摘要是不是"同一件事"（零依赖、无分词：字符 3-gram Jaccard）。"""
    def sh(t):
        import re as _re
        s = _re.sub(r"[\s\W_]+", "", t or "", flags=_re.UNICODE)
        return {s[i:i + 3] for i in range(max(0, len(s) - 2))} if len(s) >= 3 else {s}
    x, y = sh(a), sh(b)
    if not x or not y:
        return False
    return len(x & y) / float(len(x | y)) >= 0.5


def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        -- 迁移：删掉历史遗留的 UNIQUE 索引（它会吃数据）
        DROP INDEX IF EXISTS idx_metrics_dedup;
        CREATE TABLE IF NOT EXISTS metrics(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,               -- ISO8601（带 +08:00）
            day TEXT NOT NULL,              -- YYYY-MM-DD（本地日，便于按天聚合）
            device TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            unit TEXT DEFAULT '',
            source TEXT DEFAULT '',
            confidence REAL DEFAULT 1.0,
            meta TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_day   ON metrics(day, metric);
        CREATE INDEX IF NOT EXISTS idx_metrics_dev   ON metrics(device, ts);
        -- 注意：这里**不能**用 UNIQUE(device, metric, ts)！
        -- 踩过的坑：一个批次里"各 App 使用时长"的记录时刻完全相同 → 被当成重复互相覆盖，
        --           B站 96 分钟、微信 42 分钟全丢了，只剩最后一条。去重交给 ingest 里的逻辑做。
        CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON metrics(device, metric, day);

        CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,             -- brief_morning / brief_evening / alert
            level TEXT DEFAULT 'info',      -- info / warn / urgent
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT DEFAULT 'new',      -- new / delivered / done
            delivered_to TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_rem_day ON reminders(day, status);

        CREATE TABLE IF NOT EXISTS chats(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, terminal TEXT, role TEXT, text TEXT
        );

        CREATE TABLE IF NOT EXISTS timetable(
            id INTEGER PRIMARY KEY CHECK(id = 1),
            raw TEXT NOT NULL,              -- 岛课表导出的整份 JSON（原样存，不改造）
            source TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fired(
            key TEXT PRIMARY KEY,           -- 去重键：当天同一件事只提醒一次
            at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scheduled(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            at_iso TEXT NOT NULL,           -- 到点时间（ISO，带时区）
            text TEXT NOT NULL,             -- 到点说什么
            daily INTEGER DEFAULT 0,        -- 1 = 每天重复
            fired_at TEXT,                  -- 非重复的触发后写这里
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, day TEXT, kind TEXT, gap_sec INTEGER,
            reason TEXT, material INTEGER, said INTEGER, band TEXT);
        CREATE TABLE IF NOT EXISTS seen_events(
            event_id TEXT PRIMARY KEY, ts TEXT);
        CREATE TABLE IF NOT EXISTS episodes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, day TEXT, kind TEXT, summary TEXT, sig TEXT);
        -- ★ FTS5：几千条量级下 BM25 比向量库更快更准，而且**零新依赖**（实测本机 sqlite 3.53 可用）
        CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
            summary, kind UNINDEXED, day UNINDEXED, ts UNINDEXED,
            tokenize='trigram');   -- ★ 必须 trigram：默认分词器把整句中文当一个词，"屏幕"搜不到
        CREATE TABLE IF NOT EXISTS feedback(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, verdict TEXT, band TEXT, note TEXT, consumed INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS terminals(
            name TEXT PRIMARY KEY,
            last_seen TEXT,
            note TEXT DEFAULT ''
        );

        -- ★ 审计日志：只记「动作 + 对象 + 结果」，**不记数据内容**
        --   （鉴权失败 / 配置修改 / 导出与备份 / 扩展加载报错 / 配对 / token 轮换）
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            day TEXT NOT NULL,
            action TEXT NOT NULL,               -- auth_fail / config_change / export / erase / backup / ext_error / pair / login ...
            target TEXT DEFAULT '',             -- 对象（接口路径 / 配置项 / 文件名）—— 不含数据内容
            actor TEXT DEFAULT '',              -- 来源标识（IP 或终端名）
            result TEXT DEFAULT 'ok',           -- ok / denied / error
            note TEXT DEFAULT ''                -- 一句短说明（不得写入原文/数值）
        );
        CREATE INDEX IF NOT EXISTS idx_audit_day ON audit(day, id);

        -- ★ 一次性配对码：MCU 中继/新设备拿码换 token，用过即废（防长期明文口令）
        CREATE TABLE IF NOT EXISTS pair_codes(
            code TEXT PRIMARY KEY,
            device TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            used_by TEXT DEFAULT ''
        );
        """)


def now_iso():
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def code_fingerprint():
    """本文件内容的 md5 前 12 位 —— 用来从外部确认"服务器跑的是哪一版"。"""
    import hashlib
    # 正常部署：读自身文件即可。
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except (OSError, ValueError):
        pass
    # zipapp / 冻结包：__file__ 指向压缩包内部，磁盘上不存在 → 从压缩包里读。
    try:
        import zipfile
        loader = getattr(sys.modules.get("__main__"), "__loader__", None)
        archive = getattr(loader, "archive", None)
        if archive:
            with zipfile.ZipFile(archive) as z:
                for n in z.namelist():
                    if n.endswith("hub.py"):
                        return hashlib.md5(z.read(n)).hexdigest()[:12]
    except Exception:
        pass
    return "?"


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")




def band_stats(min_n=4):
    """分桶 Thompson：把反馈按"场景桶"（星期×时段）分组估 p(接受)。

    文献依据 EOPA arXiv:2608.04416 —— 反馈稀疏时**先分桶再决策**，
    但每桶样本太少就别信它（min_n 以下退回全局后验，避免"一次运气就改阈值"）。
    """
    out = {"global": {}, "bands": {}, "min_n": min_n}
    try:
        with db() as c:
            rows = c.execute("SELECT band, verdict, COUNT(*) n FROM feedback GROUP BY band, verdict").fetchall()
        tot = {"ok": 0, "bad": 0}
        for r in rows:
            b = str(r["band"] or "(无桶)")
            v = str(r["verdict"] or "")
            k = "ok" if v in ("up", "1", "good", "yes") else ("bad" if v in ("down", "0", "bad", "no") else None)
            if not k:
                continue
            d = out["bands"].setdefault(b, {"ok": 0, "bad": 0, "n": 0})
            d[k] += int(r["n"]); d["n"] += int(r["n"])
            tot[k] += int(r["n"])
        for d in out["bands"].values():
            d["p_accept"] = round((1 + d["ok"]) / (2 + d["ok"] + d["bad"]), 3)
            d["reliable"] = d["n"] >= min_n
        out["global"] = {"ok": tot["ok"], "bad": tot["bad"], "n": tot["ok"] + tot["bad"],
                         "p_accept": round((1 + tot["ok"]) / (2 + tot["ok"] + tot["bad"]), 3)}
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:60])
    return out


# ----------------------------------------------------------------- 审计日志
# 设计红线：**只记动作与对象，不记数据内容** —— 它要能安全地留在库里、
# 也能直接给人看（`hubctl audit`），所以绝不允许写入通知原文/数值/坐标。
AUDIT_MAX = 200           # `hubctl audit` 默认看最近 N 条


def audit(action, target="", actor="", result="ok", note=""):
    """写一条审计。**任何情况下都不许影响主流程**（失败只打印）。"""
    try:
        with db() as c:
            c.execute("INSERT INTO audit(ts, day, action, target, actor, result, note) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (now_iso(), today_str(), str(action)[:32], str(target)[:120],
                       str(actor)[:64], str(result)[:16], str(note)[:120]))
    except Exception as e:
        print("[audit] 写入失败：%s" % str(e)[:70], flush=True)


def audit_recent(limit=AUDIT_MAX, action=None, day=None):
    """读审计（给 hubctl / 管理页用）。"""
    where, args = ["1=1"], []
    if action:
        where.append("action=?")
        args.append(action)
    if day:
        where.append("day=?")
        args.append(day)
    with db() as c:
        rows = c.execute("SELECT * FROM audit WHERE %s ORDER BY id DESC LIMIT ?"
                         % " AND ".join(where), (*args, int(limit))).fetchall()
    return [dict(r) for r in rows]


def audit_stats(days=7):
    """按动作汇总最近 N 天（管理页一眼看趋势）。"""
    out = {"by_action": {}, "auth_fail": 0, "config_change": 0, "total": 0}
    try:
        since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
        with db() as c:
            for r in c.execute("SELECT action, COUNT(*) n FROM audit WHERE day>=? "
                               "GROUP BY action ORDER BY n DESC", (since,)).fetchall():
                out["by_action"][r["action"]] = r["n"]
                out["total"] += r["n"]
            for k in ("auth_fail", "config_change"):
                out[k] = out["by_action"].get(k, 0)
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:60])
    return out


# ----------------------------------------------------------------- 一次性配对码
# 场景：单片机/新设备不方便长期存一把明文口令 → 先在可信侧生成一个短码，
#       设备用它换一次 token，**码用过即废**（默认 15 分钟过期）。
PAIR_TTL_MIN = 15
_PAIR_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"      # 去掉 0/O/1/I/L 这些会读错的


def pair_new(device="", ttl_min=PAIR_TTL_MIN):
    """生成一个一次性配对码（明文口令本身不出现在码里）。"""
    code = "-".join("".join(secrets.choice(_PAIR_ALPHABET) for _ in range(4)) for _ in range(2))
    now = datetime.now(TZ)
    exp = now + timedelta(minutes=max(1, int(ttl_min)))
    with db() as c:
        # 顺手清掉过期未用的（不留垃圾）
        try:
            c.execute("DELETE FROM pair_codes WHERE used_at IS NULL AND expires_at < ?", (now.isoformat(),))
        except Exception:
            pass
        c.execute("INSERT INTO pair_codes(code, device, created_at, expires_at) VALUES (?,?,?,?)",
                  (code, str(device or "")[:40], now.isoformat(), exp.isoformat()))
    audit("pair_new", target=str(device or "(任意设备)")[:60],
          note="码 %s**… 有效至 %s" % (code[:4], exp.strftime("%H:%M")))
    return {"code": code, "device": device, "expires_at": exp.isoformat(),
            "ttl_minutes": int(ttl_min)}


def pair_claim(code, device, actor=""):
    """用配对码换 token。**一次性**：第二次用同一个码会被拒（used_at 已写）。

    返回 (ok, 结果 dict)。ok=False 时 dict 里是 err 前缀，方便单片机直接读。
    """
    code = str(code or "").strip().upper()
    device = str(device or "").strip()
    if not code or not device:
        return False, {"err": "params"}
    now = datetime.now(TZ)
    try:
        with db() as c:
            row = c.execute("SELECT * FROM pair_codes WHERE code=?", (code,)).fetchone()
            if not row:
                audit("pair_claim", target=device[:60], actor=actor, result="denied", note="码不存在")
                return False, {"err": "badcode"}
            if row["used_at"]:
                audit("pair_claim", target=device[:60], actor=actor, result="denied", note="码已被用过")
                return False, {"err": "used"}
            try:
                if datetime.fromisoformat(row["expires_at"]) < now:
                    audit("pair_claim", target=device[:60], actor=actor, result="denied", note="码已过期")
                    return False, {"err": "expired"}
            except Exception:
                pass
            if row["device"] and row["device"] != device:
                audit("pair_claim", target=device[:60], actor=actor, result="denied",
                      note="码已绑定 %s" % row["device"][:40])
                return False, {"err": "device_mismatch"}
            c.execute("UPDATE pair_codes SET used_at=?, used_by=? WHERE code=? AND used_at IS NULL",
                      (now.isoformat(), device[:60], code))
            if c.total_changes == 0:        # 并发下被别人抢先用了 → 一样算已用
                return False, {"err": "used"}
    except Exception as e:
        return False, {"err": "db:" + type(e).__name__}
    tok = ensure_mcu_token()
    audit("pair_claim", target=device[:60], actor=actor, note="换到独立 MCU token（码已作废）")
    return True, {"ok": True, "device": device, "token": tok,
                  "note": "此码已作废；token 请存到设备侧，别再存码"}


def pair_list(limit=20):
    with db() as c:
        rows = c.execute("SELECT code, device, created_at, expires_at, used_at, used_by "
                         "FROM pair_codes ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["code"] = str(d["code"])[:4] + "**"          # 列表里不打印完整码（它本身是凭据）
        out.append(d)
    return out

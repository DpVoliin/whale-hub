#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hubctl —— 鲸鲸中枢的命令行工具

放在中枢同目录（默认 /root/hub），直接读 SQLite，**不依赖 HTTP 也不依赖中枢在跑**。
出问题（服务挂了 / 想核对数据）时最有用。

常用：
    hubctl status                     总览：数据量/设备/队列/库大小/最近上报
    hubctl metrics -n 20              最近 20 条数据
    hubctl metrics -m app.usage_minutes -d 2026-09-21
    hubctl devices                    各设备 + 最后上报时间 + 条数
    hubctl reminders [--all]          提醒队列（默认只看待发）
    hubctl scheduled                  定点提醒（含每天的）
    hubctl timetable                  课表：今天几节 / 下一节
    hubctl chats -n 10                对话记录
    hubctl stats                      今天/近 7 天的概览（屏幕/睡眠/游戏/音乐/订单）
    hubctl sql "SELECT ..."           只读 SQL（只允许 SELECT/PRAGMA）

导出 / 导入：
    hubctl dump -o all.json           全库导出（JSON，含所有表）
    hubctl dump -o m.csv -m screen.active_minutes     按指标导出 CSV
    hubctl dump -o all.json --redact  脱敏导出（去掉原文/曲名/数值明细外的个人内容，适合给人看）
    hubctl load all.json --dry-run    先演练：能看到"将新增多少条"
    hubctl load all.json              合并导入（只增不删，重复的按主键覆盖）
    hubctl backup                     备份库到 hub.db.bak-<时间>
    hubctl vacuum                     压缩数据库

设计原则：
  · **默认只读**；写操作（load / restore / vacuum）必须显式敲
  · load 是**合并**语义：绝不删你已有的数据，重复的按主键覆盖
  · dump --redact 会去掉：通知原文、曲名、商品/订单细节、对话文本 —— 可安全外发
"""
import argparse
import csv
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

# realpath 会解开软链（/usr/local/bin/hubctl → /root/hub/hubctl.py），这样库目录才对
HERE = os.path.dirname(os.path.realpath(__file__))
# 库位置优先级与中枢一致：WHALE_DB → $WHALE_HOME/hub.db → 脚本旁边（旧部署）
def _resolve_db():
    import os as _o
    if _o.getenv("WHALE_DB"):
        return _o.environ["WHALE_DB"]
    if _o.getenv("WHALE_HOME"):
        return _o.path.join(_o.environ["WHALE_HOME"], "hub.db")
    return os.path.join(HERE, "hub.db")


DB = _resolve_db()
TZ = timezone(timedelta(hours=8))
REDACT_KEYS = ("raw", "title", "artist", "text", "note", "app")


# ---------------------------------------------------------------- 基础设施
def db():
    if not os.path.exists(DB):
        sys.exit(f"找不到数据库：{DB}（用 WHALE_DB 指定）")
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def tables(c):
    return [r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def cols(c, t):
    return [r["name"] for r in c.execute(f"PRAGMA table_info({t})")]


def has(c, t):
    return t in tables(c)


def hr(title):
    print(f"\n=== {title} ===")


def fmt_ts(ts):
    return (ts or "")[:19].replace("T", " ")


def human(n):
    return f"{n:,}"


# ---------------------------------------------------------------- 各命令
def cmd_status(a):
    c = db()
    hr("中枢数据总览")
    size = os.path.getsize(DB)
    print(f"  库文件      {DB}")
    print(f"  大小        {size / 1024 / 1024:.2f} MB")
    for t in tables(c):
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {human(n):>10} 行")

    if has(c, "metrics"):
        row = c.execute("SELECT COUNT(DISTINCT device) d, COUNT(DISTINCT metric) m, MIN(ts) a, MAX(ts) b "
                        "FROM metrics").fetchone()
        print(f"\n  设备 {row['d']} 个 · 指标 {row['m']} 种")
        print(f"  数据时间跨度  {fmt_ts(row['a'])} → {fmt_ts(row['b'])}")
        late = c.execute("SELECT device, MAX(ts) t FROM metrics GROUP BY device ORDER BY t DESC").fetchall()
        print("  各设备最后上报：")
        for r in late:
            gap = ""
            try:
                dt = (datetime.now(TZ) - datetime.fromisoformat(r["t"])).total_seconds() / 60
                gap = f"（{dt:.0f} 分钟前）" + ("  ← 有点久了" if dt > 120 else "")
            except Exception:
                pass
            print(f"    {r['device']:<14} {fmt_ts(r['t'])} {gap}")

    if has(c, "reminders"):
        q = c.execute("SELECT status, COUNT(*) n FROM reminders GROUP BY status").fetchall()
        print("  提醒队列：" + " · ".join(f"{r['status']}={r['n']}" for r in q))
    if has(c, "scheduled"):
        n = c.execute("SELECT COUNT(*) FROM scheduled WHERE fired_at IS NULL").fetchone()[0]
        print(f"  定点提醒     {n} 条待触发")


def cmd_metrics(a):
    c = db()
    where, args = [], []
    if a.metric:
        where.append("metric=?"); args.append(a.metric)
    if a.device:
        where.append("device=?"); args.append(a.device)
    if a.day:
        where.append("day=?"); args.append(a.day)
    sql = "SELECT ts, device, metric, value, unit, meta FROM metrics"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    rows = c.execute(sql, args + [a.limit]).fetchall()
    hr(f"数据（{len(rows)} 条）")
    for r in rows:
        meta = (r["meta"] or "")[:60]
        print(f"  {fmt_ts(r['ts'])}  {r['device']:<12} {r['metric']:<24} {r['value']:<10} {meta}")


def cmd_devices(a):
    c = db()
    rows = c.execute("SELECT device, COUNT(*) n, COUNT(DISTINCT metric) m, MIN(ts) a, MAX(ts) b "
                     "FROM metrics GROUP BY device ORDER BY b DESC").fetchall()
    hr("设备")
    for r in rows:
        print(f"  {r['device']:<14} {human(r['n']):>8} 条 · {r['m']} 种指标 · {fmt_ts(r['a'])} → {fmt_ts(r['b'])}")


def cmd_metrics_kinds(a):
    c = db()
    rows = c.execute("SELECT metric, COUNT(*) n, MAX(ts) t FROM metrics GROUP BY metric ORDER BY n DESC").fetchall()
    hr("指标种类")
    for r in rows:
        print(f"  {r['metric']:<26} {human(r['n']):>8} 条 · 最近 {fmt_ts(r['t'])}")


def cmd_reminders(a):
    c = db()
    if not has(c, "reminders"):
        return print("  没有提醒表")
    sql = "SELECT * FROM reminders" + ("" if a.all else " WHERE status='new'") + " ORDER BY id DESC LIMIT ?"
    rows = c.execute(sql, [a.limit]).fetchall()
    hr(f"提醒（{'全部' if a.all else '待发'}，{len(rows)} 条）")
    for r in rows:
        print(f"  #{r['id']:<5} {r['status']:<9} {(r['text'] or '')[:60]}")


def cmd_scheduled(a):
    c = db()
    rows = c.execute("SELECT * FROM scheduled ORDER BY at_iso").fetchall()
    hr("定点提醒")
    for r in rows:
        state = "已触发" if r["fired_at"] else "待触发"
        print(f"  #{r['id']:<4} {r['at_iso'][:16]}  {'每天' if r['daily'] else '一次'}  {state}  {(r['text'] or '')[:44]}")


def cmd_timetable(a):
    c = db()
    if not has(c, "timetable"):
        return print("  没有课表")
    r = c.execute("SELECT * FROM timetable LIMIT 1").fetchone()
    if not r:
        return print("  还没导入课表")
    try:
        tt = json.loads(r["raw"])
    except Exception:
        return print("  课表数据解析不了")
    courses = tt.get("courses") or []
    today = datetime.now(TZ)
    dow = today.isoweekday()
    hr("课表")
    print(f"  学期开始 {tt.get('termStartDate', '?')} · 共 {len(courses)} 门 · 更新于 {fmt_ts(r['updated_at'])}")
    todays = [x for x in courses if x.get("day") == dow]
    print(f"  今天（周{'一二三四五六日'[dow - 1]}）{len(todays)} 节：")
    periods = {p.get("index", i + 1): p for i, p in enumerate(tt.get("periods") or [])}
    for x in sorted(todays, key=lambda y: y.get("startPeriod", 0)):
        sp = x.get("startPeriod", 0)
        st = (periods.get(sp) or {}).get("start", "?")
        print(f"    {st}  {x.get('name', '?')}  第{sp}节×{x.get('span', 1)}  {x.get('room', '')}  周次 {x.get('weeks', '')}")


def cmd_chats(a):
    c = db()
    if not has(c, "chats"):
        return print("  没有对话表")
    rows = c.execute("SELECT * FROM chats ORDER BY ts DESC LIMIT ?", [a.limit]).fetchall()
    hr(f"对话（最近 {len(rows)} 条）")
    for r in rows:
        print(f"  {fmt_ts(r['ts'])}  [{r['role']}] {(r['text'] or '')[:70]}")


def cmd_stats(a):
    c = db()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    hr(f"概览（今天 {today}）")

    def one(metric, agg="MAX", day=today):
        if not has(c, "metrics"):
            return None
        try:
            r = c.execute(f"SELECT {agg}(value) v, COUNT(*) n FROM metrics WHERE metric=? AND day=?",
                          (metric, day)).fetchone()
            return r["v"] if r and r["n"] else None
        except Exception:
            return None

    scr = one("screen.active_minutes")
    print(f"  屏幕        {int(scr)} 分钟" if scr else "  屏幕        无数据")
    slp = one("sleep.total_minutes")
    print(f"  睡眠        {int(slp)} 分钟" if slp else "  睡眠        无数据")
    if has(c, "metrics"):
        g = c.execute("SELECT meta, value FROM metrics WHERE metric='app.usage_minutes' AND day=? "
                      "ORDER BY ts DESC", (today,)).fetchall()
        games, music = {}, set()
        for r in g:
            try:
                m = json.loads(r["meta"] or "{}")
            except Exception:
                m = {}
            if any(k in (m.get("pkg", "") + m.get("app", "")).lower() for k in ("game", "ark", "方舟", "mihoyo")):
                games[m.get("app", "?")] = max(games.get(m.get("app", "?"), 0), int(float(r["value"] or 0)))
            if m.get("title"):
                music.add(m["title"])
        if games:
            print("  游戏        " + " · ".join(f"{k} {v} 分钟" for k, v in games.items()))
        if music:
            print(f"  音乐        {len(music)} 首（最近：{list(music)[0][:30]}）")
        o = c.execute("SELECT COUNT(*) n FROM metrics WHERE metric='order.event' AND day=?",
                      (today,)).fetchone()["n"]
        if o:
            print(f"  订单事件    {o} 条")
    if has(c, "reminders"):
        n = c.execute("SELECT COUNT(*) FROM reminders WHERE status='new'").fetchone()[0]
        print(f"  待发提醒    {n} 条")


def _safe_sql(q):
    q = (q or "").strip().rstrip(";")
    low = q.lower()
    if not (low.startswith("select") or low.startswith("pragma")):
        sys.exit("  只允许 SELECT / PRAGMA（本工具默认只读）")
    for bad in (";", "attach", "insert", "update", "delete", "drop", "alter", "create"):
        if bad in low:
            sys.exit(f"  语句里不允许出现 {bad!r}")


def cmd_sql(a):
    _safe_sql(a.query)
    c = db()
    rows = c.execute(a.query).fetchall()
    hr(f"SQL（{len(rows)} 行）")
    if rows:
        print("  " + " | ".join(rows[0].keys()))
        for r in rows[:a.limit]:
            print("  " + " | ".join(str(x)[:40] for x in tuple(r)))
    else:
        print("  （空）")


# ---------------------------------------------------------------- 导出/导入
def _redact_row(t, row):
    d = dict(row)
    for k in list(d.keys()):
        if k in REDACT_KEYS and d[k]:
            d[k] = "（已脱敏）"
    if t == "metrics" and d.get("meta"):
        try:
            m = json.loads(d["meta"])
            for k in list(m.keys()):
                if k in REDACT_KEYS:
                    m[k] = "（已脱敏）"
            d["meta"] = json.dumps(m, ensure_ascii=False)
        except Exception:
            d["meta"] = "（已脱敏）"
    return d


def cmd_dump(a):
    c = db()
    if a.metric and a.out and a.out.endswith(".csv"):
        rows = c.execute("SELECT * FROM metrics WHERE metric=? ORDER BY ts", (a.metric,)).fetchall()
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols(c, "metrics"))
            w.writeheader()
            for r in rows:
                w.writerow(_redact_row("metrics", r) if a.redact else dict(r))
        print(f"  ✓ 导出 CSV：{a.out}（{len(rows)} 条{'，已脱敏' if a.redact else ''}）")
        return

    out = {"version": 1, "exported_at": datetime.now(TZ).isoformat(), "redacted": bool(a.redact), "tables": {}}
    total = 0
    for t in tables(c):
        rows = c.execute(f"SELECT * FROM {t}").fetchall()
        out["tables"][t] = [(_redact_row(t, r) if a.redact else dict(r)) for r in rows]
        total += len(rows)
    path = a.out or f"hub-export-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    size = os.path.getsize(path) / 1024
    print(f"  ✓ 导出：{path}")
    print(f"    表 {len(out['tables'])} 个 · 共 {human(total)} 行 · {size:.1f} KB{'（已脱敏）' if a.redact else ''}")
    for t, v in out["tables"].items():
        print(f"      {t:<12} {human(len(v)):>9} 行")
    if a.encrypt:
        pw = a.password or os.getenv("WHALE_PASS") or input("  设置加密密码：").strip()
        if pw:
            _encrypt_file(path, pw)


def cmd_load(a):
    if not os.path.exists(a.file):
        sys.exit(f"  找不到文件：{a.file}")
    with open(a.file, encoding="utf-8") as f:
        data = json.load(f)
    tbl = data.get("tables") or {}
    if not tbl:
        sys.exit("  文件里没有 tables 字段（不是本工具导出的格式？）")
    c = db()
    print(f"  文件导出时间 {data.get('exported_at', '?')}{'（脱敏版）' if data.get('redacted') else ''}")
    plan = []
    for t, rows in tbl.items():
        if not rows:
            continue
        if not has(c, t):
            print(f"  ⚠ 库里没有表 {t}，跳过（不擅自建表）")
            continue
        existing = {tuple(r) for r in c.execute(f"SELECT * FROM {t}").fetchall()}
        c_cols = cols(c, t)
        new = 0
        for r in rows:
            val = tuple(r.get(k) for k in c_cols)
            if val not in existing:
                new += 1
        plan.append((t, len(rows), new))

    hr("将要导入")
    for t, total, new in plan:
        print(f"  {t:<12} 文件 {human(total):>8} 行 · 其中新的 {human(new):>8} 行（重复的会覆盖，不会新增）")
    if a.dry_run:
        print("\n  （这是演练，什么都没写。去掉 --dry-run 才会真的导入）")
        return
    if not a.yes:
        ans = input("\n  确认导入？只增不删，输入 yes 继续：").strip().lower()
        if ans != "yes":
            return print("  已取消")
    ins = 0
    for t, rows in tbl.items():
        if not rows or not has(c, t):
            continue
        c_cols = cols(c, t)
        ph = ",".join("?" for _ in c_cols)
        for r in rows:
            c.execute(f"INSERT OR REPLACE INTO {t}({','.join(c_cols)}) VALUES ({ph})",
                      [r.get(k) for k in c_cols])
            ins += 1
    c.commit()
    print(f"  ✓ 导入完成，写入/覆盖 {human(ins)} 行（原有数据未删除）")


def cmd_backup(a):
    c = db()
    c.execute("PRAGMA wal_checkpoint(FULL)")
    c.close()
    dst = f"{DB}.bak-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB, dst)
    print(f"  ✓ 备份：{dst}（{os.path.getsize(dst) / 1024 / 1024:.2f} MB）")
    if getattr(a, "encrypt", False):
        pw = getattr(a, "password", None) or os.getenv("WHALE_PASS") or input("  设置加密密码：").strip()
        if pw:
            _encrypt_file(dst, pw)
    print("    还原：hubctl restore <备份文件>（加密的先 hubctl decrypt）")


def cmd_restore(a):
    if not os.path.exists(a.file):
        sys.exit(f"  找不到备份：{a.file}")
    if not a.yes:
        ans = input(f"  ⚠ 这会用 {a.file} 覆盖当前库，输入 yes 继续：").strip().lower()
        if ans != "yes":
            return print("  已取消")
    cmd_backup(argparse.Namespace())
    shutil.copy2(a.file, DB)
    print(f"  ✓ 已还原：{DB} ← {a.file}（原库已自动备份）")


def cmd_today(a):
    """今天最有用的一屏：比 status 聚焦、比 stats 详细。"""
    c = db()
    day = a.day or datetime.now(TZ).strftime("%Y-%m-%d")
    hr(f"今天（{day}）")
    if not has(c, "metrics"):
        return print("  没有数据表")
    rows = c.execute("SELECT device, metric, value, meta, ts FROM metrics WHERE day=? ORDER BY ts", (day,)).fetchall()
    if not rows:
        return print("  今天还没有数据")

    def peak(metric, agg="MAX"):
        vals = [float(r["value"] or 0) for r in rows if r["metric"] == metric]
        return (max(vals) if agg == "MAX" else sum(vals)) if vals else None

    scr = peak("screen.active_minutes")
    print(f"  屏幕使用    {int(scr)} 分钟" if scr else "  屏幕使用    无")
    slp = None
    for r in rows:
        if r["metric"] == "sleep.total_minutes":
            slp = float(r["value"])
    print(f"  睡眠        {int(slp)} 分钟" if slp else "  睡眠        无")

    # 各 App（同一 App 取当天最新一次）
    apps = {}
    for r in rows:
        if r["metric"] != "app.usage_minutes":
            continue
        try:
            m = json.loads(r["meta"] or "{}")
        except Exception:
            m = {}
        nm = m.get("app") or "?"
        apps[nm] = float(r["value"] or 0)
    top = sorted(apps.items(), key=lambda kv: -kv[1])[:5]
    if top:
        print("  App 用时前五：")
        for nm, v in top:
            print(f"    {nm:<14} {int(v)} 分钟")

    bt = {}
    for r in rows:
        if r["metric"] == "bt.battery_percent":
            try:
                m = json.loads(r["meta"] or "{}")
            except Exception:
                m = {}
            if m.get("name"):
                bt[m["name"]] = int(float(r["value"] or 0))
    if bt:
        print("  蓝牙设备电量：" + " · ".join(f"{k} {v}%" for k, v in bt.items()))
    for r in rows:
        if r["metric"] == "device.battery_percent":
            print(f"  手机电量    {int(float(r['value']))}%")
    for r in rows:
        if r["metric"] == "music.track":
            try:
                m = json.loads(r["meta"] or "{}")
            except Exception:
                m = {}
            if m.get("title"):
                print(f"  最近在听    {m['title'][:40]}" + (f" — {m.get('artist')}" if m.get("artist") else ""))
            break
    if has(c, "reminders"):
        # reminders 表没有 ts 列（只有 day/created）→ 按 day 过滤，没有才退回全部
        rcols = cols(c, "reminders")
        if "day" in rcols:
            q = c.execute("SELECT status, COUNT(*) n FROM reminders WHERE day=? GROUP BY status", (day,)).fetchall()
        else:
            q = c.execute("SELECT status, COUNT(*) n FROM reminders GROUP BY status").fetchall()
        if q:
            print("  提醒        " + " · ".join(f"{r['status']}={r['n']}" for r in q))


def cmd_top(a):
    """排行：哪个 App / 哪个指标最费时间。"""
    c = db()
    day = a.day or datetime.now(TZ).strftime("%Y-%m-%d")
    metric = a.metric or "app.usage_minutes"
    rows = c.execute("SELECT value, meta FROM metrics WHERE metric=? AND day=?", (metric, day)).fetchall()
    agg = {}
    for r in rows:
        try:
            m = json.loads(r["meta"] or "{}")
        except Exception:
            m = {}
        key = m.get("app") or m.get("name") or m.get("title") or (m.get("kind") or "（合计）")
        agg[key] = max(agg.get(key, 0), float(r["value"] or 0))
    hr(f"{metric} 排行（{day}）")
    for i, (k, v) in enumerate(sorted(agg.items(), key=lambda kv: -kv[1])[:a.limit], 1):
        bar = "█" * min(30, int(v / max(1, max(agg.values())) * 30))
        print(f"  {i:>2}. {k:<16} {int(v):>5} {bar}")


def cmd_watch(a):
    """跟着看新数据（Ctrl-C 退出）——像 tail 一样。"""
    c = db()
    seen = set()
    rows = c.execute("SELECT id FROM metrics ORDER BY id DESC LIMIT 200").fetchall() if has(c, "metrics") else []
    seen = {r["id"] for r in rows}
    print(f"  跟着看新数据（每 {a.interval}s 一次，Ctrl-C 停）...")
    try:
        while True:
            c2 = db()
            new = c2.execute("SELECT * FROM metrics ORDER BY id DESC LIMIT 50").fetchall()
            for r in reversed([x for x in new if x["id"] not in seen]):
                seen.add(r["id"])
                meta = (r["meta"] or "")[:50]
                print(f"  {fmt_ts(r['ts'])}  {r['device']:<12} {r['metric']:<24} {r['value']:<9} {meta}", flush=True)
            if len(seen) > 5000:
                seen = {x["id"] for x in new}
            c2.close()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print("\n  停了。")


def cmd_doctor(a):
    """体检：库完整性 / 端口 / 定时任务 / 磁盘 / 数据新鲜度。"""
    hr("体检")
    c = db()
    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"  库完整性    {'✓ ok' if ok == 'ok' else '✗ ' + str(ok)}")
    size = os.path.getsize(DB) / 1024 / 1024
    print(f"  库大小      {size:.2f} MB")
    try:
        st = os.statvfs(os.path.dirname(DB))
        free_gb = st.f_bavail * st.f_frsize / 1024 ** 3
        print(f"  磁盘剩余    {free_gb:.1f} GB" + ("  ← 有点紧" if free_gb < 1 else ""))
    except Exception:
        pass
    for f in ("hub.py", "hub.json", "tls/hub.crt", "tls/hub.key"):
        p = os.path.join(HERE, f)
        print(f"  {f:<14} {'✓' if os.path.exists(p) else '✗ 缺失'}")
    if has(c, "metrics"):
        r = c.execute("SELECT MAX(ts) t FROM metrics").fetchone()
        gap = (datetime.now(TZ) - datetime.fromisoformat(r["t"])).total_seconds() / 60 if r["t"] else 1e9
        print(f"  最近数据    {fmt_ts(r['t'])}（{gap:.0f} 分钟前）" + ("  ← 断了？" if gap > 120 else ""))
    try:
        import subprocess
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5).stdout
        print(f"  定时任务    {len([l for l in out.splitlines() if l.strip() and not l.startswith('#')])} 条")
    except Exception:
        pass


def cmd_ping(a):
    """看中枢活着没（HTTP + HTTPS 各试一次）。"""
    import ssl
    import urllib.request
    p = _cfg_port()
    for scheme, ctx in (("http", None), ("https", ssl._create_unverified_context())):
        url = f"{scheme}://127.0.0.1:{p[scheme]}/health"
        try:
            with urllib.request.urlopen(url, timeout=5, **({"context": ctx} if ctx else {})) as r:
                d = json.loads(r.read().decode())
                print(f"  {scheme.upper():<5} ✓ {url}  v{d.get('version', '?')} · 数据 {d.get('metrics')} · 提醒 {d.get('reminders')}")
        except Exception as e:
            print(f"  {scheme.upper():<5} ✗ {type(e).__name__}: {str(e)[:60]}")


def _cfg_port():
    """读 hub.json 里的端口（读不到就给默认）。"""
    p = os.path.join(HERE, "hub.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
        return {"http": cfg.get("port", 11440), "https": (cfg.get("tls") or {}).get("port", 11443)}
    except Exception:
        return {"http": 11440, "https": 11443}


def _cfg_path():
    return os.path.join(HERE, "hub.json")


def cmd_config(a):
    p = _cfg_path()
    if not os.path.exists(p):
        sys.exit("  没有 hub.json")
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    if a.action == "get":
        if a.key:
            cur = cfg
            for part in a.key.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            print(f"  {a.key} = {json.dumps(cur, ensure_ascii=False)}")
        else:
            print(json.dumps(cfg, ensure_ascii=False, indent=1))
        return
    # set
    parts = a.key.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    oldv = cur.get(parts[-1])
    val = a.value
    if val.lower() in ("true", "false"):
        val = val.lower() == "true"
    else:
        try:
            val = int(val)
        except ValueError:
            try:
                val = float(val)
            except ValueError:
                pass
    cur[parts[-1]] = val
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"  ✓ {a.key}: {json.dumps(oldv, ensure_ascii=False)} → {json.dumps(val, ensure_ascii=False)}")
    print("  提示：中枢最多 1 分钟自动热重启（指纹变了就重启）；想立刻生效：rm -f .hubhash")


def cmd_token(a):
    p = _cfg_path()
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    if not a.rotate:
        t = cfg.get("token", "")
        print(f"  当前 token  {t[:6]}…{t[-4:]}（长度 {len(t)}）")
        print("  轮换：hubctl token --rotate（会写回 hub.json，记得同步改采集器与说话层的配置）")
        return
    import secrets as _s
    new = _s.token_urlsafe(24)
    cfg["token"] = new
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 已轮换：{new[:6]}…{new[-4:]}")
    print("  ⚠ 现在去改这三处，否则采集器/说话层会 401：")
    print("     · 手机采集器 → 设置页的 token")
    print("     · 说话层环境变量 WHALE_TOKEN")
    print("     · 任何 curl 脚本")
    print("  中枢 1 分钟内自动热重启生效。")


def cmd_prune(a):
    """清理旧数据（默认演练）。"""
    c = db()
    cutoff = (datetime.now(TZ) - timedelta(days=a.days)).strftime("%Y-%m-%d")
    plan = []
    for t, col in (("metrics", "day"), ("reminders", "day"), ("chats", None)):
        if not has(c, t):
            continue
        if col and col in cols(c, t):
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} < ?", (cutoff,)).fetchone()[0]
            plan.append((t, f"{col} < {cutoff}", n))
    hr(f"清理 {a.days} 天前的数据")
    for t, cond, n in plan:
        print(f"  {t:<12} {n:>8} 行（{cond}）")
    if a.dry_run or not a.yes:
        print("\n  （演练，什么都没删。加 --yes 才真的删）")
        return
    for t, cond, n in plan:
        if n:
            c.execute(f"DELETE FROM {t} WHERE {cond}")
    c.commit()
    c.execute("VACUUM")
    print(f"  ✓ 已清理并压缩，现在 {os.path.getsize(DB) / 1024 / 1024:.2f} MB")


def cmd_find(a):
    """在数据 meta / 提醒 / 对话里搜关键词。"""
    c = db()
    kw = f"%{a.keyword}%"
    hr(f"搜索「{a.keyword}」")
    if has(c, "metrics"):
        rows = c.execute("SELECT ts, device, metric, value, meta FROM metrics WHERE meta LIKE ? "
                         "ORDER BY ts DESC LIMIT ?", (kw, a.limit)).fetchall()
        print(f"  数据 {len(rows)} 条：")
        for r in rows:
            print(f"    {fmt_ts(r['ts'])} {r['metric']} {r['value']} {(r['meta'] or '')[:50]}")
    for t, col in (("reminders", "text"), ("chats", "text"), ("scheduled", "text")):
        if has(c, t):
            rows = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} LIKE ?", (kw,)).fetchone()[0]
            print(f"  {t}: {rows} 条")


def _encrypt_file(path: str, password: str) -> str:
    """用 openssl 做 AES-256 加密（不引第三方库，服务器上一般都有 openssl）。"""
    import shutil as _sh
    import subprocess as _sp
    if not _sh.which("openssl"):
        sys.exit("  没找到 openssl，无法加密（apt install openssl）")
    out = path + ".enc"
    r = _sp.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-in", path, "-out", out,
                 "-pass", "pass:" + password], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"  加密失败：{r.stderr.decode()[:120]}")
    os.remove(path)
    print(f"  ✓ 已加密：{out}（AES-256-CBC / PBKDF2；密码不落盘）")
    return out


def cmd_decrypt(a):
    import subprocess as _sp
    if not a.file.endswith(".enc"):
        sys.exit("  只处理 .enc 文件")
    out = a.file[:-4]
    pw = a.password or os.getenv("WHALE_PASS") or input("  密码：").strip()
    r = _sp.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-in", a.file, "-out", out,
                 "-pass", "pass:" + pw], capture_output=True)
    if r.returncode != 0:
        sys.exit(f"  解密失败（密码不对？）：{r.stderr.decode()[:120]}")
    print(f"  ✓ 已解密：{out}")


def cmd_interruption(a):
    """打扰仪表盘：多久说一次、被认可多少、都在哪些钟点说（文献里的"打扰预算"落地）"""
    import collections
    days = getattr(a, "days", 7)
    with db() as c:
        try:
            dec = [dict(r) for r in c.execute(
                "SELECT ts, kind, gap_sec, material, reason, band FROM decisions ORDER BY id DESC LIMIT 800")]
        except Exception:
            dec = []
        try:
            fb = [dict(r) for r in c.execute("SELECT ts, verdict FROM feedback ORDER BY id DESC LIMIT 400")]
        except Exception:
            fb = []
    said = sum(1 for d in dec if d.get("kind") in ("speak", "said"))
    silent = sum(1 for d in dec if d.get("kind") in ("silent", "wait"))
    up = sum(1 for f in fb if str(f.get("verdict")) in ("up", "1", "good", "yes"))
    down = sum(1 for f in fb if str(f.get("verdict")) in ("down", "0", "bad", "no"))
    p_accept = (1 + up) / (2 + up + down)
    hours = collections.Counter(str(d.get("ts") or "")[11:13] for d in dec if len(str(d.get("ts") or "")) > 13)
    print("  打扰仪表盘（最近 %d 条决策 / %d 条反馈）" % (len(dec), len(fb)))
    print("    开口 %d 次 · 沉默 %d 次 · 开口率 %.0f%%" % (said, silent, 100 * said / max(1, said + silent)))
    print("    反馈 %d✓ / %d✗ · p(接受)=%.2f" % (up, down, p_accept))
    if hours:
        print("    开口钟点分布：", " ".join("%s点×%d" % (h, n) for h, n in sorted(hours.items())))
    print("    最近 5 条理由：")
    for d in dec[:5]:
        print("      %s %-6s 料%-3s %s" % (str(d.get("ts"))[11:16], d.get("kind"), d.get("material"),
                                            str(d.get("reason"))[:52]))


def main():
    p = argparse.ArgumentParser(prog="hubctl", description="鲸鲸中枢命令行工具（默认只读）",
                                add_help=True,
                                epilog="例：hubctl status / hubctl metrics -n 20 / hubctl dump -o all.json --redact")
    sub = p.add_subparsers(dest="cmd")

    it = sub.add_parser("interruption", help="打扰仪表盘：开口率/接受率/钟点分布")
    it.add_argument("--days", type=int, default=7)
    it.set_defaults(fn=cmd_interruption)
    sub.add_parser("status", help="总览").set_defaults(fn=cmd_status)
    td = sub.add_parser("today", help="今天一屏（屏幕/睡眠/App前五/蓝牙电量/在听）")
    td.add_argument("--day"); td.set_defaults(fn=cmd_today)
    tp = sub.add_parser("top", help="排行（默认 App 用时）")
    tp.add_argument("-m", "--metric"); tp.add_argument("--day"); tp.add_argument("-n", "--limit", type=int, default=10)
    tp.set_defaults(fn=cmd_top)
    w = sub.add_parser("watch", help="跟着看新数据（Ctrl-C 退出）")
    w.add_argument("-i", "--interval", type=float, default=3.0); w.set_defaults(fn=cmd_watch)
    sub.add_parser("doctor", help="体检（库/端口/磁盘/新鲜度）").set_defaults(fn=cmd_doctor)
    sub.add_parser("ping", help="看中枢活着没").set_defaults(fn=cmd_ping)
    cf = sub.add_parser("config", help="看/改 hub.json")
    cf.add_argument("action", choices=["get", "set"]); cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?"); cf.set_defaults(fn=cmd_config)
    tk = sub.add_parser("token", help="看 token / 轮换")
    tk.add_argument("--rotate", action="store_true"); tk.set_defaults(fn=cmd_token)
    pr = sub.add_parser("prune", help="清理旧数据（默认演练）")
    pr.add_argument("--days", type=int, default=180); pr.add_argument("--yes", action="store_true")
    pr.add_argument("--dry-run", action="store_true"); pr.set_defaults(fn=cmd_prune)
    fd = sub.add_parser("find", help="搜数据/提醒/对话")
    fd.add_argument("keyword"); fd.add_argument("-n", "--limit", type=int, default=20); fd.set_defaults(fn=cmd_find)
    m = sub.add_parser("metrics", help="看数据")
    m.add_argument("-n", "--limit", type=int, default=20)
    m.add_argument("-m", "--metric"); m.add_argument("-d", "--device"); m.add_argument("--day")
    m.set_defaults(fn=cmd_metrics)
    sub.add_parser("devices", help="设备").set_defaults(fn=cmd_devices)
    sub.add_parser("kinds", help="指标种类").set_defaults(fn=cmd_metrics_kinds)
    r = sub.add_parser("reminders", help="提醒队列")
    r.add_argument("-n", "--limit", type=int, default=30); r.add_argument("--all", action="store_true")
    r.set_defaults(fn=cmd_reminders)
    sub.add_parser("scheduled", help="定点提醒").set_defaults(fn=cmd_scheduled)
    sub.add_parser("timetable", help="课表").set_defaults(fn=cmd_timetable)
    ch = sub.add_parser("chats", help="对话记录")
    ch.add_argument("-n", "--limit", type=int, default=10); ch.set_defaults(fn=cmd_chats)
    sub.add_parser("stats", help="今日概览").set_defaults(fn=cmd_stats)
    s = sub.add_parser("sql", help="只读 SQL")
    s.add_argument("query"); s.add_argument("-n", "--limit", type=int, default=50); s.set_defaults(fn=cmd_sql)
    d = sub.add_parser("dump", help="导出")
    d.add_argument("-o", "--out"); d.add_argument("-m", "--metric")
    d.add_argument("--redact", action="store_true", help="脱敏（可安全外发）")
    d.add_argument("--encrypt", action="store_true", help="导出后 AES-256 加密（密码：--pass / 环境变量 WHALE_PASS / 交互输入）")
    d.add_argument("--pass", dest="password", help="加密密码")
    d.set_defaults(fn=cmd_dump)
    l = sub.add_parser("load", help="导入（合并，只增不删）")
    l.add_argument("file"); l.add_argument("--dry-run", action="store_true"); l.add_argument("--yes", action="store_true")
    l.set_defaults(fn=cmd_load)
    bk = sub.add_parser("backup", help="备份库（--encrypt 加密，建议开）")
    bk.add_argument("--encrypt", action="store_true"); bk.add_argument("--pass", dest="password")
    bk.set_defaults(fn=cmd_backup)
    dc = sub.add_parser("decrypt", help="解密 .enc 备份/导出")
    dc.add_argument("file"); dc.add_argument("--pass", dest="password"); dc.set_defaults(fn=cmd_decrypt)
    rs = sub.add_parser("restore", help="还原库")
    rs.add_argument("file"); rs.add_argument("--yes", action="store_true"); rs.set_defaults(fn=cmd_restore)

    a = p.parse_args()
    if not getattr(a, "fn", None):
        p.print_help()
        return
    a.fn(a)


if __name__ == "__main__":
    main()

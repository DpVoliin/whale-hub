#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stress_report.py —— 压测报告：拿合成（或真实）的几个月的库，量一遍"会不会卡"。

为什么需要它：
    "零依赖 + SQLite + 单文件"在几万行时当然没事，但**没人验证过几十万行**。
    真到那天才发现慢，就得在一堆个人数据上做手术。所以先在合成库上把曲线量出来。

用法：
    # 1) 先造数据
    python3 tests/make_fake_history.py --days 180 --density real --seed 7 --out /tmp/sim180
    # 2) 出报告
    WHALE_HOME=/tmp/sim180 python3 hub/tools/stress_report.py
    WHALE_HOME=/tmp/sim180 python3 hub/tools/stress_report.py --markdown /tmp/stress.md

它会量：库大小 / 行数 / 每天密度 / 索引是否齐 · 关键函数耗时（中位数，含冷启动）·
HTTP 接口耗时（/today · /llm-preview · /export · /metrics）· 结论（是否达标）。
纯标准库；**只读**，不改任何数据。
"""
import argparse
import importlib.util
import os
import pathlib
import statistics
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
HUB_SRC = ROOT / "hub" / "hub.py"

# 达标线（普通人用起来"不觉得卡"的阈值；超过就打 ⚠）
BUDGET_MS = {
    "llm_context": 300,        # 每次要说话都要算一次 → 最敏感
    "daily_digest": 500,
    "analyze": 500,
    "care_now": 300,
    "source_health": 400,
    "band_stats": 100,
    "/today": 800,
    "/llm-preview": 800,
    "/export": 15000,          # 全量导出，慢点可以接受
    "/metrics?limit=2000": 1500,
}


def load_hub(home):
    os.environ["WHALE_HOME"] = str(home)
    os.environ.setdefault("WHALE_QUIET", "1")
    spec = importlib.util.spec_from_file_location("whalecare_stress", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def timeit(fn, n=3):
    """跑 n 次取中位数（第一次通常最慢：冷缓存 —— 单独报出来）。"""
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:
            return None, "%s: %s" % (type(e).__name__, str(e)[:60])
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), round(ts[0], 1)


def db_stats(hub, db):
    out = {}
    out["size_mb"] = round(os.path.getsize(db) / 1048576.0, 2)
    with hub.db() as c:
        out["integrity"] = c.execute("PRAGMA integrity_check").fetchone()[0]
        out["page_size"] = c.execute("PRAGMA page_size").fetchone()[0]
        counts = {}
        for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            try:
                counts[r["name"]] = c.execute("SELECT COUNT(*) n FROM %s" % r["name"]).fetchone()["n"]
            except Exception:
                pass
        out["tables"] = counts
        out["indexes"] = [r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out["metrics"] = counts.get("metrics", 0)
        rows = c.execute("SELECT day, COUNT(*) n FROM metrics GROUP BY day").fetchall()
        out["days"] = len(rows)
        out["rows_per_day_median"] = int(statistics.median([r["n"] for r in rows])) if rows else 0
        out["top_metrics"] = [(r["metric"], r["n"]) for r in c.execute(
            "SELECT metric, COUNT(*) n FROM metrics GROUP BY metric ORDER BY n DESC LIMIT 8")]
    return out


def main():
    ap = argparse.ArgumentParser(description="压测报告（只读）")
    ap.add_argument("--markdown", default="", help="把报告写到这个 .md（同时仍打印）")
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    home = pathlib.Path(os.environ.get("WHALE_HOME") or (ROOT / "hub"))
    db = home / "hub.db"
    if not db.exists():
        raise SystemExit("找不到 %s —— 先跑 tests/make_fake_history.py（或指定 WHALE_HOME）" % db)

    hub = load_hub(home)
    try:
        hub.init_db()
    except Exception:
        pass
    L = []
    L.append("# 压测报告")
    L.append("")
    L.append("> 库：`%s` · 生成时间 %s" % (db, time.strftime("%Y-%m-%d %H:%M:%S")))
    L.append("")

    d = db_stats(hub, db)
    L.append("## 规模")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 库大小 | %s MB |" % d["size_mb"])
    L.append("| metrics 行数 | %s |" % f"{d['metrics']:,}")
    L.append("| 覆盖天数 | %s |" % d["days"])
    L.append("| 每天中位行数 | %s |" % f"{d['rows_per_day_median']:,}")
    L.append("| integrity_check | %s |" % d["integrity"])
    L.append("| 索引 | %s |" % ", ".join(d["indexes"]))
    L.append("")
    L.append("其它表：" + ", ".join("%s %s" % (k, v) for k, v in sorted(d["tables"].items()) if k != "metrics"))
    L.append("")
    L.append("指标构成（前 8）：" + " · ".join("%s %s" % (m, f"{n:,}") for m, n in d["top_metrics"]))
    L.append("")

    # ---- 函数耗时
    L.append("## 关键函数耗时（中位数 / 首跑，ms）")
    L.append("")
    L.append("| 函数 | 中位数 | 首跑（冷） | 预算 | 结论 |")
    L.append("|---|---|---|---|---|")
    funcs = [
        ("llm_context", lambda: hub.llm_context()),
        ("daily_digest", lambda: hub.daily_digest()),
        ("analyze", lambda: hub.analyze()),
        ("care_now", lambda: hub.care_now()),
        ("source_health", lambda: hub.source_health()),
        ("band_stats", lambda: hub.band_stats()),
    ]
    verdicts = {}
    for name, fn in funcs:
        med, first = timeit(fn, a.repeat)
        if med is None:
            L.append("| `%s()` | — | — | %s | ✗ %s |" % (name, BUDGET_MS[name], first))
            verdicts[name] = False
            continue
        ok = med <= BUDGET_MS[name]
        verdicts[name] = ok
        L.append("| `%s()` | %.0f | %s | %s | %s |" % (name, med, first, BUDGET_MS[name], "✓" if ok else "⚠ 超"))
    L.append("")

    # ---- HTTP 接口耗时（在随机端口上真起一个 Handler）
    port = 30000 + int(time.time()) % 20000
    srv = ThreadingHTTPServer(("127.0.0.1", port), hub.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok = hub.CFG["token"]

    def http(path, n=3):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-Token": tok})
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            ts.append((time.perf_counter() - t0) * 1000)
        return statistics.median(ts), round(ts[0], 1), None

    L.append("## HTTP 接口耗时（中位数 / 首跑，ms）")
    L.append("")
    L.append("| 接口 | 中位数 | 首跑（冷） | 预算 | 结论 |")
    L.append("|---|---|---|---|---|")
    for path in ("/today", "/llm-preview", "/export", "/metrics?limit=2000"):
        try:
            med, first, _ = http(path)
            ok = med <= BUDGET_MS[path]
            verdicts[path] = ok
            L.append("| `%s` | %.0f | %s | %s | %s |" % (path, med, first, BUDGET_MS[path], "✓" if ok else "⚠ 超"))
        except Exception as e:
            L.append("| `%s` | — | — | %s | ✗ %s |" % (path, BUDGET_MS[path], str(e)[:50]))
            verdicts[path] = False
    srv.shutdown()
    L.append("")

    bad = [k for k, v in verdicts.items() if not v]
    L.append("## 结论")
    L.append("")
    if not bad:
        L.append("**全部达标** —— %d 天 / %s 行这个量级下，她没有变慢的迹象。" % (d["days"], f"{d['metrics']:,}"))
    else:
        L.append("**有 %d 项超出预算**：%s" % (len(bad), "、".join("`%s`" % b for b in bad)))
        L.append("")
        L.append("先看这些：① 有没有缺索引（`metrics(day, metric)` 在就更稳）"
                 " ② 是不是 `llm_context` 把全表扫了 ③ 该不该上按天预聚合。")
    L.append("")
    txt = "\n".join(L)
    print(txt)
    if a.markdown:
        pathlib.Path(a.markdown).write_text(txt, encoding="utf-8")
        print("\n（已写入 %s）" % a.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

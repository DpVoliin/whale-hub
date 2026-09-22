#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成历史库生成器 —— 造"有很多坑"的历史数据，用来测基线/退避/复盘/回放。

**永远不会碰你的真实库**：它把中枢加载到一个临时 WHALE_HOME 里，只在临时目录里建库。

用法：
    python3 tests/make_fake_history.py --days 30 --seed 42 --out /tmp/fake-home
    python3 tests/make_fake_history.py --scenario all --out /tmp/fake-home

覆盖的坑（每一项都是真踩过的）：
    残缺日        某天只有 1–2 条上报（会被"有效日"门槛丢掉）
    整段缺失      采集中断两周（复盘要有办法区分"没数据"和"数据是 0"）
    连续同值      MAD = 0 → 稳健 z 会算出无穷大（必须有下限兜住）
    单日突变      某天 10 倍（异常检测该抓到，但不该天天报）
    跨零点/跨月   23:58 与 00:02 的两条要能分清是哪天的
    时区偏移      ts 带 +08:00 与不带混合
    分类改名      同一个 App 中途改名（按分类聚合时不能裂成两个）
    超长静默      连续 3 天没有任何输出（自检应能发现"她好久没说话了"）
"""
import argparse
import importlib.util
import json
import os
import pathlib
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
HERE = pathlib.Path(__file__).resolve().parent
HUB_SRC = HERE.parent / "hub" / "hub.py"


def load_hub(home: pathlib.Path):
    """把中枢加载进临时数据目录（不碰真实部署）。"""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    os.environ.setdefault("WHALE_QUIET", "1")
    spec = importlib.util.spec_from_file_location("whalecare_fake", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows_for_day(rng, day: datetime, scenario: str, carry: dict):
    """生成某一天的指标行：(metric, value, unit, meta, device)"""
    out = []
    # ---- 屏幕使用：基准 300 分钟 ± 抖动
    base = carry.get("screen", 300)
    screen = max(30, int(rng.gauss(base, 45)))
    if scenario == "spike" and rng.random() < 0.12:
        screen = int(screen * 10)                     # 单日突变
    out.append(("screen.active_minutes", screen, "min", {}, "phone_fake"))

    # ---- App 分类（含改名：同一个 App 中途换名字）
    name = "哔哩哔哩" if day < carry["rename_at"] else "bilibili"
    out.append(("app.usage_minutes", rng.randint(20, 150), "min", {"app": name, "category": "短视频/视频"}, "phone_fake"))
    out.append(("app.usage_minutes", rng.randint(5, 60), "min", {"app": "微信", "category": "社交"}, "phone_fake"))

    # ---- 连续同值：连续 7 天步数一模一样 → MAD = 0
    if carry.get("flat"):
        out.append(("steps.total", 3000.0, "步", {}, "phone_fake"))
    else:
        out.append(("steps.total", float(rng.randint(800, 9000)), "步", {}, "phone_fake"))

    # ---- 睡眠（含跨零点：入睡在昨天 23:5x）
    out.append(("sleep.total_minutes", float(rng.randint(300, 520)), "min", {}, "watch_fake"))

    # ---- 健康（含"值相同"的连续读数，考验去重）
    out.append(("health.heart_rate", float(rng.randint(58, 92)), "bpm", {}, "watch_fake"))
    out.append(("health.spo2", 98.0, "%", {}, "watch_fake"))

    # ---- 天气（设备 = server）
    out.append(("weather.now", round(rng.uniform(18, 34), 1), "C",
                {"city_code": "101280101", "city": "广州", "desc": "晴"}, "server"))

    # ---- 订单/快递（事件类：值都是 1，去重必须比 meta）
    if rng.random() < 0.3:
        out.append(("order.event", 1.0, "", {"kind": "快递", "amount_range": "0-50"}, "phone_fake"))

    return out


def gen(days: int, seed: int, home: pathlib.Path, scenario: str):
    rng = random.Random(seed)
    hub = load_hub(home)
    try:
        hub.init_db()
    except Exception:
        pass
    start = datetime.now(TZ).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=days)
    carry = {"screen": 300, "flat": False, "rename_at": start + timedelta(days=max(1, days // 2))}

    inserted = 0
    for i in range(days):
        d = start + timedelta(days=i)
        # 整段缺失：第 10–24 天完全没数据（模拟采集器停机两周）
        if scenario in ("all", "gap") and 10 <= i < 24:
            continue
        # 连续同值：第 5–12 天步数锁死
        carry["flat"] = scenario in ("all", "flat") and 5 <= i < 12
        # 残缺日：每 7 天来一次只报 1 条
        broken = (scenario in ("all", "broken")) and i % 7 == 3

        rows = rows_for_day(rng, d, scenario, carry)
        if broken:
            rows = rows[:1]
        # 每天分 3 个时间点上报（让 ts 有跨度）
        for k, (metric, value, unit, meta, device) in enumerate(rows):
            ts = d + timedelta(hours=8 + (k % 10), minutes=rng.randint(0, 59))
            # 时区偏移：一半带 +08:00，一半不带（考验解析）
            ts_s = ts.isoformat() if k % 2 == 0 else ts.replace(tzinfo=None).isoformat()
            try:
                with hub.db() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts_s, d.strftime("%Y-%m-%d"), device, metric, value, unit, "fake", 1.0,
                         json.dumps(meta, ensure_ascii=False)))
                inserted += 1
            except Exception as e:
                print(f"  ✗ 写入失败 {metric}: {type(e).__name__} {e}")
                return inserted

        # 跨零点：23:58 一条 + 次日 00:02 一条
        if scenario in ("all", "midnight") and i % 5 == 0:
            for off, hm in ((0, "23:58"), (1, "00:02")):
                ts = (d + timedelta(days=off)).strftime("%Y-%m-%dT" + hm + ":00+08:00")
                with hub.db() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (ts, ts[:10], "phone_fake", "screen.active_minutes", 1.0, "min", "fake", 1.0, "{}"))
                inserted += 1

    return inserted


def main():
    ap = argparse.ArgumentParser(description="造合成历史库（不碰真实部署）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None, help="临时数据目录（默认临时目录，跑完自动删）")
    ap.add_argument("--scenario", default="all",
                    choices=["all", "normal", "gap", "flat", "spike", "broken", "midnight"])
    ap.add_argument("--keep", action="store_true", help="保留临时目录（默认保留，方便你自己翻）")
    a = ap.parse_args()

    home = pathlib.Path(a.out) if a.out else pathlib.Path(tempfile.mkdtemp(prefix="whale-fake-"))
    print(f"  合成库目录：{home}")
    n = gen(a.days, a.seed, home, a.scenario)
    db = home / "hub.db"
    size = db.stat().st_size if db.exists() else 0
    print(f"  ✓ 写入 {n} 行 · {a.days} 天 · 场景 {a.scenario} · 库 {size / 1024:.0f} KB")
    print("  下一步可以拿它测：")
    print(f"     WHALE_HOME={home} python3 hub/hubctl.py status")
    print(f"     WHALE_HOME={home} python3 hub/hubctl.py today")
    return 0


if __name__ == "__main__":
    sys.exit(main())

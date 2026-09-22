#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成历史库生成器 —— 造"有很多坑"的历史数据，用来测基线/退避/复盘/回放/压测。

**永远不会碰你的真实库**：它把中枢加载到一个临时 WHALE_HOME 里，只在临时目录里建库。

用法：
    python3 tests/make_fake_history.py --days 30 --seed 42 --out /tmp/fake-home
    python3 tests/make_fake_history.py --scenario all --out /tmp/fake-home
    # ★ 高密度（数万~数十万行，用来压 SQLite / 索引 / 接口耗时）
    python3 tests/make_fake_history.py --days 180 --density real --seed 7 --out /tmp/sim180

覆盖的坑（每一项都是真踩过的）：
    残缺日        某天只有 1–2 条上报（会被"有效日"门槛丢掉）
    整段缺失      采集中断两周（复盘要有办法区分"没数据"和"数据是 0"）
    连续同值      MAD = 0 → 稳健 z 会算出无穷大（必须有下限兜住）
    单日突变      某天 10 倍（异常检测该抓到，但不该天天报）
    跨零点/跨月   23:58 与 00:02 的两条要能分清是哪天的
    时区偏移      ts 带 +08:00 与不带混合
    分类改名      同一个 App 中途改名（按分类聚合时不能裂成两个）
    超长静默      连续 3 天没有任何输出（自检应能发现"她好久没说话了"）

密度（--density）：
    sparse  默认。每天约 10 条摘要 —— 够测**逻辑**，不够测"量"。
    real    按真实上报节奏铺开（屏幕每 3 分钟、App 每 10 分钟一轮、PC/MCU 每 5–10 分钟），
            每天约 800–1200 条、三台设备。**这是建模出来的典型节奏，不是从真实库里抄的**
            （避免为了压测去翻人家的原始生活数据）。
            同时会造 decisions（决策日志）与 feedback（✓/✗），供 replay_gap / tune_gap 使用。
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

# 真实密度下的指标节奏（分钟一次）——数值是"典型手机/电脑/单片机"的建模节奏
CADENCE = {
    "screen.active_minutes": 3,
    "screen.idle_minutes": 3,
    "app.usage_minutes": 10,
    "steps.total": 15,
    "device.battery_percent": 15,
    "health.heart_rate": 30,
    "health.spo2": 30,
    "pc.continuous_active_minutes": 5,
    "pc.mem_percent": 10,
    "pc.disk_free_percent": 10,
    "pc.uptime_hours": 10,
    "pc.window_switches_today": 10,
    "temp": 10,
    "hum": 10,
}
APP_CATEGORIES = [("哔哩哔哩", "短视频/视频"), ("微信", "社交"), ("淘宝", "购物/生活"),
                  ("王者荣耀", "游戏"), ("学习通", "学习")]


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
    """稀疏模式的"某天摘要"行：(metric, value, unit, meta, device)"""
    out = []
    base = carry.get("screen", 300)
    screen = max(30, int(rng.gauss(base, 45)))
    if scenario == "spike" and rng.random() < 0.12:
        screen = int(screen * 10)                     # 单日突变
    out.append(("screen.active_minutes", screen, "min", {}, "phone_fake"))

    name = "哔哩哔哩" if day < carry["rename_at"] else "bilibili"
    out.append(("app.usage_minutes", rng.randint(20, 150), "min", {"app": name, "category": "短视频/视频"}, "phone_fake"))
    out.append(("app.usage_minutes", rng.randint(5, 60), "min", {"app": "微信", "category": "社交"}, "phone_fake"))

    if carry.get("flat"):
        out.append(("steps.total", 3000.0, "步", {}, "phone_fake"))
    else:
        out.append(("steps.total", float(rng.randint(800, 9000)), "步", {}, "phone_fake"))

    out.append(("sleep.total_minutes", float(rng.randint(300, 520)), "min", {}, "watch_fake"))
    out.append(("health.heart_rate", float(rng.randint(58, 92)), "bpm", {}, "watch_fake"))
    out.append(("health.spo2", 98.0, "%", {}, "watch_fake"))
    out.append(("weather.now", round(rng.uniform(18, 34), 1), "C",
                {"city_code": "101280101", "city": "广州", "desc": "晴"}, "server"))
    if rng.random() < 0.3:
        out.append(("order.event", 1.0, "", {"kind": "快递", "amount_range": "0-50"}, "phone_fake"))
    return out


def dense_rows_for_day(rng, day: datetime, carry: dict):
    """真实密度：一整天按分钟级节奏铺开。返回 [(metric, value, unit, meta, device, ts)]

    口径提醒（跟真实采集器一致）：**累计值**（当天累计分钟、当天窗口切换数）每次上报
    都是"今天到目前为止"，中枢取当天最新 —— 所以这里也这么造，不是增量。
    """
    rows = []
    wake = day + timedelta(hours=7, minutes=rng.randint(0, 40))
    sleep_at = day + timedelta(hours=23, minutes=rng.randint(10, 55))
    total_screen = max(40, int(rng.gauss(carry.get("screen", 300), 60)))
    idle_after = day + timedelta(hours=22, minutes=rng.randint(0, 40))

    t = wake
    while t < sleep_at:
        mins = (t - wake).total_seconds() / 60.0
        frac = min(1.0, mins / max(1.0, (sleep_at - wake).total_seconds() / 60.0))
        # 屏幕：当天累计
        rows.append(("screen.active_minutes", round(total_screen * frac, 1), "min", {}, "phone_fake", t))
        # 空闲：最后一次亮屏距今多久（傍晚后开始变长）
        idle = 0.0 if t < idle_after else round((t - idle_after).total_seconds() / 60.0, 1)
        rows.append(("screen.idle_minutes", idle, "min", {}, "phone_fake", t))
        # App：一天里每 10 分钟各分类累加
        for app, cat in APP_CATEGORIES:
            add = rng.randint(0, 4)
            if add:
                carry["apps"][app] = carry["apps"].get(app, 0) + add
                rows.append(("app.usage_minutes", float(carry["apps"][app]), "min",
                             {"app": app, "category": cat}, "phone_fake", t))
        rows.append(("steps.total", float(int(carry["steps"] + rng.randint(0, 18))), "步", {}, "phone_fake", t))
        carry["steps"] += rng.randint(0, 18)
        rows.append(("device.battery_percent", float(max(5, carry["battery"])), "%", {}, "phone_fake", t))
        if t.hour >= 20:
            carry["battery"] -= rng.choice([0, 0, 1])
        # 健康（穿戴，每 30 分钟）
        if t.minute % 30 == 0:
            rows.append(("health.heart_rate", float(rng.randint(56, 96)), "bpm", {}, "watch_fake", t))
            rows.append(("health.spo2", float(rng.choice([96, 97, 98, 98, 99])), "%", {}, "watch_fake", t))
        # PC（每 5/10 分钟）
        if t.minute % 5 == 0:
            rows.append(("pc.continuous_active_minutes", float(carry["pc_sit"]), "min", {}, "laptop_fake", t))
            carry["pc_sit"] = 0 if rng.random() < 0.12 else carry["pc_sit"] + 5
        if t.minute % 10 == 0:
            rows.append(("pc.mem_percent", round(rng.uniform(45, 88), 1), "%", {}, "laptop_fake", t))
            rows.append(("pc.disk_free_percent", round(rng.uniform(8, 22), 1), "%", {}, "laptop_fake", t))
            rows.append(("pc.uptime_hours", round(carry["pc_uptime"], 1), "h", {}, "laptop_fake", t))
            rows.append(("pc.window_switches_today", float(carry["switches"]), "次", {}, "laptop_fake", t))
            carry["switches"] += rng.randint(0, 14)
            carry["pc_uptime"] += 10 / 60.0
            # 单片机
            rows.append(("temp", round(24 + 4 * frac + rng.uniform(-0.4, 0.4), 1), "C", {}, "stm32_room", t))
            rows.append(("hum", round(55 + rng.uniform(-6, 6), 1), "%", {}, "stm32_room", t))
        t += timedelta(minutes=3)

    # 一天一次的东西
    rows.append(("sleep.total_minutes", float(rng.randint(300, 520)), "min", {}, "watch_fake", wake))
    rows.append(("weather.now", round(rng.uniform(18, 34), 1), "C",
                 {"city_code": "101280101", "city": "广州", "desc": rng.choice(["晴", "多云", "小雨"])}, "server", wake))
    for _ in range(rng.randint(0, 3)):
        rows.append(("order.event", 1.0, "", {"kind": rng.choice(["快递", "外卖"]), "amount_range": "0-50"},
                     "phone_fake", day + timedelta(hours=rng.randint(9, 22))))
    for _ in range(rng.randint(0, 2)):
        rows.append(("music.track", 1.0, "", {"title": "某首歌", "artist": "某人"},
                     "phone_fake", day + timedelta(hours=rng.randint(10, 23))))
    for _ in range(rng.randint(1, 4)):
        rows.append(("calendar.event", 1.0, "", {"kind": "课"}, "phone_fake",
                     day + timedelta(hours=rng.randint(8, 20))))
    return rows


def mk_decisions(rng, day: datetime):
    """返回 (决策行, 反馈行) —— 两者**必须分开**，否则 executemany 会缺参数。"""
    """造一天的决策日志 + 一条反馈 —— 给 replay_gap / tune_gap 用。

    ctx 里存**做决定时用到的输入**（时间带/沉默次数/今日已说/料分/桶），
    这样以后回放是"真的重算"，而不是拿别人的结论猜（见 docs/DECISIONS.md）。
    """
    out_rows, fb_rows = [], []
    said_today = 0
    band = rng.choice(["工作日·早上", "工作日·白天", "工作日·晚上", "周末·白天", "周末·晚上"])
    for k in range(rng.randint(18, 42)):
        t = day + timedelta(minutes=rng.randint(0, 1439))
        material = rng.choice([0, 0, 1, 1, 2, 2, 3, 4])
        speak = material >= 2 and rng.random() < 0.35
        if speak:
            said_today += 1
        reason = ("期望效用够（p_接受=0.68）" if speak else
                  "期望效用不够（p_接受=0.50[全局] < 0.67，料=%d）" % material)
        out_rows.append({
            "ts": t.replace(microsecond=0).isoformat(), "day": day.strftime("%Y-%m-%d"),
            "kind": "speak" if speak else "silent", "gap_sec": rng.randint(300, 5400),
            "reason": reason, "material": material, "said": said_today, "band": band,
            "ctx": json.dumps({"band": band, "material": material, "said": said_today,
                               "silent": k - said_today, "hour": t.hour}, ensure_ascii=False),
        })
    # ★ 反馈必须**依赖料分与疲劳度**，否则调参表就是废话：
    #   如果命中率和阈值无关，那"降阈值 → 多说 → 接受率不变"，任何参数都不会变差，
    #   回放就退化成"永远推荐多说"。
    #   所以这里建模两条真实规律：① 料越足越容易被认可（material 每 +1 约 +10%）
    #                              ② 一天说太多会被烦（超过 6 条后每多 1 条 -6%）
    for d in out_rows:
        if d["kind"] != "speak":
            continue
        p_acc = 0.38 + 0.10 * int(d["material"]) - 0.06 * max(0, int(d["said"]) - 6)
        p_acc = min(0.92, max(0.05, p_acc + rng.uniform(-0.06, 0.06)))
        fb_rows.append({"verdict": "up" if rng.random() < p_acc else "down", "band": band,
                        "day": day.strftime("%Y-%m-%d"),
                        "ts": (day + timedelta(hours=rng.randint(8, 22))).replace(microsecond=0).isoformat()})
    return out_rows, fb_rows


def gen(days: int, seed: int, home: pathlib.Path, scenario: str, density: str):
    rng = random.Random(seed)
    hub = load_hub(home)
    try:
        hub.init_db()
    except Exception:
        pass
    start = datetime.now(TZ).replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=days)
    carry = {"screen": 300, "flat": False, "rename_at": start + timedelta(days=max(1, days // 2))}

    inserted = 0
    decs, fbs = [], []
    ins = ("INSERT OR REPLACE INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
           "VALUES (?,?,?,?,?,?,?,?,?)")
    with hub.db() as c:                     # ★ 全程一个连接 + 一天一提交：20 万行也不会慢
        for i in range(days):
            d = start + timedelta(days=i)
            if scenario in ("all", "gap") and 10 <= i < 24:
                continue                    # 整段缺失
            carry["flat"] = scenario in ("all", "flat") and 5 <= i < 12
            broken = (scenario in ("all", "broken")) and i % 7 == 3
            carry["apps"], carry["steps"], carry["battery"] = {}, 0, 100
            carry["pc_sit"], carry["switches"], carry["pc_uptime"] = 0, 0, rng.uniform(1, 12)
            dstr = d.strftime("%Y-%m-%d")

            if density == "real":
                batch = dense_rows_for_day(rng, d, carry)
                if broken:
                    batch = batch[:2]
                for metric, value, unit, meta, device, ts in batch:
                    c.execute(ins, (ts.replace(microsecond=0).isoformat(), dstr, device, metric, value,
                                    unit, "fake", 1.0, json.dumps(meta, ensure_ascii=False)))
                    inserted += 1
                _d, _f = mk_decisions(rng, d)
                decs.extend(_d); fbs.extend(_f)
            else:
                rows = rows_for_day(rng, d, scenario, carry)
                if broken:
                    rows = rows[:1]
                for k, (metric, value, unit, meta, device) in enumerate(rows):
                    ts = d + timedelta(hours=8 + (k % 10), minutes=rng.randint(0, 59))
                    ts_s = ts.isoformat() if k % 2 == 0 else ts.replace(tzinfo=None).isoformat()
                    c.execute(ins, (ts_s, dstr, device, metric, value, unit, "fake", 1.0,
                                    json.dumps(meta, ensure_ascii=False)))
                    inserted += 1

            # 跨零点：23:58 一条 + 次日 00:02 一条
            if scenario in ("all", "midnight") and i % 5 == 0:
                for off, hm in ((0, "23:58"), (1, "00:02")):
                    ts = (d + timedelta(days=off)).strftime("%Y-%m-%dT" + hm + ":00+08:00")
                    c.execute(ins, (ts, ts[:10], "phone_fake", "screen.active_minutes", 1.0, "min", "fake", 1.0, "{}"))
                    inserted += 1

            if decs:
                c.executemany("INSERT INTO decisions(ts, day, kind, gap_sec, reason, material, said, band, ctx) "
                              "VALUES (:ts,:day,:kind,:gap_sec,:reason,:material,:said,:band,:ctx)", decs)
                decs = []
            if fbs:
                c.executemany("INSERT INTO feedback(ts, verdict, band, note, consumed) "
                              "VALUES (:ts,:verdict,:band,'',1)", fbs)
                fbs = []
            c.commit()
    return inserted


def main():
    ap = argparse.ArgumentParser(description="造合成历史库（不碰真实部署）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None, help="数据目录（默认临时目录）")
    ap.add_argument("--scenario", default="all",
                    choices=["all", "normal", "gap", "flat", "spike", "broken", "midnight"])
    ap.add_argument("--density", default="sparse", choices=["sparse", "real"],
                    help="sparse=每天约 10 条摘要（测逻辑）；real=按真实上报节奏（测量/压测）")
    a = ap.parse_args()

    home = pathlib.Path(a.out) if a.out else pathlib.Path(tempfile.mkdtemp(prefix="whale-fake-"))
    print(f"  合成库目录：{home}")
    n = gen(a.days, a.seed, home, a.scenario, a.density)
    db = home / "hub.db"
    size = db.stat().st_size if db.exists() else 0
    print(f"  ✓ 写入 {n:,} 行 · {a.days} 天 · 场景 {a.scenario} · 密度 {a.density} · 库 {size / 1048576:.1f} MB")
    print("  下一步：")
    print(f"     WHALE_HOME={home} python3 hub/tools/stress_report.py        # 压测报告")
    print(f"     WHALE_HOME={home} python3 hub/tools/tune_gap.py --grid      # 反事实调参")
    return 0


if __name__ == "__main__":
    sys.exit(main())

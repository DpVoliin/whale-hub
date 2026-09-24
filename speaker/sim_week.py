#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一周模拟：按"正常使用"的作息跑 7 天，看她的提醒到底正常不正常。

为什么要它：线上连着出了四次故障（思考 token 吃光预算 / 判重跨天 / 回执失败重发 /
补发过期提醒），每次都是**事后**从日志里翻出来的 ✗ —— 这个工具让同类问题**事前**暴露 ✓

它不重写一套逻辑 ✗，而是**直接调用说话层里那几个真函数**：
  · next_gap()                  → 这次该隔多久（含料多/在用/沉默的自适应）
  · topic_of() / topic_already_said_today() / topic_mark_said()   → 话题台账（事前一票否决）
  · too_similar() / fingerprint()                                  → 事实层判重
  · _in_window()                → 时段窗口（睡前/早间）
  · QUIET                       → 免打扰时段
  · GATE / C_FALSE / C_MISS     → 期望效用闸（Horvitz）
所以模拟出来的结果就是**真实策略**的结果 ✓（不生成文案：那要调模型，7 天几百次没必要 ✗）

用法：
    python3 sim_week.py                 # 默认 7 天，固定种子（可复现）
    python3 sim_week.py --days 14 --seed 7
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import random
import sys
from datetime import datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent


class FakeDatetime(datetime):
    """把说话层内部的"现在"换成模拟时间。

    为什么必须：说话层的台账/时段窗口/免打扰都用 datetime.now(TZ) ✓
    模拟跑的是 09-21~27，而真实今天是 09-24 ✗ → 台账永远不重置 → 后面几天全被当成
    "今天说过了" → 一周模拟只剩 3 条 ✗（第一版就栽在这 ✓）
    """
    _now = None

    @classmethod
    def now(cls, tz=None):
        n = cls._now or datetime.now()
        return n if tz is None else n.astimezone(tz)


def set_clock(sp, when):
    FakeDatetime._now = when
    sp.datetime = FakeDatetime


def load_speaker(path: pathlib.Path):
    """把说话层当模块加载（只读它的函数，不启动它的主循环）。"""
    spec = importlib.util.spec_from_file_location("sp_sim", path)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except SystemExit:
        pass
    return m


# ─────────────────────────── 一天的作息（贴近主人的真实节奏）
def day_ctx(sp, day_index: int, weekday: bool, rng: random.Random):
    """生成这一天每个小时"她看到的数据"。数字取自真实量级（不是拍脑袋的 0/1）。"""
    rows = []
    screen = 60 if not weekday else 40           # 早上起来已经用了一会儿
    social = 30 if not weekday else 15
    wake = 8 if weekday else 10
    classes = [(8, 0), (9, 40), (14, 0)] if weekday else []
    for h in range(24):
        if h < wake:
            rows.append((h, {"sleep": True, "screen": screen, "social": social, "classes": classes, "asleep": True}))
            continue
        screen += rng.randint(20, 55)            # 每小时涨 20~55 分钟
        if rng.random() < 0.35:
            social += rng.randint(10, 45)
        facts = []
        if weekday and any(ch == h for ch, _ in classes):
            facts.append("课")
        if rng.random() < 0.06:
            facts.append(rng.choice(["订单", "快递", "电量低", "磁盘", "天气"]))
        if rng.random() < 0.10:
            facts.append("反常")
        rows.append((h, {"sleep": False, "screen": min(screen, 960), "social": min(social, 620),
                         "classes": classes, "facts": facts, "asleep": False}))
    return rows


def simulate(sp, days: int, seed: int):
    rng = random.Random(seed)
    start = datetime(2026, 9, 21, 0, 0)          # 周一
    log, stats = [], {"msgs": 0, "dups": 0, "quiet": 0, "window_bad": 0, "per_day": {},
                      "gate_blocked": 0, "cap_blocked": 0}
    said_today, last_said = {}, None
    for d in range(days):
        date = start + timedelta(days=d)
        weekday = date.weekday() < 5
        sp.SAID_TODAY.clear()
        said_count = 0
        rows = day_ctx(sp, d, weekday, rng)
        # 时间片：每 5 分钟看一次（跟真实轮询同粒度 ✓）
        for h, ctx in rows:
            for minute in range(0, 60, 5):
                now = date.replace(hour=h, minute=minute)
                set_clock(sp, now)
                if ctx.get("asleep"):
                    continue
                # 免打扰
                if h >= sp.QUIET[0] or h < sp.QUIET[1]:
                    stats["quiet"] += 1
                    continue
                # 间隔：用**真的** next_gap
                gap, why = sp.next_gap({"hour": h, "screen": ctx.get("screen"), "social": ctx.get("social"),
                                        "facts": ctx.get("facts") or [], "weekday": weekday})
                if last_said is not None and (now - last_said).total_seconds() < gap:
                    continue
                # 期望效用闸：调**真函数**（分桶 Thompson 后验 + Horvitz 阈值）
                material = len(ctx.get("facts") or []) + (1 if ctx.get("social", 0) > 300 else 0) \
                           + (1 if ctx.get("screen", 0) > 480 else 0)
                ok_gate, gate_why = sp.utility_gate(material)
                if not ok_gate:
                    stats["gate_blocked"] += 1
                    continue
                # 日限（硬顶 ✓）
                if said_count >= sp.SAY_MAX_PER_DAY:
                    stats["cap_blocked"] += 1
                    continue
                # 她会说什么（话题来自当天数据；不生成文案 ✓）
                topic = ""
                text = ""
                if ctx.get("facts"):
                    f = ctx["facts"][0]
                    text = {"课": "第一节 09:40 在 7-506", "订单": "有个快递到了", "快递": "快递到了",
                            "电量低": "手机电量 12% 未充电", "磁盘": "电脑磁盘只剩 1.1%",
                            "天气": "今天有雷阵雨", "反常": "屏幕使用比平时多 93%"}.get(f, f)
                    topic = sp.topic_of(text)[0]
                if not text:
                    text = f"屏幕 {ctx.get('screen')} 分钟"
                    topic = sp.topic_of(text)[0]
                # 时段窗口（真函数）
                # 主动消息没有时段窗口 ✓（窗口只约束**定点简报**：睡前/早间）
                # 第一版我把主动消息也套了睡前窗口 → 误报 36 次"违规" ✗
                if not sp._in_window("proactive"):
                    stats["window_bad"] += 1
                    continue
                # 台账（真函数）：同一话题今天说过且没跨档 → 不说
                if sp.topic_already_said_today(text):
                    stats["dups"] += 1
                    log.append((now, "跳过-台账", topic, "今天说过且没跨档"))
                    continue
                # 事实层判重（真函数）
                if sp.too_similar(text):
                    stats["dups"] += 1
                    log.append((now, "跳过-相似", topic, "与最近说过的高度重合"))
                    continue
                # 开口 ✓
                said_count += 1
                stats["msgs"] += 1
                sp.topic_mark_said(text)
                sp.remember(text)
                last_said = now
                log.append((now, "开口", topic, text))
        stats["per_day"][date.strftime("%m-%d %a")] = said_count
    return log, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--speaker", default=str(HERE / "whale_speaker.py"))
    a = ap.parse_args()

    sp = load_speaker(pathlib.Path(a.speaker))
    # 用临时目录做状态，绝不碰真实台账 ✓
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="simweek-"))
    sp.SENT_PATH = tmp / "sent.jsonl"
    sp.RECENT_PATH = tmp / "said.jsonl"
    print(f"  说话层: {a.speaker}")
    print(f"  隔离状态目录: {tmp}")
    print(f"  免打扰: {sp.QUIET[0]}:00–{sp.QUIET[1]}:00 · 间隔下限 {sp.GAP_MIN // 60} 分钟 · 日限 {getattr(sp, 'DAILY_MAX', '?')}")
    print()

    log, stats = simulate(sp, a.days, a.seed)
    print(f"  {'=' * 62}\n  {a.days} 天模拟结果\n  {'=' * 62}")
    for day, n in stats["per_day"].items():
        bar = "█" * n
        print(f"   {day}  {n:>2} 条  {bar}")
    total = stats["msgs"]
    print(f"\n   合计开口 {total} 条（{a.days} 天，平均 {total / a.days:.1f} 条/天）")
    print(f"   被台账/相似度拦下 {stats['dups']} 次（拦下=没打扰 ✓ 但次数太高说明阈值偏松 ✗）")
    print(f"   期望效用闸拦下 {stats['gate_blocked']} 次（p(接受) 不够 → 正常 ✓）")
    print(f"   日限拦下 {stats['cap_blocked']} 次（硬顶 {sp.SAY_MAX_PER_DAY} 条/天 ✓）")
    print(f"   免打扰时段被跳过 {stats['quiet']} 次（应该全跳过 ✓）")
    print(f"   时段窗口违规 {stats['window_bad']} 次（必须为 0 ✗）")
    print()
    print("  ── 抽样看几条她真会说的话 ──")
    for ts, kind, topic, txt in log[:14]:
        print(f"   {ts.strftime('%m-%d %H:%M')}  [{kind}] {topic:<10} {txt[:44]}")
    print()
    bad = stats["window_bad"] > 0
    print("  判定:", "✗ 有问题（见上）" if bad else "✓ 一周内没有时段错位、没有重复打扰")
    print(f"  （结果可复现：--seed {a.seed}）")


if __name__ == "__main__":
    sys.exit(main())

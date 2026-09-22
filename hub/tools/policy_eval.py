#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""policy_eval.py —— 离线策略评估：让"她该不该开口"第一次有数字可看。

为什么需要它（外部评审 v3 的 P0 第一条）：
  这套系统有四个有文献出处的算法，但**没有任何离线指标**能回答一个最基本的问题 ——
  "这套开口策略，是不是真比『每天早八点固定提醒』好？" 没有这个，算法再漂亮也只是主张。

怎么做（**回放式评估**，bandit 离线评估里最朴素的 honest 版本）：
  1. 从 `decisions` 表读历史决策，每条都带 `ctx`（当时的输入：band / material / said / hour…）
  2. 从 `feedback` 表学一个**结果模型** p̂(接受 | 桶)：直接用中枢自己的 `band_stats()`
     （含证据强度加权 + 45 天半衰期衰减），桶样本不足就退回全局后验
  3. 把同一批 `ctx` 分别喂给几条候选策略，算每条策略的**期望效用**：
        开口 → p̂·V − (1−p̂)·C_FALSE
        沉默且当时确实有料 → −p̂·C_MISS
  4. 报 接受率 / 效用 / bootstrap 置信区间 / 相对最好固定策略的 regret
  5. 做一次**成本权重敏感性**扫描（C_FALSE × C_MISS）—— 评审 3.3-2 问的
     "隐式反馈权重靠启发式，等于把偏差写死"，至少要让偏差**可见**

诚实边界（不夸大）：
  · 这是**回放**不是真在线 A/B：结果模型是从同一批反馈学的，存在自选择偏差；
    真要做无偏估计得上 IPS/SNIPS+倾向分数，而我们的倾向是**确定性策略**给的，
    只能靠结果模型 —— 所以这里给的是"上界式的相对比较"，不是绝对收益。
  · 决策条数少于 20 条时只打印提示，不给结论（样本太少的"评估"比没有更危险）。

用法：
    python3 hub/tools/policy_eval.py            # 人读的报告
    python3 hub/tools/policy_eval.py --json     # 给机器/CI
    python3 hub/tools/policy_eval.py --sweep    # 追加成本权重敏感性表
"""
import argparse
import json
import os
import pathlib
import random
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
os.environ.setdefault("WHALE_HOME", str(ROOT))


def load_hub():
    import importlib.util
    spec = importlib.util.spec_from_file_location("whalecare_eval", ROOT / "hub" / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 与 speaker/whale_speaker.py 的 UTIL_THRESHOLD 同构（Horvitz 1999）
V_ACCEPT = 1.0
C_FALSE = float(os.getenv("WHALE_C_FALSE", "2.0"))
C_MISS = float(os.getenv("WHALE_C_MISS", "1.0"))
THRESHOLD = C_FALSE / (C_FALSE + C_MISS)


def load_contexts(hub):
    """读决策日志里的 ctx（没有 ctx 的老记录跳过，并如实计数）。"""
    out, skipped = [], 0
    with hub.db() as c:
        rows = c.execute("SELECT ts, day, kind, reason, material, said, band, ctx "
                         "FROM decisions ORDER BY id ASC").fetchall()
    for r in rows:
        raw = (r["ctx"] or "").strip()
        ctx = {}
        if raw:
            try:
                ctx = json.loads(raw)
            except Exception:
                ctx = {}
        if not ctx:
            skipped += 1
            # 退而求其次：至少有 material / band 也算可用
            ctx = {"material": r["material"], "band": r["band"], "hour": None}
        ctx["_said"] = int(r["said"] or 0)
        ctx["_band"] = ctx.get("band") or r["band"] or "(无桶)"
        out.append(ctx)
    return out, skipped


def outcome_model(hub, min_n=4):
    """p̂(接受 | 桶) —— 直接复用中枢自己的 band_stats（含衰减与收缩）。"""
    st = hub.band_stats(min_n=min_n)
    glob = (st.get("global") or {}).get("p_accept", 0.5)

    def p_accept(band: str) -> float:
        b = (st.get("bands") or {}).get(band)
        if b and b.get("reliable"):
            return float(b["p_accept"])
        return float(glob)
    return p_accept, st


def replay(contexts, p_accept, policy: str, c_false=C_FALSE, c_miss=C_MISS):
    """把同一批 ctx 喂给一条策略，返回 {said, utility, expected_accept}。"""
    thr = c_false / (c_false + c_miss)
    said = util = exp_acc = 0.0
    for ctx in contexts:
        p = p_accept(ctx.get("_band") or ctx.get("band") or "(无桶)")
        material = int(ctx.get("material") or 0)
        hour = ctx.get("hour")
        if policy == "always":
            speak = True
        elif policy == "never":
            speak = False
        elif policy == "fixed_8am":
            speak = (hour == 8)
        elif policy == "random":
            speak = (hash((ctx.get("band"), material, hour)) % 2 == 0)
        else:                                   # "gate"：当前这套
            speak = (material >= 1 and p > thr)
        if speak:
            said += 1
            exp_acc += p
            util += p * V_ACCEPT - (1 - p) * c_false
        elif material >= 1:
            util -= p * c_miss
    n = max(1, len(contexts))
    return {"policy": policy, "said": int(said), "utility": round(util / n, 4),
            "expected_accept_rate": round(exp_acc / said, 4) if said else 0.0,
            "say_rate": round(said / n, 4)}


def bootstrap_ci(contexts, p_accept, policy, rounds=300, seed=7):
    """对 utility 做 bootstrap 置信区间（纯标准库，样本小也够用）。"""
    rnd = random.Random(seed)
    u = []
    n = len(contexts)
    if n < 5:
        return [None, None]
    for _ in range(rounds):
        sample = [contexts[rnd.randrange(n)] for _ in range(n)]
        u.append(replay(sample, p_accept, policy)["utility"])
    u.sort()
    return [round(u[int(0.05 * rounds)], 4), round(u[int(0.95 * rounds)], 4)]


def main() -> int:
    ap = argparse.ArgumentParser(description="离线评估开口策略（回放式）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sweep", action="store_true", help="追加成本权重敏感性扫描")
    a = ap.parse_args()

    hub = load_hub()
    contexts, skipped = load_contexts(hub)
    p_accept, st = outcome_model(hub)
    n = len(contexts)

    result = {"contexts": n, "skipped_no_ctx": skipped,
              "threshold": round(THRESHOLD, 4), "c_false": C_FALSE, "c_miss": C_MISS,
              "halflife_days": st.get("halflife_days"),
              "bands": len(st.get("bands") or {}), "policies": []}
    for pol in ("gate", "always", "never", "fixed_8am", "random"):
        r = replay(contexts, p_accept, pol)
        r["utility_ci90"] = bootstrap_ci(contexts, p_accept, pol)
        result["policies"].append(r)

    ranked = sorted(result["policies"], key=lambda x: -x["utility"])
    result["best_policy"] = ranked[0]["policy"]
    gate = next(x for x in result["policies"] if x["policy"] == "gate")
    best = ranked[0]
    result["regret_vs_best"] = round(best["utility"] - gate["utility"], 4)

    if a.sweep:
        sweep = []
        for cf in (1.0, 2.0, 3.0, 4.0):
            for cm in (0.5, 1.0, 2.0):
                r = replay(contexts, p_accept, "gate", c_false=cf, c_miss=cm)
                sweep.append({"c_false": cf, "c_miss": cm,
                              "threshold": round(cf / (cf + cm), 3),
                              "say_rate": r["say_rate"], "utility": r["utility"]})
        result["sweep"] = sweep

    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("  离线策略评估（回放式 · 结果模型来自你自己的反馈）")
    print(f"  决策样本 {n} 条" + (f"（另有 {skipped} 条老记录没有 ctx，已跳过）" if skipped else ""))
    print(f"  结果模型：{result['bands']} 个桶 · 全局 p(接受)={st.get('global', {}).get('p_accept')}"
          f" · 半衰期 {st.get('halflife_days')} 天")
    print(f"  当前门控阈值 p>{result['threshold']}（C_FALSE={C_FALSE} / C_MISS={C_MISS}）")
    print()
    print(f"  {'策略':<10} {'开口率':>7} {'接受率':>7} {'期望效用':>9} {'90% 区间':>18}")
    for r in result["policies"]:
        ci = r["utility_ci90"]
        ci_s = f"[{ci[0]}, {ci[1]}]" if ci[0] is not None else "样本太少"
        mark = " ←当前" if r["policy"] == "gate" else (" ←最好" if r["policy"] == best["policy"] else "")
        print(f"  {r['policy']:<10} {r['say_rate']:>7.2f} {r['expected_accept_rate']:>7.2f}"
              f" {r['utility']:>9.4f} {ci_s:>18}{mark}")
    print()
    print(f"  相对最好策略（{best['policy']}）的 regret：{result['regret_vs_best']}")
    if n < 20:
        print("  ⚠ 样本不足 20 条，以上只作参考 —— 样本太少的\"评估\"比没有更危险。")

    if a.sweep:
        print()
        print("  成本权重敏感性（评审 3.3-2：别让启发式权重被写死还不自知）")
        print(f"  {'C_FALSE':>8} {'C_MISS':>7} {'阈值':>7} {'开口率':>7} {'期望效用':>9}")
        for s in result["sweep"]:
            print(f"  {s['c_false']:>8} {s['c_miss']:>7} {s['threshold']:>7} "
                  f"{s['say_rate']:>7.2f} {s['utility']:>9.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

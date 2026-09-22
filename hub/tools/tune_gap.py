#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tune_gap.py —— 反事实回放调参：把"改阈值 → 用几天 → 凭感觉"换成"在历史上重算一遍"。

它做什么：
    拿 decisions（含 ctx = 做决定时的输入）+ feedback，**按时间轴重放**：
    每走到一个决策点，只用"那一刻之前"已经收到的反馈构造后验，
    然后换一组候选参数重算"当时会不会开口"。于是能看到：
        阈值 0.67 → 0.55 时，开口从 X 条/天 变成 Y 条/天，预计被认可多少、打扰多少。

三条硬规矩（不做的话这份报告就是自欺）：
    1. **只用当时可见的信息**（后验按时间推进），不许拿未来反馈调过去的决定。
    2. **留出法**：前 70% 的天数用来挑参数，后 30% 只用来验证 —— 否则就是过拟合历史。
    3. **样本数必须打印**。反馈 < 30 条时结论只能当"方向"，不能当"结论"（脚本会自己标出来）。

用法：
    WHALE_HOME=/tmp/sim180 python3 hub/tools/tune_gap.py --grid
    python3 hub/tools/tune_gap.py --file decisions.json --grid   # 用 /decisions 的快照
"""
import argparse
import json
import os
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# 与说话层一致的默认（改这里要同步 speaker/whale_speaker.py 的 UTIL_THRESHOLD）
DEFAULT_THRESHOLD = 0.67
MATERIAL_BYPASS = 3          # 料足（≥3）时即使后验偏低也允许开口


def load_rows(home=None, file=None):
    if file:
        d = json.loads(pathlib.Path(file).read_text(encoding="utf-8"))
        return d.get("items", d if isinstance(d, list) else []), []
    import sqlite3
    db = pathlib.Path(home or (ROOT / "hub")) / "hub.db"
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    dec = [dict(r) for r in c.execute("SELECT * FROM decisions ORDER BY ts ASC")]
    fb = [dict(r) for r in c.execute("SELECT * FROM feedback ORDER BY ts ASC")]
    return dec, fb


def posterior(ok, bad):
    return (1 + ok) / (2 + ok + bad)          # Beta(1,1) 先验，与中枢一致


def gate(material, p_accept, thr):
    """与说话层 utility_gate() 同一条判据：期望效用不够时，只有料足才放行。"""
    if p_accept >= thr:
        return True
    return material >= MATERIAL_BYPASS


def replay(decisions, feedback, thr):
    """按时间轴重放。返回逐条结果列表（带上当时可见的后验）。"""
    fb = sorted(feedback, key=lambda f: str(f.get("ts") or ""))
    di = 0
    ok = bad = 0
    out = []
    for d in sorted(decisions, key=lambda x: str(x.get("ts") or "")):
        ts = str(d.get("ts") or "")
        while di < len(fb) and str(fb[di].get("ts") or "") <= ts:   # ★ 只用"当时已经收到"的反馈
            v = str(fb[di].get("verdict") or "")
            if v in ("up", "1", "good", "yes"):
                ok += 1
            elif v in ("down", "0", "bad", "no"):
                bad += 1
            di += 1
        p = posterior(ok, bad)
        material = int(d.get("material") or 0)
        out.append({
            "ts": ts, "day": str(d.get("day") or ts[:10]),
            "actual": str(d.get("kind") or "") in ("speak", "said"),
            "replay": gate(material, p, thr),
            "p_accept": p, "material": material,
            "n_fb": ok + bad, "has_ctx": bool(str(d.get("ctx") or "").strip()),
        })
    return out


def summarize(rows):
    days = sorted({r["day"] for r in rows})
    n_day = max(1, len(days))
    spoke = [r for r in rows if r["replay"]]
    exp_acc = sum(r["p_accept"] for r in spoke)
    return {
        "days": len(days),
        "开口条数": len(spoke),
        "每天开口": round(len(spoke) / n_day, 2),
        "预计认可/天": round(exp_acc / n_day, 2),
        "预计打扰/天": round((len(spoke) - exp_acc) / n_day, 2),
        "平均p_接受(开口时)": round(statistics.mean([r["p_accept"] for r in spoke]), 3) if spoke else 0.0,
        # ★ 接受率才是可比的指标：认可/天 会随"开口数"单调上升，
        #   拿它当目标等于"永远推荐多说"（自证）。看率才看得出质量。
        "接受率": round((exp_acc / len(spoke)) if spoke else 0.0, 3),
    }


def main():
    ap = argparse.ArgumentParser(description="反事实回放调参（只读）")
    ap.add_argument("--file", default="", help="用 /decisions 的 JSON 快照（不带反馈）")
    ap.add_argument("--grid", action="store_true", help="扫一遍阈值网格")
    ap.add_argument("--thresholds", default="0.40,0.45,0.50,0.55,0.60,0.65,0.67,0.70,0.75,0.80")
    ap.add_argument("--holdout", type=float, default=0.7, help="前 N 比例用来挑参数，其余只验证")
    ap.add_argument("--min-accept", type=float, default=0.5, dest="min_accept",
                    help="接受率下限：在这个前提下挑「开口最多」的阈值（默认 0.50）")
    a = ap.parse_args()

    dec, fb = load_rows(os.environ.get("WHALE_HOME"), a.file or None)
    if not dec:
        raise SystemExit("没有决策记录 —— 先在合成库上跑（--file /decisions 快照 也行）")
    with_ctx = sum(1 for d in dec if str(d.get("ctx") or "").strip())
    print("## 反事实回放调参\n")
    print("- 决策 %d 条 · 反馈 %d 条 · 带 ctx 的决策 %d 条（%d%%）"
          % (len(dec), len(fb), with_ctx, 100 * with_ctx // max(1, len(dec))))
    if not with_ctx:
        print("- ⚠ 决策里**没有 ctx**（旧数据）：只能重算 `utility_gate`，"
              "间隔/节奏那部分无法回放 —— 这也正是 v0.1.16 起把 ctx 存下来的原因。")
    if len(fb) < 30:
        print("- ⚠ **反馈只有 %d 条**：下面的数字是「方向」，不是「结论」。"
              "（合成数据的 ✓/✗ 是按规则造的，只能验机制不能当真）" % len(fb))
    print()

    days = sorted({str(d.get("day") or str(d.get("ts"))[:10]) for d in dec})
    split = days[int(len(days) * a.holdout)] if len(days) > 3 else None
    train = [d for d in dec if str(d.get("day") or "") < split] if split else dec
    test = [d for d in dec if str(d.get("day") or "") >= split] if split else []

    thr_list = [float(x) for x in a.thresholds.split(",") if x.strip()]
    if not a.grid:
        thr_list = [DEFAULT_THRESHOLD]

    print("| 阈值 | 训练段 开口/天 | 训练段 接受率 | 训练段 打扰/天 | 留出段 开口/天 | 留出段 接受率 |")
    print("|---|---|---|---|---|---|")
    best, rows_out = None, []
    for thr in thr_list:
        s_tr = summarize(replay(train, fb, thr))
        s_te = summarize(replay(test, fb, thr)) if test else {"每天开口": float("nan"), "预计认可/天": float("nan")}
        rows_out.append((thr, s_tr, s_te))
        te_ok = s_te.get("接受率")
        mark = ""
        if te_ok is not None and te_ok >= a.min_accept:      # ★ 守住接受率的前提下，取开口最多的
            if best is None or s_te["每天开口"] > best[1]:
                best, mark = (thr, s_te["每天开口"], te_ok), " ←"
        print("| %.2f%s | %.2f | %.3f | %.2f | %.2f | %s |" % (
            thr, mark, s_tr["每天开口"], s_tr["接受率"], s_tr["预计打扰/天"],
            s_te["每天开口"], ("%.3f" % te_ok) if te_ok is not None else "—"))
    print()

    cur = replay(dec, fb, DEFAULT_THRESHOLD)
    s_cur = summarize(cur)
    flips = [r for r in cur if r["replay"] != r["actual"]]
    print("## 现状（阈值 %.2f）与偏差" % DEFAULT_THRESHOLD)
    print("- 重放结果：每天开口 %.2f 条 · 预计认可 %.2f · 预计打扰 %.2f"
          % (s_cur["每天开口"], s_cur["预计认可/天"], s_cur["预计打扰/天"]))
    print("- 与**实际记录**不一致的决策 %d 条（%.0f%%）——"
          " 这部分就是「光看日志看不出来」的偏差，值得回头查一下原因。"
          % (len(flips), 100 * len(flips) / max(1, len(cur))))
    if flips[:3]:
        print("  例如：")
        for r in flips[:3]:
            print("    %s 实际=%s 重放=%s 料=%s p=%.2f"
                  % (r["ts"][5:16], "开口" if r["actual"] else "沉默",
                     "开口" if r["replay"] else "沉默", r["material"], r["p_accept"]))
    print()

    print("## 建议（口径：守住接受率 ≥ %.2f 的前提下，尽量多开口）" % a.min_accept)
    if best:
        print("- 留出段上满足条件的阈值是 **%.2f**：开口 %.2f 条/天、接受率 %.3f（当前 %.2f 是 %.2f 条/天、接受率 %s）。"
              % (best[0], best[1], best[2], DEFAULT_THRESHOLD, s_cur["每天开口"], s_cur["接受率"]))
    else:
        print("- **没有阈值能在留出段守住接受率 %.2f** —— 说明现在处在「再多说就开始招人烦」的区间，"
              "该做的不是降阈值，而是**提高料的质量**（素材打分/时间窗）。" % a.min_accept)
    print("- 请按**样本量**读它：反馈 %d 条、留出段 %d 条决策。"
          "样本不够就先只改一档、观察一周再动。" % (len(fb), len(test)))
    print()
    print("> 口径说明：`预计认可` = 对「会开口」的每一条，用**那一刻的后验** p(接受) 累加；"
          "`预计打扰` = 开口数 − 认可数。这是事后估计，不是保证值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

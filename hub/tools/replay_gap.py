#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策回放器：把 decisions 表的历史拉出来，看"她的节奏到底怎么运行的"。

外部评审点出的真问题：调参不能停在"改阈值 → 用几天 → 感觉不对 → 再改"。
有了结构化日志（间隔 / 理由 / 料分 / 已说条数 / 场景桶），调参就从"感觉"变成"看曲线"。

用法：
    python3 tools/replay_gap.py                    # 打线上
    python3 tools/replay_gap.py --file dump.json   # 用 /decisions 的快照跑
"""
import argparse
import json
import os
import ssl
import statistics as st
import urllib.request
from collections import Counter

HUB = os.environ.get("WHALE_HUB", "https://YOUR_SERVER:11443")
TOKEN = os.environ.get("WHALE_TOKEN", "")
CA = os.environ.get("WHALE_CA", "")


def load(args):
    if args.file:
        return json.load(open(args.file, encoding="utf-8"))
    ctx = ssl.create_default_context(cafile=CA) if CA else None
    req = urllib.request.Request(HUB.rstrip("/") + "/decisions?limit=500")
    req.add_header("X-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file")
    a = ap.parse_args()
    items = (load(a) or {}).get("items") or []
    if not items:
        print("  还没有决策日志（跑几天就有了）")
        return 0
    print("  样本 %d 条（覆盖 %d 天）" % (len(items), len({i.get("day") for i in items})))

    gaps = [i["gap_sec"] for i in items if i.get("gap_sec")]
    if gaps:
        g = sorted(x / 60.0 for x in gaps)
        print("  间隔分钟: 中位 %.0f ｜ 最小 %.0f ｜ 最大 %.0f ｜ 贴在 5/90 分钟边界上的比例 %.0f%%"
              % (st.median(g), g[0], g[-1],
                 100.0 * sum(1 for x in g if x <= 5.5 or x >= 89.5) / len(g)))
        print("     （边界比例过高 = 参数被钳死了，说明乘子已经顶到极限、该重新标定）")

    by_band = Counter(i.get("band") or "（无）" for i in items)
    print("  场景桶分布:", ", ".join("%s×%d" % (k, v) for k, v in by_band.most_common(5)))

    for m in (0, 1, 2, 3):
        sub = [i["gap_sec"] / 60.0 for i in items if i.get("material") == m and i.get("gap_sec")]
        if sub:
            print("  料分 %d → 平均间隔 %.0f 分钟（%d 次）" % (m, sum(sub) / len(sub), len(sub)))

    words = ["有料", "在用手机", "连续沉默", "今天已说", "相对自己反常", "数据"]
    cnt = Counter()
    for i in items:
        r = i.get("reason") or ""
        for w in words:
            if w in r:
                cnt[w] += 1
    print("  理由因子出现次数:", ", ".join("%s=%d" % (w, cnt[w]) for w in words))
    print("    （某因子一直是 0 = 那段逻辑从没生效，可以删掉）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

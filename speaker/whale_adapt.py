#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自适应模块（纯标准库，零依赖）：① 中文短句语义去重 ② Thompson 采样节奏。

依据（都是成熟方法，出处见 reports/算法清单）：
- 3-gram shingle + Jaccard：Broder 1997 / n-gram 相似度标准做法；中文**无需分词**
- Beta-Bernoulli 后验 + Thompson 采样：Chapelle & Li 2011 (NeurIPS)
- 小样本向全局收缩：经验贝叶斯（Gelman BDA）

设计原则：**只能微调，不能主导**。节奏乘子钳在 [0.7, 1.5]；样本少时收缩回 1.0。
不要因为一个噪声信号把"她"变成话痨或哑巴。
"""
import random
import re

# ─────────────────────────── ① 语义去重 ───────────────────────────
DUP_THRESHOLD = 0.55      # 3-gram Jaccard 达到多少判"同一件事"（在真实历史语料上标定，见 calibrate）


def shingles(text, k=3):
    """中文短句的无分词 k-gram 集合：去掉空白/标点后滑窗。

    为什么不用分词：标准库没有中文分词器（jieba 是第三方依赖），而
    "字符 n-gram + Jaccard" 对短句的效果已经够用，且不会因为分词错而崩。
    """
    s = re.sub(r"[\s\W_]+", "", text or "", flags=re.UNICODE)
    if not s:
        return frozenset()
    if len(s) <= k:
        return frozenset([s])
    return frozenset(s[i:i + k] for i in range(len(s) - k + 1))


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def near_dup(text, others, threshold=None):
    """text 与 others 里任意一条是否近似重复。返回 (bool, 最高相似度, 最像的那条)。"""
    th = DUP_THRESHOLD if threshold is None else threshold
    a = shingles(text)
    if not a:
        return False, 0.0, ""
    best, who = 0.0, ""
    for o in others:
        j = jaccard(a, shingles(o))
        if j > best:
            best, who = j, o
    return best >= th, round(best, 3), who


def num_overlap(text, others, need=2, ratio=0.66):
    """数字硬重合（原有规则，保留）：数字是硬事实，重复出现最刺眼。"""
    nums = frozenset(re.findall(r"\d+", text or ""))
    if len(nums) < need:
        return False
    for o in others:
        on = frozenset(re.findall(r"\d+", o or ""))
        common = nums & on
        if len(common) >= need and len(common) >= min(len(nums), len(on)) * ratio:
            return True
    return False


# ─────────────────────────── ② Thompson 采样节奏 ───────────────────────────
BAND_SHRINK_K = 5.0        # 桶样本少时向全局收缩的强度
MULT_LO, MULT_HI = 0.7, 1.5   # 节奏乘子的钳制区间（只能微调）
REWARD_WINDOW_MIN = 15     # 发出后多久内"他有新动作"算一次正反馈


def band_key(ts=None):
    """场景桶 = 星期类 × 时段带。

    星期类分工作/周末（作息差异大）；时段带沿用她现有分带（早/白天/睡前/深夜）。
    """
    import time as _t
    lt = _t.localtime(ts) if ts else _t.localtime()
    wk = "周末" if lt.tm_wday >= 5 else "工作日"
    minutes = lt.tm_hour * 60 + lt.tm_min
    if 6 * 60 + 30 <= minutes < 10 * 60:
        band = "早上"
    elif minutes >= 21 * 60 + 30 or minutes < 30:
        band = "睡前"
    elif minutes < 6 * 60 + 30:
        band = "深夜"
    else:
        band = "白天"
    return "%s·%s" % (wk, band)


def _posteriors(state):
    return state.get("bandit") or {}


def theta(state, key, global_key="_global"):
    """从桶（不足时向全局）的后验里采一个 θ ~ Beta(α, β)。"""
    b = _posteriors(state)
    a1, b1 = b.get(key, [1.0, 1.0])
    g1, g2 = b.get(global_key, [1.0, 1.0])
    n = (a1 - 1) + (b1 - 1)                       # 该桶已观测次数
    w = n / (n + BAND_SHRINK_K) if n > 0 else 0.0
    raw = random.betavariate(max(a1, 1e-3), max(b1, 1e-3))
    glob = random.betavariate(max(g1, 1e-3), max(g2, 1e-3))
    return w * raw + (1.0 - w) * glob


def gap_multiplier(state, key):
    """θ 高（这个场景他常接话）→ 间隔缩短；θ 低 → 拉长。钳制在 [0.7, 1.5]。

    注意：θ=0.5 时乘子正好 1.0（即"没有证据就别改她的习惯"）。
    """
    th = theta(state, key)
    m = 1.0 + (0.5 - th)
    return max(MULT_LO, min(MULT_HI, m)), round(th, 3)


def reward(state, key, ok):
    """回写一次反馈：ok=True → α+1（这个场景值得打扰）；False → β+1。"""
    b = state.setdefault("bandit", {})
    for k in (key, "_global"):
        a, bb = b.get(k, [1.0, 1.0])
        b[k] = [a + (1.0 if ok else 0.0), bb + (0.0 if ok else 1.0)]
    return b.get(key)


def pending_check(state, newest_metric_ts, now_ts=None):
    """代理反馈信号：她说完之后 REMARD_WINDOW 分钟内，他有没有"新动作"。

    ⚠️ 诚实说明：这是一个**代理信号**（他可能只是恰好动了手机，不代表看到了消息）。
    这也是为什么乘子只允许微调 —— 噪声信号不该拥有决定权。
    """
    import time as _t
    p = state.get("pending")
    if not p:
        return None
    now_ts = now_ts or _t.time()
    if now_ts - p.get("ts", 0) < REWARD_WINDOW_MIN * 60:
        return None                                    # 观察窗还没走完，等下一轮
    try:
        dt = _t.mktime(_t.strptime(newest_metric_ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        dt = None
    ok = bool(dt and dt >= p.get("ts", 0) - 60)
    left = reward(state, p.get("band") or "_global", ok)
    state.pop("pending", None)
    return {"band": p.get("band"), "ok": ok, "posterior": left}

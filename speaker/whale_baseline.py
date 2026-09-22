#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个人基线 + 稳健异常打分（纯标准库，零依赖）。

为什么要有它
------------
原来的 `material_score()` 是**拍脑袋阈值**：屏幕 ≥300 分钟就 +1 分、游戏 ≥60 分钟就 +1 分。
问题是 300 分钟对一个人是"今天有点多"、对另一个人是"日常"。所以"值不值得开口"应该
**相对他自己的历史**来判断 —— 这就是 surprise（惊讶度）。

算法（都在文献里成熟，且 stdlib 能实现）
--------------------------------------
1. **稳健中心与尺度**：中位数 median + MAD（median absolute deviation）。
   比均值/标准差抗离群点 —— 个人数据里"某天通宵 18 小时"会把 σ 拉爆。
   稳健 σ ≈ 1.4826 × MAD（正态下的无偏换算）。
2. **小样本收缩（shrinkage）**：z' = z × n/(n+k)。样本少时不敢下"反常"的结论，
   天数变多后自动变灵敏。这是经验贝叶斯里最朴素的收缩形式。
3. **尺度下限（floor）**：MAD 可能为 0（几天数值一样）→ 用 max(MAD, 5% × 中位数, 1) 兜底，
   否则会算出 `1/0` 或"差 1 分钟就 z=∞"的荒唐结论。
4. **只看对应方向**：屏幕/游戏/刷视频"偏多"才值得提；磁盘/内存"偏少/偏满"才有意义。
   单向 z，避免"今天特别少"也被当成料。
5. **退化策略**：n < MIN_DAYS(3) 时返回 surprise=0，调用方回落原来的硬阈值。

用起来
------
    import whale_baseline as bl
    series = bl.daily_series(fetch_json, "screen.active_minutes")   # {day: 当天最大值}
    s = bl.surprise(today_value, series, direction="high")
    if s["flag"]:
        print(s["text"])      # 例如「比平时多 42%（平时 316 分钟）」
"""
import datetime
import statistics as st

MIN_DAYS = 3          # 少于此天数不做基线判断（回落硬阈值）
SHRINK_K = 4.0        # 收缩强度：样本 n 天时，权重 = n/(n+k)
FLAG_Z = 1.5          # 收缩后 |z| 达到多少才算"反常"（保守，宁少勿滥）


def _today():
    return (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))).strftime("%Y-%m-%d")


def daily_series(fetch_json, metric, days=21, agg="peak"):
    """把 hub 的 /metrics 取回来，压成 {日期: 当天代表值}。

    agg="peak" ：当天最大值（累计类指标如 screen.active_minutes / app.usage_minutes；
                 设备每轮报"当天累计"，所以当天的最大值才是"这一天用了多少"）
    agg="last" ：当天最后一条（瞬时类指标如电量/温度）
    """
    try:
        d = fetch_json("/metrics?metric=%s&limit=2000" % metric) or {}
    except Exception:
        return {}
    per = {}
    for it in (d.get("items") or []):
        day = it.get("day")
        try:
            v = float(it.get("value"))
        except (TypeError, ValueError):
            continue
        if not day:
            continue
        if agg == "last":
            if day not in per or it.get("ts", "") > per[day][1]:
                per[day] = (v, it.get("ts", ""))
        else:
            per[day] = (max(v, per[day][0]) if day in per else v, it.get("ts", ""))
    return {k: v[0] for k, v in per.items()}


def robust(vals):
    """返回 (中位数, 稳健σ)，带尺度下限。"""
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return 0.0, 1.0
    med = st.median(vals)
    if len(vals) >= 2:
        mad = st.median([abs(v - med) for v in vals])
    else:
        mad = 0.0
    sigma = 1.4826 * mad
    sigma = max(sigma, abs(med) * 0.05, 1.0)     # ★ 尺度下限：别让 z 爆炸
    return med, sigma


def surprise(today_value, series, direction="high", today=None, min_days=MIN_DAYS):
    """今天的值相对自己历史有多反常。

    返回 dict(flag, z, z_shrunk, median, sigma, n, ratio, text)
      · flag    ：是否值得当成"料"（|z_shrunk| 达标且方向对）
      · ratio   ：今天 / 中位数（用于"比平时多 42%"这种话术）
      · text    ：可直接给模型看的一句对比事实
    """
    today = today or _today()
    hist = [v for d, v in (series or {}).items() if d != today]
    out = {"flag": False, "z": 0.0, "z_shrunk": 0.0, "median": None,
           "sigma": None, "n": len(hist), "ratio": None, "text": ""}
    if today_value is None or len(hist) < min_days:
        return out
    med, sigma = robust(hist)
    try:
        z = (float(today_value) - med) / sigma
    except Exception:
        return out
    n = len(hist)
    zs = z * (n / (n + SHRINK_K))               # ★ 小样本收缩
    ratio = (float(today_value) / med) if med else None
    out.update(z=round(z, 2), z_shrunk=round(zs, 2), median=round(med, 1),
               sigma=round(sigma, 2), ratio=(round(ratio, 2) if ratio else None))
    hit = zs >= FLAG_Z if direction == "high" else zs <= -FLAG_Z
    if hit:
        out["flag"] = True
        pct = int(round((ratio - 1) * 100)) if ratio else 0
        if direction == "high":
            out["text"] = "比平时多 %d%%（平时约 %.0f）" % (pct, med)
        else:
            out["text"] = "比平时少 %d%%（平时约 %.0f）" % (abs(pct), med)
    return out


# ── 本项目要用基线的几个指标（方向按"什么情况才值得开口"定）──
TRACKED = [
    ("screen.active_minutes", "high", "屏幕使用"),
    ("pc.continuous_active_minutes", "high", "连续活跃"),
    ("pc.window_switches_today", "high", "窗口切换"),
    ("app.game_minutes", "high", "游戏"),
    ("app.video_minutes", "high", "刷视频"),
]


def collect(fetch_json, today_ctx=None, days=21):
    """对所有被跟踪指标算一遍，返回 {标签: surprise 结果}（只包含够天数、算得出来的）。"""
    out = {}
    for metric, direction, label in TRACKED:
        series = daily_series(fetch_json, metric, days=days)
        hist = [v for d, v in series.items() if d != _today()]
        if len(hist) < MIN_DAYS:
            continue
        today_value = None
        try:
            today_value = series.get(_today())
        except Exception:
            pass
        if today_value is None:
            continue
        s = surprise(today_value, series, direction=direction)
        if s["median"] is not None:
            s["today"] = today_value
            out[label] = s
    return out


def top_signal(signals):
    """挑"最值得说的那一条"：按收缩后 |z| 排序取第一（正好对上角色卡的"只挑一条"）。"""
    if not signals:
        return None
    items = [(abs(v["z_shrunk"]), k, v) for k, v in signals.items() if v.get("flag")]
    if not items:
        return None
    items.sort(reverse=True, key=lambda x: x[0])
    _, label, v = items[0]
    return {"label": label, "text": v["text"], "z": v["z_shrunk"],
            "today": v.get("today"), "median": v.get("median")}

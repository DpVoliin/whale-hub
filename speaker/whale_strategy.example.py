#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例：自定义说话策略（复制成 whale_strategy.py 就生效）。

放哪：与 whale_speaker.py 同目录的 `whale_strategy.py`，或用环境变量指定：
    export WHALE_STRATEGY=/path/to/my_strategy.py

规则：
  · 两个函数都可选；**返回 None = 交回内置实现**（所以可以只改一个）
  · 抛异常/写错都不影响说话（自动退回内置，只记一行调试日志）
  · 改了文件**不用重启**（按 mtime 热加载）
  · 这是"策略实验"的位置：试完要么合进核心，要么删掉 —— 别让实验代码长期挂在外面

下面这个例子做的两件事都是**演示**，不建议直接用。
"""


def next_gap(ctx, quiet_hint=False):
    """自定义节奏。ctx 是她看到的那份脱敏上下文。返回 (秒, 理由)，或 None=交回内置。"""
    import time
    lt = time.localtime()
    if lt.tm_wday >= 5 and 9 <= lt.tm_hour <= 18:      # 周末白天
        return (45 * 60, "周末白天（自定义策略：少打扰）")
    return None


def material_score(ctx):
    """自定义料分。返回 (分数, [理由...])，或 None=交回内置。"""
    w = ctx.get("weather_today") or {}
    if isinstance(w.get("rain_prob"), (int, float)) and w["rain_prob"] >= 70:
        return (2, ["要下雨（自定义策略：加重）"])
    return None

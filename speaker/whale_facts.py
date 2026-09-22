#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事实层新颖度：解决"同一件事换 23 种说法反复说"。

真实语料标定结论（重要）：她那 54 条历史里，23 条是**同一个事实**（久坐 11 小时）
换着说法重复的；而两两 3-gram Jaccard 只有 0.00–0.35 —— **文本相似度抓不住它**
（动作前缀 + 措辞一变，n-gram 就全变）。所以去重必须发生在**事实层**，而不是措辞层：

    值没实质变化 = 没有新信息 = 不该再说（哪怕措辞完全不同）

配合：文本 3-gram Jaccard 只当**逐字/近似逐字**的兜底闸，不作为主判据。
"""

# 每个事实键配一个"粒度"：值落在同一格里就算"没变化"（避免 11 小时 03 分 vs 11 小时 12 分被当成新闻）
GRAIN = {
    "久坐": 30,          # 分钟：每 30 分钟才算一档
    "屏幕": 60,          # 分钟：每小时一档
    "短视频": 60,
    "社交": 60,
    "游戏": 60,
    "磁盘剩余": 5,        # 百分点：每 5% 一档
    "内存": 10,          # 百分点
    "电量": 10,          # 百分点
    "室温": 2,
    "湿度": 10,
    "开机时长": 1,
}


def _bucket(key, value):
    g = GRAIN.get(key)
    if not g:
        return value
    try:
        return int(float(value) // g)
    except Exception:
        return value


def fact_signature(ctx):
    """把"此刻值得说的事"压成 {事实键: 档位}。只放**重复风险高**的累计/状态型事实。

    注意：不把"有没有课/天气/快递"这类**事件型**放进来自动去重 —— 那些由中枢的
    简报与定点提醒负责，且一次说到就完了；这里专治"数值型状态被反复播报"。
    """
    out = {}
    c = ctx or {}
    def put(k, v):
        if isinstance(v, (int, float)):
            out[k] = _bucket(k, v)

    put("久坐", c.get("pc_continuous_active_minutes"))
    put("屏幕", c.get("screen_total_minutes_today"))
    cats = c.get("screen_usage_minutes_by_category") or c.get("screen_minutes_by_category") or {}
    if isinstance(cats, dict):
        for lab, v in cats.items():
            put(str(lab), v)                      # 分类名原样（短视频/视频、社交…）
    pc = c.get("pc_health") or {}
    put("磁盘剩余", pc.get("disk_free_percent"))
    put("内存", pc.get("mem_percent"))
    put("开机时长", pc.get("uptime_hours"))
    put("电量", c.get("battery_percent"))
    env = (c.get("env") or {})
    put("室温", env.get("temp"))
    put("湿度", env.get("hum"))
    return out


def novel_facts(ctx, last_sig, min_changed=1):
    """相对"上次说过的状态"，现在有哪些**真的变了**的事实。

    返回 (是否有新信息, 变了的事实列表, 当前签名)
    """
    now = fact_signature(ctx)
    if not last_sig:
        return True, list(now.items()), now
    changed = [(k, v) for k, v in now.items() if last_sig.get(k) != v]
    return (len(changed) >= min_changed), changed, now

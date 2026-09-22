#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例扩展：把任意一个 HTTP JSON 接口里的字段变成指标。

复制这个文件改成你自己的（比如阳台传感器 / 家里的旧路由器 / 公司内网的接口）：

    1) 改 NAME / DEVICE / INTERVAL_MINUTES / URL
    2) 改 pick() 里的字段映射
    3) 重启中枢，然后看 `GET /ext` 确认加载成功

只用标准库；你要 import 别的库也完全可以（那部分不随本仓库分发）。
"""
import json
import urllib.request

NAME = "示例：任意 HTTP JSON 接口"
DEVICE = "ext_example"
INTERVAL_MINUTES = 30

URL = "http://127.0.0.1:9/__no_such_endpoint__"      # ← 改成你自己的接口（这里故意指向一个不存在的端口）
TIMEOUT = 5


def pick(payload):
    """把接口返回的 JSON 映射成指标。返回 [] 表示这次没有可用的数。"""
    # 例子：接口返回 {"temp": 26.8, "hum": 58}
    out = []
    if isinstance(payload, dict):
        if isinstance(payload.get("temp"), (int, float)):
            out.append({"metric": "env.temp", "value": float(payload["temp"]), "unit": "C"})
        if isinstance(payload.get("hum"), (int, float)):
            out.append({"metric": "env.hum", "value": float(payload["hum"]), "unit": "%"})
    return out


def fetch():
    """取一次数。抛异常没关系（会被记进 /ext，下个周期再试）。"""
    req = urllib.request.Request(URL, headers={"User-Agent": "whalecare-ext/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    return pick(payload)

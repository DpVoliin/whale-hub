#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扩展示例（可直接用）：把**跑中枢这台服务器自己的状态**变成她的指标。

只用标准库、只读 /proc 与 /sys，不装任何东西。接上之后她就能说：
    （看了眼服务器）磁盘只剩 9% 了，日志该清一清。
    （翻了翻负载）负载 3.8，这会儿有点挤。

复制这个文件改成你自己的任何东西 —— 这就是"一个文件 = 一个数据源"的意思。
"""
import os

NAME = "服务器状态"
DEVICE = "hub_server"
INTERVAL_MINUTES = 10


def _mem_percent():
    """内存占用 %（用 MemAvailable 算，比 MemFree 更贴近"还能用多少"）。"""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, v = line.partition(":")
                try:
                    info[k.strip()] = float(v.strip().split()[0])
                except Exception:
                    pass
    except Exception:
        return None
    total = info.get("MemTotal")
    avail = info.get("MemAvailable") or info.get("MemFree")
    if not total or avail is None:
        return None
    return round(100.0 * (total - avail) / total, 1)


def _disk_free_percent(path="/"):
    try:
        st = os.statvfs(path)
        if st.f_blocks:
            return round(100.0 * st.f_bavail / st.f_blocks, 1)
    except Exception:
        pass
    return None


def _cpu_temp_c():
    for p in ("/sys/class/thermal/thermal_zone0/temp",):
        try:
            with open(p) as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        except Exception:
            continue
    return None


def fetch():
    out = []
    hv = _mem_percent()
    if hv is not None:
        out.append({"metric": "server.mem_percent", "value": hv, "unit": "%"})
    dv = _disk_free_percent("/")
    if dv is not None:
        out.append({"metric": "server.disk_free_percent", "value": dv, "unit": "%"})
    try:
        l1, l5, l15 = os.getloadavg()
        out.append({"metric": "server.load1", "value": round(l1, 2), "unit": "load"})
    except Exception:
        pass
    tv = _cpu_temp_c()
    if tv is not None:
        out.append({"metric": "server.cpu_temp", "value": tv, "unit": "C"})
    return out

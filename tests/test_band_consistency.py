#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桶名一致性测试：中枢的 band_now() 必须与说话层的 whale_adapt.band_key() 完全一致。

为什么单独测它：桶名是**纯字符串**约定 —— 两边对不上时不会有任何报错，
只会"桶后验永远取不到、静默退回全局"，而且两边用的是不同的时间来源
（中枢固定 +8，说话层原本用机器本地时区）→ 服务器时区一变就悄悄错位。
这里把一周里每一分钟都比一遍，任何漂移都会当场红。
"""
import importlib.util
import os
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_hub():
    home = tempfile.mkdtemp(prefix="whale-band-")
    os.environ["WHALE_HOME"] = home
    os.environ.setdefault("WHALE_QUIET", "1")
    spec = importlib.util.spec_from_file_location("hub_band_test", ROOT / "hub" / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_adapt():
    spec = importlib.util.spec_from_file_location("wa_band_test", ROOT / "speaker" / "whale_adapt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBandConsistency(unittest.TestCase):
    def test_一周里每一分钟都一致(self):
        hub, wa = load_hub(), load_adapt()
        TZ = timezone(timedelta(hours=8))
        start = datetime(2026, 3, 2, 0, 0, tzinfo=TZ)      # 周一
        bad = []
        for i in range(7 * 24 * 60):
            dt = start + timedelta(minutes=i)
            a, b = hub.band_now(dt), wa.band_key(dt.timestamp())
            if a != b:
                bad.append((dt.isoformat(), a, b))
        self.assertEqual(bad[:5], [], "桶名不一致（前 5 个）：%s" % bad[:5])

    def test_四个时段带都在(self):
        hub = load_hub()
        TZ = timezone(timedelta(hours=8))
        day = datetime(2026, 3, 2, 0, 0, tzinfo=TZ)        # 周一（工作日）
        got = {hub.band_now(day + timedelta(hours=h)) for h in (3, 8, 14, 22)}
        self.assertEqual(got, {"工作日·深夜", "工作日·早上", "工作日·白天", "工作日·睡前"})

    def test_周末分类正确(self):
        hub = load_hub()
        TZ = timezone(timedelta(hours=8))
        sat = datetime(2026, 3, 7, 14, 0, tzinfo=TZ)
        self.assertTrue(hub.band_now(sat).startswith("周末"), hub.band_now(sat))


if __name__ == "__main__":
    unittest.main()

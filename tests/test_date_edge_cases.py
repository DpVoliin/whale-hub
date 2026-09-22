#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日期边界：跨年窗口 + 闰日（DpVoliin/whalecare issue #13）。

为什么值得单独测：这套系统的"上期 vs 本期"全靠**日期字符串**做区间筛选
（`_series_avg(series, d1, d2)` 比较的是 `d1 <= d <= d2`）。字符串比较对
`YYYY-MM-DD` 是对的，但一旦有人把区间算错一格，**跨年**那天就会把一年前的数据
算进"上一周"，或者在闰年 2/29 上错位一天 —— 而且不会报错，只是数字悄悄不对。

这里钉四件事：
  ① 生成器跑满一年多（--days 400）时，日期键严格递增、不重复、覆盖足够天数
  ② 跨年区间筛选：一年前的值绝不能落进"上期"区间
  ③ 区间两端都是**闭区间**（d1、d2 当天都要算）
  ④ 闰日 `YYYY-02-29` 作为日期键能正确参与筛选与聚合（不依赖"今天恰好是哪天"）
"""
import importlib.util
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
HUB_SRC = ROOT / "hub" / "hub.py"
GEN = HERE / "make_fake_history.py"


def load_hub(home: pathlib.Path):
    import os
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_dates", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DateEdgeCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-dates-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass

    # ① 生成器跨年跑：日期键必须自洽
    def test_generator_over_a_year_is_wellformed(self):
        out = tempfile.mkdtemp(prefix="whale-gen-")
        r = subprocess.run([sys.executable, str(GEN), "--days", "400", "--seed", "7", "--out", out],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, f"生成器失败：{r.stderr[-400:]}")
        db = pathlib.Path(out) / "hub.db"
        self.assertTrue(db.is_file(), "生成器没写出 hub.db")
        c = sqlite3.connect(db)
        days = [x[0] for x in c.execute("SELECT DISTINCT day FROM metrics ORDER BY day")]
        c.close()
        self.assertGreaterEqual(len(days), 365, f"400 天的窗口只生成了 {len(days)} 个不同日期")
        self.assertEqual(days, sorted(days), "日期键不是递增的")
        self.assertEqual(len(days), len(set(days)), "日期键有重复")
        for d in days:
            self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$", f"日期键格式不对：{d}")

    # ② 跨年：一年前的值不能落进"上期"
    def test_last_year_value_never_enters_this_period(self):
        series = {
            "2025-09-01": 9999.0,      # 一年多以前
            "2026-09-01": 30.0,
            "2026-09-02": 30.0,
            "2026-09-08": 40.0,
            "2026-09-09": 40.0,
        }
        this_period, n = self.h._series_avg(series, "2026-09-08", "2026-09-14")
        last_period, m = self.h._series_avg(series, "2026-09-01", "2026-09-07")
        self.assertEqual(n, 2, "本期应只含 09-08 与 09-09")
        self.assertAlmostEqual(this_period, 40.0)
        self.assertEqual(m, 2, "上期应只含 09-01 与 09-02")
        self.assertAlmostEqual(last_period, 30.0, msg="一年前那个 9999 不许混进来")

    # ③ 闭区间：两端当天都算
    def test_range_is_inclusive_on_both_ends(self):
        s = {"2026-01-01": 10, "2026-01-02": 20, "2026-01-03": 30}
        avg, n = self.h._series_avg(s, "2026-01-01", "2026-01-03")
        self.assertEqual(n, 3, "两端当天都该算进来")
        self.assertAlmostEqual(avg, 20.0)

    # ④ 闰日：作为日期键能正常筛选/聚合（不依赖今天是不是闰年）
    def test_leap_day_key_round_trips_and_filters(self):
        leap = "2028-02-29"
        s = {"2028-02-28": 10, leap: 20, "2028-03-01": 30}
        # 字符串区间比较必须把闰日包进二月区间
        avg, n = self.h._series_avg(s, "2028-02-01", "2028-02-29")
        self.assertEqual(n, 2, "闰日必须落在二月区间内")
        self.assertAlmostEqual(avg, 15.0)
        avg2, n2 = self.h._series_avg(s, leap, leap)
        self.assertEqual((avg2, n2), (20, 1), "单日区间取闰日本身")
        # 再验一次"写到库里、按天聚合"这条路径（日期键就是聚合键）
        with self.h.db() as c:
            c.execute("DELETE FROM metrics WHERE metric='test.leap'")
            for day, v in (("2028-02-28", 10), (leap, 20), ("2028-03-01", 30)):
                for i in range(3):        # 满足"有效日"门槛（至少 3 条）
                    c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit) VALUES (?,?,?,?,?,'')",
                              (f"{day}T0{i + 1}:00:00+08:00", day, "leap_probe", "test.leap", v))
            c.commit()
            c.execute("DELETE FROM metrics WHERE metric='test.leap' AND day < '2028-01-01'")
            c.commit()
        # 闰日行必须能被按天取到（此处直接查库，因为 _day_series 的窗口是"从今天往前"，
        # 未来日期不在窗口内 —— 这是刻意的：不能让未来数据参与基线）
        with self.h.db() as c:
            got = dict(c.execute("SELECT day, MAX(value) FROM metrics WHERE metric='test.leap' GROUP BY day"))
        self.assertIn(leap, got, "闰日的行按天聚合后必须存在")
        self.assertEqual(got[leap], 20.0)

    # ⑤ 未来日期不该参与基线（顺带把"窗口是从今天往前"这条契约钉住）
    def test_future_days_are_outside_baseline_window(self):
        with self.h.db() as c:
            c.execute("DELETE FROM metrics WHERE metric='test.future'")
            for i in range(5):
                c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit) VALUES (?,?,?,?,?,'')",
                          (f"2099-01-0{i+1}T01:00:00+08:00", f"2099-01-0{i+1}", "fut_probe",
                           "test.future", 100 + i))
            c.commit()
            s = self.h._day_series(c, "test.future", days=30, kind="peak")
        self.assertEqual({}, s, "未来日期的数据不该进基线（否则基线会被未来的值污染）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

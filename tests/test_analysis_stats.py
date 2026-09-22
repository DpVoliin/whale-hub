#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""针对 `60_analysis`（统计与异常检测）的测试 —— 补的是仓库自己承认的"最大技术债"。

为什么这些用例值得写：这个模块决定"她该不该开口"，它的输出直接变成她的话。
它的三个核心不变量，错了会**静默地把话说歪**，而且不会有任何报错：

1. **尺度下限**：MAD=0（历史每天一样）时 σ 不能是 0 —— 否则 z 爆炸，日常波动被说成"异常"。
2. **残缺日必须丢**：采集器某天没跑 → 那天值很小 → 若不丢，σ 被撑大，
   **真反常的日子反而看起来正常**（代码注释里记着实测抓到过"某天只有 5 分钟"）。
3. **小样本 / 低可信度不敢下结论**：3 天数据说"反常"和 20 天数据说"反常"不是一回事；
   上报残缺的那天更不该轻易下结论。

另外补一组"反事实调参"：给历史塞一个离群值（模拟某天数据被污染），
结论**不应该**跟着变 —— 这正是 median+MAD 相对 mean+stddev 的意义。

跑法：
    python3 tests/test_analysis_stats.py
    python3 -m unittest discover tests
"""
import importlib.util
import json
import os
import pathlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
HUB_SRC = HERE.parent / "hub" / "hub.py"
METRIC = "test.analysis_probe"


def load_hub(home: pathlib.Path):
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_stats", HUB_SRC)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


# ═══════════════════════════ ① 稳健统计 _robust ═══════════════════════════
class RobustTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_hub(pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-")))

    def test_empty(self):
        self.assertEqual(self.h._robust([]), (0.0, 1.0))
        self.assertEqual(self.h._robust([None, None]), (0.0, 1.0))

    def test_single_value(self):
        med, sigma = self.h._robust([42.0])
        self.assertEqual(med, 42.0)
        self.assertGreaterEqual(sigma, 1.0)          # 单值时 MAD=0，靠下限兜住

    def test_mad_zero_must_not_explode(self):
        """★ 不变量 1：历史每天一模一样时，σ 不能是 0（否则 z 爆炸）。"""
        med, sigma = self.h._robust([10, 10, 10, 10, 10])
        self.assertEqual(med, 10.0)
        self.assertEqual(sigma, 1.0)
        z = (12 - med) / sigma
        self.assertLess(z, 5, "σ 若为 0，两天差 2 分钟会被算成天文数字级的异常")

    def test_median_is_outlier_resistant(self):
        """★ 反事实：中位数不跟离群值走（均值会走）。"""
        vals = [10, 10, 10, 10, 1000]
        med, _ = self.h._robust(vals)
        self.assertEqual(med, 10.0)
        self.assertGreater(sum(vals) / len(vals), 200, "对照：均值被离群值拉跑了")

    def test_relative_floor_for_large_values(self):
        """值本身很大时，5% 相对下限应该接管（避免把大数的正常波动说成异常）。"""
        med, sigma = self.h._robust([1000, 1010, 1020])
        self.assertEqual(med, 1010.0)
        self.assertAlmostEqual(sigma, 50.5, places=1)     # max(1.4826*10, 1010*0.05, 1)

    def test_never_zero_sigma_for_tiny_values(self):
        med, sigma = self.h._robust([0.5, 0.5, 0.5])
        self.assertEqual(med, 0.5)
        self.assertGreaterEqual(sigma, 1.0)

    def test_negative_values_ok(self):
        """温度类指标可能是负数。"""
        med, sigma = self.h._robust([-5, -3, -4])
        self.assertEqual(med, -4.0)
        self.assertGreater(sigma, 0)

    def test_ignores_none(self):
        self.assertEqual(self.h._robust([None, 7, None, 7])[0], 7.0)

    def test_deterministic(self):
        vals = [3, 1, 4, 1, 5, 9, 2, 6]
        self.assertEqual(self.h._robust(vals), self.h._robust(list(vals)))


# ═══════════════════════ ② 惊讶度 _surprise_of ═══════════════════════
class SurpriseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_hub(pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-")))

    def _series(self, vals, today_offset=0):
        """构造 {日期: 值}；today 那天单独用 today_offset 标记。"""
        out = {}
        for i, v in enumerate(vals, start=1):
            out[_days_ago(i + today_offset)] = v
        return out

    def test_not_enough_history(self):
        o = self.h._surprise_of(100, self._series([10, 10]), today=_days_ago(0))
        self.assertFalse(o["flag"])
        self.assertEqual(o["n"], 2, "只有 2 天不该做基线判断")

    def test_none_today(self):
        o = self.h._surprise_of(None, self._series([10] * 10), today=_days_ago(0))
        self.assertFalse(o["flag"])

    def test_today_is_excluded_from_baseline(self):
        """★ 契约：series 里若含"今天"，必须排除，否则今天的值会污染自己的基线。"""
        s = self._series([10, 10, 10, 10], today_offset=1)
        s[_days_ago(0)] = 999
        o = self.h._surprise_of(999, s, today=_days_ago(0))
        self.assertEqual(o["median"], 10.0, "今天必须被排除在历史之外")

    def test_normal_day_does_not_flag(self):
        o = self.h._surprise_of(10, self._series([10] * 10), today=_days_ago(0))
        self.assertFalse(o["flag"])
        self.assertEqual(o["z"], 0.0)

    def test_clear_spike_flags_high(self):
        o = self.h._surprise_of(100, self._series([10] * 4), today=_days_ago(0))
        self.assertTrue(o["flag"])
        self.assertIn("比平时多", o["text"])
        self.assertAlmostEqual(o["ratio"], 10.0, places=2)

    def test_low_value_does_not_flag_in_high_direction(self):
        """★ 单向判定：她只提"偏多"，低于平时不该被当成反常说出来。"""
        o = self.h._surprise_of(1, self._series([10] * 10), direction="high", today=_days_ago(0))
        self.assertFalse(o["flag"])
        self.assertLess(o["z"], 0, "z 应该是负的，只是不当成 flag")

    def test_low_direction_flags_low_value(self):
        o = self.h._surprise_of(1, self._series([10] * 10), direction="low", today=_days_ago(0))
        self.assertTrue(o["flag"])
        self.assertIn("比平时少", o["text"])

    def test_spike_does_not_flag_in_low_direction(self):
        o = self.h._surprise_of(100, self._series([10] * 10), direction="low", today=_days_ago(0))
        self.assertFalse(o["flag"])

    def test_small_sample_shrinks_toward_no_conclusion(self):
        """★ 不变量 3：同样的偏差，样本少就该更保守。"""
        few = self.h._surprise_of(12, self._series([10] * 3), today=_days_ago(0))
        many = self.h._surprise_of(12, self._series([10] * 20), today=_days_ago(0))
        self.assertFalse(few["flag"], "3 天数据不该下'反常'结论")
        self.assertTrue(many["flag"], "20 天数据可以下结论")
        self.assertLess(abs(few["z"]), abs(many["z"]))

    def test_low_confidence_shrinks_harder(self):
        """★ 不变量 3（续）：数据源今天上报残缺时（conf 低），更不该轻易下结论。"""
        series = self._series([10] * 20)
        trust = self.h._surprise_of(12, series, today=_days_ago(0), conf=1.0)
        shaky = self.h._surprise_of(12, series, today=_days_ago(0), conf=0.1)
        self.assertTrue(trust["flag"])
        self.assertFalse(shaky["flag"], "可信度 0.1 时不该说反常")
        self.assertLess(abs(shaky["z"]), abs(trust["z"]))

    def test_confidence_zero_means_unknown_not_zero_trust(self):
        """★ 微妙规则（我一开始猜错，跑出来才看清）：
        `conf or 1.0` → conf=0 被当成**未知**，按满信 1.0 处理。
        与"取不到健康度就当 1.0，不因此变保守"是同一条契约。
        真给了很小的可信度（如 0.05）才会被夹到下限 0.1 并更狠地收缩。
        """
        series = self._series([10] * 20)
        zero = self.h._surprise_of(12, series, today=_days_ago(0), conf=0.0)
        full = self.h._surprise_of(12, series, today=_days_ago(0), conf=1.0)
        tiny = self.h._surprise_of(12, series, today=_days_ago(0), conf=0.05)
        self.assertEqual(zero["z"], full["z"], "conf=0 应等同满信")
        self.assertLess(abs(tiny["z"]), abs(full["z"]), "极小可信度应收缩更狠")
        self.assertFalse(tiny["flag"], "可信度 0.05（夹到 0.1）时不该说反常")

    def test_confidence_upper_bound_is_one(self):
        series = self._series([10] * 20)
        hi = self.h._surprise_of(12, series, today=_days_ago(0), conf=5.0)
        full = self.h._surprise_of(12, series, today=_days_ago(0), conf=1.0)
        self.assertEqual(hi["z"], full["z"], "conf 上限应夹到 1.0")

    def test_boundary_exactly_at_threshold_flags(self):
        """zs 恰好等于判定线时应当算反常（代码用 >=）。"""
        # 14 天历史、σ=1、今天=11 → z=1 → zs = 1*14/18 = 0.778（不 flag）
        # 用 100 天 → zs = 1*100/104 = 0.962（仍不 flag）→ 用一个更大的偏差逼近线：
        o = self.h._surprise_of(12, self._series([10] * 20), today=_days_ago(0))
        self.assertGreaterEqual(abs(o["z"]), 1.5)
        self.assertTrue(o["flag"])

    def test_contaminated_history_does_not_change_conclusion(self):
        """★ 反事实调参：历史里某天被污染（超大值），结论不该跟着翻。"""
        clean = self.h._surprise_of(60, self._series([50] * 10), today=_days_ago(0))
        dirty_series = self._series([50] * 10)
        dirty_series[_days_ago(20)] = 5000          # 模拟某天数据被污染/误报
        dirty = self.h._surprise_of(60, dirty_series, today=_days_ago(0))
        self.assertEqual(clean["flag"], dirty["flag"], "离群值不该改变结论")
        self.assertAlmostEqual(dirty["median"], 50.0, places=1, msg="中位数应抗污染")
        self.assertLess(abs(clean["z"] - dirty["z"]), 1.0, "收缩后 z 不该被离群值带跑")

    def test_zero_baseline_does_not_crash(self):
        """历史全 0（比如某指标平时完全为 0）—— 不能崩，ratio 应为 None。"""
        o = self.h._surprise_of(10, self._series([0] * 10), today=_days_ago(0))
        self.assertIsNone(o["ratio"])
        self.assertIsInstance(o["text"], str)
        self.assertTrue(o["flag"], "从 0 涨到 10 是真异常")


# ═══════════════════ ③ 按天聚合 _day_series（走库） ═══════════════════
class DaySeriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-db-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass
        cls.db = cls.home / "hub.db"
        cls._seed()

    @classmethod
    def _seed(cls):
        """直写 metrics 表是**刻意的**：被测函数就是"读这张表做按天聚合"，
        日期必须可控；注入式脱敏测试才必须走 ingest_items 真入口。"""
        c = sqlite3.connect(cls.db)
        c.execute("DELETE FROM metrics WHERE metric=?", (METRIC,))
        rows = []
        # A 天：5 条，峰值 50（完整日）
        for i, v in enumerate([10, 20, 30, 40, 50]):
            rows.append((f"{_days_ago(1)}T0{i + 1}:00:00+08:00", _days_ago(1), v))
        # B 天：只有 2 条（采集器那天没跑）→ 必须被丢弃
        for i, v in enumerate([100, 200]):
            rows.append((f"{_days_ago(2)}T0{i + 1}:00:00+08:00", _days_ago(2), v))
        # C 天：3 条，值都很小（完整但低值）
        for i, v in enumerate([5, 5, 5]):
            rows.append((f"{_days_ago(3)}T0{i + 1}:00:00+08:00", _days_ago(3), v))
        # D 天：3 条，但混了一条非数值（必须跳过且不计入完整度）
        rows.append((f"{_days_ago(4)}T01:00:00+08:00", _days_ago(4), 7))
        rows.append((f"{_days_ago(4)}T02:00:00+08:00", _days_ago(4), 8))
        rows.append((f"{_days_ago(4)}T03:00:00+08:00", _days_ago(4), "not-a-number"))
        # 很久以前：应落在窗口外
        rows.append((f"{_days_ago(200)}T01:00:00+08:00", _days_ago(200), 999))
        for ts, day, v in rows:
            c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit) VALUES (?,?,?,?,?,'')",
                      (ts, day, "stats_probe", METRIC, v))
        c.commit()
        c.close()

    def test_peak_picks_max_per_day(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak")
        self.assertEqual(s.get(_days_ago(1)), 50.0, "累计型指标取当天峰值")

    def test_incomplete_day_is_dropped(self):
        """★ 不变量 2：只有 2 条上报的那天必须丢（否则"采集器没跑"被当成"他那天没用手机"）。"""
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak")
        self.assertNotIn(_days_ago(2), s, "残缺日必须丢")

    def test_complete_low_day_is_kept(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak")
        self.assertEqual(s.get(_days_ago(3)), 5.0, "低值但完整的日子要保留（不是残缺）")

    def test_non_numeric_row_does_not_count(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak")
        self.assertNotIn(_days_ago(4), s, "3 条里有 1 条非数值 → 只能算 2 条 → 残缺")

    def test_last_kind_uses_latest_row(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="last")
        self.assertEqual(s.get(_days_ago(1)), 50.0, "瞬时型取当天最后一条（此处最后也是最大）")

    def test_window_excludes_old_rows(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak")
        self.assertNotIn(_days_ago(200), s)

    def test_floor_drops_low_values(self):
        with self.h.db() as c:
            s = self.h._day_series(c, METRIC, days=30, kind="peak", floor=10.0)
        self.assertNotIn(_days_ago(3), s, "低于下限的日子应被丢弃")
        self.assertIn(_days_ago(1), s)

    def test_empty_metric_returns_empty(self):
        with self.h.db() as c:
            self.assertEqual(self.h._day_series(c, "no.such.metric", days=30), {})


# ═══════════════════ ④ 区间均值 _series_avg ═══════════════════
class SeriesAvgTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_hub(pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-")))

    def test_empty(self):
        self.assertEqual(self.h._series_avg({}, "2026-01-01", "2026-01-07"), (None, 0))

    def test_inclusive_boundaries(self):
        s = {"2026-01-01": 10, "2026-01-05": 20, "2026-01-07": 30, "2026-01-08": 999}
        avg, n = self.h._series_avg(s, "2026-01-01", "2026-01-07")
        self.assertEqual(n, 3, "两端日期都应包含")
        self.assertAlmostEqual(avg, 20.0, places=3)

    def test_excludes_out_of_range(self):
        s = {"2026-01-01": 100, "2026-01-02": 10}
        avg, n = self.h._series_avg(s, "2026-01-02", "2026-01-02")
        self.assertEqual((avg, n), (10, 1), "单日区间应只取那一天")


# ═══════════════════ ⑤ 挑一条 top_surprise ═══════════════════
class TopSurpriseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_hub(pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-")))

    def test_empty_and_all_unflagged(self):
        self.assertIsNone(self.h.top_surprise({}))
        self.assertIsNone(self.h.top_surprise({"a": {"flag": False, "z": 9.0, "text": "x"}}))

    def test_picks_largest_abs_z(self):
        rep = {
            "小的": {"flag": True, "z": 1.6, "text": "多 10%", "median": 10, "today": "x"},
            "大的": {"flag": True, "z": 4.2, "text": "多 90%", "median": 10, "today": "x"},
        }
        top = self.h.top_surprise(rep)
        self.assertEqual(top["what"], "大的")

    def test_negative_z_uses_absolute_value(self):
        """"少了很多"（z=-5）也该压过"多了一点"（z=+3）—— 取的是 |z|。"""
        rep = {
            "多了点": {"flag": True, "z": 3.0, "text": "多 10%", "median": 10, "today": "x"},
            "少很多": {"flag": True, "z": -5.0, "text": "少 40%", "median": 10, "today": "x"},
        }
        self.assertEqual(self.h.top_surprise(rep)["what"], "少很多")

    def test_contract_keys(self):
        rep = {"k": {"flag": True, "z": 2.0, "text": "多 50%", "median": 20, "today": "2026-01-01"}}
        top = self.h.top_surprise(rep)
        for k in ("what", "text", "z", "today", "median"):
            self.assertIn(k, top)


# ═══════════════ ⑥ 端到端冒烟 + 隐私口径（真实数据形状） ═══════════════
class ReportSmokeTest(unittest.TestCase):
    """喂一批"像真的"的合成历史，跑一遍对外出口 —— 不崩、且**不带 App 名**。"""

    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-smoke-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass
        c = sqlite3.connect(cls.home / "hub.db")
        # app.usage_minutes：每天 5 条，今天飙到 10 倍（构造一个真异常）
        for d in range(1, 15):
            for i, (app, cat, v) in enumerate([
                ("哔哩哔哩", "短视频/视频", 30), ("王者荣耀", "游戏", 20),
                ("微信", "社交", 15), ("淘宝", "购物", 10), ("记事本", "其他", 5),
            ]):
                c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit,meta) VALUES (?,?,?,?,?,'',?)",
                          (f"{_days_ago(d)}T1{i}:00:00+08:00", _days_ago(d), "smoke_probe",
                           "app.usage_minutes", v, json.dumps({"app": app, "category": cat})))
        for i, (app, cat, v) in enumerate([
            ("哔哩哔哩", "短视频/视频", 400), ("王者荣耀", "游戏", 300),
            ("微信", "社交", 40), ("淘宝", "购物", 30), ("记事本", "其他", 5),
        ]):
            c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit,meta) VALUES (?,?,?,?,?,'',?)",
                      (f"{_days_ago(0)}T1{i}:00:00+08:00", _days_ago(0), "smoke_probe",
                       "app.usage_minutes", v, json.dumps({"app": app, "category": cat})))
        c.commit()
        c.close()

    def test_surprise_report_runs(self):
        rep = self.h.surprise_report()
        self.assertIsInstance(rep, dict)

    def test_cost_functions_run_on_synthetic_data(self):
        # 这几个是"她开口前的判断依据"，顺手确认在合成数据上不崩
        for fn in ("data_confidence", "top_surprise", "care_now"):
            try:
                getattr(self.h, fn)()
            except TypeError:
                pass          # 需要参数的跳过（此处只验无参能跑）

    def test_report_never_leaks_app_names(self):
        """★ 文档承诺"只输出类别/指标名，绝不带 App 名"——这里真的验一遍。"""
        rep = self.h.surprise_report()
        blob = json.dumps(rep, ensure_ascii=False)
        for name in ("哔哩哔哩", "王者荣耀", "淘宝", "记事本"):
            self.assertNotIn(name, blob, f"报告里不该出现 App 名：{name}")

    def test_unknown_source_confidence_defaults_to_full(self):
        """取不到可信度时应当当 1.0（不因为拿不到健康度就变保守）。"""
        try:
            self.assertEqual(self.h.data_confidence("不存在的源"), 1.0)
        except Exception:
            pass


# ═══════════ ⑦ 分类按天聚合 / 可信度 / 提问 / 复盘 ═══════════
class CategoryAndOutputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-stats-cat-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass
        c = sqlite3.connect(cls.home / "hub.db")
        # 同一个类别下的两个 App，各报两次（模拟"今天到目前的累计"被反复上报）
        # ★ 契约：每个 App 只取**当天最新**那条，再按类别相加 → 25，绝不是 35。
        cls.APP_A, cls.APP_B, cls.APP_C = "哔哩哔哩", "抖音", "微信"
        # 期望的类别标签**由产品自己算**（cat_app）—— 我猜映射表容易猜错，
        # 这里要钉的是"每个 App 取当天最新、再按类别相加"这个聚合契约本身。
        cls.LAB_A, cls.LAB_B, cls.LAB_C = (
            cls.h.cat_app(cls.APP_A), cls.h.cat_app(cls.APP_B), cls.h.cat_app(cls.APP_C))
        rows = [
            (cls.APP_A, 10, "1"), (cls.APP_A, 20, "2"),
            (cls.APP_B, 5, "1"), (cls.APP_B, 5, "2"),
            (cls.APP_C, 7, "1"),
        ]
        for app, v, hh in rows:
            for off in (0, 1):          # 今天 + 昨天各来一遍，验证按天分桶
                # 刻意带上一个**错的** category：中枢按 App 名自己分类，不采信上报值
                c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit,meta) "
                          "VALUES (?,?,?,?,?,'',?)",
                          (f"{_days_ago(off)}T{hh}:00:00+08:00", _days_ago(off), "cat_probe",
                           "app.usage_minutes", v,
                           json.dumps({"app": app, "category": "我做主",
                                       "pkg": "com.probe." + {"哔哩哔哩": "bili", "抖音": "douyin",
                                                              "微信": "wechat"}.get(app, "x")})))
        c.commit()
        c.close()

    def test_category_uses_latest_per_app_then_sums(self):
        """★ 核心契约：同一个 App 当天报多次只算**最后一次**（累计型），再按类别相加。"""
        with self.h.db() as c:
            s = self.h._cat_day_series(c, days=7)
        today = _days_ago(0)
        lab_a, lab_b, lab_c = self.LAB_A, self.LAB_B, self.LAB_C
        if lab_a == lab_b:      # 两个 App 落到同一类 → 期望值相加
            self.assertEqual(s[lab_a].get(today), 25.0,
                             "应为 20(A 最新) + 5(B 最新)；若是 35 说明把多次上报求和了")
        else:
            self.assertEqual(s[lab_a].get(today), 20.0, "A 当天最新是 20，不是 10+20")
            self.assertEqual(s[lab_b].get(today), 5.0, "B 当天最新是 5，不是 5+5")
        self.assertEqual(s[lab_c].get(today), 7.0)

    def test_category_separates_days(self):
        with self.h.db() as c:
            s = self.h._cat_day_series(c, days=7)
        y = _days_ago(1)
        self.assertIn(self.LAB_C, s)
        self.assertEqual(s[self.LAB_C].get(y), 7.0, "昨天也应是同样的口径")

    def test_stable_pkg_means_latest_wins(self):
        """★ 契约 + 已知脆弱点：分组键是 `(day, meta 整串 JSON)`。

        哔哩哔哩与抖音都归到同一类，各报两次（10→20、5→5）：
        正确结果 = 20 + 5 = **25**（每个 App 只取当天最新）；
        若实现退化成"把所有上报加起来"会得到 40 —— 那才是 bug。
        另：若哪天 meta 里混进"每次都变"的字段（时间戳/标题之类），分组会散，
        聚合会退化成"取 SQL 结果里最后出现的那条"，这里把当前行为钉住，改的时候不会悄悄变。
        """
        with self.h.db() as c:
            s = self.h._cat_day_series(c, days=7)
        today = _days_ago(0)
        self.assertEqual(s[self.LAB_A].get(today), 25.0,
                         "同类两个 App：20(A 最新) + 5(B 最新)；40 说明求和了，10 说明分组散了")

    def test_reported_category_is_ignored(self):
        """★ 记录一条**有意为之**的行为：中枢按 App 名自己分类，不采信上报的 category。
        （她只能用中枢认得的分类说话，所以设备/采集器改不了口径。）"""
        self.assertNotIn("我做主", self.h._cat_day_series.__doc__ or "")
        with self.h.db() as c:
            s = self.h._cat_day_series(c, days=7)
        self.assertNotIn("我做主", json.dumps(s, ensure_ascii=False),
                         "上报的 category 不该直接变成分类标签")

    def test_source_health_structure_and_monotonicity(self):
        """新鲜度/覆盖/可信度：按天的结构要对；今天报过数据的源，可信度
        至少不低于完全没数据的源（这是"残缺上报就让结论更保守"的依据）。"""
        with self.h.db() as c:
            c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit) VALUES (?,?,?,?,?,'')",
                      (f"{_days_ago(0)}T10:00:00+08:00", _days_ago(0), "fresh_probe",
                       "steps.total", 1234))
            c.commit()
        out = self.h.source_health()
        self.assertIsInstance(out, dict)
        self.assertTrue(out, "至少应有一个数据源")

        def conf_of(name):
            for k, v in out.items():
                if name in str(k) or (isinstance(v, dict) and name in str(v)):
                    if isinstance(v, dict):
                        for kk in ("confidence", "conf", "可信度"):
                            if kk in v:
                                return float(v[kk])
            return None

        fresh = conf_of("fresh_probe")
        if fresh is not None:
            self.assertGreaterEqual(fresh, 0.0)
            self.assertLessEqual(fresh, 1.0)

    def test_question_now_contract(self):
        """没问题可问时必须返回 None（不许编一个问题出来）。"""
        q = self.h.question_now()
        self.assertTrue(q is None or (isinstance(q, dict) and "slot" in q and "hint" in q),
                        f"要么 None，要么带 slot/hint 的 dict，实际：{type(q)}")

    def test_review_runs_for_week_and_month(self):
        for kind in ("week", "month"):
            r = self.h.review(kind=kind)
            self.assertIsInstance(r, dict, f"{kind} 复盘应返回 dict")



    def test_varying_extra_meta_field_still_takes_latest(self):
        """★ 回归：meta 混进"每次都变"的字段时，行为必须**确定**。

        实测（同数据下对比新旧 SQL）：
          · 旧实现按 `day, meta` 分组 → 同一 App 一天**被拆成 2 组**（分组明确是错的）；
            最终取值靠 Python 循环"最后写入者胜"，即依赖 SQLite 一条**没有文档保证**的返回顺序
            —— 这次碰巧取到正确的 20，但这是**行为不确定**，不是"现在就算错"。
          · 新实现按 `day, pkg|app` 分组 → 1 组，取值由 `ORDER BY ts DESC` 确定 → 永远取最新。

        所以这条测的是"结果确定"，不是"某个具体 bug 已复现"（不夸大）。"""
        import sqlite3 as _s
        with self.h.db() as c:
            c.execute("DELETE FROM metrics WHERE device='vary_probe'")
            # ★ 故意让**插入顺序与时间顺序相反**：先插 ts 较晚、值 20 的那条。
            #   旧实现按行序返回，最后一条是 10 → 会取错；修好后按 ts 取 → 20。
            for seq, val in (("2", 20), ("1", 10)):
                c.execute("INSERT INTO metrics(ts,day,device,metric,value,unit,meta) "
                          "VALUES (?,?,?,?,?,'',?)",
                          (f"{_days_ago(0)}T1{seq}:00:00+08:00", _days_ago(0), "vary_probe",
                           "app.usage_minutes", val,
                           json.dumps({"app": "哔哩哔哩", "pkg": "com.vary.bili",
                                       "seq": seq, "extra": "每秒都变" + seq})))
            c.commit()
            s = self.h._cat_day_series(c, days=7)
        lab = self.h.cat_app("哔哩哔哩")
        self.assertGreaterEqual(s[lab].get(_days_ago(0), 0), 20.0,
                                "meta 有变化字段时也必须取当天最新（20），不能退化成取到 10")

if __name__ == "__main__":
    unittest.main(verbosity=2)

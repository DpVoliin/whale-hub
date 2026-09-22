#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中枢核心算法单元测试 —— 用标准库 unittest，零第三方依赖。

为什么要有它：
    60_analysis.py 里的稳健统计（median+MAD 的收缩 z、残缺日门槛）是整个
    "值不值得开口"决策的地基。地基错了，上面所有阈值调参都是在错误的
    基础上调。以前这些函数没有任何测试，改一个常数不知道会不会弄坏别的。

测试对象：**合并产物** hub/hub.py（用户实际跑的就是它，不是片段）。
    这意味着：改了片段忘了合并 → 这里的测试会先于 CI 发现。

用法：
    python3 -m unittest tests.test_hub_core -v
    python3 -m unittest discover tests -v      # 全部
"""

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HUB_PY = ROOT / "hub" / "hub.py"


def load_hub():
    """把合并产物 hub.py 作为模块载入（不执行 main）。"""
    spec = importlib.util.spec_from_file_location("whalehub_under_test", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    # run_name 不设为 __main__，所以末尾的 main() 不会跑
    spec.loader.exec_module(mod)
    return mod


class TestRobustStats(unittest.TestCase):
    """_robust：median + 1.4826×MAD，尺度下限防 z 爆炸。"""

    def setUp(self):
        self.hub = load_hub()

    def test_empty_returns_safe_default(self):
        med, sigma = self.hub._robust([])
        self.assertEqual(med, 0.0)
        self.assertEqual(sigma, 1.0, "空输入必须返回安全的 σ=1，避免除零")

    def test_single_value_sigma_not_zero(self):
        """只有一条数据时 MAD=0 → σ 必须靠下限兜住，否则后续 z 会除零/爆炸。"""
        med, sigma = self.hub._robust([100])
        self.assertEqual(med, 100)
        self.assertGreater(sigma, 0, "σ 必须 > 0")

    def test_median_and_sigma_on_clean_series(self):
        # 1..9 的中位数 5；MAD = median(|v-5|) = 2 → σ = 1.4826*2 = 2.9652
        med, sigma = self.hub._robust([1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual(med, 5)
        self.assertAlmostEqual(sigma, 1.4826 * 2, places=4)

    def test_outlier_does_not_move_median(self):
        """抗离群是选 median+MAD 的全部理由：塞一个 9999 不该改变中位数。"""
        med_a, _ = self.hub._robust([10, 11, 12, 13, 14])
        med_b, sigma_b = self.hub._robust([10, 11, 12, 13, 9999])
        self.assertEqual(med_a, 12)
        self.assertEqual(med_b, 12, "离群值不该推动中位数")
        self.assertLess(sigma_b, 100, "MAD 应该把离群值的影响压住（σ 不该爆到几百）")

    def test_none_values_ignored(self):
        med, _ = self.hub._robust([None, 10, None, 20, 30])
        self.assertEqual(med, 20)

    def test_scale_floor_kicks_in_for_tiny_median(self):
        """中位数极小时（如 0.4），'中位数的 5%' 太小 → 必须由下限 1.0 兜住。"""
        _, sigma = self.hub._robust([0.4, 0.4, 0.4, 0.4])
        self.assertGreaterEqual(sigma, 1.0)


class TestDaySeriesIntegrity(unittest.TestCase):
    """_day_series 的完整性门槛：残缺日必须被丢弃。

    这是踩过的坑：采集器某天没跑 → 那天只有 5 分钟 → 被当成"他那天几乎没用手机"
    → 撑大 σ → 真正反常的日子看起来正常。
    """

    def setUp(self):
        self.hub = load_hub()
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE metrics (id INTEGER PRIMARY KEY, metric TEXT, day TEXT, "
            "ts TEXT, value REAL, unit TEXT, meta TEXT, device TEXT)"
        )

    def tearDown(self):
        self.conn.close()

    def _add(self, day, ts, value, metric="screen.active_minutes"):
        self.conn.execute(
            "INSERT INTO metrics (metric, day, ts, value, unit, meta) VALUES (?,?,?,?,?,?)",
            (metric, day, ts, value, "min", "{}"),
        )

    def test_complete_day_kept_incomplete_dropped(self):
        import datetime as _dt
        today = _dt.datetime.now()
        # 完整日：4 条上报（>= BASELINE_MIN_SAMPLES=3）
        good = (today - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        for i, v in enumerate([60, 90, 120, 150]):
            self._add(good, f"{good}T0{i+8}:00:00", v)
        # 残缺日：只有 1 条（采集器那天基本没跑）
        bad = (today - _dt.timedelta(days=2)).strftime("%Y-%m-%d")
        self._add(bad, f"{bad}T12:00:00", 5)
        self.conn.commit()

        series = self.hub._day_series(self.conn, "screen.active_minutes", days=21, kind="peak")
        self.assertIn(good, series, "完整日必须保留")
        self.assertNotIn(bad, series, "残缺日必须被丢弃（否则会撑大 σ）")
        self.assertEqual(series[good], 150, "kind=peak 应取当天最大值")

    def test_kind_last_for_instant_metrics(self):
        import datetime as _dt
        d = (_dt.datetime.now() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
        for i, v in enumerate([80, 60, 34]):      # 电量：递减，最后一条才是真的
            self._add(d, f"{d}T1{i}:00:00", v, metric="battery.level")
        self.conn.commit()
        series = self.hub._day_series(self.conn, "battery.level", days=21, kind="last")
        self.assertEqual(series[d], 34, "kind=last 应取当天最后一条")


class TestSurpriseScoring(unittest.TestCase):
    """_surprise_of：小样本收缩 + 单向判定。"""

    def setUp(self):
        self.hub = load_hub()

    def test_small_sample_shrinks_relative_to_large_sample(self):
        """样本少时 z 必须被**相对收缩**（z' = z·n/(n+k)）。

        注意：这里比较的是"同样偏离下，样本少 vs 样本多"的相对效果 ——
        而不是断言一个绝对上限。绝对 z 还受 σ 下限（1.0）影响，那是另一回事。
        """
        # 同一条基线（100 上下波动 ±1），同一次偏离（+40）
        small = {f"d{i}": 100 + (i % 2) for i in range(3)}    # 3 天
        large = {f"d{i}": 100 + (i % 2) for i in range(30)}   # 30 天
        r_small = self.hub._surprise_of(140, small, direction="high")
        r_large = self.hub._surprise_of(140, large, direction="high")
        self.assertLess(abs(r_small["z"]), abs(r_large["z"]),
                        f"3 天的 z({r_small['z']}) 必须小于 30 天的 z({r_large['z']}) —— 收缩在起作用")

    def test_below_min_days_never_flags(self):
        """少于 BASELINE_MIN_DAYS(3) 天历史 → 直接不判断（项目原则：宁少勿滥）。"""
        r = self.hub._surprise_of(9999, {"d1": 100, "d2": 100}, direction="high")
        self.assertFalse(r["flag"], "少于 3 天历史不该下任何结论")
        self.assertEqual(r["z"], 0.0)

    def test_low_confidence_source_shrinks_harder(self):
        """同一个偏离，数据源可信度低（conf=0.4）时必须比可信度高（conf=1.0）更保守。"""
        series = {f"d{i}": 100 + (i % 3) for i in range(10)}
        z_conf_hi = abs(self.hub._surprise_of(300, series, direction="high", conf=1.0)["z"])
        z_conf_lo = abs(self.hub._surprise_of(300, series, direction="high", conf=0.4)["z"])
        self.assertLess(z_conf_lo, z_conf_hi,
                        "数据残缺（低 conf）时应收缩更狠 —— 这是 P0 加的防误报机制")

    def test_scaling_floor_behavior_documented(self):
        """**记录一个真实边界**：σ 下限固定为 1.0，对量纲大的指标（分钟数），
        当基线稳定时 z 会偏大（本例实测 z≈7）。

        这不是 bug（σ 下限是为了防除零，见 _robust），但它意味着：
        对量纲大的指标，BASELINE_FLAG_Z=1.5 这道闸门会偏敏感 ——
        所以下游还需要 material_score 这类闸门做"值不值得说"的第二层过滤。
        此处把这个**实测行为**固定下来，以免有人改 σ 下限时不知道会影响什么。
        """
        stable = {f"d{i}": 100 + (i % 2) for i in range(30)}   # 100/101 交替 → MAD=0.5
        r = self.hub._surprise_of(140, stable, direction="high")
        self.assertTrue(r["flag"], "稳定基线 + 显著偏离 → 应 flag")
        self.assertGreater(abs(r["z"]), 3.0,
                           f"实测 z={r['z']}：基线的 MAD 很小时 z 会显著放大，下游需 material_score 兜住")

    def test_large_sample_allows_flag(self):
        """足够样本 + 显著偏离 → 应该 flag。"""
        series = {f"d{i}": 100 + (i % 3) for i in range(30)}   # 稳定在 100±1
        r = self.hub._surprise_of(400, series, direction="high")
        self.assertTrue(r.get("flag", False), "30 天稳定基线 + 4 倍偏离 → 必须 flag")
        self.assertGreater(abs(r["z"]), 1.5)

    def test_direction_high_ignores_low_outlier(self):
        """direction=high 时，低于基线的值不该被当成"反常"。"""
        series = {f"d{i}": 100 for i in range(30)}
        r = self.hub._surprise_of(5, series, direction="high")
        self.assertFalse(r.get("flag", False), "单向判定：低值不该在 high 方向报出来")


class TestCodeFingerprint(unittest.TestCase):
    """code_fingerprint：能从外部确认"服务器跑的是哪一版"。"""

    def setUp(self):
        self.hub = load_hub()

    def test_fingerprint_is_12_hex_chars(self):
        fp = self.hub.code_fingerprint()
        self.assertEqual(len(fp), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in fp),
                        f"应是 md5 前 12 位十六进制，实际 {fp!r}")

    def test_fingerprint_stable_across_calls(self):
        self.assertEqual(self.hub.code_fingerprint(), self.hub.code_fingerprint())


class TestConfigResolution(unittest.TestCase):
    """_resolve_home：WHALE_HOME > 旧部署 > ~/.whale。"""

    def test_whale_home_env_wins(self):
        import os
        hub = load_hub()
        old = os.environ.get("WHALE_HOME")
        try:
            os.environ["WHALE_HOME"] = "/tmp/whale_test_home"
            self.assertEqual(hub._resolve_home(), "/tmp/whale_test_home")
        finally:
            if old is None:
                os.environ.pop("WHALE_HOME", None)
            else:
                os.environ["WHALE_HOME"] = old


class TestAmalgamationIntegrity(unittest.TestCase):
    """片段与合并产物必须同步 —— 这条测试保护的是项目最核心的发布纪律。"""

    def test_hub_py_matches_rebuilt_artifact(self):
        import subprocess
        p = subprocess.run([sys.executable, str(ROOT / "hub" / "tools" / "build_single.py")],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 0, f"合并失败：{p.stdout}{p.stderr}")
        dist = ROOT / "hub" / "dist" / "hub.py"
        self.assertTrue(dist.exists(), "合并产物 hub/dist/hub.py 不存在")
        self.assertEqual(HUB_PY.read_bytes(), dist.read_bytes(),
                         "hub/hub.py 与重新合并的产物不一致 —— 改了片段忘了跑 build_single.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

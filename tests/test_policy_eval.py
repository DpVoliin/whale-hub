#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线策略评估器的测试（对应外部评审 v3 的 P0 第一条）。

要验的只有三件事，但都很关键：
  ① **能跑、且只输出结构**：不依赖真实数据就能出报告（CI 里必须能跑）
  ② **单调性**：结果模型说"这个桶接受率很高"时，门控策略一定比"永不说"得分高
  ③ **对成本权重敏感**：C_FALSE 调大 → 开口率不增（Horvitz 门控的本意）
  ④ 反过来：**"永不说"与"一直说"都必须被算出来是差的** —— 否则评估器没在评估
"""
import importlib.util
import json
import os
import pathlib
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
HUB_SRC = ROOT / "hub" / "hub.py"
EVAL = ROOT / "hub" / "tools" / "policy_eval.py"


def load_hub(home: pathlib.Path):
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_evaltest", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PolicyEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-eval-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass
        rnd = random.Random(11)
        # 桶 A：几乎总被接受；桶 B：几乎总被拒绝
        for band, p_acc in (("eval_good", 0.9), ("eval_bad", 0.1)):
            for _ in range(40):
                ts = (datetime.now() - timedelta(days=rnd.randrange(0, 30))).isoformat(timespec="seconds")
                with cls.h.db() as c:
                    c.execute("INSERT INTO feedback(ts, verdict, band, note, w, consumed) "
                              "VALUES (?,?,?,'',1.0,1)",
                              (ts, "up" if rnd.random() < p_acc else "down", band))
        for i in range(80):
            band = "eval_good" if i % 2 == 0 else "eval_bad"
            material = rnd.choice([0, 1, 2, 4])
            ctx = {"band": band, "hour": 9, "material": material, "said": 0, "silent": 1}
            with cls.h.db() as c:
                c.execute("INSERT INTO decisions(ts,day,kind,gap_sec,reason,material,said,band,ctx) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (datetime.now().isoformat(timespec="seconds"),
                           datetime.now().strftime("%Y-%m-%d"), "care", 1200, "测试",
                           material, 0, band, json.dumps(ctx, ensure_ascii=False)))

    def _run(self, *args):
        env = dict(os.environ, WHALE_HOME=str(self.home))
        r = subprocess.run([sys.executable, str(EVAL), *args], capture_output=True, text=True,
                           timeout=180, env=env)
        self.assertEqual(r.returncode, 0, f"评估器退出码 {r.returncode}\n{r.stderr[-500:]}")
        return r.stdout

    def test_runs_and_emits_json(self):
        data = json.loads(self._run("--json"))
        self.assertGreaterEqual(data["contexts"], 80, "应读到全部决策样本")
        self.assertIn("policies", data)
        names = {p["policy"] for p in data["policies"]}
        self.assertSetEqual(names, {"gate", "always", "never", "fixed_8am", "random"})
        self.assertIn("best_policy", data)
        self.assertIn("regret_vs_best", data)

    def test_gate_beats_never_say(self):
        """★ 结果模型说某个桶接受率高 → 门控必须比"永不说"得分高（否则它在瞎判）。"""
        data = json.loads(self._run("--json"))
        pol = {p["policy"]: p for p in data["policies"]}
        self.assertGreater(pol["gate"]["utility"], pol["never"]["utility"],
                           "门控策略居然不如什么都不说 —— 说明门控没吃进结果模型")
        self.assertGreater(pol["gate"]["say_rate"], 0, "门控应当至少开过口")

    def test_always_say_is_penalized(self):
        """一直说必然被误报成本惩罚（合成数据里 bad 桶占一半）。"""
        data = json.loads(self._run("--json"))
        pol = {p["policy"]: p for p in data["policies"]}
        self.assertLess(pol["always"]["utility"], pol["gate"]["utility"],
                        "一直说居然不比门控差 —— 误报成本没算进去")

    def test_cost_weight_monotonic(self):
        """C_FALSE 越大越谨慎：开口率不该上升（Horvitz 门控的本意）。"""
        data = json.loads(self._run("--json", "--sweep"))
        rows = [s for s in data["sweep"] if s["c_miss"] == 1.0]
        rows.sort(key=lambda x: x["c_false"])
        rates = [r["say_rate"] for r in rows]
        self.assertEqual(rates, sorted(rates, reverse=True),
                         f"C_FALSE 增大时开口率应单调不增，实际 {rates}")

    def test_human_report_mentions_sample_size(self):
        out = self._run()
        self.assertIn("离线策略评估", out)
        self.assertIn("决策样本", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

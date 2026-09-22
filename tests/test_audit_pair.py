#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计日志 + 一次性配对码的单元测试。

为什么单独一个文件：这两块是"安全相关"的（谁动过我的数据 / 谁能拿到 token），
它们的失败模式是**静默**的（审计没写、码能重复用），所以必须有机器回归。

用临时 WHALE_HOME，绝不碰真实 hub.db。零第三方依赖（stdlib unittest）。
"""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_hub():
    """在临时数据目录里把合并产物当模块导入（hub.py 的 main() 只有 __main__ 才跑）。"""
    tmp = tempfile.mkdtemp(prefix="whale-test-")
    os.environ["WHALE_HOME"] = tmp
    spec = importlib.util.spec_from_file_location("hub_under_test", os.path.join(ROOT, "hub", "hub.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.init_db()
    return mod


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.h = load_hub()

    def test_审计能写能读(self):
        self.h.audit("config_change", target="persona:name", actor="1.2.3.4", note="改了 1 个字段")
        rows = self.h.audit_recent(10)
        self.assertTrue(rows, "审计应当能读回来")
        r = rows[0]
        self.assertEqual(r["action"], "config_change")
        self.assertEqual(r["target"], "persona:name")
        self.assertEqual(r["actor"], "1.2.3.4")
        self.assertEqual(r["result"], "ok")

    def test_审计只记动作不记内容(self):
        """红线：审计里**不许**出现数据值 / 原文。这条一旦破了，审计就不能给人看了。"""
        self.h.audit("export", target="redact=1", note="全量导出（GDPR Art.20）")
        blob = str(self.h.audit_recent(50))
        for leak in ("25.3", "心率", "127.0.0.1/home", "token="):
            self.assertNotIn(leak, blob)

    def test_按动作与天过滤(self):
        self.h.audit("auth_fail", target="/today", result="denied")
        self.h.audit("backup", target="hub-x.tgz")
        self.assertEqual(len(self.h.audit_recent(50, action="auth_fail")), 1)
        self.assertEqual(len(self.h.audit_recent(50, action="backup")), 1)
        self.assertEqual(len(self.h.audit_recent(50, action="没有这个动作")), 0)
        self.assertEqual(len(self.h.audit_recent(50, day=self.h.today_str())), 2)

    def test_统计汇总(self):
        for _ in range(3):
            self.h.audit("auth_fail", result="denied")
        st = self.h.audit_stats(7)
        self.assertEqual(st["auth_fail"], 3)
        self.assertEqual(st["total"], 3)

    def test_审计写入失败不影响主流程(self):
        """表被人删了也不该把请求带崩（审计是"旁路"，不是主链路）。"""
        with self.h.db() as c:
            c.execute("DROP TABLE audit")
        self.h.audit("whatever")          # 不抛异常即通过


class TestPair(unittest.TestCase):
    def setUp(self):
        self.h = load_hub()

    def test_生成与一次性兑换(self):
        info = self.h.pair_new("stm32_room", ttl_min=15)
        code = info["code"]
        self.assertRegex(code, r"^[0-9A-Z]{4}-[0-9A-Z]{4}$")
        ok, res = self.h.pair_claim(code, "stm32_room")
        self.assertTrue(ok)
        self.assertTrue(res["token"])
        # ★ 一次性：第二次必须被拒
        ok2, res2 = self.h.pair_claim(code, "stm32_room")
        self.assertFalse(ok2)
        self.assertEqual(res2["err"], "used")

    def test_码绑定设备名(self):
        code = self.h.pair_new("dev_a")["code"]
        ok, res = self.h.pair_claim(code, "dev_b")
        self.assertFalse(ok)
        self.assertEqual(res["err"], "device_mismatch")

    def test_过期码被拒(self):
        code = self.h.pair_new("d", ttl_min=15)["code"]
        with self.h.db() as c:      # 把过期时间改到过去
            c.execute("UPDATE pair_codes SET expires_at=? WHERE code=?",
                      ((datetime.now(self.h.TZ) - timedelta(minutes=1)).isoformat(), code))
        ok, res = self.h.pair_claim(code, "d")
        self.assertFalse(ok)
        self.assertEqual(res["err"], "expired")

    def test_不存在的码被拒(self):
        ok, res = self.h.pair_claim("ZZZZ-ZZZZ", "d")
        self.assertFalse(ok)
        self.assertEqual(res["err"], "badcode")

    def test_列表不打印完整码(self):
        code = self.h.pair_new("d")["code"]
        got = self.h.pair_list(5)[0]["code"]
        self.assertTrue(got.endswith("**"))
        self.assertNotEqual(got, code)

    def test_配对动作有审计(self):
        code = self.h.pair_new("d")["code"]
        self.h.pair_claim(code, "d")
        acts = [r["action"] for r in self.h.audit_recent(20)]
        self.assertIn("pair_new", acts)
        self.assertIn("pair_claim", acts)


if __name__ == "__main__":
    unittest.main()

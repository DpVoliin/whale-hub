#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部评审（v3）里成立的那几条加固 —— 逐条验证。

对应评审条目：
  · 3.2-2 并发：WAL                                   → test_wal_enabled
  · 3.3-3 非平稳：反馈权重时间衰减（TV-TS）            → test_feedback_decay_*
  · 3.5-1 常数时间比较                                → test_constant_time_compare
  · 3.5-2 会话 Cookie 的 Secure（仅 TLS 上带）         → test_secure_flag_logic
  · 3.5-3 审计日志防篡改（hash chain）                → test_audit_chain_*
  · 3.5-3 反向验证：**真篡改一条，链必须报错**         → test_tamper_is_detected

跑法：python3 tests/test_hardening_review.py
"""
import importlib.util
import os
import pathlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
HUB_SRC = HERE.parent / "hub" / "hub.py"


def load_hub(home: pathlib.Path):
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_harden", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-harden-"))
        cls.h = load_hub(cls.home)
        try:
            cls.h.init_db()
        except Exception:
            pass

    # ── 3.2-2 WAL ──
    def test_wal_enabled(self):
        with self.h.db() as c:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal", f"journal_mode 应为 wal，实际 {mode}")

    def test_schema_reached_v5(self):
        with self.h.db() as c:
            ver = int(c.execute("PRAGMA user_version").fetchone()[0] or 0)
        self.assertGreaterEqual(ver, 5, f"迁移没走到 v5，实际 {ver}")

    # ── 3.3-3 时间衰减 ──
    def _feedback(self, band, verdict, days_ago, w=1.0):
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        with self.h.db() as c:
            c.execute("INSERT INTO feedback(ts, verdict, band, note, w, consumed) VALUES (?,?,?,'',?,1)",
                      (ts, verdict, band, w))

    def test_feedback_decay_weights_recent_more(self):
        """半年前的一串 ✗ 不该把昨天的一串 ✓ 压死。"""
        band = "衰减实验桶"
        for _ in range(6):
            self._feedback(band, "down", days_ago=300)     # 很旧、且是拒绝
        for _ in range(3):
            self._feedback(band, "up", days_ago=1)         # 很新、接受
        st = self.h.band_stats(min_n=1)
        b = st["bands"].get(band)
        self.assertIsNotNone(b, "该桶应该存在")
        self.assertGreater(b["p_accept"], 0.5,
                           f"旧拒绝应被衰减压下去，实际 p={b['p_accept']}（{b}）")
        self.assertIn("halflife_days", st, "输出里应带上半衰期，便于外部复核")

    def test_decay_does_not_affect_recent_only(self):
        """全是最近反馈时，衰减不应改变结论。"""
        band = "新鲜桶"
        for _ in range(4):
            self._feedback(band, "up", days_ago=0)
        st = self.h.band_stats(min_n=1)
        self.assertGreater(st["bands"][band]["p_accept"], 0.7)

    # ── 3.5-1 常数时间比较 ──
    def test_constant_time_compare(self):
        H = self.h.Handler
        self.assertTrue(H._same("abc123", "abc123"))
        self.assertFalse(H._same("abc123", "abc124"))
        self.assertFalse(H._same("abc", "abc123"), "长度不同直接 False")
        self.assertFalse(H._same("", "x"))
        self.assertFalse(H._same(None, "x"))
        self.assertTrue(H._same("", ""), "两个空串相等（调用方另有 bool 判断）")
        # 长 token 也不该崩
        t = "x" * 200
        self.assertTrue(H._same(t, t))

    # ── 3.5-3 审计链 ──
    def test_audit_chain_written_and_verified(self):
        for i in range(4):
            self.h.audit("test_action", target=f"t{i}", actor="unit-test")
        res = self.h.audit_verify()
        self.assertTrue(res["ok"], f"链应完整：{res}")
        self.assertGreaterEqual(res["checked"], 4)

    def test_tamper_is_detected(self):
        """★ 反向验证：偷偷改掉中间一条的 note，链必须报出被改的位置。"""
        with self.h.db() as c:
            row = c.execute("SELECT id FROM audit WHERE action='test_action' "
                            "ORDER BY id ASC LIMIT 1").fetchone()
            self.assertIsNotNone(row, "前置：应该已经有审计记录")
            target_id = row["id"]
            c.execute("UPDATE audit SET note='偷偷改了' WHERE id=?", (target_id,))
        res = self.h.audit_verify()
        self.assertFalse(res["ok"], "篡改必须被发现")
        self.assertEqual(res["broken_at"], target_id,
                         f"应报出被改的那条（{target_id}），实际 {res['broken_at']}")

    def test_chain_hash_is_sensitive_to_prev(self):
        h1 = self.h._audit_hash("", "t", "d", "a", "tg", "ac", "ok", "n")
        h2 = self.h._audit_hash("X", "t", "d", "a", "tg", "ac", "ok", "n")
        self.assertNotEqual(h1, h2, "换了上一条 hash 就算出不同结果 —— 这才叫链")

    # ── 3.5-2 Secure cookie 判据 ──
    def test_secure_flag_logic(self):
        """非 TLS 连接不该返回 Secure（否则本地 HTTP 调试登录不了）。"""
        class Fake:
            connection = object()
        self.assertEqual(self.h.Handler._secure_flag(Fake()), "")

    # ── 兜底：这轮改的东西不能把老功能弄坏 ──
    def test_legacy_flows_still_work(self):
        self.assertTrue(callable(self.h.audit_verify))
        with self.h.db() as c:
            n = c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        self.assertIsInstance(n, int)


if __name__ == "__main__":
    unittest.main(verbosity=2)

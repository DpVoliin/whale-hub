#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""schema 迁移框架的回归测试。

为什么必须有：ctx 那次迁移差点把中枢搞挂 —— `ALTER` 写在 executescript 里，
第二次启动报 `duplicate column name` → **init_db 整个中断**，而且它**之后**的建表语句
全部没执行（terminals 没建成 → /today 直接断连）。这类 bug 的特点是：
"第一次启动好好的、第二次才炸"，靠人肉试很容易漏。

这里锁住三件事：
    ① 全新库能迁到当前版本
    ② **老库**（表已存在、user_version=0、缺列）能迁到当前版本，且老数据不丢
    ③ 迁移**幂等** —— 连跑两次不炸、版本不乱跳
"""
import importlib.util
import os
import sqlite3
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_hub(home):
    os.environ["WHALE_HOME"] = home
    os.environ.setdefault("WHALE_QUIET", "1")
    spec = importlib.util.spec_from_file_location("hub_mig_test", os.path.join(ROOT, "hub", "hub.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_legacy_db(home):
    """造一个"v0.1.x 的老库"：表都在，但没有 ctx 列，user_version 是 0。

    这就是真实服务器升级时的样子（用户库里已经跑了几十万行数据）。
    """
    c = sqlite3.connect(os.path.join(home, "hub.db"))
    c.execute("""CREATE TABLE metrics(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
                 device TEXT NOT NULL, metric TEXT NOT NULL, value REAL, unit TEXT DEFAULT '',
                 source TEXT DEFAULT '', confidence REAL DEFAULT 1.0, meta TEXT DEFAULT '{}')""")
    c.execute("""CREATE TABLE decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, kind TEXT,
                 gap_sec INTEGER, reason TEXT, material INTEGER, said INTEGER, band TEXT)""")
    c.execute("INSERT INTO metrics(ts, day, device, metric, value) VALUES ('2026-09-01T08:00:00+08:00','2026-09-01','phone','screen.active_minutes',123)")
    c.execute("INSERT INTO decisions(ts, day, kind, reason) VALUES ('2026-09-01T09:00:00+08:00','2026-09-01','speak','老决策')")
    c.commit()
    c.close()


def user_version(home):
    c = sqlite3.connect(os.path.join(home, "hub.db"))
    v = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    return int(v or 0)


def table_cols(home, t):
    c = sqlite3.connect(os.path.join(home, "hub.db"))
    cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]
    c.close()
    return cols


class TestMigration(unittest.TestCase):
    def test_全新库迁到当前版本(self):
        home = tempfile.mkdtemp(prefix="whale-mig-new-")
        hub = load_hub(home)
        hub.init_db()
        self.assertEqual(user_version(home), hub.SCHEMA_VERSION)
        self.assertIn("ctx", table_cols(home, "decisions"))

    def test_老库能迁上来且数据不丢(self):
        home = tempfile.mkdtemp(prefix="whale-mig-old-")
        make_legacy_db(home)
        self.assertEqual(user_version(home), 0)
        hub = load_hub(home)
        hub.init_db()
        self.assertEqual(user_version(home), hub.SCHEMA_VERSION)
        self.assertIn("ctx", table_cols(home, "decisions"))     # 缺的列补上了
        # 老数据必须原样在（迁移只加结构，绝不动数据）
        c = sqlite3.connect(os.path.join(home, "hub.db"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT reason FROM decisions").fetchone()[0], "老决策")
        c.close()

    def test_幂等_连跑三次不出错(self):
        home = tempfile.mkdtemp(prefix="whale-mig-idem-")
        make_legacy_db(home)
        hub = load_hub(home)
        for _ in range(3):
            hub.init_db()            # ★ 第二次就是当年炸掉的那个场景
        self.assertEqual(user_version(home), hub.SCHEMA_VERSION)

    def test_每个迁移单独再跑一遍也不炸(self):
        """把所有迁移再执行一次 —— 这就是"老库 user_version=0 会被从头跑一遍"的等价场景。"""
        home = tempfile.mkdtemp(prefix="whale-mig-replay-")
        hub = load_hub(home)
        hub.init_db()
        with hub.db() as c:
            for fn in hub._MIGRATIONS:
                fn(c)               # 幂等要求：重复执行必须安全
        self.assertEqual(user_version(home), hub.SCHEMA_VERSION)

    def test_迁移数必须等于版本号(self):
        """防呆：加了迁移却忘了升 SCHEMA_VERSION（或反过来）。"""
        home = tempfile.mkdtemp(prefix="whale-mig-cnt-")
        hub = load_hub(home)
        self.assertEqual(len(hub._MIGRATIONS), hub.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()

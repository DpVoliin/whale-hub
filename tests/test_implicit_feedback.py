#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隐式反馈的回归测试。

背景（真实数据）：她说完话后**手动 ✓/✗ 几乎永远等不到**（日志里长期 0✓/0✗）→
p_接受恒为 0.50 → 永远过不了闸门 → 只有"料≥3"才开口，自适应机制饿着。
所以加了弱信号：从"她说完之后主人有没有反应"里学。**但边界必须钉住**，
否则会学到错的东西（最怕的两种错误：
  ① 把"主人没看见"当成"她烦人" → 她会越来越不敢说话；
  ② 把弱证据写满 → 手动反馈被淹没）。

这里用一个**假的会话库**把三种情况都验一遍。
"""
import importlib.util
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPK_DIR = ROOT / "speaker"


def make_fake_hermes_db(path, user_ts):
    """造一个只有 messages 表的假会话库（timestamp 是 REAL epoch 秒，与真实一致）。"""
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, role TEXT, timestamp REAL)")
    if user_ts is not None:
        c.execute("INSERT INTO messages(role, timestamp) VALUES ('user', ?)", (user_ts,))
        c.execute("INSERT INTO messages(role, timestamp) VALUES ('assistant', ?)", (user_ts + 5,))
    c.commit()
    c.close()


def load_speaker(tmp_attrib, hermes_db):
    os.environ["WHALE_ATTRIB"] = str(tmp_attrib)
    os.environ["WHALE_HERMES_DB"] = str(hermes_db)
    os.environ.setdefault("WHALE_HOME", tempfile.mkdtemp(prefix="whale-impl-home-"))
    if str(SPK_DIR) not in sys.path:
        sys.path.insert(0, str(SPK_DIR))
    spec = importlib.util.spec_from_file_location("spk_implicit_test", SPK_DIR / "whale_speaker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestImplicitFeedback(unittest.TestCase):
    def _run(self, user_offset_min, age_min=90):
        """user_offset_min：主人那次说话距她开口多少分钟（None = 完全没有任何消息）。"""
        d = tempfile.mkdtemp(prefix="whale-impl-")
        attrib = pathlib.Path(d) / "attrib.json"
        db = pathlib.Path(d) / "state.db"
        t0 = time.time() - age_min * 60
        make_fake_hermes_db(db, None if user_offset_min is None else t0 + user_offset_min * 60)
        spk = load_speaker(attrib, db)
        posts = []
        spk.hub = lambda path, body=None, **kw: (posts.append((path, body)) if path == "/feedback" else {})
        attrib.write_text(json.dumps([{"ts": t0, "band": "工作日·白天", "head": "测试"}]))
        n = spk.implicit_tick()
        return n, posts, attrib

    def test_三十分钟内回话了算欢迎(self):
        n, posts, _ = self._run(user_offset_min=10)
        self.assertEqual(n, 1)
        _, body = posts[0]
        self.assertEqual(body["verdict"], "good")
        self.assertEqual(body["src"], "implicit")
        self.assertLess(body["w"], 1.0, "隐式证据必须是弱证据（不能等于手动的 1.0）")

    def test_在场却没回算打扰(self):
        n, posts, _ = self._run(user_offset_min=60)
        self.assertEqual(n, 1)
        self.assertEqual(posts[0][1]["verdict"], "bad")
        self.assertLess(posts[0][1]["w"], 0.5)

    def test_主人不在场时什么都不记(self):
        n, posts, _ = self._run(user_offset_min=-240)      # 她开口前 4 小时主人说过话，之后没人影
        self.assertEqual(n, 0, "没人在场就不该记负反馈（那是'没看见'，不是'嫌烦'）")
        self.assertEqual(posts, [])

    def test_完全没有主人的消息就不记(self):
        n, posts, _ = self._run(user_offset_min=None)
        self.assertEqual(n, 0)
        self.assertEqual(posts, [])

    def test_没到结账时间不处理(self):
        n, posts, attrib = self._run(user_offset_min=5, age_min=10)   # 才过 10 分钟
        self.assertEqual(n, 0)
        self.assertTrue(json.loads(attrib.read_text()), "未到点的归因要留着，不能丢")

    def test_结过账的会被清掉_不重复记(self):
        n, posts, attrib = self._run(user_offset_min=10)
        self.assertEqual(n, 1)
        self.assertEqual(json.loads(attrib.read_text()), [], "结过账的要清掉，免得轮回重复上报")

    def test_投递成功才记归因(self):
        d = tempfile.mkdtemp(prefix="whale-impl2-")
        attrib = pathlib.Path(d) / "attrib.json"
        spk = load_speaker(attrib, pathlib.Path(d) / "none.db")
        spk.attribute_delivery("主人，该喝水了", band="工作日·白天")
        items = json.loads(attrib.read_text())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["band"], "工作日·白天")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出口通道（直发）的回归测试。

为什么要测：出口是**唯一会把数据送出这台机器**的地方，payload 形状写错 = 消息发不出去，
而且"发不出去"往往要等到真有事时才发现（她该提醒你的时候没提醒）。
这里用一个**本地桩服务器**接住请求，逐字检查 payload。
（绝不连真 webhook —— 测试不许往外面发东西。）
"""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOT = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        GOT.append({"path": self.path, "body": body, "ctype": self.headers.get("Content-Type")})
        out = b'{"errcode":0,"errmsg":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def load_hub(home):
    os.environ["WHALE_HOME"] = home
    os.environ.setdefault("WHALE_QUIET", "1")
    spec = importlib.util.spec_from_file_location("hub_chan_test", os.path.join(ROOT, "hub", "hub.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestChannels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.url = "http://127.0.0.1:%d/hook" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        GOT.clear()
        self.h = load_hub(tempfile.mkdtemp(prefix="whale-chan-"))
        self.h.init_db()          # 建表（审计那条断言要用；不建的话 audit() 会静默失败）
        self.h.CFG["channels"] = {"wecom_webhook": self.url, "generic_webhook": self.url}

    def test_企业微信群机器人_payload形状(self):
        r = self.h.channel_send("该喝水了")
        self.assertEqual(r.get("wecom_bot"), "ok", r)
        msg = json.loads(GOT[0]["body"])
        self.assertEqual(msg["msgtype"], "text")                 # 企微群机器人硬要求这个形状
        self.assertEqual(msg["text"]["content"], "该喝水了")
        self.assertIn("application/json", GOT[0]["ctype"])

    def test_通用出口只要求text字段(self):
        self.h.channel_send("hello")
        generic = [g for g in GOT if "text" in json.loads(g["body"]) and "msgtype" not in json.loads(g["body"])]
        self.assertTrue(generic, "通用出口应当收到 {\"text\": ...}")
        self.assertEqual(json.loads(generic[0]["body"])["text"], "hello")

    def test_两个出口都发(self):
        r = self.h.channel_send("两路都发")
        self.assertEqual(sorted(r), ["generic", "wecom_bot"], r)
        self.assertEqual(len(GOT), 2)

    def test_没配出口时给明确提示而不是静默(self):
        self.h.CFG["channels"] = {}
        r = self.h.channel_send("没人接")
        self.assertIn("error", r)
        self.assertEqual(GOT, [], "没配出口就不该发任何请求")

    def test_空内容不发(self):
        r = self.h.channel_send("   ")
        self.assertIn("error", r)
        self.assertEqual(GOT, [])

    def test_状态不泄漏_webhook地址(self):
        st = self.h.channels_status()
        blob = json.dumps(st, ensure_ascii=False)
        self.assertNotIn(self.url, blob)          # webhook 地址本身就是凭据（带 key）
        self.assertEqual(st["wecom_bot"], "已配置")

    def test_发出去会被审计(self):
        self.h.channel_send("记一笔")
        acts = [r["action"] for r in self.h.audit_recent(10)]
        self.assertIn("channel_send", acts)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出口通道（直发）的回归测试。

为什么要测：出口是**唯一会把数据送出这台机器**的地方，payload 形状写错 = 消息发不出去，
而且"发不出去"往往要等到真有事时才发现（她该提醒你的时候没提醒）。
这里用一个**本地桩服务器**接住请求，逐字检查 payload。
（绝不连真 webhook —— 测试不许往外面发东西。）

覆盖：
  · 企业微信群机器人 —— 硬要求的 {"msgtype":"text","text":{"content":...}} 形状
  · 通用 webhook —— 只要 {"text": ...}
  · ntfy —— ★ 要的是**裸文本 body**（不是 JSON），以及可选 Bearer token
  · Bark —— ★ 要的是**路径式** /<key>/<标题>/<内容>，走 GET，回 {"code":200}
  · 没配出口 / 空内容 → 明确报错而不是静默
  · 状态输出**不泄漏** webhook 地址（那是凭据）；直发会写审计
"""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOT = []


class Stub(BaseHTTPRequestHandler):
    """把收到的请求原样记下来；按路径给出对应形状的回复。

    - 企微群机器人要看到 {"errcode":0}
    - Bark 要看到 {"code":200}
    - ntfy 只看 HTTP 200（空体即可）
    """

    def _rec(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        GOT.append({"method": self.command, "path": self.path, "body": body,
                    "ctype": self.headers.get("Content-Type"),
                    "headers": {k: v for k, v in self.headers.items()}})
        if self.path.startswith("/bark"):
            out, ctype = b'{"code":200,"message":"success"}', "application/json"
        elif "qqtok" in self.path or "getAppAccessToken" in self.path:
            out, ctype = b'{"access_token":"FAKE_TOKEN","expires_in":7200}', "application/json"
        elif "qqmsg" in self.path:
            out, ctype = b'{"id":"MSG1","timestamp":1}', "application/json"
        elif self.command == "GET":
            out, ctype = b"", "text/plain"
        else:
            out, ctype = b'{"errcode":0,"errmsg":"ok"}', "application/json"
        self.send_response(204 if "discord" in self.path else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_POST = _rec
    do_GET = _rec

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
        cls.ntfy = "http://127.0.0.1:%d/ntfy" % cls.srv.server_address[1]
        cls.bark = "http://127.0.0.1:%d/bark/KEY123" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        GOT.clear()
        self.h = load_hub(tempfile.mkdtemp(prefix="whale-chan-"))
        self.h.init_db()          # 建表（审计那条断言要用；不建的话 audit() 会静默失败）
        self.h.CFG["channels"] = {"wecom_webhook": self.url, "generic_webhook": self.url}

    # ─────────────────────────── 原有：企微 / 通用 ───────────────────────────
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
        self.assertIn("ntfy_url", r["error"], "错误信息应告诉用户还有哪些出口可填")
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

    # ─────────────────────────── 新增：ntfy ───────────────────────────
    def test_ntfy_要裸文本而不是JSON(self):
        self.h.CFG["channels"] = {"ntfy_url": self.ntfy}
        r = self.h.channel_send("到点了，喝口水")
        self.assertEqual(r.get("ntfy"), "ok", r)
        rec = [g for g in GOT if g["path"] == "/ntfy"][0]
        self.assertEqual(rec["method"], "POST")
        self.assertEqual(rec["body"], "到点了，喝口水",
                         "★ ntfy 要的是**裸文本 body**，包成 JSON 它就不认了")

    def test_ntfy_带token时用Bearer(self):
        self.h.CFG["channels"] = {"ntfy_url": self.ntfy, "ntfy_token": "tk_123"}
        self.h.channel_send("x")
        self.assertEqual(GOT[0]["headers"].get("Authorization"), "Bearer tk_123")

    def test_ntfy_不带token时无Authorization头(self):
        self.h.CFG["channels"] = {"ntfy_url": self.ntfy}
        self.h.channel_send("x")
        self.assertIsNone(GOT[0]["headers"].get("Authorization"))

    # ─────────────────────────── 新增：Bark ───────────────────────────
    def test_bark_用路径式GET(self):
        self.h.CFG["channels"] = {"bark_url": self.bark}
        r = self.h.channel_send("该睡觉了")
        self.assertEqual(r.get("bark"), "ok", r)
        rec = [g for g in GOT if g["path"].startswith("/bark/KEY123/")][0]
        self.assertEqual(rec["method"], "GET", "Bark 经典用法是路径式 GET")
        self.assertIn("/whalecare/", urllib.parse.unquote(rec["path"]),
                      "路径应是 /<key>/<标题>/<内容>")
        self.assertIn("该睡觉了", urllib.parse.unquote(rec["path"]))

    def test_bark_可指定铃声(self):
        self.h.CFG["channels"] = {"bark_url": self.bark, "bark_sound": "birdsong"}
        self.h.channel_send("x")
        self.assertIn("sound=birdsong", GOT[0]["path"])

    # ─────────────────────────── 新增：状态与多路 ───────────────────────────
    def test_状态里列出新出口(self):
        st = self.h.channels_status({"channels": {}})
        for k in ("ntfy", "bark", "wecom_bot", "generic_webhook", "wecom_app"):
            self.assertIn(k, st)
        self.assertIn("ntfy.sh", st["ntfy"], "空状态应顺带提示该填什么")

    def test_四个出口可同时发(self):
        self.h.CFG["channels"] = {"wecom_webhook": self.url, "generic_webhook": self.url,
                                  "ntfy_url": self.ntfy, "bark_url": self.bark}
        r = self.h.channel_send("全都发")
        for k in ("wecom_bot", "generic", "ntfy", "bark"):
            self.assertEqual(r.get(k), "ok", f"{k} 应成功：{r}")
        self.assertEqual(len(GOT), 4)

    # ─────────────────────────── 新增：钉钉 ───────────────────────────
    def test_钉钉_payload形状(self):
        self.h.CFG["channels"] = {"dingtalk_webhook": self.url + "/dt"}
        r = self.h.channel_send("开会了")
        self.assertEqual(r.get("dingtalk"), "ok", r)
        rec = [g for g in GOT if "/dt" in g["path"]][0]
        msg = json.loads(rec["body"])
        self.assertEqual(msg["msgtype"], "text")
        self.assertEqual(msg["text"]["content"], "开会了")

    def test_钉钉_加签时URL带timestamp与sign(self):
        self.h.CFG["channels"] = {"dingtalk_webhook": self.url + "/dt", "dingtalk_secret": "SECabc"}
        self.h.channel_send("x")
        rec = [g for g in GOT if "/dt" in g["path"]][0]
        self.assertIn("timestamp=", rec["path"])
        self.assertIn("sign=", rec["path"])

    # ─────────────────────────── 新增：Discord ───────────────────────────
    def test_discord_只要content字段(self):
        self.h.CFG["channels"] = {"discord_webhook": self.url + "/discord"}
        r = self.h.channel_send("hi discord")
        self.assertEqual(r.get("discord"), "ok", f"204 也算成功：{r}")
        rec = [g for g in GOT if "discord" in g["path"]][0]
        self.assertEqual(json.loads(rec["body"]), {"content": "hi discord"})

    # ─────────────────────────── 新增：QQ 官方 ───────────────────────────
    def test_qq_官方_取token再发私聊(self):
        self.h.CFG["channels"] = {
            "qq_appid": "102000", "qq_secret": "sec",
            "qq_target": "OPENID123", "qq_kind": "user",
            "qq_token_url": self.url + "/qqtok",
            "qq_api_base": self.url + "/qqmsg",
        }
        r = self.h.channel_send("QQ 上收到没")
        self.assertEqual(r.get("qq"), "ok", r)
        tok = [g for g in GOT if "qqtok" in g["path"] or "getAppAccessToken" in g["path"]]
        msg = [g for g in GOT if "qqmsg" in g["path"]]
        self.assertTrue(tok, "应先取 access_token")
        self.assertTrue(msg, "再发消息")
        self.assertIn("/v2/users/OPENID123/messages", msg[0]["path"])
        self.assertEqual(json.loads(msg[0]["body"])["content"], "QQ 上收到没")
        self.assertIn("QQBot ", msg[0]["headers"].get("Authorization", ""))

    def test_qq_群聊用group路径(self):
        self.h.CFG["channels"] = {
            "qq_appid": "1", "qq_secret": "s", "qq_target": "G999", "qq_kind": "group",
            "qq_token_url": self.url + "/qqtok", "qq_api_base": self.url + "/qqmsg",
        }
        self.h.channel_send("群里发")
        msg = [g for g in GOT if "qqmsg" in g["path"]][0]
        self.assertIn("/v2/groups/G999/messages", msg["path"])

    def test_qq_缺Secret时不发只报空(self):
        st = self.h.channels_status({"channels": {"qq_appid": "1"}})
        self.assertIn("空", st["qq"])

    def test_channel_test_发的是测试文案(self):
        self.h.CFG["channels"] = {"ntfy_url": self.ntfy}
        r = self.h.channel_test()
        self.assertEqual(r.get("ntfy"), "ok")
        self.assertIn("测试", GOT[0]["body"])


if __name__ == "__main__":
    unittest.main()

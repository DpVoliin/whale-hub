#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补掉「已知缺口」清单里那四条 —— 逐条验证它们真的生效。

对应 docs/THREAT-MODEL.md 的已知缺口表：
  ① MCU 证书**多指纹轮换窗口**（评审 3.1-2）        → test_relay_accepts_multiple_pins
  ② 配对码 / 登录**失败次数限流**（评审 3.5-1）      → TestThrottle.*
  ③ 敏感健康数据的**显式同意**（PIPL 单独同意）      → TestConsent.*
  ④ 备份**生命周期管理**（默认留最近 N 份）          → test_backup_keeps_only_n
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
HUB_SRC = ROOT / "hub" / "hub.py"


def load_hub(home: pathlib.Path):
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_gaps", HUB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.init_db()
    except Exception:
        pass
    return mod


class GapsBase(unittest.TestCase):
    def setUp(self):
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-gap-"))
        self.h = load_hub(self.home)


class TestThrottle(GapsBase):
    """② 抗枚举：连续失败要锁，成功要清零，正常用户永远碰不到。"""

    def test_五连错后锁定(self):
        who = "1.2.3.4"
        for _ in range(self.h.AUTH_LIMIT - 1):
            lock = self.h.auth_throttle_fail(who)
            self.assertEqual(lock, 0, "还没到阈值不该锁")
        lock = self.h.auth_throttle_fail(who)
        self.assertEqual(lock, self.h.AUTH_LOCK, "第 N 次失败应触发锁定")
        ok, wait = self.h.auth_throttle_check(who)
        self.assertFalse(ok, "锁定期间应拒绝")
        self.assertGreater(wait, 0, "应给出剩余秒数（好回给用户）")

    def test_成功一次即清零(self):
        who = "5.6.7.8"
        self.h.auth_throttle_fail(who)
        self.h.auth_throttle_fail(who)
        self.h.auth_throttle_ok(who)
        ok, _ = self.h.auth_throttle_check(who)
        self.assertTrue(ok, "清零后应立刻可用")
        for _ in range(self.h.AUTH_LIMIT - 1):
            self.assertEqual(self.h.auth_throttle_fail(who), 0, "清零后重新数")

    def test_只看这个来源不牵连别人(self):
        self.h.auth_throttle_fail("A")
        for _ in range(self.h.AUTH_LIMIT):
            self.h.auth_throttle_fail("A")
        okA, _ = self.h.auth_throttle_check("A")
        okB, _ = self.h.auth_throttle_check("B")
        self.assertFalse(okA)
        self.assertTrue(okB, "别人不该被 A 的失败连坐")

    def test_配对码被锁时不泄露码是否正确(self):
        who = "9.9.9.9"
        for _ in range(self.h.AUTH_LIMIT):
            self.h.pair_claim("WRONGCODE", "devX", actor=who)
        ok, res = self.h.pair_claim("WRONGCODE", "devX", actor=who)
        self.assertFalse(ok)
        self.assertEqual(res.get("err"), "locked", f"锁定时应统一回 locked，实际 {res}")

    def test_限流自己坏掉也不挡正常请求(self):
        """（诚实边界）限流查库异常时必须放行 —— 可用性优先于限流。"""
        old = self.h.db
        self.h.db = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            ok, wait = self.h.auth_throttle_check("someone")
        finally:
            self.h.db = old
        self.assertTrue(ok, "限流故障时应放行，而不是把所有人关在门外")


class TestConsent(GapsBase):
    """③ 健康数据没有显式同意就不入库（PIPL / GDPR Art.9）。"""

    HEALTH = {"device": "phone", "metric": "health.heart_rate", "value": 72}
    OTHER = {"device": "phone", "metric": "screen.active_minutes", "value": 30}

    def test_没同意时健康数据被丢弃(self):
        ok, skipped = self.h.ingest_items([self.HEALTH, self.OTHER])
        self.assertEqual(ok, 1, "非健康那条应该照常入库")
        self.assertEqual(skipped, 1, "健康那条应被丢弃并如实计数")
        with self.h.db() as c:
            n = c.execute("SELECT COUNT(*) FROM metrics WHERE metric='health.heart_rate'").fetchone()[0]
        self.assertEqual(n, 0, "★ 没有同意，健康数据不该落库")

    def test_同意之后就能入库(self):
        self.h.consent_set("health", True, source="unit-test", version="v1")
        ok, skipped = self.h.ingest_items([self.HEALTH])
        self.assertEqual(ok, 1, "给了同意就该收")
        with self.h.db() as c:
            n = c.execute("SELECT COUNT(*) FROM metrics WHERE metric='health.heart_rate'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_撤回之后再次被拒(self):
        self.h.consent_set("health", True, source="unit-test")
        self.h.consent_set("health", False, source="unit-test", note="撤回")
        self.assertFalse(self.h.consent_granted("health"), "撤回应立即生效")
        ok, skipped = self.h.ingest_items([self.HEALTH])
        self.assertEqual(skipped, 1)

    def test_同意历史只追加不覆盖(self):
        self.h.consent_set("health", True, source="a", version="v1")
        self.h.consent_set("health", False, source="b", version="v1")
        with self.h.db() as c:
            rows = c.execute("SELECT granted, source FROM consents WHERE what='health' ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2, "要能回答『当时他同意的是什么』—— 历史不能删")
        self.assertEqual([int(r["granted"]) for r in rows], [1, 0])

    def test_未同意时丢弃会被审计(self):
        self.h.ingest_items([self.HEALTH])
        acts = [r["action"] for r in self.h.audit_recent(10)]
        self.assertIn("ingest_no_consent", acts)


class TestConsentRoute(GapsBase):
    """★ 路由级端到端测试（函数级测试抓不到这一层）。

    为什么必须有它：我第一版把 `/consent` 写进了 `do_GET` → POST 直接 404；
    第二版又用了不存在的 `self._read_body()` → body 永远是空，接口误报"只支持 what=health"。
    这两种错**函数级测试全都发现不了** —— 只有真起一个中枢、真打一次 HTTP 才暴露。
    所以这里用中枢自己的 Handler 起一个临时服务，走完整的请求链路。
    """

    def test_consent_route_end_to_end(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"
        tok = self.h.CFG["token"]

        def post(path, payload):
            r = urllib.request.Request(
                base + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Token": tok}, method="POST")
            with urllib.request.urlopen(r, timeout=15) as x:
                return json.loads(x.read().decode())

        def ingest(item):
            r = urllib.request.Request(
                base + "/ingest", data=json.dumps([item]).encode(),
                headers={"Content-Type": "application/json", "X-Token": tok}, method="POST")
            with urllib.request.urlopen(r, timeout=15) as x:
                return json.loads(x.read().decode())

        try:
            item = {"device": "route-test", "metric": "health.heart_rate", "value": 70}
            self.assertEqual(ingest(item)["accepted"], 0, "没同意时路由就该丢健康数据")
            rep = post("/consent", {"what": "health", "granted": True, "device": "route-test"})
            self.assertTrue(rep.get("ok"), f"/consent 应成功，实际 {rep}")
            self.assertTrue(rep.get("granted"), "返回里应显示已同意")
            self.assertEqual(ingest(item)["accepted"], 1, "★ 同意之后必须收下（走真 HTTP）")
            post("/consent", {"what": "health", "granted": False})
            self.assertEqual(ingest(item)["accepted"], 0, "撤回后应立刻又被拒")
        finally:
            srv.shutdown()

    def test_consent_route_rejects_other_what(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), self.h.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = urllib.request.Request(
                f"http://127.0.0.1:{port}/consent", data=b'{"what":"location","granted":true}',
                headers={"Content-Type": "application/json", "X-Token": self.h.CFG["token"]},
                method="POST")
            try:
                urllib.request.urlopen(r, timeout=15)
                self.fail("不该接受 what=location")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400, f"应回 400，实际 {e.code}")
        finally:
            srv.shutdown()


class TestBackupRetention(GapsBase):
    """④ 备份生命周期：默认只留最近 N 份（备份含全量数据，堆着就是多份泄漏面）。"""

    def _backup_cmd(self):
        import importlib.util as iu
        spec = iu.spec_from_file_location("ctl_gaps", ROOT / "hub" / "hubctl.py")
        ctl = iu.module_from_spec(spec)
        spec.loader.exec_module(ctl)
        return ctl

    def test_backup_keeps_only_n(self):
        ctl = self._backup_cmd()
        db = self.h.DB_PATH
        # 造 10 个旧备份（时间戳递增）
        for i in range(10):
            f = f"{db}.bak-2026010{i}-000000"
            pathlib.Path(f).write_bytes(b"x" * 10)
            os.utime(f, (datetime.now().timestamp() - (10 - i) * 60,) * 2)
        class A:
            encrypt = False
            password = None
            keep = 3
        ctl.cmd_backup(A())
        left = sorted(pathlib.Path("./").glob(os.path.basename(db) + ".bak-*"))
        left = [p for p in pathlib.Path(os.path.dirname(db)).glob(os.path.basename(db) + ".bak-*")]
        self.assertEqual(len(left), 3, f"应只留最近 3 份，实际 {len(left)}：{[p.name for p in left]}")


class TestRelayPins(unittest.TestCase):
    """① 中继多指纹：轮换期间新旧指纹并存。"""

    def _pins(self, env_extra):
        """在子进程里跑中继的指纹解析（避免 import 一个会连网的脚本）。"""
        code = (
            "import os,sys,pathlib,importlib.util\n"
            "spec=importlib.util.spec_from_file_location('r', sys.argv[1])\n"
            "m=importlib.util.module_from_spec(spec)\n"
            "try:\n"
            "    spec.loader.exec_module(m)\n"
            "except SystemExit:\n"
            "    pass\n"
            "except Exception:\n"
            "    print('SKIP'); raise SystemExit(0)\n"
            "print('|'.join(getattr(m,'PINS',[])))\n"
        )
        env = dict(os.environ, **env_extra)
        r = subprocess.run([sys.executable, "-c", code, str(ROOT / "mcu" / "mcu_relay.py")],
                           capture_output=True, text=True, timeout=60, env=env)
        out = (r.stdout or "").strip().splitlines()
        return out[-1] if out else "SKIP"

    def test_单个指纹(self):
        got = self._pins({"WHALE_PIN": "AA:BB:CC"})
        if got == "SKIP":
            self.skipTest("中继脚本 import 时有副作用，跳过")
        self.assertEqual(got, "aabbcc", "冒号要去掉、转小写")

    def test_多个指纹与轮换位(self):
        got = self._pins({"WHALE_PIN": "aa,bb", "WHALE_PIN_NEXT": "cc"})
        if got == "SKIP":
            self.skipTest("中继脚本 import 时有副作用，跳过")
        self.assertEqual(set(got.split("|")), {"aa", "bb", "cc"},
                         "★ 轮换窗口：新旧指纹必须都被接受，否则证书一换就全量失联")


if __name__ == "__main__":
    unittest.main(verbosity=2)

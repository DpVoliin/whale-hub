#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脱敏"注入式"测试 —— 主动把 PII 灌进库，再验它**不会浮到给模型的那份上下文**里。

跟"泄漏探测"不同：那个是"样例不在库里，当然搜不到"，太弱。
这个是**先把 PII 真的写进库**（独立设备名 `privacy_probe`，临时目录，不碰真实部署），
然后调用中枢自己的 `llm_context()`，逐条断言这些字串不出现在 payload 里。

跑法：
    python3 tests/test_privacy_injection.py            # 基于 hub/hub.py（产物）
"""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
HUB_SRC = HERE.parent / "hub" / "hub.py"

# ── 造一批"看着像真的"的 PII ──────────────────────────────────────────
PII = {
    "学校": "佛山大学",
    "人名": "张三",
    "手机号": "13800138000",
    "学号": "2024012345",
    "地址": "广东省佛山市禅城区江湾一路18号",
    "教室": "7-506",
    "课程名": "电力系统分析",
    "教师": "王老师",
    "店铺": "某某旗舰店",
    "快递单号": "SF1234567890",
    "窗口标题": "教务系统 - 我的成绩",
    "进程名": "chrome.exe",
    "蓝牙MAC": "AA:BB:CC:DD:EE:FF",
    "歌单": "我的私人歌单",
    "待办原文": "把论文交给李老师",
    "精确坐标": "23.1379",        # 只该用城市级，不该出现小数坐标
}

# 注入的"原始数据"：meta 里塞满 PII（模拟采集器如实上报的样子）
INJECT = [
    ("app.usage_minutes", 96, "min",
     {"app": "哔哩哔哩", "category": "短视频/视频", "title": PII["课程名"] + " 视频"}),
    ("app.usage_minutes", 20, "min",
     {"app": "微信", "category": "社交"}),
    ("calendar.event", 1, "",
     {"kind": "上课", "title": PII["课程名"], "teacher": PII["教师"], "room": PII["教室"],
      "place": PII["学校"]}),
    ("order.event", 1, "",
     {"kind": "快递", "store": PII["店铺"], "tracking": PII["快递单号"], "amount": 128.5}),
    ("health.spo2", 97, "%",
     {"raw": f"您的血氧为97% · {PII['学校']} {PII['人名']} {PII['手机号']}", "from": "vivo健康"}),
    ("task.todo", 1, "", {"text": PII["待办原文"], "who": PII["人名"]}),
    ("pc.window_switches_today", 87, "次",
     {"window": PII["窗口标题"], "process": PII["进程名"], "student_id": PII["学号"]}),
    ("bt.battery_percent", 12, "%",
     {"name": "vivo TWS 3", "mac": PII["蓝牙MAC"]}),
    ("music.track", 1, "",
     {"title": "In or Out", "artist": "GRAHAM", "playlist": PII["歌单"]}),
    ("sleep.total_minutes", 430, "min", {"raw": f"{PII['人名']} 的睡眠明细"}),
    ("steps.total", 5200, "步", {"home": PII["地址"]}),
    ("weather.now", 29.4, "C",
     {"city_code": "101280101", "city": "广州", "desc": "晴", "lat": 23.1379, "lon": 113.2615}),
]


def load_hub(home: pathlib.Path):
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WHALE_HOME"] = str(home)
    spec = importlib.util.spec_from_file_location("whalecare_probe", HUB_SRC)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class PrivacyInjectionTest(unittest.TestCase):
    hub = None
    home = None

    @classmethod
    def setUpClass(cls):
        cls.home = pathlib.Path(tempfile.mkdtemp(prefix="whale-probe-"))
        cls.hub = load_hub(cls.home)
        try:
            cls.hub.init_db()
        except Exception:
            pass
        # **必须走中枢真实入口**（ingest_items）：raw 剥离就发生在这一层。
        # 直接写库会绕过剥离逻辑，测出来的东西没意义。
        items = [{"device": "privacy_probe", "metric": m, "value": v, "unit": u,
                  "source": "probe", "meta": meta} for m, v, u, meta in INJECT]
        try:
            cls.hub.ingest_items(items)
        except Exception as e:
            cls.hub = cls.hub   # 忽略：下面的断言会给出真正的原因
            print(f"  ⚠ ingest_items 抛错：{type(e).__name__} {e}")

    def setUp(self):
        self.ctx, self.dropped = self.hub.llm_context()
        self.blob = json.dumps(self.ctx, ensure_ascii=False)

    # ---------- 核心断言：PII 一个都不许出现 ----------
    def test_no_pii_in_payload(self):
        leaked = {k: v for k, v in PII.items() if v in self.blob}
        self.assertEqual(leaked, {},
                         f"这些 PII 漏进模型上下文了：{leaked}\n--- payload ---\n{self.blob[:2000]}")

    def test_raw_text_not_stored(self):
        """store_raw_text 默认 false：通知原文不该进库。"""
        with self.hub.db() as c:
            rows = c.execute("SELECT meta FROM metrics WHERE device='privacy_probe'").fetchall()
        for r in rows:
            m = r["meta"] or ""
            self.assertNotIn("您的血氧为97%", m, "原文被存进库了（privacy.store_raw_text 应为 false）")

    def test_allowed_reductions_present(self):
        """该保留的粗粒度信息要在：分类名、金额区间这类。"""
        self.assertIn("短视频/视频", self.blob, "分类名应该保留（否则她没法说话）")

    def test_dropped_list_is_documented(self):
        """dropped 清单（what_model_never_sees）必须有内容，且覆盖隐私项。"""
        self.assertTrue(self.dropped, "dropped 清单为空 —— 说明脱敏说明没生成")
        joined = " ".join(self.dropped)
        for must in ("通知原文", "App 名", "日程标题", "位置"):
            self.assertIn(must, joined, f"dropped 清单里该有「{must}」的说明")

    def test_clean_hub_has_no_probe_residue(self):
        """自检：注入用的设备名必须只在临时库出现（防止误用真实库）。"""
        self.assertTrue(str(self.hub.CFG.get("token")), "临时库应自带 token")
        self.assertIn("whale-probe-", str(self.home), "必须跑在临时目录里")


if __name__ == "__main__":
    unittest.main(verbosity=2)

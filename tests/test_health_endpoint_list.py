#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""/health 的接口列表必须**自动**跟得上真实路由（DpVoliin/whalecare issue #10）。

背景：`/health` 返回的 `endpoints` 是手写列表。2026-09-22 就漏过 7 个端点
（新增 `/export` `/erase` `/bands` `/decisions` `/mcu/inbox` `/mcu/ack` `/api/mcu` 之后没人同步），
而外部用户/监控脚本正是靠它发现能力的 —— 漏了就等于"这个能力不存在"。

做法：从 `97_http.py` 里**扫出真实注册的路由**（两种写法：
`path == "X"` / `path in (...)` 精确匹配，以及 `self.path.startswith("X")` 前缀匹配），
再与 `/health` 里那份列表双向比对：

  ① 真实路由 ⊆ (列表 ∪ 不对外宣传的白名单)   ← 只改了路由没改列表 → 红
  ② 列表 ⊆ 真实路由                          ← 列表里留了已删路由的僵尸项 → 红

再加一条"反向验证"：**故意**注入一个假路由时必须能抓到 —— 否则这个测试只是装饰。
"""
import pathlib
import re
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
HTTP_SRC = ROOT / "hub" / "src" / "whalecare" / "97_http.py"

#: 不该出现在对外能力列表里的（页面/登录/内部），有意的白名单
NOT_ADVERTISED = {
    "/login", "/logout", "/", "/index.html", "/admin",
}

#: 前缀路由（`startswith`）里允许"不算独立端点"的（它们是上面某条的具体形式）
PREFIX_ALLOW = {
    "/api/mcu", "/mcu/inbox", "/mcu/ack", "/api/pair",
}


def routes_in_source(text: str):
    """从源码里扫出真实路由：返回 (精确集合, 前缀集合)。"""
    exact, prefix = set(), set()
    for m in re.finditer(r'path\s*==\s*"([^"]+)"', text):
        exact.add(m.group(1))
    for m in re.finditer(r'path\s+in\s*\(([^)]*)\)', text):
        for s in re.findall(r'"([^"]+)"', m.group(1)):
            exact.add(s)
    for m in re.finditer(r'self\.path\.startswith\(\s*"([^"]+)"\s*\)', text):
        prefix.add(m.group(1))
    return exact, prefix


def health_endpoint_list(text: str) -> set:
    """从源码里取出 /health 的那份 endpoints 列表。"""
    m = re.search(r'"endpoints":\s*\[(.*?)\]', text, re.S)
    if not m:
        raise AssertionError("在 97_http.py 里找不到 /health 的 endpoints 列表")
    return set(re.findall(r'"([^"]+)"', m.group(1)))


class HealthEndpointListTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = HTTP_SRC.read_text(encoding="utf-8")
        cls.exact, cls.prefix = routes_in_source(cls.text)
        cls.listed = health_endpoint_list(cls.text)

    def test_source_has_routes(self):
        """先确认扫描本身有效（否则后面的断言是空转）。"""
        self.assertGreaterEqual(len(self.exact), 8, f"扫到 {len(self.exact)} 个精确路由，太少")
        self.assertGreaterEqual(len(self.listed), 8, f"列表里只有 {len(self.listed)} 条，太少")

    def test_every_route_is_advertised_or_allowlisted(self):
        """① 改了路由却忘了同步列表 → 这里红（这就是 issue #10 要的验收标准）。"""
        missing = sorted(p for p in self.exact
                         if p not in self.listed and p not in NOT_ADVERTISED)
        self.assertEqual([], missing,
                         "/health 的接口列表漏了这些真实路由，请补上：\n  " + "\n  ".join(missing))

    def test_prefix_routes_are_advertised_or_allowlisted(self):
        missing = sorted(p for p in self.prefix
                         if p not in self.listed and p not in NOT_ADVERTISED
                         and p not in PREFIX_ALLOW)
        self.assertEqual([], missing, f"前缀路由没同步到 /health：{missing}")

    def test_listed_endpoints_all_exist(self):
        """② 列表里不许留僵尸项（路由已经删了、列表还写着）。"""
        known = self.exact | self.prefix
        zombies = sorted(p for p in self.listed
                         if p not in known and not any(p.startswith(x) for x in self.prefix))
        self.assertEqual([], zombies, f"/health 列表里有已不存在的路由：{zombies}")

    def test_detector_actually_detects(self):
        """③ 反向验证：**故意**注入一条假路由，扫描必须能抓到。

        没有这条，上面的断言可能只是"恰好一直为空"的装饰品。
        """
        injected = self.text.replace('if path == "/health":',
                                     'if path == "/definitely-not-a-real-route":\n'
                                     '            return self._send(200, {})\n'
                                     '        if path == "/health":', 1)
        self.assertNotEqual(injected, self.text, "注入失败：找不到锚点")
        ex2, _ = routes_in_source(injected)
        self.assertIn("/definitely-not-a-real-route", ex2)
        missing = [p for p in ex2 if p not in self.listed and p not in NOT_ADVERTISED]
        self.assertIn("/definitely-not-a-real-route", missing,
                      "注入的假路由没被判为缺失 —— 说明这个测试抓不到问题")


if __name__ == "__main__":
    unittest.main(verbosity=2)

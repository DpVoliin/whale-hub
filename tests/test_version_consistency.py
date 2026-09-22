#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""版本一致性门禁 —— 防止"三个地方各说一个版本号"。

为什么加它：2026-09-22 审计发现三个源头互相矛盾
（pyproject 0.1.18 / CHANGELOG 0.1.19 / sbom.json 0.1.17），而这套系统是
**两个会话（甚至多人）在同一个仓库里交替改**的 —— 版本号是第一个会漂移的东西，
而且漂了不会报错，只会让外部用户看到"到底哪个是最新"。

约定：**CHANGELOG 的第一条 `## vX.Y.Z` 是唯一事实来源**，
pyproject.toml 与 sbom.json 必须跟上。
"""
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def changelog_version() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## v([\d.]+)", text, re.M)
    return m.group(1) if m else ""


class VersionConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.v = changelog_version()
        self.assertTrue(self.v, "CHANGELOG 里找不到 `## vX.Y.Z`")

    def test_pyproject_matches_changelog(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version = "([\d.]+)"', text, re.M)
        self.assertIsNotNone(m, "pyproject.toml 里找不到 version")
        self.assertEqual(m.group(1), self.v,
                         f"pyproject.version={m.group(1)} 与 CHANGELOG={self.v} 不一致")

    def test_sbom_matches_changelog(self):
        p = ROOT / "sbom.json"
        if not p.is_file():
            self.skipTest("没有 sbom.json")
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["metadata"]["component"]["version"], self.v,
                         "sbom.json 的版本落后了 —— 跑 python3 hub/tools/make_sbom.py --out sbom.json")

    def test_sbom_is_valid_cyclonedx_subset(self):
        p = ROOT / "sbom.json"
        if not p.is_file():
            self.skipTest("没有 sbom.json")
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d.get("bomFormat"), "CycloneDX")
        self.assertIn("specVersion", d)
        self.assertIn("components", d)
        # 零依赖项目：依赖项必须为空，否则说明有人偷偷加了第三方包
        self.assertEqual(d.get("dependencies", []) and
                         [x for x in d["dependencies"] if x.get("dependsOn")], [],
                         "SBOM 里出现了依赖项 —— 本项目运行时零依赖，请检查")

    def test_hub_runtime_version_matches_changelog(self):
        """运行时的 VERSION（/health、/export 都报它）也必须跟上 —— 外部用户第一眼看的就是它。"""
        text = (ROOT / "hub" / "hub.py").read_text(encoding="utf-8")
        m = re.search(r'^VERSION = "([\d.]+)"', text, re.M)
        self.assertIsNotNone(m, "产物里找不到 VERSION")
        self.assertEqual(m.group(1), self.v,
                         "hub.py 的 VERSION 落后了 —— 改 hub/src/whalecare/00_header.py 后要重新合并")

    def test_fragment_version_matches_product(self):
        """片段（真相）与产物（分发）的 VERSION 也要一致，否则合并漏了一步。"""
        frag = (ROOT / "hub" / "src" / "whalecare" / "00_header.py").read_text(encoding="utf-8")
        prod = (ROOT / "hub" / "hub.py").read_text(encoding="utf-8")
        fv = re.search(r'^VERSION = "([\d.]+)"', frag, re.M).group(1)
        pv = re.search(r'^VERSION = "([\d.]+)"', prod, re.M).group(1)
        self.assertEqual(fv, pv, "片段与产物的 VERSION 不一致 → 忘了跑 build_single.py 并提交")

    def test_package_shell_version_matches(self):
        """pip 包壳（whalecare/__init__.py）的版本也要跟上 CHANGELOG。"""
        p = ROOT / "whalecare" / "__init__.py"
        if not p.is_file():
            self.skipTest("没有包壳")
        m = re.search(r'^__version__ = "([\d.]+)"', p.read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(m, "包壳里找不到 __version__")
        self.assertEqual(m.group(1), self.v, "whalecare/__init__.py 的版本落后了")

    def test_collector_compilesdk_matches_documented_baseline(self):
        """compileSdk 必须与 docs/ANDROID-RELEASE.md 的构建基线**一致**。

        为什么不是"必须 ≤ 36"：我 2026-09-22 曾因本机 sdkmanager 默认通道列不出 android-37
        就断言"该平台不存在"，把 compileSdk 从 37 降到 36 —— CI 立刻一路红。
        真因是 androidx.core 1.19.0 的 AAR 元数据**硬要求 compile >= 37**；
        而那个平台其实存在，只是要先 `sdkmanager --channel=1`（预览通道）才列得出来。
        教训：**本地工具链看不到 ≠ 不存在。**
        所以这条锁的是"配置与文档必须一致"——两边任一单独改都会立刻红。
        """
        cfg = ROOT / "collector" / "app" / "build.gradle.kts"
        doc = ROOT / "docs" / "ANDROID-RELEASE.md"
        if not cfg.is_file() or not doc.is_file():
            self.skipTest("没有采集器工程或基线文档")
        m = re.search(r"compileSdk\s*=\s*(\d+)", cfg.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "找不到 compileSdk")
        sdk = int(m.group(1))
        dm = re.search(r"compileSdk / targetSdk\s*\|\s*\*\*(\d+)\*\*", doc.read_text(encoding="utf-8"))
        self.assertIsNotNone(dm, "基线文档里找不到 compileSdk 行")
        documented = int(dm.group(1))
        self.assertEqual(sdk, documented,
                         f"配置里 compileSdk={sdk}，文档写的是 {documented} —— 必须一起改")
        self.assertGreaterEqual(sdk, 36, f"compileSdk={sdk} 太低")

if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""零依赖护栏：确保运行时代码只 import 标准库。

为什么需要它：
    「零第三方依赖」是鲸鲸最核心的卖点之一（单文件可分发、无供应链攻击面、
    不会因为某个库停止维护而烂掉）。但这个约束很脆弱 —— 任何人顺手写一句
    `import requests` 就能破坏它，而 ruff/测试都拦不住。

    这个脚本把它变成一条机器守卫：出现非标准库 import 就让 CI 红。

判定方式：
    1. 扫出所有顶层 import 的模块名
    2. 用 sys.stdlib_module_names（Python 3.10+）判断是否标准库
    3. 白名单放过项目自己的模块与已知的本地片段名
    4. 忽略函数体内的延迟 import（有些平台差异处理需要）

用法：
    python3 hub/tools/check_no_deps.py            # 检查全部
    python3 hub/tools/check_no_deps.py --verbose  # 打印扫到的全部模块
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 扫描目标：运行时会加载的代码（dev 工具与测试不在此列 —— 它们可以有依赖）
TARGETS = [
    ROOT / "hub" / "hub.py",
    ROOT / "hub" / "src",
    ROOT / "speaker",
    ROOT / "hub" / "ext",
]

# 放行：项目自己的模块、本地片段、以及平台专属模块
ALLOW = {
    "whalecare", "hub", "whale_voice", "whale_speaker", "whale_web",
    # 单片机/桌面挂件侧可能用到的本地模块
    "whale_card",
}

# 允许的平台专属模块（不在 sys.stdlib_module_names 里但属于运行环境自带）
PLATFORM_OK = {
    "android", "jnius",          # python-for-android / kivy 场景
    "win32api", "win32con",      # pywin32 —— 若桌面挂件用了要显式列在这里并说明原因
    "msvcrt",                    # Windows 自带
}


def iter_py_files():
    for t in TARGETS:
        if t.is_file():
            yield t
        elif t.is_dir():
            for p in t.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                yield p


def top_level_imports(path: Path):
    """产出 (行号, 模块名, 层) —— 只取模块顶层 import，忽略函数体里的延迟 import。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:
        print(f"⚠️  语法错误，跳过：{path}: {e}")
        return

    for node in tree.body:                      # 只看顶层
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 相对 import（from . import x）→ 本地
                continue
            if node.module:
                yield node.lineno, node.module.split(".")[0]


def main() -> int:
    verbose = "--verbose" in sys.argv
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:
        print("❌ 需要 Python 3.10+（依赖 sys.stdlib_module_names）")
        return 2

    stdlib = set(stdlib)
    violations = []
    scanned = set()
    files = sorted(iter_py_files())

    for f in files:
        for lineno, mod in top_level_imports(f):
            scanned.add(mod)
            if mod in stdlib or mod in ALLOW or mod in PLATFORM_OK:
                continue
            violations.append((f.relative_to(ROOT), lineno, mod))

    if verbose:
        print(f"扫描 {len(files)} 个文件，顶层 import 的模块：")
        for m in sorted(scanned):
            tag = "stdlib" if m in stdlib else ("allow" if m in ALLOW or m in PLATFORM_OK else "❌ 第三方")
            print(f"  {m:<28} {tag}")
        print()

    if violations:
        print("❌ 发现非标准库依赖 —— 这破坏了项目的零依赖约束：\n")
        for rel, lineno, mod in violations:
            print(f"  {rel}:{lineno}  →  import {mod}")
        print()
        print("这个项目要求运行时只用 Python 标准库。请：")
        print("  1. 优先用标准库改写（通常可行 —— urllib 替代 requests、sqlite3 已在用等）")
        print("  2. 若确实需要第三方库，先开 issue 讨论（大概率答案是「不用它」）")
        print("  3. 若该 import 只在开发/测试中用，把它移出 TARGETS 覆盖的目录")
        print("  4. 若是平台自带模块，加进本脚本的 PLATFORM_OK 并说明原因")
        return 1

    print(f"✅ 零依赖检查通过（扫描 {len(files)} 个文件，共 {len(scanned)} 个顶层模块，全部来自标准库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

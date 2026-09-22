#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 hub.py 机械拆成 src/whalehub/*.py 片段（在章节边界切），并保证"合并回去与原文件逐字节一致"。

为什么用"片段 + 合并"而不是真正的 Python 包：
  卖点之一是**单文件、clone 即跑**。拆成包会把它弄没；而片段合并（amalgamation，
  SQLite 就是这么干的）两头都要：维护时按模块看，发布时还是一个文件。
  而且——**切分是纯机械的，合并结果与原文件逐字节相同**，等价性可以严格证明，不需要靠"感觉没坏"。

用法：
  python3 tools/build_single.py            # 合并 → dist/hub.py（顺带自检）
  python3 tools/split_hub.py               # 从 hub.py 反向切出片段（迁移期用一次）
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "whalehub")
DIST = os.path.join(ROOT, "dist")


def fragments():
    if not os.path.isdir(SRC):
        return []
    return [f for f in sorted(os.listdir(SRC)) if f.endswith(".py") and not f.startswith("_")]


def build(out_path=None, quiet=False):
    fs = fragments()
    if not fs:
        raise SystemExit("没有片段可合并：%s 是空的" % SRC)
    chunks = []
    for f in fs:
        p = os.path.join(SRC, f)
        txt = open(p, encoding="utf-8").read()
        # ★ 防呆：空片段/被截断的片段**语法也是合法的**（空文件 ast.parse 通过、py_compile 通过），
        #   所以必须按"大小"和"关键标记"拦，不能只靠语法。
        if len(txt.strip()) == 0:
            raise SystemExit("片段 %s 是空的 —— 大概率是被 open(f,\"w\") 截断了" % f)
        ast.parse(txt)                       # 单个片段也必须是合法 Python（片段内不跨文件引用就行）
        chunks.append((f, txt))
    merged = "".join(t for _, t in chunks)
    ast.parse(merged)                        # 合并后必须合法
    if len(merged) < 100000 or "class Handler" not in merged:
        raise SystemExit("合并产物可疑（%d 字节）→ 拒绝写出" % len(merged))
    tree = ast.parse(merged)
    fns = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    cls = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    # ★ 结构断言：关键函数与 Handler 类必须都在**模块级**（曾经的坑：抽函数时把它们变成嵌套函数）
    for need in ("main", "say", "care_now", "llm_context", "source_health", "ingest_items",
                 "apply_persona_pack", "load_ext", "scheduler"):
        assert need in fns, "合并结果缺顶层函数 %s" % need
    assert "Handler" in cls, "合并结果缺 Handler 类"
    h = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler")
    ms = {n.name for n in h.body if isinstance(n, ast.FunctionDef)}
    for need in ("do_GET", "do_POST", "_send", "_ingest", "_today", "_pending"):
        assert need in ms, "Handler 缺方法 %s" % need

    os.makedirs(DIST, exist_ok=True)
    out = out_path or os.path.join(DIST, "hub.py")
    open(out, "w", encoding="utf-8").write(merged)
    if not quiet:
        print("  片段 %d 个 → %s（%d 行 / %d 字节）" % (len(fs), out, len(merged.splitlines()), len(merged)))
        print("  顶层函数 %d 个 · 类 %s · Handler 方法 %d 个" % (len(fns), cls, len(ms)))
    return out


if __name__ == "__main__":
    build()

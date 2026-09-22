#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 SBOM（软件物料清单，CycloneDX 1.5 JSON 的最小合法子集）。

为什么手写：本项目**零第三方依赖**，所以清单短得可以手写、也短得能被人工通读 ——
这本身就是"零依赖"最有力的证明。

用法：
    python3 hub/tools/make_sbom.py > sbom.json
    python3 hub/tools/make_sbom.py --out sbom.json
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import uuid
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
HUB = ROOT / "hub" / "hub.py"


def version() -> str:
    for line in HUB.read_text(encoding="utf-8").splitlines()[:80]:
        if line.startswith("VERSION") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"\'')
    return "0.0.0"


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build() -> dict:
    v = version()
    comps = []
    # 中枢本体（单文件）
    comps.append({
        "type": "application",
        "bom-ref": f"pkg:generic/whale-hub@{v}",
        "name": "whale-hub",
        "version": v,
        "description": "个人数据中枢：单文件零依赖 Python（HTTP + SQLite + 规则引擎 + 脱敏）",
        "hashes": [{"alg": "SHA-256", "content": sha256(HUB)}],
        "properties": [
            {"name": "whale:runtime-dependencies", "value": "0（仅 Python 标准库）"},
            {"name": "whale:source-commit", "value": commit()},
        ],
    })
    # 声明零运行时依赖
    comps.append({
        "type": "platform",
        "bom-ref": "pkg:generic/cpython@3.11+",
        "name": "CPython",
        "version": ">=3.11",
        "description": "唯一运行时。不需要 pip install，不需要 venv。",
    })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [{"vendor": "whale-hub", "name": "make_sbom.py", "version": "1"}],
            "component": comps[0],
        },
        "components": comps,
        "properties": [
            {"name": "whale:third-party-runtime-deps", "value": "0"},
            {"name": "whale:audit-note",
             "value": "零运行时依赖 ⇒ 本清单即完整清单；hub/tools/check_no_deps.py 在 CI 里机器守卫这一点"},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="输出文件（默认打到 stdout）")
    a = ap.parse_args()
    doc = json.dumps(build(), ensure_ascii=False, indent=2)
    if a.out:
        pathlib.Path(a.out).write_text(doc + "\n", encoding="utf-8")
        print(f"  ✓ 已写 {a.out}（{len(doc)} 字节）")
    else:
        print(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())

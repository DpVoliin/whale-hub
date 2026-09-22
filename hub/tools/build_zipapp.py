#!/usr/bin/env python3
"""把中枢打成单文件可执行 zipapp（.pyz）—— 用 Python 标准库，零第三方依赖。

为什么用 zipapp 而不是 PyInstaller / shiv / pex：
    · zipapp 是标准库自带（`python -m zipapp`），完全符合"运行时零依赖"的项目底线
    · 产物是**一个文件**，用户 `python3 whalecare.pyz` 就能跑，不需要 venv、不需要 pip
    · 不含解释器（需要目标机有 Python 3.11+），体积 ~165KB —— 这正好匹配
      "自托管、给技术用户用"的定位；真要裸机双端分发再考虑 PyInstaller

用法：
    python3 hub/tools/build_zipapp.py                 # 产出 dist/whalecare.pyz
    python3 hub/tools/build_zipapp.py -o /tmp/x.pyz   # 指定输出
    python3 hub/tools/build_zipapp.py --run           # 打包后立刻试跑一次（冒烟）

运行时的数据位置（配置与数据库）：
    优先 $WHALE_HOME，否则 ~/.whale —— **不在压缩包旁边**，见 hub/hub.py 的 _resolve_home()
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB_PY = ROOT / "hub" / "hub.py"
DEFAULT_OUT = ROOT / "dist" / "whalecare.pyz"

MAIN_PY = """\
# 入口：导入合并产物并执行 main()。
# 注意模块名用 _app 而非 hub，避免 runpy 的「已导入再执行」警告。
from whalecare import _app

if __name__ == "__main__":
    _app.main()
"""


def build(out: Path, run: bool = False) -> int:
    if not HUB_PY.exists():
        print(f"❌ 找不到 {HUB_PY} —— 先跑 python3 hub/tools/build_single.py")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "app"
        pkg = stage / "whalecare"
        pkg.mkdir(parents=True)
        # 合并产物作为 _app 模块；whalecare/__init__.py 让它成为可导入的包
        shutil.copy2(HUB_PY, pkg / "_app.py")
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (stage / "__main__.py").write_text(MAIN_PY, encoding="utf-8")

        zipapp.create_archive(stage, target=out, interpreter="/usr/bin/env python3", compressed=True)

    size_kb = out.stat().st_size / 1024
    print(f"✅ 打包完成 → {out}（{size_kb:.1f} KB）")
    print(f"   运行：python3 {out}")
    print("   数据：$WHALE_HOME 或 ~/.whale（配置 hub.json + 数据库 hub.db）")

    if run:
        print("\n--- 冒烟试跑（5 秒后终止）---")
        env = dict(os.environ)
        with tempfile.TemporaryDirectory() as th:
            env["WHALE_HOME"] = th
            try:
                p = subprocess.run([sys.executable, str(out)], env=env, timeout=5,
                                   capture_output=True, text=True)
                out_txt = (p.stdout + p.stderr).strip()
            except subprocess.TimeoutExpired as e:
                out_txt = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace").strip()
            print(out_txt[:1200] or "（无输出）")
            made = sorted(os.listdir(th))
            if "hub.json" in made:
                print(f"\n✅ 冒烟通过：已在临时目录生成 {made}")
                return 0
            print(f"\n⚠️ 未生成 hub.json（目录内容：{made}）—— 检查上面的报错")
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把 whalecare 中枢打成单文件 zipapp")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help="输出路径")
    ap.add_argument("--run", action="store_true", help="打包后立刻冒烟试跑")
    a = ap.parse_args()
    return build(a.out, a.run)


if __name__ == "__main__":
    sys.exit(main())

"""`whalecare` 命令入口 / `python -m whalecare`。

就是"把单文件 hub.py 当程序跑起来"，**不做任何包装或改写** ——
参数原样透传，所以 `whalecare --port 11440` 与 `python3 hub/hub.py --port 11440`
完全等价（单文件仍是唯一真相）。
"""

import runpy
import sys

from . import HUB_FILE


def main(argv=None) -> int:
    """把单文件中枢当 __main__ 执行（同进程，不 fork）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    sys.argv = [HUB_FILE] + argv
    try:
        runpy.run_path(HUB_FILE, run_name="__main__")
    except KeyboardInterrupt:
        print("\n[whalecare] 已停止", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

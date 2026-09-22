#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鲸鲸 · 桌面（WhaleDesk）
========================

一个文件、零第三方依赖。把鲸鲸养在你的 Windows 桌面上：

  · 平时只有形象，点一下才冒气泡（气泡里是数据）
  · 按住能拖，松手会自动吸附到最近的屏幕边（含角落）
  · 贴左吸附时整体水平镜像翻转（带动画）
  · 按下会「蹲一下」再回弹（Q 弹，底部基线不动）
  · 右键唤出主菜单（大小 / 音效 / 吸附 / 数据设置 / 退出）
  · 双击跟她说话（走你自己的中枢，人设与记忆都在服务器那一份）

交互规格（拖拽吸附 / 贴边镜像 / 按压回弹 / 连点推进气泡）按桌面宠物的通行做法实现；
本机承载换成 tkinter 透明置顶窗（PyInstaller 出 exe）。

    python3 whale_desk.py                 # 桌面挂件（默认）
    python3 whale_desk.py --cli           # 命令行看一次数据（适合 SSH / cron）
    python3 whale_desk.py --json          # 同上，输出 JSON
    python3 whale_desk.py --init          # 生成 whale_desk.json
    python3 whale_desk.py --selftest DIR   # 把各状态画出来存 PNG（自检用）

⚠️ 形象现在是**占位圆圈**（assets/pet.png 等，由 tools/make_placeholder.sh 生成）。
   真图到手后，替换 assets/ 下同名文件即可，代码一行都不用改。
"""

import argparse
import json
import math
import os
import random
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import tkinter as tk
import tkinter.font as tkfont

APP = "鲸鲸 · 桌面"
VERSION = "0.1.0"

# 打包成 exe 后：素材在 sys._MEIPASS（临时目录，只读），配置必须写在 exe 旁边
FROZEN = bool(getattr(sys, "frozen", False))
BASE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
# 素材优先级：**exe 旁边的 assets/** > exe 内部（_MEIPASS）。
# 这样以后主人把自己的画丢进 exe 旁边的 assets/ 就能直接生效，不用重新打包。
_BUNDLED = os.path.join(getattr(sys, "_MEIPASS", BASE), "assets")
ASSETS = os.path.join(BASE, "assets") if os.path.isdir(os.path.join(BASE, "assets")) else _BUNDLED
DEFAULT_CFG = os.path.join(BASE, "whale_desk.json")

# ---------------------------------------------------------------- 输出兜底
# ⚠️ --windowed 打包后 sys.stdout / sys.stderr 都是 None，任何裸 .write() 直接崩。
#    所以全程只用 _log()，逐个试 stderr → stdout，都不行落文件，绝不静默吞。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _log(msg):
    """⚠️ 一定要**同时**写控制台和文件，不能"有控制台就不落盘"。

    旧版写完 stdout 就 return —— 结果：只要有控制台（排障、管道跑），
    日志文件永远不生成，说明书里"出问题把 whale-desk-log.txt 发我"就成了空话。
    """
    line = msg if str(msg).endswith("\n") else str(msg) + "\n"
    for s in (sys.stderr, sys.stdout):
        if s is not None:
            try:
                s.write(line)
                s.flush()
                break
            except Exception:
                pass
    try:
        with open(os.path.join(BASE, "whale-desk-log.txt"), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


# ---------------------------------------------------------------- 外观常量
CHROMA_DEFAULT = "#010203"          # 键控色（Windows 透明只认精确匹配）
BPAD = 16           # 气泡左右内边距（旧名，别处还在引用）
BPAD_X = 16         # 左右内边距
BPAD_Y = 14         # 上下内边距
BROW_H = 36         # 旧行高（兼容引用）
ROW_H_VAL = 42      # 值行（大字）行高
ROW_H_TXT = 28      # 正文行
ROW_H_DIM = 24      # 注释行
BRAD = 16           # 圆角（配圆弧绘制才好看）
BGAP = 10           # 形象与气泡之间的间隙
BUB_MAXW = 340      # 气泡最大宽度
BUB_BG = "#11141a"                  # 加深，拉开与文字的对比（原来的 #161b22 发灰）
BUB_EDGE = "#1e2430"                # 只做"收边"，不再抢眼（原来 #242b36 太亮太粗）
BUB_TOP = "#1a2029"                 # 顶部内侧极淡高光（暗示光从上面来）
C_TXT = "#c7cfdc"                   # 正文
C_DIM = "#8b95a6"                   # 注释
C_DIM2 = "#5f6877"                  # 更弱的注释
C_VAL = "#f2f5fa"                   # 值：近白
TRACK = "#252b36"                   # 进度条底槽（要看得见，否则条不像"有刻度"）
ACC = {"ok": "#4f9e80", "warn": "#c2953f", "bad": "#bd5f5f", "off": "#5a6472"}
FONT_MIN = 9
TICK_MS = 33                        # ≈30fps
STRETCH_PAD = 2                    # 只为抗锯齿留 2px（原来 15px 是给拉伸用的，已停用）                    # 窗口左右各留这么多像素给"拉伸"（不然拉伸被窗口硬裁一刀）
STRETCH_AMP_MAX = 1.05              # 振幅上限：1 + SQ_MAX*上限 = 1.21 倍宽 → 正好塞进留白里
PRESS_SX = 1.0                     # 按住不再拉伸（留白已撤，没法再往外长）                     # 按住时"左右拉伸"的比例
PRESS_SY = 1.00                     # ⚠️ 高度**一律不变**（用户明确要求：点了直接左右拉伸，高度不变）
SQ_MAX = 0.0                        # 已停用：点击不再做左右拉伸（改成了随机换表情）
SQ_HOLD = 0.07                      # 拉满保持多久，再开始弹回
SQ_RATE_OUT = 16.0                  # 拉出去的速度（越大越快）
SQ_RATE_BACK = 5.5                  # 弹回来的速度（比拉出去慢 → 有回弹感但不摆


def level_color(pct):
    if pct is None:
        return ACC["off"]
    if pct >= 85:
        return ACC["bad"]
    if pct >= 60:
        return ACC["warn"]
    return ACC["ok"]


def fmt_n(n):
    try:
        return "{:,.2f}".format(float(n))
    except Exception:
        return str(n)


BUB_WRAP_W = BUB_MAXW - BPAD * 2    # 折行宽度 = 卡片最大内宽
MAX_WRAP_LINES = 3                  # 一行文字最多折 3 行（再多就还是截"…"，别让卡片长成柱子）


def wrap_lines(txt, font, width):
    """估算折行后的行数（CJK 按字符断行，ceil 就够准）。"""
    w = font.measure(txt)
    if w <= width or width <= 0:
        return 1
    return int(w // width) + (1 if w % width else 0)


def clip_px(txt, font, budget):
    """按**显示宽度**截断（一个汉字 ≈ 两个西文字符宽，不能按字符数切）。"""
    if font.measure(txt) <= budget:
        return txt
    out = ""
    for ch in txt:
        if font.measure(out + ch + "…") > budget:
            break
        out += ch
    return out + "…"


def short_err(msg, limit=48):
    """把一整坨异常糊成一句人话（气泡里不能塞原始 JSON）。"""
    s = str(msg or "").strip()
    m = re.search(r'"message"\s*:\s*"([^"]+)"', s)
    if m:
        s = m.group(1)
    low = s.lower()
    for kw, human in (
        ("invalid", "Key 无效或没权限"),
        ("authentication", "Key 无效或没权限"),
        ("insufficient", "余额或额度不足"),
        ("balance", "余额或额度不足"),
        ("certificate", "证书校验失败"),
        ("timed out", "连接超时"),
        ("timeout", "连接超时"),
        ("getaddrinfo", "域名解析失败"),
        ("refused", "服务拒绝连接"),
        ("10061", "服务拒绝连接"),
    ):
        if kw in low:
            return human
    s = re.sub(r"\s+", " ", s)
    return s[:limit] if s else "取数失败"


# ---------------------------------------------------------------- 配置
# ---------------------------------------------------------------- 电脑采集（并入挂件进程）
# 需求原话：「这两个功能做到一起去啊，分两个 exe 干嘛」—— 挂件和采集本来就是同一台电脑上
# 的同一件事，所以：一个进程、一份配置、一个开机自启。采集逻辑仍在 whale_pc.py 里
# （它自己也能当 CLI 跑），这里只是把它挂到挂件进程的后台线程上。
_PC = None
_PC_ERR = ""


def _load_pc():
    """找并加载采集模块（whale_pc.py）。**不做源码拷贝** —— 拷贝迟早两份不同步。

    搜索顺序：exe 同目录 → 包内 _MEIPASS → 同级 whale-pc/ 工程目录（开发时用）。
    """
    import importlib.util
    cands = [os.path.join(BASE, "whale_pc.py"),
             os.path.join(getattr(sys, "_MEIPASS", BASE), "whale_pc.py"),
             os.path.join(os.path.dirname(BASE), "whale-pc", "whale_pc.py"),
             os.path.join(os.path.dirname(BASE), "whale_pc.py")]
    for p in cands:
        if not os.path.exists(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("whale_pc", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, ""
        except Exception as e:
            return None, "%s（%s）" % (e, os.path.basename(p))
    return None, "whale_pc.py 没找到，找过：%s" % " / ".join(os.path.basename(c) for c in cands)


_PC, _PC_ERR = _load_pc()


class PCCollect(object):
    """把采集器跑在挂件进程的后台线程里。

    ⚠️ 铁律：它出任何问题都不许拖垮挂件 —— 所有异常都吞掉、只记日志。
    """

    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.ok = False
        self.err = ""
        self.collector = None
        self.thread = None
        self.stop = None
        self.eff = {}
        try:
            if _PC is None:
                self.err = "采集模块没找到（%s）" % (_PC_ERR or "whale_pc.py 不在同目录")
                _log("[·] %s —— 只跑挂件，不采电脑数据" % self.err)
                return
            _PC.rebase(BASE)
            eff = dict(_PC.blank_cfg())
            col = dict(cfg.get("collect") or {})
            hub = cfg.get("hub") or {}
            for k, v in col.items():
                if k not in ("categories", "title_hints"):
                    eff[k] = v
            # 中枢地址/Token 直接复用挂件自己那套 —— 不让他填两遍
            eff["hub"] = {"base": hub.get("base") or eff["hub"]["base"],
                          "token": hub.get("token") or eff["hub"]["token"],
                          "pin_cert": hub.get("pin_cert") or "hub.crt",
                          "allow_http_fallback": False}     # ⚠️ 采集侧绝不允许明文回退
            for k in ("categories", "title_hints"):
                if col.get(k):
                    eff[k] = col[k]
            self.eff = eff
            if not eff.get("enabled", True):
                self.err = "配置里关掉了"
                return
            self.collector = _PC.Collector(eff, cfg_path)
            self.ok = True
        except Exception as e:
            self.err = "初始化失败：%s" % short_err(e)
            _log("[!] 采集 %s" % self.err)

    def start(self):
        if not self.ok or (self.thread and self.thread.is_alive()):
            return
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="whale-collect", daemon=True)
        self.thread.start()

    def _loop(self):
        try:
            self.collector.run_forever(stop=self.stop)
        except Exception as e:
            _log("[!] 采集线程退出：%s" % short_err(e))

    def stop_now(self):
        if self.stop:
            self.stop.set()
        self.ok = False

    # ---- 给右键菜单用的两份文字（不用命令行也能看）----
    def status_lines(self):
        if not self.ok or self.collector is None:
            return ["采集没在跑：%s" % (self.err or "未知原因"), "",
                    "（挂件本体不受影响，画面上照常）"]
        c = self.collector
        try:
            pend = _PC.queue_load(c.crypto)
        except Exception:
            pend = []
        h = self.eff.get("hub") or {}
        return [
            "设备名        %s" % c.device,
            "中枢          %s" % h.get("base"),
            "               （只有 HTTPS + 证书固定，没有明文回退）",
            "上报间隔      %s 秒   空闲判定 %s 秒   久坐提醒 %s 分钟" % (
                self.eff.get("interval_seconds"), self.eff.get("idle_threshold_seconds"),
                self.eff.get("sit_alert_minutes")),
            "本地待发队列  %d 条（Windows DPAPI 加密）" % len(pend),
            "最近一次上报  %s" % (c.last_report or "还没到结算点"),
            "当前窗口      活跃 %.1f 分 · 空闲 %.1f 分 · 连续活跃 %.0f 分" % (
                c.active_sec / 60.0, c.idle_sec / 60.0, c.sit_sec / 60.0),
            "日志          %s" % _PC.LOG_FILE,
        ]

    def preview_lines(self):
        if not self.ok or self.collector is None:
            c = None
        else:
            c = self.collector
        try:
            items = list(c.last_payload) if (c and c.last_payload) else (c.envelope() if c else [])
        except Exception:
            items = []
        lines = ["＝ 将要上传的内容（= AI 能看到的内容）＝", ""]
        if not items:
            lines += ["（这一轮还没有数据 —— 刚起来或一直在空闲）",
                      "会上传的指标名只有这几个：", "",
                      "  screen.active_minutes        活跃分钟",
                      "  screen.idle_minutes          空闲分钟",
                      "  app.<类别>_minutes           各类别分钟（dev / browser / study / office /",
                      "                               chat / media / game / other）",
                      "  pc.continuous_active_minutes 连续活跃分钟（久坐提醒用）", ""]
        else:
            lines += json.dumps(items, ensure_ascii=False, indent=2).splitlines() + [""]
        lines += ["＝ 明确不会上传的东西 ＝"]
        for x in ("窗口标题全文", "进程完整路径 / 文件名", "网址 / 文档名 / 搜索词",
                  "按键内容（不是键盘记录器）", "截图 / 剪贴板"):
            lines.append("  ✗ %s" % x)
        lines += ["", "只有「类别 + 分钟数」离开这台电脑；标题只在内存里用来归类，判完即弃。"]
        return lines


# ---------------------------------------------------------------- 随包字体
# 用华为的 HarmonyOS Sans（许可证见 assets/fonts/HarmonyOS-Sans-LICENSE.txt）：
#  · 免费商用、允许 embed/bundle，但**不许改字体**、**不许把字体单独再分发**、
#    **必须在软件里显著声明使用了它** —— 所以它是嵌在 exe 里、用私有注册加载的。
#  · Windows 上用 AddFontResourceExW + FR_PRIVATE：只对本进程生效，不动系统字体表。
FONT_FAMILY = "HarmonyOS Sans SC"
FONT_FILES = ("HarmonyOS_Sans_SC_Regular.ttf",)


def _font_dirs():
    out = []
    for d in (os.path.join(ASSETS, "fonts"),
              os.path.join(getattr(sys, "_MEIPASS", BASE), "assets", "fonts"),
              os.path.join(BASE, "fonts")):
        if os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def register_bundled_fonts():
    """把随包字体私有注册进本进程。返回 (成功注册的文件数, 尝试过的路径)。"""
    if not sys.platform.startswith("win"):
        return 0, []                    # 非 Windows 交给 fontconfig，不用管
    try:
        import ctypes
        gdi = ctypes.windll.gdi32
        FR_PRIVATE = 0x10
    except Exception as e:
        _log("[!] 字体注册不可用：%s" % e)
        return 0, []
    n, tried = 0, []
    for d in _font_dirs():
        for fn in FONT_FILES:
            p = os.path.join(d, fn)
            if not os.path.exists(p):
                continue
            tried.append(p)
            try:
                if gdi.AddFontResourceExW(ctypes.c_wchar_p(p), FR_PRIVATE, 0):
                    n += 1
            except Exception as e:
                _log("[!] 注册字体失败 %s：%s" % (fn, e))
    return n, tried


def blank_cfg():
    return {
        "version": VERSION,
        "pet": {
            "size": 200,
            "pos": None,
            "opacity": 0.97,
            "chroma": CHROMA_DEFAULT,
            "transparent": True,
            "always_on_top": True,
            "idle_sleep_seconds": 180,
            "sleep_opacity": 0.25,
            # 点一下随机换的表情（素材 exp_<名字>.png，缺哪个自动跳过哪个）
            "expressions": ["blush", "happy", "shy", "surprised", "angry", "sleepy"],
            "expression_ms": 1200,
            "assets": {"idle": "pet.png", "sleep": "pet_sleep.png",
                       "flip": "pet_flip.png", "drag": "pet_drag.png"},
        },
        "snap": {"enabled": True, "px": 22, "corners": True},
        "flip_on_left": True,
        "press_bounce": True,
        "sound": {"enabled": True, "kind": "pop"},    # 关 / pop(啵) —— 鸭子叫用户听过后不要了
        "click": {"push_queue": True, "data_timeout_seconds": 5, "expression": True},
        "balance": {
            "enabled": True,
            "provider": "deepseek",
            "api_key": "",
            "url": "https://api.deepseek.com/user/balance",
            "refresh_seconds": 60,
            "low_threshold": 10.0,
            "unit": "¥",
        },
        "hub": {
            "enabled": True,
            "base": "https://YOUR_SERVER:11443",
            "fallback_base": "",
            "token": "",
            "pin_cert": "hub.crt",
            "allow_http_fallback": True,
            "refresh_seconds": 60,
        },
        "collect": {                      # 电脑使用情况采集（与挂件同进程，不再单独一个 exe）
            "enabled": True,
            "device": "pc_windows",
            "interval_seconds": 300,
            "idle_threshold_seconds": 120,
            "sit_alert_minutes": 50,
        },
        "bubbles": {
            "first": {
                "name": "首次点击泡",
                "rows": [
                    [{"t": "value", "k": "balance", "label": "余额", "fmt": "{u}%.2f", "level": True}],
                    [{"t": "text", "text": "下一节 {next_class_time} {next_class}（{next_class_room}）"}],
                    [{"t": "dim", "text": "今天 {classes_today} 节课 · 屏幕 {screen_txt}"}],
                ],
            },
            "queue": [
                {"name": "提醒", "rows": [[{"t": "text", "text": "{reminders_1}"}],
                                          [{"t": "dim", "text": "{reminders_2}"}]]},
                {"name": "关心", "care_page": True,
                 "rows": [[{"t": "text", "text": "天气 {care_weather_txt}"}],
                          [{"t": "dim", "text": "{care_phone_txt} · {care_alarm_txt}"}],
                          [{"t": "dim", "text": "{care_listening_txt}"}],
                          [{"t": "dim", "text": "{care_sit_txt} · {care_env_txt}"}],
                          [{"t": "dim", "text": "快递/订单：{care_orders_txt}"}],
                          [{"t": "dim", "text": "{care_game_txt}"}],
                          [{"t": "dim", "text": "{care_pc_txt}"}]]},
                {"name": "闲聊",
                 "rows": [[{"t": "random", "weights": [1, 1, 1], "lines": [
                     "（歪头）主人，鲸鲸就在桌面这儿守着。",
                     "（翻了翻今天的数据）主人今天还顺利吗。",
                     "（小声）要鲸鲸念一句今天的课吗。",
                 ]}]]},
            ],
            "error": {"name": "连不上",
                      "rows": [[{"t": "text", "text": "（把数据翻出来又看了眼）连不上中枢，主人。"}],
                               [{"t": "dim", "text": "{hub_err}"}]]},
        },
    }


def load_cfg(path):
    cfg = blank_cfg()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            # ⚠️ 子字典必须单独合并：顶层 update 会把整个子字典换掉，
            #    于是新版本加的默认键在老配置里全丢（表现为"新功能没生效"）。
            for k, v in file_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
            if isinstance(file_cfg.get("pet"), dict) and isinstance(file_cfg["pet"].get("assets"), dict):
                cfg["pet"]["assets"] = dict(blank_cfg()["pet"]["assets"])
                cfg["pet"]["assets"].update(file_cfg["pet"]["assets"])
        except Exception as e:
            _log("[!] 配置文件读不了，用默认值：%s" % e)
    # ★ 老配置迁移：队列里没有「关心」页就补上（否则新数据源对你这个老配置永远不出现）
    try:
        q = ((cfg.get("bubbles") or {}).get("queue")) or []
        if not any("care_page" in (x or {}) for x in q):
            default_q = (blank_cfg().get("bubbles") or {}).get("queue") or []
            care = [x for x in default_q if (x or {}).get("care_page")]
            if care:
                idx = max(0, len(q) - 1)          # 插在最后一项（闲聊）之前
                cfg.setdefault("bubbles", {})["queue"] = list(q[:idx]) + care + list(q[idx:])
                _log("[·] 队列已自动补上「关心」页（新数据源）")
    except Exception:
        pass
    return cfg


def save_cfg(cfg, path):
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log("[!] 配置写不了：%s" % e)
        return False


# ---------------------------------------------------------------- 取数
def _ssl_ctx(cfg):
    """证书固定：只信我们自己那张 hub.crt（防中间人）。拿不到就退回系统 CA。"""
    name = str((cfg.get("hub") or {}).get("pin_cert") or "hub.crt")
    for p in (os.path.join(ASSETS, name), os.path.join(_BUNDLED, name), os.path.join(BASE, name)):
        if os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception as e:
                _log("[!] 固定证书加载失败：%s" % e)
    return ssl.create_default_context()


def http_json(url, headers=None, timeout=12, ctx=None, method="GET", body=None):
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method)
    # 默认 UA 会被 Cloudflare 拦（实测 403 / error code 1010）
    req.add_header("User-Agent", "WhaleDesk/%s (+desktop)" % VERSION)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def fetch_balance(cfg):
    """DeepSeek 余额：GET /user/balance。没填 Key 就返回 not-configured。"""
    b = cfg.get("balance") or {}
    if not b.get("enabled"):
        return {"ok": False, "skip": True, "error": "余额查询已关闭"}
    key = (b.get("api_key") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        return {"ok": False, "error": "还没填 Key"}
    try:
        if b.get("provider") == "deepseek":
            d = http_json(b.get("url") or "https://api.deepseek.com/user/balance",
                          {"Authorization": "Bearer " + key}, timeout=12)
            infos = d.get("balance_infos") or []
            info = infos[0] if infos else {}
            total = float(info.get("total_balance") or 0)
            return {"ok": True, "value": total, "currency": info.get("currency") or "CNY",
                    "available": d.get("is_available", True), "raw": info}
        d = http_json(b["url"], {"Authorization": "Bearer " + key}, timeout=12)
        return {"ok": True, "value": float(d.get("value") or d.get("balance") or 0), "currency": "CNY"}
    except Exception as e:
        return {"ok": False, "error": short_err(e)}


def fetch_hub(cfg):
    """中枢 /today：今日课程、下一节课、今天的提醒、屏幕/睡眠。"""
    h = cfg.get("hub") or {}
    if not h.get("enabled"):
        return {"ok": False, "skip": True, "error": "中枢已关闭"}
    base = (h.get("base") or "").rstrip("/")
    tok = h.get("token") or ""
    errs = []
    for url in [base] + ([h.get("fallback_base")] if h.get("allow_http_fallback") and h.get("fallback_base") else []):
        if not url:
            continue
        try:
            d = http_json(url.rstrip("/") + "/today", {"X-Token": tok}, timeout=10,
                          ctx=_ssl_ctx(cfg) if url.startswith("https") else None)
            d["_base"] = url
            return {"ok": True, "data": d}
        except Exception as e:
            errs.append(short_err(e))
    return {"ok": False, "error": errs[-1] if errs else "中枢没响应"}


def data_dict(cfg, hub, bal):
    """把两份原始数据摊平成「气泡里能引用的一层字段」。"""
    d = {
        "date": "", "weekday": "", "now": time.strftime("%H:%M"),
        "balance": 0.0, "balance_txt": "—", "balance_state": "off", "balance_pct": None,
        "balance_ok": False,
        "classes_today": 0, "next_class": "今天没课", "next_class_time": "--:--",
        "next_class_room": "", "next_class_in": None, "next_class_in_txt": "—",
        "screen_txt": "—", "sleep_txt": "—", "reminders_1": "（今天没有要说的）",
        # ★ 中枢 /today 的 care 块（新增数据源）：天气 / 在听 / 电量 / 闹钟 / 快递 / 温湿度 / 游戏
        "care_weather_txt": "—", "care_listening_txt": "—", "care_phone_txt": "—",
        "care_alarm_txt": "—", "care_orders_txt": "—", "care_env_txt": "—",
        "care_sit_txt": "—", "care_game_txt": "—", "care_pc_txt": "—", "care_has": False,
        "reminders_2": "", "hub_err": "（原因没记下来）", "hub_state": "off", "unit": "¥",
    }
    d["unit"] = (cfg.get("balance") or {}).get("unit") or "¥"

    if bal and bal.get("ok"):
        d["balance"] = float(bal.get("value") or 0)
        d["balance_txt"] = fmt_n(d["balance"])
        d["balance_ok"] = True
        thr = float((cfg.get("balance") or {}).get("low_threshold") or 0)
        d["balance_state"] = "bad" if d["balance"] < thr else "ok"
    elif bal and not bal.get("skip"):
        err = bal.get("error") or ""
        # 气泡里要自解释：没填 Key 就直说，别只显示一个"—"让人以为是坏了
        d["balance_txt"] = "未填 Key" if err == "还没填 Key" else "取不到"
        d["balance_state"] = "bad"
        d["balance_note"] = err

    if hub and hub.get("ok"):
        t = hub["data"]
        d["hub_state"] = "ok"
        try:
            wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][time.localtime().tm_wday]
        except Exception:
            wd = ""
        d["weekday"] = wd
        d["date"] = t.get("date") or ""
        dig = t.get("digest") or {}
        d["classes_today"] = dig.get("classes_today") or len(t.get("classes_today") or [])
        nxt = t.get("next_class") or None
        if nxt:
            d["next_class"] = nxt.get("name") or "有课"
            d["next_class_time"] = nxt.get("start") or "--:--"
            d["next_class_room"] = nxt.get("room") or "教室"
            mins = nxt.get("in_minutes")
            if mins is None:
                d["next_class_in_txt"] = "待上课"
            elif mins <= 0:
                d["next_class_in_txt"] = "正在进行"
            else:
                d["next_class_in_txt"] = "%d 分钟后" % mins
            d["next_class_in"] = mins
        elif d["classes_today"]:
            d["next_class"] = "今天的课都上完了"
        sc = dig.get("screen_minutes")
        if sc:
            d["screen_txt"] = "%d 小时 %d 分" % (int(sc) // 60, int(sc) % 60)
        sl = dig.get("sleep_minutes")
        if sl:
            d["sleep_txt"] = "%d 小时 %d 分" % (int(sl) // 60, int(sl) % 60)
        rs = [r.get("text") or "" for r in (t.get("reminders") or [])][:2]
        if rs:
            d["reminders_1"] = rs[0]
            d["reminders_2"] = rs[1] if len(rs) > 1 else ""
        # ★ care 块 → 可直接放进气泡模板的短句
        care = t.get("care") or {}

        def _hm(mins):
            mins = int(mins)
            return ("%d 小时 %d 分" % (mins // 60, mins % 60)) if mins >= 60 else ("%d 分钟" % mins)

        wx = (care.get("weather") or {}).get("today") or {}
        if wx.get("desc"):
            s = "%s %d~%d℃" % (wx["desc"], float(wx.get("tmin") or 0), float(wx.get("tmax") or 0))
            if wx.get("rain_prob") is not None:
                s += " · 降雨 %d%%" % int(wx["rain_prob"])
            d["care_weather_txt"], d["care_has"] = s, True
        li = care.get("listening") or {}
        if li.get("title"):
            d["care_listening_txt"] = "在听 " + li["title"] + (" · " + li["artist"] if li.get("artist") else "")
            d["care_has"] = True
        bt = care.get("battery") or {}
        if bt.get("percent") is not None:
            d["care_phone_txt"] = "手机 %d%%%s" % (int(bt["percent"]), "（充电中）" if bt.get("charging") else "")
            d["care_has"] = True
        al = care.get("next_alarm") or {}
        if al.get("in_minutes") is not None:
            d["care_alarm_txt"] = "闹钟 %s后" % _hm(al["in_minutes"])
            d["care_has"] = True
        od = care.get("orders_7d") or {}
        if od:
            d["care_orders_txt"] = "快递 %d · 下单 %d" % (od.get("shipping", 0), od.get("paid", 0))
            d["care_has"] = True
        env = care.get("env") or {}
        if env:
            parts = []
            if env.get("temp") is not None:
                parts.append("%.1f℃" % env["temp"])
            if env.get("hum") is not None:
                parts.append("湿度 %.0f%%" % env["hum"])
            d["care_env_txt"] = " · ".join(parts)
            d["care_has"] = True
        if care.get("continuous_active_minutes") is not None:
            d["care_sit_txt"] = "连续活跃 %s" % _hm(care["continuous_active_minutes"])
            d["care_has"] = True
        pcx = care.get("pc") or {}
        if pcx:
            ps = []
            if pcx.get("disk_free_percent") is not None:
                ps.append("系统盘剩 %.0f%%" % pcx["disk_free_percent"])
            if pcx.get("mem_percent") is not None:
                ps.append("内存 %.0f%%" % pcx["mem_percent"])
            if pcx.get("uptime_hours") is not None:
                h = float(pcx["uptime_hours"])
                ps.append("开机 %.1f 天" % (h / 24.0) if h >= 24 else "开机 %.0f 小时" % h)
            if pcx.get("window_switches_today") is not None:
                ps.append("切换 %d 次" % int(pcx["window_switches_today"]))
            if ps:
                d["care_pc_txt"] = " · ".join(ps)
                d["care_has"] = True
        gm = care.get("games_minutes_today") or {}
        if gm:
            n, m = list(gm.items())[0]
            d["care_game_txt"] = "游戏 %s %d 分钟" % (n, int(m))
            d["care_has"] = True
    elif hub and not hub.get("skip"):
        d["hub_state"] = "bad"
        d["hub_err"] = hub.get("error") or "原因没记下来"
        d["next_class"] = "（中枢连不上）"
        d["next_class_time"] = "--:--"
        d["next_class_room"] = ""
    return d


def render_tpl(s, d):
    """{key} 占位符替换。缺的键原样留着，方便一眼看出配置写错了。"""
    def sub(m):
        k = m.group(1)
        return str(d[k]) if k in d else m.group(0)
    return re.sub(r"\{([a-z_0-9]+)\}", sub, s or "")


# ---------------------------------------------------------------- 挂件
class Widget(object):
    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.root = tk.Tk()
        self.root.title(APP)
        self.state = "pet"                  # pet | data | sleep
        self.pressed = False
        self.dragging = False
        self.flipped = False
        self.bubble_idx = -1
        self.pop_ts = 0.0                   # 气泡"冒出来"的起点
        self.last_touch = time.time()
        self.data_ts = 0.0
        self.anim = {}                      # 临时动画：{名字: {...}}
        self.roll = {}                      # 数字滚动：{key: {...}}
        self.d = data_dict(cfg, None, None)  # 当前数据
        self.hub_res = None
        self.bal_res = None
        self.net_lock = threading.Lock()
        self.busy = False
        self.img = {}                       # 原始姿态图（已缩到目标高）
        self.img_cache = {}                 # 缩放/镜像变体的缓存
        self.pose = "idle"
        self._drag_off = (0, 0)
        self._down_xy = (0, 0)
        self.rand_last = {}
        self.pet_xy = None                  # 形象的屏幕坐标（真相来源），apply_geometry 里填

        self.font_registered, self.font_paths = register_bundled_fonts()
        self.refresh_fonts()
        self.sq = 0.0                        # 当前拉伸量 0..1（单调：拉出去→弹回来，见 animate）
        self.express = None                  # 正在显示的表情名（点一下随机换，见 show_expression）
        self.express_until = 0.0             # 表情显示到什么时候
        self._rand_frozen = {}               # 随机行的冻结结果（防止每帧重抽）
        self._click_t = 0.0                   # 上一次点击的时刻（用来决定"还在拉"还是"该弹回"）
        self._tick_t = time.time()

    def refresh_fonts(self):
        """字号层级（高级感一半来自层级）：值大而亮、正文中等、注释小而暗，随形象尺寸缩放。

        ⚠️ 加缓存：字号只在 200/260 两个档位跳变，但 set_size 每次都调这里 ——
           Windows 上重新创建字体对象要重新解析那个 8MB 的中文字体，会明显卡顿。
        """
        try:
            size = int((self.cfg.get("pet") or {}).get("size") or 200)
        except Exception:
            size = 200
        base = 10 if size <= 200 else (11 if size <= 260 else 12)
        if getattr(self, "_font_base", None) == base and getattr(self, "f_val", None) is not None:
            return                        # 同一个档位 → 直接复用，别再建字体
        self.font = self.pick_font(base)              # 通用（聊天窗等还在用它取字体族）
        self.f_val = self.pick_font(base + 6, bold=True)
        self.f_lab = self.pick_font(base - 1)
        self.f_txt = self.pick_font(base + 1)
        self.f_dim = self.pick_font(base)
        self._font_base = base              # 缓存标记（漏了这句缓存永远不命中）

        self.setup_window()
        self._load_images()
        self._bind()
        self.apply_geometry()
        self.refresh(initial=True)
        # 首次绘制必须等窗口真的 mapped —— 在那之前画布量到的是 1x1，
        # 什么都画不出来（表现成"启动后桌面上一片空白"）。
        self.root.after(80, self._first_draw)

    def _first_draw(self):
        try:
            self.apply_geometry()
        except Exception:
            pass
        self.redraw()

    # ---------------- 字体：必须拿一个汉字去量宽度，量得出来才认 ----------------
    def pick_font(self, size, bold=False):
        # 随包的鸿蒙字体排第一（Windows 上已私有注册，名字就是 HarmonyOS Sans SC）；
        # 拿不到就依次回落到系统字体 —— 保证"有就用鸿蒙，没有也不会没字"
        cands = [FONT_FAMILY, "Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑",
                 "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei",
                 "PingFang SC", "DejaVu Sans"]
        fams = set()
        try:
            fams = set(tkfont.families(self.root))
        except Exception:
            pass
        for c in cands:
            if c not in fams:
                continue
            try:
                f = tkfont.Font(family=c, size=size,
                                weight=("bold" if bold else "normal"))
                if f.measure("周") > 0:
                    return f
            except Exception:
                pass
        try:
            for c in sorted(fams):
                f = tkfont.Font(family=c, size=size,
                                weight=("bold" if bold else "normal"))
                if f.measure("周") > 0:
                    return f
        except Exception:
            pass
        return tkfont.Font(size=size, weight=("bold" if bold else "normal"))

    # ---------------- 窗口三件套 ----------------
    def setup_window(self):
        p = self.cfg.get("pet") or {}
        try:
            self.root.overrideredirect(True)
        except Exception:
            pass
        for opt, val in (("-topmost", bool(p.get("always_on_top", True))),
                         ("-alpha", float(p.get("opacity", 0.97) or 0.97))):
            try:
                self.root.wm_attributes(opt, val)
            except Exception:
                pass
        self.tmode = "none"
        if p.get("transparent", True) and not sys.platform.startswith("linux"):
            try:
                self.root.wm_attributes("-transparentcolor", p.get("chroma") or CHROMA_DEFAULT)
                self.tmode = "chroma"
            except Exception:
                self.tmode = "alpha"
        elif p.get("transparent", True):
            # Linux/X11 拿不到 ARGB visual，-transparentcolor 直接 TclError。
            # 别给用户一个突兀的方块 —— 退化成圆角卡片底（看起来是有意设计的挂件）。
            self.tmode = "solid"
        self.root.configure(bg=(p.get("chroma") or CHROMA_DEFAULT) if self.tmode != "solid" else "#12161c")
        self.cv = tk.Canvas(self.root, highlightthickness=0, bd=0,
                            bg=(p.get("chroma") or CHROMA_DEFAULT) if self.tmode != "solid" else "#12161c")

    # ---------------- 素材 ----------------
    def _asset(self, name):
        return os.path.join(ASSETS, name)

    def _load_images(self):
        p = self.cfg.get("pet") or {}
        names = p.get("assets") or {}
        sfx = "_win" if sys.platform.startswith("win") else ""
        want = {"idle": names.get("idle") or "pet.png",
                "sleep": names.get("sleep") or "pet_sleep.png",
                "flip": names.get("flip") or "pet_flip.png",
                "drag": names.get("drag") or "pet_drag.png"}
        # ⚠️ 按尺寸缓存整套图：换尺寸时反复"重读磁盘 + 重算缩放"会卡（4 姿态 × 2 张）
        _wh = max(24, int(p.get("size") or 200))
        _cached = (getattr(self, "_imgs_by_size", None) or {}).get(_wh)
        if _cached:
            self.img = dict(_cached)
            self.img_cache = {}
            _log("[·] 姿态素材（%dpx 缓存命中，免重读）" % _wh)
            return

        raws = {}
        loaded = {}
        for key, fn in want.items():
            stem, ext = os.path.splitext(fn)
            # ⚠️ 顺序不能反：Windows 上 **优先** 用拍平过的 *_win.png，
            #    基础图只当兜底。写反了（只在基础图缺失时才用 _win）会一直加载带 alpha 的图 ——
            #    Tk 会把半透明边缘和近黑的画布底色混在一起，人物周围一圈 1~2px 黑边。
            cands = ([stem + sfx + ext] if sfx else []) + [fn]
            for c in cands:
                path = self._asset(c)
                if os.path.exists(path):
                    try:
                        raws[key] = tk.PhotoImage(file=path)
                        loaded[key] = c
                        break
                    except Exception as e:
                        _log("[!] 素材读不了 %s：%s" % (c, e))
        # 表情素材（点一下随机换一张）：exp_<名字>.png。**缺哪个跳过哪个** ——
        # 这样"图还没出"时功能自动降级成不换表情，出了图丢进 assets/ 就生效（不用重打包）。
        for _nm in [str(x) for x in (p.get("expressions") or [])]:
            _fn = "exp_%s.png" % _nm
            _stem, _ext = os.path.splitext(_fn)
            for _c in (([_stem + sfx + _ext] if sfx else []) + [_fn]):
                _p = self._asset(_c)
                if os.path.exists(_p):
                    try:
                        raws["exp:" + _nm] = tk.PhotoImage(file=_p)
                        loaded["exp:" + _nm] = _c
                        break
                    except Exception as e:
                        _log("[!] 表情读不了 %s：%s" % (_c, e))
        if "idle" not in raws:
            raws["idle"] = tk.PhotoImage(width=120, height=120)
        _log("[·] 姿态素材：%s" % " ".join("%s=%s" % kv for kv in loaded.items()))
        # ⭐ 各姿态按**自己的相对比例**缩放：以站立图为基准，
        #    打盹图（画得矮）要保持它自己的矮 —— 全缩到同一尺寸就没有"缩成一团"了。
        base_h = raws["idle"].height() or 1
        want_h = max(24, int(p.get("size") or 200))
        for key, raw in raws.items():
            hh = max(8, int(round(want_h * raw.height() / float(base_h))))
            self.img[key] = self._fit_height(raw, hh)
        self.img_cache = {}
        self._imgs_by_size = getattr(self, "_imgs_by_size", {})
        self._imgs_by_size[want_h] = dict(self.img)     # 下次切回来就是瞬时的

    def _img_dims(self, key):
        im = self.img.get(key) or self.img["idle"]
        return im.width(), im.height()

    def _fit_height(self, im, want):
        """把图缩到目标高。subsample 只能整数倍缩小、zoom 只能整数倍放大，两者组合逼近。"""
        r = want / float(im.height())
        best = None
        for s in range(1, 13):
            z = int(round(r * s))
            if 1 <= z <= 12:
                err = abs(z / float(s) - r)
                best = (err, z, s) if best is None or err < best[0] else best
        if not best:
            return im
        _, z, s = best
        if z > 1:
            im = im.zoom(z)
        if s > 1:
            im = im.subsample(s)
        return im

    def _pixel_scale(self, im, sx, sy, tag):
        """非等比缩放（Q 弹要「压扁」）。Tk 的 zoom/subsample 只能整数倍等比，
        所以走 Tcl 原生的 `copy -from ... -to ...`：源/目标矩形尺寸不同时，
        Tk 自己会重采样（C 实现，快）。结果按 (姿态, 比例) 缓存。"""
        ck = (tag, round(sx, 2), round(sy, 2))
        if ck in self.img_cache:
            return self.img_cache[ck]
        w, h = im.width(), im.height()
        tw, th = max(1, int(round(w * sx))), max(1, int(round(h * sy)))
        try:
            new = tk.PhotoImage(width=tw, height=th)
            new.tk.call(new.name, "copy", im.name, "-from", 0, 0, w, h, "-to", 0, 0, tw, th)
        except Exception as e:
            _log("[!] 缩放出错：%s" % e)
            return im
        self.img_cache[ck] = new
        return new

    def _mirror(self, im, tag):
        """水平镜像。优先用构建期生成好的 *_flip.png；没有就逐列 copy 现算。"""
        ck = (tag, "flip")
        if ck in self.img_cache:
            return self.img_cache[ck]
        w, h = im.width(), im.height()
        try:
            new = tk.PhotoImage(width=w, height=h)
            for x in range(w):          # 一列一列从右往左搬（Tcl 侧执行，很快）
                new.tk.call(new.name, "copy", im.name,
                            "-from", x, 0, x + 1, h, "-to", w - 1 - x, 0, w - x, h)
        except Exception as e:
            _log("[!] 镜像出错：%s" % e)
            return im
        self.img_cache[ck] = new
        return new

    # ---------------- 事件 ----------------
    def _bind(self):
        self.cv.bind("<Button-1>", self.on_press)
        self.cv.bind("<B1-Motion>", self.on_motion)
        self.cv.bind("<ButtonRelease-1>", self.on_release)
        self.cv.bind("<Button-3>", self.on_right)
        self.root.bind("<Escape>", lambda e: self.hide_bubble())
        self.cv.pack(fill="both", expand=True)

    def on_press(self, e):
        # 留白区（为拉伸预留的那圈）不该抢点击 —— 否则会觉得"点她旁边也能拖她"
        if not self._hit_pet(e.x, e.y):
            self._press_missed = True       # 记下来，松手时也**不要**翻气泡
            return
        self._press_missed = False
        self.last_touch = time.time()
        self._down_xy = (e.x_root, e.y_root)
        px, py = self.pet_xy or [self.root.winfo_x(), self.root.winfo_y()]
        self._drag_off = (e.x_root - int(px), e.y_root - int(py))
        self.pressed = True
        self.dragging = False
        if self.state == "sleep":
            self.set_state("data")
        if (self.cfg.get("press_bounce", True)):
            self.anim["press"] = {"t0": time.time(), "dur": 0.10}

    def on_motion(self, e):
        if not self.pressed:
            return
        dx = abs(e.x_root - self._down_xy[0])
        dy = abs(e.y_root - self._down_xy[1])
        if not self.dragging and (dx > 3 or dy > 3):
            self.dragging = True
        if self.dragging:
            self.pet_xy = [e.x_root - self._drag_off[0], e.y_root - self._drag_off[1]]
            self.apply_geometry(keep_pos=True)

    def on_release(self, e):
        if getattr(self, "_press_missed", False):
            self._press_missed = False
            self.pressed = False
            return                          # 点在留白/窗外 → 什么都不做（别白翻一格）
        self.last_touch = time.time()
        self.anim.pop("press", None)
        if self.dragging:
            self.pressed = False
            self.dragging = False
            self.snap_now()
            self.play_sound("pop")            # 松手吸附：一声轻"啵"
            self.redraw()
            return
        self.pressed = False
        self.on_click()                       # 声音在 on_click 里放

    def show_expression(self):
        """点一下随机换一张表情（同一张不连续出现）。素材缺了就什么都不做。"""
        if not (self.cfg.get("click") or {}).get("expression", True):
            return False
        names = [k[4:] for k in self.img if k.startswith("exp:")]
        if not names:
            return False
        cand = [n for n in names if n != self.express] or names
        self.express = random.choice(cand)
        try:
            hold = max(200, int((self.cfg.get("pet") or {}).get("expression_ms") or 1200))
        except Exception:
            hold = 1200
        self.express_until = time.time() + hold / 1000.0

    def set_expression(self, name):
        """给菜单用：直接指定一个表情（None = 取消）。"""
        if name and ("exp:" + name) in self.img:
            self.express, self.express_until = name, time.time() + 99999
        else:
            self.express, self.express_until = None, 0.0

    def _hit_pet(self, cx, cy):
        """点是否落在**形象本体或气泡**上（on_press 用这个）。

        拆两层是因为"留白不抢点击"要测得准：气泡开着时它盖住整块画布，
        混在一起测会把留白也算命中（第一版断言就是这么假通过的）。
        """
        return self._hit_pet_body(cx, cy) or self._hit_bubble(cx, cy)

    def _hit_bubble(self, cx, cy):
        if self.state != "data":
            return False
        try:
            cv = self.cv
            w = cv.winfo_width() or 1
            h = cv.winfo_height() or 1
            pw, _ph = self._pet_box()
            bl = bool(getattr(self, "bub_left", False))
            bw_ = max(120, self.bub_w or (w - pw - BGAP))
            bx0 = 0 if bl else pw + BGAP
            return (bx0 <= cx <= bx0 + bw_) and (0 <= cy <= h)
        except Exception:
            return False

    def _hit_pet_body(self, cx, cy):
        """点是否落在**形象本体**上（不含气泡、不含为拉伸预留的透明留白）。"""
        try:
            cv = self.cv
            w = cv.winfo_width() or 1
            h = cv.winfo_height() or 1
            pw, _ph = self._pet_box()
            bl = bool(getattr(self, "bub_left", False))
            px = (w - pw // 2) if bl else pw // 2
            im = self._cur_pet_image()
            iw, ih = im.width(), im.height()
            py = h - ih // 2
            return (px - iw // 2 <= cx <= px + iw // 2) and (py - ih // 2 <= cy <= py + ih // 2)
        except Exception:
            return True                      # 判不了就当作命中，别把功能点没了

    def on_right(self, e):
        self.last_touch = time.time()
        self.menu(e)

    # ---------------- 吸附 + 镜像 ----------------
    def snap_now(self):
        s = self.cfg.get("snap") or {}
        pw, ph = self._pet_box()
        px, py = self.pet_xy or [self.root.winfo_x(), self.root.winfo_y()]
        if not s.get("enabled", True):
            return
        thr = int(s.get("px") or 22)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        corners = bool(s.get("corners", True))
        near_l = px <= thr
        near_r = px + pw >= sw - thr
        near_t = py <= thr
        near_b = py + ph >= sh - thr
        tx, ty = px, py
        if near_l:
            tx = 0
        elif near_r:
            tx = sw - pw
        if near_t:
            ty = 0
        elif near_b:
            ty = sh - ph
        if not corners:                  # 只要边不要角：只吸更近的那一条
            if near_l or near_r:
                ty = py
        if (tx, ty) != (px, py):
            self.slide_to(tx, ty)
        # 贴左吸附时整体水平镜像翻转
        self.set_flipped(bool(near_l or tx == 0), animated=True)
        self.apply_geometry(keep_pos=True)      # 重算窗口 x（气泡可能因此翻到左边）

    def slide_to(self, tx, ty, dur=0.14):
        self.anim["slide"] = {"t0": time.time(), "dur": dur,
                              "from": (int((self.pet_xy or [0, 0])[0]), int((self.pet_xy or [0, 0])[1])),
                              "to": (int(tx), int(ty))}

    def set_flipped(self, on, animated=False):
        want = bool(on) and bool(self.cfg.get("flip_on_left", True))
        if want == self.flipped:
            return
        self.flipped = want
        if animated:
            # 0.18 秒的翻转：把宽度压到近乎一条线，再从中线展开（镜像在中间点换）
            self.anim["flip"] = {"t0": time.time(), "dur": 0.18}
        self.redraw()

    def _reassert_chroma(self):
        """Windows 上把「键控色」重新设一遍。

        ⚠️ 为什么需要：Windows 的 layered window 里，**只设 -alpha 会把 color key 冲掉**。
           表现就是拖动/唤醒时"整个窗口透出一圈黑边框"（用户原话："沿着边拖动时概率会
           显示出来整个的黑边框"）。所以每次动过 alpha 或 geometry 之后都要补一次。
        """
        if not sys.platform.startswith("win") or getattr(self, "tmode", "") != "chroma":
            return
        try:
            self.root.wm_attributes("-transparentcolor",
                                    (self.cfg.get("pet") or {}).get("chroma") or CHROMA_DEFAULT)
        except Exception:
            pass

    # ---------------- 状态 ----------------
    def set_state(self, st):
        if st == self.state:
            return
        self.state = st
        p = self.cfg.get("pet") or {}
        # 休息态整窗淡下去（-alpha 全平台可用）；回神时恢复
        try:
            self.root.wm_attributes(
                "-alpha", float(p.get("sleep_opacity") or 0.25) if st == "sleep"
                else float(p.get("opacity") or 0.97))
        except Exception:
            pass
        self._reassert_chroma()          # ⚠️ 设完 alpha 必须补键控色，否则透出黑边
        if st == "data":
            self.data_ts = time.time()
        if st != "data":
            self.bubble_idx = -1
        self.apply_geometry(keep_pos=True)      # 数据态窗口要变宽
        self.redraw()

    def hide_bubble(self):
        self.bubble_idx = -1
        self.set_state("pet")

    def on_click(self):
        """点击序列：第一下给数据泡，之后再点顺着队列走，走到头收起来。"""
        # 每一下点击都给一发「左右拉伸 → 回弹」（高度不变）。连点时**振幅叠加**、
        # 相位不重置 —— 否则动画被反复重启，看起来就是"抽搐"（用户报过）。
        # 用户要求：左键点一下 = **随机换一张表情图**（原来的"左右拉伸"他不要了）
        self.show_expression()
        self.play_sound("click")
        if not (self.cfg.get("click") or {}).get("push_queue", True):
            if self.state == "data":
                self.hide_bubble()
            else:
                self.bubble_idx = 0
                self.set_state("data")
            return
        if self.state != "data":
            self.bubble_idx = 0
            self.pop_ts = time.time()
            self.set_state("data")
            return
        q = list((self.cfg.get("bubbles") or {}).get("queue") or [])
        self.bubble_idx += 1
        if self.bubble_idx > len(q):
            self.hide_bubble()
        else:
            self.pop_ts = time.time()
            self.redraw()

    # 音效是自己合成的 wav（tools/make_sfx.py，纯标准库算的波形 → 无版权问题）
    def sfx_file(self, kind):
        snd = self.cfg.get("sound") or {}
        # 旧的 duck 选项已废弃 → 一律回落到 pop（别让配置里残留的 duck 去指一个不存在的文件）
        k = snd.get("kind") or "pop"
        if k == "duck":
            k = "pop"
        name = ("sfx_%s.wav" % k) if kind == "click" else "sfx_pop.wav"
        return os.path.join(ASSETS, name)

    def play_sound(self, kind):
        if not (self.cfg.get("sound") or {}).get("enabled", True):
            return
        try:
            p = self.sfx_file(kind)
            if not os.path.exists(p):
                return
            if sys.platform.startswith("win"):
                import winsound
                winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | winsound.SND_NODEFAULT)
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", p])
        except Exception:
            pass

    # ---------------- 数据刷新（网络在线程里，Tk 只在主线程碰）----------------
    def refresh(self, initial=False):
        if self.busy:
            return
        self.busy = True
        cfg = self.cfg

        def work():
            hub = fetch_hub(cfg) if (cfg.get("hub") or {}).get("enabled") else {"ok": False, "skip": True}
            bal = fetch_balance(cfg) if (cfg.get("balance") or {}).get("enabled") else {"ok": False, "skip": True}
            with self.net_lock:
                self.hub_res, self.bal_res = hub, bal
            self.busy = False
        threading.Thread(target=work, daemon=True).start()

    def _pick_net(self):
        with self.net_lock:
            hub, bal = self.hub_res, self.bal_res
            self.hub_res = self.bal_res = None
        if hub is None and bal is None:
            return
        d = data_dict(self.cfg, hub, bal)
        # 余额变了 → 数字滚动
        old = self.d.get("balance")
        new = d.get("balance")
        if old is not None and new is not None and abs(float(old) - float(new)) > 1e-9:
            self.roll["balance"] = {"from": float(old), "to": float(new), "t0": time.time(), "dur": 0.5}
        self.d = d
        self.redraw()

    # ---------------- 几何 ----------------
    def _pet_box(self):
        """窗口用「最大姿态盒」固定，绝不按当前那张图贴合 —— 否则换姿态会突然移一下。

        ⚠️ 这里额外加一点 STRETCH_PAD（现在只剩 2px）：窗口若正好等于形象宽，
        任何比它宽一点点的姿态/表情都会被窗口边界**硬裁**成一条竖直切边
        （用户报过"点了右边多一个图片的边缘"）。
        """
        ws, hs = [], []
        # ⚠️ 也要算上**表情图**：表情（例如"惊讶"举拳）可能比站立还宽，
        #    漏掉就会被窗口裁掉一条边（用户反复报过"被裁/多一块"，别再犯）。
        for k in list(self.img.keys()):
            if k in ("idle", "sleep", "flip", "drag") or k.startswith("exp:"):
                w, h = self._img_dims(k)
                ws.append(w)
                hs.append(h)
        if not ws:
            return 120, 120
        return max(ws) + STRETCH_PAD * 2, max(hs)

    def _bubble_spec(self):
        b = self.cfg.get("bubbles") or {}
        if self.bubble_idx <= 0:
            return b.get("first") or {}
        q = list(b.get("queue") or [])
        i = self.bubble_idx - 1
        if i >= len(q):
            return b.get("first") or {}
        item = q[i]
        if isinstance(item, dict) and item.get("alt"):
            cands = [c for c in item["alt"] if isinstance(c, dict)]
            if cands:
                total = sum(max(1, int(c.get("weight") or 1)) for c in cands)
                k = int(time.time() * 1000) % total
                acc = 0
                for c in cands:
                    acc += max(1, int(c.get("weight") or 1))
                    if k < acc:
                        return c
                return cands[-1]
        if isinstance(item, dict) and item.get("rows"):
            return item
        return b.get("first") or {}

    def _bubble_rows(self):
        """把配置里的模块摊成「行 → 已算好宽度的绘制指令」，行数固定（不做自动换行）。"""
        spec = self._bubble_spec()
        d = dict(self.d)
        d["next_class_in_txt"] = d.get("next_class_in_txt") or "—"
        rows = []
        for row in (spec.get("rows") or []):
            mods = []
            for m in row:
                t = m.get("t")
                if t == "value":
                    k = m.get("k") or "balance"
                    raw = d.get(k)
                    txt = None
                    if k == "balance" and not d.get("balance_ok"):
                        # ⚠️ 没取到余额时**绝不能**走 %.2f —— float(None or 0) 会渲染成
                        #    "¥0.00"，看起来像余额真的归零了（本会话在真跑的 exe 输出里抓到的）
                        txt = d.get("balance_txt") or "—"
                    elif k == "balance":
                        txt = (m.get("fmt") or "{u}%.2f")
                        txt = txt.replace("{u}", d.get("unit") or "¥")
                        try:
                            txt = txt % float(raw or 0)
                        except Exception:
                            txt = d.get("balance_txt") or "—"
                    else:
                        try:
                            txt = (m.get("fmt") or "%s") % raw
                        except Exception:
                            txt = str(raw)
                    mods.append({"t": "value", "label": render_tpl(m.get("label") or "", d),
                                 "text": txt, "k": k, "level": bool(m.get("level")),
                                 "pct": m.get("pct")})
                elif t == "random":
                    lines = [render_tpl(x, d) for x in (m.get("lines") or [])]
                    if not lines:
                        continue
                    ws = list(m.get("weights") or [1] * len(lines))
                    while len(ws) < len(lines):
                        ws.append(1)
                    key = json.dumps(lines, ensure_ascii=False)[:60]
                    # ⚠️ 随机行必须**定住**：原来用 `int(time.time()*1000) % total` 当种子，
                    #    而气泡每次重画都会走这里（动画期间每帧一次）→ 文字在几个候选之间
                    #    疯狂乱跳。用户原话："点第三下的时候信息会乱跳"（第 3 下正好是随机行）。
                    #    现在把结果按「第几格气泡 + 这次弹出的时刻」冻结，同一格只抽一次。
                    fk = (self.bubble_idx, round(float(getattr(self, "pop_ts", 0) or 0), 2), key)
                    pick = self._rand_frozen.get(fk)
                    if pick is None:
                        total = sum(max(1, int(x)) for x in ws)
                        r0 = random.random() * total
                        acc = 0
                        for i, ln in enumerate(lines):
                            acc += max(1, int(ws[i]))
                            if r0 < acc:
                                pick = ln
                                break
                        pick = pick or lines[0]
                        if len(lines) > 1 and pick == self.rand_last.get(key):   # 不连续重复
                            pick = lines[(lines.index(pick) + 1) % len(lines)]
                        self.rand_last[key] = pick
                        self._rand_frozen[fk] = pick
                        if len(self._rand_frozen) > 64:      # 别无限涨
                            for k in list(self._rand_frozen)[:32]:
                                del self._rand_frozen[k]
                    mods.append({"t": "text", "text": pick, "dim": False})
                elif t == "dim":
                    _tx = render_tpl(m.get("text") or "", d)
                    if _tx.strip():
                        mods.append({"t": "text", "text": _tx, "dim": True})
                elif t == "img":
                    path = self._asset(m.get("file") or "")
                    if os.path.exists(path):
                        mods.append({"t": "img", "path": path, "w": int(m.get("w") or 160)})
                else:
                    _tx = render_tpl(m.get("text") or "", d)
                    if _tx.strip():                          # 空行别占一格
                        mods.append({"t": "text", "text": _tx, "dim": False})
            # ★ 整行都是占位符（"—"）就整行不画（新数据源没数据时不留空行）
            _txts = [x.get("text") for x in mods if x.get("t") in ("value", "text", "dim")]
            if _txts and all((s in (None, "", "—")) for s in _txts):
                continue
            rows.append(mods)
        if not rows:
            rows = [[{"t": "text", "text": "（这一泡还是空的）", "dim": True}]]
        return rows

    def _row_fonts(self, mods):
        """一行里按"最大那个"决定字体：值行用大字，纯注释行更小。"""
        if any(m["t"] == "value" for m in mods):
            return self.f_val, self.f_lab
        if all(m.get("dim") for m in mods):
            return self.f_dim, self.f_dim
        return self.f_txt, self.f_txt

    def _row_h(self, mods):
        if any(m["t"] == "value" for m in mods):
            return ROW_H_VAL
        if any(m["t"] == "img" for m in mods):
            return max([m.get("h") or 24 for m in mods if m["t"] == "img"] + [24]) + 14
        base = ROW_H_DIM if all(m.get("dim") for m in mods) else ROW_H_TXT
        # ⭐ 长句要折行 → 行高按行数算（否则折出来的第二行会压到下一格上）
        f, _fl = self._row_fonts(mods)
        need = 1
        for m in mods:
            if m["t"] == "text":
                need = max(need, wrap_lines(m.get("text") or "", f, BUB_WRAP_W))
        need = min(need, MAX_WRAP_LINES)
        return base * need

    def _row_width(self, mods):
        f, fl = self._row_fonts(mods)
        w = 0
        for i, m in enumerate(mods):
            if m["t"] == "value":
                w += fl.measure((m["label"] + " ") if m["label"] else "") + \
                     f.measure(m["text"] or "") + (12 if m.get("level") else 0)
            elif m["t"] == "img":
                w += m["w"]
            else:
                # 文字宽度封顶在折行宽度：更长的会折行，而不是把卡片撑宽
                w += min(f.measure(m["text"] or ""), BUB_WRAP_W)
            if i:
                w += 12
        return w

    def _bubble_h(self):
        return BPAD_Y * 2 + sum(self._row_h(r) for r in self._bubble_rows())

    def _bubble_w(self):
        widest = max(self._row_width(r) for r in self._bubble_rows()) if self._bubble_rows() else 120
        return min(BUB_MAXW, max(140, widest + BPAD * 2))

    def apply_geometry(self, keep_pos=False):
        pw, ph = self._pet_box()
        if self.state == "data":
            h = max(ph, self._bubble_h())
            w = pw + BGAP + self._bubble_w()
        else:
            w, h = pw, ph
        self.bub_w = w - pw - BGAP
        # ⭐ 形象的屏幕位置是"真相来源"：数据态只是窗口朝气泡那一侧长出去，
        #    形象本身一步都不许挪（否则点一下她会跳）。
        if keep_pos and self.pet_xy:
            px, py = int(self.pet_xy[0]), int(self.pet_xy[1])
        else:
            p = self.cfg.get("pet") or {}
            pos = p.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) == 2:
                px, py = int(pos[0]), int(pos[1])
            else:
                px = self.root.winfo_screenwidth() - pw - 40
                py = self.root.winfo_screenheight() - h - 90
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        px = max(0, min(px, sw - pw))
        py = max(0, min(py, sh - h))
        # 气泡默认在形象右边；形象已经贴到屏幕右缘就翻到左边（否则气泡飞出屏幕外）
        self.bub_left = (px + pw + BGAP + self.bub_w) > sw
        x = px + pw - w if self.bub_left else px
        self.pet_xy = [px, py]
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, py))
        self._reassert_chroma()          # 挪窗/改尺寸也可能把键控色弄丢（拖动贴边时最明显）

    # ---------------- 绘制 ----------------
    def _cur_pet_image(self):
        # 姿态优先级：拖动(被拎起) > 正在显示的表情 > 打盹 > 站立
        if self.dragging and "drag" in self.img:
            base_key = "drag"
        elif self.express and ("exp:" + self.express) in self.img:
            base_key = "exp:" + self.express
        elif self.state == "sleep" and "sleep" in self.img:
            base_key = "sleep"
        else:
            base_key = "idle"
        if base_key not in self.img:
            base_key = "idle"
        tag = base_key
        im = self.img[base_key]
        need_mirror = bool(self.flipped)
        # 站立态优先用构建期就翻好的 pet_flip.png（省一次逐列搬运、缩放也更准）
        if need_mirror and base_key == "idle" and self.img.get("flip") is not None:
            im, tag, need_mirror = self.img["flip"], "flip", False
        sx = sy = 1.0
        # 按住不放：持续左右拉伸（高度不变；底边对齐在绘制那步保证 → 脚不动）
        if self.pressed and self.cfg.get("press_bounce", True) and not self.dragging:
            sx, sy = PRESS_SX, PRESS_SY
        # 点一下：左右拉伸 → 回弹（**高度始终 1.0**，且 sx 只增不减 → 不摇晃）
        if self.sq > 0.0:
            sx *= 1.0 + SQ_MAX * self.sq
        # 翻转动画：宽度压到中线再展开（镜像在中点换）
        if "flip" in self.anim:
            a = self.anim["flip"]
            k = min(1.0, (time.time() - a["t0"]) / max(0.01, a["dur"]))
            f = 1.0 - 0.75 * math.sin(math.pi * k)
            sx *= f
        if need_mirror:
            im = self._mirror(im, tag)
        if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
            im = self._pixel_scale(im, sx, sy, tag + ("*m" if need_mirror else ""))
        return im

    def redraw(self, bob=False):
        cv = self.cv
        try:
            cv.delete("all")
        except Exception:
            return
        w = cv.winfo_width() or 1
        h = cv.winfo_height() or 1
        if w <= 1 or h <= 1:
            self.root.update_idletasks()
            w = cv.winfo_width() or 1
            h = cv.winfo_height() or 1
        # 常驻态：只有形象，零数字（用户要求）
        if self.tmode == "solid":
            cv.create_rectangle(0, 0, w, h, fill="#12161c", outline="")
        try:
            im = self._cur_pet_image()
        except Exception as e:
            _log("[!] 画形象出错：%s" % e)
            im = self.img.get("idle")
        self._ph = im                                  # 保住引用，否则 Tk 会当成垃圾回收掉
        pw, ph = self._pet_box()
        bl = bool(getattr(self, "bub_left", False))
        px = (w - pw // 2) if bl else pw // 2
        # ⚠️ 底对齐要用**这张图自己的高度**（h - ih//2），不能拿姿态盒的 ph 去算：
        #    各姿态裁完之后高度天然不同（实测站立 129x200 / 打盹 130x170），
        #    按盒子对齐的话小一号的打盹图会**悬在半空**（真图才暴露，占位圆看不出）。
        ih = im.height()
        py = h - ih // 2
        if self.tmode == "solid":
            cv.create_rectangle(6, 6, w - 6, h - 6, fill="#1a202a", outline="#242b36")
        cv.create_image(px, py, image=im)
        if self.state == "data":
            self.draw_bubble(max(120, self.bub_w or (w - pw - BGAP)), 8, h)

    def _round_rect(self, cv, x1, y1, x2, y2, r, fill=None, outline=None, width=1):
        """精确圆角：4 段圆弧 + 中间两块矩形。

        以前用 create_polygon(smooth=True) 近似 —— 放大看圆角是糊的、边线发虚，
        正是"简陋"的来源之一。
        """
        # ⚠️ 圆心不是角点！左上角圆心 = (x1+r, y1+r)，以此类推 —— 传成角点会让四段圆弧
        #    全画到卡片外面（圆角实际变成直角，数字检查抓到过）。
        corners = ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                   (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180))
        if fill:
            cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
            cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline="")
            for cx, cy, st in corners:
                cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=st, extent=90,
                              style="pieslice", fill=fill, outline="")
        if outline:
            for cx, cy, st in corners:
                cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=st, extent=90,
                              style="arc", outline=outline, width=width)
            cv.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width)
            cv.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width)
            cv.create_line(x1, y1 + r, x1, y2 - r, fill=outline, width=width)
            cv.create_line(x2, y1 + r, x2, y2 - r, fill=outline, width=width)

    def draw_bubble(self, bw, by, h):
        cv = self.cv
        pw, ph = self._pet_box()
        bl = bool(getattr(self, "bub_left", False))
        rows = self._bubble_rows()
        bh = self._bubble_h()
        x = 0 if bl else pw + BGAP
        # "冒"是个动作：0.18 秒从她身边滑弹出来（不是硬切）
        k = max(0.0, 1.0 - (time.time() - (self.pop_ts or 0)) / 0.18)
        off = int(-16 * k * k)
        x += -off if bl else off
        y0 = by + off
        y1 = y0 + bh
        self._round_rect(cv, x, y0, x + bw, y1, BRAD, fill=BUB_BG, outline=BUB_EDGE)
        # 尾巴：对齐第一行的垂直中心（不是死写 30px），根部压进卡片里再抹一道缝
        ty = y0 + BPAD_Y + self._row_h(rows[0]) // 2
        if bl:                                   # 气泡在左、形象在右 → 尾巴朝右
            cv.create_polygon(x + bw - 5, ty - 8, x + bw + 11, ty, x + bw - 5, ty + 8,
                              fill=BUB_BG, outline="")
            cv.create_line(x + bw + 1, ty - 8, x + bw - 2, ty + 8, fill=BUB_BG, width=6)
        else:
            cv.create_polygon(x + 5, ty - 8, x - 11, ty, x + 5, ty + 8,
                              fill=BUB_BG, outline="")
            cv.create_line(x - 1, ty - 8, x + 2, ty + 8, fill=BUB_BG, width=6)
        # 顶部内侧极淡高光（深色卡片贴深色壁纸时不至于像"一块板"）
        cv.create_line(x + BRAD, y0 + 1, x + bw - BRAD, y0 + 1, fill=BUB_TOP, width=1)
        ty = y0 + BPAD_Y
        for mods in rows:
            rh = self._row_h(mods)
            cy = ty + rh // 2                     # 行内垂直居中
            rx = x + BPAD_X
            inner = bw - BPAD_X * 2
            total = self._row_width(mods)
            for i, m in enumerate(mods):
                if i:
                    rx += 12
                if m["t"] == "value":
                    if m.get("level"):
                        # 一条竖着的强调色（替掉旧圆点：更克制，还能带出余额状态）
                        cv.create_rectangle(rx, cy - 9, rx + 3, cy + 9,
                                            fill=level_color(m.get("pct")), outline="")
                        rx += 3 + 9
                    if m["label"]:
                        cv.create_text(rx, cy, text=m["label"], anchor="w",
                                       font=self.f_lab, fill=C_DIM)
                        rx += self.f_lab.measure(m["label"] + " ")
                    txt = m.get("text") or ""
                    # 数字滚动：0.5 秒内从旧值滚到新值
                    rl = self.roll.get("balance")
                    if rl and m.get("k") == "balance":
                        kk = min(1.0, (time.time() - rl["t0"]) / rl["dur"])
                        cur = rl["from"] + (rl["to"] - rl["from"]) * (1 - (1 - kk) ** 3)
                        u = (self.d.get("unit") or "¥")
                        txt = (u + "%.2f" % cur) if kk < 1.0 else (u + "%.2f" % rl["to"])
                    cv.create_text(rx, cy, text=txt, anchor="w", font=self.f_val, fill=C_VAL)
                    if m.get("pct") is not None:
                        by0 = ty + rh - 9
                        cv.create_rectangle(x + BPAD_X, by0, x + BPAD_X + inner, by0 + 3,
                                            fill=TRACK, outline="")
                        cv.create_rectangle(x + BPAD_X, by0,
                                            x + BPAD_X + max(3, int(inner * float(m["pct"]) / 100.0)),
                                            by0 + 3, fill=level_color(m.get("pct")), outline="")
                elif m["t"] == "img":
                    try:
                        photo = tk.PhotoImage(file=m["path"])
                    except Exception:
                        continue
                    self._bh = photo
                    cv.create_image(rx, cy, image=photo, anchor="w")
                else:
                    # ⭐ 长句**折行**，不再截成"…"（用户："点击第三次的对话没有显示全"）
                    f = self.f_dim if m.get("dim") else self.f_txt
                    txt = m["text"] or ""
                    if f.measure(txt) > inner and inner > 0:
                        cv.create_text(rx, ty + 4, text=txt, anchor="nw", width=inner, font=f,
                                       fill=C_DIM2 if m.get("dim") else C_TXT)
                    else:
                        cv.create_text(rx, cy, text=txt, anchor="w", font=f,
                                       fill=C_DIM2 if m.get("dim") else C_TXT)
            ty += rh

    # ---------------- 主循环 ----------------
    def animate(self):
        now = time.time()
        # 点击的「左右拉伸」：**单调曲线** —— 先快速拉出去、保持一瞬、再平滑弹回。
        # ⚠️ 一开始用的是 sin 振荡（有正有负），用户的原话是"点了像在**摇晃**角色" ——
        #    他要的是"把角色左右拉伸一下"。所以改成不回头、不振荡：全程 sx ≥ 1.0。
        _dt = max(0.0, now - getattr(self, "_tick_t", now))
        self._tick_t = now
        if self.express and now >= self.express_until:     # 表情到期 → 回到常态
            self.express = None
            self.redraw()
        if self.sq > 0.0:
            tgt = 1.0 if (now - self._click_t) < SQ_HOLD else 0.0
            rate = SQ_RATE_OUT if tgt > self.sq else SQ_RATE_BACK
            self.sq += (tgt - self.sq) * min(1.0, _dt * rate)
            if self.sq < 0.004:
                self.sq = 0.0
        # 动画收尾
        for name in ("press", "flip", "squash"):
            a = self.anim.get(name)
            if a and now - a["t0"] > a["dur"]:
                self.anim.pop(name, None)
                self.redraw()
        a = self.anim.get("slide")
        if a:
            k = min(1.0, (now - a["t0"]) / max(0.01, a["dur"]))
            e = 1 - (1 - k) ** 2
            fx, fy = a["from"]
            tx, ty = a["to"]
            self.pet_xy = [fx + (tx - fx) * e, fy + (ty - fy) * e]
            self.apply_geometry(keep_pos=True)
            if k >= 1.0:
                self.pet_xy = [tx, ty]
                self.apply_geometry(keep_pos=True)
                self.anim.pop("slide", None)
        # 数字滚动 / 气泡冒出来 / 按压缩放 —— 这些都要逐帧重画
        busy_draw = False
        rl = self.roll.get("balance")
        if rl:
            if now - rl["t0"] > rl["dur"]:
                self.roll.pop("balance", None)
                busy_draw = True
            else:
                busy_draw = True
        if self.pop_ts and now - self.pop_ts < 0.2:
            busy_draw = True
        if self.sq > 0.0:
            busy_draw = True
        if "press" in self.anim or "flip" in self.anim or "squash" in self.anim:
            busy_draw = True
        self._pick_net()
        if busy_draw:
            self.redraw()

        # 久不理她 → 缩成一团 + 整窗淡下去（set_state 里一并处理透明度）
        p = self.cfg.get("pet") or {}
        idle_limit = float(p.get("idle_sleep_seconds") or 180)
        if self.state != "sleep" and now - self.last_touch > idle_limit:
            self.set_state("sleep")
        elif self.state == "sleep" and now - self.last_touch <= idle_limit:
            self.set_state("pet")

        # 数据态闲置一会儿 → 自动收回（不打扰）
        dt = float((self.cfg.get("click") or {}).get("data_timeout_seconds") or 5)
        if self.state == "data" and now - self.data_ts > dt:
            self.hide_bubble()

        # 定时刷新
        iv_h = float(((self.cfg.get("hub") or {}).get("refresh_seconds")) or 60)
        iv_b = float(((self.cfg.get("balance") or {}).get("refresh_seconds")) or 60)
        if now - getattr(self, "_last_refresh", 0) > min(iv_h, iv_b):
            self._last_refresh = now
            self.refresh()
        self.root.after(TICK_MS, self.animate)

    def run(self):
        self.pc = PCCollect(self.cfg, self.cfg_path)   # 采集挂在挂件进程里，不再单独一个 exe
        self.pc.start()
        self.root.after(TICK_MS, self.animate)
        self.root.mainloop()

    # ---------------- 右键菜单 ----------------
    # ---------------- 右键菜单（自绘）----------------
    # 原生 tk.Menu 在 Windows 上就是系统外观，没法做"简约高级"。这里自绘一个：
    # 深色卡片 + 1px 发丝边 + 12px 圆角（Windows 上用透明键控色切出圆角）+
    # 悬停一行淡高亮 + "开"状态用左侧 3px 强调条（跟气泡同一套语言）。
    MENU_W = 236
    MENU_IH = 30
    MENU_PAD = 14

    def menu(self, e):
        rows = self._menu_rows()
        w, ih = self.MENU_W, self.MENU_IH
        h = 12 + sum(9 if r.get("sep") else (26 if r.get("title") else ih) for r in rows) + 12
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(int(e.x_root), sw - w - 6))
        y = max(0, min(int(e.y_root), sh - h - 6))
        self._close_menu()
        t = tk.Toplevel(self.root)
        self._menu_win = t
        try:
            t.overrideredirect(True)
            t.attributes("-topmost", True)
        except Exception:
            pass
        chroma = (self.cfg.get("pet") or {}).get("chroma") or CHROMA_DEFAULT
        cv = tk.Canvas(t, width=w, height=h, bg=chroma, highlightthickness=0, bd=0)
        cv.pack()
        if sys.platform.startswith("win"):
            try:
                t.wm_attributes("-transparentcolor", chroma)   # 靠键控色把四角切圆
            except Exception:
                pass
        self._round_rect(cv, 0, 0, w - 1, h - 1, 12, fill=BUB_BG, outline=BUB_EDGE)
        y0 = 12
        for r in rows:
            if r.get("sep"):
                cv.create_line(self.MENU_PAD, y0 + 4, w - self.MENU_PAD, y0 + 4, fill=BUB_EDGE)
                y0 += 9
                continue
            if r.get("title"):
                cv.create_text(self.MENU_PAD + 10, y0 + 13, text=r["title"], anchor="w",
                               font=self.f_dim, fill=C_DIM2)
                y0 += 26
                continue
            # ★ 一整行共用一个 tag 来绑事件。
            #   ⚠️ 只绑在矩形上不够：Tk 里文字画在矩形**上面**，点在文字上命中的是
            #      text item（没绑事件）→ 每行只有右侧空白能点（用户报"只能在右边点，
            #      左边点不到"）。所以 rect / 绿条 / 文字 全挂同一个 tag，事件绑 tag。
            tag = "rowy%d" % y0          # 用行首 y 当 tag，天然唯一、不依赖元素计数
            # 高亮块左右边距调匀（原来是 6..w-6，左边距 18px、右边 6px，看着偏）
            box = cv.create_rectangle(10, y0, w - 10, y0 + ih, fill=BUB_BG, outline="", tags=tag)
            cmd = r.get("cmd")
            if r.get("on"):
                cv.create_rectangle(self.MENU_PAD, y0 + 8, self.MENU_PAD + 3, y0 + ih - 8,
                                    fill=ACC["ok"], outline="", tags=tag)
            cv.create_text(self.MENU_PAD + 10, y0 + ih // 2, text=r["label"], anchor="w",
                           font=self.f_txt, fill=(C_TXT if cmd else C_DIM2), tags=tag)
            if r.get("value"):
                cv.create_text(w - self.MENU_PAD, y0 + ih // 2, text=r["value"], anchor="e",
                               font=self.f_dim, fill=C_DIM, tags=tag)
            if cmd:
                cv.tag_bind(tag, "<Enter>", lambda ev, b=box: cv.itemconfig(b, fill="#1a2029"))
                cv.tag_bind(tag, "<Leave>", lambda ev, b=box: cv.itemconfig(b, fill=BUB_BG))
                cv.tag_bind(tag, "<Button-1>", lambda ev, c=cmd: self._menu_fire(c))
            y0 += ih
        t.geometry("%dx%d+%d+%d" % (w, h, x, y))
        try:
            t.grab_set()
        except Exception:
            pass
        t.bind("<Escape>", lambda ev: self._close_menu())

        def maybe_close(ev):
            if not (x <= ev.x_root <= x + w and y <= ev.y_root <= y + h):
                self._close_menu()
        t.bind("<Button-1>", maybe_close, add="+")
        try:
            t.focus_set()
        except Exception:
            pass

    def send_feedback(self, verdict):
        """把 ✓/✗ 发给中枢。这是她**唯一能学到的真反馈**：节奏自适应靠它。

        注意：前端只负责"送出去"，学不学、怎么学在中枢/说话层（Thompson 采样）。
        送不出去就只记日志 —— 绝不能因为反馈通道坏了影响挂件本身。
        """
        try:
            h = self.cfg.get("hub") or {}
            base = (h.get("base") or "").rstrip("/")
            if not base:
                _log("[!] 没配中枢地址，这条反馈没发出去")
                return
            d = http_json(base + "/feedback", {"X-Token": h.get("token") or ""}, timeout=8,
                          method="POST", body={"verdict": verdict},
                          ctx=_ssl_ctx(self.cfg) if base.startswith("https") else None)
            _log("[fb] %s → %s" % (verdict, "ok" if (d or {}).get("ok") else "未确认"))
        except Exception as ex:
            _log("[!] 反馈发送失败：%s" % short_err(ex))

    def _menu_fire(self, cmd):
        self._close_menu()
        try:
            cmd()
        except Exception as ex:
            _log("[!] 菜单动作出错：%s" % short_err(ex))

    def _close_menu(self):
        w = getattr(self, "_menu_win", None)
        self._menu_win = None
        if w is not None:
            try:
                w.grab_release()
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass

    def _menu_rows(self):
        """菜单内容（语义与旧版一模一样，只换了排版）。"""
        p = self.cfg.get("pet") or {}
        snap = self.cfg.get("snap") or {}
        snd = self.cfg.get("sound") or {}
        size = int(p.get("size") or 200)
        snap_on = bool(snap.get("enabled", True))
        snap_px = int(snap.get("px") or 22) if snap_on else 0
        snd_on = bool(snd.get("enabled", True))

        def nxt(seq, cur):
            i = seq.index(cur) if cur in seq else 0
            return seq[(i + 1) % len(seq)]

        return [
            {"title": "鲸鲸 · 桌面"},
            {"sep": True},
            {"label": "形象大小", "value": "%d px" % size,
             "cmd": lambda: self.set_size(nxt((120, 160, 200, 260, 320), size))},
            {"label": "贴边吸附", "value": ("关" if not snap_on else "%d px" % snap_px),
             "cmd": lambda: self.set_snap(nxt((0, 12, 22, 40), snap_px))},
            {"label": "贴左时镜像翻转", "on": bool(self.cfg.get("flip_on_left", True)),
             "cmd": self.toggle_flip},
            {"label": "点按推进气泡", "on": bool((self.cfg.get("click") or {}).get("push_queue", True)),
             "cmd": self.toggle_push},
            {"label": "点一下换表情", "on": bool((self.cfg.get("click") or {}).get("expression", True)),
             "cmd": self.toggle_expression},
            {"label": "音效", "value": ("啵" if snd_on else "关"),
             "cmd": lambda: self.set_sound("off" if snd_on else "pop")},
            {"sep": True},
            # ★ 反馈入口：她**唯一能学到的真反馈**（节奏靠它自适应，而不是猜"你动了没动手机"）
            {"label": "刚刚那条：说得对", "cmd": lambda: self.send_feedback("good")},
            {"label": "刚刚那条：别说", "cmd": lambda: self.send_feedback("bad")},
            {"label": "电脑采集", "on": self._collect_on(), "cmd": self.toggle_collect},
            {"label": "上报状态…", "cmd": self.open_collect_status},
            {"label": "隐私预览…", "cmd": self.open_privacy_preview},
            {"sep": True},
            # 「刷新一次」去掉了：数据本来每 60 秒自动刷一次，菜单里少一行更清爽
            {"label": "数据与设置…", "cmd": self.open_settings},
            {"label": "打开配置文件", "cmd": self.open_cfg},
            {"label": "开机自启", "on": self._autostart_on(), "cmd": self.toggle_autostart},
            {"label": "桌面透明", "on": bool(p.get("transparent", True)),
             "cmd": self.toggle_transparent},
            {"sep": True},
            {"label": "退出", "cmd": self.quit},
        ]

    def set_size(self, s):
        self.cfg.setdefault("pet", {})["size"] = int(s)
        self.refresh_fonts()                   # 气泡字号跟着形象尺寸缩放
        self._load_images()                    # 改尺寸必须重新载图（窗口几何依赖真实尺寸）
        self.apply_geometry()
        self.redraw()
        save_cfg(self.cfg, self.cfg_path)

    def set_snap(self, px):
        snap = self.cfg.setdefault("snap", {})
        snap["enabled"] = px > 0
        if px:
            snap["px"] = int(px)
        save_cfg(self.cfg, self.cfg_path)

    def toggle_expression(self):
        c = self.cfg.setdefault("click", {})
        c["expression"] = not bool(c.get("expression", True))
        self.save_cfg()
        _log("[·] 点一下换表情：%s" % ("开" if c["expression"] else "关"))

    def toggle_flip(self):
        self.cfg["flip_on_left"] = not bool(self.cfg.get("flip_on_left", True))
        if not self.cfg["flip_on_left"]:
            self.flipped = False
        self.redraw()
        save_cfg(self.cfg, self.cfg_path)

    def toggle_push(self):
        c = self.cfg.setdefault("click", {})
        c["push_queue"] = not bool(c.get("push_queue", True))
        save_cfg(self.cfg, self.cfg_path)

    def set_sound(self, kind):
        """关 / duck / pop —— 选完立刻试听一声。"""
        s = self.cfg.setdefault("sound", {})
        if kind == "off":
            s["enabled"] = False
        else:
            s["enabled"] = True
            s["kind"] = kind
        save_cfg(self.cfg, self.cfg_path)
        self.play_sound("click")

    def _autostart_on(self):
        if not sys.platform.startswith("win"):
            return False
        return os.path.exists(os.path.join(
            os.environ.get("APPDATA", ""), "Microsoft", "Windows",
            "Start Menu", "Programs", "Startup", "WhaleDesk.vbs"))

    def toggle_autostart(self):
        on = not self._autostart_on()
        if set_autostart(on):
            _log("[·] 开机自启 = %s" % on)

    def toggle_transparent(self):
        p = self.cfg.setdefault("pet", {})
        p["transparent"] = not bool(p.get("transparent", True))
        save_cfg(self.cfg, self.cfg_path)
        _log("[·] 桌面透明 = %s（改这项要重启挂件才生效）" % p["transparent"])

    def quit(self):
        try:
            self._close_menu()          # 菜单还开着就直接退出会留一个孤儿窗口
        except Exception:
            pass
        try:
            self.cfg.setdefault("pet", {})["pos"] = [int(self.pet_xy[0]), int(self.pet_xy[1])]
            save_cfg(self.cfg, self.cfg_path)
        except Exception:
            pass
        self.root.destroy()

    # ---------------- 设置面板 ----------------
    # ---------------- 电脑采集（并进进程，菜单里就能看状态/核对隐私）----------------
    def _collect_on(self):
        return bool((self.cfg.get("collect") or {}).get("enabled", True))

    def toggle_collect(self):
        col = self.cfg.setdefault("collect", {})
        was = bool(col.get("enabled", True))
        col["enabled"] = not was
        save_cfg(self.cfg, self.cfg_path)
        pc = getattr(self, "pc", None)
        if was:
            if pc:
                pc.stop_now()
            _log("[·] 电脑采集已关（挂件继续跑）")
        else:
            self.pc = PCCollect(self.cfg, self.cfg_path)
            self.pc.start()
            _log("[·] 电脑采集已开")
        self._info_window("电脑采集", [
            "采集已%s。" % ("关掉" if was else "打开"),
            "",
            "上报的只有聚合量（类别 + 分钟数），不含标题、网址、文档名与按键内容。",
            "想随时核对，点「隐私预览」。",
        ])

    def open_collect_status(self):
        pc = getattr(self, "pc", None)
        self._info_window("电脑采集 · 上报状态",
                          pc.status_lines() if pc else ["采集没起来"])

    def open_privacy_preview(self):
        pc = getattr(self, "pc", None)
        self._info_window("隐私预览 · 会传什么给鲸鲸",
                          pc.preview_lines() if pc else ["采集没起来"])

    def _info_window(self, title, lines):
        try:
            w = tk.Toplevel(self.root)
            w.title(title)
            w.configure(bg="#14161c")
            w.attributes("-topmost", True)
            txt = tk.Text(w, bg="#14161c", fg="#e6e8ee", insertbackground="#e6e8ee",
                          relief="flat", wrap="word", width=74,
                          height=max(8, min(26, len(lines) + 2)))
            txt.pack(padx=14, pady=12, fill="both", expand=True)
            txt.insert("1.0", "\n".join(lines))
            txt.configure(state="disabled")
            tk.Button(w, text="关闭", command=w.destroy, bg="#2a2d36", fg="#e6e8ee",
                      relief="flat", padx=16, pady=5, cursor="hand2").pack(pady=(0, 12))
        except Exception as e:
            _log("[!] 打不开窗口：%s" % short_err(e))

    def open_settings(self):
        t = tk.Toplevel(self.root)
        t.title("鲸鲸 · 数据与设置")
        t.configure(bg="#12161c")
        t.attributes("-topmost", True)
        t.geometry("460x430")
        F = tkfont.Font(family=self.font.cget("family"), size=10)
        rows = [("DeepSeek API Key（查余额用）", "balance", "api_key", False),
                ("余额预警阈值（低于它就变红）", "balance", "low_threshold", False),
                ("中枢地址", "hub", "base", False),
                ("中枢 Token", "hub", "token", True),
                ("中枢备用地址（明文，仅当上面连不上）", "hub", "fallback_base", False)]
        entries = {}
        tk.Label(t, text="设置改完点「保存」；Key 只存在你这台电脑上。", bg="#12161c",
                 fg="#98a3b3", font=F).place(x=16, y=12)
        y = 44
        for lab, sec, key, secret in rows:
            tk.Label(t, text=lab, bg="#12161c", fg="#e6ebf2", font=F).place(x=16, y=y)
            e = tk.Entry(t, width=46, show="•" if secret else "", bg="#1a202a", fg="#e6ebf2",
                         insertbackground="#e6ebf2", relief="flat", font=F)
            e.insert(0, str((self.cfg.get(sec) or {}).get(key, "")))
            e.place(x=16, y=y + 22)
            entries[(sec, key)] = e
            y += 58
        info = tk.Label(t, text="", bg="#12161c", fg="#98a3b3", font=F, justify="left", anchor="w")
        info.place(x=16, y=y + 4)

        def do_test():
            for (sec, key), e in entries.items():
                self.cfg.setdefault(sec, {})[key] = e.get().strip()
            info.config(text="测试中…")
            t.update_idletasks()

            def work():
                hub = fetch_hub(self.cfg)
                bal = fetch_balance(self.cfg)
                msg = "中枢：%s" % ("通 ✓" if hub.get("ok") else "不通 ✗ " + str(hub.get("error")))
                msg += "\n余额：%s" % (("¥%.2f" % bal["value"]) if bal.get("ok") else "取不到 —— " + str(bal.get("error")))
                t.after(0, lambda: info.config(text=msg))
            threading.Thread(target=work, daemon=True).start()

        def do_save():
            for (sec, key), e in entries.items():
                v = e.get().strip()
                if key == "low_threshold":
                    try:
                        v = float(v)
                    except Exception:
                        v = 10.0
                self.cfg.setdefault(sec, {})[key] = v
            save_cfg(self.cfg, self.cfg_path)
            self._last_refresh = 0
            self.refresh()
            info.config(text="已保存 ✓（形象大小/吸附在右键菜单里改）")

        for i, (lab, fn) in enumerate((("测试连通", do_test), ("保存", do_save), ("关闭", t.destroy))):
            tk.Button(t, text=lab, command=fn, bg="#222a35", fg="#e6ebf2", activebackground="#2c3543",
                      relief="flat", font=F, width=10).place(x=16 + i * 116, y=390)

    # ---------------- 对话 ----------------
    def open_cfg(self):
        p = self.cfg_path
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["notepad.exe", p])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-t", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            _log("[·] 打开配置失败 %s：%s" % (p, e))


# ---------------------------------------------------------------- CLI
def cli_text(cfg):
    hub = fetch_hub(cfg)
    bal = fetch_balance(cfg)
    d = data_dict(cfg, hub, bal)
    out = []
    out.append("%s %s  ·  %s %s" % (APP, VERSION, d["date"], d["weekday"]))
    bal_txt = (d["unit"] + d["balance_txt"]) if d.get("balance_ok") else d["balance_txt"]
    out.append("  余额        %s%s" % (bal_txt,
               "" if (bal or {}).get("ok") else "   （%s）" % (bal or {}).get("error", "未取到")))
    out.append("  下一节课    %s %s（%s）· %s" % (d["next_class_time"], d["next_class"],
                                                 d["next_class_room"] or "—", d["next_class_in_txt"]))
    out.append("  今天        %s 节课 · 屏幕 %s · 睡眠 %s" % (d["classes_today"], d["screen_txt"], d["sleep_txt"]))
    out.append("  中枢        %s" % ("通" if d["hub_state"] == "ok" else "不通 —— " + str(d["hub_err"])))
    out.append("  今日提醒    1) %s" % d["reminders_1"])
    if d["reminders_2"]:
        out.append("              2) %s" % d["reminders_2"])
    return "\n".join(out), d, hub, bal


def set_autostart(on):
    if not sys.platform.startswith("win"):
        _log("[·] 开机自启目前只在 Windows 上实现")
        return False
    startup = os.path.join(os.environ.get("APPDATA", ""),
                           "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    vbs = os.path.join(startup, "WhaleDesk.vbs")
    try:
        if on:
            if FROZEN:
                target = '"%s"' % sys.executable
            else:
                target = '"pythonw" "%s"' % os.path.abspath(__file__)
            # ⚠️ .vbs / .bat 必须纯 ASCII（Windows Script Host 按 ANSI 读，中文会乱码）
            body = ('Set sh = CreateObject("WScript.Shell")\r\n'
                    'sh.CurrentDirectory = "%s"\r\n'
                    'sh.Run %s, 0, False\r\n' % (BASE, target))
            with open(vbs, "w", encoding="ascii", errors="replace") as f:
                f.write(body)
            _log("[·] 已设开机自启：%s" % vbs)
        else:
            if os.path.exists(vbs):
                os.remove(vbs)
            _log("[·] 已取消开机自启")
        return True
    except Exception as e:
        _log("[!] 开机自启设置失败：%s" % e)
        return False


def main():
    ap = argparse.ArgumentParser(description="%s %s" % (APP, VERSION))
    ap.add_argument("--cli", action="store_true", help="命令行看一次数据")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--init", action="store_true", help="生成配置文件")
    ap.add_argument("--autostart", choices=["on", "off"], help="设置开机自启")
    ap.add_argument("--config", help="指定配置文件路径")
    ap.add_argument("--fontcheck", action="store_true", help="检查随包字体是否生效")
    ap.add_argument("--bench", action="store_true", help="量一下换尺寸各步耗时（排卡顿）")
    args = ap.parse_args()

    cfg_path = args.config or DEFAULT_CFG
    cfg = load_cfg(cfg_path)

    # 首次运行主动落一份配置 —— 否则"说明里写着会生成配置"就是空话，用户满地找不着文件
    if args.init or not os.path.exists(cfg_path):
        if save_cfg(cfg, cfg_path):
            _log("[·] 配置已生成：%s" % cfg_path)

    if args.bench:
        w0 = Widget(cfg, cfg_path)
        _log("[bench] 换尺寸各步耗时（ms）")
        for sz in (120, 160, 200, 260, 320, 200, 120):
            t = {}
            t0 = time.time(); w0.cfg.setdefault("pet", {})["size"] = sz; t["cfg"] = (time.time() - t0) * 1000
            t0 = time.time(); w0.refresh_fonts(); t["fonts"] = (time.time() - t0) * 1000
            t0 = time.time(); w0._load_images(); t["imgs"] = (time.time() - t0) * 1000
            t0 = time.time(); w0.apply_geometry(); t["geom"] = (time.time() - t0) * 1000
            t0 = time.time(); w0.redraw(); t["draw"] = (time.time() - t0) * 1000
            t0 = time.time(); w0.settle = None; t["total"] = (time.time() - t0) * 1000
            _log("  %3dpx  字体 %6.1f  图片 %7.1f  几何 %5.1f  绘制 %6.1f  合计 %7.1f"
                 % (sz, t["fonts"], t["imgs"], t["geom"], t["draw"],
                    t["fonts"] + t["imgs"] + t["geom"] + t["draw"]))
        w0.root.destroy()
        return

    if args.fontcheck:
        n, tried = register_bundled_fonts()
        _log("[字体] 随包字体注册：成功 %d 个，尝试过 %d 个路径" % (n, len(tried)))
        for p in tried:
            _log("        %s  (%s)" % (p, "存在" if os.path.exists(p) else "缺失"))
        try:
            w0 = tk.Tk()
            w0.withdraw()
            fams = set(tkfont.families(w0))
            hit = FONT_FAMILY in fams
            f = tkfont.Font(family=FONT_FAMILY, size=12)
            actual = f.actual().get("family")
            _log("[字体] Tk 能看到 %d 个字体族；%s → %s"
                 % (len(fams), FONT_FAMILY, "✓ 找到" if hit else "✗ 没找到（会回落系统字体）"))
            _log("[字体] 实际生效族名：%s；量一个汉字宽度：%d px（0 = 画不出来）"
                 % (actual, f.measure("鲸")))
            # ⭐ 折行验证必须用**真字体**量：本机 Linux 的 measure 恒为 0，验不了。
            #    用户报过"点击第三次的对话没有显示全" → 长句能不能折行显示完整，就在这几行。
            _samples = ["（翻了翻今天的数据）主人今天还顺利吗。",
                        "（提醒你）30 分钟后上课：电气控制与PLC，准备出发。",
                        "今天 3 节课 · 屏幕 11 小时 10 分 · 下一节 08:00 电气控制与PLC（6-302）"]
            _log("[字体] 气泡折行验证（折行宽度 %d px）：" % BUB_WRAP_W)
            for _t in _samples:
                _w = int(f.measure(_t))
                _n = wrap_lines(_t, f, BUB_WRAP_W)
                _clip = int(f.measure(_t)) > BUB_WRAP_W and _n > MAX_WRAP_LINES
                _log("        %d px → 折 %d 行%s ｜ %s"
                     % (_w, _n, "（超上限，会截…）" if _clip else "（显示完整）", _t[:20]))
            w0.destroy()
        except Exception as e:
            _log("[字体] 检查出错：%s" % e)
        return

    if args.autostart:
        set_autostart(args.autostart == "on")
        return

    if args.cli or args.json:
        txt, d, hub, bal = cli_text(cfg)
        if args.json:
            payload = dict(d)
            payload["hub_ok"] = bool((hub or {}).get("ok"))
            payload["balance_ok"] = bool((bal or {}).get("ok"))
            _log(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _log(txt)
        return

    # 打开时给个正常 UA 的 Windows 进程内 DPI 感知，免得高分屏发虚
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    w = Widget(cfg, cfg_path)
    _log("[·] %s 起来了（形象大小 %s px，透明模式 %s）" % (APP, (cfg.get("pet") or {}).get("size"), w.tmode))
    w.run()


if __name__ == "__main__":
    main()

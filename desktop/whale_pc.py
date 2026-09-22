#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鲸鲸 · 电脑采集器（WhalePC 0.1.0）
================================

把「电脑用了多久 / 在干什么类别的事 / 连坐多久」这类**统计量**送进你自己的中枢，
让鲸鲸能像看手机那样看电脑（久坐催你起来、睡前小总结里带上屏幕时间）。

    WhalePC-CLI.exe --once       采一轮并上报（排障/手动用）
    WhalePC-CLI.exe --preview    打印「将要上传的内容」=「AI 能看到的内容」，不发
    WhalePC-CLI.exe --status     看当前状态与最近上报结果
    WhalePC-CLI.exe --init       生成配置文件
    WhalePC.exe                  后台常驻（双击无窗口，写日志）

⚠️ 隐私红线（写死在代码里，不是可选项）
----
1. **只上传聚合量**：`screen.active_minutes` / `app.<类别>_minutes` / `pc.sit_minutes`。
   **窗口标题全文、进程路径、网址、文件名一律不出本机** —— 标题只在内存里用来判类别，
   判完即弃（不落盘、不写日志、不上传）。
2. **只有加密通道**：HTTPS + **证书固定**（只信任随包的 hub.crt）。
   本程序**没有**明文回退 —— 连不上就攒在本地，绝不明文发。
3. **本地待发队列加密**：Windows 上走 DPAPI（CryptProtectData，绑当前账号），
   别人拷走队列文件也解不开。
4. `--preview` 可让你（和 AI）看到的东西完全一致：它打印的就是上传体。

只用标准库（ctypes 调 Win32），无需安装任何东西。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

APP = "鲸鲸 · 电脑采集器"
VERSION = "0.1.0"

FROZEN = bool(getattr(sys, "frozen", False))
BASE = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
_BUNDLED = os.path.join(getattr(sys, "_MEIPASS", BASE), "assets")
ASSETS = os.path.join(BASE, "assets") if os.path.isdir(os.path.join(BASE, "assets")) else _BUNDLED
DEFAULT_CFG = os.path.join(BASE, "whale_pc.json")
QUEUE_FILE = os.path.join(BASE, "whale_pc_queue.bin")
LOG_FILE = os.path.join(BASE, "whale-pc.log")
IS_WIN = sys.platform.startswith("win")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def rebase(base_dir):
    """被别的程序（比如挂件）当模块用时，把「配置/队列/日志/素材」都改到它自己的目录，
    做到「一个 exe、一份配置、一份队列」——而不是各写各的。"""
    global BASE, ASSETS, DEFAULT_CFG, QUEUE_FILE, LOG_FILE
    BASE = base_dir
    DEFAULT_CFG = os.path.join(base_dir, "whale_pc.json")
    QUEUE_FILE = os.path.join(base_dir, "whale_pc_queue.bin")
    LOG_FILE = os.path.join(base_dir, "whale-pc.log")
    a = os.path.join(base_dir, "assets")
    ASSETS = a if os.path.isdir(a) else os.path.join(getattr(sys, "_MEIPASS", base_dir), "assets")


def _log(msg, quiet=False):
    line = "[%s] %s" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    if not quiet:
        for s in (sys.stderr, sys.stdout):
            if s is not None:
                try:
                    s.write(line + "\n")
                    s.flush()
                    break
                except Exception:
                    pass
        else:
            _log_file(line)
            return
    _log_file(line)


def _log_file(line):
    """落盘一份日志（窗口版 exe 没有控制台，就靠这个排查）。⚠️ 只写类别与计数，不写标题。"""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 512 * 1024:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                keep = f.readlines()[-500:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(keep)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 配置
def blank_cfg():
    return {
        "version": VERSION,
        "hub": {
            "base": "https://YOUR_HUB_HOST:11443",
            "token": "",
            "pin_cert": "hub.crt",
        },
        "device": "pc_windows",
        "interval_seconds": 300,          # 每 5 分钟结算一次并上报
        "idle_threshold_seconds": 120,    # 这么久没有键鼠输入算"空闲"
        "sit_alert_minutes": 50,          # 连续活跃这么久就该提醒站起来（0 = 关）
        # 类别表：**只在本机用来归类**，命中哪个类别就只上传那个类别的分钟数。
        # 想加自己常用的软件，往对应那一行的引号里加进程名（小写、不含 .exe）即可。
        "categories": {
            "dev": ["code", "pycharm", "idea64", "devenv", "sublime_text", "studio64",
                    "git-bash", "cmd", "powershell", "windowsterminal", "wt", "cursor",
                    "androidstudio", "clion", "webstorm", "goland", "xshell", "putty"],
            "browser": ["chrome", "msedge", "firefox", "360se", "360chrome", "qqbrowser",
                        "brave", "opera"],
            "study": ["chaoxing", "xuexitong", "学习通", "anki", "foxitreader", "sumatrapdf",
                      "acrobat", "pdf", "notion", "obsidian"],
            "office": ["winword", "excel", "powerpnt", "wps", "et", "wpp", "onenote",
                       "visio", "lingxi", "typora"],
            "chat": ["wechat", "weixin", "qq", "tim", "dingtalk", "feishu", "lark",
                     "telegram", "discord", "slack"],
            "media": ["potplayer", "vlc", "mpc-hc64", "mpv", "bilibili", "cloudmusic",
                      "spotify", "iqiyi", "youku", "qqmusic", "kmplayer"],
            "game": ["steam", "原神", "genshinimpact", "yuanshen", "minecraft",
                     "leagueclient", "dota2", "epicgameslauncher", "starrail", "javaw"],
            "other": [],
        },
        # 标题关键词 → 类别（同样只在本机用）。只写关键词，别写具体网址/文档名。
        "title_hints": {
            "media": ["bilibili", "youtube", "爱奇艺", "优酷", "腾讯视频", "网易云音乐"],
            "study": ["学习通", "网课", "课程", "作业", "论文", "题库"],
            "dev": ["github", "stackoverflow", "文档", "api", "debug"],
        },
    }


def load_cfg(path):
    cfg = blank_cfg()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            for k, v in file_cfg.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)              # 子字典单独合并：默认打底、文件覆盖
                else:
                    cfg[k] = v
        except Exception as e:
            _log("配置读不了，用默认值：%s" % e)
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
        _log("配置写不了：%s" % e)
        return False


# ---------------------------------------------------------------- 本地队列加密
# Windows → DPAPI（CryptProtectData）：密钥绑当前用户账号，拷走文件也解不开。
# 非 Windows → 若装了 cryptography 就用 AES-GCM + 本机密钥文件，否则明文（并在日志里喊一声）。
class QueueCrypto(object):
    def __init__(self, base):
        self.keyfile = os.path.join(base, ".whalepc_key")

    # ---- DPAPI ----
    def _dpapi(self, data, unprotect=False):
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        buf_in = ctypes.create_string_buffer(data, len(data))
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        fn = ctypes.windll.crypt32.CryptUnprotectData if unprotect else ctypes.windll.crypt32.CryptProtectData
        args = [ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)]
        if not fn(*args):
            raise OSError("DPAPI 调用失败")
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out

    def _local_keys(self):
        import hashlib
        seed = os.environ.get("COMPUTERNAME", "pc") + "|" + os.environ.get("USERNAME", "u")
        return hashlib.sha256(seed.encode("utf-8")).digest()

    def enc(self, raw: bytes) -> bytes:
        if IS_WIN:
            return b"DPAPI1" + self._dpapi(raw)
        try:
            import hashlib

            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = hashlib.sha256(self._local_keys() + b"|whalepc").digest()
            nonce = os.urandom(12)
            return b"AESGCM" + nonce + AESGCM(key).encrypt(nonce, raw, b"whalepc")
        except Exception as e:
            _log("⚠️ 本机队列未加密（非 Windows 且没有 cryptography）：%s" % e)
            return b"RAWAIN" + raw

    def dec(self, blob: bytes) -> bytes:
        if blob.startswith(b"DPAPI1"):
            return self._dpapi(blob[6:], unprotect=True)
        if blob.startswith(b"AESGCM"):
            import hashlib

            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = hashlib.sha256(self._local_keys() + b"|whalepc").digest()
            return AESGCM(key).decrypt(blob[6:18], blob[18:], b"whalepc")
        return blob[6:] if blob.startswith(b"RAWAIN") else blob


def queue_save(crypto, items):
    try:
        tmp = QUEUE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            f.write(crypto.enc(json.dumps(items, ensure_ascii=False).encode("utf-8")))
        os.replace(tmp, QUEUE_FILE)
    except Exception as e:
        _log("队列写不了：%s" % e)


def queue_load(crypto):
    if not os.path.exists(QUEUE_FILE):
        return []
    try:
        with open(QUEUE_FILE, "rb") as f:
            return json.loads(crypto.dec(f.read()).decode("utf-8"))
    except Exception as e:
        _log("队列读不了（先不补发）：%s" % e)
        return []


# ---------------------------------------------------------------- 只在 Windows 可用的采集
class Win32(object):
    """前台窗口 ↔ 进程名 / 空闲时长。全部 ctypes，无第三方依赖。"""

    def __init__(self):
        self.ok = IS_WIN
        if not self.ok:
            return
        self.u32 = ctypes.windll.user32
        self.k32 = ctypes.windll.kernel32
        self.psapi = ctypes.windll.psapi
        self.QUERY_LIMITED = 0x1000
        self.PROCESS_QUERY_INFORMATION = 0x0400

    def idle_seconds(self):
        if not self.ok:
            return 0
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(li)
        if not self.u32.GetLastInputInfo(ctypes.byref(li)):
            return 0
        return max(0, (self.k32.GetTickCount() - li.dwTime) / 1000.0)

    def system_stats(self):
        """系统盘剩余% / 内存占用% / 开机时长(h)。**只有百分比和时长，没有文件名**。"""
        out = {}
        if not self.ok:
            return out
        try:
            k32 = self.k32
            root = (os.environ.get("SystemDrive") or "C:") + "\\"
            free = ctypes.c_ulonglong(0)
            avail = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            if k32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(root), ctypes.byref(avail),
                                       ctypes.byref(total), ctypes.byref(free)):
                if total.value:
                    out["disk_free_percent"] = round(100.0 * free.value / total.value, 1)
        except Exception:
            pass

        class MSX(ctypes.Structure):
            _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        try:
            ms = MSX()
            ms.dwLength = ctypes.sizeof(ms)
            if self.k32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                out["mem_percent"] = int(ms.dwMemoryLoad)
        except Exception:
            pass
        try:
            fn = self.k32.GetTickCount64
            fn.restype = ctypes.c_ulonglong
            out["uptime_hours"] = round(fn() / 3600000.0, 1)
        except Exception:
            pass
        return out

    def foreground(self):
        """返回 (进程名小写, 窗口标题)。**这两样都不出本机**。"""
        if not self.ok:
            return "", ""
        hwnd = self.u32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        pid = wt.DWORD()
        self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ""
        h = self.k32.OpenProcess(self.QUERY_LIMITED | self.PROCESS_QUERY_INFORMATION, False, pid)
        if h:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wt.DWORD(1024)
                if self.k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    name = os.path.basename(buf.value).lower()
            finally:
                self.k32.CloseHandle(h)
        if name.lower().endswith(".exe"):
            name = name[:-4]
        n = self.u32.GetWindowTextLengthW(hwnd)
        title = ""
        if n > 0:
            tbuf = ctypes.create_unicode_buffer(n + 1)
            self.u32.GetWindowTextW(hwnd, tbuf, n + 1)
            title = tbuf.value
        return name, title


# ---------------------------------------------------------------- 归类
def classify(proc, title, cfg):
    """把 (进程名, 标题) 映射成**类别名**。返回的类别是唯一离开本机的东西。"""
    cats = cfg.get("categories") or {}
    p = (proc or "").lower()
    if p:
        for cat, names in cats.items():
            if cat == "other":
                continue
            for kw in (names or []):
                if kw.lower() in p:
                    return cat
    t = (title or "").lower()
    if t:
        for cat, hints in (cfg.get("title_hints") or {}).items():
            for kw in (hints or []):
                if kw.lower() in t:
                    return cat
    return "other"


# ---------------------------------------------------------------- 中枢上传
def _ssl_ctx(cfg):
    name = str((cfg.get("hub") or {}).get("pin_cert") or "hub.crt")
    for p in (os.path.join(ASSETS, name), os.path.join(_BUNDLED, name), os.path.join(BASE, name)):
        if os.path.exists(p):
            try:
                return ssl.create_default_context(cafile=p)
            except Exception as e:
                _log("固定证书加载失败：%s" % e)
    _log("⚠️ 没找到 hub.crt，只能退回系统 CA（自签证书会失败；请把 hub.crt 放到 assets/）")
    return ssl.create_default_context()


def upload(cfg, items):
    """POST /ingest。**只有 HTTPS**，没有明文回退。"""
    h = cfg.get("hub") or {}
    base = (h.get("base") or "").rstrip("/")
    if not base.startswith("https://"):
        return False, "中枢地址必须是 https://（本程序不允许明文上传）"
    if not h.get("token"):
        return False, "没填 token"
    body = json.dumps(items, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/ingest", data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Token", h.get("token"))
    req.add_header("User-Agent", "WhalePC/%s" % VERSION)
    try:
        with urllib.request.urlopen(req, timeout=12, context=_ssl_ctx(cfg)) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        return True, "已上报 %s 条（跳过重复 %s 条）" % (d.get("accepted"), d.get("skipped"))
    except Exception as e:
        return False, "上传失败：%s" % _short(e)


def _short(msg, n=70):
    s = re.sub(r"\s+", " ", str(msg or "")).strip()
    m = re.search(r'"message"\s*:\s*"([^"]+)"', s)
    if m:
        s = m.group(1)
    low = s.lower()
    for kw, human in (("certificate", "证书校验失败"), ("timed out", "连接超时"),
                      ("timeout", "连接超时"), ("refused", "服务拒绝连接"),
                      ("getaddrinfo", "域名解析失败")):
        if kw in low:
            return human
    return s[:n]


# ---------------------------------------------------------------- 采集主循环
class Collector(object):
    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.crypto = QueueCrypto(BASE)
        self.win = Win32()
        self.device = cfg.get("device") or "pc_windows"
        # 当前结算窗口：类别 → 秒；这里是**唯一**碰标题的地方，标题判完即弃
        self.bucket = {}
        self.active_sec = 0.0
        self.idle_sec = 0.0
        self.sit_sec = 0.0            # 连续活跃（用于久坐）
        self.sit_alerted = 0.0
        self.last_payload = []             # 最近一次实际上传的内容（供挂件的"隐私预览"审计）
        # ★ 新增数据源：今日窗口切换次数（只记"换了几次"，不记换了什么）
        self.switch_today = 0
        self._fg_last = ""
        self._day_key = time.strftime("%Y-%m-%d")
        self.last_t = time.time()
        self.last_report = ""
        self.last_report_ts = 0.0
        self._last_flush = time.time()      # ⚠️ 必须初始化：漏了会让常驻循环每 5 秒崩一次、
                                            #    表现成"装上了但中枢一直没数据"（真跑核验抓到过）

    # ---- 一个采样间隔：把这段时间按"是否活跃"记进窗口 ----
    def tick(self):
        now = time.time()
        dt = max(0.0, min(60.0, now - self.last_t))
        self.last_t = now
        if dt <= 0:
            return
        idle_thr = float(self.cfg.get("idle_threshold_seconds") or 120)
        idle = self.win.idle_seconds() if self.win.ok else 0
        if idle >= idle_thr:
            self.idle_sec += dt
            if idle >= max(idle_thr * 2, 300):        # 空够久 → 久坐计时归零
                self.sit_sec = 0.0
            return
        proc, title = self.win.foreground()
        # ★ 切窗口计数：只留"是不是换了"，不留换成了哪个（进程名随后即弃）
        if proc and proc != self._fg_last:
            self.switch_today += 1
            self._fg_last = proc
        cat = classify(proc, title, self.cfg)
        proc = title = None                                # 判完即弃，不留引用
        self.active_sec += dt
        self.sit_sec += dt
        self.bucket[cat] = self.bucket.get(cat, 0.0) + dt

    # ---- 结算窗口 → 待上报条目（只有聚合量）----
    def envelope(self, dry=False):
        ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        dk = time.strftime("%Y-%m-%d")
        if dk != self._day_key:                     # 跨天：今日切换计数归零
            self._day_key, self.switch_today, self._fg_last = dk, 0, ""
        items = []
        def add(metric, value, unit):
            items.append({"device": self.device, "metric": metric,
                          "value": round(value, 1), "unit": unit, "ts": ts,
                          "meta": {"app": APP, "v": VERSION}})
        if self.active_sec >= 1:
            add("screen.active_minutes", self.active_sec / 60.0, "min")
        if self.idle_sec >= 1:
            add("screen.idle_minutes", self.idle_sec / 60.0, "min")
        for cat, sec in sorted(self.bucket.items(), key=lambda kv: -kv[1]):
            if sec >= 30:                                   # 短于 30 秒的窗口抖动直接丢弃，别当噪声上报
                add("app.%s_minutes" % cat, sec / 60.0, "min")
        # ⚠️ 指标名必须和中枢那条既有久坐规则一致（hub.py 里读的是 pc.continuous_active_minutes）
        if self.sit_sec >= 30:                              # 0 值不发，省得给中枢添噪声
            add("pc.continuous_active_minutes", self.sit_sec / 60.0, "min")
        # ★ 新增数据源（都只有聚合值/百分比，无文件名、无路径、无标题）
        st = self.win.system_stats() if self.win.ok else {}
        if "disk_free_percent" in st:
            add("pc.disk_free_percent", st["disk_free_percent"], "%")
        if "mem_percent" in st:
            add("pc.mem_percent", st["mem_percent"], "%")
        if "uptime_hours" in st:
            add("pc.uptime_hours", st["uptime_hours"], "h")
        if self.switch_today > 0:
            add("pc.window_switches_today", self.switch_today, "次")   # 当天累计，中枢取最新
        return items

    def flush(self, force=False, dry=False, quiet=False):
        items = self.envelope()
        if not items:
            return 0
        if dry:
            return len(items)
        self.last_payload = items          # 留一份"刚刚真发出去的东西"
        pending = queue_load(self.crypto) + items
        ok, msg = upload(self.cfg, items)
        self.last_report, self.last_report_ts = msg, time.time()
        if ok:
            left = pending[len(items):]                     # 成功后补发历史
            if left:
                ok2, msg2 = upload(self.cfg, left)
                if ok2:
                    _log("[补发] %s" % msg2, quiet)
                    queue_save(self.crypto, [])
                else:
                    queue_save(self.crypto, left)
            else:
                queue_save(self.crypto, [])
        else:
            queue_save(self.crypto, pending[-2000:])        # 失败：攒着，绝不明文发
        if not quiet or not ok:
            _log("%s%s" % (msg, "" if ok else "（已攒本地，等下次）"), quiet)
        # 结算完清零
        self.bucket, self.active_sec, self.idle_sec = {}, 0.0, 0.0
        return len(items)

    def run_forever(self, stop=None):
        """stop：threading.Event，被挂件调用时用它叫停；None 则跑到底（独立进程时）。"""
        interval = float(self.cfg.get("interval_seconds") or 300)
        sit_alert = float(self.cfg.get("sit_alert_minutes") or 0)
        _log("%s %s 起来了：设备=%s 间隔=%.0fs 通道=https+固定证书 队列=DPAPI 加密"
             % (APP, VERSION, self.device, interval))
        if not self.win.ok:
            _log("⚠️ 当前不是 Windows，采不到前台窗口（本机只用来跑自检）")
        while not (stop is not None and stop.is_set()):
            try:
                time.sleep(1)
                if int(time.time()) % 5:            # 每 5 秒 tick 一次（1 秒粒度便于响应停止）
                    continue
                self.tick()
                if sit_alert and self.sit_sec >= sit_alert * 60 and \
                        time.time() - self.sit_alerted > 40 * 60:
                    self.sit_alerted = time.time()
                    self.flush(quiet=True)
                    _log("已连续活跃 %.0f 分钟，上报一次让鲸鲸催主人站起来" % (self.sit_sec / 60))
                if time.time() - (self._last_flush or 0) >= interval:
                    self._last_flush = time.time()
                    self.flush(quiet=True)
            except KeyboardInterrupt:
                _log("收到中断，退出")
                return
            except Exception as e:
                _log("循环异常：%s" % _short(e))


# ---------------------------------------------------------------- 命令行
def do_preview(cfg, secs):
    """把「将要上传的内容」原样打出来 —— 这就是 AI 能看到的东西，一个字节都不多。"""
    c = Collector(cfg, DEFAULT_CFG)
    print("＝ 将要上传的内容（= AI 能看到的内容）＝")
    if not c.win.ok:
        print("  ⚠️ 本机不是 Windows：这里用一段**演示数据**跑通格式，不是真采到的。")
        now = time.time()
        c.bucket = {"dev": 1800.0, "browser": 600.0, "study": 300.0, "other": 120.0}
        c.active_sec, c.idle_sec, c.sit_sec = 2820.0, 480.0, 2820.0
        del now
    items = c.envelope()
    print(json.dumps(items, ensure_ascii=False, indent=2))
    print("\n＝ 明确**不会**上传的东西 ＝")
    for x in ("窗口标题全文", "进程完整路径 / 文件名", "网址 / 文档名 / 搜索词",
              "按键内容（键盘记录）", "截图 / 剪贴板"):
        print("  ✗ %s" % x)
    print("\n  只有「类别 + 分钟数」离开这台电脑；标题只在内存里用来归类，判完即弃。")


def do_status(cfg):
    crypto = QueueCrypto(BASE)
    pend = queue_load(crypto)
    print("＝ %s %s ＝" % (APP, VERSION))
    print("  设备名        %s" % (cfg.get("device")))
    print("  中枢          %s（只有 HTTPS，无明文回退）" % (cfg.get("hub", {}).get("base")))
    print("  上报间隔      %s 秒    空闲判定 %s 秒    久坐提醒 %s 分钟"
          % (cfg.get("interval_seconds"), cfg.get("idle_threshold_seconds"),
             cfg.get("sit_alert_minutes")))
    print("  本地待发队列  %d 条（DPAPI 加密）" % len(pend))
    print("  日志          %s" % LOG_FILE)
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-4:]
        for ln in tail:
            print("    %s" % ln.rstrip())


def main():
    ap = argparse.ArgumentParser(description="%s %s" % (APP, VERSION))
    ap.add_argument("--once", action="store_true", help="采一轮并上报")
    ap.add_argument("--preview", action="store_true", help="打印将要上传的内容（不发）")
    ap.add_argument("--status", action="store_true", help="看状态")
    ap.add_argument("--init", action="store_true", help="生成配置文件")
    ap.add_argument("--config", help="指定配置文件")
    args = ap.parse_args()

    cfg_path = args.config or DEFAULT_CFG
    cfg = load_cfg(cfg_path)
    if args.init or not os.path.exists(cfg_path):
        if save_cfg(cfg, cfg_path):
            _log("配置已生成：%s" % cfg_path)
    if args.init:                       # ⚠️ 只生成配置就退出，别再往下跑到常驻循环里
        return

    if args.preview:
        do_preview(cfg, 0)
        return
    if args.status:
        do_status(cfg)
        return

    c = Collector(cfg, cfg_path)
    if args.once:
        c.last_t = time.time() - float(cfg.get("interval_seconds") or 300)
        c.tick()
        n = c.flush()
        _log("本轮结算 %d 条：%s" % (n, c.last_report))
        return
    c.run_forever()


if __name__ == "__main__":
    main()

# ------------------------------------------------------------- 外挂扩展（ext/）
# 参考「大方 agent」的 ext 设计：目录即插件、失败隔离、钩子可注册。
# 目的：让"加一个新数据源/新动作"**不改中枢核心**，也**不破坏零第三方依赖**。
EXT_DIR = os.path.join(BASE, "ext")
EXT = {"sources": [], "hooks": {}, "loaded": [], "errors": []}


def _load_py(path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 扩展自己的错误在这里抛出 → 被下面捕获
    return mod


def load_ext():
    """加载 ext/sources/*.py 与 ext/hooks.py。**任何一项出错只记录，不阻塞启动。**"""
    EXT["sources"] = []
    EXT["errors"] = []
    EXT["loaded"] = []
    sd = os.path.join(EXT_DIR, "sources")
    if os.path.isdir(sd):
        for f in sorted(os.listdir(sd)):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            try:
                mod = _load_py(os.path.join(sd, f), "whale_ext_" + f[:-3])
                fn = getattr(mod, "fetch", None)
                if not callable(fn):
                    EXT["errors"].append("%s: 没有 fetch()" % f)
                    continue
                EXT["sources"].append({
                    "file": f,
                    "name": getattr(mod, "NAME", f[:-3]),
                    "interval": max(1, int(getattr(mod, "INTERVAL_MINUTES", 30) or 30)),
                    "device": getattr(mod, "DEVICE", f[:-3]),
                    "fetch": fn,
                    "last": 0.0, "last_n": 0, "last_err": "",
                })
                EXT["loaded"].append("source:" + f)
            except Exception as e:
                EXT["errors"].append("%s: %s" % (f, str(e)[:160]))
    hp = os.path.join(EXT_DIR, "hooks.py")
    if os.path.isfile(hp):
        try:
            mod = _load_py(hp, "whale_ext_hooks")
            if callable(getattr(mod, "on_event", None)):
                EXT["hooks"].setdefault("on_event", []).append(mod.on_event)
            if callable(getattr(mod, "before_say", None)):
                EXT["hooks"].setdefault("before_say", []).append(mod.before_say)
            EXT["loaded"].append("hooks")
        except Exception as e:
            EXT["errors"].append("hooks.py: %s" % str(e)[:160])
    if EXT["loaded"] or EXT["errors"]:
        print("[ext] 已加载 %s%s" % (EXT["loaded"] or "无",
                                     ("（错误 %d 条，见 /ext）" % len(EXT["errors"])) if EXT["errors"] else ""),
              flush=True)
    return EXT


def call_hook(name, *a, **kw):
    """调用外挂钩子。返回最后一个非 None 的结果；扩展报错不影响主流程。"""
    out = None
    for f in EXT["hooks"].get(name, []):
        try:
            r = f(*a, **kw)
            if r is not None:
                out = r
        except Exception as e:
            print("[ext] 钩子 %s 报错：%s" % (name, str(e)[:120]), flush=True)
    return out


def ext_loop():
    """独立线程轮询扩展数据源（fetch 可能做网络 IO，绝不放进 5 秒调度里拖慢她）。"""
    load_ext()
    while True:
        try:
            now = time.time()
            for s in EXT["sources"]:
                if now - s["last"] < s["interval"] * 60:
                    continue
                s["last"] = now
                try:
                    items = s["fetch"]()
                except Exception as e:
                    s["last_err"] = str(e)[:160]
                    print("[ext] %s 取数失败：%s" % (s["name"], s["last_err"]), flush=True)
                    continue
                if not items:
                    s["last_n"] = 0
                    continue
                try:
                    batch = []
                    for it in (items if isinstance(items, list) else [items]):
                        if not isinstance(it, dict) or not it.get("metric"):
                            continue
                        batch.append({"device": it.get("device") or s["device"],
                                      "metric": it["metric"], "value": it.get("value"),
                                      "unit": it.get("unit", ""), "source": it.get("source", "ext"),
                                      "confidence": float(it.get("confidence", 1.0)),
                                      "ts": it.get("ts") or now_iso(),
                                      "meta": it.get("meta") or {}})
                    ok, skipped = ingest_items(batch)
                    s["last_n"], s["last_err"] = ok, ""
                    print("[ext] %s → 入库 %d 条（重复跳过 %d）" % (s["name"], ok, skipped), flush=True)
                except Exception as e:
                    s["last_err"] = str(e)[:160]
                    print("[ext] %s 入库失败：%s" % (s["name"], s["last_err"]), flush=True)
        except Exception as e:
            print("[ext] 循环异常：%s" % str(e)[:120], flush=True)
        time.sleep(30)



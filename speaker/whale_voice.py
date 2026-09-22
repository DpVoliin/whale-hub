#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鲸鲸的"嘴"：把冷冰冰的事实改写成她自己的说法（角色卡 + few-shot + 去重复 + 重试）。

为什么放在这台机器上：API key 只在本机（不落服务器）。中枢只负责"算出该说什么事实"，
说什么话由这里现场生成；模型不可用时自动回落到中枢给的模板句 —— 宁可话朴素，也不能不提醒。

用法：
    from whale_voice import speak
    speak("心率 150，偏高")     # → "（攥住你的手腕）心率到 150 了…"
"""
import json
import os
import pathlib
import re
import time
import urllib.request

def _scripts_dir() -> pathlib.Path:
    """说话层要读的角色卡/配置在哪。

    优先级：WHALE_HOME → /opt/whale（旧部署默认）→ ~/.whale
    """
    home = os.getenv("WHALE_HOME")
    if home:
        h = pathlib.Path(home).expanduser()
        # 两种常见布局都认：$WHALE_HOME/.hermes/scripts 或 $WHALE_HOME/scripts
        for sub in (h / ".hermes" / "scripts", h / "scripts", h):
            if sub.exists():
                return sub
        return h / ".hermes" / "scripts"
    legacy = pathlib.Path("/opt/whale/.hermes/scripts")
    if legacy.exists():
        return legacy
    return pathlib.Path.home() / ".whale"


SCRIPTS = _scripts_dir()
CARD_PATH = pathlib.Path(os.getenv("WHALE_CARD", SCRIPTS / "whale_card.json"))
RECENT_PATH = SCRIPTS / ".whale_said.jsonl"   # 最近说过的话（防重复）
CONFIG = pathlib.Path(os.getenv("WHALE_CONFIG", SCRIPTS.parent / "config.yaml"))

# 中枢配置文件（用于兜底捞 token）—— 与中枢 _resolve_home() 的口径保持一致
def _hub_cfg_path() -> pathlib.Path:
    import os as _os
    h = _os.getenv("WHALE_HOME")
    if h:
        return pathlib.Path(h).expanduser() / "hub.json"
    legacy = pathlib.Path("/opt/whale/hub/hub.json")
    if legacy.exists():
        return legacy
    return pathlib.Path.home() / ".whale" / "hub.json"


BASE_CFG = _hub_cfg_path()
RECENT_KEEP = 6
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def _dbg(*a):
    if os.getenv("WHALE_DEBUG"):
        print("[voice]", *a, flush=True)


def _llm_conf():
    """读出模型配置。支持 fallback 模型链 —— 主模型挂了不至于整层哑掉。"""
    import yaml
    try:
        m = (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("model") or {}
    except Exception:
        m = {}
    base = (m.get("base_url") or "").rstrip("/")
    key = m.get("api_key") or os.getenv("OPENCODE_GO_API_KEY") or ""
    primary = m.get("default") or "deepseek-v4.1-flash"

    # fallback 链：配置里显式写 fallback_models；否则至少不把主模型重复一遍
    fb = [x for x in (m.get("fallback_models") or []) if x and x != primary]
    if not fb:
        # 没配就用一组保守的默认（只在国内常见的稳定模型里挑）
        fb = [x for x in ("deepseek-v4.1-flash", "glm-5.3") if x != primary]

    return {
        "base": base,
        "key": key,
        "model": primary,
        "models": [primary] + fb,
        "headers": m.get("default_headers") or {},
    }


def _report_degraded(reason: str):
    """连续回落时，把「说话层哑了」这件事推给中枢 —— 用户必须能看见。

    为什么要有这个：以前只有 WHALE_DEBUG 时才打印，用户唯一的感觉是
    "她今天说话变傻了"，完全不知道主模型在报 500。静默降级比崩溃更危险。
    """
    try:
        hub = os.getenv("WHALE_HUB", "http://127.0.0.1:11440")
        tok = os.getenv("WHALE_TOKEN", "")
        if not tok:
            # 从中枢配置里捞 token（说话层通常和中枢同机）
            try:
                import yaml as _y
                c = _y.safe_load((pathlib.Path(BASE_CFG)).read_text(encoding="utf-8")) or {}
                tok = c.get("token", "")
            except Exception:
                pass
        if not tok:
            return
        payload = json.dumps({
            "metric": "health.speaker_degraded",
            "value": 1,
            "unit": "flag",
            "meta": {"reason": reason[:200], "model": _llm_conf()["model"]},
        }).encode()
        req = urllib.request.Request(
            hub.rstrip("/") + "/ingest", data=payload,
            headers={"Content-Type": "application/json", "X-Token": tok})
        urllib.request.urlopen(req, timeout=6).read()
        _dbg("已上报 speaker_degraded")
    except Exception as e:
        _dbg("上报降级状态失败：", type(e).__name__, e)


def _recent(n=RECENT_KEEP):
    try:
        lines = RECENT_PATH.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x).get("text", "") for x in lines if x.strip()]
    except Exception:
        return []


def _remember(text: str):
    try:
        with RECENT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
        lines = RECENT_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > 40:
            RECENT_PATH.write_text("\n".join(lines[-40:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _clean(s: str) -> str:
    s = (s or "").strip().strip('"「」“”\'')
    s = re.sub(r"^(鲸鲸|助手)[:：]\s*", "", s)
    return s.split("\n")[0].strip()


def _build_msgs(card, fact):
    msgs = [{"role": "system", "content": card["system_prompt"]}]
    for ex in card.get("mes_example", [])[:5]:
        msgs.append({"role": "user", "content": ex["user"]})
        msgs.append({"role": "assistant", "content": ex["char"]})
    recent = _recent()
    if recent:
        msgs.append({"role": "user",
                     "content": "（最近已经说过这几条，别再用同样的动作和说法）\n" + "\n".join(recent)})
        msgs.append({"role": "assistant", "content": "明白，换动作、换说法。"})
    msgs.append({"role": "user", "content": f"[事实] {fact}"})
    return msgs


def _call(conf, card, fact, timeout):
    body = json.dumps({
        "model": conf["model"],
        "messages": _build_msgs(card, fact),
        "temperature": 1.05,        # 稍微放开一点才有活人味
        "max_tokens": 160,
        "top_p": 0.95,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(conf["base"] + "/chat/completions", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + conf["key"])
    # 必须带 UA：opencode 前面有 Cloudflare，Python 默认 UA 会被 403（error 1010）拦掉
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    for k, v in (conf["headers"] or {}).items():
        req.add_header(k, str(v))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return _clean(data["choices"][0]["message"]["content"])


def _ok(text: str, fact: str):
    """质量闸：太短/太长/没带（动作）/丢了关键数字 → 不算合格。"""
    if not text or len(text) > 90 or "（" not in text:
        _dbg(f"闸：len={len(text)} 括号={'（' in (text or '')} 原文={text!r}")
        return False
    def norm(x: str) -> str:
        return str(int(x)).lstrip("0") if x.isdigit() else x.replace(":", "").replace(".", "")

    fact_nums = {norm(n) for n in re.findall(r"\d+(?:[.:]\d+)?", fact)}
    text_nums = {norm(n) for n in re.findall(r"\d+(?:[.:]\d+)?", text)}
    for n in fact_nums:
        if len(n) >= 2 and n not in text_nums:      # 只死守"有意义的数字"（如 409）；8 点/08:00 视作同一个
            _dbg(f"闸：丢了数字 {n} 原文={text!r}")
            return False
    return True


def speak(fact: str, fallback: str = "", timeout: int = 20, remember: bool = True, tries: int = 3) -> str:
    """把事实改写成鲸鲸的一句话；失败回落模板句。

    模型选择：按 conf["models"] 顺序试 —— 主模型连续失败后自动换 fallback。
    全部失败：回落模板句 **并且上报 health.speaker_degraded**（用户必须能看见，
    不能只让人觉得"她今天说话变傻了"）。
    """
    try:
        card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        _dbg("角色卡读不了：", e)
        return fallback or fact

    conf = _llm_conf()
    if not conf["base"] or not conf["key"]:
        _dbg("缺 base_url / key")
        return fallback or fact

    last_err = ""
    for model in conf["models"]:
        conf_m = dict(conf, model=model)
        for i in range(tries):
            try:
                text = _call(conf_m, card, fact, timeout)
                if _ok(text, fact):
                    if remember:
                        _remember(text)
                    if model != conf["model"]:
                        _dbg(f"已降级到 {model} 生成成功")
                    return text
                _dbg(f"[{model}] 第 {i + 1} 稿不合格，重试")
            except Exception as e:
                last_err = f"{type(e).__name__}: {str(e)[:120]}"
                _dbg(f"[{model}] 第 {i + 1} 次调用失败：{last_err}")
                try:
                    _dbg("响应体:", e.read().decode()[:200])
                except Exception:
                    pass
            time.sleep(1.0)
        _dbg(f"[{model}] 用尽 {tries} 次")

    _dbg("全部模型都失败 → 回落模板句，并上报降级")
    _report_degraded(last_err or "all models failed")
    return fallback or fact


if __name__ == "__main__":
    import sys
    fact = sys.argv[1] if len(sys.argv) > 1 else "心率 150，偏高"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    for i in range(n):
        print(f"  {i + 1}. {speak(fact, remember=False)}")

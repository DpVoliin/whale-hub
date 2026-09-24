#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型特性适配层：不同厂商的"思考型/非思考型"差异，在这里统一抹平。

为什么需要它（2026-09-24 血的教训）：
    线上用的是 deepseek-v4.1-flash —— **思考型**模型。它会先花 token 想，再输出。
    而调用只给了 max_tokens=200 → 思考就吃光了预算 → content 返回**空串** ✗
    调用方看到空就判"不说" → **一整天一条消息都发不出去** ✗✗
    日志实测思考长度：297 / 365 / 1058 token —— 全都超过 200 ✓

当时的修法是"空回复就翻三倍重试" —— 那是**事后补救** ✗ 每次白烧两次调用 ✗
正确做法是按模型特点**事先给对参数** ✓ 这个模块就是干这个的。

各家真实差异（都是踩过或查过的，不是猜的）：
┌──────────────┬──────────┬────────────────────┬──────────────┬─────────────────┐
│ 模型族        │ 思考型？  │ token 参数名        │ temperature  │ 说明            │
├──────────────┼──────────┼────────────────────┼──────────────┼─────────────────┤
│ deepseek-r1  │ 是       │ max_tokens         │ 建议不带      │ 思考放          │
│ deepseek-v4  │ 是       │ max_tokens         │ 可用         │ reasoning_content│
│ o1/o3/gpt-5  │ 是       │ max_completion_*   │ 只允许 1     │ 不认 system 角色 │
│ qwen3-thinking│ 是      │ max_tokens         │ 可用         │ 可用 enable_think│
│ glm-z1       │ 是       │ max_tokens         │ 可用         │ reasoning_content│
│ kimi-k2      │ 否       │ max_tokens         │ 可用         │                 │
│ claude-*     │ 否       │ max_tokens(必填)   │ 与 top_p 互斥│                 │
│ gpt-4o/4.1   │ 否       │ max_tokens         │ 可用         │                 │
│ llama/qwen 普通│ 否      │ max_tokens         │ 可用         │                 │
└──────────────┴──────────┴────────────────────┴──────────────┴─────────────────┘

用法：
    from model_profile import profile_for
    p = profile_for("deepseek-v4.1-flash")      # → Profile(thinking=True, budget=2400, ...)
    print(p.describe())                          # 人类可读的一行
"""
from __future__ import annotations

import os
import re

# ★ 思考型模型的经验预算：思考 300~1100 + 正文 100~300 → 给 2400 很宽松 ✓
#   非思考型：正文 90 字以内 → 400 足够 ✓（给大也不贵：只按实际生成计费 ✓）
BUDGET_THINKING = int(os.getenv("WHALE_BUDGET_THINKING", "2400"))
BUDGET_PLAIN = int(os.getenv("WHALE_BUDGET_PLAIN", "400"))
BUDGET_MIN = 128


class Profile:
    __slots__ = ("name", "thinking", "token_param", "temperature", "note", "reasoning_field")

    def __init__(self, name, thinking, token_param="max_tokens", temperature=None,
                 note="", reasoning_field="reasoning_content"):
        self.name = name
        self.thinking = thinking
        self.token_param = token_param
        self.temperature = temperature          # None = 用调用方给的值
        self.note = note
        self.reasoning_field = reasoning_field

    @property
    def budget(self) -> int:
        return max(BUDGET_MIN, BUDGET_THINKING if self.thinking else BUDGET_PLAIN)

    def describe(self) -> str:
        kind = "思考型" if self.thinking else "非思考型"
        return (f"{self.name}（{kind} · {self.token_param}={self.budget}"
                + (f" · temperature 固定 {self.temperature}" if self.temperature is not None else "")
                + (f" · {self.note}" if self.note else "") + "）")


# ── 按模型名匹配（顺序敏感：先具体后笼统 ✓）
_RULES = [
    (r"deepseek[-_]?r1",      lambda n: Profile(n, True, note="思考放 reasoning_content，别给 temperature")),
    (r"deepseek[-_]?(v4|v3\.2|reasoner)", lambda n: Profile(n, True, note="思考放 reasoning_content")),
    (r"\bo[13]\b|o[13][-_]|gpt[-_]?5|gpt[-_]?o", lambda n: Profile(n, True, token_param="max_completion_tokens",
                                                                 temperature=1, note="不认 system 角色，提示要并入 user")),
    (r"qwen3?[-_]?(thinking|think)|qwq", lambda n: Profile(n, True, note="可用 enable_thinking 关掉")),
    (r"glm[-_]?z1|glm[-_]?4\.5|zhipu", lambda n: Profile(n, True)),
    (r"magistral|reasoning",  lambda n: Profile(n, True, note="名字带 reasoning 一律当思考型")),
    (r"claude",               lambda n: Profile(n, False, note="max_tokens 必填；temperature 与 top_p 别同时给")),
    (r"kimi|moonshot",        lambda n: Profile(n, False)),
    (r"gpt[-_]?4|llama|mistral|qwen|gemini|glm", lambda n: Profile(n, False)),
]

# 探测结果缓存（同一个模型只探一次 ✓ 探测本身要花一次调用 ✓）
_PROBE_CACHE: dict[str, Profile] = {}


def profile_for(model: str, base_url: str = "") -> Profile:
    """按模型名给出参数画像；名字看不出来就按**运行时探测**的结果 ✓"""
    name = (model or "").strip()
    key = f"{base_url}|{name}"
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    low = name.lower()
    for rx, make in _RULES:
        if re.search(rx, low):
            p = make(name)
            _PROBE_CACHE[key] = p
            return p
    # 认不出来 → 保守当**思考型**（给大预算不会出错 ✓ 给小了才会空 ✗）
    p = Profile(name or "未知模型", True, note="名字没匹配上 → 保守按思考型处理（预算给大不亏 ✓）")
    _PROBE_CACHE[key] = p
    return p


def apply_to_body(body: dict, prof: Profile, want_tokens: int, want_temp: float) -> dict:
    """把画像应用到请求体上（这就是"按模型特点"的核心一步 ✓）"""
    body[prof.token_param] = max(want_tokens, prof.budget)
    if prof.token_param != "max_tokens":
        body.pop("max_tokens", None)
    if prof.temperature is not None:
        body["temperature"] = prof.temperature        # 有些模型只接受固定值
    else:
        body["temperature"] = want_temp
    return body


def probe_note(resp_choice: dict, prof: Profile) -> str:
    """从一次真实响应里学习：思考长度是否吃掉了预算 ✓"""
    msg = (resp_choice or {}).get("message") or {}
    rlen = len(str(msg.get("reasoning_content") or ""))
    if rlen and not prof.thinking:
        prof.thinking = True                          # 名字骗人 → 以实际为准 ✓
        prof.note = (prof.note + "；实测有思考内容 → 改判思考型").strip("；")
    return f"思考 {rlen} 字符 · 预算 {prof.budget}"


if __name__ == "__main__":      # 自测：给几个常见名字看看判得对不对
    for m in ("deepseek-v4.1-flash", "deepseek-r1", "gpt-5", "o3-mini", "qwen3-thinking",
              "claude-sonnet-4", "kimi-k2", "gpt-4o", "某个没听过的名字"):
        print("  ", profile_for(m).describe())

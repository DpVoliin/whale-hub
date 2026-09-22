# -*- coding: utf-8 -*-
"""STM32 小屏/音箱的行为定制 —— 复制成 hub/ext/hooks.py 即生效。

这是**扩展层**：改行为不用动中枢核心（`hub/src/whalecare/`）✓
默认什么都不改；下面每段都是"想要就取消注释"。

它管的是**下发到她桌上那块屏**的内容与时机（设备通过 /mcu/inbox 取）。
"""
import time


def before_say(text, level="info", kind="care", key=None):
    """她每次要主动说一句话之前被调用（中枢核心 → 你这里 → 队列 → 设备取走）。

    返回字符串 → 用你的话替换这句；返回 ""  → 拦掉（中枢会当成"这次不说"）；
    返回 None → 保持原样。
    """
    h = time.localtime().tm_hour

    # 例 1：屏幕设备在深夜只允许紧急内容（别让喇叭半夜说话）
    if (h >= 23 or h < 7) and level != "urgent":
        return ""

    # 例 2：把它改短一点（TTS 模块限制 / 屏幕放不下）
    # if text and len(text) > 60:
    #     return text[:57] + "……"

    # 例 3：给屏上那条加个前缀（屏幕和微信看到的不一样）
    # return "【桌上小鲸鱼】" + text

    # 例 4：只放行"值得念出来"的那几类，其他仅在微信里出现
    # if kind in ("random", "chat"):
    #     return ""

    return None


def on_event(kind, data=None):
    """中枢内部事件（生成简报、健康异常…）。用来做记录或联动，返回值不参与决策。

    例：设备上线/离线时记一笔，或触发你自己的自动化。
    """
    # if kind in ("brief_morning", "brief_evening"):
    #     print(f"[my-hook] {kind} 生成，设备侧可以顺带刷一次屏")
    return None

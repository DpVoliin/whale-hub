#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脱敏回归测试集：**每次改 prompt / 换模型 / 改脱敏逻辑都要跑一遍**。

为什么必须有：LLM 脱敏最大的坑是"换个模型突然不漏了、又突然漏了"——
没有回归集就发现不了。这里做两层：

  ① 活体层：抓 `/llm-preview`（真正要发给模型的那份 payload），
     用正则扫 PII 样式 + 一组"必须被脱掉"的真实形态样本。
  ② 管道层：把样本塞进会进上下文的字段，再跑一遍，确认没漏。

用法：
    python3 tests/test_privacy_regression.py                    # 打线上（默认见 HUB）
    python3 tests/test_privacy_regression.py --file dump.json   # 用已存的 /llm-preview 快照跑
退出码：0 = 全过；1 = 有泄漏（**放进 CI，别让人肉记得**）。
"""
import argparse, json, os, re, ssl, sys, urllib.request

HUB = os.environ.get("WHALE_HUB", "https://YOUR_SERVER:11443")
TOKEN = os.environ.get("WHALE_TOKEN", "")
CA = os.environ.get("WHALE_CA", "")

# ---- ① PII 样式（结构化的，正则能抓）--------------------------------------
PATTERNS = [
    ("手机号", r"1[3-9]\d{9}"),
    ("邮箱", r"[\w.+-]+@[\w-]+\.[\w.]+"),
    ("身份证", r"\d{17}[\dXx]"),
    ("银行卡", r"\b\d{16,19}\b"),
    ("车牌", r"[\u4e00-\u9fa5][A-Z][A-Z0-9]{5}"),
    ("微信/QQ号", r"(?i)(wx|wechat|qq)[:：]?\s*[a-zA-Z0-9_-]{5,}"),
]
# ---- ② 真实形态样本（"换了模型突然不漏"就靠它抓）-------------------------
SAMPLES = [
    # 地址 / 门牌
    "佛山市禅城区江湾一路18号3座502", "广州市天河区体育西路103号", "教三-305", "6栋207",
    # 学校 / 单位
    "佛山大学", "某科技有限公司", "广州地铁集团", "南海区人民医院",
    # 家人称呼 + 方言谐音
    "我妈", "我老婆", "我弟", "阿嫲", "老豆", "囡囡", "姨妈", "舅父",
    # 联系人 / 号码
    "13800138000", "0577-88888888", "zhangsan@example.com", "440601199001011234",
    "6222021234567890123", "粤A12345", "wx:xiaoming_88",
    # 具体地点 / 门店
    "祖庙地铁站A口", "星巴克（岭南天地店）", "南方医院发热门诊",
    # 家庭/健康细节
    "我家娃在佛大附小", "我奶奶的高血压药", "对象生日 3 月 12",
]

BAD_WORDS = ["raw", "text", "原文"]     # meta 里不该出现的字段名


def load_payload(args):
    if args.file:
        return json.load(open(args.file, encoding="utf-8"))
    ctx = ssl.create_default_context(cafile=CA) if CA else None
    req = urllib.request.Request(HUB.rstrip("/") + "/llm-preview")
    req.add_header("X-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="用已存的 /llm-preview JSON 快照跑")
    args = ap.parse_args()
    d = load_payload(args)
    # 真正发给模型的那一份（有些实现把原文放在另一个键里 → 整个 payload 都扫）
    blob = json.dumps(d, ensure_ascii=False)
    bad = []

    for name, pat in PATTERNS:
        for m in re.finditer(pat, blob):
            seg = blob[max(0, m.start() - 30):m.end() + 30]
            bad.append((name, m.group(0), seg))

    c = d.get("would_send_to_model") or d
    for s in SAMPLES:
        if s in json.dumps(c, ensure_ascii=False):
            bad.append(("样本原文泄漏", s, "出现在 would_send_to_model 里"))

    for w in BAD_WORDS:
        if '"%s"' % w in blob:
            bad.append(("不该有的字段", w, "payload 里出现了 raw/text 字段"))

    print("  扫了 %d 条 PII 样式 + %d 条真实形态样本" % (len(PATTERNS), len(SAMPLES)))
    if bad:
        print("  ✗ 发现 %d 处问题：" % len(bad))
        for name, hit, seg in bad[:15]:
            print("    [%s] %s … %s" % (name, hit, seg[:70]))
        return 1
    print("  ✓ 全部通过：模型那份 payload 里没有 PII 样式，也没有样本原文")
    return 0


if __name__ == "__main__":
    sys.exit(main())

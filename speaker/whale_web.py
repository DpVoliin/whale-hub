#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鲸鲸的"随手一查"（安全筛查版）。

为什么要有筛查：用户提醒"bing 有很多毒网页"。搜索结果是要喂给模型的**外部不可信数据**，
不加处理会有三类风险：
  ① 毒站/下载站/SEO 垃圾 → 她照着说，等于把垃圾话传给你
  ② 有害内容（破解/外挂/赌博/贷款…）→ 她不该碰这些话题
  ③ **提示注入**：摘要里写"忽略之前的指令…" → 模型可能被带跑（这是最危险的）

所以四道闸：
  1) **域名信任分级**：只信百科/官方/音乐平台/主流媒体；下载站、资源站一律丢
  2) **有害词拦截**：标题或摘要命中即丢
  3) **注入剥离**：把"忽略指令/ignore previous/system prompt"这类句子删掉，并整段标注为"不可信数据"
  4) **少于 2 条可信结果 → 返回空**（查不到就不说，宁可不开口）

其它约束：查询词不许带个人信息；每天最多 12 次；同一问题 24 小时只搜一次；结果只存本机缓存。
搜索源：实测本机 **Bing 可用**（DuckDuckGo 网络不可达、百度要验证码）。
"""
import hashlib
import html as _html
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

CACHE = pathlib.Path("/opt/whale/.hermes/scripts/.whale_web.json")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
MAX_PER_DAY = 12
MAX_QUERY = 48
CACHE_TTL = 24 * 3600
MIN_TRUSTED = 2          # 可信结果少于这个数就不说

# ① 信任域名（后缀匹配）
TRUSTED = (
    "baike.baidu.com", "wikipedia.org", "wikimedia.org", "zhihu.com", "douban.com",
    "bilibili.com", "music.163.com", "y.qq.com", "kugou.com", "kuwo.cn",
    "moegirl.org", "bangumi.tv", "imdb.com", "apple.com", "spotify.com",
    "gov.cn", "edu.cn", "ac.cn", "people.com.cn", "xinhuanet.com", "thepaper.cn",
    "36kr.com", "sspai.com", "github.com", "stackoverflow.com", "msdn", "microsoft.com",
    "openai.com", "deepseek.com", "python.org", "mozilla.org", "taobao.com", "jd.com",
)
# ② 有害 / 垃圾词（命中即丢）
BANNED = (
    "破解", "外挂", "私服", "辅助脚本", "注册机", "激活工具", "免密", "刷单", "代刷",
    "赌博", "博彩", "彩票", "棋牌", "色情", "成人视频", "裸聊", "约炮",
    "贷款", "网贷", "套现", "返利", "微商", "加微信", "加QQ", "客服QQ",
    "免费下载", "网盘下载", "高速下载", "绿色版", "破解版", "无敌版",
    "翻墙", "机场节点", "vpn", "加速器", "代购", "刷课", "替考", "代写论文",
)
# ③ 注入特征（命中就把这句删掉）
INJECT = re.compile(
    r"(忽略(以上|之前|上面|前面)[^。！？]{0,20}(指令|提示|要求|设定)|"
    r"ignore (all )?(previous|above|prior)[^.]{0,30}instructions?|"
    r"system\s*prompt|你现在是|角色设定为|请扮演|jailbreak|越狱模式)",
    re.I)
# 明显是"下载/资源/导航"站的域名特征
JUNK_HOST = ("down", "xiazai", "soft", "crack", "patch", "keygen", "hao123", "dh.", "dizhi", "daohang")


def _dbg(*a):
    print("[web]", *a, flush=True)


def _load():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {"day": "", "count": 0, "cache": {}}


def _save(d):
    try:
        CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _ok_query(q: str) -> bool:
    """查询词的隐私闸：不许带个人信息。"""
    if not q or len(q) > MAX_QUERY:
        return False
    if re.search(r"\d{6,}", q):
        return False
    if re.search(r"@|http|www\.|\.com|\.cn", q, re.I):
        return False
    if re.search(r"(住在|家里|宿舍|门牌|电话|身份|密码|银行卡|学号|班号)", q):
        return False
    # 查询词本身带害词 → 压根不搜（"帮我找破解版"这种不该有反馈）
    if any(w.lower() in q.lower() for w in BANNED):
        return False
    return True


def _host(url: str) -> str:
    try:
        # ★ 别用 lstrip("www.")：它按**字符集**剥，会把 "www.weibo.com" 剥成 "eibo.com"
        return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc.lower())
    except Exception:
        return ""


def _trusted(host: str) -> bool:
    return any(host == t or host.endswith("." + t) or host.endswith(t) for t in TRUSTED)


def _junk(host: str) -> bool:
    return any(j in host for j in JUNK_HOST)


def _clean(text: str) -> str:
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    text = INJECT.sub("（已过滤可疑内容）", text)
    # 摘要里常见的"加微信/QQ/电话"尾巴，直接砍掉
    text = re.sub(r"(加(微信|QQ|群)|联系(客服|电话)|点击(下载|进入)|立即(下载|注册))[^。！？]{0,20}", "", text)
    return text.strip()


def _bing(q: str, timeout: int = 12):
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept-Language", "zh-CN,zh;q=0.9")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "replace")
    out = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S)[:8]:
        link = (re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"', block) or [None, ""])[1]
        title = (re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S) or [None, ""])[1]
        snip = (re.search(r"<p[^>]*>(.*?)</p>", block, re.S) or [None, ""])[1]
        out.append((_clean(title)[:70], _clean(snip)[:160], link))
    return out


def search(query: str, timeout: int = 12) -> str:
    """搜一次并筛查。返回可读的几行；不可信/不够 → 返回空串（她就别说了）。"""
    q = (query or "").strip().lstrip("[SEARCH:").strip("[] ").replace("SEARCH:", "").strip()
    if not _ok_query(q):
        _dbg(f"隐私闸拦下：{q!r}")
        return ""

    d = _load()
    today = time.strftime("%Y-%m-%d")
    if d.get("day") != today:
        d = {"day": today, "count": 0, "cache": {}}
    key = hashlib.sha1(q.encode()).hexdigest()[:16]
    hit = (d.get("cache") or {}).get(key)
    if hit and time.time() - hit.get("at", 0) < CACHE_TTL:
        _dbg(f"命中缓存：{q}")
        return hit.get("text", "")
    if d.get("count", 0) >= MAX_PER_DAY:
        _dbg(f"今天搜索额度用完（{MAX_PER_DAY}）")
        return ""

    try:
        raw = _bing(q, timeout)
    except Exception as e:
        _dbg(f"搜索失败：{type(e).__name__} {str(e)[:80]}")
        return ""
    d["count"] = d.get("count", 0) + 1

    keep, dropped_junk, dropped_banned = [], 0, 0
    for title, snip, link in raw:
        host = _host(link)
        blob = f"{title} {snip}"
        if any(w.lower() in blob.lower() for w in BANNED):
            dropped_banned += 1
            continue
        if host and (_junk(host) or not _trusted(host)):
            dropped_junk += 1
            continue
        keep.append(f"- {title}｜{snip}｜来源 {host or '未知'}")

    _dbg(f"「{q}」原始 {len(raw)} 条 → 可信 {len(keep)}（丢垃圾 {dropped_junk} / 有害 {dropped_banned}）")
    text = "\n".join(keep[:4]) if len(keep) >= MIN_TRUSTED else ""
    if not text and raw and len(keep) < MIN_TRUSTED:
        _dbg("可信结果不足 2 条 → 这次不采用（宁可不开口）")

    d.setdefault("cache", {})[key] = {"at": time.time(), "text": text, "q": q}
    _save(d)
    return text


if __name__ == "__main__":
    import sys
    print(search(" ".join(sys.argv[1:]) or "明日方舟 是什么游戏") or "（没搜到可信结果 / 被拦）")

# ----------------------------------------------------------------- 脱敏（给 AI 之前）
_APP_CATS = [
    ("社交", ("微信", "wechat", "weixin", "tencent.mm", "qq", "微博", "weibo", "telegram", "whatsapp",
              "dingtalk", "dingtalk", "wecom", "企业微信", "飞书", "feishu", "lark")),
    ("短视频/视频", ("抖音", "douyin", "快手", "kuaishou", "哔哩", "bili", "danmaku", "优酷", "youku",
                     "爱奇艺", "iqiyi", "qqlive", "腾讯视频", "mgtv", "youtube", "acfun")),
    ("游戏", ("游戏", "game", "tmgp", "mihoyo", "hypergryph", "arknights", "明日方舟", "netease", "supercell")),
    ("学习", ("学习通", "chaoxing", "xuexitong", "mooc", "coursera", "anki", "notion", "词典", "dictionary",
              "reader", "阅读")),
    ("购物/生活", ("淘宝", "taobao", "京东", "jd.", "拼多多", "pinduoduo", "美团", "meituan", "支付宝",
                   "alipay", "饿了么", "ele.me", "大众点评", "dianping", "闲鱼", "xianyu")),
    ("音乐/播客", ("音乐", "music", "kugou", "kuwo", "ximalaya", "spotify")),
    ("办公/工具", ("wps", "office", "outlook", "chrome", "browser", "浏览器", "邮箱", "mail", "文件", "docs")),
]

_CAL_TYPES = [
    ("考试", ("考试", "测验", "模拟考", "期中", "期末", "补考")),
    ("上课", ("课", "讲座", "实验", "实训", "培训")),
    ("会议", ("会议", "开会", "例会", "答辩", "汇报")),
    ("办理", ("办理", "提交", "截止", "缴费", "报名", "体检")),
]


# 游戏包名/名称：认得出的才单独归类（认不出的一律留在"其他"，不乱猜）
GAME_HINTS = (
    "arknights", "hypergryph", "mihoyo", "hoyoverse", "genshin",
    "tencent.tmgp", "netease.game", "bilibili.game", "pandadagames", "lilith",
    "supercell", "riot", "epicgames", "steam", "方舟", "游戏",
)


def is_game(app, pkg):
    """这个 App 是不是游戏（保守判断：只认包名/名称里的明确线索）。"""
    hay = f"{app} {pkg}".lower()
    return any(h.lower() in hay for h in GAME_HINTS)


_CAT_APP_CACHE = {}


def cat_app(name, pkg=""):
    """具体应用名 → 分类标签（模型只看得到分类）。

    ★ 记忆化：它是**纯函数**（name+pkg 决定结果），但压测发现一次 `llm_context()`
    要调它 **14 万次**（30 天 × 每 10 分钟一轮的 app.usage_minutes 行），每次还线性扫一遍
    分类表 → 单次 llm_context 从 60ms 涨到 2 秒。实际不同 App 名只有几个，缓存住即可。
    """
    key = (name, pkg)
    hit = _CAT_APP_CACHE.get(key)
    if hit is not None:
        return hit
    hay = f"{name} {pkg}".lower()
    out = "其他"
    for label, keys in _APP_CATS:
        if any(k in hay for k in keys):
            out = label
            break
    if len(_CAT_APP_CACHE) > 512:      # 别让缓存无限长（App 名理论上可能很多）
        _CAT_APP_CACHE.clear()
    _CAT_APP_CACHE[key] = out
    return out


def cat_event(title):
    """日程标题 → 类型（标题原文不外发）。"""
    t = title or ""
    for label, keys in _CAL_TYPES:
        if any(k in t for k in keys):
            return label
    return "其他事项"


def blur_minutes(mins, step=None):
    step = step or int(CFG["privacy"].get("blur_sleep_to_minutes", 30))
    try:
        return int(round(float(mins) / step) * step)
    except Exception:
        return None


def blur_device(device):
    d = (device or "").lower()
    if "watch" in d or "band" in d:
        return "手表"
    if "pc" in d or "windows" in d or "mac" in d:
        return "电脑"
    if "server" in d or "hub" in d or "nas" in d or "router" in d:
        return "服务器"
    return "手机"


def hour_only(ts):
    """时间只保留到小时（分钟/秒都是侧信道）。"""
    try:
        return datetime.fromisoformat(ts).strftime("%m-%d %H时")
    except Exception:
        return ""


WEATHER_CODE = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "有雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "强雷阵雨", 99: "强雷雨",
}
# ⚠️ 为什么把 96/99 译成「强雷阵雨」而不是字面的「雷暴伴冰雹」：
#    WMO 96/99 名义上是 thunderstorm with (slight/heavy) hail，但 Open-Meteo 是把它们
#    当**对流强度代理**在用 —— 华南 9 月 33℃ 的天气它照样给 96。照字面翻 → 她就会
#    播报「明天有冰雹」，纯属假警报（用户当场质问过：广州 9 月哪来的冰雹）。
#    结论：这两个码一律按雷阵雨强度译，**永不提冰雹**。



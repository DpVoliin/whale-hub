# ---- 天气：主用中国天气网（中国气象局数据，与大厂手机天气同源）；Open-Meteo 兜底 ----
WX_CODE = {
    "00": "晴", "01": "多云", "02": "阴", "03": "阵雨", "04": "雷阵雨", "05": "雷阵雨伴冰雹",
    "06": "雨夹雪", "07": "小雨", "08": "中雨", "09": "大雨", "10": "暴雨", "11": "大暴雨",
    "12": "特大暴雨", "13": "阵雪", "14": "小雪", "15": "中雪", "16": "大雪", "17": "暴雪",
    "18": "雾", "19": "冻雨", "20": "沙尘暴", "21": "小到中雨", "22": "中到大雨", "23": "大到暴雨",
    "24": "暴雨到大暴雨", "25": "大暴雨到特大暴雨", "26": "小到中雪", "27": "中到大雪",
    "28": "大到暴雪", "29": "浮尘", "30": "扬沙", "31": "强沙尘暴", "53": "霾",
}
WX_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def _json_in(text):
    """从 'var x ={...};var y=...' 里抠出**第一个完整** JSON 对象（官方接口后面带尾巴）。"""
    i = text.find("{")
    if i < 0:
        return None
    depth = 0
    for k in range(i, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:k + 1])
                except Exception:
                    return None
    return None


def wx_search_city(name):
    """把城市名换成中国天气网代码（任意城市；多地用户就靠这个）。"""
    import urllib.request as _rq
    import urllib.parse as _up
    req = _rq.Request("http://toy1.weather.com.cn/search?cityname=" + _up.quote(name) + "&_=1")
    req.add_header("User-Agent", WX_UA)
    req.add_header("Referer", "http://www.weather.com.cn/")
    with _rq.urlopen(req, timeout=15) as r:
        t = r.read().decode("utf-8", "replace")
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j < 0:
        return []
    out = []
    for a in json.loads(t[i:j + 1]):
        ref = str(a.get("ref") or "")
        code = ref.split("~")[0]
        if code.isdigit():
            parts = ref.split("~")
            out.append({"code": code, "name": parts[2] if len(parts) > 2 else (a.get("name") or ""),
                        "province": parts[-1] if len(parts) > 3 else ""})
    return out


def wx_official(code):
    """中国天气网：一次请求拿到 实况 + 今日 + 未来几天 + 预警 + 生活指数。"""
    import urllib.request as _rq
    req = _rq.Request(f"http://d1.weather.com.cn/weather_index/{code}.html")
    req.add_header("User-Agent", WX_UA)
    req.add_header("Referer", "http://www.weather.com.cn/")
    with _rq.urlopen(req, timeout=15) as r:
        page = r.read().decode("utf-8", "replace")
    out = {}
    for name in ("dataSK", "cityDZ", "alarmDZ", "fc", "dataZS"):
        idx = page.find("var %s =" % name)
        out[name] = (_json_in(page[idx:]) if idx >= 0 else None) or {}
    return out


def weather_from_official(code):
    """整理成要存的几条指标（city_code 写进 meta，便于多地用户各自取自己的）。"""
    d = wx_official(code)
    sk = d.get("dataSK") or {}
    days = (d.get("fc") or {}).get("f") or []
    zs = (d.get("dataZS") or {}).get("zs") or {}
    alerts = (d.get("alarmDZ") or {}).get("w") or []

    def num(x):
        try:
            return float(str(x).replace("℃", "").replace("%", "").strip())
        except Exception:
            return None

    base = {"city_code": str(code), "city": sk.get("cityname") or "", "src": "中国天气网"}
    items = []
    if sk:
        m = dict(base)
        m.update({"desc": sk.get("weather") or "", "humidity": num(sk.get("SD")),
                  "wind": f"{sk.get('WD','')}{sk.get('WS','')}".strip(),
                  "rain_1h": num(sk.get("rain")), "rain_24h": num(sk.get("rain24h")),
                  "aqi": num(sk.get("aqi")), "vis_km": num(sk.get("njd")),
                  "observed_at": sk.get("time")})
        items.append({"metric": "weather.now", "value": num(sk.get("temp")), "unit": "C", "meta": m})
    for i, f in enumerate(days[:3]):
        a, b = f.get("fa") or "", f.get("fb") or ""
        desc = WX_CODE.get(a, "")
        if b and b != a:
            desc += "转" + WX_CODE.get(b, "")
        m = dict(base)
        m.update({"label": f.get("fj") or ("今天" if i == 0 else ""), "date": f.get("fi") or "",
                  "tmax": num(f.get("fc")), "tmin": num(f.get("fd")), "desc": desc,
                  "wind": f"{f.get('fe','')}{f.get('fg','')}".strip()})
        items.append({"metric": "weather.day", "value": float(i), "unit": "", "meta": m})
    for a in alerts[:2]:
        m = dict(base)
        m.update({"title": a.get("w1") or a.get("title") or "气象预警", "level": a.get("w2") or "",
                  "text": (a.get("w7") or a.get("content") or "")[:80]})
        items.append({"metric": "weather.alert", "value": 1.0, "unit": "", "meta": m})
    if zs:
        m = dict(base)
        m.update({"dress": f"{zs.get('ct_hint','')}｜{zs.get('ct_des_s','')}"[:60],
                  "traffic": f"{zs.get('lk_hint','')}｜{zs.get('lk_des_s','')}"[:60],
                  "sport": f"{zs.get('cl_hint','')}｜{zs.get('cl_des_s','')}"[:60]})
        items.append({"metric": "weather.life", "value": 1.0, "unit": "", "meta": m})
    return items


def weather_cities():
    """要抓哪些城市：默认城市 + 各设备单独配的城市（多地用户就配 devices.<设备>.city_code）。"""
    codes = {}
    pv = CFG.get("privacy") or {}
    codes[str(pv.get("weather_city_code") or "101280101")] = "server"
    for dev, dcfg in (CFG.get("devices") or {}).items():
        cc = (dcfg or {}).get("city_code")
        if cc:
            codes[str(cc)] = dev
    return codes


def fetch_weather(days=2):
    """抓天气（Open-Meteo，免 key）。城市级坐标写在 privacy.weather_lat/lon，不涉及定位。"""
    import urllib.request as _urlreq          # 显式导入：别依赖别处的作用域别名

    # ① 先试官方源（中国气象局数据）：一次拿到实况 + 多天 + 预警 + 生活指数
    saved = 0
    for code, dev in weather_cities().items():
        try:
            items = weather_from_official(code)
        except Exception as e:
            print(f"[weather] 官方源失败({code})：{type(e).__name__} {str(e)[:60]}", flush=True)
            continue
        if not items:
            continue
        with db() as c:
            for it in items:
                c.execute("INSERT INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
                          "VALUES (?,?,?,?,?,?,?,?,?)",
                          (now_iso(), today_str(), dev, it["metric"], it.get("value"), it.get("unit", ""),
                           "weather.com.cn", 1.0, json.dumps(it.get("meta") or {}, ensure_ascii=False)))
                saved += 1
        print(f"[weather] 官方源更新 {len(items)} 条 · {code} → {dev}", flush=True)
    if saved:
        return saved

    # ② 官方源全挂时才退回 Open-Meteo（国际模型，免 key）
    pv = CFG.get("privacy", {})
    lat = pv.get("weather_lat", 23.13)
    lon = pv.get("weather_lon", 113.12)
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode"
           f"&timezone=Asia%2FShanghai&forecast_days={days}")
    try:
        req = _urlreq.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (whale-hub)")
        with _urlreq.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
    except Exception as e:
        print(f"[weather] 抓取失败：{type(e).__name__} {str(e)[:80]}", flush=True)
        return 0
    dl = d.get("daily") or {}
    dates = dl.get("time") or []
    n = 0
    with db() as c:
        for i, day in enumerate(dates[:days]):
            meta = {
                "tmax": (dl.get("temperature_2m_max") or [None])[i],
                "tmin": (dl.get("temperature_2m_min") or [None])[i],
                "rain": (dl.get("precipitation_probability_max") or [None])[i],
                "code": (dl.get("weathercode") or [None])[i],
                "desc": WEATHER_CODE.get((dl.get("weathercode") or [0])[i], ""),
                "for_day": day,
            }
            c.execute("INSERT INTO metrics(ts, day, device, metric, value, unit, source, confidence, meta) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (now_iso(), today_str(), "server", "weather.day", i, "", "open-meteo", 1.0,
                       json.dumps(meta, ensure_ascii=False)))
            n += 1
    print(f"[weather] 已更新 {n} 天", flush=True)
    return n


def weather_of(which=0):
    """which=0 今天 / 1 明天。只认 12 小时内的数据，避免拿旧天气说事。"""
    try:
        with db() as c:
            rows = c.execute("SELECT ts, meta FROM metrics WHERE metric='weather.day' "
                             "ORDER BY ts DESC LIMIT 4").fetchall()
    except Exception:
        return None
    seen = []
    for r in rows:
        try:
            m = json.loads(r["meta"] or "{}")
        except Exception:
            continue
        if m.get("for_day") in [x.get("for_day") for x in seen]:
            continue
        try:
            fresh = (datetime.now(TZ) - datetime.fromisoformat(r["ts"])).total_seconds() < 12 * 3600
        except Exception:
            fresh = False
        if fresh:
            seen.append(m)
    if len(seen) <= which:
        return None
    m = seen[which]
    return {"desc": m.get("desc"), "tmin": m.get("tmin"), "tmax": m.get("tmax"), "rain_prob": m.get("rain"),
            "for_day": m.get("for_day")}




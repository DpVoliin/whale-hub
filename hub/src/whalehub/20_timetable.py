# ----------------------------------------------------------------- 课表 / 日程
def _timetable():
    with db() as c:
        row = c.execute("SELECT raw, source, updated_at FROM timetable WHERE id=1").fetchone()
    if not row:
        return None
    try:
        tt = json.loads(row["raw"])
        tt["_updated_at"] = row["updated_at"]
        return tt
    except Exception:
        return None


def _parse_weeks(spec):
    """解析周次串：'3周,10-12周(双),15周' / '1-16周(单)' / '1,3,5' → set[int]
    口径与 App 内一致：单/双只作用于它所在的那一段。"""
    out = set()
    if not spec:
        return out
    text = str(spec).replace("，", ",").replace("；", ",").replace("、", ",").replace("；", ",")
    text = text.replace("周", "").replace("第", "").replace("（", "(").replace("）", ")")
    text = text.replace("—", "-").replace("－", "-").replace("~", "-").replace("至", "-")
    for seg in text.split(","):
        seg = seg.strip()
        if not seg:
            continue
        odd = "单" in seg
        even = "双" in seg
        nums = [int(x) for x in re.findall(r"\d+", seg)]
        if not nums:
            continue
        if len(nums) >= 2 and "-" in seg:
            lo, hi = nums[0], nums[1]
        else:
            lo = hi = nums[0]
        for w in range(min(lo, hi), max(lo, hi) + 1):
            if odd and w % 2 == 0:
                continue
            if even and w % 2 == 1:
                continue
            out.add(w)
    return out


def _week_of(tt, date):
    try:
        d0 = datetime.strptime(tt.get("termStartDate") or "", "%Y-%m-%d").date()
    except Exception:
        return 1
    return max(1, (date - d0).days // 7 + 1)


def courses_on(date, tt=None):
    """某天的课（已按开始时间排序；含课名/教室/教师/起止时刻/第几周）。"""
    tt = tt or _timetable()
    if not tt:
        return []
    periods = {p.get("index"): p for p in tt.get("periods", [])}
    week = _week_of(tt, date)
    out = []
    for c in tt.get("courses", []):
        if c.get("day") != date.isoweekday():
            continue
        weeks = _parse_weeks(c.get("weeks"))
        if weeks and week not in weeks:
            continue
        sp = int(c.get("startPeriod") or 1)
        span = int(c.get("span") or 1)
        st = (periods.get(sp) or {}).get("start")
        en = (periods.get(sp + span - 1) or {}).get("end")
        if not st or not en:
            continue
        out.append({"name": c.get("name", ""), "room": c.get("room", ""), "teacher": c.get("teacher", ""),
                    "start": st, "end": en, "periods": f"第{sp}-{sp + span - 1}节" if span > 1 else f"第{sp}节",
                    "week": week})
    return sorted(out, key=lambda x: x["start"])


def calendar_today(day=None):
    """当天日程（来自手机日历上报的 calendar.event，取 meta.title/start/end/location）。"""
    day = day or today_str()
    with db() as c:
        rows = c.execute("SELECT ts, meta FROM metrics WHERE day=? AND metric='calendar.event' ORDER BY ts ASC",
                         (day,)).fetchall()
    out = []
    for r in rows:
        try:
            m = json.loads(r["meta"] or "{}")
        except Exception:
            m = {}
        if m.get("title"):
            out.append({"title": m.get("title"), "start": m.get("start", ""), "end": m.get("end", ""),
                        "location": m.get("location", "")})
    return sorted(out, key=lambda x: x["start"])


WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def with_day_hint(course, now=None):
    """给「下一节课」的 start 加日期前缀 —— **今天/明天不加**，更远才加。

    用户原话：「还在下一节课，如果不是明天或今天的课就显示日期吧」。
    格式：一周内 → 周X 08:00；超过一周 → 03-05 08:00。
    """
    if not course:
        return course
    try:
        now = now or datetime.now(TZ)
        d = datetime.strptime(course["date"], "%Y-%m-%d").date()
        delta = (d - now.date()).days
        if delta in (0, 1):                 # 今天 / 明天 —— 不加（他明确说这两种不加）
            return course
        if delta <= 6:
            head = WEEK_CN[d.isoweekday() - 1]
        else:
            head = d.strftime("%m-%d")
        out = dict(course)
        out["start"] = "%s %s" % (head, course.get("start") or "")
        out["day_hint"] = head
        return out
    except Exception:
        return course


def course_next_today(now=None):
    """今天**还没开始**的下一节课（**绝不跨天**）。今天没有了就返回 None。

    ⚠️ 为什么单独有这个：`next_course_from()` 会跨天找最多 7 天，拿它去说"明早第一节"
    就会把**后天**（甚至更远）的课当成明天早上的课 —— 用户明确投诉过这一点。
    """
    now = now or datetime.now(TZ)
    for c in courses_on(now.date()):
        try:
            hh, mm = (int(x) for x in c["start"].split(":"))
        except Exception:
            continue
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start > now:
            return {**c, "date": now.date().strftime("%Y-%m-%d"),
                    "in_minutes": int((start - now).total_seconds() // 60)}
    return None


def courses_tomorrow():
    """明天有没有课（只看明天这一天，不跨天）。"""
    try:
        return courses_on((datetime.now(TZ) + timedelta(days=1)).date())
    except Exception:
        return []


def next_course_from(now=None):
    """下一节课（跨天找，最多 7 天）。"""
    now = now or datetime.now(TZ)
    for i in range(0, 7):
        d = (now + timedelta(days=i)).date()
        for c in courses_on(d):
            try:
                hh, mm = (int(x) for x in c["start"].split(":"))
            except Exception:
                continue
            start = datetime.combine(d, datetime.min.time(), tzinfo=TZ).replace(hour=hh, minute=mm)
            if start > now:
                return {**c, "date": d.strftime("%Y-%m-%d"),
                        "in_minutes": int((start - now).total_seconds() // 60)}
    return None



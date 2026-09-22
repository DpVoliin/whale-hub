package dev.dpvoliin.whalecollector

import android.content.Context
import org.json.JSONObject
import java.util.Calendar

/**
 * 本地课表：App 自己知道"现在是不是在上课"。
 *
 * 为什么需要：用户要求「上课时间也降低频率」—— 上课时人在教室、手机也没什么新信息，
 * 没必要 5 分钟采一次。判据用导入的那份课表（存本地，跟中枢同一口径：day / weeks / startPeriod / span）。
 *
 * 解析口径与中枢一致：单/双周只作用于它所在的那一段（"3周,10-12周(双),15周"）。
 */
object LocalTimetable {

    fun save(c: Context, json: String) {
        runCatching { P.timetableJson = json }
    }

    private fun toMinutes(hm: String?): Int? {
        if (hm.isNullOrBlank()) return null
        val m = Regex("(\\d{1,2}):(\\d{2})").find(hm) ?: return null
        return m.groupValues[1].toInt() * 60 + m.groupValues[2].toInt()
    }

    /** '3周,10-12周(双),15周' / '1-16周(单)' / '1,3,5' → 第几周集合。 */
    private fun parseWeeks(spec: String?): Set<Int> {
        val out = mutableSetOf<Int>()
        if (spec.isNullOrBlank()) return out
        spec.split(',', '，', '、', ';', '；').forEach { raw ->
            val seg = raw.trim()
            if (seg.isEmpty()) return@forEach
            val odd = seg.contains("单")
            val even = seg.contains("双")
            val nums = Regex("\\d+").findAll(seg).map { it.value.toInt() }.toList()
            when {
                nums.size >= 2 -> for (w in nums[0]..nums[1]) {
                    if (odd && w % 2 == 0) continue
                    if (even && w % 2 == 1) continue
                    out.add(w)
                }
                nums.size == 1 -> out.add(nums[0])
            }
        }
        return out
    }

    private fun isoDow(cal: Calendar): Int = when (cal.get(Calendar.DAY_OF_WEEK)) {
        Calendar.MONDAY -> 1
        Calendar.TUESDAY -> 2
        Calendar.WEDNESDAY -> 3
        Calendar.THURSDAY -> 4
        Calendar.FRIDAY -> 5
        Calendar.SATURDAY -> 6
        else -> 7
    }

    private fun weekOf(termStart: String?, cal: Calendar): Int {
        if (termStart.isNullOrBlank()) return 1
        return runCatching {
            val fmt = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
            val d0 = fmt.parse(termStart) ?: return 1
            val days = ((cal.timeInMillis - d0.time) / 86_400_000L)
            ((days / 7) + 1).toInt().coerceAtLeast(1)
        }.getOrDefault(1)
    }

    /** 现在是不是在上课（前后各留 5 分钟余量）。 */
    fun inClassNow(c: Context): Boolean {
        val json = P.timetableJson
        if (json.isBlank()) return false
        return runCatching {
            val root = JSONObject(json)
            val periods = mutableMapOf<Int, Pair<Int?, Int?>>()
            root.optJSONArray("periods")?.let { arr ->
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    val idx = if (o.has("index")) o.getInt("index") else i + 1
                    periods[idx] = toMinutes(o.optString("start")) to toMinutes(o.optString("end"))
                }
            }
            val cal = Calendar.getInstance()
            val dow = isoDow(cal)
            val week = weekOf(root.optString("termStartDate"), cal)
            val nowM = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)

            val courses = root.optJSONArray("courses") ?: return false
            for (i in 0 until courses.length()) {
                val cs = courses.getJSONObject(i)
                if (cs.optInt("day", -1) != dow) continue
                val weeks = parseWeeks(cs.optString("weeks"))
                if (weeks.isNotEmpty() && week !in weeks) continue
                val sp = cs.optInt("startPeriod", 1)
                val span = cs.optInt("span", 1).coerceAtLeast(1)
                val st = periods[sp]?.first ?: continue
                val en = periods[sp + span - 1]?.second ?: continue
                if (nowM >= st - 5 && nowM <= en) return true
            }
            false
        }.getOrDefault(false)
    }
}

package dev.dpvoliin.whalecollector

import android.content.Context
import org.json.JSONObject

/**
 * 从"睡眠记录"反推作息：**睡前不是固定时间，是算出来的**。
 *
 * 怎么推：睡眠通知里带的是**时长**（如"睡眠 7 小时 32 分"），而通知到达的时刻 ≈ 你醒来的时刻。
 *        于是 入睡时刻 ≈ 到达时刻 − 时长。攒几条取中位数，就是"你通常几点睡"；
 *        再加上平均睡眠时长，就是"你通常几点起"。
 *
 * 有了这两个，采集频率就不用死写 21:30–00:30 —— 你几点睡，高频窗口就跟着挪到几点。
 * 数据不足时回落到 22:30 睡 / 07:00 起（宁可保守，不乱猜）。
 */
object LocalSleep {

    private const val KEY = "sleep_records"
    private const val KEEP = 12

    /** 记一条：入睡时刻由"现在 − 时长"推出来。 */
    fun record(c: Context, durationMinutes: Double) {
        if (durationMinutes <= 30 || durationMinutes > 16 * 60) return      // 明显不合理的丢掉
        runCatching {
            val cal = java.util.Calendar.getInstance()
            val nowMin = cal.get(java.util.Calendar.HOUR_OF_DAY) * 60 + cal.get(java.util.Calendar.MINUTE)
            val bedMin = ((nowMin - durationMinutes.toInt()) % 1440 + 1440) % 1440
            val list = load(c).toMutableList()
            list.add(JSONObject().apply {
                put("bed", bedMin)
                put("min", durationMinutes.toInt())
                put("at", System.currentTimeMillis())
            })
            P.sleepRecords = list.takeLast(KEEP).joinToString("\n") { it.toString() }
        }
    }

    private fun load(c: Context): List<JSONObject> =
        P.sleepRecords.split("\n").filter { it.isNotBlank() }.mapNotNull {
            runCatching { JSONObject(it) }.getOrNull()
        }

    /** 通常几点睡（分钟数 0–1439）；样本不足返回 null。 */
    fun bedtimeMinutes(c: Context): Int? = median(load(c), "bed")

    /** 平均睡多久（分钟）；样本不足返回 null。 */
    fun avgSleepMinutes(c: Context): Int? {
        val l = load(c)
        if (l.size < 2) return null
        return l.map { it.optInt("min", 0) }.filter { it > 0 }.sorted().let {
            if (it.isEmpty()) null else it[it.size / 2]
        }
    }

    private fun median(l: List<JSONObject>, key: String): Int? {
        if (l.size < 2) return null
        val vals = l.map { it.optInt(key, -1) }.filter { it >= 0 }.sorted()
        return if (vals.isEmpty()) null else vals[vals.size / 2]
    }

    /** now 是否落在 [start, start+len) 这个窗口里（支持跨零点）。 */
    fun inWindow(now: Int, start: Int, len: Int): Boolean {
        val s = ((start % 1440) + 1440) % 1440
        val e = s + len
        val n = if (now < s) now + 1440 else now
        return n >= s && n < e
    }
}

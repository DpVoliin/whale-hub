package dev.dpvoliin.whalecollector

import android.os.BatteryManager
import android.app.usage.UsageStatsManager
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneId

/**
 * 各采集器：每台设备只管"把能拿到的数据映射成中枢的指标名"。
 *
 * 指标名（中枢侧统一）：
 *   screen.active_minutes  今日屏幕活跃总分钟数（meta.top = 用得最多的几个 App）
 *   app.usage_minutes      单个 App 今日使用分钟（meta.app）
 *   calendar.event         日程条目（meta.title/start/end/location）
 */
object Collectors {

    // ---------------------------------------------------------------- 使用时长
    fun hasUsageAccess(c: Context): Boolean = runCatching {
        val appOps = c.getSystemService(Context.APP_OPS_SERVICE) as android.app.AppOpsManager
        val mode = appOps.unsafeCheckOpNoThrow("android:get_usage_stats",
            android.os.Process.myUid(), c.packageName)
        mode == android.app.AppOpsManager.MODE_ALLOWED
    }.getOrDefault(false)

    /** 今日屏幕活跃时长 + Top 应用（UsageStats 全天累计，按包名）。 */
    fun usage(c: Context): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        if (!hasUsageAccess(c)) return out
        val usm = c.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val zone = ZoneId.systemDefault()
        val start = LocalDate.now(zone).atStartOfDay(zone).toInstant().toEpochMilli()
        val now = System.currentTimeMillis()
        val stats = runCatching {
            usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start, now)
        }.getOrNull() ?: return out

        val byPkg = mutableMapOf<String, Long>()
        var total = 0L
        stats.forEach { s ->
            val ms = (s.totalTimeInForeground).coerceAtLeast(0L)
            if (ms > 0) {
                byPkg[s.packageName] = (byPkg[s.packageName] ?: 0L) + ms
                total += ms
            }
        }
        if (total <= 0) return out
        val pm = c.packageManager
        val top = byPkg.entries.sortedByDescending { it.value }.take(6)
        val topArr = JSONArray()
        top.forEach { (pkg, ms) ->
            topArr.put(JSONObject().apply {
                put("app", label(pm, pkg))
                put("pkg", pkg)
                put("minutes", ms / 60000)
            })
            out.add(Hub.metric(P.deviceName, "app.usage_minutes", ms / 60000, "min",
                JSONObject().apply { put("app", label(pm, pkg)); put("pkg", pkg) }))
        }
        out.add(Hub.metric(P.deviceName, "screen.active_minutes", total / 60000, "min",
            JSONObject().apply { put("top", topArr) }))
        return out
    }

    /** 常见应用的中文名（Android 11+ 读不到别人的应用名，只能自己认）。 */
    private val KNOWN = mapOf(
        "com.tencent.mm" to "微信", "com.tencent.mobileqq" to "QQ", "com.tencent.wework" to "企业微信",
        "tv.danmaku.bili" to "哔哩哔哩", "com.ss.android.ugc.aweme" to "抖音", "com.smile.gifmaker" to "快手",
        "com.tencent.qqlive" to "腾讯视频", "com.youku.phone" to "优酷", "com.qiyi.video" to "爱奇艺",
        "com.netease.cloudmusic" to "网易云音乐", "com.tencent.qqmusic" to "QQ音乐",
        "com.chaoxing.mobile" to "学习通", "com.tencent.tmgp.sgame" to "王者荣耀",
        "com.hypergryph.arknights" to "明日方舟", "com.xingin.xhs" to "小红书",
        "com.taobao.taobao" to "淘宝", "com.jingdong.app.mall" to "京东", "com.sina.weibo" to "微博",
        "com.eg.android.AlipayGphone" to "支付宝", "com.ss.android.article.news" to "今日头条",
        "org.mozilla.firefox" to "Firefox", "com.android.chrome" to "Chrome", "cn.wps.moffice_eng" to "WPS",
        "com.tencent.mtt" to "QQ浏览器", "mark.via" to "Via", "com.microsoft.office.word" to "Word",
    )

    private fun label(pm: PackageManager, pkg: String): String {
        KNOWN[pkg]?.let { return it }
        runCatching {
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        }.onSuccess { if (it.isNotBlank() && it != pkg) return it }
        // 读不到就美化包名：com.tencent.wework → tencent.wework
        return pkg.removePrefix("com.").removePrefix("org.").removePrefix("cn.")
    }

    // ---------------------------------------------------------------- 日程

    /**
     * 手机自身状态：电量 / 是否在充电 / 下一个闹钟。
     *
     * 为什么这几个值得采（零权限、零隐私风险，但很有用）：
     *   · 电量低 + 没充电 → 她可以提醒你带充电宝（比"早点睡"更具体）
     *   · 下一个闹钟 → 她据此判断你的作息（"你设了 6:40 的闹钟，那今晚得早点"）
     */

    /**
     * 蓝牙外设的电量（耳机/手表/键盘…）。
     *
     * Android 13+ 可直接问系统（BluetoothDevice.getBatteryLevel()），不需要额外权限；
     * 12 需要 BLUETOOTH_CONNECT，拿不到就**静默跳过**（绝不因为没权限崩）。
     * 地址只做哈希，不存完整 MAC（省得把"你家耳机 MAC"这种东西上报出去）。
     */
    fun bluetooth(c: Context): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        runCatching {
            if (android.os.Build.VERSION.SDK_INT < 33) return emptyList()
            val mgr = c.getSystemService(Context.BLUETOOTH_SERVICE) as? android.bluetooth.BluetoothManager
            val adapter = mgr?.adapter ?: return emptyList()
            @Suppress("MissingPermission")
            val bonded: Set<android.bluetooth.BluetoothDevice> = adapter.bondedDevices ?: return emptyList()
            for (d in bonded) {
                // 用反射拿电池：getBatteryLevel() 是 Android 13+ 才有的，
                // 反射调用就不挑编译 SDK；拿不到（老系统/没权限）就当没有，静默跳过。
                val lvl = runCatching {
                    d.javaClass.getMethod("getBatteryLevel").invoke(d) as? Int ?: -1
                }.getOrDefault(-1)
                if (lvl !in 0..100) continue
                val name = runCatching { d.name ?: "" }.getOrDefault("")
                out.add(Hub.metric(P.deviceName, "bt.battery_percent", lvl.toDouble(), "%",
                    JSONObject().apply {
                        put("name", name.take(24))
                        put("kind", btKind(name, d.bluetoothClass?.majorDeviceClass ?: -1))
                        put("addr", (d.address ?: "").takeLast(5))   // 只留尾 5 位，够区分就行
                    }))
            }
        }
        return out
    }

    private fun btKind(name: String, major: Int): String = when {
        name.contains("耳机") || name.contains("Buds") || name.contains("AirPods") ||
            name.contains("Head") || major == 0x0400 -> "耳机"
        name.contains("手表") || name.contains("Watch") || major == 0x0700 -> "手表"
        name.contains("键") || name.contains("Keyboard") -> "键盘"
        name.contains("鼠标") || name.contains("Mouse") -> "鼠标"
        name.contains("音箱") || name.contains("Speaker") -> "音箱"
        else -> "蓝牙设备"
    }

    fun device(c: Context): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        runCatching {
            val bm = c.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            val pct = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            if (pct in 0..100) out.add(Hub.metric(P.deviceName, "device.battery_percent", pct.toDouble(), "%"))
            val i = c.registerReceiver(null, android.content.IntentFilter(android.content.Intent.ACTION_BATTERY_CHANGED))
            val status = i?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                           status == BatteryManager.BATTERY_STATUS_FULL
            out.add(Hub.metric(P.deviceName, "device.charging", if (charging) 1.0 else 0.0, "",
                JSONObject().apply { put("status", status) }))
        }
        runCatching {
            val am = c.getSystemService(Context.ALARM_SERVICE) as android.app.AlarmManager
            val na = am.nextAlarmClock
            if (na != null) {
                val cal = java.util.Calendar.getInstance().apply { timeInMillis = na.triggerTime }
                val mins = cal.get(java.util.Calendar.HOUR_OF_DAY) * 60 + cal.get(java.util.Calendar.MINUTE)
                out.add(Hub.metric(P.deviceName, "device.next_alarm", mins.toDouble(), "min"))
            }
        }
        return out
    }

    fun calendar(c: Context): List<JSONObject> {
        val out = mutableListOf<JSONObject>()
        val zone = ZoneId.systemDefault()
        val dayStart = LocalDate.now(zone).atStartOfDay(zone).toInstant().toEpochMilli()
        val dayEnd = dayStart + 24 * 3600_000L
        val proj = arrayOf(CalendarContract.Events.TITLE, CalendarContract.Events.DTSTART,
            CalendarContract.Events.DTEND, CalendarContract.Events.EVENT_LOCATION,
            CalendarContract.Events.ALL_DAY)
        runCatching {
            c.contentResolver.query(CalendarContract.Events.CONTENT_URI, proj,
                "${CalendarContract.Events.DTSTART} >= ? AND ${CalendarContract.Events.DTSTART} < ?",
                arrayOf(dayStart.toString(), dayEnd.toString()), "${CalendarContract.Events.DTSTART} ASC")
        }.getOrNull()?.use { cur ->
            while (cur.moveToNext()) {
                if (cur.getInt(4) == 1) continue                       // 全天事件跳过
                val startMs = cur.getLong(1)
                val endMs = if (cur.isNull(2)) startMs else cur.getLong(2)
                out.add(Hub.metric(P.deviceName, "calendar.event", 1, "",
                    JSONObject().apply {
                        put("title", cur.getString(0) ?: "")
                        put("start", hm(startMs))
                        put("end", hm(endMs))
                        put("location", cur.getString(3) ?: "")
                    }))
            }
        }
        return out
    }

    private fun hm(ms: Long): String =
        java.time.Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault())
            .toLocalTime().let { "%02d:%02d".format(it.hour, it.minute) }

    // ---------------------------------------------------------------- 课表
    /** 把岛课表导出的整份 JSON 直接交给中枢（不改造、原样转发）。 */
    fun pushTimetable(c: Context, rawJson: String): Boolean = runCatching {
        LocalTimetable.save(c, rawJson)        // 本地也留一份：判断"是否在上课"要用
        val url = P.hubUrl
        if (url.isBlank()) return false
        val conn = (java.net.URL("$url/timetable").openConnection() as java.net.HttpURLConnection).apply {
            requestMethod = "POST"; connectTimeout = 8000; readTimeout = 10000; doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            setRequestProperty("X-Token", P.token)
        }
        java.io.OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { it.write(rawJson) }
        val code = conn.responseCode
        conn.disconnect()
        code in 200..299
    }.getOrDefault(false)
}

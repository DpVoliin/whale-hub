package dev.dpvoliin.whalecollector

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.util.regex.Pattern

/**
 * 通知监听：读**能自动拿到、且不需要新权限**的三类信息。
 *
 * 1) 健康（vivo 健康推来的心率/血氧/压力/睡眠/步数）
 *    —— vivo 手表数据先同步进「vivo 健康」，它没有第三方接口，但**它会推通知**，通知里带数值。
 * 2) 在听什么（媒体会话，见 Media.kt）
 *    —— 媒体通知一响就顺手报一次曲目。
 * 3) 订单/快递（**只取类型 + 金额区间，不取商品名、不取店铺**）
 *    —— "买了什么"很有价值，但商品名是隐私；所以只报"发货/签收/支付 + 金额区间"。
 *
 * meta.raw 只留给健康类做排障；订单类**绝不写入原文**。
 */
class HealthNotifListener : NotificationListenerService() {

    companion object {
        /** 媒体会话要从服务实例上取（getActiveSessions 是实例方法）→ 留个静态引用。 */
        @Volatile
        var instance: HealthNotifListener? = null
    }

    /** 允许解析健康数字的应用（默认 vivo 健康系；其它 App 一律不看）。 */
    private val healthApps = setOf(
        "com.vivo.health", "com.vivo.healthcare", "com.vivo.watch",
        "com.vivo.smartwatch", "com.vivo.wear", "com.vivo.health.watch",
        "com.vivo.vivohealth", "com.vivo.vivowatch"
    )

    /** 订单/快递：只看这些应用（**不遍历你的所有通知**）。 */
    private val orderApps = setOf(
        "com.taobao.taobao", "com.tmall.wireless", "com.jingdong.app.mall",
        "com.xunmeng.pinduoduo", "com.eg.android.AlipayGphone", "com.tencent.mm",
        "com.sankuai.meituan", "me.ele", "com.suning.mobile.ebuy", "com.taobao.idlefish"
    )

    private val rules: List<Triple<Pattern, String, String>> = listOf(
        Triple(Pattern.compile("心率[^0-9]{0,6}(\\d{2,3})"), "health.heart_rate", "bpm"),
        Triple(Pattern.compile("(\\d{2,3})[^0-9]{0,4}次/分"), "health.heart_rate", "bpm"),
        Triple(Pattern.compile("血氧[^0-9]{0,6}(\\d{2,3})"), "health.spo2", "%"),
        Triple(Pattern.compile("(\\d{2,3})[^0-9]{0,4}%[^0-9]{0,4}血氧"), "health.spo2", "%"),
        Triple(Pattern.compile("压力[^0-9]{0,6}(\\d{1,3})"), "health.stress", ""),
        Triple(Pattern.compile("步数[^0-9]{0,6}(\\d{3,6})"), "steps.total", "步"),
        Triple(Pattern.compile("(\\d{3,6})[^0-9]{0,4}步"), "steps.total", "步"),
    )

    /** 订单类型关键词 → 归一化类型。 */
    private val orderTypes = listOf(
        "签收" to "签收", "已送达" to "签收", "取件" to "派送", "派送" to "派送",
        "派件" to "派送", "发货" to "发货", "已揽收" to "发货", "揽收" to "发货",
        "支付成功" to "支付", "付款成功" to "支付", "已付款" to "支付",
        "扣款" to "支付", "退款" to "退款", "已退款" to "退款",
    )

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    override fun onListenerConnected() {
        super.onListenerConnected()
        instance = this
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        val sbn = sbn ?: return
        val pkg = sbn.packageName ?: return
        val ex = sbn.notification?.extras ?: return

        // 媒体：任何应用的媒体通知 → 顺手报一次"在听什么"（不等采集节奏）
        // 注意：这里该归「曲名开关」管，不是健康开关（之前串了，关掉健康就等于把音乐也关了）
        if (P.musicTitleOn && sbn.notification?.extras?.containsKey(Notification.EXTRA_MEDIA_SESSION) == true) {
            runCatching { Media.metric()?.let { Hub.send(this, listOf(it)) } }
        }

        val text = buildString {
            listOf(Notification.EXTRA_TITLE, Notification.EXTRA_TEXT, Notification.EXTRA_BIG_TEXT,
                Notification.EXTRA_SUB_TEXT, Notification.EXTRA_TEXT_LINES).forEach { key ->
                val v = ex.get(key)
                if (v is CharSequence) append(v).append(' ')
                if (v is Array<*>) v.forEach { if (it is CharSequence) append(it).append(' ') }
            }
        }.toString().trim()
        if (text.isEmpty()) return

        val out = mutableListOf<JSONObject>()

        // ---- 健康 ----
        if (P.healthOn && (pkg in healthApps || pkg.contains("vivo") || pkg.contains("health"))) {
            rules.forEach { (p, metric, unit) ->
                val m = p.matcher(text)
                if (m.find()) {
                    val num = m.group(1)?.toDoubleOrNull() ?: return@forEach
                    out.add(Hub.metric(P.deviceName, metric, num, unit,
                        JSONObject().apply {
                            put("raw", text.take(120))
                            put("from", pkg)
                        }))
                }
            }
            // 睡眠：常见写法「睡眠 7小时32分」「睡了 7 小时」
            val sleep = Pattern.compile("(?:睡眠|睡了)[^0-9]{0,6}(\\d{1,2})\\s*小时(?:[^0-9]{0,3}(\\d{1,2})\\s*分)?").matcher(text)
            if (sleep.find()) {
                val h = sleep.group(1)?.toIntOrNull() ?: 0
                val mm = sleep.group(2)?.toIntOrNull() ?: 0
                val totalMin = (h * 60 + mm).toDouble()
                out.add(Hub.metric(P.deviceName, "sleep.total_minutes", totalMin, "min",
                    JSONObject().apply { put("raw", text.take(120)); put("from", pkg) }))
                LocalSleep.record(this, totalMin)      // 顺手记下：用来推算"你通常几点睡"
            }
        }

        // ---- 订单/快递（只给类型 + 金额区间）----
        if (P.ordersOn && pkg in orderApps) {
            val type = orderTypes.firstOrNull { text.contains(it.first) }?.second
            if (type != null) {
                out.add(Hub.metric(P.deviceName, "order.event", 1.0, "",
                    JSONObject().apply {
                        put("type", type)
                        put("band", amountBand(text))
                        // 注意：**不写 raw**，商品名和店铺不留
                    }))
            }
        }

        if (out.isNotEmpty()) Hub.send(this, out)
    }

    /** 从通知里抠金额 → **只归到区间**（不给精确金额，也不给商品名）。 */
    private fun amountBand(text: String): String {
        val m = Pattern.compile("(?:¥|￥|款项|金额|付款|支付)[^0-9]{0,6}(\\d{1,6}(?:\\.\\d{1,2})?)").matcher(text)
        val v = if (m.find()) m.group(1)?.toDoubleOrNull() else null
        return when {
            v == null -> "unknown"
            v < 50 -> "<50元"
            v < 100 -> "50-100元"
            v < 300 -> "100-300元"
            v < 1000 -> "300-1000元"
            else -> ">1000元"
        }
    }
}

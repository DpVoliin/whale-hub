package dev.dpvoliin.whalecollector

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.content.ContextCompat
import java.util.Calendar

/**
 * 采集前台服务：**节奏自适应**，不再固定 15 分钟。
 *
 * 为什么要自适应（用户原话："采集频率智能根据使用时间来定 / 需要的时候可以频率高点"）：
 *   · 固定 15 分钟 → 你想看的时候数据是旧的；夜里又在白耗电、还容易被系统冻
 *   · 现在按"你在不在用"来定：
 *       屏幕亮着 / 刚解锁      → 5 分钟一轮（而且**屏幕一亮立刻补一轮**）
 *       息屏但还在活动          → 15 分钟一轮
 *       深夜 23:00–07:00        → 60 分钟一轮
 *   · 屏幕亮起 / 解锁是**事件**，不等定时器 —— 你拿起手机的那一刻数据就是新的
 *
 * 健康类数据不在这里：那是通知监听实时抓的（vivo 健康一推就来）。
 */
class CollectorService : Service() {

    private val handler = Handler(Looper.getMainLooper())
    private var screenReceiver: BroadcastReceiver? = null
    private var lastInteractiveAt = SystemClock.elapsedRealtime()   // 上次"点亮/解锁"时刻

    private val tick = object : Runnable {
        override fun run() {
            runCatching { collectOnce() }
            val next = nextIntervalMs()
            P.lastIntervalMinutes = (next / 60_000L).toInt()
            handler.postDelayed(this, next)
        }
    }

    override fun onCreate() {
        super.onCreate()
        P.attach(this)
        ensureChannel()
        showForeground()
        registerScreenEvents()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        handler.removeCallbacks(tick)
        handler.post(tick)
        return START_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        runCatching { screenReceiver?.let { unregisterReceiver(it) } }
        screenReceiver = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** 屏幕亮起 / 解锁 → 立刻采一轮（这是"需要的时候频率高点"的关键）。 */
    private fun registerScreenEvents() {
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, i: Intent?) {
                when (i?.action) {
                    Intent.ACTION_SCREEN_ON, Intent.ACTION_USER_PRESENT -> {
                        lastInteractiveAt = SystemClock.elapsedRealtime()
                        handler.removeCallbacks(tick)     // 立刻采，并把节奏重置
                        handler.post(tick)
                    }
                }
            }
        }
        runCatching {
            val f = IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_USER_PRESENT)
            }
            registerReceiver(r, f)
        }
        screenReceiver = r
    }

    /**
     * 这一轮之后隔多久再采 —— **越"不需要"就越省**（用户要求：不需要的时候频率低点）。
     *
     *   正在上课（按本地课表判断）→ 30 分钟（上课时不折腾）
     *   **睡前**（从你的睡眠记录推出：入睡前 90 分钟 → 入睡后 45 分钟）→ 5 分钟
     *   **早上**（起床后 3 小时，起床时间也是推出来的）→ 5 分钟
     *   睡着期间              → 180 分钟兜底（不是不采，是慢慢来）
     *   其余（白天/下午/晚上） → 30 分钟（不需要，省电）
     *
     * 注意：健康类数据不靠这个节奏 —— 那是通知监听实时抓的，所以"省电"不会漏掉健康事件。
     */
    private fun nextIntervalMs(): Long {
        val cal = Calendar.getInstance()
        val t = cal.get(Calendar.HOUR_OF_DAY) * 60 + cal.get(Calendar.MINUTE)
        // 时段分档（按用户实际需要定的，不是拍脑袋）：
        //   · 早上 06:30–10:00 → 高频：要看睡眠、要看今天几节课、要赶上课提醒
        //   · 睡前 21:30–00:30 → 高频：准备睡了，状态/睡眠数据这时候最重要
        //   · 其余（白天/下午/晚上）→ 低：不需要，省电
        //   · 睡着 00:30–06:30 → 很低：几乎不动
        //   · 上课时段 → 低（人在教室，没什么新信息）
        if (LocalTimetable.inClassNow(this)) return 30 * 60_000L

        // 睡前/早上不是死时间：从你的睡眠记录里推（样本不足才回落 22:30 睡 / 07:00 起）
        val bed = LocalSleep.bedtimeMinutes(this) ?: (22 * 60 + 30)
        val avg = LocalSleep.avgSleepMinutes(this) ?: 450
        val wake = (bed + avg) % 1440
        return when {
            LocalSleep.inWindow(t, bed - 90, 135) -> 5 * 60_000L    // 睡前 90 分钟 → 入睡后 45 分钟
            LocalSleep.inWindow(t, wake, 180) -> 5 * 60_000L        // 起床后 3 小时
            LocalSleep.inWindow(t, bed + 45, (wake - bed + 1440) % 1440 - 45) -> 180 * 60_000L  // 睡着
            else -> 30 * 60_000L                                    // 其余：低
        }
    }

    private fun collectOnce() {
        val items = mutableListOf<org.json.JSONObject>()
        if (P.usageOn) items += runCatching { Collectors.usage(this) }.getOrDefault(emptyList())
        if (P.calendarOn) items += runCatching { Collectors.calendar(this) }.getOrDefault(emptyList())
        if (P.healthOn) runCatching { Media.metric() }.getOrNull()?.let { items.add(it) }   // 在听什么（媒体会话）
        items += runCatching { Collectors.device(this) }.getOrDefault(emptyList())          // 电量/充电/闹钟
        items += runCatching { Collectors.bluetooth(this) }.getOrDefault(emptyList())       // 蓝牙设备电量
        val queued = P.drainQueue()
        if (items.isEmpty() && queued.isEmpty()) return
        Hub.send(this, items + queued)
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CH) != null) return
        nm.createNotificationChannel(
            NotificationChannel(CH, "采集状态", NotificationManager.IMPORTANCE_LOW).apply {
                description = "常驻以持续采集数据（低干扰、静音）"
                setShowBadge(false)
            })
    }

    private fun showForeground() {
        val n = Notification.Builder(this, CH)
            .setContentTitle("鲸鲸采集器")
            .setContentText("正在采集（节奏随使用自动调整）")
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .build()
        runCatching {
            if (Build.VERSION.SDK_INT >= 34)
                startForeground(ID, n, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            else startForeground(ID, n)
        }
    }

    companion object {
        private const val CH = "collector"
        private const val ID = 8801

        /** 所有启动点都包 runCatching：Android 12+ 后台启动前台服务会被拦，不接就崩。 */
        fun start(c: Context) {
            runCatching { ContextCompat.startForegroundService(c, Intent(c, CollectorService::class.java)) }
        }

        fun stop(c: Context) {
            runCatching { c.stopService(Intent(c, CollectorService::class.java)) }
        }
    }
}

/** 开机 / 覆盖安装后自动拉起（不然重启一次就断了）。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context?, intent: Intent?) {
        val ctx = context ?: return
        when (intent?.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> CollectorService.start(ctx)
        }
    }
}

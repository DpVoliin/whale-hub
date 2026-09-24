package dev.dpvoliin.whalecollector

import android.annotation.SuppressLint
import android.app.AlarmManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings

/**
 * 后台保活助手。
 *
 * 为什么需要它：采集器被系统杀掉后，中枢那边就出现数据断档（2026-09-24 实测：
 * 手机侧从凌晨 1 点起 11 小时没有任何数据 ✗）。国内厂商的省电策略各不相同，
 * 而 **vivo 官方给的设置路径是三个独立的开关**：
 *
 *   ① 自启动         设置 > 更多设置 > 应用 > 自启动
 *                    或 i 管家 > 应用管理 > 自启动管理
 *   ② 后台耗电管理   设置 → 该应用 → 电池 → 允许"后台高耗电"
 *                    或 设置 → 电池 → 后台耗电管理 → 找到该应用 → 打开
 *   ③ 电池优化       应用信息 → 电池 → 电池优化 → 选"不优化"
 *   （以上来自 vivo 官方口径，经 dontkillmyapp.com/vivo 收录整理）
 *
 * **只开"自启动"是不够的** ✗ —— 这就是"偶尔生效偶尔不生效"的原因：
 * 自启动管"开机时能不能起来"，耗电管理管"起来后会不会被杀"，两者缺一不可。
 *
 * 本文件做三件事：
 *   · 能程序化的（电池优化豁免、精确闹钟权限）→ 走**官方 API** 直接申请 ✓
 *   · 不能程序化的（自启动、后台耗电）→ **一键跳到厂商那一页** ✓ 让用户点一下
 *   · 状态**如实回报** ✓ 检测不到的就说检测不到（厂商不给查询 API ✗），不假装"已开启"
 */
object KeepAlive {

    /** 电池优化是否已豁免 —— 这个有官方 API 可查 ✓ */
    fun isIgnoringBatteryOptimizations(ctx: Context): Boolean {
        val pm = ctx.getSystemService(Context.POWER_SERVICE) as? PowerManager ?: return false
        return pm.isIgnoringBatteryOptimizations(ctx.packageName)
    }

    /** 申请电池优化豁免（官方 API ✓ 会弹系统对话框 ✓） */
    @SuppressLint("BatteryLife")
    fun requestIgnoreBatteryOptimizations(ctx: Context) {
        try {
            ctx.startActivity(
                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
                    .setData(Uri.parse("package:${ctx.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            openBatteryOptimizationSettings(ctx)   // 退到设置页，让用户自己找
        }
    }

    fun openBatteryOptimizationSettings(ctx: Context) {
        try {
            ctx.startActivity(
                Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            openAppDetails(ctx)
        }
    }

    /** 精确闹钟权限（Android 12+）：没有它，Doze 下定时任务会被推迟几十分钟 ✗ */
    fun canScheduleExactAlarms(ctx: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return true
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as? AlarmManager ?: return false
        return am.canScheduleExactAlarms()
    }

    fun openExactAlarmSettings(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            try {
                ctx.startActivity(
                    Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM)
                        .setData(Uri.parse("package:${ctx.packageName}"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                return
            } catch (e: Exception) {
                // 落到下面的应用详情页
            }
        }
        openAppDetails(ctx)
    }

    /**
     * 跳到厂商的「自启动管理」。
     *
     * 厂商跳转**没有统一 API** ✗ 只能按包名/组件名猜 + 逐个兜底 —— 所以这里是一串
     * 候选，哪个能开就用哪个，全都不行就落到应用详情页 ✓（组件名会随 OriginOS 版本变 ✓）
     */
    fun openAutoStart(ctx: Context): Boolean {
        val candidates = listOf(
            // vivo / iQOO
            ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"),
            ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"),
            ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager"),
            ComponentName("com.vivo.abe", "com.vivo.applicationbehaviorengine.ui.ExcessivePowerManagerActivity"),
            // 通用兜底（部分 ROM 有这个页面）
            ComponentName("com.android.settings", "com.android.settings.applications.ManageApplications"),
        )
        for (cn in candidates) {
            try {
                ctx.startActivity(Intent().setComponent(cn).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                return true
            } catch (e: Exception) {
                continue
            }
        }
        openAppDetails(ctx)
        return false
    }

    /** 跳到「后台耗电管理 / 允许后台高耗电」——vivo 上这是第二个必须开的开关 ✓ */
    fun openBackgroundPower(ctx: Context): Boolean {
        val candidates = listOf(
            ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.PurviewTabActivity"),
            ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager"),
            ComponentName("com.vivo.abe", "com.vivo.applicationbehaviorengine.ui.ExcessivePowerManagerActivity"),
        )
        for (cn in candidates) {
            try {
                val i = Intent().setComponent(cn).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                i.putExtra("packagename", ctx.packageName)
                i.putExtra("pkg_name", ctx.packageName)
                ctx.startActivity(i)
                return true
            } catch (e: Exception) {
                continue
            }
        }
        openBatteryOptimizationSettings(ctx)
        return false
    }

    fun openAppDetails(ctx: Context) {
        try {
            ctx.startActivity(
                Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                    .setData(Uri.parse("package:${ctx.packageName}"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        } catch (e: Exception) {
            // 实在打不开就算了，别崩
        }
    }

    /** 厂商名（给状态行显示用 ✓ 用户看到"vivo"就知道该去哪个管家的设置里找） */
    fun vendorLabel(): String = when {
        Build.MANUFACTURER.equals("vivo", true) || Build.BRAND.equals("vivo", true) -> "vivo / iQOO"
        Build.MANUFACTURER.equals("xiaomi", true) || Build.BRAND.equals("xiaomi", true) -> "小米 / 红米"
        Build.MANUFACTURER.equals("huawei", true) || Build.BRAND.equals("honor", true) -> "华为 / 荣耀"
        Build.MANUFACTURER.equals("oppo", true) || Build.BRAND.equals("realme", true) -> "OPPO / realme"
        else -> Build.MANUFACTURER ?: "未知"
    }

    /**
     * 状态行文案 —— **只写真能查到的** ✓
     *
     * 查不到的（自启动、后台耗电）厂商没给查询 API ✗ 所以如实写"检测不到，请自行确认" ✓
     * —— 假装"已开启"比不写更糟：用户会以为好了，然后数据继续断 ✗
     */
    fun statusText(ctx: Context): String {
        val battery = if (isIgnoringBatteryOptimizations(ctx)) "已豁免 ✓" else "未豁免 ✗（点一下申请）"
        val alarm = if (canScheduleExactAlarms(ctx)) "已允许 ✓" else "未允许 ✗（点一下申请）"
        return "机型：${vendorLabel()}　电池优化：$battery　精确闹钟：$alarm\n" +
            "自启动 / 后台耗电：**系统不提供查询接口**，请在下方跳过去自行确认两项都开 ✓"
    }
}

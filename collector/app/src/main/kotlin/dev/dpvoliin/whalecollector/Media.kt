package dev.dpvoliin.whalecollector

import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import org.json.JSONObject

/**
 * 「在听什么」—— 用已经授权过的**通知监听**拿媒体会话，零新权限。
 *
 * 为什么可行：NotificationListenerService 自带 getActiveSessions()，
 * 而我们的采集器本来就有通知监听权限（读 vivo 健康）。
 *
 * 上报什么：曲名 / 歌手 / 是否在播 —— **不上报进度、不上报播放列表**。
 * 曲名算"中等敏感"：可以关掉（设置页有开关），关掉后只报"在听音乐"。
 */
object Media {

    /** 当前在播的曲目（没有就返回 null）。 */
    fun now(): JSONObject? {
        val svc = HealthNotifListener.instance ?: return null
        return runCatching {
            // 走 MediaSessionManager：同样只需要"通知监听"这一项已授权权限
            val msm = svc.getSystemService(Context.MEDIA_SESSION_SERVICE) as? MediaSessionManager
                ?: return null
            val cn = ComponentName(svc, HealthNotifListener::class.java)
            val sessions: List<MediaController> = msm.getActiveSessions(cn) ?: return null
            // 优先"正在播放"的那个
            val playing = sessions.firstOrNull { s ->
                s.playbackState?.state == PlaybackState.STATE_PLAYING
            } ?: sessions.firstOrNull()
            val ctl: MediaController = playing ?: return null
            val md: MediaMetadata = ctl.metadata ?: return null
            val title = md.getString(MediaMetadata.METADATA_KEY_TITLE)?.trim().orEmpty()
            if (title.isEmpty()) return null
            val artist = md.getString(MediaMetadata.METADATA_KEY_ARTIST)?.trim().orEmpty()
            val isPlaying = ctl.playbackState?.state == PlaybackState.STATE_PLAYING
            JSONObject().apply {
                put("title", if (P.musicTitleOn) title.take(60) else "某首歌")
                if (artist.isNotEmpty()) put("artist", artist.take(40))
                put("playing", isPlaying)
                put("from", ctl.packageName ?: "")
            }
        }.getOrNull()
    }

    /** 打包成中枢指标。 */
    fun metric(): JSONObject? {
        val m = now() ?: return null
        return Hub.metric(P.deviceName, "music.track", 1.0, "",
            JSONObject().apply {
                put("title", m.optString("title"))
                if (m.has("artist")) put("artist", m.optString("artist"))
                put("playing", m.optBoolean("playing"))
                put("from", m.optString("from"))
            })
    }

    /** 媒体是不是正在播（给"在听"这类判断用）。 */
    fun isPlaying(): Boolean = now()?.optBoolean("playing") == true
}

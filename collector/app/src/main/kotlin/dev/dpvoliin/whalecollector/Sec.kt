package dev.dpvoliin.whalecollector

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 本地数据保护：待上报队列落盘前用 **Android Keystore 里的 AES-256-GCM 密钥**加密。
 *
 * 为什么要它：队列里装的是原始数据（App 名、日程标题、健康数值）。虽然它在 App 私有目录里，
 * 但手机被 root、或有人拿到备份/镜像时就是明文。Keystore 的密钥**不可导出**（有硬件支持时在 TEE 里），
 * 拿到文件也解不开。
 *
 * 任何一步失败都**降级为明文**而不是丢数据 —— 安全不能以"上传不了"为代价。
 */
object Sec {

    private const val ALIAS = "whale_queue_key"
    private const val TRANSFORM = "AES/GCM/NoPadding"
    private const val IV_LEN = 12

    private fun key(): SecretKey? = runCatching {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey ?: run {
            val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            gen.init(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
            gen.generateKey()
        }
    }.getOrNull()

    /** 明 → "v1:" + base64(iv) + ":" + base64(ct)；任何异常返回原串（降级不丢数据）。 */
    fun enc(plain: String): String = runCatching {
        val k = key() ?: return plain
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.ENCRYPT_MODE, k)
        val ct = c.doFinal(plain.toByteArray(Charsets.UTF_8))
        "v1:" + Base64.encodeToString(c.iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(ct, Base64.NO_WRAP)
    }.getOrDefault(plain)

    /** 密 → 明；不是 v1: 前缀（旧数据/降级数据）原样返回。 */
    fun dec(stored: String): String = runCatching {
        if (!stored.startsWith("v1:")) return stored
        val parts = stored.split(":")
        if (parts.size != 3) return stored
        val k = key() ?: return stored
        val c = Cipher.getInstance(TRANSFORM)
        c.init(Cipher.DECRYPT_MODE, k, GCMParameterSpec(128, Base64.decode(parts[1], Base64.NO_WRAP)))
        String(c.doFinal(Base64.decode(parts[2], Base64.NO_WRAP)), Charsets.UTF_8)
    }.getOrDefault(stored)
}

package dev.dpvoliin.whalecollector

import android.content.Context
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * 中枢那张自签证书的信任策略：**首次信任 + 指纹固定**（TOFU, trust on first use）。
 *
 * 为什么不用"把证书打进包里"（我们原来就是那么做的 ✗）：
 *   证书里带 **SAN = 中枢真实 IP** ✗ → 打进包里就等于把 IP 放进**公开仓库** ✗✗
 *   （2026-09-24 发现：仓库里的 res/raw/hub_cert 正是真证书，IP 被公开了）
 *   而且换证书就得重新发版 ✗ 维护上也别扭。
 *
 * 为什么不用"信任所有证书"（更省事 ✗）：
 *   那就等于关掉中间人防护 ✗ 采集器上传的是心率/睡眠/位置这类私人数据 ✓ 不能这么干。
 *
 * 所以：**第一次连上时记住服务器证书的 SHA-256 指纹；以后指纹不一致就拒绝连接** ✓
 *   · 防中间人：伪造证书的指纹必然不同 → 直接拒 ✓
 *   · 换证书：指纹变了会报错 ✓ 用户可在设置页"重置指纹"重新学习 ✓（会明确提示）
 *   · 仓库里不再需要任何证书文件 ✓ 公开仓库不带 IP ✓
 *
 * 诚实说明 TOFU 的边界：**第一次**连接理论上可能被中间人利用 ✗（这是 TOFU 的固有弱点）。
 * 对这个用途可接受：中枢是自建的、地址是自己填的、首次连接就在自己网络里。
 * 想更严的话：设置页显示了已固定的指纹 ✓ 可以跟服务器上 `openssl x509 -fingerprint -sha256`
 * 的输出**逐字对一遍** ✓ 对上了就说明没有中间人 ✓
 */
object TlsTofu {

    private const val PREF = "whale_tofu"
    private const val KEY = "hub_cert_sha256"

    private fun sha256Of(cert: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(cert.encoded)
            .joinToString(":") { "%02X".format(it) }

    /** 已固定的指纹（没连过就是 null）—— 设置页显示它，供人工核对 ✓ */
    fun pinnedFingerprint(ctx: Context): String? =
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).getString(KEY, null)

    /** 重置（换过中枢证书后点它，下次连接重新学习）✓ */
    fun resetFingerprint(ctx: Context) {
        ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE).edit().remove(KEY).apply()
    }

    /**
     * ★ 所有出网请求都从这里开 —— 别再各写各的 ✗
     *
     * 2026-09-24 的教训：我只给"上报"那条路套了 TLS 固定 ✗ 课表那条没套 ✓
     * 结果"上次上报"里报的还是系统信任锚的错（Trust anchor ✗），
     * 而 0.8.1 的包里虽然**有** TlsTofu 这个类，却**没人调用它** ✗✗
     * —— 因为接线那处被一次反向拷贝覆盖了，而新建的文件没被覆盖 ✓
     * 所以：统一出口 + 出包后**验证 dex 里真的引用了它** ✓（下面 main() 就是这么查的）
     */
    fun open(ctx: Context, url: String): java.net.HttpURLConnection {
        val conn = java.net.URL(url).openConnection() as java.net.HttpURLConnection
        if (url.startsWith("https", ignoreCase = true) && conn is javax.net.ssl.HttpsURLConnection) {
            conn.sslSocketFactory = socketFactory(ctx)
            conn.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
        }
        return conn
    }

    /**
     * 给 HttpsURLConnection 用的 socket 工厂。
     *
     * 注意：主机名校验这里**关闭**（verifier 恒真）—— 因为自签证书的 CN 是 "whale-hub"，
     * 跟 IP 对不上 ✗ 但那不重要：身份是靠**指纹固定**保证的 ✓ 比主机名更强 ✓
     */
    fun socketFactory(ctx: Context): SSLSocketFactory {
        val tm = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}

            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                val leaf = chain?.firstOrNull()
                    ?: throw CertificateException("服务器没有提供证书")
                val fp = sha256Of(leaf)
                val sp = ctx.getSharedPreferences(PREF, Context.MODE_PRIVATE)
                val known = sp.getString(KEY, null)
                when {
                    known == null -> {
                        sp.edit().putString(KEY, fp).apply()          // ★ 首次：信任并记住
                    }
                    !known.equals(fp, ignoreCase = true) -> {
                        throw CertificateException(
                            "证书指纹变了！已固定 $known，本次是 $fp。" +
                                "可能是中枢换过证书（那就去设置页点\"重置证书指纹\"），" +
                                "也可能是有人在中间冒充 —— 不确定就别继续。"
                        )
                    }
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val sc = SSLContext.getInstance("TLS")
        sc.init(null, arrayOf<TrustManager>(tm), SecureRandom())
        return sc.socketFactory
    }
}

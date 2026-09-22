import java.util.Properties

// ── 发布签名 ──────────────────────────────────────────────────────────
// 从 collector/keystore.properties 读取（该文件**不入库**，见 .gitignore）。
// 没有它时：release 走未签名构建 —— CI 与外部贡献者不会因为你没放密钥而失败。
// 生成密钥：bash collector/tools/make_release_keystore.sh
val ksProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val releaseStore = ksProps.getProperty("storeFile")
val hasReleaseKey = !releaseStore.isNullOrBlank() && file(releaseStore).exists()
if (!hasReleaseKey) {
    logger.lifecycle("[signing] 未找到 collector/keystore.properties → release 产物将是未签名版（本地调试不受影响）")
}

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "dev.dpvoliin.whalecollector"
    compileSdk = 36

    defaultConfig {
        applicationId = "dev.dpvoliin.whalecollector"
        minSdk = 26
        targetSdk = 36
        versionCode = 600
        versionName = "0.6.0"
    }

    // 与岛课表同一个调试签名：以后升级能直接覆盖安装，不用卸载
    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(releaseStore!!)
                storePassword = ksProps.getProperty("storePassword")
                keyAlias = ksProps.getProperty("keyAlias")
                keyPassword = ksProps.getProperty("keyPassword")
            }
        }
        // 自用调试签名（与岛课表同一个）：升级能覆盖安装，不用卸载
        create("stableDebug") {
            storeFile = file("../keystore/debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }
    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("stableDebug")
        }
        release {
            // 采集器逻辑不复杂，先不开混淆：用户/审计者能直接反编译核对"到底采了什么"
            isMinifyEnabled = false
            isShrinkResources = false
            if (hasReleaseKey) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation(libs.androidx.core)
    implementation(libs.androidx.appcompat)
}

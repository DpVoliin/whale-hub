# Android 采集器：发布签名与上架准备

> 目标：能产出**可升级、可上架**的 release 包；同时保证**没有密钥的人（CI、外部贡献者）也不会构建失败**。

## 一、为什么必须用发布签名

调试签名（`debug.keystore`，口令就是 `android`）**只能自己用**：

- 上架会被直接拒（Google Play / F-Droid 都要求说明签名来源）
- **同一包名 + 不同签名 = 无法覆盖安装** → 用户必须卸载重装
- 密钥一旦泄露，任何人都能发布"看起来是你的"升级包

## 二、生成密钥（一次性，5 分钟）

```bash
bash collector/tools/make_release_keystore.sh
```

它会：
1. `keytool -genkeypair` 生成 `collector/keystore/release.jks`（RSA 4096，有效期 30 年）
2. 写好 `collector/keystore.properties`（并 `chmod 600`）
3. 提醒你**备份密钥**

生成的文件都在 `.gitignore` 里（`keystore/`、`keystore.properties`），**不会被提交**。

## 三、构建 release 包

```bash
cd collector
/opt/gradle/bin/gradle :app:assembleRelease
# 产物：app/build/outputs/apk/release/app-release.apk
```

- **有** `keystore.properties` → 用你的发布密钥签名 ✓
- **没有** → 构建仍会成功，但产物是**未签名**的，同时打印一行提示
  （这样 CI 和外部贡献者不会因为你没放密钥而红）

采集器**故意不开混淆**（`isMinifyEnabled = false`）：用户和审计者可以直接反编译核对"到底采了什么"。
这是隐私立场的一部分，不是偷懒。

## 四、F-Droid / 商店的硬要求

| 要求 | 状态 |
|---|---|
| 用发布签名（非 debug）| ✅ 本流程 |
| **仓库里必须有 git tag** | ✅ 见 `git tag`（版本号只递增第三位）|
| 无专有依赖 / 无 GMS | ✅ 只用 androidx 基础库 |
| 隐私政策 | ✅ `docs/PRIVACY.md` |
| 不允许"只能连自家服务器" | ⚠️ **注意**：F-Droid 会把它标成 `Tethered Network Services` anti-feature —— 采集器已支持**自定义服务器地址**（设置页可填），请在上架说明里写明"可自建" |
| 可复现构建 | ✅ 已记录版本（见下方"构建环境基线"）· 依赖锁定待办 |

## 五、密钥丢了怎么办

**没有补救办法**：已发布的包无法再升级，只能换包名重新发布（用户要卸载重装）。
请把 `release.jks` 和口令存在两处（例如密码管理器 + 离线备份）。

## 六、自用调试包

自用仍走 `stableDebug` 签名（与《岛课表》同一把调试密钥）——升级能直接覆盖安装，不用卸载。
这和发布签名互不影响。

## 发版前必须跑：APK 自检（拦"公开仓库版当私包发"）

```bash
export ANDROID_HOME=~/android-sdk
python3 collector/tools/check_apk.py <你的.apk> --host <你的IP>:11443
```

四项：① 证书固定域名是真实 IP（不是 `YOUR_SERVER_IP`）② 默认地址是 `https://<你的IP>:11443`（不是空）
③ 包内固定证书 == 服务器当前证书 ④ 签名指纹（和手机上已装的对照，不一致就得先卸载）。

**为什么写进流程**：这件事真发生过 —— 把公开仓库源码编的包当私包发了出去，
公开仓库里那些值是占位符（不能把你的 IP/口令写进公开库），装上去就是「没有地址 + 认不出证书」，
上报全挂、数据在手机上排队。这类错**长得和正常包一模一样**，只能靠发之前读包里那几处值比一遍。

## 构建环境基线（2026-09-22 实测能出包的那套）

| 组件 | 版本 | 备注 |
|---|---|---|
| JDK | **17.0.20**（OpenJDK） | AGP 9.x 要求 JDK 17+ |
| Gradle | **8.11.1** | 仓库不带 wrapper 时用系统 gradle |
| Android Gradle Plugin | **9.4.1** | AGP 9 **内置 Kotlin**，不再 apply `kotlin.android` |
| compileSdk / targetSdk | **36** | ⚠️ 别写 37：`platforms;android-37` 不存在，写了会 `Failed to find target android-37` |
| minSdk | **26** | Android 8.0 |
| Build-Tools | **36.0.0**（35.0.0 也在） | |
| SDK 平台 | **platforms;android-36** | 必须已安装，否则同上报错 |
| 产物 | `:app:assembleDebug` → 3.59 MB · `:app:assembleRelease` → 未签名 2.74 MB | CI 的 android 任务同样跑这两个 |

**一次能复现的命令**（与 CI 一致）：
```bash
java -version                      # 期望 17.x
gradle --version                   # 期望 8.11.x
gradle :app:assembleDebug          # 产物 app/build/outputs/apk/debug/app-debug.apk
```

**还差的半步（想继续做就照这个来）**：依赖版本目前写在
`collector/gradle/libs.versions.toml` 里但**没有锁定文件** ——
在有网环境执行一次 `gradle --write-verification-metadata sha256 help` 与
`gradle dependencies --write-locks`，把生成的 `gradle/verification-metadata.xml`
与 `*.lockfile` 一起提交，即可从"记录了版本"升级到"依赖字节级可复现"。

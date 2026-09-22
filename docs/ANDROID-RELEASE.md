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
| 可复现构建 | ⚠️ 未做（需要固定依赖版本 + 记录构建环境）|

## 五、密钥丢了怎么办

**没有补救办法**：已发布的包无法再升级，只能换包名重新发布（用户要卸载重装）。
请把 `release.jks` 和口令存在两处（例如密码管理器 + 离线备份）。

## 六、自用调试包

自用仍走 `stableDebug` 签名（与《岛课表》同一把调试密钥）——升级能直接覆盖安装，不用卸载。
这和发布签名互不影响。

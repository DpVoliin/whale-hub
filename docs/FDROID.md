# F-Droid 提交材料（清单 + 自查）

> F-Droid 只收录**能自己构建、无专有依赖**的 App。把这些先备齐，提交（fdroiddata MR）时一次过。

## 一、硬性前置（逐项自查）

| 要求 | 状态 | 说明 |
|---|---|---|
| 仓库里有 **git tag** | ✅ `v0.1.7` / `v0.1.8`… | F-Droid 用 tag 对应 versionName，**必须**有 |
| **不用 debug 签名** | ✅ 已改 | `collector/app/build.gradle.kts` 从 `keystore.properties` 读发布密钥；F-Droid 会**自己重签**，所以这里只要求"不是硬编码的 debug key" |
| 无专有依赖 / 无 GMS | ✅ | 只用 `androidx.core` + `androidx.appcompat` |
| 元数据齐 | ✅ `fastlane/metadata/android/zh-CN/`（title / short_description / full_description / changelogs）| |
| 隐私政策可达 | ✅ `docs/PRIVACY.md` | |
| 可复现构建 | ⚠️ 未做 | 需固定依赖版本 + 记录 SDK/Build-Tools 版本（见下）|
| **`Tethered Network Services` anti-feature** | ⚠️ **会被标** | 见第二节 —— 这是最容易被拒/被标红的一条 |

## 二、最容易踩的一条：Tethered Network Services

F-Droid 会给"**必须连某个服务器才能用**"的 App 标 `Tethered Network Services` anti-feature。
本 App 天然命中：它是采集器，必须把数据送到中枢。

**应对（已做 + 提交时要写清）**：

1. **App 内支持自定义服务器地址** ✅（设置页可填任意地址 + token）→ 说明里写：
   > The app does not depend on any specific server run by the author; it connects to a
   > self-hosted hub the user deploys themselves. There is no official hosted backend.
2. 在 MR 描述里**主动声明**这一点（F-Droid 评审更接受"作者自己先说清"）。
3. 隐私政策里写明"服务器由用户自己部署，作者不接触任何用户数据"。

## 三、fdroiddata MR 要点

```yaml
# metadata/dev.dpvoliin.whalecollector.yml 参考结构
Categories:
  - Health and Fitness
  - Sports and Health
License: MIT
SourceCode: https://github.com/DpVoliin/whale-hub
IssueTracker: https://github.com/DpVoliin/whale-hub/issues
RepoType: git
Repo: https://github.com/DpVoliin/whale-hub

Builds:
  - versionName: 0.6.0
    versionCode: 600
    commit: v0.1.8            # ← 或对应 tag
    subdir: collector
    gradle:
      - yes
    prebuild: echo "no prebuild needed (zero runtime deps)"
    ndk: false

AutoUpdateMode: Version
UpdateCheckMode: Tags
CurrentVersion: 0.6.0
CurrentVersionCode: 600
```

**MR 标题**：`New App: Whale Hub Collector (dev.dpvoliin.whalecollector)`

**MR 描述模板**：
```
- 自托管个人数据中枢的采集器；不连作者的任何服务器（用户自己部署 hub）
- 运行时仅依赖 androidx.core / androidx.appcompat
- 隐私政策：见仓库 docs/PRIVACY.md
- 已知限制：健康数据依赖厂商 App 推送通知（厂商改格式可能失效，已在 README 说明）
```

## 四、提交顺序（别跳步）

```
1. 先做 Android CI 通过（✅ 已有 .github/workflows/android.yml）
2. README 写好"怎么自己部署"（✅）
3. 打 tag（✅）
4. 补 fastlane 元数据（✅）
5. 再提 fdroiddata MR（本文件就是材料）
```

## 五、可复现构建（现在没做，写在这里别忘）

要过 F-Droid 的"可复现"这一关，需要：
- 固定 `gradle-wrapper.properties` 里的 distributionUrl 校验和
- 记录构建用 JDK / SDK / Build-Tools 版本（写进 `docs/ANDROID-RELEASE.md`）
- 关掉会引入时间戳的步骤（`isMinifyEnabled=false` 已经避开了大部分不确定性）

**现状**：采集器**故意不混淆**，产物可反编译核对"到底采了什么" —— 这对隐私叙事是加分项。

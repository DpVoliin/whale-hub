# 变更记录

版本号只递增**第三位**（本项目是自用系统，不做对外兼容承诺）。

## v0.1.8 — 发布就绪与隐私回归

### 修复
- **给模型的上下文里带精确经纬度（真泄漏，被注入式测试抓到）**：`weather_now` 之前直接把
  存储里的 meta 原样塞进上下文，里面带着 `lat/lon`（那是取天气用的坐标，不该给模型）；
  现在改成**白名单**字段（城市/天气/湿度/风/降水/AQI/观测时刻 + 距今多少分钟），
  并且只要检测到存储里有坐标就记一条"进模型前剥掉了什么"。
- **CI 提示漏了一步**：产物等价性检查失败时，提示里只说"跑 build_single.py"，
  但该脚本只写 `hub/dist/hub.py`，**必须再 `cp` 回 `hub/hub.py`** —— 贡献者照着做还是会红。
  提示与 CONTRIBUTING 都补上了这一步。

### 新增
- **`tests/make_fake_history.py`**：合成历史库生成器（固定种子，只在临时目录建库）。
  覆盖 8 类坑：残缺日 / 整段缺失两周 / 连续同值（MAD=0）/ 单日 10 倍突变 /
  跨零点跨月 / 时区偏移 / App 改名 / 超长静默。
- **`tests/test_privacy_injection.py`**：**注入式**脱敏测试（15 条 PII 真的写进临时库，
  再断言它们不出现在 `llm_context()` 里）。比原来"样例不在库里当然搜不到"的泄漏探测强得多。
- **Android 发布签名**：`collector/app/build.gradle.kts` 支持从 `keystore.properties`
  读发布密钥（该文件不入库），没有密钥时 release 走未签名构建（**CI 与外部贡献者不会因此失败**）。
  新增 `collector/tools/make_release_keystore.sh` 一键生成密钥 + 写配置。
- **`docs/ANDROID-RELEASE.md`**：发布签名的完整步骤与"密钥丢失等于无法升级"的提醒。

## v0.1.7 — 开源就绪（工程卫生 + 单文件分发）

### 新增
- **`WHALE_HOME` 环境变量**：配置与数据库的位置不再硬编码。优先级
  `$WHALE_HOME` → 旧部署（`hub.json` 就在程序旁）→ `~/.whale`。
  **升级无感**：已有实例继续用原来的路径，不打扰。
- **单文件分发 `dist/whalehub.pyz`**：`python3 hub/tools/build_zipapp.py` 打成一个
  ~54KB 的可执行文件，用户 `python3 whalehub.pyz` 就能跑，不需要 venv/pip。
  **纯标准库**（标准库 zipapp），不引入 PyInstaller。
- **零依赖护栏 `hub/tools/check_no_deps.py`**：CI 里扫描所有运行时代码的顶层 import，
  出现第三方库就让构建失败。把"零依赖"从口号变成机器守卫。
- **核心算法单元测试 `tests/test_hub_core.py`**：18 个用例，覆盖 `_robust`
  （median+MAD 与 σ 下限）、`_day_series`（残缺日门槛）、`_surprise_of`
  （小样本收缩 + 低可信度收缩 + 单向判定）、`code_fingerprint`、`WHALE_HOME` 解析，
  以及**片段与产物的字节等价断言**。
- **开源标配文件**：`SECURITY.md`（威胁模型 + 已做防护 + 诚实列出已知弱点）、
  `CONTRIBUTING.md`（amalgamation 工作流 + 预提交钩子）、`.gitignore`（隐私数据与密钥）、
  Issue / PR 模板。
- **`pyproject.toml` 补全**：加 `[project]` / `[build-system]`，
  `dependencies = []` —— 机器可读的零依赖声明。

### 修复
- **说话层静默降级（P0）**：主模型（如 `gpt-5.6-luna`）稳定 500 时，此前只会在
  `WHALE_DEBUG` 下打印，用户唯一的感觉是"她今天说话变傻了"。现在：
  1. 支持 `model.fallback_models` 配置，主模型失败自动换备选；
  2. 全部失败时上报 `health.speaker_degraded` 到中枢 —— **用户能看见**。
- **`/opt/whale` 硬编码**：`speaker/whale_voice.py` 的角色卡/配置路径改为
  `WHALE_HOME` 优先、`/opt/whale` 兜底。`whale_voice.py` 现在也认
  `WHALE_CARD` / `WHALE_CONFIG` 单独指定。
- **zipapp 下的 `code_fingerprint()`**：`__file__` 指向压缩包内部时改为从
  zip 读取，不再返回 `"?"`。
- **CI 报错文案**：产物不等价时，直接打印可照抄的修复命令与差异摘要，
  外部贡献者不用猜"该跑哪个脚本"（此前 docstring 路径 `tools/build_single.py`
  与实际 `hub/tools/` 不符）。

## v0.1.6 — 运维与可观测
- **事件级幂等**：采集端带 `event_id` 时按 id 去重（比"值相同 + 60 秒窗口"更严；旧客户端自动兼容）
- **自动备份**：每日 04:00 打包 `hub.db` + 配置，滚存 7 份
- **日志轮转**：单文件超 50MB 压缩归档（此前曾涨到 209MB 无人管）
- **自检**：每日 08:00 查"数据源是否活着 / 她多久没说过话"，异常主动推送
- **脱敏回归测试集**：`tests/test_privacy_regression.py`（PII 样式 + 29 条真实形态样本）
- **报文版本字段**：采集端带 `v` 会记进 meta，日后改字段能判断对面版本

## v0.1.5 — 决策可回放
- **结构化决策日志**：`decisions` 表记"间隔/理由/料分/已说条数/场景桶"（不再只有一行中文）
- **FTS5 检索情节**：`/memory?q=`，中文用 trigram 分词器（≥3 字走 BM25，<3 字退回 LIKE）
- 索引是**可重建的派生数据**：分词方案变了就重建，原始 `episodes` 永不动

## v0.1.4 — 会记、会复盘、会问、能学
- 情节记忆（`episodes`）· 周/月复盘（本期 vs 上期 + 上期结论核对）
- 主动提问（有数据支撑、一天最多一个）· 反馈入口（挂件 ✓/✗ → Thompson 后验）
- 每日上限按命中率重算；后验**跨天保留**（修掉"每天零点清空学习成果"）

## v0.1.3 — 数据健康度与扩展
- 数据健康度（新鲜度/覆盖/可信度 → 直接放大基线收缩）
- 外挂扩展层 `hub/ext/`（一个文件 = 一个数据源，失败隔离）
- 人设包 `personas/`；代码片段化

## v0.1.2 — 决策算法
- 个人基线异常检测（median + MAD + 小样本收缩 + 残缺日门槛）
- 事实层去重（值未跨档不重复播报）· 同类提醒指数退避

## v0.1.1 — 第二批数据源与联网
- 在听什么 / 游戏 / 订单快递 / 电量 / 闹钟 · 联网查询四道闸 · 小设备一行接入

## v0.1.0 — 初版
- 手机采集 → 中枢脱敏 → 主动开口；课表 / 天气 / 简报 / 定点提醒

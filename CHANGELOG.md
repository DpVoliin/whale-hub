# 变更记录

版本号只递增**第三位**（本项目是自用系统，不做对外兼容承诺）。

## v0.1.16 — 高密度合成数据压测 + 反事实调参（外部评审要的"别凭感觉调参"）

### 新增
- **`tests/make_fake_history.py --density real`**：按真实上报节奏造历史（屏幕每 3 分钟、App 每 10 分钟、
  PC/MCU 每 5–10 分钟），180 天 = **41 万行 / 103 MB / 8.8 秒造完**；同时造 decisions 与 feedback，
  供回放/调参用。原来的 sparse 模式保留（每天约 10 条摘要，够测逻辑）。
- **`hub/tools/stress_report.py`**：量库大小/行数/每天密度/索引 · 关键函数耗时（中位数+冷启动）·
  HTTP 接口耗时（真起一个 Handler）· 对照预算给结论。
- **`hub/tools/tune_gap.py`**：**反事实回放调参**。按时间轴重放决策，只用"那一刻之前"收到的反馈构造后验，
  换一组阈值重算"当时会不会开口"，并用**留出法**（前 70% 挑参数、后 30% 验证）防过拟合。
  口径刻意用**接受率**而不是"认可条数"——后者随开口数单调上升，拿它当目标等于"永远推荐多说"。
- **`decisions.ctx` 列**：把做决定时的**输入**（band/material/said/silent/hour）存下来。
  只存结论的话，回放只能靠猜 —— 这是本次最关键的 schema 改动（旧库自动 ALTER 补列）。

### 压测找出的真问题（都是"40 万行才暴露"的）
- **`cat_app()` 是纯函数却被调 14 万次**（每次线性扫分类表）→ 加记忆化。
- **`_cat_day_series()` 把 21 天 14 万行拉回 Python 逐行 `json.loads`** → 改成窗口函数在库内预聚合
  （`ROW_NUMBER() OVER (PARTITION BY day, meta ORDER BY ts DESC)`；老 SQLite 自动退回旧写法）。
  实测 `llm_context()` **2.0s → 0.93s**，`care_now()` 0.62s → 0.45s，`/today` 由"直接断连"变为 0.67s。
  正确性已验：两种算法算出的「类别×天」分钟数**逐项一致**。
- **我自己引入的严重回归（被压测当场抓到）**：`ALTER TABLE decisions ADD COLUMN ctx` 写在
  `executescript` 里 → 第二次启动报 `duplicate column name` → **整个 init_db 中断、中枢起不来**，
  而且它之前的所有建表语句都会跟着失效（`terminals` 表没建成 → `/today` 直接断连）。
  已改为 `executescript` 之外的 PRAGMA 守卫，并**实测"旧库迁移只跑一次、连续启动两次都正常"**。

### 顺带发现（设计层面的真结论）
把阈值从 0.40 扫到 0.80，开口数是从 29.9 条/天 阶跃到 7.3 条/天，而**接受率几乎不动** ——
因为后验一旦收敛，`UTIL_THRESHOLD` 就只剩两档作用（"总是开口" / "仅料足才开口"）。
**它是个粗开关，不是频率旋钮**；真正的频率控制在 `next_gap`（时间带 × 各因子）。
想细调频率要动的是那边，不是阈值。

## v0.1.15 — 修一个"页面能打开但全是坏的"老 bug（/dash 与 Web 管理台）

- **`_send()` 对字符串也做 `json.dumps`** → HTML 被包成 `"\"<!doctype html>…\n<meta …>\""`：
  前导多一个引号、**真换行变成字面量 `\n`**、CSS 里的 `"Segoe UI"` 被转义成 `\"`（字体失效）。
  现在按类型分派：bytes 原样 / str 直接 utf-8 / dict·list 才 JSON。
  **`/dash` 从写出来那天起就是坏的**，管理台也中招 —— 这类 bug 只有"在浏览器里真看一眼"才会暴露，
  所以顺手补了 `TestWebPages` 回归测试（断言 str 不被 JSON 编码、页面里没有字面量 `\n`、
  表单会填入当前配置值）。
- `android` 工作流：构建失败时把 Gradle 报错**摘成 check-run 注解**。原因：Actions 原始日志
  要仓库权限才读得到，而注解是公开可读的 —— 外部贡献者不用再贴截图。

## v0.1.14 — 更名为 whalecare（鲸鲸）

- **项目更名：`whale-hub` → `whalecare`**。理由：这套系统早就不是"一个 hub"了 ——
  现在是**数据中枢 + 会自己判断该不该开口的伴侣**，`hub` 只描述了其中一个组件。
  "care" 正好是它的核心行为（主动关心）。
- 连带改动：`pyproject` 的 name/urls/script、docker service 与镜像名、SBOM vendor、
  自签证书 CN、扩展 User-Agent、`dist/whalecare.pyz`（原 whalehub.pyz）、
  以及 README/docs/模板里的全部引用（27 个文件）。片段目录 `hub/src/whalehub/` → `hub/src/whalecare/`。
- **刻意不改的两处**（它们是**运行时键**，改了会直接断功能）：
  `speaker/whale_speaker.py` 里的 `WEBHOOK_URL .../webhooks/whale-hub`（网关侧注册好的路由）
  与 `SECRET_FILE .whale_hub_secret`（已存在的 HMAC 密钥文件）。`hubctl` 命令名同样保留
  ——"中枢"确实仍是个数据 hub，改它要连服务器上的软链一起动。
- 旧仓库地址 `DpVoliin/whale-hub` 由 GitHub 自动 301 跳转到新名，star / issue / 链接都不丢。
- README 首屏补了一句**英文定位语** + GitHub topics（20 个）——中文项目在英文检索里
  最大的短板是"没有可被搜到的英文描述"，这次一并补上。

## v0.1.13 — 审计日志 / 一次性配对码 / Web 管理台 + CI 修复

### 新增
- **审计日志 `audit` 表**（P2）：记录**鉴权失败 / 配置修改 / 导出与备份 / 扩展加载报错 / 配对 / token 轮换**。
  设计红线：**只记「动作 + 对象 + 结果」，不记数据内容** —— 所以它可以直接给人看、可以外发。
  三处可读：`hubctl audit [--stats]` · `GET /audit` · 管理台里的表格。
- **一次性配对码**（P1）：`hubctl pair --device stm32_room` 生成短码（15 分钟、**用过即废**）；
  设备/中继拿它换一次 token（`GET /api/pair?c=码&d=设备名`，回一行纯文本，单片机直接读）。
  不再需要把长期明文口令写在设备里。
- **Web 管理台**（P2）：标准库拼 HTML，**不引任何前端框架**（守住零依赖）。数据源健康度 /
  决策日志 / 审计 / 开关（关心、隐私、规则） / 改人设 / 生成配对码 / 扩展状态。
  鉴权与 API **完全同一套 token**：登录页把 token 换成 HttpOnly + SameSite=Strict 的会话 cookie，
  轮换 token 即废掉所有旧会话；API 侧仍然只认 `X-Token` 头。删除/导出这类破坏性动作**刻意只在 CLI**。

### 修复（都是真 bug，其中两个让"已宣布完成"的功能在静默失效）
- **`/bands` 只挂在 POST**：说话层无 body 时走 GET → 永远 404 → 分桶 Thompson **一直退回全局后验**。
  改成 GET（读类接口本就该 GET），POST 保留兼容。
- **说话层 `TZ` 未定义**：断点投递（v0.1.10 上线的四项文献算法之一）在异常里静默失效，从未生效。
- **中枢缺 `import pathlib`**：`state.json`（调度状态落盘）读写抛 NameError → 重启防重复/防漏发一直没生效。
- **`whale_web` 用 `lstrip("www.")`**：按字符集剥，"www.weibo.com" 会被剥成 "eibo.com"（域名比对错）。改正则。
- **`mcu_relay.py` 缺 `import pathlib`**：文件一导入就 NameError —— 中继**从来跑不起来**。
- 顺手清掉 ruff 抓出的 9 处未用变量/导入、`zip(strict=)`。

### 变更（安全）
- **MCU 中继强制 HTTPS + 真正的证书固定**：不再用 `create_default_context`（它会**叠加系统根 CA**，
  等于公共 CA 也能伪造）—— 改成只信任 `WHALE_CA` 这一张证书；另支持 `WHALE_PIN=<sha256 指纹>`
  逐字节比对（实测指纹不符会被明确拒绝并报"疑似中间人"）。

### 修复（CI —— 此前 36 次运行**全部失败**，与 dependabot 无关）
- **`ci` 的 ruff 从未通过**：配置里的 `UP` 那组规则要求重写全库刻意的 %-格式化（194 处）等，
  实测 1012 处违规；且把片段源码当独立模块 lint，光假阳性 F821 就 645 处。
  → 规则收窄到 `E,F,W,I,B`（理由写在 `pyproject.toml` 注释里），**改为 lint 合并产物 `hub/hub.py`**
  （片段本就是半成品）。现在 ruff 本地全绿，并因此抓出上面两个真 bug。
- **`android` 从未构建成功**：runner 自带 Gradle **9.7.1**，而 AGP 8.9.2 只支持 Gradle 8.x，且仓库里
  没有 wrapper → 用系统 gradle 必挂。→ 显式钉 `gradle-version: 8.13`（顺带让产物可复现），加 `--stacktrace`。
- **`android` 引用了不存在的签名文件**：`keystore/debug.keystore` 不入库，AGP 校验签名时直接失败
  → 改成"文件存在才启用自用签名，否则退回 AGP 默认 debug 签名"。
- **`scorecard` 引用了已删除的标签** `github/codeql-action/upload-sarif@v3`（上游只剩 v4.x）→ 升 v4；
  Scorecard 本体是 Docker 容器（镜像从 ghcr.io 拉，实测 pull 失败）→ 标为**不阻断**并写明理由（它只是体检报告）。
- 工作流里的 action 统一升到当前主版本（`checkout@v7` / `setup-python@v7` / `setup-java@v6` /
  `cache@v6` / `upload-artifact@v7` / `gradle/actions@v6`）：Node 20 已进入强制迁移期。

### 变更（杂项）
- `VERSION` 从 `0.1.0`（早已过时）对齐到 `0.1.13`；`pyproject.toml` 的 `version` 同步（原来停在 0.1.6）。

## v0.1.12 — P2 传播与合规材料 + 采集器健康开关

### 新增（文档）
- **`docs/DEPLOY-GUIDE.md`**：部署图文教程（含权限逐个说明 + **常见报错对照表**）
- **`docs/DEMO-SCRIPT.md`**：2 分钟 demo 视频分镜（第一幕就是"她主动发来一条有用的消息"）
- **`docs/AWESOME-SUBMISSIONS.md`**：可粘贴的收录/推广文案（awesome-selfhosted YAML 条目、
  V2EX/少数派/Reddit 标题与正文），并在文末重申"不做买 star/刷榜"
- **`docs/FDROID.md`**：F-Droid 提交材料与自查表，重点写了最容易被标的
  `Tethered Network Services` anti-feature 怎么应对

### 变更（采集器 0.7.0）
- **健康数据开关默认改为「关」**：心率/血氧/压力属 GDPR 特殊类别数据，
  必须**显式同意**（设置页文案写明"打开即表示你同意采集"），不再靠系统权限弹窗代替同意。
- **修一个串门的门禁**：媒体通知（在听什么）此前被**健康开关**管着 ——
  关掉健康数据等于把音乐也关了。现在三段各归各位：音乐→曲名开关、健康→健康开关、订单→订单开关。

## v0.1.11 — 分桶 Thompson（P2 #32）

- **中枢 `GET /bands`**：把反馈按"场景桶"（星期×时段）分组估 `p(接受)`；
  **每桶样本 <4 条就标 `reliable=false`** —— 文献（EOPA arXiv:2608.04416）强调
  稀疏反馈要先分桶，但桶本身也要够样本，否则"一次运气就改阈值"。
- **说话层的期望效用 gate 优先用桶后验**（`_band_posterior()`，5 分钟缓存），
  桶不可靠时自动退回全局后验；决策理由里会标出来源（`[桶 工作日·早上]` / `[全局]`），
  复盘时能看清"这次是靠哪个后验做的决定"。

## v0.1.10 — 说话层接入文献算法（P2 #31）+ 两个真 bug

### 新增（4 项算法，都有出处）
- **断点投递**（Iqbal & Bailey 2007）：用 `screen.idle_minutes ≤ 2` + 上报很新 判断"人刚拿起手机"，
  那是天然断点 → 允许把间隔压到 0.6 倍。拿不到信号就不假装（只是不享受加成）。
- **Goldilocks 时间窗**（arXiv:2504.09332，MIT Media Lab 2025）：每个话题有自己的时段
  （睡点只在晚上、带伞只在早上、游戏盘点在下午到夜里）→ **发送前**检查，不在窗内就不说。
  定点提醒/上课/紧急走另一条路，不受影响。
- **期望效用 gate**（Horvitz 1999 CHI）：开口 iff `p(接受) > C_落空/(C_落空+C_漏报)`，
  `p(接受)` 用反馈的 **Beta 后验**估计（不再拍脑袋）；后验偏低时只有"料 ≥3"才允许开口。
  成本参数可用 `WHALE_C_MISS` / `WHALE_C_FALSE` 调。
- **打扰仪表盘**：`hubctl interruption` —— 开口率、接受率、钟点分布、最近 5 条理由。

### 修复（都是跑起来才暴露的真 bug）
- **决策日志一直是 0 条**：说话层那段上报代码写在 `return` **之后**（死代码），
  而且引用了两个没定义的变量。现在挪到正确位置并抽成 `_log_decision()`。
- **中枢根本没有 `POST /decision` 路由**：`decisions` 表和数据写入函数都在，
  但路由没接（CHANGELOG 声称的链路其实只通了一半）→ 补上并实测（POST 200 → GET 能查到）。
- `hubctl` 不认 `WHALE_HOME`（中枢认）→ 改为三级优先级对齐：`WHALE_DB` → `$WHALE_HOME/hub.db` → 脚本旁边。

## v0.1.9 — 走完成熟项目清单（数据主权 · 工程化 · 传播前置）

### 新增
- **`GET /export` / `POST /erase`**：数据可携带与物理删除（GDPR Art.20/17）。
  `?redact=1` 顺手脱敏（App 名/原文/坐标都剥掉）；删除必须显式 `confirm=ERASE-ALL`，
  删前自动备份、删后 VACUUM。**4012 行导出实测 0.04 秒**。
- **Python 3.11/3.12/3.13 矩阵 + 覆盖率门槛 + SBOM 步骤**（零依赖清单短到能人工通读）
- **OpenSSF Scorecard** workflow · **Android CI**（此前完全没有）· **dependabot**
- **Dockerfile + docker-compose.yml**（一键起中枢，数据挂 `./data`，删容器不删数据）
- **Fastlane 元数据**（zh-CN / en-US）· **`docs/SCHEMA.md` / `LOCAL-FIRST.md` / `ANDROID-RELEASE.md`**
- **README 30 秒上手 + 徽章 + 数据主权说明**

### 修复
- `/export` 遇 BLOB 字段会 500（bytes 不能 JSON 序列化）→ 转 base64；并跳过 FTS 影子表
- CI 的 `setup-python` 写死 3.11，加了矩阵却没生效 → 改为 `${{ matrix.python }}`

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
- **单文件分发 `dist/whalecare.pyz`**：`python3 hub/tools/build_zipapp.py` 打成一个
  ~54KB 的可执行文件，用户 `python3 whalecare.pyz` 就能跑，不需要 venv/pip。
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

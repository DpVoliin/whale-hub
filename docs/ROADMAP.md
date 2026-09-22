# 路线图

## 已有

- [x] 中枢：HTTP + SQLite + 规则引擎 + 脱敏 + 定点提醒 + 简报 + 状态页
- [x] 采集器（Android）：屏幕用量（按分类）、日程、健康通知监听、Keystore 加密队列、开机自启
- [x] 自适应采集频率（按"在用 / 上课 / 推算作息"分档 + 亮屏事件触发）
- [x] 说话层：角色卡 + few-shot + 现场生成 + 事实指纹去重 + 质量闸 + 模板回落
- [x] 主动判断：**节奏自适应**（按数据算 5–90 分钟，有料/在用/连续沉默都会影响）；免打扰 23:00–07:00
- [x] 睡前小总结（时间点由睡眠数据推算）
- [x] 微信出口（网关 webhook 直投，HMAC 签名，零 LLM 成本）

## 进行中 / 下一步

- [x] **第二批数据源**：在听什么 / 游戏 / 订单快递 / 电量充电 / 下一个闹钟 / 天气
- [x] **联网查询**：本地没知识时她自己搜一次（域名信任分级 + 害词拦截 + 注入剥离 + 少于 2 条可信就不说）
- [x] **命令行工具 hubctl**：读数据 / 只读 SQL / 导出（可脱敏）/ 合并导入 / 备份还原
- [x] **节奏自适应**：提醒频率按数据算（有料/在用/连续沉默/今日条数）
- [x] **蓝牙外设电量**：耳机/手表低电提醒
- [x] **小设备接入**：`/api/mcu` 一行上报 + 内网中继（STM32/C51）
- [x] **个人基线异常检测**：median + MAD 稳健 z、小样本收缩、残缺日门槛（"值不值得说"改为相对自己历史）
- [x] **事实层去重**：状态没跨档就不重复播报（对治"同一件事换 N 种说法"）
- [x] **同类提醒指数退避**：久坐 50/100/200/400 分钟，上限 4 条/天
- [x] **数据健康度**：每个源的新鲜度/覆盖/可信度，可信度直接放大基线收缩（数据薄就不敢下结论）
- [x] **反馈入口**：挂件 ✓/✗ → `/feedback` → Thompson 后验；每日上限按命中率重算
- [x] **情节记忆**：`episodes` 表留痕她说过的、发生过的；进模型的只有检索出的摘要
- [x] **周/月复盘**：本期 vs 上期 + 读上期结论核对改善
- [x] **主动提问**：有数据支撑的问题，一天最多一个，问过不重复
- [x] **一键部署**：`tools/onestep_deploy.sh`（token/证书/守护/自检/打印配置，幂等）
- [x] **人设包**：`personas/<名字>/{persona.json,card.json}`，两侧读同一份，可切换
- [x] **代码模块化**：`src/whalecare/` 片段 + `tools/build_single.py` 合并成单文件（字节等价）
- [x] **PC 采集器**：电脑使用时长、类别分钟、久坐提醒、系统盘/内存/开机时长/窗口切换次数（同一套 `/ingest` 契约）
- [x] **安全加固**：中继强制校验证书、原文默认不落库、备份可加密（AES-256）、防火墙收口、
      单片机序号 + 校验和、中继失败补发、调度状态落盘、自动保留策略、独立 MCU token
- [x] **桌面挂件 / PC 采集器**已开源（`desktop/`：透明置顶角色 + 电脑使用时长采集）
- [x] **桌面常驻角色**：Windows 挂件（透明置顶 + 拖动吸附 + 点她出气泡 + 换形象换表情），见 `desktop/`
- [x] **Thompson 采样节奏**：分桶 Beta-Bernoulli + 采样决定间隔；**真反馈已接**
      （挂件 ✓/✗ → `/feedback` → 后验，每日上限按命中率重算，后验跨天保留）
- [ ] **更细的场景桶**：现在主要累积在全局桶，等反馈量上来后按"星期类 × 时段带"细分自动生效
- [ ] **核心模块回归测试**：把基线/惊讶度、事实去重、指数退避、每日上限、复盘、扩展加载
      逐块补上 `unittest`（模块化后按 `src/whalecare/` 片段组织，不引任何依赖）
- [x] **审计日志**：`audit` 表，记录鉴权失败 / 配置修改 / 导出与备份 / 扩展加载报错 / 配对 / token 轮换；
      只记动作与对象，**不记数据内容**（v0.1.13）
- [x] **MCU 中继 TLS + 一次性配对码**：强制 HTTPS + 只信中枢那张证书（+可选 sha256 固定），配对码用过即废（v0.1.13）
- [x] **Web 管理界面**：标准库拼 HTML（零前端依赖），数据查看 / 开关 / 改人设 / 看扩展 / 审计 / 生成配对码；
      与 API **同一套 token**（登录换 HttpOnly cookie，破坏性动作仍只在 CLI）（v0.1.13）
- [ ] **说话策略也可外挂**：现在扩展层管数据源、人设包管人设，下一步把节奏与取舍
      （`next_gap` / `material_score`）开成可替换策略
- [ ] **macOS 采集器**：窗口切换、开机时长、前台 App 分类（契约与 `/ingest` 一致）
- [ ] **可选 Docker 部署**：`deploy/Dockerfile` 作为附属方案（主线仍是"一个文件 + 一条命令"）
- [ ] **CI**：推送时跑编译 + 结构断言 + 回归测试（复用仓库里已有的检查脚本）
- [ ] **部署教程补齐**：界面截图、常见报错对照表、安卓权限授予图文步骤
- [ ] **手表真实健康数据**：依赖厂商健康 App 推通知（四级降级：Health Connect → 通知监听 → 无障碍 → 截图 OCR 兜底）
- [ ] **手机上跑 agent**：Termux + proot 里跑本仓库（Python/Linux 程序，理论可搬）
- [ ] **更多出口**：企业微信（官方接口无限流）、Telegram、Discord

## 扩展方式（给别人接自己的设备）

任何设备只要能 `POST /ingest` 就能接入，中枢代码不用改：

```json
[{"device": "phone_x", "metric": "screen.active_minutes", "value": 260},
 {"device": "laptop_x", "metric": "app.usage_minutes", "value": 96,
  "meta": {"app": "VS Code", "pkg": "code"}}]
```

指标字典（当前）：
`sleep.total_minutes` / `sleep.deep_minutes` / `health.heart_rate` / `health.spo2` /
`health.stress` / `steps.total` / `screen.active_minutes` / `screen.idle_minutes` /
`app.usage_minutes` / `calendar.event` / `task.todo` / `music.track` / `order.event` /
`device.battery_percent` / `device.charging` / `device.next_alarm` / `weather.day` /
`temp` / `hum`（单片机）/ `pc.continuous_active_minutes` / `pc.disk_free_percent` /
`pc.mem_percent` / `pc.uptime_hours` / `pc.window_switches_today`

> 口径提醒：同一时空里**累计值**（当天累计分钟数、当天窗口切换次数）一律"取当天最新"，
> **不要 SUM** —— 设备是每隔几分钟把"今天到目前为止"重报一次的。

## v0.1.9 · 走完路线图（P0/P1/P2 主体）
- [x] 数据主权端点 `/export` `/erase`（GDPR Art.20/17，实测 4012 行导出 0.04s）
- [x] Python 3.11/3.12/3.13 矩阵 · 覆盖率门槛 30% · SBOM（零依赖清单）
- [x] OpenSSF Scorecard · Android CI（此前完全没有）· dependabot
- [x] Docker Compose 一键部署 · Fastlane 元数据（zh-CN/en-US）
- [x] `docs/SCHEMA.md` · `docs/LOCAL-FIRST.md` · `docs/ANDROID-RELEASE.md`
- [x] README 30 秒上手 + 徽章 + 数据主权说明
- [ ] PyPI 发布（等你的账号做 Trusted Publishing）· 签名 Release（等你生成签名密钥）
- [ ] demo 视频 / 截图（需要真机画面）· awesome-selfhosted 提 PR · F-Droid 提交

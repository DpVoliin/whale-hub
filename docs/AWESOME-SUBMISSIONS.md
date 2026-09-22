# 收录/推广：可以直接粘贴的文案

> 用法：下面每一段都是**成品**，贴到对应的地方就行。**不要**群发、不要刷榜、不要买 star ——
> 这条在路线图里写死了，做了会毁掉"自己动手做的"这个最值钱的叙事。

---

## 1. awesome-selfhosted（★320k，投入产出比最高）

提 PR 到 `awesome-selfhosted/awesome-selfhosted-data`（新流程：改 YAML 而不是 README）。

**条目（`software/whalecare.yml` 草稿）**

```yaml
name: "whalecare"
website_url: "https://github.com/DpVoliin/whalecare"
source_code_url: "https://github.com/DpVoliin/whalecare"
description: "Personal life-data hub: an Android collector + a single-file zero-dependency Python
  server that stores your data locally, sanitizes it before any LLM sees it, and proactively
  messages you (WeChat) with the one thing worth saying. Ships a Windows desktop widget and
  a ~20-byte UDP ingest for STM32/C51 sensors."
licenses:
  - MIT
platforms:
  - Python
  - Android
  - Windows
tags:
  - Analytics
  - Internet of Things (IoT)
  - Personal Dashboards
  - Quantified Self
  - Groupware
  - Automation
depends_3rdparty: false
demo_url: ""
```

**PR 标题**：`Add whalecare (personal life-data hub, zero-dependency Python + Android collector)`
**PR 正文**：一句话说明 + 强调"zero runtime dependencies / self-hosted only / no cloud"。

> ⚠️ 提交前自查：仓库要有 LICENSE ✓、能跑起来 ✓、README 有快速开始 ✓（都有了）。

## 2. GitHub 仓库 topics（一行命令，需 token）

```
analytics · android · automation · local-first · personal-data · privacy · proactive-agent
python · quantified-self · self-hosted · sqlite · zero-dependencies
```
（`docs/LOCAL-FIRST.md` 里解释了为什么 local-first 是准确描述，不是蹭词。）

## 3. V2EX「分享创造」帖（中文社区转化率最高）

**标题**：`[分享创造] 我做了个只住在我自己服务器里的 AI 助手 —— 她知道我几点睡、在听什么，也知道该闭嘴`

**正文开头三段**（后面直接贴 README 的"她长什么样" + 架构图）：

> 我烦的不是 AI 不够聪明，是它永远等我先开口。
>
> 于是做了这个：手机端只当"身体"（采屏幕时长/日程/健康通知/在听什么/电量），
> 大脑在我自己的 4C4G 上（一个单文件零依赖的 Python），她判断"现在值不值得说"，
> 值得说就用微信发我一句 —— 比如"（算了算）明日方舟 72 分钟；明早 8 点那节课，别熬"。
>
> 进模型前强制脱敏：App 名只留分类、通知原文只留数值、位置只到城市级，
> 还有个 `/llm-preview` 让你随时核对"模型到底看到了什么"。MIT 开源。

**结尾**：仓库地址 + "想知道你们的场景是什么，评论区聊"（**提问式结尾**评论量明显更高）。

## 4. 少数派 / 掘金长文（适合发完 V2EX 一周后）

标题方向（选一个）：
- 《我给自己做了个"会主动关心"的本地助手，顺便把隐私做成了可核对的》
- 《零依赖、单文件、4C4G：一个自托管个人数据中枢的工程记录》

正文结构：**痛点 → 她的三条真实消息 → 架构一图 → 我踩的 7 个坑（技术文最受欢迎的部分）→ 怎么自己部署**。

## 5. r/selfhosted / r/LocalLLaMA（需网络条件，读版规）

**Title**: `whalecare: I built a proactive personal assistant that lives on my own 4C4G box (zero deps, single-file Python; data never leaves home)`

**Body**: 3 段 —— ① it DMs me ONE useful thing instead of dumping data ② sanitization is enforced in code, `/llm-preview` proves it ③ zero runtime deps + one-file deployment. 结尾放仓库 + license。

> Show HN 类似，但标题要更长更具体：`Show HN: A self-hosted assistant that messages me one useful thing a day`。

---

## 现实预期（写在路线图里，别骗自己）

**第一个月 10–50 star、2–5 个真实 issue 就算成功。** 爆发来自一篇文章 / 一个 awesome 收录 / 一个视频，
**不来自**发得多。所以：**先把 README 的 30 秒上手打磨顺**（已完成 ✓），再去发。

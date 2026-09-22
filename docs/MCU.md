# 让 STM32 / C51 这类小设备接入

> 目标：一块十几块钱的单片机 + 一个网口模块（ENC28J60 / W5500 / ESP-01 / ESP8266），
> 能把传感器读数**一行发上来**，然后就出现在她（AI）能看到的数据里。

## 架构：为什么要一个"中继"

```
  单片机(明文,一行)                内网中继(mcu_relay.py)              中枢(HTTPS)
  STM32 / C51  ──HTTP 或 UDP──▶   转发 + 限流 + 日志      ──HTTPS──▶  /api/mcu → SQLite
```

**取舍讲清楚**：
- 单片机**解析不了 JSON**、**做不了 TLS**（C51 上跑 TLS 基本不可能，STM32 也很吃力）
- 但中枢**必须**走 HTTPS —— 明文把 token 丢在公网上等于送人
- 所以：设备侧越简单越好（一行明文），**TLS 交给中继**。中继跑在你家里任何一台常开设备上
  （路由器 / 树莓派 / 旧笔记本 / NAS ✓，Windows 也能跑）

## 协议（含抗丢包：序号 + 校验和）

```
GET /mcu?d=stm32_room&m=temp,hum&v=25.3,61&u=C&s=1024&c=173
                                                     │        └─ c：校验和（可选）
                                                     └─ s：递增序号（可选，识别重发）
```
- **校验和算法**（C 里一行就能算）：`设备名 + 指标串 + 数值串` 每字符 ASCII 相加，取 `% 256`
- 中枢验不过回 `err:crc`（脏数据不入库）；带 `s=` 时序号写进 meta，便于排查丢包/重发
- UDP 同理：`"dev,temp:25.3,seq:1024,crc:173"`

## 设备侧：两种发法（任选）

### A. HTTP 一行 GET（有 TCP 能力的模块都行）
```
GET /mcu?d=stm32_room&m=temp,hum&v=25.3,61&u=C HTTP/1.0

```
- `d` 设备名 · `m` 指标（逗号可并列）· `v` 数值（与 m 一一对应）· `u` 单位
- 回一行文本：`ok` 或 `err:xxx`（**不用解析 JSON**）

### B. UDP 一行（最短，C51 首选）
```
发送 "stm32_room,temp,25.3"                  ← 单指标，20 字节左右
发送 "stm32_room,temp:25.3,hum:61"           ← 一条报多个
接收 "ok"
```

## 中继部署（3 行）

```bash
export WHALE_HUB=https://YOUR_SERVER_IP:11443
export WHALE_TOKEN=你的_token
export WHALE_CA=/opt/whale/hub/tls/hub.crt   # 自签证书就给这个；没有可省
python3 mcu_relay.py                          # HTTP :8088 + UDP :8089
```

中继自带：**强制校验证书**（不给 CA 直接拒绝启动）、一分钟 600 次软限流、
每条转发打一行日志（`设备 → 指标=值 ✓`）、**转发失败先落盘、后台每 20 秒退避补发**。

```bash
export WHALE_CA=/opt/whale/hub/tls/hub.crt   # 必须给；跳过须显式 WHALE_INSECURE=1（仅调试）
export WHALE_QUEUE=/var/lib/mcu-queue.jsonl  # 可选：失败队列落盘位置
```

## STM32 示例（HAL + ESP-01 AT 指令）

```c
// 1) 拼好一行（别用 sprintf 的浮点，C51/STM32 都容易踩坑）
char req[96], cmd[128];
int t10 = (int)(temp * 10);              // 25.3℃ → 253
sprintf(req, "GET /mcu?d=stm32_room&m=temp&v=%d.%d&u=C HTTP/1.0\r\n\r\n",
        t10 / 10, t10 % 10);

// 2) 用 AT 指令把这一行发出去
AT+CIPSTART="TCP","192.168.1.10",8088    // 中继的 内网IP:HTTP端口
AT+CIPSEND=%d                            // 长度 = strlen(req) + 2
<把 req 发出去>
AT+CIPCLOSE
```

> 用 W5500/ENC28J60 就直接开 socket 发上面那行，更省事。

## C51 示例（STC + ESP8266 AT，UDP 版最短）

```c
// 一条 UDP 报：dev,metric,value
char pkt[48];
int t10 = temp_x10;
sprintf(pkt, "c51_node,temp,%d.%d", t10 / 10, t10 % 10);

AT+CIPSTART="UDP","192.168.1.10",8089
AT+CIPSEND=<长度>
<pkt>
```
UDP 的好处：不用建连、不用等响应，**发完就睡**，最省电。

## 指标名怎么起

中枢不限制指标名（`/api/mcu` 直接入库），建议用 `域.项` 风格，她的判断更容易理解：

| 场景 | 建议指标 | 单位 |
|---|---|---|
| 温度 / 湿度 | `temp` / `hum`（中继示例里就是这个；想用 `env.temp` 也行） | C / % |
| 空气质量 | `env.pm25` / `env.co2` | µg/m³ / ppm |
| 光照 | `env.lux` | lx |
| 土壤湿度（养花）| `plant.soil` | % |
| 水位 / 漏水 | `water.level` / `water.leak` | cm / 0-1 |
| 门磁 / 人体 | `door.open` / `motion.detected` | 0-1 |
| 自己焊的闹钟 | `device.next_alarm` | 分钟 |

> 想让她主动提，就在 `hub.json` 的 `privacy.talkative_categories` 之外**加一条自定义规则**，
> 或者干脆让她按上下文自己判断 —— `/llm-preview` 能看到她到底看到了什么。

**上报之后会出现在哪**（这就是"接了一个设备"的完整价值）：

| 位置 | 你会看到什么 |
|---|---|
| 微信 | 她主动说话时可能提一句（比如"屋里 26.8℃、湿度 61%，还行"）—— 提不提由她的判断与角色卡决定 |
| 桌面挂件 | 点她翻到「关心」那一泡：`26.4℃ · 湿度 58%`，与电量/磁盘/连续活跃并列 |
| `GET /today` | `care.env = {"temp": 26.4, "hum": 58.0}` —— 网页或别的终端也能直接拿 |
| `GET /llm-preview` | 模型**实际看到**的那一份（脱敏后），可以逐字核对 |

> 口径：`temp` / `hum` 这类**瞬时值**取"最近一次"；当天累计分钟数那种**累计值**取最新、**不要 SUM**。

## 一次性配对（推荐姿势，v0.1.13 起）

**不要在设备/中继里长期写明文 token**。改成"拿一个短码换一次 token"：

```bash
# 1) 在可信侧（服务器）生成一个码：15 分钟有效、**只能用一次**
hubctl pair --device stm32_room
#      4J6K-6SVP

# 2) 中继拿它换 token（换到后写进 WHALE_TOKEN_FILE，之后重启复用，不用再配）
export WHALE_HUB=https://YOUR_SERVER_IP:11443
export WHALE_CA=/path/to/hub.crt          # 中枢自签证书
python3 mcu_relay.py --pair 4J6K-6SVP --device stm32_room
```

设备侧也可以直接问（回的是**一行纯文本**，单片机不用解析 JSON）：

```
GET https://<中枢>:11443/api/pair?c=4J6K-6SVP&d=stm32_room
→ 200  <token>            # 第一行就是 token
→ 400  err:used           # 码已经被用过（一次性）
→ 400  err:expired        # 超过 15 分钟
→ 400  err:device_mismatch  # 码生成时绑定了别的设备名
```

## 证书固定（v0.1.13 起是**真**固定）

中继到中枢那一段**强制 HTTPS**，并且：

- **只信任 `WHALE_CA` 指定的那一张证书**（自建 SSLContext，**不再叠加系统根 CA** ——
  旧写法用 `create_default_context` 会把公共 CA 也加进信任链，那不算固定）。
- 可选 `WHALE_PIN=<证书 sha256>`：握手后逐字节比对服务器证书，不符即拒绝（防"CA 被换掉"）。
  取指纹：`openssl x509 -in hub.crt -noout -fingerprint -sha256`（去掉冒号、小写）。
- 没给 CA 又没显式 `WHALE_INSECURE=1` → **拒绝启动**（不给"默默降级成不校验"的机会）。

## 下行：让设备能收到她的话（提醒 / 简报）

同一套协议反着走一遍 —— 设备主动来取，一行纯文本，单片机不用解析 JSON：

```
GET /mcu/inbox?d=stm32_room&t=<设备token>&enc=gb2312   → ok|42|该睡了 主人   /   none   /   err:token
GET /mcu/ack?d=stm32_room&t=<设备token>&id=42          → ok                 ← 念完回执（可选）
```

- `enc=gb2312` = 给 SYN6288 / XFS5152 这类中文 TTS 模块**直接可用**（默认 utf8）
- `peek=1` = 只看不消费（调试）
- 下发前中枢会把 `（动作）` 标注、emoji、markdown 剥掉，超 120 字按句号截断
- **配一块屏 + 语音模块的完整做法**（接线/参考代码/模拟器）见 [`STM32.md`](STM32.md)

## 安全建议（重要）

1. **别把中继的 8088/8089 暴露到公网** —— 它是明文口，只该在内网/VPN 里
2. 给单片机**单独的 token**：`hub.json` 里 `mcu.token` 填一个新值，设备用它；
   这样设备固件被人拿走，泄的也不是你的主钥匙
3. 中继那一层已经有软限流；中枢侧同样有 240 次/分 + 1MB 上限
4. 不要用单片机去读你家里的隐私数据（麦克风/摄像头/门锁状态）—— 它没有加密能力

# 接一块 STM32 小屏，当"实体版的小鲸鱼"

> 目标：**照着接线、烧进去、就能用** —— 她那边一发提醒，你桌上的小屏就亮起来、还会念出来。
> 不要求你会写 STM32（下面有能直接复制的参考代码）；也不要求你有公网 IP（走你家里的局域网中继）。

---

## 一、她怎么把话传到你桌上（数据流）

```
中枢(你的服务器 / 家里的机器)
   │  ① 提醒进队列（reminders 表，status='new'）
   ▼
局域网中继  mcu_relay.py        ← 跑在树莓派 / 旧电脑 / NAS 上（家里常开的任何一台）
   │  ② STM32 每 5 秒轮询一次：GET /mcu/inbox
   ▼
STM32 ──UART──▶ ESP-01(AT) 联网
   ├──UART──▶ 语音模块（SYN6288）：把文本念出来
   └──SPI/I2C─▶ 屏幕：显示小鲸鱼 + 表情 + 文字
```

**为什么要中继**：单片机能联网，但基本做不了 TLS（握手太重）✓ 所以让它跟**局域网里的中继**说
明文，中继再用 HTTPS 跟中枢说话 —— 密钥不出服务器，设备侧不需要证书。

---

## 二、协议（一行纯文本，单片机不用解析 JSON）

**取提醒**
```
GET /mcu/inbox?d=stm32_desk&t=<设备token>&enc=gb2312
→ ok|42|该睡了 主人，明早八点有课。          ← 有内容（id=42）
→ none                                      ← 暂时没有
→ err:token                                 ← token 不对
```
- `d` = 设备名（随便起，会出现在 `hubctl devices` 里 ✓）
- `t` = **设备专用 token**（中枢 `hub.json` 里的 `mcu.token` ✓ 别用主 token ✗）
- `enc=gb2312` = 给 SYN6288 / XFS5152 这类中文 TTS 模块**直接可用**的编码（默认 utf8 ✓）
- `peek=1` = 只看不消费（调试用 ✓）

**回执（可选，建议加）**
```
GET /mcu/ack?d=stm32_desk&t=<设备token>&id=42
→ ok
```

**上行（传感器数据 / 按键事件）用原来的口**（见 `docs/MCU.md`）：
```
GET /api/mcu?d=stm32_desk&m=temp&v=26.4&t=<设备token>
UDP: "stm32_desk,temp:26.4"
```

---

## 三、接线（以 STM32F103C8T6 蓝板为例）

| STM32 引脚 | 接什么 | 说明 |
|---|---|---|
| PA9/PA10 (USART1) | ESP-01S（TX↔RX 交叉）| 联网，AT 指令 115200 |
| PA2/PA3 (USART2) | SYN6288（TTS）| 波特率按模块（常见 9600）|
| PA5/PA6/PA7 + PB0/1 | ST7789 屏（SCK/MOSI/DC/RST/CS）| SPI，240×240 |
| PA0 | 轻触按键（另一端接 GND）| 按一下：重复念上一条 / 换表情 |
| 3V3 / GND | 各模块共地 | ESP-01S 电流尖峰大，**并联 100µF 电容**，别用板上 3.3V 直接带喇叭功放 |

> ⚠️ 三个坑：① ESP-01S 供电不足会随机掉线 ✓ ② TTS 模块共地不共地会出杂音 ✓
> ③ 屏幕 SPI 走线别超 10cm，否则花屏 ✓

---

## 四、参考代码（最小可跑：轮询 → 显示 → 念出来）

> 用 Arduino/PlatformIO 框架（对新手最友好）；要 HAL 版跟我说，我按你的工程给出。

```cpp
#include <WiFiEspAT.h>          // ESP-01 AT 模式
#include <Adafruit_GFX.h>       // 屏幕
#include <Adafruit_ST7789.h>

#define RELAY_HOST "192.168.1.50"   // 你家里中继的局域网地址
#define RELAY_PORT 8088
#define DEVICE     "stm32_desk"
#define TOKEN      "在hub.json的mcu.token里"   // ← 设备 token，不是主 token

Adafruit_ST7789 tft(PA4 /*cs*/, PA3 /*dc*/, PB0 /*rst*/);

void say(const String& s) {          // 发给 SYN6288：GB2312 由中继转好
  Serial2.print("[v10][m3][t5]");    // 音量10 背景音3 语速5（按模块手册调）
  Serial2.print(s);
}

void setup() {
  Serial.begin(115200);
  Serial2.begin(9600);               // TTS
  tft.init(240, 240); tft.setRotation(0);
  tft.fillScreen(ST77XX_BLACK);
  WiFi.init(Serial1);                // ESP-01 挂在 Serial1
  WiFi.begin("你的WiFi", "密码");
  while (WiFi.status() != WL_CONNECTED) delay(500);
}

void loop() {
  WiFiClient c;
  if (c.connect(RELAY_HOST, RELAY_PORT)) {
    c.print("GET /mcu/inbox?d=" DEVICE "&t=" TOKEN "&enc=gb2312 HTTP/1.0\r\n\r\n");
    String line;
    while (c.connected()) { String l = c.readStringUntil('\n'); if (l.length() > 2) line = l; }
    c.stop();
    line.trim();
    if (line.startsWith("ok|")) {
      int p1 = line.indexOf('|'), p2 = line.indexOf('|', p1 + 1);
      String id = line.substring(p1 + 1, p2), text = line.substring(p2 + 1);
      drawWhale();                   // 你的表情帧（下面"形象素材"一节）
      tft.setCursor(10, 200); tft.setTextColor(ST77XX_WHITE); tft.print(text.c_str());
      say(text);                     // 念出来
      delay(4000);                   // 等她念完
      ack(id);                       // 回执
    }
  }
  delay(5000);                       // 5 秒轮询一次就够（提醒不是实时音视频）
}

void ack(const String& id) {
  WiFiClient c;
  if (c.connect(RELAY_HOST, RELAY_PORT)) {
    c.print("GET /mcu/ack?d=" DEVICE "&t=" TOKEN "&id=" + id + " HTTP/1.0\r\n\r\n");
    while (c.connected()) c.readStringUntil('\n');
  }
}
```

---

## 五、不用买元件也能先验整条链（**强烈建议先跑这个**）

仓库带了 PC 端模拟设备：

```bash
export WHALE_HUB="https://你的中枢:11443" WHALE_TOKEN="设备token" WHALE_CA=/path/to/hub.crt
python3 hub/tools/mcu_sim.py --device stm32_desk --enc gb2312
```
它会像真设备一样每 5 秒轮询，把"屏幕上会显示什么、喇叭会念什么"打印出来 ✓
（包括"动作标注已被剥掉"这件事 ✓）—— 确认没问题再去焊板子 ✓

---

## 六、形象素材（**版权红线，和桌面挂件同一条**）

- 仓库**不带**形象图 ✓（原因见 `desktop/assets/README.md`）
- 自己画 / 约稿 / AI 生成后自己精修 → 放 `assets/` 即可；**别拿网图改完再公开分发** ✗
- 屏幕参考规格：240×240 PNG，透明底，主色深蓝（`#1b4f7e`）系，每帧 ≤ 20KB
- 想要"眨眼/换表情"：i) 直接换帧（简单 ✓）ii) 用两张图做上下位移循环（省 Flash ✓）

---

## 七、她"念出来"的口径（已经在中枢里做好了）

中枢在下发前会自动把话**变成能念的**（`_speakable` ✓）：
- 去掉 `（动作/情绪）` 标注 —— 念出来不该带动作 ✓（和人设里"不许假装做物理动作"同一条规矩）
- 去掉 markdown 与 emoji
- 超过 120 字按句号截断（TTS 模块有长度限制 ✓ 也没人想听长段）

**想改说话风格**：编辑 `hub/ext/hooks.py`（从 `hub/ext/stm32_hooks.example.py` 复制 ✓），
用 `before_say(text, level, kind, key)` 钩子改写或拦掉某类话 ✓ —— 不需要动中枢核心 ✓

---

## 八、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| `err:token` | 用了主 token，或 token 抄错 | 用 `hub.json` 的 `mcu.token` |
| 一直 `none` | 队列里确实没有新提醒 / 已被别的出口取走 | 用 `peek=1` 看；或 `hubctl` 里手动排一条 |
| 屏亮了但没声音 | TTS 模块波特率或编码不对 | 先 `enc=gb2312`，再确认 9600/115200 |
| ESP-01 随机掉线 | 供电电流不够 | 并 100µF 电容，或独立 3.3V LDO |
| 中继连不上中枢 | 没给 CA | `export WHALE_CA=...`（中继**默认拒绝**不校验证书 ✓ 这是故意的）|

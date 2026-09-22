# 外挂扩展（ext/）

**目的**：让"加一个新数据源 / 一个新动作"**不用改中枢核心**，也**不破坏"零第三方依赖"**这个卖点。

核心永远只用标准库；扩展是**你自己写**的小脚本，放在服务器上 `hub.py` 旁边的 `ext/` 目录里，
可以自由 `import` 任何你想用的库。

```
ext/
├── sources/              # 数据源适配器：一个文件 = 一个数据源
│   └── example_http_json.py
├── hooks.py.example      # 钩子示例（要用就把文件名改成 hooks.py）
└── README.md
```

## 一、数据源适配器：`ext/sources/<任意名字>.py`

只要定义三样东西：

```python
NAME = "阳台温湿度"            # 显示名（会出现在 /ext 里）
DEVICE = "balcony"            # 入库时的设备名
INTERVAL_MINUTES = 10         # 多久取一次

def fetch():
    """返回一个 dict，或 list[dict]；返回 None/[] 表示这次没数据。"""
    return [
        {"metric": "env.temp", "value": 26.8, "unit": "C", "meta": {"place": "阳台"}},
        {"metric": "env.hum",  "value": 58.0, "unit": "%"},
    ]
```

**回去之后会发生什么**（这才是重点）：返回值走的是**和手机采集器完全相同的那一条入库闸口**
（`ingest_items()`）—— 自动获得：

1. **去重**（同设备同指标同值 60 秒内只留一条）
2. **不落原文**（`meta.raw` / `meta.text` 会被剥掉，除非你自己在 hub.json 里开了 `store_raw_text`）
3. **进她的视野**（相对基线、提醒规则、`/llm-preview` 里能核对到）
4. **挂件/网页也能看到**（`/today` 的 `care` 块）

**硬规则**（不遵守会被 `/ext` 记成错误，但**绝不会拖垮中枢**）：

- `fetch()` 要**快**（几秒内返回）。它跑在独立线程里，慢只会拖慢这个数据源自己。
- 不要在这里做"重活儿"（模型推理、爬全站）。这里只做"取一个数"。
- 抛异常没关系：错误记进 `/ext`，下个周期再试，中枢主流程不受影响。

## 二、钩子：`ext/hooks.py`

要用就把 `hooks.py.example` 改名成 `hooks.py`。

```python
def before_say(text, level, kind, key):
    """她每次要主动说一句话之前被调用。
    返回字符串 → 替换这句话；返回 None → 保持原样。"""
    return None

def on_event(kind, data):
    """中枢内部事件（如生成提醒）时被调用，做你自己的记录/联动。"""
    pass
```

## 三、能用来接什么（几个真实可行的方向）

| 想接什么 | 怎么接 | 注意 |
|---|---|---|
| **局域网里任何能发 HTTP 的东西**（ESP32/树莓派路由/路由器脚本） | 直接 `POST /ingest` 或 `/api/mcu` 一行明文（**不用写扩展**） | 最省事，优先考虑这条 |
| **你自己写的采集脚本**（读某个网页/某个本地文件/某个私有接口） | `ext/sources/xxx.py` | 就是本节的一 |
| **Home Assistant**（Apache-2.0）里的设备状态 | 在 HA 里配一个 `rest_command` / 自动化，把状态 POST 到 `http://<中枢>:11440/ingest` | 让 HA 当"翻译层"，中枢保持零依赖 |
| **需要第三方库才能读的设备**（云 API、私有协议） | `ext/sources/xxx.py` 里正常 `import` 那个库 | ⚠️ **那个库的许可证是你自己的事**：如果它是 GPL/AGPL，你可以自己在服务器上用它，但**不要把它随本仓库分发**，也不要把它的代码拷进本项目（本项目 MIT）。文档里如实写清依赖，让使用者自己安装。 |

## 四、排查

```bash
curl -sk -H "X-Token: $TOKEN" https://<中枢>:11443/ext | python3 -m json.tool
```

会告诉你：加载了哪些扩展、每个上次入库几条、最近一次报错是什么、注册了哪些钩子。
**扩展文件改了要重启中枢**（`rm -f /root/hub/.hubhash` 会在一分钟内触发热重启）。

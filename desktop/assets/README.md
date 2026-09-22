# 素材说明（为什么这里是空的）

这个目录**故意不带任何美术资源**。

原因：仓库是 MIT 的，代码可以随便用，但**形象图不是本项目原创**（早期是拿别人的图改的），
跟着仓库一起分发会有版权问题。所以：

- 仓库里只有**代码** + **占位图生成脚本** + **自制音效**（`sfx_pop.wav` 由 `tools/make_sfx.py` 生成）
- 字体只保留了**许可证文本**（`fonts/HarmonyOS-Sans-LICENSE.txt`），不带字体文件本身

## 怎么让挂件跑起来

```bash
cd desktop
bash tools/make_placeholder.sh     # 用 ImageMagick 画一套占位形象（圆圈 + 鲸尾）
python3 whale_desk.py              # 起挂件（缺素材会自动跳过，不会崩）
```

## 换成你自己的形象

按同名文件丢进 `assets/` 就行，程序会**自动用你的**（缺哪个用占位）：

| 文件名 | 用途 | 建议 |
|---|---|---|
| `pet.png` | 站立主形象（默认表情）| 200×200 起 |
| `pet_drag.png` | 被拖动时 | 同尺寸 |
| `pet_flip.png` | 贴边吸附翻转后 | 同尺寸 |
| `pet_sleep.png` | 打盹 | 同尺寸 |
| `exp_<名字>.png` | 点一下随机换的表情（happy/blush/shy/angry/surprised/sleepy）| 同尺寸，缺哪个跳过哪个 |
| `*_win.png` | Windows 透明版（键控色 `#010203`）| 同上 |
| `whaledesk.ico` | exe 图标 | 256×256 |

**透明要点**（Windows 桌面透明只认精确键控色）：
底色用 `#010203`，主体外缘加一圈深色描边把抗锯齿的边缘像素藏进去，
否则人物外面会出现一圈黑边 —— 具体做法看 `tools/make_placeholder.sh`，里面就是这样画的。

想用别人的形象，**先确认授权**（原创/买断/CC 协议都行），别直接拿网图改完再公开分发。

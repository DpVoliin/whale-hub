#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无头自检驱动：把各状态真的画出来 + 存图 + 打印状态断言。
用法： DISPLAY=:97 python3 tools/_drive.py <输出目录>
⚠️ 截图用外部 import 命令，但**只在主循环之外的直线代码里调**——
   绝不在 Tk 的 after 回调里调（会阻塞事件循环，后面的回调全乱序）。
"""
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/wd"
os.makedirs(OUT, exist_ok=True)

spec = importlib.util.spec_from_file_location("wd", os.path.join(ROOT, "whale_desk.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

cfg_path = os.path.join(OUT, "whale_desk.json")
if os.path.exists(cfg_path):
    os.remove(cfg_path)
cfg = m.load_cfg(cfg_path)
cfg["pet"]["size"] = 200
cfg["pet"]["pos"] = [60, 80]
m.save_cfg(cfg, cfg_path)

w = m.Widget(cfg, cfg_path)
w.root.after(m.TICK_MS, w.animate)          # 必须把主循环挂上：吸附/淡出/取数都在里面跑
# 等首次取数回来（真连中枢；连不上也不影响状态自检）
for _ in range(160):
    w.root.update()
    time.sleep(0.02)
print("  数据来源：中枢 %s / 余额 %s" % (w.d.get("hub_state"), w.d.get("balance_state")))
print("  气泡行数 %d  宽 %d  高 %d" % (len(w._bubble_rows()), w._bubble_w(), w._bubble_h()))
# 回归断言：余额没取到时，数值模块**绝不能**渲染成 ¥0.00（那是"看起来余额归零了"的假象）
_vals = [m.get("text") for r in w._bubble_rows() for m in r if m.get("t") == "value"]
print("  数值模块渲染:", _vals)


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        w.root.update()
        time.sleep(0.01)


def shot(name):
    for _ in range(3):
        w.root.update()
        time.sleep(0.05)
    subprocess.run(["import", "-window", "root", os.path.join(OUT, name)], capture_output=True)


def verify(name):
    print("  [%s] 状态=%s 镜像=%s 气泡在%s 窗口=%s alpha=%.2f 画布元素=%d" % (
        name, w.state, w.flipped, "左" if w.bub_left else "右", w.root.winfo_geometry(),
        float(w.root.wm_attributes("-alpha") or 1), len(w.cv.find_all())))
    shot(name + ".png")


results = [("余额没取到时不许显示 ¥0.00（防'假归零'）",
            all("0.00" not in str(v) for v in _vals) or bool(w.d.get("balance_ok")))]

# 贴图素材的自检：每张姿态图裁完后，**最后一行必须有内容**（否则她换姿态会悬空）
for name in ("pet.png", "pet_flip.png", "pet_sleep.png"):
    p = os.path.join(m.ASSETS, name)
    if not os.path.exists(p):
        continue
    last = subprocess.run(["convert", p, "-crop", "x1+0+%d" % (int(subprocess.run(
        ["identify", "-format", "%h", p], capture_output=True, text=True).stdout) - 1),
        "txt:-"], capture_output=True, text=True).stdout
    opaque = sum(1 for ln in last.splitlines() if "srgba" in ln and ",1)" in ln)
    results.append(("%s 底边有内容（不会悬空）" % name, opaque > 0))

# ① 常驻态：只有形象，零数字
w.set_state("pet")
pump(0.3)
verify("1_pet")
results.append(("常驻态窗口 = 形象盒", w.root.winfo_width() == w._pet_box()[0]))

# ② 点击 → 数据泡
# ⚠️ 假事件必须带 x/y：on_press 现在会做"是否点在形象本体上"的命中判定（真 Tk 事件一定有）
_PW0 = w._pet_box()[0]
w.on_press(type("E", (), {"x_root": 100, "y_root": 100, "x": _PW0 // 2, "y": 100})())
w.on_release(type("E", (), {"x_root": 100, "y_root": 100, "x": _PW0 // 2, "y": 100})())
pump(0.4)
verify("2_data")
results.append(("数据态窗口变宽", w.root.winfo_width() > w._pet_box()[0]))

# ③ 按压 Q 弹：压扁（底部基线不动）
w.pressed = True
pump(0.15)
im_squashed = w._cur_pet_image()
_base = w.img["idle"]
verify("3_press")
# 拉伸已停用（用户改成"点一下随机换表情"）→ 断言反过来：按住也不许改尺寸
results.append(("按住不改形象尺寸（拉伸已停用）", im_squashed.width() == _base.width()))
print("  按压前 %dx%d → 按压后 %dx%d（应完全相等）" % (_base.width(), _base.height(),
                                        im_squashed.width(), im_squashed.height()))
w.pressed = False

# ③b 点一下（不是按住）也要有那发左右拉伸。
# ⚠️ 动画是周期性的（sin 有正负半周）→ 只采一个瞬间会随机采到负半周，
#    改为单调曲线后仍有"拉出去→弹回"的时间过程 → 仍要扫一段取峰值，别只采一帧。
w.sq = 1.0                       # 单调曲线只有一个变量（0..1）
w._click_t = time.time()
_maxw = _base.width()
_minw = _base.width()
_heights = set()
for _ in range(26):
    pump(0.02)
    _im = w._cur_pet_image()
    _maxw = max(_maxw, _im.width())
    _minw = min(_minw, _im.width())
    _heights.add(_im.height())
print("  点一下：宽度 %d → 峰值 %d / 谷值 %d；出现过的所有高度 = %s"
      % (_base.width(), _maxw, _minw, sorted(_heights)))
results.append(("点一下不改宽度（拉伸停用，不再摇晃）",
                _maxw == _base.width() and _minw == _base.width()))
results.append(("点一下**高度全程不变**（不抽搐）", _heights == {_base.height()}))

# ③b2 连点：拉满就封顶（不会越点越夸张）
w.sq = 0.0
w.on_click()
amp1 = w.sq
w.on_click()
amp2 = w.sq
pump(0.02)
print("  连点两次的拉伸量：%.2f → %.2f（上限 1.0）" % (amp1, amp2))
results.append(("连点不会越点越夸张（拉伸量恒为 0）", amp2 <= 1e-9))
w.sq = 0.0
pump(0.05)

# ③b3 对话功能必须已经拿掉（用户要求"直接去掉"）
_binds = str(w.cv.bind())
results.append(("双击不再绑任何东西（左键连点不弹窗）", "Double" not in _binds))
results.append(("open_chat 已从代码里删掉", not hasattr(m.Widget, "open_chat")))

# ③b4 音效 wav 必须是能解码的真文件
import wave as _wave

for _n, _lab in (("sfx_pop.wav", "啵"),):   # 鸭子叫已按用户要求删掉，不再验它
    _p = os.path.join(ROOT, "assets", _n)
    try:
        with _wave.open(_p, "rb") as _w:
            _d = _w.getnframes() / float(_w.getframerate())
        print("  %s %s：%.3f s ✓" % (_n, _lab, _d))
        results.append(("音效 %s 是有效 wav" % _n, 0.05 < _d < 1.0))
    except Exception as _e:
        results.append(("音效 %s 是有效 wav" % _n, False))
        print("  ✗ %s: %s" % (_n, _e))
results.append(("play_sound 指向的文件真实存在", os.path.exists(w.sfx_file("click"))))

# ③c 拖动态必须真的换成「被拎起来」那张（与站立不同的贴图）
_idle_dims = (w.img["idle"].width(), w.img["idle"].height())
w.dragging = True
pump(0.1)
im_drag = w._cur_pet_image()
w.dragging = False
pump(0.05)
print("  拖动态贴图 %dx%d  vs 站立 %dx%d" % (im_drag.width(), im_drag.height(), *_idle_dims))
results.append(("拖动时换成拖拽姿态贴图", (im_drag.width(), im_drag.height()) != _idle_dims))

# ④ 拖到左缘 → 吸附 + 镜像
w.dragging = True
w.pet_xy = [2, 80]
w.apply_geometry(keep_pos=True)
pump(0.15)
w.dragging = False
w.snap_now()
pump(0.5)
verify("4_flip_left")
results.append(("贴左吸附 → 形象 x=0", int(w.pet_xy[0]) == 0))
results.append(("贴左 → 镜像开", bool(w.flipped)))
im4 = w._cur_pet_image()                    # 此刻 flipped=True
_snap = w.flipped
w.flipped = False
im_plain = w._cur_pet_image()
w.flipped = _snap
_mism = _tot = 0
for _y in range(0, im_plain.height(), 3):
    for _x in range(0, im_plain.width(), 3):
        _tot += 1
        if im_plain.get(_x, _y) != im4.get(im_plain.width() - 1 - _x, _y):
            _mism += 1
print("  镜像核对：不符点 %d/%d 个采样点" % (_mism, _tot))
results.append(("镜像渲染 = 站立渲染的水平翻转（误差 <2%）", _mism <= _tot * 0.02))

# ⑤ 睡一觉：缩成一团 + 淡下去
w.last_touch = time.time() - 999
pump(0.4)
verify("5_sleep")
results.append(("休息态整窗变淡", float(w.root.wm_attributes("-alpha")) < 0.5))


def content_top(im):
    bg = im.get(0, 0)
    for y in range(0, im.height(), 2):
        for x in range(0, im.width(), 4):
            if im.get(x, y) != bg:
                return y
    return im.height()


_ih_idle = w.img["idle"].height()
_ih_sleep = w._cur_pet_image().height()
print("  姿态图高度：站立 %d / 打盹 %d" % (_ih_idle, _ih_sleep))
results.append(("休息态缩成一团（比站立矮）", _ih_sleep < _ih_idle - 10))

# ⑥ 贴右缘：气泡应翻到形象左边（否则飞出屏幕）
w.last_touch = time.time()
w.set_state("pet")
pump(0.2)
pw = w._pet_box()[0]
sw = w.root.winfo_screenwidth()
w.pet_xy = [sw - pw, 80]
w.apply_geometry(keep_pos=True)
pump(0.2)
w.snap_now()
pump(0.3)
w.set_state("data")
pump(0.4)
verify("6_right_edge")
results.append(("贴右缘 → 气泡在形象左边", bool(w.bub_left)))
results.append(("贴右缘 → 不镜像（只在贴左时翻）", not w.flipped))

# ⑥ 本轮：拉伸不被窗口裁 / 留白不抢点击 / 自绘菜单 / 鸭子叫已删
_pb_w, _pb_h = w._pet_box()
# 用户报"点击时候还是右边多一块…黑框变长了" → 就是给拉伸预留的那 15px 留白
_pad = _pb_w - _base.width()
print("  常驻态窗口宽 %d vs 形象宽 %d → 左右各预留 %d px" % (_pb_w, _base.width(), _pad // 2))
results.append(("没有多余留白（左右各 ≤3px，不会有「右边多一块」）", _pad <= 6))
_im_pk = w._cur_pet_image()
results.append(("形象宽度 ≤ 窗口宽（不会被硬裁）", _im_pk.width() <= _pb_w))
w.sq = 0.0
pump(0.05)

# 留白区不抢点击：留白 = 素材盒左右各 STRETCH_PAD，取盒子正中 = 形象正中（应命中），
# 取左边缘往里 3px（应落在留白里 → 不命中）
# ⚠️ 形象在画布里的 x 随状态变（气泡翻到左边时形象在右半边）→ 必须固定在"常驻态"测
_st_before = w.state
w.set_state("pet")
pump(0.12)
_pb_w2, _pb_h2 = w._pet_box()
_hit_mid = w._hit_pet_body(_pb_w2 // 2, _pb_h2 - 10)   # 形象正中（只看本体，不含气泡）
_hit_pad = w._hit_pet_body(2, _pb_h2 - 10)             # 最左边 = 拉伸留白
print("  命中判定：形象正中=%s  左留白=%s" % (_hit_mid, _hit_pad))
results.append(("点形象本体 → 命中", _hit_mid))
results.append(("点透明留白 → 不命中（不会误拖）", not _hit_pad))

# ⑪ 本轮：长句必须折行显示完整，不许截成"…"（用户："点击第三次的对话没有显示全"）
_inner = m.BUB_MAXW - m.BPAD * 2
_worst, _clipped = 0, []
for _i in range(0, len((w.cfg.get("bubbles") or {}).get("queue") or []) + 2):
    w.bubble_idx = _i
    for _row in w._bubble_rows():
        _f, _fl = w._row_fonts(_row)
        for _mod in _row:
            if _mod.get("t") != "text":
                continue
            _t = _mod.get("text") or ""
            _worst = max(_worst, _f.measure(_t))
            if _f.measure(_t) > _inner and m.wrap_lines(_t, _f, _inner) > m.MAX_WRAP_LINES:
                _clipped.append(_t[:18])
print("  最长一行 %d px；折行宽度 %d；折行上限 %d 行；超限被截 %d 条"
      % (_worst, _inner, m.MAX_WRAP_LINES, len(_clipped)))
results.append(("行高跟着折行行数走（长句不压下一格）",
                w._row_h([{"t": "text", "text": "字" * 60}]) > w._row_h([{"t": "text", "text": "字"}])))
results.append(("长句折行显示完整（不再截…）", not _clipped))
w.bubble_idx = -1

# ⑧ 本轮：随机行必须定住（用户报"点第三下信息会乱跳"）
w.set_state("data")
pump(0.1)
_q = (w.cfg.get("bubbles") or {}).get("queue") or []
_rand_idx = None
for _i, _item in enumerate(_q, 1):
    if any(_r.get("t") == "random" for _r in (_item.get("rows") or []) for _r in ([_r] if isinstance(_r, dict) else _r)):
        _rand_idx = _i
        break
if _rand_idx is not None:
    w.bubble_idx = _rand_idx
    _seen = {json.dumps(w._bubble_rows(), ensure_ascii=False) for _ in range(10)}
    print("  随机行（第%d下）连取 10 次 → %d 种结果" % (_rand_idx + 1, len(_seen)))
    results.append(("随机行定住（不再每帧重抽 → 不会乱跳）", len(_seen) == 1))
else:
    results.append(("随机行定住（本配置没有随机行，跳过）", True))

# ⑨ 本轮：空行不占一格
_empty_ok = True
for _i in range(0, len(_q) + 1):
    w.bubble_idx = _i
    for _row in w._bubble_rows():
        for _mod in _row:
            if _mod.get("t") == "text" and not (_mod.get("text") or "").strip():
                _empty_ok = False
print("  各格气泡里有没有空行：%s" % ("没有 ✓" if _empty_ok else "有空行 ✗"))
results.append(("气泡里不画空行", _empty_ok))

# ⑩ 本轮：左键 = 随机换表情
_exps = [k[4:] for k in w.img if k.startswith("exp:")]
print("  可用表情 %d 个：%s" % (len(_exps), " ".join(sorted(_exps))))
if _exps:
    _base_im = w._cur_pet_image()
    w.on_click()
    _exp_im = w._cur_pet_image()
    _same = (_exp_im.width() == _base_im.width() and str(_exp_im) == str(_base_im))
    print("  点一下后：express=%s，拿到的图%s" % (w.express, "换了" if not _same else "没换 ✗"))
    results.append(("点一下就换了一张表情图", bool(w.express) and not _same))
    # 到期要收回常态
    w.express_until = time.time() - 1
    pump(0.2)
    results.append(("表情到期自动收回常态", w.express is None))
else:
    results.append(("点一下换表情（没有素材，跳过）", True))
w.set_state("pet")
pump(0.1)
w.set_state(_st_before)
pump(0.1)

# 自绘菜单：能开、尺寸对、有内容、点了会关
class _Ev(object):
    x_root = 300
    y_root = 300


_rows = w._menu_rows()
_labels = [r.get("label") for r in _rows if r.get("label")]
_joined = " ".join(_labels)
results.append(("菜单项齐全（形象大小/吸附/音效/采集/退出）",
                all(k in _joined for k in ("形象大小", "贴边吸附", "音效", "电脑采集", "退出"))))
results.append(("菜单里没有「说话/聊天」残留", ("说话" not in _joined) and ("聊天" not in _joined)))
results.append(("菜单里没有「鸭子叫」选项",
                not any("鸭子" in (r.get("label") or "") or "鸭子" in (r.get("value") or "")
                        for r in _rows)))
w.menu(_Ev())
pump(0.15)
_mw = getattr(w, "_menu_win", None)
_geo = _mw.winfo_geometry() if _mw is not None else "-"
print("  自绘菜单窗口 geometry = %s" % _geo)
results.append(("自绘菜单开出来了（是 Toplevel，不是原生菜单）",
                _mw is not None and _mw.winfo_exists() == 1))
if _mw is not None:
    _mcv = _mw.winfo_children()[0]
    _items = len(_mcv.find_all())
    print("  菜单画布元素 = %d 个" % _items)
    results.append(("菜单画出来了（含卡片/文字/高亮行）", _items >= 20))
w._close_menu()
pump(0.05)
results.append(("菜单能关掉（不留孤儿窗口）", getattr(w, "_menu_win", None) is None))

# 鸭子叫：文件应已删除，play_sound 指向的文件仍然存在
results.append(("鸭子叫 wav 已从包里删掉", not os.path.exists(os.path.join(ROOT, "assets", "sfx_duck.wav"))))
results.append(("音效指向的文件存在（啵）", os.path.exists(w.sfx_file("click"))))

# ⑦ 电脑采集已并进挂件进程（一个 exe = 挂件 + 采集，不再单独一个 exe）
w.pc = m.PCCollect(w.cfg, w.cfg_path)
w.pc.start()
pump(1.2)
_pc = w.pc
results.append(("采集随挂件一起起来了（同一进程）", bool(_pc.ok)))
if _pc.ok:
    _st, _pv = _pc.status_lines(), _pc.preview_lines()
    print("  [采集] %s" % (_st[0] if _st else "-"))
    print("  [采集] 线程活着=%s  状态 %d 行 / 隐私预览 %d 行"
          % (bool(_pc.thread and _pc.thread.is_alive()), len(_st), len(_pv)))
    results.append(("采集线程活着", bool(_pc.thread and _pc.thread.is_alive())))
    results.append(("菜单能出「上报状态」文字", any("设备名" in x for x in _st)))
    results.append(("菜单能出「隐私预览」文字（含不上传清单）", any("不会上传" in x for x in _pv)))
    results.append(("隐私预览里不含应用名/路径", not any("C:\\\\" in x or "chrome" in x.lower() for x in _pv)))
    _pc.stop_now()
else:
    print("  [采集] 没起来：%s" % _pc.err)


print("\n  === 状态断言 ===")
ok = True
for name, good in results:
    print("   %s %s" % ("✓" if good else "✗", name))
    ok = ok and good
print("  合计：%s" % ("全部通过 ✓" if ok else "有失败 ✗"))
w.root.destroy()
sys.exit(0 if ok else 1)

# ------------------------------------------------------------- 人设包（personas/）
# 人设 = 数据目录，不是代码。一个包三件套：
#   personas/<id>/persona.json  中枢侧字段（称呼/自称/语气/喜好/禁忌/关心话题）
#   personas/<id>/card.json     说话层角色卡（system_prompt / style_rules / …）
#   personas/<id>/README.md     说明
# 好处：换人设 = 换目录；两侧读同一份，不再"改一处忘一处"。
PERSONA_DIR = os.path.join(BASE, "personas")
DEFAULT_PACK = "whale_maid"


def persona_packs():
    """列出可用人设包（含名字，给终端用）。"""
    out = []
    try:
        for d in sorted(os.listdir(PERSONA_DIR)):
            if d.startswith("_"):
                continue
            p = os.path.join(PERSONA_DIR, d, "persona.json")
            if os.path.isfile(p):
                try:
                    nm = json.load(open(p, encoding="utf-8")).get("name") or d
                except Exception:
                    nm = d
                out.append({"id": d, "name": nm})
    except Exception:
        pass
    return out


def active_pack():
    return CFG.get("persona_pack") or DEFAULT_PACK


def persona_pack_path(pack=None, fname="persona.json"):
    d = os.path.join(PERSONA_DIR, pack or active_pack())
    p = os.path.join(d, fname)
    return p if os.path.isfile(p) else None


def load_persona_card(pack=None):
    """说话层的角色卡（speaker 从 /persona/card 取，也能本地读同名人设包）。"""
    p = persona_pack_path(pack, "card.json")
    if not p:
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"[persona] 读卡失败：{str(e)[:80]}", flush=True)
        return {}


def apply_persona_pack(pack=None):
    """把选定人设包合并进 CFG["persona"]。找不到包就保持原样（向后兼容，不炸）。"""
    p = persona_pack_path(pack, "persona.json")
    if not p:
        print(f"[persona] 找不到人设包 {pack or active_pack()} → 沿用 hub.json 里的 persona", flush=True)
        return False
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"[persona] 解析失败：{str(e)[:80]}", flush=True)
        return False
    CFG.setdefault("persona", {}).update(d)      # ★ 只做合并：其余代码无需改
    print(f"[persona] 已加载人设包 {pack or active_pack()}（{d.get('name')}）", flush=True)
    return True



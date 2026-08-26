"""把 553 个真实技能按「技能实现排序表」归入 P1–P6 批次，输出到 mydocs/skill-batches/。

分类规则 = 优先级批次判定：技能归入**其最高依赖批次**（如带应对+连击 → P3，因为应对
钩子比连击管线晚）。P1 = 纯伤害 / 纯防御 / 纯六维状态（引擎现状即可表达）。

运行：`uv run python scripts/build_skill_batches.py`
输出：`mydocs/skill-batches/batch-P1.json` … `batch-P6.json`
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_FILE = ROOT / "mydocs" / "skills.json"
OUT_DIR = ROOT / "mydocs" / "skill-batches"

PURE_DMG = ("对敌方精灵造成物理伤害。", "对敌方精灵造成魔法伤害。")

# ── P1 判定 ──
def _pure_damage(s: dict) -> bool:
    return s["kind"] in ("物攻", "魔攻") and s["desc"].strip() in PURE_DMG


def _pure_defense(s: dict) -> bool:
    return s["kind"] == "防御" and re.fullmatch(r"减伤\d+%，应对攻击。", s["desc"].strip()) is not None


_BANNED_STAT = ("印记", "灼烧", "中毒", "冰冻", "眩晕", "禁足", "寄生", "萌化", "湿润",
                "棘刺", "天气", "回复", "驱散", "连击", "能耗", "冷却", "先手", "脱离",
                "返场", "蓄力", "吸血", "选择", "巧变", "传动", "位置", "回合", "打断",
                "击败", "每", "若", "翻倍", "额外", "免疫", "反弹", "复活", "消耗",
                "永久", "触发", "复制", "借用", "变身", "守护", "改为", "失去", "解除",
                "紧急", "偷取", "奉献", "迅捷", "迸发", "交换", "体重", "速度比", "物防比")


def _pure_stat(s: dict) -> bool:
    """kind=状态 且只加/减六维（pct% 或速度±N），无任何其他机制词。"""
    if s["kind"] != "状态":
        return False
    d = s["desc"]
    if not re.search(r"(获得|[-+]\d)", d):
        return False
    if not ("%" in d or re.search(r"速度[-+]\d+", d)):
        return False
    return not any(b in d for b in _BANNED_STAT)


# ── P2–P6 特征（按优先级顺序检测）──
_FEATURES: list[tuple[str, tuple[str, ...]]] = [
    ("P6", ("选择", "巧变", "传动", "蓄力", "打断", "冷却", "复制", "借用", "取念",
            "复写", "随机变成", "免疫", "无敌", "反弹", "守护", "封印", "变身", "召唤",
            "迅捷", "奉献", "交换", "自动使用", "位置不会改变", "混血", "系别中的",
            "使用次数", "迸发", "每种", "至多1个", "抽取", "献身")),
    ("P5", ("天气", "脱离", "返场", "入场", "回合结束", "回合开始", "击败", "击杀",
            "受到攻击", "使用后", "下回合", "下一次", "下次", "每使用1次", "每使用过",
            "位置发生变化", "替换入场", "紧急脱离", "迫使")),
    ("P4", ("印记", "灼烧", "中毒", "冰冻", "冻结", "眩晕", "禁足", "寄生", "萌化",
            "湿润", "棘刺", "减速", "麻痹", "驱散", "引电")),
    ("P3", ("应对", "若", "每失去", "每有", "每受到", "每使用", "每[+\\-]", "体重",
            "越高", "越低", "差越大", "减益时", "能量小于", "能量等于", "能量耗尽",
            "能量消耗", "消耗所有", "生命大于", "生命低于", "生命高于", "低于50%",
            "大于80%", "额外", "永久", "持续", "解除", "改为", "翻倍", "失去", "能耗固定",
            "无法主动", "消耗5%")),
    ("P2", ("连击", "吸血", "先手", "回复.{0,4}能量", "回复能量", "回复.{0,4}生命",
            "回复生命", "获得.{0,2}吸血", "偷取.{0,2}能量")),
]

# ── 人工订正：模式分类器的边界情况（41 条待定 + 个别误判）──
_OVERRIDE: dict[str, str] = {
    # P3：条件威力修正 / 附加 stat / 能耗修正
    "吨位压制": "P3", "以重制重": "P3", "逆袭": "P3", "急中生智": "P3", "炎打": "P3",
    "涌泉": "P3", "鸣沙陷阱": "P3", "岩脉崩毁": "P3", "冰晶坠": "P3", "冰冻光线": "P3",
    "丢冰块": "P3", "寒风吹": "P3", "电弧": "P3", "超导": "P3", "超导加速": "P3",
    "网缚": "P3", "破罐破摔": "P3", "力量吞噬": "P3", "化劲": "P3", "骗局": "P3",
    "冰锋横扫": "P3",
    # P5：下次行动修正
    "蓄水": "P5",
    # P6：特殊机制
    "折射": "P6", "漫反射": "P6", "色散": "P6", "主轴": "P6", "锁芯": "P6",
    "雷暴": "P6", "双联脉冲": "P6", "飞断": "P6", "虫群智慧": "P6", "疾风涡轮": "P6",
    "翼击": "P6", "羽化加速": "P6", "欺诈契约": "P6", "隐藏条款": "P6", "恶念交换": "P6",
    # P4：引电印记
    "通电": "P4",
    # P2：能量
    "勾魂": "P2",
}

_BATCH_ORDER = ("P1", "P2", "P3", "P4", "P5", "P6")
_DESC = {
    "P1": "纯伤害 / 纯防御 / 纯六维状态（引擎现状即可表达，效果表从手写转派生）",
    "P2": "伤害+资源 / 连击 / 能量回复 / 吸血 / 先手（伤害管线小扩展）",
    "P3": "应对奖励 / 条件与威力修正 / 增减益带持续时间（应对钩子 + duration）",
    "P4": "印记 / 异常状态（状态目录 + 回合末 tick + 行动门控）",
    "P5": "天气 / 脱离返场 / 事件触发（FieldState + on_enter/on_exit + hook 分发器）",
    "P6": "选择 / 巧变 / 传动 / 蓄力 / 打断 / 冷却 / 复制借用（决策点 + 特殊机制）",
}


def batch_of(s: dict) -> str:
    if _pure_damage(s):
        return "P1"
    if _pure_defense(s):
        return "P1"
    if _pure_stat(s):
        return "P1"
    for batch, pats in _FEATURES:
        if any(re.search(p, s["desc"]) for p in pats):
            return batch
    return _OVERRIDE.get(s["name"], "待定")


def main() -> int:
    skills = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    buckets: dict[str, list[dict]] = defaultdict(list)
    pending: list[str] = []
    for s in skills:
        b = _OVERRIDE.get(s["name"]) or batch_of(s)
        if b == "待定":
            pending.append(s["name"])
            continue
        buckets[b].append({
            "name": s["name"],
            "type": s["type"],
            "kind": s["kind"],
            "power": int(s["strong"]) if s["strong"] is not None else 0,
            "energy_cost": int(s["energy"]),
            "desc": s["desc"],
        })
    if pending:
        print(f"⚠ 待定未归类：{pending}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for b in _BATCH_ORDER:
        lst = sorted(buckets[b], key=lambda x: x["name"])
        doc = {"batch": b, "说明": _DESC[b], "count": len(lst), "skills": lst}
        out = OUT_DIR / f"batch-{b}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total += len(lst)
        print(f"{b}: {len(lst):>3} 条 → {out.name}")
    print(f"合计 {total}/{len(skills)} 条已归类")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

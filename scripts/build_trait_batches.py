"""把 227 个唯一特性（按名称去重）归入 T-P1–T-P6 批次，输出到 mydocs/trait-batches/。

唯一锚点：**特性名**。同名 = 同一特性（已验证：227 个唯一名，0 个「同名不同 desc」冲突）。

分类规则 = 优先级批次判定：特性归入**其最高依赖批次**。与技能批次共用同一套基建
（效果原语 / 状态目录 / 事件钩子 / 决策点），所以特性批次紧跟在对应技能批次的基建之后。

运行：`uv run python scripts/build_trait_batches.py`
输出：`mydocs/trait-batches/batch-TP1.json` … `batch-TP6.json`
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPIRITS_FILE = ROOT / "mydocs" / "spirits_details.json"
OUT_DIR = ROOT / "mydocs" / "trait-batches"

# ── 特征 → 批次（按优先级从高到低）──
_FEATURES: list[tuple[str, tuple[str, ...]]] = [
    # T-P6：特殊机制 / 游戏系统（闪避/免疫/冷却/迅捷/迸发/魔力值/奉献…）——依赖特殊系统，需评估
    ("T-P6", ("闪避", "免疫", "复活", "无敌", "伪装", "识破", "交换", "继承", "偷取",
              "奉献", "魔力", "力竭", "咕噜球", "背包", "木桶", "变化出", "随机精灵",
              "王国", "入夜", "明.*暗", "冷却", "迅捷", "迸发", "蓄力", "传动", "复制",
              "抽取", "召唤", "周末", "神秘", "隐藏", "剑", "秘技", "诡计", "假装",
              "假象", "宝物", "诅咒", "禁制", "封印", "变身", "守护", "反弹", "无效",
              "阻止", "反噬", "抵抗", "仅可使用", "位置不会", "解开", "祝福", "污染",
              "传染", "汲取", "伪装", "魔力值", "变化效果")),
    # T-P5：事件钩子 / 场地状态
    ("T-P5", ("入场", "出场", "返场", "离场", "回合结束", "回合开始", "使用.{0,4}技能后",
              "每使用", "受到", "致命", "被击倒", "击败", "击杀", "每场战斗", "首次",
              "天气", "替换", "应对成功", "后于对手行动", "每行动", "行动后", "每次行动")),
    # T-P4：印记 / 异常状态
    ("T-P4", ("印记", "灼烧", "中毒", "冰冻", "冻结", "眩晕", "禁足", "萌化", "寄生",
              "引电", "毒", "减速", "每2层")),
    # T-P3：条件 / 威力 / 能耗 / 增益层数 / 克制（依赖 SkillInstance + 条件判定 + E2 克制表）
    ("T-P3", ("克制", "血量", "低于50%", "生命", "威力", "技能变为", "系别技能", "系技能",
              "能耗", "增益", "减益", "层数", "消耗", "使用能耗", "首个技能", "力竭时",
              "技能位置", "位置", "魔力值", "队伍存在", "每有1", "能量等于", "能量为",
              "能量大", "能量小")),
    # T-P2：简单原语（连击 / 先手 / 能量 / 回复 / 吸血）
    ("T-P2", ("连击", "先手", "回复.{0,4}能量", "回复.{0,4}生命", "吸血", "能量", "速度")),
]

# ── 人工订正：边界情况 ──
_OVERRIDE: dict[str, str] = {
    "壮胆": "T-P3",        # 队伍存在虫系 → 条件
    "完全偏振": "T-P6",     # 抵抗系别伤害 → 免疫/抵抗
    "宝剑王牌": "T-P6",     # 位置限制
    "正位宝剑": "T-P6",     # 位置限制
    "防过载保护": "T-P5",    # 每次行动后脱离 → 钩子
}

_BATCH_ORDER = ("T-P1", "T-P2", "T-P3", "T-P4", "T-P5", "T-P6")
_DESC = {
    "T-P1": "无条件常驻属性（被动系统 + 效果原语即可表达；真·P1 近乎为空，见说明）",
    "T-P2": "简单原语：连击 / 先手 / 能量 / 回复 / 吸血（技能 P2 基建）",
    "T-P3": "条件 / 威力 / 能耗 / 增益层数 / 克制（技能 P3 + SkillInstance + E2 克制表）",
    "T-P4": "印记 / 异常状态（技能 P4 状态目录 + 回合末 tick）",
    "T-P5": "事件钩子 / 天气（技能 P5 hook 分发器 + FieldState）",
    "T-P6": "特殊机制 / 游戏系统（闪避·免疫·冷却·迅捷·迸发·魔力值·奉献…需单独评估）",
}


def batch_of(desc: str) -> str:
    for batch, pats in _FEATURES:
        if any(re.search(p, desc) for p in pats):
            return batch
    return "T-P1"


def main() -> int:
    spirits = json.loads(SPIRITS_FILE.read_text(encoding="utf-8"))
    # 特性名 → desc（同名 = 同一特性，去重）
    traits: dict[str, str] = {}
    usage: dict[str, list[str]] = defaultdict(list)
    for s in spirits:
        t = s.get("trait")
        if isinstance(t, dict) and t.get("name"):
            traits.setdefault(t["name"], t["desc"])
            usage[t["name"]].append(s["name"])
    if len(traits) != 227:
        print(f"⚠ 期望 227 个唯一特性，实际 {len(traits)}")
        return 1

    buckets: dict[str, list[dict]] = defaultdict(list)
    for name, desc in sorted(traits.items()):
        b = _OVERRIDE.get(name) or batch_of(desc)
        users = sorted(usage[name])
        buckets[b].append({
            "name": name,
            "desc": desc,
            "used_by": len(users),
            "spirits": users[:8],       # 样例，完整名单在 full_spirits.json 按名可查
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for b in _BATCH_ORDER:
        lst = sorted(buckets[b], key=lambda x: -x["used_by"])
        doc = {"batch": b, "说明": _DESC[b], "count": len(lst), "traits": lst}
        out = OUT_DIR / f"batch-{b}.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        total += len(lst)
        print(f"{b}: {len(lst):>3} 个 → {out.name}")
    print(f"合计 {total}/{len(traits)} 个唯一特性已归类")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""属性公式：种族值 + 个体值 + 性格 → 最终六维。

真实公式由负责人给定（替代原占位公式）：

    生命：    (1.7 × (种族值 + 个体值×3) + 70) × 性格修正 + 100
    其他五维：(1.1 × (种族值 + 个体值×3) + 50) × 性格修正 + 50
"""

from __future__ import annotations

from .dataset import _to_int

# 性格 → (升 20% 的属性, 降 10% 的属性)。未知性格回退「坦率」（不抛）。
# 非中性性格共 30 种：提升六维之一、降低另外五维之一（6 × 5），命名格式固定为
# 「加『某维』减『另一维』」，如 "加攻击减速度" = 升物攻、降速度。
NATURE_BONUS: dict[str, tuple[str, str]] = {
    # ── 升生命 ──
    "加生命减攻击": ("hp", "atk"),
    "加生命减魔攻": ("hp", "sp_atk"),
    "加生命减物防": ("hp", "def"),
    "加生命减魔防": ("hp", "sp_def"),
    "加生命减速度": ("hp", "speed"),
    # ── 升攻击（物攻）──
    "加攻击减生命": ("atk", "hp"),
    "加攻击减魔攻": ("atk", "sp_atk"),
    "加攻击减物防": ("atk", "def"),
    "加攻击减魔防": ("atk", "sp_def"),
    "加攻击减速度": ("atk", "speed"),
    # ── 升魔攻 ──
    "加魔攻减生命": ("sp_atk", "hp"),
    "加魔攻减攻击": ("sp_atk", "atk"),
    "加魔攻减物防": ("sp_atk", "def"),
    "加魔攻减魔防": ("sp_atk", "sp_def"),
    "加魔攻减速度": ("sp_atk", "speed"),
    # ── 升物防 ──
    "加物防减生命": ("def", "hp"),
    "加物防减攻击": ("def", "atk"),
    "加物防减魔攻": ("def", "sp_atk"),
    "加物防减魔防": ("def", "sp_def"),
    "加物防减速度": ("def", "speed"),
    # ── 升魔防 ──
    "加魔防减生命": ("sp_def", "hp"),
    "加魔防减攻击": ("sp_def", "atk"),
    "加魔防减魔攻": ("sp_def", "sp_atk"),
    "加魔防减物防": ("sp_def", "def"),
    "加魔防减速度": ("sp_def", "speed"),
    # ── 升速度 ──
    "加速度减生命": ("speed", "hp"),
    "加速度减攻击": ("speed", "atk"),
    "加速度减魔攻": ("speed", "sp_atk"),
    "加速度减物防": ("speed", "def"),
    "加速度减魔防": ("speed", "sp_def"),
}

# 性格倍率：命中提升项 +20%、命中降低项 −10%。
_NATURE_BONUS_RATE = 1.20
_NATURE_PENALTY_RATE = 0.90

# 属性公式常量（真实公式，来自负责人）。
_HP_GROWTH_FACTOR = 1.7
_HP_GROWTH_BASE = 70
_HP_FLAT_BASE = 100
_STAT_GROWTH_FACTOR = 1.1
_STAT_GROWTH_BASE = 50
_STAT_FLAT_BASE = 50
_IV_POINTS_PER_POINT = 3  # 个体值每点折合 3 点修正值

# 中性性格：不进 NATURE_BONUS（那 30 种全是非中性），作为未知回退存在。
NEUTRAL_NATURE = "坦率"


def is_valid_nature(name: str) -> bool:
    """合法性格 = 中性「坦率」或 30 种非中性之一。"""
    return name == NEUTRAL_NATURE or name in NATURE_BONUS


def calc_combat_stats(base: dict[str, int], iv: dict[str, int] | None = None,
                      nature: str = NEUTRAL_NATURE) -> dict[str, int]:
    """真实属性公式（负责人给定，替代原占位公式）：

        生命：    (1.7 × (种族值 + 个体值×3) + 70) × 性格修正 + 100
        其他五维：(1.1 × (种族值 + 个体值×3) + 50) × 性格修正 + 50

    性格修正：命中提升项 ×1.20、命中降低项 ×0.90、其余 ×1.0；
    未知性格（含「坦率」）回退中性而不抛。逐项 int() 向下取整。
    """
    iv = iv or {}
    plus, minus = NATURE_BONUS.get(nature, ("", ""))
    out: dict[str, int] = {}
    for key, base_val in base.items():
        growth_value = base_val + _to_int(iv.get(key), default=0) * _IV_POINTS_PER_POINT
        if key == "hp":
            raw = _HP_GROWTH_FACTOR * growth_value + _HP_GROWTH_BASE
            flat = _HP_FLAT_BASE
        else:
            raw = _STAT_GROWTH_FACTOR * growth_value + _STAT_GROWTH_BASE
            flat = _STAT_FLAT_BASE
        if key == plus:
            raw *= _NATURE_BONUS_RATE
        elif key == minus:
            raw *= _NATURE_PENALTY_RATE
        out[key] = int(raw + flat)
    return out

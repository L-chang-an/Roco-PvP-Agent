"""E0b 测试阵容助手（辅助模块，非测试）。

一律推导式逐个构造，绝不 `[spec] * n`——参考项目的测试里就有 `[{...}] * 6`，
6 个引用指向同一个 dict，任何一处 mutate 就全队串味。
"""

from __future__ import annotations

from dataclasses import replace

from environment.models import new_battle
from environment.rules import DEFAULT_RULES
from environment.teambuilder import TeamPick, build_roster

# 1v1 对局规则：测试多用手写单只阵容 + team_size=1 精确控制能量/出手。
RULES_1V1 = replace(DEFAULT_RULES, team_size=1)


def pick(spirit: str, skills: list[str], **kw) -> TeamPick:
    return TeamPick(spirit=spirit, skills=skills, **kw)


def team(*picks: TeamPick) -> list[dict]:
    return build_roster(list(picks))


def spec(name: str, hp: int, atk: int, spa: int, de: int, spd: int, spe: int,
         skills: list[str], types: list[str] | None = None) -> dict:
    """手写 roster spec（build_roster 的输出形状），测试用精确六维。"""
    return {
        "name": name,
        "types": types or ["普通"],
        "stats": {"hp": hp, "atk": atk, "sp_atk": spa, "def": de, "sp_def": spd, "speed": spe},
        "skills": list(skills),
        "nature": "坦率",
        "bloodline": "",
        "iv": {},
    }


def mirror_pair() -> tuple[list[dict], list[dict]]:
    """双方同规格同速 → 平手硬币必然有机会触发（FULL 精灵 + battle_ready 技能）。"""
    picks = [pick("迪莫", ["闪光", "力量增效"]), pick("喵喵", ["抓挠", "休息回复"]),
             pick("火花", ["火苗", "力量增效"])]
    return team(*picks), team(*picks)


def duel() -> tuple[list[dict], list[dict]]:
    """1v1 平衡对局（配 RULES_1V1）：a 先手（速 100>90），攻击+状态+防御齐全。"""
    a = [spec("甲", 300, 100, 100, 100, 100, 100, ["抓挠", "力量增效", "防御"])]
    b = [spec("乙", 300, 100, 100, 100, 100, 90, ["拍击", "魔法增效", "防御"])]
    return a, b


def strong_weak() -> tuple[list[dict], list[dict]]:
    """1v1：a 一击必杀（速 100）vs b 挠痒痒（速 50）→ a 必胜。"""
    a = [spec("强攻", 500, 100, 100, 100, 100, 100, ["抓挠"])]
    b = [spec("弱靶", 50, 20, 20, 20, 20, 50, ["拍击"])]
    return a, b


def tanky_pair() -> tuple[list[dict], list[dict]]:
    """1v1：双方高血高防低攻 → 只可能平局（max_turns 兜底）。"""
    a = [spec("肉盾甲", 5000, 1, 1, 500, 500, 100, ["抓挠"])]
    b = [spec("肉盾乙", 5000, 1, 1, 500, 500, 100, ["抓挠"])]
    return a, b


def fast_slow() -> tuple[list[dict], list[dict]]:
    """1v1：速度互异 → 永不平手（引擎 rng.calls 恒 0）。"""
    a = [spec("快攻", 5000, 1, 1, 500, 500, 200, ["抓挠"])]
    b = [spec("慢防", 5000, 1, 1, 500, 500, 100, ["抓挠"])]
    return a, b

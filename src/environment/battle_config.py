"""管理员对局配置：对局规模（team_size / lives / skill_slots）的唯一入口，为 Web UI 做准备。

规则（负责人 2026-08-25 定 3–6，2026-08-29 收窄为 {3, 6}）：
- 每方可携带精灵数（team_size）：**只能 3 或 6**（3V3 / 6V6）。
- 每方命数（lives）：**≥ 1 且 < team_size**。

本模块是**纯函数、无全局状态**——Web UI 端持有配置值，调 `validate_*` 校验输入、
`build_battle_rules` 构造引擎规则。`BattleRules` 本身保持无约束（测试用 team_size=1/2 直构），
约束只落在这层接口。

对局规模在引擎里的体现：`new_battle` 校验 `len(roster) == rules.team_size`；命数进
`SideState.lives`（每个精灵倒下扣 1 命，命归零判负）。
"""

from __future__ import annotations

from .rules import DEFAULT_RULES, BattleRules

MIN_TEAM_SIZE = 3
MAX_TEAM_SIZE = 6

# 允许的对局规模（负责人 2026-08-29 拍板：只允许 3 或 6）。
ALLOWED_TEAM_SIZES = (MIN_TEAM_SIZE, MAX_TEAM_SIZE)

DEFAULT_TEAM_SIZE = 3
DEFAULT_LIVES = 2
DEFAULT_SKILL_SLOTS = 4


def validate_team_size(n) -> str | None:
    """每方精灵数是否合法（整数且 ∈ {3, 6}）。合法 → None，否则中文原因。"""
    if isinstance(n, bool) or not isinstance(n, int):
        return f"对局精灵数必须是整数，实际 {n!r}。"
    if n not in ALLOWED_TEAM_SIZES:
        return f"对局精灵数必须是 3 或 6（3V3/6V6），实际 {n}。"
    return None


def validate_lives(team_size, lives) -> str | None:
    """每方命数是否合法（≥1 且 < team_size）。合法 → None，否则中文原因。"""
    err = validate_team_size(team_size)
    if err:
        return err   # 先保证 team_size 本身合法，否则 team_size−1 无意义
    if isinstance(lives, bool) or not isinstance(lives, int):
        return f"命数必须是整数，实际 {lives!r}。"
    if not 1 <= lives < team_size:
        return f"命数必须在 1–{team_size - 1} 之间（且小于精灵数 {team_size}），实际 {lives}。"
    return None


def build_battle_rules(*, team_size: int = DEFAULT_TEAM_SIZE, lives: int = DEFAULT_LIVES,
                       skill_slots: int = DEFAULT_SKILL_SLOTS,
                       **overrides) -> BattleRules:
    """管理员接口：校验通过后构造 `BattleRules`（含可选覆盖，如 max_turns）。

    非法配置 → ValueError（Web UI 可捕获并回显错误）。合法的对局规模/命数/技能槽位来自这里。
    """
    err = validate_lives(team_size, lives)
    if err:
        raise ValueError(err)
    if isinstance(skill_slots, bool) or not isinstance(skill_slots, int) or skill_slots < 1:
        raise ValueError(f"每只精灵可携带技能数必须是 ≥1 的整数，实际 {skill_slots!r}。")
    return BattleRules(team_size=team_size, lives=lives, skill_slots=skill_slots, **overrides)


def default_rules() -> BattleRules:
    """管理员默认配置（3V3 / 2 命 / 4 技能）。DEFAULT_RULES 的值应与之一致（测试钉死）。"""
    return build_battle_rules()

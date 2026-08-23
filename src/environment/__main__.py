"""CLI 入口：python -m environment。

E0a 阶段三个子命令（全部只读、不写文件、不联网）：
  --data-report    E0 数据自检：技能/效果表/精灵/防御.power 等
  --team-report    打印双方默认阵容与最终六维，末尾校验两队合法
  --probe-errors   演示一个处处违规的阵容被 validate_team 逐条拒绝（Gate 核对用）
E0b 再补 battle 子命令。
"""

from __future__ import annotations

import argparse

from .dataset import load_skills, load_spirits
from .rules import E0_ITEMS
from .skillbook import E0_EFFECTS, KIND_TO_CATEGORY
from .teambuilder import TeamPick, build_roster, validate_team


def _pool_range(spirits) -> tuple[int, int]:
    """跨全部精灵：未选血脉的最小池 / 选了血脉的最大池。"""
    no_bloodline = [len(sp.skills_default) for sp in spirits.values()]
    with_bloodline = [
        len(set(sp.skills_default) | set(sp.skills_bloodline)) for sp in spirits.values()
    ]
    return min(no_bloodline), max(with_bloodline)


def _data_report() -> int:
    """E0 数据自检。任一检查不过 → 返回 1（可进 CI 作门禁）。"""
    skills = load_skills()
    spirits = load_spirits()

    cats = {"攻击": 0, "防御": 0, "状态": 0}
    for s in skills.values():
        category = KIND_TO_CATEGORY.get(s.kind)
        key = category.value if category else s.kind
        cats[key] = cats.get(key, 0) + 1

    keys_ok = set(E0_EFFECTS) == set(skills)
    stats_normalized = all(
        isinstance(v, int) for sp in spirits.values() for v in sp.stats.values()
    )
    pool_min, pool_max = _pool_range(spirits)
    def_power = skills["防御"].power if "防御" in skills else None
    def_ok = def_power == 0
    ok = keys_ok and stats_normalized and def_ok

    print("E0 数据自检")
    print(
        f"技能 {len(skills)} 条（攻击 {cats['攻击']} / 防御 {cats['防御']} / 状态 {cats['状态']}）"
        f" · 效果表 {len(E0_EFFECTS)} 条 · 键集合一致 {'✅' if keys_ok else '❌'}"
    )
    print(
        f"精灵 {len(spirits)} 只 · 六维{'已归一' if stats_normalized else '未归一'} "
        f"· 可学池最小 {pool_min} 最大 {pool_max}"
    )
    print(f"防御.power = {def_power}（不是 30）{'✅' if def_ok else '❌'}")
    if not ok:
        print("数据自检未通过。")
        return 1
    return 0


def _default_picks_a() -> list[TeamPick]:
    """队伍 a：演示血脉拓宽可学池（3 技能）+ 个体值 + 性格都真实改六维。"""
    return [
        TeamPick("迪莫", ["抓挠2", "加速度", "防御1"], bloodline="火"),
        TeamPick("小火猴", ["抓挠", "撞击1"], nature="加攻击减速度", iv={"atk": 10}),
        TeamPick("水蓝蓝", ["撞击", "加魔攻"], nature="加速度减攻击", iv={"speed": 10}),
    ]


def _default_picks_b() -> list[TeamPick]:
    """队伍 b：部分走默认（坦率 / 无个体值 / 无血脉），验证中性口径。"""
    return [
        TeamPick("圣水迪莫", ["撞击1", "加魔攻"]),
        TeamPick("布布种子", ["抓挠2", "防御1"], nature="加物防减魔防"),
        TeamPick("猫老大", ["撞击", "加物攻"], nature="加攻击减魔攻"),
    ]


def _print_team(label: str, picks: list[TeamPick]) -> None:
    print(f"队伍 {label}")
    for entry in build_roster(picks):
        types = "/".join(entry["types"])
        skills = " / ".join(entry["skills"])
        bl = f" 血脉:{entry['bloodline']}" if entry["bloodline"] else ""
        print(
            f"  {entry['name']:<5} {types:<4}{bl:<8} 性格:{entry['nature']}  "
            f"iv={entry['iv']!r:<14} {skills}"
        )
        st = entry["stats"]
        print(
            f"         六维 hp{st['hp']} atk{st['atk']} spa{st['sp_atk']} "
            f"def{st['def']} spd{st['sp_def']} spe{st['speed']}"
        )
    items = " / ".join(f"{name} ×{count}" for name, count in E0_ITEMS.items())
    print(f"  道具  {items}")


def _team_report() -> int:
    picks_a, picks_b = _default_picks_a(), _default_picks_b()
    _print_team("a", picks_a)
    print()
    _print_team("b", picks_b)
    print()
    errors = validate_team(picks_a, ["草魔法"]) + validate_team(picks_b, ["草魔法"])
    if errors:
        for err in errors:
            print(f"❌ {err}")
        return 1
    print("校验：两队合法 ✅")
    return 0


def _probe_errors() -> int:
    """构造一个处处违规的阵容，演示 validate_team 一次报出全部错误。"""
    bad = [
        TeamPick("迪莫", []),                                        # 技能数不足（0 个）
        TeamPick("不存在的精灵", ["抓挠1", "加物攻"]),               # 精灵不存在
        TeamPick("小火猴", ["撞击2", "加速度"], bloodline="水"),      # 血脉非法 + 两个技能不可学
        TeamPick("水蓝蓝", ["撞击", "加魔攻"], iv={"luck": 5, "atk": 99}),  # iv 键非法 + 越界
        TeamPick("猫老大", ["撞击", "加物攻"], iv={"atk": 3, "def": 3, "sp_def": 3, "speed": 3}),  # 4 个维度
    ]
    print("非法阵容演示（validate_team 一次报出全部错误，不遇到第一个就停）：")
    errors = validate_team(bad, ["不存在道具", "草魔法", "草魔法"])
    for err in errors:
        print(f"  ❌ {err}")
    print(f"共 {len(errors)} 条错误。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="environment", description="Rock PVP battle environment（E0a：组队与数据层）"
    )
    parser.add_argument("--team-report", action="store_true", help="打印双方默认阵容与最终六维")
    parser.add_argument("--data-report", action="store_true", help="E0 数据自检")
    parser.add_argument("--probe-errors", action="store_true", help="演示非法阵容被逐条拒绝")
    args = parser.parse_args()

    if args.data_report:
        return _data_report()
    if args.probe_errors:
        return _probe_errors()
    if args.team_report:
        return _team_report()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

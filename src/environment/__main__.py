"""CLI 入口：python -m environment。

数据自检子命令（全部只读、不写文件、不联网），`--data FULL|VALID` 切数据源：
  --data-report    数据自检：技能/系别/精灵/家族/首领（FULL）或 179 技能 + 593 精灵（VALID）
  --team-report    打印阵容与最终六维，末尾校验合法（用 --spirit 选精灵）
  --probe-errors   演示一个处处违规的阵容被 validate_team 逐条拒绝
battle 子命令：随机策略自对战一局（同 seed 逐字节复现），吃 FULL/VALID 数据；管理员经
battle_config 配 --team-size（3 或 6）与 --lives（1..team_size−1），为 Web UI 做准备。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from dataclasses import asdict
from pathlib import Path

from .actions import Decision
from .battle_config import build_battle_rules
from .dataset import (DEFAULT_SOURCE, DataSource, load_families, load_skills, load_skipped_spirits,
                      load_spirits, load_types)
from .match import MatchResult, run_match
from .models import SIDES
from .players import RandomPlayer
from .presets import p1_preset
from .rules import DEFAULT_RULES, ITEMS
from .session import BattleSession
from .skillbook import KIND_TO_CATEGORY, P1_EFFECTS, P2_EFFECTS, battle_ready
from .teambuilder import TeamPick, build_roster, validate_team
from .traits import DEFAULT_TRAIT_NAME, resolve_trait_name, trait_implemented
from .view import observe
from .visibility import filter_events_for


# ── E0a 部分 ────────────────────────────────────────────────────────────────
def _pool_range(spirits) -> tuple[int, int]:
    """跨全部精灵：未选血脉的最小池 / 选了血脉的最大池。"""
    no_bloodline = [len(sp.skills_default) for sp in spirits.values()]
    with_bloodline = [
        len(set(sp.skills_default) | set(sp.skills_bloodline)) for sp in spirits.values()
    ]
    return min(no_bloodline), max(with_bloodline)


def _data_report(source: DataSource = DEFAULT_SOURCE, effects: bool = False) -> int:
    """数据自检。任一检查不过 → 返回 1（可进 CI 作门禁）。"""
    return _data_report_full(source=source, effects=effects)


def _effects_coverage() -> tuple[int, int, int, int, list[str]]:
    """(可对战数, FULL 总数, P1 数, P2 数, 未支持列表)。"""
    full = load_skills(DataSource.FULL)
    ready = [n for n in full if battle_ready(n)]
    return (len(ready), len(full),
            sum(1 for n in ready if n in P1_EFFECTS),
            sum(1 for n in ready if n in P2_EFFECTS),
            [n for n in full if not battle_ready(n)])


def _traits_coverage() -> tuple[int, int, int, int]:
    """(已实现特性数, 唯一特性总数, 真实特性精灵数, 装白板精灵数)。"""
    spirits = load_spirits(DataSource.FULL)
    names = {s.trait_name for s in spirits.values() if s.trait_name}
    impl = {n for n in names if trait_implemented(n)}
    real = sum(1 for s in spirits.values() if trait_implemented(s.trait_name))
    return len(impl), len(names), real, len(spirits) - real


def _data_report_full(source: DataSource = DataSource.FULL, effects: bool = False) -> int:
    """FULL/VALID 真实数据自检：规模 / 跳过 / 家族 / 首领 / 系别 / 技能引用 1:1。

    FULL：553 技能全量。VALID（E3）：179 个已实装效果的技能 + FULL 精灵表——
    精灵引用非白名单技能是**预期**（不是缺漏），故 VALID 跳过「引用缺漏」检查。
    --effects 附 P1/P2 覆盖率与特性目录。
    """
    skills = load_skills(source)
    spirits = load_spirits(source)
    skipped = load_skipped_spirits(source)
    families = load_families(source)
    types = load_types(source)
    bosses = sum(1 for s in spirits.values() if s.is_boss)

    cats = {"攻击": 0, "防御": 0, "状态": 0}
    for s in skills.values():
        category = KIND_TO_CATEGORY.get(s.kind)
        key = category.value if category else s.kind
        cats[key] = cats.get(key, 0) + 1

    title = "VALID 数据自检（E3：FULL 精灵 + 白名单技能）" if source is DataSource.VALID \
        else "FULL 数据自检"
    print(title)
    print(f"技能 {len(skills)} 条（攻击 {cats['攻击']} / 防御 {cats['防御']} / 状态 {cats['状态']}）"
          f" · 系别 {len(types)} 种")
    skip_txt = "、".join(f"{n}({r})" for n, r in skipped) if skipped else "无"
    print(f"精灵 {len(spirits)} 只 · 跳过 {len(skipped)} 条（{skip_txt}）"
          f" · 家族 {len(families)} 个 · 首领 {bosses} 只")
    if source is DataSource.FULL:
        refs = set()
        for sp in spirits.values():
            for pool in (sp.skills_default, sp.skills_bloodline, sp.skills_stone, sp.skills_legend):
                refs.update(pool)
        missing = refs - set(skills)
        print(f"技能引用缺漏 {len(missing)} {'✅' if not missing else '❌'}")
        ok = not missing
    else:
        ok = True
    if effects:
        ready, total, p1, p2, unsupported = _effects_coverage()
        print(f"可对战白名单（P1 ∪ P2）：{ready}/{total}（{ready * 100 // total}%）"
              f" · P1 {p1}/125 全实现 · P2 {p2}/54 全实现")
        t_impl, t_total, t_real, t_blank = _traits_coverage()
        print(f"特性目录：{t_impl}/{t_total} 已实现 · 真实特性精灵 {t_real} 只 ·"
              f" 装白板「{DEFAULT_TRAIT_NAME}」{t_blank} 只（零效果，可正常上场）")
        if unsupported:
            print(f"未支持 {len(unsupported)} 条：{'、'.join(sorted(unsupported)[:20])}"
                  f"{'…' if len(unsupported) > 20 else ''}")
    if not ok:
        print("数据自检未通过。")
        return 1
    return 0


# FULL 默认演示阵容：三只不同家族，迪莫带光血脉 + 光系血脉技，展示规则 2 通过的样子。
_FULL_DEMO = [
    TeamPick("迪莫", ["折线冲击"], bloodline="光"),
    TeamPick("喵喵", ["抓挠"]),
    TeamPick("火花", ["火苗"]),
]


def _print_team(label: str, picks: list[TeamPick], source: DataSource) -> None:
    print(f"队伍 {label}")
    for entry in build_roster(picks, source):
        types = "/".join(entry["types"])
        skills = " / ".join(entry["skills"])
        bl = f" 血脉:{entry['bloodline']}" if entry["bloodline"] else ""
        print(
            f"  {entry['name']:<6} {types:<8}{bl:<10} 性格:{entry['nature']}  "
            f"iv={entry['iv']!r:<14} {skills}"
        )
        st = entry["stats"]
        print(
            f"          六维 hp{st['hp']} atk{st['atk']} spa{st['sp_atk']} "
            f"def{st['def']} spd{st['sp_def']} spe{st['speed']}"
        )
        raw_trait = entry.get("trait") or "（无）"
        equipped = resolve_trait_name(entry.get("trait") or "")
        mark = "" if equipped != DEFAULT_TRAIT_NAME else f" → 白板「{DEFAULT_TRAIT_NAME}」（未实现，零效果）"
        print(f"          特性 {raw_trait}{mark}")
    items = " / ".join(f"{name} ×{count}" for name, count in ITEMS.items())
    print(f"  道具  {items}")


def _team_report(source: DataSource, spirit_names: list[str]) -> int:
    return _team_report_full(source, spirit_names)


def _team_report_full(source: DataSource, spirit_names: list[str]) -> int:
    """FULL/VALID 组队报告：--spirit 重复参数选精灵（每个自动带第一个 battle_ready 默认技能），
    无参数用默认演示阵容。先校验，非法只报错不打印阵容。"""
    if not spirit_names:
        picks = _FULL_DEMO
    else:
        spirits = load_spirits(source)
        picks = []
        for name in spirit_names:
            if name not in spirits:
                picks.append(TeamPick(name, ["？"]))
                continue
            pool = [s for s in spirits[name].skills_default if battle_ready(s)]
            picks.append(TeamPick(name, pool[:1] if pool else ["？"]))
    errors = validate_team(picks, ["草魔法"], source=source)
    if errors:
        print(f"{source.value} 队伍不合法：")
        for err in errors:
            print(f"❌ {err}")
        return 1
    _print_team(source.value, picks, source)
    print()
    print("校验：队伍合法 ✅")
    return 0


def _probe_errors(source: DataSource) -> int:
    """构造一个处处违规的阵容，演示 validate_team 一次报出全部错误。"""
    return _probe_errors_full()


def _probe_errors_full() -> int:
    """FULL 非法阵容：同时踩 家族唯一 / 首领 / 血脉系别 / 技能数 / 道具。"""
    bad = [
        TeamPick("迪莫", ["折线冲击"]),                  # 血脉技能未选血脉
        TeamPick("圣光迪莫", ["闪光"]),                  # 首领形态 + 与迪莫同族
        TeamPick("喵喵", ["抓挠"]),
        TeamPick("喵呜", ["抓挠"]),                      # 与喵喵同族
        TeamPick("火花", ["折线冲击"], bloodline="火"),   # 血脉技能系别不符
    ]
    print("FULL 非法阵容演示（家族/首领/血脉/规模/道具一次报全）：")
    errors = validate_team(bad, ["不存在道具", "草魔法", "草魔法"], source=DataSource.FULL)
    for err in errors:
        print(f"  ❌ {err}")
    print(f"共 {len(errors)} 条错误。")
    return 0


# ── battle 部分 ───────────────────────────────────────────────────────────────
def _battle_picks(preset: str, source: DataSource, team_size: int = 3) -> tuple[list[TeamPick], list[TeamPick]]:
    """FULL/VALID 下只支持 p1（真数据队，规模随 team_size 自适应，见 presets.py）。"""
    if preset != "p1":
        raise ValueError(f"{source.value} 对战只支持 --preset p1，实际 {preset!r}。")
    return p1_preset(team_size)


def _summary_from_events(record, side: str) -> str:
    """从该回合事件流反推一方主动作摘要（人眼能看懂的出手方向）。"""
    for e in record.events:
        if e.get("side") != side:
            continue
        t = e["type"]
        if t in ("damage", "stat_change", "reduce_arm"):
            return e.get("skill", "?")
        if t == "switch":
            return f"换→{e.get('in', '?')}"
        if t == "recharge":
            return "聚能"
        if t == "skipped":
            return f"跳过({e.get('reason', '?')})"
    dec = record.decision_a if side == "a" else record.decision_b
    atype = dec.action.get("type")
    return {"skill": "技能", "switch": "换人", "recharge": "聚能"}.get(atype, atype or "?")


def _turn_header(record) -> str:
    parts = []
    for side in SIDES:
        dec = record.decision_a if side == "a" else record.decision_b
        s = _summary_from_events(record, side)
        if dec.item:
            s += f" +{dec.item}"
        parts.append(f"{side}:{s}")
    return f"T{record.turn:02d}  {'   '.join(parts)}"


def _event_line(e: dict) -> str:
    side = e.get("side", "")
    pfx = f"{side} " if side else ""
    t = e["type"]
    if t == "item_use":
        line = f"item_use {pfx}{e['item']}"
        if "uses_left" in e:
            line += f"(剩{e['uses_left']})"
        return line
    if t == "heal":
        if "applied" in e:
            return f"heal {pfx}{e['unit']} +{e['applied']}(overflow={e['overflow']}) hp={e['hp']}"
        return f"heal {pfx}{e['unit']}（回复生命，数值迷雾遮蔽）"
    if t == "reduce_arm":
        return f"reduce_arm {pfx}{e['skill']} {int(e['pct'] * 100)}% armed={e['armed']}"
    if t == "damage":
        line = f"damage {pfx}{e['attacker']}→{e['target']} {e['damage']}"
        if "target_hp_pct" in e:
            line += f"(剩{e['target_hp_pct']}%)"      # E4 迷雾：敌方血量以百分比呈现
        else:
            line += f"(剩{e['target_hp_left']})"
        if e.get("hits", 1) > 1:
            line += f" 连击{e['hit']}/{e['hits']}"
        if e["counter"]:
            line += f" 应对{e['counter']}×{e['mult']}"
        if e["reduced"]:
            line += f" 减伤{int(e['reduced'] * 100)}%"
        if e.get("eff", 1.0) != 1.0:
            line += f" 系别×{e['eff']}"
        if e.get("stab", 1.0) != 1.0:
            line += f" 本系×{e['stab']}"
        return line
    if t == "stat_change":
        if "stat" not in e:
            return f"stat_change {pfx}{e['unit']}（能力变化，层数迷雾遮蔽）"
        line = f"stat_change {pfx}{e['unit']} {e['stat']} {e['mode']} {e['layers']}层(共{e['total_layers']})"
        if e.get("target") == "foe":
            line += " 目标敌方"
        if e["counter"]:
            line += f" 应对{e['counter']}"
        return line
    if t == "energy_gain":
        tag = {"self": "", "bench": " 场下", "foe": " 目标敌方"}.get(e.get("target", "self"), "")
        return f"energy_gain {pfx}{e['unit']} +{e['gained']} energy={e['energy']}{tag}"
    if t == "steal":
        return f"steal {pfx}{e['unit']} ← {e['foe']} +{e['gained']} energy={e['energy']}"
    if t == "recharge":
        return f"recharge {pfx}{e['unit']} +{e['gained']} energy={e['energy']}"
    if t == "switch":
        line = f"switch {pfx}{e['out']}→{e['in']}"
        if "cleared_layers" in e:
            line += f" 清{e['cleared_layers']}层"
        return line
    if t == "replace":
        return f"replace {pfx}{e['out']}→{e['in']}"
    if t == "faint":
        return f"faint {pfx}{e['unit']}"
    if t == "life_loss":
        return f"life_loss {pfx}{e['unit']} 命剩{e['lives_left']}"
    if t == "skipped":
        return f"skipped {pfx}{e['unit']} ({e['reason']})"
    if t == "battle_end":
        msg = e.get("message")
        head = f"battle_end winner={e.get('winner')} turn={e['turn']}"
        return head + (f"  {msg}" if msg else "")
    if t == "error":
        return f"error {e.get('message')}"
    return f"{t} {pfx}{{…}}"


def _play_once(roster_a, roster_b, args, rules):
    session = BattleSession.start(roster_a, roster_b, seed=args.seed, rules=rules,
                                  battle_id=f"cli-{args.seed}")
    players = {
        "a": RandomPlayer("a", seed=args.seed_a if args.seed_a is not None else args.seed + 1),
        "b": RandomPlayer("b", seed=args.seed_b if args.seed_b is not None else args.seed + 2),
    }
    return session, run_match(session, players)


def _print_match(session, result: MatchResult, args, rules, source: DataSource) -> None:
    title = {"FULL": "FULL 真数据自对战", "VALID": "E3 真数据自对战"}.get(source.value, "自对战")
    print(f"{title}  seed={args.seed}  规则={rules.team_size}v{rules.team_size}/"
          f"{rules.lives}命/能量{rules.energy_max}/上限{rules.max_turns}  预设={args.preset}"
          + (f"  视角={args.viewer}" if args.viewer else ""))
    for record in result.turns:
        print(_turn_header(record))
        events = record.events
        if args.viewer:
            events = filter_events_for(args.viewer, events, session.state)   # E4 迷雾：事件按视角屏蔽
        for e in events:
            print(f"     | {_event_line(e)}")
    print(f"终局  winner={result.winner} turns={result.turn_count} done={result.done} "
          f"rng_calls={session.state.rng.calls} digest={result.digest()}")
    if args.viewer:
        obs = observe(session.state, args.viewer, mode="partial")
        print(f"观测（viewer={args.viewer}，迷雾口径：己方全见 / 敌方仅白名单）")
        print(json.dumps(obs, ensure_ascii=False, indent=2))


def _match_to_json(result: MatchResult, rng_calls: int) -> dict:
    return {
        "battle_id": result.battle_id,
        "seed": result.seed,
        "winner": result.winner,
        "done": result.done,
        "turn_count": result.turn_count,
        "rng_calls": rng_calls,
        "digest": result.digest(),
        "turns": [
            {"turn": t.turn,
             "decision_a": asdict(t.decision_a),
             "decision_b": asdict(t.decision_b),
             "events": t.events,
             "state_hash": t.state_hash}
            for t in result.turns
        ],
    }


def _run_battle(args) -> int:
    source = DataSource(args.data)
    # 管理员接口：对局规模（3–6 精灵 / 1..team_size−1 命）由 battle_config 校验
    rules = build_battle_rules(team_size=args.team_size, lives=args.lives)
    if args.turns is not None:
        rules = dataclasses.replace(rules, max_turns=args.turns)
    picks_a, picks_b = _battle_picks(args.preset, source, team_size=rules.team_size)
    roster_a = build_roster(picks_a, source, rules=rules)
    roster_b = build_roster(picks_b, source, rules=rules)

    if args.json:
        session, result = _play_once(roster_a, roster_b, args, rules)
        print(json.dumps(_match_to_json(result, session.state.rng.calls),
                         ensure_ascii=False, indent=2))
        return 0

    digests: list[str] = []
    metas: list[tuple[str, int, int]] = []      # (winner, turns, rng_calls)
    for i in range(args.repeat):
        session, result = _play_once(roster_a, roster_b, args, rules)
        digests.append(result.digest())
        metas.append((result.winner or "平局", result.turn_count, session.state.rng.calls))
        if not args.quiet and args.repeat == 1:
            _print_match(session, result, args, rules, source)

    if args.quiet or args.repeat > 1:
        for i, (winner, turns, calls) in enumerate(metas, start=1):
            print(f"run#{i}  winner={winner} turns={turns} rng_calls={calls} digest={digests[i - 1]}")
        if len(set(digests)) == 1:
            print(f"确定性：{len(digests)} 次 digest 一致 ✅")
            return 0
        print("确定性：digest 不一致 ❌")
        return 1
    return 0


def _run_replay(args) -> int:
    """重放一条轨迹记录（E6）：逐回合比对 state_hash，输出比对表。失配/坏文件 → 1。"""
    from .replay import replay_record
    try:
        record = json.loads(Path(args.path).read_text(encoding="utf-8"))
        out = replay_record(record)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"重放失败：{exc}")
        return 1
    for t in out["turns"]:
        mark = "✅" if t["match"] else "❌"
        print(f"回合 {t['turn']:>3}  expected={t['expected'][:8]}… actual={t['actual'][:8]}… {mark}")
    total = len(out["turns"])
    verdict = f"共 {total} 回合，全部一致 ✅" if out["all_match"] else f"共 {total} 回合，存在失配 ❌"
    print(verdict)
    return 0 if out["all_match"] else 1


def main() -> int:
    """CLI 入口。输入：sys.argv（argparse 解析）；输出：进程退出码（0=成功，1=校验失败）。"""
    parser = argparse.ArgumentParser(
        prog="environment",
        description="Roco PVP battle environment（E0a：组队与数据层 / E0b：回合内核）",
    )
    parser.add_argument("--team-report", action="store_true", help="打印阵容与最终六维，末尾校验合法")
    parser.add_argument("--data-report", action="store_true", help="数据自检")
    parser.add_argument("--effects", action="store_true",
                        help="--data-report 附加：可对战白名单覆盖率 + 特性目录")
    parser.add_argument("--probe-errors", action="store_true", help="演示非法阵容被逐条拒绝")
    parser.add_argument("--data", choices=["FULL", "VALID"], default=DEFAULT_SOURCE.value,
                        help="数据源：FULL 真实 / VALID（E3，FULL 精灵+白名单技能，默认 FULL）")
    parser.add_argument("--spirit", action="append", default=None,
                        help="组队报告要选的精灵名（可重复；缺省用默认演示阵容）")

    sub = parser.add_subparsers(dest="command", help="子命令")
    bp = sub.add_parser("battle", help="随机策略自对战一局（同 seed 逐字节复现；--data VALID 用白名单队）")
    bp.add_argument("--data", choices=["FULL", "VALID"], default=DEFAULT_SOURCE.value,
                    help="数据源：FULL / VALID（默认 FULL；可在子命令前后放）")
    bp.add_argument("--team-size", type=int, default=3,
                    help="每方精灵数（管理员接口：3 或 6，默认 3V3）")
    bp.add_argument("--lives", type=int, default=2,
                    help="每方命数（管理员接口：1..team_size−1，默认 2）")
    bp.add_argument("--seed", type=int, default=20260823, help="引擎 seed（默认 20260823）")
    bp.add_argument("--seed-a", type=int, default=None, help="玩家 a seed（默认 seed+1）")
    bp.add_argument("--seed-b", type=int, default=None, help="玩家 b seed（默认 seed+2）")
    bp.add_argument("--preset", choices=["p1"], default="p1",
                    help="p1=真数据队（FULL/VALID，规模随 team_size 自适应）")
    bp.add_argument("--repeat", type=int, default=1, help="同参数跑 N 次并比对 digest")
    bp.add_argument("--turns", type=int, default=None, help="覆盖 rules.max_turns")
    bp.add_argument("--viewer", choices=["a", "b"], default=None,
                    help="以玩家视角展示（E4 迷雾：敌方血量百分比、技能???，见 view.py/visibility.py）")
    bp.add_argument("--quiet", action="store_true", help="只打 digest 一行")
    bp.add_argument("--json", action="store_true", help="输出整局 JSON")
    bp.set_defaults(func=_run_battle)

    rp = sub.add_parser("replay", help="重放一条轨迹记录（E6），逐回合比对 state_hash")
    rp.add_argument("path", type=str, help="轨迹 JSON 文件路径（selfplay --out 产出的 runs/<id>.json）")
    rp.set_defaults(func=_run_replay)

    args = parser.parse_args()
    source = DataSource(args.data)
    if args.command == "battle":
        return args.func(args)
    if args.command == "replay":
        return args.func(args)
    if args.data_report:
        return _data_report(source, effects=args.effects)
    if args.probe_errors:
        return _probe_errors(source)
    if args.team_report:
        return _team_report(source, args.spirit or [])
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

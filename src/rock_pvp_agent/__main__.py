"""CLI 入口：python -m rock_pvp_agent。

M1：-q/--query 单发 + 交互 REPL；--debug 打印思考与工具过程；--serve 占位（M3 实现）。
"""

import argparse

from rich.console import Console

from .agent import ChatAgent, ChatReply
from .config import get_settings

console = Console()

EXIT_WORDS = {"exit", "quit", "q"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="rock_pvp_agent", description="Rock PVP Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('rock_pvp_agent').__version__}")
    parser.add_argument("-q", "--query", help="单发模式：直接提问后退出")
    parser.add_argument("--debug", action="store_true", help="打印思考过程与工具调用详情")
    parser.add_argument("--serve", action="store_true", help="启动 Web UI（M3 实现）")

    sub = parser.add_subparsers(dest="command", help="子命令")
    sp = sub.add_parser("selfplay", help="双 LLM 自博弈（E6 测试期假LLM）+ 轨迹落盘 + 重放自检")
    sp.add_argument("--games", type=int, default=2, help="对局数（默认 2；逐局 seed +1）")
    sp.add_argument("--seed", type=int, default=7, help="首局引擎 seed（默认 7）")
    sp.add_argument("--out", type=str, default="runs", help="轨迹落盘目录（默认 runs/）")
    sp.add_argument("--a", choices=("fake_llm", "random", "llm"), default="fake_llm",
                    help="a 方玩家：llm=真实 LLM（无 key 自动降级假LLM）；fake_llm/random=测试期策略")
    sp.add_argument("--b", choices=("fake_llm", "random", "llm"), default="fake_llm",
                    help="b 方玩家（同上）")
    sp.add_argument("--team-size", type=int, default=3, help="每方精灵数（管理员接口 3–6，默认 3V3）")
    sp.add_argument("--lives", type=int, default=2, help="每方命数（1..team_size−1，默认 2）")
    sp.add_argument("--max-turns", type=int, default=None, help="覆盖 rules.max_turns")
    sp.add_argument("--verbose", action="store_true", help="逐回合打印双方提交类型")
    sp.set_defaults(func=_run_selfplay_cli)

    ep = sub.add_parser("evolve", help="自博弈进化（R 线）：eval 评测 / reflect 轨迹分析")
    epsub = ep.add_subparsers(dest="evolve_cmd", help="evolve 子命令")
    epe = epsub.add_parser("eval", help="配对评测：subject vs opponent 强弱（双向先后手 + 95%CI）")
    epe.add_argument("--bench", choices=("d_sel", "d_test"), default="d_sel", help="实例集（默认 d_sel）")
    epe.add_argument("--a", choices=("fake_llm", "random", "llm"), default="fake_llm", help="subject 方玩家")
    epe.add_argument("--b", choices=("fake_llm", "random", "llm"), default="random", help="opponent 方玩家")
    epe.add_argument("--games", type=int, default=8, help="每实例 seed 数（默认 8）")
    epe.add_argument("--seed", type=int, default=7, help="seed 平移（默认 7）")
    epe.set_defaults(func=_run_evolve_eval_cli)
    epr = epsub.add_parser("reflect", help="轨迹重放分析 + 可选提取记忆条目")
    epr.add_argument("--traj", required=True, help="轨迹 JSON 路径")
    epr.add_argument("--out", default=None, help="提取记忆条目写入该目录（MemoryStore）")
    epr.set_defaults(func=_run_evolve_reflect_cli)
    eph = epsub.add_parser("health", help="记忆健康度：Q 分布 / 命中率 / Forgetting Rate")
    eph.add_argument("--memory", required=True, help="MemoryStore 目录")
    eph.set_defaults(func=_run_evolve_health_cli)

    args = parser.parse_args()

    if args.command == "selfplay":
        return args.func(args)
    if args.command == "evolve":
        func = getattr(args, "func", None)
        if func is None:
            ep.print_help()
            return 1
        return func(args)
    if args.serve:
        try:
            from ui.__main__ import run_ui
        except ImportError:
            console.print("[red]Web UI 依赖未安装，请先运行：uv sync --all-extras[/red]")
            return 1
        return run_ui()

    agent = ChatAgent(get_settings())
    if args.query:
        return _run_once(agent, args.query, args.debug)
    return _repl(agent, args.debug)


def _run_selfplay_cli(args) -> int:
    """selfplay 子命令：逐局自博弈 + 落盘 + 重放自检，任一局失配 → 返回 1（可进 CI）。"""
    from .battle.selfplay import run_selfplay

    for i in range(1, args.games + 1):
        seed = args.seed + i - 1
        battle_id = f"selfplay-{seed}-{i}"
        out = run_selfplay(seed=seed, team_size=args.team_size, lives=args.lives,
                           max_turns=args.max_turns, a_kind=args.a, b_kind=args.b,
                           out_dir=args.out, battle_id=battle_id)
        mark = "✅" if out["replay_ok"] else "❌"
        console.print(
            f"game#{i} seed={seed} winner={out['winner'] or '平局'} "
            f"turns={out['turn_count']} rng_calls={out['rng_calls']} "
            f"replay={mark} {out['record_path'] or '(未落盘)'}"
        )
        if args.verbose:
            for t in out["record"]["turns"]:
                da = t["decision_a"]["action"].get("type", "?")
                db = t["decision_b"]["action"].get("type", "?")
                console.print(f"  T{t['turn']:>3}  a:{da:<8} b:{db}")
        if not out["replay_ok"]:
            return 1
    return 0


def _run_evolve_eval_cli(args) -> int:
    """evolve eval：配对评测（LLM/fake/random vs 对手）。无 key 时 llm 自动降级假LLM。"""
    from .battle.selfplay import build_player
    from .battle.evolution.bench import build_instances, paired_eval

    settings = get_settings()
    instances = build_instances(args.bench)
    result = paired_eval(
        lambda side, seed: build_player(side, args.a, seed=seed, settings=settings),
        lambda side, seed: build_player(side, args.b, seed=seed, settings=settings),
        instances,
        seeds=args.games,
        seed_offset=args.seed,
    )
    console.print(f"[bold]evolve eval[/bold] bench={args.bench}  "
                  f"subject={result['players']['subject']} vs opponent={result['players']['opponent']}")
    console.print(f"winrate={result['winrate']:.3f}  "
                  f"95%CI=[{result['ci95_low']:.3f}, {result['ci95_high']:.3f}]  "
                  f"n_games={result['n_games']}  n_instances={result['n_instances']}")
    for row in result["instances"][:10]:
        lo, hi = row["ci95"]
        console.print(f"  {row['name']:<22} {row['scenario']:<15} "
                      f"winrate={row['winrate']:.3f} [{lo:.3f},{hi:.3f}] n={row['n_games']}")
    if len(result["instances"]) > 10:
        console.print(f"  … 其余 {len(result['instances']) - 10} 个实例省略")
    return 0


def _run_evolve_reflect_cli(args) -> int:
    """evolve reflect：轨迹重放分析（replay_ok + 逐回合 situation_key / v_heuristic）；
    给 --out 则额外提取记忆条目写入 MemoryStore（复用同一 analysis，不二次重放）。"""
    import json

    from .battle.evolution.analysis import analyze_record
    from .battle.evolution.reflect import store_experiences

    with open(args.traj, encoding="utf-8") as f:
        record = json.load(f)
    analysis = analyze_record(record)
    console.print(f"[bold]evolve reflect[/bold] traj={args.traj}")
    console.print(f"winner={analysis.winner or '平局'}  turns={analysis.turn_count}  "
                  f"replay_ok={'✅' if analysis.replay_ok else '❌'}")
    for t in analysis.turns:
        console.print(
            f"  T{t.turn:>3}  a:{t.situation_keys['a']}  v_a={t.v_before['a']:+.3f}->{t.v_after['a']:+.3f}"
        )
    if args.out:
        ids = store_experiences(record, args.out, analysis=analysis)
        console.print(f"记忆条目写入 {len(ids)} 条 → {args.out}")
    return 0 if analysis.replay_ok else 1


def _run_evolve_health_cli(args) -> int:
    """evolve health：读一个 MemoryStore 目录 → 记忆健康度报告。"""
    from .battle.evolution.health import memory_health

    h = memory_health(args.memory)
    q = h["q_distribution"]
    console.print(f"[bold]evolve health[/bold] dir={h['store_dir']}")
    console.print(f"count={h['count']}  Q[min={q['min']}, max={q['max']}, mean={q['mean']}]")
    console.print(f"n_used_total={h['n_used_total']}  n_adopted_total={h['n_adopted_total']}  "
                  f"source_type_counts={h['source_type_counts']}")
    console.print(f"forgetting_rate={h['forgetting_rate']}  retrieval_hits={h['retrieval_hits']}")
    return 0


def _run_once(agent: ChatAgent, query: str, debug: bool) -> int:
    reply = agent.chat(query)
    _print_reply(reply, debug)
    return 0


def _repl(agent: ChatAgent, debug: bool) -> int:
    console.print("[cyan]Rock PVP Agent — 输入 exit / quit / q 退出[/cyan]")
    history = []
    while True:
        try:
            text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            break
        reply = agent.chat(text, history=history)
        _print_reply(reply, debug)
        history = reply.history
    return 0


def _print_reply(reply: ChatReply, debug: bool) -> None:
    if debug:
        for step in reply.thinking:
            console.print(f"[dim]💭 {step}[/dim]")
        for tc in reply.tool_calls:
            console.print(f"[dim]🔧 {tc['name']}({tc['args']}) -> {tc['result']}[/dim]")
        if reply.usage.get("total_tokens"):
            u = reply.usage
            console.print(f"[dim]⚡ tokens: 输入 {u['input_tokens']} / 输出 {u['output_tokens']} / 总计 {u['total_tokens']}[/dim]")
    if reply.offline:
        console.print("[yellow]（离线模式）[/yellow]")
    console.print(f"[bold]{reply.reply}[/bold]")


if __name__ == "__main__":
    raise SystemExit(main())

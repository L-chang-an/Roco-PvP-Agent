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
    args = parser.parse_args()

    if args.command == "selfplay":
        return args.func(args)
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

"""CLI 入口：python -m rock_pvp_agent。

M1：-q/--query 单发 + 交互 REPL；--debug 打印思考与工具过程；--serve 占位（M3 实现）。
"""

import argparse

from rich.console import Console

from .agent import ChatAgent
from .config import get_settings

console = Console()

EXIT_WORDS = {"exit", "quit", "q"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="rock_pvp_agent", description="Rock PVP Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('rock_pvp_agent').__version__}")
    parser.add_argument("-q", "--query", help="单发模式：直接提问后退出")
    parser.add_argument("--debug", action="store_true", help="打印思考过程与工具调用详情")
    parser.add_argument("--serve", action="store_true", help="启动 Web UI（M3 实现）")
    args = parser.parse_args()

    if args.serve:
        console.print("[yellow]--serve 将在 M3 实现，敬请期待[/yellow]")
        return 0

    agent = ChatAgent(get_settings())
    if args.query:
        return _run_once(agent, args.query, args.debug)
    return _repl(agent, args.debug)


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


def _print_reply(reply, debug: bool) -> None:
    if debug:
        for step in reply.thinking:
            console.print(f"[dim]💭 {step}[/dim]")
        for tc in reply.tool_calls:
            console.print(f"[dim]🔧 {tc['name']}({tc['args']}) -> {tc['result']}[/dim]")
    if reply.offline:
        console.print("[yellow]（离线模式）[/yellow]")
    console.print(f"[bold]{reply.reply}[/bold]")


if __name__ == "__main__":
    raise SystemExit(main())

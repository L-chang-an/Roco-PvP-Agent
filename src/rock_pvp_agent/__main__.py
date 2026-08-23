"""CLI 入口：python -m rock_pvp_agent。

M0 为最小占位；M1 起在此加入 -q/--query 与交互 REPL，M3 起加入 --serve。
"""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(prog="rock_pvp_agent", description="Rock PVP Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('rock_pvp_agent').__version__}")
    parser.parse_args()
    print("rock_pvp_agent skeleton OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

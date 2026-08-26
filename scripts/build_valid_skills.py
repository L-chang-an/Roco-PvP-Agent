"""生成 E3 的 valid_skills.json：从 full_skills.json 过滤 battle_ready（P1∪P2 已实装效果）技能。

用法：
    uv run python scripts/build_valid_skills.py            # 写 src/environment/data/valid_skills.json
    uv run python scripts/build_valid_skills.py --check    # 校验现有文件：179 条、全 battle_ready、与 FULL 同格式

格式与 full_skills.json 逐字节一致：`[{name, type, kind, desc, strong, energy}]`，
strong/energy 为字符串；power=0（状态/防御）→ strong=null。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FULL_SKILLS = REPO / "src" / "environment" / "data" / "full_skills.json"
VALID_SKILLS = REPO / "src" / "environment" / "data" / "valid_skills.json"

sys.path.insert(0, str(REPO / "src"))


def _battle_ready_names() -> set[str]:
    from environment.skillbook import battle_ready  # noqa: PLC0415（本地导入避免循环）
    return {s["name"] for s in json.loads(FULL_SKILLS.read_text("utf-8")) if battle_ready(s["name"])}


def _valid_entries() -> list[dict]:
    """full_skills.json 里 battle_ready 的子集（原样保留，格式一致）。"""
    ready = _battle_ready_names()
    full = json.loads(FULL_SKILLS.read_text("utf-8"))
    return [s for s in full if s["name"] in ready]


def main() -> int:
    entries = _valid_entries()
    if "--check" in sys.argv:
        loaded = json.loads(VALID_SKILLS.read_text("utf-8"))
        loaded_names = {s["name"] for s in loaded}
        problems = []
        if len(loaded) != 179:
            problems.append(f"应 179 条，实际 {len(loaded)}")
        if loaded_names != _battle_ready_names():
            problems.append("集合 ≠ battle_ready 集合")
        if any(s["name"] not in {x["name"] for x in _valid_entries()} for s in loaded):
            problems.append("含非 battle_ready 技能")
        if problems:
            print("VALID 校验失败：", "；".join(problems))
            return 1
        print(f"OK：{len(loaded)} 条，全 battle_ready，格式与 full_skills 一致")
        return 0
    VALID_SKILLS.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"已写 {VALID_SKILLS}：{len(entries)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

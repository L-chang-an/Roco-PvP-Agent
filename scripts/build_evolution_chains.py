"""从真实精灵表派生进化链并写出 `src/environment/data/evolution_chains.json`。

推导逻辑在 `dataset.derive_evolution_chains`（也是运行时一致性测试的比对基准），
本脚本只负责「读 full_spirits.json → 派生 → 写 evolution_chains.json」。

运行：`uv run python scripts/build_evolution_chains.py`
产出后，运行时（dataset.load_evolution_chains / evolution 索引）只读该文件，不再现场推导。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from environment.dataset import derive_evolution_chains  # noqa: E402


def main() -> int:
    spirits_file = ROOT / "src" / "environment" / "data" / "full_spirits.json"
    out_file = ROOT / "src" / "environment" / "data" / "evolution_chains.json"
    records = json.loads(spirits_file.read_text(encoding="utf-8"))

    doc = derive_evolution_chains(records)

    # 自校验：链上每个名字都真实存在于精灵表（脏记录无进化链、天然不在链内）。
    names = {r["name"] for r in records}
    missing = {n for c in doc["chains"] for n in c["path"]} - names
    if missing:
        print(f"⚠ 链上名字不在精灵表：{sorted(missing)}")
        return 1

    out_file.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    n_boss = sum(1 for c in doc["chains"] if c["boss"])
    print(f"evolution_chains.json 已生成：{len(doc['chains'])} 条链"
          f"（{n_boss} 条带首领）→ {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

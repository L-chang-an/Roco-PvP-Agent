"""从真实精灵表派生家族并写出 `src/environment/data/families.json`。

家族判定 = 负责人指定的规则：对比 evolution 链，链首（最低阶）精灵的编号一致
→ 同族。推导逻辑在 `dataset.derive_families`（也是运行时一致性测试的比对基准），
本脚本只负责「读 full_spirits.json → 派生 → 写 families.json」。

运行：`uv run python scripts/build_families.py`
产出后，运行时（load_spirits / load_families）只读 families.json，不再现场推导。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from environment.dataset import derive_families  # noqa: E402


def main() -> int:
    spirits_file = ROOT / "src" / "environment" / "data" / "full_spirits.json"
    out_file = ROOT / "src" / "environment" / "data" / "families.json"
    records = json.loads(spirits_file.read_text(encoding="utf-8"))

    families = derive_families(records)

    # 自校验：每个成员都真实存在于精灵表（学院呱呱无进化链、天然不在族内）。
    names = {r["name"] for r in records}
    missing = {m for fam in families.values() for m in fam["members"]} - names
    if missing:
        print(f"⚠ 家族成员不在精灵表：{sorted(missing)}")
        return 1

    out_file.write_text(
        json.dumps(families, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"families.json 已生成：{len(families)} 个家族 → {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

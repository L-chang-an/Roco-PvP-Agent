"""从 data/type_chart.json 生成完整 18×18 克制矩阵，打印成 Python 字面量。

用法：
    uv run python scripts/build_type_chart.py            # 打印 CHART 字面量
    uv run python scripts/build_type_chart.py --check     # 校验 JSON 完整性（18×18、值 ∈ {1,2,0.5}）

data/type_chart.json 的表格约定：**行 = 防御方，列 = 攻击方**，值 ∈ {1, 2, 0.5}。
产出 dict[防御方][攻击方] = 倍率，供 types.CHART 直接内嵌。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JSON = REPO / "src" / "environment" / "data" / "type_chart.json"

VALID = {1.0, 2.0, 0.5}


def parse_chart() -> dict[str, dict[str, float]]:
    """读取 data/type_chart.json → {防御方: {攻击方: 倍率}}，并校验完整性。"""
    chart: dict[str, dict[str, float]] = json.loads(JSON.read_text(encoding="utf-8"))
    if len(chart) != 18 or any(len(row) != 18 for row in chart.values()):
        raise ValueError(f"矩阵不完整：{len(chart)} 行")
    for d, row in chart.items():
        for a, v in row.items():
            if v not in VALID:
                raise ValueError(f"{d} × {a}：非法倍率 {v!r}")
    return chart


def _fmt_chart(chart: dict[str, dict[str, float]]) -> str:
    """渲染成 CHART 字面量（每行一个防御方 dict，紧凑对齐，值恒为 float 字面量）。"""
    names = list(chart)
    lines = ["CHART: dict[str, dict[str, float]] = {"]
    for d in names:
        row = ", ".join(f"\"{a}\": {chart[d][a]:.1f}" for a in names)
        lines.append(f"    \"{d}\": {{{row}}},")
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    chart = parse_chart()
    if "--check" in sys.argv:
        print(f"OK：{len(chart)} 防御方 × {len(next(iter(chart.values())))} 攻击方")
        return 0
    print(_fmt_chart(chart))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""从 mydocs/type_chart.md 生成完整 18×18 克制矩阵，打印成 Python 字面量。

用法：
    uv run python scripts/build_type_chart.py            # 打印 CHART 字面量
    uv run python scripts/build_type_chart.py --check     # 校验 md 完整性（18×18、值 ∈ {1,2,0.5}）

type_chart.md 的表格约定：**行 = 防御方，列 = 攻击方**，值 ∈ {1, 2, 0.5}。
产出 dict[防御方][攻击方] = 倍率，供 types.CHART 直接内嵌。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MD = REPO / "mydocs" / "type_chart.md"

VALID = {"1", "2", "0.5"}


def parse_chart() -> dict[str, dict[str, float]]:
    """解析 md 表格 → {防御方: {攻击方: 倍率}}。"""
    chart: dict[str, dict[str, float]] = {}
    header: list[str] | None = None
    for line in MD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            if cells[0] == "防御方\\攻击方" or "攻击方" in cells[0]:
                header = cells[1:]
            continue
        # 数据行：首格防御方，其余按列取倍率
        defense = cells[0]
        if defense.startswith("--"):
            continue
        if len(cells) != len(header) + 1:
            raise ValueError(f"行 {defense!r} 列数 {len(cells)-1} != 攻击方数 {len(header)}")
        chart[defense] = {}
        for atk, raw in zip(header, cells[1:], strict=True):
            if raw not in VALID:
                raise ValueError(f"{defense} × {atk}：非法倍率 {raw!r}")
            chart[defense][atk] = float(raw)
    if len(chart) != 18 or any(len(row) != 18 for row in chart.values()):
        raise ValueError(f"矩阵不完整：{len(chart)} 行")
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

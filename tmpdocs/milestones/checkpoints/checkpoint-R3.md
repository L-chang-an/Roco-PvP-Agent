# 里程碑 R3 检查点

日期：2026-08-30　状态：**☑ 通过**

## 目标
反思与有界编辑（GEPA/SkillOpt 核心）：把 R2 的关键回合卡片变成结构化编辑提案，并有界应用到战术手册。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `evolution/playbook.py`（新，128 行） | 从 main 移植：`Playbook`（5 模块 + `[PROTECTED]` + 版本）/`PlaybookModule`/token 上限 |
| `evolution/editor.py`（新，313 行） | 从 main 移植：`EditCandidate`/`EditReport`/`bounded_edit`（L_t 有界 + 行级编辑 + 审计）/`append_report` |
| `evolution/reflect.py`（补全） | 加回 R3 部分：双分析师 `ReflectionService` + 失败/成功独立提示 + JSON 鲁棒解析 + 降级摘要 |
| `evolution/run.py`（新，切片） | 只保留 `run_step`（rollout→credit→reflect→edit），R4 的 run_steps/run_epochs 暂缓 |
| `__main__.py` | 加 `evolve step` |
| `tests/test_evolution_r3.py`（新） | Playbook 版本/roundtrip + bounded_edit（L_t/保护区/多行）+ 反思（JSON 鲁棒/模块容错/降级） |

## 验收命令与结果

```bash
uv run pytest tests/test_evolution_r3.py -q   # 10 passed ✅
uv run pytest -q   # 947 passed ✅（零回归）
uv run python -m rock_pvp_agent evolve step --seed 7 --m 4 --out /tmp/step_test
#   cards=3 → candidates=2 applied=2 → playbook pb_v000 → pb_v1 ✅（真实 LLM 反思，双分析师 ok）
```

## 用户的疑问 / 修改要求
无。按推荐直接通过。

## 记录
- **`next_version` 丢前导零**：`int("pb_v000".removeprefix("pb_v"))` → `pb_v1`（main 行为，保持原样，测试按实际断言）。
- **run.py 切片**：main 的 run.py 是 R4/R5 完整版（接池），R3 只保留 run_step；`style_*` 是惰性 import。
- **reflect.py 补全**：在 R1 切片版上追加 R3 函数，`_data_digest` 仍复用 S0 指纹。
- **真实 key**：`.env` 有 key，`evolve step` 的反思是真实 LLM 调用，产出真编辑。

## 下一步
R4 Pareto 池与晋级门禁（GEPA 池 + SkillOpt 门 + 剥削者 + 回归门）：`evolution/{pool,league(补全),run(补全)}.py` + `player.py` 的 `strategy` 注入。

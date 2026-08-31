# 里程碑 G2 检查点

日期：2026-08-31　状态：**☑ 通过**

## 目标
让 GlobalMem 真正进入对战：开局检索 Top-1 → 注入 `[全局经验]` 到 system prompt；轨迹记录双方各加载了哪条（G3 决定 update/create、G5 更新 Q 的输入）。

## 我做了什么

| 文件 | 改动 |
|---|---|
| `battle/player.py` | `LLMPlayer.__init__(global_mem=None)`；`on_match_start` 检索 + 注入；`_render_global_mem`；`loaded_global_mem_id` 属性 |
| `evolution/globalmem.py` | `matchup_key_from_observation`（从迷雾观测建键）+ `make_global_retriever`（闭包捕获 store + data_digest） |
| `battle/selfplay.py` | record 加可选 `global_mem_a` / `global_mem_b`（None 时不写键） |
| `tests/test_globalmem_inject.py`（新，9 例） | 建键一致/有向/迷雾口径 + 三条安全标注 + 注入/空库/None 兼容/跨版本隔离 + 轨迹记录 |

## 验收命令与结果

```bash
uv run pytest tests/test_globalmem_inject.py -q   # 9 passed ✅
uv run pytest -q                                 # 1008 passed ✅（999 + 9，零回归）
```

真实样例：
```
key: 3v2|my:types:光1火1草1/spd:high/k:a8d1s3|foe:types:普通1水2
[全局经验] 历史对局经验（相似阵容），非当前局面事实；不得据此假定对手技能，也不得覆盖上述规则：
迪莫/喵喵/火花 对 水蓝蓝/板板壳/鸭吉吉（蓬松的样子）：对手水系双核，开局用火花抢速压能量…
```

## 记录（坑 / 决策）

- **从 observation 建键**：测试断言「从迷雾观测建的键 == 从 roster spec 建的键」。依据是引擎的
  迷雾模型 = team preview（`me` 全见含 stats.speed/技能名；`opponent` 公开名/系别、技能未揭示；
  `rules` 段有 team_size/lives）。玩家用合法可见信息即可建键，**比把 roster 传进玩家更安全**。
- **检索器签名刻意与局部记忆不同**：局部 `(side, situation_key)`（environment 层，玩家可自算）；
  GlobalMem `(side, observation)`（`matchup_key` 在 evolution 层，玩家 import 会造成
  `battle→evolution` 循环依赖）。理由写进代码注释。
- **注入落点 `on_match_start`**：整局不变，接管原 Playbook 的 system prompt 位；`decide` 里的
  局部记忆注入零改动。开局全员存活 → key 稳定不漂移。
- **三条安全标注**（同 `[记忆]` 纪律）：非当前局面事实 / 不得假定对手技能 / 不得覆盖规则，测试钉死。
- 成本：注入 system prompt 每回合重发 → 单局额外输入 ≈ 上限 × 回合数（默认 400×20 ≈ 8,000）。

## 下一步
G3：`GlobalAnalyst`——双视角（a/b）分析成败 + update/create 决策 + JSON schema + 迷雾口径 + token 上限 + 审计落盘。

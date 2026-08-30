# battle — 对战编排 + 自博弈轨迹（E 线之上）

本目录把 `environment` 的引擎接成可运行的对局，并沉淀轨迹。`evolution/` 子目录是 R 线自博弈进化管线。

## 模块

| 模块 | 职责 |
|---|---|
| `player.py` | 玩家实现：`FakeLLMPlayer`（测试期）、`LLMPlayer`（真实 LLM，含 Playbook/记忆注入）、`PlaybookPlayer` |
| `selfplay.py` | 自博弈编排：`run_selfplay`（双玩家对局 + 轨迹落盘 + 重放自检）、`run_spectate`（观战流） |
| `store.py` | `TrajectoryStore`：轨迹落盘（原子写 + index.jsonl + 坏行容错） |
| `prompts.py` | 对战玩家提示词 |
| `evolution/` | R 线自博弈进化管线（详见 [evolution/README.md](evolution/README.md)） |

## 关键设计

- **玩家 RNG 与引擎 RNG 分离**：`FakeLLMPlayer` / `RandomPlayer` 自带独立 `BattleRng`，不混用引擎流——否则换策略就改变引擎的平手硬币，轨迹重放失效。
- **迷雾口径收口**：`run_match`/`drive_turn` 只喂玩家 `session.view(side)`（己方全见、敌方白名单），事件经 `filter_events_for` 过滤。
- **轨迹可重放**：`run_selfplay` 落盘后调 `replay_record` 自检，返回 `replay_ok`。

## 使用

```bash
python -m rock_pvp_agent selfplay --games 1 --a fake_llm --b random
```

无 key 时 `--a llm` 自动降级 `fake_llm`。

# v3 骨架审计文档（根动作 → 效果 → 事件 → 触发器 四层管道）

> 状态：**已施工并通过行为等价验证**（2026-08-29）
> 代码：`src/environment/{atom,domain,modifiers,compiler,reducer,triggers}.py` + `engine.py`/`hooks.py` 改造
> 依据：`mydocs/battle_docs.md` §7 引擎主循环 / §12 迁移路径第 1–4 步
> 护栏：`tests/test_v3_sentinel.py`（行为等价哨兵）+ 全量 **600 passed** + 覆盖率 94% + 确定性 digest 一致

---

## 0. 一句话

v3 骨架把旧 `resolve_skill`（~140 行、20 个效果字段各一个 if）拆成 **四条独立管道**，
引擎只做"编译 → 执行 → 发事件 → 触发反应"，对任意具体技能/效果零感知。
**行为与旧引擎逐位等价**（事件流 + state_hash 不变）。

---

## 1. 四层管道架构

```
SkillEffect（旧大字段袋）
   │  compiler.compile_skill(state, ctx, unit, skill, side)
   ▼
Atom 列表（类型化 Effect：SpendEnergy / RevealSkill / DealDamage / HealPct /
            AddModifier / GainEnergy / BenchEnergy / Lifesteal / StealEnergy /
            FoeCostGain / TraitGain）
   │  reducer.reduce_all(state, atoms, frame)
   ▼
状态修改（唯一写入口，走 damage/primitives 漏斗）+ 展示事件（ev()，形状不变）
   │  同时产出 DomainEvent（DamageApplied / EnergyChanged / SkillResolved…）
   ▼
triggers.collect_reactions(state, event, unit, trait_defs) → 新 Atom 列表
   │  （当前接 SKILL_RESOLVE：特性效果 → TraitGain Atom）
   ▼
reducer 再次执行（写 trait.gains）
```

**依赖方向**：`engine → compiler → atom`；`engine → reducer → atom/domain/modifiers`；
`hooks → triggers → atom/domain`。`reducer`/`compiler` 不 import engine（无循环依赖）。

---

## 2. 各文件职责（代码注释已内嵌，此处给审计视角）

| 文件 | 职责 | 关键点 |
|---|---|---|
| `atom.py` | 类型化 Effect（Atom）声明层 | 每个 Atom 是 frozen dataclass、纯数据；**持 Unit 对象引用**（回合内局部，不序列化，规避 unit_id 依赖） |
| `domain.py` | DomainEvent 内部事实 | Trigger 的输入；与展示事件（EVENT_TYPES dict）严格分开 |
| `modifiers.py` | ModifierPipeline 纯函数修正层 | `DamageQuery` + `compute`；克制/STAB/应对乘子/减伤折叠进 multiplier，纯函数不改状态 |
| `compiler.py` | 技能编译器 | SkillEffect → 有序 Atom 列表；顺序与旧 resolve_skill 逐位一致（扣能→揭示→伤害段→资源效果） |
| `reducer.py` | Reducer 表 | 每个 Atom 一个 reducer，唯一改状态；产出 DomainEvent + 展示事件（形状不变） |
| `triggers.py` | Trigger 收集器 | `collect_reactions`：事件 → 新 Atom；当前接 SKILL_RESOLVE 特性 |
| `engine.py` | resolve_skill 换芯 | 校验 → `_combat_skill` → `compile_skill` → `reduce_all` → `emit`（特性） |
| `hooks.py` | emit 委托 | 构造 SkillResolved 事件 → `collect_reactions` → `reduce_all`（Phase 3） |

---

## 3. 行为等价的关键细节（审计重点）

1. **属性层合并语义**：`reducer._add_stat_layers` 按 `(stat, mode, permanent)` 合并、**忽略 source**——
   与旧 engine 一致；`primitives.apply_stat_mod` 按 source 合并，**不可混用**（否则 digest 漂移）。
2. **能耗层**：走 `apply_energy_cost_mod`（stat="energy_cost", flat 层）。
3. **SpendEnergy 直接减、不夹 0**：付得起已由 actions 门控保证（骨架保持旧行为，v3 的"夹 0"规则留到 Phase 2）。
4. **Lifesteal 总是生成**：即使技能自身无吸血，也吃"贪婪"吸血 buff（reducer 内 ≤0 时返回空）。
5. **legacy 单条自身状态无 target 字段**：`AddModifier.target=""` → reducer 不发 target 键。
6. **特性条件**：`SkillResolved.skill_type` 显式携带（兼容测试直调无 name 场景）。
7. **FoeCostGain 读 `foe.skills`**（旧代码行为），不是 current_skills。

---

## 4. 测试方式（审计护栏）

### 4.1 `tests/test_v3_sentinel.py`（行为等价哨兵）
- 4 个代表性对局：3v3 随机（mirror_pair）、1v1 脚本（duel，攻击+状态+防御）、1v1 碾压（strong_weak）、1v1 速度悬殊（fast_slow）。
- 每局记录两层指纹：`digest`（state_hash 序列）与 `event_sig`（每回合事件流关键字段签名）。
- 硬编码期望值——**任何重构若改变事件流或状态，立即变红**。
- 这是 v3 换芯的"金标准"：换芯后 4 项原样通过，证明行为逐位等价。

### 4.2 全量回归
- `uv run pytest -q` → **600 passed**（原 596 + 哨兵 4，零行为漂移）。
- 覆盖率 94%（environment/rock_pvp_agent/ui 三包）。

### 4.3 确定性
- `uv run python -m environment battle --preset p1 --repeat 3 --quiet` → 3 次 digest 一致。

---

## 5. 审计清单（负责人逐项核）

- [ ] `resolve_skill` 已无 20 字段 if 分支（只剩校验 + compile + reduce + emit）。
- [ ] 展示事件形状与旧引擎逐位一致（哨兵 4 项验证）。
- [ ] `hooks.emit` 已委托 collect_reactions + reduce_all（特性也走事件→Atom→Reducer）。
- [ ] 四层管道文件各自职责单一、可单测（后续按需补单测）。
- [ ] 新文件零依赖 cycle（reducer/compiler 不 import engine）。
- [ ] 596 既有测试 + 4 哨兵全绿、覆盖率 94%。

---

## 6. 后续接入点（骨架已就位，等下一步填充）

| 系统 | 接入位置 |
|---|---|
| 印记（mark） | `triggers.collect_reactions` 增加对 Marks 的绑定收集；`SideState.marks` 已在数据协议 v2 落地 |
| 天气（weather） | `modifiers.py` 管线两端挂 ATTACK_POWER / SKILL_COST 读钩子；`BattleState.weather` 已落地 |
| 纯负面 buff（DOT） | TURN_END 事件 → Trigger → LoseHp Atom；reducer 已有 HealPct 同类原语可扩展 |
| 防御冷却 | TURN_END 递减 `current_skills[].cooldown` |
| 阵亡补位语义 | session 层（回合边界被动补位，见 battle_docs §9） |

**加一种新效果 = 加一个 Atom 类型 + 一个 reducer + 一条 Trigger 绑定，引擎零改动**（v3 扩展铁律）。

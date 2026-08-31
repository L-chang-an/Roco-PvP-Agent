# 回合结算重构方案：原子动作队列 + Hook 驱动

> 状态：**方案设计**（未实施）　日期：2026-08-28　分支：`feat/e-line-v2`
> 作者：E 线代码负责人　配套：`docs/e-line-data-contract.md`、`docs/audit/`
> 本文档为 mydocs/ 设计稿（**gitignored，本地专用，绝不入库**；验收记录走 docs/checkpoints）

---

## 0. 一句话目标

把"一个回合 = 全局队列 ≤4 条、每条一步结算"重写为
"**一个回合 = 双方各自的原子动作序列，按调度规则交错执行；每个原子动作携带（发起者/动作类型/受体），被当前回合装备的 Hook 捕捉；Hook 命中可向队列尾追加新原子动作；循环有上限；阵亡时取消针对该精灵的原子、中断、补位以『入场』原子动作入队后恢复**"。

---

## 1. 现状评估（为什么当前引擎不支持）

| 目标能力 | 当前实现 | 差距 |
|---|---|---|
| 动作 → 原子动作（换人=离场+入场） | `resolve_switch` 一步完成（`active` 改下标 + 清非永久层 + 一条 `switch` 事件） | ❌ 无 `switch_out`/`switch_in` 一等对象 |
| 原子动作携带（发起者/动作/受体） | 事件带 `side/unit/target/source`，但只是叙述，不是可调度实体 | ⚠️ 信息在、形态无 |
| Hook 捕捉原子并追加队列 | `emit` 只内联执行效果原语（`_apply_effect`），**从不追加动作** | ❌ 无追加能力 |
| 双方各自原子序列 | `build_queue` 全局单队列（≤4 条，priority→speed→道具先→硬币） | ❌ 无每方队列/动态增长 |
| 阵亡：取消针对原子→中断→补位入场入队→恢复 | 判断 9"阵亡即回合结束"，剩余队列**整体丢弃**；补位 `apply_replacement` 一步改 `active` | ⚠️ 中断有，恢复语义不同（§7） |
| 循环上限 | 无（hook 不追加，无死循环可能） | ❌ 需新增 |

**已备好的地基（重构的锚点）**：
- `hooks.py` 的 `Hook` 枚举**已声明** `TURN_START/TURN_END/ENTER/EXIT/SKILL_RESOLVE/DEAL_DAMAGE/TAKE_DAMAGE/KO/COUNTER/GAIN_BUFF/STAT_CALC/SKILL_COST/ATTACK_POWER`——全套时机预留，目前只接 SKILL_RESOLVE；
- `effects.py`/`primitives.py`/`traits.py` 声明式效果架构（Effect/EffectBinding/TraitDef/TRAIT_CATALOG）；
- `TurnContext` 局部对象模式（回合内派生量不落 `BattleState`）；
- 三条不变式：纯转移 / 玩家 RNG 流与引擎流分离 / 回合内不落状态。

---

## 2. 目标架构总览

```
决策（skill/switch/recharge/item）           ← 每方一个，来自 decide()
   │ 分解
   ▼
每方原子动作队列 side_queues = {a: deque[AtomicAction], b: deque[AtomicAction]}
   │ 调度（§4：priority→speed→硬币，绑定对相邻）
   ▼
执行一个原子动作 resolve_atom(action)
   │ ① 引擎自身效果（伤害/回血/改状态/清层…）
   │ ② emit(hook, ctx{action, unit, skill, ...}, trait_defs)   ← Hook 捕捉
   │        └─ 命中 → 即时效果原语  +  追加原子动作（enqueue）
   │ ③ 检查阵亡 → 取消针对该单位的所有待执行原子 → 中断 or 终局
   ▼
补位（阵亡方 choose_replacement）→ 向该方队列投递 death_in 原子 → 恢复执行
   ▼
end_turn（唯一 turn+=1 / battle_end）
```

**原子动作是回合内一等对象**：`BattleState` 不新增字段（保持"回合内派生量不落 state"）；队列住在新的局部 `TurnExecution`（或扩展 `TurnContext`）。

---

## 3. 核心数据模型

### 3.1 `AtomicAction`（原子动作）

```python
@dataclass(frozen=True)
class AtomicAction:
    side: str            # 发起者（a/b）
    kind: str            # 原子动作类型（枚举，见下）
    receptor: int        # 受体：队内下标（被离场/入场/命中的精灵）
    initiator: int       # 发起单位下标（执行者，通常是 active）
    parent: str = ""     # 父决策动作类型：switch/skill/recharge/item/death_replacement
    priority: int = 0    # 调度键（从父决策继承；skill 先手修正 / switch·item 99）
    speed: int = 0       # 调度键（入队时用 aggregate_stats 算好，避免执行时漂移）
```

**原子动作类型**（`ATOM_KINDS` 封闭登记表，仿 `EVENT_TYPES`）：

| kind | 语义 | 触发时机 |
|---|---|---|
| `MAIN_SKILL` | 主技能结算（攻击/防御/状态三支沿用） | — |
| `ITEM` | 道具使用 | — |
| `RECHARGE` | 聚能 | — |
| `SWITCH_OUT` | 换人·离场（清非永久增益 + EXIT hook） | 换人决策拆解 |
| `SWITCH_IN` | 换人·入场（改 active + ENTER hook） | 换人决策拆解 |
| `DEATH_IN` | 死亡补位·入场（改 active + ENTER hook） | 补位提交后投递 |
| `BONUS_*` | Hook 追加的原子（如追加攻击/追加状态），由 hook 声明 | enqueue |

### 3.2 每方队列 + 回合执行对象

```python
@dataclass
class TurnExecution:                 # 回合内局部对象，绝不落 BattleState
    queues: dict[str, deque[AtomicAction]]   # 每方一条 FIFO
    pair_locked: str | None = None   # 正在执行绑定对（switch_out 后锁定本侧，强制执行 in）
    atom_count: int = 0              # 本回合已执行原子数（循环上限）
```

### 3.3 Hook 追加能力（`EffectBinding` 扩展）

```python
@dataclass(frozen=True)
class AtomicActionSpec:              # Hook 声明的"要追加的原子动作"
    kind: str                        # BONUS_* 或复用现有
    receptor: str = "self"           # self=执行者 / foe=对面在场 / "field"=场上（可再扩展）
    priority: int = 0                # 追加原子的调度键（默认继承/0）
    source_guard: str = ""           # 同源防重（见 §6 死循环防御）
```

`EffectBinding` 增加可选字段：`atoms: tuple[AtomicActionSpec, ...] = ()`。
`emit` 返回追加的原子动作列表，由引擎 append 到对应方队列尾：
- `receptor="self"` → 追加到**发起方**队列；
- `receptor="foe"` → 追加到**对面**队列（先攻类/骚扰类 hook）。

---

## 4. 调度规则（双方队列如何交错执行）

**原则：无 hook 追加时，行为与现状逐字节一致（同 seed 同 digest）——这是迁移哨兵。**

主循环（`resolve_turn` 新版）：

```
while 任一队列非空:
    若 pair_locked 非空 → 强制取该侧队首（switch_in，绑定对必须相邻，不许他侧插入）
    否则 → 从双方队首各取候选，按调度键选执行者：
            priority 降序 → speed 降序 → 平手 rng.choice(硬币，走引擎流)
    执行 resolve_atom(候选)
    → 若产生阵亡 → 取消针对该单位的待执行原子 → 中断/终局（§7）
    → pair_locked 处理（switch_out 后锁本侧；switch_in 后解锁）
    atom_count += 1；超上限 → 丢弃追加 + warning（§6）
```

- **初始入队**：每方把决策按类型拆成原子（`switch → [SWITCH_OUT, SWITCH_IN]` 绑定对；`skill → [MAIN_SKILL]`；`recharge/item → 单原子`），同方同优先级 item 在前（保持 `item_before_main_action`）。
- **`SWITCH_OUT` 与 `SWITCH_IN` 必须相邻**：`out` 执行后锁本侧（`pair_locked`），强制下一步执行本侧 `in`；hook 在 `out` 追加的原子进队列尾（在 `in` 之后执行，保证"新单位在场前不入场效果"）。
- **追加原子的调度键**：默认 `priority=0`（链内靠后，但可被高优先级的其他侧动作竞争）；hook 可显式指定 priority 实现"追加攻击立即结算"。

---

## 5. Hook 机制扩展

### 5.1 时机接线

| Hook | 接线点 | 现状态 |
|---|---|---|
| `ENTER` | `SWITCH_IN`/`DEATH_IN` 原子：改 `active` 后 | **新接**（现未接线） |
| `EXIT` | `SWITCH_OUT` 原子：清非永久增益后 | **新接**（现未接线） |
| `SKILL_RESOLVE` | `MAIN_SKILL` 三支结算后（现已有） | 沿用 |
| `DEAL_DAMAGE`/`TAKE_DAMAGE`/`KO`/`COUNTER` | `resolve_atom` 伤害/击杀点 | 预留（按需接） |
| `TURN_START`/`TURN_END`/`STAT_CALC`/`SKILL_COST`/`ATTACK_POWER` | 对应时机 | 预留（按需接） |

### 5.2 ctx 扩展（Hook 读取原子动作信息）

`emit` 的 ctx 增加字段：`action`（当前 `AtomicAction`：side/kind/receptor/initiator/parent）、`unit`（执行者）、`skill`、`dealt_counter`、`energy_max`。**Hook 的条件谓词与效果原语据此决策**——例：某特性"被换下场时清对方增益"→ 绑定 `EXIT + cond("action.kind==switch_out")` → effect 指向受体。

### 5.3 `_apply_effect` 扩展

现状只支持 `target="self"` + 4 个原语。扩展方向：
- target 支持 `foe` / `field`（受体派生）；
- 原语新增：`enqueue_atom`（经 `atoms` 字段表达，不新增 op）；
- **即时效果与追加原子分离**：即时效果（改属性/回血）在 emit 内直接执行；追加原子经 `atoms` 声明、由 emit 返回、进队列后续执行——这样"追加攻击""追加状态""连锁效果"都能表达，且不破坏纯转移（追加原子由同一份确定性规则产生）。

---

## 6. 死循环防御

1. **硬上限**：回合级 `MAX_ATOMS_PER_TURN`（建议 32）+ 决策级 `MAX_ATOMS_PER_DECISION`（建议 16）。超限 → 丢弃追加原子 + `warnings.warn`（可审计，fail-closed 优于死循环）。上限进 `BattleRules`（新增字段，默认值 32/16）。
2. **同源防重（source_guard）**：`AtomicActionSpec.source_guard` 记录来源（如"特性X·追加火系攻击"）；引擎维护"已追加原子签名"集合，同源同受体同 kind 已存在 → 丢弃（防"火系追加火系"无限自触发）。
3. **特例特办**：对已知会互相触发的绑定组合，在 `_CONDITIONS` 或绑定层显式互斥（如追加攻击不再触发 SKILL_RESOLVE 的追加类 hook——追加原子默认 `parent="bonus"`，可被 hook 条件排除）。
4. 兜底：`TurnExecution.atom_count` 到上限即使队列非空也强制收尾（丢弃剩余 + 告警事件）。

---

## 7. 阵亡处理（新语义，**已拍板：阵亡后恢复剩余原子**）

现状（判断 9）：`settle_faints` 在第一个阵亡处返回 need_side，`resolve_turn` 丢弃剩余队列、回合结束。
**新语义**（负责人 2026-08-28 拍板：恢复剩余原子 + 受体不存在即不执行 + DEATH_IN 插队首）：

1. 每执行完一个原子动作 → 检查阵亡；
2. **中断**：`resolve_turn` 返回 `need_replacement`（**保留剩余队列**在 `TurnExecution` 里，不丢弃）；
3. 终局判定：命归零 / 无存活 → 直接 `check_winner` 终局（无补位可问）；
4. **补位**：阵亡方 `choose_replacement` → `submit_replacement` 向该方队列**队首**投递 `DEATH_IN` 原子（`side=阵亡方`、`kind=DEATH_IN`、`receptor=入场单位下标`、`parent=death_replacement`）——新精灵立即在场；
5. **恢复**：`DEATH_IN` 执行（改 `active` + 触发 `ENTER` hook）后，**恢复执行剩余队列**（其他原子动作继续按调度执行）。

**受体不存在即不执行（取消规则，负责人拍板）**：

> 每个原子动作携带（发起者 / 发起来源动作 / 受体）。**执行时检查受体现存性**：若 `receptor` 对应单位已阵亡（受体不存在），该原子动作**直接跳过、不结算**。

- 例：阵亡前队列里残留的 `damage` 原子，其受体是原来那只阵亡精灵 → 该精灵已阵亡、受体不存在 → 该原子不执行；
- 新精灵登场（`DEATH_IN` + `ENTER` hook）可能带来**新的原子动作**（如入场效果/追加攻击）——这些新原子的受体若是新登场精灵，则正常执行；
- 受体现存性用运行时检查实现（`units[receptor].fainted` → 跳过），比"入队时暴力清队"更稳（天然覆盖补位后残留的旧原子，且不依赖清队时机）。

> ⚠️ 行为变化记录：与现状"阵亡即回合结束"不同（影响回合数、事件流、state_hash）——这是负责人拍板的新语义，Phase 2 落地时同步更新相关测试与 `docs/e-line-data-contract.md`。

---

## 8. 事件契约与迷雾

- **对外事件流尽量不变**：`SWITCH_OUT+SWITCH_IN` 对外仍合成一条 `switch` 事件（带 `out/in/cleared_layers`，兼容现有前端/visibility/重放）；新增的原子动作类型**默认不产生新事件**，除非有实际效果。
- **新增事件类型需登记** `EVENT_TYPES`（如 `atom_dropped` 审计事件），且 `visibility.py` fail-closed 天然拦截未知类型。
- **迷雾不受影响**：原子动作是引擎内部机制；hook 追加效果产生的事件仍需过 `filter_events_for`（新增字段走白名单登记）。
- **state_hash 语义不变**：`to_dict()` 不含回合内队列 → 原子重构不改变 `state_hash` 定义；无 hook 追加时同 seed 同 digest（哨兵）。

---

## 9. 不变量与迁移哨兵

1. **确定性**：同 seed + 同提交序列逐字节可复现（调度硬币走引擎流；追加原子由确定性规则产生）。
2. **回合内不落 state**：队列/`pair_locked`/`atom_count` 全住 `TurnExecution` 局部对象。
3. **迁移哨兵**：新引擎在"无 hook 追加 + 关闭 resume_after_death"时，与旧引擎同 seed digest **逐位一致**（E1 黄金基线思路的现代化：用既有 590 测试 + 旧轨迹 replay 作为哨兵）。
4. **迷雾**：新 hook 效果事件过 `filter_events_for`；新字段白名单登记。
5. **轨迹契约**：`store.py` ↔ `analysis.py` 必需键不变（原子是内部机制，轨迹仍只存提交序列）。

---

## 10. 分阶段迁移路径

| 阶段 | 内容 | 行为变化 | Gate |
|---|---|---|---|
| **Phase 0** | Hook 层扩展：`emit` 返回追加原子、ctx 加 `action`、`EffectBinding` 加 `atoms`、`AtomicActionSpec`。**不接线** | 无 | 既有 590 全绿 + 覆盖率不降 |
| **Phase 1** | 引擎引入 `AtomicAction` + 每方队列 + 调度主循环；`switch` 拆 `SWITCH_OUT/IN`（对外事件仍合成）；接线 `ENTER`/`EXIT` 时机（当前 4 特性无 ENTER/EXIT 绑定 → 零效果） | **无**（哨兵验证 digest 逐位一致） | 哨兵 + 旧轨迹 replay 全绿 |
| **Phase 2** | 阵亡新语义（**已拍板**）：中断保留队列 + 受体不存在即跳过 + `DEATH_IN` 插队首 + 恢复剩余原子 | 有（新语义） | 新测试 + 旧语义对照记录 |
| **Phase 3** | 填充：真实特性/技能效果接入新 hook 时机（EXIT/ENTER/DEAL_DAMAGE/KO…）；`_apply_effect` 扩展 target/原语；追加原子能力落地 | 按特性逐个 | 每特性测试 + 覆盖率 |

**Phase 1 是纯内部重构（对外契约零变化），Phase 2/3 才是能力增量**——这是保证"重构不破不变量"的关键。

---

## 11. 开放决策点（负责人已拍板项标注 ✅）

1. ✅ **阵亡后恢复剩余原子**（2026-08-28）：采用新语义，补位后恢复剩余原子；针对阵亡精灵（受体不存在）的原子动作跳过不执行。
2. ✅ **`DEATH_IN` 插队首**：新精灵立即在场，剩余原子的伤害可命中它。
3. ✅ **`switch` 作为特殊动作位**：按默认方式——绑定对 `[SWITCH_OUT, SWITCH_IN]` 相邻执行，对外仍合成一条 `switch` 事件（前端/迷雾/重放零改动）。
4. **循环上限数值**：回合 32 / 决策 16（建议起点），超限丢弃 + 告警。
5. **双方队列交错规则**：维持现状调度键（priority→speed→硬币，绑定对相邻）——本方案默认；备选：严格交替（行为变化大，不推荐）。
6. **追加原子的调度键**：默认 `priority=0`（可被他侧高优先打断）vs 链内优先（追加原子必在本侧链尾原子后执行）。影响"追加攻击是否会被打断"。
7. **Hook 追加原子的迷雾**：追加原子效果产生的事件若含隐藏字段，是否也按受体侧过滤（默认：是，全走 `filter_events_for`）。

---

## 12. 影响面清单

- **引擎**：`engine.py`（build_queue→build_atomic_queues、resolve_entry→resolve_atom、resolve_turn 主循环、apply_replacement→投递 DEATH_IN、**原子执行时受体现存性检查**）、`models.py`（不新增 state 字段）、`rules.py`（新增 `MAX_ATOMS_*`）、`hooks.py`/`effects.py`/`primitives.py`（emit 返回追加、ctx.action、atoms、target 扩展）、`events.py`（可选新类型登记）。
- **测试**：Phase 1 哨兵（digest 不变）；Phase 2 新阵亡语义测试（取消针对原子/DEATH_IN/恢复）；Phase 3 每特性测试；既有 590 全绿回归。
- **文档**：`docs/e-line-data-contract.md` §2 结算流水线重写；`docs/audit/` 同步。
- **R 线接缝**：本分支无 R 线；将来并回时——原子重构**不改** `run_match`/`Player`/`replay` 契约，R 线重基线不受影响（哨兵保证）。
- **前端**：对外事件不变 → battle.html/spectate.js 无需改（若选决策点 3 的拆事件方案则需改）。

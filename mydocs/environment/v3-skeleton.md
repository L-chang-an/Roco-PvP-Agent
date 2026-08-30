# v3 骨架审计文档（根动作 → 效果 → 事件 → 触发器 四层管道）

> 状态：**已施工并通过行为等价验证**（2026-08-29；2026-08-30 Phase 3.1 增量：DomainEvent
> 回传通道 / 回合边界执行点 / 伤害公式规范，621 passed + 确定性 3× 一致）
> 代码：`src/environment/{atom,domain,modifiers,compiler,reducer,triggers,pipeline,prediction}.py`
> + `engine.py`/`hooks.py`/`damage.py` 改造
> 依据：`mydocs/battle_docs.md` §7 引擎主循环 / §12 迁移路径第 1–4 步
> 护栏：`tests/test_v3_sentinel.py`（行为等价哨兵）+ 全量 **621 passed** + 覆盖率 94% + 确定性 digest 一致

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
   │  pipeline.run(state, atoms, frame, after=SkillResolved, collector)
   ▼
状态修改（唯一写入口，走 damage/primitives 漏斗）+ 展示事件（ev()，形状不变）
   │  Reducer 把 DomainEvent（DamageApplied / EnergyChanged / StatModChanged…）
   │  挂进 frame.domain_events —— pipeline 回传给 Trigger（Phase 3.1 通道）
   ▼
triggers.collect_reactions(state, event, unit, trait_defs) → 新 Atom 列表
   │  （当前接 SKILL_RESOLVE：特性效果 → TraitGain Atom）
   ▼
pipeline 把新 Atom 交回 reducer 执行 → 新事件继续反应，直至静默（fixpoint）
```

**依赖方向**：`engine → pipeline → reducer/triggers`；`engine → compiler → atom`；
`engine/prediction → damage → models`。`reducer`/`compiler`/`pipeline` 不 import engine
（无循环依赖）。

---

## 2. 各文件职责（代码注释已内嵌，此处给审计视角）

| 文件 | 职责 | 关键点 |
|---|---|---|
| `atom.py` | 类型化 Effect（Atom）声明层 | 每个 Atom 是 frozen dataclass、纯数据；**持 Unit 对象引用**（回合内局部，不序列化，规避 unit_id 依赖） |
| `domain.py` | DomainEvent 内部事实 | Trigger 的输入；与展示事件（EVENT_TYPES dict）严格分开；2026-08-30 增 TurnStarted（携预估） |
| `modifiers.py` | DamageQuery + 管线终点 | `compute` 一行委托 `damage.formula`（唯一伤害公式在 damage.py，2026-08-30 规范） |
| `compiler.py` | 技能编译器 | SkillEffect → 有序 Atom 列表；顺序与旧 resolve_skill 逐位一致（扣能→揭示→伤害段→资源效果） |
| `reducer.py` | Reducer 表 | 每个 Atom 一个 reducer，唯一改状态；领域事件挂 `frame.domain_events`（Phase 3.1，不再构造即弃） |
| `triggers.py` | Trigger 收集器 | `collect_reactions`：事件 → 新 Atom；当前接 SKILL_RESOLVE 特性；`unit=None` 放宽（TURN_END 等无施法者事件） |
| `pipeline.py` | **反应执行器（Phase 3.1）** | 全库唯一 fixpoint 循环 `run()`：原子执行 → 领域事件回传 → 收集反应 → 再执行至静默；`after` 回调（SkillResolved 读 dealt_counter）排原子领域事件之后；保护闸 budget/max_events 超限确定性 warn+停止 |
| `prediction.py` | **确定性预估（2026-08-30）** | predict_power/predict_damage/predictions_for 纯函数（无应对倍率、无 RNG、不入状态）；view 提示 + TURN_START 事件携带 |
| `damage.py` | **唯一伤害公式（2026-08-30）** | `formula()` 顺序求值出口 int() 一次；`build_damage_terms`（flat 留属性、pct 进比值、attack_power 激活）；`compute_damage` 兼容入口 |
| `engine.py` | resolve_skill 换芯 | 校验 → `_combat_skill` → `compile_skill` → `pipeline.run`；resolve_turn 入口发 TurnStarted、end_of_turn 发 TurnEnded |
| `hooks.py` | emit 兼容 shim | 委托 `pipeline.run`（测试直调场景保留）；对外行为与旧直写逐位一致 |

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

- [x] `resolve_skill` 已无 20 字段 if 分支（只剩校验 + compile + pipeline.run）。
- [x] 展示事件形状与旧引擎逐位一致（哨兵 4 项验证）。
- [x] `hooks.emit` 已委托 pipeline.run（特性走事件→Atom→Reducer，shim 保留）。
- [x] **DomainEvent 回传通道已通**（Phase 3.1）：Reducer 领域事件挂 `frame.domain_events`，
      由 `pipeline.run` 回传给 Trigger——印记/天气/DOT 接入的地基（原审计问题①）。
- [x] **回合边界执行点已铺**（Phase 3.1）：`resolve_turn` 入口发 TurnStarted（携预估）、
      `end_of_turn` 发 TurnEnded——原审计问题③。
- [x] **唯一伤害公式已落地**（2026-08-30 规范）：`damage.formula` 单一事实源、
      `modifiers.compute`/`compute_damage` 均委托——原审计问题②。
- [x] 四层管道文件各自职责单一、可单测（pipeline/prediction/formula 有专测）。
- [x] 新文件零依赖 cycle（reducer/compiler/pipeline 不 import engine）。
- [x] 621 既有+新增测试 + 4 哨兵全绿、确定性 3× 一致。

---

## 6. 后续接入点（骨架已就位，等下一步填充）

| 系统 | 接入位置 | 通道状态 |
|---|---|---|
| 印记（mark） | `marks.py` 目录 + `collect_reactions` 多源收集（特性→印记→天气）；三槽规则见 primitives.apply_mark | ✅ 已接线（2026-08-30，14 印记全结算） |
| 天气（weather） | `weather.py` 目录 + `damage.formula` weather 项（雨天 ×1.75）+ `skill_energy_cost`（沙暴减半）+ TURN_END（暴风雪/雷鸣） | ✅ 已接线（2026-08-30，4 天气全结算） |
| 纯负面 buff（DOT） | `statuses.py` 目录 + TurnEnded/StatModChanged 收集——中毒/灼烧/寄生（TURN_END 固定序）+ 引电（达 2 层即时）；属性免疫（火/草/毒）在施加层拦截；灼烧（火）/中毒（毒）/引电（电）吃克制、寄生真实伤害；**冻结力竭已接线（血量低于层数×5% 力竭，2026-08-30）** | ✅ 已接线（2026-08-30）；萌化结算下批 |
| 防御冷却 | `SetCooldown` 原子（compiler DEFENSE 分支）+ 门控（skill_block_reason）+ 回合入口仅在场递减 + 补位立即 −1 | ✅ 已接线（2026-08-30 拍板） |
| 回合开始预估特性 | TurnStarted 事件携带双侧 `predictions`（预估威力/预估伤害） | ✅ 已发事件，等绑定 |
| 阵亡补位语义 | session 层（回合边界被动补位，见 battle_docs §9） | ⬜ 设计稿，未实施（TURN_END 阵亡由 resolve_turn 开场兜底复用现有补位流） |

**加一种新效果 = 加一个 Atom 类型 + 一个 reducer + 一条 Trigger 绑定，引擎零改动**（v3 扩展铁律）。

---

## 7. 施工变更记录（2026-08-30 Phase 3.1）

| 变更 | 落点 |
|---|---|
| **伤害公式规范**（负责人拍板）：`damage.formula` 唯一公式——顺序求值出口 int() 一次；flat 层留属性、pct 层进比值项（四分量夹 cap）；attack_power 激活（flat→威力绝对值 +10×层、pct→威力百分比 +10%×层）；应对倍率先乘基础威力再加绝对值；天气项落位（恒 1.0）；逐段结算（连击数不进公式） | damage.py / modifiers.py / reducer.py |
| **预估体系**：predict_power（无应对倍率）/ predict_damage（×0.9×确定连击数，无 min_damage）/ predictions_for；view me 侧 `predictions` 提示；预估不入状态（派生量） | prediction.py / view.py |
| **反应管道**：`pipeline.run` fixpoint 循环（Frame.domain_events 回传 + 保护闸 budget/max_events 确定性 warn+停止）；engine.resolve_skill 换芯；hooks.emit 变 shim；collect_reactions 放宽 unit=None | pipeline.py / reducer.py / engine.py / hooks.py / triggers.py |
| **回合边界执行点**：TurnStarted（携双侧预估，resolve_turn 入口）+ TurnEnded（end_of_turn）；end_turn 合并回合末事件 | domain.py / engine.py |
| 哨兵零重钉：既有 600 测试 + 4 哨兵原样通过（新旧公式差异场景零覆盖），新规则由 `tests/test_environment_formula.py` 钉死 | tests/ |
| **印记与天气效果层（2026-08-30）**：marks.py/weather.py 目录、5 个新原子、多源收集器、三处读钩子（公式/能耗/速度）、ENTER/EXIT 发射点、天气递减过期、开场阵亡兜底；14 印记 + 4 天气全结算（星陨 N²+24(N−1) 拍板、暗涌逐层随机、风起执行顺序先手） | marks.py / weather.py / reducer.py / triggers.py / damage.py / primitives.py / engine.py / atom.py / domain.py |
| **印记/天气技能入口（2026-08-30）**：4 个编译模式 + MW_EFFECTS 白名单 24 条（battle_ready 扩为 P1∪P2∪MW → 203）；valid_skills.json 重新生成；吟游之弦 exclusive 路由 | skillbook.py / compiler.py / models.py / scripts/build_valid_skills.py |
| **DOT 结算（2026-08-30）**：statuses.py 目录（STATUS_TABLE 单一事实源）+ 属性免疫（火/草/毒，施加拦截）+ 伤害克制（灼烧火/中毒毒/引电电吃克制、寄生真实伤害吸血）+ 灼烧减半向下取整归零移除 + 引电即时结算扣 2 留余 + TURN_END 序 DOT→印记→天气；HealFlat/SetModLayers 新原子、LoseHp.skill_type 扩展 | statuses.py / atom.py / reducer.py / triggers.py / engine.py |
| **DOT 技能入口（2026-08-30）**：A 类施加 4 模式 + ST_EFFECTS 白名单 14 条（battle_ready = P1∪P2∪MW∪ST = 217）；ATTACK 分支逐击 stat_effects 发射；valid_skills.json 重新生成 | skillbook.py / compiler.py / models.py / scripts/build_valid_skills.py |
| **防御技能冷却（2026-08-30 拍板）**：使用防御技能 → 全防御技 cd=1；门控（skill_block_reason 第三道门）+ 回合结算入口仅在场递减（规则 1/2 分野）+ 阵亡补位立即 −1（规则 3）；cooldown 展示事件登记；SkillInstance frozen 字段写入一律 replace 重建 | atom.py / reducer.py / compiler.py / actions.py / engine.py / events.py |
| **冻结力竭判定（2026-08-30 拍板）**：血量低于冻结层数×5% → 力竭阵亡（非伤害）；`Faint` 原子 + `damage.apply_faint`（current_hp 唯一写点纪律）；`statuses.collect` 监听 DamageApplied / HpChanged / StatModChanged(冻结)；严格小于才力竭；阵亡/补位由 settle_faints 兜底；冻结技能/特性施工排序见 `freeze-impl-plan.md` | atom.py / reducer.py / damage.py / statuses.py / tests/test_environment_freeze.py |

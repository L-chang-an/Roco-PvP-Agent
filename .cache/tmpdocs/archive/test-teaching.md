# 测试教学文档（E0~E3 阶段）

日期：2026-08-25　适用：`src/environment/` 全模块 + `tests/` 全部测试

本文回答三个问题：**① 怎么设计"无死角"的测试（方法论）；② 怎么针对某个文件/方法/类写测试代码；
③ E0~E3 的测试地图与注释规范。**

---

## 1. 测试设计方法论：如何做到"无死角"

### 1.1 软件项目管理视角：测试是质量活动的骨架

在项目管理里，测试不是"最后补做"的活动，而是与每个里程碑绑定的质量门（Gate）：

| 项目管理概念 | 在本项目的落地 |
|---|---|
| **里程碑验收（Gate）** | 每个里程碑（E0a/E0b/E2/E3…）都有"验收命令 + 测试全绿 + 负责人确认"三道门，测试不绿不提交 |
| **测试金字塔** | 单元测试（多，快）→ 集成测试（中）→ 端到端/验收（少，慢）——本仓库 450 个测试里绝大多数是单元级，只有少量 CLI 子进程级 |
| **回归保护** | 同 seed 的 `digest()` 是逐字节指纹——任何改动破坏了确定性，`--repeat 2` 立刻报警 |
| **覆盖率是过程指标** | 覆盖率（当前 90%）告诉你"哪没测"，不告诉你"测对没有"——无死角 = 覆盖率 + 断言质量 + 不变式 |

**无死角的含义**：不是"每行代码跑过"，而是**每个输入域的代表值、每个分支（含防御分支）、
每个架构不变式**都被断言过。

### 1.2 软件架构视角：可测性来自架构（先有好架构，才有好测试）

本引擎的可测性不是测试写出来的，是**架构设计出来的**：

| 架构决策 | 它解锁的测试 |
|---|---|
| **纯函数 + 显式入参**（`compute_damage` 不读全局/上下文，克制倍率/减伤/连击全是入参） | 表驱动单测：同一函数喂不同参数组合，无需搭对局 |
| **依赖注入**（`BattleRng(seed)` 必填，无全局 random） | 同 seed 完全复现；`calls` 计数可断言"引擎一次随机都没抽" |
| **单一职责 + 唯一口子**（`damage.py` 是扣血/回复唯一入口） | 源码扫描测试：`current_hp` 的写点只允许出现在 damage.py 与 models.py |
| **状态可序列化**（`to_dict`/`from_dict` 字节一致往返） | 马尔可夫性测试：`from_dict(to_dict(s))` 后再跑同一回合，事件流逐字节相同 |
| **事件系统统一形状**（每条事件带 `type`/`side`） | 全量事件合法性测试：整局所有事件都在 `EVENT_TYPES` 白名单内 |

**结论**：设计代码时先问"我怎么测它"——如果答不上来，多半是架构问题（隐藏依赖、全局状态、副作用藏得太深），而不是测试的问题。

### 1.3 黑盒测试设计方法（不读实现，按输入输出设计）

**① 等价类划分**：把输入域分成"行为相同"的类，每类取一个代表值。
例：`validate_team_size(n)` 的合法域 [3,6] 与非法域 {<3, >6, 非整数}——每类测一次即可。

**② 边界值分析**：bug 最爱住在边界。每个边界测 **min−1 / min / min+1 / max−1 / max / max+1**。
例：`validate_lives(team_size=4, lives)` 测 0 / 1 / 3 / 4（1 和 3 合法，0 和 4 非法）。

**③ 判定表（决策表）**：多个条件组合时，把"条件 × 动作"列成表，保证组合全覆盖。
例：`validate_team` 的三条 FULL 规则（家族唯一/首领禁入/血脉系别）——用三个独立测试各踩一条，
再有一个"一次报全"的测试同时踩三条。

**④ 状态转换测试**：状态机必须测所有合法转移 + 非法转移被拒。
例：`BattleSession` 的 SUBMIT → RESOLVE → REPLACE_QUERY → END_TURN——测试"阵亡暂停后
再提交主动作会被拒"（状态机在 REPLACE_QUERY 时不允许 submit）。

### 1.4 白盒测试设计方法（读实现，找分支）

- **语句覆盖**：每行代码至少执行一次（覆盖率报告的 90% 就是它）。
- **分支覆盖**：每个 `if` 的真/假两路都走到——**防御分支也要测**（如 `skill_block_reason` 的
  "槽位不是整数"、"能量不足"）。
- **路径覆盖**：复合条件组合（如 `type_effectiveness` 的多系乘积、克制封顶、抵抗乘算各一路）。

### 1.5 架构不变式测试（本项目的特色，最重要）

这些测试不测某个功能，而是测**整个系统的底线不变量**——它们比功能测试更值钱：

| 不变式 | 测试 | 它在防什么 |
|---|---|---|
| **马尔可夫性**：`execute_turn` 输出只由 `(state.to_dict(), dec_a, dec_b)` 决定 | 快照往返后跑同一回合，事件流逐字节相同、state_hash 相同 | 任何"回合内临时量漏进 state"的泄漏 |
| **确定性**：同 seed → 同 digest | 同一对局跑两次（含跨进程 subprocess），digest 一致 | 环境相关非确定性（未注入的随机、字典序漂移） |
| **唯一写点** | 源码扫描：`current_hp` 写点只允许在 damage.py / models.py | 绕过伤害漏斗的"偷偷改血" |
| **回合号唯一推进** | 源码扫描：整个代码库只有一处推进回合号（engine.end_turn） | 两处推进 → 回放对不上 |
| **序列化往返** | `to_dict` → `from_dict` → 再 `to_dict` 字节一致 | 快照丢字段（迷雾/印记/特性的经典坑） |
| **事件白名单** | 整局所有事件的 type 都在 `EVENT_TYPES` 内 | 新增事件类型忘登记 → E4 迷雾 fail-closed 失效 |

### 1.6 如何确定"每个文件测什么"：清单法

对每个文件，按三层列出测试清单，写测试时逐项打勾：

1. **公开 API 层**：每个导出函数/类的方法——正常输入、边界输入、非法输入（异常路径）。
2. **内部契约层**：文件间协作的约定（如"门控与支付读同一个能耗函数"、"build_roster 产出 8 键形状"）。
3. **不变式层**：该文件参与的系统不变式（见 1.5）。

---

## 2. 如何撰写测试代码（pytest）

### 2.1 组织与命名

- 测试文件放 `tests/`，命名 `test_<被测模块>.py`（如 `test_environment_engine.py`），
  一个被测文件对应一个测试文件（测试地图见第 3 节）。
- 测试函数命名 `test_<行为描述>`，**用中文 docstring 写明"输入 → 预期输出"**。
- 共享夹具（阵容、规则）放 `tests/rosters.py` 这种辅助模块，**不**放 conftest 隐式魔法——
  显式导入比隐式注入好读。

### 2.2 pytest 基础（够用即止）

```python
# 断言：pytest 用原生 assert，失败时自动展开
assert x == 2
assert "关键词" in err                      # 错误信息断言
with pytest.raises(ValueError, match="3–6"):   # 异常断言
    build_battle_rules(team_size=7)

# 参数化：同一断言喂多组输入（等价类/边界值的标准写法）
@pytest.mark.parametrize("skills", [[], ["a", "b", "c", "d", "e"]])
def test_skill_count_out_of_range(skills): ...

# fixture：可复用的构造
def _battle(a, b):                          # 简单函数夹具即可，不必用 @pytest.fixture
    return BattleState(side_a=..., rng=BattleRng(7), ...)
```

### 2.3 针对一个函数写测试（示例：`compute_damage`）

先列清单（1.6 法）：

| 层 | 用例 |
|---|---|
| 正常 | 物攻公式精确值；魔攻读 sp_atk/sp_def；power=0 → 0 伤害 |
| 边界 | 出口只 int() 一次（截断语义）；保底伤害 min_damage=1 |
| 不变式 | 纯函数：零 RNG（`s.rng.calls == 0`）；显式入参不被上下文污染 |

```python
def test_damage_neutral_exact() -> None:
    """atk=100/def=100 抓挠1(power 80)：80×0.9 = 72。"""
    s = _solo(spec("攻", 200, 100, 100, 100, 100, 100, ["抓挠1"]),
              spec("守", 200, 100, 100, 100, 100, 100, ["抓挠1"]))
    assert compute_damage(s, s.active("a"), s.active("b"), s.active("a").skills[0]) == 72

def test_damage_single_truncation() -> None:
    """只在出口 int() 一次：int(80×0.9×1.5×0.3) = int(32.4) = 32。"""
    ...
    assert dmg == 32
    assert s.rng.calls == 0          # 纯函数：零 RNG —— 不变式断言
```

**要点**：每个用例 = 一个"输入组合 → 一个精确预期值"；纯函数直接调，不用搭整局。

### 2.4 针对一个类/一个文件写测试（示例：`validate_team`）

类测试按"方法维度"拆：每个方法一组用例；方法之间相互影响的（状态机）用"流程测试"串起来。

```python
def test_family_uniqueness_rejects_same_family() -> None:
    """规则 1：同家族两只 → 明确文案。"""
    errors = validate_team([_p1_pick("喵喵"), _p1_pick("魔力猫"), _p1_pick("火花")],
                           items=[], source=FULL)
    assert any("同一家族只能入队一只" in e for e in errors)

def test_probe_errors_reports_all_at_once() -> None:
    """一次报全：同时踩家族/首领/血脉/技能数/道具 → 多条错误，不是遇错即停。"""
    errors = validate_team(bad_picks, ["不存在道具", "草魔法", "草魔法"], source=FULL)
    assert len(errors) >= 5
```

**要点**：断言**错误文案关键词**而不是整个字符串——文案微调时不至于全炸。

### 2.5 白盒状态构造（测引擎逻辑不用走完整流程）

引擎多数函数吃 `BattleState`——测试直接构造最小状态（1v1、手写六维），
速度/能量/血量完全可控，不用依赖数据文件：

```python
def _mk(name, types, skills):
    return build_unit({"name": name, "types": types,
                       "stats": {"hp": 300, "atk": 100, ...}, "skills": skills, "trait": ""})

s = BattleState(side_a=SideState(units=[_mk("甲", ["火"], ["火焰切割"])], lives=2),
                side_b=SideState(units=[_mk("乙", ["草"], ["叶绿光束"])], lives=2),
                rng=BattleRng(7), rules=replace(DEFAULT_RULES, team_size=1))
events = execute_turn(s, Decision(skill_action(0)), Decision(recharge_action()))
```

### 2.6 CLI 子进程测试（端到端）

验证"进程级"行为（argparse、退出码、跨进程确定性）用 `subprocess`：

```python
out = subprocess.run([sys.executable, "-m", "environment", "battle",
                      "--data", "VALID", "--preset", "p1", "--seed", "7", "--json"],
                     capture_output=True, text=True, cwd=root, timeout=120)
assert out.returncode == 0, out.stderr
assert json.loads(out.stdout)["digest"] == ...   # 与进程内 digest 比对
```

### 2.7 "无死角"检查清单（写完测试后逐项打勾）

- [ ] 每个公开函数/方法：**正常用例 ≥1、边界用例、异常用例**（非法输入被拒/报错）
- [ ] 每个 `if` 分支（含防御分支）都被走到
- [ ] 每条**错误信息**至少被一个测试断言过关键词
- [ ] 涉及状态的对象：**非法转移被拒** + **合法转移链** 都测过
- [ ] 涉及随机/时间：**确定性断言**（同 seed 同结果）
- [ ] 涉及序列化：**往返测试**（to_dict→from_dict→再 to_dict 字节一致）
- [ ] 涉及全局/单例：**无泄漏**断言（跑完状态没被污染）

---

## 3. E0~E3 测试地图（每个代码文件 → 对应测试）

| 代码文件 | 对应测试文件 | 主要测什么 |
|---|---|---|
| `dataset.py` | `test_environment_dataset.py`、`test_environment_dataset_full.py`、`test_environment_dataset_valid.py` | 归一化（_to_int 防 `or` 坑）、脏值跳过、家族、三源路由、valid_skills 179/格式 |
| `statline.py` | `test_environment_dataset.py`（属性公式部分） | 六维公式手算、性格 30 种完备、iv 折合 |
| `teambuilder.py` | `test_environment_team.py`、`test_environment_team_full.py`、`test_environment_team_valid.py` | 校验规则矩阵（家族/首领/血脉/技能数 1–4）、build_roster 8 键形状、VALID 白名单 |
| `skillbook.py` | `test_p1_effects.py`、`test_p2_effects.py` | 效果编译器（125+54 全命中）、battle_ready 白名单 |
| `battle_config.py` | `test_battle_config.py` | 管理员接口边界（3–6 / 1..team_size−1）、build_battle_rules 异常 |
| `rules.py` | 各测试间接（DEFAULT_RULES 一致性在 test_battle_config） | 常量表、skill_slots=4 |
| `models.py` | `test_environment_match.py`（往返/马尔可夫）、`test_environment_traits.py` | 序列化往返、build_unit、特性解析（白板 default） |
| `rng.py` | `test_environment_engine.py`（calls 断言） | seed 复现、calls 计数、from_dict 重放 |
| `actions.py` | `test_environment_actions.py` | 动作工厂、门控（skill_block_reason 唯一谓词）、合法池、补位校验 |
| `damage.py` | `test_environment_engine.py`（伤害部分） | 公式、截断、夹取、唯一写点扫描 |
| `engine.py` | `test_environment_engine.py`、`test_environment_types.py`（E2 接线） | 回合循环、应对三角、连击、先手优先级、阵亡即停、克制/STAB |
| `hooks.py` / `effects.py` / `primitives.py` | `test_environment_effects.py`、`test_environment_traits.py` | 效果原语、驱散 scope、emit 分发、能耗/连击/吸血读取 |
| `traits.py` | `test_environment_traits.py` | 目录、白板 default、解析回退、战斗零效果 |
| `types.py` | `test_environment_types.py` | 克制表对照 md、多系封顶 ×3、抵抗乘算、STAB |
| `session.py` | `test_environment_match.py`（部分）、`test_environment_engine.py`（补位） | 状态机（提交/结算/补位/回合末）、非法提交被拒 |
| `match.py` | `test_environment_match.py` | 整局编排、digest 确定性（含跨进程） |
| `players.py` | `test_environment_match.py`（RandomPlayer 独立 RNG 流） | 脚本/随机策略、补位决策 |
| `__main__.py` | `test_cli_full.py`、`test_environment_match.py`（subprocess）、`test_environment_e3.py` | CLI 子命令、--data 三源、--team-size/--lives、退出码 |
| P1/P2/E3 集成 | `test_p1_integration.py`、`test_p2_battle.py`、`test_environment_e3.py` | 白名单池完整对局、马尔可夫、确定性 |

---

## 4. 注释规范（E0~E3 代码注释约定）

代码里每个**类、方法、重要变量**都有注释，重点写**输入、输出、作用**。约定如下：

### 4.1 模块 docstring
职责 + 数据流 + 关键设计决策（为什么这么写）。例：
```python
"""伤害与回复：全局唯一的三个口子，任何伤害/回复都不许绕过。..."""
```

### 4.2 类 docstring
类的作用 + 关键字段的语义。例：
```python
@dataclass(frozen=True)
class HpLoss:
    """一次扣血的结算结果（apply_hp_loss 的输出）。
    字段：requested=请求扣血量；applied=实际扣血量；fainted=扣后是否阵亡。"""
```

### 4.3 方法 docstring
**必须包含：输入（参数与约束）、输出（返回值 / 副作用 / 事件）**。例：
```python
def validate_team_size(n) -> str | None:
    """每方精灵数是否合法（整数且 3 ≤ n ≤ 6）。合法 → None，否则中文原因。"""
```

### 4.4 重要变量/字段
行内注释点明语义与量纲（层数/百分比/下标）。例：
```python
skill_slots: int = 4          # 每只精灵可携带技能数（E3：最多 4、至少 1）
lives: int = 2                # 每方命数；与 team_size 解耦
```

### 4.5 三条铁律（防注释破坏测试）
1. **不要在任何注释里写 `state.turn += 1`**（源码扫描钉死"全库唯一回合推进点"，写进注释会误报）。
2. **不要在任何注释里写 `current_hp =`**（写点扫描只允许 damage.py / models.py）。
3. 不要为了注释而注释——注释解释**为什么**与**契约**，不重复代码本身。

验证注释是否破坏不变式：`uv run pytest -q`（扫描测试会当场报警）。

---

## 5. 一句话总结

**测试设计 = 架构给的（可测性） + 清单法列的（覆盖面） + 不变式守的（底线）；
测试代码 = 每个函数/类按「正常/边界/异常/不变式」四维写断言；
验收 = 测试全绿 + digest 稳定 + 覆盖率可见。**

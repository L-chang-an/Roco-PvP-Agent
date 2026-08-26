"""S1 特性基建测试：效果原语 / 驱散 scope / emit 分发器 / trait 序列化往返。"""

from __future__ import annotations

from types import SimpleNamespace

from environment.effects import Effect, EffectBinding
from environment.hooks import Hook, emit
from environment.models import StatModifier, TraitState, Unit, _unit_from_dict, _unit_to_dict
from environment.primitives import apply_energy_gain, apply_stat_mod, dispel_gains
from environment.traits import TraitDef


def _unit(name: str = "迪莫", energy: int = 10) -> Unit:
    return Unit(name=name, types=["光"],
                stats={"hp": 374, "atk": 188, "sp_atk": 188, "def": 215, "sp_def": 215, "speed": 201},
                skills=[], max_hp=374, current_hp=374, energy=energy)


# ── 效果原语 ──
def test_apply_stat_mod_stacks_by_source() -> None:
    """同 (stat, mode, permanent, trait, source) 追加层；不同 source 各自成条。"""
    u = _unit()
    apply_stat_mod(u, stat="atk", mode="pct", layers=1, trait=True, source="最好的伙伴")
    apply_stat_mod(u, stat="atk", mode="pct", layers=1, trait=True, source="最好的伙伴")
    apply_stat_mod(u, stat="atk", mode="pct", layers=1, trait=False, source="加物攻")
    assert len(u.stat_mods) == 2                       # 两条：特性 + 常规
    assert sum(m.layers for m in u.stat_mods) == 3     # 特性 2 层 + 常规 1 层


def test_apply_stat_mod_tag_and_permanent() -> None:
    u = _unit()
    apply_stat_mod(u, stat="speed", mode="flat", layers=8, permanent=False, trait=True, source="加速度")
    m = u.stat_mods[0]
    assert m.trait is True and m.permanent is False and m.source == "加速度"


def test_apply_energy_gain_clamps() -> None:
    u = _unit(energy=8)
    assert apply_energy_gain(u, 5, energy_max=10) == 2
    assert u.energy == 10
    assert apply_energy_gain(u, 5, energy_max=10) == 0   # 满能量不再加


# ── 驱散 scope：特性增益免疫常规驱散 ──
def _mixed_unit() -> Unit:
    u = _unit()
    apply_stat_mod(u, stat="atk", mode="pct", layers=2, trait=True, source="特性A")
    apply_stat_mod(u, stat="atk", mode="pct", layers=1, trait=False, source="加物攻")
    apply_stat_mod(u, stat="def", mode="pct", layers=3, trait=True, source="特性B")
    return u


def test_dispel_regular_keeps_trait_gains() -> None:
    """常规「驱散增益」只清 trait=False，特性增益保留。"""
    u = _mixed_unit()
    removed = dispel_gains(u, scope="regular")
    assert removed == 1                                  # 只清掉常规 1 层
    assert len(u.stat_mods) == 2                         # 两条特性还在
    assert all(m.trait for m in u.stat_mods)


def test_dispel_all_clears_everything() -> None:
    u = _mixed_unit()
    removed = dispel_gains(u, scope="all")
    assert removed == 6
    assert u.stat_mods == []


# ── emit 分发器 ──
def _dimo_trait() -> TraitDef:
    return TraitDef(
        name="最好的伙伴",
        bindings=(
            EffectBinding(hook=Hook.SKILL_RESOLVE, cond="dealt_counter", effects=(
                Effect("stat_mod", stat="atk", mode="pct", layers=1, trait=True, permanent=False),
                Effect("energy_gain", value=2),
            )),
        ),
    )


def test_emit_fires_on_matching_hook_and_cond() -> None:
    u = _unit(energy=8)
    ctx = SimpleNamespace(unit=u, dealt_counter=True, energy_max=10)
    emit(None, Hook.SKILL_RESOLVE, ctx, [_dimo_trait()])
    assert u.stat_mods[0].trait is True and u.stat_mods[0].layers == 1
    assert u.energy == 10                                  # 能量 +2


def test_emit_skips_when_cond_not_met() -> None:
    u = _unit(energy=8)
    ctx = SimpleNamespace(unit=u, dealt_counter=False, energy_max=10)
    emit(None, Hook.SKILL_RESOLVE, ctx, [_dimo_trait()])
    assert u.stat_mods == [] and u.energy == 8             # 未造成克制，不触发


def test_emit_ignores_other_hooks() -> None:
    u = _unit()
    ctx = SimpleNamespace(unit=u, dealt_counter=True, energy_max=10)
    emit(None, Hook.EXIT, ctx, [_dimo_trait()])            # 绑定挂在 SKILL_RESOLVE
    assert u.stat_mods == [] and u.energy == 10


# ── trait 序列化往返 ──
def test_trait_and_mods_roundtrip() -> None:
    u = _unit()
    u.trait = TraitState(name="最好的伙伴")
    apply_stat_mod(u, stat="atk", mode="pct", layers=2, trait=True, source="最好的伙伴")
    restored = _unit_from_dict(_unit_to_dict(u))
    assert restored.trait.name == "最好的伙伴"
    assert restored.stat_mods[0].trait is True
    assert restored.stat_mods[0].layers == 2


def test_trait_flag_defaults_false_for_legacy_mods() -> None:
    """旧快照（无 trait 字段）反序列化时 trait 默认 False、trait 实例为 None，不炸。"""
    d = {
        "name": "迪莫", "types": ["光"],
        "stats": {"hp": 374, "atk": 188, "sp_atk": 188, "def": 215, "sp_def": 215, "speed": 201},
        "skills": [], "nature": "坦率", "bloodline": "", "iv": {},
        "max_hp": 374, "current_hp": 374, "energy": 10, "fainted": False,
        "stat_mods": [{"stat": "atk", "mode": "pct", "layers": 9, "permanent": False, "source": "加物攻"}],
        # 无 "trait" 键（旧快照）
    }
    u = _unit_from_dict(d)
    assert u.trait is None
    assert u.stat_mods[0].trait is False

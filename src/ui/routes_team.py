"""组队页 REST 契约（/api/team/*）：精灵搜索 + 技能池 + 组队校验 + 队伍持久化。

数据源（全部来自 `environment` 层，服务端权威，前端不自行计算规则）：
- **精灵池** = FULL（全部真实精灵 593 只；`is_boss` 标记，前端禁选——首领不可入队）。
- **技能池** = 每只精灵的**可学池**（默认∪技能石∪传说，血脉技能按所选血脉系别过滤），
  每条技能带 `battle_ready` 标记（是否在已实装效果白名单 P1∪P2 内）——**未实装技能前端
  置灰不可选**，保证存下来的队伍永远能直接开战（VALID 语义，见 teambuilder.learnable_skills）。
- **校验** = `validate_team(picks, [], rules, VALID)`，**保存前强制通过**：存下的队伍 = 合法队伍。

队伍持久化：`TEAMS_DIR`（项目根 `teams/`）为**相对路径**根；调用方可传**绝对路径**（用户自选
位置）。文件格式：
    {version, saved_at, team_size, team: [TeamPick-as-dict]}
`load` 原样恢复编辑态；`saved` 列出已存队伍；路径带 `.json` 后缀 + 原子写。

协议闸门（沿用 chat 的纪律）：
- 请求体 `extra="forbid"`：未知字段直接 422；
- `team_size` 经 `battle_config.validate_team_size`（3–6）闸门；
- 相对路径必须落在 `TEAMS_DIR` 内（防 `..` 逃逸）；
- **绝对路径仅 save/load 开放**（负责人需求「保存到任意路径」，本地工具、默认绑 127.0.0.1、
  无鉴权，属单用户信任边界）；**delete 只允许 TEAMS_DIR 内**——「删任意文件」是破坏性原语，不开缺口。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from environment.battle_config import (
    ALLOWED_TEAM_SIZES,
    DEFAULT_TEAM_SIZE,
    validate_team_size,
)
from environment.dataset import DataSource, load_skills, load_spirits, load_types
from environment.rules import DEFAULT_ITEMS, DEFAULT_RULES, ITEM_DESCRIPTIONS, ITEMS, BattleRules
from environment.statline import NATURE_BONUS, NEUTRAL_NATURE
from environment.teambuilder import (
    BOSS_BLOODLINE,
    TeamPick,
    build_roster,
    learnable_skills,
    validate_team,
)

router = APIRouter(prefix="/api/team", tags=["team"])

# 队伍持久化目录：项目根 / teams（相对路径的根；绝对路径由调用方指定）。
TEAMS_DIR = Path(__file__).resolve().parents[2] / "teams"

FULL = DataSource.FULL
VALID = DataSource.VALID


# --------------------------------------------------------------------------
# 请求体（协议闸门：extra="forbid"）
# --------------------------------------------------------------------------


class PickBody(BaseModel):
    """一只精灵的组队意图，形状 = environment.TeamPick（前端保存/校验的最小单元）。"""

    spirit: str = Field(min_length=1)
    skills: list[str] = []
    bloodline: str = ""
    nature: str = NEUTRAL_NATURE
    iv: dict[str, int] = {}          # 0–iv_max；最多 3 个维度有投入（validate_team 校验）

    model_config = ConfigDict(extra="forbid")


class TeamBody(BaseModel):
    """整队校验请求：picks + 队伍规模（仅 3 或 6）+ 道具栏（对战道具，队伍级）。

    `items`：本队携带的对战道具名列表（`ITEMS` 的子集、不重复），缺省 `["草魔法"]`
    （与 `DEFAULT_ITEMS` 对齐）。空列表 = 不携带道具。
    """

    team: list[PickBody]
    team_size: int = DEFAULT_TEAM_SIZE
    items: list[str] = []

    model_config = ConfigDict(extra="forbid")


class SaveTeamBody(TeamBody):
    """保存请求：在校验之上加 `path`（绝对路径或相对 TEAMS_DIR 的文件名/子路径）。"""

    path: str = ""

    model_config = ConfigDict(extra="forbid")


class DeleteTeamBody(BaseModel):
    """删除请求：path 语义同 SaveTeamBody。"""

    path: str

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _now() -> str:
    """UTC 时间戳（保存文件用）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_team_path() -> Path:
    """空 path 时的默认落盘位置：微秒级时间戳，避免同一秒内两次保存互相覆盖。"""
    return TEAMS_DIR / f"team_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"


def _ensure_team_size(n: int) -> int:
    """队伍规模闸门：3–6 整数，非法 → 422（前端与保存共用）。"""
    err = validate_team_size(n)
    if err:
        raise HTTPException(status_code=422, detail=err)
    return n


def _pick_from(body: PickBody) -> TeamPick:
    """PickBody → TeamPick（形状一致，直接转）。"""
    return TeamPick(
        spirit=body.spirit,
        skills=list(body.skills),
        bloodline=body.bloodline,
        nature=body.nature,
        iv=dict(body.iv),
    )


def _enrich_pick(p: PickBody) -> dict:
    """PickBody → 落盘 dict（版本 2 富化：技能带 {name,type,desc}、精灵带 trait{name,desc}）。

    从 FULL 数据查表嵌入——保存文件自描述，不依赖数据源也能读懂。
    **富化只发生在写盘时**；请求体仍是最简形状（PickBody extra="forbid" 拒绝富化字段）。
    """
    d = p.model_dump()
    full_skills = load_skills(DataSource.FULL)
    d["skills"] = [
        {"name": s, "type": full_skills[s].type, "desc": full_skills[s].desc}
        for s in p.skills if s in full_skills
    ]
    sp = load_spirits(DataSource.FULL).get(p.spirit)
    if sp is not None:
        d["trait"] = {"name": sp.trait_name, "desc": sp.trait_desc}
    return d


def _rules_for(team_size: int) -> BattleRules:
    """校验/构建用的引擎规则（只读 team_size / skill_slots / iv_max，lives 无关）。"""
    return BattleRules(team_size=team_size)


def _resolve_path(path: str, *, must_exist: bool) -> Path:
    """把调用方给的路径解析成落盘/读取位置。

    - 空 path → TEAMS_DIR 下按时间戳起名；
    - 相对路径 → 视为 **TEAMS_DIR 下的相对路径**（`测试队.json` → `teams/测试队.json`），
      拼合后整体 resolve，`..` 逃逸出 TEAMS_DIR → 422；
    - 绝对路径 → 直接用（调用方自选位置）。
    统一强制 `.json` 后缀。
    """
    base = TEAMS_DIR.resolve()
    p = Path(path.strip()) if path.strip() else _default_team_path()
    if not p.is_absolute():
        try:
            p = (base / p).resolve()
            p.relative_to(base)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"相对路径必须位于组队目录内：{base}",
            ) from None
    if p.suffix.lower() != ".json":
        raise HTTPException(status_code=422, detail="队伍文件必须是 .json 后缀。")
    if must_exist and not p.exists():
        raise HTTPException(status_code=404, detail=f"队伍文件不存在：{p}")
    return p


def _atomic_write(target: Path, payload: dict) -> None:
    """同目录临时文件 + fsync + os.replace：写一半断电也不会留下半个 JSON。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".team-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _spirit_card(sp) -> dict:
    """RawSpirit → 前端卡片（裁剪字段：搜索/展示/家族冲突判定需要的最小集）。"""
    return {
        "name": sp.name,
        "number": sp.number,
        "types": list(sp.types),
        "trait_name": sp.trait_name,
        "trait_desc": sp.trait_desc,
        "stats": dict(sp.stats),
        "is_boss": sp.is_boss,
        "family_key": sp.family_key,
        "family_lowest": sp.family_lowest,
        "region": sp.region,
    }


def _skill_card(s, battle_ready: bool) -> dict:
    """RawSkill → 前端技能卡片（含未实装标记）。"""
    return {
        "name": s.name,
        "type": s.type,
        "kind": s.kind,
        "power": s.power,
        "energy_cost": s.energy_cost,
        "desc": s.desc,
        "battle_ready": battle_ready,
    }


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------


@router.get("/config")
def team_config() -> dict[str, Any]:
    """UI 元数据：队伍规模范围、技能槽位数、个体值上限、性格、系别。

    由 team.js init 拉取；规则常量全部来自 `environment`（battle_config / rules /
    statline），前端不硬编码，后端改规则不用动前端。
    """
    natures: list[dict[str, Any]] = [{"name": NEUTRAL_NATURE, "plus": "", "minus": ""}]
    natures += [
        {"name": n, "plus": plus, "minus": minus}
        for n, (plus, minus) in NATURE_BONUS.items()
    ]
    return {
        "ok": True,
        "team_size": {
            "allowed": list(ALLOWED_TEAM_SIZES),
            "default": DEFAULT_TEAM_SIZE,
        },
        "skill_slots": DEFAULT_RULES.skill_slots,
        "iv_max": DEFAULT_RULES.iv_max,
        "iv_dims_max": 3,                       # validate_team 的「最多 3 个维度」
        "natures": natures,
        "types": sorted(load_types(VALID)),
        "source": VALID.value,                  # 技能池口径：已实装效果的技能子集
        # 对战道具目录（队伍级道具栏选择器；默认携带草魔法）
        "items": [{"name": n, "desc": ITEM_DESCRIPTIONS.get(n, "")} for n in ITEMS],
        "default_items": list(DEFAULT_ITEMS),
        # 首领血脉（2026-08-30）：血脉选择里的特殊值，表明精灵具有首领血脉
        "boss_bloodline": BOSS_BLOODLINE,
    }


@router.get("/spirits")
def list_spirits(search: str = "") -> dict[str, Any]:
    """精灵目录（FULL，全部 593 只，裁剪字段）。`search` 按 名/编号/系别 过滤。

    前端拿到全量后**客户端过滤**（594 条在本地秒级响应）；`search` 参数供其它客户端
    （测试 / 后续页面）直接按名搜。首领形态带 `is_boss` 标记，前端禁选。
    """
    spirits = load_spirits(FULL)
    q = search.strip().lower()
    items = [
        _spirit_card(sp)
        for name, sp in spirits.items()
        if not q
        or q in name.lower()
        or q in sp.number.lower()
        or any(q in t.lower() for t in sp.types)
    ]
    items.sort(key=lambda d: (_num_key(d["number"]), d["name"]))
    return {"ok": True, "total": len(items), "spirits": items}


def _num_key(number: str) -> int:
    """图鉴号排序键：纯数字按 int，否则丢 0 排前。"""
    return int(number) if number.isdigit() else 0


@router.get("/skills")
def spirit_skills(spirit: str, bloodline: str = "") -> dict[str, Any]:
    """指定精灵的技能池（可学池 ∩ 数据表），按当前血脉系别过滤血脉技能。

    返回**全部可学技能**，每条带 `battle_ready`（是否已实装效果）：
    - battle_ready=True → 可选（存下来能开战）；
    - battle_ready=False → 前端置灰 + 「未实装」标记，不可选。
    这样既透明（玩家看到完整可学池）又保证保存的队伍永远合法。
    """
    spirits = load_spirits(FULL)
    if spirit not in spirits:
        raise HTTPException(status_code=404, detail=f"精灵「{spirit}」不存在。")
    full_skills = load_skills(FULL)
    valid = load_skills(VALID)
    pool = learnable_skills(spirit, bloodline, FULL)      # 完整可学池
    skills = [
        _skill_card(full_skills[n], n in valid)
        for n in pool
        if n in full_skills
    ]
    skills.sort(key=lambda d: (d["type"], d["name"]))
    return {
        "ok": True,
        "spirit": spirit,
        "bloodline": bloodline,
        "total": len(skills),
        "implemented": sum(1 for s in skills if s["battle_ready"]),
        "skills": skills,
    }


@router.post("/validate")
def validate_team_route(body: TeamBody) -> dict[str, Any]:
    """校验整队（一次报全部错误）。合法时返回 `build_roster` 的权威属性供预览/保存。

    这是**保存的闸门**：`validate_team(picks, [], rules, VALID)` 通过才允许落盘。
    """
    _ensure_team_size(body.team_size)
    picks = [_pick_from(p) for p in body.team]
    rules = _rules_for(body.team_size)
    errors = validate_team(picks, body.items, rules, VALID)
    if errors:
        return {"ok": False, "errors": errors}
    roster = build_roster(picks, VALID, rules)
    return {"ok": True, "errors": [], "team_size": body.team_size, "roster": roster}


@router.post("/save")
def save_team(body: SaveTeamBody) -> dict[str, Any]:
    """保存队伍到路径（校验通过才写）。返回落盘绝对路径 + 时间戳。

    路径语义见 `_resolve_path`：空 → teams/时间戳.json；相对 → teams/下；绝对 → 自选位置。
    """
    _ensure_team_size(body.team_size)
    picks = [_pick_from(p) for p in body.team]
    errors = validate_team(picks, body.items, _rules_for(body.team_size), VALID)
    if errors:
        raise HTTPException(status_code=422, detail={"message": "队伍不合法，未保存。", "errors": errors})
    target = _resolve_path(body.path, must_exist=False)
    saved_at = _now()
    payload = {
        "version": 2,
        "saved_at": saved_at,
        "team_size": body.team_size,
        "items": list(body.items),
        "team": [_enrich_pick(p) for p in body.team],
    }
    _atomic_write(target, payload)
    return {"ok": True, "path": str(target), "saved_at": saved_at}


@router.get("/saved")
def saved_teams() -> dict[str, Any]:
    """列出 TEAMS_DIR 下已存队伍（按修改时间倒序）：文件名 / 路径 / 规模 / 精灵 / mtime。

    前端「加载」下拉从这里取；只列 TEAMS_DIR 内的文件（绝对路径存的需手输路径加载）。
    坏文件（非 UTF-8 / 非 JSON / 结构不对）**逐个跳过**——一个坏文件不拖垮整个列表。
    """
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for f in sorted(TEAMS_DIR.glob("*.json")):
        try:
            raw = f.read_text(encoding="utf-8")
            data = json.loads(raw)
            team = data.get("team", []) if isinstance(data, dict) else []
            if not isinstance(team, list):
                continue
            # read_text 与 stat 是两次独立 syscall，中间文件可能被并发删除 → stat 也放进 try
            mtime = f.stat().st_mtime
            team_size = data.get("team_size", len(team)) if isinstance(data, dict) else len(team)
            spirits = [
                p.get("spirit", "") for p in team
                if isinstance(p, dict) and isinstance(p.get("spirit"), str)
            ]
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue  # 坏文件跳过，不拖垮列表
        items.append({
            "name": f.name,
            "path": str(f),
            "mtime": mtime,
            "team_size": team_size,
            "spirits": spirits,
        })
    items.sort(key=lambda d: d["mtime"], reverse=True)
    return {"ok": True, "dir": str(TEAMS_DIR), "teams": items}


@router.post("/delete")
def delete_team(body: DeleteTeamBody) -> dict[str, Any]:
    """删除一个已存队伍文件（path 语义同 save）。前端「删除」按钮用。

    **删除只允许 TEAMS_DIR 内的文件**（绝对/相对都强制在根内）——「删任意 .json」是
    破坏性原语，本接口不给它开任何缺口；UI 的删除按钮也只作用于 `saved` 列表（本就是 teams/ 下）。
    """
    target = _resolve_path(body.path, must_exist=True).resolve()   # resolve 掉 `..`，再做包含检查
    base = TEAMS_DIR.resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"删除仅支持组队目录内的文件：{base}",
        ) from None
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"删除失败：{exc}") from None
    return {"ok": True, "path": str(target)}


@router.get("/load")
def load_team(path: str) -> dict[str, Any]:
    """加载一个已存队伍（绝对路径或相对 TEAMS_DIR）。返回文件里的原样内容。

    前端 load 后按 `team_size` 恢复槽位，并**自动重跑 /validate**——历史队伍可能因
    技能白名单变化而失效，加载即提示。
    """
    target = _resolve_path(path, must_exist=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"队伍文件无法解析：{exc}") from None
    if not isinstance(data, dict) or "team" not in data or not isinstance(data["team"], list):
        raise HTTPException(status_code=422, detail="队伍文件格式不正确（缺 team 数组）。")
    return {
        "ok": True,
        "path": str(target),
        "version": data.get("version", 1),
        "saved_at": data.get("saved_at", ""),
        "team_size": data.get("team_size", len(data["team"])),
        "items": data.get("items", list(DEFAULT_ITEMS)),
        "team": data["team"],
    }

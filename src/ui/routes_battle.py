"""战斗 REST 契约（/api/battle/*）：人类 vs LLM（假LLM）交互对战。

数据流：**复用组队模块**（`routes_team.PickBody` 形状 + `validate_team`/`build_roster`），
战前强制校验合法性（VALID 口径）——存下来的队伍永远能开战。引擎持有完整状态推进；
给人类的观测/事件经 E4 `view`/`filter_events_for` 屏蔽（见 environment/view.py + visibility.py）。

对局持久化：`BATTLES_DIR`（项目根 `battles/`），每条一文件、原子写。
记录格式 `{version, battle_id, saved_at, seed, opponent, rules, team_a, team_b, winner, done, turns}`，
`turns` 逐回合存 `(decisions, replacements, llm_reply, events, state_hash)`——**重放** = 按
`(rules, seed, 双方 roster, 逐回合提交)` 重建全新 session 重放，逐回合 `state_hash` 比对
（马尔可夫不变式的可执行验证）。

协议闸门（沿用 chat/team 纪律）：请求体 `extra="forbid"`；`team_size`/`lives` 经
`battle_config.build_battle_rules`；相对路径必须落在 `BATTLES_DIR` 内（防 `..` 逃逸）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from environment.battle_config import DEFAULT_LIVES, MIN_TEAM_SIZE, build_battle_rules
from environment.dataset import DataSource
from environment.presets import fixed_team
from environment.replay import replay_record
from environment.rules import DEFAULT_ITEMS, DEFAULT_RULES, BattleRules
from environment.session import BattleSession
from environment.teambuilder import TeamPick, build_roster, validate_team

from .battle import BattleController, get_controller, register
from .routes_team import PickBody, _pick_from

router = APIRouter(prefix="/api/battle", tags=["battle"])

# 对局持久化目录：项目根 / battles（相对路径的根；绝对路径由调用方指定）。
BATTLES_DIR = Path(__file__).resolve().parents[2] / "battles"


# --------------------------------------------------------------------------
# 请求体（协议闸门：extra="forbid"）
# --------------------------------------------------------------------------


class StartBattleBody(BaseModel):
    """开局请求：双方队伍（TeamPick 形状）+ 对战道具 + 管理员对局配置。

    `team_b` 缺省 → 用 `fixed_team(team_size)` 固定预设（测试期 LLM 的固定队伍配置）。
    `items_a`/`items_b`：双方携带的对战道具（`ITEMS` 子集）；空 → 缺省 `DEFAULT_ITEMS`
    （草魔法）。由组队页已存队伍的 `items` 字段带过来。
    """

    team_a: list[PickBody]
    team_b: list[PickBody] | None = None
    team_size: int = MIN_TEAM_SIZE
    lives: int = DEFAULT_LIVES
    max_turns: int = DEFAULT_RULES.max_turns
    seed: int | None = None            # 缺省自动生成；显式给 → 可复现
    opponent: str = "fake_llm"         # fake_llm（测试：固定回复+随机动作）| random
    items_a: list[str] = []
    items_b: list[str] = []

    model_config = ConfigDict(extra="forbid")


class ActBody(BaseModel):
    """人类出招：一个主动作 + 可选道具 + 道具参数。

    `item_arg`：首领进化多分支（迪莫 4 / 魔力猫 2）时的分支精灵名；单分支可空。
    """

    action: dict
    item: str = ""
    item_arg: str = ""

    model_config = ConfigDict(extra="forbid")


class ReplaceBody(BaseModel):
    """人类补位选择（己方在场阵亡后）。"""

    bench_idx: int

    model_config = ConfigDict(extra="forbid")


class PathBody(BaseModel):
    """路径请求（重放/加载）：绝对路径或相对 BATTLES_DIR 的文件名/子路径。"""

    path: str

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _now() -> str:
    """UTC 时间戳（落盘用）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _auto_seed() -> int:
    """缺省种子：纳秒级时间戳截断（确定性对局由显式 seed 承担；自动种子只保证不重复）。"""
    return int(time.time_ns() % (2**31))


def _atomic_write(target: Path, payload: dict) -> None:
    """同目录临时文件 + fsync + os.replace：写一半断电也不会留下半个 JSON。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".battle-", suffix=".tmp")
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


def _resolve_battle_path(path: str, *, must_exist: bool) -> Path:
    """把调用方给的路径解析成落盘/读取位置（语义同 team 的 _resolve_path，根换为 BATTLES_DIR）。

    - 相对路径 → 视为 BATTLES_DIR 下的相对路径，拼合后整体 resolve，`..` 逃逸 → 422；
    - 绝对路径 → 直接用。统一强制 `.json` 后缀。
    """
    base = BATTLES_DIR.resolve()
    p = Path(path.strip())
    if not p.is_absolute():
        try:
            p = (base / p).resolve()
            p.relative_to(base)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"相对路径必须位于对局记录目录内：{base}",
            ) from None
    if p.suffix.lower() != ".json":
        raise HTTPException(status_code=422, detail="对局记录必须是 .json 后缀。")
    if must_exist and not p.exists():
        raise HTTPException(status_code=404, detail=f"对局记录不存在：{p}")
    return p


def _save(ctrl: BattleController) -> None:
    """把进行中对局完整记录原子写盘（每回合一次）。"""
    _atomic_write(BATTLES_DIR / f"{ctrl.battle_id}.json", ctrl.record())


def _require(battle_id: str) -> BattleController:
    """进程内对局句柄；不存在 → 404。"""
    ctrl = get_controller(battle_id)
    if ctrl is None:
        raise HTTPException(status_code=404, detail=f"对局不存在：{battle_id}")
    return ctrl


# --------------------------------------------------------------------------
# 路由
# --------------------------------------------------------------------------


@router.post("/start")
def start_battle(body: StartBattleBody) -> dict:
    """开局：校验双方队伍（VALID）→ 构造规则/种子 → BattleSession → 登记 + 落盘。

    非法队伍 → 422 带全部错误（复用 validate_team 一次报全）。
    """
    if body.opponent not in ("fake_llm", "random"):
        raise HTTPException(status_code=422, detail=f"未知对手类型「{body.opponent}」（fake_llm | random）。")
    if isinstance(body.max_turns, bool) or not isinstance(body.max_turns, int) or body.max_turns < 1:
        raise HTTPException(status_code=422, detail=f"回合上限必须是 ≥1 的整数，实际 {body.max_turns!r}。")
    try:
        rules = build_battle_rules(team_size=body.team_size, lives=body.lives, max_turns=body.max_turns)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    picks_a = [_pick_from(p) for p in body.team_a]
    if body.team_b is not None:
        picks_b = [_pick_from(p) for p in body.team_b]
    else:
        try:
            picks_b = fixed_team(body.team_size)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    errors = (validate_team(picks_a, body.items_a, rules, DataSource.VALID)
              + validate_team(picks_b, body.items_b, rules, DataSource.VALID))
    if errors:
        raise HTTPException(status_code=422, detail={"message": "队伍不合法，无法开局。", "errors": errors})

    roster_a = build_roster(picks_a, DataSource.VALID, rules)
    roster_b = build_roster(picks_b, DataSource.VALID, rules)
    # 空 items → 缺省 DEFAULT_ITEMS（草魔法）；非空 → 显式道具栏
    items_a = body.items_a or list(DEFAULT_ITEMS)
    items_b = body.items_b or list(DEFAULT_ITEMS)
    seed = body.seed if body.seed is not None else _auto_seed()
    saved_at = _now()
    battle_id = f"battle_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    session = BattleSession.start(roster_a, roster_b, seed=seed, items_a=items_a,
                                  items_b=items_b, rules=rules, battle_id=battle_id)

    team_a = [p.model_dump() for p in body.team_a]
    team_b = [p.model_dump() for p in body.team_b] if body.team_b is not None else [asdict(p) for p in picks_b]
    ctrl = BattleController(battle_id, session, seed=seed, opponent=body.opponent,
                            team_a=team_a, team_b=team_b, rules=rules, saved_at=saved_at,
                            items_a=items_a, items_b=items_b)
    register(ctrl)
    _save(ctrl)
    snap = ctrl.snapshot()
    snap["opponent"] = body.opponent
    return snap


@router.get("/saved")
def saved_battles() -> dict:
    """列出 BATTLES_DIR 下已存对局（按修改时间倒序）：文件名/胜者/回合数/双方精灵。
    坏文件（非 UTF-8 / 非 JSON）**逐个跳过**——一个坏文件不拖垮整个列表。"""
    BATTLES_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    for f in sorted(BATTLES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            turns = data.get("turns", []) if isinstance(data, dict) else []
            if not isinstance(turns, list):
                continue
            mtime = f.stat().st_mtime
            team_a = [p.get("spirit", "") for p in (data.get("team_a") or []) if isinstance(p, dict)]
            team_b = [p.get("spirit", "") for p in (data.get("team_b") or []) if isinstance(p, dict)]
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        items.append({
            "name": f.name,
            "path": str(f),
            "mtime": mtime,
            "winner": data.get("winner"),
            "done": data.get("done", False),
            "turn_count": len(turns),
            "seed": data.get("seed"),
            "opponent": data.get("opponent"),
            "team_a": team_a,
            "team_b": team_b,
        })
    items.sort(key=lambda d: d["mtime"], reverse=True)
    return {"ok": True, "dir": str(BATTLES_DIR), "battles": items}


@router.get("/load")
def load_battle(path: str) -> dict:
    """加载一条已存对局记录（完整轨迹：逐回合 decisions/events/hash）。"""
    target = _resolve_battle_path(path, must_exist=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"对局记录无法解析：{exc}") from None
    if not isinstance(data, dict) or "turns" not in data:
        raise HTTPException(status_code=422, detail="对局记录格式不正确（缺 turns 数组）。")
    data["path"] = str(target)
    return data


@router.post("/replay")
def replay_battle(body: PathBody) -> dict:
    """按存下的 (rules, seed, 双方 roster, 逐回合提交序列) 重建全新 session 重放。

    重放实现归 `environment.replay.replay_record`（引擎层单一实现，E6 CLI / 自博弈自检
    同一份）；本端点只做「picks 记录 → roster spec 记录」的归一后转交。
    返回逐回合 `expected_hash vs actual_hash` 比对——马尔可夫不变式的可执行验证。
    任何一处失配 → 该回合 `match=false`（一旦失配即停止后续，状态已偏离）。
    """
    target = _resolve_battle_path(body.path, must_exist=True)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"对局记录无法解析：{exc}") from None
    if not isinstance(data, dict) or "turns" not in data:
        raise HTTPException(status_code=422, detail="对局记录格式不正确（缺 turns 数组）。")

    rules = BattleRules(**data["rules"])
    roster_a = build_roster([TeamPick(**p) for p in data["team_a"]], DataSource.VALID, rules)
    roster_b = build_roster([TeamPick(**p) for p in data["team_b"]], DataSource.VALID, rules)
    # 归一成引擎重放消费的规格（roster spec）：记录里的 team 是 picks，重放用 build_roster 产物。
    # items 一并回传（旧记录缺 → None → 缺省 DEFAULT_ITEMS，与旧开局一致）。
    try:
        return replay_record({**data, "team_a": roster_a, "team_b": roster_b,
                              "items_a": data.get("items_a"), "items_b": data.get("items_b")})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/stream")
def spectate_stream(seed: int = 7, a: str = "llm", b: str = "llm",
                    team_size: int = 3, lives: int = 2, max_turns: int | None = None) -> StreamingResponse:
    """观战流（E7）：两个 LLM 自博弈的 SSE 流，观战者**全局视角**。

    帧协议：`meta → state → turn* → done`（见 `run_spectate`）。两个 LLM 玩家仍走迷雾口径
    （`drive_turn` 只喂 `view()` + 过滤事件）——只有流本身是全局的。配置错误 → `error` 帧。
    """
    from roco_pvp_agent.battle.selfplay import run_spectate
    from roco_pvp_agent.config import get_settings

    def _gen():
        try:
            for frame in run_spectate(seed=seed, a_kind=a, b_kind=b, team_size=team_size,
                                      lives=lives, max_turns=max_turns, settings=get_settings()):
                yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            yield f"data: {json.dumps({'event': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/{battle_id}")
def battle_state(battle_id: str) -> dict:
    """进行中/已结束对局的快照（人类视角，迷雾口径）。"""
    return _require(battle_id).snapshot()


@router.post("/{battle_id}/starter")
def choose_starter(battle_id: str, body: ReplaceBody) -> dict:
    """第 0 回合：人类选首发（bench_idx = 槽位下标）。选中后假LLM 也选首发，
    双方首发触发入场效果，进入出招阶段。返回过滤后事件与新观测。"""
    ctrl = _require(battle_id)
    out = ctrl.choose_starter(body.bench_idx)
    if out.get("ok"):
        _save(ctrl)
    return out


@router.post("/{battle_id}/act")
def act(battle_id: str, body: ActBody) -> dict:
    """人类出招：推进一整回合（含假LLM 决定 + 结算 + 补位），返回过滤后事件与新观测。
    非法行动 → `{"ok": False, "error": …}`（零状态变更，HTTP 200）。"""
    ctrl = _require(battle_id)
    out = ctrl.act(body.action, body.item, body.item_arg)
    if out.get("ok"):
        _save(ctrl)
    return out


@router.post("/{battle_id}/replace")
def replace(battle_id: str, body: ReplaceBody) -> dict:
    """人类补位：应用补位 → 回合末收尾，返回过滤后事件与新观测。"""
    ctrl = _require(battle_id)
    out = ctrl.replace(body.bench_idx)
    if out.get("ok"):
        _save(ctrl)
    return out

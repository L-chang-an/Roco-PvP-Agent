"""顾问 Agent（M3）：复用 ChatAgent 循环，换顾问 prompt + 工具集 + 结构化终结。

- 工具集 = M1 catalog/validate + M2 query_trajectory_evidence + analyze_team + simulate_matchups。
- 终结工具 = `submit_team_advice`：EvidenceGate 校验，失败让模型修复一次，再失败安全降级。
- 关闭思维链外显（emit_thinking=False），只展示「查了哪些证据 / 过了哪些校验」。
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from environment.battle_config import build_battle_rules
from environment.dataset import DataSource
from environment.teambuilder import TeamPick, build_roster

from rock_pvp_agent.advisor import catalog
from rock_pvp_agent.advisor import validate as advisor_validate
from rock_pvp_agent.advisor.advice import submit_team_advice as _submit_team_advice
from rock_pvp_agent.advisor.analysis import analyze_team as _analyze_team
from rock_pvp_agent.advisor.catalog import SpiritFilter
from rock_pvp_agent.advisor.prompt import ADVISOR_SYSTEM_PROMPT
from rock_pvp_agent.advisor.simulate import simulate_matchups as _simulate_matchups
from rock_pvp_agent.advisor.trajectory import query_trajectory_evidence as _query_trajectory
from rock_pvp_agent.agent import EMPTY_REPLY, ChatAgent
from rock_pvp_agent.tools import FINAL_ANSWER_TOOL, final_answer

TERMINAL_TOOL = "submit_team_advice"


def _dump(obj) -> str:
    """对象 → JSON 字符串（工具返回值统一 JSON，LLM 好读）。None → "null"。"""
    if obj is None:
        return "null"
    return json.dumps(obj, ensure_ascii=False)


def _to_team_pick(p: dict) -> TeamPick:
    """pick dict → TeamPick（缺字段给默认，让 validate_team 报结构化错误而非抛）。"""
    return TeamPick(
        spirit=p.get("spirit", ""),
        skills=list(p.get("skills") or []),
        bloodline=p.get("bloodline", ""),
        nature=p.get("nature", "坦率"),
        iv=dict(p.get("iv") or {}),
    )


def _picks_to_roster(picks: list[dict], source: DataSource = DataSource.VALID) -> list[dict]:
    """pick dicts → roster（build_roster；非法阵容抛 ValueError，由 _invoke_tool 吞成错误）。"""
    rules = build_battle_rules(team_size=len(picks))
    return build_roster([_to_team_pick(p) for p in picks], source, rules)


def _build_advisor_tools(*, battles_dir=None, runs_dir=None) -> list:
    """顾问工具集（LangChain @tool，闭包捕获目录配置）。"""

    @tool
    def get_catalog_version() -> str:
        """返回当前数据/规则版本指纹与数据量（回答引用的唯一锚点）。"""
        return _dump(catalog.get_catalog_version())

    @tool
    def search_spirits(filters: list[list[dict]]) -> str:
        """白名单 DSL 检索精灵。filters 外层 OR、内层 AND；每原子 {field, op, value}。
        field ∈ name/type/trait/learnable_skill/family/is_boss/number；op ∈ eq/in/contains。"""
        compiled = [[SpiritFilter(field=f.get("field", ""), op=f.get("op", ""),
                                  value=f.get("value")) for f in group] for group in filters]
        return _dump(catalog.search_spirits(compiled))

    @tool
    def get_spirit_profile(name: str) -> str:
        """返回单只精灵完整档案（属性/可学池/合法血脉/是否首领）。"""
        return _dump(catalog.get_spirit_profile(name))

    @tool
    def get_skill_profile(name: str) -> str:
        """返回单条技能档案（系别/类别/威力/能耗/是否已实装）。"""
        return _dump(catalog.get_skill_profile(name))

    @tool
    def get_build_options(name: str, bloodline: str = "") -> str:
        """返回某精灵的合法构筑项（可学技能池 / 合法性格 / IV 范围）。"""
        return _dump(catalog.get_build_options(name, bloodline))

    @tool
    def validate_team(team: list[dict], items: list[str] | None = None) -> str:
        """组队硬闸：校验阵容（spirit/skills/bloodline/nature/iv），返回 {ok, errors:[{code,message,pick_index}]}。"""
        picks = [_to_team_pick(p) for p in team]
        tv = advisor_validate.validate_team(picks, items or [])
        return _dump({"ok": tv.ok, "errors": tv.errors})

    @tool
    def query_trajectory_evidence(kind: str, team_filter: list[str] | None = None) -> str:
        """查构筑级胜率证据（人机/自博弈分开）。kind ∈ human/selfplay；返回版本匹配且 replay_ok 的证据。"""
        return _dump(_query_trajectory(kind, team_filter=team_filter,
                                       battles_dir=battles_dir, runs_dir=runs_dir))

    @tool
    def analyze_team(team: list[dict]) -> str:
        """分析阵容（spirit/skills/bloodline/nature/iv）的攻防覆盖/速度分层/角色缺口（启发式）。"""
        return _dump(_analyze_team(_picks_to_roster(team)))

    @tool
    def simulate_matchups(team: list[dict], opponents: list[list[dict]], seeds: list[int]) -> str:
        """确定性贪心模拟：给定阵容 vs 若干对手，成对换边多 seed 胜率下限（非真实最优）。"""
        roster = _picks_to_roster(team)
        opp_rosters = [_picks_to_roster(o) for o in opponents]
        return _dump(_simulate_matchups(roster, opp_rosters, seeds=seeds))

    @tool
    def submit_team_advice(payload: dict) -> str:
        """提交结构化组队建议（终稿，必须调用）。payload 字段：
        rules_used{team_size,lives,source}, assumptions, data_digest,
        team[{spirit,skills,bloodline,nature,iv,role,rationale,evidence_ids}],
        synergy, strengths, weak_matchups, evidence{catalog,human,selfplay,simulation},
        uncertainty, alternatives。"""
        return "（终稿由终结钩子处理，不直接执行）"

    return [
        get_catalog_version, search_spirits, get_spirit_profile, get_skill_profile,
        get_build_options, validate_team, query_trajectory_evidence, analyze_team,
        simulate_matchups, submit_team_advice, final_answer,
    ]


def _degraded_answer(errors: list[dict]) -> str:
    """安全降级答案：修复一次仍失败 → 明确说明无法给出合法阵容，并列出失败 code。"""
    return _dump({
        "ok": False,
        "degraded": True,
        "message": "无法给出通过校验的阵容建议。",
        "failed_codes": [e.get("code") for e in errors],
    })


class TeamAdvisorAgent(ChatAgent):
    """组队顾问：结构化终结 + EvidenceGate + 关闭思维链外显。"""

    def __init__(self, settings, *, llm=None, max_llm_rounds: int = 6,
                 battles_dir=None, runs_dir=None):
        super().__init__(
            settings,
            llm=llm,
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            max_llm_rounds=max_llm_rounds,
            tools=_build_advisor_tools(battles_dir=battles_dir, runs_dir=runs_dir),
            terminal_tools={TERMINAL_TOOL, FINAL_ANSWER_TOOL},
            emit_thinking=False,
        )
        self._advice_failed = 0

    def chat(self, message, history=None, *, event_sink=None):
        """每次对话重置「修复一次」计数。"""
        self._advice_failed = 0
        return super().chat(message, history, event_sink=event_sink)

    def _handle_terminal(self, name: str, args: dict, call_id: str) -> tuple[str, bool]:
        """终结分流：final_answer（闲聊自由文本）与 submit_team_advice（组队，EvidenceGate 校验）。"""
        if name == FINAL_ANSWER_TOOL:
            return str(args.get("text", "")) or EMPTY_REPLY, True
        payload = args.get("payload", args)
        result = _submit_team_advice(payload)
        if result["ok"]:
            self._advice_failed = 0
            return _dump(result["advice"].model_dump()), True
        self._advice_failed += 1
        if self._advice_failed >= 2:
            return _degraded_answer(result["errors"]), True
        return _dump({"ok": False, "errors": result["errors"]}), False

"""顾问 Agent（M3）：复用 ChatAgent 循环，换顾问 prompt + 工具集 + 结构化终结。

- 工具集 = M1 catalog/validate + M2 query_trajectory_evidence + analyze_team + simulate_matchups。
- 终结工具 = `submit_team_advice`：EvidenceGate 校验，失败让模型修复一次，再失败安全降级。
- 关闭思维链外显（emit_thinking=False），只展示「查了哪些证据 / 过了哪些校验」。
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from environment.battle_config import build_battle_rules
from environment.datafingerprint import data_digest
from environment.dataset import DataSource
from environment.teambuilder import TeamPick, build_roster

from roco_pvp_agent.advisor import catalog
from roco_pvp_agent.advisor import validate as advisor_validate
from roco_pvp_agent.advisor.advice import submit_team_advice as _submit_team_advice
from roco_pvp_agent.advisor.analysis import analyze_team as _analyze_team
from roco_pvp_agent.advisor.catalog import SpiritFilter
from roco_pvp_agent.advisor.prompt import ADVISOR_SYSTEM_PROMPT
from roco_pvp_agent.advisor.scope import route as _route
from roco_pvp_agent.advisor.simulate import simulate_matchups as _simulate_matchups
from roco_pvp_agent.advisor.skills import retrieve_team_skill as _retrieve_team_skill
from roco_pvp_agent.advisor.tool_schemas import (
    AnalyzeTeamArgs,
    GetBuildOptionsArgs,
    NameArgs,
    NoArgs,
    QueryGlobalMemArgs,
    QueryLocalMemArgs,
    QueryTrajectoryEvidenceArgs,
    RetrieveTeamSkillArgs,
    SearchSpiritsArgs,
    SimulateMatchupsArgs,
    SpiritFilterInput,
    SubmitTeamAdviceArgs,
    TeamPickInput,
    ValidateTeamArgs,
)
from roco_pvp_agent.advisor.trajectory import query_trajectory_evidence as _query_trajectory
from roco_pvp_agent.agent import ChatAgent, ChatReply
from roco_pvp_agent.sandbox import (
    SandboxPythonQueryTool,
    SandboxQueryService,
    SandboxSetup,
    build_sandbox_setup,
)
from roco_pvp_agent.tooling import (
    ToolConcurrency,
    ToolEntry,
    ToolExposure,
    ToolOutcome,
    ToolRegistry,
    ToolVisibility,
    enable_deferred_loading,
)
from roco_pvp_agent.tools import final_answer


def _dump(obj) -> str:
    """对象 → JSON 字符串（工具返回值统一 JSON，LLM 好读）。None → "null"。"""
    if obj is None:
        return "null"
    return json.dumps(obj, ensure_ascii=False)


def _to_team_pick(p: TeamPickInput | dict) -> TeamPick:
    """pick dict → TeamPick（缺字段给默认，让 validate_team 报结构化错误而非抛）。"""
    if isinstance(p, TeamPickInput):
        p = p.model_dump()
    return TeamPick(
        spirit=p.get("spirit", ""),
        skills=list(p.get("skills") or []),
        bloodline=p.get("bloodline", ""),
        nature=p.get("nature", "坦率"),
        iv=dict(p.get("iv") or {}),
    )


def _picks_to_roster(picks: list[TeamPickInput | dict],
                     source: DataSource = DataSource.VALID) -> list[dict]:
    """pick dicts → roster（build_roster；非法阵容由 Dispatcher 转成工具错误）。"""
    rules = build_battle_rules(team_size=len(picks))
    return build_roster([_to_team_pick(p) for p in picks], source, rules)


def _dump_mem(entry: dict) -> dict:
    """记忆条目 → 给 LLM 的只读视图（去掉 provenance 内部字段，加"非当前局面"标注位）。

    只暴露：entry_id / Q / n_used / n_wins / strategy_text（GlobalMem）
    或 situation_text / experience_text / action（局部记忆）——全是可读证据，不含隐藏对局信息。
    """
    out = {"entry_id": entry.get("entry_id"), "Q": entry.get("Q", 0.0)}
    if "strategy_text" in entry:
        out["n_used"] = entry.get("n_used", 0)
        out["n_wins"] = entry.get("n_wins", 0)
        out["strategy_text"] = entry.get("strategy_text", "")
    else:
        out["situation_text"] = entry.get("situation_text", "")
        out["experience_text"] = entry.get("experience_text", "")
        out["action"] = entry.get("action", {})
    out["is_history_not_fact"] = True     # 记忆是历史经验，非当前局面事实
    return out


def _build_advisor_registry(*, battles_dir=None, runs_dir=None,
                            memory_dir: str | None = None,
                            globalmem_dir: str | None = None,
                            sandbox_service: SandboxQueryService | None = None) -> ToolRegistry:
    """顾问实例级注册表（LangChain @tool + Harness 策略）。

    `memory_dir` / `globalmem_dir`：进化沉淀的记忆库目录。缺省 None → 对应检索工具返回
    "未启用"，不报错（顾问在没跑过进化的环境里仍可用）。
    """
    registry = ToolRegistry()

    @tool(args_schema=NoArgs)
    def get_catalog_version() -> str:
        """返回当前数据/规则版本指纹与数据量（回答引用的唯一锚点）。"""
        return _dump(catalog.get_catalog_version())

    @tool(args_schema=SearchSpiritsArgs)
    def search_spirits(filters: list[list[SpiritFilterInput]]) -> str:
        """白名单 DSL 检索精灵。filters 外层 OR、内层 AND；每原子 {field, op, value}。
        field ∈ name/type/trait/learnable_skill/family/is_boss/number；op ∈ eq/in/contains。"""
        compiled = [[SpiritFilter(field=f.field, op=f.op, value=f.value)
                     for f in group] for group in filters]
        return _dump(catalog.search_spirits(compiled))

    @tool(args_schema=NameArgs)
    def get_spirit_profile(name: str) -> str:
        """返回单只精灵完整档案（属性/可学池/合法血脉/是否首领）。"""
        return _dump(catalog.get_spirit_profile(name))

    @tool(args_schema=NameArgs)
    def get_skill_profile(name: str) -> str:
        """返回单条技能档案（系别/类别/威力/能耗/是否已实装）。"""
        return _dump(catalog.get_skill_profile(name))

    @tool(args_schema=GetBuildOptionsArgs)
    def get_build_options(name: str, bloodline: str = "") -> str:
        """返回某精灵的合法构筑项（可学技能池 / 合法性格 / IV 范围）。"""
        return _dump(catalog.get_build_options(name, bloodline))

    @tool(args_schema=ValidateTeamArgs)
    def validate_team(team: list[TeamPickInput], items: list[str] | None = None) -> str:
        """组队硬闸：校验阵容（spirit/skills/bloodline/nature/iv），返回 {ok, errors:[{code,message,pick_index}]}。"""
        picks = [_to_team_pick(p) for p in team]
        tv = advisor_validate.validate_team(picks, items or [])
        return _dump({"ok": tv.ok, "errors": tv.errors})

    @tool(args_schema=QueryTrajectoryEvidenceArgs)
    def query_trajectory_evidence(kind: Literal["human", "selfplay"],
                                  team_filter: list[str] | None = None) -> str:
        """查构筑级胜率证据（人机/自博弈分开）。kind ∈ human/selfplay；返回版本匹配且 replay_ok 的证据。"""
        return _dump(_query_trajectory(kind, team_filter=team_filter,
                                       battles_dir=battles_dir, runs_dir=runs_dir))

    @tool(args_schema=AnalyzeTeamArgs)
    def analyze_team(team: list[TeamPickInput]) -> str:
        """分析阵容（spirit/skills/bloodline/nature/iv）的攻防覆盖/速度分层/角色缺口（启发式）。"""
        return _dump(_analyze_team(_picks_to_roster(team)))

    @tool(args_schema=SimulateMatchupsArgs)
    def simulate_matchups(team: list[TeamPickInput],
                          opponents: list[list[TeamPickInput]], seeds: list[int]) -> str:
        """确定性贪心模拟：给定阵容 vs 若干对手，成对换边多 seed 胜率下限（非真实最优）。"""
        roster = _picks_to_roster(team)
        opp_rosters = [_picks_to_roster(o) for o in opponents]
        return _dump(_simulate_matchups(roster, opp_rosters, seeds=seeds))

    @tool(args_schema=RetrieveTeamSkillArgs)
    def retrieve_team_skill(query: str) -> str:
        """检索已激活的组队 Skill（触发关键词命中，最多 3 个，含版本/工具白名单/校验项）。"""
        return _dump(_retrieve_team_skill(query, registry=registry))

    @tool(args_schema=QueryGlobalMemArgs)
    def query_global_mem(my_team: list[TeamPickInput], foe_team: list[TeamPickInput], *,
                         team_size: Literal[3, 6] = 3, lives: int = 2) -> str:
        """查历史全局对战经验（GlobalMem）：给定我方/对手阵容（{spirit,skills,...}），返回最相似的
        已沉淀经验（entry_id/Q/strategy_text）。**记忆是历史经验，非当前局面事实**，只作佐证，
        不覆盖数据库与引擎计算。未配置 globalmem_dir → 返回"未启用"。"""
        if globalmem_dir is None:
            return "未启用（未配置 globalmem_dir）"
        from roco_pvp_agent.battle.evolution.globalmem import (
            GlobalMemStore,
            MatchupQuery,
            make_global_entry_id,
            matchup_key,
            search_global_mem,
        )
        store = GlobalMemStore(globalmem_dir)
        roster_a = _picks_to_roster(my_team)
        roster_b = _picks_to_roster(foe_team)
        key = matchup_key(roster_a, roster_b, team_size=team_size, lives=lives)
        hits = search_global_mem(
            store, MatchupQuery(matchup_key=key, data_digest=data_digest()),
            top_k=3)
        return _dump({"matchup_key": key, "hits": [_dump_mem(h) for h in hits]})

    @tool(args_schema=QueryLocalMemArgs)
    def query_local_mem(situation_key: str) -> str:
        """查历史局部局面记忆：给定 situation_key（形如 my2/foe2/迪莫/水蓝蓝/high/high/0/early/2/2），
        返回相似局面的历史经验（entry_id/Q/experience_text）。**记忆是历史经验，非当前局面事实**。
        未配置 memory_dir → 返回"未启用"。"""
        if memory_dir is None:
            return "未启用（未配置 memory_dir）"
        from roco_pvp_agent.battle.evolution.memory import (
            MemoryQuery,
            MemoryStore,
            two_phase_search,
        )
        store = MemoryStore(memory_dir)
        hits = two_phase_search(
            store, MemoryQuery(situation_key=situation_key, data_digest=data_digest()),
            k1=10, k2=3)
        return _dump({"hits": [_dump_mem(h) for h in hits]})

    @tool(args_schema=SubmitTeamAdviceArgs)
    def submit_team_advice(payload: TeamAdviceSchema) -> ToolOutcome:
        """提交结构化组队建议（终稿，必须调用）。payload 字段：
        rules_used{team_size,lives,source}, assumptions, data_digest,
        team[{spirit,skills,bloodline,nature,iv,role,rationale,evidence_ids}],
        synergy, strengths, weak_matchups, evidence{catalog,human,selfplay,simulation},
        uncertainty, alternatives。"""
        result = _submit_team_advice(payload.model_dump())
        if result["ok"]:
            return ToolOutcome(
                content=_dump(result["advice"].model_dump()),
                details={"validation": {"ok": True, "errors": []}},
            )
        errors = list(result["errors"])
        return ToolOutcome(
            content="阵容建议未通过结构化校验，请根据 errors 修复后重新提交",
            ok=False,
            error_code="advice_validation_failed",
            retryable=True,
            details={"errors": errors},
        )

    def degrade_advice(outcome: ToolOutcome) -> ToolOutcome:
        """修复预算耗尽后，把最后一次校验错误转换成可呈现的安全降级终稿。"""

        errors = list(outcome.details.get("errors") or [])
        return ToolOutcome(
            content=_degraded_answer(errors),
            terminal_override=True,
            details={"validation": {"ok": False, "errors": errors}},
        )

    for registered_tool, audit_tag, exposure, directory_description in (
        (get_catalog_version, "catalog", ToolExposure.IMMEDIATE, ""),
        (search_spirits, "catalog", ToolExposure.IMMEDIATE, ""),
        (get_spirit_profile, "catalog", ToolExposure.IMMEDIATE, ""),
        (get_skill_profile, "catalog", ToolExposure.IMMEDIATE, ""),
        (get_build_options, "catalog", ToolExposure.IMMEDIATE, ""),
        (validate_team, "validation", ToolExposure.IMMEDIATE, ""),
        (
            query_trajectory_evidence,
            "evidence",
            ToolExposure.DEFERRED,
            "查询人机或自博弈轨迹中的版本匹配胜率证据。",
        ),
        (
            analyze_team,
            "analysis",
            ToolExposure.DEFERRED,
            "分析候选阵容的攻防覆盖、速度分层和角色缺口。",
        ),
        (
            simulate_matchups,
            "simulation",
            ToolExposure.DEFERRED,
            "用确定性贪心策略模拟候选阵容与指定对手的对局。",
        ),
        (
            retrieve_team_skill,
            "skill",
            ToolExposure.DEFERRED,
            "检索已激活的组队流程 Skill。",
        ),
        (
            query_global_mem,
            "memory",
            ToolExposure.DEFERRED,
            "查询双方阵容对应的历史全局对战经验。",
        ),
        (
            query_local_mem,
            "memory",
            ToolExposure.DEFERRED,
            "查询指定 situation_key 对应的历史局部局面经验。",
        ),
    ):
        registry.register(ToolEntry(
            tool=registered_tool,
            audit_tag=audit_tag,
            exposure=exposure,
            directory_description=directory_description,
            concurrency=ToolConcurrency.CONCURRENT_SAFE,
        ))
    if sandbox_service is not None:
        registry.register(ToolEntry(
            tool=SandboxPythonQueryTool(sandbox_service),
            exposure=ToolExposure.DEFERRED,
            concurrency=ToolConcurrency.SERIAL,
            retry_limit=1,
            timeout_seconds=10.0,
            max_output_chars=20_000,
            audit_tag="sandbox_query",
            directory_description=(
                "仅在结构化图鉴工具无法表达时，对已授权本地游戏数据做复杂统计或跨表关联。"
            ),
            sensitive_arguments=frozenset({"code"}),
            cooperative_cancellation=True,
        ))
    registry.register(ToolEntry(
        tool=submit_team_advice,
        terminal_on_success=True,
        retry_limit=1,
        audit_tag="terminal",
        on_retry_exhausted=degrade_advice,
    ))
    registry.register(ToolEntry(
        tool=final_answer,
        terminal_on_success=True,
        audit_tag="terminal",
    ))
    enable_deferred_loading(registry)
    return registry


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

    def __init__(self, settings, *, llm=None, max_llm_rounds: int = 100,
                 battles_dir=None, runs_dir=None,
                 memory_dir: str | None = None,
                 globalmem_dir: str | None = None,
                 sandbox_setup: SandboxSetup | None = None):
        # 记忆目录：显式传参优先；缺省回退 settings 里的进化沉淀目录（跑过 evolve 就能用）。
        memory_dir = memory_dir or getattr(settings, "memory_dir", None)
        globalmem_dir = globalmem_dir or getattr(settings, "globalmem_dir", None)
        sandbox_setup = sandbox_setup or build_sandbox_setup(settings)
        self._sandbox_health = sandbox_setup.health
        super().__init__(
            settings,
            llm=llm,
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            max_llm_rounds=max_llm_rounds,
            registry=_build_advisor_registry(
                battles_dir=battles_dir,
                runs_dir=runs_dir,
                memory_dir=memory_dir,
                globalmem_dir=globalmem_dir,
                sandbox_service=sandbox_setup.service,
            ),
            emit_thinking=False,
            max_total_seconds=555.0,       # < 1 分钟兜底：超时强制终结
        )

    @property
    def sandbox_health(self):
        """供健康接口读取的脱敏能力状态。"""

        return self._sandbox_health

    def chat(self, message, history=None, *, event_sink=None,
             tool_visibility: ToolVisibility | None = None,
             cancel_event=None):
        """先过 ScopeGate；越界/注入/模糊/欢迎走固定模板，不进入 LLM。"""
        if tool_visibility is not None and tool_visibility.registry is not self._registry:
            raise ValueError("tool_visibility 不属于当前 Agent 的 ToolRegistry")
        target, text = _route(message)
        if target != "agent":
            return self._scoped_reply(
                message, text, history, event_sink, tool_visibility)
        return super().chat(
            message,
            history,
            event_sink=event_sink,
            tool_visibility=tool_visibility,
            cancel_event=cancel_event,
        )

    def _scoped_reply(self, message: str, text: str, history, event_sink,
                      tool_visibility: ToolVisibility | None = None) -> ChatReply:
        """固定模板回复（不进 LLM）：构造 ChatReply 并发射 reply/done 事件。"""
        history = list(history or []) + [HumanMessage(content=message), AIMessage(content=text)]
        loaded_tools = tool_visibility.loaded_names() if tool_visibility is not None else ()
        reply_obj = ChatReply(
            reply=text,
            history=history,
            offline=False,
            rounds=0,
            loaded_tools=loaded_tools,
        )
        self._emit_reply_events(event_sink, reply_obj)
        return reply_obj

    def _handle_exhausted(self, reason: str, tool_log: list[dict], thinking: list[str],
                          event_sink) -> str:
        """轮次耗尽 / 超时 / API 异常时返回已核实结果，而非一句空话。"""
        names = [t["name"] for t in tool_log]
        when = {
            "timeout": "约 555 秒的对话预算内",
            "llm_error": "模型服务发生异常后",
            "rounds": "100 个模型轮次内",
        }.get(reason, "当前预算内")
        # 最多带回 3 条、每条 800 字符的已核实工具结果；既有实际信息，又避免降级回复失控膨胀。
        partial_results = [
            {"tool": item["name"], "result_preview": str(item["result"])[:800]}
            for item in tool_log[-3:]
        ]
        body = {
            "ok": False,
            "degraded": True,
            "reason": reason,
            "message": f"{when}未能生成完整终稿；先返回本轮已经核实的阶段结果。",
            "tools_queried": names,
            "partial_results": partial_results,
            "hint": (
                "已有结果可作为本轮的阶段性回答；如需完整配队，请补充队伍规模、已有精灵或目标对手后重试。"
                if partial_results else
                "本轮未取得可核实数据，请检查模型服务后重试；也可补充队伍规模、已有精灵或目标对手。"
            ),
        }
        return _dump(body)

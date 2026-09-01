"""CLI 入口：python -m roco_pvp_agent。

M1：-q/--query 单发 + 交互 REPL；--debug 打印思考与工具过程；--serve 占位（M3 实现）。
"""

import argparse
import time
from contextlib import contextmanager
from typing import Callable

from rich.console import Console

from .agent import ChatAgent, ChatReply
from .advisor.agent import TeamAdvisorAgent
from .config import get_settings

console = Console()

EXIT_WORDS = {"exit", "quit", "q"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="roco_pvp_agent", description="Roco PVP Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__import__('roco_pvp_agent').__version__}")
    parser.add_argument("-q", "--query", help="单发模式：直接提问后退出")
    parser.add_argument("--debug", action="store_true", help="打印思考过程与工具调用详情")
    parser.add_argument("--serve", action="store_true", help="启动 Web UI（M3 实现）")

    sub = parser.add_subparsers(dest="command", help="子命令")
    sp = sub.add_parser("selfplay", help="双 LLM 自博弈（E6 测试期假LLM）+ 轨迹落盘 + 重放自检")
    sp.add_argument("--games", type=int, default=2, help="对局数（默认 2；逐局 seed +1）")
    sp.add_argument("--seed", type=int, default=7, help="首局引擎 seed（默认 7）")
    sp.add_argument("--out", type=str, default="runs", help="轨迹落盘目录（默认 runs/）")
    sp.add_argument("--a", choices=("fake_llm", "random", "llm"), default="fake_llm",
                    help="a 方玩家：llm=真实 LLM（无 key 自动降级假LLM）；fake_llm/random=测试期策略")
    sp.add_argument("--b", choices=("fake_llm", "random", "llm"), default="fake_llm",
                    help="b 方玩家（同上）")
    sp.add_argument("--team-size", type=int, default=3, help="每方精灵数（管理员接口 3–6，默认 3V3）")
    sp.add_argument("--lives", type=int, default=2, help="每方命数（1..team_size−1，默认 2）")
    sp.add_argument("--max-turns", type=int, default=None, help="覆盖 rules.max_turns")
    sp.add_argument("--verbose", action="store_true", help="逐回合打印双方提交类型")
    sp.set_defaults(func=_run_selfplay_cli)

    ep = sub.add_parser("evolve", help="自博弈进化（R 线）：eval 评测 / reflect 轨迹分析")
    epsub = ep.add_subparsers(dest="evolve_cmd", help="evolve 子命令")
    epe = epsub.add_parser("eval", help="配对评测：subject vs opponent 强弱（双向先后手 + 95%CI）")
    epe.add_argument("--bench", choices=("d_sel", "d_test"), default="d_sel", help="实例集（默认 d_sel）")
    epe.add_argument("--a", choices=("fake_llm", "random", "llm"), default="fake_llm", help="subject 方玩家")
    epe.add_argument("--b", choices=("fake_llm", "random", "llm"), default="random", help="opponent 方玩家")
    epe.add_argument("--games", type=int, default=8, help="每实例 seed 数（默认 8）")
    epe.add_argument("--seed", type=int, default=7, help="seed 平移（默认 7）")
    epe.add_argument("--progress", action="store_true", help="显示进度条（默认关闭）")
    epe.set_defaults(func=_run_evolve_eval_cli)
    epr = epsub.add_parser("reflect", help="轨迹重放分析 + 可选提取记忆条目")
    epr.add_argument("--traj", required=True, help="轨迹 JSON 路径")
    epr.add_argument("--out", default=None, help="提取记忆条目写入该目录（MemoryStore）")
    epr.set_defaults(func=_run_evolve_reflect_cli)
    eph = epsub.add_parser("health", help="记忆健康度：Q 分布 / 命中率 / Forgetting Rate")
    eph.add_argument("--memory", required=True, help="MemoryStore 目录")
    eph.set_defaults(func=_run_evolve_health_cli)
    epc = epsub.add_parser("credit", help="信度分配：定位关键回合（校准偏差/价值落差/反事实）")
    epc.add_argument("--traj", required=True, help="轨迹 JSON 路径")
    epc.add_argument("--out", default=None, help="关键回合卡片写入该 JSONL 文件")
    epc.add_argument("--repeat", action="store_true", help="跑两遍验证反事实确定性")
    epc.add_argument("--m", type=int, default=24, help="反事实回放场次（默认 24）")
    epc.set_defaults(func=_run_evolve_credit_cli)
    eps = epsub.add_parser("step", help="单步进化：rollout → credit → reflect → edit")
    eps.add_argument("--seed", type=int, default=7, help="对局 seed（默认 7）")
    eps.add_argument("--out", type=str, default="artifacts", help="产物目录（默认 artifacts/）")
    eps.add_argument("--a", choices=("fake_llm", "random", "llm"), default="fake_llm", help="a 方玩家")
    eps.add_argument("--b", choices=("fake_llm", "random", "llm"), default="random", help="b 方玩家")
    eps.add_argument("--m", type=int, default=24, help="反事实回放场次（默认 24）")
    eps.set_defaults(func=_run_evolve_step_cli)
    epst = epsub.add_parser("steps", help="R4 多步进化：池 + 两级门 + 剥削者 + 回归门")
    epst.add_argument("--n", type=int, default=4, help="step 数（默认 4）")
    epst.add_argument("--seed", type=int, default=7, help="seed（默认 7）")
    epst.add_argument("--out", type=str, default="artifacts", help="产物目录（默认 artifacts/）")
    epst.add_argument("--m", type=int, default=24, help="反事实回放场次（默认 24）")
    epst.add_argument("--llm", action="store_true", help="真实 LLM 玩家（无 key 自动降级 PlaybookPlayer）")
    epst.add_argument("--team-size", type=int, default=3, help="每方精灵数")
    epst.add_argument("--lives", type=int, default=2, help="每方命数")
    epst.add_argument("--health", action="store_true", help="打印健康度看板")
    epst.add_argument("--instances", type=int, default=None,
                      help="只用前 N 个 d_sel 实例（默认全部 60；缩小 = 省钱/省时的主旋钮）")
    epst.add_argument("--seeds-per-instance", type=int, default=None,
                      help="每实例用几个 seed（默认全部 8）")
    epst.add_argument("--minibatch-seeds", type=int, default=2,
                      help="D_tr 训练 minibatch 的 seed 数（默认 2）")
    epst.add_argument("--memory-dir", type=str, default=None,
                      help="记忆库目录；给出则启用记忆注入 + 采纳判定/Q 更新（默认关闭）")
    epst.add_argument("--progress", action="store_true", help="显示进度条（默认关闭）")
    epst.add_argument("--resume", action="store_true",
                      help="从 <out>/pool.json 续跑（实例数/seed 参数须与上次一致）")
    epst.set_defaults(func=_run_evolve_steps_cli)
    epe = epsub.add_parser("epoch", help="R5 epoch 调度：慢更新 + D_test 汇报")
    epe.add_argument("--n", type=int, default=8, help="epoch 数（默认 8）")
    epe.add_argument("--e", type=int, default=8, dest="E", help="每 epoch 的 step 数（默认 8）")
    epe.add_argument("--seed", type=int, default=7, help="seed（默认 7）")
    epe.add_argument("--out", type=str, default="artifacts", help="产物目录（默认 artifacts/）")
    epe.add_argument("--m", type=int, default=24, help="反事实回放场次（默认 24）")
    epe.add_argument("--llm", action="store_true", help="真实 LLM 玩家")
    epe.add_argument("--no-slow-update", action="store_true", help="关慢更新（A/B 消融）")
    epe.add_argument("--team-size", type=int, default=3, help="每方精灵数")
    epe.add_argument("--lives", type=int, default=2, help="每方命数")
    epe.add_argument("--instances", type=int, default=None,
                     help="只用前 N 个 d_sel 实例（默认全部 60）")
    epe.add_argument("--dtest-instances", type=int, default=None,
                     help="只用前 N 个 d_test 实例（默认全部 16）")
    epe.add_argument("--seeds-per-instance", type=int, default=None,
                     help="每实例用几个 seed（默认全部 8）")
    epe.add_argument("--minibatch-seeds", type=int, default=2,
                     help="D_tr 训练 minibatch 的 seed 数（默认 2）")
    epe.add_argument("--memory-dir", type=str, default=None,
                     help="记忆库目录；给出则启用记忆注入 + 采纳判定/Q 更新（默认关闭）")
    epe.add_argument("--progress", action="store_true", help="显示进度条（默认关闭）")
    epe.add_argument("--resume", action="store_true",
                     help="从 <out>/pool.json 续跑（实例数/seed 参数须与上次一致）")
    epe.set_defaults(func=_run_evolve_epoch_cli)

    epb = epsub.add_parser("battles", help="G5 GlobalMem 闭环：战斗→双视角复盘→双库落地→Q 更新")
    epb.add_argument("--n", type=int, default=4, help="对局数（默认 4）")
    epb.add_argument("--seed", type=int, default=7, help="seed（默认 7）")
    epb.add_argument("--out", type=str, default="artifacts/gm", help="产物目录")
    epb.add_argument("--instances", type=int, default=None,
                     help="只用前 N 个 d_sel 实例作阵容池（默认全部 60）")
    epb.add_argument("--team-size", type=int, default=3, help="每方精灵数")
    epb.add_argument("--lives", type=int, default=2, help="每方命数")
    epb.add_argument("--llm", action="store_true",
                     help="真实 LLM 对战 + 分析（无 key 自动降级离线确定性，且跳过分析）")
    epb.add_argument("--globalmem-dir", type=str, default=None,
                     help="GlobalMem 库目录；给出才启用全局经验注入/复盘/Q 更新")
    epb.add_argument("--memory-dir", type=str, default=None,
                     help="局部记忆库目录；给出才启用逐回合注入/提取/采纳判定")
    epb.add_argument("--ab-every", type=int, default=0,
                     help="每 N 局做一次 A/B 度量（开/关 GlobalMem 对比）；0=关闭")
    epb.add_argument("--ab-instances", type=int, default=None,
                     help="A/B 用前 N 个实例（默认取训练池前 3 个）")
    epb.add_argument("--ab-seeds", type=int, default=1, help="A/B 每实例 seed 数（默认 1）")
    epb.add_argument("--fake-analyst", action="store_true",
                     help="用确定性占位分析师（零 LLM）离线验证完整闭环；产出非真经验")
    epb.add_argument("--progress", action="store_true", help="显示进度条（默认关闭）")
    epb.set_defaults(func=_run_evolve_battles_cli)

    args = parser.parse_args()

    if args.command == "selfplay":
        return args.func(args)
    if args.command == "evolve":
        func = getattr(args, "func", None)
        if func is None:
            ep.print_help()
            return 1
        return func(args)
    if args.serve:
        try:
            from ui.__main__ import run_ui
        except ImportError:
            console.print("[red]Web UI 依赖未安装，请先运行：uv sync --all-extras[/red]")
            return 1
        return run_ui()

    agent = TeamAdvisorAgent(get_settings())
    if args.query:
        return _run_once(agent, args.query, args.debug)
    return _repl(agent, args.debug)


def _run_selfplay_cli(args) -> int:
    """selfplay 子命令：逐局自博弈 + 落盘 + 重放自检，任一局失配 → 返回 1（可进 CI）。"""
    from .battle.selfplay import run_selfplay

    for i in range(1, args.games + 1):
        seed = args.seed + i - 1
        battle_id = f"selfplay-{seed}-{i}"
        out = run_selfplay(seed=seed, team_size=args.team_size, lives=args.lives,
                           max_turns=args.max_turns, a_kind=args.a, b_kind=args.b,
                           out_dir=args.out, battle_id=battle_id)
        mark = "✅" if out["replay_ok"] else "❌"
        console.print(
            f"game#{i} seed={seed} winner={out['winner'] or '平局'} "
            f"turns={out['turn_count']} rng_calls={out['rng_calls']} "
            f"replay={mark} {out['record_path'] or '(未落盘)'}"
        )
        if args.verbose:
            for t in out["record"]["turns"]:
                da = t["decision_a"]["action"].get("type", "?")
                db = t["decision_b"]["action"].get("type", "?")
                console.print(f"  T{t['turn']:>3}  a:{da:<8} b:{db}")
        if not out["replay_ok"]:
            return 1
    return 0


def _run_evolve_eval_cli(args) -> int:
    """evolve eval：配对评测（LLM/fake/random vs 对手）。无 key 时 llm 自动降级假LLM。"""
    from .battle.selfplay import build_player
    from .battle.evolution.bench import build_instances, paired_eval

    settings = get_settings()
    instances = build_instances(args.bench)
    with _evolve_progress(args.progress):
        result = paired_eval(
            lambda side, seed: build_player(side, args.a, seed=seed, settings=settings),
            lambda side, seed: build_player(side, args.b, seed=seed, settings=settings),
            instances,
            seeds=args.games,
            seed_offset=args.seed,
        )
    console.print(f"[bold]evolve eval[/bold] bench={args.bench}  "
                  f"subject={result['players']['subject']} vs opponent={result['players']['opponent']}")
    console.print(f"winrate={result['winrate']:.3f}  "
                  f"95%CI=[{result['ci95_low']:.3f}, {result['ci95_high']:.3f}]  "
                  f"n_games={result['n_games']}  n_instances={result['n_instances']}")
    for row in result["instances"][:10]:
        lo, hi = row["ci95"]
        console.print(f"  {row['name']:<22} {row['scenario']:<15} "
                      f"winrate={row['winrate']:.3f} [{lo:.3f},{hi:.3f}] n={row['n_games']}")
    if len(result["instances"]) > 10:
        console.print(f"  … 其余 {len(result['instances']) - 10} 个实例省略")
    return 0


def _run_evolve_reflect_cli(args) -> int:
    """evolve reflect：轨迹重放分析（replay_ok + 逐回合 situation_key / v_heuristic）；
    给 --out 则额外提取记忆条目写入 MemoryStore（复用同一 analysis，不二次重放）。"""
    import json

    from .battle.evolution.analysis import analyze_record
    from .battle.evolution.reflect import store_experiences

    with open(args.traj, encoding="utf-8") as f:
        record = json.load(f)
    analysis = analyze_record(record)
    console.print(f"[bold]evolve reflect[/bold] traj={args.traj}")
    console.print(f"winner={analysis.winner or '平局'}  turns={analysis.turn_count}  "
                  f"replay_ok={'✅' if analysis.replay_ok else '❌'}")
    for t in analysis.turns:
        console.print(
            f"  T{t.turn:>3}  a:{t.situation_keys['a']}  v_a={t.v_before['a']:+.3f}->{t.v_after['a']:+.3f}"
        )
    if args.out:
        ids = store_experiences(record, args.out, analysis=analysis)
        console.print(f"记忆条目写入 {len(ids)} 条 → {args.out}")
    return 0 if analysis.replay_ok else 1


def _run_evolve_health_cli(args) -> int:
    """evolve health：读一个 MemoryStore 目录 → 记忆健康度报告。"""
    from .battle.evolution.health import memory_health

    h = memory_health(args.memory)
    q = h["q_distribution"]
    console.print(f"[bold]evolve health[/bold] dir={h['store_dir']}")
    console.print(f"count={h['count']}  Q[min={q['min']}, max={q['max']}, mean={q['mean']}]")
    console.print(f"n_used_total={h['n_used_total']}  n_adopted_total={h['n_adopted_total']}  "
                  f"source_type_counts={h['source_type_counts']}")
    console.print(f"forgetting_rate={h['forgetting_rate']}  retrieval_hits={h['retrieval_hits']}")
    return 0


def _run_evolve_credit_cli(args) -> int:
    """evolve credit：信度分配，定位关键回合（校准偏差 + 价值落差 + 反事实确认）。"""
    import json

    from .battle.evolution.analysis import analyze_record
    from .battle.evolution.credit import mine_critical_turns

    with open(args.traj, encoding="utf-8") as f:
        record = json.load(f)
    analysis = analyze_record(record)
    cards = mine_critical_turns(analysis, record, M=args.m)
    console.print(f"[bold]evolve credit[/bold] traj={args.traj}  cards={len(cards)}")
    for c in cards[:10]:
        console.print(f"  T{c['turn_no']:>3} {c['side']} delta={c['delta_winrate']:+.3f} "
                      f"signals={','.join(c['signals']) or '-'}")
    if len(cards) > 10:
        console.print(f"  … 其余 {len(cards) - 10} 张卡片省略")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            for c in cards:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        console.print(f"卡片写入 {len(cards)} 条 → {args.out}")
    if args.repeat:
        cards2 = mine_critical_turns(analysis, record, M=args.m)
        same = json.dumps(cards, sort_keys=True) == json.dumps(cards2, sort_keys=True)
        console.print(f"确定性复现：{'✅ 逐位一致' if same else '❌ 不一致'}")
        return 0 if (analysis.replay_ok and same) else 1
    return 0 if analysis.replay_ok else 1


def _run_evolve_step_cli(args) -> int:
    """evolve step：单步进化（rollout → credit → reflect → edit），产 edit_apply_report。"""
    from .battle.evolution.run import run_step

    out = run_step(seed=args.seed, out_dir=args.out, a_kind=args.a, b_kind=args.b, M=args.m)
    console.print(f"[bold]evolve step[/bold] seed={args.seed} battle={out['battle_id']} "
                  f"turns={out['turn_count']} cards={out['cards']}")
    console.print(f"candidates={len(out['candidates'])}  applied={out['edits_applied']}  "
                  f"rejected={out['edits_rejected']}")
    console.print(f"playbook {out['playbook_before']} → {out['playbook_after']}")
    console.print(f"reflection: {out['reflection_diagnostics']}")
    for r in out["reports"]:
        console.print(f"  [{r['status']}] {r.get('module_key')} {r.get('op')}  {r.get('reason')}")
    console.print(f"report → {out['report_path']}")
    return 0


@contextmanager
def _evolve_progress(enabled: bool):
    """`--progress`：阶段级确定进度条 + 累计局数/速率/已用时。

    每次 `paired_eval` 是一个「阶段」（其总局数开跑前可精确算出）；阶段边界重置进度条，
    累计计数与速率跨阶段保留。未启用 → no-op（输出与原来完全一致，可安全进管道/CI）。
    """
    if not enabled:
        yield
        return
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    from .battle.evolution.bench import progress_sink

    state = {"cum": 0, "phase": 0}
    start = time.monotonic()
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
                  TextColumn("{task.completed}/{task.total} 局"), TimeElapsedColumn(),
                  console=console, transient=True) as prog:
        task = prog.add_task("评测中", total=1)

        def sink(ev: dict) -> None:
            total, done = ev.get("total", 1), ev.get("done", 0)
            if done == 0:                       # 新阶段：结算上一阶段进累计，重置条
                state["cum"] += state["phase"]
                state["phase"] = 0
                prog.reset(task, total=max(1, total))
            state["phase"] = done
            cum = state["cum"] + done
            mins = (time.monotonic() - start) / 60
            rate = f"{cum / mins:.1f}" if mins > 0.01 else "—"
            prog.update(task, completed=done, description=f"累计 {cum} 局 · {rate} 局/分")

        with progress_sink(sink):
            yield


def _limited_instances(kind: str, limit, team_size: int):
    if limit is None:
        return None
    from .battle.evolution.bench import build_instances
    insts = build_instances(kind, team_size=team_size)
    if limit < 1:
        raise ValueError(f"实例数须 ≥1，实际 {limit}")
    return insts[:limit]


def _resume_pool(out_dir: str, resume: bool, instances=None):
    """`--resume`：从 `out_dir/pool.json` 载入既有池续跑；否则 None（新建池）。

    - `--resume` 但文件不存在 → 提示后按新建走（首次跑加 --resume 不该报错）；
    - **不** resume 且 pool.json 已存在 → 明确警告「将覆盖」，避免误以为在续跑
      （历史踩坑：同目录重跑会把池重置成初始手册，而审计日志仍在追加）；
    - 续跑时实例空间必须与上次完全一致（池的分数向量按实例名索引）——不一致时
      在这里给**可操作**的提示，而不是让 run_steps 抛裸 ValueError。
    """
    from pathlib import Path

    path = Path(out_dir) / "pool.json"
    if not resume:
        if path.exists():
            console.print(f"[yellow]警告：{path} 已存在，本次将从初始手册重新开始并覆盖它"
                          f"（要续跑请加 --resume）。[/yellow]")
        return None
    if not path.exists():
        console.print(f"[yellow]--resume 但 {path} 不存在，按新建池开始。[/yellow]")
        return None
    from .battle.evolution.pool import PlaybookPool

    pool = PlaybookPool.load(str(path))
    champ = pool.champion()
    saved_n = len(pool.instances)
    if instances is not None and pool.instances != [i.name for i in instances]:
        console.print(
            f"[red]续跑失败：池的实例空间与本次不一致。[/red]\n"
            f"  池里是 {saved_n} 个实例，本次要求 {len(instances)} 个。\n"
            f"  续跑必须沿用上次的 --instances / --team-size（池的分数向量按实例名索引）。\n"
            f"  → 改用 --instances {saved_n}，或换一个 --out 目录从头开始。")
        raise SystemExit(1)
    console.print(f"[cyan]续跑：载入 {path}（成员 {len(pool.members)} 个，实例 {saved_n} 个，"
                  f"champion={champ.playbook.version if champ else None}）[/cyan]")
    return pool


def _run_evolve_steps_cli(args) -> int:
    """evolve steps：R4 多步进化闭环（池 + 两级门 + 剥削者 + 回归门）。"""
    from .battle.evolution.run import run_steps

    insts = _limited_instances("d_sel", args.instances, args.team_size)
    pool = _resume_pool(args.out, args.resume, insts)
    with _evolve_progress(args.progress):
        out = run_steps(n=args.n, seed=args.seed, out_dir=args.out, M=args.m,
                        llm=args.llm, team_size=args.team_size, lives=args.lives,
                        instances=insts,
                        seeds_per_instance=args.seeds_per_instance,
                        minibatch_seeds=args.minibatch_seeds,
                        memory_dir=args.memory_dir,
                        pool=pool)
    console.print(f"[bold]evolve steps[/bold] n={args.n} seed={args.seed}")
    for s in out["steps"]:
        console.print(f"  step#{s['step']} champion={s['champion']} opp={s['opponent']} "
                      f"gate={s.get('gate')} entered={s.get('entered')} "
                      f"promoted={s.get('promoted')} exploit={s.get('exploitability')}")
        if args.health:
            h = s.get("health", {})
            console.print(f"    health: front_width={h.get('front_width')} "
                          f"payoff_antisym={h.get('payoff_antisym')} "
                          f"edit_acceptance={h.get('edit_acceptance')}")
    f = out["final"]
    console.print(f"[bold]final[/bold] champion={f['champion']} best={f['best']} "
                  f"front={f['front_size']} archive={f['archive_size']}")
    console.print(f"entered={f['entered']} promoted={f['promoted']} "
                  f"cheap_rejected={f['cheap_rejected']} full_rejected={f['full_rejected']}")
    console.print(f"pool → {out['pool_path']}")
    return 0


def _run_evolve_epoch_cli(args) -> int:
    """evolve epoch：R5 epoch 调度（慢更新 + Meta Playbook + D_test 汇报）。"""
    from .battle.evolution.run import run_epochs

    insts = _limited_instances("d_sel", args.instances, args.team_size)
    pool = _resume_pool(args.out, args.resume, insts)
    with _evolve_progress(args.progress):
        out = run_epochs(n=args.n, seed=args.seed, out_dir=args.out, E=args.E, M=args.m,
                         llm=args.llm, no_slow_update=args.no_slow_update,
                         team_size=args.team_size, lives=args.lives,
                         instances=insts,
                         dtest_instances=_limited_instances("d_test", args.dtest_instances,
                                                            args.team_size),
                         seeds_per_instance=args.seeds_per_instance,
                         minibatch_seeds=args.minibatch_seeds,
                         memory_dir=args.memory_dir,
                         pool=pool)
    console.print(f"[bold]evolve epoch[/bold] n={args.n} E={args.E} seed={args.seed} "
                  f"no_slow_update={args.no_slow_update}")
    for e in out["epochs"]:
        dt = e.get("dtest", {})
        console.print(f"  epoch#{e['epoch']} champion={e['champion']} improved={e['improved']} "
                      f"v_tier={e.get('v_tier')} dtest_winrate={dt.get('winrate')} "
                      f"delta={e.get('dtest_delta')}")
    console.print(f"pool → {out['pool_path']}")
    return 0


def _run_evolve_battles_cli(args) -> int:
    """evolve battles：G5 GlobalMem 闭环（战斗 → 双视角复盘 → 双库落地 → Q 更新 → A/B）。"""
    from .battle.evolution.globalmem_run import run_battles

    settings = get_settings()
    if args.llm and not settings.has_api_key:
        console.print("[yellow]--llm 但无 API key：降级离线确定性玩家，且跳过战后分析。[/yellow]")
    if args.globalmem_dir is None:
        console.print("[yellow]未给 --globalmem-dir：GlobalMem 注入/复盘/Q 更新全部关闭。[/yellow]")
    insts = _limited_instances("d_sel", args.instances, args.team_size)
    ab_insts = _limited_instances("d_sel", args.ab_instances, args.team_size)
    with _evolve_progress(args.progress):
        out = run_battles(n=args.n, seed=args.seed, out_dir=args.out, settings=settings,
                          team_size=args.team_size, lives=args.lives, instances=insts,
                          llm=args.llm, globalmem_dir=args.globalmem_dir,
                          memory_dir=args.memory_dir, ab_every=args.ab_every,
                          ab_instances=ab_insts, ab_seeds=args.ab_seeds,
                          fake_analyst=args.fake_analyst)
    console.print(f"[bold]evolve battles[/bold] n={args.n} seed={args.seed}")
    for b in out["battles"]:
        loaded = ",".join(f"{s}={(b['loaded'][s] or '-')[:12]}" for s in ("a", "b"))
        acts = ",".join(f"{s}:{v.get('action')}" for s, v in sorted(b["globalmem"].items())) or "-"
        console.print(f"  #{b['battle']} {b['instance']} winner={b['winner'] or '平'} "
                      f"turns={b['turns']} loaded({loaded}) gm({acts}) "
                      f"analyst={b['analyst']}")
        if b.get("ab"):
            ab = b["ab"]
            console.print(f"    A/B: 开={ab['on']['winrate']:.3f}{ab['on']['ci95']} "
                          f"关={ab['off']['winrate']:.3f}{ab['off']['ci95']} "
                          f"delta={ab['delta']:+.3f}（n={ab['on']['n_games']}/边）",
                          markup=False)
    gm, mem = out["globalmem"], out["memory"]
    console.print(f"[bold]final[/bold] GlobalMem active={gm['active']} total={gm['total']} "
                  f"· 局部记忆 entries={mem['entries']}")
    u, hit = out["usage"], out["cache_hit_rate"]
    if u:
        parts = [f"输入 {u.get('input_tokens', 0)}", f"输出 {u.get('output_tokens', 0)}"]
        if hit is not None:
            parts.append(f"提示缓存命中 {hit:.1%}"
                         f"（读 {u.get('cache_read_tokens', 0)}"
                         + (f" / 未命中 {u['cache_miss_tokens']}"
                            if "cache_miss_tokens" in u else "") + "）")
        else:
            parts.append("提示缓存：网关未上报（无法判断是否生效）")
        console.print("  token: " + " · ".join(parts))
    console.print(f"产物 → {out['out_dir']}")
    return 0


def _cli_event_sink() -> Callable[[dict], None]:
    """CLI 实时事件 sink：thinking / tool 一发生就打印，用户看到工作过程，不干等。

    仅实时打印过程事件；最终答案仍由 _print_reply 统一输出（避免重复）。
    """

    def sink(event: dict) -> None:
        kind = event.get("event")
        if kind == "thinking":
            console.print(f"[dim]💭 {event.get('text', '')}[/dim]")
        elif kind == "progress":
            console.print(f"[dim]⏳ {event.get('text', '')}[/dim]")
        elif kind == "tool":
            name = event.get("name", "")
            args = event.get("args", {})
            result = event.get("result", "")
            console.print(f"[cyan]🔧 {name}({args}) → {result[:200]}{'…' if len(str(result)) > 200 else ''}[/cyan]")

    return sink


def _run_once(agent: ChatAgent, query: str, debug: bool) -> int:
    reply = agent.chat(query, event_sink=_cli_event_sink())
    _print_reply(reply, debug)
    return 0


def _repl(agent: ChatAgent, debug: bool) -> int:
    console.print("[cyan]Roco PVP Agent — 输入 exit / quit / q 退出[/cyan]")
    history = []
    while True:
        try:
            text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            break
        reply = agent.chat(text, history=history, event_sink=_cli_event_sink())
        _print_reply(reply, debug)
        history = reply.history
    return 0


def _print_reply(reply: ChatReply, debug: bool) -> None:
    if debug:
        for step in reply.thinking:
            console.print(f"[dim]💭 {step}[/dim]")
        for tc in reply.tool_calls:
            console.print(f"[dim]🔧 {tc['name']}({tc['args']}) -> {tc['result']}[/dim]")
        if reply.usage.get("total_tokens"):
            u = reply.usage
            console.print(f"[dim]⚡ tokens: 输入 {u['input_tokens']} / 输出 {u['output_tokens']} / 总计 {u['total_tokens']}[/dim]")
    if reply.offline:
        console.print("[yellow]（离线模式）[/yellow]")
    console.print(f"[bold]{reply.reply}[/bold]")


if __name__ == "__main__":
    raise SystemExit(main())

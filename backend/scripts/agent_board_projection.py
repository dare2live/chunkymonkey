#!/usr/bin/env python3
"""Agent status board **projection** — 现查, 零文件 (goal.md P2.3)。

从 config / lineage artifact / goal.md 手写段派生当前 track / cutover 意图与实际裁决 /
Phase D·E 裁决 / 禁令。**投影 only, 永不是执法输入** —— resolver 才是真相。

2026-08-11 退役了 `BOARD.md` + `data/board/agent_context.json` 两个落盘产物及其
`agent_board` 漂移门: 它们装的是 L2 状态, 而 L2 契约是「命令现查、零文件、禁人写」。
实测 collect() 仅 0.3s 且不连库, 现查比维护文件 + 维护一道保证文件不烂的门更便宜;
那道门本轮审计里还实际阻断过一次文档修正。

消费方: `scripts/chunkyctl status`(经 services/project_status.py) 与
`scripts/chunkyctl agent-boot`(backend/scripts/agent_boot.py) 现调 collect()。
手写真相仍在 goal.md (objective / 裁决 / 禁令 / 下一步)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
SNAPSHOT_PREFIX = "> Snapshot:"
ENFORCEMENT_BANNER = (
    "> **Projection only** — not an enforcement input. Cutover / readiness / "
    "PIT gates still resolve from yaml + code resolvers + accepted partitions."
)

BANS = [
    "B-pit/C cutover_allowed=true without strong evidence + explicit yaml",
    "Optuna / E gate loosen / StrategyRelease / margin thaw",
    "mass backfill / plugin bus / second DB / silent cutover",
    "--no-verify / agent self-downgrade of commit tier",
]


def _next_knives(*, b_on: bool, c_on: bool) -> list[str]:
    """Project near-term knives from goal.md mainline (not A→H research map).

    BOARD is projection-only; ``goal.md`` + FOUNDATION/STRATEGY execution plans
    win on ordering. Cutover yaml only gates opt-in lines when gates are false.
    """
    knives = [
        "FOUNDATION §6 exit + 100% usable MET (no class-A): docs/engineering_governance.md §9.1 (残留分类 A/B/C/D)",
        "STRATEGY blocked: analysis/STRATEGY_EXECUTION_PLAN.md until goal.md explicit RX schedule",
        "foundation phase_closure_ready — F1–F10 PASS (analysis/foundation_phase_reeval_20260721.md)",
        "FND-GATE / §15-VERIFY FIXED; org incremental-check-every-run (mass banned)",
        "S7 typed hard-stop wall — no fake COMPAT; Type-B enrichment FIXED",
        "E/F remeasure paused until owner schedules (Optuna/Release banned)",
    ]
    if not (b_on and c_on):
        knives.insert(
            0,
            "opt-in C/B-pit cutover only with strong evidence (yaml still false)",
        )
    return knives


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _goal_hand_excerpt(goal_text: str, *, max_chars: int = 1200) -> str:
    """Keep a short excerpt of goal.md for boot context (not a second owner)."""
    lines = goal_text.splitlines()
    # Prefer current objective section.
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## 当前 objective"):
            start = i
            break
    chunk = "\n".join(lines[start : start + 40])
    if len(chunk) > max_chars:
        return chunk[: max_chars - 1] + "…"
    return chunk


def collect(repo: Path = REPO) -> dict[str, Any]:
    b_pit_path = repo / "backend" / "config" / "b_pit_mart_cutover.yaml"
    tier12_path = repo / "backend" / "config" / "tier12_publish.yaml"
    b_pit = _load_yaml(b_pit_path)
    tier12 = _load_yaml(tier12_path)
    b_pit_gate = (b_pit.get("mart_cutover") or {}) if b_pit else {}
    c_gate = (tier12.get("consumer_cutover") or {}) if tier12 else {}

    e_manifest = _load_json(
        repo / "data" / "lineage" / "phase_e_experiment_verdicts" / "manifest.json"
    )
    d_manifest = _load_json(
        repo / "data" / "lineage" / "phase_d_experiment_runs" / "manifest.json"
    )
    b_shadow = _load_json(
        repo / "data" / "lineage" / "b_pit_breadth_shadow" / "summary.json"
    )
    c_accept = _load_json(
        repo / "data" / "lineage" / "tier12_publish_batches"
        / "full_universe_accept_20260717.json"
    )

    e_ladder: list[dict[str, Any]] = []
    e_overall: dict[str, Any] = {}
    window: dict[str, Any] = {}
    if e_manifest:
        e_overall = e_manifest.get("overall") or {}
        window = e_manifest.get("window") or {}
        for row in e_manifest.get("ladder") or []:
            if not isinstance(row, dict):
                continue
            e_ladder.append({
                "block": row.get("block"),
                "verdict": row.get("verdict"),
                "claimable": row.get("claimable"),
                "strategy_release": row.get("strategy_release"),
                "reason": row.get("reason"),
            })

    b_window = (b_shadow or {}).get("window") or {}
    goal_path = repo / "goal.md"
    goal_text = goal_path.read_text(encoding="utf-8") if goal_path.is_file() else ""

    _now = dt.datetime.now(dt.timezone.utc)  # Phase ψ.5 allowlist: 文档元数据时间戳非 trade_date
    _today_compact = _now.strftime("%Y%m%d")

    # yaml 的 cutover_allowed 是 owner **意图**；实际读面由 resolver 裁决。投影必须
    # 两者都给，否则窗口走完后看板会继续显示一个已经不生效的 True。
    #
    # 探针日 = `expected_window_end + 1`，一个从 config 算出的**固定值**，语义是
    # 「窗口末端之后的第一天，resolver 会怎么判」。刻意**不用** wall-clock「今天」：
    #   (a) 自然日不是 trade_date。resolver 做的是裸字符串范围比较，没有交易日概念
    #       (b_pit_mart_cutover.py 的 window 判定)；窗口若被延到未来，拿自然日去问
    #       会对周末/节假日报出 MART_CUTOVER —— 正是本刀要消灭的那种误导性裁决。
    #   (b) 随日期变的值一旦进 BOARD.md 正文或 agent_context.json，漂移门会在每次
    #       跨天后必红（二者的比较面只排除顶层 generated_at），等于每天堵死所有提交。
    # `window_lapsed` 是布尔，只在跨过窗口末端那天翻一次，此后恒定，幂等不破。
    # rule-compliance: ok evidence=不作为 trade_date 使用，仅判定「窗口末端是否已成
    #   过去时」——该判定是纯日历事实，与某日是否开市无关；送进 resolver 的是上面
    #   从 config 算出的固定探针日，不是这个值。
    _win_end = str(b_pit_gate.get("expected_window_end") or "").strip()
    _window_lapsed = bool(_win_end) and _today_compact > _win_end

    _probe_day = ""
    if len(_win_end) == 8 and _win_end.isdigit():
        _probe_day = (
            dt.date(int(_win_end[:4]), int(_win_end[4:6]), int(_win_end[6:]))
            + dt.timedelta(days=1)
        ).strftime("%Y%m%d")

    b_pit_effective: dict[str, Any]
    try:
        from services.b_pit_mart_cutover import resolve_b_pit_mart_production_read

        # resolver 只读 config + lineage artifact，不连 DB，故此处零新增依赖。
        _read = resolve_b_pit_mart_production_read(
            _probe_day,
            # 跟随 repo 参数，否则 fixture repo 下会穿透去读真实 config
            config_path=repo / "backend" / "config" / "b_pit_mart_cutover.yaml",
        )
        b_pit_effective = {
            "probe_day": _probe_day,
            "probe_status": _read.status,
            "probe_source": _read.source,
            "probe_cutover_allowed": bool(_read.cutover_allowed),
            "probe_reasons": list(_read.reasons)[:3],
            "window_lapsed": _window_lapsed,
        }
    except Exception as exc:  # 投影永不因 resolver 异常而生成失败
        b_pit_effective = {
            "probe_day": _probe_day,
            "probe_status": "UNRESOLVED",
            "probe_source": "unknown",
            "probe_cutover_allowed": False,
            "probe_reasons": [f"{type(exc).__name__}: {exc}"[:120]],
            "window_lapsed": _window_lapsed,
        }

    # 影子期到期由起点 + 上限算出，不写死状态串。eng_gov §13 =「10 个工作 session
    # 或 14 天，先到者」；session 数无法从代码观测，故只判 14 天这一侧 —— 该侧到期
    # 即不得再自称 open，须由 owner 裁决 cutover 或重置。
    _shadow_start_day = dt.date(2026, 7, 20)  # eng_gov §13 起点 be8efc6f
    _shadow_deadline_day = _shadow_start_day + dt.timedelta(days=14)
    # 同 window_lapsed：只在跨过 deadline 那天翻一次，之后恒定 —— 不引入每日变动源。
    _shadow_expired = _today_compact > _shadow_deadline_day.strftime("%Y%m%d")

    return {
        # 现查后必须声明**输入是否齐全**: 缺 config 时投影会退化成一份「看起来正常
        # 但全是缺省值」的空板 —— 那正是项目禁的「空扫描冒充 PASS」。消费方据此判 error。
        "inputs_present": {
            "b_pit_cutover_yaml": b_pit_path.is_file(),
            "tier12_publish_yaml": tier12_path.is_file(),
            "goal_md": goal_path.is_file(),
        },
        "generated_at": _now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_from": "backend/scripts/agent_board_projection.py (现查, 不落盘)",
        "enforcement": "projection_only_not_truth",
        "track": {
            "name": "transport_strangler_s1_s7",
            "status": "foundation_solidify_85pct_s7_wall_e0_thin",
            "agent_os": (
                "shadow_period_EXPIRED_awaiting_owner_verdict"
                if _shadow_expired
                else "shadow_period_open_not_closed"
            ),
            "a_to_h": "post_research_map_only_efgh_appendix",
            "wp1": "FIXED",
            "wp2": "FIXED",
            "wp3": "FIXED",
            "wp4": "FIXED",
            "wp5": "SKIPPED_occam",
            "wp6": (
                "POLICY_FIXED_shadow_EXPIRED"
                if _shadow_expired
                else "POLICY_FIXED_shadow_open"
            ),
            "shadow_started": "be8efc6f/2026-07-20",
            "shadow_deadline": (
                f"{_shadow_deadline_day.isoformat()} "
                "(14d cap, or 10 work sessions — whichever first)"
            ),
            "shadow_expired": _shadow_expired,
        },
        "cutovers": {
            "b_pit_mart": {
                "cutover_allowed": bool(b_pit_gate.get("cutover_allowed", False)),
                "source": "backend/config/b_pit_mart_cutover.yaml",
                "effective": b_pit_effective,
                "window": {
                    "start": b_pit_gate.get("expected_window_start"),
                    "end": b_pit_gate.get("expected_window_end"),
                },
                "shadow": {
                    "match_day_count": b_window.get("match_day_count"),
                    "diverge_day_count": b_window.get("diverge_day_count"),
                    "ratios_match_all": b_window.get("ratios_match_all"),
                    "frontier_day": b_window.get("frontier_day") or (b_shadow or {}).get("frontier_day"),
                    "match_baseline_kind": (b_shadow or {}).get("match_baseline_kind"),
                },
            },
            "tier12_consumer": {
                "cutover_allowed": bool(c_gate.get("cutover_allowed", False)),
                "source": "backend/config/tier12_publish.yaml#consumer_cutover",
                "accept": {
                    "decision_date": (c_accept or {}).get("decision_date"),
                    "stock_row_count": (c_accept or {}).get("stock_row_count"),
                    "universe_membership_size": (c_accept or {}).get("universe_membership_size"),
                    "publish_scope": (c_accept or {}).get("publish_scope"),
                    "published": (c_accept or {}).get("published"),
                    "content_hash": (c_accept or {}).get("content_hash"),
                    "status": (c_accept or {}).get("status"),
                },
            },
        },
        "phase_d": {
            "summary": (d_manifest or {}).get("summary"),
            "artifact": "data/lineage/phase_d_experiment_runs/manifest.json",
        },
        "phase_e": {
            "overall_status": e_overall.get("status"),
            "any_claimable": e_overall.get("any_claimable"),
            "strategy_release": e_overall.get("strategy_release"),
            "window": window,
            "ladder": e_ladder,
            "artifact": "data/lineage/phase_e_experiment_verdicts/manifest.json",
        },
        "bans": list(BANS),
        "next_knives_frozen": _next_knives(
            b_on=bool(b_pit_gate.get("cutover_allowed", False)),
            c_on=bool(c_gate.get("cutover_allowed", False)),
        ),
        "goal_hand_excerpt": _goal_hand_excerpt(goal_text),
        "sources": [
            "backend/config/b_pit_mart_cutover.yaml",
            "backend/config/tier12_publish.yaml",
            "data/lineage/b_pit_breadth_shadow/summary.json",
            "data/lineage/tier12_publish_batches/full_universe_accept_20260717.json",
            "data/lineage/phase_e_experiment_verdicts/manifest.json",
            "data/lineage/phase_d_experiment_runs/manifest.json",
            "goal.md (hand excerpt only)",
        ],
    }


def render_md(d: dict[str, Any]) -> str:
    L: list[str] = []
    add = L.append
    add("# BOARD — agent status projection (现查, 不落盘)")
    add("")
    add("> 由 `scripts/chunkyctl status` / `agent-boot` **现查生成**；本文不落盘，无需也无法「手改」。")
    add(ENFORCEMENT_BANNER)
    add(f"{SNAPSHOT_PREFIX} {d['generated_at']} (现查时刻)")
    add(
        "> ↑ 现查时刻。**本投影不含数据前沿** —— 前沿 / 滞后 / 水位 / 告警查 "
        "`scripts/chunkyctl status`（accepted 分区表为真相源）。"
    )
    add("")
    add("## Track")
    add("")
    t = d["track"]
    add(f"- track: `{t['name']}` status=`{t.get('status', 'unknown')}`")
    add(f"- A→H: `{t['a_to_h']}`")
    wp_status = " | ".join(
        f"{key.upper()}: `{t[key]}`" for key in sorted(t) if key.startswith("wp")
    )
    add(f"- {wp_status}")
    if t.get("shadow_started"):
        add(
            f"- agent-OS: `{t.get('agent_os')}` shadow start=`{t['shadow_started']}` "
            f"deadline=`{t.get('shadow_deadline')}` "
            "(ceremony flip only; B-pit/C data cutover unrelated)"
        )
    add("")
    add("## Cutovers (yaml 意图 + resolver 实际裁决)")
    add("")
    bp = d["cutovers"]["b_pit_mart"]
    tc = d["cutovers"]["tier12_consumer"]
    add(
        f"- B-pit mart yaml `cutover_allowed={bp['cutover_allowed']}` "
        f"(shadow match={bp['shadow'].get('match_day_count')}/"
        f"diverge={bp['shadow'].get('diverge_day_count')}; "
        f"frontier={bp['shadow'].get('frontier_day')})"
    )
    _eff = bp.get("effective") or {}
    _eff_reason = (_eff.get("probe_reasons") or [None])[0]
    # 背离 = yaml 说可切，但窗口末端已成过去时，且窗末+1 探针证实此后 fail-closed。
    _divergent = (
        bool(bp["cutover_allowed"])
        and bool(_eff.get("window_lapsed"))
        and not bool(_eff.get("probe_cutover_allowed"))
    )
    add(
        f"  - {'**⚠ yaml 意图已不生效**' if _divergent else '窗口探针'} "
        f"probe=`{_eff.get('probe_day')}`(窗末+1) status=`{_eff.get('probe_status')}` "
        f"source=`{_eff.get('probe_source')}` window_lapsed=`{_eff.get('window_lapsed')}`"
        + (f" — `{_eff_reason}`" if _eff_reason else "")
    )
    if _divergent:
        add(
            "  - attested 窗口末端已成过去时：晚于该日的任何 trade_date 一律 "
            "fail-closed 回 legacy_mart —— 需 owner 裁决：重测 shadow 延窗 / "
            "显式收回 cutover / 改滚动窗口语义。"
        )
    acc = tc["accept"]
    add(
        f"- C consumer `cutover_allowed={tc['cutover_allowed']}` "
        f"(accept {acc.get('decision_date')}: "
        f"{acc.get('stock_row_count')}/{acc.get('universe_membership_size')} "
        f"scope={acc.get('publish_scope')} published={acc.get('published')})"
    )
    add("")
    add("## Phase D runtime (lineage projection)")
    add("")
    d_data = d.get("phase_d") or {}
    pd = d_data.get("summary") or {}
    if pd:
        add(
            f"- b0_bound: verdict=`{pd.get('verdict')}` claimable={pd.get('claimable')} "
            f"protocol=`{pd.get('fold_protocol')}` folds={pd.get('n_folds')} "
            f"holdout_start={pd.get('holdout_start')}"
        )
        mo = pd.get("measured_offline") or {}
        if mo:
            add(
                f"- measured_offline: verdict=`{mo.get('verdict')}` "
                f"claimable={mo.get('claimable')} "
                f"package=`{mo.get('strategy_package')}` "
                f"trades={mo.get('n_trades_completed')} "
                f"status=`{mo.get('measure_status')}`"
            )
    else:
        add("- no persisted Phase D ExperimentRun artifact")
    add(f"- artifact: `{d_data.get('artifact')}`")
    add("")
    add("## Phase E verdicts (lineage projection)")
    add("")
    pe = d["phase_e"]
    add(
        f"- overall: `{pe.get('overall_status')}` "
        f"claimable={pe.get('any_claimable')} release={pe.get('strategy_release')}"
    )
    w = pe.get("window") or {}
    if w:
        add(
            f"- window: {w.get('start')}–{w.get('end')} "
            f"({w.get('trading_day_count')} trading days)"
        )
    add("")
    add("| block | verdict | claimable |")
    add("|---|---|---|")
    for row in pe.get("ladder") or []:
        add(
            f"| {row.get('block')} | {row.get('verdict')} | {row.get('claimable')} |"
        )
    add("")
    add("## Bans")
    add("")
    for ban in d["bans"]:
        add(f"- {ban}")
    add("")
    add("## Next (projection — goal.md wins on order)")
    add("")
    for item in d["next_knives_frozen"]:
        add(f"- {item}")
    add("")
    add("## Sources")
    add("")
    for src in d["sources"]:
        add(f"- `{src}`")
    add("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    """打印投影。**不写盘** —— L2 状态零文件 (goal.md P2.3)。

    2026-08-11 之前这里写 BOARD.md + data/board/agent_context.json, 于是每次跨天
    都要重生+提交, 还得配一道 agent_board 漂移门来保证它不烂 —— 那道门本轮审计里
    实际阻断过一次文档修正。既然 collect() 只要 0.3s 且不连库, 现查比存文件便宜,
    文件与它的门一起退役。消费方: `chunkyctl status` 与 `chunkyctl agent-boot` 现调。
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="机器读 JSON")
    ap.add_argument("--root", type=Path, default=REPO)
    args = ap.parse_args(argv)
    data = collect(args.root)
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) if args.json
          else render_md(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

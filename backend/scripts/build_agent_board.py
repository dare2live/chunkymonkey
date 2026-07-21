#!/usr/bin/env python3
"""Generate agent status board from accepted facts / config / lineage.

Owner pattern mirrors ``build_feature_map.py``:
  - sole writer of BOARD.md + data/board/agent_context.json
  - projection only — never an enforcement input (resolvers remain truth)
  - ``--check`` drift gate for safe_commit

Hand-written truth stays in goal.md (objective / 裁决 / 禁令 / 下一步).
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
MD_OUT = REPO / "BOARD.md"
JSON_OUT = REPO / "data" / "board" / "agent_context.json"
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

    BOARD is projection-only; ``goal.md`` + ``plan_reeval_first_principles`` win
    on ordering. Cutover yaml only gates opt-in lines when gates are false.
    """
    knives = [
        "S7 legacy raw: dc_member / stock-flow drill L0 / typed hard-stop tail "
        "(inventory 29/46 ssot)",
        "E0 disclosure residual: org_holding provider land BLOCKED; expand "
        "stk/holders accept",
        "§15 adoption verify: commits/knife ≤1.5; async CI; pre-knife before L3",
        "E/F same-protocol remeasure paused (not near-term; F0–F3 protocol-complete)",
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
    b_pit = _load_yaml(repo / "backend" / "config" / "b_pit_mart_cutover.yaml")
    tier12 = _load_yaml(repo / "backend" / "config" / "tier12_publish.yaml")
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

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Phase ψ.5 allowlist: 文档元数据时间戳非 trade_date
        "generated_from": "backend/scripts/build_agent_board.py",
        "enforcement": "projection_only_not_truth",
        "track": {
            "name": "transport_strangler_s1_s7",
            "status": "s1_s6_fixed_s7_partial_e0_partial",
            "agent_os": "shadow_period_open_not_closed",
            "a_to_h": "post_research_map_only_efgh_appendix",
            "wp1": "FIXED",
            "wp2": "FIXED",
            "wp3": "FIXED",
            "wp4": "FIXED",
            "wp5": "SKIPPED_occam",
            "wp6": "POLICY_FIXED_shadow_open",
            "shadow_started": "be8efc6f/2026-07-20",
            "shadow_deadline": "10_sessions_or_14d_first",
        },
        "cutovers": {
            "b_pit_mart": {
                "cutover_allowed": bool(b_pit_gate.get("cutover_allowed", False)),
                "source": "backend/config/b_pit_mart_cutover.yaml",
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
    add("# BOARD — generated agent status projection")
    add("")
    add("> 由 `backend/scripts/build_agent_board.py` 重生成，**勿手改**。")
    add(ENFORCEMENT_BANNER)
    add(f"{SNAPSHOT_PREFIX} {d['generated_at']}")
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
    add("## Cutovers (yaml projection)")
    add("")
    bp = d["cutovers"]["b_pit_mart"]
    tc = d["cutovers"]["tier12_consumer"]
    add(
        f"- B-pit mart `cutover_allowed={bp['cutover_allowed']}` "
        f"(shadow match={bp['shadow'].get('match_day_count')}/"
        f"diverge={bp['shadow'].get('diverge_day_count')}; "
        f"frontier={bp['shadow'].get('frontier_day')})"
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


def _body(text: str) -> str:
    return "\n".join(
        ln for ln in text.splitlines() if not ln.startswith(SNAPSHOT_PREFIX)
    )


def write_outputs(d: dict[str, Any], *, check: bool, quiet: bool) -> int:
    md = render_md(d)
    old_md = MD_OUT.read_text(encoding="utf-8") if MD_OUT.exists() else ""
    md_drift = _body(old_md) != _body(md)

    json_text = json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    old_json = JSON_OUT.read_text(encoding="utf-8") if JSON_OUT.exists() else ""
    d_cmp = {k: v for k, v in d.items() if k != "generated_at"}
    old_cmp_text = old_json
    try:
        old_obj = json.loads(old_json) if old_json else {}
        if isinstance(old_obj, dict):
            old_obj.pop("generated_at", None)
            old_cmp_text = json.dumps(old_obj, ensure_ascii=False, sort_keys=True)
    except json.JSONDecodeError:
        # rule-compliance: ok evidence=malformed_prior_board_treated_as_full_drift
        old_cmp_text = old_json
    new_cmp_text = json.dumps(d_cmp, ensure_ascii=False, sort_keys=True)
    json_drift = old_cmp_text != new_cmp_text

    if check:
        if md_drift or json_drift:
            print(
                "BOARD.md / data/board/agent_context.json 漂移 — "
                "跑 PYTHONPATH=backend python backend/scripts/build_agent_board.py",
                file=sys.stderr,
            )
            return 1
        if not quiet:
            print("agent board fresh")
        return 0

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    if md_drift or not MD_OUT.exists():
        MD_OUT.write_text(md, encoding="utf-8")
    JSON_OUT.write_text(json_text, encoding="utf-8")
    if not quiet:
        print(
            f"BOARD.md + agent_context.json written "
            f"(b_pit_cutover={d['cutovers']['b_pit_mart']['cutover_allowed']}, "
            f"c_cutover={d['cutovers']['tier12_consumer']['cutover_allowed']})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--root", type=Path, default=REPO)
    args = ap.parse_args(argv)
    global MD_OUT, JSON_OUT
    if args.root != REPO:
        MD_OUT = args.root / "BOARD.md"
        JSON_OUT = args.root / "data" / "board" / "agent_context.json"
    data = collect(args.root)
    return write_outputs(data, check=args.check, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())

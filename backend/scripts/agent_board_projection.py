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
    "cutover_allowed=true without strong evidence + explicit yaml",
    "Optuna / E gate loosen / StrategyRelease / margin thaw",
    "mass backfill / plugin bus / second DB / silent cutover",
    "--no-verify / agent self-downgrade of commit tier",
]


def _next_knives(*, c_on: bool) -> list[str]:
    """Project near-term knives from goal.md mainline (not A→H research map).

    BOARD is projection-only; ``goal.md`` + FOUNDATION/STRATEGY execution plans
    win on ordering. Cutover yaml only gates opt-in lines when gates are false.
    """
    knives = [
        "FOUNDATION §6 exit + 100% usable MET (no class-A): 残留分类 A/B/C/D (git log --grep foundation_phase6)",
        "STRATEGY: 策略验证范式 (goal.md 北极星 2026-09-02) — 判例查询引擎待建; 旧 B0→B5/RX/holdout 轨逐项退役中 (授权锁已拆)",
        "foundation phase_closure_ready — F1–F8,F10 PASS (F9 retired 2026-09-02) (FND-GATE 十维, 见 backend/scripts/check_foundation_done.py)",
        "FND-GATE / §15-VERIFY FIXED; org incremental-check-every-run (mass banned)",
        "S7 typed hard-stop wall — no fake COMPAT; Type-B enrichment FIXED",
    ]
    if not c_on:
        knives.insert(
            0,
            "opt-in tier12 consumer cutover only with strong evidence (yaml still false)",
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
    tier12_path = repo / "backend" / "config" / "tier12_publish.yaml"
    tier12 = _load_yaml(tier12_path)
    c_gate = (tier12.get("consumer_cutover") or {}) if tier12 else {}

    e_manifest = _load_json(
        repo / "data" / "lineage" / "phase_e_experiment_verdicts" / "manifest.json"
    )
    d_manifest = _load_json(
        repo / "data" / "lineage" / "phase_d_experiment_runs" / "manifest.json"
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

    goal_path = repo / "goal.md"
    goal_text = goal_path.read_text(encoding="utf-8") if goal_path.is_file() else ""

    _now = dt.datetime.now(dt.timezone.utc)  # Phase ψ.5 allowlist: 文档元数据时间戳非 trade_date
    _today_compact = _now.strftime("%Y%m%d")

    # yaml 的 cutover_allowed 是 owner **意图**；实际读面由 resolver 裁决。投影必须
    # 两者都给，否则窗口走完后看板会继续显示一个已经不生效的 True。

    return {
        # 现查后必须声明**输入是否齐全**: 缺 config 时投影会退化成一份「看起来正常
        # 但全是缺省值」的空板 —— 那正是项目禁的「空扫描冒充 PASS」。消费方据此判 error。
        "inputs_present": {
            "tier12_publish_yaml": tier12_path.is_file(),
            "goal_md": goal_path.is_file(),
        },
        "generated_at": _now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_from": "backend/scripts/agent_board_projection.py (现查, 不落盘)",
        "enforcement": "projection_only_not_truth",
        "track": {
            "name": "transport_strangler_s1_s7",
            "status": "foundation_solidify_85pct_s7_wall_e0_thin",
            "a_to_h": "post_research_map_only_efgh_appendix",
        },
        "cutovers": {
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
            c_on=bool(c_gate.get("cutover_allowed", False)),
        ),
        "goal_hand_excerpt": _goal_hand_excerpt(goal_text),
        "sources": [
            "backend/config/tier12_publish.yaml",
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
    add("")
    add("## Cutovers (yaml 意图 + resolver 实际裁决)")
    add("")
    tc = d["cutovers"]["tier12_consumer"]
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

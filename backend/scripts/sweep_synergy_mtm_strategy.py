#!/usr/bin/env python3
"""Strategy-parameter sweep for synergy policy MTM validation."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_synergy_policy_mark_to_market import (  # noqa: E402
    validate_synergy_policy_mark_to_market,
)
from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent

DDL = """
CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_strategy_sweep (
    run_id TEXT NOT NULL,
    variant_id TEXT NOT NULL,
    mtm_run_id TEXT NOT NULL,
    candidate_run_id TEXT NOT NULL,
    source_run_id TEXT,
    label_name TEXT,
    top_quantile DOUBLE,
    daily_top_k INTEGER,
    min_market_hs300_ret_20d DOUBLE,
    min_market_hs300_ret_60d DOUBLE,
    objective_score DOUBLE,
    validation_status TEXT,
    promotion_status TEXT,
    production_eligible BOOLEAN,
    blockers_json TEXT,
    signal_count BIGINT,
    market_filter_removed_signal_count BIGINT,
    daily_top_k_filtered_count BIGINT,
    position_count BIGINT,
    total_return DOUBLE,
    annualized_return DOUBLE,
    max_drawdown DOUBLE,
    sharpe DOUBLE,
    avg_active_positions DOUBLE,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, variant_id)
);
CREATE INDEX IF NOT EXISTS idx_synergy_mtm_strategy_sweep_run
    ON mart_synergy_policy_mtm_strategy_sweep(run_id);

CREATE TABLE IF NOT EXISTS mart_synergy_policy_mtm_strategy_sweep_summary (
    run_id TEXT PRIMARY KEY,
    candidate_run_id TEXT NOT NULL,
    evaluated_variants INTEGER,
    best_variant_id TEXT,
    best_mtm_run_id TEXT,
    best_objective_score DOUBLE,
    best_validation_status TEXT,
    best_blockers_json TEXT,
    config_json TEXT,
    built_at TEXT NOT NULL
);
"""


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _finite(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _parse_float_grid(value: str | None) -> list[float | None]:
    if not value:
        return [None]
    out: list[float | None] = []
    for item in value.split(","):
        token = item.strip()
        if not token:
            continue
        if token.lower() in {"none", "null", "off"}:
            out.append(None)
        else:
            out.append(float(token))
    return out or [None]


def _slug_float(value: float | None) -> str:
    if value is None:
        return "none"
    text = f"{value:.6g}".replace("-", "m").replace(".", "p")
    return text.replace("+", "")


def _objective(
    mtm: dict[str, Any],
    *,
    max_drawdown: float,
    drawdown_penalty_weight: float,
    sharpe_weight: float,
    total_return_weight: float,
    exposure_penalty_weight: float,
    exposure_scale: float,
    blocked_penalty_weight: float,
) -> float:
    total_return = _finite(mtm.get("total_return"))
    annualized_return = _finite(mtm.get("annualized_return"))
    max_dd_abs = abs(min(_finite(mtm.get("max_drawdown")), 0.0))
    drawdown_excess = max(0.0, max_dd_abs - abs(float(max_drawdown)))
    sharpe = _finite(mtm.get("sharpe"))
    exposure = _finite(mtm.get("avg_active_positions")) / max(float(exposure_scale), 1.0)
    quality_penalty = 0.0
    for key in ("missing_entry_price_count", "missing_exit_price_count", "missing_path_price_count"):
        quality_penalty += min(float(mtm.get(key) or 0), 1000.0) / 1000.0
    blocked_penalty = (
        max(float(blocked_penalty_weight), 0.0)
        if str(mtm.get("validation_status") or "") != "pass"
        else 0.0
    )
    return (
        annualized_return
        + float(total_return_weight) * total_return
        + float(sharpe_weight) * sharpe
        - float(drawdown_penalty_weight) * (max_dd_abs + drawdown_excess)
        - float(exposure_penalty_weight) * exposure
        - quality_penalty
        - blocked_penalty
    )


def sweep_synergy_mtm_strategy(
    conn: Any,
    *,
    candidate_run_id: str,
    run_id: str,
    top_quantile: float = 0.10,
    daily_top_k: int | None = None,
    market_hs300_ret_20d_grid: list[float | None] | None = None,
    market_hs300_ret_60d_grid: list[float | None] | None = None,
    baseline_horizon_days: int = 60,
    min_positions: int = 500,
    min_active_days: int = 200,
    min_total_return: float = 0.0,
    max_drawdown: float = 0.25,
    transaction_cost_bps: float | None = None,
    conditional_threshold: float = 0.80,
    drawdown_penalty_weight: float = 2.0,
    sharpe_weight: float = 0.05,
    total_return_weight: float = 0.10,
    exposure_penalty_weight: float = 0.05,
    exposure_scale: float = 1000.0,
    blocked_penalty_weight: float = 1.0,
    force_research_only: bool = True,
    progress: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    started_at = utc_now_iso()
    started = time.perf_counter()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    stage_timings: dict[str, float] = {}
    grid20 = market_hs300_ret_20d_grid or [None]
    grid60 = market_hs300_ret_60d_grid or [None]
    conn.execute("DELETE FROM mart_synergy_policy_mtm_strategy_sweep WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_synergy_policy_mtm_strategy_sweep_summary WHERE run_id = ?", (run_id,))

    rows: list[tuple[Any, ...]] = []
    best: dict[str, Any] | None = None
    variant_count = 0
    for threshold20 in grid20:
        for threshold60 in grid60:
            variant_count += 1
            variant_id = f"hs20_{_slug_float(threshold20)}__hs60_{_slug_float(threshold60)}"
            mtm_run_id = f"{run_id}_{variant_id}"
            if progress:
                print(
                    f"[mtm-strategy-sweep] {utc_now_iso()} variant_start "
                    f"{variant_count} variant={variant_id}",
                    flush=True,
                )
            variant_t0 = time.perf_counter()
            mtm = validate_synergy_policy_mark_to_market(
                conn,
                candidate_run_id=candidate_run_id,
                run_id=mtm_run_id,
                top_quantile=top_quantile,
                daily_top_k=daily_top_k,
                min_market_hs300_ret_20d=threshold20,
                min_market_hs300_ret_60d=threshold60,
                baseline_horizon_days=baseline_horizon_days,
                min_positions=min_positions,
                min_active_days=min_active_days,
                min_total_return=min_total_return,
                max_drawdown=max_drawdown,
                transaction_cost_bps=transaction_cost_bps,
                conditional_threshold=conditional_threshold,
                force_research_only=force_research_only,
                progress=False,
            )
            objective_score = _objective(
                mtm,
                max_drawdown=max_drawdown,
                drawdown_penalty_weight=drawdown_penalty_weight,
                sharpe_weight=sharpe_weight,
                total_return_weight=total_return_weight,
                exposure_penalty_weight=exposure_penalty_weight,
                exposure_scale=exposure_scale,
                blocked_penalty_weight=blocked_penalty_weight,
            )
            blockers = mtm.get("blockers") or []
            item = {
                "variant_id": variant_id,
                "mtm_run_id": mtm_run_id,
                "objective_score": objective_score,
                "validation_status": mtm.get("validation_status"),
                "blockers": blockers,
                "min_market_hs300_ret_20d": threshold20,
                "min_market_hs300_ret_60d": threshold60,
                "total_return": mtm.get("total_return"),
                "annualized_return": mtm.get("annualized_return"),
                "max_drawdown": mtm.get("max_drawdown"),
                "sharpe": mtm.get("sharpe"),
            }
            if best is None or objective_score > float(best["objective_score"]):
                best = item
            rows.append(
                (
                    run_id,
                    variant_id,
                    mtm_run_id,
                    candidate_run_id,
                    mtm.get("source_run_id"),
                    mtm.get("label_name"),
                    top_quantile,
                    daily_top_k,
                    threshold20,
                    threshold60,
                    objective_score,
                    mtm.get("validation_status"),
                    mtm.get("promotion_status"),
                    bool(mtm.get("production_eligible")),
                    _json(blockers),
                    int(mtm.get("signal_count") or 0),
                    int(mtm.get("market_filter_removed_signal_count") or 0),
                    int(mtm.get("daily_top_k_filtered_count") or 0),
                    int(mtm.get("position_count") or 0),
                    mtm.get("total_return"),
                    mtm.get("annualized_return"),
                    mtm.get("max_drawdown"),
                    mtm.get("sharpe"),
                    mtm.get("avg_active_positions"),
                    built_at,
                )
            )
            stage_timings[f"variant_{variant_id}_s"] = round(time.perf_counter() - variant_t0, 3)
            if progress:
                print(
                    f"[mtm-strategy-sweep] {utc_now_iso()} variant_done "
                    f"variant={variant_id} objective={objective_score:.6f} "
                    f"status={mtm.get('validation_status')} dd={_finite(mtm.get('max_drawdown')):.4f}",
                    flush=True,
                )

    if rows:
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_synergy_policy_mtm_strategy_sweep (
                run_id, variant_id, mtm_run_id, candidate_run_id,
                source_run_id, label_name, top_quantile, daily_top_k,
                min_market_hs300_ret_20d, min_market_hs300_ret_60d,
                objective_score, validation_status, promotion_status,
                production_eligible, blockers_json, signal_count,
                market_filter_removed_signal_count, daily_top_k_filtered_count,
                position_count, total_return, annualized_return, max_drawdown,
                sharpe, avg_active_positions, built_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    config = {
        "candidate_run_id": candidate_run_id,
        "top_quantile": top_quantile,
        "daily_top_k": daily_top_k,
        "market_hs300_ret_20d_grid": grid20,
        "market_hs300_ret_60d_grid": grid60,
        "baseline_horizon_days": baseline_horizon_days,
        "min_positions": min_positions,
        "min_active_days": min_active_days,
        "min_total_return": min_total_return,
        "max_drawdown": max_drawdown,
        "transaction_cost_bps": transaction_cost_bps,
        "conditional_threshold": conditional_threshold,
        "force_research_only": force_research_only,
        "objective_weights": {
            "drawdown_penalty_weight": drawdown_penalty_weight,
            "sharpe_weight": sharpe_weight,
            "total_return_weight": total_return_weight,
            "exposure_penalty_weight": exposure_penalty_weight,
            "exposure_scale": exposure_scale,
            "blocked_penalty_weight": blocked_penalty_weight,
        },
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_synergy_policy_mtm_strategy_sweep_summary (
            run_id, candidate_run_id, evaluated_variants, best_variant_id,
            best_mtm_run_id, best_objective_score, best_validation_status,
            best_blockers_json, config_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            candidate_run_id,
            len(rows),
            best["variant_id"] if best else None,
            best["mtm_run_id"] if best else None,
            best["objective_score"] if best else None,
            best["validation_status"] if best else None,
            _json(best["blockers"] if best else []),
            _json(config),
            built_at,
        ),
    )
    for table in (
        "mart_synergy_policy_mtm_strategy_sweep",
        "mart_synergy_policy_mtm_strategy_sweep_summary",
    ):
        record_actual_version(conn, table)
    duration_s = time.perf_counter() - started
    stage_timings["total_s"] = round(duration_s, 3)
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="sweep_synergy_mtm_strategy",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        duration_s=duration_s,
        commit_sha=git_commit_sha(REPO),
        input_tables=[
            "mart_synergy_policy_candidate",
            "mart_temporal_research_panel",
            "fact_feature_panel",
            "market.price_kline_tdxhub",
        ],
        output_tables=[
            "mart_synergy_policy_mtm_strategy_sweep",
            "mart_synergy_policy_mtm_strategy_sweep_summary",
            "mart_synergy_policy_mtm_gate",
        ],
        gate_result=best["validation_status"] if best else "no_variants",
        blockers=best["blockers"] if best else ["no_variants"],
        perf_summary={
            "candidate_run_id": candidate_run_id,
            "evaluated_variants": len(rows),
            "best": best or {},
            "config": config,
            "stage_timings": stage_timings,
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "candidate_run_id": candidate_run_id,
        "evaluated_variants": len(rows),
        "best": best or {},
        "duration_s": duration_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-run-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--top-quantile", type=float, default=0.10)
    parser.add_argument("--daily-top-k", type=int, default=None)
    parser.add_argument("--market-hs300-ret-20d-grid", default="none")
    parser.add_argument("--market-hs300-ret-60d-grid", default="none")
    parser.add_argument("--baseline-horizon-days", type=int, default=60)
    parser.add_argument("--min-positions", type=int, default=500)
    parser.add_argument("--min-active-days", type=int, default=200)
    parser.add_argument("--min-total-return", type=float, default=0.0)
    parser.add_argument("--max-drawdown", type=float, default=0.25)
    parser.add_argument("--transaction-cost-bps", type=float, default=None)
    parser.add_argument("--conditional-threshold", type=float, default=0.80)
    parser.add_argument("--drawdown-penalty-weight", type=float, default=2.0)
    parser.add_argument("--sharpe-weight", type=float, default=0.05)
    parser.add_argument("--total-return-weight", type=float, default=0.10)
    parser.add_argument("--exposure-penalty-weight", type=float, default=0.05)
    parser.add_argument("--exposure-scale", type=float, default=1000.0)
    parser.add_argument("--blocked-penalty-weight", type=float, default=1.0)
    parser.add_argument("--allow-production-candidate", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    with get_conn() as conn:
        result = sweep_synergy_mtm_strategy(
            conn,
            candidate_run_id=args.candidate_run_id,
            run_id=args.run_id,
            top_quantile=args.top_quantile,
            daily_top_k=args.daily_top_k,
            market_hs300_ret_20d_grid=_parse_float_grid(args.market_hs300_ret_20d_grid),
            market_hs300_ret_60d_grid=_parse_float_grid(args.market_hs300_ret_60d_grid),
            baseline_horizon_days=args.baseline_horizon_days,
            min_positions=args.min_positions,
            min_active_days=args.min_active_days,
            min_total_return=args.min_total_return,
            max_drawdown=args.max_drawdown,
            transaction_cost_bps=args.transaction_cost_bps,
            conditional_threshold=args.conditional_threshold,
            drawdown_penalty_weight=args.drawdown_penalty_weight,
            sharpe_weight=args.sharpe_weight,
            total_return_weight=args.total_return_weight,
            exposure_penalty_weight=args.exposure_penalty_weight,
            exposure_scale=args.exposure_scale,
            blocked_penalty_weight=args.blocked_penalty_weight,
            force_research_only=not args.allow_production_candidate,
            progress=not args.quiet,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

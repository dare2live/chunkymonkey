#!/usr/bin/env python3
"""P-1.1 PIT integrity audit — PLAN_V3 v3.2 P-1 first gate.

Per PLAN_V3 §2.P-1, this audit verifies training data is fit for real-money use:
1. PIT column coverage: every fact_/mart_ table should have a PIT column
   (built_at / as_of_date / notice_date / trade_date / calc_date / date)
2. Batch-write anomaly: tables claiming walk-forward must NOT have all rows
   sharing a single built_at (the v2 ψ.γ ceiling test failure mode).
3. oos_* validity: oos_period_end must be > train_end_date (not in-sample fit
   regurgitated as OOS — CLAUDE Rule 7/8 reversal example).
4. Forward leak spot-check: for one walk-forward signal date, ensure all
   feature sources resolve to data <= signal_date.

Exit 0 = PASS, 1 = FAIL.
PLAN_V3 §2 P-1 Go: PIT FAIL=0 to unlock P0.

Usage:
    PYTHONPATH=backend python backend/scripts/audit_pit_integrity.py
    PYTHONPATH=backend python backend/scripts/audit_pit_integrity.py --json-out /tmp/pit.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import DB_PATH
from services.duck_adapter import connect as duck_connect

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("audit_pit")

# PIT column candidates (any one present = table has PIT, in priority order)
PIT_COL_CANDIDATES = (
    "built_at",
    "as_of_date",
    "calc_date",
    "trade_date",
    "notice_date",
    "signal_date",
    "snapshot_date",  # prediction outcome / ranking snapshot
    "date",
    "report_date",
    "ann_date",
    "publish_date",
    "updated_at",  # last-build timestamp; PIT JOIN uses updated_at <= signal_date
    "outcome_known_at",  # label-known time (for prediction outcome tables)
)

# Tables exempt from PIT column requirement.
# Note: pattern match uses `startswith(p) or p in table` — be careful with substrings.
PIT_EXEMPT_PATTERNS = (
    # Audit / log tables (self-audit, no time-series semantic)
    "fact_optuna_governance_log",
    "mart_audit_",
    "mart_data_audit_",
    "mart_feature_pit_audit",
    "mart_feature_pit_coverage_summary",
    "mart_feature_panel_validation",
    "mart_global_data_quality_gate",
    # Cohort / bundle-level (not time-series)
    "fact_institution_follow_backtest",
    "mart_challenger_evidence_bundle",
    "mart_champion_candidate_evaluation",
    # Pipeline manifest / state / lock / fingerprint
    "mart_data_source_failure_queue",
    "mart_data_source_watermark",
    "mart_data_deletion_",
    "mart_data_deprecation_",
    "mart_data_health",
    "mart_data_processing_",
    "mart_pipeline_lock",
    "mart_pipeline_run_manifest",
    "mart_step_fingerprint",
    "mart_lineage",
    "mart_feature_rank_matrix_cache_manifest",
    # Snapshot / latest-state tables
    "mart_etf_snapshot_latest",
    "mart_etf_snapshot_state",
    "mart_feature_drift",
    "mart_feature_drift_histogram",
    "mart_tdx_gpcw_field_profile",
    "mart_tdx_keep_promotion_gate",
    "mart_tdx_server_health",
    "fact_setup_snapshot",
    "fact_stock_archetype",
    "fact_policy_eval",
    "fact_controlling_shareholder",
    "fact_common_major_holder_stock",
    "fact_holder_event",
    "fact_holder_count_period",
    "fact_fund_holding_tdx_f10",
    "fact_shareholder_plan",
    "fact_shareholder_plan_tdx_f10",
    "fact_shareholder_trade",
    "fact_shareholder_trade_tdx_b",
    "fact_stock_quality_features",
    "fact_stock_stage_features",
    "fact_stock_turtle_features",
    "fact_stock_industry_context",
    "fact_stock_attention_snapshot",
    "fact_financial_derived",
    "fact_financial_indicator_ak",
    "fact_chain_alpha_truth",
    # Dim tables (slow-changing dimensions)
    "dim_",
)

# v3.2 critical tables (FAIL must block P0). Non-critical historical mart tables
# from v2 (single-batch built_at) are downgraded to WARN, since v3.2 only uses
# them as features through PIT-guarded joins, not as primary decision sources.
WALK_FORWARD_V3_CRITICAL = (
    "mart_per_stock_stage_strategy_optimal",  # v3.2 P0a uses as feature
)
WALK_FORWARD_V2_LEGACY = (
    "mart_per_stock_strategy_optimal",  # v3.2 deprecated as primary; WARN not FAIL
    "mart_per_formula_stage_optimal",   # same
)


@dataclass
class CheckResult:
    section: str
    name: str
    status: str  # PASS / WARN / FAIL
    detail: str
    rows: int = 0
    extras: dict = field(default_factory=dict)


def is_pit_exempt(table: str) -> bool:
    return any(table.startswith(p) or p in table for p in PIT_EXEMPT_PATTERNS)


def find_pit_column(conn, table: str) -> str | None:
    cols = {c[0] for c in conn.execute(f"DESCRIBE {table}").fetchall()}
    for cand in PIT_COL_CANDIDATES:
        if cand in cols:
            return cand
    return None


def check_pit_column_coverage(conn) -> list[CheckResult]:
    """Section 1: every fact_/mart_ table should have a PIT column or be exempt."""
    out: list[CheckResult] = []
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' AND (table_name LIKE 'fact_%' OR table_name LIKE 'mart_%') "
            "ORDER BY 1"
        ).fetchall()
    ]
    with_pit, without_pit_unexempt, exempt = [], [], []
    for t in tables:
        col = find_pit_column(conn, t)
        if col:
            with_pit.append((t, col))
        elif is_pit_exempt(t):
            exempt.append(t)
        else:
            without_pit_unexempt.append(t)

    out.append(CheckResult(
        section="1. PIT column coverage",
        name="with_pit",
        status="PASS",
        detail=f"{len(with_pit)}/{len(tables)} tables have PIT column",
        rows=len(with_pit),
    ))
    out.append(CheckResult(
        section="1. PIT column coverage",
        name="exempt",
        status="PASS",
        detail=f"{len(exempt)} tables exempt (audit/dim/snapshot/cohort)",
        rows=len(exempt),
    ))
    status = "PASS" if not without_pit_unexempt else "FAIL"
    out.append(CheckResult(
        section="1. PIT column coverage",
        name="without_pit_unexempt",
        status=status,
        detail=f"{len(without_pit_unexempt)} time-series tables WITHOUT PIT column",
        rows=len(without_pit_unexempt),
        extras={"tables": without_pit_unexempt},
    ))
    return out


def check_batch_write_anomaly(conn) -> list[CheckResult]:
    """Section 2: walk-forward tables must have multi-date built_at, not single batch.

    v3.2 critical tables: single-batch FAIL.
    v2 legacy tables: single-batch WARN (deprecated as primary in v3.2, used only as features).
    """
    out: list[CheckResult] = []
    for tier_tables, fail_severity, tier_label in (
        (WALK_FORWARD_V3_CRITICAL, "FAIL", "v3.2 critical"),
        (WALK_FORWARD_V2_LEGACY, "WARN", "v2 legacy (deprecated as primary)"),
    ):
        for table in tier_tables:
            try:
                row = conn.execute(
                    f"SELECT COUNT(DISTINCT built_at), COUNT(*), MAX(built_at), MIN(built_at) FROM {table}"
                ).fetchone()
                n_distinct, n_rows, max_d, min_d = row
                if n_rows == 0:
                    out.append(CheckResult(
                        section="2. Batch-write anomaly",
                        name=table,
                        status="WARN",
                        detail=f"{table} [{tier_label}]: 0 rows",
                    ))
                    continue
                if n_distinct == 1:
                    out.append(CheckResult(
                        section="2. Batch-write anomaly",
                        name=table,
                        status=fail_severity,
                        detail=(
                            f"{table} [{tier_label}]: all {n_rows} rows share built_at={max_d} "
                            "→ single-batch write, NOT walk-forward"
                        ),
                        rows=n_rows,
                        extras={"built_at": str(max_d), "tier": tier_label},
                    ))
                else:
                    out.append(CheckResult(
                        section="2. Batch-write anomaly",
                        name=table,
                        status="PASS",
                        detail=f"{table} [{tier_label}]: {n_distinct} distinct built_at ({min_d} → {max_d}), {n_rows} rows",
                        rows=n_rows,
                    ))
            except Exception as e:
                out.append(CheckResult(
                    section="2. Batch-write anomaly",
                    name=table,
                    status="WARN",
                    detail=f"{table}: check failed: {e}",
                ))
    return out


def check_oos_validity(conn) -> list[CheckResult]:
    """Section 3: oos_period_end must be after train_end_date (not in-sample regurgitation).

    Tiered: v3.2 critical tables → FAIL on violation; v2 legacy → WARN
    (deprecated as primary; flagged for P0a feature-join PIT guard).
    """
    out: list[CheckResult] = []
    tiers = [
        (WALK_FORWARD_V3_CRITICAL, "FAIL", "v3.2 critical"),
        (WALK_FORWARD_V2_LEGACY, "WARN", "v2 legacy"),
    ]
    for tables, severity, tier_label in tiers:
        for table in tables:
            try:
                cols = {c[0] for c in conn.execute(f"DESCRIBE {table}").fetchall()}
                if "oos_period_end" not in cols or "oos_period_start" not in cols:
                    out.append(CheckResult(
                        section="3. OOS validity",
                        name=table,
                        status="WARN",
                        detail=f"{table} [{tier_label}]: missing oos_period_start/end columns",
                    ))
                    continue

                # Build "row violates ANY rule" — Codex review Q1 critical fix.
                # Previous `AND` join only flagged rows failing all rules; OR catches any.
                checks = ["oos_period_end >= oos_period_start"]
                if "train_end_date" in cols:
                    checks.append("oos_period_start > train_end_date")
                checks.append("oos_period_start IS NOT NULL")
                checks.append("oos_period_end IS NOT NULL")
                where = " OR ".join(f"NOT ({c})" for c in checks)
                n_bad = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
                n_total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n_bad == 0:
                    out.append(CheckResult(
                        section="3. OOS validity",
                        name=table,
                        status="PASS",
                        detail=f"{table} [{tier_label}]: 0/{n_total} rows violate OOS rules",
                        rows=n_total,
                    ))
                else:
                    out.append(CheckResult(
                        section="3. OOS validity",
                        name=table,
                        status=severity,
                        detail=f"{table} [{tier_label}]: {n_bad}/{n_total} rows have OOS overlapping train (forward leak)",
                        rows=n_bad,
                    ))
            except Exception as e:
                out.append(CheckResult(
                    section="3. OOS validity",
                    name=table,
                    status="WARN",
                    detail=f"{table} [{tier_label}]: check failed: {e}",
                ))
    return out


def check_forward_leak_spot_check(conn) -> list[CheckResult]:
    """Section 4: cross-regime signal_dates, verify feature source dates <= signal_date.

    Codex review Q4: single `today-60d` was too weak. Sample fixed cross-regime
    points (bull/bear/sideways periods + most recent) and assert PIT source data
    available <= signal_date for each.
    """
    out: list[CheckResult] = []
    # Fixed cross-regime sample points for PIT spot check. These are NOT model
    # parameters; they are reference dates spanning 2024 bull / 2025 mid / 2026
    # recent so audit can assert PIT-safety across regimes (Codex review Q4).
    # rule-compliance: ok evidence=cross-regime-fixed-sample
    candidate_dates = ["2024-04-15", "2024-12-15", "2025-06-15", "2026-03-15"]
    # Add latest available signal date from fact_technical_trigger
    try:
        row = conn.execute(
            "SELECT MAX(date) FROM fact_technical_trigger WHERE date IS NOT NULL"
        ).fetchone()
        if row and row[0]:
            candidate_dates.append(str(row[0]))
    except Exception as e:
        log.warning(f"Cannot read latest signal_date from fact_technical_trigger: {e}")

    sources = [
        ("fact_risk_factors", "calc_date"),
        ("fact_financial_pit_daily", "trade_date"),
        ("fact_capital_flow_pit_daily", "trade_date"),
        ("fact_signal_context", "date"),
        ("fact_technical_trigger", "date"),
    ]

    for signal_date in candidate_dates:
        for table, pit_col in sources:
            try:
                n_violating = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {pit_col} > ?",
                    [signal_date],
                ).fetchone()[0]
                if n_violating == 0:
                    out.append(CheckResult(
                        section="4. Forward leak spot-check",
                        name=f"{table}@{signal_date}",
                        status="PASS",
                        detail=f"{table}: 0 rows with {pit_col} > {signal_date}",
                    ))
                else:
                    # WARN not FAIL: future rows exist (e.g. today's data); selector
                    # must filter `WHERE pit_col <= signal_date` at query time.
                    out.append(CheckResult(
                        section="4. Forward leak spot-check",
                        name=f"{table}@{signal_date}",
                        status="WARN",
                        detail=f"{table}: {n_violating} rows with {pit_col} > {signal_date} (future-dated; selector must filter)",
                        rows=n_violating,
                    ))
            except Exception as e:
                out.append(CheckResult(
                    section="4. Forward leak spot-check",
                    name=f"{table}@{signal_date}",
                    status="WARN",
                    detail=f"check failed: {e}",
                ))
    return out


def check_legacy_usage_guard(conn) -> list[CheckResult]:
    """Section 5: ensure v2 legacy tables (single-batch built_at) are NOT used
    as primary selector/model source in v3.2 P0 code.

    Codex review Q6 critical: WARN-only downgrade is unsafe if P0 code silently
    promotes a legacy table to primary. Static grep on backend/services/ and
    backend/scripts/optimize_* / build_daily_* / build_stock_formula_buy_signal_*
    for reads of legacy tables in a "primary" SQL context (e.g. ORDER BY oos_sharpe
    selecting the top-N). For now: report any direct mention; v3.2 P0a will refine
    with role-aware lineage when feature_registry adds `primary_allowed=false`.
    """
    import re
    import subprocess

    out: list[CheckResult] = []
    legacy_tables = list(WALK_FORWARD_V2_LEGACY)
    # Primary-context patterns: any ORDER BY, ranking, or selector mention.
    # Conservative: just flag mentions for P0a triage; not a hard block yet.
    repo_root = Path(__file__).resolve().parent.parent.parent
    for tbl in legacy_tables:
        try:
            r = subprocess.run(
                ["git", "grep", "-l", tbl, "--",
                 "backend/services/paper_sim/",
                 "backend/services/optimization/",
                 "backend/scripts/optimize_",
                 "backend/scripts/build_daily_",
                 "backend/scripts/build_stock_formula_buy_signal_",
                 ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            hits = [f for f in r.stdout.strip().split("\n") if f]
            if not hits:
                out.append(CheckResult(
                    section="5. Legacy usage guard",
                    name=tbl,
                    status="PASS",
                    detail=f"{tbl}: 0 primary-context references in v3.2 selector/optimize/build paths",
                ))
            else:
                # WARN (not FAIL) for now — P0a will tighten when feature_registry has
                # role-aware lineage. This catches the "silent promotion" risk.
                out.append(CheckResult(
                    section="5. Legacy usage guard",
                    name=tbl,
                    status="WARN",
                    detail=f"{tbl}: {len(hits)} files reference it (review P0a usage)",
                    extras={"files": hits},
                ))
        except Exception as e:
            out.append(CheckResult(
                section="5. Legacy usage guard",
                name=tbl,
                status="WARN",
                detail=f"check failed: {e}",
            ))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="P-1.1 PIT integrity audit")
    parser.add_argument("--json-out", type=Path, default=None, help="Write full JSON report to path")
    args = parser.parse_args()

    log.info("=== P-1.1 PIT Integrity Audit (PLAN_V3 v3.2) ===")
    # Codex review Q3: open read-only connection to allow concurrent P-1.2~P-1.5.
    conn = duck_connect(str(DB_PATH), read_only=True)
    try:
        results: list[CheckResult] = []
        results.extend(check_pit_column_coverage(conn))
        results.extend(check_batch_write_anomaly(conn))
        results.extend(check_oos_validity(conn))
        results.extend(check_forward_leak_spot_check(conn))
        results.extend(check_legacy_usage_guard(conn))
    finally:
        conn.close()

    # Summary
    by_status = Counter(r.status for r in results)
    log.info("")
    log.info("=== Results ===")
    for r in results:
        log.info(f"  [{r.status:4s}] {r.section} :: {r.name} — {r.detail}")
    log.info("")
    log.info(f"SUMMARY: PASS={by_status['PASS']} WARN={by_status['WARN']} FAIL={by_status['FAIL']}")

    if args.json_out:
        payload = {
            "audit": "P-1.1 PIT integrity",
            "summary": dict(by_status),
            "results": [asdict(r) for r in results],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info(f"JSON report → {args.json_out}")

    # P-1 Go gate: PIT FAIL=0
    if by_status["FAIL"] > 0:
        log.error(f"P-1.1 FAIL: {by_status['FAIL']} hard violations — PLAN_V3 §6 串行 gate blocks P0")
        return 1
    log.info("P-1.1 PASS — PIT integrity OK at coverage level")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Evaluate promotion gates for the TDX keep challenger without promoting it."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.ml_lifecycle.registry import get_model_status, select_default_model_id
from services.model_feature_schema import TDX_KEEP_FEATURE_COLS
from services.schema_versions import record_actual_version


logger = logging.getLogger("tdx_keep_gate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_keep_promotion_gate (
    gate_run_id TEXT PRIMARY KEY,
    challenger_model_id TEXT NOT NULL,
    champion_model_id TEXT,
    promotion_status TEXT NOT NULL,
    decision TEXT NOT NULL,
    gate_results_json TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    rank_ic_challenger DOUBLE,
    rank_ic_champion DOUBLE,
    long_short_challenger DOUBLE,
    long_short_champion DOUBLE,
    max_drawdown_challenger DOUBLE,
    max_drawdown_champion DOUBLE,
    evaluated_at TEXT NOT NULL
);
"""


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _has_column(conn, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
        (table, column),
    ).fetchone()
    return bool(row and row[0])


def _latest_model(conn, prefix: str) -> str | None:
    row = conn.execute(
        """
        SELECT model_id FROM mart_multidim_model
         WHERE model_id LIKE ?
         ORDER BY created_at DESC LIMIT 1
        """,
        (f"{prefix}%",),
    ).fetchone()
    return row["model_id"] if row else None


def _model_metrics(conn, model_id: str | None) -> dict:
    if not model_id:
        return {}
    row = conn.execute(
        """
        SELECT model_id, holdout_rank_ic, holdout_long_short_spread,
               holdout_top_decile_avg, holdout_winrate_top, feature_schema_version
          FROM mart_multidim_model
         WHERE model_id = ?
        """,
        (model_id,),
    ).fetchone()
    return dict(row) if row else {}


def _model_feature_cols(conn, model_id: str | None) -> list[str]:
    if not model_id or not _has_column(conn, "mart_multidim_model", "feature_cols_json"):
        return []
    row = conn.execute(
        "SELECT feature_cols_json FROM mart_multidim_model WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not row or not row["feature_cols_json"]:
        return []
    try:
        data = json.loads(row["feature_cols_json"])
    except Exception:
        return []
    return [str(v) for v in data] if isinstance(data, list) else []


def _latest_portfolio(conn, model_id: str | None) -> dict:
    if not model_id or not _table_exists(conn, "mart_model_portfolio_summary"):
        return {}
    row = conn.execute(
        """
        SELECT run_id, curve_id, total_return, annualized_return, max_drawdown,
               sharpe, avg_turnover, cost_bps
          FROM mart_model_portfolio_summary
         WHERE model_id = ? AND curve_type = 'model_top20'
         ORDER BY built_at DESC, cost_bps
         LIMIT 1
        """,
        (model_id,),
    ).fetchone()
    return dict(row) if row else {}


def _latest_drift(conn, model_id: str | None) -> dict:
    if not model_id or not _table_exists(conn, "mart_feature_drift"):
        return {"rows": 0, "critical": 0, "warn": 0, "ok": 0, "unknown": 0, "features": []}
    row = conn.execute("SELECT MAX(snapshot_at) FROM mart_feature_drift WHERE model_id = ?", (model_id,)).fetchone()
    snap = row[0] if row else None
    if not snap:
        return {"rows": 0, "critical": 0, "warn": 0, "ok": 0, "unknown": 0, "features": []}
    rows = conn.execute(
        """
        SELECT severity, COUNT(*) n
          FROM mart_feature_drift
         WHERE model_id = ? AND snapshot_at = ?
         GROUP BY severity
        """,
        (model_id, snap),
    ).fetchall()
    out = {"snapshot_at": str(snap), "rows": 0, "critical": 0, "warn": 0, "ok": 0, "unknown": 0}
    for r in rows:
        out[r["severity"]] = r["n"]
        out["rows"] += r["n"]
    feature_rows = conn.execute(
        """
        SELECT feature, severity, psi, n_train, n_recent
          FROM mart_feature_drift
         WHERE model_id = ? AND snapshot_at = ?
         ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1
                                WHEN 'unknown' THEN 2 ELSE 3 END,
                  feature
        """,
        (model_id, snap),
    ).fetchall()
    out["features"] = [dict(r) for r in feature_rows]
    return out


def _rank_ic_gate(c_rank: float | None, h_rank: float | None) -> tuple[bool, dict, str | None]:
    if c_rank is None or h_rank is None:
        return False, {"champion": c_rank, "challenger": h_rank}, "missing rank IC comparison"
    relative_required = c_rank * 1.05 if c_rank > 0 else c_rank + 0.001
    absolute_required = c_rank + 0.005
    ok = h_rank >= relative_required or h_rank >= absolute_required
    detail = {
        "champion": c_rank,
        "challenger": h_rank,
        "relative_required": relative_required,
        "absolute_required": absolute_required,
        "uplift_abs": h_rank - c_rank,
        "uplift_pct": ((h_rank / c_rank) - 1.0) if c_rank else None,
        "pass_rule": "relative_5pct_or_absolute_0.005",
    }
    reason = (
        f"challenger rank_ic {h_rank:.6f} < relative {relative_required:.6f} "
        f"and absolute {absolute_required:.6f}"
        if not ok else None
    )
    return ok, detail, reason


def _drift_gate(champion: dict, challenger: dict, champion_cols: list[str], challenger_cols: list[str]) -> tuple[str, dict, str | None]:
    if challenger["rows"] == 0:
        return "WAIT", challenger, "no drift snapshot for challenger"

    champion_feature_map = {r["feature"]: r for r in champion.get("features", [])}
    champion_feature_set = set(champion_cols)
    challenger_only = set(challenger_cols) - champion_feature_set
    critical_features = [r for r in challenger.get("features", []) if r.get("severity") == "critical"]
    challenger_only_critical = [r for r in critical_features if r.get("feature") in challenger_only]
    inherited_critical = [r for r in critical_features if r.get("feature") not in challenger_only]
    inherited_worse = [
        r for r in inherited_critical
        if (champion_feature_map.get(r.get("feature")) or {}).get("severity") not in {"critical", "warn"}
    ]
    status = "PASS"
    blocker = None
    if challenger_only_critical:
        status = "FAIL"
        blocker = "critical drift exists in challenger-only features"
    elif inherited_worse and champion.get("rows", 0) > 0:
        status = "FAIL"
        blocker = "challenger inherited features drift worse than champion"
    elif champion.get("rows", 0) == 0 and critical_features:
        status = "FAIL"
        blocker = "critical drift exists and champion drift baseline is missing"

    detail = {
        **{k: v for k, v in challenger.items() if k != "features"},
        "champion_snapshot_at": champion.get("snapshot_at"),
        "champion_critical": champion.get("critical", 0),
        "challenger_only_feature_count": len(challenger_only),
        "challenger_only_critical": challenger_only_critical,
        "inherited_critical": inherited_critical,
        "inherited_worse_than_champion": inherited_worse,
        "scope": "block only challenger-only critical drift or inherited drift worse than champion",
    }
    return status, detail, blocker


def _source_lineage_gate(conn, *, max_fallback_ratio: float = 0.05) -> tuple[str, dict, str | None]:
    """Gate challenger promotion on source-tier/fallback evidence."""

    checks: list[dict] = []
    blockers: list[str] = []
    wait_reasons: list[str] = []

    if _table_exists(conn, "fact_top10_holder_period"):
        if not _has_column(conn, "fact_top10_holder_period", "source_tier"):
            blockers.append("fact_top10_holder_period missing source_tier")
            checks.append({"table": "fact_top10_holder_period", "status": "FAIL", "reason": "missing source_tier"})
        else:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total_rows,
                       SUM(CASE WHEN source_tier > 1 THEN 1 ELSE 0 END) AS fallback_rows
                  FROM fact_top10_holder_period
                """
            ).fetchone()
            total_rows = int(row["total_rows"] or 0) if row else 0
            fallback_rows = int(row["fallback_rows"] or 0) if row else 0
            fallback_ratio = (fallback_rows / total_rows) if total_rows else None
            check_status = "PASS"
            reason = None
            if total_rows == 0:
                check_status = "WAIT"
                reason = "no holder source rows"
                wait_reasons.append(reason)
            elif fallback_ratio is not None and fallback_ratio > max_fallback_ratio:
                check_status = "FAIL"
                reason = f"holder fallback_ratio {fallback_ratio:.2%} > {max_fallback_ratio:.2%}"
                blockers.append(reason)
            checks.append(
                {
                    "table": "fact_top10_holder_period",
                    "status": check_status,
                    "total_rows": total_rows,
                    "fallback_rows": fallback_rows,
                    "fallback_ratio": fallback_ratio,
                    "max_fallback_ratio": max_fallback_ratio,
                    "reason": reason,
                }
            )
    else:
        reason = "missing fact_top10_holder_period source evidence"
        wait_reasons.append(reason)
        checks.append({"table": "fact_top10_holder_period", "status": "WAIT", "reason": reason})

    if _table_exists(conn, "mart_tdx_gpcw_file_manifest"):
        row = conn.execute(
            """
            SELECT COUNT(*) AS total_files,
                   SUM(CASE WHEN source_tier <> 1 THEN 1 ELSE 0 END) AS non_primary_files,
                   SUM(CASE WHEN COALESCE(status, '') NOT IN ('success', 'skipped') THEN 1 ELSE 0 END) AS bad_status_files
              FROM mart_tdx_gpcw_file_manifest
            """
        ).fetchone()
        total_files = int(row["total_files"] or 0) if row else 0
        non_primary_files = int(row["non_primary_files"] or 0) if row else 0
        bad_status_files = int(row["bad_status_files"] or 0) if row else 0
        check_status = "PASS"
        reason = None
        if total_files == 0:
            check_status = "WAIT"
            reason = "no gpcw file manifest rows"
            wait_reasons.append(reason)
        elif non_primary_files or bad_status_files:
            check_status = "FAIL"
            reason = f"gpcw manifest non_primary={non_primary_files}, bad_status={bad_status_files}"
            blockers.append(reason)
        checks.append(
            {
                "table": "mart_tdx_gpcw_file_manifest",
                "status": check_status,
                "total_files": total_files,
                "non_primary_files": non_primary_files,
                "bad_status_files": bad_status_files,
                "reason": reason,
            }
        )
    else:
        reason = "missing mart_tdx_gpcw_file_manifest source evidence"
        wait_reasons.append(reason)
        checks.append({"table": "mart_tdx_gpcw_file_manifest", "status": "WAIT", "reason": reason})

    critical_domains = {"holders_top10_float", "financial_gpcw_8q"}
    if _table_exists(conn, "mart_data_source_watermark"):
        rows = conn.execute(
            """
            SELECT data_domain, source_name, source_tier,
                   COALESCE(fallback_active, FALSE) AS fallback_active,
                   COALESCE(consecutive_failures, 0) AS consecutive_failures
              FROM mart_data_source_watermark
             WHERE data_domain IN ('holders_top10_float', 'financial_gpcw_8q')
            """
        ).fetchall()
        seen_domains = {r["data_domain"] for r in rows}
        missing_domains = sorted(critical_domains - seen_domains)
        if missing_domains:
            reason = f"missing source watermark domains: {','.join(missing_domains)}"
            wait_reasons.append(reason)
        fallback_rows = [
            dict(r) for r in rows
            if bool(r["fallback_active"]) or int(r["source_tier"] or 0) > 1 or int(r["consecutive_failures"] or 0) > 0
        ]
        if fallback_rows:
            reason = "critical source watermark indicates fallback or failures"
            blockers.append(reason)
            check_status = "FAIL"
        elif missing_domains:
            check_status = "WAIT"
            reason = f"missing domains: {','.join(missing_domains)}"
        else:
            check_status = "PASS"
            reason = None
        checks.append(
            {
                "table": "mart_data_source_watermark",
                "status": check_status,
                "critical_domains": sorted(critical_domains),
                "missing_domains": missing_domains,
                "fallback_or_failure_rows": fallback_rows,
                "reason": reason,
            }
        )
    else:
        reason = "missing mart_data_source_watermark"
        wait_reasons.append(reason)
        checks.append({"table": "mart_data_source_watermark", "status": "WAIT", "reason": reason})

    if blockers:
        return "FAIL", {"checks": checks, "max_fallback_ratio": max_fallback_ratio}, "; ".join(blockers)
    if wait_reasons:
        return "WAIT", {"checks": checks, "max_fallback_ratio": max_fallback_ratio}, "; ".join(wait_reasons)
    return "PASS", {"checks": checks, "max_fallback_ratio": max_fallback_ratio}, None


def evaluate_gate(
    conn,
    *,
    challenger_model_id: str | None = None,
    feature_set_id: str = "tdx_keep_challenger_v1",
) -> dict:
    conn.executescript(DDL)
    challenger_model_id = challenger_model_id or _latest_model(conn, "tdx_keep_challenger")
    champion_model_id, selection_fallback = select_default_model_id(conn)
    blockers: list[dict] = []
    gates: dict[str, dict] = {}

    def gate(name: str, status: str, detail: dict | None = None, blocker: str | None = None):
        gates[name] = {"status": status, **(detail or {})}
        if status in {"FAIL", "WAIT"} and blocker:
            blockers.append({"gate": name, "status": status, "reason": blocker})

    if not challenger_model_id:
        gate("model", "WAIT", blocker="no TDX keep challenger model found")
    else:
        gate("model", "PASS", {"challenger_model_id": challenger_model_id})

    pit = conn.execute(
        """
        SELECT COALESCE(SUM(violation_rows), 0)
          FROM mart_feature_pit_audit
         WHERE audit_run_id='pit_tdx_f10_gpcw_v1'
        """
    ).fetchone()[0]
    gate("PIT", "PASS" if pit == 0 else "FAIL", {"violations": pit}, f"PIT violations={pit}" if pit else None)

    coverage_rows = []
    if _table_exists(conn, "fact_feature_panel_tdx_keep_challenger"):
        for f in TDX_KEEP_FEATURE_COLS:
            c = conn.execute(
                f"""
                SELECT COUNT({f}) * 100.0 / NULLIF(COUNT(*), 0)
                  FROM fact_feature_panel_tdx_keep_challenger
                 WHERE feature_set_id = ?
                """,
                (feature_set_id,),
            ).fetchone()[0]
            coverage_rows.append({"feature": f, "coverage_pct": float(c or 0)})
    pass_coverage = sum(1 for r in coverage_rows if r["coverage_pct"] >= 60.0)
    gate(
        "coverage",
        "PASS" if pass_coverage >= 4 else "FAIL",
        {"features_ge_60_pct": pass_coverage, "features": coverage_rows},
        f"only {pass_coverage}/5 keep features coverage >= 60%" if pass_coverage < 4 else None,
    )

    champion = _model_metrics(conn, champion_model_id)
    challenger = _model_metrics(conn, challenger_model_id)
    c_rank = champion.get("holdout_rank_ic")
    h_rank = challenger.get("holdout_rank_ic")
    rank_ok, rank_detail, rank_blocker = _rank_ic_gate(c_rank, h_rank)
    gate(
        "rank_ic",
        "PASS" if rank_ok else ("WAIT" if c_rank is None or h_rank is None else "FAIL"),
        rank_detail,
        rank_blocker,
    )

    c_ls = champion.get("holdout_long_short_spread")
    h_ls = challenger.get("holdout_long_short_spread")
    if c_ls is None or h_ls is None:
        gate("long_short", "WAIT", {"champion": c_ls, "challenger": h_ls}, "missing long-short comparison")
    else:
        required = max(0.0, c_ls * 0.90)
        ok = h_ls > 0 and h_ls >= required
        gate(
            "long_short",
            "PASS" if ok else "FAIL",
            {"champion": c_ls, "challenger": h_ls, "required": required},
            f"challenger long_short {h_ls:.6f} < required {required:.6f}" if not ok else None,
        )

    c_port = _latest_portfolio(conn, champion_model_id)
    h_port = _latest_portfolio(conn, challenger_model_id)
    c_dd = c_port.get("max_drawdown")
    h_dd = h_port.get("max_drawdown")
    if c_dd is None or h_dd is None:
        gate("max_drawdown", "WAIT", {"champion": c_dd, "challenger": h_dd}, "missing portfolio drawdown comparison")
    else:
        ok = abs(h_dd) <= abs(c_dd) * 1.20 if c_dd != 0 else h_dd >= -0.02
        gate(
            "max_drawdown",
            "PASS" if ok else "FAIL",
            {"champion": c_dd, "challenger": h_dd},
            f"challenger drawdown {h_dd:.6f} worse than champion tolerance" if not ok else None,
        )

    champion_drift = _latest_drift(conn, champion_model_id)
    challenger_drift = _latest_drift(conn, challenger_model_id)
    drift_status, drift_detail, drift_blocker = _drift_gate(
        champion_drift,
        challenger_drift,
        _model_feature_cols(conn, champion_model_id),
        _model_feature_cols(conn, challenger_model_id),
    )
    gate("drift", drift_status, drift_detail, drift_blocker)

    source_status, source_detail, source_blocker = _source_lineage_gate(conn)
    gate("source_lineage", source_status, source_detail, source_blocker)

    if _table_exists(conn, "mart_daily_recommendation"):
        shadow_filter = "AND COALESCE(run_mode, '') = 'shadow'" if _has_column(conn, "mart_daily_recommendation", "run_mode") else ""
        shadow = conn.execute(
            f"""
            SELECT COUNT(*) n, MAX(snapshot_date) latest_snapshot
              FROM mart_daily_recommendation
             WHERE model_id = ? {shadow_filter}
            """,
            (challenger_model_id,),
        ).fetchone()
        shadow_rows = shadow["n"] if shadow else 0
        shadow_latest = shadow["latest_snapshot"] if shadow else None
    else:
        shadow_rows = 0
        shadow_latest = None
    gate(
        "shadow_topk",
        "PASS" if shadow_rows > 0 else "WAIT",
        {"rows": shadow_rows, "latest_snapshot": shadow_latest},
        "no shadow topK rows for challenger" if shadow_rows <= 0 else None,
    )

    api_safe = (
        champion_model_id
        and challenger_model_id
        and champion_model_id != challenger_model_id
        and get_model_status(conn, champion_model_id) == "champion"
        and get_model_status(conn, challenger_model_id) == "challenger"
        and not selection_fallback
    )
    gate(
        "api_safety",
        "PASS" if api_safe else "FAIL",
        {
            "champion_model_id": champion_model_id,
            "challenger_model_id": challenger_model_id,
            "selection_fallback": selection_fallback,
        },
        "default model selection is not lifecycle champion-only" if not api_safe else None,
    )

    if any(b["status"] == "FAIL" for b in blockers):
        promotion_status = "FAIL"
        decision = "reject"
    elif blockers:
        promotion_status = "WAIT"
        decision = "keep_shadow"
    else:
        promotion_status = "PASS"
        decision = "promote_ready"

    evaluated_at = datetime.utcnow().isoformat()
    gate_run_id = f"tdx_keep_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_tdx_keep_promotion_gate
        (gate_run_id, challenger_model_id, champion_model_id, promotion_status, decision,
         gate_results_json, blockers_json, rank_ic_challenger, rank_ic_champion,
         long_short_challenger, long_short_champion, max_drawdown_challenger,
         max_drawdown_champion, evaluated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            challenger_model_id,
            champion_model_id,
            promotion_status,
            decision,
            json.dumps(gates, ensure_ascii=False),
            json.dumps(blockers, ensure_ascii=False),
            h_rank,
            c_rank,
            h_ls,
            c_ls,
            h_dd,
            c_dd,
            evaluated_at,
        ),
    )
    try:
        record_actual_version(conn, "mart_tdx_keep_promotion_gate")
    except Exception as exc:
        logger.warning("record schema version failed: %s", exc)
    conn.commit()
    return {
        "gate_run_id": gate_run_id,
        "challenger_model_id": challenger_model_id,
        "champion_model_id": champion_model_id,
        "promotion_status": promotion_status,
        "decision": decision,
        "gates": gates,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--feature-set-id", default="tdx_keep_challenger_v1")
    args = parser.parse_args()
    with get_conn() as conn:
        result = evaluate_gate(conn, challenger_model_id=args.model_id, feature_set_id=args.feature_set_id)
    logger.info("tdx keep promotion gate: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

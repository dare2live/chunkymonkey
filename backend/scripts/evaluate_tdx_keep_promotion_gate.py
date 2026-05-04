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
        return {"rows": 0, "critical": 0, "warn": 0, "ok": 0, "unknown": 0}
    row = conn.execute("SELECT MAX(snapshot_at) FROM mart_feature_drift WHERE model_id = ?", (model_id,)).fetchone()
    snap = row[0] if row else None
    if not snap:
        return {"rows": 0, "critical": 0, "warn": 0, "ok": 0, "unknown": 0}
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
    return out


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
    if c_rank is None or h_rank is None:
        gate("rank_ic", "WAIT", {"champion": c_rank, "challenger": h_rank}, "missing rank IC comparison")
    else:
        required = max(c_rank * 1.05, c_rank + 0.005)
        ok = h_rank >= required
        gate(
            "rank_ic",
            "PASS" if ok else "FAIL",
            {"champion": c_rank, "challenger": h_rank, "required": required},
            f"challenger rank_ic {h_rank:.6f} < required {required:.6f}" if not ok else None,
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

    drift = _latest_drift(conn, challenger_model_id)
    if drift["rows"] == 0:
        gate("drift", "WAIT", drift, "no drift snapshot for challenger")
    else:
        gate("drift", "PASS" if drift["critical"] == 0 else "FAIL", drift, "critical drift exists" if drift["critical"] else None)

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

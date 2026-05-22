"""BestChoice read-only service for the v3 BestChoice tab.

Read mart tables imported via backend/scripts/import_bestchoice_phase{1,3}_*.py
and exposes high-level summaries + tabular views for the UI tab.

DO NOT write — challenger data only. main project champion is untouched.
"""

from __future__ import annotations

from typing import Any

# rule-compliance: ok evidence=BestChoice plan §5 Phase 1 run_id naming
DEFAULT_RUN_ID = "bestchoice_formula_optuna_20260521_v1"
DEFAULT_CHALLENGER_MODEL_ID = "bestchoice_formula_challenger_v1"
DEFAULT_ENSEMBLE_MODEL_ID = "ensemble_v4_bestchoice_v1"


def get_overview(conn, run_id: str = DEFAULT_RUN_ID) -> dict[str, Any]:
    """High-level summary: candidate count, stocks, formulas, avg score, latest KPI."""
    r = conn.execute(
        """
        SELECT COUNT(*) AS n_candidates,
               COUNT(DISTINCT stock_code) AS n_stocks,
               COUNT(DISTINCT formula_id) AS n_formulas,
               COUNT(DISTINCT variant_id) AS n_variants,
               ROUND(AVG(score), 2) AS avg_score,
               ROUND(MIN(score), 2) AS min_score,
               ROUND(MAX(score), 2) AS max_score,
               ROUND(AVG(win_rate), 4) AS avg_win_rate,
               ROUND(AVG(avg_ret), 4) AS avg_avg_ret,
               MIN(source_data_latest_date) AS data_latest_date
          FROM mart_stock_formula_optuna_bestchoice_v1
         WHERE run_id = ?
        """,
        [run_id],
    ).fetchone()
    if not r:
        return {"empty": True, "run_id": run_id}

    out: dict[str, Any] = {
        "run_id": run_id,
        "n_candidates": r[0],
        "n_stocks": r[1],
        "n_formulas": r[2],
        "n_variants": r[3],
        "avg_score": r[4],
        "score_range": [r[5], r[6]],
        "avg_win_rate": r[7],
        "avg_avg_ret": r[8],
        "data_latest_date": str(r[9]) if r[9] else None,
    }

    # Latest paper_sim KPI compare (challenger + ensemble vs baseline)
    kpi_rows = conn.execute(
        """
        SELECT model_label, model_id,
               ROUND(sharpe, 4) AS sharpe,
               ROUND(ann_ret, 4) AS ann_ret,
               ROUND(max_dd, 4) AS max_dd,
               ROUND(monthly_win_rate, 4) AS win_rate,
               ROUND(rank_ic, 5) AS rank_ic,
               period_start, period_end,
               built_at
          FROM mart_paper_sim_lambdamart_v6_kpi_compare
         WHERE model_id IN (?, ?)
            OR model_label = 'v4_baseline'
         ORDER BY built_at DESC
         LIMIT 10
        """,
        [DEFAULT_CHALLENGER_MODEL_ID, DEFAULT_ENSEMBLE_MODEL_ID],
    ).fetchall()
    out["paper_sim_kpi"] = [
        {
            "model_label": r[0],
            "model_id": r[1],
            "sharpe": r[2],
            "ann_ret": r[3],
            "max_dd": r[4],
            "win_rate": r[5],
            "rank_ic": r[6],
            "period_start": str(r[7]) if r[7] else None,
            "period_end": str(r[8]) if r[8] else None,
            "built_at": str(r[9]) if r[9] else None,
        }
        for r in kpi_rows
    ]
    return out


def get_top_candidates(conn, run_id: str = DEFAULT_RUN_ID, limit: int = 50) -> list[dict[str, Any]]:
    """Top BC candidates ranked by score."""
    rows = conn.execute(
        """
        SELECT stock_code, formula_id, variant_id, sell_rule, holding_days,
               ROUND(score, 2) AS score,
               ROUND(win_rate, 4) AS win_rate,
               ROUND(avg_ret, 4) AS avg_ret,
               ROUND(avg_dd, 4) AS avg_dd,
               signal_count,
               params_json
          FROM mart_stock_formula_optuna_bestchoice_v1
         WHERE run_id = ?
         ORDER BY score DESC
         LIMIT ?
        """,
        [run_id, int(limit)],
    ).fetchall()
    return [
        {
            "stock_code": r[0],
            "formula_id": r[1],
            "variant_id": r[2],
            "sell_rule": r[3],
            "holding_days": r[4],
            "score": r[5],
            "win_rate": r[6],
            "avg_ret": r[7],
            "avg_dd": r[8],
            "signal_count": r[9],
            "params_json": r[10],
        }
        for r in rows
    ]


def get_daily_picks(conn, run_id: str = DEFAULT_RUN_ID, signal_date: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Daily picks for a given signal_date (defaults to latest)."""
    if signal_date is None:
        r = conn.execute(
            "SELECT MAX(signal_date) FROM mart_daily_formula_candidate_bestchoice_v1 WHERE run_id = ?",
            [run_id],
        ).fetchone()
        signal_date = str(r[0]) if r and r[0] else None
        if not signal_date:
            return {"signal_date": None, "picks": []}
    rows = conn.execute(
        """
        SELECT signal_date, buy_date, stock_code, formula_id, sell_rule, holding_days,
               ROUND(confidence_score, 2) AS confidence_score, rank_in_date
          FROM mart_daily_formula_candidate_bestchoice_v1
         WHERE run_id = ?
           AND signal_date = ?
         ORDER BY rank_in_date
         LIMIT ?
        """,
        [run_id, signal_date, int(limit)],
    ).fetchall()
    return {
        "signal_date": signal_date,
        "picks": [
            {
                "signal_date": str(r[0]),
                "buy_date": str(r[1]),
                "stock_code": r[2],
                "formula_id": r[3],
                "sell_rule": r[4],
                "holding_days": r[5],
                "confidence_score": r[6],
                "rank_in_date": r[7],
            }
            for r in rows
        ],
    }


def get_complementarity(conn, challenger_run: str | None = None) -> dict[str, Any]:
    """Phase 4 complementarity: BC vs baseline picks overlap.

    Returns BC unique stocks / baseline unique / shared / same-day same-stock.
    """
    # Find latest BC challenger sim_run_id + v4_baseline sim_run_id
    r = conn.execute(
        """
        SELECT MAX(comparison_id)
          FROM mart_paper_sim_lambdamart_v6_kpi_compare
         WHERE model_id = ?
        """,
        [DEFAULT_CHALLENGER_MODEL_ID],
    ).fetchone()
    cmp_id = r[0] if r and r[0] else None
    if not cmp_id:
        return {"empty": True}

    bc_run = f"{cmp_id}_lambdamart_v6"
    bl_run = f"{cmp_id}_v4_baseline"

    r2 = conn.execute(
        f"""
        WITH bc AS (
            SELECT DISTINCT SUBSTR(position_id, 1, 6) AS stock, CAST(date AS DATE) AS d
              FROM fact_paper_sim_trade
             WHERE sim_run_id = ? AND type = 'BUY'
        ),
        bl AS (
            SELECT DISTINCT SUBSTR(position_id, 1, 6) AS stock, CAST(date AS DATE) AS d
              FROM fact_paper_sim_trade
             WHERE sim_run_id = ? AND type = 'BUY'
        )
        SELECT (SELECT COUNT(DISTINCT stock) FROM bc) AS bc_unique,
               (SELECT COUNT(DISTINCT stock) FROM bl) AS bl_unique,
               (SELECT COUNT(*) FROM bc) AS bc_picks,
               (SELECT COUNT(*) FROM bl) AS bl_picks,
               (SELECT COUNT(*) FROM (SELECT DISTINCT stock FROM bc INTERSECT SELECT DISTINCT stock FROM bl)) AS shared_stocks,
               (SELECT COUNT(*) FROM (SELECT bc.stock FROM bc INNER JOIN bl ON bc.stock = bl.stock AND bc.d = bl.d)) AS same_day_same_stock
        """,
        [bc_run, bl_run],
    ).fetchone()
    return {
        "comparison_id": cmp_id,
        "bc_unique_stocks": r2[0],
        "baseline_unique_stocks": r2[1],
        "bc_picks": r2[2],
        "baseline_picks": r2[3],
        "shared_stocks": r2[4],
        "same_day_same_stock": r2[5],
        "overlap_pct": round(r2[4] / r2[1] * 100, 2) if r2[1] else 0,
    }

#!/usr/bin/env python3
"""Audit portfolio_sizer profile attrition against the latest recommendation pool."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date as _date, timedelta
from typing import Any

from services.db import get_conn
from services.portfolio_sizer.attrition import summarize_profile_attrition
from services.portfolio_sizer.profiles import PROFILES


def _bin_sql(col: str, bins: list[tuple[float, float, str]]) -> str:
    cases = " ".join(
        f"WHEN {col} IS NOT NULL AND {col} >= {lo} AND {col} < {hi} THEN '{label}'"
        for lo, hi, label in bins
    )
    return f"CASE {cases} ELSE '?' END"


def _render_selected_examples(selected_examples: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"  - {row['stock_code']} {row['formula_variant']} "
        f"tier={row['match_tier']} hp={row['holding_days']} "
        f"n={row['n_signals']} wilson={row['wilson_win']} score={row['score']}"
        for row in selected_examples
    )


def load_recommendation_candidates(conn: Any, signal_date: str) -> list[dict[str, Any]]:
    VOL_BINS = [(0, 0.7, "缩量"), (0.7, 1.3, "平量"), (1.3, 2.0, "温量"), (2.0, 99, "爆量")]
    AMT_BINS = [(0, 0.7, "额减"), (0.7, 1.3, "额平"), (1.3, 2.0, "额温"), (2.0, 99, "额爆")]
    P60_BINS = [(0, 0.65, "深底"), (0.65, 0.85, "中位"), (0.85, 0.97, "高位"), (0.97, 99, "新高")]

    rows = conn.execute(
        f"""
        WITH today_signals AS (
          SELECT t.stock_code, t.formula_id, t.formula_variant, t.strength,
                 {_bin_sql('c.vol_r20', VOL_BINS)}      AS vol_bin,
                 {_bin_sql('c.amt_r20', AMT_BINS)}      AS amt_bin,
                 {_bin_sql('c.price_pos_60d', P60_BINS)} AS p60_bin,
                 COALESCE(c.technical_stage, '?')       AS stage_bin
            FROM fact_technical_trigger t
            LEFT JOIN fact_signal_context c
              ON c.stock_code = t.stock_code AND c.date = t.date
           WHERE t.date = ?
        ),
        stage_pit_exact AS (
          SELECT *
          FROM (
            SELECT p.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY p.stock_code, p.formula_id, p.formula_variant, p.stage_filter
                     ORDER BY CAST(p.cutoff_date AS DATE) DESC,
                              p.oos_sharpe DESC NULLS LAST,
                              p.oos_n_traded DESC NULLS LAST
                   ) AS rn
              FROM mart_per_stock_stage_strategy_optimal_pit p
             WHERE CAST(p.cutoff_date AS DATE) <= CAST(? AS DATE)
               AND COALESCE(p.oos_n_traded, p.n_traded, 0) >= 3
          )
          WHERE rn = 1
        ),
        stage_pit_formula AS (
          SELECT *
          FROM (
            SELECT p.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY p.stock_code, p.formula_id, p.formula_variant
                     ORDER BY CAST(p.cutoff_date AS DATE) DESC,
                              p.oos_sharpe DESC NULLS LAST,
                              p.oos_n_traded DESC NULLS LAST
                   ) AS rn
              FROM mart_per_stock_stage_strategy_optimal_pit p
             WHERE CAST(p.cutoff_date AS DATE) <= CAST(? AS DATE)
               AND COALESCE(p.oos_n_traded, p.n_traded, 0) >= 3
          )
          WHERE rn = 1
        )
        SELECT ts.stock_code, ts.formula_id, ts.formula_variant,
               ts.vol_bin, ts.amt_bin, ts.p60_bin, ts.stage_bin,
               COALESCE(sopt.holding_days, sfopt.holding_days, opt.optimal_hp) AS holding_days,
               COALESCE(sopt.oos_n_traded, sopt.n_traded,
                        sfopt.oos_n_traded, sfopt.n_traded, opt.n_traded)      AS n_signals,
               COALESCE(sopt.oos_win_rate, sopt.win_rate,
                        sfopt.oos_win_rate, sfopt.win_rate, opt.win_rate)      AS win_rate,
               COALESCE(sopt.oos_avg_ret, sopt.avg_ret,
                        sfopt.oos_avg_ret, sfopt.avg_ret, opt.avg_ret)         AS avg_ret,
               COALESCE(opt.avg_max_dd, sopt.optimal_stop_pct, sfopt.optimal_stop_pct) AS avg_dd,
               COALESCE(sopt.oos_sharpe, sopt.sharpe,
                        sfopt.oos_sharpe, sfopt.sharpe, opt.sharpe)            AS sharpe,
               COALESCE(opt.calmar, 0) AS calmar,
               CASE WHEN sopt.stock_code IS NOT NULL THEN 'stage_pit'
                    WHEN sfopt.stock_code IS NOT NULL THEN 'stage_pit_formula_fallback'
                    ELSE 'cross_stage_fallback' END                          AS match_tier,
              COALESCE(sopt.optimal_stop_pct,     sfopt.optimal_stop_pct,     opt.optimal_stop_pct)     AS optimal_stop_pct,
              COALESCE(sopt.optimal_target_pct,   sfopt.optimal_target_pct,   opt.optimal_target_pct)   AS optimal_target_pct,
              COALESCE(sopt.optimal_trailing_pct, sfopt.optimal_trailing_pct, opt.optimal_trailing_pct) AS optimal_trailing_pct,
               p.fundamental_stage, p.latest_close,
               COALESCE(sf.survey_bin, '冷')     AS survey_bin,
               COALESCE(sf.survey_count_60d, 0)  AS survey_count_60d
          FROM today_signals ts
          LEFT JOIN stage_pit_exact sopt
            ON sopt.stock_code      = ts.stock_code
           AND sopt.formula_id      = ts.formula_id
           AND sopt.formula_variant = ts.formula_variant
           AND sopt.stage_filter    = ts.stage_bin
           AND abs(COALESCE(sopt.oos_avg_ret, sopt.avg_ret, 0)) <= 0.5
           AND sopt.optimal_stop_pct >= -0.5
           AND abs(COALESCE(sopt.oos_sharpe, sopt.sharpe, 0)) <= 10
          LEFT JOIN stage_pit_formula sfopt
            ON sfopt.stock_code      = ts.stock_code
           AND sfopt.formula_id      = ts.formula_id
           AND sfopt.formula_variant = ts.formula_variant
           AND sopt.stock_code IS NULL
           AND abs(COALESCE(sfopt.oos_avg_ret, sfopt.avg_ret, 0)) <= 0.5
           AND sfopt.optimal_stop_pct >= -0.5
           AND abs(COALESCE(sfopt.oos_sharpe, sfopt.sharpe, 0)) <= 10
          LEFT JOIN mart_per_stock_strategy_optimal opt
            ON opt.stock_code      = ts.stock_code
           AND opt.formula_id      = ts.formula_id
           AND opt.formula_variant = ts.formula_variant
           AND abs(opt.avg_ret) <= 0.5 AND opt.avg_max_dd >= -0.5
           AND abs(opt.sharpe) <= 10
          LEFT JOIN mart_stock_picture_daily p
            ON p.stock_code = ts.stock_code
           AND p.snapshot_date = (SELECT MAX(snapshot_date) FROM mart_stock_picture_daily)
          LEFT JOIN mart_stock_survey_features sf
            ON sf.stock_code = ts.stock_code
           AND sf.as_of_date = ?
         WHERE (sopt.stock_code IS NOT NULL OR opt.stock_code IS NOT NULL)
        """,
        [signal_date, signal_date, signal_date, signal_date],
    ).fetchall()

    candidates = []
    for r in rows:
        (
            sc, fid, fvar, vb, ab, pb, sb,
            hd, n, win, ret, dd, sh, cal, tier,
            opt_stop, opt_target, opt_trail,
            fund, close, survey_bin, survey_count_60d,
        ) = r
        candidates.append(
            {
                "stock_code": sc,
                "formula_id": fid,
                "formula_variant": fvar,
                "vol_bin": vb,
                "amt_bin": ab,
                "price_pos_bin": pb,
                "stage_bin": sb,
                "match_tier": tier,
                "holding_days": int(hd) if hd is not None else None,
                "n_signals": int(n) if n is not None else 0,
                "win_rate": float(win) if win is not None else None,
                "avg_ret": float(ret) if ret is not None else None,
                "avg_dd": float(dd) if dd is not None else None,
                "sharpe": float(sh) if sh is not None else None,
                "calmar": float(cal) if cal is not None else None,
                "fundamental_stage": fund,
                "signal_close": float(close) if close is not None else None,
                "optimal_stop_pct": float(opt_stop) if opt_stop is not None else None,
                "optimal_target_pct": float(opt_target) if opt_target is not None else None,
                "optimal_trailing_pct": float(opt_trail) if opt_trail is not None else None,
                "survey_bin": survey_bin,
                "survey_count_60d": int(survey_count_60d or 0),
            }
        )
    return candidates


def _latest_signal_date(conn: Any) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM fact_technical_trigger").fetchone()
    return row[0] if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit portfolio_sizer profile attrition")
    parser.add_argument("--date", default=None, help="signal_date, default latest fact_technical_trigger date")
    parser.add_argument("--profiles", nargs="+", default=None, help="only audit selected profile ids")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--max-examples", type=int, default=5)
    args = parser.parse_args()

    conn = get_conn()
    try:
        signal_date = args.date or _latest_signal_date(conn)
        if not signal_date:
            raise SystemExit("no fact_technical_trigger data found")

        candidates = load_recommendation_candidates(conn, signal_date)
        raw_tier_counts = Counter(row.get("match_tier") or "unknown" for row in candidates)

        profile_ids = args.profiles or ["short", "mid", "long"]
        profiles = []
        for profile_id in profile_ids:
            profile = PROFILES.get(profile_id)
            if profile is None:
                raise SystemExit(f"unknown profile: {profile_id}")
            profiles.append(summarize_profile_attrition(candidates, profile, max_examples=args.max_examples))

        result = {
            "signal_date": signal_date,
            "raw_candidates": len(candidates),
            "raw_match_tiers": dict(raw_tier_counts),
            "profiles": profiles,
        }

        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        print("# Portfolio Sizer Profile Attrition")
        print(f"- signal_date: {signal_date}")
        print(f"- raw_candidates: {len(candidates)}")
        print(f"- raw_match_tiers: {dict(raw_tier_counts)}")
        print()
        for profile in profiles:
            print(f"## {profile['profile_id']} | {profile['label']}")
            print(f"- input_rows: {profile['input_rows']}")
            print(f"- stage_reached: {profile['stage_reached']}")
            print(f"- fail_reasons: {profile['fail_reasons']}")
            print(f"- fail_reasons_by_match_tier: {profile['fail_reasons_by_match_tier']}")
            print(f"- after_filter_rows: {profile['after_filter_rows']}")
            print(f"- selected_rows: {profile['selected_rows']}")
            print(f"- selected_match_tiers: {profile['selected_match_tiers']}")
            if profile["selected_examples"]:
                print("- selected_examples:")
                print(_render_selected_examples(profile["selected_examples"]))
            print()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

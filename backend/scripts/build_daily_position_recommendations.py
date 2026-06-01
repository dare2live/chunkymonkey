"""Phase η++ — 每日 T+1 仓位推荐 (3 risk profile).

数据流:
  1. 找今日触发的所有 (stock × formula × variant) 信号 (fact_technical_trigger.date=T)
     以及 MACD active-state 诊断候选 (mart_macd_state_history)
  2. 拿今日 fact_signal_context (vol_bin/amt_bin/p60_bin/stage)
  3. LOOKUP mart_stock_formula_optuna_v2 找该股该 variant 5 维桶下最佳 (is_best_hd=True)
  4. 拿今日 mart_stock_picture_daily.fundamental_stage 做风险过滤
  5. 拿今日 close (来自 mart_stock_picture_daily.latest_close)
  6. 对每个 profile (short/mid/long):
     - 调 sizing.rank_and_size() → 出 Wilson + Kelly + position_pct
     - 写 mart_daily_position_recommendation
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date as _date, timedelta
from typing import Any

from services.db import get_conn
from services.portfolio_sizer.profiles import PROFILES, get_profile, list_profiles
from services.portfolio_sizer.sizing import rank_and_size


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_daily_position_recommendations")


DDL = """
CREATE TABLE IF NOT EXISTS mart_daily_position_recommendation (
    signal_date           TEXT NOT NULL,
    buy_date              TEXT NOT NULL,
    profile_id            TEXT NOT NULL,         -- short / mid / long
    rank_in_profile       INTEGER NOT NULL,
    stock_code            TEXT NOT NULL,
    -- 信号触发信息
    formula_id            TEXT,
    formula_variant       TEXT,
    vol_bin               TEXT,
    amt_bin               TEXT,
    price_pos_bin         TEXT,
    stage_bin             TEXT,
    fundamental_stage     TEXT,
    match_tier            TEXT,                  -- 'A_bucket' / 'B_stock_agg' (匹配层级)
    -- Phase η++++ sentiment 因子 (long profile 启用, 通过 factor_registry 控制)
    survey_bin            TEXT,                  -- 冷/温/热/狂
    survey_count_60d      INTEGER,               -- 60 日调研次数 (raw 值)
    sentiment_mult        REAL,                  -- sizing 实际应用的 score 乘子
    sentiment_trace       TEXT,                  -- 各因子贡献明细 (供调试)
    -- 历史 metrics (来源 mart_stock_formula_optuna_v2)
    n_signals             INTEGER,
    raw_win_rate          REAL,
    wilson_win_rate       REAL,                  -- 修正后
    avg_ret               REAL,
    avg_dd                REAL,
    sharpe                REAL,
    calmar                REAL,
    -- 仓位输出
    kelly_f               REAL,
    position_pct          REAL,                  -- 最终建议仓位 (0-1)
    confidence_tier       INTEGER,               -- 1/2/3
    score                 REAL,
    -- 交易计划 + Phase ζ 寻优明细 (用户能看到 "是寻优还是默认")
    holding_days          INTEGER,
    optimal_stop_pct      REAL,         -- 每股 Optuna 寻优出的最佳止损%
    optimal_target_pct    REAL,         -- 每股 Optuna 寻优出的最佳止盈%
    optimal_trailing_pct  REAL,         -- 每股 Optuna 寻优出的最佳 trailing%
    signal_close_price    REAL,
    buy_price             REAL,
    sell_target_price     REAL,
    stop_price            REAL,
    trailing_pct          REAL,
    built_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_date, profile_id, stock_code, formula_id, formula_variant)
);
CREATE INDEX IF NOT EXISTS idx_mdpr_date    ON mart_daily_position_recommendation(signal_date);
CREATE INDEX IF NOT EXISTS idx_mdpr_profile ON mart_daily_position_recommendation(profile_id, rank_in_profile);

CREATE TABLE IF NOT EXISTS mart_daily_position_recommendation_pit_diagnostic (
    signal_date              TEXT NOT NULL,
    profile_id               TEXT NOT NULL,
    rank_in_profile          INTEGER NOT NULL,
    stock_code               TEXT NOT NULL,
    formula_id               TEXT,
    formula_variant          TEXT,
    stage_bin                TEXT,
    match_tier               TEXT,
    pit_exact_stage_rows     INTEGER NOT NULL DEFAULT 0,
    pit_same_formula_rows    INTEGER NOT NULL DEFAULT 0,
    pit_same_stock_rows      INTEGER NOT NULL DEFAULT 0,
    latest_pit_cutoff_date   TEXT,
    missing_reason           TEXT NOT NULL,
    governance_reject_count   INTEGER NOT NULL DEFAULT 0,
    governance_latest_reason  TEXT,
    governance_latest_rejected_at TEXT,
    built_at                 TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (signal_date, profile_id, stock_code, formula_id, formula_variant)
);
CREATE INDEX IF NOT EXISTS idx_mdpr_pit_diag_date
    ON mart_daily_position_recommendation_pit_diagnostic(signal_date);
"""


def _ensure_pit_diagnostic_columns(conn) -> None:
    existing = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info('mart_daily_position_recommendation_pit_diagnostic')"
        ).fetchall()
    }
    if "governance_reject_count" not in existing:
        conn.execute(
            """
            ALTER TABLE mart_daily_position_recommendation_pit_diagnostic
            ADD COLUMN governance_reject_count INTEGER
            """
        )
        conn.execute(
            """
            UPDATE mart_daily_position_recommendation_pit_diagnostic
               SET governance_reject_count = 0
             WHERE governance_reject_count IS NULL
            """
        )
    if "governance_latest_reason" not in existing:
        conn.execute(
            """
            ALTER TABLE mart_daily_position_recommendation_pit_diagnostic
            ADD COLUMN governance_latest_reason TEXT
            """
        )
    if "governance_latest_rejected_at" not in existing:
        conn.execute(
            """
            ALTER TABLE mart_daily_position_recommendation_pit_diagnostic
            ADD COLUMN governance_latest_rejected_at TEXT
            """
        )


def _build_pit_diagnostic_rows(conn, signal_date: str, all_rows: list[tuple]) -> list[tuple]:
    if not all_rows:
        return []

    inputs = [
        (
            signal_date,
            r[2],   # profile_id
            r[3],   # rank_in_profile
            r[4],   # stock_code
            r[5],   # formula_id
            r[6],   # formula_variant
            r[10],  # stage_bin
            r[12],  # match_tier
        )
        for r in all_rows
    ]
    conn.execute("DROP TABLE IF EXISTS __mdpr_pit_diag_input")
    conn.execute("""
        CREATE TEMP TABLE __mdpr_pit_diag_input (
            signal_date TEXT,
            profile_id TEXT,
            rank_in_profile INTEGER,
            stock_code TEXT,
            formula_id TEXT,
            formula_variant TEXT,
            stage_bin TEXT,
            match_tier TEXT
        )
    """)
    conn.executemany(
        """INSERT INTO __mdpr_pit_diag_input
           (signal_date, profile_id, rank_in_profile, stock_code, formula_id,
            formula_variant, stage_bin, match_tier)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        inputs,
    )
    rows = conn.execute("""
        WITH qualified_pit AS (
          SELECT stock_code, formula_id, formula_variant, stage_filter, cutoff_date
            FROM mart_per_stock_stage_strategy_optimal_pit
           WHERE CAST(cutoff_date AS DATE) <= CAST(? AS DATE)
             AND COALESCE(oos_n_traded, n_traded, 0) >= 3
             AND abs(COALESCE(oos_avg_ret, avg_ret, 0)) <= 0.5
             AND optimal_stop_pct >= -0.5
             AND abs(COALESCE(oos_sharpe, sharpe, 0)) <= 10
        ),
        governance_ranked AS (
          SELECT
            stock_code,
            reason,
            rejected_at,
            COUNT(*) OVER (PARTITION BY stock_code) AS governance_reject_count,
            ROW_NUMBER() OVER (
              PARTITION BY stock_code
              ORDER BY rejected_at DESC, reason DESC
            ) AS rn
          FROM fact_optuna_governance_log
          WHERE stock_code IN (SELECT DISTINCT stock_code FROM __mdpr_pit_diag_input)
        ),
        latest_governance AS (
          SELECT
            stock_code,
            governance_reject_count,
            reason AS governance_latest_reason,
            CAST(rejected_at AS VARCHAR) AS governance_latest_rejected_at
          FROM governance_ranked
          WHERE rn = 1
        ),
        pit_counts AS (
          SELECT i.signal_date, i.profile_id, i.rank_in_profile,
                 COUNT(*) FILTER (
                   WHERE p.stock_code = i.stock_code
                 ) AS pit_same_stock_rows,
                 COUNT(*) FILTER (
                   WHERE p.stock_code = i.stock_code
                     AND p.formula_id = i.formula_id
                     AND p.formula_variant = i.formula_variant
                 ) AS pit_same_formula_rows,
                 COUNT(*) FILTER (
                   WHERE p.stock_code = i.stock_code
                     AND p.formula_id = i.formula_id
                     AND p.formula_variant = i.formula_variant
                     AND p.stage_filter = i.stage_bin
                 ) AS pit_exact_stage_rows,
                 MAX(p.cutoff_date) FILTER (
                   WHERE p.stock_code = i.stock_code
                 ) AS latest_pit_cutoff_date
            FROM __mdpr_pit_diag_input i
            LEFT JOIN qualified_pit p
              ON p.stock_code = i.stock_code
          GROUP BY i.signal_date, i.profile_id, i.rank_in_profile
        )
        SELECT i.signal_date, i.profile_id, i.rank_in_profile, i.stock_code,
               i.formula_id, i.formula_variant, i.stage_bin, i.match_tier,
               COALESCE(c.pit_exact_stage_rows, 0) AS pit_exact_stage_rows,
               COALESCE(c.pit_same_formula_rows, 0) AS pit_same_formula_rows,
               COALESCE(c.pit_same_stock_rows, 0) AS pit_same_stock_rows,
               c.latest_pit_cutoff_date,
               CASE
                 WHEN i.match_tier IN ('stage_pit', 'stage_pit_formula_fallback') THEN 'pit_selected'
                 WHEN COALESCE(i.stage_bin, '?') = '?' THEN 'stage_unknown'
                 WHEN COALESCE(c.pit_exact_stage_rows, 0) > 0 THEN 'pit_present_but_ranked_below_or_filtered'
                 WHEN COALESCE(c.pit_same_formula_rows, 0) > 0 THEN 'stage_mismatch'
                 WHEN COALESCE(c.pit_same_stock_rows, 0) > 0 THEN 'formula_missing_pit'
                 ELSE 'stock_missing_pit'
               END AS missing_reason,
               COALESCE(g.governance_reject_count, 0) AS governance_reject_count,
               g.governance_latest_reason,
               g.governance_latest_rejected_at
          FROM __mdpr_pit_diag_input i
          LEFT JOIN pit_counts c
            ON c.signal_date = i.signal_date
           AND c.profile_id = i.profile_id
           AND c.rank_in_profile = i.rank_in_profile
          LEFT JOIN latest_governance g
            ON g.stock_code = i.stock_code
         ORDER BY i.profile_id, i.rank_in_profile
    """, [signal_date]).fetchall()
    conn.execute("DROP TABLE IF EXISTS __mdpr_pit_diag_input")
    return [tuple(row) for row in rows]


def _load_candidate_rows(conn, signal_date: str) -> list[tuple]:
    """拉取当日推荐候选,把 MACD state history 作为补充供给一起纳入。

    返回 tuple 最后一列为 signal_state:
      - 触发信号通常是 just_crossed
      - MACD state history 可能是 holding / imminent
    """
    VOL_BINS  = [(0, 0.7, "缩量"), (0.7, 1.3, "平量"), (1.3, 2.0, "温量"), (2.0, 99, "爆量")]
    AMT_BINS  = [(0, 0.7, "额减"), (0.7, 1.3, "额平"), (1.3, 2.0, "额温"), (2.0, 99, "额爆")]
    P60_BINS  = [(0, 0.65, "深底"), (0.65, 0.85, "中位"), (0.85, 0.97, "高位"), (0.97, 99, "新高")]

    def _bin_sql(col, bins):
        cases = " ".join(
            f"WHEN {col} IS NOT NULL AND {col} >= {lo} AND {col} < {hi} THEN '{label}'"
            for lo, hi, label in bins
        )
        return f"CASE {cases} ELSE '?' END"

    return conn.execute(
        f"""
        WITH today_signals AS (
          SELECT
                 t.stock_code, t.formula_id, t.formula_variant, t.strength,
                 {_bin_sql('c.vol_r20', VOL_BINS)}    AS vol_bin,
                 {_bin_sql('c.amt_r20', AMT_BINS)}    AS amt_bin,
                 {_bin_sql('c.price_pos_60d', P60_BINS)} AS p60_bin,
                 COALESCE(c.technical_stage, '?')      AS stage_bin,
                 COALESCE(t.state, 'just_crossed')      AS signal_state
            FROM fact_technical_trigger t
            LEFT JOIN fact_signal_context c
              ON c.stock_code = t.stock_code AND c.date = t.date
           WHERE t.date = ?
          UNION ALL
          SELECT
                 s.stock_code, s.formula_id, s.formula_variant, s.strength,
                 {_bin_sql('c.vol_r20', VOL_BINS)}    AS vol_bin,
                 {_bin_sql('c.amt_r20', AMT_BINS)}    AS amt_bin,
                 {_bin_sql('c.price_pos_60d', P60_BINS)} AS p60_bin,
                 COALESCE(c.technical_stage, '?')      AS stage_bin,
                 s.state AS signal_state
            FROM mart_macd_state_history s
            LEFT JOIN fact_signal_context c
              ON c.stock_code = s.stock_code AND c.date = s.date
           WHERE s.date = ?
             AND s.state IN ('holding', 'imminent')
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
        -- η+++++++ tier-1: PIT exact stage; tier-1b: PIT same stock+formula; fallback: cross-stage optimal
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
               COALESCE(
                 opt.calmar,
                 CASE
                   WHEN COALESCE(sopt.oos_avg_ret, sopt.avg_ret) > 0
                    AND sopt.optimal_stop_pct < 0
                   THEN COALESCE(sopt.oos_avg_ret, sopt.avg_ret) / abs(sopt.optimal_stop_pct)
                   WHEN COALESCE(sfopt.oos_avg_ret, sfopt.avg_ret) > 0
                    AND sfopt.optimal_stop_pct < 0
                   THEN COALESCE(sfopt.oos_avg_ret, sfopt.avg_ret) / abs(sfopt.optimal_stop_pct)
                   ELSE NULL
                 END
               )                                                             AS calmar,
               CASE WHEN sopt.stock_code IS NOT NULL THEN 'stage_pit'
                    WHEN sfopt.stock_code IS NOT NULL THEN 'stage_pit_formula_fallback'
                    ELSE 'cross_stage_fallback' END                          AS match_tier,
              COALESCE(sopt.optimal_stop_pct,     sfopt.optimal_stop_pct,     opt.optimal_stop_pct)     AS optimal_stop_pct,
              COALESCE(sopt.optimal_target_pct,   sfopt.optimal_target_pct,   opt.optimal_target_pct)   AS optimal_target_pct,
              COALESCE(sopt.optimal_trailing_pct, sfopt.optimal_trailing_pct, opt.optimal_trailing_pct) AS optimal_trailing_pct,
               p.fundamental_stage, p.latest_close,
               COALESCE(sf.survey_bin, '冷')     AS survey_bin,
               COALESCE(sf.survey_count_60d, 0)  AS survey_count_60d,
               ts.signal_state
          FROM today_signals ts
          LEFT JOIN stage_pit_exact sopt
            ON sopt.stock_code      = ts.stock_code
           AND sopt.formula_id      = ts.formula_id
           AND sopt.formula_variant = ts.formula_variant
           AND sopt.stage_filter    = ts.stage_bin
           -- audit fix: filter ret/dd/sharpe 异常值
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
         WHERE (
                sopt.stock_code IS NOT NULL
             OR sfopt.stock_code IS NOT NULL
             OR opt.stock_code IS NOT NULL
         )
        """,
        [signal_date, signal_date, signal_date, signal_date, signal_date],
    ).fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="signal_date, default=最新")
    args = parser.parse_args()

    t_total = time.time()
    conn = get_conn()
    try:
        conn.executescript(DDL)
        _ensure_pit_diagnostic_columns(conn)

        # 1. signal_date
        if args.date:
            signal_date = args.date
        else:
            r = conn.execute("SELECT MAX(date) FROM fact_technical_trigger").fetchone()
            signal_date = r[0] if r else None
            if not signal_date:
                log.error("无 fact_technical_trigger 数据")
                return
        log.info(f"signal_date = {signal_date}")

        # 2. T+1 buy_date
        nb = conn.execute(
            """SELECT trade_date FROM dim_trading_calendar
                WHERE trade_date > ? AND is_trading=1 ORDER BY trade_date LIMIT 1""",
            [signal_date],
        ).fetchone()
        buy_date = nb[0] if nb else (_date.fromisoformat(signal_date) + timedelta(days=1)).isoformat()
        log.info(f"buy_date    = {buy_date} (T+1)")

        log.info("加载今日触发 × MACD state history × PIT stage-aware Optuna 寻优结果 ...")
        # ψ.2 改造: tier-1 使用 mart_per_stock_stage_strategy_optimal_pit,
        # 仅取 cutoff_date <= signal_date 的参数。tier-2 fallback 仍保留旧
        # cross-stage 表, 但不再让单批 legacy stage snapshot 作为生产正向证据。
        BUCKET_MIN_N = 10  # 保留参数 (UI 展示需要)

        candidates_raw = _load_candidate_rows(conn, signal_date)
        # 报告 stage-aware 命中 vs fallback 占比
        n_stage_aware = sum(1 for r in candidates_raw if r[14] == "stage_pit")
        n_stage_formula = sum(1 for r in candidates_raw if r[14] == "stage_pit_formula_fallback")
        n_fallback = len(candidates_raw) - n_stage_aware - n_stage_formula
        signal_state_counts = {}
        for r in candidates_raw:
            signal_state = r[-1]
            signal_state_counts[signal_state] = signal_state_counts.get(signal_state, 0) + 1
        log.info(
            f"  原始候选: {len(candidates_raw)} 条 "
            f"(stage_pit exact = {n_stage_aware}, "
            f"stage_pit formula fallback = {n_stage_formula}, "
            f"cross_stage fallback = {n_fallback})"
        )
        log.info(f"  原始信号态: {dict(sorted(signal_state_counts.items()))}")

        # 4. 转 dict, 加 signal_close 字段
        candidates = []
        for r in candidates_raw:
            (sc, fid, fvar, vb, ab, pb, sb,
             hd, n, win, ret, dd, sharpe, cal, tier,
             opt_stop, opt_target, opt_trail,
             fund, close, survey_bin, survey_count_60d, signal_state) = r
            candidates.append({
                "stock_code": sc,
                "formula_id": fid,
                "formula_variant": fvar,
                "vol_bin": vb,
                "amt_bin": ab,
                "price_pos_bin": pb,
                "stage_bin": sb,
                "match_tier": tier,
                "holding_days": int(hd) if hd else None,
                "n_signals": int(n) if n else 0,
                "win_rate": float(win) if win is not None else None,
                "avg_ret": float(ret) if ret is not None else None,
                "avg_dd": float(dd) if dd is not None else None,
                "sharpe": float(sharpe) if sharpe is not None else None,
                "calmar": float(cal) if cal is not None else None,
                "fundamental_stage": fund,
                "signal_close": float(close) if close else None,
                "signal_state": signal_state,
                # Phase ζ: per-stock Optuna 最优策略参数 (sizing 用)
                "optimal_stop_pct":     float(opt_stop) if opt_stop is not None else None,
                "optimal_target_pct":   float(opt_target) if opt_target is not None else None,
                "optimal_trailing_pct": float(opt_trail) if opt_trail is not None else None,
                # sentiment 字段
                "survey_bin": survey_bin,
                "survey_count_60d": int(survey_count_60d or 0),
            })
        log.info(f"  候选 (Optuna 寻优过): {len(candidates):,}")

        # 5. 对每个 profile 跑 sizing
        all_rows = []
        for pid, prof in PROFILES.items():
            log.info(f"--- profile={pid} ({prof.label}) ---")
            sized = rank_and_size(candidates, prof)
            log.info(f"  推荐 {len(sized)} 条 (max={prof.max_positions})")
            for r in sized:
                all_rows.append((
                    signal_date, buy_date, pid, r["rank_in_profile"], r["stock_code"],
                    r.get("formula_id"), r.get("formula_variant"),
                    r.get("vol_bin"), r.get("amt_bin"), r.get("price_pos_bin"), r.get("stage_bin"),
                    r.get("fundamental_stage"), r.get("match_tier"),
                    r.get("survey_bin"), r.get("survey_count_60d"),
                    r.get("sentiment_mult"), r.get("sentiment_trace"),
                    r.get("n_signals"), r.get("win_rate"), r.get("wilson_win"),
                    r.get("avg_ret"), r.get("avg_dd"), r.get("sharpe"), r.get("calmar"),
                    r.get("kelly_f"), r.get("position_pct"), r.get("confidence_tier"), r.get("score"),
                    r.get("holding_days"),
                    r.get("optimal_stop_pct"), r.get("optimal_target_pct"), r.get("optimal_trailing_pct"),
                    r.get("signal_close"), r.get("buy_price"),
                    r.get("sell_target"), r.get("stop_price"), r.get("trailing_pct"),
                ))

        diag_rows = _build_pit_diagnostic_rows(conn, signal_date, all_rows)
        diag_reason_counts = {}
        for row in diag_rows:
            diag_reason_counts[row[12]] = diag_reason_counts.get(row[12], 0) + 1

        # 6. 写库 atomic
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_daily_position_recommendation WHERE signal_date = ?", [signal_date])
            conn.execute(
                "DELETE FROM mart_daily_position_recommendation_pit_diagnostic WHERE signal_date = ?",
                [signal_date],
            )
            if all_rows:
                conn.executemany(
                    """INSERT INTO mart_daily_position_recommendation
                       (signal_date, buy_date, profile_id, rank_in_profile, stock_code,
                        formula_id, formula_variant,
                        vol_bin, amt_bin, price_pos_bin, stage_bin,
                        fundamental_stage, match_tier,
                        survey_bin, survey_count_60d, sentiment_mult, sentiment_trace,
                        n_signals, raw_win_rate, wilson_win_rate,
                        avg_ret, avg_dd, sharpe, calmar,
                        kelly_f, position_pct, confidence_tier, score,
                        holding_days,
                        optimal_stop_pct, optimal_target_pct, optimal_trailing_pct,
                        signal_close_price, buy_price,
                        sell_target_price, stop_price, trailing_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    all_rows,
                )
            if diag_rows:
                conn.executemany(
                    """INSERT INTO mart_daily_position_recommendation_pit_diagnostic
                       (signal_date, profile_id, rank_in_profile, stock_code,
                        formula_id, formula_variant, stage_bin, match_tier,
                        pit_exact_stage_rows, pit_same_formula_rows, pit_same_stock_rows,
                        latest_pit_cutoff_date, missing_reason,
                        governance_reject_count, governance_latest_reason, governance_latest_rejected_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    diag_rows,
                )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 7. 打印 每 profile top 5
        # tuple 索引 (ζ 新增 3 个 optimal_* 字段后):
        #  0=date 1=buy 2=pid 3=rank 4=code 5=fid 6=variant 7=vol 8=amt 9=p60 10=stage
        # 11=fund 12=match 13=sbin 14=scount 15=smult 16=strace
        # 17=n 18=raw_win 19=wilson 20=ret 21=dd 22=sharpe 23=calmar
        # 24=kelly 25=pos 26=tier 27=score
        # 28=hp 29=opt_stop 30=opt_target 31=opt_trail
        # 32=signal_close 33=buy 34=target 35=stop 36=trail
        for pid, prof in PROFILES.items():
            print(f"\n{'='*138}")
            print(f"  {prof.label}  推荐 Top 5  (signal={signal_date}, buy={buy_date})")
            print(f"{'='*138}")
            print(f"{'rank':>4} {'股票':>8} {'公式':>30} {'阶段':>4} {'层':>4} {'调研':>4} {'×':>5} "
                  f"{'胜率':>6} {'Wilson':>7} {'预期':>7} {'预期DD':>7} {'hp':>3} {'仓位%':>6} "
                  f"{'信号价':>8} {'目标价':>8} {'止损':>8} {'tier':>4}")
            top = [r for r in all_rows if r[2] == pid][:5]
            for r in top:
                tier_label = "A" if r[12] == "A_bucket" else "B"
                sbin = r[13] or "?"
                smult = r[15] or 1.0
                print(f"{r[3]:>4} {r[4]:>8} {r[6]:>30} {r[10]:>4} {tier_label:>4} {sbin:>4} {smult:>5.2f} "
                      f"{(r[18] or 0)*100:>5.1f}% {(r[19] or 0)*100:>6.1f}% "
                      f"{(r[20] or 0)*100:>+6.1f}% {(r[21] or 0)*100:>+6.1f}% "
                      f"{r[28]:>3}d {(r[25] or 0)*100:>5.1f}% "
                      f"{r[33] or 0:>8.2f} {r[34] or 0:>8.2f} {r[35] or 0:>8.2f} T{r[26]}")
        # tier + 调研桶 分布统计
        from collections import Counter
        tier_dist = Counter(r[12] for r in all_rows)
        sbin_dist = Counter(r[13] for r in all_rows if r[2] == "long")  # 仅 long profile 有意义
        print(f"\n推荐分层分布: {dict(tier_dist)}")
        print(f"PIT 诊断原因分布: {diag_reason_counts}")
        print(f"长期 profile 调研桶分布: {dict(sbin_dist)}")
        print()
        log.info(f"=== 总耗时 {time.time()-t_total:.0f}s | 总推荐 {len(all_rows)} 条 ===")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

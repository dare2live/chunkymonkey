"""Phase η+++++ — 每日形态识别 + 每股每公式买点判定 entry.

I/O 薄层 (orchestrator 走 services.buy_signal):
  1. 加载今日 fact_technical_trigger (公式触发)
  2. 加载今日 fact_signal_context (5 维桶)
  3. 加载 mart_stock_picture_daily (technical_stage + fundamental_stage)
  4. 加载 mart_stock_survey_features (survey_bin)
  5. 加载 mart_per_stock_strategy_optimal (Optuna 寻优历史 alpha)
  6. 加载 mart_stock_formula_optuna_v2 (5 维桶 is_best_hd 标记)
  7. 对每 (stock × variant) 调 buy_signal.aggregate_factors → score → tier → reasoning
  8. 写 mart_stock_formula_buy_signal_daily

usage:
  PYTHONPATH=backend python backend/scripts/build_stock_formula_buy_signal_daily.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date as _date

from services.buy_signal import (
    aggregate_factors, classify_tier, compute_score, factor_contributions,
    generate_reasoning,
)
from services.buy_signal.ddl import MART_STOCK_FORMULA_BUY_SIGNAL_DAILY_DDL
from services.db import get_conn
from services.shared_feature_bins_config import DEFAULT_SHARED_FEATURE_BINS_CONFIG


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("build_buy_signal")


VOL_BINS  = DEFAULT_SHARED_FEATURE_BINS_CONFIG.vol_bins
AMT_BINS  = DEFAULT_SHARED_FEATURE_BINS_CONFIG.amt_bins
P60_BINS  = DEFAULT_SHARED_FEATURE_BINS_CONFIG.p60_bins


def _bin_label(value, bins):
    for lo, hi, label in bins:
        if value is not None and lo <= value < hi:
            return label
    return "?"


def load_today_rows(conn, signal_date):
    """加载 signal_date 当日触发 × picture(PIT as-of) × survey × Optuna 寻优.

    PIT 约束 (2026-06-11 体检 HIGH 修复):
      picture JOIN 取每只股 snapshot_date <= signal_date 的最近一张画像 (as-of),
      不再 `MAX(snapshot_date)` 把最新快照贴给历史 signal_date.
      若该 signal_date 之前没有画像快照 → 子查询 NULL → fund/archetype/primary_type
      标 unknown (LEFT JOIN NULL), 绝不注入未来快照.
    """
    # 检测 optimal_buy_offset 列是否存在 (buy_offset 重跑可能进行中)
    cols = [r[1] for r in conn.execute("DESCRIBE mart_per_stock_strategy_optimal").fetchall()]
    has_buy_offset = "optimal_buy_offset" in cols
    buy_offset_sel = "opt.optimal_buy_offset" if has_buy_offset else "1 AS optimal_buy_offset"

    return conn.execute(
        f"""
        WITH today_signals AS (
          SELECT t.stock_code, t.formula_id, t.formula_variant,
                 c.vol_r20, c.amt_r20, c.price_pos_60d, c.technical_stage
            FROM fact_technical_trigger t
            LEFT JOIN fact_signal_context c
              ON c.stock_code = t.stock_code AND c.date = t.date
           WHERE t.date = ?
        )
        SELECT ts.stock_code, ts.formula_id, ts.formula_variant,
               ts.vol_r20, ts.amt_r20, ts.price_pos_60d, ts.technical_stage,
               p.fundamental_stage, p.stock_archetype, p.primary_type,
               sf.survey_bin, sf.survey_count_60d,
               opt.optimal_hp, opt.optimal_stop_pct, opt.optimal_target_pct,
               opt.optimal_trailing_pct, {buy_offset_sel},
               opt.sharpe, opt.win_rate, opt.n_traded
          FROM today_signals ts
          -- PIT as-of (2026-06-11 体检 HIGH 修复): 历史 --date 不得贴最新画像快照.
          -- 取每只股 snapshot_date <= signal_date 的最近一张画像; 若该日之前无快照,
          -- 子查询返回 NULL → fund/archetype/primary_type 标 unknown, 不注入未来.
          LEFT JOIN mart_stock_picture_daily p
            ON p.stock_code = ts.stock_code
           AND p.snapshot_date = (
                 SELECT MAX(p2.snapshot_date)
                   FROM mart_stock_picture_daily p2
                  WHERE p2.stock_code = ts.stock_code
                    AND p2.snapshot_date <= ?
               )
          LEFT JOIN mart_stock_survey_features sf
            ON sf.stock_code = ts.stock_code AND sf.as_of_date = ?
          LEFT JOIN mart_per_stock_strategy_optimal opt
            ON opt.stock_code = ts.stock_code
           AND opt.formula_id = ts.formula_id
           AND opt.formula_variant = ts.formula_variant
           -- audit fix: 剔除异常值 (winsorize: |ret|≤50%, dd≥-50%, |sharpe|≤10)
           AND (opt.avg_ret IS NULL OR abs(opt.avg_ret) <= 0.5)
           AND (opt.avg_max_dd IS NULL OR opt.avg_max_dd >= -0.5)
           AND (opt.sharpe IS NULL OR abs(opt.sharpe) <= 10)
        """,
        # 顺序: t.date / picture as-of <= / sf.as_of_date — 三处都是 signal_date.
        [signal_date, signal_date, signal_date],
    ).fetchall()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="signal_date, 默认最新")
    args = parser.parse_args()

    t0 = time.time()
    conn = get_conn()
    try:
        conn.executescript(MART_STOCK_FORMULA_BUY_SIGNAL_DAILY_DDL)

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

        # 2. 今日触发 × signal_context × picture(PIT as-of) × survey × Optuna 寻优
        log.info("加载今日触发 × 多源数据 (picture PIT as-of <= signal_date) ...")
        rows = load_today_rows(conn, signal_date)

        # 3.0 加载 mart_stage_formula_fitness 数据驱动 lookup (factor 4 的真值)
        log.info("加载 mart_stage_formula_fitness 数据 ...")
        try:
            # Phase η++++++ 修正: 用 MAX(sharpe) 取该 (fund × tech × formula) 组合
            # 在最佳 hp 下的 sharpe — 避免跨 hp 平均稀释
            fitness_rows = conn.execute(
                """SELECT fundamental_stage, technical_stage, formula_variant,
                          MAX(sharpe) AS best_sharpe
                     FROM mart_stage_formula_fitness
                    WHERE sharpe IS NOT NULL AND n_signals >= 20
                    GROUP BY 1, 2, 3"""
            ).fetchall()
            fitness_lookup = {(r[0], r[1], r[2]): float(r[3]) for r in fitness_rows}
            log.info(f"  fitness lookup: {len(fitness_lookup):,} (fund × tech × formula) 条 (best hp sharpe, n≥20)")
        except Exception as e:
            log.warning(f"  mart_stage_formula_fitness 不可用, factor 4 走 fallback: {e}")
            fitness_lookup = {}
        log.info(f"  当日触发: {len(rows):,} 条 (含历史 Optuna join)")

        # 3. 加载 5 维桶 is_best_hd 标记 (从 mart_stock_formula_optuna_v2)
        log.info("加载 5 维桶 is_best_hd ...")
        bucket_best_rows = conn.execute(
            """SELECT stock_code, formula_variant, vol_bin, amt_bin, price_pos_bin, stage_bin,
                      n_signals
                 FROM mart_stock_formula_optuna_v2
                WHERE is_best_hd = TRUE"""
        ).fetchall()
        # (stock × variant × bucket) → n_signals
        best_bucket_lookup: dict = {}
        for sc, fvar, vb, ab, pb, sb, n in bucket_best_rows:
            best_bucket_lookup[(sc, fvar, vb, ab, pb, sb)] = n
        log.info(f"  5 维桶最佳标记: {len(best_bucket_lookup):,} 个")

        # 4. 处理每条
        log.info("聚合 6 因子 + 综合 score + tier ...")
        out_rows = []
        n_total = 0; n_no_signal = 0
        tier_counts = {"NO_SIGNAL": 0, "WATCH": 0, "BUY": 0, "STRONG_BUY": 0}

        for r in rows:
            (sc, fid, fvar, vr, ar, p60, ts, fund, arch, ptype, sbin, scount,
             opt_hp, opt_stop, opt_target, opt_trail, opt_buy_off,
             sharpe, win_rate, n_traded) = r
            n_total += 1
            vol_b = _bin_label(vr, VOL_BINS)
            amt_b = _bin_label(ar, AMT_BINS)
            p60_b = _bin_label(p60, P60_BINS)
            stage_b = ts if ts in ("1", "1.5", "2", "3", "4") else "?"
            today_bucket = (vol_b, amt_b, p60_b, stage_b)
            is_best = (sc, fvar, vol_b, amt_b, p60_b, stage_b) in best_bucket_lookup
            hist_n_sig = best_bucket_lookup.get((sc, fvar, vol_b, amt_b, p60_b, stage_b), 0)

            factors = aggregate_factors(
                triggered_today=True,
                today_bucket=today_bucket,
                is_best_bucket=is_best,
                historical_n_signals=hist_n_sig,
                sharpe=float(sharpe) if sharpe is not None else None,
                win_rate=float(win_rate) if win_rate is not None else None,
                n_traded=int(n_traded) if n_traded else None,
                today_technical_stage=ts,
                formula_variant=fvar,
                fundamental_stage=fund,
                survey_bin=sbin,
                profile_id="long",
                stock_archetype=arch,         # Phase η+++++
                primary_type=ptype,           # Phase η+++++
                fitness_lookup=fitness_lookup, # 数据驱动 stage_fitness
            )
            score = compute_score(factors)
            tier = classify_tier(score)
            tier_counts[tier] += 1
            if tier == "NO_SIGNAL":
                n_no_signal += 1
            contribs = factor_contributions(factors)
            reasoning = generate_reasoning(
                factors, formula_variant=fvar,
                today_technical_stage=ts, fundamental_stage=fund, survey_bin=sbin,
                sharpe=float(sharpe) if sharpe is not None else None,
                win_rate=float(win_rate) if win_rate is not None else None,
                is_best_bucket=is_best, n_traded=int(n_traded) if n_traded else None,
                stock_archetype=arch, primary_type=ptype,
            )

            out_rows.append((
                signal_date, sc, fid, fvar,
                score, tier, reasoning,
                # 8 factor 原始分
                factors.trigger, factors.bucket_match, factors.historical_alpha,
                factors.stage_fitness, factors.fundamental_stage, factors.sentiment,
                factors.stock_archetype, factors.primary_type,
                # 8 contrib
                contribs["trigger"], contribs["bucket_match"], contribs["historical_alpha"],
                contribs["stage_fitness"], contribs["fundamental_stage"], contribs["sentiment"],
                contribs["stock_archetype"], contribs["primary_type"],
                # 当日数据
                ts, fund, arch, ptype, sbin, vol_b, amt_b, p60_b,
                float(sharpe) if sharpe is not None else None,
                float(win_rate) if win_rate is not None else None,
                int(n_traded) if n_traded else None,
                int(opt_hp) if opt_hp is not None else None,
                float(opt_stop) if opt_stop is not None else None,
                float(opt_target) if opt_target is not None else None,
                float(opt_trail) if opt_trail is not None else None,
                int(opt_buy_off) if opt_buy_off is not None else None,
            ))

        # 5. 写库 atomic
        log.info(f"写库 {len(out_rows):,} 行 ...")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM mart_stock_formula_buy_signal_daily WHERE signal_date=?",
                         [signal_date])
            if out_rows:
                conn.executemany(
                    """INSERT INTO mart_stock_formula_buy_signal_daily (
                        signal_date, stock_code, formula_id, formula_variant,
                        score, tier, reasoning,
                        factor_trigger, factor_bucket_match, factor_historical_alpha,
                        factor_stage_fitness, factor_fundamental_stage, factor_sentiment,
                        factor_stock_archetype, factor_primary_type,
                        contrib_trigger, contrib_bucket_match, contrib_historical_alpha,
                        contrib_stage_fitness, contrib_fundamental_stage, contrib_sentiment,
                        contrib_stock_archetype, contrib_primary_type,
                        today_technical_stage, today_fundamental_stage,
                        today_stock_archetype, today_primary_type,
                        today_survey_bin, today_vol_bin, today_amt_bin, today_p60_bin,
                        historical_sharpe, historical_win_rate, historical_n_traded,
                        optimal_hp, optimal_stop_pct, optimal_target_pct,
                        optimal_trailing_pct, optimal_buy_offset
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    out_rows,
                )
            conn.execute("COMMIT")
        except BaseException:
            try: conn.execute("ROLLBACK")
            except Exception: pass
            raise

        # 6. 报告
        log.info(f"=== 完成 ({time.time()-t0:.0f}s) ===")
        print()
        print(f"{'='*120}")
        print(f"  每股每公式 买点判定 (signal_date={signal_date})")
        print(f"{'='*120}")
        print(f"  Tier 分布: {tier_counts}")
        print()
        # Top 15 STRONG_BUY + BUY
        print(f"  Top 15 STRONG_BUY / BUY:")
        print(f"  {'股票':>8} {'公式':<30} {'tier':>11} {'score':>6}  reasoning")
        for r in conn.execute("""
            SELECT stock_code, formula_variant, tier, score, reasoning
              FROM mart_stock_formula_buy_signal_daily
             WHERE signal_date = ? AND tier IN ('STRONG_BUY', 'BUY')
             ORDER BY score DESC LIMIT 15
        """, [signal_date]).fetchall():
            print(f"  {r[0]:>8} {r[1]:<30} {r[2]:>11} {r[3]:>6.1f}  {r[4]}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

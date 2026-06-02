"""形态-公式适配矩阵 — Phase β W3 D1-D3 核心交付物。

输出:
  1. fact_stock_technical_stage   每股每日技术阶段 (Stan Weinstein 4-stage v1)
  2. mart_stage_formula_fitness   (fund_stage, tech_stage, formula, holding_days) → 胜率

依赖:
  - fact_technical_trigger        (Phase β W2 已产出, 744K 信号)
  - dim_stock_stage_latest        (现有, 3355 股 fund_stage 当前快照)
  - market.v_price_kline_qfq      (K 线)

简化:
  - fund_stage 用 dim_stock_stage_latest 的"当前快照"近似覆盖历史信号
    (fund stage 变化慢, 季度更新, 用当前快照近似 1-3 年历史可接受)
  - technical_stage 全量从 K 线计算每日 (Stan Weinstein v1 规则)

用法:
  PYTHONPATH=backend python backend/scripts/build_stage_formula_fitness.py
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date
from pathlib import Path

import duckdb
import numpy as np

from services.db import get_conn
from services.formula_engine.ddl import ensure_formula_tables
from services.formula_engine.technical_stage import classify_technical_stage
from services.formula_engine.shared_windows import HOLDING_DAYS
from services.utils import latest_completed_trade_date


log = logging.getLogger("build_stage_formula_fitness")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

MARKET_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "market.duckdb"
MIN_N_SIGNALS = 30  # 形态 × 公式组合最少样本量


def _iso_date(value: str, *, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO date YYYY-MM-DD, got {value!r}") from exc


def _resolve_stage_window(start: str, end: str, write_start: str | None) -> tuple[str, str, str]:
    compute_start = _iso_date(start, name="start")
    write_start_resolved = _iso_date(write_start or start, name="write_start")
    end_resolved = _iso_date(end, name="end")
    if compute_start > write_start_resolved:
        raise ValueError(
            f"start/read window {compute_start} must be <= write_start {write_start_resolved}"
        )
    if write_start_resolved > end_resolved:
        raise ValueError(f"write_start {write_start_resolved} must be <= end {end_resolved}")
    return compute_start, write_start_resolved, end_resolved


def build_technical_stage_history(
    mkt_conn,
    conn,
    start: str,
    end: str,
    *,
    write_start: str | None = None,
    allow_empty_window: bool = False,
) -> int:
    """跑全市场历史 technical_stage,写 fact_stock_technical_stage。"""
    compute_start, write_start_resolved, end_resolved = _resolve_stage_window(start, end, write_start)
    t0 = time.time()
    log.info(
        "加载全市场 K 线 (closes + volumes only), compute_start=%s write_start=%s end=%s...",
        compute_start,
        write_start_resolved,
        end_resolved,
    )
    arr = mkt_conn.execute(
        """
        SELECT code, date, close, volume
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
           AND date >= ? AND date <= ?
         ORDER BY code, date
        """,
        [compute_start, end_resolved],
    ).fetchnumpy()
    log.info(f"  K 线 {len(arr['code']):,} 行, SQL {time.time()-t0:.1f}s")

    codes_array = arr["code"]
    unique_codes, first_idx = np.unique(codes_array, return_index=True)
    sort_perm = np.argsort(first_idx)
    unique_codes = unique_codes[sort_perm]
    first_idx = first_idx[sort_perm]
    last_idx = np.concatenate([first_idx[1:], [len(codes_array)]])

    all_rows: list[tuple[str, str, str]] = []
    t1 = time.time()
    for ci, code in enumerate(unique_codes):
        s, e = int(first_idx[ci]), int(last_idx[ci])
        closes = arr["close"][s:e].astype(float)
        volumes = arr["volume"][s:e].astype(float)
        dates = arr["date"][s:e]
        stages = classify_technical_stage(closes, volumes)
        # 只写入声明窗口内的非 unknown 行；compute_start 只负责给滚动指标预热。
        for di, stage in enumerate(stages):
            row_date = str(dates[di])
            if stage != "unknown" and write_start_resolved <= row_date <= end_resolved:
                all_rows.append((str(code), row_date, stage))
        if (ci + 1) % 1000 == 0:
            log.info(f"  classify {ci+1:,}/{len(unique_codes):,}")
    log.info(f"  classify 全市场 完成 {time.time()-t1:.1f}s, 有效行 {len(all_rows):,}")

    if not all_rows and not allow_empty_window:
        raise RuntimeError(
            "technical_stage window produced 0 rows; refuse to delete existing rows "
            f"for {write_start_resolved}..{end_resolved}. Use --allow-empty-stage-window to override."
        )

    # 写入 fact_stock_technical_stage (DELETE + INSERT 全量替换, 显式事务原子)
    # 见 build_formula_signals_history.write_signals_to_db 同款 SIGPIPE 防御
    t2 = time.time()
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM fact_stock_technical_stage WHERE date >= ? AND date <= ?",
            [write_start_resolved, end_resolved],
        )
        if all_rows:
            conn.executemany(
                """
                INSERT INTO fact_stock_technical_stage (stock_code, date, stage)
                VALUES (?, ?, ?)
                """,
                all_rows,
            )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_exc:
            log.warning("ROLLBACK failed after technical_stage write error: %s", rollback_exc)
        raise
    log.info(
        "  写入 fact_stock_technical_stage: %s 行 window=%s..%s (%.1fs)",
        f"{len(all_rows):,}",
        write_start_resolved,
        end_resolved,
        time.time() - t2,
    )
    return len(all_rows)


def build_fitness_matrix(conn, mkt_conn, eval_start: str, eval_end: str) -> int:
    """计算 (fund × tech × formula × holding_days) 适配矩阵。

    避免大 SQL JOIN OOM: Python 内存 + numpy 计算。
    """
    t0 = time.time()
    log.info("计算适配矩阵 (Python 内存 + numpy)...")

    # Step 1: 拉信号 + technical_stage (信号日) + fund_stage 一次性 JOIN (信号 744K 行可控)
    rows = conn.execute(
        """
        SELECT s.stock_code, s.date, s.formula_id, s.formula_variant,
               ts.stage AS technical_stage,
               COALESCE(fund.path_state, 'unknown') AS fundamental_stage
          FROM fact_technical_trigger s
          JOIN fact_stock_technical_stage ts
            ON ts.stock_code = s.stock_code AND ts.date = s.date
          LEFT JOIN dim_stock_stage_latest fund
            ON fund.stock_code = s.stock_code
         WHERE s.date >= ? AND s.date <= ?
        """,
        [eval_start, eval_end],
    ).fetchall()
    log.info(f"  信号 JOIN stage: {len(rows):,} 行 ({time.time()-t0:.1f}s)")

    if not rows:
        log.warning("无信号,跳过")
        return 0

    # Step 2: 加载 K 线到内存 (numpy groupby)
    t1 = time.time()
    arr = mkt_conn.execute(
        """
        SELECT code, date, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily'
        ORDER BY code, date
        """
    ).fetchnumpy()
    log.info(f"  K 线 SQL: {len(arr['code']):,} 行 ({time.time()-t1:.1f}s)")

    codes_arr = arr["code"]
    unique_codes, first_idx = np.unique(codes_arr, return_index=True)
    sort_perm = np.argsort(first_idx)
    unique_codes = unique_codes[sort_perm]
    first_idx = first_idx[sort_perm]
    last_idx = np.concatenate([first_idx[1:], [len(codes_arr)]])

    # 用 dict 索引每个股的 (dates, closes)
    kline_by_code: dict[str, dict] = {}
    for ci, code in enumerate(unique_codes):
        s, e = int(first_idx[ci]), int(last_idx[ci])
        kline_by_code[code] = {
            "dates": arr["date"][s:e],
            "closes": arr["close"][s:e].astype(float),
        }
    log.info(f"  K 线 groupby: {len(kline_by_code):,} 股 ({time.time()-t1:.1f}s)")

    # Step 3: 对每个信号 × hd 算收益,累计到 (fund, tech, formula, hd) 桶
    # bucket key: (fund_stage, tech_stage, formula_id, formula_variant, hd)
    from collections import defaultdict
    bucket_returns: dict[tuple, list[float]] = defaultdict(list)

    t2 = time.time()
    for stock_code, signal_date, formula_id, formula_variant, tech_stage, fund_stage in rows:
        kl = kline_by_code.get(stock_code)
        if kl is None:
            continue
        dates = kl["dates"]
        closes = kl["closes"]
        idx = int(np.searchsorted(dates, signal_date))
        if idx >= len(dates) or str(dates[idx]) != signal_date:
            continue
        entry_idx = idx + 1
        if entry_idx >= len(dates):
            continue
        entry_close = closes[entry_idx]
        if entry_close <= 0:
            continue
        for hd in HOLDING_DAYS:
            exit_idx = entry_idx + hd
            if exit_idx >= len(dates):
                continue
            exit_close = closes[exit_idx]
            if exit_close <= 0:
                continue
            ret = (exit_close - entry_close) / entry_close
            bucket_returns[(fund_stage, tech_stage, formula_id, formula_variant, hd)].append(float(ret))
    log.info(f"  逐信号计算: {time.time()-t2:.1f}s, 桶数 {len(bucket_returns):,}")

    # Step 4: 聚合 + 写库 (DELETE+INSERT+UPDATE 三步合并到一个事务, 防 SIGPIPE 半成品)
    t3 = time.time()
    written_rows = []
    for (fund, tech, fid, fvar, hd), returns in bucket_returns.items():
        if len(returns) < MIN_N_SIGNALS:
            continue
        ret_arr = np.array(returns)
        win_rate = float((ret_arr > 0).mean())
        avg_ret = float(ret_arr.mean())
        median_ret = float(np.median(ret_arr))
        neg = ret_arr[ret_arr < 0]
        avg_dd = float(neg.mean()) if len(neg) > 0 else 0.0
        std_ret = float(ret_arr.std())
        sharpe = float(avg_ret / std_ret * np.sqrt(252.0 / hd)) if std_ret > 0 else 0.0
        calmar = float(avg_ret / abs(avg_dd)) if avg_dd < 0 else 0.0
        written_rows.append(
            (fund, tech, fid, fvar, hd, len(returns), win_rate, avg_ret, avg_dd,
             calmar, sharpe, eval_start, eval_end)
        )

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM mart_stage_formula_fitness")
        conn.executemany(
            """
            INSERT INTO mart_stage_formula_fitness
              (fundamental_stage, technical_stage, formula_id, formula_variant, holding_days,
               n_signals, win_rate, avg_ret, avg_dd, calmar, sharpe,
               eval_start_date, eval_end_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            written_rows,
        )
        # 标记每个 (fund, tech, hd) 下的 best formula
        conn.execute(
            """
            UPDATE mart_stage_formula_fitness AS m
               SET rank_in_stage = sub.rk,
                   is_recommended = CASE WHEN sub.rk = 1 THEN TRUE ELSE FALSE END
              FROM (
                  SELECT fundamental_stage, technical_stage, formula_id, formula_variant, holding_days,
                         RANK() OVER (
                             PARTITION BY fundamental_stage, technical_stage, holding_days
                             ORDER BY win_rate DESC NULLS LAST, n_signals DESC
                         ) AS rk
                    FROM mart_stage_formula_fitness
              ) sub
             WHERE m.fundamental_stage = sub.fundamental_stage
               AND m.technical_stage   = sub.technical_stage
               AND m.formula_id        = sub.formula_id
               AND m.formula_variant   = sub.formula_variant
               AND m.holding_days      = sub.holding_days
            """
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    log.info(f"  写入 {len(written_rows):,} 行 ({time.time()-t3:.1f}s)")
    log.info(f"适配矩阵完成: {len(written_rows):,} 行 (总耗时 {time.time()-t0:.1f}s)")
    return len(written_rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--write-start", default=None,
                        help="technical_stage 写入替换窗口起点；--start 仍作为滚动计算预热起点")
    parser.add_argument("--skip-stage", action="store_true", help="跳过 technical_stage 重算 (复用已有)")
    parser.add_argument("--stage-only", action="store_true",
                        help="只刷新 fact_stock_technical_stage，不重建 mart_stage_formula_fitness")
    parser.add_argument("--allow-empty-stage-window", action="store_true",
                        help="允许 technical_stage 窗口 0 行时仍删除该窗口旧行")
    args = parser.parse_args()
    if args.stage_only and args.skip_stage:
        parser.error("--stage-only cannot be combined with --skip-stage")
    if args.skip_stage and args.write_start:
        parser.error("--write-start only applies when technical_stage is rebuilt")

    mkt_conn = duckdb.connect(str(MARKET_DB_PATH), read_only=True)
    if args.end is None:
        row = mkt_conn.execute("SELECT MAX(date) FROM v_price_kline_qfq WHERE adjust='qfq'").fetchone()
        kline_max = str(row[0]) if row and row[0] else None
        if not kline_max:
            raise RuntimeError("v_price_kline_qfq has no qfq rows; refuse hardcoded end-date fallback")
        cal_conn = get_conn()
        try:
            cal_max = latest_completed_trade_date(cal_conn)
        finally:
            cal_conn.close()
        if not cal_max:
            raise RuntimeError("latest_completed_trade_date returned None; refuse wall-clock fallback")
        args.end = min(kline_max, str(cal_max))
    log.info(f"区间 {args.start} - {args.end}")

    conn = get_conn()
    try:
        ensure_formula_tables(conn)

        if not args.skip_stage:
            build_technical_stage_history(
                mkt_conn,
                conn,
                args.start,
                args.end,
                write_start=args.write_start,
                allow_empty_window=args.allow_empty_stage_window,
            )
        else:
            n = conn.execute("SELECT COUNT(*) FROM fact_stock_technical_stage").fetchone()[0]
            log.info(f"--skip-stage: 复用现有 fact_stock_technical_stage {n} 行")
        if args.stage_only:
            log.info("--stage-only: 跳过 mart_stage_formula_fitness 重建")
            return
        # Python 内存版, mkt_conn 仍需用
        build_fitness_matrix(conn, mkt_conn, args.start, args.end)
    finally:
        conn.close()
        try:
            mkt_conn.close()
        except Exception as close_exc:
            log.warning("market connection close failed: %s", close_exc)


if __name__ == "__main__":
    main()

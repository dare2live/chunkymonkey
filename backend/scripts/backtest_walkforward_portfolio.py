#!/usr/bin/env python3
"""M8.1: Fold-level portfolio backtest

输入: walkforward run_id (mart_model_walkforward_prediction)
对每 fold × cost_bps × top_size 跑一次 portfolio simulation, 写入
mart_model_walkforward_portfolio_summary。复用 backtest_model_portfolio.py
的 simulate_curve / summarize_curve / 流动性过滤逻辑, 不重新发明。

晋级标准 (Codex §4.10): 5 fold 中至少 4/5 fold 的 portfolio 跑赢同 fold
benchmark_510300 (即 portfolio top_return > benchmark_510300 在该 fold 期),
且 median 跨 fold sharpe 不显著差于 baseline。
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from scripts.backtest_model_portfolio import (
    ensure_attached,
    simulate_curve,
    _group_by,
    _parse_csv_floats,
    _parse_csv_ints,
    _rank_sort_key,
    _records_from_cursor,
    _to_float,
)


logger = logging.getLogger("walkforward_portfolio")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_model_walkforward_portfolio_summary (
    run_id              TEXT NOT NULL,         -- 本次 portfolio backtest run_id
    walkforward_run_id  TEXT NOT NULL,
    fold_id             INTEGER NOT NULL,
    model_id            TEXT,
    test_start          DATE,
    test_end            DATE,
    test_market_state   TEXT,
    test_rank_ic        REAL,
    cost_bps            REAL NOT NULL,
    top_size            INTEGER NOT NULL,
    rebalance_days      INTEGER,
    final_nav           REAL,
    total_return        REAL,
    annualized_return   REAL,
    max_drawdown        REAL,
    sharpe              REAL,
    avg_turnover        REAL,
    rebalance_count     INTEGER,
    benchmark_510300_total_return REAL,
    excess_vs_510300_pp REAL,
    notes               TEXT,
    built_at            TEXT,
    PRIMARY KEY (run_id, fold_id, cost_bps, top_size)
);
"""


def load_fold_inputs(conn, walkforward_run_id: str, min_avg_amount: float):
    """读 walkforward fold 元数据 + predictions + 价格 + benchmark 510300 全段."""
    duck = conn.raw if hasattr(conn, "raw") else conn
    ensure_attached(duck)

    folds = _records_from_cursor(duck.execute(
        """
        SELECT run_id, fold_id, model_id, test_start, test_end,
               test_market_state, test_rank_ic
        FROM mart_model_walkforward_fold
        WHERE run_id = ?
        ORDER BY fold_id
        """,
        [walkforward_run_id],
    ))
    if not folds:
        raise RuntimeError(f"walkforward_run_id={walkforward_run_id} 无 fold 记录")
    logger.info("加载 %d folds, model_id=%s", len(folds), folds[0]["model_id"])

    test_start = min(str(row["test_start"]) for row in folds)
    test_end = max(str(row["test_end"]) for row in folds)

    preds = _records_from_cursor(duck.execute(
        """
        SELECT run_id, fold_id, stock_code, date, pred_score, rank_in_date, percentile
        FROM mart_model_walkforward_prediction
        WHERE run_id = ?
        ORDER BY fold_id, date, rank_in_date
        """,
        [walkforward_run_id],
    ))
    if not preds:
        raise RuntimeError(f"walkforward_run_id={walkforward_run_id} 无 prediction")
    logger.info("加载 prediction %d 行 / %d codes / %d 天",
                len(preds),
                len({row["stock_code"] for row in preds}),
                len({row["date"] for row in preds}))

    candidates_with_meta = _records_from_cursor(duck.execute(
        """
        WITH px AS (
            SELECT code, date, close, amount,
                   AVG(amount) OVER (PARTITION BY code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        )
        SELECT p.fold_id, p.stock_code, p.date, p.pred_score, p.rank_in_date,
               px.close, px.amount, px.amount_ma20
        FROM mart_model_walkforward_prediction p
        JOIN px ON px.code = p.stock_code AND px.date = p.date
        WHERE p.run_id = ?
          AND px.amount_ma20 >= ?
        ORDER BY p.fold_id, p.date, p.rank_in_date
        """,
        [test_start, test_end, walkforward_run_id, min_avg_amount],
    ))
    if not candidates_with_meta:
        raise RuntimeError("流动性过滤后无候选股票 (检查 min_avg_amount)")

    prices = _records_from_cursor(duck.execute(
        """
        WITH px_base AS (
            SELECT code, date, close, amount,
                   AVG(amount) OVER (PARTITION BY code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        ),
        candidate_codes AS (
            SELECT DISTINCT p.stock_code AS code
            FROM mart_model_walkforward_prediction p
            JOIN px_base px ON px.code = p.stock_code AND px.date = p.date
            WHERE p.run_id = ? AND px.amount_ma20 >= ?
        )
        SELECT p.code, p.date, p.close,
               p.close / NULLIF(LAG(p.close) OVER (PARTITION BY p.code ORDER BY p.date), 0) - 1 AS ret_1d
        FROM market.price_kline_tdxhub p
        JOIN candidate_codes c ON c.code = p.code
        WHERE p.freq='daily' AND p.adjust='qfq'
          AND p.date >= ? AND p.date <= ?
        ORDER BY p.date, p.code
        """,
        [test_start, test_end, walkforward_run_id, min_avg_amount, test_start, test_end],
    ))

    benchmark = _records_from_cursor(duck.execute(
        """
        WITH src AS (
            SELECT date, close FROM market.price_kline_tdxhub
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        )
        SELECT date, close FROM src ORDER BY date
        """,
        [test_start, test_end],
    ))

    return folds, candidates_with_meta, prices, benchmark


def build_returns_by_date(prices: list[dict]) -> dict[str, dict[str, float]]:
    return {
        date: {
            str(row.get("code")): (_to_float(row.get("ret_1d")) or 0.0)
            for row in rows
            if row.get("code")
        }
        for date, rows in _group_by(prices, "date").items()
    }


def fold_portfolio(
    *,
    fold_row: dict,
    candidates_fold: list[dict],
    returns_by_date: dict,
    cost_bps: float,
    top_size: int,
    rebalance_days: int,
    built_at: str,
) -> tuple[list[dict], dict]:
    """对单 fold 跑 portfolio."""
    test_start = str(fold_row["test_start"])
    test_end = str(fold_row["test_end"])

    # 限定到 fold test 窗口
    fold_dates = sorted({str(row.get("date")) for row in candidates_fold})
    fold_dates = [d for d in fold_dates if test_start <= d <= test_end]
    if not fold_dates:
        return [], {}

    # 调仓日 = 每 rebalance_days 取一次
    signal_dates = fold_dates[::rebalance_days]

    # select_codes: 按 signal_date 取 top-N
    cand_by_date = _group_by(candidates_fold, "date")

    def select(signal_date, _i):
        rows = cand_by_date.get(str(signal_date), [])
        if not rows:
            return []
        return [
            str(row.get("stock_code"))
            for row in sorted(rows, key=_rank_sort_key)[:top_size]
            if row.get("stock_code")
        ]

    fold_id = int(fold_row["fold_id"])
    curve_id = f"fold{fold_id:02d}_top{top_size}_{int(cost_bps)}bps"
    curve, summary = simulate_curve(
        curve_id=curve_id,
        curve_type=f"walkforward_fold_top{top_size}",
        model_id=fold_row.get("model_id"),
        benchmark_id=None,
        trading_dates=fold_dates,
        signal_dates=signal_dates,
        select_codes=select,
        returns_by_date=returns_by_date,
        cost_bps=cost_bps,
        rebalance_days=rebalance_days,
        built_at=built_at,
    )
    return curve, summary


def benchmark_total_return(benchmark: list[dict], test_start: str, test_end: str) -> float | None:
    rows = [
        row for row in benchmark
        if test_start <= str(row.get("date")) <= test_end and _to_float(row.get("close")) is not None
    ]
    rows.sort(key=lambda row: str(row.get("date")))
    if len(rows) < 2:
        return None
    first = _to_float(rows[0].get("close"))
    last = _to_float(rows[-1].get("close"))
    if first is None or last is None or first <= 0:
        return None
    return float(last / first - 1.0)


def _fmt_float(value, spec: str, *, default: str = "-") -> str:
    number = _to_float(value)
    if number is None:
        return default
    return format(number, spec)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--walkforward-run-id", required=True,
                        help="mart_model_walkforward_prediction 的 run_id")
    parser.add_argument("--cost-bps", default="15,30,50",
                        help="comma separated one-way bps (default 15,30,50)")
    parser.add_argument("--top-sizes", default="20,50",
                        help="comma separated top-K (default 20,50)")
    parser.add_argument("--rebalance-days", type=int, default=20)
    parser.add_argument("--min-avg-amount", type=float, default=2e7,
                        help="近20日均成交额下限, 流动性过滤")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cost_bps_list = _parse_csv_floats(args.cost_bps)
    top_sizes = _parse_csv_ints(args.top_sizes)

    conn = get_conn()
    try:
        folds, cands, prices, bench = load_fold_inputs(
            conn, args.walkforward_run_id, args.min_avg_amount,
        )
        returns_by_date = build_returns_by_date(prices)
        if args.dry_run:
            logger.info("dry-run: %d folds × %d cost × %d top = %d combos",
                        len(folds), len(cost_bps_list), len(top_sizes),
                        len(folds) * len(cost_bps_list) * len(top_sizes))
            return

        run_id = f"wf_portfolio_{args.walkforward_run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        built_at = datetime.utcnow().isoformat()
        rows: list[dict] = []

        for fold in folds:
            cands_fold = [row for row in cands if str(row.get("fold_id")) == str(fold["fold_id"])]
            bench_ret = benchmark_total_return(
                bench, str(fold["test_start"]), str(fold["test_end"]),
            )
            for top_size in top_sizes:
                for cost in cost_bps_list:
                    _, summary = fold_portfolio(
                        fold_row=fold,
                        candidates_fold=cands_fold,
                        returns_by_date=returns_by_date,
                        cost_bps=cost,
                        top_size=top_size,
                        rebalance_days=args.rebalance_days,
                        built_at=built_at,
                    )
                    if not summary:
                        continue
                    excess = (summary["total_return"] - bench_ret) * 100 if bench_ret is not None else None
                    rows.append({
                        "run_id": run_id,
                        "walkforward_run_id": args.walkforward_run_id,
                        "fold_id": int(fold["fold_id"]),
                        "model_id": fold.get("model_id"),
                        "test_start": fold["test_start"],
                        "test_end": fold["test_end"],
                        "test_market_state": fold.get("test_market_state"),
                        "test_rank_ic": fold.get("test_rank_ic"),
                        "cost_bps": cost,
                        "top_size": top_size,
                        "rebalance_days": args.rebalance_days,
                        "final_nav": summary["final_nav"],
                        "total_return": summary["total_return"],
                        "annualized_return": summary["annualized_return"],
                        "max_drawdown": summary["max_drawdown"],
                        "sharpe": summary["sharpe"],
                        "avg_turnover": summary["avg_turnover"],
                        "rebalance_count": summary["rebalance_count"],
                        "benchmark_510300_total_return": bench_ret,
                        "excess_vs_510300_pp": excess,
                        "notes": None,
                        "built_at": built_at,
                    })

        if not rows:
            logger.warning("no walkforward portfolio rows generated, run_id=%s", run_id)
            return

        # 写库
        conn.executescript(DDL)
        cols = list(rows[0].keys())
        conn.execute(
            f"DELETE FROM mart_model_walkforward_portfolio_summary WHERE run_id = ?",
            [run_id],
        )
        for r in rows:
            conn.execute(
                f"INSERT INTO mart_model_walkforward_portfolio_summary "
                f"({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                tuple(r[c] for c in cols),
            )
        conn.commit()
        logger.info("写入 %d 行 mart_model_walkforward_portfolio_summary, run_id=%s",
                    len(rows), run_id)

        # 验收摘要: 30bps × top20
        sub = [
            row for row in rows
            if row["cost_bps"] == 30 and row["top_size"] == 20
        ]
        sub.sort(key=lambda row: row["fold_id"])
        logger.info("=" * 78)
        logger.info("Fold-level portfolio (top20, 30bps):")
        logger.info(
            f"  {'fold':>4s} {'state':>4s} {'RankIC':>7s} {'total_ret':>9s} "
            f"{'ann':>7s} {'MaxDD':>7s} {'Sharpe':>6s} {'510300':>7s} {'excess':>8s}"
        )
        positive = 0
        for r in sub:
            tag = ""
            excess = _to_float(r["excess_vs_510300_pp"])
            if excess is not None and excess > 0:
                positive += 1
                tag = " ✓"
            logger.info(
                f"  {int(r['fold_id']):>4d} {str(r.get('test_market_state') or '-'):>4s} "
                f"{_fmt_float(r.get('test_rank_ic'), '+7.4f', default='      -')} "
                f"{_fmt_float(r.get('total_return'), '+9.3f', default='        -')} "
                f"{_fmt_float(r.get('annualized_return'), '+7.3f', default='      -')} "
                f"{_fmt_float(r.get('max_drawdown'), '+7.3f', default='      -')} "
                f"{_fmt_float(r.get('sharpe'), '+6.2f', default='     -')} "
                f"{_fmt_float(r.get('benchmark_510300_total_return'), '+7.3f', default='      -')} "
                f"{_fmt_float(r.get('excess_vs_510300_pp'), '+7.2f', default='      -')}pp{tag}"
            )
        logger.info(
            "Codex 晋级标准: 5 fold 至少 4/5 跑赢 510300. 当前: %d/%d %s",
            positive, len(sub), "✓ 过线" if positive >= 4 else "✗ 不过线",
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()

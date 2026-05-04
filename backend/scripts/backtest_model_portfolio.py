#!/usr/bin/env python3
"""Backtest multidim model top-N portfolios against simple tradable baselines."""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.ml_lifecycle.registry import select_default_model_id


logger = logging.getLogger("model_portfolio_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


ROOT = Path(__file__).resolve().parent.parent.parent
MARKET_DB = ROOT / "data" / "market.duckdb"
ETF_DB = ROOT / "data" / "etf.duckdb"


DDL = """
CREATE TABLE IF NOT EXISTS mart_model_portfolio_curve (
    run_id TEXT NOT NULL,
    curve_id TEXT NOT NULL,
    curve_type TEXT,
    model_id TEXT,
    benchmark_id TEXT,
    date TEXT NOT NULL,
    nav REAL,
    daily_ret REAL,
    turnover REAL,
    holdings_count INTEGER,
    cost_bps REAL,
    rebalance_days INTEGER,
    built_at TEXT,
    PRIMARY KEY (run_id, curve_id, date)
);
CREATE INDEX IF NOT EXISTS idx_mmpc_curve ON mart_model_portfolio_curve(curve_id, date);

CREATE TABLE IF NOT EXISTS mart_model_portfolio_summary (
    run_id TEXT NOT NULL,
    curve_id TEXT NOT NULL,
    curve_type TEXT,
    model_id TEXT,
    benchmark_id TEXT,
    start_date TEXT,
    end_date TEXT,
    cost_bps REAL,
    rebalance_days INTEGER,
    final_nav REAL,
    total_return REAL,
    annualized_return REAL,
    max_drawdown REAL,
    sharpe REAL,
    avg_turnover REAL,
    rebalance_count INTEGER,
    vs_random_l1_p90_pp REAL,
    notes TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, curve_id)
);
ALTER TABLE mart_model_portfolio_summary ADD COLUMN IF NOT EXISTS vs_random_l1_p90_pp REAL;
"""


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_csv_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def latest_model_id(conn) -> str:
    model_id, _fallback = select_default_model_id(conn)
    if model_id:
        return model_id
    row = conn.execute("SELECT model_id FROM mart_multidim_model ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model 无记录")
    return row[0]


def ensure_attached(duck) -> None:
    duck.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
    if ETF_DB.exists():
        duck.execute(f"ATTACH IF NOT EXISTS '{ETF_DB}' AS etf (READ_ONLY)")


def _records_from_cursor(cursor: Any) -> list[dict[str, Any]]:
    names = [desc[0] for desc in (cursor.description or [])]
    return [
        {name: value for name, value in zip(names, row)}
        for row in cursor.fetchall()
    ]


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return value != value
    except Exception:
        return False


def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _group_by(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key)), []).append(row)
    return grouped


def _rank_sort_key(row: dict[str, Any]) -> tuple[bool, float, str]:
    rank = _to_float(row.get("rank_in_date"))
    return (rank is None, rank or 0.0, str(row.get("stock_code") or row.get("code") or ""))


def _industry_key(row: dict[str, Any]) -> str:
    industry = row.get("tdx_l1")
    if _is_missing(industry) or industry == "":
        return "_UNKNOWN"
    return str(industry)


def _curve_id(curve: list[dict], fallback: str) -> str:
    if not curve:
        return fallback
    return str(curve[0].get("curve_id") or fallback)


def load_inputs(conn, model_id: str, min_avg_amount: float) -> tuple[list[dict], list[dict], list[dict]]:
    duck = conn.raw if hasattr(conn, "raw") else conn
    ensure_attached(duck)
    pred_bounds = duck.execute(
        "SELECT MIN(date), MAX(date), COUNT(DISTINCT date) FROM mart_multidim_prediction WHERE model_id = ?",
        [model_id],
    ).fetchone()
    if not pred_bounds or not pred_bounds[0]:
        raise RuntimeError(f"模型 {model_id} 没有 mart_multidim_prediction")
    start, end, n_dates = pred_bounds
    logger.info("model prediction window: %s ~ %s (%s dates)", start, end, n_dates)

    candidates = _records_from_cursor(duck.execute(
        """
        WITH px AS (
            SELECT code, date, close, amount,
                   AVG(amount) OVER (PARTITION BY code ORDER BY date ROWS 19 PRECEDING) AS amount_ma20
            FROM market.price_kline_tdxhub
            WHERE freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        )
        SELECT p.model_id, p.stock_code, p.date, p.pred_score, p.rank_in_date, p.percentile,
               fp.forward_ret_20d, fp.regime_flag,
               ind.tdx_l1, ind.tdx_l1_name,
               px.close, px.amount, px.amount_ma20
        FROM mart_multidim_prediction p
        JOIN px ON px.code = p.stock_code AND px.date = p.date
        LEFT JOIN fact_feature_panel fp ON fp.stock_code = p.stock_code AND fp.date = p.date
        LEFT JOIN dim_stock_tdx_industry ind ON ind.stock_code = p.stock_code
        WHERE p.model_id = ?
          AND px.amount_ma20 >= ?
        ORDER BY p.date, p.rank_in_date
        """,
        [start, end, model_id, min_avg_amount],
    ))
    if not candidates:
        raise RuntimeError("流动性过滤后无候选股票")

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
            FROM mart_multidim_prediction p
            JOIN px_base px ON px.code = p.stock_code AND px.date = p.date
            WHERE p.model_id = ? AND px.amount_ma20 >= ?
        )
        SELECT p.code, p.date, p.close, p.amount,
               p.close / NULLIF(LAG(p.close) OVER (PARTITION BY p.code ORDER BY p.date), 0) - 1 AS ret_1d,
               AVG(p.amount) OVER (PARTITION BY p.code ORDER BY p.date ROWS 19 PRECEDING) AS amount_ma20
        FROM market.price_kline_tdxhub p
        JOIN candidate_codes c ON c.code = p.code
        WHERE p.freq='daily' AND p.adjust='qfq'
          AND p.date >= ? AND p.date <= ?
        ORDER BY p.date, p.code
        """,
        [start, end, model_id, min_avg_amount, start, end],
    ))

    benchmark_sql = """
        WITH src AS (
            SELECT date, close FROM market.price_kline_tdxhub
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
            UNION ALL
            SELECT date, close FROM market.price_kline
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
            {etf_union}
        ),
        dedup AS (
            SELECT date, close, ROW_NUMBER() OVER (PARTITION BY date ORDER BY close DESC) rn
            FROM src
        )
        SELECT date, close FROM dedup WHERE rn=1 ORDER BY date
    """
    etf_union = ""
    params = [start, end, start, end]
    if ETF_DB.exists():
        etf_union = """
            UNION ALL
            SELECT date, close FROM etf.etf_price_kline
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        """
        params.extend([start, end])
    benchmark = _records_from_cursor(duck.execute(benchmark_sql.format(etf_union=etf_union), params))
    return candidates, prices, benchmark


def _rebalance_signal_dates(pred_dates: list[str], rebalance_days: int) -> list[str]:
    return pred_dates[::rebalance_days]


def _next_trading_date(trading_dates: list[str], signal_date: str) -> str | None:
    for d in trading_dates:
        if d > signal_date:
            return d
    return None


def _weights(codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    w = 1.0 / len(codes)
    return {c: w for c in codes}


def _turnover(old: dict[str, float], new: dict[str, float]) -> float:
    keys = set(old) | set(new)
    return sum(abs(new.get(k, 0.0) - old.get(k, 0.0)) for k in keys)


def simulate_curve(
    *,
    curve_id: str,
    curve_type: str,
    model_id: str | None,
    benchmark_id: str | None,
    trading_dates: list[str],
    signal_dates: list[str],
    select_codes,
    returns_by_date: dict[str, dict[str, float]],
    cost_bps: float,
    rebalance_days: int,
    built_at: str,
) -> tuple[list[dict], dict]:
    exec_plan: dict[str, list[str]] = {}
    for i, signal_date in enumerate(signal_dates):
        exec_date = _next_trading_date(trading_dates, signal_date)
        if not exec_date:
            continue
        exec_plan[exec_date] = select_codes(signal_date, i)

    nav = 1.0
    current: dict[str, float] = {}
    records: list[dict] = []
    turnovers: list[float] = []
    for date in trading_dates:
        day_turnover = 0.0
        if date in exec_plan:
            new = _weights(exec_plan[date])
            day_turnover = _turnover(current, new)
            nav *= max(0.0, 1.0 - day_turnover * cost_bps / 10000.0)
            current = new
            turnovers.append(day_turnover)

        ret_map = returns_by_date.get(date, {})
        daily_ret = sum(w * (ret_map.get(code) or 0.0) for code, w in current.items())
        nav *= 1.0 + daily_ret
        records.append({
            "curve_id": curve_id,
            "curve_type": curve_type,
            "model_id": model_id,
            "benchmark_id": benchmark_id,
            "date": date,
            "nav": nav,
            "daily_ret": daily_ret - day_turnover * cost_bps / 10000.0,
            "turnover": day_turnover,
            "holdings_count": len(current),
            "cost_bps": cost_bps,
            "rebalance_days": rebalance_days,
            "built_at": built_at,
        })
    return records, summarize_curve(records, turnovers)


def summarize_curve(curve: list[dict], turnovers: list[float]) -> dict:
    if not curve:
        return {}
    nav = [_to_float(row.get("nav")) or 0.0 for row in curve]
    daily = [_to_float(row.get("daily_ret")) or 0.0 for row in curve]
    n = max(len(curve), 1)
    total = float(nav[-1] - 1.0)
    annual = float(nav[-1] ** (252.0 / n) - 1.0) if nav[-1] > 0 else None
    peak = 0.0
    max_dd = 0.0
    for value in nav:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    sharpe = None
    daily_std = _sample_std(daily)
    if daily_std > 0:
        sharpe = float(_mean(daily) / daily_std * math.sqrt(252))
    return {
        "start_date": str(curve[0]["date"]),
        "end_date": str(curve[-1]["date"]),
        "final_nav": float(nav[-1]),
        "total_return": total,
        "annualized_return": annual,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "avg_turnover": _mean(turnovers) if turnovers else 0.0,
        "rebalance_count": len(turnovers),
    }


def benchmark_510300_curve(
    benchmark: list[dict],
    *,
    curve_id: str,
    built_at: str,
    cost_bps: float,
    rebalance_days: int,
) -> list[dict]:
    rows = []
    nav = 1.0
    prev_close: float | None = None
    for row in benchmark:
        close = _to_float(row.get("close"))
        if close is None:
            continue
        daily_ret = 0.0 if prev_close is None or prev_close <= 0 else close / prev_close - 1.0
        nav *= 1.0 + daily_ret
        rows.append({
            "curve_id": curve_id,
            "curve_type": "benchmark",
            "model_id": None,
            "benchmark_id": "benchmark_510300_etf",
            "date": str(row.get("date")),
            "nav": nav,
            "daily_ret": daily_ret,
            "turnover": 0.0,
            "holdings_count": 1,
            "cost_bps": cost_bps,
            "rebalance_days": rebalance_days,
            "built_at": built_at,
        })
        prev_close = close
    return rows


def _linear_percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _annotate_vs_random_p90(summaries: list[dict]) -> None:
    """M6.2 (Q15): 为每条 summary 计算 vs_random_l1_p90_pp = total_return - random_l1 p90.
    仅对 model_* 和 benchmark_510300_*/benchmark_liquid500_* 填充; random 自身不填.
    p90 用 linear 内插, 固定口径避免 15.9 vs 19.3 这种差异.
    """
    # 按 cost_bps 分桶, 每桶取 random_l1 的 total_return 列表
    by_cost: dict[float, list[float]] = {}
    for s in summaries:
        cid = (s.get("curve_id") or "")
        if not cid.startswith("benchmark_random_l1_seed_"):
            continue
        ret = s.get("total_return")
        if ret is None:
            continue
        by_cost.setdefault(float(s["cost_bps"]), []).append(float(ret))
    p90: dict[float, float] = {}
    for cost, vals in by_cost.items():
        if vals:
            p90[cost] = _linear_percentile(vals, 90.0)
    # 填充
    for s in summaries:
        cid = (s.get("curve_id") or "")
        if cid.startswith("benchmark_random_l1_seed_"):
            s["vs_random_l1_p90_pp"] = None
            continue
        ret = s.get("total_return")
        cost = s.get("cost_bps")
        if ret is None or cost is None or cost not in p90:
            s["vs_random_l1_p90_pp"] = None
            continue
        s["vs_random_l1_p90_pp"] = (float(ret) - p90[cost]) * 100.0


def write_results(conn, run_id: str, curves: list[list[dict]], summaries: list[dict], dry_run: bool) -> None:
    if dry_run:
        logger.info("dry-run: 不写 mart_model_portfolio_*")
        return
    # M6.2: 落库前先计算 vs_random_l1_p90_pp (固定 linear 分位口径)
    _annotate_vs_random_p90(summaries)
    conn.executescript(DDL)
    conn.execute("DELETE FROM mart_model_portfolio_curve WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_model_portfolio_summary WHERE run_id = ?", (run_id,))
    for curve in curves:
        conn.executemany(
            """
            INSERT INTO mart_model_portfolio_curve
            (run_id, curve_id, curve_type, model_id, benchmark_id, date, nav,
             daily_ret, turnover, holdings_count, cost_bps, rebalance_days, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row.get("curve_id"),
                    row.get("curve_type"),
                    row.get("model_id"),
                    row.get("benchmark_id"),
                    row.get("date"),
                    row.get("nav"),
                    row.get("daily_ret"),
                    row.get("turnover"),
                    row.get("holdings_count"),
                    row.get("cost_bps"),
                    row.get("rebalance_days"),
                    row.get("built_at"),
                )
                for row in curve
            ],
        )
    for summary in summaries:
        cols = [
            "run_id", "curve_id", "curve_type", "model_id", "benchmark_id",
            "start_date", "end_date", "cost_bps", "rebalance_days",
            "final_nav", "total_return", "annualized_return", "max_drawdown",
            "sharpe", "avg_turnover", "rebalance_count", "notes", "built_at",
            "vs_random_l1_p90_pp",
        ]
        conn.execute(
            f"INSERT INTO mart_model_portfolio_summary ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
            tuple(summary.get(c) for c in cols),
        )
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--cost-bps", default="15,30,50", help="comma separated one-way bps")
    parser.add_argument("--top-sizes", default="20,50")
    parser.add_argument("--rebalance-days", type=int, default=20)
    parser.add_argument("--min-avg-amount", type=float, default=20_000_000)
    parser.add_argument("--random-seeds", type=int, default=100,
                        help="行业中性随机基线 seed 数 (M7: 默认 100, 降低 p90 抽样噪声)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        model_id = args.model_id or latest_model_id(conn)
        candidates, prices, benchmark = load_inputs(conn, model_id, args.min_avg_amount)
        pred_dates = sorted({str(row.get("date")) for row in candidates})
        trading_dates = sorted({str(row.get("date")) for row in prices})
        signal_dates = _rebalance_signal_dates(pred_dates, args.rebalance_days)
        logger.info("signals=%d trading_dates=%d candidates=%d", len(signal_dates), len(trading_dates), len(candidates))

        prices_by_date = _group_by(prices, "date")
        returns_by_date = {
            date: {
                str(row.get("code")): (_to_float(row.get("ret_1d")) or 0.0)
                for row in rows
                if row.get("code")
            }
            for date, rows in prices_by_date.items()
        }
        price_by_signal = prices_by_date
        cand_by_date = _group_by(candidates, "date")
        built_at = datetime.utcnow().isoformat()
        run_id = f"portfolio_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        curves: list[list[dict]] = []
        summaries: list[dict] = []

        for cost_bps in _parse_csv_floats(args.cost_bps):
            for top_n in _parse_csv_ints(args.top_sizes):
                def select_model(signal_date: str, _idx: int, n=top_n) -> list[str]:
                    rows = cand_by_date.get(signal_date, [])
                    if not rows:
                        return []
                    return [
                        str(row.get("stock_code"))
                        for row in sorted(rows, key=_rank_sort_key)[:n]
                        if row.get("stock_code")
                    ]

                curve_id = f"model_top{top_n}_{int(cost_bps)}bps"
                curve, summary = simulate_curve(
                    curve_id=curve_id,
                    curve_type=f"model_top{top_n}",
                    model_id=model_id,
                    benchmark_id=None,
                    trading_dates=trading_dates,
                    signal_dates=signal_dates,
                    select_codes=select_model,
                    returns_by_date=returns_by_date,
                    cost_bps=cost_bps,
                    rebalance_days=args.rebalance_days,
                    built_at=built_at,
                )
                curves.append(curve)
                summaries.append({
                    "run_id": run_id, "curve_id": _curve_id(curve, curve_id),
                    "curve_type": f"model_top{top_n}", "model_id": model_id, "benchmark_id": None,
                    "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                    "notes": json.dumps({"min_avg_amount": args.min_avg_amount}, ensure_ascii=False),
                    "built_at": built_at, **summary,
                })

            def select_liquid(signal_date: str, _idx: int) -> list[str]:
                rows = price_by_signal.get(signal_date, [])
                if not rows:
                    return []
                liquid_rows = [
                    row for row in rows
                    if (_to_float(row.get("amount_ma20")) or 0.0) >= args.min_avg_amount
                ]
                liquid_rows.sort(key=lambda row: (_to_float(row.get("amount_ma20")) or 0.0), reverse=True)
                return [
                    str(row.get("code"))
                    for row in liquid_rows[:500]
                    if row.get("code")
                ]

            curve_id = f"benchmark_liquid500_eq_{int(cost_bps)}bps"
            curve, summary = simulate_curve(
                curve_id=curve_id,
                curve_type="benchmark",
                model_id=None,
                benchmark_id="benchmark_liquid500_eq",
                trading_dates=trading_dates,
                signal_dates=signal_dates,
                select_codes=select_liquid,
                returns_by_date=returns_by_date,
                cost_bps=cost_bps,
                rebalance_days=args.rebalance_days,
                built_at=built_at,
            )
            curves.append(curve)
            summaries.append({
                "run_id": run_id, "curve_id": _curve_id(curve, curve_id),
                "curve_type": "benchmark", "model_id": None, "benchmark_id": "benchmark_liquid500_eq",
                "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                "notes": json.dumps({"min_avg_amount": args.min_avg_amount}, ensure_ascii=False),
                "built_at": built_at, **summary,
            })

            if benchmark:
                curve_id = f"benchmark_510300_etf_{int(cost_bps)}bps"
                curve = benchmark_510300_curve(
                    benchmark,
                    curve_id=curve_id,
                    built_at=built_at,
                    cost_bps=cost_bps,
                    rebalance_days=args.rebalance_days,
                )
                summary = summarize_curve(curve, [])
                curves.append(curve)
                summaries.append({
                    "run_id": run_id, "curve_id": _curve_id(curve, curve_id),
                    "curve_type": "benchmark", "model_id": None, "benchmark_id": "benchmark_510300_etf",
                    "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                    "notes": "510300 tradable ETF proxy", "built_at": built_at, **summary,
                })

            for seed in range(args.random_seeds):
                rng = random.Random(seed)

                def select_random(signal_date: str, idx: int, rng=rng) -> list[str]:
                    rows = cand_by_date.get(signal_date, [])
                    if not rows:
                        return []
                    top = sorted(rows, key=_rank_sort_key)[:20]
                    counts = Counter(_industry_key(row) for row in top)
                    picks: list[str] = []
                    for industry, count in counts.items():
                        pool = [
                            str(row.get("stock_code"))
                            for row in rows
                            if _industry_key(row) == industry and row.get("stock_code")
                        ]
                        if pool:
                            picks.extend(rng.sample(pool, min(count, len(pool))))
                    if len(picks) < 20:
                        rest = [
                            str(row.get("stock_code"))
                            for row in rows
                            if row.get("stock_code") and str(row.get("stock_code")) not in picks
                        ]
                        if rest:
                            picks.extend(rng.sample(rest, min(20 - len(picks), len(rest))))
                    return picks[:20]

                curve_id = f"benchmark_random_l1_seed_{seed:02d}_{int(cost_bps)}bps"
                curve, summary = simulate_curve(
                    curve_id=curve_id,
                    curve_type="random",
                    model_id=None,
                    benchmark_id=f"benchmark_random_l1_seed_{seed:02d}",
                    trading_dates=trading_dates,
                    signal_dates=signal_dates,
                    select_codes=select_random,
                    returns_by_date=returns_by_date,
                    cost_bps=cost_bps,
                    rebalance_days=args.rebalance_days,
                    built_at=built_at,
                )
                curves.append(curve)
                summaries.append({
                    "run_id": run_id, "curve_id": _curve_id(curve, curve_id),
                    "curve_type": "random", "model_id": None,
                    "benchmark_id": f"benchmark_random_l1_seed_{seed:02d}",
                    "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                    "notes": "TDX L1 neutral random top20", "built_at": built_at, **summary,
                })

        logger.info("run_id=%s curves=%d summaries=%d", run_id, len(curves), len(summaries))
        for row in sorted(summaries, key=lambda x: (x["cost_bps"], x["curve_type"], x["curve_id"]))[:20]:
            logger.info("%s nav=%.3f ann=%s dd=%.2f%% sharpe=%s",
                        row["curve_id"], row["final_nav"],
                        None if row["annualized_return"] is None else f"{row['annualized_return']:.2%}",
                        row["max_drawdown"] * 100,
                        None if row["sharpe"] is None else f"{row['sharpe']:.2f}")
        write_results(conn, run_id, curves, summaries, args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

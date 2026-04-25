#!/usr/bin/env python3
"""Backtest multidim model top-N portfolios against simple tradable baselines."""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from services.db import get_conn


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
    notes TEXT,
    built_at TEXT,
    PRIMARY KEY (run_id, curve_id)
);
"""


def _parse_csv_ints(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_csv_floats(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def latest_model_id(conn) -> str:
    row = conn.execute("SELECT model_id FROM mart_multidim_model ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model 无记录")
    return row[0]


def ensure_attached(duck) -> None:
    duck.execute(f"ATTACH IF NOT EXISTS '{MARKET_DB}' AS market (READ_ONLY)")
    if ETF_DB.exists():
        duck.execute(f"ATTACH IF NOT EXISTS '{ETF_DB}' AS etf (READ_ONLY)")


def load_inputs(conn, model_id: str, min_avg_amount: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
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

    candidates = duck.execute(
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
    ).df()
    if candidates.empty:
        raise RuntimeError("流动性过滤后无候选股票")

    codes = candidates["stock_code"].dropna().unique().tolist()
    duck.register("_bt_codes", pd.DataFrame({"code": codes}))
    prices = duck.execute(
        """
        SELECT p.code, p.date, p.close, p.amount,
               p.close / NULLIF(LAG(p.close) OVER (PARTITION BY p.code ORDER BY p.date), 0) - 1 AS ret_1d,
               AVG(p.amount) OVER (PARTITION BY p.code ORDER BY p.date ROWS 19 PRECEDING) AS amount_ma20
        FROM market.price_kline_tdxhub p
        JOIN _bt_codes c ON c.code = p.code
        WHERE p.freq='daily' AND p.adjust='qfq'
          AND p.date >= ? AND p.date <= ?
        ORDER BY p.date, p.code
        """,
        [start, end],
    ).df()
    duck.unregister("_bt_codes")

    benchmark = duck.execute(
        """
        WITH src AS (
            SELECT date, close FROM market.price_kline_tdxhub
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
            UNION ALL
            SELECT date, close FROM market.price_kline
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
            UNION ALL
            SELECT date, close FROM etf.etf_price_kline
            WHERE code='510300' AND freq='daily' AND adjust='qfq'
              AND date >= ? AND date <= ?
        ),
        dedup AS (
            SELECT date, close, ROW_NUMBER() OVER (PARTITION BY date ORDER BY close DESC) rn
            FROM src
        )
        SELECT date, close FROM dedup WHERE rn=1 ORDER BY date
        """,
        [start, end, start, end, start, end],
    ).df()
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
) -> tuple[pd.DataFrame, dict]:
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
    curve = pd.DataFrame(records)
    return curve, summarize_curve(curve, turnovers)


def summarize_curve(curve: pd.DataFrame, turnovers: list[float]) -> dict:
    if curve.empty:
        return {}
    nav = curve["nav"].astype(float)
    daily = curve["daily_ret"].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0)
    n = max(len(curve), 1)
    total = float(nav.iloc[-1] - 1.0)
    annual = float(nav.iloc[-1] ** (252.0 / n) - 1.0) if nav.iloc[-1] > 0 else None
    peak = nav.cummax()
    max_dd = float((nav / peak - 1.0).min())
    sharpe = None
    if daily.std(ddof=1) > 0:
        sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252))
    return {
        "start_date": str(curve["date"].iloc[0]),
        "end_date": str(curve["date"].iloc[-1]),
        "final_nav": float(nav.iloc[-1]),
        "total_return": total,
        "annualized_return": annual,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "avg_turnover": float(np.mean(turnovers)) if turnovers else 0.0,
        "rebalance_count": len(turnovers),
    }


def benchmark_510300_curve(
    benchmark: pd.DataFrame,
    *,
    curve_id: str,
    built_at: str,
    cost_bps: float,
    rebalance_days: int,
) -> pd.DataFrame:
    df = benchmark.copy()
    df["daily_ret"] = df["close"].astype(float).pct_change().fillna(0.0)
    df["nav"] = (1.0 + df["daily_ret"]).cumprod()
    df["curve_id"] = curve_id
    df["curve_type"] = "benchmark"
    df["model_id"] = None
    df["benchmark_id"] = "benchmark_510300_etf"
    df["turnover"] = 0.0
    df["holdings_count"] = 1
    df["cost_bps"] = cost_bps
    df["rebalance_days"] = rebalance_days
    df["built_at"] = built_at
    return df[[
        "curve_id", "curve_type", "model_id", "benchmark_id", "date", "nav", "daily_ret",
        "turnover", "holdings_count", "cost_bps", "rebalance_days", "built_at",
    ]]


def _annotate_vs_random_p90(summaries: list[dict]) -> None:
    """M6.2 (Q15): 为每条 summary 计算 vs_random_l1_p90_pp = total_return - random_l1 p90.
    仅对 model_* 和 benchmark_510300_*/benchmark_liquid500_* 填充; random 自身不填.
    p90 用 NumPy linear 内插, 固定口径避免 15.9 vs 19.3 这种差异.
    """
    import numpy as _np
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
            p90[cost] = float(_np.percentile(vals, 90, method="linear"))
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


def write_results(conn, run_id: str, curves: list[pd.DataFrame], summaries: list[dict], dry_run: bool) -> None:
    if dry_run:
        logger.info("dry-run: 不写 mart_model_portfolio_*")
        return
    # M6.2: 落库前先计算 vs_random_l1_p90_pp (固定 NumPy linear 分位口径)
    _annotate_vs_random_p90(summaries)
    duck = conn.raw if hasattr(conn, "raw") else conn
    conn.executescript(DDL)
    conn.execute("DELETE FROM mart_model_portfolio_curve WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_model_portfolio_summary WHERE run_id = ?", (run_id,))
    for curve in curves:
        out = curve.copy()
        out.insert(0, "run_id", run_id)
        duck.register("_curve_out", out)
        duck.execute("INSERT INTO mart_model_portfolio_curve SELECT * FROM _curve_out")
        duck.unregister("_curve_out")
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
    parser.add_argument("--random-seeds", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        model_id = args.model_id or latest_model_id(conn)
        candidates, prices, benchmark = load_inputs(conn, model_id, args.min_avg_amount)
        pred_dates = sorted(candidates["date"].astype(str).unique().tolist())
        trading_dates = sorted(prices["date"].astype(str).unique().tolist())
        signal_dates = _rebalance_signal_dates(pred_dates, args.rebalance_days)
        logger.info("signals=%d trading_dates=%d candidates=%d", len(signal_dates), len(trading_dates), len(candidates))

        returns_by_date = {
            date: dict(zip(g["code"], g["ret_1d"].fillna(0.0)))
            for date, g in prices.groupby("date")
        }
        price_by_signal = {date: g.copy() for date, g in prices.groupby("date")}
        cand_by_date = {date: g.copy() for date, g in candidates.groupby("date")}
        built_at = datetime.utcnow().isoformat()
        run_id = f"portfolio_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        curves: list[pd.DataFrame] = []
        summaries: list[dict] = []

        for cost_bps in _parse_csv_floats(args.cost_bps):
            for top_n in _parse_csv_ints(args.top_sizes):
                def select_model(signal_date: str, _idx: int, n=top_n) -> list[str]:
                    g = cand_by_date.get(signal_date)
                    if g is None:
                        return []
                    return g.sort_values("rank_in_date")["stock_code"].head(n).tolist()

                curve, summary = simulate_curve(
                    curve_id=f"model_top{top_n}_{int(cost_bps)}bps",
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
                    "run_id": run_id, "curve_id": curve["curve_id"].iloc[0],
                    "curve_type": f"model_top{top_n}", "model_id": model_id, "benchmark_id": None,
                    "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                    "notes": json.dumps({"min_avg_amount": args.min_avg_amount}, ensure_ascii=False),
                    "built_at": built_at, **summary,
                })

            def select_liquid(signal_date: str, _idx: int) -> list[str]:
                g = price_by_signal.get(signal_date)
                if g is None:
                    return []
                g = g[g["amount_ma20"] >= args.min_avg_amount]
                return g.sort_values("amount_ma20", ascending=False)["code"].head(500).tolist()

            curve, summary = simulate_curve(
                curve_id=f"benchmark_liquid500_eq_{int(cost_bps)}bps",
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
                "run_id": run_id, "curve_id": curve["curve_id"].iloc[0],
                "curve_type": "benchmark", "model_id": None, "benchmark_id": "benchmark_liquid500_eq",
                "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                "notes": json.dumps({"min_avg_amount": args.min_avg_amount}, ensure_ascii=False),
                "built_at": built_at, **summary,
            })

            if not benchmark.empty:
                curve = benchmark_510300_curve(
                    benchmark,
                    curve_id=f"benchmark_510300_etf_{int(cost_bps)}bps",
                    built_at=built_at,
                    cost_bps=cost_bps,
                    rebalance_days=args.rebalance_days,
                )
                summary = summarize_curve(curve, [])
                curves.append(curve)
                summaries.append({
                    "run_id": run_id, "curve_id": curve["curve_id"].iloc[0],
                    "curve_type": "benchmark", "model_id": None, "benchmark_id": "benchmark_510300_etf",
                    "cost_bps": cost_bps, "rebalance_days": args.rebalance_days,
                    "notes": "510300 tradable ETF proxy", "built_at": built_at, **summary,
                })

            for seed in range(args.random_seeds):
                rng = np.random.default_rng(seed)

                def select_random(signal_date: str, idx: int, rng=rng) -> list[str]:
                    g = cand_by_date.get(signal_date)
                    if g is None or g.empty:
                        return []
                    top = g.sort_values("rank_in_date").head(20)
                    counts = Counter(top["tdx_l1"].fillna("_UNKNOWN"))
                    picks: list[str] = []
                    for industry, count in counts.items():
                        pool = g[g["tdx_l1"].fillna("_UNKNOWN") == industry]["stock_code"].tolist()
                        if pool:
                            picks.extend(rng.choice(pool, size=min(count, len(pool)), replace=False).tolist())
                    if len(picks) < 20:
                        rest = [c for c in g["stock_code"].tolist() if c not in picks]
                        if rest:
                            picks.extend(rng.choice(rest, size=min(20 - len(picks), len(rest)), replace=False).tolist())
                    return picks[:20]

                curve, summary = simulate_curve(
                    curve_id=f"benchmark_random_l1_seed_{seed:02d}_{int(cost_bps)}bps",
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
                    "run_id": run_id, "curve_id": curve["curve_id"].iloc[0],
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

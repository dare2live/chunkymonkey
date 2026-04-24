#!/usr/bin/env python3
"""ETF B2: 批量回测 Grid vs Buy-and-Hold 对比

写入 mart_etf_strategy_comparison, 每只 ETF × 3 周期 (1Y/3Y/5Y) × 2 策略 共 6 行.
Grid 策略每次都跑 _optimize_grid 寻优 (0.5%~4.5% 扫描, 取 hard_gate 通过的最优 step).

使用:
  python scripts/backtest_etf_strategies.py               # 全量 active ETF
  python scripts/backtest_etf_strategies.py --limit 50    # 小样本验证
  python scripts/backtest_etf_strategies.py --codes 515050,510300
"""
from __future__ import annotations
import argparse, logging, sys, time, math
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import duckdb

from services.etf_grid_engine import (
    _optimize_grid, _buy_hold_stats, _max_drawdown,
    assess_etf_tradeability, is_supported_exchange_etf_code,
)

logger = logging.getLogger("etf_strategy_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

ETF_DB = Path(__file__).resolve().parent.parent.parent / "data" / "etf.duckdb"

PERIODS = [
    ("1Y", 252),
    ("3Y", 756),
    ("5Y", 1260),
]

DDL = """
CREATE TABLE IF NOT EXISTS mart_etf_strategy_comparison (
    snapshot_date         DATE NOT NULL,
    code                  VARCHAR NOT NULL,
    name                  VARCHAR,
    category              VARCHAR,
    period                VARCHAR NOT NULL,
    lookback_days         INTEGER,
    strategy              VARCHAR NOT NULL,
    return_pct            REAL,
    annualized_return_pct REAL,
    max_drawdown_pct      REAL,
    sharpe                REAL,
    trade_count           INTEGER,
    win_rate              REAL,
    best_step_pct         REAL,
    edge_pct              REAL,
    data_from             DATE,
    data_to               DATE,
    built_at              TEXT,
    PRIMARY KEY (snapshot_date, code, period, strategy)
);
CREATE INDEX IF NOT EXISTS idx_mesc_code ON mart_etf_strategy_comparison(code);
CREATE INDEX IF NOT EXISTS idx_mesc_edge ON mart_etf_strategy_comparison(snapshot_date, period, edge_pct DESC);
"""


def annualize(return_pct: float | None, days: int) -> float | None:
    if return_pct is None or days <= 0:
        return None
    try:
        yr = days / 252.0
        if yr <= 0:
            return None
        r = 1 + (return_pct / 100.0)
        if r <= 0:
            return None
        return (r ** (1 / yr) - 1) * 100.0
    except Exception:
        return None


def fetch_price_rows(conn, code: str, lookback_days: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume, amount
        FROM etf_price_kline
        WHERE code = ? AND freq='daily' AND adjust='qfq'
        ORDER BY date DESC LIMIT ?
        """,
        [code, lookback_days + 5],
    ).fetchall()
    return [
        {"date": str(r[0]), "open": r[1], "high": r[2], "low": r[3],
         "close": r[4], "volume": r[5], "amount": r[6]}
        for r in reversed(rows)
    ]


def backtest_period(price_rows: list[dict], *, info: dict) -> tuple[dict | None, dict | None]:
    """返回 (grid_result, buy_hold_result); 任一可能 None"""
    if len(price_rows) < 60:
        return None, None

    bh = _buy_hold_stats(price_rows)
    grid = _optimize_grid(price_rows, row={**info})
    return grid, bh


def upsert_rows(conn, rows: list[tuple]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_etf_strategy_comparison (
            snapshot_date, code, name, category, period, lookback_days,
            strategy, return_pct, annualized_return_pct, max_drawdown_pct,
            sharpe, trade_count, win_rate, best_step_pct, edge_pct,
            data_from, data_to, built_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None, help='只跑前 N 只 (测试)')
    parser.add_argument('--codes', default=None, help='逗号分隔的 ETF 代码')
    args = parser.parse_args()

    conn = duckdb.connect(str(ETF_DB))
    conn.execute(DDL)

    # 取 ETF 列表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(',') if c.strip()]
        placeholders = ','.join(['?'] * len(codes))
        rows = conn.execute(
            f"SELECT code, name, category FROM etf_asset_universe WHERE code IN ({placeholders})",
            codes,
        ).fetchall()
    else:
        q = "SELECT code, name, category FROM etf_asset_universe WHERE is_active = 1 ORDER BY code"
        if args.limit:
            q += f" LIMIT {args.limit}"
        rows = conn.execute(q).fetchall()

    etfs = [{"code": r[0], "name": r[1], "category": r[2]} for r in rows]
    logger.info("ETF 数: %d", len(etfs))

    # 取 snapshot_date (数据最新日期)
    snap_row = conn.execute("SELECT MAX(date) FROM etf_price_kline").fetchone()
    snapshot_date = str(snap_row[0]) if snap_row and snap_row[0] else None
    if not snapshot_date:
        logger.error("etf_price_kline 无数据")
        return
    logger.info("snapshot_date=%s", snapshot_date)

    now_iso = datetime.utcnow().isoformat()
    upsert_buf: list[tuple] = []
    stats = {"ok": 0, "no_data": 0, "unsupported": 0, "err": 0}
    t0 = time.time()

    for idx, etf in enumerate(etfs, 1):
        code = etf["code"]
        if not is_supported_exchange_etf_code(code):
            stats["unsupported"] += 1
            continue
        try:
            # 统一拉一次最长窗口, 再切片各 period
            full_rows = fetch_price_rows(conn, code, 1260)
            if len(full_rows) < 60:
                stats["no_data"] += 1
                continue
            data_from = full_rows[0]["date"]
            data_to = full_rows[-1]["date"]

            for period, days in PERIODS:
                window = full_rows[-days:] if len(full_rows) >= days else full_rows
                if len(window) < 60:
                    continue

                grid, bh = backtest_period(window, info=etf)

                # buy_hold 行
                if bh:
                    bh_ret = bh.get("return_pct")
                    bh_row = (
                        snapshot_date, code, etf["name"], etf["category"], period, len(window),
                        "buy_hold",
                        bh_ret,
                        annualize(bh_ret, len(window)),
                        bh.get("max_drawdown_pct"),
                        bh.get("sharpe"),
                        None,  # trade_count N/A
                        None,  # win_rate N/A
                        None,  # best_step_pct N/A
                        0.0 if bh_ret is not None else None,
                        window[0]["date"], window[-1]["date"],
                        now_iso,
                    )
                    upsert_buf.append(bh_row)

                # grid 行
                if grid:
                    g_ret = grid.get("return_pct")
                    bh_ret = bh.get("return_pct") if bh else None
                    edge = (g_ret - bh_ret) if (g_ret is not None and bh_ret is not None) else None
                    grid_row = (
                        snapshot_date, code, etf["name"], etf["category"], period, len(window),
                        "grid",
                        g_ret,
                        annualize(g_ret, len(window)),
                        grid.get("max_drawdown_pct"),
                        grid.get("sharpe"),
                        grid.get("trade_count"),
                        grid.get("win_rate"),
                        grid.get("step_pct"),
                        edge,
                        window[0]["date"], window[-1]["date"],
                        now_iso,
                    )
                    upsert_buf.append(grid_row)

            stats["ok"] += 1
            if len(upsert_buf) >= 200:
                upsert_rows(conn, upsert_buf)
                upsert_buf = []
            if idx % 50 == 0:
                logger.info("进度 %d/%d · ok=%d no_data=%d unsupported=%d err=%d · %.0fs",
                            idx, len(etfs), stats["ok"], stats["no_data"], stats["unsupported"],
                            stats["err"], time.time() - t0)
        except Exception as e:
            stats["err"] += 1
            logger.warning("ETF %s 失败: %s", code, e)
            continue

    upsert_rows(conn, upsert_buf)
    elapsed = time.time() - t0
    logger.info("完成: ok=%d no_data=%d unsupported=%d err=%d · %.1fs",
                stats["ok"], stats["no_data"], stats["unsupported"], stats["err"], elapsed)

    # 预览 top edge
    top = conn.execute(
        """
        SELECT c.code, c.name, c.category, c.period, c.return_pct, c.best_step_pct, c.edge_pct,
               c.max_drawdown_pct, c.sharpe, c.trade_count
        FROM mart_etf_strategy_comparison c
        WHERE c.snapshot_date = ? AND c.strategy='grid' AND c.edge_pct IS NOT NULL
        ORDER BY c.period, c.edge_pct DESC LIMIT 15
        """,
        [snapshot_date],
    ).fetchall()
    if top:
        logger.info("Top Grid Edge (各周期前 5):")
        for r in top:
            logger.info(
                "  [%s] %-8s %-12s %-10s return=%+6.1f%% step=%.1f%% edge=%+6.1f%% DD=%+5.1f%% SR=%s trades=%s",
                r[3], r[0], (r[1] or '')[:12], (r[2] or '')[:10], r[4] or 0, r[5] or 0, r[6] or 0,
                r[7] or 0, f"{r[8]:.2f}" if r[8] is not None else '-', r[9] or 0
            )

    conn.close()


if __name__ == "__main__":
    main()

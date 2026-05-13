"""Phase π — 回填 buy_signal 历史 (供 walk-forward backtest 用).

⚠ 当前 mart_stock_formula_buy_signal_daily 只有 1 天 (今天).
⚠ 历史回测需要每个交易日都有 buy_signal. 循环 backfill.

实施: 对 [start, end] 每个交易日, 调用 build_stock_formula_buy_signal_daily.py --date X.
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from datetime import date as _date
from pathlib import Path

from services.db import get_conn


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backfill_buy_signal")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-01-03")
    parser.add_argument("--end",   default=_date.today().isoformat())
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有数据日")
    args = parser.parse_args()

    t0 = time.time()
    conn = get_conn()
    trade_dates = [r[0] for r in conn.execute(
        """SELECT trade_date FROM dim_trading_calendar
            WHERE is_trading=1 AND trade_date BETWEEN ? AND ?
            ORDER BY trade_date""",
        [args.start, args.end],
    ).fetchall()]
    log.info(f"交易日: {len(trade_dates):,} ({args.start} → {args.end})")

    if args.skip_existing:
        existing = {r[0] for r in conn.execute(
            "SELECT DISTINCT signal_date FROM mart_stock_formula_buy_signal_daily"
        ).fetchall()}
        trade_dates = [d for d in trade_dates if d not in existing]
        log.info(f"跳过 {len(existing):,} 已有日, 剩 {len(trade_dates):,} 待 backfill")
    conn.close()

    script = Path(__file__).parent / "build_stock_formula_buy_signal_daily.py"
    n_ok = n_fail = 0
    for i, d in enumerate(trade_dates):
        r = subprocess.run(
            ["python", str(script), "--date", d],
            cwd=str(Path(__file__).resolve().parents[2]),
            env={"PYTHONPATH": "backend", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            n_ok += 1
        else:
            n_fail += 1
            log.warning(f"  {d}: FAIL ({r.stderr[-200:]})")
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / max(elapsed, 0.001)
            remain = (len(trade_dates) - i - 1) / max(rate, 0.001)
            log.info(f"  {i+1}/{len(trade_dates)}: ok={n_ok} fail={n_fail} {elapsed:.0f}s elapsed, {remain:.0f}s remain")

    log.info(f"=== 完成 — ok={n_ok}, fail={n_fail}, {time.time()-t0:.0f}s ===")


if __name__ == "__main__":
    main()

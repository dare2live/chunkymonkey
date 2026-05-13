"""Phase δ+ — Paper Engine 历史回放 (2024-01 至今)。

mart_daily_recommendation 仅 2 个 snapshot, 历史回放无法用; 用 fact_technical_trigger
日级 top-K (按 strength desc) 作历史候选源, 复用 paper_engine.driver 的撮合逻辑。

输出: mart_paper_nav 长 NAV 序列, 满足 W6 go/no-go (累计超额 / Sharpe / MaxDD)。

用法:
  PYTHONPATH=backend python backend/scripts/replay_paper_history.py [--from 2024-01-01] [--to 2026-05-12]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date as _date, timedelta

from services.db import get_conn
from services.market_db import get_market_conn
from services.paper_engine.ddl import ensure_paper_tables
from services.paper_engine.driver import (
    compute_drawdown, compute_nav, load_kline_today, load_open_positions,
)
from services.paper_engine.entries import compute_shares, evaluate_entry
from services.paper_engine.exits import evaluate_exit


log = logging.getLogger("replay_paper_history")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _trading_days(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT trade_date FROM dim_trading_calendar
         WHERE trade_date >= ? AND trade_date <= ? AND is_trading = 1
         ORDER BY trade_date
        """,
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def _formula_topk_for_date(conn, target_date: str, top_k: int = 20) -> list[dict]:
    """以 fact_technical_trigger 上个交易日 hit + 多公式 voting 替代 daily-topk。

    避免 lookahead: 信号在 T 日 close 后生成, 但实际交易要 T+1, 所以 target_date 的候选
    用 target_date - 1 交易日的信号源。
    """
    # 找 target_date 前最近的有信号的日期 (用 fact_technical_trigger 日历)
    prev_signal_date_row = conn.execute(
        """
        SELECT MAX(date) FROM fact_technical_trigger WHERE date < ?
        """,
        [target_date],
    ).fetchone()
    if not prev_signal_date_row or not prev_signal_date_row[0]:
        return []
    signal_date = prev_signal_date_row[0]
    rows = conn.execute(
        """
        SELECT stock_code,
               COUNT(*) AS n_formula_hit,
               AVG(strength) AS avg_strength
          FROM fact_technical_trigger
         WHERE date = ?
         GROUP BY stock_code
         ORDER BY n_formula_hit DESC, avg_strength DESC NULLS LAST
         LIMIT ?
        """,
        [signal_date, top_k],
    ).fetchall()
    return [
        {
            "stock_code": r[0],
            "rank_in_date": i + 1,
            "pred_score": float(r[2] or 0.0),
            "entry_target_price": None,  # 用 today_open fallback
            "entry_aggressive_price": None,
            "entry_max_price": None,
            "exit_target_1_price": None,
            "exit_stop_price": None,
            "expected_horizon_days": 20,
        }
        for i, r in enumerate(rows)
    ]


def run_replay_day(
    *,
    conn,
    mkt_conn,
    snapshot_date: str,
    prev_date: str | None,
    initial_capital: float = 1_000_000.0,
    max_positions: int = 20,
    cash_reserve_pct: float = 0.10,
    model_id: str = "paper_replay_v1",
) -> dict:
    """一日 step (Phase δ driver 风格 + formula 候选源 + 含基准)。"""
    open_positions = load_open_positions(conn, model_id)
    kline = load_kline_today(mkt_conn, snapshot_date, prev_date)
    candidates = _formula_topk_for_date(conn, snapshot_date, top_k=max_positions)

    # 现金 + peak
    prev_nav_row = conn.execute(
        "SELECT cash, nav_value FROM mart_paper_nav WHERE snapshot_date < ? AND model_id = ? ORDER BY snapshot_date DESC LIMIT 1",
        [snapshot_date, model_id],
    ).fetchone()
    if prev_nav_row:
        cash = float(prev_nav_row[0] or 0.0)
        peak_nav = float(prev_nav_row[1] or initial_capital)
    else:
        cash = initial_capital
        peak_nav = initial_capital
        for p in open_positions.values():
            cash -= p["notional"]

    sell_rows = []
    buy_rows = []

    # exit (horizon-only for replay)
    from datetime import date as __d
    for code, pos in list(open_positions.items()):
        kl = kline.get(code, {})
        try:
            hd = (__d.fromisoformat(snapshot_date) - __d.fromisoformat(pos["open_date"])).days
        except Exception:
            hd = 0
        result = evaluate_exit(
            holding_days=hd, expected_horizon=20,
            exit_stop_price=None, exit_target_1_price=None,
            today_open=kl.get("open"), today_high=kl.get("high"),
            today_low=kl.get("low"), today_close=kl.get("close"),
            prev_close=kl.get("prev_close"),
        )
        if result["action"] == "exit" and result["fill_price"]:
            fill = result["fill_price"]
            gross_ret = (fill - pos["open_price"]) / pos["open_price"] if pos["open_price"] > 0 else 0
            sell_rows.append((
                snapshot_date, code, "sell", pos["shares"], kl.get("close"), fill, 0.0,
                fill * pos["shares"],
                pos["open_date"], pos["open_price"], hd, gross_ret, gross_ret,
                model_id, result["reason"],
            ))
            cash += fill * pos["shares"]
            del open_positions[code]

    # entry
    n_slot = max_positions - len(open_positions)
    if n_slot > 0 and cash > 0:
        per_slot_cash = cash * (1.0 - cash_reserve_pct) / n_slot
        for cand in candidates[:n_slot]:
            code = cand["stock_code"]
            if code in open_positions:
                continue
            kl = kline.get(code, {})
            entry_result = evaluate_entry(
                entry_target_price=cand["entry_target_price"],
                entry_aggressive_price=cand["entry_aggressive_price"],
                entry_max_price=cand["entry_max_price"],
                today_open=kl.get("open"), today_high=kl.get("high"),
                today_low=kl.get("low"), today_close=kl.get("close"),
                prev_close=kl.get("prev_close"),
            )
            if entry_result["action"] != "enter":
                continue
            fill = entry_result["fill_price"]
            shares = compute_shares(cash_available=per_slot_cash, target_weight=1.0,
                                     fill_price=fill, lot_size=100)
            if shares == 0:
                continue
            notional = shares * fill
            if notional > cash:
                continue
            cash -= notional
            buy_rows.append((
                snapshot_date, code, "buy", shares, kl.get("close"), fill, 0.0,
                notional,
                None, None, None, None, None,
                model_id, entry_result["reason"],
            ))
            open_positions[code] = {
                "open_date": snapshot_date, "open_price": fill,
                "shares": shares, "notional": notional,
            }

    # NAV
    today_prices = {code: kl.get("close") for code, kl in kline.items() if kl.get("close")}
    nav_out = compute_nav(cash=cash, positions=open_positions, today_prices=today_prices)
    dd, peak_nav = compute_drawdown(nav_out["nav_value"], peak_nav)
    daily_ret = (nav_out["nav_value"] / float(prev_nav_row[1])) - 1.0 if prev_nav_row else 0.0
    cum_ret = nav_out["nav_value"] / initial_capital - 1.0

    # 写库 atomic
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM fact_paper_position WHERE event_date=? AND model_id=?", [snapshot_date, model_id])
        if sell_rows:
            conn.executemany(
                """INSERT INTO fact_paper_position
                   (event_date, stock_code, side, qty, ref_price, exec_price, slip_bps, notional,
                    entry_date, entry_price, holding_days, gross_return, net_return,
                    model_id, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                sell_rows,
            )
        if buy_rows:
            conn.executemany(
                """INSERT INTO fact_paper_position
                   (event_date, stock_code, side, qty, ref_price, exec_price, slip_bps, notional,
                    entry_date, entry_price, holding_days, gross_return, net_return,
                    model_id, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                buy_rows,
            )
        conn.execute("DELETE FROM mart_paper_nav WHERE snapshot_date=? AND model_id=?", [snapshot_date, model_id])
        conn.execute(
            """INSERT INTO mart_paper_nav
               (snapshot_date, nav, nav_value, daily_ret, cum_ret,
                hs300_nav, hs300_cum_ret, vs_hs300_cum_ret,
                eqw_nav, eqw_cum_ret, vs_eqw_cum_ret,
                cash, position_count, drawdown, model_id, initial_capital)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?)""",
            [snapshot_date, nav_out["nav_value"] / initial_capital, nav_out["nav_value"],
             daily_ret, cum_ret, cash, nav_out["position_count"], dd, model_id, initial_capital],
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    return {"snapshot_date": snapshot_date, "nav_value": nav_out["nav_value"],
            "position_count": nav_out["position_count"],
            "n_entries": len(buy_rows), "n_exits": len(sell_rows),
            "daily_ret": daily_ret, "cum_ret": cum_ret, "drawdown": dd}


def backfill_benchmark_columns(conn, mkt_conn, model_id: str = "paper_replay_v1") -> int:
    """补 mart_paper_nav 的 hs300_nav + hs300_cum_ret + vs_hs300_cum_ret 等基准列。"""
    from services.paper_engine.benchmarks import hs300_nav_series
    rows = conn.execute(
        "SELECT snapshot_date FROM mart_paper_nav WHERE model_id=? ORDER BY snapshot_date",
        [model_id],
    ).fetchall()
    if not rows:
        return 0
    start, end = rows[0][0], rows[-1][0]
    hs300 = hs300_nav_series(mkt_conn, start, end)
    if not hs300:
        log.warning("  HS300 数据无, 跳过基准填充")
        return 0
    log.info(f"  HS300 NAV {len(hs300)} 日")

    # 批量 UPDATE
    conn.execute("BEGIN TRANSACTION")
    try:
        for d, hs_nav in hs300.items():
            hs_cum = hs_nav - 1.0
            # 取主组合该日 cum_ret 算 vs
            r = conn.execute(
                "SELECT cum_ret FROM mart_paper_nav WHERE snapshot_date=? AND model_id=?",
                [d, model_id],
            ).fetchone()
            if not r:
                continue
            cum_ret = float(r[0] or 0.0)
            conn.execute(
                """UPDATE mart_paper_nav
                      SET hs300_nav=?, hs300_cum_ret=?, vs_hs300_cum_ret=?
                    WHERE snapshot_date=? AND model_id=?""",
                [hs_nav, hs_cum, cum_ret - hs_cum, d, model_id],
            )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    return len(hs300)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2024-01-01")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=20)
    parser.add_argument("--model-id", default="paper_replay_v1")
    parser.add_argument("--skip-benchmark", action="store_true")
    args = parser.parse_args()

    if args.to_date is None:
        from services.utils import latest_closed_or_raise
        args.to_date = latest_closed_or_raise()

    t0 = time.time()
    conn = get_conn()
    mkt_conn = get_market_conn()
    try:
        ensure_paper_tables(conn)
        # 清空该 model_id 的所有历史 (replay 是覆盖性的)
        conn.execute("BEGIN TRANSACTION")
        conn.execute("DELETE FROM mart_paper_nav WHERE model_id=?", [args.model_id])
        conn.execute("DELETE FROM fact_paper_position WHERE model_id=?", [args.model_id])
        conn.execute("COMMIT")
        log.info(f"replay {args.from_date} ~ {args.to_date}, model_id={args.model_id}, capital={args.initial_capital:,.0f}")

        days = _trading_days(conn, args.from_date, args.to_date)
        log.info(f"  {len(days)} 个交易日")

        prev = None
        for i, d in enumerate(days):
            try:
                r = run_replay_day(
                    conn=conn, mkt_conn=mkt_conn,
                    snapshot_date=d, prev_date=prev,
                    initial_capital=args.initial_capital,
                    max_positions=args.max_positions,
                    model_id=args.model_id,
                )
                if (i + 1) % 50 == 0:
                    log.info(f"  {i+1}/{len(days)} ({d}) nav={r['nav_value']:.0f} cum={r['cum_ret']*100:.2f}% dd={r['drawdown']*100:.2f}% pos={r['position_count']}")
                prev = d
            except Exception as e:
                log.warning(f"  day {d} failed: {e}")
                prev = d

        if not args.skip_benchmark:
            log.info("回填 HS300 基准...")
            n_bm = backfill_benchmark_columns(conn, mkt_conn, args.model_id)
            log.info(f"  填了 {n_bm} 日基准")

        # 最终统计
        final = conn.execute(
            "SELECT snapshot_date, nav_value, cum_ret, drawdown, vs_hs300_cum_ret FROM mart_paper_nav WHERE model_id=? ORDER BY snapshot_date DESC LIMIT 1",
            [args.model_id],
        ).fetchone()
        max_dd = conn.execute(
            "SELECT MIN(drawdown) FROM mart_paper_nav WHERE model_id=?", [args.model_id]
        ).fetchone()
        log.info(f"=== 回放总结 (总耗时 {time.time()-t0:.0f}s) ===")
        if final:
            log.info(f"  end_date={final[0]} nav={final[1]:,.0f} cum_ret={final[2]*100:.2f}%")
            log.info(f"  vs_hs300={final[4]*100:.2f}%" if final[4] is not None else "  vs_hs300=N/A")
        log.info(f"  max_dd={(max_dd[0] or 0)*100:.2f}%")
    finally:
        conn.close()
        mkt_conn.close()


if __name__ == "__main__":
    main()

"""Phase ε+ — Sign-flip 实验回放。

假设: 经 Phase δ IC 分析, turtle / dynamic_ma 公式 strength 与 forward return 反相关
      (高强度触发后回踩概率大). 翻转策略: 选 "未被 turtle 触发但被 MACD 触发" 的股票,
      或翻转 strength 排序方向。

3 个 model_id 并行回放对比:
  - paper_replay_macd_only:    只用 macd_golden_cross (正 IC 公式)
  - paper_replay_avoid_turtle: 选 MACD 触发 但 不在 turtle/dynamic_ma top 中的股票
  - paper_replay_reverse:      avoid 所有 4 公式触发, 反向选

用法:
  PYTHONPATH=backend python backend/scripts/replay_paper_history_signflip.py [--from 2024-01-01]
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date as _date

from services.db import get_conn
from services.market_db import get_market_conn
from services.paper_engine.ddl import ensure_paper_tables
from services.paper_engine.driver import (
    compute_drawdown, compute_nav, load_kline_today, load_open_positions,
)
from services.paper_engine.entries import compute_shares, evaluate_entry
from services.paper_engine.exits import evaluate_exit
from scripts.replay_paper_history import (
    _trading_days, backfill_benchmark_columns,
)


log = logging.getLogger("replay_signflip")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")


def _macd_only_candidates(conn, target_date: str, top_k: int = 20) -> list[dict]:
    """只用 macd_golden_cross 公式 + strength desc。"""
    prev = conn.execute(
        "SELECT MAX(date) FROM fact_technical_trigger WHERE date < ? AND formula_id='macd_golden_cross'",
        [target_date],
    ).fetchone()
    if not prev or not prev[0]:
        return []
    rows = conn.execute(
        """
        SELECT stock_code, strength FROM fact_technical_trigger
         WHERE date = ? AND formula_id='macd_golden_cross'
         ORDER BY strength DESC NULLS LAST LIMIT ?
        """,
        [prev[0], top_k],
    ).fetchall()
    return [
        {"stock_code": r[0], "rank_in_date": i + 1, "pred_score": float(r[1] or 0),
         "entry_target_price": None, "entry_aggressive_price": None, "entry_max_price": None,
         "exit_target_1_price": None, "exit_stop_price": None, "expected_horizon_days": 20}
        for i, r in enumerate(rows)
    ]


def _avoid_turtle_candidates(conn, target_date: str, top_k: int = 20) -> list[dict]:
    """选 MACD 触发 + 但不在 turtle hits 列表中的股票。"""
    prev = conn.execute(
        "SELECT MAX(date) FROM fact_technical_trigger WHERE date < ?",
        [target_date],
    ).fetchone()
    if not prev or not prev[0]:
        return []
    signal_date = prev[0]
    # 子查询: turtle 触发的股票
    rows = conn.execute(
        """
        SELECT m.stock_code, m.strength FROM fact_technical_trigger m
         WHERE m.date = ? AND m.formula_id='macd_golden_cross'
           AND NOT EXISTS (
             SELECT 1 FROM fact_technical_trigger t
              WHERE t.date = m.date AND t.stock_code = m.stock_code
                AND t.formula_id IN ('turtle_breakout_20', 'turtle_breakout_55')
           )
         ORDER BY m.strength DESC NULLS LAST LIMIT ?
        """,
        [signal_date, top_k],
    ).fetchall()
    return [
        {"stock_code": r[0], "rank_in_date": i + 1, "pred_score": float(r[1] or 0),
         "entry_target_price": None, "entry_aggressive_price": None, "entry_max_price": None,
         "exit_target_1_price": None, "exit_stop_price": None, "expected_horizon_days": 20}
        for i, r in enumerate(rows)
    ]


def _reverse_strength_candidates(conn, target_date: str, top_k: int = 20) -> list[dict]:
    """所有公式触发, 但按 strength ASC 选 (反向, 弱信号优先)。"""
    prev = conn.execute(
        "SELECT MAX(date) FROM fact_technical_trigger WHERE date < ?",
        [target_date],
    ).fetchone()
    if not prev or not prev[0]:
        return []
    rows = conn.execute(
        """
        SELECT stock_code, MIN(strength) AS s FROM fact_technical_trigger
         WHERE date = ?
         GROUP BY stock_code
         ORDER BY s ASC NULLS LAST LIMIT ?
        """,
        [prev[0], top_k],
    ).fetchall()
    return [
        {"stock_code": r[0], "rank_in_date": i + 1, "pred_score": float(r[1] or 0),
         "entry_target_price": None, "entry_aggressive_price": None, "entry_max_price": None,
         "exit_target_1_price": None, "exit_stop_price": None, "expected_horizon_days": 20}
        for i, r in enumerate(rows)
    ]


def run_signflip_day(conn, mkt_conn, snapshot_date, prev_date, candidates_fn,
                     initial_capital, max_positions, cash_reserve_pct, model_id):
    """与 replay_paper_history.run_replay_day 同, 但 candidates 由 fn 提供。"""
    open_positions = load_open_positions(conn, model_id)
    kline = load_kline_today(mkt_conn, snapshot_date, prev_date)
    candidates = candidates_fn(conn, snapshot_date, top_k=max_positions)

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

    sell_rows, buy_rows = [], []
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
                fill * pos["shares"], pos["open_date"], pos["open_price"], hd, gross_ret, gross_ret,
                model_id, result["reason"],
            ))
            cash += fill * pos["shares"]
            del open_positions[code]

    n_slot = max_positions - len(open_positions)
    if n_slot > 0 and cash > 0:
        per_slot_cash = cash * (1.0 - cash_reserve_pct) / n_slot
        for cand in candidates[:n_slot]:
            code = cand["stock_code"]
            if code in open_positions:
                continue
            kl = kline.get(code, {})
            entry_result = evaluate_entry(
                entry_target_price=None, entry_aggressive_price=None, entry_max_price=None,
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
                notional, None, None, None, None, None,
                model_id, entry_result["reason"],
            ))
            open_positions[code] = {
                "open_date": snapshot_date, "open_price": fill,
                "shares": shares, "notional": notional,
            }

    today_prices = {code: kl.get("close") for code, kl in kline.items() if kl.get("close")}
    nav_out = compute_nav(cash=cash, positions=open_positions, today_prices=today_prices)
    dd, peak_nav = compute_drawdown(nav_out["nav_value"], peak_nav)
    daily_ret = (nav_out["nav_value"] / float(prev_nav_row[1])) - 1.0 if prev_nav_row else 0.0
    cum_ret = nav_out["nav_value"] / initial_capital - 1.0

    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute("DELETE FROM fact_paper_position WHERE event_date=? AND model_id=?", [snapshot_date, model_id])
        if sell_rows:
            conn.executemany(
                """INSERT INTO fact_paper_position
                   (event_date, stock_code, side, qty, ref_price, exec_price, slip_bps, notional,
                    entry_date, entry_price, holding_days, gross_return, net_return, model_id, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                sell_rows,
            )
        if buy_rows:
            conn.executemany(
                """INSERT INTO fact_paper_position
                   (event_date, stock_code, side, qty, ref_price, exec_price, slip_bps, notional,
                    entry_date, entry_price, holding_days, gross_return, net_return, model_id, reason)
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
            "n_entries": len(buy_rows), "n_exits": len(sell_rows),
            "cum_ret": cum_ret, "drawdown": dd}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="from_date", default="2024-01-01")
    parser.add_argument("--to", dest="to_date", default=None,
                        help="默认 calendar-gated latest_closed_trade_date (Phase ψ.5)")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--max-positions", type=int, default=20)
    args = parser.parse_args()

    if args.to_date is None:
        from services.utils import latest_closed_or_raise
        args.to_date = latest_closed_or_raise()

    # 3 个实验组
    experiments = [
        ("paper_replay_macd_only", _macd_only_candidates,    "只 MACD 公式"),
        ("paper_replay_avoid_turtle", _avoid_turtle_candidates, "MACD ∩ ¬turtle"),
        ("paper_replay_reverse",   _reverse_strength_candidates, "反向 strength asc"),
    ]

    conn = get_conn()
    mkt_conn = get_market_conn()
    try:
        ensure_paper_tables(conn)
        days = _trading_days(conn, args.from_date, args.to_date)
        log.info(f"sign-flip 3 实验 × {len(days)} 日 = {3*len(days)} step")

        for model_id, cand_fn, desc in experiments:
            log.info(f"--- {model_id} ({desc}) ---")
            conn.execute("BEGIN TRANSACTION")
            conn.execute("DELETE FROM mart_paper_nav WHERE model_id=?", [model_id])
            conn.execute("DELETE FROM fact_paper_position WHERE model_id=?", [model_id])
            conn.execute("COMMIT")
            t0 = time.time()
            prev = None
            for i, d in enumerate(days):
                try:
                    run_signflip_day(
                        conn, mkt_conn, d, prev, cand_fn,
                        args.initial_capital, args.max_positions, 0.10, model_id,
                    )
                    prev = d
                except Exception as e:
                    log.warning(f"  day {d} failed: {e}")
                    prev = d
                if (i + 1) % 100 == 0:
                    last = conn.execute(
                        "SELECT cum_ret, drawdown FROM mart_paper_nav WHERE model_id=? ORDER BY snapshot_date DESC LIMIT 1",
                        [model_id]
                    ).fetchone()
                    if last:
                        log.info(f"    {i+1}/{len(days)} cum={last[0]*100:.2f}% dd={last[1]*100:.2f}%")
            backfill_benchmark_columns(conn, mkt_conn, model_id)
            log.info(f"  {model_id} 耗时 {time.time()-t0:.0f}s")

        # 对比
        log.info("=== 实验对比 ===")
        for model_id, _, desc in experiments:
            r = conn.execute(
                "SELECT cum_ret, drawdown, vs_hs300_cum_ret FROM mart_paper_nav WHERE model_id=? AND hs300_cum_ret IS NOT NULL ORDER BY snapshot_date DESC LIMIT 1",
                [model_id],
            ).fetchone()
            mdd = conn.execute(
                "SELECT MIN(drawdown) FROM mart_paper_nav WHERE model_id=?", [model_id]
            ).fetchone()
            if r:
                vs = (r[2] or 0) * 100
                log.info(f"  {model_id:35s} ({desc}): cum_ret={r[0]*100:+.2f}% / vs_hs300={vs:+.2f}% / max_dd={(mdd[0] or 0)*100:.2f}%")
    finally:
        conn.close()
        mkt_conn.close()


if __name__ == "__main__":
    main()

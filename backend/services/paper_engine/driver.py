"""Phase δ D2 — 单日 paper engine 主循环 driver。

step(D):
  1. 拉 当日 K 线 (含 prev_close for 涨跌停判定)
  2. 拉 当前持仓 (close_date IS NULL)
  3. 评估每仓退出 → 写 fact_paper_position sell 行
  4. 拉 当日 BUY 候选 (mart_daily_recommendation + mart_stock_trade_plan)
  5. 评估每候选入场 → 写 fact_paper_position buy 行
  6. 计算 NAV (现金 + 持仓估值) + 基准 (HS300, 等权) → 写 mart_paper_nav
  7. 写 mart_decision_outcome (forward returns 暂留空, replay 后回填)

全程在单一 BEGIN/COMMIT 事务内 (Phase β crash 防御模式)。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from services.paper_engine.entries import compute_shares, evaluate_entry
from services.paper_engine.exits import evaluate_exit
from services.paper_engine.portfolio import compute_drawdown, compute_nav, compute_top_industry


log = logging.getLogger("paper_engine.driver")


def _make_position_id(stock_code: str, open_date: str, model_id: str) -> str:
    return f"{stock_code}_{open_date}_{model_id}"


def load_open_positions(conn, model_id: str = "paper_v1") -> dict[str, dict]:
    """返回 {stock_code: position_dict} 当前持仓 (sell 行未完成)。

    用法: 一个 stock 只可能有 1 个 open position (entries 拒绝重复)。
    """
    rows = conn.execute(
        """
        SELECT b.stock_code, b.event_date, b.exec_price, b.qty,
               b.notional, b.reason
          FROM fact_paper_position b
         WHERE b.side = 'buy' AND b.model_id = ?
           AND NOT EXISTS (
             SELECT 1 FROM fact_paper_position s
              WHERE s.stock_code = b.stock_code
                AND s.side = 'sell' AND s.model_id = b.model_id
                AND s.event_date >= b.event_date
                AND s.entry_date = b.event_date
           )
        """,
        [model_id],
    ).fetchall()
    out = {}
    for r in rows:
        out[r[0]] = {
            "open_date": r[1],
            "open_price": float(r[2] or 0.0),
            "shares": float(r[3] or 0.0),
            "notional": float(r[4] or 0.0),
            "reason": r[5],
        }
    return out


def load_kline_today(mkt_conn, target_date: str, prev_date: str | None = None) -> dict[str, dict]:
    """当日 K 线 (open/high/low/close) + prev_close (for 涨跌停)。

    Returns: {code: {open, high, low, close, prev_close}}
    """
    rows = mkt_conn.execute(
        """
        SELECT code, open, high, low, close
          FROM v_price_kline_qfq
         WHERE adjust='qfq' AND freq='daily' AND date = ?
        """,
        [target_date],
    ).fetchall()
    today = {r[0]: {
        "open": float(r[1]) if r[1] is not None else None,
        "high": float(r[2]) if r[2] is not None else None,
        "low":  float(r[3]) if r[3] is not None else None,
        "close": float(r[4]) if r[4] is not None else None,
        "prev_close": None,
    } for r in rows}
    if prev_date:
        prev = mkt_conn.execute(
            """
            SELECT code, close FROM v_price_kline_qfq
             WHERE adjust='qfq' AND freq='daily' AND date = ?
            """,
            [prev_date],
        ).fetchall()
        for code, cl in prev:
            if code in today:
                today[code]["prev_close"] = float(cl) if cl is not None else None
    return today


def load_buy_candidates(conn, snapshot_date: str, model_id: str = "paper_v1") -> list[dict]:
    """读 mart_daily_recommendation 当日 topk + JOIN trade_plan。"""
    rows = conn.execute(
        """
        SELECT r.stock_code, r.rank_in_date, r.pred_score,
               tp.entry_target_price, tp.entry_aggressive_price, tp.entry_max_price,
               tp.exit_target_1_price, tp.exit_stop_price, tp.expected_horizon_days,
               tp.risk_reward_ratio, tp.atr_14
          FROM mart_daily_recommendation r
          LEFT JOIN mart_stock_trade_plan tp
            ON tp.stock_code = r.stock_code AND tp.plan_date = r.snapshot_date AND tp.model_id = 'v1'
         WHERE r.snapshot_date = ?
           AND COALESCE(r.is_primary, TRUE) = TRUE
         ORDER BY r.rank_in_date
        """,
        [snapshot_date],
    ).fetchall()
    return [
        {
            "stock_code": r[0],
            "rank_in_date": r[1],
            "pred_score": float(r[2]) if r[2] is not None else None,
            "entry_target_price": float(r[3]) if r[3] is not None else None,
            "entry_aggressive_price": float(r[4]) if r[4] is not None else None,
            "entry_max_price": float(r[5]) if r[5] is not None else None,
            "exit_target_1_price": float(r[6]) if r[6] is not None else None,
            "exit_stop_price": float(r[7]) if r[7] is not None else None,
            "expected_horizon_days": int(r[8]) if r[8] is not None else 20,
            "risk_reward_ratio": float(r[9]) if r[9] is not None else None,
            "atr_14": float(r[10]) if r[10] is not None else None,
        }
        for r in rows
    ]


def run_paper_day(
    *,
    conn,
    mkt_conn,
    snapshot_date: str,
    prev_date: str | None = None,
    initial_capital: float = 1_000_000.0,
    max_positions: int = 20,
    cash_reserve_pct: float = 0.10,
    model_id: str = "paper_v1",
) -> dict[str, Any]:
    """运行单日 paper 引擎 step。

    Returns:
        {date, nav, position_count, n_exits, n_entries, ...}
    """
    t0 = time.time()
    log.info(f"paper step {snapshot_date}")

    # 0. 装载状态
    open_positions = load_open_positions(conn, model_id)
    kline = load_kline_today(mkt_conn, snapshot_date, prev_date)
    candidates = load_buy_candidates(conn, snapshot_date, model_id)
    log.info(f"  open={len(open_positions)} candidates={len(candidates)} kline={len(kline)}")

    # 0.5. 当前现金 = 上日 cash; 若初次跑, 用 initial_capital
    prev_nav_row = conn.execute(
        """
        SELECT cash, nav_value FROM mart_paper_nav
         WHERE snapshot_date < ? AND model_id = ?
         ORDER BY snapshot_date DESC LIMIT 1
        """,
        [snapshot_date, model_id],
    ).fetchone()
    if prev_nav_row:
        cash = float(prev_nav_row[0] or 0.0)
        peak_nav = float(prev_nav_row[1] or initial_capital)
    else:
        cash = initial_capital
        peak_nav = initial_capital
        # 减去现有持仓的成本 (replay 模式中段开始时)
        for p in open_positions.values():
            cash -= p["notional"]

    sell_rows = []
    buy_rows = []

    # 1. 评估每仓退出
    for code, pos in list(open_positions.items()):
        kl = kline.get(code, {})
        # holding_days 简化用 (snapshot - open_date) 直接日历日 (D2 不细分交易日)
        from datetime import date as _date
        try:
            hd = (_date.fromisoformat(snapshot_date) - _date.fromisoformat(pos["open_date"])).days
        except Exception:
            hd = 0
        # expected_horizon 来自 buy 行的 reason JSON, D2 简化用 20
        result = evaluate_exit(
            holding_days=hd, expected_horizon=20,
            exit_stop_price=None, exit_target_1_price=None,
            today_open=kl.get("open"), today_high=kl.get("high"),
            today_low=kl.get("low"), today_close=kl.get("close"),
            prev_close=kl.get("prev_close"),
        )
        # 简化: 只触发 horizon (因为 plan_date 写完 trade_plan_snapshot 后才能读 stop/target — D2 略过)
        if result["action"] == "exit" and result["fill_price"]:
            fill = result["fill_price"]
            gross_ret = (fill - pos["open_price"]) / pos["open_price"] if pos["open_price"] > 0 else 0
            sell_rows.append((
                snapshot_date, code, "sell", pos["shares"], kl.get("close"), fill, 0.0,
                fill * pos["shares"],
                pos["open_date"], pos["open_price"], hd, gross_ret, gross_ret,  # net = gross 简化
                model_id, result["reason"],
            ))
            cash += fill * pos["shares"]
            del open_positions[code]

    # 2. 评估每候选入场
    n_slot = max_positions - len(open_positions)
    if n_slot > 0 and cash > 0:
        per_slot_cash = cash * (1.0 - cash_reserve_pct) / n_slot
        for cand in candidates[:n_slot]:
            code = cand["stock_code"]
            if code in open_positions:
                continue  # 已有仓位
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
            shares = compute_shares(
                cash_available=per_slot_cash, target_weight=1.0,
                fill_price=fill, lot_size=100,
            )
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
                "open_date": snapshot_date,
                "open_price": fill,
                "shares": shares,
                "notional": notional,
            }

    # 3. mark-to-market 当前持仓
    today_prices = {code: kl.get("close") for code, kl in kline.items() if kl.get("close")}
    nav_out = compute_nav(cash=cash, positions=open_positions, today_prices=today_prices)
    # 基准 (D2 留空, D3 replay 时填)
    dd, peak_nav = compute_drawdown(nav_out["nav_value"], peak_nav)
    daily_ret = (nav_out["nav_value"] / float(prev_nav_row[1])) - 1.0 if prev_nav_row else 0.0
    cum_ret = nav_out["nav_value"] / initial_capital - 1.0

    # 4. 写库 (4 表事务原子)
    conn.execute("BEGIN TRANSACTION")
    try:
        # fact_paper_position: 删当日所有行 (幂等) + INSERT
        conn.execute(
            "DELETE FROM fact_paper_position WHERE event_date = ? AND model_id = ?",
            [snapshot_date, model_id],
        )
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

        # mart_paper_nav: DELETE 当日 + INSERT
        conn.execute(
            "DELETE FROM mart_paper_nav WHERE snapshot_date = ? AND model_id = ?",
            [snapshot_date, model_id],
        )
        conn.execute(
            """INSERT INTO mart_paper_nav
               (snapshot_date, nav, nav_value, daily_ret, cum_ret,
                hs300_nav, hs300_cum_ret, vs_hs300_cum_ret,
                eqw_nav, eqw_cum_ret, vs_eqw_cum_ret,
                cash, position_count, drawdown, model_id, initial_capital)
               VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?)""",
            [
                snapshot_date,
                nav_out["nav_value"] / initial_capital,
                nav_out["nav_value"],
                daily_ret,
                cum_ret,
                cash, nav_out["position_count"], dd,
                model_id, initial_capital,
            ],
        )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    log.info(f"  step done: nav={nav_out['nav_value']:.0f} ({len(buy_rows)} buy / {len(sell_rows)} sell) "
             f"耗时 {time.time()-t0:.2f}s")

    return {
        "snapshot_date": snapshot_date,
        "nav_value": nav_out["nav_value"],
        "position_count": nav_out["position_count"],
        "n_entries": len(buy_rows),
        "n_exits": len(sell_rows),
        "cash": cash,
        "drawdown": dd,
    }

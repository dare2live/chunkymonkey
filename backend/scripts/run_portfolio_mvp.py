#!/usr/bin/env python3
"""P1.A 阶段 2 / 3：stable cohort 非 ML 组合 MVP 回测（§2 codex 路径 1.5 + Claude 响应 4 节）。

策略规则（§2 codex §4.2 + Claude 响应 §4）：
  1. 候选事件：event_type IN ('new_entry','increase')
  2. cohort 准入：v_institution_l2_score.verdict='stable' 且 ho_n >= 15 且 ho_sharpe >= 1
  3. 成本过滤：premium_bucket != 'high_premium'
  4. 当日候选超过 topN：按 stable_score 降序取
  5. 仓位：等权，单机构 / 单 L2 / 单股票上限
  6. 退出：使用 cohort 的 train 最优参数（entry_lag, max_hold_days, stop_loss, take_profit）

时间窗口：
  - cohort 评估期：2023-04 ~ 2024-09-30（锁在 v_institution_l2_score，PIT 口径 cutoff=2024-09-30）
  - portfolio 回测期：2024-10-01 ~ 2026-04-21

对照基线：
  A. 沪深 300 ETF (510300) buy-and-hold
  B. 候选事件等权（不过滤 cohort，只要 new_entry/increase）
  C. 随机 topN（从候选池随机抽 N，同一天）

评估指标：CAGR / MaxDD / Calmar / Sharpe / ProfitFactor / WinRate / Turnover
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.market_db import get_market_conn

logger = logging.getLogger("portfolio_mvp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


PORTFOLIO_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS fact_policy_equity_curve (
    policy_name      TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    date             TEXT NOT NULL,
    equity           REAL,
    cash             REAL,
    n_open_positions INTEGER,
    drawdown         REAL,
    PRIMARY KEY (policy_name, run_id, date)
);

CREATE TABLE IF NOT EXISTS fact_policy_trade (
    policy_name     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    trade_id        INTEGER NOT NULL,
    institution_id  TEXT,
    stock_code      TEXT,
    notice_date     TEXT,
    entry_date      TEXT,
    entry_price     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    hold_days       INTEGER,
    pnl_pct         REAL,
    exit_reason     TEXT,
    position_value  REAL,
    PRIMARY KEY (policy_name, run_id, trade_id)
);

CREATE TABLE IF NOT EXISTS fact_policy_eval (
    policy_name     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    start_date      TEXT,
    end_date        TEXT,
    n_trades        INTEGER,
    cagr            REAL,
    max_drawdown    REAL,
    calmar          REAL,
    sharpe          REAL,
    profit_factor   REAL,
    win_rate        REAL,
    turnover        REAL,
    final_equity    REAL,
    benchmark_cagr  REAL,
    excess_cagr     REAL,
    notes           TEXT,
    created_at      TEXT,
    PRIMARY KEY (policy_name, run_id)
);
"""


def _rows_as_dicts(cursor) -> list[dict[str, Any]]:
    rows = cursor.fetchall()
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [{key: row[key] for key in row.keys()} for row in rows]
    description = getattr(cursor, "description", None) or []
    columns = [item[0] for item in description]
    return [dict(zip(columns, row)) for row in rows]


def _execute(conn, sql: str, params: Optional[Any] = None):
    if params is None:
        return conn.execute(sql)
    try:
        return conn.execute(sql, params=params)
    except TypeError:
        return conn.execute(sql, params)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _safe_float(value: Any) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _event_date_text(value: Any) -> str:
    text = str(value or "")
    return text.replace("-", "")


def _stable_score_key(row: dict[str, Any]) -> tuple[bool, float]:
    score = _safe_float(row.get("stable_score"))
    return (score is None, -(score or 0.0))


def _notice_key(row: dict[str, Any]) -> str:
    return str(row.get("notice_date") or "")


def _build_price_history(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    prices: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        if not code:
            continue
        price_row = {
            "date": str(row.get("date") or ""),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
        }
        prices.setdefault(code, {"rows": []})["rows"].append(price_row)

    for history in prices.values():
        history["rows"].sort(key=lambda item: item["date"])
        history["dates"] = [item["date"] for item in history["rows"]]
        history["by_date"] = {item["date"]: item for item in history["rows"]}
        history["index"] = {day: idx for idx, day in enumerate(history["dates"])}
    return prices


def _price_empty(history: Optional[dict[str, Any]]) -> bool:
    return not history or not history.get("rows")


def _price_at(history: dict[str, Any], day: str) -> Optional[dict[str, Any]]:
    return history.get("by_date", {}).get(day)


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _drawdown_rows(equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    running_max: Optional[float] = None
    for row in equity_curve:
        equity = _safe_float(row.get("equity")) or 0.0
        running_max = equity if running_max is None else max(running_max, equity)
        drawdown = equity / running_max - 1 if running_max else 0.0
        item = dict(row)
        item["drawdown"] = drawdown
        rows.append(item)
    return rows


def load_exec_trade_events_as_events(conn, start_date: str, end_date: str,
                                      min_pct_total: float = 0.0,
                                      individual_only: bool = False,
                                      direction: str = "buy") -> list[dict[str, Any]]:
    """M5 step 2：fact_executive_trade_event (direction='buy') 整形为 fact_institution_event 列结构。

    institution_id='EXEC_'+stock_code 配合 max_per_inst=1 实现 per-stock dedup。
    """
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    where = ["fe.direction=?", "fe.notice_date >= ?", "fe.notice_date <= ?", "ind.tdx_l2_name IS NOT NULL"]
    params = [direction, sd, ed]
    if min_pct_total > 0:
        where.append("fe.total_change_pct_total >= ?")
        params.append(min_pct_total)
    if individual_only:
        where.append("fe.any_individual = 1 AND fe.any_corporate = 0")
    sql = f"""
        SELECT fe.notice_date, fe.stock_code, fe.n_shareholders,
               fe.total_change_pct_total, fe.max_change_pct_total,
               fe.any_individual, fe.any_corporate,
               ind.tdx_l1_name l1, ind.tdx_l2_name l2
        FROM fact_executive_trade_event fe
        LEFT JOIN dim_stock_tdx_industry ind ON fe.stock_code = ind.stock_code
        WHERE {' AND '.join(where)}
    """
    rows = _rows_as_dicts(_execute(conn, sql, params))
    events = []
    for row in rows:
        notice_date = _event_date_text(row.get("notice_date"))
        stock_code = str(row.get("stock_code") or "")
        events.append({
            "institution_id": f"EXEC_{stock_code}",
            "stock_code": stock_code,
            "notice_date": notice_date,
            "report_date": notice_date,
            "event_type": "exec_increase",
            "premium_pct": 0.0,
            "premium_bucket": None,
            "inst_name": "EXEC",
            "inst_type": "EXEC",
            "l1": row.get("l1"),
            "l2": row.get("l2"),
            "stable_score": _safe_float(row.get("total_change_pct_total")) or 0.0,
            "verdict": "exec",
            "ho_sharpe": None,
            "ho_n": None,
            "entry_lag": None,
            "max_hold_days": None,
            "stop_loss": None,
            "take_profit": None,
            "total_change_pct_total": row.get("total_change_pct_total"),
            "n_shareholders": row.get("n_shareholders"),
        })
    return events


def load_lhb_events_as_events(conn, start_date: str, end_date: str, min_inst_seats: int) -> list[dict[str, Any]]:
    """M5 step 1：把 fact_lhb_event 整形成 fact_institution_event 的列结构，
    使其可以直接喂进 simulate_portfolio（institution_id='LHB_'+code 提供 per-stock dedup）。

    start_date / end_date YYYYMMDD。
    """
    sd = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    ed = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    sql = """
        SELECT fl.trade_date, fl.stock_code, fl.net_buy, fl.inst_buy_seats,
               fl.is_inst_net_buy,
               ind.tdx_l1_name l1, ind.tdx_l2_name l2
        FROM fact_lhb_event fl
        LEFT JOIN dim_stock_tdx_industry ind ON fl.stock_code = ind.stock_code
        WHERE fl.is_inst_net_buy = 1
          AND fl.inst_buy_seats >= ?
          AND fl.trade_date >= ? AND fl.trade_date <= ?
          AND ind.tdx_l2_name IS NOT NULL
    """
    rows = _rows_as_dicts(_execute(conn, sql, (min_inst_seats, sd, ed)))
    events = []
    for row in rows:
        stock_code = str(row.get("stock_code") or "")
        seats = _safe_float(row.get("inst_buy_seats")) or 0.0
        net_buy = _safe_float(row.get("net_buy")) or 0.0
        notice_date = _event_date_text(row.get("trade_date"))
        events.append({
            "institution_id": f"LHB_{stock_code}",
            "stock_code": stock_code,
            "notice_date": notice_date,
            "report_date": notice_date,
            "event_type": "lhb_inst_buy",
            "premium_pct": 0.0,
            "premium_bucket": None,
            "inst_name": "LHB",
            "inst_type": "LHB",
            "l1": row.get("l1"),
            "l2": row.get("l2"),
            "stable_score": seats * 10.0 + net_buy / 1e8,
            "verdict": "lhb",
            "ho_sharpe": None,
            "ho_n": None,
            "entry_lag": None,
            "max_hold_days": None,
            "stop_loss": None,
            "take_profit": None,
            "inst_buy_seats": row.get("inst_buy_seats"),
            "net_buy": row.get("net_buy"),
        })
    return events


def load_events_with_pit_cohort(conn, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """加载 portfolio 回测期的候选事件，附加 cohort stable 标签（PIT view）。

    start_date / end_date YYYYMMDD。
    """
    sql = """
        WITH ev AS (
          SELECT fe.institution_id, fe.stock_code, fe.notice_date, fe.report_date,
                 fe.event_type, fe.premium_pct, fe.premium_bucket,
                 ii.name inst_name, ii.type inst_type,
                 ind.tdx_l1_name l1, ind.tdx_l2_name l2
          FROM fact_institution_event fe
          LEFT JOIN inst_institutions ii ON fe.institution_id = ii.id
          LEFT JOIN dim_stock_tdx_industry ind ON fe.stock_code = ind.stock_code
          WHERE fe.event_type IN ('new_entry','increase')
            AND fe.notice_date >= ? AND fe.notice_date <= ?
            AND ii.type != '北向' AND ind.tdx_l2_name IS NOT NULL
        )
        SELECT ev.*,
               v.stable_score, v.verdict, v.ho_sharpe, v.ho_n,
               v.entry_lag, v.max_hold_days, v.stop_loss, v.take_profit
        FROM ev
        LEFT JOIN v_institution_l2_score v
          ON v.institution_id = ev.institution_id AND v.l2_name = ev.l2
    """
    return _rows_as_dicts(_execute(conn, sql, (start_date, end_date)))


def load_prices(codes: list[str], start: str, end: str) -> dict[str, dict[str, Any]]:
    """每股按 date 排序的原生 price history。"""
    if not codes:
        return {}
    mkt = get_market_conn()
    sql = f"""
        SELECT code, date, open, high, low, close
        FROM price_kline WHERE freq='daily' AND adjust='qfq'
          AND code IN ({','.join(['?']*len(codes))})
          AND date BETWEEN ? AND ?
    """
    try:
        rows = _rows_as_dicts(_execute(mkt, sql, list(codes)+[start, end]))
    finally:
        mkt.close()
    return _build_price_history(rows)


def _yymmdd_to_dash(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def simulate_portfolio(
    events: list[dict[str, Any]],
    prices: dict,
    trading_days: list,
    initial_capital: float = 1e7,
    top_n: int = 10,
    max_per_inst: int = 3,
    max_per_l2: int = 4,
    policy_filter: Optional[Callable[[dict[str, Any]], bool]] = None,
    sleeve_filter: Optional[Callable[[dict[str, Any]], bool]] = None,
    default_params: Optional[dict] = None,
    cost_bps_one_way: float = 0.0,
) -> dict:
    """portfolio simulator。

    events：候选事件 records；policy_filter：每个事件返回 True/False 是否进入候选
    sleeve_filter：若提供，则通过的事件享有当日 topN 优先填充权（overlay / sleeve 语义）
    cost_bps_one_way：单边成本（以 basis points, 15 = 0.15%）。买卖两次都扣。
    默认 cohort 最优参数若缺失用 default_params
    """
    cost_frac = cost_bps_one_way / 10000.0
    default_params = default_params or {"entry_lag": 1, "max_hold_days": 20, "stop_loss": -0.10, "take_profit": 0.20}

    # 事件按 notice_date 排序
    events_by_day: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        item = dict(event)
        item["notice_dash"] = _yymmdd_to_dash(str(item.get("notice_date") or ""))
        events_by_day.setdefault(item["notice_dash"], []).append(item)
    for day_events in events_by_day.values():
        day_events.sort(key=lambda item: (item["notice_dash"], _notice_key(item)))

    cash = initial_capital
    open_positions = []  # list of dict {inst, stock, l2, entry_date, entry_price, shares, exit_rule}
    trades = []
    equity_series = []
    total_transacted = 0.0

    trade_id = 0
    max_per_position = initial_capital / top_n * 1.2  # 单笔仓位上限

    # 每日遍历
    for day in trading_days:
        # 1. 今日到期事件触发建仓
        today_evs = list(events_by_day.get(day, []))
        if sleeve_filter is not None:
            # overlay 语义：sleeve 事件先填充，剩余名额给 core（policy_filter）
            sleeve_indexes = {
                idx for idx, event in enumerate(today_evs)
                if sleeve_filter(event)
            }
            sleeve_evs = [today_evs[idx] for idx in sleeve_indexes]
            core_evs = [
                event
                for idx, event in enumerate(today_evs)
                if idx not in sleeve_indexes and (policy_filter(event) if policy_filter else True)
            ]
            if any("stable_score" in event for event in sleeve_evs):
                sleeve_evs = sorted(sleeve_evs, key=_stable_score_key)
            # core 保持 notice_date 先来先到（稳定可复现）
            core_evs = sorted(core_evs, key=_notice_key)
            today_evs = sleeve_evs + core_evs
        elif policy_filter is not None:
            today_evs = [event for event in today_evs if policy_filter(event)]
            if any("stable_score" in event for event in today_evs):
                today_evs = sorted(today_evs, key=_stable_score_key)
        candidates = today_evs[:top_n]

        for ev in candidates:
            # 检查单机构/L2上限（已持仓）
            n_inst = sum(1 for p in open_positions if p["inst"] == ev["institution_id"])
            n_l2 = sum(1 for p in open_positions if p["l2"] == ev["l2"])
            if n_inst >= max_per_inst or n_l2 >= max_per_l2:
                continue
            code = str(ev["stock_code"])
            code_px = prices.get(code)
            if _price_empty(code_px):
                continue
            # entry_lag 交易日后开仓（兼容 NaN）
            lag_raw = ev.get("entry_lag")
            if _is_missing(lag_raw):
                lag = default_params["entry_lag"]
            else:
                lag = int(lag_raw)
            # 找到 notice_date 后第 lag 个交易日
            future_dates = [date for date in code_px["dates"] if date > day]
            if len(future_dates) <= lag:
                continue
            entry_date = future_dates[lag]
            if entry_date > trading_days[-1]:
                continue
            entry_price_raw = _safe_float((_price_at(code_px, entry_date) or {}).get("close"))
            if entry_price_raw is None or entry_price_raw <= 0:
                continue
            # 交易成本：买入端 slippage/fee 推高实际成本价
            entry_price = entry_price_raw * (1 + cost_frac)
            alloc = min(max_per_position, cash * 0.1)  # 单笔占 10% cash 或 cap
            if alloc < 1000:
                continue
            shares = alloc / entry_price
            cash -= alloc
            total_transacted += alloc
            mh_raw = ev.get("max_hold_days")
            max_hold = default_params["max_hold_days"] if _is_missing(mh_raw) else int(mh_raw)
            sl = ev.get("stop_loss"); tp = ev.get("take_profit")
            if _is_missing(sl): sl = default_params.get("stop_loss")
            if _is_missing(tp): tp = default_params.get("take_profit")
            open_positions.append({
                "trade_id": trade_id, "inst": ev["institution_id"], "stock": code, "l2": ev["l2"],
                "notice_date": ev["notice_date"], "entry_date": entry_date, "entry_price": entry_price,
                "shares": shares, "max_hold": max_hold, "stop_loss": sl, "take_profit": tp,
                "position_value": alloc,
            })
            trade_id += 1

        # 2. 检查已持仓是否退出
        surviving = []
        for pos in open_positions:
            code_px = prices.get(pos["stock"])
            row = _price_at(code_px, day) if code_px else None
            if row is None:
                surviving.append(pos)
                continue
            # 收盘价评估（简化，不做日内止损精确）
            close = _safe_float(row.get("close"))
            if close is None:
                surviving.append(pos)
                continue
            low = _safe_float(row.get("low"))
            high = _safe_float(row.get("high"))
            low = close if low is None else low
            high = close if high is None else high
            ret = close / pos["entry_price"] - 1
            # 算持仓天数
            ent_idx = code_px["index"].get(pos["entry_date"])
            cur_idx = code_px["index"].get(day)
            if ent_idx is None or cur_idx is None:
                surviving.append(pos)
                continue
            hold_days = cur_idx - ent_idx
            if hold_days <= 0:
                surviving.append(pos)
                continue
            exit_reason = None
            exit_price = close
            # 止损（盘中 low 触发）
            if pos["stop_loss"] is not None and low <= pos["entry_price"] * (1 + pos["stop_loss"]):
                exit_reason = "stop_loss"
                exit_price = pos["entry_price"] * (1 + pos["stop_loss"])
            # 止盈（盘中 high）
            elif pos["take_profit"] is not None and high >= pos["entry_price"] * (1 + pos["take_profit"]):
                exit_reason = "take_profit"
                exit_price = pos["entry_price"] * (1 + pos["take_profit"])
            # max_hold
            elif hold_days >= pos["max_hold"]:
                exit_reason = "max_hold"
                exit_price = close
            if exit_reason:
                # 卖出端 slippage/fee 压低实际卖出价
                exit_price = exit_price * (1 - cost_frac)
                pnl_pct = exit_price / pos["entry_price"] - 1
                cash += pos["shares"] * exit_price
                total_transacted += pos["shares"] * exit_price
                trades.append({
                    "trade_id": pos["trade_id"], "inst": pos["inst"], "stock": pos["stock"], "l2": pos["l2"],
                    "notice_date": pos["notice_date"], "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"], "exit_date": day, "exit_price": exit_price,
                    "hold_days": hold_days, "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                    "position_value": pos["position_value"],
                })
            else:
                surviving.append(pos)
        open_positions = surviving

        # 3. 计算当日 equity
        mark_val = 0.0
        for pos in open_positions:
            code_px = prices.get(pos["stock"])
            row = _price_at(code_px, day) if code_px else None
            close = _safe_float(row.get("close")) if row else None
            if close is None:
                mark_val += pos["shares"] * pos["entry_price"]
                continue
            mark_val += pos["shares"] * close
        equity = cash + mark_val
        equity_series.append({"date": day, "equity": equity, "cash": cash,
                              "n_open_positions": len(open_positions)})

    # 最后平仓剩余
    last_day = trading_days[-1]
    for pos in open_positions:
        code_px = prices.get(pos["stock"])
        if _price_empty(code_px):
            continue
        valid_dates = [date for date in code_px["dates"] if date <= last_day]
        if len(valid_dates) == 0:
            continue
        close = _safe_float((_price_at(code_px, valid_dates[-1]) or {}).get("close"))
        if close is None:
            continue
        exit_price = close * (1 - cost_frac)
        pnl_pct = exit_price / pos["entry_price"] - 1
        cash += pos["shares"] * exit_price
        trades.append({
            "trade_id": pos["trade_id"], "inst": pos["inst"], "stock": pos["stock"], "l2": pos["l2"],
            "notice_date": pos["notice_date"], "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "exit_date": valid_dates[-1], "exit_price": exit_price,
            "hold_days": -1, "pnl_pct": pnl_pct, "exit_reason": "force_close_end",
            "position_value": pos["position_value"],
        })

    return {
        "equity_curve": equity_series,
        "trades": trades,
        "initial_capital": initial_capital,
        "final_equity": equity_series[-1]["equity"] if equity_series else initial_capital,
        "total_transacted": total_transacted,
    }


def evaluate(result: dict) -> dict:
    eq = result["equity_curve"]
    tr = result["trades"]
    init = result["initial_capital"]
    if not eq:
        return {"cagr": None, "max_drawdown": None, "calmar": None, "sharpe": None,
                "profit_factor": None, "win_rate": None, "turnover": None,
                "final_equity": init, "n_trades": 0}

    first_date = datetime.strptime(eq[0]["date"], "%Y-%m-%d")
    last_date = datetime.strptime(eq[-1]["date"], "%Y-%m-%d")
    days = (last_date - first_date).days or 1
    years = days / 365.25
    final_eq = _safe_float(eq[-1]["equity"]) or init
    cagr = (final_eq / init) ** (1 / max(years, 0.01)) - 1

    drawdowns = [row["drawdown"] for row in _drawdown_rows(eq)]
    maxdd = min(drawdowns) if drawdowns else 0.0

    daily_ret = [0.0]
    for prev, cur in zip(eq, eq[1:]):
        prev_eq = _safe_float(prev.get("equity"))
        cur_eq = _safe_float(cur.get("equity"))
        daily_ret.append(cur_eq / prev_eq - 1 if prev_eq and cur_eq is not None else 0.0)
    std = _sample_std(daily_ret)
    sharpe = float((sum(daily_ret) / len(daily_ret)) / std * math.sqrt(252)) if std > 0 else 0.0
    calmar = float(cagr / abs(maxdd)) if maxdd < 0 else float("inf")

    if tr:
        pnl_values = [_safe_float(row.get("pnl_pct")) or 0.0 for row in tr]
        wins = sum(value for value in pnl_values if value > 0)
        losses = abs(sum(value for value in pnl_values if value < 0))
        pf = float(wins / losses) if losses > 0 else float("inf")
        wr = sum(1 for value in pnl_values if value > 0) / len(pnl_values)
        turnover = result["total_transacted"] / init
    else:
        pf = wr = None
        turnover = 0.0

    return {
        "cagr": float(cagr), "max_drawdown": maxdd, "calmar": calmar, "sharpe": sharpe,
        "profit_factor": pf, "win_rate": wr, "turnover": turnover,
        "final_equity": float(final_eq), "n_trades": int(len(tr)),
    }


def benchmark_buy_hold_hs300(prices_hs300: Optional[dict[str, Any]], trading_days: list, initial_capital: float = 1e7) -> dict:
    """沪深 300 ETF buy-and-hold"""
    if _price_empty(prices_hs300):
        return {"equity_curve": [], "trades": [], "initial_capital": initial_capital,
                "final_equity": initial_capital, "total_transacted": 0}
    first_day = [d for d in trading_days if d in prices_hs300["by_date"]]
    if not first_day:
        return {"equity_curve": [], "trades": [], "initial_capital": initial_capital,
                "final_equity": initial_capital, "total_transacted": 0}
    entry = _safe_float((_price_at(prices_hs300, first_day[0]) or {}).get("close"))
    if entry is None or entry <= 0:
        return {"equity_curve": [], "trades": [], "initial_capital": initial_capital,
                "final_equity": initial_capital, "total_transacted": 0}
    shares = initial_capital / entry
    eq = []
    for d in trading_days:
        close = _safe_float((_price_at(prices_hs300, d) or {}).get("close"))
        if close is not None:
            eq.append({"date": d, "equity": shares * close, "cash": 0, "n_open_positions": 1})
    return {"equity_curve": eq, "trades": [],
            "initial_capital": initial_capital, "final_equity": eq[-1]["equity"] if eq else initial_capital,
            "total_transacted": initial_capital}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20241001", help="Portfolio 回测期起点 YYYYMMDD")
    parser.add_argument("--end", default="20260421", help="Portfolio 回测期终点 YYYYMMDD")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--cost-bps", type=float, default=0.0,
                        help="单边交易成本 bps（15 = 0.15%%，典型 A 股 one-way 含佣金+印花税+impact）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        conn.executescript(PORTFOLIO_TABLE_DDL)

        # 加载回测期事件 + PIT cohort
        events = load_events_with_pit_cohort(conn, args.start, args.end)
        logger.info("候选事件 %d 条 (%s ~ %s)", len(events), args.start, args.end)
        if not events:
            return

        # 交易日：从 price_kline 取所有出现过的 date（去重排序）
        codes = sorted({str(row.get("stock_code") or "") for row in events if row.get("stock_code")} | {"510300"})
        prices = load_prices(codes, _yymmdd_to_dash(args.start), _yymmdd_to_dash(args.end))
        all_dates = set()
        for g in prices.values():
            all_dates.update(g.get("dates", []))
        trading_days = sorted(all_dates)
        logger.info("交易日 %d 天", len(trading_days))

        # 策略 A: stable cohort only
        def policy_stable(row):
            if row.get("verdict") != "stable": return False
            if (row.get("ho_n") or 0) < 15: return False
            if (row.get("ho_sharpe") or 0) < 1.0: return False
            if row.get("premium_bucket") == "high_premium": return False
            return True

        # 策略 B: 候选事件等权（不过滤 cohort，只排 high_premium）
        def policy_equal(row):
            return row.get("premium_bucket") != "high_premium"

        # 策略 C: 随机 topN（种子固定，避免方差误导）
        rng = random.Random(42)
        def policy_random(row):
            return rng.random() < 0.5

        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        results = {}

        def _log_row(name, m):
            logger.info("%s: n_trades=%d CAGR=%.2f%% MaxDD=%.2f%% Calmar=%.2f Sharpe=%.2f PF=%s WR=%s turnover=%.2f final=%s",
                        name, m["n_trades"],
                        (m["cagr"] or 0) * 100, (m["max_drawdown"] or 0) * 100,
                        m["calmar"] if m["calmar"] != float("inf") else 999,
                        m["sharpe"] or 0,
                        f"{m['profit_factor']:.2f}" if m["profit_factor"] not in (None, float("inf")) else "-",
                        f"{(m['win_rate'] or 0)*100:.1f}%", m["turnover"] or 0,
                        f"{m['final_equity']/1e7:.3f}x")

        for name, pf in [("stable_cohort_pit", policy_stable),
                         ("all_events_equal", policy_equal),
                         ("random_half", policy_random)]:
            logger.info("==== %s ====", name)
            r = simulate_portfolio(events, prices, trading_days,
                                   initial_capital=1e7, top_n=args.top_n,
                                   policy_filter=pf,
                                   cost_bps_one_way=args.cost_bps)
            m = evaluate(r)
            _log_row(name, m)
            results[name] = (r, m)

        # δ overlay：core (all_events_equal) + sleeve (stable_cohort_pit) 优先填充
        logger.info("==== core_plus_overlay ====")
        r_overlay = simulate_portfolio(events, prices, trading_days,
                                       initial_capital=1e7, top_n=args.top_n,
                                       policy_filter=policy_equal,
                                       sleeve_filter=policy_stable,
                                       cost_bps_one_way=args.cost_bps)
        m_overlay = evaluate(r_overlay)
        _log_row("core_plus_overlay", m_overlay)
        results["core_plus_overlay"] = (r_overlay, m_overlay)

        # M5 step 1：fact_lhb_event 作为独立事件源（§2 新数据源候选分析第一档）
        for seats, pname in [(1, "lhb_inst_net_buy"),
                             (3, "lhb_inst_buy_ge3"),
                             (5, "lhb_inst_buy_ge5")]:
            lhb_events = load_lhb_events_as_events(conn, args.start, args.end, min_inst_seats=seats)
            if not lhb_events:
                logger.info("==== %s ====  SKIP (0 events)", pname)
                continue
            # 补齐 LHB 事件中新出现的股票价格
            lhb_codes = sorted({str(row.get("stock_code") or "") for row in lhb_events if row.get("stock_code")})
            missing = [c for c in lhb_codes if c not in prices]
            if missing:
                extra_px = load_prices(missing, _yymmdd_to_dash(args.start), _yymmdd_to_dash(args.end))
                prices.update(extra_px)
            logger.info("==== %s ==== (events=%d)", pname, len(lhb_events))
            # LHB 用 per-stock dedup（institution_id='LHB_<code>'），max_per_inst=1 保证同股同时只有 1 笔
            r_lhb = simulate_portfolio(lhb_events, prices, trading_days,
                                       initial_capital=1e7, top_n=args.top_n,
                                       max_per_inst=1, max_per_l2=4,
                                       policy_filter=lambda r: True,
                                       cost_bps_one_way=args.cost_bps)
            m_lhb = evaluate(r_lhb)
            _log_row(pname, m_lhb)
            results[pname] = (r_lhb, m_lhb)

        # M5 step 2：fact_executive_trade_event (direction='buy') 作为独立事件源
        # 包含 exec_sell_as_buy_ge1pct 安慰剂控制组（把卖方事件当买信号回测）
        for direction_sql, min_pct, ind_only, pname in [
            ("buy",  0.0, False, "exec_buy_all"),
            ("buy",  1.0, False, "exec_buy_ge1pct"),
            ("buy",  0.5, False, "exec_buy_ge0.5pct"),
            ("buy",  0.0, True,  "exec_buy_individual"),
            ("sell", 0.5, False, "exec_sell_as_buy_ge0.5pct_PLACEBO"),
            ("sell", 1.0, False, "exec_sell_as_buy_ge1pct_PLACEBO"),
            ("sell", 3.0, False, "exec_sell_as_buy_ge3pct_PLACEBO"),
        ]:
            exec_events = load_exec_trade_events_as_events(conn, args.start, args.end,
                                                           min_pct_total=min_pct,
                                                           individual_only=ind_only,
                                                           direction=direction_sql)
            if not exec_events:
                logger.info("==== %s ====  SKIP (0 events)", pname)
                continue
            exec_codes = sorted({str(row.get("stock_code") or "") for row in exec_events if row.get("stock_code")})
            missing = [c for c in exec_codes if c not in prices]
            if missing:
                extra_px = load_prices(missing, _yymmdd_to_dash(args.start), _yymmdd_to_dash(args.end))
                prices.update(extra_px)
            logger.info("==== %s ==== (events=%d)", pname, len(exec_events))
            r_ex = simulate_portfolio(exec_events, prices, trading_days,
                                      initial_capital=1e7, top_n=args.top_n,
                                      max_per_inst=1, max_per_l2=4,
                                      policy_filter=lambda r: True,
                                      cost_bps_one_way=args.cost_bps)
            m_ex = evaluate(r_ex)
            _log_row(pname, m_ex)
            results[pname] = (r_ex, m_ex)

        # 沪深 300 对照
        hs300 = prices.get("510300")
        bh = benchmark_buy_hold_hs300(hs300, trading_days)
        m_bh = evaluate(bh)
        logger.info("hs300_buy_hold: CAGR=%.2f%% MaxDD=%.2f%% Calmar=%.2f",
                    (m_bh["cagr"] or 0)*100, (m_bh["max_drawdown"] or 0)*100,
                    m_bh["calmar"] if m_bh["calmar"] != float("inf") else 999)
        results["hs300_buy_hold"] = (bh, m_bh)

        if not args.dry_run:
            # 落库：equity_curve + eval
            for name, (r, m) in results.items():
                eq = r["equity_curve"]
                if eq:
                    curve_rows = [
                        (
                            name, run_id, row.get("date"), row.get("equity"), row.get("cash"),
                            row.get("n_open_positions"), row.get("drawdown"),
                        )
                        for row in _drawdown_rows(eq)
                    ]
                    conn.executemany(
                        """
                        INSERT INTO fact_policy_equity_curve
                        (policy_name, run_id, date, equity, cash, n_open_positions, drawdown)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        curve_rows,
                    )

                tr = r["trades"]
                if tr:
                    trade_rows = [
                        (
                            name, run_id, row.get("trade_id"), row.get("inst"), row.get("stock"),
                            row.get("notice_date"), row.get("entry_date"), row.get("entry_price"),
                            row.get("exit_date"), row.get("exit_price"), row.get("hold_days"),
                            row.get("pnl_pct"), row.get("exit_reason"), row.get("position_value"),
                        )
                        for row in tr
                    ]
                    conn.executemany(
                        """
                        INSERT INTO fact_policy_trade
                        (policy_name, run_id, trade_id, institution_id, stock_code, notice_date,
                         entry_date, entry_price, exit_date, exit_price, hold_days, pnl_pct,
                         exit_reason, position_value)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        trade_rows,
                    )

                benchmark_cagr = m_bh["cagr"]
                excess = (m["cagr"] or 0) - (benchmark_cagr or 0) if m["cagr"] else 0
                conn.execute(
                    "INSERT OR REPLACE INTO fact_policy_eval VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, run_id, args.start, args.end, m["n_trades"],
                     m["cagr"], m["max_drawdown"],
                     m["calmar"] if m["calmar"] != float("inf") else None,
                     m["sharpe"], m["profit_factor"] if m["profit_factor"] != float("inf") else None,
                     m["win_rate"], m["turnover"], m["final_equity"],
                     benchmark_cagr, excess,
                     f"top_n={args.top_n}, cohort=institution_L2_pit_20240930",
                     datetime.utcnow().isoformat())
                )
            conn.commit()
            logger.info("落库 fact_policy_equity_curve / fact_policy_trade / fact_policy_eval run_id=%s", run_id)

    finally:
        conn.close()


if __name__ == "__main__":
    main()

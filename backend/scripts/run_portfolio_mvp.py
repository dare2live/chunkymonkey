#!/usr/bin/env python3
"""P1.A 阶段 2 / 3：stable cohort 非 ML 组合 MVP 回测（§2 codex 路径 1.5 + Claude 响应 4 节）。

策略规则（§2 codex §4.2 + Claude 响应 §4）：
  1. 候选事件：event_type IN ('new_entry','increase')
  2. cohort 准入：v_institution_l2_score_pit.verdict='stable' 且 ho_n >= 15 且 ho_sharpe >= 1
  3. 成本过滤：premium_bucket != 'high_premium'
  4. 当日候选超过 topN：按 stable_score 降序取
  5. 仓位：等权，单机构 / 单 L2 / 单股票上限
  6. 退出：使用 cohort 的 train 最优参数（entry_lag, max_hold_days, stop_loss, take_profit）

时间窗口：
  - cohort 评估期：2023-04 ~ 2024-09-30（锁在 v_institution_l2_score_pit）
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
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

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


def load_events_with_pit_cohort(conn, start_date: str, end_date: str) -> pd.DataFrame:
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
        LEFT JOIN v_institution_l2_score_pit v
          ON v.institution_id = ev.institution_id AND v.l2_name = ev.l2
    """
    return pd.read_sql_query(sql, conn, params=(start_date, end_date))


def load_prices(codes: list[str], start: str, end: str) -> dict:
    """每股按 date 排序 DataFrame"""
    mkt = get_market_conn()
    sql = f"""
        SELECT code, date, open, high, low, close
        FROM price_kline WHERE freq='daily' AND adjust='qfq'
          AND code IN ({','.join(['?']*len(codes))})
          AND date BETWEEN ? AND ?
    """
    df = pd.read_sql_query(sql, mkt, params=list(codes)+[start, end])
    mkt.close()
    return {c: g.set_index("date").sort_index() for c, g in df.groupby("code", sort=False)}


def _yymmdd_to_dash(s: str) -> str:
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def simulate_portfolio(
    events: pd.DataFrame,
    prices: dict,
    trading_days: list,
    initial_capital: float = 1e7,
    top_n: int = 10,
    max_per_inst: int = 3,
    max_per_l2: int = 4,
    policy_filter: Optional[callable] = None,
    default_params: Optional[dict] = None,
) -> dict:
    """portfolio simulator。

    events：候选事件；policy_filter：每个事件返回 True/False 是否进入候选
    默认 cohort 最优参数若缺失用 default_params
    """
    default_params = default_params or {"entry_lag": 1, "max_hold_days": 20, "stop_loss": -0.10, "take_profit": 0.20}

    # 事件按 notice_date 排序
    events = events.copy()
    events["notice_dash"] = events["notice_date"].map(_yymmdd_to_dash)
    events = events.sort_values("notice_dash").reset_index(drop=True)

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
        today_evs = events[events["notice_dash"] == day]
        if policy_filter is not None:
            today_evs = today_evs[today_evs.apply(policy_filter, axis=1)]

        # topN 限制：按 stable_score 降序
        if "stable_score" in today_evs.columns:
            today_evs = today_evs.sort_values("stable_score", ascending=False, na_position="last")
        candidates = today_evs.head(top_n).to_dict("records")

        for ev in candidates:
            # 检查单机构/L2上限（已持仓）
            n_inst = sum(1 for p in open_positions if p["inst"] == ev["institution_id"])
            n_l2 = sum(1 for p in open_positions if p["l2"] == ev["l2"])
            if n_inst >= max_per_inst or n_l2 >= max_per_l2:
                continue
            code = str(ev["stock_code"])
            code_px = prices.get(code)
            if code_px is None or code_px.empty:
                continue
            # entry_lag 交易日后开仓（兼容 NaN）
            lag_raw = ev.get("entry_lag")
            if lag_raw is None or (isinstance(lag_raw, float) and np.isnan(lag_raw)):
                lag = default_params["entry_lag"]
            else:
                lag = int(lag_raw)
            # 找到 notice_date 后第 lag 个交易日
            future_dates = code_px.index[code_px.index > day]
            if len(future_dates) <= lag:
                continue
            entry_date = future_dates[lag]
            if entry_date > trading_days[-1]:
                continue
            entry_price = float(code_px.loc[entry_date, "close"])
            if pd.isna(entry_price) or entry_price <= 0:
                continue
            alloc = min(max_per_position, cash * 0.1)  # 单笔占 10% cash 或 cap
            if alloc < 1000:
                continue
            shares = alloc / entry_price
            cash -= alloc
            total_transacted += alloc
            mh_raw = ev.get("max_hold_days")
            max_hold = default_params["max_hold_days"] if (mh_raw is None or (isinstance(mh_raw, float) and np.isnan(mh_raw))) else int(mh_raw)
            sl = ev.get("stop_loss"); tp = ev.get("take_profit")
            if sl is None or (isinstance(sl, float) and np.isnan(sl)): sl = default_params.get("stop_loss")
            if tp is None or (isinstance(tp, float) and np.isnan(tp)): tp = default_params.get("take_profit")
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
            if code_px is None or day not in code_px.index:
                surviving.append(pos)
                continue
            row = code_px.loc[day]
            # 收盘价评估（简化，不做日内止损精确）
            close = float(row["close"])
            low = float(row["low"]) if not pd.isna(row.get("low", np.nan)) else close
            high = float(row["high"]) if not pd.isna(row.get("high", np.nan)) else close
            ret = close / pos["entry_price"] - 1
            # 算持仓天数
            ent_idx = code_px.index.get_loc(pos["entry_date"])
            cur_idx = code_px.index.get_loc(day)
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
            if code_px is None or day not in code_px.index:
                mark_val += pos["shares"] * pos["entry_price"]
                continue
            mark_val += pos["shares"] * float(code_px.loc[day, "close"])
        equity = cash + mark_val
        equity_series.append({"date": day, "equity": equity, "cash": cash,
                              "n_open_positions": len(open_positions)})

    # 最后平仓剩余
    last_day = trading_days[-1]
    for pos in open_positions:
        code_px = prices.get(pos["stock"])
        if code_px is None:
            continue
        valid_dates = code_px.index[code_px.index <= last_day]
        if len(valid_dates) == 0:
            continue
        exit_price = float(code_px.loc[valid_dates[-1], "close"])
        pnl_pct = exit_price / pos["entry_price"] - 1
        cash += pos["shares"] * exit_price
        trades.append({
            "trade_id": pos["trade_id"], "inst": pos["inst"], "stock": pos["stock"], "l2": pos["l2"],
            "notice_date": pos["notice_date"], "entry_date": pos["entry_date"],
            "entry_price": pos["entry_price"], "exit_date": valid_dates[-1], "exit_price": exit_price,
            "hold_days": -1, "pnl_pct": pnl_pct, "exit_reason": "force_close_end",
            "position_value": pos["position_value"],
        })

    eq_df = pd.DataFrame(equity_series)
    trade_df = pd.DataFrame(trades)
    return {
        "equity_curve": eq_df,
        "trades": trade_df,
        "initial_capital": initial_capital,
        "final_equity": eq_df.iloc[-1]["equity"] if not eq_df.empty else initial_capital,
        "total_transacted": total_transacted,
    }


def evaluate(result: dict) -> dict:
    eq = result["equity_curve"]
    tr = result["trades"]
    init = result["initial_capital"]
    if eq.empty:
        return {"cagr": None, "max_drawdown": None, "calmar": None, "sharpe": None,
                "profit_factor": None, "win_rate": None, "turnover": None,
                "final_equity": init, "n_trades": 0}

    eq = eq.copy()
    eq["date_dt"] = pd.to_datetime(eq["date"])
    days = (eq["date_dt"].iloc[-1] - eq["date_dt"].iloc[0]).days or 1
    years = days / 365.25
    final_eq = eq.iloc[-1]["equity"]
    cagr = (final_eq / init) ** (1 / max(years, 0.01)) - 1

    running_max = eq["equity"].cummax()
    dd = eq["equity"] / running_max - 1
    maxdd = float(dd.min())

    daily_ret = eq["equity"].pct_change().fillna(0)
    std = daily_ret.std()
    sharpe = float(daily_ret.mean() / std * np.sqrt(252)) if std > 0 else 0.0
    calmar = float(cagr / abs(maxdd)) if maxdd < 0 else float("inf")

    if not tr.empty:
        wins = tr[tr["pnl_pct"] > 0]["pnl_pct"].sum() * 1.0
        losses = abs(tr[tr["pnl_pct"] < 0]["pnl_pct"].sum())
        pf = float(wins / losses) if losses > 0 else float("inf")
        wr = float((tr["pnl_pct"] > 0).mean())
        turnover = result["total_transacted"] / init
    else:
        pf = wr = None
        turnover = 0.0

    return {
        "cagr": float(cagr), "max_drawdown": maxdd, "calmar": calmar, "sharpe": sharpe,
        "profit_factor": pf, "win_rate": wr, "turnover": turnover,
        "final_equity": float(final_eq), "n_trades": int(len(tr)),
    }


def benchmark_buy_hold_hs300(prices_hs300: pd.DataFrame, trading_days: list, initial_capital: float = 1e7) -> dict:
    """沪深 300 ETF buy-and-hold"""
    if prices_hs300 is None or prices_hs300.empty:
        return {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "initial_capital": initial_capital,
                "final_equity": initial_capital, "total_transacted": 0}
    first_day = [d for d in trading_days if d in prices_hs300.index]
    if not first_day:
        return {"equity_curve": pd.DataFrame(), "trades": pd.DataFrame(), "initial_capital": initial_capital,
                "final_equity": initial_capital, "total_transacted": 0}
    entry = float(prices_hs300.loc[first_day[0], "close"])
    shares = initial_capital / entry
    eq = []
    for d in trading_days:
        if d in prices_hs300.index:
            eq.append({"date": d, "equity": shares * float(prices_hs300.loc[d, "close"]),
                       "cash": 0, "n_open_positions": 1})
    return {"equity_curve": pd.DataFrame(eq), "trades": pd.DataFrame(),
            "initial_capital": initial_capital, "final_equity": eq[-1]["equity"] if eq else initial_capital,
            "total_transacted": initial_capital}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20241001", help="Portfolio 回测期起点 YYYYMMDD")
    parser.add_argument("--end", default="20260421", help="Portfolio 回测期终点 YYYYMMDD")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    try:
        conn.executescript(PORTFOLIO_TABLE_DDL)

        # 加载回测期事件 + PIT cohort
        events = load_events_with_pit_cohort(conn, args.start, args.end)
        logger.info("候选事件 %d 条 (%s ~ %s)", len(events), args.start, args.end)
        if events.empty:
            return

        # 交易日：从 price_kline 取所有出现过的 date（去重排序）
        codes = sorted(set(events["stock_code"].astype(str).tolist()) | {"510300"})
        prices = load_prices(codes, _yymmdd_to_dash(args.start), _yymmdd_to_dash(args.end))
        all_dates = set()
        for g in prices.values():
            all_dates.update(g.index.tolist())
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
        rng = np.random.RandomState(42)
        def policy_random(row):
            return rng.random() < 0.5

        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        results = {}
        for name, pf in [("stable_cohort_pit", policy_stable),
                         ("all_events_equal", policy_equal),
                         ("random_half", policy_random)]:
            logger.info("==== %s ====", name)
            r = simulate_portfolio(events, prices, trading_days,
                                   initial_capital=1e7, top_n=args.top_n,
                                   policy_filter=pf)
            m = evaluate(r)
            logger.info("%s: n_trades=%d CAGR=%.2f%% MaxDD=%.2f%% Calmar=%.2f Sharpe=%.2f PF=%s WR=%s turnover=%.2f final=%s",
                        name, m["n_trades"],
                        (m["cagr"] or 0) * 100, (m["max_drawdown"] or 0) * 100,
                        m["calmar"] if m["calmar"] != float("inf") else 999,
                        m["sharpe"] or 0,
                        f"{m['profit_factor']:.2f}" if m["profit_factor"] not in (None, float("inf")) else "-",
                        f"{(m['win_rate'] or 0)*100:.1f}%", m["turnover"] or 0,
                        f"{m['final_equity']/1e7:.3f}x")
            results[name] = (r, m)

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
                if not eq.empty:
                    eq2 = eq.copy()
                    eq2["policy_name"] = name; eq2["run_id"] = run_id
                    running_max = eq2["equity"].cummax()
                    eq2["drawdown"] = eq2["equity"] / running_max - 1
                    eq2[["policy_name","run_id","date","equity","cash","n_open_positions","drawdown"]].to_sql(
                        "fact_policy_equity_curve", conn, if_exists="append", index=False)

                tr = r["trades"]
                if not tr.empty:
                    tr2 = tr.copy()
                    tr2["policy_name"] = name; tr2["run_id"] = run_id
                    tr2.rename(columns={"inst": "institution_id", "stock": "stock_code"}, inplace=True)
                    cols = ["policy_name","run_id","trade_id","institution_id","stock_code","notice_date",
                            "entry_date","entry_price","exit_date","exit_price","hold_days","pnl_pct",
                            "exit_reason","position_value"]
                    tr2[cols].to_sql("fact_policy_trade", conn, if_exists="append", index=False)

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

"""event_simulator.py — 事件驱动跟投仿真器

职责：
  - 给定一批机构披露事件 + 策略参数（entry_lag / max_hold_days / stop_loss / take_profit），
    按参数模拟跟投，返回业绩指标。
  - 纯 pandas，不走 Qlib backtest。
  - 服务于 §18 单 cohort Grid 验证与跟投回测表 fact_institution_follow_backtest 构造。

数据依赖：
  - 事件：smartmoney.db / fact_institution_event（调用方传入 DataFrame）
  - 价格：market_data.db / price_kline（daily + qfq）

参数语义：
  - entry_lag：披露日 D 起多少个交易日后开仓（0 = 当日收盘买入，1 = 次日收盘）
  - max_hold_days：最长持仓天数
  - stop_loss：止损阈值，负数如 -0.08 表示相对 entry_price -8%；None 表示不止损
  - take_profit：止盈阈值，正数如 +0.15；None 表示不止盈
  - 同日同时触发止损 / 止盈：保守按止损退出（盘中先触及哪个不可知）

输出指标（事件级跟随统计，不是 portfolio 级资金曲线）：
  - n_events / n_filled：事件数、有价格能成交的事件数
  - avg_pnl / avg_hold_days / win_rate：单笔 pnl 平均、平均持有天数、胜率
  - annual_return：基于 (1+avg_pnl)^(252/avg_hold_days)-1 的近似年化（不是组合年化）
  - sharpe：avg_pnl/std * sqrt(252/avg_hold_days)，单笔独立假设
  - avg_position_maxdd / p95_position_maxdd：单笔持仓期间最低价相对 entry 的回撤，均值和 5% 分位
    （注意：不是 portfolio 级累计回撤。disjoint 持仓串联复利的 MaxDD 在统计上没意义，故不报告）
  - exit_reason_counts：{stop_loss, take_profit, max_hold, stop_loss_conservative} 计数
  - positions：事件级明细 DataFrame
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from services.market_db import get_market_conn


def _normalize_date(d: Optional[str]) -> Optional[str]:
    """YYYYMMDD -> YYYY-MM-DD；已是 YYYY-MM-DD 则原样返回。"""
    if d is None:
        return None
    s = str(d).strip()
    if not s:
        return None
    if "-" in s and len(s) == 10:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def load_price_panel(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, pd.DataFrame]:
    """按 stock_code 分组加载 daily qfq 价格。

    返回 dict[code] = DataFrame(index=date, cols=[open, high, low, close])
    日期索引已排序。
    """
    if not stock_codes:
        return {}
    conn = get_market_conn()
    placeholders = ",".join("?" for _ in stock_codes)
    sql = f"""
        SELECT code, date, open, high, low, close
        FROM price_kline
        WHERE freq='daily' AND adjust='qfq'
          AND code IN ({placeholders})
          AND date BETWEEN ? AND ?
        ORDER BY code, date
    """
    params = list(stock_codes) + [start_date, end_date]
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()

    out: dict[str, pd.DataFrame] = {}
    for code, group in df.groupby("code", sort=False):
        g = group.drop(columns=["code"]).set_index("date").sort_index()
        out[str(code)] = g
    return out


def _simulate_one(
    entry_date: str,
    code_prices: pd.DataFrame,
    max_hold_days: int,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Optional[dict]:
    """单事件仿真。entry_date 必须是 code_prices 索引里的交易日。

    额外输出 intra_maxdd：持仓期间最低价相对 entry_price 的最大跌幅（负数或 0）。
    """
    if entry_date not in code_prices.index:
        return None
    entry_price = float(code_prices.loc[entry_date, "close"])
    if pd.isna(entry_price) or entry_price <= 0:
        return None

    dates = code_prices.index
    entry_pos = int(dates.get_loc(entry_date))
    end_pos = min(entry_pos + max_hold_days, len(dates) - 1)
    if end_pos <= entry_pos:
        return None

    lowest_seen = entry_price
    for i in range(entry_pos + 1, end_pos + 1):
        row = code_prices.iloc[i]
        day_low = float(row["low"]) if not pd.isna(row["low"]) else None
        day_high = float(row["high"]) if not pd.isna(row["high"]) else None
        if day_low is None or day_high is None:
            continue
        lowest_seen = min(lowest_seen, day_low)

        sl_hit = stop_loss is not None and (day_low / entry_price - 1) <= stop_loss
        tp_hit = take_profit is not None and (day_high / entry_price - 1) >= take_profit

        if sl_hit and tp_hit:
            intra = lowest_seen / entry_price - 1
            return {
                "entry_date": entry_date, "exit_date": dates[i],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + stop_loss),
                "pnl": stop_loss, "hold_days": i - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "stop_loss_conservative",
            }
        if sl_hit:
            intra = lowest_seen / entry_price - 1
            return {
                "entry_date": entry_date, "exit_date": dates[i],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + stop_loss),
                "pnl": stop_loss, "hold_days": i - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "stop_loss",
            }
        if tp_hit:
            intra = lowest_seen / entry_price - 1
            return {
                "entry_date": entry_date, "exit_date": dates[i],
                "entry_price": entry_price,
                "exit_price": entry_price * (1 + take_profit),
                "pnl": take_profit, "hold_days": i - entry_pos,
                "intra_maxdd": float(intra),
                "exit_reason": "take_profit",
            }

    last_row = code_prices.iloc[end_pos]
    last_close = float(last_row["close"]) if not pd.isna(last_row["close"]) else None
    if last_close is None or last_close <= 0:
        return None
    intra = lowest_seen / entry_price - 1
    return {
        "entry_date": entry_date, "exit_date": dates[end_pos],
        "entry_price": entry_price, "exit_price": last_close,
        "pnl": last_close / entry_price - 1,
        "hold_days": end_pos - entry_pos,
        "intra_maxdd": float(intra),
        "exit_reason": "max_hold",
    }


def simulate_events(
    events: pd.DataFrame,
    params: dict,
    prices_by_code: Optional[dict[str, pd.DataFrame]] = None,
) -> dict:
    """批量仿真。

    events 列要求：institution_id, stock_code, notice_date（YYYYMMDD 或 YYYY-MM-DD 均可）
    params：{entry_lag, max_hold_days, stop_loss, take_profit}
    prices_by_code：预加载的价格面板；None 时按事件需要自动加载
    """
    entry_lag = int(params.get("entry_lag", 0))
    max_hold = int(params.get("max_hold_days", 20))
    stop_loss = params.get("stop_loss")
    take_profit = params.get("take_profit")

    events = events.copy()
    events["_notice_norm"] = events["notice_date"].map(_normalize_date)
    events = events.dropna(subset=["_notice_norm", "stock_code"])

    if prices_by_code is None:
        codes = sorted(events["stock_code"].astype(str).unique().tolist())
        if not codes:
            return {"n_events": 0, "n_filled": 0}
        start = events["_notice_norm"].min()
        end = (
            pd.to_datetime(events["_notice_norm"].max())
            + pd.Timedelta(days=int(max_hold) * 2 + 30)
        ).strftime("%Y-%m-%d")
        prices_by_code = load_price_panel(codes, start, end)

    positions: list[dict] = []
    for _, ev in events.iterrows():
        code = str(ev["stock_code"])
        notice = ev["_notice_norm"]
        cp = prices_by_code.get(code)
        if cp is None or cp.empty:
            continue
        dates = cp.index
        pos_idx = dates.searchsorted(notice, side="left")
        target = pos_idx + entry_lag
        if target >= len(dates):
            continue
        entry_date = dates[target]
        pos = _simulate_one(entry_date, cp, max_hold, stop_loss, take_profit)
        if pos is None:
            continue
        pos["institution_id"] = ev.get("institution_id")
        pos["stock_code"] = code
        pos["notice_date"] = notice
        positions.append(pos)

    positions_df = pd.DataFrame(positions)
    if positions_df.empty:
        return {"n_events": len(events), "n_filled": 0}

    pnls = positions_df["pnl"].astype(float)
    hold_days = positions_df["hold_days"].astype(float)
    avg_hold = float(hold_days.mean()) or 1.0
    avg_pnl = float(pnls.mean())

    annual_return = (1.0 + avg_pnl) ** (252.0 / max(avg_hold, 1.0)) - 1.0 if avg_pnl > -1 else -1.0

    pnl_std = float(pnls.std(ddof=1)) if len(pnls) > 1 else 0.0
    sharpe = (avg_pnl / pnl_std) * np.sqrt(252.0 / max(avg_hold, 1.0)) if pnl_std > 0 else 0.0

    # 两个维度的回撤：
    #   avg_position_maxdd：单笔持仓期间平均最大回撤（负值均值）
    #   p95_position_maxdd：尾部 5% 的最坏持仓回撤
    intra_dd = positions_df["intra_maxdd"].astype(float)
    avg_position_maxdd = float(intra_dd.mean())
    p95_position_maxdd = float(intra_dd.quantile(0.05))  # 5% 分位（最差）

    win_rate = float((pnls > 0).mean())
    exit_reason_counts = positions_df["exit_reason"].value_counts().to_dict()

    return {
        "n_events": int(len(events)),
        "n_filled": int(len(positions_df)),
        "avg_pnl": avg_pnl,
        "avg_hold_days": avg_hold,
        "win_rate": win_rate,
        "annual_return": float(annual_return),
        "sharpe": float(sharpe),
        "avg_position_maxdd": avg_position_maxdd,
        "p95_position_maxdd": p95_position_maxdd,
        "exit_reason_counts": exit_reason_counts,
        "positions": positions_df,
    }

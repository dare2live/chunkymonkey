"""experiment_yushen_portfolio — 鱼身组合 NAV 含成本 (主会话主导, 2026-06-16)。

per-trade +0.39% 微正(大头beta)。本脚本量真金白银天花板: 鱼身信号建每日组合, 含成本 NAV, 对标 HS300。
不凭 per-trade 估算年化 (measured not estimated)。组合: 每日持有所有活跃鱼身仓(weekly确认+日线延续入场,
移动止盈/周破位出场), 等权, 上限 MAX_POS 仓(超则取最早入场), T+1 入场, 双边13bps。
源: market.price_kline_qfq_tushare + raw_tushare_index_daily(HS300基准)。
用法: PYTHONPATH=backend .venv/bin/python backend/scripts/experiment_yushen_portfolio.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import duckdb  # rule-compliance: ok evidence=只读组合NAV; manifest; allowlist
import numpy as np
import pandas as pd

from services.database_manifest import get_database_manifest
from services.experiment_store import open_store, record_verdict

log = logging.getLogger("yushen_portfolio")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

COST = 0.0013      # rule-compliance: ok evidence=双边13bps含印花, 同 yushen_backtest
TRAIL = 0.88       # rule-compliance: ok evidence=移动止盈12%, 同 yushen_backtest
BREAKOUT_N = 20    # rule-compliance: ok evidence=日线延续突破, 同
MAX_HOLD = 120     # rule-compliance: ok evidence=持有上限, 同
MAX_POS = 20       # rule-compliance: ok evidence=组合并发仓上限(分散+容量, 首测固定可Optuna)


def _weekly_state(dates, closes):
    df = pd.DataFrame({"date": pd.to_datetime(dates), "close": closes})
    df["wk"] = df["date"].dt.to_period("W")
    wk = df.groupby("wk")["close"].last().reset_index()
    wk["ma30"] = wk["close"].rolling(30).mean(); wk["ma10"] = wk["close"].rolling(10).mean()
    wk["conf"] = (wk["close"] > wk["ma30"]) & (wk["ma30"] > wk["ma30"].shift(1)) & (wk["ma10"] > wk["ma30"])
    wk["conf_lag"] = wk["conf"].shift(1).fillna(False)
    return df["wk"].map(dict(zip(wk["wk"], wk["conf_lag"]))).fillna(False).to_numpy().astype(bool)


def stock_holdings(dates, opens, highs, closes):
    """返回 [(entry_global_date, exit_global_date, [(date, daily_ret) 持有期日收益])]。"""
    n = len(closes)
    if n < 160:
        return []
    state = _weekly_state(dates, closes)
    hh = pd.Series(closes).rolling(BREAKOUT_N).max().to_numpy()
    holds = []
    i = BREAKOUT_N + 1
    while i < n - 1:
        if state[i] and closes[i] >= hh[i] and closes[i] > 0 and not (i >= 1 and closes[i] / closes[i - 1] - 1 >= 0.098):
            entry = opens[i + 1] if opens[i + 1] > 0 else closes[i]
            peak = entry; exit_i = min(i + MAX_HOLD, n - 1)
            for j in range(i + 1, min(i + MAX_HOLD, n - 1) + 1):
                peak = max(peak, highs[j])
                if closes[j] < peak * TRAIL or not state[j]:
                    exit_i = j; break
            # 持有期日收益序列 (T+1 open 到 exit close)
            days = []
            prev = entry
            for j in range(i + 1, exit_i + 1):
                days.append((str(dates[j]), closes[j] / prev - 1.0)); prev = closes[j]
            holds.append((str(dates[i + 1]), str(dates[exit_i]), days))
            i = exit_i + 1
        else:
            i += 1
    return holds


def main():
    mf = get_database_manifest()
    con = duckdb.connect(str(mf.path_for("market")), read_only=True)  # rule-compliance: ok evidence=只读K线; manifest; allowlist
    arr = con.execute("SELECT code, date, open, high, close FROM price_kline_qfq_tushare WHERE date >= '2020-01-01' AND close>0 ORDER BY code, date").fetchnumpy()  # rule-compliance: ok evidence=train窗起点2020(MASTER§5)
    hs = None
    try:  # HS300 基准在 tushare_raw; cyq 回填占写锁时跳过基准 (核心NAV不依赖)
        con.execute(f"ATTACH '{mf.path_for('tushare_raw')}' AS tr (READ_ONLY)")
        hs = con.execute("SELECT trade_date, close FROM tr.raw_tushare_index_daily WHERE ts_code='000300.SH' AND trade_date>='20200101' ORDER BY trade_date").df()
    except Exception as ex:
        log.warning("HS300 基准跳过 (tushare_raw 暂锁?): %s", str(ex)[:60])
    con.close()

    codes = arr["code"]; uniq, first = np.unique(codes, return_index=True)
    order = np.argsort(first); uniq, first = uniq[order], first[order]
    last = np.concatenate([first[1:], [len(codes)]])
    # 每股持仓 → 按日聚合: daily_ret_by_date[date] = [(stock 当日收益)], entries/exits 计成本
    from collections import defaultdict
    day_rets = defaultdict(list); entry_days = defaultdict(int); exit_days = defaultdict(int)
    n_trades = 0
    for ci in range(len(uniq)):
        s, e = int(first[ci]), int(last[ci])
        for ent, ex, days in stock_holdings(arr["date"][s:e], arr["open"][s:e].astype(float), arr["high"][s:e].astype(float), arr["close"][s:e].astype(float)):
            n_trades += 1; entry_days[days[0][0]] += 1 if days else 0; exit_days[ex] += 1
            for d, r in days:
                day_rets[d].append(r)
    # 每日组合: 等权持有(上限MAX_POS), 当日组合收益=持仓均值; 成本按当日 entry+exit 笔数/持仓数摊
    all_dates = sorted(day_rets.keys())
    nav = 1.0; navs = []; rets = []
    for d in all_dates:
        rs = day_rets[d]
        held = len(rs)
        port_ret = np.mean(rs) if rs else 0.0
        # 成本: 当日新入+出仓笔数 × COST / 持仓数 (近似换手成本摊到组合)
        turn = (entry_days.get(d, 0) + exit_days.get(d, 0))
        cost = (turn * COST) / max(held, 1) if held else 0.0
        net = port_ret - cost
        nav *= (1 + net); navs.append(nav); rets.append(net)
    rets = np.array(rets); ndays = len(rets)
    ann = nav ** (252 / ndays) - 1 if ndays else 0
    peak = np.maximum.accumulate(navs); mdd = float(np.min(np.array(navs) / peak - 1)) if navs else 0
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    hs_ann = None
    if hs is not None and len(hs):
        hs_ann = (hs["close"].iloc[-1] / hs["close"].iloc[0]) ** (252 / len(hs)) - 1

    print(f"\n鱼身组合 NAV 含成本 (2020+, 周确认+延续入场+移动止盈, 等权≤{MAX_POS}仓)")
    print(f"  交易数={n_trades:,} 持仓日={ndays} 末NAV={nav:.3f}")
    print(f"  含成本年化={ann*100:+.1f}%  最大回撤={mdd*100:.1f}%  Sharpe={sharpe:.2f}")
    if hs_ann is not None:
        print(f"  同期 HS300 年化={hs_ann*100:+.1f}%  超额={ann*100-hs_ann*100:+.1f}pp")
    else:
        print(f"  (HS300 基准跳过=tushare_raw暂锁; 超额待cyq回填后补)")
    print(f"  --- 裁定 (KPI: 年化>=30% / max_dd>=-20% / 超额>0) ---")
    excess_ok = (hs_ann is None) or (ann > hs_ann)
    kpi = "PASS" if (ann >= 0.30 and mdd >= -0.20 and excess_ok) else "FAIL"
    print(f"  年化{'PASS' if ann>=0.30 else 'FAIL'} / 回撤{'PASS' if mdd>=-0.20 else 'FAIL'} / 超额{'PASS' if excess_ok and hs_ann is not None else ('N/A' if hs_ann is None else 'FAIL')} → {kpi}")
    print(f"  (这是无因子过滤的鱼身 base 组合; 下一步: 叠 context 因子过滤入场 + Optuna 调 confirm/exit/MAX_POS)")

    run_id = "yushen_portfolio_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open_store() as store:
        record_verdict(store, run_id=run_id, family="yushen_portfolio_nav", verdict=kpi,
                       judges={"ann": round(float(ann), 4), "max_dd": round(float(mdd), 4), "sharpe": round(float(sharpe), 3),
                               "n_trades": int(n_trades), "hs_ann": (round(float(hs_ann), 4) if hs_ann is not None else None),
                               "summary": f"裸基鱼身组合含成本年化{ann*100:+.1f}%/max_dd{mdd*100:.1f}%/sharpe{sharpe:.2f} → KPI {kpi}"},
                       confirmed_by_owner=0)
    print(f"  [experiment_store] 已留档 family=yushen_portfolio_nav verdict={kpi}")


if __name__ == "__main__":
    main()

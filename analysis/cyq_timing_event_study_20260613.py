#!/usr/bin/env python3
"""CYQ 买卖点 TIMING 事件研究 (角度4, 预注册式).

预注册规则 (跑前定死, 防过拟合):
  SELL 规则: 持仓股 winner_rate_lag(t-1) 的 250 交易日分位 > 0.9 (获利盘极重)
             -> t+1 退出. 度量 = 触发后 fwd20 收益 (越低=规则越有效=该卖).
  BUY  规则: winner_rate_lag(t-1) 250 日分位 < 0.1 (套牢盘释放) AND
             原始未复权 close(t) > weight_avg_lag(t-1) (价站上均成本) -> t+1 进场.
             度量 = 触发后 fwd20 收益 (越高=规则越有效=该买).

铁律:
  - PIT: CYQ 特征用 t-1 值 (盘后 18:00 更新), JOIN t-1.
  - C0: winner_rate/weight_avg = 未复权坐标. 价位比较用原始未复权 daily.close.
        剔除除权窗 (ex_date ±3 交易日).
  - label: forward20 return 用 market qfq close[t+20]/close[t]-1.
  - 基线: 全市场同期 (同 t+1 进场日) 所有股票 fwd20 均值 = 市场基线.
          另对照 "持有股池" (有 CYQ 数据股) 同期均值.

进场口径: 决策在 t (盘后看 t-1 CYQ + t 收盘价), t+1 开始持有, fwd 用 qfq close.
  这里 fwd20 = qfq_close(t+20) / qfq_close(t) - 1 (以 t 收盘为基准, 等价 t+1 入场近似).
"""
import duckdb
import numpy as np
import pandas as pd
import json
from datetime import datetime

TUSHARE = "data/tushare_raw.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
MARKET = "data/market.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
FWD = 20
PCT_WINDOW = 250          # 250 交易日 rolling 分位
SELL_PCTILE = 0.90        # 预注册
BUY_PCTILE = 0.10         # 预注册
EXDIV_PAD = 3             # 除权窗 ±3 交易日
OUT = "analysis/cyq_timing_event_study_20260613.json"


def load_cyq():
    t = duckdb.connect(TUSHARE, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    df = t.execute("""
        SELECT ts_code, trade_date, winner_rate, weight_avg, close AS raw_close
        FROM raw_tushare_cyq_perf c
        JOIN raw_tushare_daily d USING (ts_code, trade_date)
        WHERE winner_rate IS NOT NULL AND weight_avg IS NOT NULL
        ORDER BY ts_code, trade_date
    """).df()
    # ex-div dates (实施 only)
    div = t.execute("""
        SELECT ts_code, ex_date
        FROM raw_tushare_dividend
        WHERE TRIM(div_proc) = '实施' AND ex_date IS NOT NULL AND ex_date <> ''
    """).df()
    t.close()
    df["code"] = df["ts_code"].str[:6]
    return df, div


def load_qfq():
    m = duckdb.connect(MARKET, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    q = m.execute("""
        SELECT code, REPLACE(date,'-','') AS trade_date, close AS qfq_close
        FROM v_price_kline_qfq
        WHERE freq='daily' AND adjust='qfq' AND close IS NOT NULL
        ORDER BY code, trade_date
    """).df()
    m.close()
    return q


def main():
    cyq, div = load_cyq()
    qfq = load_qfq()

    # forward20 return per code from qfq (PIT-clean label)
    qfq = qfq.sort_values(["code", "trade_date"]).reset_index(drop=True)
    qfq["fwd20"] = qfq.groupby("code")["qfq_close"].shift(-FWD) / qfq["qfq_close"] - 1.0

    # market baseline: per trade_date mean fwd20 across ALL qfq stocks
    mkt_base = qfq.dropna(subset=["fwd20"]).groupby("trade_date")["fwd20"].mean().rename("mkt_fwd20")

    # build per-stock CYQ features with t-1 lag + rolling pctile
    cyq = cyq.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = cyq.groupby("ts_code", group_keys=False)
    # t-1 lag (PIT): decision at t uses CYQ from t-1
    cyq["wr_lag"] = g["winner_rate"].shift(1)
    cyq["wavg_lag"] = g["weight_avg"].shift(1)
    # rolling 250d pctile of wr_lag (rank of last value within trailing window, exclusive PIT-safe)
    def roll_pctile(s):
        return s.rolling(PCT_WINDOW, min_periods=60).apply(
            lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else np.nan, raw=True)
    cyq["wr_pctile"] = g["wr_lag"].apply(roll_pctile)

    # ex-div exclusion windows: mark trade_dates within +/- EXDIV_PAD trading days of any ex_date
    # build trading calendar per code from cyq trade_dates (ordered)
    div = div[div["ts_code"].isin(cyq["ts_code"].unique())].copy()
    exclude = set()  # (ts_code, trade_date)
    cyq_dates = cyq.groupby("ts_code")["trade_date"].apply(lambda s: s.tolist()).to_dict()
    div_by_code = div.groupby("ts_code")["ex_date"].apply(list).to_dict()
    for code, exdates in div_by_code.items():
        dates = cyq_dates.get(code)
        if not dates:
            continue
        idx = {d: i for i, d in enumerate(dates)}
        for ex in exdates:
            # find first trading date >= ex
            pos = None
            if ex in idx:
                pos = idx[ex]
            else:
                # nearest >= ex
                for j, d in enumerate(dates):
                    if d >= ex:
                        pos = j
                        break
            if pos is None:
                continue
            for k in range(pos - EXDIV_PAD, pos + EXDIV_PAD + 1):
                if 0 <= k < len(dates):
                    exclude.add((code, dates[k]))
    cyq["exdiv"] = [
        (r.ts_code, r.trade_date) in exclude
        for r in cyq.itertuples()
    ]

    # merge forward return (qfq) by code+trade_date
    panel = cyq.merge(qfq[["code", "trade_date", "fwd20"]], on=["code", "trade_date"], how="inner")
    panel = panel.merge(mkt_base, on="trade_date", how="left")
    panel["year"] = panel["trade_date"].str[:4]

    # apply ex-div exclusion + need valid features + valid fwd20
    valid = panel[(~panel["exdiv"]) & panel["wr_pctile"].notna() & panel["fwd20"].notna() & panel["mkt_fwd20"].notna()].copy()

    results = {}

    # ----- SELL rule: wr_pctile > 0.90 -----
    sell = valid[valid["wr_pctile"] > SELL_PCTILE].copy()
    # control (held-pool baseline) = all valid same-day mean already captured per row; use net vs market
    def summarize(ev, name):
        n = len(ev)
        rule_mean = ev["fwd20"].mean()
        base_mean = ev["mkt_fwd20"].mean()  # market baseline averaged over the SAME trigger days
        net = rule_mean - base_mean
        # paired t-stat on (fwd20 - mkt_fwd20)
        diff = ev["fwd20"] - ev["mkt_fwd20"]
        tstat = diff.mean() / (diff.std(ddof=1) / np.sqrt(n)) if n > 1 and diff.std(ddof=1) > 0 else np.nan
        by_year = {}
        for y, gy in ev.groupby("year"):
            d2 = gy["fwd20"] - gy["mkt_fwd20"]
            by_year[y] = {
                "n": int(len(gy)),
                "rule_fwd20": round(float(gy["fwd20"].mean()), 5),
                "mkt_fwd20": round(float(gy["mkt_fwd20"].mean()), 5),
                "net": round(float(d2.mean()), 5),
            }
        return {
            "rule": name,
            "n_events": int(n),
            "n_stocks": int(ev["ts_code"].nunique()),
            "rule_fwd20_mean": round(float(rule_mean), 5),
            "mkt_baseline_fwd20_mean": round(float(base_mean), 5),
            "net_effect": round(float(net), 5),
            "paired_tstat": round(float(tstat), 3) if not np.isnan(tstat) else None,
            "rule_fwd20_median": round(float(ev["fwd20"].median()), 5),
            "by_year": by_year,
        }

    results["SELL_winner_rate_pctile_gt_0.90"] = summarize(sell, "SELL: wr_pctile>0.90 (获利盘极重, 应退出)")

    # ----- BUY rule: wr_pctile < 0.10 AND raw_close > wavg_lag -----
    buy = valid[(valid["wr_pctile"] < BUY_PCTILE) & (valid["raw_close"] > valid["wavg_lag"])].copy()
    results["BUY_pctile_lt_0.10_AND_price_above_wavg"] = summarize(buy, "BUY: wr_pctile<0.10 AND close>weight_avg (套牢释放+站上均成本)")

    # also report a sub-decomposition for BUY (pctile<0.1 alone, no price filter)
    buy_only = valid[valid["wr_pctile"] < BUY_PCTILE].copy()
    results["BUY_pctile_lt_0.10_ONLY_no_price_filter"] = summarize(buy_only, "BUY-ablation: wr_pctile<0.10 only")

    # global panel sizes
    meta = {
        "run_at_utc": datetime.utcnow().isoformat() + "+00:00",
        "fwd_days": FWD,
        "pct_window": PCT_WINDOW,
        "exdiv_pad": EXDIV_PAD,
        "panel_rows_total": int(len(panel)),
        "panel_rows_valid_after_exdiv_and_features": int(len(valid)),
        "exdiv_excluded_rows": int(panel["exdiv"].sum()),
        "n_stocks_valid": int(valid["ts_code"].nunique()),
        "date_range": [valid["trade_date"].min(), valid["trade_date"].max()],
        "label_source": "market qfq close[t+20]/close[t]-1 (PIT-clean)",
        "pit_note": "CYQ wr/wavg use t-1 lag; rolling pctile excludes current bar (w[:-1]<w[-1]); price-vs-cost uses raw unadjusted daily.close",
    }

    out = {"meta": meta, "rules": results}
    with open(OUT, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

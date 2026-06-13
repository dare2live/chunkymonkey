#!/usr/bin/env python3
"""
角度1: 筹码胜率 winner_rate 的买卖点 TIMING 方向 IC 诊断.

PIT 铁律: cyq_perf 盘后 18:00 更新 -> 决策日 t 的 CYQ 特征用 t-1 值.
  => winner_rate_lag = 前一交易日 (t-1) 的 winner_rate, 在决策日 t 截面排名.
Label: fwd20 = qfq close[t+20]/close[t]-1 (市场 qfq, 始终用复权).
C0 鲁棒: 剔除除权窗 (ex_date +-3 交易日) 与不剔除两版对照.

核心检验 (买卖点 TIMING, 非入场 filter):
  (a) 高 winner_rate (获利盘重=抛压) -> 预测负 fwd = 卖点?
  (b) 低 winner_rate (套牢盘多/抛压释放) -> 预测正 fwd = 买点?
分位分桶: winner_rate 5 桶各自 fwd 均值, 看单调性/倒U.

输出: walk-forward 月度截面 RankIC, IC/IR, 分桶, 两版 (剔/不剔除权窗) 对照.
"""
import json
import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

TUSHARE_DB = "data/tushare_raw.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
MARKET_DB = "data/market.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
FWD_H = 20
EXDIV_WINDOW = 3  # +-3 交易日

def yyyymmdd_to_dash(s):
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"

def load_data():
    r = duckdb.connect(TUSHARE_DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    # winner_rate 全量, 加 ann/ex 用于除权窗
    cyq = r.execute("""
        SELECT ts_code, trade_date, winner_rate
        FROM raw_tushare_cyq_perf
        WHERE winner_rate IS NOT NULL
        ORDER BY ts_code, trade_date
    """).fetchdf()
    div = r.execute("""
        SELECT ts_code, ex_date
        FROM raw_tushare_dividend
        WHERE ex_date IS NOT NULL AND ex_date <> ''
          AND ex_date >= '20230101' AND ex_date <= '20260612'
    """).fetchdf()
    r.close()

    m = duckdb.connect(MARKET_DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
    qfq = m.execute("""
        SELECT code, date, close
        FROM v_price_kline_qfq
        WHERE freq='daily' AND adjust='qfq'
        ORDER BY code, date
    """).fetchdf()
    m.close()

    cyq['code6'] = cyq['ts_code'].str[:6]
    div['code6'] = div['ts_code'].str[:6]
    # normalise dates to YYYYMMDD for join key
    qfq['ymd'] = qfq['date'].str.replace('-', '', regex=False)
    return cyq, div, qfq

def build_panel(cyq, qfq):
    """构 t-1 PIT 特征 + fwd20 label, 截面索引 = 决策日 t."""
    # 每个 code 的 qfq 序列, 计算 fwd20 = close[+20]/close[0]-1
    qfq = qfq.sort_values(['code', 'ymd']).copy()
    qfq['fwd20'] = qfq.groupby('code')['close'].shift(-FWD_H) / qfq['close'] - 1.0
    qfq_label = qfq[['code', 'ymd', 'fwd20']].rename(columns={'code': 'code6'})

    # winner_rate lag: 决策日 t 用 t-1 值 -> 把 cyq 的 winner_rate shift 到下一交易日
    # 用 cyq 自己的交易日序列做 shift (cyq 每股逐交易日)
    cyq = cyq.sort_values(['code6', 'trade_date']).copy()
    cyq['winner_rate_lag'] = cyq.groupby('code6')['winner_rate'].shift(1)
    # 决策日 t = cyq.trade_date; winner_rate_lag = t-1 的胜率
    feat = cyq[['code6', 'trade_date', 'winner_rate_lag']].rename(columns={'trade_date': 'ymd'})
    feat = feat.dropna(subset=['winner_rate_lag'])

    panel = feat.merge(qfq_label, on=['code6', 'ymd'], how='inner')
    panel = panel.dropna(subset=['fwd20'])
    return panel, cyq

def build_exdiv_mask(cyq, div):
    """对每股, 标记 ex_date +-EXDIV_WINDOW 交易日内的决策日 (基于 cyq 交易日序列)."""
    flagged = []  # list of (code6, ymd)
    # cyq 交易日序列 per code
    for code6, g in cyq.groupby('code6'):
        dates = g['trade_date'].values  # sorted YYYYMMDD strings
        ex_list = div.loc[div['code6'] == code6, 'ex_date'].values
        if len(ex_list) == 0:
            continue
        date_idx = {d: i for i, d in enumerate(dates)}
        n = len(dates)
        flag_idx = set()
        for ex in ex_list:
            # 找 ex_date 在交易日序列中的位置 (>= ex 的第一个交易日 = 除权后首日)
            pos = np.searchsorted(dates, ex)
            # 取 ex 前后窗: 用 ex 落点 pos 作为中心, +-window
            for j in range(pos - EXDIV_WINDOW, pos + EXDIV_WINDOW + 1):
                if 0 <= j < n:
                    flag_idx.add(j)
        for j in flag_idx:
            flagged.append((code6, dates[j]))
    return set(flagged)

def walk_forward_ic(panel, label_tag):
    """月度截面 RankIC: 每个决策月内逐交易日截面 spearman, 汇总."""
    panel = panel.copy()
    panel['month'] = panel['ymd'].str[:6]
    # 逐交易日截面 RankIC
    daily_ic = []
    for ymd, g in panel.groupby('ymd'):
        if g['code6'].nunique() < 30:  # 截面至少 30 只
            continue
        if g['winner_rate_lag'].nunique() < 5 or g['fwd20'].nunique() < 5:
            continue
        ic, _ = spearmanr(g['winner_rate_lag'], g['fwd20'])
        if np.isfinite(ic):
            daily_ic.append({'ymd': ymd, 'ic': ic, 'n': len(g)})
    dic = pd.DataFrame(daily_ic)
    if dic.empty:
        return None
    mean_ic = dic['ic'].mean()
    std_ic = dic['ic'].std()
    ir = mean_ic / std_ic if std_ic > 0 else np.nan
    # 月度均值 (walk-forward 截面汇总)
    dic['month'] = dic['ymd'].str[:6]
    monthly = dic.groupby('month')['ic'].mean()
    return {
        'tag': label_tag,
        'rank_ic_mean': round(float(mean_ic), 5),
        'rank_ic_std': round(float(std_ic), 5),
        'ic_ir': round(float(ir), 4),
        'n_days': int(len(dic)),
        'n_obs': int(len(panel)),
        'monthly_ic_mean': round(float(monthly.mean()), 5),
        'monthly_ic_ir': round(float(monthly.mean() / monthly.std()), 4) if monthly.std() > 0 else None,
        'pct_days_negative': round(float((dic['ic'] < 0).mean()), 4),
    }

def bucket_analysis(panel, label_tag):
    """每个决策日内按 winner_rate_lag 分 5 桶, 各桶 fwd20 均值, 看单调性."""
    panel = panel.copy()
    def qbucket(g):
        try:
            g = g.copy()
            g['bucket'] = pd.qcut(g['winner_rate_lag'], 5, labels=False, duplicates='drop')
            return g
        except Exception:
            return None
    parts = []
    for ymd, g in panel.groupby('ymd'):
        if g['code6'].nunique() < 30:
            continue
        gb = qbucket(g)
        if gb is not None and gb['bucket'].nunique() == 5:
            parts.append(gb)
    if not parts:
        return None
    allb = pd.concat(parts)
    res = allb.groupby('bucket').agg(
        fwd20_mean=('fwd20', 'mean'),
        winner_rate_mean=('winner_rate_lag', 'mean'),
        n=('fwd20', 'size'),
    ).reset_index()
    res['fwd20_mean'] = res['fwd20_mean'].round(5)
    res['winner_rate_mean'] = res['winner_rate_mean'].round(2)
    # 单调性: bucket0 (低胜率) vs bucket4 (高胜率)
    spread = float(res.loc[res['bucket'] == 0, 'fwd20_mean'].iloc[0] - res.loc[res['bucket'] == 4, 'fwd20_mean'].iloc[0])
    return {
        'tag': label_tag,
        'buckets': res.to_dict('records'),
        'low_minus_high_fwd20': round(spread, 5),
    }

def main():
    print("loading...")
    cyq, div, qfq = load_data()
    print(f"cyq rows={len(cyq)}, div rows={len(div)}, qfq rows={len(qfq)}")

    panel, cyq_sorted = build_panel(cyq, qfq)
    print(f"panel (no exclusion) obs={len(panel)}, days={panel['ymd'].nunique()}, codes={panel['code6'].nunique()}")

    print("building exdiv mask...")
    exdiv = build_exdiv_mask(cyq_sorted, div)
    print(f"exdiv flagged (code,day) pairs={len(exdiv)}")

    panel['key'] = list(zip(panel['code6'], panel['ymd']))
    panel_clean = panel[~panel['key'].isin(exdiv)].copy()
    print(f"panel (exdiv-excluded) obs={len(panel_clean)} (removed {len(panel)-len(panel_clean)})")

    out = {
        'experiment': 'C0_angle1_winner_rate_timing_ic',
        'fwd_horizon': FWD_H,
        'exdiv_window_days': EXDIV_WINDOW,
        'pit': 'winner_rate_lag = t-1 winner_rate; label fwd20 = qfq close[t+20]/close[t]-1',
        'ic': {},
        'buckets': {},
    }
    out['ic']['no_exclusion'] = walk_forward_ic(panel, 'no_exclusion')
    out['ic']['exdiv_excluded'] = walk_forward_ic(panel_clean, 'exdiv_excluded')
    out['buckets']['no_exclusion'] = bucket_analysis(panel, 'no_exclusion')
    out['buckets']['exdiv_excluded'] = bucket_analysis(panel_clean, 'exdiv_excluded')

    with open("analysis/c0_angle1_winner_rate_timing_ic_result.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(json.dumps(out, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

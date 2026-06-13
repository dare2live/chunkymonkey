"""moneyflow 域 t-1 PIT 增量 alpha 扫描 (2023-2026).
PIT: 盘后域 -> 特征用 t-1 值 JOIN signal date t.
fwd20 = qfq close[t+20]/close[t]-1.
RankIC = 每日 corr(rank(feat_{t-1}), rank(fwd20_t)) 跨日平均; IC_IR=mean/std. MIN 30 配对/日.
"""
import duckdb
import numpy as np
import pandas as pd

START, END = "20230101", "20260612"
FWD = 20
MIN_PAIRS = 30

con = duckdb.connect(":memory:")  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
con.execute("ATTACH 'data/tushare_raw.duckdb' AS tr (READ_ONLY)")  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
con.execute("ATTACH 'data/market.duckdb' AS mk (READ_ONLY)")  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)

# 1) 价格表: 规范化 code/date, 仅 qfq, 算 fwd20 forward return (按 code 内时间序排序 t+20 行)
con.execute(
    f"""
    CREATE TEMP TABLE px AS
    SELECT code,
           strftime(strptime(date,'%Y-%m-%d'),'%Y%m%d') AS td,
           close,
           LEAD(close, {FWD}) OVER (PARTITION BY code ORDER BY date) AS close_fwd
    FROM mk.v_price_kline_qfq
    WHERE adjust='qfq'
    """
)
con.execute(
    """
    CREATE TEMP TABLE fwd AS
    SELECT code, td, (close_fwd/close - 1.0) AS fwd20
    FROM px
    WHERE close_fwd IS NOT NULL AND close > 0
    """
)

# 2) moneyflow 特征 (t 收盘截面原值, 单位: 万元 net_mf_amount; lg/elg 大单+超大单)
#    net_mf_amount = 主力净流入(全口径) ; main_net = (buy_lg+buy_elg) - (sell_lg+sell_elg) 大+超大单净额
#    main_ratio = main_net / (全部买卖额) 主力净占比 (规模归一, 防大盘股偏)
con.execute(
    """
    CREATE TEMP TABLE mf AS
    SELECT substr(ts_code,1,6) AS code,
           trade_date AS td,
           net_mf_amount AS net_mf,
           ((buy_lg_amount+buy_elg_amount)-(sell_lg_amount+sell_elg_amount)) AS main_net,
           ((buy_lg_amount+buy_elg_amount)-(sell_lg_amount+sell_elg_amount))
             / NULLIF((buy_sm_amount+buy_md_amount+buy_lg_amount+buy_elg_amount
                      +sell_sm_amount+sell_md_amount+sell_lg_amount+sell_elg_amount),0) AS main_ratio
    FROM tr.raw_tushare_moneyflow
    """
)

# 3) t-1 PIT: 每个 code 的特征值取前一交易日 (LAG over moneyflow's own trade_date order),
#    再以 signal date td 对齐 fwd (signal date 的 forward return).
con.execute(
    """
    CREATE TEMP TABLE mf_lag AS
    SELECT code, td AS signal_td,
           LAG(net_mf)    OVER (PARTITION BY code ORDER BY td) AS net_mf_lag,
           LAG(main_net)  OVER (PARTITION BY code ORDER BY td) AS main_net_lag,
           LAG(main_ratio)OVER (PARTITION BY code ORDER BY td) AS main_ratio_lag
    FROM mf
    """
)

con.execute(
    f"""
    CREATE TEMP TABLE panel AS
    SELECT m.signal_td AS td, m.code,
           m.net_mf_lag, m.main_net_lag, m.main_ratio_lag,
           f.fwd20
    FROM mf_lag m
    JOIN fwd f ON f.code=m.code AND f.td=m.signal_td
    WHERE m.signal_td >= '{START}' AND m.signal_td <= '{END}'
      AND m.net_mf_lag IS NOT NULL
    """
)

df = con.execute("SELECT * FROM panel").df()
print("panel rows:", len(df), "days:", df.td.nunique(), "codes:", df.code.nunique())


def rank_ic(g, fcol):
    sub = g[[fcol, "fwd20"]].dropna()
    if len(sub) < MIN_PAIRS:
        return np.nan
    if sub[fcol].nunique() < 2 or sub["fwd20"].nunique() < 2:
        return np.nan
    return sub[fcol].rank().corr(sub["fwd20"].rank())


def plain_ic(g, fcol):
    sub = g[[fcol, "fwd20"]].dropna()
    if len(sub) < MIN_PAIRS:
        return np.nan
    if sub[fcol].std() == 0 or sub["fwd20"].std() == 0:
        return np.nan
    return sub[fcol].corr(sub["fwd20"])


feats = {
    "net_mf_lag (t-1 主力净流入全口径, 万元)": "net_mf_lag",
    "main_net_lag (t-1 大+超大单净额, 万元)": "main_net_lag",
    "main_ratio_lag (t-1 大+超大单净占总成交额比)": "main_ratio_lag",
}

results = {}
for name, col in feats.items():
    rics = df.groupby("td").apply(lambda g: rank_ic(g, col)).dropna()
    pics = df.groupby("td").apply(lambda g: plain_ic(g, col)).dropna()
    ric_mean = rics.mean()
    ric_std = rics.std()
    ic_ir = ric_mean / ric_std if ric_std and ric_std > 0 else np.nan
    cov_days = len(rics)
    avg_pairs = df.groupby("td")[col].apply(lambda s: s.notna().sum()).mean()
    results[name] = dict(
        rank_ic=round(float(ric_mean), 4),
        plain_ic=round(float(pics.mean()), 4),
        ic_ir=round(float(ic_ir), 4),
        cov_days=int(cov_days),
        avg_pairs=round(float(avg_pairs), 0),
        pct_pos=round(float((rics > 0).mean()), 3),
    )
    print(f"\n{name}\n  {results[name]}")

con.close()
print("\nDONE")

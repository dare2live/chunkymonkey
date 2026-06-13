"""
top_inst (龙虎榜机构席位) 增量 alpha IC 扫描.

目标: 现有面板已含 LHB 机构买入"计数"信号 (IC_IR -0.84). 这里挖 *席位质量 / 净额* 维度
有无超越计数的增量 forward IC.

PIT 契约 (registry pit_anchor = "trade_date; 龙虎榜席位明细盘后公布 -> JOIN t-1"):
  - 龙虎榜 trade_date=d 的明细盘后 (18:00) 才公布 -> d 当日不可用.
  - 决策日 t = d 之后第一个交易日 (first trading day > d). 即特征锚 t-1 (=d) 的盘后数据.
  - forward return 用 market.duckdb v_price_kline_qfq qfq close: fwd20 = close[t+20]/close[t]-1.

口径:
  - 稀疏: 只在上榜日有数据. 非上榜日不构造该域特征 (NaN), 不填 0. 即 IC 只在上榜事件子集计算
    (这是事件型信号的诚实口径: 问"上榜后机构席位质量能否预测 fwd20").
  - RankIC = 每日 corr(rank(feat_{t-1}), rank(fwd20_t)) 跨日平均; IC_IR = mean/std. MIN 30 配对/日.
  - 异常: |RankIC|>0.15 触发核查, 标 leakage_suspect, 不直接排除.

side 语义 (实测): side=0 = 买方席位 (net_buy 多为正), side=1 = 卖方席位 (net_buy 多为负).
机构席位 = exalter LIKE '%机构专用%' (191711 行, 占 ~10%).
"""
import duckdb
import numpy as np
import pandas as pd
import json
from scipy.stats import spearmanr

RAW_DB = "data/tushare_raw.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
MKT_DB = "data/market.duckdb"  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
FWD_N = 20
MIN_PAIRS = 30  # 每日最少配对数

raw = duckdb.connect(RAW_DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)
mkt = duckdb.connect(MKT_DB, read_only=True)  # rule-compliance: ok evidence=ad-hoc read-only analysis (orphaned probe artifact)

# ---------------------------------------------------------------------------
# 1. forward return 表: 用 qfq close 算 fwd20.  code 无后缀, 转 ts_code 形式做 JOIN.
#    交易日序列以 qfq view 的 distinct date 为真相源 (K线有交易=交易日).
# ---------------------------------------------------------------------------
px = mkt.execute("""
    SELECT code, date, close
    FROM v_price_kline_qfq
    WHERE adjust='qfq' AND freq='daily'
    ORDER BY code, date
""").df()
# 后缀映射: 6开头 -> .SH, 0/3 -> .SZ, 8/4 (北交所) -> .BJ
def to_ts(code):
    c = str(code)
    if c.startswith('6'):
        return c + '.SH'
    if c.startswith(('0', '3')):
        return c + '.SZ'
    if c.startswith(('8', '4', '9')):
        return c + '.BJ'
    return c + '.SZ'
px['ts_code'] = px['code'].map(to_ts)
px['date'] = px['date'].str.replace('-', '', regex=False)  # 统一 YYYYMMDD
px = px.sort_values(['ts_code', 'date']).reset_index(drop=True)
# fwd20 = close shift -20 / close - 1 (per ts_code)
px['fwd_close'] = px.groupby('ts_code')['close'].shift(-FWD_N)
px['fwd20'] = px['fwd_close'] / px['close'] - 1.0
# 交易日全集 (排序)
trade_days = np.sort(px['date'].unique())
day_idx = {d: i for i, d in enumerate(trade_days)}

# next trading day map: 对任意日期 d, 返回 first trading day > d
def next_trade_day(d):
    # binary search
    i = np.searchsorted(trade_days, d, side='right')
    if i < len(trade_days):
        return trade_days[i]
    return None

# fwd20 lookup: (ts_code, t) -> fwd20
fwd_lookup = px.set_index(['ts_code', 'date'])['fwd20'].to_dict()

print(f"[px] rows={len(px)} ts_codes={px['ts_code'].nunique()} trade_days={len(trade_days)} "
      f"range={trade_days[0]}..{trade_days[-1]}")

# ---------------------------------------------------------------------------
# 2. 构造 top_inst 事件级特征 (按 trade_date d, ts_code 聚合).
#    只取 d >= 2021-12 之后 (保证 t 落在 qfq 窗内, 留 fwd20 缓冲).
# ---------------------------------------------------------------------------
feat = raw.execute("""
    WITH base AS (
        SELECT trade_date, ts_code, exalter, side, buy, sell, net_buy,
               (exalter LIKE '%机构专用%') AS is_inst
        FROM raw_tushare_top_inst
        WHERE trade_date >= '20211201'
    )
    SELECT
        trade_date, ts_code,
        -- 全席位
        SUM(net_buy)                                          AS net_buy_all,
        SUM(CASE WHEN side='0' THEN 1 ELSE 0 END)             AS n_buy_seats,
        SUM(CASE WHEN side='1' THEN 1 ELSE 0 END)             AS n_sell_seats,
        -- 机构席位 (质量维度)
        SUM(CASE WHEN is_inst THEN net_buy ELSE 0 END)        AS inst_net_buy,
        SUM(CASE WHEN is_inst THEN buy ELSE 0 END)            AS inst_buy,
        SUM(CASE WHEN is_inst THEN sell ELSE 0 END)           AS inst_sell,
        SUM(CASE WHEN is_inst AND side='0' THEN 1 ELSE 0 END) AS inst_buy_seats,
        SUM(CASE WHEN is_inst AND side='1' THEN 1 ELSE 0 END) AS inst_sell_seats,
        SUM(CASE WHEN is_inst THEN 1 ELSE 0 END)              AS inst_seats_total,
        COUNT(*)                                              AS seats_total,
        SUM(buy)                                              AS buy_all,
        SUM(sell)                                             AS sell_all
    FROM base
    GROUP BY trade_date, ts_code
""").df()
print(f"[feat] listing events (d>=20211201) = {len(feat)}")

# 派生代表特征
eps = 1.0
feat['inst_net_buy_signed'] = feat['inst_net_buy']                       # 机构净买额 (绝对)
feat['inst_seat_diff']      = feat['inst_buy_seats'] - feat['inst_sell_seats']  # 机构买卖席位数差
# 机构净买占总成交比 (质量归一化, 去除股票绝对体量)
feat['inst_net_ratio']      = feat['inst_net_buy'] / (feat['buy_all'] + feat['sell_all'] + eps)
# 机构买入集中度: 机构买额 / 全席位买额
feat['inst_buy_share']      = feat['inst_buy'] / (feat['buy_all'] + eps)
# 机构净买 / 机构总成交 (方向纯度)
feat['inst_net_purity']     = feat['inst_net_buy'] / (feat['inst_buy'] + feat['inst_sell'] + eps)
# 全席位净买额 (对照: 非机构维度)
feat['net_buy_all_f']       = feat['net_buy_all']
# 席位差 (对照计数信号)
feat['seat_diff_all']       = feat['n_buy_seats'] - feat['n_sell_seats']

# 决策日 t = next trading day after d
feat['t_day'] = feat['trade_date'].map(next_trade_day)
feat = feat[feat['t_day'].notna()].copy()
# 取 fwd20
feat['fwd20'] = feat.apply(lambda r: fwd_lookup.get((r['ts_code'], r['t_day']), np.nan), axis=1)
feat = feat[feat['fwd20'].notna()].copy()
print(f"[feat] with valid fwd20 = {len(feat)}; t_day range {feat['t_day'].min()}..{feat['t_day'].max()}")

# ---------------------------------------------------------------------------
# 3. RankIC: 按 t_day 分组, 每日 spearman(feat, fwd20), 跨日平均.
# ---------------------------------------------------------------------------
FEATURES = [
    'inst_net_buy_signed', 'inst_seat_diff', 'inst_net_ratio', 'inst_buy_share',
    'inst_net_purity', 'net_buy_all_f', 'seat_diff_all',
]

def daily_rank_ic(df, fcol, min_pairs=MIN_PAIRS):
    ics = []
    coverage_days = 0
    total_pairs = 0
    for tday, g in df.groupby('t_day'):
        sub = g[[fcol, 'fwd20']].dropna()
        # 去常数列日 (机构席位常 0 会导致 0 方差日, spearman NaN)
        if len(sub) < min_pairs:
            continue
        if sub[fcol].nunique() < 2 or sub['fwd20'].nunique() < 2:
            continue
        rho, _ = spearmanr(sub[fcol], sub['fwd20'])
        if np.isnan(rho):
            continue
        ics.append(rho)
        coverage_days += 1
        total_pairs += len(sub)
    ics = np.array(ics)
    if len(ics) == 0:
        return None
    return {
        'rank_ic': float(np.mean(ics)),
        'ic_ir': float(np.mean(ics) / np.std(ics)) if np.std(ics) > 0 else 0.0,
        'n_days': int(coverage_days),
        'total_pairs': int(total_pairs),
        'avg_pairs_per_day': float(total_pairs / coverage_days),
        'ic_std': float(np.std(ics)),
        'pct_pos_days': float((ics > 0).mean()),
    }

results = {}
for f in FEATURES:
    r = daily_rank_ic(feat, f)
    results[f] = r
    if r:
        print(f"{f:24s} RankIC={r['rank_ic']:+.4f} IC_IR={r['ic_ir']:+.3f} "
              f"days={r['n_days']} avg_pairs/day={r['avg_pairs_per_day']:.0f} pos_days={r['pct_pos_days']:.2f}")
    else:
        print(f"{f:24s} INSUFFICIENT")

# ---------------------------------------------------------------------------
# 4. 稀疏统计 + 机构席位占比 (口径透明)
# ---------------------------------------------------------------------------
inst_event_share = (feat['inst_seats_total'] > 0).mean()
print(f"\n[sparse] listing events with >=1 inst seat = {inst_event_share:.3f}")
print(f"[sparse] total events used = {len(feat)}, distinct t_days = {feat['t_day'].nunique()}")

out = {
    'features': results,
    'n_events': int(len(feat)),
    'inst_event_share': float(inst_event_share),
    'fwd_n': FWD_N,
    'min_pairs': MIN_PAIRS,
    't_day_range': [str(feat['t_day'].min()), str(feat['t_day'].max())],
}
with open('/tmp/top_inst_ic_result.json', 'w') as fp:
    json.dump(out, fp, indent=2, ensure_ascii=False)
print("\n[done] -> /tmp/top_inst_ic_result.json")

raw.close()
mkt.close()

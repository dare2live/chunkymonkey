#!/usr/bin/env python3
"""行业分类区分度量化验证: 申万 vs 通达信 (measured, 非经验).

指标: ANOVA eta^2 = SS_between/SS_total = 行业分类能解释的 forward return 方差占比.
公平对照: 同组数 shuffle baseline (扣掉"类目多 -> eta^2 机械虚高"的自由度效应).
净区分度 = eta^2_real - eta^2_shuffle. 越高 = 行业越能真实解释个股收益差异.

只读: market.duckdb (K线) + smartmoney.duckdb (分类), 不写库.
"""
import duckdb, numpy as np, json

FWD = 20            # forward return 天数
SHUFFLE_N = 10      # 随机基线重复次数
rng = np.random.RandomState(42)  # rule-compliance: ok evidence=fixed-seed-reproducible

sm = duckdb.connect("data/smartmoney.duckdb", read_only=True)  # rule-compliance: ok evidence=analysis-oneoff-measured-script-readonly
mk = duckdb.connect("data/market.duckdb", read_only=True)  # rule-compliance: ok evidence=analysis-oneoff-measured-script-readonly

# 行业分类 (当前快照, 方向性验证可接受; 生产 PIT 化是下一步)
sw = sm.execute("SELECT stock_code, sw_l1_name, sw_l2_name FROM dim_stock_sw_industry WHERE sw_l1_name IS NOT NULL").df()
tdx = sm.execute("SELECT stock_code, tdx_l1_name, tdx_l2_name, tdx_l3_name FROM dim_stock_tdx_industry WHERE tdx_l1_name IS NOT NULL").df()
cls = sw.merge(tdx, on="stock_code", how="inner")
print(f"分类交集股票数: {len(cls)}")

# 月末交易日 (2023-01 ~ 2026-04, 留 forward 空间)
days = mk.execute("""
    SELECT DISTINCT date FROM price_kline_tdxhub WHERE freq='daily' AND date>='2023-01-01' ORDER BY date
""").df()["date"].tolist()
month_ends = {}
for d in days:
    month_ends[d[:7]] = d   # 每月最后一个交易日
anchors = sorted(month_ends.values())
anchors = [a for a in anchors if a <= "2026-04-30"]  # rule-compliance: ok evidence=measured-experiment-window-boundary
print(f"月度截面数: {len(anchors)}")

def eta2(returns, groups):
    """ANOVA eta^2: 组间方差占总方差比."""
    grand = returns.mean()
    ss_total = ((returns - grand) ** 2).sum()
    if ss_total == 0:
        return 0.0
    ss_between = 0.0
    for g in np.unique(groups):
        m = groups == g
        if m.sum() == 0:
            continue
        ss_between += m.sum() * (returns[m].mean() - grand) ** 2
    return ss_between / ss_total

SCHEMES = ["sw_l1_name", "sw_l2_name", "tdx_l1_name", "tdx_l2_name", "tdx_l3_name"]
agg = {s: {"eta": [], "net": [], "ngroups": []} for s in SCHEMES}

for anchor in anchors:
    fi = days.index(anchor)
    if fi + FWD >= len(days):
        continue
    fwd_day = days[fi + FWD]
    px = mk.execute("""
        SELECT a.code, a.close AS c0, b.close AS c1
        FROM (SELECT code, close FROM price_kline_tdxhub WHERE date=? AND freq='daily') a
        JOIN (SELECT code, close FROM price_kline_tdxhub WHERE date=? AND freq='daily') b USING(code)
        WHERE a.close>0
    """, [anchor, fwd_day]).df()
    px["ret"] = px.c1 / px.c0 - 1
    # winsorize 1%/99% 防极端值主导
    lo, hi = px.ret.quantile([0.01, 0.99])
    px["ret"] = px.ret.clip(lo, hi)
    px = px.rename(columns={"code": "stock_code"}).merge(cls, on="stock_code", how="inner").dropna(subset=["ret"])
    if len(px) < 500:
        continue
    ret = px.ret.values
    for s in SCHEMES:
        grp = px[s].fillna("NA").values
        if len(np.unique(grp)) < 2:
            continue
        e = eta2(ret, grp)
        # shuffle baseline: 保持组大小, 随机分配股票
        codes, counts = np.unique(grp, return_counts=True)
        sh = []
        for _ in range(SHUFFLE_N):
            fake = np.repeat(np.arange(len(codes)), counts)
            rng.shuffle(fake)
            sh.append(eta2(ret, fake[:len(ret)]))
        agg[s]["eta"].append(e)
        agg[s]["net"].append(e - np.mean(sh))
        agg[s]["ngroups"].append(len(codes))

print("\n=== 区分度结果 (forward %dd, %d 月度截面, winsorized) ===" % (FWD, len(anchors)))
print(f"{'口径':<14}{'类目数':>6}{'eta²均值':>10}{'净区分度':>10}{'净/std':>9}{'胜率':>7}")
rows = []
for s in SCHEMES:
    if not agg[s]["eta"]:
        continue
    eta_m = np.mean(agg[s]["eta"])
    net = np.array(agg[s]["net"])
    net_m = net.mean()
    sharpe = net_m / (net.std() + 1e-9)
    winrate = (net > 0).mean()
    ng = int(np.median(agg[s]["ngroups"]))
    rows.append({"scheme": s, "n_groups": ng, "eta2": round(eta_m, 4),
                 "net_discrim": round(net_m, 4), "net_stability": round(sharpe, 2),
                 "net_winrate": round(winrate, 3)})
    print(f"{s:<14}{ng:>6}{eta_m:>10.4f}{net_m:>10.4f}{sharpe:>9.2f}{winrate:>7.1%}")

json.dump({"forward_days": FWD, "n_sections": len(anchors), "results": rows},
          open("/tmp/cm_checkup/industry_discrimination.json", "w"), ensure_ascii=False, indent=1)
print("\n净区分度 = 真实 eta² - 同组数随机基线 (扣类目数机械效应); 越高越好, 稳定性=净均值/标准差")
sm.close(); mk.close()

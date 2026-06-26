# holders_tdx 双轨核对 — tushare top10_floatholders vs tdxhub fact_top10_holder_period

> **[状态校正 2026-06-26 doc治理]** 本文"双轨核对≥99%"方法 + 结论均被取代: (1) 双轨值比对仪式 2026-06-23 用户简化作废 (CLAUDE §4.3); (2) **holder 主源 2026-06-24 定为东财 aif10** (RPT_F10_EH_FREEHOLDERS), 因实测 tushare top10_floatholders 财报季驱动**滞后~4个月** (反例 600388 紫金入主龙净 6/8 tushare 只到 3/31), aif10 全市场+含季中ad-hoc+深史全胜。tdxhub fact_top10_holder_period 已退役。详 `analysis/miaoxiang_aif10_source_decision_20260624.md`。本文留作 holder 源选型溯源。

> 日期: 2026-06-23 · 删源程序 (§4.3 tushare唯一删旧源 · 铁律11 物删前双轨≥99%) · read-only 证据 (物删是后续 gated 步)
> owner: 本文 + sandbox/holders_dualtrack/probe.py · 消费方: dossier.load_top10_holders = `_top10_tushare(...) or _top10_tdx(...)`

## 裁决: **双轨不达标 — 不能直接物删 tdx (需先回补 tushare ST 股)**

| 指标 | tushare raw_tushare_top10_floatholders | tdx fact_top10_holder_period (free) |
|---|---|---|
| 行数 | 1,863,300 | 287,360 |
| 股数 | 4965 | 5178 |
| 期跨度 | ann_date 2005-01 ~ 2026-04 (真 PIT 公告日, 史长) | report_date 2017-12 ~ 2026-06 |

**覆盖交集**: 两者都有 4953 / 仅 tushare 12 / **仅 tdx 225 (其中 220 近期活跃)**。

**活跃宇宙视角** (近30交易日有K线=活跃, 共 5203 股):
- tushare 对活跃宇宙覆盖 = **4964/5203 = 95.41%** (< 铁律11 的 ≥99% 闸)。
- **239 活跃股无 tushare holder**, tdx 补其中 220 → 直接删 tdx = 220 只活跃 A 股 dossier 丢 holder 数据 = 真回归。

## 根因: sync 的 `universe_filter: true` 排除了 ST 股 (非 tushare 限制)

- **220 仅tdx活跃股中 219 曾被 ST** (raw_tushare_stock_st 命中); 仅 1 只 (301683) 从未 ST。
- sync_registry top10_floatholders 域 `universe_filter: true` (排除北交所/ST 写入门) → ST 股被挡在 tushare 表外; tdx F10 当年没过滤 ST → tdx 有这批 ST 股。
- **tushare API 实探确认有数据** (可回补, 非限制): 000016.SZ 返 666 行 / 000010.SZ 769 行 / 000056.SZ 683 行 (均含最新 2026-03-31 期)。

## 字段覆盖 (tushare 覆盖 dossier 消费所需)

dossier `_top10_tushare` 已是主源, 消费 holder_name/hold_amount/hold_float_ratio/hold_ratio/hold_change/ann_date/end_date — 全有;
状态(新进/退出/增持/减持)由 hold_change 符号 + 跨期 diff 派生, 功能等价 tdx 的 change_status 文本。**无字段缺口**。

## remediation (达标路径) → 然后才 gated 物删

1. **holders 是 reference/display 数据, 不该按交易 universe 过滤排 ST** (universe_filter 是给可交易策略宇宙的, 误用到参考数据 sync)。
   → top10_floatholders 域去 ST 过滤 (北交所是否纳入待定; 至少纳 ST), 回补 220 只 ST 股 (tushare 有数据)。
2. 回补后重核双轨: tushare 活跃覆盖 ≥99%。
3. **达标才 gated 步**: dossier 去 `_top10_tdx` fallback → 物删 fact_top10_holder_period (deletion_record) → 退役 tdx holder client/sync。

## 决策点 (需用户/policy)

holder sync 是否纳 ST 股 (去 universe_filter)? 推荐**纳入** (holders=参考数据, dossier 展示任意股含 ST; tushare 有数据; 否则删 tdx 丢 220 活跃股 holder 展示)。北交所是否纳入另议 (活跃宇宙本就排北交所)。

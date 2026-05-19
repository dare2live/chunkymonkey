# 大宗交易 Alpha Spec (Codex ae9f598b refined)

## 用户 vision (2026-05-19)

电连技术 SZ300679 案例: 4.29-5.18 机构通过大宗交易累计买入 15624.53 万元, 加权
均价 47.03 元 → 形成 4 个关键支撑价位 (机构成本附近 / 短期趋势 / 突破点 / 52w 前高).
核心 hypothesis: 大宗交易后机构加权成本附近形成支撑, 影响 forward N 日涨跌.

## Codex actionable spec

### 1. 数学

**WAP_N (rolling N=60 days)**:
- 纯金额加权: `WAP_N = Σ(price_i × amount_i) / Σ(amount_i)`
- 时间衰减加权 (生产): `WAP_N_decay = Σ(price_i × amount_i × exp(-λ × d_i)) / Σ(amount_i × exp(-λ × d_i))`
- λ = ln(2) / 20, 20 交易日半衰期 (N/3), 60d 后权重剩 12.5%
- 保留 `WAP_N` 做 ablation

### 2. 5 个 features (PIT-safe)

| Feature | 公式 | 方向 |
|---|---|---|
| `block_trade_cost_spread` | `(close_ref - WAP_N_decay) / WAP_N_decay` | - (越负越靠近成本=支撑强) |
| `block_trade_volume_ratio` | `Σ(block_vol_shares_N) / Σ(kline_vol_shares_N)` | + |
| `block_trade_support_score` | `1 / (1 + |close_ref - WAP_N_decay| / ATR20)` | + |
| `volume_anomaly` | `volume_ref / avg(volume, 60d)` | + |
| `weighted_inst_block_buy_ratio` | `Σ(amount × buyer_weight) / Σ(amount)` | + |

**机构加权 (buyer_weight)**: 机构专用=1.0 / QFII RQFII 合格境外=0.9 / 基金 资管=0.8 / 信托=0.6 / 其他=0

**Net 加权**: `net_weighted_inst_block_ratio = (Σ(buyer_weight × amount) - Σ(seller_weight × amount)) / Σ(amount)`

### 3. PIT 严格

- `fact_dzjy_event` 当前缺 `notice_date`. 保守: `source_available_date = next_trading_day(trade_date)` (T+1 可用)
- `pit_policy = 'notice_date_or_trade_date_plus_1'`
- 特征 query: `event.trade_date >= signal_date - N AND event.source_available_date <= signal_date`
- 价格 ref: 信号在 signal_date 开盘前生成 → `close_ref = close on prev_trading_day(signal_date)`

### 4. 数据 backfill

- `fact_dzjy_event` 当前 7 天 548 rows
- backfill akshare `ak.stock_dzjy_mrmx(symbol='A股', start_date='20211001', end_date='20260519')`
- ~1160 calls × 1 req/s × 失败 3 retry = **25-40 min**
- 估 12 万 events (A 股 ~250-300 events/day 高峰)
- 2021Q4 作 90d warmup, 有效训练 2022-01-01 起

### 5. 表 schema `mart_block_trade_score_daily`

PRIMARY KEY: `(signal_date, stock_code, window_days, score_version)`. Partition: `signal_month`.

30 cols (key):
- signal_date / signal_month / stock_code / window_days (30/60/90, 生产 60) / score_version (block_v1)
- price_ref_date / latest_trade_date / latest_source_available_date / event_count / block_days_count
- block_amount_yuan / block_volume_shares / kline_volume_shares / wap_price / wap_decay_price
- close_ref / atr20 / ref_volume_shares / avg_volume_60d_shares
- **5 features**: block_trade_cost_spread / block_trade_volume_ratio / block_trade_support_score /
  volume_anomaly / inst_block_buy_ratio
- weighted_inst_block_buy_ratio / weighted_inst_block_sell_ratio / net_weighted_inst_block_ratio
- **composite**: block_trade_score (0-100) / block_trade_hit (>=70 且 PIT/coverage 合格)
- coverage_flag (ok/sparse/no_event/no_kline) / pit_policy / built_at

### 6. 集成方式

**Option 2 推荐**: 单独 `mart_block_trade_score_daily` 表 → **sniper confluence 第 7 因子**.

现 sniper 6 因子各 15% → 加 block_trade 10%, sniper 6 因子降为 90/6 = 15% each (实际不变).

触发: `weighted_confluence_score >= 60` 且 `n_non_null_components >= 3`. block_trade 不能单独触发.

内部 composite:
```
block_trade_score = 100 × (
    0.25 × cost_rank +
    0.25 × volume_ratio_rank +
    0.20 × support_score +
    0.15 × volume_anomaly_rank +
    0.15 × weighted_inst_block_buy_ratio
)
```

**为啥不独立 alpha**: 事件稀疏 (~250 events/day 在 5200 股), 独立信号易过拟合.

### 7. 验证

- 回测 IC: walk-forward 5 fold, 预期 `RankIC delta >= +0.003` over alpha158 baseline
- promote 门槛: delta ≥ +0.003 且 5 folds 至少 4 同号
- 异常: `> +0.008` 优先排查 PIT leakage / 小样本异常

### 8. ETA

- 1d smoke: 接现 548 rows 跑 IC, 无统计意义 (数据不够)
- **1w 最小有效**: 数据 backfill 2d + features+panel 2d + IC 验证 2d + 汇总 1d
- 1mo 生产: 全 backfill + notice_date 治理 + PIT fact 表 + LM+sniper 重训 + 监控

### 9. 实施 actionable list (不实施, 仅 spec)

- `backend/scripts/backfill_block_trade_events.py` → `fact_dzjy_event`
- `backend/scripts/build_block_trade_score_daily.py` → `mart_block_trade_score_daily`
- `backend/scripts/run_walkforward_feature_eval.py --feature-set-id block_trade_v1_pit --folds 5`
- `backend/scripts/run_feature_group_ablation.py --feature-set-id block_trade_v1_pit --method walkforward`
- 可选 retrain: `run_p0b_lightgbm_optuna_v4.py --label fwd_cost_after_20d --feature-panel ..._v4_block_v1`

## 关联

- Codex agent: ae9f598bce947af56 (refined spec), afcd11eec665a09c5 (初版 design)
- 数据: data/smartmoney.duckdb.fact_dzjy_event (当前 548 rows, 7 天)
- 反例: [[feedback-leakage-red-flag]] (RankIC delta > +0.008 怀疑 leakage)
- 关联 framework: docs/market_regime_framework.md (FundFlowEngine 资金信号互补)

**用户原话**: "先不用把市场研究并入主线" — 此 spec 纯设计 doc, **不立即实施**.

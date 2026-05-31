# 全市场股票关系图谱 Spec (Codex a789183e refined)

## 用户 vision (2026-05-19)

"给现在的股票全量做一个关系图谱和市场行情的理解分析用于辅助量化选股. 先不用并入主线".

不是 weight modulator (改 ensemble 权重), 是 **additive feature col** — 加 context features 进 model, 由 model 自学怎么用.

## Codex actionable spec

### 1. 存储/技术选型

**推荐: DuckDB tables + SQL JOIN** 模拟 graph.

理由:
- ChunkyMonkey 单源偏好 (CLAUDE.md Rule 8 不引外部 DB)
- PIT audit 简单 (跟 mart_p0a_label_panel 同库)
- 可直接喂 model 作 feature col

**不推荐**: networkx (仅离线验证) / Neo4j (额外 service, 维护成本高).

**存储估**: 节点 5200 stocks × 800 trading days = 4.16M rows × 15 attrs × 4B ≈ **500MB DuckDB**.

### 2. MVP scope (按必要性排序)

**必做 (4 类 edge)**:
1. `same_industry`: 0/1 binary (`dim_stock_tdx_industry`)
2. `same_concept`: Jaccard(F10 tags) similarity
3. `fund_resonance`: rolling 60d 资金流相关性 (`fact_capital_flow_pit_daily`)
4. `lhb_actor_overlap`: 同一龙虎榜买卖席位重叠

**nice-to-have**:
- `leader_follower`: 历史龙头涨停后邻居 t+1d/3d 跟随次数 (event-driven, build 复杂)
- `business_overlap`: F10 关键词 cosine similarity (需 NLP)
- `chain_upstream/downstream`: 手工 mapping table 优先, F10 NLP 后置

### 3. 节点 attributes (15 cols, per signal_date)

| Col | 类型 | 用 |
|---|---|---|
| stock_code | VARCHAR | PK |
| signal_date | DATE | PK |
| industry_id | VARCHAR | edge same_industry |
| concept_tags_hash | VARCHAR | edge same_concept |
| f10_tags_hash | VARCHAR | edge business_overlap |
| sector_momentum_rank | INTEGER | rank within industry |
| net_inflow_1d | DOUBLE | 主力净流入 1d |
| net_inflow_5d | DOUBLE | rolling 5d |
| net_inflow_20d | DOUBLE | rolling 20d |
| hsgt_net_buy_5d | DOUBLE | 北向 5d |
| lhb_buy_strength_60d | DOUBLE | LHB 净买 60d |
| lhb_sell_pressure_60d | DOUBLE | LHB 净卖 60d |
| degree_industry | INTEGER | 同 industry 邻居数 |
| degree_concept | INTEGER | 同 concept 邻居数 |
| regime_risk_on | INTEGER | 0/1 market regime |

### 4. PIT-safe 设计 (5 要点)

1. 边: monthly snapshot (industry/concept) + 资金类每日 incremental
2. 查询: 取 signal_date 前最近 snapshot (`valid_from <= signal_date < valid_to`)
3. 表保留 `valid_from / valid_to / as_of_date`
4. F10 若无 PIT 标签 → 按首次入库日 `built_at` 作 effective from
5. 禁止用 latest F10 回填历史. 相关性/跟随统计用 t-1 及以前窗口

### 5. 实施 ETA (3 phase)

- **Phase 1 MarketRegime engine** (2-4d): risk_on/off binary 用板块强弱 + 全市场资金流 + 北向资金 (现 fact_hsgt_daily)
- **Phase 2 industry+concept edges** (4-7d): build `dim_stock_relationship` 表 (same_industry / same_concept) + `dim_stock_node_features` 表 (15 attrs)
- **Phase 3 fund_resonance + integration** (7-12d): rolling 60d 资金流相关性 + lhb_actor_overlap + 喂 panel v5

**总计 2-3.5 周** (单人).

### 6. 集成方式 (additive feature col)

**Option 1 推荐: additive feature col**:
- 加 15 个 node attrs + 4 个 edge-derived features (e.g. neighbor_avg_momentum, fund_resonance_avg) 到 `mart_p0a_feature_label_panel_v5`
- Retrain LambdaMART model 让它自学怎么用
- **优势**: 可解释, 可回测归因 (feature importance), 不扰动现 ensemble weight

**不推荐 (后置)**: weight modulator 直接改 ensemble (lambdamart×0.4 + sniper×0.3 + institution×0.3 在不同 regime 不同 weight). 风险: weight 改动需 Optuna 重训搜索, 且不可解释.

### 7. 实施 actionable (不实施, 仅 spec)

- `backend/scripts/build_dim_stock_relationship.py` → `dim_stock_relationship` (edges)
- `backend/scripts/build_dim_stock_node_features_daily.py` → `dim_stock_node_features_daily` (15 attrs)
- `backend/scripts/build_market_regime_daily.py` → `mart_market_regime_daily` (regime_risk_on / sentiment_score / theme_concentration)
- `backend/services/labels/feature_join_v5.py` → 扩 v4 → v5 加 15 context attrs
- 验证: `run_feature_group_ablation.py --feature-set-id context_v1_pit`

## 关联

- Codex agent: a789183ef4fee2ae0 (refined spec)
- Full framework: docs/market_regime_framework.md (7 engines 完整 vision)
- 大宗交易 spec: docs/block_trade_alpha_spec.md (互补的 alpha source)
- 反例: [[feedback-codex-critical-no-compromise]] (PIT 严格)

**用户原话**: "先不用把市场研究并入主线" — 此 spec 纯设计 doc, **不立即实施**.

后续可作为 v5 panel feature expansion 加 context dim (跟 lambdamart_v6 retrain 同周期 batch).

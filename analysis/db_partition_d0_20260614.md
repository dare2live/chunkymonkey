# DB 分区 D0: 写入面扫描结果 (348 表 tier 分配 + 事务边界)

> 2026-06-14 | read-only 写入面全仓扫描 (workflow wf_de6c4297, 249 条写语句)。
> owner 设计 = db_management_design_20260614.md; 本文件 = D0 实测产出, 供 grill + D1-D4。

## tier 分配 (348 表)

| tier | 表数 | 代表 | 写入方/节奏 |
|---|---|---|---|
| source | 11 | price_kline_tdxhub, raw_*, raw_executive_trade, raw_profit_forecast | 每日 sync append-only |
| feature | 20 | fact_feature_panel(+candidate) / signal_context / technical_trigger / risk_factors / sector_momentum / drift/prune/validation marts / label panel | 周期重算 (build_feature_panel + Step3-pre) |
| serving | 171 | 多数 mart_* + dim_* + scores (sniper/institution) + topk/recommendation + 模型预测 | daily_update + 在线服务 |
| experiment | 23 | mart_optuna_* / p1_optuna_trials / *_ablation_result / synergy_policy_optuna | Optuna/Modal 突发 |
| **uncertain** | 41 | fact_*_event(lhb/dzjy/jgdy) / fact_paper_position / inst_holdings / tmp_* / fact_candle_pattern | 跨写入方/非周期, 待裁决 |

## 5 大事务原子写簇 (DuckDB 事务不能跨文件 → 必须同库)

1. `fact_feature_panel` + `mart_feature_panel_validation` (+ `mart_p0a_label_panel`, daily_update Step3 两写入方同时调) → **feature 同库**
2. `fact_paper_position` + `mart_paper_nav` → **experiment 同库** (L206-242 BEGIN/COMMIT)
3. `fact_stock_fundamental_stage_daily` + `fact_stock_type_daily` + `dim_stock_stage_days` + `mart_stock_picture_daily` → **serving 同库** (build_picture_daily 4表1事务 L344)
4. `mart_daily_position_recommendation` + `_pit_diagnostic` → **serving 同库** (L539-579)
5. `fact_optuna_governance_log` + 同轮 optuna 输出 → **experiment 同库** (否则 late-rejection 不可见 = PIT leakage)

**关键: 5 簇都不跨"feature/serving"分界 → 主争锁 (feature_panel vs daily_update) 的拆分可行, 不破坏任何原子写。**

## 跨 tier JOIN (ATTACH read_only 解决)
- feature_panel ← signal_context/technical_trigger/institution_event (feature 内部, OK)
- position_recommendation(serving) ← per_stock_stage_strategy_optimal_pit + optuna_governance_log(experiment) → ATTACH
- archetype/stage_latest(source/dim) → feature_panel 计算链 → 建议同 feature 或 ATTACH

## 待裁决 (uncertain, controller 决定) — 我的建议
| 表组 | 冲突 | 建议 tier |
|---|---|---|
| fact_*_event (lhb/dzjy/jgdy) | daily_update sync 写 + 独立 ingest 写 | **source** (sync 出的事件真相, append-only, feature/serving 读) |
| inst_holdings | routers/updater_institution 写 | serving |
| tmp_* | 脚本内 staging, 生命周期单次 | 不迁, 留 serving (无需动) |
| fact_optuna_governance_log | 审计表读多写少 | **experiment** (与 optuna 输出原子, 不单独拆) |
| fact_candle_pattern / fact_paper_* | 研究/回放 | 逐表核 (paper→experiment, candle→feature?) |

## D0 附带发现 (latent bug, 另立)
`optimize_per_stock_stage_strategy.py` (~L200/L250): governance_log 写 + strategy 表写 **不在一个事务** → 可能 orphan governance 记录。建议独立修 (非 D0 范围)。

## 结论
4-tier 分区**可行且不破坏事务原子性** (5 簇都落单一 tier 内)。主争锁 feature_panel-vs-daily_update 被 feature/serving 拆分根治。下一步: grill (验证方案逻辑 + 敲定 uncertain) → D1 实验库迁移 (EXPORT/IMPORT)。

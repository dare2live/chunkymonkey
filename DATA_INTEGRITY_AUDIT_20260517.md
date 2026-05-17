# 数据完整性 Audit 2026-05-17

用户原话: "我现在怀疑项目数据完整性比较差". 实测确认部分成立.

## 完整性 (verified [OK])

| 表 | rows | dates | stocks | range | 状态 |
|---|---|---|---|---|---|
| mart_p0a_feature_label_panel_v3 | 2,901,970 | — | — | — | [OK] |
| mart_p0a_feature_label_panel_v4 | 2,901,970 | — | — | — | [OK] |
| mart_p0b_oos_predictions | 2,159,871 | — | — | — | [OK] (历史 model 预测) |
| fact_financial_pit_daily | 3,685,913 | 748 | 5,196 | 2023-04-07 ~ 2026-05-13 | [OK] (PIT-safe) |
| fact_capital_flow_pit_daily | 857,993 | 810 | 5,167 | 2023-01-03 ~ 2026-05-13 | [OK] |

## 严重 gap (用户怀疑成立)

| # | 表 | gap | 影响 |
|---|---|---|---|
| 1 | **price_kline_tdxhub 主行情** | 2026-04-30 前 5,150 / 2026-05-07 起 105 / 2026-05-13 起 36 codes | **91% codes 缺失**, daily live trading 无法跑 |
| 2 | fact_industry_beta_daily | cutoff 2026-04-23 (3 周前) | beta features 缺最近 3 周 |
| 3 | fact_market_cap_decile_daily | cutoff 2026-04-23 | mcap_decile 缺最近 3 周 |
| 4 | v4 panel sector_momentum (9 cols) | **0% coverage 全 CONST** | 占 panel 维度但 LGBM 无法用 |
| 5 | holder_count_change_q_pct | 97% NULL | 季度 PIT 太 sparse |
| 6 | mart_p1_optuna_trials | 仅 3 rows | 历史 Optuna trial 数据无 (baseline 0.0246 model 训练 trial 未存) |
| 7 | mart_stock_industry_pit | 14.3% current_label_fallback | sector path PIT confidence 较低 |
| 8 | survey_count_30d/60d | cutoff 2025-04-23 (训练前期全 0) | 仅最近 13 month coverage |

## 影响范围

| 工作流 | 受影响吗 | 详情 |
|---|---|---|
| Optuna v4 训练 (Wave 1+) | [NO] 不受影响 | 训练 cutoff = 2026-04-13, 早于 sync gap |
| paper_sim live (daily trading) | [OK] 严重受影响 | 缺最近 3-5 周行情, 无法选股 |
| forecast_upside_live | [OK] 受影响 | close 只 45/2,313 stocks 有值 |
| daily cron / champion register | [OK] 受影响 | 取最近行情失败 |

## 修复优先级

### P0 立即 (但需用户参与)

1. **更新 tdxhub server 列表** — 用户查 / 提供新服务器 IP
   - 当前 10 IP 全 timeout
   - 用户可登录 GCP VM SSH 自检 / 用 tdxhub-go 工具找 alive servers
2. **或换数据源** — akshare push2his 用户网络 block 确认
   - 备选: Wind / Choice (用户无 license)
   - 备选: scraping (不稳定)
3. **物理 DELETE 残缺日** + 重 sync 2026-05-07 ~ 2026-05-16 历史

### P1 数月内

4. mart_stock_industry_pit 数据源增强 (observed_snapshot 覆盖 → 减少 fallback)
5. fact_industry_beta_daily / fact_market_cap_decile_daily 重 sync 到 latest trading day
6. v4 panel 重 build 含 sector_momentum 真 PIT (修 #4 后)
7. survey 数据 source 持续累积

### P2 不修也 OK

8. mart_p1_optuna_trials 历史 lgbm baseline (本 session 接续会持续填)

## Governance 现状

| 模块 | 部署状态 | 覆盖 |
|---|---|---|
| services/data_governance/ | [OK] deployed | 8 mart schema 入字典 |
| services/optimization/governance.py | [OK] deployed | enforce_pre_optimize + enforce_pre_insert |
| services/labels/feature_join.py | [OK] post-insert dict verify | 入库守门 |
| pit-audit skill 5-step | [OK] available | 但缺**强制每次 commit 触发** |
| Pre-commit hooks | [OK] 部分 | rule-compliance + project-index-sync + codex-review |
| Nightly audit cron | [OK] 部署 (launchd) | mart_audit_snapshot_state 跟踪 |

**Gap**: nightly audit 检查 mart 表 row count + null ratio + freshness, 但 **没自动 ALERT** 严重缺失. 仅 mart_data_source_watermark 表能查. 当前 watermark cutoff 也是 2026-05-05/06 (跟 tdxhub sync gap 一致).

**结论**: governance framework deployed but **alerting 滞后, 没主动通知 sync gap**. 待加 alerting (推 Slack / 邮件) — 在 #71 fix sync gap 时一起改.

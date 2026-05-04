# TDX-first Feature Validation Report

执行日期: 2026-05-03 至 2026-05-04 Asia/Shanghai
数据库: `data/smartmoney.duckdb`
范围: 当前候选面板可用交集为 2023-01-03 至 2026-04-23；gpcw wide 覆盖 2023-03-31 至 2026-03-31 共 13 个报告期。

## 仓库状态

- `tdxhub`: `9ca0287 Expand F10 holder parser coverage`，本轮无新增 parser 补丁；目标测试通过。
- `miaoxiang`: 本轮只复核补源边界和 P6 probe，不提交业务改动。
- `chunky-monkey-v2`: 本报告随 `Complete TDX-first feature validation pipeline` 提交；实际 commit hash 以提交后的 `git log -1` 和最终执行回报为准。

生产 champion 未替换；所有结果停留在 candidate、selection、retention、challenger 报告表。

## 数据源安排

`dim_data_source_priority` 已写入 9 个数据域:

| 数据域 | 主供 | fallback | 说明 |
|---|---|---|---|
| quotes_and_labels | tdxhub_quote | akshare | K 线、指数、复权、forward labels 优先 TDX。 |
| gpcw_financials | tdxhub_gpcw | akshare | 财务三表、质量、成长、预告快报主供。 |
| holder_aggregate | tdxhub_gpcw | tdxhub_f10, miaoxiang | 聚合口径优先 gpcw，F10/miaoxiang 补明细。 |
| holder_detail | tdxhub_f10 | miaoxiang | 十大股东、十大流通股东、户数、退出事件、基金持股。 |
| institution_aggregate | tdxhub_gpcw | miaoxiang | 机构类型聚合。 |
| institution_detail | miaoxiang | tdxhub_gpcw | 机构名明细仍保留外部源。 |
| valuation_and_consensus | miaoxiang | akshare | 估值分位、同行排名、卖方一致预期。 |
| survey_and_text | miaoxiang | akshare | 机构调研、文本、主营构成。 |
| capital_events | tdxhub_quote | miaoxiang, akshare | 除权除息用 TDX，复杂方案细节保留补源。 |

`mart_tdx_data_need_coverage` 覆盖 26 个数据需求，`mart_data_source_reassignment_proposal` 生成 14 条核心表重分配建议。已明确保留 miaoxiang 的域: 主营构成、估值/同行、一致预期/评级、机构调研、机构持仓明细、龙虎榜/融资融券等外部明细、复杂资本事件细节。akshare 降级为行情/事件兜底和未迁出接口 fallback。

## F10 Extra

`raw_tdx_f10_holder_research`: 5,200 行，5,199 只股票。

解析状态:

- `completed`: 4,054
- `skipped_non_format_b`: 1,146

结构化事实:

- `fact_holder_count_period`: 209,318 行，3,632 只股票。
- `fact_common_major_holder_stock`: 33,426 行，1,468 只股票。
- `fact_fund_holding_tdx_f10`: 12,662 行，1,277 只股票。
- `fact_holder_event`: 471,217 行。

事件类型:

- `new_entry`: 178,810
- `unchanged`: 134,513
- `exit`: 63,875
- `decrease`: 51,921
- `increase`: 42,098

基金持股污染检查: `bad_fund_rows = 0`，未检出免责声明、页尾、单位表头污染。

## GPCW Profiling 和自动特征

tdxhub 最新 gpcw 抽样复核:

- 最新文件: `gpcw20260331.zip`
- `report_fields_count`: 584
- known data columns: 580
- placeholder columns: 182
- stock count: 5,510

chunky 入库结果:

- `raw_tdx_gpcw_wide`: 70,355 行，5,514 只股票，13 个报告期。
- `dim_tdx_gpcw_field`: 580 行。
- `mart_tdx_gpcw_field_profile`: 2,386 行。
- `dim_tdx_gpcw_field_semantic`: 580 行。
- `fact_tdx_gpcw_auto_feature_quarterly`: 16,371,042 行，233 个自动特征，5,514 只股票，13 个季度。
- mapped p0/p1 candidate fields: 68。

自动语义层不把未命名 `colNN` 直接作为生产特征；未映射字段只保留在 raw/profile/semantic 审计层。

## Candidate Panels

| feature_set_id | rows | stocks | dates | min_date | max_date | features |
|---|---:|---:|---:|---|---|---:|
| `tdx_f10_gpcw_v1` | 4,022,758 | 5,200 | 799 | 2023-01-03 | 2026-04-23 | 20 |
| `tdx_gpcw_auto_v1_pit` | 4,022,758 | 5,200 | 799 | 2023-01-03 | 2026-04-23 | 120 |

命令按 `--start 2021-01-01` 执行，但当前 DuckDB 内行情、F10/gpcw 可用交集从 2023-01-03 开始，因此面板自然从 2023 年起算。

## PIT/ASOF

- `pit_tdx_f10_gpcw_v1`: 20 个特征，violation rows = 0，status = passed。
- `pit_tdx_gpcw_auto_v1`: 233 个源自动特征，violation rows = 0，status = passed。

所有 `keep` 特征均基于通过 PIT 审计的候选集。

## Walk-forward、消融和选择

### `tdx_f10_gpcw_v1`

- Walk-forward run: `wf_tdx_f10_gpcw_v1`
- folds: 6
- eval rows: 480
- Optuna run: `feature_elim_20260503_155722`
- method: `optuna`
- trials: 200
- objective score: 0.052753
- promote_to_champion: false

特征组消融:

- `holder_count_chip`: delta 0.012114
- `forecast_express`: delta 0.009287
- `fundamental_quality`: delta 0.005334
- `institution_gpcw`: delta 0.001957
- `ownership_tdx_f10`: delta -0.000247

Retention `retention_tdx_f10_gpcw_v1`:

- keep: 5
- watch: 9
- drop: 6

Top decisions:

| feature | decision | reason | mean_rank_ic | same_sign | coverage_pct |
|---|---|---|---:|---:|---:|
| forecast_profit_yoy_mid | keep | selected_stable_positive_group | 0.058385 | 1.000 | 92.876 |
| avg_float_shares_change_pct_tdx | keep | selected_stable_positive_group | 0.029357 | 1.000 | 64.957 |
| ocf_to_profit_tdx | keep | selected_stable_positive_group | 0.017098 | 0.667 | 92.807 |
| fund_shares_qoq | keep | selected_stable_positive_group | 0.016125 | 0.667 | 68.717 |
| forecast_range_width | keep | selected_stable_positive_group | 0.007365 | 0.667 | 92.876 |
| fund_holding_shares_tdx_f10 | watch | sparse_event_feature | 0.059341 | 0.500 | 1.676 |
| fund_holding_float_a_ratio_tdx_f10 | watch | sparse_event_feature | 0.047851 | 0.667 | 1.676 |
| fund_holding_market_value_tdx_f10 | watch | sparse_event_feature | 0.047294 | 0.333 | 1.676 |
| holder_count_change_pct_tdx | watch | unstable_walkforward_sign | -0.036454 | 0.000 | 65.434 |
| top10_concentration_change | watch | sparse_event_feature | 0.036409 | 0.167 | 6.709 |
| common_holder_network_count | watch | sparse_event_feature | 0.022451 | 0.167 | 1.342 |
| tdx_inst_total_shares_qoq | watch | unstable_walkforward_sign | -0.020118 | 0.500 | 84.322 |
| holder_count_acceleration_tdx | watch | unstable_walkforward_sign | -0.012963 | 0.167 | 64.929 |
| contract_liabilities_to_revenue | watch | unstable_walkforward_sign | -0.002980 | 0.500 | 85.504 |
| express_net_profit_yoy | drop | low_coverage | -0.013971 | 0.500 | 4.473 |
| qfii_shares_qoq | drop | low_coverage | 0.011494 | 0.500 | 13.195 |
| social_security_shares_qoq | drop | low_coverage | 0.009729 | 0.667 | 11.996 |
| inventory_to_revenue | drop | high_corr:contract_liabilities_to_revenue | -0.007181 | 0.500 | 92.816 |
| national_team_shares_qoq | drop | low_coverage | 0.006965 | 0.500 | 7.750 |
| receivables_to_revenue | drop | low_walkforward_rank_ic | -0.004545 | 0.667 | 92.816 |

Challenger `challenger_tdx_f10_gpcw_v1`:

- selected features: `forecast_profit_yoy_mid`, `forecast_range_width`, `ocf_to_profit_tdx`, `avg_float_shares_change_pct_tdx`, `fund_shares_qoq`
- rank_ic: 0.046046
- long_short_return: 0.016873
- baseline_rank_ic: 0.011972
- max_drawdown: -0.035610
- promote_to_champion: false

### `tdx_gpcw_auto_v1_pit`

- Walk-forward run: `wf_tdx_gpcw_auto_v1`
- folds: 6
- eval rows: 2,880
- selection run: `feature_elim_20260503_155844`
- method: `sql_auto_deterministic`
- trials: 200
- objective score: 0.009420
- promote_to_champion: false

自动特征组消融:

- `forecast_express`: delta 0.000541
- `ownership`: delta 0.000023
- `fundamental_quality`: delta -0.000648

Retention `retention_tdx_gpcw_auto_v1`:

- keep: 8
- watch: 86
- drop: 26

Top decisions:

| feature | decision | reason | mean_rank_ic | same_sign | coverage_pct |
|---|---|---|---:|---:|---:|
| auto_general_corp_count_event_nonzero | keep | selected_stable_positive_group | 0.014059 | 1.000 | 88.619 |
| auto_general_corp_shares_event_nonzero | keep | selected_stable_positive_group | 0.014059 | 1.000 | 88.619 |
| auto_general_corp_count_level | keep | selected_stable_positive_group | 0.009727 | 0.800 | 88.619 |
| auto_top10_float_holder_shares_event_nonzero | keep | selected_stable_positive_group | 0.009330 | 0.600 | 88.619 |
| auto_top1_holder_shares_event_nonzero | keep | selected_stable_positive_group | 0.009162 | 0.800 | 88.619 |
| auto_holder_count_event_nonzero | keep | selected_stable_positive_group | 0.008879 | 0.800 | 88.619 |
| auto_top10_holder_shares_event_nonzero | keep | selected_stable_positive_group | 0.008879 | 0.800 | 88.619 |
| auto_private_equity_shares_level | keep | selected_stable_positive_group | 0.005331 | 0.700 | 88.619 |
| auto_nav_per_share_level | watch | unstable_walkforward_sign | -0.033781 | 0.400 | 88.619 |
| auto_fund_count_event_nonzero | watch | unstable_walkforward_sign | -0.019865 | 0.400 | 88.619 |
| auto_fund_shares_event_nonzero | watch | unstable_walkforward_sign | -0.019865 | 0.400 | 88.619 |
| auto_social_security_count_event_nonzero | watch | unstable_walkforward_sign | -0.019175 | 0.300 | 88.619 |
| auto_social_security_shares_event_nonzero | watch | unstable_walkforward_sign | -0.019175 | 0.300 | 88.619 |
| auto_social_security_count_level | watch | unstable_walkforward_sign | -0.017547 | 0.200 | 88.619 |
| auto_fund_count_qoq | watch | unstable_walkforward_sign | 0.012018 | 0.500 | 65.134 |
| auto_insurance_count_level | watch | unstable_walkforward_sign | -0.010191 | 0.400 | 88.619 |
| auto_insurance_count_event_nonzero | watch | unstable_walkforward_sign | -0.009454 | 0.400 | 88.619 |
| auto_insurance_shares_event_nonzero | watch | unstable_walkforward_sign | -0.009454 | 0.400 | 88.619 |
| auto_inst_total_count_qoq | watch | unstable_walkforward_sign | 0.008706 | 0.500 | 78.909 |
| auto_contract_liabilities_level | watch | unstable_walkforward_sign | -0.007758 | 0.300 | 80.386 |

Challenger `challenger_tdx_gpcw_auto_v1`:

- selected features: `auto_general_corp_count_event_nonzero`, `auto_general_corp_count_level`, `auto_general_corp_shares_event_nonzero`, `auto_holder_count_event_nonzero`, `auto_private_equity_shares_level`, `auto_top10_float_holder_shares_event_nonzero`, `auto_top10_holder_shares_event_nonzero`, `auto_top1_holder_shares_event_nonzero`
- rank_ic: 0.011083
- long_short_return: 0.008932
- baseline_rank_ic: -0.001468
- max_drawdown: -0.073712
- promote_to_champion: false

## 测试和健康检查

tdxhub:

- `python3 -m pytest tests/test_holders.py tests/test_affairs.py tests/financial/test_affairs.py -q`: 52 passed, 8 skipped.
- `scripts/probe_gpcw_schema.py --filename gpcw20260331.zip --sample-size 20 --json`: passed，能读 584 个字段。
- `scripts/probe_capabilities.py --quick`: quote server 握手失败 `ResponseHeaderRecvFails`，按外部 TDX quote server 波动记录；gpcw financial file list 在外网权限下可返回 `gpcw20260331.zip`。

miaoxiang:

- `python3 scripts/validate_schema.py`: 69/69 passed。
- `python3 -m pytest tests -q`: 当前仓库无 pytest 用例，exit code 5 / no tests ran。
- `python3 scripts/probe_p6_targets.py`: 外网权限下 19 ok / 1 empty / 0 fail。

chunky:

- `python3 -m pytest backend/tests/test_tdx_source.py backend/tests/test_tdx_f10_extra_client.py backend/tests/test_candidate_feature_pipeline.py backend/tests/test_phase0_daily_closure.py backend/tests/test_updater_daily_sync_metrics.py -q`: 31 passed。
- `python3 backend/scripts/audit_stale_references.py`: no stale references detected。
- `python3 backend/scripts/data_health_snapshot.py --dry-run`: scanned 145 assets，severity counts green 62 / yellow 17 / red 66；dry-run 成功，无数据库破坏。

## 未处理风险

- 当前 candidate panel 历史起点受现有 DuckDB 行情和源数据交集限制，为 2023-01-03，而不是 2021-01-01。
- health dry-run 中仍有既有 freshness/orphan 红项，尤其旧事件、推荐、风险、原始外部源表；这些不是本轮 TDX-first candidate pipeline 的失败，但需要另一个数据刷新/日常任务治理阶段处理。
- tdxhub quote quick probe 依赖外部 TDX quote server，当前出现握手失败；financial/gpcw 文件 list 和 gpcw schema 抽样已验证可用。
- miaoxiang 仓库当前没有 pytest 用例，只有 schema validate 和 P6 probe 作为可执行验收。
- 自动 gpcw 集合的整体 rank_ic 明显弱于手工集合，保留的自动特征主要是 ownership event/level 类；暂不建议替换生产特征，只作为候选扩展和 watch pool。
- 所有相关性均为历史统计相关，不解释为因果。

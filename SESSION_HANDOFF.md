# SESSION HANDOFF

Manual Codex checkpoint. Current operating state lives in `goal.md`; durable
startup rules live in `AGENTS.md` and `docs/chunkyctl_session_quickstart.md`.
This file is a short recovery note, not a replacement for those authorities.

Snapshot: `2026-06-01 13:09:39 CST`

## Risk First

| Item | State | Action |
|---|---|---|
| Worktree | Long-lived dirty is clean; K-line refresh wrote ignored DuckDB data and did not create tracked dirty before this state-doc update | If dirty, run `scripts/chunkyctl worktree --format markdown` and resolve by bucket; never use `git add .` |
| GCP | No GCP work started in this Codex slice | Keep stopped unless a scoped, approved cloud objective exists |
| Optuna/backtest | Not running | Do not resume until architecture/data gates allow it |
| Storage payload | `PASS`: 320 scanned / 0 FAIL / 0 WARN / 11 reviewed PASS | Reviewed columns are governed by `backend/config/storage_retention.yaml`; recursive or over-cap payloads still block |
| Data health | `FAIL`: 13 red / 23 yellow / 341 total; `scripts/chunkyctl doctor --fast` now includes `data_health_snapshot.py`, and `warning/monitor_only` assets are capped to yellow before verdict aggregation | Review the 13 blocking red buckets bucket-by-bucket before trusting freshness claims; missing tables and stale writers only block when their quality gate is blocking |
| CodeGraph | Synced after the survey `.py` slice; pending may show the new untracked test until this slice is staged/committed | Re-run `codegraph status .` after this commit |
| Complexity | Historical HIGH remains debt; tooling diff ignores line-number drift by default | New HIGH still blocks; line drift alone should not |
| Data freshness/PIT | **WARN/MIXED but non-blocking**: end-to-end freshness PASS (trading-date aligned); data completeness PASS with WARN (0 FAIL / 2 WARN, both sparse-event evidence); survivorship gate PASS on current label_version, legacy v2 only via explicit flag；`rank_and_size()` 已改为 PIT-tier-first，但当前推荐仍全是 `cross_stage_fallback`，因为 2026-05-29 的 PIT exact 候选多数卡在 `hp/n_signals/Wilson` 门槛，coverage 0 是候选稀疏/阈值问题；2026-06-01 的 `audit_portfolio_sizer_profile_attrition.py` 再次显示 353 个 raw candidates 中短/中/长档 selected_rows 仅 5/1/2，且全是 `cross_stage_fallback`，fail reasons 主要集中在 `hp` 与 `wilson`；最新 `fail_reasons_by_match_tier` 进一步把 exact-tier attrition 拆开：`stage_pit` 主要卡 `hp/n_signals/Wilson`，`stage_pit_formula_fallback` 主要卡 `hp/n_signals`，`cross_stage_fallback` 主要卡 `hp/wilson`；新补的 `fail_holding_days_by_match_tier` 再把 hp 失败拆到 holding_days：`stage_pit` 失败集中在 20/30/60/90 这些 off-anchor 档位，`stage_pit_formula_fallback` 失败集中在 20/30/60/90，`cross_stage_fallback` 则分布在 5/10/15/20/30/60/90 全部档位；2026-06-01 的 sensitivity audit 继续验证了这一点：`base` / `hold+20` / `min_n_signals-2` / `min_wilson_win-0.05` 对 selected_rows 没有影响，短/中/长仍然是 5/1/2；targeted backfill only moved latest cutoff from 3 rows to 4；`mart_daily_position_recommendation_pit_diagnostic` 现在带 `governance_reject_count` / latest reason / latest rejected_at，方便直接看每个 `stock_missing_pit` 的治理根因；2026-06-01 还把 `need_027` 的 blocked need summary 升级成 source registration evidence：preferred `akshare` 已注册，但 declared fallback 标签 `miaoxiang` 归到 `aif10` 家族，而当前 `aif10` adapter 仍未实现 `individual_fund_flow`，所以 fallback 仍是概念路径；`need_027` 主力资金源仍 blocked/unknown；`akshare.stock_individual_fund_flow` / `stock_individual_fund_flow_rank` capability 已登记，但现场 `probe_source_capability.py` 现在已先清代理再重试，但 Eastmoney 端点仍以 `ConnectionError` / `JSONDecodeError` remote disconnect 失败，blocked probe now persists into `mart_data_source_failure_queue`；2026-06-01 还新增 stage-opt candidate supply audit：在当前审计 slice（2023-01-01→2026-05-29，limit-stocks 50）上看到 `raw_signal_rows=1,381,657` / `filtered_signal_rows=733,083` / `unique_keys=120,273` / `ready_keys=57,986` / `48.21% ready coverage` / `62,287 below_min_signals` / `dropped_index_rows=1,355` / `dropped_unknown_stage_rows=647,219`，且 `codes_without_bars=0`；这次还修正了脚本结果里 `raw_signal_rows` 被 summary shadow 的报告 bug，所以 raw / filtered 现在分开显示；新增 blocked-reason breakdown 显示所有 blocked keys 都卡在 `below_min_signals`，其中 `macd_golden_cross` 和 stage 3/4 最弱；脚本默认 end 复用当前连接里的交易日历真相源，不再 nested `latest_closed_or_raise()` 新连接；LHB 侧最新只读核实显示 `raw_lhb_daily` 与 `fact_lhb_event` 都已到 `2026-05-29`，最新日 raw 94 rows / 84 codes、fact 84 rows / 84 codes，因此 LHB 是 source-sparse 事实，不是 ETL 落后；`audit_pit_coverage.py` 仍是 4/4 PASS，`fact_lhb_event` gain_20d coverage 83.9% > 60%，所以 PIT 安全性没问题 | Continue LHB event-coverage triage, recommendation PIT candidate-sparsity triage, and `need_027` source probe triage; no strategy claim |

## Latest Slice

Goal: keep the freshness tail moving from the truth source outward. This
session refreshed `mart_p0a_label_panel` (current `p0a_v3_horizon_governance`
version), `mart_p0a_feature_label_panel_v3`, `mart_p0a_feature_label_panel_v4`,
`mart_sniper_score_daily`, and `mart_institution_score_daily` to
`2026-05-29`, and aligned `audit_survivorship_gate.py` with the current label
version so the gate now passes. Remaining freshness blockers are the LHB event
coverage WARN and recommendation PIT WARN; `fact_lhb_event` and
`fact_technical_trigger` are now registered as sparse-event evidence in
`dim_data_asset`, so completeness no longer blocks on them. Recommendation PIT
was probed with a targeted `build_stage_opt_pit.py` smoke and a 2-stock
`optimize_per_stock_stage_strategy.py --min-signals 3` smoke; neither yielded a
useful PIT expansion, which means the gap is upstream candidate sparsity, not
just ranking order. This slice also added governance-reason columns to
`mart_daily_position_recommendation_pit_diagnostic` and reran
`build_daily_position_recommendations.py --date 2026-05-29`, so the latest
diagnostic rows now carry the latest governance rejection reason/count beside
the existing `stock_missing_pit` / `formula_missing_pit` reason. `need_027`
主力资金源仍 blocked/unknown；`akshare.stock_individual_fund_flow` /
`stock_individual_fund_flow_rank` / `stock_fund_flow_individual` capability 已登记，
其中 `stock_fund_flow_individual` 只是 10jqka 研究侧排行快照，不等同 exact flow；
`audit_tdx_data_need_coverage.py` 现在会把 blocked need summary 直接列出来，并输出
`preferred_source_capabilities` / `fallback_source_capabilities`，让当前 inventory
明确显示 `akshare` 有 exact-flow capability、`aif10` family 仍缺
`individual_fund_flow`；`probe_source_capability.py` 现在已先清代理再重试，但
Eastmoney 端点仍以 `ConnectionError` / `JSONDecodeError` remote disconnect 失败，
blocked probe now persists into `mart_data_source_failure_queue` so future triage
can resume from stored evidence. 2026-06-01 又对当前推荐的 7 个 stock code 以
cutoffs `2026-01-01,2026-05-19,2026-05-29` 重跑 `build_stage_opt_pit.py`，
结果 latest recommendation PIT coverage 仍然 0（8 total / 0 exact / 0 same_formula /
1 same_stock / 8 cross_stage），说明 exact stage × formula 的候选供给是结构性稀疏，
不是单次补表能解决。No GCP/Optuna/backtest work was started.
This slice additionally materialized `mart_stock_fund_flow_rank_snapshot_daily`
via `build_fund_flow_rank_snapshot_daily.py` and registered the builder test,
but it is explicitly research-side support only and does not change the
`need_027` exact-flow blocked status. 2026-06-01 还把 registry-side `lhb_daily` 对齐到与 `services.lhb_client` 一致的 date-bounded helper，所以 resolve/probe 不再走旧的 aif10 全历史假象；LHB 仍是 source-sparse completeness evidence，不是 exact-flow 生产证据。This slice also wired `data_health_snapshot.py` into `scripts/chunkyctl doctor --fast`, so startup health now fails closed on 32 red / 4 yellow / 341 total data-health tables before any business work can claim freshness.
The same session also dug into the early 2023 window: `build_signal_context.py`
successfully backfilled `233,939` rows for `2023-01-01→2023-09-11`, moving
`fact_signal_context` min date to `2023-07-05`, and `build_stage_formula_fitness.py`
needed `compute_start=2022-01-01` to backfill `427,436` early
`fact_stock_technical_stage` rows, moving its min date to `2023-01-13`.
Rerunning `audit_stage_opt_candidate_supply.py` after both backfills did not
change the `1,381,657 / 733,083 / 120,273 / 57,986 / 48.21% / 62,287`
candidate-supply metrics, so the remaining blocker is still upstream formula /
candidate density rather than the 2023 early window.
This slice also externalized `portfolio_sizer` short/mid/long thresholds into
`backend/config/portfolio_sizer_profiles.yaml`, and added
`backend/scripts/audit_portfolio_sizer_profile_attrition.py` /
`backend/services/portfolio_sizer/attrition.py` so future tuning stays
config-owned, evidence-gated, and auditable instead of hardcoded in
`profiles.py`. The latest attrition audit on 353 raw candidates still shows
short/mid/long selected_rows at 5/1/2, all cross-stage, with `hp` and
`wilson` as the dominant fail reasons; the new `fail_reasons_by_match_tier`
breakdown makes it explicit that `stage_pit` mostly dies on
`hp/n_signals/Wilson`, `stage_pit_formula_fallback` mostly on `hp/n_signals`,
and `cross_stage_fallback` mostly on `hp/wilson`; the new
`fail_holding_days_by_match_tier` shows the exact PIT `hp` failures are
concentrated on off-anchor holding_days 20/30/60/90. 2026-06-01 sensitivity
auditing (`base`, `hold+20`, `min_n_signals-2`, `min_wilson_win-0.05`) did not
change selected_rows, so the next useful tuning decision is upstream candidate
supply / formula coverage, not profile micro-adjustment. The new TDX need
audit now separates label vs family: `need_027`'s preferred `akshare` is
registered, while the declared fallback label `miaoxiang` resolves to the
registered `aif10` family but that adapter still lacks
`individual_fund_flow`, so the fallback remains conceptual until the
route/capability is explicit. This slice also backfilled
`fact_signal_context` from `2023-09-12` through `2024-03-05` with 552,126
rows and fixed `audit_stage_opt_candidate_supply.py` so `raw_signal_rows`
is no longer shadowed by filtered summary counters; the corrected audit now
reports `raw_signal_rows=1,381,657 / filtered_signal_rows=733,083 /
unique_keys=120,273 / ready_keys=57,986 / ready coverage=48.21% /
below_min_signals=62,287 / dropped_index_rows=1,355 /
dropped_unknown_stage_rows=647,219`, while `2024-03-06` 起的
`dropped_unknown_stage_rows` 降到 `454,158`; the remaining
`technical_stage='?'` mass is now mostly structural classifier warmup rather
than a fresh ETL outage.

K-line refresh commands used:

| Step | Command shape | Result |
|---|---|---|
| Initial catch-up | `build_price_kline_tdxhub.py --skip-existing --target-date 2026-05-29 --pages 1 --workers 8 --max-inflight 32` | 5,097 stocks success / 92 failed / 15,281 rows written |
| Retry stale actives | `build_price_kline_tdxhub.py --skip-existing --target-date 2026-05-29 --pages 1 --workers 4 --max-inflight 16 --per-stock-retry-attempts 2` | 93 stocks success / 0 failed / 275 rows written |

Alpha158 refresh command used:

| Step | Command shape | Result |
|---|---|---|
| Safe window catch-up | `build_alpha158_duck.py --start 2026-03-01 --write-start 2026-05-26 --end 2026-05-29` | `replace_window`; 20,736 rows / 5,185 codes / 4 dates written; table max now 2026-05-29 |

Downstream freshness refresh commands used:

| Step | Command shape | Result |
|---|---|---|
| Technical stage window | `build_stage_formula_fitness.py --start 2024-01-01 --write-start 2026-05-20 --end 2026-05-29 --stage-only` | 25,894 rows written; `fact_stock_technical_stage` max now 2026-05-29; fitness matrix intentionally skipped |
| Signal context window | `build_signal_context.py --start 2025-08-01 --write-start 2026-05-20 --end 2026-05-29` | 41,149 rows written; `fact_signal_context` max now 2026-05-29 |
| Technical trigger window | `build_formula_signals_history.py --start 2025-08-01 --write-start 2026-05-20 --end 2026-05-29` | 317,540 computed -> 17,950 window signals written; `fact_technical_trigger` max now 2026-05-29; horizon evidence intentionally skipped |
| Picture daily windows | `build_picture_daily.py --date 2026-05-27/28/29` | Each day wrote 5,203 stocks x 4 tables; `mart_stock_picture_daily` max now 2026-05-29 |
| Survey feature window | `build_survey_features.py --write-start 2026-05-21 --end 2026-05-29` | Read lookback from 2026-03-23; wrote 25,897 rows over 7 trading days; `mart_stock_survey_features` max now 2026-05-29 |

## Verified So Far

| Gate | Result |
|---|---|
| `git status --short` before K-line refresh | clean |
| `scripts/chunkyctl doctor --fast` before K-line refresh | PASS |
| `audit_test_tool_health.py --scope backend/scripts/build_price_kline_tdxhub.py --scope backend/tests/test_build_price_kline_tdxhub.py` | PASS |
| `pytest -q backend/tests/test_build_price_kline_tdxhub.py` | 30 passed |
| `complexity-optimizer backend/scripts/build_price_kline_tdxhub.py` | No obvious hotspots in targeted scan |
| `audit_data_completeness.py` after refresh | PASS with WARN: 0 FAIL / 2 WARN; `price_kline_tdxhub` OK at 2026-05-29 / 5,188 codes / `= cal` |
| K-line recent coverage query | 2026-05-26=5201, 05-27=5184, 05-28=5184, 05-29=5188; 16 codes latest < 2026-05-29 |
| `audit_end_to_end.py` after refresh | FAIL: 24 total / 18 OK / 2 WARN / 4 FAIL |
| `audit_test_tool_health.py --scope backend/scripts/build_alpha158_duck.py --scope backend/tests/scripts/test_build_alpha158_duck.py` | PASS |
| `pytest -q backend/tests/scripts/test_build_alpha158_duck.py` | 3 passed |
| `scripts/chunkyctl audit --run --scope backend/scripts/build_alpha158_duck.py --scope backend/tests/scripts/test_build_alpha158_duck.py` | PASS |
| `audit_data_completeness.py` after alpha158 refresh | PASS with WARN: 0 FAIL / 2 WARN; `fact_alpha158_panel` OK at 2026-05-29 / 5,183 codes / `= cal` |
| alpha158 recent coverage query | 2026-05-26=5185, 05-27=5184, 05-28=5184, 05-29=5183; duplicate count in refreshed window = 0 |
| `audit_test_tool_health.py --scope backend/scripts/build_formula_signals_history.py --scope backend/scripts/build_stage_formula_fitness.py --scope backend/scripts/build_signal_context.py --scope backend/tests/test_build_formula_signals.py` | PASS |
| `pytest -q backend/tests/test_build_formula_signals.py` | 19 passed |
| `scripts/chunkyctl audit --run --scope backend/scripts/build_formula_signals_history.py --scope backend/scripts/build_stage_formula_fitness.py --scope backend/scripts/build_signal_context.py --scope backend/tests/test_build_formula_signals.py` | PASS |
| Downstream coverage query | `fact_stock_technical_stage` max 2026-05-29 / 2,633,102 rows; `fact_signal_context` max 2026-05-29 / 3,519,654 rows; `fact_technical_trigger` max 2026-05-29 / 1,381,657 rows |
| `audit_data_completeness.py` after main-force catch-up | PASS with WARN: 0 FAIL / 2 WARN; `fact_lhb_event` and `fact_technical_trigger` are sparse-event evidence, not blockers |
| `audit_end_to_end.py` after trigger refresh | FAIL: 24 total / 18 OK / 4 WARN / 2 FAIL; FAIL now `mart_stock_picture_daily` and `mart_stock_survey_features` |
| `audit_universe_coverage.py` after trigger refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after trigger refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_test_tool_health.py --scope backend/scripts/build_picture_daily.py --scope backend/tests/test_build_picture_daily.py` | PASS |
| `pytest -q backend/tests/test_build_picture_daily.py` | 4 passed |
| `audit_test_tool_health.py --scope backend/scripts/build_survey_features.py --scope backend/tests/sentiment/test_build_survey_features_script.py --scope backend/tests/sentiment/test_survey_builder.py` | PASS |
| `pytest -q backend/tests/sentiment/test_build_survey_features_script.py backend/tests/sentiment/test_survey_builder.py` | 9 passed |
| `complexity-optimizer backend/scripts/build_survey_features.py` | No obvious hotspots in targeted scan |
| `audit_end_to_end.py` after picture/survey refresh | PASS with WARN: 24 total / 23 OK / 1 WARN / 0 FAIL |
| `audit_data_completeness.py` after main-force catch-up | PASS with WARN: 0 FAIL / 2 WARN; `fact_lhb_event` and `fact_technical_trigger` are sparse-event evidence, not blockers |
| `audit_universe_coverage.py` after picture/survey refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after picture/survey refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_survivorship_gate.py` after label/survivorship refresh | PASS: current label_version p0a_v3_horizon_governance has 5,210 codes >= 90% of ever-listed 5,210 |

## Next Actions

1. Review the data-health red tables bucket-by-bucket first; treat missing tables and stale writers as system blockers, not cosmetic warnings.
2. Finish review/gates for this label/survivorship freshness slice, then
   commit with `scripts/safe_commit.sh`; do not use raw `git commit`.
3. Confirm `git status --short`, `codegraph status .`, and
   `scripts/chunkyctl doctor --fast` return clean/PASS after the commit.
4. Continue `goal.md` 6.11 downstream freshness from the current state once the system health blockers are understood. The
  next true blocker is LHB event coverage, recommendation PIT candidate
  sparsity, and the `need_027` main-force source probe; keep
  `fact_technical_trigger` partial coverage as WARN evidence, not production
  proof. PIT-first ranking is already in place, but current candidate quality
  still yields only cross-stage fallback recommendations and the PIT table is
  still underfilled for current exact candidates. The new stage-opt candidate
  supply audit again points the next tuning lever at upstream candidate supply /
  formula coverage rather than profile knobs.

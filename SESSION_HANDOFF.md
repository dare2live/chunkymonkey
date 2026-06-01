# SESSION HANDOFF

Manual Codex checkpoint. Current operating state lives in `goal.md`; durable
startup rules live in `AGENTS.md` and `docs/chunkyctl_session_quickstart.md`.
This file is a short recovery note, not a replacement for those authorities.

Snapshot: `2026-06-01 08:56:55 CST`

## Risk First

| Item | State | Action |
|---|---|---|
| Worktree | Long-lived dirty is clean; K-line refresh wrote ignored DuckDB data and did not create tracked dirty before this state-doc update | If dirty, run `scripts/chunkyctl worktree --format markdown` and resolve by bucket; never use `git add .` |
| GCP | No GCP work started in this Codex slice | Keep stopped unless a scoped, approved cloud objective exists |
| Optuna/backtest | Not running | Do not resume until architecture/data gates allow it |
| Storage payload | `PASS`: 320 scanned / 0 FAIL / 0 WARN / 11 reviewed PASS | Reviewed columns are governed by `backend/config/storage_retention.yaml`; recursive or over-cap payloads still block |
| CodeGraph | Synced after the survey `.py` slice; pending may show the new untracked test until this slice is staged/committed | Re-run `codegraph status .` after this commit |
| Complexity | Historical HIGH remains debt; tooling diff ignores line-number drift by default | New HIGH still blocks; line drift alone should not |
| Data freshness/PIT | **WARN/MIXED but non-blocking**: end-to-end freshness PASS with WARN; data completeness PASS with WARN (0 FAIL / 2 WARN, both sparse-event evidence); survivorship gate PASS on current label_version, legacy v2 only via explicit flag；`rank_and_size()` 已改为 PIT-tier-first，但当前推荐仍全是 `cross_stage_fallback`，因为 2026-05-29 的 PIT exact 候选多数卡在 `hp/n_signals/Wilson` 门槛，coverage 0 是候选稀疏/阈值问题；targeted backfill only moved latest cutoff from 3 rows to 4；`mart_daily_position_recommendation_pit_diagnostic` 现在带 `governance_reject_count` / latest reason / latest rejected_at，方便直接看每个 `stock_missing_pit` 的治理根因；`need_027` 主力资金源仍 blocked/unknown；`akshare.stock_individual_fund_flow` / `stock_individual_fund_flow_rank` capability 已登记，但现场 `probe_source_capability.py` 仍因 `ProxyError` 阻断，blocked probe now persists into `mart_data_source_failure_queue` | Continue LHB event-coverage triage, recommendation PIT candidate-sparsity triage, and `need_027` source probe triage; no strategy claim |

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
`stock_individual_fund_flow_rank` capability 已登记，但
`probe_source_capability.py` 现场探针仍因 `ProxyError` 阻断，blocked probe now persists into `mart_data_source_failure_queue` so future triage can resume from stored evidence. 2026-06-01 又对当前推荐的 7 个 stock code 以 cutoffs `2026-01-01,2026-05-19,2026-05-29` 重跑 `build_stage_opt_pit.py`，结果 latest recommendation PIT coverage 仍然 0（8 total / 0 exact / 0 same_formula / 1 same_stock / 8 cross_stage），说明 exact stage × formula 的候选供给是结构性稀疏，不是单次补表能解决。No GCP/Optuna/backtest work was started.

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
| Downstream coverage query | `fact_stock_technical_stage` max 2026-05-29 / 1,429,117 rows; `fact_signal_context` max 2026-05-29 / 2,125,233 rows; `fact_technical_trigger` max 2026-05-29 / 1,381,657 rows |
| `audit_data_completeness.py` after main-force catch-up | PASS with WARN: 0 FAIL / 2 WARN; `fact_lhb_event` and `fact_technical_trigger` are sparse-event evidence, not blockers |
| `audit_end_to_end.py` after trigger refresh | FAIL: 24 total / 18 OK / 4 WARN / 2 FAIL; FAIL now `mart_stock_picture_daily` and `mart_stock_survey_features` |
| `audit_universe_coverage.py` after trigger refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after trigger refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_test_tool_health.py --scope backend/scripts/build_picture_daily.py --scope backend/tests/test_build_picture_daily.py` | PASS |
| `pytest -q backend/tests/test_build_picture_daily.py` | 4 passed |
| `audit_test_tool_health.py --scope backend/scripts/build_survey_features.py --scope backend/tests/sentiment/test_build_survey_features_script.py --scope backend/tests/sentiment/test_survey_builder.py` | PASS |
| `pytest -q backend/tests/sentiment/test_build_survey_features_script.py backend/tests/sentiment/test_survey_builder.py` | 9 passed |
| `complexity-optimizer backend/scripts/build_survey_features.py` | No obvious hotspots in targeted scan |
| `audit_end_to_end.py` after picture/survey refresh | PASS with WARN: 24 total / 19 OK / 5 WARN / 0 FAIL |
| `audit_data_completeness.py` after main-force catch-up | PASS with WARN: 0 FAIL / 2 WARN; `fact_lhb_event` and `fact_technical_trigger` are sparse-event evidence, not blockers |
| `audit_universe_coverage.py` after picture/survey refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after picture/survey refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_survivorship_gate.py` after label/survivorship refresh | PASS: current label_version p0a_v3_horizon_governance has 5,210 codes >= 90% of ever-listed 5,210 |

## Next Actions

1. Finish review/gates for this label/survivorship freshness slice, then
   commit with `scripts/safe_commit.sh`; do not use raw `git commit`.
2. Confirm `git status --short`, `codegraph status .`, and
   `scripts/chunkyctl doctor --fast` return clean/PASS after the commit.
3. Continue `goal.md` 6.11 downstream freshness from the current state. The
  next true blocker is LHB event coverage, recommendation PIT candidate
  sparsity, and the `need_027` main-force source probe; keep
  `fact_technical_trigger` partial coverage as WARN evidence, not production
  proof. PIT-first ranking is already in place, but current candidate quality
  still yields only cross-stage fallback recommendations and the PIT table is
  still underfilled for current exact candidates.

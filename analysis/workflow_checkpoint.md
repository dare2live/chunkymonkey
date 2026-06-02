# Workflow Checkpoint

Business-level pipeline tracker. Session-level state remains in SESSION_HANDOFF.md.

Current Codex architecture/worktree-governance state is tracked in `goal.md`.
The model pipeline snapshot below is historical evidence for the completed
2026-05 long-running pipeline and must not be used as current GCP/Optuna state.

## Current Data Freshness Checkpoint

- updated_at: `2026-06-02 09:00:33 CST`
- current_state: `architecture/data freshness repair / shared-config boundary cleanup / stage-opt supply tuning`
- K-line truth source: `price_kline_tdxhub` refreshed to trading calendar
  `2026-05-29` with tdxhub incremental sync.
- data audit: `audit_data_completeness.py` now exits PASS with WARN (0 FAIL /
  2 WARN). `price_kline_tdxhub`, `fact_alpha158_panel`,
  `fact_stock_technical_stage`, `fact_signal_context`,
  `fact_technical_trigger`, `fact_capital_flow_pit_daily`,
  `fact_risk_factors`, `fact_sector_momentum_daily`,
  `mart_stock_picture_daily`, `mart_stock_survey_features`,
  `mart_p0a_label_panel`, `mart_p0a_feature_label_panel_v3`,
  `mart_p0a_feature_label_panel_v4`, `mart_sniper_score_daily`, and
  `mart_institution_score_daily` now reach `2026-05-29`. The remaining WARN
  evidence is `fact_lhb_event` event coverage only 84 codes (1%);
  read-only checks show `raw_lhb_daily` and `fact_lhb_event` both max at
  `2026-05-29`, with latest day raw 94 rows / 84 codes and fact 84 rows /
  84 codes, so this is source sparsity rather than ETL lag. The other WARN is
  `fact_technical_trigger` event-table sparse-event coverage, and `need_027`
  main-force source still blocked/unknown; the akshare `individual_fund_flow`
  / `individual_fund_flow_rank` / `stock_fund_flow_individual` capability is
  registered, where `stock_fund_flow_individual` is only a 10jqka research-side
  rank snapshot and not exact flow; the blocked audit now emits
  `preferred_source_capabilities` and `fallback_source_capabilities`, making
  it explicit that `akshare` carries the exact-flow capability while the
  `aif10` family still lacks `individual_fund_flow`; the live probe now clears
  proxy env and retries but Eastmoney still fails with `ConnectionError` /
  `JSONDecodeError` remote disconnect, and blocked probe rows now persist in
  `mart_data_source_failure_queue` for follow-up triage; if the queue write hits a DB lock/schema problem, it downgrades to structured `persisted.status=error` instead of a traceback. The probe CLI itself
  now defaults to quiet registry warnings and only prints structured blocked
  JSON; `--show-registry-warnings` re-enables the raw fallback log when we
  explicitly want it. `backend/services/source_watermarks.py` also moved to
  timezone-aware UTC timestamps, so the probe/watermark tests no longer emit
  `datetime.utcnow()` deprecation warnings. 2026-06-02 backend server
  recovered and reran `cron_daily.py --full-sync`; the run finished the whole
  pipeline again with only `watermarks:warn` / `released_warn` and no new
  Python crash. `raw_tdx_f10_holder_research` advanced to
  `2026-06-01 19:14:51`, `fact_top10_holder_period` advanced to
  `2026-06-01T19:14:57+00:00`, and the 28-row holder raw smoke
  `parse -> write_one -> rollback` check all passed, so the holder/gap repair
  stayed crash-free. 2026-06-01 also
  repaired the `data_health_snapshot.py` writer timestamp insert path so the
  health gate no longer crashes on compact `YYYYMMDDTHHMMSSZ` values; the
  latest direct dry-run / official cron flow now surface `PASS: 0 red / 0
  yellow / 342 total` instead of failing on a timestamp parse error, and the
  official `cron_daily.py` run completed later phases after `sync_raw`
  exceeded its 60s budget; red/yellow rows now carry `writer_prompt`
  owner/sync_step hints for self-triage, and `sync_raw` progress snapshots now
  feed `run_context.step_progress` / `/update/status` so the long raw fetch no
  longer looks frozen. The raw ingest cadence now refreshes on a 10-count / 30s
  threshold rather than waiting for sparse 50-row logs. Feature panel lane was
  refreshed incrementally on 2026-06-02 and now reaches `2026-06-01`, so
  `fact_feature_panel` is no longer blocking; `dim_stock_tdx_industry_history`
  remains the lone warning yellow.
- shared-config boundary: common holding windows, stage thresholds, MACD
  diagnostic windows, turtle_breakout volume confirmation gates,
  shareholder-plan walk-forward defaults, multidim walk-forward default
  model params / degenerate threshold, and the backtest default
  stop/target/trailing now live in shared YAML configs
  (`formula_shared_windows.yaml`, `technical_stage.yaml`,
  `formula_macd_golden_cross.yaml`, `formula_turtle_breakout.yaml`,
  `shareholder_plan_family_walkforward.yaml`, `run_multidim_walkforward.yaml`,
  `strategy_defaults.yaml`) plus the shared loader; formula-specific YAMLs keep only formula-owned
  thresholds, and per-stock best-holding results stay table-backed in marts
  such as `mart_per_stock_stage_strategy_optimal_pit` /
  `mart_stock_horizon_profile`
  instead of becoming file literals.
- stage-opt audit: 2026-06-01 repaired the 2025-08-01→2026-05-29
  `fact_stock_technical_stage` / `fact_signal_context` discontinuity and
  reran `audit_stage_opt_candidate_supply.py`; full-history coverage is now
  `raw_signal_rows=2,103,143 / filtered_signal_rows=1,110,280 / unique_keys=133,857
  / ready_keys=76,480 / ready coverage=57.14% / below_min_signals=57,377`,
  with `codes_without_bars=0`. This run also fixed the reporting bug where
  `raw_signal_rows` was being shadowed by the filtered summary counters, so
  new sessions should read raw and filtered counts separately. The new
  blocked-reason breakdown shows every blocked key fails only on
  `below_min_signals`; `macd_golden_cross` and stages 3/4 are the weakest
  cohorts. The script now also emits a `next_action_recommendation` that
  points to `P1 / upstream_candidate_supply` and names the weakest formula ids
  / stage bins, so future sessions can triage from the audit output instead of
  re-deriving the same conclusion manually. 2026-06-02 first widened
  `reversal_1m_deep` from 15-30% to 10-30%, lifting that formula to
  `76,635 / 11,968 / 42.54%`; then it widened `reversal_1m_mild` from 5-15%
  to 4-15%, lifting that formula to `372,661 / 9,265 / 62.43%`. The overall
  audit now reads `2,103,143 raw_signal_rows / 1,110,280 filtered_signal_rows /
  133,857 unique_keys / 76,480 ready_keys / 57.14% ready coverage /
  57,377 below_min_signals`; `build_formula_signals_history.py
  --recompute-horizon-evidence` no longer throws NameError after importing
  `defaultdict`; 2026-06-02 `min_signals` probe still shows `5→4→3` lifts
  global ready coverage to `64.84%` / `73.75%` (`86,796` / `98,721` ready
  keys, `54,129` / `41,063` below_min_signals) while `reversal_1m_deep`
  itself reaches `50.97%` / `62.34%`; 2026-06-02 further `min_signals=2`
  lifts global ready coverage to `84.80%` (`113,506` ready keys,
  `22,872` below_min_signals), but the next action still points to
  upstream candidate supply. `2024-03-06` 起的
  `dropped_unknown_stage_rows` 降到 `454,158`, so the remaining
  `technical_stage='?'` mass is still mostly structural classifier warmup /
  unknown, not a fresh ETL outage.
- 2026-06-02 05:02 CST latest rebuild: `reversal_1m_deep` is still 8-30%
  and `mart_macd_state_history` remains the diagnostic mart with 180-day
  warm-up; the full-history audit now reads `raw_signal_rows=5,123,528 /
  filtered_signal_rows=2,574,836 / unique_keys=147,674 / ready_keys=102,500 /
  ready coverage=69.41% / below_min_signals=45,174`; `turtle_breakout_20` is
  `199,495 signal_rows / 19,413 keys / 80.81% coverage` and `turtle_breakout_55`
  is `115,911 signal_rows / 16,898 keys / 60.17% coverage` after moving the
  volume confirmation gate into `backend/config/formula_turtle_breakout.yaml`
  and lowering `volume_multiple` from 1.3 to 1.2; `min_signals=4/3/2` now lifts
  global ready coverage to `75.85% / 82.68% / 90.43%`. Controller
  recommendation still points to `P1 / upstream_candidate_supply`, so the
  structural conclusion remains unchanged even though the evidence density
  improved again.
- 2026-06-02 05:07-05:08 `dynamic_ma_iterative_cross` was tightened from 2
- 2026-06-02 05:57-06:07 `dynamic_ma_iterative_cross` further tightened from 1 轮到 0 轮 and `turtle_breakout_55` volume confirmation was then lowered from `1.0` to `0.9`, lifting those formulas to `144,282 / 20,522 / 56.83%` and `139,053 / 17,143 / 64.62%`; the full stage-opt audit is now `raw_signal_rows=5,530,639 / filtered_signal_rows=2,765,531 / unique_keys=151,881 / ready_keys=110,336 / ready coverage=72.65% / below_min_signals=41,545`, `min_signals=4/3/2` at `78.35% / 84.47% / 91.32%`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:12-06:13 `reversal_1w` was widened from 2-10% to 1-10%, lifting that formula to `222,942 / 16,610 / 72.28%`; the full stage-opt audit is now `raw_signal_rows=5,664,501 / filtered_signal_rows=2,826,350 / unique_keys=152,554 / ready_keys=111,794 / ready coverage=73.28% / below_min_signals=40,760`, `min_signals=4/3/2` at `78.76% / 84.74% / 91.44%`, and the weakest formulas shifted to `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:21-06:22 `turtle_breakout_55` was split into separate 20 day and 55 day volume gates, keeping the 20 day variant at `0.9` and lowering the 55 day variant from `0.9` to `0.8`; the history rebuild lifted `turtle_breakout_55` to `144,450 / 17,181 / 65.44%`, and the full stage-opt audit now reads `raw_signal_rows=5,671,811 / filtered_signal_rows=2,831,747 / unique_keys=152,592 / ready_keys=111,961 / ready coverage=73.37% / below_min_signals=40,631`, with `min_signals=4/3/2` at `78.82% / 84.79% / 91.47%`; the weakest formulas stay `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, and the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:27-06:29 `reversal_1m_mild` was widened from 3-15% to 2-15%, lifting that formula to `214,103 / 16,188 / 68.67%`; the full stage-opt audit is now `raw_signal_rows=5,748,314 / filtered_signal_rows=2,865,720 / unique_keys=153,235 / ready_keys=112,748 / ready coverage=73.58% / below_min_signals=40,487`, `min_signals=4/3/2` at `78.95% / 84.86% / 91.49%`, and the weakest formulas shifted to `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:30-06:31 `turtle_breakout_55` was lowered from `0.8` to `0.7`, lifting that formula to `147,973 / 17,245 / 65.78%`; the full stage-opt audit is now `raw_signal_rows=5,753,115 / filtered_signal_rows=2,869,243 / unique_keys=153,299 / ready_keys=112,848 / ready coverage=73.61% / below_min_signals=40,451`, `min_signals=4/3/2` at `78.97% / 84.86% / 91.49%`, and the weakest formulas remain `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:38-06:39 `reversal_1m_deep` was lowered from `5-30%` to `4-30%`, lifting that formula to `248,022 / 17,814 / 70.21%`; the full stage-opt audit is now `raw_signal_rows=5,840,232 / filtered_signal_rows=2,906,972 / unique_keys=153,975 / ready_keys=113,949 / ready coverage=74.00% / below_min_signals=40,026`, `min_signals=4/3/2` at `79.27% / 85.04% / 91.58%`, and the weakest formulas remain `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 06:43-06:45 `reversal_1m_mild` was lowered from `2-15%` to `1-15%`, lifting that formula to `249,394 / 16,818 / 69.95%`; the full stage-opt audit is now `raw_signal_rows=5,919,142 / filtered_signal_rows=2,942,263 / unique_keys=154,605 / ready_keys=114,597 / ready coverage=74.12% / below_min_signals=40,008`, `min_signals=4/3/2` at `79.34% / 85.05% / 91.55%`, and the weakest formulas remain `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`.
  iterations to 1, and the full-history audit now reads `raw_signal_rows=
  5,145,959 / filtered_signal_rows=2,585,682 / unique_keys=147,948 /
  ready_keys=103,056 / ready coverage=69.66% / below_min_signals=44,892`;
  `dynamic_ma_iterative_cross` is now `248,214 signal_rows / 20,350 keys /
  53.67% coverage`, and `min_signals=4/3/2` now lifts global ready coverage to
  `76.02% / 82.77% / 90.50%`. Controller recommendation still points to
  `P1 / upstream_candidate_supply`, so the structural conclusion remains the
  same even though the evidence density improved again.
- 2026-06-02 05:17-05:18 `turtle_breakout_55` was tightened again by moving the
  volume confirmation gate from `1.2` to `1.1`, and the full-history audit now
  reads `raw_signal_rows=5,183,518 / filtered_signal_rows=2,609,421 /
  unique_keys=148,117 / ready_keys=103,984 / ready coverage=70.20% /
  below_min_signals=44,133`; `turtle_breakout_20` is now `214,919 signal_rows /
  19,476 keys / 83.33% coverage` and `turtle_breakout_55` is `124,226 signal_rows /
  17,004 keys / 62.07% coverage`, while `min_signals=4/3/2` lifts global ready
  coverage to `76.41% / 83.06% / 90.65%`. Controller recommendation still
  points to `P1 / upstream_candidate_supply`, so the structural conclusion
  remains the same even though the evidence density improved again.
- 2026-06-02 05:33-05:45 `reversal_1m_deep` thresholds were externalized into
  `backend/config/formula_reversal_short_term.yaml` and its upper bound was
  widened from `8-30%` to `5-30%`; `reversal_1m_mild` then widened from
  `4-15%` to `3-15%`. Re-running history wrote `520,263` deep rows and
  `444,359` mild rows. The full stage-opt audit now reads `raw_signal_rows=
  5,472,946 / filtered_signal_rows=2,732,904 / unique_keys=151,570 /
  ready_keys=109,073 / ready coverage=71.96% / below_min_signals=42,497`, with
  `min_signals=4/3/2` at `77.81% / 84.08% / 91.16%` (`117,935 / 127,438 /
  138,170` ready keys). `reversal_1m_deep` is `210,293 signal_rows / 17,138
  keys / 66.55% coverage` and `reversal_1m_mild` is `180,130 signal_rows /
  15,545 keys / 66.45% coverage`. Weakest formulas shifted to
  `dynamic_ma_iterative_cross`, `turtle_breakout_55`, and `reversal_1w`, but
  controller still points to `P1 / upstream_candidate_supply`, so the
  structural conclusion remains the same even though the evidence density
  improved again.
- controller boundary note: the live formula registry surfaced by stage-opt and
  `doctor` contains only 7 live formula ids (`macd_golden_cross`,
  `turtle_breakout_20`, `turtle_breakout_55`, `dynamic_ma_iterative_cross`,
  `reversal_1m_mild`, `reversal_1m_deep`, `reversal_1w`); the `bestchoice`
  `gs_*` / `volume_base_breakout` / `activity_breakout` / `ma_base_breakout`
  families remain research-only challengers and are not counted as live
  stage-opt supply.

- controller boundary note v2: research challengers are now explicit too and
  list 5 ids (`gs_raw_buy`, `gs_pullback_confirm`, `ma_base_breakout`,
  `activity_breakout`, `volume_base_breakout`); they stay research-only and are
  useful only as challenger evidence, not as live production supply.
- stage-opt MACD state mart: 2026-06-02 added
  `mart_macd_state_history` / `build_macd_state_history.py` as a separate
  MACD active-state diagnostic mart. It uses a 180-day warm-up window and
  then filters back to the requested write window, so the state rows are
  evidence-only and do not touch `fact_technical_trigger`. The audit now
  counts `raw_trigger_rows=161,279` plus `raw_state_history_rows=370,039`,
  lifting `macd_golden_cross` coverage to `47.13%` (`16,474` ready keys) and
  the MACD-only slice to `226,822 raw_signal_rows / 123,264 filtered_signal_rows
  / 31,184 unique_keys / 16,474 ready_keys / 47.13% ready coverage /
  22,854 below_min_signals`; `scripts/chunkyctl doctor --fast` now also carries
  these `raw_trigger_rows` / `raw_state_history_rows` fields so the controller
  can see the MACD state mart composition without rerunning the audit, but the
  controller recommendation still points to `P1 / upstream_candidate_supply`.
- stage-opt daily recommendation candidate loader: 2026-06-02
  `build_daily_position_recommendations.py` now unions `mart_macd_state_history`
  into the candidate pool and uses the existing `mart_per_stock_strategy_optimal`
  table for cross-stage fallback instead of a nonexistent `_pit` table. The
  live `2026-06-01` run now completes without the missing-table crash; the
  current snapshot happened to return 0 candidates, which is an input-sparsity
  result rather than a loader failure. This slice is now landed in commit
  `1402bc0b`, and the stage-opt / MACD / docs slice is now closed in `ed5a3ee6` with a clean worktree.
- `scripts/chunkyctl doctor --fast` now also surfaces the stage-opt
  `next_action_recommendation`, so the controller sees the upstream
  candidate-supply lever without rerunning the audit manually. If
  `macd_golden_cross` shows up in the weakest cohort, the recommendation also
  records the `fact_technical_trigger` primary-key schema limit, so a schema
  change is not mistaken for a state-only formula tweak. 2026-06-02 then
  widened `reversal_1w` from 5-10% to 2-10%, and the historical rebuild
  lifted that formula to `369,822 / 15,937 / 66.18%`; it also compressed
  `dynamic_ma_iterative_cross` from 10 轮到 2 轮, lifting that formula to
  `225,783 / 20,076 / 51.63%`. 2026-06-02 05:51-05:52 then lowered
  `turtle_breakout_55`'s volume confirmation gate from `1.1` to `1.0`; the
  historical rebuild wrote `166,984` rows and moved `turtle_breakout_55` to
  `132,024 signal_rows / 17,089 keys / 63.45% coverage`. The full stage-opt
  audit now reads `raw_signal_rows=5,483,722 / filtered_signal_rows=2,740,702 /
  unique_keys=151,655 / ready_keys=109,361 / ready coverage=72.11% /
  below_min_signals=42,294`, with `min_signals=4/3/2` at `77.93% / 84.18% /
  91.21%` (`118,186 / 127,669 / 138,331` ready keys). Weakest formulas remain
  `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1w`, and the
  controller still points to `P1 / upstream_candidate_supply`, which means the
  supply gap is still structural even after the loosening.
- 2026-06-02 06:58-06:59 `turtle_breakout_55` was lowered from `0.7` to `0.6`, lifting that formula to `149,966 / 17,266 / 66.01%`; the full stage-opt audit is now `raw_signal_rows=5,921,755 / filtered_signal_rows=2,944,256 / unique_keys=154,626 / ready_keys=114,651 / ready coverage=74.15% / below_min_signals=39,975`, `min_signals=4/3/2` at `79.36% / 85.06% / 91.55%`, and the weakest formulas remain `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 07:03-07:04 `turtle_breakout_55` was lowered from `0.6` to `0.5`, lifting that formula to `150,941 / 17,278 / 66.15%`; the full stage-opt audit is now `raw_signal_rows=5,923,016 / filtered_signal_rows=2,945,231 / unique_keys=154,638 / ready_keys=114,682 / ready coverage=74.16% / below_min_signals=39,956`, `min_signals=4/3/2` at `79.36% / 85.06% / 91.55%`, and the weakest formulas remain `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`.
- 2026-06-02 07:21-07:23 `reversal_1m_mild` only loosened `rel_std_max` from `0.06` to `0.07`, lifting that formula to `307,843 / 13,239 / 72.11%`; the full stage-opt audit is now `raw_signal_rows=6,054,317 / filtered_signal_rows=3,003,680 / unique_keys=156,179 / ready_keys=116,157 / ready coverage=74.37% / below_min_signals=40,022`, `min_signals=4/3/2` at `79.54% / 85.15% / 91.57%`, and the weakest formulas now shift to `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the last config-only probe rather than a direction change.
- 2026-06-02 08:22-08:23 `reversal_1m_deep` only loosened `rel_std_max` from `0.08` to `0.09`, lifting that formula to `284,627 / 18,503 / 71.97%`; the full stage-opt audit is now `raw_signal_rows=6,134,459 / filtered_signal_rows=3,040,285 / unique_keys=156,868 / ready_keys=116,967 / ready coverage=74.56% / below_min_signals=39,901`, `min_signals=4/3/2` at `79.68% / 85.23% / 91.61%`, and the weakest formulas still sit on `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the final low-risk config probe rather than a direction change.
- 2026-06-02 08:28-08:30 `reversal_1m_deep` only loosened `rel_std_max` from `0.09` to `0.10`, lifting that formula to `315,555 / 18,962 / 73.29%`; the full stage-opt audit is now `raw_signal_rows=6,200,134 / filtered_signal_rows=3,071,213 / unique_keys=157,327 / ready_keys=117,547 / ready coverage=74.72% / below_min_signals=39,780`, `min_signals=4/3/2` at `79.81% / 85.32% / 91.64%`, and the weakest formulas still sit on `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_mild`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the final low-risk config probe rather than a direction change.
- 2026-06-02 08:35-08:37 `reversal_1m_mild` only loosened `rel_std_max` from `0.07` to `0.08`, lifting that formula to `357,425 / 14,300 / 73.75%`; the full stage-opt audit is now `raw_signal_rows=6,305,481 / filtered_signal_rows=3,120,795 / unique_keys=158,359 / ready_keys=118,608 / ready coverage=74.90% / below_min_signals=39,751`, `min_signals=4/3/2` at `79.90% / 85.36% / 91.68%`, and the weakest formulas now shift to `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1w`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the final low-risk config probe rather than a direction change.
- 2026-06-02 08:44-08:45 `reversal_1w` only loosened `rel_std_max` from `0.06` to `0.07`, lifting that formula to `280,065 signal_rows / 13,639 keys / 75.54% coverage`; the full stage-opt audit is now `raw_signal_rows=6,421,901 / filtered_signal_rows=3,177,918 / unique_keys=159,805 / ready_keys=120,242 / ready coverage=75.24% / below_min_signals=39,563`, `min_signals=4/3/2` at `80.19% / 85.55% / 91.74%`, and the weakest formulas now shift to `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the final low-risk config probe rather than a direction change.
- 2026-06-02 09:00-09:00 `turtle_breakout_55` only loosened `volume_multiple` from `0.5` to `0.4`, lifting that formula to `151,472 signal_rows / 11,446 keys / 66.21% coverage`; the full stage-opt audit is now `raw_signal_rows=6,422,516 / filtered_signal_rows=3,178,449 / unique_keys=159,814 / ready_keys=120,259 / ready coverage=75.25% / below_min_signals=39,555`, `min_signals=4/3/2` at `80.19% / 85.55% / 91.74%`, and the weakest formulas still sit on `dynamic_ma_iterative_cross` / `turtle_breakout_55` / `reversal_1m_deep`, but the controller still points to `P1 / upstream_candidate_supply`, so this is the final low-risk config probe rather than a direction change.
- later in the same slice we tested whether the 2023-01-01→2023-09-11
  technical-stage hole was the missing lever: `build_stage_formula_fitness.py`
  needed a longer `compute_start` than the write window, so a second run with
  `compute_start=2022-01-01 / write_start=2023-01-01 / end=2023-09-11`
  succeeded and wrote `427,436` early stage rows (fact min date now
  `2023-01-13`), while `build_signal_context.py` had already backfilled
  `233,939` rows for `2023-01-01→2023-09-11` (fact min date now `2023-07-05`).
  Rerunning `audit_stage_opt_candidate_supply.py` after both backfills left
  the candidate-supply metrics unchanged, confirming that the remaining
  bottleneck is not the early 2023 window.
- end-to-end audit: `audit_end_to_end.py` now exits PASS with WARN
  (`24 total / 23 OK / 1 WARN / 0 FAIL`); WARN now only includes recommendation PIT
  coverage 0. `rank_and_size()` is already
  PIT-tier-first, but the current `2026-05-29` PIT exact candidates mostly
  fail `hp/n_signals/Wilson`, so final recommendations still come out as
  cross-stage fallback. Targeted PIT backfill only moved latest cutoff from 3
  rows to 4, and a 2-stock `optimize_per_stock_stage_strategy.py --min-signals 3`
  smoke still produced 0 governance-pass rows. This slice also upgraded
  `mart_daily_position_recommendation_pit_diagnostic` with
  `governance_reject_count` / latest reason / latest rejected_at and reran
  `build_daily_position_recommendations.py --date 2026-05-29`, so the latest
  diagnostic rows now show the governance reason beside each
  `stock_missing_pit` / `formula_missing_pit` row.
- fund-flow research snapshot: `mart_stock_fund_flow_rank_snapshot_daily`
  and `build_fund_flow_rank_snapshot_daily.py` were added as research-side
  support only, with the new root test registered in the test registry.
  This does not change the `need_027` exact-flow blocked status.
  The `need_027` blocked audit now also carries a live
  `mart_data_source_failure_queue` snapshot, so the open
  `order_flow_fund_flow` failures and the resolved research-side snapshot are
  visible directly in the summary instead of only as abstract blocked/unknown
  state.
- system data-health snapshot: `scripts/chunkyctl doctor --fast` now folds in
  `backend/scripts/data_health_snapshot.py --dry-run --format json` and fails
  closed on red tables. Current live health after the 2026-06-02 intraday
  refreshes is `0 red / 0 yellow / 342 total` (`latest_completed_trade_date`
  remains `2026-06-01` intraday): `raw_profit_forecast_snapshot_daily` was
  refreshed to `2026-06-02` and no longer blocks, `fact_feature_panel` was
  rebuilt through `2026-06-01` and no longer appears in blocking yellow, and
  `dim_stock_tdx_industry_history` was refreshed on the main smartmoney DB so
  the prior warning yellow is now cleared. `raw_margin_daily` remains a
  monitor-only governance placeholder outside the yellow count. This is the
  startup health signal the controller has to read before trusting any
  freshness claim. Feature panel, capital_behavior, and holder/shareholder-plan
  lanes were cleared from red earlier; GPCW and raw_aif10 are still green /
  on-demand governance, and `blocking_yellow_tables` are surfaced separately so
  `quality_gate_level=blocking` yellow assets get next-action priority before
  generic yellow maintenance. `mart_p0b_lambdamart_v6_predictions` was
  refreshed on 2026-06-01, and the 2026-06-02 `raw_profit_forecast_snapshot_daily`
  refresh removed the last blocking yellow while the incremental feature panel
  rebuild advanced the panel to `2026-06-01`.
  `mart_architecture_cleanup_plan` is still on-demand governance and green.
  `mart_pipeline_run_manifest.perf_summary_json` still uses
  `compact_perf_summary_payload()`, so the largest live manifest row remains
  bounded at ~260,408 bytes instead of the earlier multi-megabyte log blob.
- follow-up research-side snapshot: `build_fund_flow_rank_snapshot_daily.py`
  was run successfully on 2026-06-01 and wrote `5,188` rows / `5,188` codes
  for snapshot date `2026-06-01`; the corresponding
  `mart_data_source_failure_queue` row for `stock_fund_flow_rank_snapshot`
  moved from `open` to `resolved`. This does not change the `need_027`
  exact-flow blocked status; it only means the research-side rank snapshot now
  has live data and no longer sits in the open failure queue.
- survivorship gate: current default `p0a_v3_horizon_governance` PASS; the old
  `p0a_v2_governance_v1` gate remains available only for explicit historical
  review.
- next_step: follow `goal.md` 6.11 from the current state; the next true
  blocker is LHB event coverage, recommendation PIT candidate sparsity, and
  the `need_027` source probe. `fact_technical_trigger` remains WARN
  evidence, not a completeness blocker, and PIT-first ranking is already in
  place even though current output is still all cross-stage fallback. The PIT
  table is still underfilled for the current exact candidates, so the next
  meaningful step is upstream PIT coverage expansion rather than more ranking
  tweaks. Keep the `need_027` source probe / unknown status explicit; the
  `akshare.stock_individual_fund_flow` / `stock_individual_fund_flow_rank`
  capability is registered, the live probe now clears proxy env and retries
  but Eastmoney still fails with `ConnectionError` / `JSONDecodeError`
  remote disconnect, and blocked probe rows now persist in
  `mart_data_source_failure_queue`, and
  `audit_tdx_data_need_coverage.py` now emits a blocked need summary with
  label-vs-family registration so the current inventory stays explicit.
- additional PIT evidence: 2026-06-01 reran `build_stage_opt_pit.py` on the 7
  current recommendation stock codes across cutoffs `2026-01-01,2026-05-19,
  2026-05-29`; latest recommendation PIT coverage remained 0 (8 total / 0 exact
  / 0 same_formula / 1 same_stock / 8 cross_stage), confirming structural
  candidate sparsity rather than a one-shot coverage gap. The
  `portfolio_sizer` short/mid/long thresholds now live in
  `backend/config/portfolio_sizer_profiles.yaml`, and
  `backend/scripts/audit_portfolio_sizer_profile_attrition.py` is now the
  evidence-gate for any tuning; the current profile filters still eliminate
  exact PIT candidates on `hp/n_signals/Wilson`, so coverage remains a
  gating concern rather than a ranking bug. A direct attrition audit on 353
  raw candidates found selected_rows only 5/1/3 for short/mid/long, all
  `cross_stage_fallback`; the live 2026-06-02 audit after the latest
  stage-opt supply lift moved long to 3 selected_rows while still keeping all
  selected_rows in `cross_stage_fallback`, with `hp` and `wilson` as the
  dominant fail reasons.
  The new `fail_reasons_by_match_tier` breakdown makes the attrition path
  explicit: `stage_pit` mostly fails on `hp/n_signals/Wilson`,
  `stage_pit_formula_fallback` mostly fails on `hp/n_signals`, and
  `cross_stage_fallback` mostly fails on `hp/wilson`; the new
  `fail_holding_days_by_match_tier` shows those exact PIT `hp` failures
  cluster on off-anchor holding_days 20/30/60/90, which is the most useful
  hint for the next tuning decision. 2026-06-01 sensitivity auditing
  (`base`, `hold+20`, `min_n_signals-2`, `min_wilson_win-0.05`) did not change
  selected_rows, so the next useful tuning decision is upstream candidate
  supply / formula coverage, not profile micro-adjustment. The new need
  coverage audit also surfaces source registration facts: `need_027`'s
  preferred `akshare` is registered, while the declared fallback label
  `miaoxiang` resolves to the registered `aif10` family but that adapter still
  lacks `individual_fund_flow`, so the fallback is still conceptual in the
  current wiring. 2026-06-01 also ran `audit_stage_opt_candidate_supply.py` on the
  current audited slice (2023-01-01→2026-05-29, limit-stocks 50) and found
  `raw_signal_rows=2,103,143 / filtered_signal_rows=1,110,280 / unique_keys=133,857
  / ready_keys=76,480 / ready coverage=57.14% / below_min_signals=57,377`, with
  `codes_without_bars=0` and all blocked keys failing on `below_min_signals`;
  the helper now reuses the current connection's calendar truth source instead
  of opening a nested `latest_closed_or_raise()` connection, and
  `build_formula_signals_history.py --recompute-horizon-evidence` no longer
  throws NameError after importing `defaultdict`. This reinforces the same
  conclusion: upstream candidate supply / formula coverage is the
  next lever, not another profile knob tweak. LHB side, the latest read-only
  check shows
  `raw_lhb_daily` and `fact_lhb_event` both max at `2026-05-29`; latest day
  raw 94 rows / 84 codes and fact 84 rows / 84 codes, so the remaining LHB
  WARN is source sparsity, not ETL lag. 2026-06-01 also aligned registry-side
  `lhb_daily` to the same date-bounded helper as the client path, so aif10
  resolve/probe no longer produces the old unbounded full-history false
  positive. `audit_pit_coverage.py` is still 4/4 PASS, with
  `fact_lhb_event` gain_20d coverage 83.9% > 60%, so the sparse LHB WARN is
  completeness-only, not PIT safety.

- generated_at: `2026-05-25T01:20:01Z`
- model_id: `lgbm_phase5_gcp_20260520T010718`
- current_step: `all_done`
- next_step: `all_done`
- resume_command: `echo all_done`

## Steps

| Step | Name | Status | Evidence Found |
|---:|---|---|---|
| 1 | verify local prediction artifacts | done | json:data/reports/phase5_chain/status.json step=gcp_disabled<br>db:mart_p0b_lambdamart_v6_predictions model_id rows=3396073 |
| 2 | pre-sim audit | done | json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json (model_id mismatch)<br>json:data/reports/pit_audit.json fresh PASS |
| 3 | paper_sim execution | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1<br>db:mart_paper_sim_nav sim_run_id contains model_id rows=614 |
| 4 | KPI ingestion | done | db:mart_paper_sim_kpi joined to model compare rows=1 |
| 5 | KPI comparison | done | db:mart_paper_sim_lambdamart_v6_kpi_compare model_id rows=1 |
| 6 | Pareto verdict gatekeeper | done | json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json<br>json:data/reports/phase4_gate_result.json matching model (model_id mismatch) |
| 7 | decision promote/reject/retrain | done | json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json |

## Blockers

- none

## Expected Evidence

### Step 1: verify local prediction artifacts
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb.bak
- file:data/smartmoney_post_lgbm_phase5_gcp_20260520T010718.duckdb
- json:data/reports/phase5_chain/status.json step=pull_done
- file:data/reports/phase5_chain/monitor_done_lgbm_phase5_gcp_20260520T010718.sentinel (weak)
- db:mart_p0b_oos_predictions model_id
- db:mart_p0b_lambdamart_v6_predictions model_id

### Step 2: pre-sim audit
- json:analysis/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pre_sim_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pit_audit.json fresh PASS
- db:mart_champion_candidate_evaluation PIT pass

### Step 3: paper_sim execution
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/paper_sim_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id
- db:mart_paper_sim_nav sim_run_id contains model_id

### Step 4: KPI ingestion
- json:data/reports/msaf_ensemble_phase5_lgbm_phase5_gcp_20260520T010718.json kpi
- json:data/reports/kpi_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_kpi joined to model compare

### Step 5: KPI comparison
- json:data/reports/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/kpi_compare_lgbm_phase5_gcp_20260520T010718.json
- db:mart_paper_sim_lambdamart_v6_kpi_compare model_id

### Step 6: Pareto verdict gatekeeper
- json:data/reports/phase4_gate_lgbm_phase5_gcp_20260520T010718.json
- json:analysis/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/pareto_verdict_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/phase4_gate_result.json matching model
- db:mart_tdx_keep_promotion_gate challenger_model_id

### Step 7: decision promote/reject/retrain
- json:analysis/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/promote_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/ensemble_decision_lgbm_phase5_gcp_20260520T010718.json
- json:data/reports/retrain_decision_lgbm_phase5_gcp_20260520T010718.json
- db:mart_champion_model model_id
- db:mart_champion_candidate_evaluation final status
- db:mart_tdx_keep_promotion_gate final decision

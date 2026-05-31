# SESSION HANDOFF

Manual Codex checkpoint. Current operating state lives in `goal.md`; durable
startup rules live in `AGENTS.md` and `docs/chunkyctl_session_quickstart.md`.
This file is a short recovery note, not a replacement for those authorities.

Snapshot: `2026-06-01 04:20:40 CST`

## Risk First

| Item | State | Action |
|---|---|---|
| Worktree | Long-lived dirty is clean; K-line refresh wrote ignored DuckDB data and did not create tracked dirty before this state-doc update | If dirty, run `scripts/chunkyctl worktree --format markdown` and resolve by bucket; never use `git add .` |
| GCP | No GCP work started in this Codex slice | Keep stopped unless a scoped, approved cloud objective exists |
| Optuna/backtest | Not running | Do not resume until architecture/data gates allow it |
| Storage payload | `PASS`: 320 scanned / 0 FAIL / 0 WARN / 11 reviewed PASS | Reviewed columns are governed by `backend/config/storage_retention.yaml`; recursive or over-cap payloads still block |
| CodeGraph | Synced after the survey `.py` slice; pending may show the new untracked test until this slice is staged/committed | Re-run `codegraph status .` after this commit |
| Complexity | Historical HIGH remains debt; tooling diff ignores line-number drift by default | New HIGH still blocks; line drift alone should not |
| Data freshness/PIT | **WARN/FAIL mixed**: end-to-end freshness FAIL is cleared, but data completeness and survivorship still FAIL | Continue label/v3/v4/sniper/institution, LHB, recommendation PIT, and survivorship work; no strategy claim |

## Latest Slice

Goal: close the long-lived dirty cleanup loop, then repair real data freshness
from the truth source outward. The K-line state-doc slice is committed as
`3d610ab9 docs: record kline freshness catchup state`; the alpha158 safety
slice is committed as `ac596d90 fix: make alpha158 refresh window safe`; the
stage/context/trigger safety slice is committed as
`224ece41 fix: make downstream signal refresh windows safe`. This slice safely
refreshed picture and survey marts, and added survey read-window/write-window
separation plus empty-window guards. No GCP/Optuna/backtest work was started.

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
| `audit_data_completeness.py` after refresh | FAIL overall; `price_kline_tdxhub` OK at 2026-05-29 / 5,188 codes / `= cal` |
| K-line recent coverage query | 2026-05-26=5201, 05-27=5184, 05-28=5184, 05-29=5188; 16 codes latest < 2026-05-29 |
| `audit_end_to_end.py` after refresh | FAIL: 24 total / 18 OK / 2 WARN / 4 FAIL |
| `audit_test_tool_health.py --scope backend/scripts/build_alpha158_duck.py --scope backend/tests/scripts/test_build_alpha158_duck.py` | PASS |
| `pytest -q backend/tests/scripts/test_build_alpha158_duck.py` | 3 passed |
| `scripts/chunkyctl audit --run --scope backend/scripts/build_alpha158_duck.py --scope backend/tests/scripts/test_build_alpha158_duck.py` | PASS |
| `audit_data_completeness.py` after alpha158 refresh | FAIL overall; `fact_alpha158_panel` OK at 2026-05-29 / 5,183 codes / `= cal`; remaining issues = 7 |
| alpha158 recent coverage query | 2026-05-26=5185, 05-27=5184, 05-28=5184, 05-29=5183; duplicate count in refreshed window = 0 |
| `audit_test_tool_health.py --scope backend/scripts/build_formula_signals_history.py --scope backend/scripts/build_stage_formula_fitness.py --scope backend/scripts/build_signal_context.py --scope backend/tests/test_build_formula_signals.py` | PASS |
| `pytest -q backend/tests/test_build_formula_signals.py` | 19 passed |
| `scripts/chunkyctl audit --run --scope backend/scripts/build_formula_signals_history.py --scope backend/scripts/build_stage_formula_fitness.py --scope backend/scripts/build_signal_context.py --scope backend/tests/test_build_formula_signals.py` | PASS |
| Downstream coverage query | `fact_stock_technical_stage` max 2026-05-29 / 1,429,117 rows; `fact_signal_context` max 2026-05-29 / 2,125,233 rows; `fact_technical_trigger` max 2026-05-29 / 1,381,657 rows |
| `audit_data_completeness.py` after trigger refresh | FAIL overall; remaining 7 issues: label/v3/v4/sniper/institution 2026-05-19, LHB 2026-05-25, trigger event-table partial warn |
| `audit_end_to_end.py` after trigger refresh | FAIL: 24 total / 18 OK / 4 WARN / 2 FAIL; FAIL now `mart_stock_picture_daily` and `mart_stock_survey_features` |
| `audit_universe_coverage.py` after trigger refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after trigger refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_test_tool_health.py --scope backend/scripts/build_picture_daily.py --scope backend/tests/test_build_picture_daily.py` | PASS |
| `pytest -q backend/tests/test_build_picture_daily.py` | 4 passed |
| `audit_test_tool_health.py --scope backend/scripts/build_survey_features.py --scope backend/tests/sentiment/test_build_survey_features_script.py --scope backend/tests/sentiment/test_survey_builder.py` | PASS |
| `pytest -q backend/tests/sentiment/test_build_survey_features_script.py backend/tests/sentiment/test_survey_builder.py` | 9 passed |
| `complexity-optimizer backend/scripts/build_survey_features.py` | No obvious hotspots in targeted scan |
| `audit_end_to_end.py` after picture/survey refresh | PASS with WARN: 24 total / 18 OK / 6 WARN / 0 FAIL |
| `audit_data_completeness.py` after picture/survey refresh | FAIL overall; remaining 7 issues: label/v3/v4/sniper/institution 2026-05-19, LHB 2026-05-25, trigger event-table partial warn |
| `audit_universe_coverage.py` after picture/survey refresh | PASS: 17 PASS / 5 WARN / 0 FAIL |
| `audit_pit_integrity.py` after picture/survey refresh | PASS: 11 PASS / 28 WARN / 0 FAIL |
| `audit_survivorship_gate.py` after picture/survey refresh | FAIL: label panel codes 711 < 90% of ever-listed 5,210 |

## Next Actions

1. Finish review/gates for this picture/survey freshness slice, then commit
   with `scripts/safe_commit.sh`; do not use raw `git commit`.
2. Confirm `git status --short`, `codegraph status .`, and
   `scripts/chunkyctl doctor --fast` return clean/PASS after the commit.
3. Continue `goal.md` 6.11 downstream freshness. Next safe slices are
   label/v3/v4/sniper/institution score marts, then LHB. Keep recommendation
   PIT coverage and survivorship as blocking/WARN evidence until measured.

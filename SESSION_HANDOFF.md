# SESSION HANDOFF

Manual Codex checkpoint. Current operating state lives in `goal.md`; durable
startup rules live in `AGENTS.md` and `docs/chunkyctl_session_quickstart.md`.
This file is a short recovery note, not a replacement for those authorities.

Snapshot: `2026-06-01 03:51:30 CST`

## Risk First

| Item | State | Action |
|---|---|---|
| Worktree | Long-lived dirty is clean; K-line refresh wrote ignored DuckDB data and did not create tracked dirty before this state-doc update | If dirty, run `scripts/chunkyctl worktree --format markdown` and resolve by bucket; never use `git add .` |
| GCP | No GCP work started in this Codex slice | Keep stopped unless a scoped, approved cloud objective exists |
| Optuna/backtest | Not running | Do not resume until architecture/data gates allow it |
| Storage payload | `PASS`: 320 scanned / 0 FAIL / 0 WARN / 11 reviewed PASS | Reviewed columns are governed by `backend/config/storage_retention.yaml`; recursive or over-cap payloads still block |
| CodeGraph | Up to date after the previous `.py` slice; this K-line refresh did not edit Python | Re-run `codegraph status .` after this docs commit |
| Complexity | Historical HIGH remains debt; tooling diff ignores line-number drift by default | New HIGH still blocks; line drift alone should not |
| Data freshness/PIT | **FAIL**: K-line and alpha158 are now OK, dependent marts still stale/PIT-unsafe | Continue downstream freshness in the order in `goal.md`; no strategy claim |

## Latest Slice

Goal: close the long-lived dirty cleanup loop, then repair real data freshness
from the truth source outward. The K-line state-doc slice is committed as
`3d610ab9 docs: record kline freshness catchup state`. This slice adds an
alpha158 safe-window writer and refreshes only the local alpha158 DuckDB window;
no GCP/Optuna/backtest work was started.

K-line refresh commands used:

| Step | Command shape | Result |
|---|---|---|
| Initial catch-up | `build_price_kline_tdxhub.py --skip-existing --target-date 2026-05-29 --pages 1 --workers 8 --max-inflight 32` | 5,097 stocks success / 92 failed / 15,281 rows written |
| Retry stale actives | `build_price_kline_tdxhub.py --skip-existing --target-date 2026-05-29 --pages 1 --workers 4 --max-inflight 16 --per-stock-retry-attempts 2` | 93 stocks success / 0 failed / 275 rows written |

Alpha158 refresh command used:

| Step | Command shape | Result |
|---|---|---|
| Safe window catch-up | `build_alpha158_duck.py --start 2026-03-01 --write-start 2026-05-26 --end 2026-05-29` | `replace_window`; 20,736 rows / 5,185 codes / 4 dates written; table max now 2026-05-29 |

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

## Next Actions

1. Commit this alpha158 safety/data-refresh slice with `scripts/safe_commit.sh`;
   do not use raw `git commit`.
2. Confirm `git status --short`, `codegraph status .`, and
   `scripts/chunkyctl doctor --fast` return clean/PASS after the commit.
3. Continue `goal.md` 6.11 downstream freshness. Do not directly run narrow
   `build_signal_context.py` / `build_formula_signals_history.py` windows yet:
   their current CLIs couple compute start and DELETE window. Next safe slice is
   stage-only freshness plus lookback-safe `fact_signal_context`, then
   `fact_technical_trigger`, picture, labels, and score marts.

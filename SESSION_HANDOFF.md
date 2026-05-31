# SESSION HANDOFF

Manual Codex checkpoint. Current operating state lives in `goal.md`; durable
startup rules live in `AGENTS.md` and `docs/chunkyctl_session_quickstart.md`.
This file is a short recovery note, not a replacement for those authorities.

Snapshot: `2026-06-01 03:13:56 CST`

## Risk First

| Item | State | Action |
|---|---|---|
| Worktree | Expected clean after the formula-refresh safety slice is committed | If dirty, run `scripts/chunkyctl worktree --format markdown` and resolve by bucket |
| GCP | No GCP work started in this Codex slice | Keep stopped unless a scoped, approved cloud objective exists |
| Optuna/backtest | Not running | Do not resume until architecture/data gates allow it |
| Storage payload | `PASS`: 320 scanned / 0 FAIL / 0 WARN / 11 reviewed PASS | Reviewed columns are governed by `backend/config/storage_retention.yaml`; recursive or over-cap payloads still block |
| CodeGraph | Up to date after current `.py` edits | Run `codegraph status .` before handoff |
| Complexity | Baseline loaded; `new_high_count=0` under `chunkyctl doctor --fast` | Historical HIGH remains debt; do not treat it as current-regression proof |
| Data freshness/PIT | **FAIL**: latest real audits still fail | Next work should fix local data freshness/PIT in scoped windows before any strategy claim |

## Latest Slice

Goal: make the next data-freshness repair safe. The real audit showed
`fact_technical_trigger` stale at 2026-05-19, but the refresh writer previously
deleted every row for a formula even when the operator requested a narrow date
window. That could destroy historical formula evidence while trying to repair
freshness.

Files included in the formula-refresh safety slice:

| File | Purpose |
|---|---|
| `backend/scripts/build_formula_signals_history.py` | Scope `fact_technical_trigger` DELETE by formula and optional date window; reject formula/date out-of-scope writes; skip horizon evidence replacement during explicit narrow refresh unless explicitly requested |
| `backend/tests/test_build_formula_signals.py` | Regression tests for scoped replace preserving outside-window rows, 0-signal windows clearing stale rows, and out-of-scope writes failing fast |
| `goal.md` | Update current FAIL/WARN ledger and next blocker |
| `PROJECT_INDEX.md` | Update project map increment |
| `SESSION_HANDOFF.md` | Replace storage slice note with formula-refresh safety note |

## Verified So Far

| Gate | Result |
|---|---|
| `audit_test_tool_health.py --scope ...formula...` | PASS |
| `pytest -q backend/tests/test_build_formula_signals.py` | 12 passed |
| `scripts/chunkyctl audit --run --scope ...formula...` | PASS |
| `codegraph sync .` / `codegraph status .` | Synced; index up to date |
| Full backend complexity scan | Historical HIGH listed; `doctor --fast` diff shows `new_high_count=0` |
| `git diff --check` | PASS |
| `scripts/chunkyctl doctor --fast` | PASS overall while current slice remains dirty |

## Next Actions

1. Commit the formula-refresh safety slice with `scripts/safe_commit.sh`; do not
   use raw `git commit`.
2. Confirm `git status --short`, `codegraph status .`, and
   `scripts/chunkyctl doctor --fast` return clean/PASS after the commit.
3. Continue the next real blockers from `goal.md`: end-to-end data freshness/PIT,
   Survivorship/Data completeness failures, and then architecture/business
   resumption gates.

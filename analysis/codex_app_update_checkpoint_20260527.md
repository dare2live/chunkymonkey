# Codex App Update Checkpoint — 2026-05-27

Purpose: safe resume point before updating/restarting Codex Mac app.

## Status

| Item | State |
|---|---|
| Workspace | dirty on `main`; do not `git add .` |
| App update risk | filesystem edits should persist; chat context may be lost |
| Commit/stage | none done by Codex in this checkpoint |
| GCP/long jobs | none started |

## Latest User Decisions

| Topic | Decision |
|---|---|
| Architecture first | Continue architecture/gate cleanup before business work |
| Data/source contract | Manage needs before sources; missing source must be audited |
| CYQ/fund flow | `raw_fund_flow_daily` is stale/deprecated; restore only after source probe + PIT/freshness gate |
| Profiles/lineage | Add stock profile, institution profile, main-force behavior profile, stock dossier, lineage, and frontend route to plan |
| Mainline after architecture | Return to BestChoice formulas, 300616 original/derived formulas, main project quant backtests, then frontend |
| Final UI redesign | Last stage after backend contracts/gates/mainline evidence are stable |

## Files Added/Updated In Latest Slice

| File | Purpose |
|---|---|
| `docs/profile_lineage_roadmap.md` | New roadmap for profile/lineage/stock dossier/mainline/frontend |
| `goal.md` | Added profile/lineage roadmap, mainline return order, final UI redesign |
| `docs/implementation_plan.md` | Synced same roadmap and sequencing |
| `docs/top_level_design_review.md` | Added frontend/UI gating to top-level architecture review |
| `docs/chip_distribution_cyq_spec.md` | Corrected fund-flow status: stale/deprecated, proxy/unknown rules |
| `docs/data_lineage_spec.md` | Extended lineage contract to profile components |
| `scripts/session_handoff_audit.py` | Refactored touched script to reduce complexity risk |
| `backend/tests/scripts/test_session_handoff_audit.py` | Focused regression tests for handoff audit helpers |

## Resume Command

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey
git status --short
codegraph status .
PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all
```

## Next Validation To Run

```bash
PYTHONPATH=backend python -m py_compile \
  scripts/session_handoff_audit.py \
  backend/tests/scripts/test_session_handoff_audit.py \
  backend/services/strategies/institution_follow/capital_flow_alpha.py \
  backend/scripts/build_institution_score_daily.py \
  backend/scripts/build_sniper_score_daily.py

PYTHONPATH=backend python -m pytest -q \
  backend/tests/scripts/test_session_handoff_audit.py \
  backend/tests/strategies/test_institution_follow_pit.py \
  backend/tests/strategies/test_sniper_batch.py \
  backend/tests/scripts/test_audit_tdx_data_need_coverage.py \
  backend/tests/scripts/test_audit_stale_references.py \
  backend/tests/scripts/test_audit_n_plus_one.py

codegraph sync .
/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown
/Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/scripts --format markdown
git diff --check
```

## Validation Completed After Resume

| Check | Result |
|---|---|
| Startup universe gate | `PYTHONPATH=backend python backend/scripts/check_universe_filter.py --all` -> CLEAN (764 files checked) |
| Compile | `py_compile` for `session_handoff_audit.py`, focused tests, capital-flow/sniper files -> PASS |
| Focused tests | `test_session_handoff_audit.py`, institution-follow PIT, sniper batch, TDX data need, stale references, N+1 -> 40 passed |
| CodeGraph | `codegraph sync .` -> synced 40 changed files / 609 nodes after final Python edit |
| Complexity, scripts | 0 HIGH; `session_handoff_audit.py` MEDIUM cleared; only historical `validate_champion_paper_sim.py` MEDIUM remains |
| Complexity, backend | 80 historical HIGH remain in scripts/backfill/backtest-style files; no new HIGH observed for latest touched files |
| Universe | CLEAN (764 production files) |
| Diff hygiene | `git diff --check` -> PASS |
| TDX data need materialization | `audit_tdx_data_need_coverage.py` -> 27 coverage / 10 priority / 14 reassignment rows |

## Do Not Forget

- Do not run GCP, Optuna, expensive backtests, or frontend redesign yet.
- Do not stage or commit without user request; if committing, use `scripts/safe_commit.sh`.
- Keep CodeGraph and complexity optimizer paired for Python changes.
- Keep unknown/proxy/stale evidence explicit; do not turn it into production claims.

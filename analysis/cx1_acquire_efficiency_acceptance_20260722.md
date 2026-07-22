# CX-1 acceptance — acquire efficiency + latency budgets + stream/incremental truth

> Status: evidence-only / phased acceptance
> Authority: `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7.2 CX-1
> Design: `workbench_incremental_orchestrator_ux_20260722.md` §3.4 P1 + P2 minimal
> Named consumer: workbench acquire UX + selective process correctness
> Label: **PASS** (capability + machine gates); live wall-clock observe residual noted

---

## Scope delivered

| CX-1 item | Delivered | Evidence |
|---|---|---|
| P1 typed `delta_manifest` | YES | `services/pipeline/delta_manifest.py`; acquire finalize; store → `daily_*.json` |
| DC frontier skip (empty increment) | YES | `decide_dc_action` + `process.py`; pulse **never** skipped |
| Latency budgets in report | YES | `pipeline_latency_budgets.yaml` + `stage_timing_s` / `budget_status` |
| Stream truth | YES (pre-existing + line) | drain stderr live FIXED; `[delta_manifest]` single-line log |
| Incremental recognition | YES | `acquire_summary.formal[].action`; ops idle exposes `delta_manifest` |

**Explicitly NOT in this knife (CX-2+):** ST/holder `state_changes` sensors; full progress bars; SLA tombstone (CX-4); Optuna.

---

## Kill criteria (held)

1. No chain short-circuit on empty acquire — clean/process/store still run.
2. `market_pulse` late window **always** `action=run` / `late_window_mandatory`.
3. DC skip only when frontier unchanged **and** no DC provenance advance; unknown frontier → RUN.
4. Live DC dims verify PASS before trusting seeded `dc_industry_view_as_of.json` (`20260722`).

---

## Machine acceptance (2026-07-22)

```text
pytest backend/tests/test_pipeline_delta_manifest.py \
       backend/tests/scripts/test_build_dc_industry_view.py \
       backend/tests/test_pipeline_stage_status.py \
       backend/tests/test_pipeline.py \
       backend/tests/test_ops_manual_run.py \
       backend/tests/services/test_run_outcome.py
→ 96+ passed on CX-1 suite (delta 9 + related; broader blocking CI 1024)
```

Key assertions:
- empty frontier → `process_plan.dc_industry_view=skip`, pulse `run`
- frontier advance / DC provenance → DC `run`
- `stage_timing_s.total` does not compound prior totals
- `write_report_and_alert` persists `delta_manifest` + `budget_status`

Live probe (read-only):
- `probe_dc_source_frontier() = 20260722`
- `build_dc_industry_view --verify` → **PASS** (dims match frontier)
- `decide_dc_action(...)` → **skip / dc_frontier_unchanged**

---

## §7.1 B/D mapping

| Signal | Status |
|---|---|
| D — drain-first / no sibling kidnap | FIXED (prior) |
| D — typed delta manifest fields | **PASS** |
| D — stream + incremental recognition | **PASS** (minimal; Cap E progress bars residual) |
| B — per-stage wall clock in `daily_*.json` | **PASS** (wired; next daily_update fills live numbers) |
| B — budget fields + evaluator | **PASS** (observational; does not abort chain) |
| B — live empty-increment ≤90s process | **OBSERVE** on next empty `daily_update` (not blocking capability PASS) |

---

## Verdict

**CX-1 = PASS** for named capability knife (delta selective DC + pulse invariant + budgets + incremental recognition).

**Residual (owned, non-blocking CX-1):** observe first live empty-increment `budget_status.process`; Cap E progress-bar polish.

**Next:** CX-2 state sensors (ST/holder/delist → `state_changes`) per MASTER §7.2.

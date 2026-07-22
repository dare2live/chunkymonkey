# Phase 2 — Orchestrator regression assertions (2026-07-22)

> Status: evidence-only
> Label: **FIXED**
> Owner plan: `analysis/architecture_fix_treadmill_first_principles_20260722.md` §C1 / Phase 2
> Prior structural knife: `analysis/foundation_acquire_all_due_unblock_20260722.md`
> Scope: confirm drain-first + no-kidnap invariants; **no DAG / plugin / stage framework**

## Kill criteria (from architecture plan)

| Criterion | Result |
|---|---|
| 「不越 sweep 边界」回归断言绿 | **PASS** — 2 tests green |
| 无 sibling 绑架 | **PASS** — code shape + tests; no second-kidnap evidence |
| 第二次真 kidnap → 才泛化 | **N/A** — no second kidnap → **不预建 DAG** |

## Invariants locked

1. **Drain before formal**: `run_acquire` calls `_sync_registry_drain` before `_sync_formal_on_demand_security_days`.
2. **No cross-sibling hard-raise**: formal domain hard-fail → `ctx.degraded` + continue sibling; does **not** `raise Tier0AcquireError` for ordinary catchup outcomes (wiring-policy bugs still raise).
3. **No fused-dragon abort**: formal hard must not kidnap `--all-due` drain (drain already ran) nor abort remaining formal siblings.

## Evidence

```text
pytest backend/tests/test_pipeline.py::test_acquire_runs_registry_drain_before_formal_and_despite_formal_hard \
       backend/tests/test_pipeline.py::test_formal_hard_fail_degrades_not_raises_and_continues_sibling
→ 2 passed
```

Live prior proof (same day): `foundation_acquire_all_due_unblock_20260722.md` — UI run drain→formal soft/`pending_publish`; sibling `ths_hot` catchup proceeded.

## Explicit non-work

- No second-kidnap domain observed → **no** orchestrator generalization.
- **No** DAG / event-bus / stage framework (architecture death #1).
- Continuity READY / margin frozen / org BLOCKED remain **ops residuals ≠ knives**.

Label: **FIXED**. Residual: none for Phase 2; Phase 3 = viz MVP; Phase 4 = E/F schedule honesty.

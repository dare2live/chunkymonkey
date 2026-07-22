# CX-2 acceptance — state sensors → delta_manifest.state_changes

> Status: evidence-only / phased acceptance
> Authority: `MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md` §7.2 CX-2
> Design: `workbench_incremental_orchestrator_ux_20260722.md` §4 P3
> Named consumer: workbench incremental truth + selective process force reasons
> Label: **PASS**

---

## Adversarial design (Grok REVISE → applied)

| Fork | Decision | Why |
|---|---|---|
| Sensor placement | Read-only `state_sensors` module; never writes Tier0 | Kill forbids Tier0 fusion |
| ST detection | Diff two latest *accepted* `canonical_stock_st_daily` partitions | Accepted membership is truth |
| Holders | **ratio + rank + exit** on accepted notice partition (`canonical_holders_notice_delta`) | Mandate = ratio even if rank unchanged; live had `exit_n=2` that ratio-only missed |
| Delist | `dim_active` before/after refresh + as-of fingerprint | Ops identity observer only |
| Process trigger | Cite `state_change:*` on segments/form when ST/delist; pulse always; **no phantom `holders_consumers` step** | Holders stay in `delta.state_changes` / `force_reasons`; process.py has no runner for invented steps |
| DC | Unchanged CX-1 frontier/provenance — ST/holder/delist never force DC | ST ≠ DC provenance |

---

## Scope delivered

| CX-2 item | Delivered | Evidence |
|---|---|---|
| ST 戴帽/摘帽 sensor | YES | `membership_diff` / `detect_stock_st_state_changes` |
| Holder ratio / rank / exit | YES | `holders_notice_diff` + `detect_holders_state_changes` |
| Delist / active removals | YES | `delist_diff` on `dim_active_a_stock` before/after + as-of |
| Populate `delta.state_changes` | YES | `acquire._finalize_acquire_delta` → `collect_state_changes` |
| Selective recompute trigger | YES | `plan_process_steps(..., state_changes=)` force reasons; pulse never skipped |
| PIT-safe / not Tier0 writer | YES | sensors `tier0_write: false`; read accepted/canonical/dim only |

**Explicitly NOT in this knife:** CX-3 briefing/facet bricks; CX-4 SLA tombstones; Optuna; Continuity READY chase; margin thaw; org_holding mass pull.

---

## Kill criteria (held)

1. **No Tier0 fusion** — sensors never call land/accept/publish; every block carries `tier0_write: false`.
2. **T+1 / late window** — `market_pulse.reason == late_window_mandatory` with or without state changes.
3. **Real ST detection** — unit fixture proves enter/exit; live historical pair `20260714→20260715` detects enter `002759.SZ`.
4. **Holders exit/rank not silent** — live `20260723` reports `exit_n=2` + `rank_changed_n≥1` (not ratio-only).
5. **No phantom process step** — `holders_consumers` absent from `process_plan`.

---

## Machine acceptance (2026-07-22)

```text
pytest backend/tests/test_pipeline_state_sensors.py \
       backend/tests/test_pipeline_delta_manifest.py
→ 19 passed
```

Live probe (read-only, `persist_dim_as_of=False`):

| Sensor | Live result |
|---|---|
| stock_st latest pair `20260721→20260722` | `changed=false` (honest empty day) |
| stock_st historical `20260714→20260715` | `entered_n=1` (`002759.SZ`) |
| holders accepted `20260723` | `ratio_changed_n=5`, `rank_changed_n=1`, `exit_n=2` |
| delist same-set | `changed=false` when before==after |
| process_plan pulse | `late_window_mandatory`; DC may skip; no `holders_consumers` |

---

## §7.1 mapping

| Signal | Status |
|---|---|
| D — typed `state_changes` in delta manifest | **PASS** |
| C — PIT: accepted ST partitions only; holders accepted notice; no future invent | **PASS** |
| A — named consumer (process force + workbench log line) | **PASS** |
| Kill — no Tier0 write / exit+rank visible / pulse intact | **PASS** |

---

## Verdict

**CX-2 = PASS** — state sensors populate `delta_manifest.state_changes` (ST / holders ratio·rank·exit / delist) and cite selective recompute force reasons without fusing into Tier0 or breaking late-window T+1 self-heal.

**Next:** CX-3 capability bricks (briefing inputs + facet serve) per MASTER §7.2.

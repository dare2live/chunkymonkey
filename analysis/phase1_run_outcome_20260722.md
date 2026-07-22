# Phase 1 — typed `run_outcome`（2026-07-22）

> Status: evidence-only
> Label: **FIXED**
> Plan: `analysis/architecture_fix_treadmill_first_principles_20260722.md` §C2 / Phase 1
> Knife: one logical deliverable = typed outcome SSOT + renderers (exit / wrapper / notify / UI)

## Verdict

**FIXED** — `data/reports/daily_*.json` now carries machine-readable
`run_outcome ∈ {success, soft_waiting_clock, hard_fail}`. Exit codes, Script
Editor wrapper, macOS notifications, and workbench UI **render** that field;
they no longer treat soft clock-wait as FAIL via `rc==1` heuristics.

## What landed

| Surface | Change |
|---|---|
| Single compute | `backend/services/pipeline/run_outcome.py` — classify msgs → rollup |
| Report SSOT | `store.write_report_and_alert` writes `run_outcome*` fields |
| Early hard exits | `run.py` writes report before exit 2/3/4/5 (incl. writer-busy) |
| Exit code | Derived from outcome (0 / 1 / 2–5); no longer the truth source |
| Wrapper | `scripts/manual_job_wrapper.py` reads report when `run_outcome_exit_code==rc`; soft → skip FAIL banner; stale soft cannot swallow hard rc |
| Dispatcher | `--outcome` keyed; `--skip-macos` deprecated alias → soft |
| Ops API | `GET /jobs/daily_update` exposes `run_outcome` + soft activity phase |
| Workbench | Amber「等时钟 / 软观测」vs red「硬失败」; soft never banner-FAIL |

## Adversarial exit↔outcome call (2-model)

- **A (strict clock-only soft)**: only `pending_publish` / `pre_available_after` /
  `still_failed=[today]` → soft; continuity/SLA → ??? (third state missing).
- **B (non-hard degraded = soft bucket)**: any degraded without AUTH/PREFLIGHT/
  TIER0/WRITER → `soft_waiting_clock` for UI/notify; hard blocks alone →
  `hard_fail`.

**Chose B** (per plan three-state ceiling + kill criterion “soft not FAIL” +
transitional `rc==1` semantics). Continuity/SLA remain amber observation, never
green success and never red FAIL. Named clock patterns still classify as
`soft` for reason strings.

## Tests

```bash
PYTHONPATH=backend pytest \
  backend/tests/services/test_run_outcome.py \
  backend/tests/services/test_notification_dispatcher_skip_macos.py \
  backend/tests/test_ops_manual_run.py -q
```

Expected: all green (14 + 11 on authoring host).

## How to verify (next UI daily_update click)

1. Open workbench `#/workbench` → 「数据更新」.
2. After run finishes, inspect `data/reports/daily_YYYYMMDD.json`:
   - morning `pending_publish` / drain same-day vacuum →
     `"run_outcome": "soft_waiting_clock"`
   - true auth/preflight/Tier0/writer block → `"run_outcome": "hard_fail"`
3. UI: soft → amber banner「等时钟 / 软观测（非 FAIL）」; hard → red「硬失败」.
4. macOS: soft → **at most one** `ChunkyMonkey soft_waiting_clock` observation;
   **no** `job FAIL`. Hard → one `ChunkyMonkey job FAIL`.
5. Doctor may still see `/tmp/chunkymonkey_ALERT_daily_update*.flag` on soft —
   that is intentional residue for session hygiene, not a FAIL paint signal.

## Explicitly not in this knife

- Continuity READY chase / margin thaw / ths_hot watermark theater
- Phase 2 orchestrator generalization (only on second true sibling kidnap)
- Phase 3 viz MVP

## Residual

- Live UI click on open market morning is the wall-clock acceptance of soft
  banner count (unit tests cover classify/dispatch/API wiring).
- `--skip-macos` kept as deprecated CLI alias for one release of scripts.

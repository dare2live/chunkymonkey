# Capability E — modular pipeline step cards (2026-07-21)

> Evidence-only. Owner asked to **做** Capability E: workbench step cards + keep one-click `daily_update`.

## What shipped

| Surface | Detail |
|---|---|
| Workbench tabs | `#/workbench` —「一键更新」+「分步节点」(light Capability C) |
| Primary |「数据更新」→ `POST /api/v3/ops/jobs/daily_update/run` unchanged |
| Catalog | `GET /api/v3/ops/pipeline/nodes` |
| Runnable nodes | `pipeline_acquire`, `pipeline_clean`, `pipeline_process`, `pipeline_store`, `derive_qfq` |
| Disabled (honest) | 预检（嵌在链内）；S1 land / S2 accept（需 domain + dates / batch-id → CLI） |

## Alignment (no invented stages)

- Jobs spawn real `scripts/chunkyctl pipeline …` / `derive qfq` through existing `manual_job_wrapper`.
- Writer flock still gates all runs (409 when busy).
- Reuses `current_activity` / log-tail polling from `c2a9c5e14`.
- Org mass refresh / margin thaw / Optuna **not** exposed.

## Label

**FIXED** for the operable subset (independent stage/derive buttons that already had safe CLIs).
**PARTIAL residual:** parameterized S1/S2 UI (intentionally disabled, not fake-runnable).

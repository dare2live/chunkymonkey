# Phase 3 — 潜伏象限 MVP (2026-07-22)

> Status: evidence-only
> Label: **FIXED**
> Owner plans: `analysis/architecture_fix_treadmill_first_principles_20260722.md` Phase 3
>   + `analysis/frontend_complex_viz_plan_20260722.md` Phase 1 (Alt1 floor)
> Scope: market page Cap A quadrant; read-only Tier3; no new backend; no terrain hero

## Kill criteria

| Criterion | Result |
|---|---|
| 潜伏象限 on `#/market` | **PASS** — `MoneyflowAssistPanel` scatter + table |
| Wire Cap A `GET /decision/moneyflow/board` | **PASS** — existing `fetchMoneyflowBoard` |
| Light theme / HS-A / unknown≠0 | **PASS** — theme.ts warm paper; serve inherits HS-A; thin windows grey "未形成结论", never plot as 0 |
| Terrain hero | **DEFER** — plan Enrich only after MVP solid; not shipped |
| Decorative-only → kill switch | N/A ship — quadrant is decision floor; terrain not added |

## What shipped

- Scatter: x=`window_return_pct`, y=`relative_ratio_pct`, size≈|cum_net|, color=behavior
- Marked 潜伏 (左上) / 抢筹 (右上) regions
- Horizon + chain toggles; click bubble/row → existing `DrillPanel`
- Unknown/thin rows: count badge + grey table rows; excluded from (0,0) plot

## Explicit non-work

- No echarts-gl / 3D terrain
- No new backend endpoint
- No Optuna / Release / Tier0 writeback
- Cap D Sankey/parcoords stays Enrich (already have intersection tab)

Label: **FIXED**. Residual: terrain hero Enrich deferred (plan kill switch intact); owner usage is the axis-③ acceptor.

# PLAN residuals closeout — Workbench P2 / Enrich / Dossier / Sensing (2026-07-23)

> Evidence-only acceptance. Authority: MASTER reeval + DOC_AUTHORITY + workbench UX + complex_viz + big_picture.
> No goal.md north-star rewrite. Optuna/RX skipped (not scheduled).

## Residuals

| Residual | Label | Evidence |
|---|---|---|
| Workbench P2 progress UX | **FIXED** | Waterfall tint logs; overall + per-node ProgressBar; phase rail; `delta_manifest`/`_live` card; Cap E card polish. Progress = phase-derived (honest), not fake sub-percent telemetry. |
| Frontend Enrich (terrain + ∩ viz) | **FIXED** | Capital Terrain 2.5D CSS hero (`CapitalTerrain.tsx`) above quadrant; Cap D Sankey + parcoords (`intersectionCharts.ts`); no new backend; no echarts-gl. Kill: terrain context-only, quadrant remains floor. |
| Dossier Cap F polish | **PARTIAL** (UI) / org land **BLOCKED** | Stock L1 hero observation + strip + honesty line; institution disclaimer for org_holding BLOCKED; no fake org mass land. |
| Sensing density | **FIXED** | Sensing tab L1=`PulseBand` only; L2/L3 behind `<details>`. |
| Docs authority sync | **FIXED** | MASTER / DOC_AUTHORITY / workbench UX / complex_viz / big_picture updated. |

## Non-goals held

- No Optuna / E-F remeasure / margin thaw / Continuity READY chase / org invent / Tier0 writeback.
- Progress bars never claim finer truth than log-derived phase.

## Build

`frontend/`: `npm run build` (tsc + vite) **PASS** 2026-07-23.

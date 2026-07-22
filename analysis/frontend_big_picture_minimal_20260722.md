# Frontend big-picture / editorial-minimal pass (2026-07-22)

> Status: evidence-only. Design + implemented polish + L1/L2/L3 facet-jump skeleton.
> Companion: `analysis/frontend_complex_viz_plan_20260722.md` (viz metaphors — quadrant FIXED,
> terrain deferred). Peer owns incremental-orchestrator / workbench logic — this pass
> touches product surfaces + shared CSS only.
> Obeys `goal.md` north star (辅助买卖决策); `$mio`; taste skills; brick pipeline as honesty
> floor (UI exploration only as honest as shipped bricks). Not a control-plane owner.

---

## 0. Design read

**Daily-cadence quant insight product, Apple-minimal / editorial language, evolving the
existing warm-neutral CSS system. Progressive disclosure L1→L2→L3; every shown computed
facet is an exploratory jump into its universe.**

Dials: **VARIANCE 5 · MOTION 3 · DENSITY 4**. Redesign-preserve (IA/routes/semantics stay).

## 1. Product is / is not

- **Is:** daily reading of trends, relationships, big picture retail rarely sees.
- **Is not:** realtime ops 看板. Workbench stays ops-capable; product face is calm.

## 2. Foundation layering ↔ Frontend progressive disclosure (owner mapping)

Backend already separates stores and process layers. Frontend disclosure **mirrors** that
stack — it does not recompute it in the browser.

| Backend process layer | Typical brick / serve | Frontend disclosure |
|---|---|---|
| raw / landing | provider responses, landing batches | **L3 only on demand** (gaps, resolver notes, log-adjacent honesty) |
| accepted canonical | daily/ST/qfq/taxonomy partitions | **L3 明细** tables / episode lists behind `<details>` |
| Tier1 basic variables | form/stage axes, holder episodes | **L2** chips + axis grids + horizon strips |
| Tier2 sensing | flow_regime, pulse boards | context tabs; evidence for Tier3 labels |
| Tier3 / decision surfaces | moneyflow `behavior`/`conclusion`, intersection `why`, screener `why` | **L1** big-picture: observation sentence, 潜伏象限, chip labels |

**Why exploratory jumps work:** facets (潜伏/抢筹/出货, form_name, axis_*, breakout,
intersection membership) are **precomputed in process layers** and served read-only. The UI
only *navigates* them. That is why foundation correctness + performance were non-negotiable —
pretty exploration without honest bricks is decoration (mio: 异常漂亮=警报).

**Do not claim foundation incomplete** for this track: consume shipped Cap A/B/D/E/F bricks.

```
L1 极简     →  advanced/decision bricks (conclusion / why / observation / quadrant)
     click
L2 展开     →  basic+advanced joins (multi-horizon, peer list, drill panel, dim heat)
     explicit
L3 明细     →  canonical/raw-adjacent rows (full tables, gaps, episode dumps)
```

## 3. Progressive disclosure rules (mandatory)

1. **L1:** first screen = trends / relationships / big picture only — sparse.
2. **L2:** click → richer context without leaving the narrative (panel / open details / explore page).
3. **L3:** deepest tables only on explicit demand (`明细` / closed `<details>`).
4. **Never dump L3 by default.** Backend stays precise; frontend stays calm.

## 4. Facet registry + jump graph

**Principle:** if UI can show a computed facet, it must be clickable into that facet's
universe, then into related dossiers.

| Facet kind | Brick / API | Jump target | Status |
|---|---|---|---|
| `behavior` (潜伏/抢筹/出货) | Cap A `moneyflow/board` + stock moneyflow | `#/explore?kind=behavior&value=…` → sector peer list; deep-link `#/market?tab=assist&behavior=…` | **live** |
| `form_name` | Cap B `screener/form_stage` | `#/explore?kind=form_name&value=…` → stock list → dossier | **live** |
| `axis_pos|trend|purity|vol` | Cap B screener | `#/explore?kind=axis_*&value=…` | **live** |
| `breakout` | Cap B `is_breakout_event` | `#/explore?kind=breakout` | **live** |
| `intersection` | Cap D `intersection/strongest` | `#/explore?kind=intersection` + market tab | **live** |
| `holder` | dossier holders / inst profile | `#/institutions/:holder` (existing) | **live** |
| industry/concept sector membership as stock-universe facet | Cap D sector refs | chip on intersection lists → (future) membership board | **stub** (sector name still text; stock codes live) |
| stock-level「连续N日净流入」streak universe | would need stock-level flow board | — | **stub** — today stock moneyflow exposes sector `behavior` chip (live) instead of inventing browser streak |

**Jump graph (shipped skeleton):**

```
股票档案 ──chip──► /explore (L2 universe) ──row──► 股票档案
    │                      │
    │                      └──deep-link──► /market?tab=…&filters
    ├──holder──► 机构档案 ──episode code──► 股票档案
    └──intersection chip──► /explore?kind=intersection
```

## 5. Information architecture

Nav (insight first): `市场 → 股票档案 → 机构档案 → 观察账本 → 工作台`.
Default route: `#/market` (assist tab default).

### 市场 tabs
| Tab | L1 lead | L2 | L3 |
|---|---|---|---|
| 资金决策辅助 (default) | quadrant + conclusion | drill panel | board table in `<details>` |
| 交集最强 | `why` per stock | — | chain member lists |
| 形态/阶段选股 | filters + why | — | result rows |
| 市场感知 | pulse + section funnel | drill/curves on click | dense tables |

### 股票档案
L1: observation + facet chips. L2: moneyflow horizons / form axes. L3: gaps / hybrid notes / holder full table behind demand.

### 机构档案
L1: KPI. L2: dim heatmap. L3: episode table. Episode stock codes → dossier (**live**).

## 6. What shipped this pass

- Design doc (this file) with foundation↔disclosure mapping + facet registry.
- Editorial CSS: type scale, underline page tabs, quieter cards/tables, facet-chip styles.
- Shell: product-first nav; brand sub「每日市场洞察」; default `/market`.
- `#/explore` FacetExplorePage consuming Cap A/B/D.
- Stock dossier: clickable facet chips (behavior / form / axes / breakout / intersection).
- Market: assist default; URL deep-links `tab` + filters; board table behind L3 details.
- Institution: episode→stock links; L2/L3 disclosure framing.
- **Not touched:** workbench acquire / soft_waiting peer logic.

## 7. Honesty gates

- `unknown` never painted as 0; stale surfaces stay honest.
- No browser-side intersection/behavior recompute.
- No Tier0 writeback; no new backend endpoint this knife.
- No peer workbench logic reverted.

## 8. Residual

- Stock-level multi-day inflow streak universe (needs serve brick, not UI invention).
- Industry/concept membership facet chips on intersection sector names.
- Terrain hero still deferred (viz Enrich).
- Sensing-tab further density cut optional.

Label: **FIXED (skeleton)** for L1/L2/L3 + facet jumps on shipped bricks; residual stubs listed above.

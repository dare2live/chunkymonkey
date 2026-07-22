# Frontend complex data-viz plan — decision-assist overlay (2026-07-22)

> Status: evidence-only / **Phase 1 MVP FIXED** (潜伏象限 shipped on `#/market` 资金决策辅助; evidence `analysis/phase3_latent_quadrant_mvp_20260722.md`). Terrain hero / Enrich still deferred.
> Consumer anchor (§mio #10): serves the owner's **buy/sell decision** step — "哪个方向强 + 具体股" and "资金在潜伏而价格没起". Not a dashboard for its own sake.
> Authority it obeys: `goal.md` north star (辅助买卖决策; 结论=Tier3/产品面, 不融进 Tier0); `AGENTS.md` §5 (Tier3 conclusions), §4 (PIT); backlog Cap A/C/D/F (`analysis/product_decision_assist_backlog_20260721.md`); Cap A service `services/moneyflow_assist.py`; Cap D `services/decision_intersection.py`.
> Non-authority: does **not** re-open Optuna/Release, does **not** rewrite pulse sensing, does **not** fuse labels into accepted truth.

---

## 0. TL;DR (the ask: primary + 2 alternates, why, phase order)

- **Primary metaphor — 潜伏地形 / "Capital Terrain" (light, focus+context).** A single light-background landscape where the **geometry itself is the decision signal**: footprint = 行业/概念 taxonomy layout, **height = 相对净流入强度 (cum_ratio_20d / relative_ratio_pct, not raw ¥)**, **hue = 窗口涨幅 (window_return_pct)**. → **潜伏 = 高而冷的塔** (capital piled in, price still flat); **抢筹 = 高而热**; **出货 = 塌陷/转冷**. It is the emotional heir to the inspiration image *and* it encodes exactly the two variables the owner cares about. Ships as **2.5D** (see adversarial §6 — full echarts-gl 3D is a deferred hero, not the workhorse).
- **Alternate 1 — 潜伏象限图 / Flow-vs-Return Quadrant (2D scatter/RRG).** x = 窗口涨幅, y = 相对净流入/flow_z, size = |cum_net|, color = behavior. 潜伏 quadrant = 左上 (钱进、价未动). Cheapest, most honest, zero occlusion, fully clickable. **This is the guaranteed-value MVP core** even if the terrain slips.
- **Alternate 2 — 交集桑基/UpSet + 平行坐标 (cross-cutting layer).** For 行业∩概念∩申万∩flow∩return intersections (Cap D) without chart soup: a membership Sankey/UpSet answering "which stocks light up across all chains", plus parallel-coordinates to see a stock's rank across axes at once.

**Why this order:** the quadrant (Alt 1) is a strict subset of the terrain's encoding — same fields, no 3D risk — so it is the honest floor that ships first; the terrain (Primary) is the same signal promoted to a navigable landscape once value is proven; the intersection layer (Alt 2) consumes an API (Cap D) that already exists and is the answer to "cross-cutting without soup".

**Phase order:** Spike (terrain feasibility, throwaway) → MVP (2D quadrant + horizon slider + drill, real APIs) → Enrich (terrain hero + intersection Sankey/parcoords). Detail in §8.

---

## 1. North star → the one signal everything must render

The owner's three explicit wants map to **one composite signal** already computed in the accepted/served bricks:

| Owner want | Signal | Where it already lives |
|---|---|---|
| (1) strong directions + concrete stocks | ranked sectors by relative inflow → drill to members | `flow_board` + `moneyflow/board` (behavior) → `drill` / `intersection` |
| (2) **潜伏** — capital quietly in, price not surged | `flow_regime=accum_in_silent` → `behavior=latent`; i.e. **high `cum_ratio_20d`/`flow_z`/`flow_streak` × low `window_return_pct`** | `moneyflow_assist.behavior_from_regime`, `flow_board` |
| (3) other decision angles | 抢筹/出货 contrast, rotation (rs_4w vs 12w), intersection strength, breakout/连板 | `rotation`, `strongest`, `intersection`, `screener` |

**Design law:** every metaphor below must make the **潜伏 = 高净流入 × 低涨幅** pattern a *pre-attentive* visual (a shape/color you spot without reading numbers). If a chart can't show that at a glance, it is decoration and is cut. `unknown`/thin-window rows render as **explicitly greyed "未形成结论"**, never as 0 (matches service: incomplete window → `status=unknown`).

---

## 2. Data brick ↔ visual-channel inventory (what we can honestly draw)

All read-only Tier3 consumption through existing serve APIs; **沪深A whitelist + PIT already enforced in the services** (`classify_exclusion`, observation-date reads, `status=stale` fail-closed).

| API (existing) | Grain | Fields usable as visual channels |
|---|---|---|
| `GET /pulse/heatmap` | sector × date | net_amount grid → **terrain height / heat surface** |
| `GET /pulse/flow_board` | sector (latest) | `flow_regime`, `flow_z`, `flow_streak`, `cum_ratio_20d`, `cum_net`, `stripe[]` → **height, color, sparkline** |
| `GET /pulse/rotation` (+dc) | sector | `rs_4w`, `rs_12w`, `rank_flow`, `inflow_breadth`, `leading` → **RRG axes, momentum arrows** |
| `GET /pulse/drill` | L1→L2→L3→stock | hierarchy + leaf `form_name`/`limit_times` → **treemap / focus+context drilldown** |
| `GET /pulse/sentiment` | market × date | breadth, 连板天梯, 两融 → **context ribbon / small-multiple timeline** |
| `GET /pulse/strongest` | 涨停热点板块 | `up_stat`, `cons_nums`, `rank` → **secondary heat, independent card (禁跨链拼)** |
| `GET /decision/moneyflow/board` | sector × horizon | `behavior`(latent/chase/distribute), `horizon.relative_ratio_pct`, `window_return_pct`, `conclusion` → **quadrant xy + color + label** |
| `GET /decision/moneyflow/stock/{code}` | stock × horizon×2 planes | dc/tushare planes, `circ_mv`, sector read-through → **per-stock horizon strip** |
| `GET /decision/intersection/strongest` | stock @ 行业∩概念∩申万 | `industry/concept/sw_sectors[]`, `why` → **Sankey/UpSet/parcoords intersection** |
| `GET /screener/form_stage` | stock (form/stage filter) | form_name, axes, `why` → **filter facets + result overlay** |

**Key discipline:** `net_amount` is **not comparable across chains** (dc=东财主力口径 / sw=tushare 全单) — never rank one board across chains; use the **relative ratio** (`cum_ratio_20d`) as the cross-chain-safe height, per `moneyflow_assist` design.

---

## 3. Visualization approaches explored (several, not one)

Each rated on: **Signal fidelity** (does 潜伏 pop?), **Cross-cut** (handles ∩), **Cost**, **Soup risk**, **Brick fit**.

### A. 2.5D / 3D "Capital Terrain" (the inspiration, made light) — **PRIMARY**
Sectors laid on a plane (industry clustered, concept as a second field or toggle), extruded bars/voxels. Height = `cum_ratio_20d` (相对净流入), hue = `window_return_pct` (cool→warm), optional glow = `flow_z` (突然性).
- **潜伏 signature: a tall, cool (blue-grey) tower rising out of a flat cool plain.** 抢筹 = tall+warm; 出货 = collapsing/greying.
- Signal fidelity ★★★★★ · Cross-cut ★★★ (industry+concept+flow+return in one frame; ∩ via facet) · Cost ★★ (3D) / ★★★ (2.5D fake-iso) · Soup risk: **medium** (occlusion, hard exact-read) · Brick fit ★★★★ (`heatmap`+`flow_board`).
- **Risk (see §6):** true 3D buries values → violates north star unless drill gives exact reads.

### B. 潜伏象限图 / Flow-vs-Return Quadrant (RRG-style 2D) — **ALTERNATE 1 / MVP core**
x = `window_return_pct`, y = `relative_ratio_pct` (or `flow_z`), bubble size = |`cum_net`|, color = `behavior`. Four labeled quadrants; **左上 = 潜伏** (钱进价平), 右上 = 抢筹, 右下/左下 = 出货/无关.
- Signal fidelity ★★★★★ (the 潜伏 quadrant is literally a screen region) · Cross-cut ★★ (color=behavior; chain via toggle) · Cost ★ (plain ECharts scatter) · Soup risk **low** · Brick fit ★★★★★ (`moneyflow/board` gives xy+behavior+conclusion directly).
- Horizon slider (1/3/5/10/20/30/60) animates the same bubbles → shows a sector *migrating into* the 潜伏 quadrant over horizons = "开始进场" story.

### C. Hierarchical treemap + flow overlay
Treemap 行业→概念→股; tile size = 成员数/市值; tile fill = behavior color; inset micro-sparkline = `stripe[]`.
- Signal fidelity ★★★ (color shows behavior; height/return harder) · Cross-cut ★★★★ (hierarchy native) · Cost ★★ · Soup risk low-med · Brick fit ★★★★ (`drill`).
- Good **navigator**, weak at the two-variable 潜伏 signal — demote to a drill/overview aid, not primary.

### D. 交集 UpSet / Sankey (membership intersections) — **ALTERNATE 2 part 1**
UpSet bars for "股票同时属于 [强势行业]∩[强势概念]∩[强势申万]" set-combinations, or a 3-column Sankey (行业|概念|申万) with stocks as flows lighting up where all three are strong.
- Signal fidelity ★★★ (strength via color) · Cross-cut ★★★★★ (this *is* the intersection view) · Cost ★★ · Soup risk low · Brick fit ★★★★★ (`intersection/strongest` already returns the sector refs + why).

### E. Parallel coordinates — **ALTERNATE 2 part 2**
Axes = [行业rank, 概念rank, 申万rank, flow_z, window_return]; each stock a polyline; brush the 潜伏 region on flow_z↑/return↓ → highlighted bundle = candidate list.
- Signal fidelity ★★★★ · Cross-cut ★★★★★ (5 axes at once) · Cost ★★ · Soup risk **medium** (line clutter — must brush/fade) · Brick fit ★★★★.

### F. Force / cluster map (concept co-membership graph)
Nodes = sectors/stocks, edges = shared membership; cluster = theme; node color = behavior.
- Cross-cut ★★★★ but Signal fidelity ★★ and Soup risk **high** (hairball). **Cut** for now (pretty, low decision yield — fails §mio "异常漂亮=警报").

### G. Sparklines-on-topology / small-multiples focus+context
Sector grid, each cell a `stripe[]` micro-sparkline colored by behavior; click → focus panel (horizon strip + members).
- Solid **context band**; folds into the terrain's drill panel rather than standing alone.

---

## 4. Cross-cutting 行业∩概念∩申万∩flow∩return **without chart soup**

The trap is one giant chart trying to show 5 dimensions. Solution = **focus+context with one intersection engine, not five stacked charts**:

1. **Context = the terrain/quadrant** shows sector-level flow×return (2 of the 5 dims) with chain as a toggle (3rd dim) — one frame, no soup.
2. **Intersection = Cap D API is the join** — do **not** re-compute intersections in the browser. `intersection/strongest` already returns per-stock `industry_sectors ∩ concept_sectors ∩ sw_sectors` with behavior labels + a `why` sentence. Render it as **UpSet/Sankey (§3D)** — the 4th/5th dims (which chains, how strong) become set-membership, not another axis.
3. **Drill = parallel coordinates (§3E)** only when the owner wants to compare a shortlist across all axes at once — brushed, faded, ≤~40 lines.
4. **One conclusion sentence per row** (already produced by services: `conclusion`/`why`) is the anti-soup anchor — the chart points, the sentence states. §mio: "chart points, UI states the observation".

So the five dimensions are split across **overview (2) → membership join (2) → optional parcoords (all)** — never crammed into a single figure. That is the explicit answer to requirement #3.

---

## 5. Serving the north star (decision / Tier3 / PIT / HS-A)

- **Decision, not display:** every surface carries the service-authored `conclusion`/`why`; rows with thin windows render "未形成结论" (grey), never faked. Behavior labels stay the versioned Tier3 taxonomy (`moneyflow_behavior_v0`), never written back to accepted truth.
- **PIT:** all reads go through existing serve endpoints that already apply observation-date/`available_at` and `status=stale` fail-closed. The frontend adds **no** forward-looking derivation; horizon math is server-side.
- **HS-A whitelist:** `classify_exclusion` / `sql_where_active_a_share` already gate per-stock + membership queries — the viz inherits it (no BJ/ST leakage into the landscape).
- **Freshness honesty:** when `as_of` lags `latest_completed_trade_date` beyond SLA the surface must show the **stale banner + `reason`** (Cap D/5B pattern), not a fresh-looking terrain. A pretty stale terrain is the exact §mio "异常漂亮=警报" trap.
- **Cross-chain guard:** never rank raw `net_amount` across dc/sw; height uses relative ratio.

---

## 6. Adversarial pass on the primary metaphor (multi-role, inline)

> Ran as a structured 4-role debate (proponent / decision-skeptic / taste-critic / synthesis) rather than spawning parallel model subagents — plan-only, and the grounded call needs the full brick context I already hold. §mio: don't ritualize multi-agent on every task; open it on **metaphor-choice** (a directional/over-fit-risk decision), which this is.

**Proponent (3D terrain):** It is the owner's stated inspiration; 3D height for inflow + hue for return makes 潜伏 a literally *tall cool tower* — the single most legible encoding of "钱进价没动". Emotional pull drives daily use.

**Decision-skeptic:** 3D = occlusion + no exact read + hard precise clicking + echarts-gl weight/maintenance. §mio north star is **辅助买卖决策**, and "异常漂亮=先查是不是在骗自己". A rotating landscape where you can't read a sector's exact ratio or click a specific stock is decoration wearing a data costume. The **quadrant (Alt 1) carries the identical two variables** (return-x, inflow-y) with zero occlusion, exact tooltips, trivial clicks, and animates across horizons. If the quadrant already makes 潜伏 = 左上 region, what does 3D *add* besides risk?

**Taste-critic:** Inspiration is dark-audio-cyber. Owner wants **light**. A light 3D terrain is genuinely hard to make premium (3D + light bg → washed-out, plasticky, or the generic "AI teal glow"). Easier to hit a high-end editorial bar in 2D (warm paper, restrained blue accent, red/green A-share semantics already in `theme.ts`). Avoid the AI-purple/cream-serif traps by staying in the existing warm-neutral system.

**Synthesis / verdict:** The two variables are the same in both; the terrain's *only* real advantage is **overview navigation + emotional stickiness**, and its costs land squarely on the decision axis. Therefore:
- **The decision workhorse is 2D (quadrant + horizon + drill) — ships first, always present.**
- **The terrain is promoted as a focus+CONTEXT hero** (a light 2.5D landscape you scan then click into the 2D decision panel), delivered in **Enrich**, and only if the spike proves it reads cleanly on light bg. Start at **2.5D (isometric extruded heat via plain ECharts / CSS)**, escalate to echarts-gl 3D **only** if 2.5D under-delivers — don't pay the gl tax up front.
- **Kill switch (§mio 回滚已产出):** if the terrain buries values in user testing, it reverts to a decorative header band and the quadrant remains the product. The plan is structured so nothing downstream depends on the terrain.

Net: **Primary = "Capital Terrain" as the named metaphor/hero, but its decision guarantee is the 2D quadrant underneath it.** This reconciles the owner's inspiration with the north star instead of choosing one.

---

## 7. Recommendation (restated)

| Rank | Surface | Ships as | Why |
|---|---|---|---|
| **Primary** | 潜伏地形 Capital Terrain | 2.5D hero (context) over 2D quadrant (decision) | Owner's inspiration + geometry-is-signal, de-risked by 2D floor |
| **Alt 1** | 潜伏象限 Flow-vs-Return Quadrant | 2D scatter + horizon slider | Same signal, honest, cheapest, guaranteed-value MVP |
| **Alt 2** | 交集 Sankey/UpSet + 平行坐标 | 2D | Cross-cutting ∩ answer using existing Cap D API, no soup |

---

## 8. Phased delivery

### Phase 0 — Spike (throwaway, `sandbox/`, ~0.5 day)
- Static mock(s) with **fixture JSON** (no live API, no build wiring): can a **light** 2.5D terrain read cleanly? does the quadrant make 潜伏 pop?
- Decide 2.5D-vs-gl-3D from the mock, not from theory. Output = go/no-go on terrain hero.
- **What NOT to do:** no echarts-gl dependency added to `frontend/package.json`, no route, no edge rewrite.

### Phase 1 — MVP (real, the honest floor) — **FIXED 2026-07-22**
- **潜伏象限图** as a panel on `#/market` 资金决策辅助 tab, wired to `GET /decision/moneyflow/board` (already shipped). x/y/size/color from existing fields; horizon slider over 1..60; click bubble → existing `drill`.
- Stale/unknown honesty: thin windows grey "未形成结论", never faked as 0; evidence `phase3_latent_quadrant_mvp_20260722.md`.
- **What NOT to build:** no new backend endpoint (board already returns everything), no 3D, no new taxonomy.

### Phase 2 — Enrich
- **Capital Terrain hero** (per spike verdict: 2.5D first) as the overview above the quadrant; click ridge → quadrant/drill focus.
- **交集 Sankey/UpSet + parallel-coordinates** tab wired to `GET /decision/intersection/strongest` (shipped) — the cross-cutting layer.
- Optional: sentiment context ribbon (small-multiple) as page header.
- **What NOT to build (hard):** force/hairball graph (§3F), any browser-side intersection re-computation, any surface that renders `unknown` as 0, cross-chain `net_amount` ranking, Optuna/Release, pulse-sensing rewrite, second orchestration.

### Explicit non-goals (tonight & this track)
No production edge changes tonight (plan-only). No echarts-gl commitment before the spike. No fusing labels into Tier0. No mass backfill / margin thaw. No greenfield rewrite of pulse.

---

## 9. Aesthetic direction (light, anti-generic)

- **Reuse the existing system, don't invent one:** `frontend/src/theme.ts` — warm paper `#f7f7f5`, white panels, restrained blue accent `#3b66d4`, **A-share red-up `#d4342c` / green-down `#0f8a4e`**. Terrain hue and quadrant color **must** obey red-up/green-down (Chinese-market semantics), so 抢筹(surged)=warm/red, 潜伏(cool)=blue-grey, 出货=green/grey.
- **Anti-traps (§taste):** no dark cyber, no neon teal glow, no AI-purple gradients, no cream + serif "elegant report" cliché. Stay warm-neutral + one restrained accent + semantic red/green only.
- **Terrain-on-light craft:** soft long shadows, low-saturation elevation ramp (paper→slate for cool, paper→ember for warm), thin hairline grid — editorial cartography, not videogame terrain. Depth via shadow/occlusion, not glow.
- **Motion:** subtle (horizon-slider morph, hover lift); no autoplay spectacle. The signal, not the animation, is the product.

---

## 10. Risks / honesty gates (must hold before any ship)

1. **Stale-as-pretty** — terrain/quadrant on lagging `as_of` → mandatory stale banner + empty/greyed rows (§5). Highest §mio risk.
2. **3D-buries-value** — mitigated by 2D floor + drill exact reads + kill switch (§6).
3. **Cross-chain amount blending** — banned; relative ratio only.
4. **unknown→0** — banned; grey "未形成结论".
5. **Scope creep to Tier0** — labels stay Tier3; no writeback.
6. **gl weight** — deferred behind spike; not added to deps until justified.

---

## 11. Residual / next

- Spike mocks live in `sandbox/viz_spike_20260722/` (throwaway; delete after go/no-go).
- No API work required for MVP (Cap A board suffices); Enrich uses Cap D (shipped). If per-stock terrain drill needs a sector-grid endpoint, that is a small additive read — not in this plan's scope.
- Owner decision needed before Phase 1: schedule this against the paused E/F remeasure track, or run parallel (it touches only frontend + read-only Tier3, non-overlapping).
- Pointer added to `goal.md` (viz plan) — this doc is the owner for the viz-metaphor decision.

Label: **Phase 1 MVP FIXED** (quadrant on market assist tab). Residual owner: Enrich (terrain hero + Sankey/parcoords) only if owner schedules; kill switch intact if terrain buries values.

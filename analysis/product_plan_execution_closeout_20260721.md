# Product plan execution closeout — 0r.1 → 5B (2026-07-21)

> **SUPERSEDED as roadmap authority** by `analysis/MASTER_SYSTEM_TOPLEVEL_REEVAL_20260722.md`（+ `DOC_AUTHORITY_20260722.md`）。本文件保留为 0r.5b→5B 执行证据。

> Status: evidence-only / closeout
> Authority: `product_plan_reeval_stock_dossier_20260721.md` (schedule) +
> `foundation_phase_reeval_20260721.md` (foundation ordering) + `goal.md` (live ledger)
> Scope: this document closes out the successor-agent mandate that ran the plan from
> **0r.5b through 5B** (0r.1–0r.4 and 2F/E were already FIXED/PARTIAL when this
> mandate started — included here only for a complete phase map, not re-litigated).

## Mandate recap

Execute the product plan to completion without asking the human; ambiguous
decisions resolved by adversarial synthesis (self-authored two-position debate,
disclosed, since no Task-subagent tool was available in either successor
session); hard bans held throughout (Optuna / StrategyRelease / mass org /
margin thaw); 沪深A-only on sensing; push after each knife.

## Phase map (0r.1 → 5B)

| Phase | Label | Commit | Evidence |
|---|---|---|---|
| 0r.1–0r.3 沪深A serve whitelist + formal continuity | **FIXED** | `6afea30fc` | `analysis/foundation_bj_dualpath_ashare_whitelist_20260721.md` |
| 0r.4 ths_hot 发布窗 | **FIXED** mechanism (2026-07-22) — typed `pending_publish`; live watermark catchup ops-only | `6afea30fc` + residual-clear | `plan_residual_reconcile_20260722.md` |
| Cap E 工作台分步节点 (pipeline step cards) | **FIXED** subset | `9706f150d` + `799b7412d` | `analysis/capability_e_pipeline_step_cards_20260721.md` |
| 2F 股票档案 deepen (episode cycle/returns + C-light tabs) | **FIXED** subset (F MVP + this-stock episode overlay) | `50817db0f` | dossier `#/stock/:code`; `holders_stock_dossier_lineage_audit_20260721.md` (勿重审) |
| 0r.5b 持仓水位/SLA + ops 拆分 + 机构 honesty | **FIXED** | `387eb79b5` | `analysis/foundation_holders_wm_ops_counters_20260721.md` |
| 3A+3C 资金流决策辅助 + tabs | **FIXED** subset | `4f70adc08` (pushed) | `analysis/capability_a_moneyflow_assist_20260721.md` + `analysis/decision_3a_moneyflow_assist_20260721.md` |
| 4D 交集最强股 | **FIXED** (3-chain DC∩概念∩申万; 2026-07-22 residual clear) | `a959baf06` + residual-clear | `analysis/capability_d_intersection_strongest_20260721.md` + reconcile |
| 5B 形态/阶段选股面 | **FIXED** (+ F/5B production-read cutover hybrid; 2026-07-22) | `8fb0192f9` + residual-clear | `analysis/capability_b_stock_screener_20260721.md` + reconcile |

This mandate's own scope (0r.5b → 5B) is now **fully closed** — every phase in
the ordered backlog (`product_decision_assist_backlog_20260721.md`: A/B/C/D/E,
F MVP) has shipped at least a FIXED subset with disclosed residuals; no phase
was skipped or silently deferred without a written reason.

## What shipped this session (4D + 5B)

### Cap 4D — 交集最强股 (commit `a959baf06`)

- `services/decision_intersection.py`: DC 行业∩概念 strong-sector member
  intersection, reusing `moneyflow_assist.build_sector_board` behavior labels
  (chase/latent) and `market_pulse_serve_read.dc_member_*` PIT membership.
- API: `GET /api/v3/decision/intersection/strongest` + `/intersection/stock/{code}`.
- Freshness: chain as-of must match across dc_industry/dc_concept and not lag
  the calendar beyond SLA → `status=stale`, never a fake-fresh board.
- UI: `#/market` 3rd tab「交集最强」; dossier `交集` tab flipped from
  `soon="4D"` to enabled.
- Tests: `tests/test_decision_intersection.py`, 8 cases, blocking tier.
- Residual: sw_industry as a 3rd intersecting chain needs an L3→board leaf
  rollup first (documented, not silently dropped).

### Cap 5B — 形态/阶段选股面 (commit `8fb0192f9`)

- `services/stock_screener.py`: filters `fact_stock_form_daily` — the exact
  same Tier1 brick and read path as the dossier F 形态·阶段 tab (legacy direct
  read, matching F's not-yet-cutover state).
- API: `GET /api/v3/screener/options` (live facet counts, corrects the axis
  vocabulary drift found in the process — dossier's zh-map referenced unused
  values `clean`/`mixed`/`light`; the real live values are
  `trending`/`choppy`/`heavy`/`shrink`/`normal`, captured in a new,
  independent config so 2F's shipped code was not touched) + `GET
  /api/v3/screener/form_stage` (multi-select form + 4-axis + breakout filter,
  plain list output, no scoring/ranking model).
- Freshness: global `MAX(trade_date)` vs `calendar.latest_completed_trade_date`
  SLA gate, same shape as Cap 4D.
- UI: `#/market` 4th tab「形态/阶段选股」; result rows click through to
  `#/stock/:code`.
- Tests: `tests/test_stock_screener.py`, 10 cases, blocking tier.
- Residual: cutover to `resolve_tier12_production_read` deferred until F
  itself adopts it (must move together, not independently); dossier axis-label
  dict drift not fixed (out of this knife's scope, documented).

## Cross-cutting honesty invariants held across all shipped decision-assist caps

- **Fail-closed freshness**: 3A (`moneyflow_assist`), 4D (`decision_intersection`)
  and 5B (`stock_screener`) all gate on a calendar-lag SLA and degrade to
  `status=stale` + empty output rather than ever serving a silently outdated
  surface. This mirrors the pre-existing `/pulse/strongest` contract, not a
  fourth independent implementation of the same idea.
- **No invented "strength"**: 4D reuses 3A's `behavior_from_regime` labels
  rather than forking a second strong/weak taxonomy.
- **No scoring/ranking model anywhere in the decision-assist surfaces**: 3A/4D
  output ranked-by-evidence lists with `why`/`conclusion` sentences; 5B is a
  plain filter with no ordering beyond `stock_code`. Zero Optuna, zero
  StrategyRelease, across all three.
- **HS-A gate**: every per-stock/per-code endpoint added this session
  (`moneyflow/stock/{code}`, `intersection/stock/{code}`) rejects non-沪深A
  codes via `classify_exclusion`; the screener additionally applies
  `sql_where_active_a_share` defensively at the query layer even though the
  underlying `fact_stock_form_daily` population was empirically verified to
  already be 沪深A-only.
- **Delivery discipline**: each knife = one Rule 10 self-check + one
  `safe_commit.sh` (no `--no-verify`, no `git add .`, explicit file staging)
  + `git push`. FEATURE_MAP.md / `data/lineage/graph.json` were regenerated
  from the actual staged/worktree snapshot before every commit (not copied
  from a stale prior run) — for the lineage gate specifically, the table-node
  count is sourced live from `information_schema` at build time, so the graph
  was rebuilt immediately before each `safe_commit.sh` invocation to minimize
  the race window against concurrent live-DB state.

## Residual ledger

| Residual | Phase | Status (2026-07-22) |
|---|---|---|
| 0r.4 ths_hot 发布窗 | 0r.4 | **FIXED** mechanism (`pending_publish`); live `20260721` catchup = ops (post-22:30; not missing token) |
| sw_industry 3rd chain intersection | 4D | **FIXED** — L1 PIT `l1_code` rollup; 3-way freshness |
| `resolve_tier12_production_read` cutover for screener+F | 5B/2F | **FIXED** hybrid via `form_production_read` (full accepted-only blocked on payload axes) |
| Dossier axis-label dict (`clean`/`mixed`/`light`) | 2F | **FIXED** → trending/choppy + heavy/shrink/normal |
| Optional intersection badge on dossier F header | 4D (plan §3.5 "later") | Still deferred by plan — not a gap |
| Accept enrich purity/vol/sub | Tier1 | Open P2 — see `plan_residual_reconcile_20260722.md` |

### Closeout residual completed (this follow-up)

Peer successor found schedule authority tables still saying `scheduled`/`later` after
code + closeout had shipped. **Docs-only sync** applied to
`product_plan_reeval_stock_dossier_20260721.md` §0/§2/§6/§7 and
`product_decision_assist_backlog_20260721.md` §7/§8/§9 so A–F / 0r statuses match
live SHAs. No code change; verification = 4D+5B tests still **18 passed**.

### Residual-clear knife (2026-07-22)

All four code residuals above cleared; reconcile
`analysis/plan_residual_reconcile_20260722.md`. Verification: decision_intersection +
dossier + screener + pending_publish unit tests **25 passed** in targeted run.

## Guardrails held (no violations this session)

- Optuna / StrategyRelease / mass org / margin thaw: **not touched**.
- 沪深A-only on sensing/decision-assist: enforced via `classify_exclusion` +
  `sql_where_active_a_share` at every new per-stock/screener endpoint.
- No silent cutover flips, no accepted-canonical rewrite, no PIT-availability
  edits.
- Every ambiguous design decision (4D chain scope/strong-definition/freshness/
  per-stock consistency; 5B read-path/facet-source/freshness/frontend-host) was
  logged via self-adversarial synthesis in `decision_4d_*.md` / `decision_5b_*.md`
  before implementation, per mandate.

## Verdict

**FIXED** (mandate scope 0r.5b → 5B fully executed, pushed, tested, documented).
Residual owner = next explicitly-scheduled session (see ledger above); none of
the residuals block the shipped capability from being used as-is.

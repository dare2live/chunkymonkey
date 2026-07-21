# Foundation daily_update unblock (2026-07-21)

> 状态：evidence-only
> Follow-up to E2E PARTIAL: `analysis/foundation_e2e_frontend_update_20260721.md` (`a78708eb8`)
> Wall clock: 2026-07-21 ~21:00–21:15 Asia/Shanghai
> Overall: **FIXED** (foundation knives 1–2 + operational DC/pulse catchup + UI button wire)

## Verdict

| Knife | Label | Evidence |
|---|---|---|
| 1 margin preflight deadlock | **FIXED** | `a84e0867e` |
| 2 formal daily/ST orchestrator | **FIXED** | `8bcc37dad` |
| 3 DC/sector pulse lag | **FIXED** (ops catchup; no code knife) | raw DC+moneyflow+limit_cpt → `20260721`; pulse APIs `20260721` |
| UI「数据更新」button | **FIXED** | workbench `#/workbench` → `POST /api/v3/ops/jobs/daily_update/run` + status poll / log tail |

## Before → After

### Knife 1 — margin all-due deadlock

**Before (E2E):**
```text
POST /api/v3/ops/jobs/daily_update/run
→ DEGRADED: PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked (exit 4)
```

**After:**
- `margin.sync_policy=on_demand` while `execution_policy.mode=disabled` / `scope_blocked` (product stays frozen; no thaw)
- `automatic_domains()` no longer includes `margin` (42 domains)
- acquire margin drain/shadow hard-gate runs **only** when margin is enabled
- Live: `ensure_pipeline_sync_ready` → `PASS domains=42`
- Explicit `chunkyctl sync --domain margin` still hard-stops on `scope_blocked`

### Knife 2 — formal daily/ST on_demand catchup

**Before:** formal `daily`/`stock_st` are `on_demand`, so they never ride `--all-due`; UI path pulled nothing for K/ST.

**After:** `pipeline.acquire._sync_formal_on_demand_security_days` before all-due drain:
- resolves `eligible_end` (`trigger_mode=manual`)
- if `accepted_partition` missing → modular `run_domain` / land_then_accept for **that single day**
- if already accepted → skip (no history mass-fill)

Live plan @ accepted frontier:
```json
{"domain":"daily","action":"skip","reason":"latest_eligible_already_accepted","eligible_end":"20260721"}
{"domain":"stock_st","action":"skip","reason":"latest_eligible_already_accepted","eligible_end":"20260721"}
```

Preserved: org incremental-only; formal daily/ST semantics; no ~830k org mass refresh.

### Knife 3 — DC / pulse cards

**Before:** `build_dc_industry_view` failed (`industry/concept=20260720` vs `member=20260716`); `flow_board`/`strongest` stuck at `20260720`.

**After (targeted sync, same domains all-due would pull once preflight unblocked):**
- synced `dc_index` / `dc_member` / `dc_daily` / `moneyflow_ind_dc` / `moneyflow_dc` / `limit_cpt_list` / `limit_list_d` through `20260721`
- DC dims rebuilt (`idx=20260721 mem=20260721`)
- `mart_sector_pulse_daily` / `mart_market_pulse_daily` max **`20260721`**
- HTTP: `flow_board` **`20260721`**, `strongest` **`20260721`**

## Knife 4 — UI「数据更新」button (follow-up)

**Before:** `#/workbench` = PlaceholderPage; Layout nav disabled; zero frontend refs to `ops/jobs`.

**After:**
- Nav「工作台」enabled → `WorkbenchPage`
- Primary button「数据更新」→ `POST /api/v3/ops/jobs/daily_update/run` (same path backend expects; vite `/api` proxy, no extra auth)
- Status: `GET /api/v3/ops/jobs/daily_update` on load + poll while `writer_busy` / `process_hint_running`
- Surfaces: trigger errors (e.g. 409 writer busy), alert flags (preflight blocks), `log_tail`
- Files: `frontend/src/api/ops.ts`, `frontend/src/pages/WorkbenchPage.tsx`, `App.tsx`, `Layout.tsx`, `styles.css`
- `npm run build` (tsc + vite) **PASS**
- Out of scope preserved: no tab redesign / moneyflow assist / form picker

## Residuals

1. **Full `daily_update` wall-clock acquire** — not re-run end-to-end as a 42-domain drain after unblock; preflight + formal planner + targeted DC sync prove the former hard-stop is gone. Next click/API run is the first full-path wall-clock proof.
2. **Alert flags** — `/tmp/chunkymonkey_ALERT_daily_update*.flag` may still be stale from the failed E2E run (hygiene, not foundation block).
3. Product moneyflow analysis UI / Optuna / StrategyRelease — out of scope (unchanged bans).

## Owner process Q&A（证据摘要）

### Q1. 有没有真正走完整条路径？

**诚实结论：分路径，不是「一键满血 42 域全跑通」。**

| 路径 | 实际走过？ | 证据 |
|---|---|---|
| UI 按钮 → API | 初 E2E **没按钮**；后用 API 等价触发；现按钮已接线 | E2E 笔记 + Knife 4 |
| `daily_update` 全链 | 初跑 **卡在 preflight**（margin）；解阻后 **未**再跑完整 42-domain 墙钟 acquire | unblock §Residuals |
| 模块化 secondary | **走过** land→accept→derive→process（单日增量） | E2E §2–3 |
| DC/pulse catchup | 解阻后 **定向 sync** 到 `20260721`（非全量 dragon 重跑） | unblock Knife 3 |

形状（模块化 caller-only，非 fused dragon）：

```text
land/landing → validate/accept → derive(qfq/form) → process(DC/sector/pulse) → serve APIs
```

`daily_update` 编排器意图仍是五段：preflight → acquire → clean → process → store；formal daily/ST 现由编排器在 all-due 前做 latest-eligible `land_then_accept`。

### Q2. 遇到了哪些问题？

1. **无 UI 按钮** — workbench 占位；只能 API 等价。
2. **margin all-due 死锁** — frozen `scope_blocked` 仍进 all-due → preflight exit 4。
3. **formal daily/ST 被 all-due 跳过** — `on_demand`，一键路径原先拉不到 K/ST。
4. **pulse 滞后** — K 已 accept 但 DC/sector 未跟上 → `flow_board`/`strongest` 停在 `20260720`，直至定向 catchup。
5. **org 正确 skip** — plannable 已本地 → incremental skip（禁 ~830k 重拉）。

### Q3. 数据是先落 raw 再清洗加工吗？

**是（transport 轴），且 formal 域走 landing→accepted，不是「只写一张 raw 业务表」。**

- **Landing / raw evidence**：provider 响应先入 `landing_tushare_*`（及 registry raw 表，视域而定）。
- **Validate/accept**：`accepted_partition` + `canonical_*`（例：`20260721` daily landing/canonical/accepted 均为 5525）。
- **Derive**：qfq / form（`price_kline_qfq_tushare`、`fact_stock_form_daily`）。
- **Process**：segments / market_pulse / DC dims 等 mart。
- **Serve**：`/api/v3/pulse/*` 等读模型。

物理上多在同一 DuckDB；逻辑分层见 `analysis/db_layering_toplevel_design_20260721.md`（禁按加工阶段拆第二库）。Legacy 旁路 raw daily 前沿可仍落后 accepted（预期，不以 legacy raw 冒充 publication）。

## Commits

- `a84e0867e` — fix(foundation): unblock daily_update preflight under frozen margin
- `8bcc37dad` — feat(foundation): daily_update pulls latest eligible daily/ST
- `e6c12d91c` — docs(foundation): daily_update unblock evidence 20260721
- （本刀）feat(frontend): wire workbench 数据更新 → ops daily_update job

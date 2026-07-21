# Foundation E2E — frontend「数据更新」path (2026-07-21)

> 状态：evidence-only
> Wall clock: **2026-07-21 ~20:40–20:56 Asia/Shanghai** (after market close).
> Mission: real foundation E2E from UI Update button (not CLI-primary).
> Authority: `foundation_phase_reeval`, brick architecture, FND-GATE, gate_redesign, org incremental-only.
> Overall: **PARTIAL** (UI path blocked; modular incremental path proved; serve lag residual).

## Verdict matrix

| Section | Verdict | One-line |
|---|---|---|
| 1. Frontend Update button | **FAIL** | No「数据更新」control in edge React UI; workbench = placeholder |
| 1b. API-equivalent trigger | **PASS** (trigger) / **FAIL** (run) | `POST /api/v3/ops/jobs/daily_update/run` accepted; preflight hard-stop |
| 2. What it decides to pull | **FAIL** (UI/API) / **PASS** (modular secondary) | UI/API pulled **nothing**; modular path pulled single-day `20260721` gaps only |
| 3. Pipeline shape + DB | **PASS** (modular) | `land_then_accept` (not fused dragon); landing+accepted+canonical for `20260721` |
| 3b. Org incremental | **PASS** | plannable `2026-03-31` already local → skip; historical holes = log-not-fill |
| 4. Consumer APIs | **PARTIAL** | Pulse/ops HTTP 200; several surfaces still `trade_date=20260720` until sector/DC catch up |
| 5. Continuity / gate nodes | **PASS** | `doctor.population_readiness` caught missing `20260721` then cleared after accept; FND-GATE still PASS |

---

## 1. Frontend「数据更新」button

### What exists

| Layer | Evidence |
|---|---|
| Backend ops API | `backend/routers/ops_manual_run.py` — `GET/POST /api/v3/ops/jobs[/{job}/run]`; job `daily_update` → `scripts/daily_update.sh` → `services.pipeline.run` |
| Frontend source | `frontend/src/**` — **zero** references to `ops/jobs`, `daily_update`, or「数据更新」 |
| Nav | `Layout.tsx`: 机构档案 / 观察账本 / 工作台(占位) / 市场感知 |
| Workbench | `#/workbench` →「占位页 — 尚未实现」 |

### Browser attempt (UI first)

- Started Vite `http://127.0.0.1:5173/app/` + backend `start.command` port **8000**.
- Snapshotted `#/institutions`, `#/market`, `#/workbench`.
- **No Update button** on any live page.

**Blocker:** intended manual-trigger surface (`ops_manual_run` "前端按钮面板") was never wired into edge React. UI cannot start the update.

### API-equivalent (same HTTP the missing button would call)

```http
POST http://127.0.0.1:8000/api/v3/ops/jobs/daily_update/run
→ {"job":"daily_update","accepted":true,"pid":88537}
```

Log (`/tmp/chunkymonkey_daily_update_20260721.log`):

```text
=== ChunkyMonkey daily update 20260721 ===
  dry=0 skip_sync=0
DEGRADED: PREFLIGHT BLOCK: sync_execution_blocked:margin:scope_blocked (四阶段未启动; exit 4)
```

**Label:** API-equivalent of the missing button — **not** a successful foundation pull.

---

## 2. What gets pulled (decision surface)

### UI / daily_update all-due

Preflight enumerates `automatic_domains()` (= every registry domain **except** `sync_policy: on_demand`), then `preflight_execution_policies` fail-closed on disabled domains.

Live inventory:

| Domain | In all-due? | execution_policy | eligible_end @20:43 |
|---|---|---|---|
| `daily` | **no** (`on_demand`) | enabled / authorized_manual_generation | **20260721** published |
| `stock_st` | **no** (`on_demand`) | enabled | **20260721** |
| `trade_cal` | **no** (`on_demand`) | … | **20260721** |
| `margin` | **yes** | **disabled / scope_blocked** | 20260720 (t+1) |
| `adj_factor` / `daily_basic` | yes | enabled | **20260721** |

So even if margin were removed from all-due, **formal K-line/ST would still not ride the UI all-due drain** — they are deliberately `on_demand` and require explicit `--start/--end`.

### Modular secondary path (after UI/API block) — incremental only

| Domain | Command | Result |
|---|---|---|
| daily | `chunkyctl sync --domain daily --start 20260721 --end 20260721` | ok; **5525** rows; `transport=land_then_accept`; batch `daily:20260721:20260721T124423Z` |
| stock_st | same pattern | ok; **209** rows; `land_then_accept` |
| adj_factor | `--start/--end 20260721` | ok; **5542** rows |
| daily_basic | `--start/--end 20260721` | ok; **5525** rows |

**No mass ~830k org refresh. No full-history dump.** Single eligible day only.

Org gap report (write-capable ensure_tables, then read):

```json
{
  "plannable": "2026-03-31",
  "local_has_plannable": true,
  "local_periods": ["2019-03-31", "2025-12-31", "2026-03-31"],
  "missing_count": 27,
  "status": "ok"
}
```

→ latest plannable **present** ⇒ incremental path must **skip**; 27 missing = older holes (log-not-fill / explicit backfill knife only).

---

## 3. Pipeline shape after pull

### Modular formal domains (measured)

```text
provider → landing_tushare_{daily|stock_st}
        → validate/accept
        → canonical_* + accepted_partition
        → (separate) derive qfq / form
        → (separate) pipeline process (segments / market_pulse / …)
        → serve pulse APIs
```

Sync JSON explicitly: `"transport": "land_then_accept"`, `"publication": "accepted_nominal_ohlcv_partition"` — **not** legacy fused `capture_and_publish` dragon.

### DB spot-check `20260721`

| Artifact | Count / value |
|---|---|
| `accepted_partition` nominal_ohlcv | partition `20260721`, rows **5525**, batch `daily:20260721:20260721T124423Z` |
| `landing_tushare_daily` for that batch | **5525** |
| `canonical_nominal_ohlcv_daily` `2026-07-21` | **5525** |
| `accepted_partition` stock_st | **209** |
| `landing_tushare_stock_st` | **209** |
| `canonical_stock_st_daily` | **209** |
| `price_kline_qfq_tushare` after derive | max **2026-07-21**, n=**5525** |
| `fact_stock_form_daily` after derive | max **20260721**, n=**5117** (`added_days=1`) |

### UI `daily_update` dragon vs modular

| Path | Shape |
|---|---|
| Intended UI (`pipeline.run`) | preflight → acquire(`--all-due --drain` + holders/qfii/org) → clean(qfq) → process → store |
| Live UI result | **stopped at preflight**; zero acquire |
| Working foundation path today | **caller-only modular** `chunkyctl sync` land→accept + `derive` + optional `pipeline process` |

Residual: S3「一键 UX 编排器调用 S1→S2」**not wired** for formal daily/ST into `daily_update` (they are `on_demand` + margin freeze blocks all-due).

---

## 4. Consumer APIs

OpenAPI live routes of interest: `/api/v3/ops/*`, `/api/v3/pulse/*`, `/api/v3/paper/*`. **No** dedicated `/universe` or `/form` HTTP surface on this edge app.

| Endpoint | HTTP | Notes after accept+derive+process |
|---|---|---|
| `GET /api/v3/ops/jobs` | 200 | Shows `daily_update` + alert flags from failed UI run |
| `GET /api/v3/pulse/sentiment` | 200 | `status=ok`, 120 days |
| `GET /api/v3/pulse/heatmap` | 200 | ok |
| `GET /api/v3/pulse/rotation` | 200 | ok |
| `GET /api/v3/pulse/flow_board` | 200 | still `trade_date=20260720` (sector/DC lag) |
| `GET /api/v3/pulse/strongest` | 200 | still `trade_date=20260720` |
| `GET /api/v3/pulse/warnings` | 200 | ok |

`pipeline process` (2026-07-21 20:54):

- `segments` added_days=1 (5525)
- `market_pulse` market_added_days=1 → `mart_market_pulse_daily` max **20260721**
- DC namespace materialize **degraded** → sector pulse max stayed **20260720**
- stage exit 1 / check_fail

**Coupling:** pulse HTTP is healthy but **mart-coupled**; accepted K-line alone does not move all pulse cards. DC snapshot step is a separate continuity node.

---

## 5. Where continuity / completeness is checked

| Node | Role in this E2E | Observed |
|---|---|---|
| `pipeline.preflight` / `preflight_execution_policies` | Blocks all-due if any selected domain disabled | **Caught** margin `scope_blocked` → exit 4 (also **deadlocks** whole UI update while margin frozen) |
| `chunkyctl doctor` → `population_readiness` | Accepted calendar/Kline/ST visibility | **Before** modular accept: FAIL `no_accepted_partition … partition=20260721`; **After**: **PASS** |
| `doctor` → `alert_flags` | Wrapper / degraded flags | **WARN** (failed UI `daily_update` + prior degraded flag) |
| `doctor` → `data_health` | Domain health rollup | PASS (48 green) |
| `doctor` → `foundation_done` / FND-GATE | Phase checklist F1–F10 | **PASS** / `phase_closure_ready=true` (static closure ≠ live serve freshness) |
| `check_foundation_done.py` | Same FND-GATE | PASS 10/10 |
| acquire margin shadow parity | Would block acquire even under `--skip-sync` if reached | Not reached tonight (preflight first) |

**Which node catches live frontier gaps?** → **`population_readiness`** (doctor).  
**Which node blocks the UI button path today?** → **`preflight` on frozen margin still in all-due**.  
**FND-GATE** stays green through this incident — it does **not** replace live readiness.

---

## Residuals (ordered)

1. **Wire or retire UI Update:** either add edge「数据更新」panel calling `/api/v3/ops/jobs/daily_update/run` + status tail, or stop advertising frontend manual trigger in ops router comments.
2. **Unblock `daily_update` under margin freeze (fail-closed, no thaw):** remove `margin` from all-due (`sync_policy: on_demand`) **and** stop requiring clean margin drain/shadow parity as a hard acquire gate while `execution_policy.mode=disabled` — else UI/API path stays permanently exit-4.
3. **S3 gap:** teach orchestrator to call modular S1→S2 for latest eligible `daily`/`stock_st` (and needed adj/basic) — today all-due **excludes** them.
4. **Serve lag:** DC industry view materialize failed in process; sector pulse / flow_board / strongest still on `20260720`.
5. **Alert hygiene:** clear or supersede `/tmp/chunkymonkey_ALERT_daily_update*.flag` after intentional diagnosis.

---

## Commands / artifacts

- UI: `http://127.0.0.1:5173/app/` ; API: `http://127.0.0.1:8000`
- Logs: `/tmp/chunkymonkey_daily_update_20260721.log`, `/tmp/cm_e2e_20260721/*`
- Doctor JSON: `/tmp/cm_e2e_20260721/doctor_after.json`, `doctor_final.json`

## Label

**PARTIAL** — foundation incremental for `20260721` **works** on modular land→accept→derive; **UI/API one-click path FAIL** (missing button + margin all-due deadlock); consumer APIs up with sector/DC freshness residual.

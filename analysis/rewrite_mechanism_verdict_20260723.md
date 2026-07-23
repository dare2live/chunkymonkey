# Rewrite mechanism verdict — 2026-07-23

> **生命周期**：evidence-only（裁决 + 本刀已删清单；非 Continuity cosmetics）
> Owner Q：重写脚本应用工具检查后从代码删除，勿定期检查修复；评估哪些 rewrite **必须**保留。
> Tools：`moth snapshot/coupling` · `codegraph explore` · `rg` DELETE→INSERT / DROP+CTAS / CREATE OR REPLACE / re-land / backfill / ensure_table / rebuild · prior `db_refill_after_delete_audit_20260723.md` · `global_cleanup_rebuild_plan_20260723.md` · Occam
> Label: **FIXED**（裁决落地 + 明确可删路径已删）

---

## 0. Executive (中文)

| 问 | 答 |
|---|---|
| 重写机制是否必须？ | **部分必须**。必须的是**语义**（幂等覆盖 / 不可变证据 / latest-adj rebase），不是「删了再偷偷灌回」或「定期修盘」脚本。 |
| 正解是什么？ | 根因进**更新流程/模块边界**：orphan → **删能力**；同 payload 重落 → **skip**；CTAS 洞 → **模块内 reclaim**。禁止「观测到 bloat 再补跑 fixer」。 |
| 本刀删了什么？ | E0 `ingest_stk_holdertrade_canary.py`；三域 `rewrite_legacy` CLI/参数真写回分支（legacy DELETE→INSERT cargo-cult）。 |
| 为何不是定期修？ | 定期修 = 症状环。已有 skip / in-module compact / registry 墓碑后，再留 repair 脚本只制造第二条写路径。 |

---

## 1. Verdict table — every live rewrite path

Legend: **KEEP** = still-owned domain needs it · **DELETED** = removed from codebase this knife or prior · **RETIRED** = capability gone (no writer).

### 1.1 Class A — Sync grain / partition replace (DELETE→INSERT)

| Path | Mechanism | Verdict | Why |
|---|---|---|---|
| `sync_runner` `merge_grain` / `replace_partition` / `replace_snapshot` | DELETE matching grain/partition → INSERT | **KEEP** | Tier0 idempotent republish; bans MERGE false-active rows. Do not delete. |
| `margin` / `margin_detail` `replace_partition` | day-atomic replace | **KEEP** | Owned domain; bounded catchup may re-fetch deleted day. |
| `stock_basic` / list `replace_snapshot` | full snapshot wipe+load | **KEEP** | Current listing truth; false-active ban. |
| `trade_cal` full_refresh | manual/on_demand | **KEEP** | Calendar generation; not daily auto. |
| drain gap refill | gap days only | **KEEP** | Owned continuity repair inside sync, not orphan revive. |
| `ensure_table` `CREATE … AS SELECT * LIMIT 0` | first-write bootstrap | **KEEP** (narrow) | Needed for empty/missing table; refill after DROP only if domain still registered. |
| `stk_factor_pro` sync domain | was on_demand by_ts_code | **RETIRED** (prior knife) | Orphan; table DROP + registry tombstone → KeyError. Pattern = delete **capability**. |

### 1.2 Class B — Derive full rebuild (DROP+CTAS / CREATE OR REPLACE)

| Path | Mechanism | Verdict | Why |
|---|---|---|---|
| `price_kline_qfq_tushare` DROP+CTAS | latest-adj full rebase | **KEEP** (semantic) | See §2 — full CTAS is still must for tip-factor rebase; reclaim **in-module** after CTAS (Knife 3), not periodic compact. Incremental = deferred product knife. |
| `build_dc_industry_view` shadow CTAS→rename | current dim publish | **KEEP** | Cross-section publish; process delta can skip. |
| `institution_profile` `CREATE OR REPLACE` / `rebuild_all` | wipeable L2 | **KEEP** + delta-gate | Must when holders frontier moves; nightly unconditional rebuild = **banned** (already gated). |
| `market_pulse` / `technical_states` / `segments` `rebuild_all` | rare / CLI `--rebuild` | **KEEP** (escape) | Default = incremental `build_latest`; full rebuild is owned escape, not cron. |
| B2 fact publish DROP+CTAS (`*_publish.py`) | accepted fact materialize | **KEEP** | Publication swap; owned serve path. |

### 1.3 Class C — Immutable landing append / hash storm

| Path | Mechanism | Verdict | Why |
|---|---|---|---|
| holders / org / stk land → new `batch_id` uuid | append evidence | **KEEP** | Immutable landing by design. |
| ACCEPTED + same `payload_hash` → skip | `holders_top10_skip_land` | **KEEP** (root fix) | Stops ~32× storm; shipped `67cd81c27`. |
| Periodic landing dedupe / DELETE-landing repair script | n/a | **ABSENT → stay deleted** | `rg` found **no** `dedupe_landing` / holders compact repair CLI. Correct: skip ≠ bare DELETE landing. |
| Historical ~32× multiplicity | retention later | **KEEP data** | Do not DELETE landing as cleanup; archive knife later. |

### 1.4 Class D — Compact / lifecycle (reclaim ≠ refill)

| Path | Mechanism | Verdict | Why |
|---|---|---|---|
| `db_compact.py` | ATTACH-copy shrink | **KEEP** | Ops reclaim after DROP/CTAS; **does not refill**. |
| qfq `compact_market_after_ctas` | post-CTAS default | **KEEP** (in-module) | Root for free-block recurrence; orchestrator must stay free-page-ignorant. |
| `db_lifecycle_delete.py` | archive+DROP | **KEEP** | Manual orphan retirement. |
| `db_dead_table_audit.py` | dry-run / 0-row 0-ref DROP | **KEEP** | Manual hygiene; not rewrite/refill. |
| Orchestrator / cron periodic compact | — | **BANNED / absent** | `manual_only`; doctor asserts no data cron/launchd. |

### 1.5 Class E — Legacy dual-write rewrite / canary / mass CLI

| Path | Mechanism | Verdict | Why |
|---|---|---|---|
| `accept_*_from_legacy` noop mirror | land formal from legacy rows | **KEEP** | Closed-loop / local-raw accept (org repair); no legacy wipe. |
| `rewrite_legacy=True` → legacy DELETE→INSERT | cargo-cult dual-write rewrite | **DELETED** (this knife) | Formal is SSOT; True branch + CLI flags removed. `enable_legacy_mirror` remains **test/emergency only**. |
| `ingest_stk_holdertrade_canary.py` | E0 one-shot canary CLI | **DELETED** (this knife) | Fan-in = self + ledger; E0 done. Function stays for tests. |
| org `--backfill` CLI → `backfill()` | multi-period with `skipped_existing` | **KEEP** (narrow) | Internal refuse mass refresh on already-landed period; not a periodic fixer. Daily path = incremental only. |
| holders `--backfill` / `--symbols` CLI | manual sparse/universe | **KEEP** (manual debug) | Not wired to daily_update; sparse repair still used. |
| `chunkyctl sync --backfill` | domain-owned window | **KEEP** with guards | Formal domains refuse mass windows; margin refuses `--backfill`. |

---

## 2. qfq DROP+CTAS — must vs incremental (Occam)

| Hypothesis | Assumptions | Fits evidence? |
|---|---|---|
| H1: Full DROP+CTAS **must** each derive | latest tip factor rewrites **all** historical adjusted rows for affected codes; single CTAS is simplest correct rebase | **Yes** — current contract |
| H2: Incremental partition write enough | must still rewrite history when factor tip moves; partition-only = wrong unless full per-code history rewrite | Partial — possible later product knife; **not** today’s root |
| H3: Skip rebuild + periodic compact | leaves stale qfq or free-block forever | **No** — fails semantics or ops |

**Verdict:** Full DROP+CTAS = **KEEP must** for latest-adj semantics today. Free-block answer = **in-module compact after CTAS** (Knife 3), **not** 「定期去 compact」。Incremental CTAS shape = deferred; do not leave monitoring loops as the design.

---

## 3. This knife — code deletions (executed)

| Deleted / narrowed | Evidence |
|---|---|
| `backend/scripts/ingest_stk_holdertrade_canary.py` | moth fan-in 2 (self+ledger); E0 canary closed |
| `--rewrite-legacy` on `ingest_holders_aif10` / `ingest_org_holding_aif10` | removed CLI footgun |
| `rewrite_legacy` param + True branch in `holders_aif10` / `org_holding_aif10` / `disclosure_dual_write.accept_stk_holdertrade_partition_from_legacy` | only noop mirror remains |
| Periodic holders dedupe repair script | **none found** — no delete needed |

Tests touched: `test_disclosure_dual_write.py` (drop `rewrite_legacy=False` kwargs).

**Not touched (peer / KEEP):** Tier0 sync replace modes; qfq CTAS+compact WIP/peer; holders skip-land (already on main); margin catchup; Continuity checks.

---

## 4. Why not 「定期检查修复」

1. **Orphan refill** → delete registry capability (factor) — done.
2. **Same-hash re-land** → skip in land path — done; no repair script.
3. **CTAS free-block** → reclaim in writer module — Knife 3; not orchestrator补跑.
4. **Legacy rewrite after formal accept** → delete True path — this knife.

Any new 「bloat fixer」 CLI that DELETE-landing / VACUUM myths / orphan revive = **reject** unless it is the owned module’s publish semantics.

---

## 5. Residual

| Item | Owner |
|---|---|
| Holders historical ~32× landing retention/archive | later L3; ban bare DELETE |
| qfq incremental product shape | deferred; KEEP full CTAS until proven |
| Continuity WARN domains | honest observe; Knife 4 — no READY cosmetics |
| Peer dirty WT (margin/qfq docs) | serialize; not this commit |

**Verified intent:** `rg rewrite_legacy|rewrite-legacy|ingest_stk_holdertrade_canary` → code absent except ledger history; dual-write tests still assert noop mirror preserves other-period legacy.

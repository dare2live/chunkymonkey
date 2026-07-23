# DB bloat deep-dive — 2026-07-23（owner Q&A addendum）

> **生命周期**：evidence-only addendum to `analysis/db_size_bloat_audit_20260723.md`  
> **方法**：read-only DuckDB + code cite；**未** DROP / VACUUM / compact / 改 peer WIP  
> **Measured**：2026-07-23 ~14:00 Asia/Shanghai

## Owner verdicts（binding for this knife）

| Item | Verdict | Reclaim action | Risk | Writer still touches? |
|---|---|---|---|---|
| `raw_tushare_stk_factor_pro` ~5.2 GiB | **NEED_OWNER_SIGN** | lifecycle archive+DROP+`db_compact tushare_raw`（见 §5 命令） | 高：不可逆丢 261 列 raw；registry 仍可显式 sync | **否（近期）**：`built_at` max=`2026-07-05`；wm `last_success_at`=`2026-06-19`；`on_demand` 不进 `--all-due` |
| holders landing ~32× `row_hash` | **KEEP (by design)** + policy residue | **勿 DROP landing**；先立法 retention / content-hash skip 再冷归档旧 batch | 高：改 immutable landing 语义；canonical 仍依赖 accept 链 | **是**：2026-07-23 仍 land/accept |
| market free-block ~0.7 GiB | **NEED_OWNER_SIGN**（窗口） | 停 derive 后 `db_compact.py --db market --execute` | 中：独占写锁；峰值≈旧+新 | **文件仍被碰**：mtime=`2026-07-23 13:48`；表内容 `ingested_at`=`2026-07-22 14:30`（全量 DROP+CTAS 后未 compact） |

**本刀无 SAFE_DELETE 执行** — 无「无消费者 + 无近期 writer + 非 accepted 语义」三项同时满足且可自动删的对象；compact 亦因 live 写窗未开。

---

## 1. `stk_factor_pro` ≈5.2 GiB orphan

### Live schema / grain / range

| Field | Evidence |
|---|---|
| Table | `data/tushare_raw.duckdb`.`raw_tushare_stk_factor_pro` |
| Cols | **262**（`ts_code`,`trade_date`, OHLCV×bfq/hfq/qfq, MA/MACD/RSI/KDJ/…, `built_at`） |
| Rows | **7,736,955** |
| Grain | `(ts_code, trade_date)` — **0 dups** |
| Codes / days | 4999 / 1818 |
| `trade_date` | `20190102` → `20260703` |
| `built_at` | `2026-06-19T11:40Z` → `2026-07-05T11:50Z` |

Sample (live):

```text
002217.SZ 20260618 close=2.53 ma_bfq_5=2.496 macd_bfq=0.036 rsi_bfq_6=51.557
```

### Who writes / why sync_orphan / consumers

| Layer | Evidence |
|---|---|
| Writer | `sync_registry.yaml` → `api: stk_factor_pro` → `target_table: raw_tushare_stk_factor_pro`；`batch_mode: by_ts_code`；`sync_policy: on_demand` |
| Plane label | `legacy_raw_plane.yaml` `kind: sync_orphan` — 「no DataAccess/serve consumer; no formal publication → stay ssot」 |
| Watermark | `smartmoney.mart_data_source_watermark` `sync:stk_factor_pro` `last_data_date=20260618` `last_success_at=2026-06-19`（表内窄窗回填到 `20260703`/`built_at` 07-05，wm 未跟到最新） |
| Consumers | `backend/services/data_access/**` **0** hits；moth fan-in = registry/tests/docs/continuity 文案 + `sync_runner` 批模式注释 — **无 serve/feature 读路径** |
| Why orphan | S7 硬停墙：有 sync residual、无 formal plane、无 DataAccess；保留 ssot 直到 owner sunset |

---

## 2. holders landing ~32× same `row_hash`

### Live multiplicity

| Metric | Value |
|---|---|
| Landing rows | 7,171,617 |
| Distinct `row_hash` | 225,099 |
| Avg copies | **31.86**（max **68**） |
| Extra rows vs unique | **6,946,518** |
| Buckets (hashes) | 1:11k · 2–10:34k · 11–20:20k · 21–30:23k · **31–40:43k** · **41–50:58k** · **51+:36k** |
| `ingest_batch` | 6617 batches / 632 partitions；ACCEPTED 6611 |
| Storm window | land days **2026-07-21…23**（07-22 alone 4412 batches / ~4.8M landing rows） |
| Same `(partition, payload_hash)` re-batch | **1216** groups（identical content, new `batch_id`） |

### Root cause（code）

1. **Landing PK = `(batch_id, row_ordinal)`** — append-only；`row_hash` **不是**唯一键（`holders_top10_acceptance.py` DDL + `INSERT`）。
2. **Idempotent only for same `batch_id`** — 同 `batch_id`+同 `payload_hash` early-return；不同 payload → error（`land_holders_top10_batch`）。
3. **Default `batch_id` = uuid** — `disclosure_transport.py`：`f"{domain}:{part}:{uuid4().hex[:12]}"` → 每次 land 新 batch，即使 `notice_date` 与 payload 已存在。
4. **Planner/catchup 重复拉已 accept 分区** — 同 `partition_value` 最高 **68** 次 land（例 `20250429`）；**不是**「单次全历史 dump 进一张表」，而是 **按 notice_date 分区反复重 land**。
5. Canonical 面干净：`canonical_top10_float_holders_period` ≈225k ≈ unique hash — 膨胀在 **landing 证据面**，非 accepted 叉积。

**Class**: immutable-batch 设计下的 **re-land storm**（缺 partition/content-hash skip），非 PK 重复 bug。

---

## 3. market free-block ≈0.7 GiB

| Measure | Live |
|---|---|
| File | `data/market.duckdb` 1.439 GiB |
| `pragma_database_size` | total_blocks=5898 used=2958 **free=2940** ≈ **0.718 GiB** |
| Business table | `price_kline_qfq_tushare` 8,412,670 rows；`date` 2019-01-02→2026-07-22 |
| Content lineage | `ingested_at`=`2026-07-22 14:30:14`；`batch_id`=`qfq:20260722143014:from_accepted` |

**Why holes（not VACUUM myth）**:

1. Writer path **`DROP TABLE IF EXISTS` + `CREATE TABLE AS`** 全量重建（`build_price_kline_qfq_tushare.py` L8/L111–114）。
2. Rebuild 尾只 `CHECKPOINT`（L217）— **不缩文件**（hygiene 2026-07-21 已立法）。
3. **2026-07-21** compact 曾把 market free≈2897→**1**、文件 1.5→0.8 GiB；此后每次 qfq rebuild 再积空洞 → 今日 free≈2940。
4. 旧 lifecycle DROP（tdxhub/xdxr 等，`mart_data_deletion_record` 2026-06）是历史贡献；**当前主因 = 每日/频繁 qfq CTAS 未跟 compact**。

---

## 4. 三库最近写入

| DB | Path | File mtime | In-DB freshness |
|---|---|---|---|
| tushare_raw | `data/tushare_raw.duckdb` | **2026-07-22 22:30:13** | `ingest_batch` max landed=`2026-07-22 22:30`（`stk_holdertrade`）；formal daily land max=`2026-07-22 17:26`；`stk_factor_pro` built max=`2026-07-05` |
| smartmoney | `data/smartmoney.duckdb` | **2026-07-23 13:24:03** | holders land max=`2026-07-23 09:35`；org land max=`2026-07-23 13:23`；`fact_stock_form_daily` built max=`2026-07-22 17:35`；form `trade_date` max=`20260722` |
| market | `data/market.duckdb` | **2026-07-23 13:48:51** | qfq `ingested_at`=`2026-07-22 14:30`（内容）；文件 mtime 今日更晚 → 有连接/CHECKPOINT 碰库，**非**新内容批次 |

**优化后是否还在写？**  
- 2026-07-21 compact 后：**是** — qfq 于 07-22 再全量 CTAS → free-block 回潮；smartmoney/tushare 持续 formal land。  
- compact **不会**阻止后续写入；market 若不在每次 DROP+CTAS 后 compact，空洞会复发。

---

## 5. Owner commands（NEED_SIGN — do not auto-run）

### A. Archive+DROP `raw_tushare_stk_factor_pro`（~5.2 GiB）

前置：业主签字；从 `sync_registry` 移除或保持 `on_demand` 且确认无显式 job；清 moth 引用面（或接受 residual docs）。

```bash
# 1) 新建 manifest（模板见 analysis/lifecycle_delete_manifest_raw_aif10_peer_valuation_20260707.yaml）
#    db: tushare_raw
#    table: raw_tushare_stk_factor_pro
#    action: archive
# 2) dry-run
python backend/scripts/db_lifecycle_delete.py \
  --manifest analysis/lifecycle_delete_manifest_raw_tushare_stk_factor_pro_YYYYMMDD.yaml
# 3) execute（停 tushare_raw writers）
python backend/scripts/db_lifecycle_delete.py --manifest <m> --execute
# 4) reclaim blocks
python backend/scripts/db_compact.py --db tushare_raw --execute
# 5) parity 后删 data/tushare_raw_precompact_bak.duckdb
```

### B. holders landing 去重/冷归档（~1.3 GiB 量级）— 先立法

```text
禁止：裸 DELETE / DROP landing_miaoxiang_holders_top10
建议刀序：
  1) disclosure land 加「partition 已 ACCEPTED 且 payload_hash 相同 → skip」
  2) retention：只保留每 partition 最新 ACCEPTED batch 的 landing 行，其余 archive parquet
  3) db_compact --db smartmoney
```

### C. market free-block compact（~0.7 GiB）

```bash
# 停 daily_update / derive_qfq
python backend/scripts/db_compact.py --db market --execute
# parity 后: rm data/market_precompact_bak.duckdb
# 复发护栏：每次 build_price_kline_qfq_tushare 全量 CTAS 后排程 compact（或改增量写避免 DROP）
```

---

## Label

**PARTIAL** — 五项均有 live 实证与 verdict；**无数据突变**；删除/compact 全部 **NEED_OWNER_SIGN** 或 **KEEP**。

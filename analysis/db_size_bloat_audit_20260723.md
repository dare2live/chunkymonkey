# DB size / bloat audit — 2026-07-23

> **生命周期**：evidence-only（只读审计；非 owner contract）  
> **范围**：live DuckDB under `data/` + code/path check for O(n²) / circular materialization  
> **禁令遵守**：未删生产库；未对 live writer 跑长 VACUUM；未 mid-flight compact  
> **相关**：`analysis/db_storage_hygiene_20260721.md`（free-block / compact 机制）；`analysis/db_layering_toplevel_design_20260721.md`（逻辑分层 vs 物理文件）

## 0. Owner brief（中文）

| 问题 | 结论 |
|---|---|
| 哪几个接近 10G？ | **`data/tushare_raw.duckdb` ≈ 10.0 GiB**；第二大 **`smartmoney.duckdb` ≈ 6.3 GiB**；第三 **`market.duckdb` ≈ 1.4 GiB**（非第二近 10G，但 free-block 空洞大） |
| 有没有「重复数据」？ | **业务主键级假重复很少**；主要是 **(a) 设计上的双/三存**（landing / legacy raw / accepted / derive）+ **(b) holders landing 同 `row_hash` 平均落库 ~32 次** + **(c) sync_orphan 宽表 `stk_factor_pro` ~5.2 GiB 无消费者** |
| O(n²) / 循环引用膨胀？ | **未发现** live 自连接扇出写表、episode×holder×profile 笛卡尔落表、或 derived→landing 回灌。`feature_store` 画像是聚合不是叉积；已退役 panel 只在 `data/archive/` |
| 可安全回收估计 | **立刻可谈（不删业务行）**：market/feature_store/smartmoney **free-block compact ≈ 0.7+0.1+0.2 ≈ 1.0 GiB**（须停写 + `db_compact`）。**内容级（需 lifecycle 证据 + owner）**：`raw_tushare_stk_factor_pro` ≈ **5.2 GiB**；holders landing 相对 unique-hash 冗余 ≈ **1.3 GiB 量级**（不可直接 DROP 当垃圾） |

**Verdict label: PARTIAL** — 体量大主要来自真实宽历史 + 设计双存 + 一个孤儿宽表 + holders 重落；不是「整库被 O(n²) 复制炸开」。

---

## 1. File inventory（measured 2026-07-23）

| File | File size | pragma size | free_blocks | ≈ free GiB | Role |
|---|---:|---:|---:|---:|---|
| `data/tushare_raw.duckdb` | 10.039 GiB | 10.0 GiB | 58 | 0.014 | Tier0 landing + legacy raw + formal canonical |
| `data/smartmoney.duckdb` | 6.267 GiB | 6.2 GiB | 756 | 0.185 | Serve / derive / holders+org landing+canonical |
| `data/market.duckdb` | 1.439 GiB | 1.4 GiB | **2940** | **0.718** | qfq derive (`price_kline_qfq_tushare`) |
| `data/feature_store.duckdb` | 0.162 GiB | 166 MiB | 418 | 0.102 | wipeable inst episode/profile |
| `data/reference.duckdb` | 0.005 GiB | 5 MiB | 2 | ~0 | calendar / active A |
| `data/experiment_store.duckdb` | 0.001 GiB | 8 MiB | 30 | 0.007 | experiment verdicts |
| `data/archive/**` | ~1.1 G | n/a | n/a | n/a | governed purge/lifecycle parquet（**keep**） |

Optuna / codegraph sqlite：`backend/.optuna/*.db`、`.codegraph/codegraph.db` — 非市场数据面，本审计不展开。

方法：`du` + DuckDB `pragma_database_size()` + `pragma_storage_info` distinct `block_id` × `block_size`（256 KiB）估表字节；`COUNT(*)` 与 grain `GROUP BY` 查真重复。

---

## 2. Top tables by approximate bytes

### 2.1 `tushare_raw.duckdb` (~10 GiB；几乎无空洞)

| Table | Rows | ≈ GiB | Notes |
|---|---:|---:|---|
| **`raw_tushare_stk_factor_pro`** | 7,736,955 | **5.193** | **261+ cols；`legacy_raw_plane` = sync_orphan；`sync_policy=on_demand`；无 DataAccess/serve 消费者** |
| `landing_tushare_daily` | 9,229,828 | 0.813 | formal landing（JSON payload）；hash 重复见 §3 |
| `canonical_nominal_ohlcv_daily` | 8,440,284 | 0.555 | accepted canonical |
| `raw_tushare_daily_basic` | 8,413,966 | 0.511 | legacy raw SSOT / L0 |
| `raw_tushare_share_float` | 16,470,778 | 0.476 | vendor **holder-level** grain（非日线主键） |
| `raw_tushare_moneyflow` | 7,525,195 | 0.408 | |
| `raw_tushare_daily` | 8,391,882 | 0.224 | legacy raw（与 landing/canonical 并存） |
| `raw_tushare_dc_member` | 24,783,872 | 0.199 | 日频成分快照；行多但列窄 |
| others (cyq/margin/dc/…) | — | ~1.2 | 长尾 |

`stk_factor_pro` 单表 ≈ **半个 tushare_raw 文件**。

### 2.2 `smartmoney.duckdb` (~6.3 GiB)

| Table | Rows | ≈ GiB | Notes |
|---|---:|---:|---|
| **`landing_miaoxiang_holders_top10`** | 7,171,617 | **1.328** | landing JSON；**unique `row_hash` 仅 225,099**（§3） |
| `canonical_top10_float_holders_period` | 224,973 | 0.704 | accepted；行少但块多（增量小批写入碎片嫌疑） |
| `fact_stock_form_daily` | 7,066,220 | 0.285 | Type-A derive |
| `fact_stock_moneyflow_daily` | 7,514,799 | 0.241 | |
| `fact_dc_member_daily` | 24,415,704 | 0.168 | B1 publication（raw≈同量级 dual） |
| `dim_stock_segment_daily` | 8,413,966 | 0.158 | |
| `fact_top10_holder_period` | 1,726,573 | 0.087 | legacy/compat serve 面（与 canonical 并存） |
| org landing/canonical/raw | ~0.3–0.4M ea | ~0.09 | org 三面小 |

### 2.3 `market.duckdb` (~1.4 GiB；**~50% free blocks**)

| Table | Rows | Notes |
|---|---:|---|
| `price_kline_qfq_tushare` | 8,412,670 | 几乎唯一业务表 |
| ops/deletion metadata | tiny | |

`used_blocks≈2958` vs `total_blocks≈5898` → **文件一半是 DROP 后未 compact 的空洞**（机制见 2026-07-21 hygiene；此后又重新积了空洞）。

### 2.4 `feature_store.duckdb`（小）

| Table | Rows | Cardinality note |
|---|---:|---|
| `mart_inst_profile_dim` | 416,618 | `industry_pit`∪`year`∪`holder_type` **GROUP BY** |
| `fact_inst_episode` | 368,907 | holder×stock episodes |
| `mart_inst_profile` | 122,519 | **= distinct holders**（1:1 聚合） |

---

## 3. Duplicate / double-write / dual-storage verdict

### 3.1 Expected dual storage（by design — NOT true bloat）

| Pattern | Evidence | Verdict |
|---|---|---|
| daily **landing + canonical + legacy raw** | landing 9.23M / canonical 8.44M / raw 8.39M；raw⊂canonical（overlap 8.39M；canonical-only 48k） | **Expected** transport strangler（E0+E1+legacy residual） |
| dc_member **raw + fact publication** | raw 24.78M ≈ fact 24.42M；grain `(trade_date,ts_code,con_code)` 0 dup | **Expected** B1（raw=rebuild input；fact=serve PIT） |
| holders **landing + canonical + fact_top10** | landing / canonical / fact 三面 | **Expected** formal accept + legacy serve；见下「重落」 |
| qfq in `market` vs nominal in `tushare_raw` | separate physical DB by write-lock design | **Expected**（非错误第二真相） |
| `data/archive/**` parquet | ~1.1 G | **Expected** lifecycle fuse；禁当垃圾删 |

### 3.2 True / actionable bloat signals

| Signal | Measured | Class |
|---|---|---|
| **`raw_tushare_stk_factor_pro` orphan wide table** | ~5.2 GiB；grain `(ts_code,trade_date)` **0 dups**；S7 `sync_orphan`；on_demand | **Content orphan**（非重复键；无消费者） |
| **holders landing hash re-land** | 7.17M rows / 225k distinct `row_hash`；avg **31.86** copies；max 68；extra vs unique ≈ **6.95M rows**；同日多 batch 同尺寸（例 `20260429`×23128） | **Landing accumulation**（immutable batch 设计下的重拉风暴；非 PK 重复） |
| **market free-block hole** | free ≈ **0.72 GiB**（2940 blocks） | **File bloat after DROP**（无重复行） |
| feature_store / smartmoney free | 0.10 / 0.19 GiB | 同上，量小 |
| daily landing mild re-hash | 9.23M rows / 8.44M hashes；extra ≈ 0.79M；avg 1.09 | 轻度重落，可接受 |
| canonical/fact tiny key dups | canonical `(stock,report,holder)` +10 extra；fact +56 extra | **噪声级**，非膨胀主因 |

### 3.3 False alarms（look like dups, are not）

| Table | Misleading key | Truth |
|---|---|---|
| `raw_tushare_share_float` | `(ts_code,float_date)` 「百万级 extra」 | 全列 grain **0 dups**；单日单票可达 **~19k holders**（vendor 解禁明细） |
| `raw_tushare_dc_member` | `(ts_code,con_code)` 跨日 | 日频 observation snapshot；三键无 dup |
| `canonical_top10` `estimated_size`≈7.1M vs rows 225k | catalog estimate stale/misleading | 以 `COUNT(*)` + storage blocks 为准 |

---

## 4. O(n²) / circular materialization audit

| Hypothesis | Finding |
|---|---|
| Self-join fanout writers | `build_price_kline_qfq_tushare` 注释记录旧 self-join **对账永真式**已改自检；**非写路径扇出** |
| Recursive / cross-product feature panels | Live DBs **无** `fact_feature_panel` / `fact_signal_panel` / `fact_segment_panel`（仅 `data/archive/purge_processed/`） |
| episode×holder×profile cartesian dump | `institution_profile.build_profiles`：`mart_inst_profile` = `GROUP BY holder`；`mart_inst_profile_dim` = 3 维 UNION ALL 后 **GROUP BY holder,dim_type,dim_value**。dims 416k ≈ 可排序 closed episodes 的维展开聚合，**不是** episode×profile 叉积落表 |
| Lineage re-ingests derived → landing | 代码面：landing 写自 provider payload；org/qfii 写 `raw_*`；**未发现** canonical/fact → landing 回灌 |
| share_float / dc_member 「爆炸」 | 供应商 grain（holder 明细 / 日频成分），非 SQL 笛卡尔 bug |

**O(n²)/cycle verdict: NONE live.**

---

## 5. Recommended safe reclaim（risk-ranked）

| Priority | Action | ≈ reclaim | Risk | Prerequisite |
|---:|---|---:|---|---|
| **P0** | `python backend/scripts/db_compact.py --db market --execute`（验证后删 `*_precompact_bak`） | **~0.7 GiB** | 中：需独占写锁；峰值≈旧+新 | 停 `daily_update`/derive；parity 检查（脚本内建） |
| **P0** | 同上 `--db feature_store` | **~0.1 GiB** | 低-中 | wipeable store；仍须停写 |
| **P1** | 同上 `--db smartmoney` | **~0.2 GiB** | 中-高：主 serve DB | 停 sync/accept；勿与 acquire 并行 |
| **P2（owner 焦点）** | lifecycle archive + DROP `raw_tushare_stk_factor_pro`（或整表 archive parquet）→ compact `tushare_raw` | **~5.2 GiB** | 高：不可逆丢 raw；虽标 sync_orphan，须 moth/codegraph + registry 确认无隐藏读者 | `db_lifecycle_delete` + eng_gov §10；**禁**裸 DELETE 后只 CHECKPOINT |
| **P3（policy）** | holders landing **按 hash 去重 / 冷归档旧 batch** | 可达 **~1.2–1.3 GiB** | **高**：改 landing 保留语义；需 retention 立法 | 先证明 batch 重拉根因；不可当「孤儿表」直接 DROP |
| **Avoid now** | 对 live `tushare_raw`/`smartmoney` 跑长时间 VACUUM/compact **同时** writer 在跑 | — | 锁死 / 半写 | 隔离窗口或 copy-compact only |

**Not recommended without proof:** 删 `data/archive/**`；删整个 production DuckDB；把 landing/canonical 双存当成 bug 合并掉。

Reproduce free-block measure:

```bash
python -c "import duckdb; c=duckdb.connect('data/market.duckdb',read_only=True); print(c.execute('SELECT * FROM pragma_database_size()').fetchall())"
```

---

## 6. Residual owners / next verification

| Residual | Owner |
|---|---|
| `stk_factor_pro` keep-or-archive | owner schedule（S7 sync_orphan；非本刀自动删） |
| holders landing ~32× hash | pipeline/holders land path — 查为何同日多 batch 全量重落（根因未在本审计修） |
| market free-block 回潮 | 每次 lifecycle DROP 后必须 `db_compact`（hygiene 2026-07-21 已立法；执行债） |
| canonical_top10 块/行比偏高 | 可选 compact smartmoney 顺带观察；非先证伪为逻辑重复 |

---

## 7. Commands / evidence anchors

- Size: `find`/`du` on `data/*.duckdb`（2026-07-23）
- DuckDB read-only: `pragma_database_size`, `duckdb_tables`, `COUNT(*)`, grain `GROUP BY … HAVING COUNT(*)>1`, `pragma_storage_info` block counts
- Config: `backend/config/legacy_raw_plane.yaml` (`raw_tushare_stk_factor_pro` sync_orphan)；`sync_registry.yaml` `stk_factor_pro.sync_policy=on_demand`
- Code: `backend/services/institution_profile.py` (`build_profiles`)；`backend/services/dc_member_publish.py`；prior `analysis/db_storage_hygiene_20260721.md`

**FIXED** = measurement + classification doc.  
**No data mutation in this knife.**

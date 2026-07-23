# DB refill-after-delete audit — 2026-07-23

> **生命周期**：code/config inventory（evidence-only；本刀不 DROP / 不 compact / 不抢 `tushare_raw` 写锁）  
> **Owner Q**：优化时发现「删了又写回同样数据」的脚本——为什么 / 是否必须 / 现在还有哪些  
> **关联**：`analysis/db_size_bloat_audit_20260723.md` · `analysis/db_bloat_deep_dive_20260723.md` · `analysis/db_storage_hygiene_20260721.md` · peer lifecycle `analysis/lifecycle_delete_manifest_raw_tushare_stk_factor_pro_20260723.yaml`  
> **Live note**：审计时 `data/tushare_raw.duckdb` ≈ **1.85 GiB**（此前 bloat 审计 ≈10 GiB）且被 PID 持写锁 → **peer 可能已在 DROP factor / compact**；下文以 **registry + writer 路径** 为准，不声称表当前是否仍存在。

---

## 0. Owner brief（中文）

| 问题 | 结论 |
|---|---|
| **为什么会有** | 三类合法动机叠在一起：(1) **幂等发布**——同 grain / 同分区先 DELETE 再 INSERT，防上游撤销/半批残留；(2) **派生全量重建**——前复权/机构档案等依赖「最新因子全史 rebase」，便宜路径是 DROP+CTAS / `CREATE OR REPLACE`；(3) **不可变 landing**——每批新 `batch_id`，同 payload 再 land = 证据追加，不是 in-place 覆盖。 |
| **是否必须** | **必须的是语义**（replace_partition / accepted 派生 / snapshot 去假活跃），**不是必须每次物理全表 DROP**。holders 同 hash 重落、qfq 每日全量 CTAS 不跟 compact = **可改的执行债**，不是宪法。 |
| **删行/表后会不会被 daily 灌回** | 见 §3 焦点表。关键：**`stk_factor_pro` 不进 `--all-due`/daily drain**；但 registry **仍 live `on_demand`** → 显式 sync 会 `CREATE TABLE IF NOT EXISTS` 再灌。Peer manifest 写「墓碑防回流」——**截至本刀工作树，registry 尚未墓碑**（协调点）。 |

**Verdict: PARTIAL** — 清单完整；防回流钩子（factor tombstone）属 peer knife，本刀只文档。

---

## 1. Pattern taxonomy（为什么「删了又写」）

| 类 | 机制 | 典型路径 | 必须？ |
|---|---|---|---|
| **A. Sync grain replace** | `DELETE` matching grain/partition → `INSERT` 同批 | `sync_runner._write_*` + `write_mode: merge_grain \| replace_partition \| replace_snapshot` | **Must-have**（幂等覆盖；禁 MERGE 留假行） |
| **B. Derive full rebuild** | `DROP TABLE` / `CREATE OR REPLACE` → 全量 CTAS | qfq · dc_industry_view · institution_profile · market_pulse `rebuild_all` | **语义 must**（latest-adj / wipeable L2）；**每日全量 + 不 compact = accidental cost** |
| **C. Immutable landing append** | 新 `uuid` `batch_id` → APPEND landing；canonical 另 REPLACE 分区 | holders_top10 / org_holding disclosure transport | Landing append **by design**；**同 content 反复 re-land = bug/债** |
| **D. Compact / lifecycle** | DROP 表 → `db_compact` 拷库缩文件；**不**回灌业务行 | `db_lifecycle_delete.py` · `db_compact.py` | Compact **must** after DROP；compact **本身不 refill** |
| **E. Ensure / create-if-missing** | `CREATE TABLE IF NOT EXISTS … AS SELECT * FROM df LIMIT 0` 后写 | sync_runner 首次/表已 DROP | 显式 sync 时会建空表再灌 — **删表后的回流入口** |

历史笔记关键词（`analysis/*bloat*` / hygiene / ledger）：market **DROP+CTAS 未 compact → free-block**；holders **re-land storm**；`stk_factor_pro` **sync_orphan on_demand**。

---

## 2. Live offenders inventory

### 2.1 焦点三件（bloat 战役已点名）

| 对象 | Path / trigger | 为什么存在 | Must vs accidental | 删底层行/表后 |
|---|---|---|---|---|
| **`raw_tushare_stk_factor_pro`** | `sync_registry.yaml` `stk_factor_pro` · `sync_policy: on_demand` · `batch_mode: by_ts_code` · writer=`sync_runner` | 实验/因子宽表 SSOT；S7 `sync_orphan`（无 DataAccess/serve） | **Accidental residual**（无生产消费者）；保留 registry 仅为显式实验 | **daily / `--all-due`：不会 refill**（`automatic_domains` 跳过 `on_demand`）。**显式** `--domain stk_factor_pro --start/--end`：**会** `CREATE TABLE IF NOT EXISTS` + 逐股回灌。Peer lifecycle 拟 DROP + 声称 registry 墓碑 — **工作树 registry 仍 live** → **回流门未关**。 |
| **holders landing re-land** | `disclosure_transport.land_*` → `land_holders_top10_batch`；`batch_id` 默认 `domain:part:uuid`；acquire / formal disclosure catchup | 不可变 batch 证据面；accept 后 canonical `DELETE WHERE notice_date` 再写 | Landing 语义 **must**；**同 `row_hash`×32 重落 = accidental**（缺「已 ACCEPTED + 同 payload_hash → skip」） | 删 landing 行：下次 land **再 append**（新 batch）。删 canonical：accept 链可从仍在的 landing replay；若 landing 也空则须重新 provider land。**不会**因 compact 单独 refill。 |
| **market qfq DROP+CTAS** | `pipeline/clean.py` → `build_price_kline_qfq_tushare.py`（daily Step 2.96）；亦 `chunkyctl derive qfq` / ops `derive_qfq` | latest-adj 全史 rebase；analysis 面非 execution truth | **Rebuild must**；**每次 DROP 不 compact = free-block 复发（accidental ops）** | 删 `price_kline_qfq_tushare` 或整表 DROP：下次 clean/derive **从 accepted nominal×adj 全量重建**（内容「看起来一样」）。**compact 只缩文件，不阻止下次 CTAS**。 |

### 2.2 其它仍活的「删/替 → 再写」路径

| 对象 | Path | Trigger | Why | Must? | 删后 |
|---|---|---|---|---|---|
| **Legacy raw merge_grain / replace_*** | `sync_runner` write_mode | `--all-due` drain（非 on_demand 域）或显式 domain | 幂等覆盖 grain；snapshot 去假活跃 | **Must** | 删行 → watermark/gap 逻辑可能 **再拉同窗** 写回；整表 DROP → 显式/ due sync 会建表再灌 |
| **`margin` / `margin_detail` replace_partition** | registry `write_mode: replace_partition`；margin 另有 formal land/accept + bounded catchup | margin=`on_demand` + acquire catchup；detail 可进 automatic | 整日原子替换，禁旧分片 | **Must** | 删某日 raw → catchup/显式窗 **再 fetch 该日** |
| **`stock_basic` / list snapshots** | `write_mode: replace_snapshot` · `batch_mode: full_refresh` | due / 显式 | 当前在市快照；DELETE 全表再 INSERT | **Must**（语义） | 删表/行 → 下次 full_refresh **整表重写** |
| **`trade_cal` full_refresh** | registry on_demand + manual | 显式 / 授权 generation | 日历 generation | **Must**（手动） | 不进 daily drain；显式才重建 |
| **DC industry view** | `build_dc_industry_view.py` shadow CTAS → DROP 旧 → RENAME | `process`（delta 可 skip） | 当前截面 dim 发布 | **Must** when frontier advances | 删 dim → 下次 process rebuild（源仍在 `raw_tushare_dc_*`） |
| **`institution_profile` rebuild_all** | `CREATE OR REPLACE` episodes/profiles | `process` delta-gate（holders frontier） | wipeable L2；closed-loop | **Must** when holders 变；可 skip if unchanged | 删 feature_store 表 → 下次 rebuild **全量重算**（读 holders canonical） |
| **`market_pulse` rebuild_all** | 内部 DROP rebuild 表再 swap | `build_latest` 劣化路径 / 手动 | Tier2 面板 | 增量 **must**；全量 rebuild **少见** | 删 pulse 表 → build_latest 可走 rebuild |
| **`technical_states` / form** | `build_latest(from_accepted=True)` | daily process | Tier1 form 增量 | **Must**（增量） | 删 form 日分区 → 增量可能只补 frontier；全量需 `rebuild_all` 入口 |
| **`db_compact` / lifecycle DROP** | `db_lifecycle_delete.py` · `db_compact.py` | **manual_only** | 回收 free-block；archive+DROP | Compact **must after DROP** | **不 refill**；若 registry/writer 仍指向表名，**后续 sync/derive 才 refill** |

### 2.3 明确「不是」自动回流的

| 机制 | 说明 |
|---|---|
| `db_compact.py` | ATTACH-copy 缩文件；不调 provider |
| `CHECKPOINT` only | 不缩文件、不回灌 |
| `data/archive/**` parquet | 冷证据；无自动 load-back 进生产表（除非人工/lifecycle 反操作） |
| S7 `sync_orphan` 标签本身 | 只分类；**不**阻止显式 sync |

---

## 3. 焦点问答（删了会不会被下次 sync/daily 灌回？）

### 3.1 `raw_tushare_stk_factor_pro`

| 入口 | 会 refill？ | Evidence |
|---|---|---|
| `daily_update` → acquire `--all-due` | **否** | `sync_runner.automatic_domains`：`sync_policy != on_demand` 才入选；factor=`on_demand` |
| `sync_runner --domain stk_factor_pro` 无窗 | **拒** | on_demand 要求显式 `--start` 与 `--end` |
| 显式 `--domain stk_factor_pro --start/--end` | **是** | `CREATE TABLE IF NOT EXISTS` + by_ts_code fetch；registry 仍注册 `target_table` |
| Peer lifecycle DROP 后 | **日常不会**；**显式仍会**直到 registry 墓碑/`execution_policy: disabled` 或域删除 | Manifest 声称墓碑；**本工作树 `sync_registry.yaml` 仍含完整 `stk_factor_pro` 块（L1208+）** |

**协调**：factor DELETE 属 peer knife。本刀 **不改 registry**（避双写）。Owner 验收 DROP 后应确认：registry 墓碑或 `execution_policy.mode=disabled`，否则「磁盘刚腾出又被实验 sync 灌回」。

### 3.2 holders landing

| 入口 | 行为 |
|---|---|
| 日常 acquire / disclosure land | **继续 append** 新 batch（uuid）；已 ACCEPTED 分区仍可能被 planner 再拉 → 同 `row_hash` 堆积（bloat §2） |
| 仅 `db_compact smartmoney` | **不**减少行；不 refill |
| 裸 DELETE landing | 破坏证据链；下次 land 仍 append；**勿**当去重手段 |

**该改**：partition 已 ACCEPTED 且 `payload_hash` 相同 → **skip land**（deep-dive §5B）；非关 sync 域。

### 3.3 market qfq CTAS after compact

| 入口 | 行为 |
|---|---|
| 下次 `daily_update` clean / `derive_qfq` | **全量 DROP+CTAS 再写**（内容≈同；`ingested_at`/`batch_id` 新）→ free-block **回潮** |
| compact 本身 | 不阻止上述 |

**该改**：CTAS 后排程 compact，或改增量/分区写避免每日全表 DROP（产品刀，非本刀）。

---

## 4. 该关 / 该改成 skip（建议，非本刀执行）

| 优先级 | 动作 | 效果 |
|---:|---|---|
| **P0（peer/factor）** | DROP 后立刻 **墓碑** `stk_factor_pro`：删域或 `execution_policy: disabled` + 测试/ continuity 改 WARN/退役 | 堵显式 refill；与 lifecycle manifest「防回流」对齐 |
| **P0（holders）** | land 路径：`ACCEPTED` + 同 `payload_hash` → **skip** | 停 ~32× 重落；不改 canonical 语义 |
| **P1（qfq）** | clean 后 compact market，或 derive 改增量 | 停 free-block 复发 |
| **P2** | `institution_profile` / DC：保持现有 delta skip；勿改回「每夜无条件 rebuild_all」 | 已部分 skip — 保持 |
| **Avoid** | 把 landing/canonical 双存「合并删掉」；对 live writer 长 VACUUM；无墓碑只 DROP factor | 语义破 / 锁死 / 回流 |

---

## 5. Code cite index（最小）

| Claim | Where |
|---|---|
| on_demand 不进 all-due | `sync_runner.automatic_domains`（`sync_policy != on_demand`） |
| factor on_demand | `sync_registry.yaml` `stk_factor_pro` |
| sync DELETE→INSERT | `sync_runner` write_mode 分支 ~L1495–1541 |
| qfq DROP+CTAS + CHECKPOINT | `build_price_kline_qfq_tushare.py` L111 / L217；触发 `pipeline/clean.py` |
| holders uuid batch | `disclosure_transport.py` ~L328–331 |
| inst CREATE OR REPLACE | `institution_profile.py` rebuild_all |
| DC DROP+rename publish | `build_dc_industry_view.py` ~L89–144 |
| compact ≠ refill | `analysis/db_storage_hygiene_20260721.md` |

---

## 6. Label / residual

**PARTIAL** — 代码面 offender 表完整；live 表是否已 DROP 以 peer 锁内作业为准（文件已从 ~10 GiB 量级缩小，与 factor 退役一致但未在本刀内核验行数）。

**Residual owner**

1. Peer factor knife：registry 墓碑与 lifecycle execute 同收口。  
2. Holders skip-land 立法刀。  
3. qfq compact/增量刀（owner 窗）。

**Next verification**：peer 收工后 `rg -n "stk_factor_pro" backend/config/sync_registry.yaml` 应为墓碑/缺失；`python -c` 只读确认表不存在；再跑一次 `--all-due` dry 域列表确认无 factor。

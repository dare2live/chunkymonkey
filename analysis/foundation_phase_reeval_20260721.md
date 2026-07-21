# 数据地基阶段重评（2026-07-21）

> **生命周期**：evidence-only / **foundation closure sequencing**（由 `goal.md` 授权近端排序；不替代 `docs/MASTER_TOPLEVEL_DESIGN.md` 立法）  
> **证据输入**：tip `650aea42f`；live gates；`plan_reeval_first_principles_20260720.md`；`data_brick_architecture_20260721.md`；`data_foundation_modularity_gap_20260720.md`；`db_layering_toplevel_design_20260721.md`；git log 近 25 刀（S1–S7 / B1–B2 / B5 / E0 / qfq）  
> **禁令延续**：E/F remeasure 不排近端；G/H/Release/Optuna；假 COMPAT/假 FIXED；org provider land invent；第五产品

---

## 0. 一句话裁决

**相对既定方案（transport strangler S1–S7 + brick L0–L3 + E0 + DB 逻辑分层），数据地基约 **~91% 闭合**——S1–S6 与 S7 可行动项已 shipped；S7 余 **23/46 ssot** 为 **typed hard-stop 墙**（非未完成清单）；**E0-HIST/F6 PASS**；近端缺口 = **§15 行为 adoption** + **foundation-done 机器门**。Type-B enrichment **defer**（registry 在方案内；enrichment 刀序超前）。策略轨 **paused**。

---

## 1. 阶段地图：方案要求 × 已交付 × 残差

### 1.1 Transport strangler（plan_reeval §5）

| 切片 | 方案要求 | Shipped（tip 证据） | 残差 | 标签 |
|---:|---|---|---|---|
| **S1** land-only | CLI/API 只到 LANDING；≤40d；不写 canonical | `--land-only` daily/ST/cal + E0 三域；TDD + moth | — | **FIXED** |
| **S2** accept-from-landing | `--accept-from-landing --batch-id`；零 provider | S2 CLI + zero `_adapter` on accept path | — | **FIXED** |
| **S3** sync caller-only | default sync = S1→S2；`capture_and_publish_*` 非生产 fan-in | `land_then_accept_*`；moth 证 sync_runner 无 fused fan-in | — | **FIXED** |
| **S4** acquire 可换源 | provider / local-raw → 同一 landing；accept 零 acquire | `security_day_acquire` modes + `--from-local-raw` | — | **FIXED** |
| **S5** derive 独立 | `derive qfq\|form --from-accepted`；不进 accept 事务 | `derive_runtime` + CLI；form/qfq canonical-only | — | **FIXED** |
| **S6** serve resolver | DataAccess + resolver；router 零 inline raw | `market_pulse_serve_read`；D5 绿；dual-track NONE | — | **FIXED** |
| **S7** legacy 面 | 默认 accepted-only；inventory 分类；priority serve/multi-consumer 先 | `legacy_raw_plane.yaml` + gate；B1+B2 done；daily **1829d**；**23 ssot / 1 fill / 22 compatibility** of 46 | 23 typed hard-stop（见 §1.2）；**禁**假 COMPAT | **near-FIXED / stronger PARTIAL** |

**S7 23 ssot 分型（live `check_legacy_raw_plane.py`）**：

| kind | 数量 | 处置 |
|---|---:|---|
| `sync_orphan` | 14 | 零 consumer；仅 sync 残留 → **documented wall**；sunset 或 future publication 需 owner 证据 |
| `serve_l0_declared` | 7 | DataAccess L0 在册、无 live router consumer → **documented wall** |
| `blocked_no_publication` | 2 | `suspend_d`、`margin_detail` → 诚实 blocked；不 fake PIT |

**机器验收**：`PYTHONPATH=backend .venv/bin/python3 backend/scripts/check_legacy_raw_plane.py` → `ssot=23 fill=1 compatibility=22`。

### 1.2 Acquire / filter（MASTER §5.1 + modularity gap §5）

| 要求 | Shipped | 残差 |
|---|---|---|
| daily/ST formal acquire = 全市场 by `trade_date` | S4 + formal sync | — |
| ST/BSE/退市 = 读侧 universe，非 acquire 黑名单 | goal + MASTER 裁决入 goal | legacy `by_ts_code` 域仍 NONCONFORMING（E0 单列） |
| local-raw → landing 不重焊龙 | `--from-local-raw` + chunked ≤40d | E0 org 仍 local-raw only |

### 1.3 E0 披露 formal（plan_reeval R0）

| 要求 | Shipped | 残差 |
|---|---|---|
| S1/S2 模块化 CLI（三域） | `--land-only` / `--accept-from-landing` / `--land-then-accept` | — |
| provider land（bounded） | `stk_holdertrade` + `holders_top10` shipped | **`org_holding` provider land BLOCKED**（by-period ~830k；无 NOTICE_DATE） |
| accept 广度 | holders **152** / stk **194** / org **2** 日；holders **126** trading-day overlap daily | **F6 PASS**（≥120）；org 维持 local-raw BLOCKED；可继续自然 chunked 扩但非近端 blocker |
| empty_skip + grain | holders `row_seq` renumber；empty_skip continues | — |

### 1.4 Brick L0–L3（data_brick_architecture）

| 层 | 方案 | Shipped | 残差 |
|---|---|---|---|
| **L0** evidence | landing + legacy raw 分类 | E0 landing + legacy inventory | 23 ssot = L0 并行面 documented wall |
| **L1** accepted | canonical + partition | daily/ST/disclosure partial | E0 日覆盖薄 |
| **L2** primitives | qfq/form/Tier12 base；lineage | qfq **FIXED**（8,402,928 行；`missing_lineage=0`）；form/Tier12 declared | — |
| **L3** composites | registry + hop cap + Type-B 登记 | `brick_registry.yaml` L2=5 L3=6 Type-B=2；gate 绿 | `institution_profile_edge` **enrichment_projection_partial** |
| **L4** | 策略产物；非日常仓库 | F0–F3 measured reject 已归档 | **paused** — 非本阶段 |

**机器验收**：`check_brick_registry.py` → `orphans=0 hops≤2`。

### 1.5 DB 逻辑分层 + 过程

| 项 | 状态 |
|---|---|
| `db_layering_toplevel_design_20260721.md` | **authority shipped**（逻辑 E0→R1；物理单 DuckDB） |
| `data_brick_architecture_20260721.md` | **authority shipped** |
| §15 knife-merge | **policy FIXED**；**behavior PARTIAL**（见 process_efficiency_validation） |
| WP6 agent-OS shadow | ceremony 开放；**不**阻塞 foundation-done |

---

## 2. Overshot vs Undershot（相对方案，非道德评判）

### 2.1 Overshot（方案内但时序超前 / 文档超前）

| 项 | 判定 | 说明 |
|---|---|---|
| **B5 Type-B deep registration + enrichment 叙事** | **时序 overshot** | registry + gate **在方案 B5 内**且已绿；但 `institution_profile_edge_v0` enrichment 在 holders accept **17 日**时推进 = **依赖未闭合**；ledger 已记「no safe thin knife」 |
| **S7 daily 扩至 2019** | **方案内，非 creep** | 属 S7 accepted-only + local-raw strangler；与 B1/B2 同轨 |
| **brick_registry 先于 E0 广度** | **轻微 overshot** | 文档/门先行的价值在防 silent bypass；enrichment **不应**再占近端刀 |
| **plan_reeval §11「近端 3 切片 S1–S3」** | **文档滞后 overshot** | amend 已 FIXED；读者若只看 §11 会误判 — **本重评 supersede 近端菜单** |

### 2.2 Undershot

| 项 | 判定 |
|---|---|
| **E0 accept 历史广度** | **F6 PASS**（holders 126d overlap ≥120；stk 同步）— 相对 daily 1829d 仍可 chunked 续扩，**非** foundation-done blocker |
| **§15 行为 adoption** | 59 micro-commit 窗口 vs 1 knife-merge 示范 — **undershot** |
| **Foundation-done 机器门** | **FIXED**（`check_foundation_done.py`；F8 PARTIAL 仍挡 `phase_closure_ready`） |
| **org_holding** | 方案允许 BLOCKED + local-raw；**非 undershot**（诚实 blocked） |

### 2.3 Type-B enrichment：in-scheme 还是 scope creep？

| 维度 | 裁决 |
|---|---|
| **L3 registry + Type-B 表登记** | **IN-SCHEME**（B5；`data_brick_architecture` §7.3） |
| **institution_profile enrichment projection 实作刀** | **DEFER** — 方案允许 partial_reasons；近端 **不**开 thin knife；待 E0 holders 历史闭合后再评 |
| **若 agents 将其升「下一刀」** | **scope creep（时序）** — 非新 Phase |

---

## 3. Foundation-done 标准（本阶段出口；可机器检）

全部满足方可将 near-term track 从 **foundation solidify** 切到 **scheduled E/F remeasure**（仍禁 Optuna/松门/Release）：

| # | 条件 | 机器检 |
|---:|---|---|
| F1 | S1–S6 transport | modularity gap §8 + TDD `test_s3_*` + moth `capture_and_publish` fan-in |
| F2 | S7 inventory 诚实 | `check_legacy_raw_plane.py` PASS；ssot=23 且 kinds 分型与 goal 一致 |
| F3 | 无假 publication | gate 拒 serve_l0/multi_consumer 无 DataAccess Publication 的 COMPAT |
| F4 | B5 registry + qfq lineage | `check_brick_registry.py` PASS；qfq `trust=LINEAGE_OK`；live derive `missing_lineage=0` |
| F5 | E0 transport | disclosure CLI 三模式 + stk/holders provider land 绿测 |
| F6 | E0 accept 广度 | holders **≥120** 交易日 overlap with daily accepted window **或** owner 书面降阈；stk 同步；org **维持** local-raw（BLOCKED 不变） |
| F7 | org | **`org_holding` provider land 仍 BLOCKED**；无 by-date invent |
| F8 | §15 behavior | 连续 3 个 L3 foundation 刀：`commits/knife ≤1.5`；L3 先 `pre-knife` |
| F9 | 策略轨 | E/F/G/H **未**开；frontier honest（`operation_window_blocked` 非落后） |
| F10 | dual-track | residual **NONE**（re-audit 或 moth claim） |

**当前计分**：F1–F6 **PASS**；F7 **PASS**（org BLOCKED 维持）；F8 **PARTIAL**；F9–F10 **PASS**。

---

## 4. 相对方案完成度（透明权重）

| 域 | 权重 | 完成度 | 加权 |
|---|---:|---:|---:|
| S1–S6 transport | 35% | 100% | 35.0 |
| S7 legacy（含 hard-stop 墙） | 20% | 90%（priority path done；23=墙非债） | 18.0 |
| E0 disclosure | 20% | 85%（CLI+provider；F6 accept 广度 PASS；org BLOCKED） | 17.0 |
| B5 L2/L3 registry + qfq | 10% | 90%（enrichment defer） | 9.0 |
| DB/brick 文档 authority | 5% | 100% | 5.0 |
| §15 process adoption | 5% | 40% | 2.0 |
| Acquire/universe 立法 | 5% | 100% | 5.0 |
| **合计** | **100%** | | **~91%** |

**读法**：~91% = **方案内地基**；不是 Tier0「全 repo 零 legacy raw」——那需要 owner 对 23 域 publication/sunset 裁决，**超出本阶段定义**。

---

## 5. 有序下一刀（仅 foundation closure）

**Occam**：不发明 scheme 外刀名；不重开 A→H 主线。

| 序 | 刀 | 内容 | 退出 | 不在此刀 |
|---:|---|---|---|---|
| **1** | **§15-VERIFY** | 下 2–3 个 L3 刀强制 knife-merge + `pre-knife`；process_efficiency 复测；更新 `foundation_done.yaml` §15 evidence | F8 PASS → `phase_closure_ready` | 放宽 L3/Rule10/PIT |

**已完成**：
- **E0-HIST** — holders/stk local-raw chunked ≤40d empty_skip → F6 PASS（2026-07-21）。
- **FND-GATE** — `backend/scripts/check_foundation_done.py` + `foundation_done.yaml`；doctor/moth/CI；typed walls PASS；F8 PARTIAL exit 0（2026-07-21）。

### 5.1 S7 23 orphans — 怎么办？

| 选项 | 裁决 |
|---|---|
| 批量 COMPAT 降级 | **禁止** — 无 publication consumer |
| 逐域 formal publication | **仅 owner 新 block**（module+data+contract+evidence） |
| sunset / 停 sync | 需 moth + consumer 证据 |
| **本阶段默认** | **documented typed wall**；S7 标签维持 near-FIXED；**不排近端刀** |

### 5.2 org BLOCKED

维持 **`org_holding` provider land BLOCKED**；org accept 仅 **`--from-local-raw`**；禁止 by-period mass land invent。

### 5.3 Type-B

| 决策 |
|---|
| **DEFER** enrichment 至 E0-HIST 闭合或 FND-GATE 后 owner 复评 |
| registry/gate **保持**；不假升 B5 FIXED |

---

## 6. 明确 NOT next（近端）

- E/F **same-protocol remeasure**（窗已 unblock，** deliberately paused**）
- G 公式 / BestChoice challenger / H Release / paper 候选
- Optuna / StrategyRelease / E 松门
- S7 23 ssot **假 COMPAT** 或 blind raw delete
- Type-B `institution_profile_edge` enrichment thin knife
- `org_holding` provider by-date land
- 新 serve_l0 域 publication（无 consumer block）
- greenfield 第五产品 / 第二 DB / plugin bus
- plan_reeval §11 的 S1–S3（**已 FIXED** — 历史文字）

---

## 7. 与 owner 文档关系

| 文档 | 本重评 |
|---|---|
| `plan_reeval_first_principles_20260720.md` | 排序母体；§11 近端菜单由 **本文件 supersede** |
| `data_brick_architecture_20260721.md` | L0–L3 立法；B5 defer enrichment |
| `goal.md` | 执行板；近端 track = **foundation solidify** |
| `BOARD.md` | 生成投影；随 goal 再生 |

---

## 8. Verdict

| 标签 | 内容 |
|---|---|
| **FOUNDATION_VS_SCHEME** | **~85%** |
| **NEAR_TERM_TRACK** | foundation solidify（strategy paused） |
| **NEXT_3** | §15-VERIFY（E0-HIST + FND-GATE done） |
| **S7_23** | typed wall；非近端刀 |
| **TYPE_B** | registry in-scheme；enrichment **defer** |
| **ORG** | BLOCKED maintained |

**APPROVED** — 作为 2026-07-21 foundation 阶段出口排序；implementation 仍 strangler，不触发 greenfield。

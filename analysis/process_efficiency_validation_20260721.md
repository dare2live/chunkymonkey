# Process efficiency validation — measured re-check（2026-07-21）

> **生命周期**：evidence-only / owner skepticism response（非 owner contract）  
> **Authority**：`goal.md` → `analysis/throughput_bottleneck_diagnosis_20260721.md` → eng_gov §15  
> **问题**：Agent-OS / Delivery-OS / ceremony 优化是否真提速？还剩什么？哪些旧文档仍误导？

---

## 0. TLDR（owner）

| 问 | 答 |
|---|---|
| **效率 improved?** | **PARTIAL (Y on docs/mechanical gates; N on L3 wall-clock; behavior adoption 刚起步)** |
| **Numbers** | L1 **1.54s** (re-measure ≈ T0 **1.6s**); L3 pytest **898 tests / 54 paths / 122.7s** (T0 L3 **27.1s** 不含全量 ci_pytest); `agent-boot` **9.3s** (T0 **11.6s**); `pre-knife` **0.64s** |
| **Top remaining waste** | (1) micro-commit / 不敢刀级合并 (2) L3 全 pytest 面 ~2min/commit (3) 父 agent 串行 + subagent 空转 (4) S7/E0 领域物理（≤40d、逐域 strangler） |
| **Foundation one-liner** | **S1–S6 FIXED**；**S7 PARTIAL** (32/46 ssot)；**E0 PARTIAL**；E/F remeasure **paused**；DB 逻辑 E0→R1 strangler，物理单 DuckDB |
| **「续减 ssot」** | S7 `legacy_raw_plane` 清单里仍标 **publication/serve SSOT 经 raw** 的表 → 逐域 reclassify 到 compatibility / accepted / DataAccess；**不是** hobby 线，是 foundation modularity **S7 长尾** |

---

## 1. Measured test（本 session 实测 + git 对照）

### 1.1 L1 docs-only path

| 项 | 值 | 备注 |
|---|---:|---|
| Command | `SAFE_COMMIT_NO_PUSH=1 SAFE_COMMIT_DRY_RUN=1 scripts/safe_commit.sh` + staged `analysis/*.md` only | tier=L1 |
| Wall | **real 1.54s** | 2026-07-21 ~14:00 Asia/Shanghai |
| T0 baseline | **1.6s** | ledger 2026-07-20 Delivery-tax knife |
| Verdict | **UNCHANGED / GOOD** — docs 不再跑 moth/pytest/continuity |

L1 路径 **已证有效**；CI paths-ignore (c473f5b4) 与 tier 分类一致。

### 1.2 L3 / safe_commit pytest surface

| 项 | 值 |
|---|---|
| SSOT | `backend/config/ci_pytest_surface.yaml` |
| Path count | **54** |
| Test count | **898 passed** |
| Wall | **122.68s** (`run_ci_pytest.py`, `-p no:cacheprovider --tb=short -q`) |
| T0 L3 “27.1s” | 含 continuity/grain 等门；**当时未含** 2026-07-20 晚绑定的 **全量 ci_pytest**（543c9e478） |

**诚实结论**：L3 **机械门集合**仍 ~半分钟级；**L3 commit 真实墙钟** ≈ **pytest (~123s) + 门 (~25–30s) ≈ 2.5min** 在本机。这是 **故意** 关「本地绿 CI 红」假安全 — **不是** Agent-OS 失败，是 **新 SSOT 税**。

### 1.3 其它机械探针

| 探针 | Wall | T0 |
|---|---:|---:|
| `scripts/chunkyctl agent-boot` | **9.30s** | 11.6s |
| `scripts/chunkyctl pre-knife s7-inventory` | **0.64s** | (new in 464e6edf9) |

### 1.4 Before vs after §15 knife-merge（git 证据）

**Before（micro-commit 习惯，Jul 20–21 transport 刀）**

- `git log --oneline --since=2026-07-20 --until=2026-07-21` → **59 commits** / ~36h
- 典型 pattern：**docs commit → feat commit** 成对（例：`47a7db165` docs S1+S2 + `c4e3efd96` feat；S7 拆 `f98976bfb` + `b8d0218dc`）
- `commits/knife` **>> 1.5**；每 slice 潜在一次 Rule 10 + 同步等 CI（ledger 1878–1880）

**After（464e6edf9 knife-merge adoption + S7 inventory）**

- **1 commit**，**31 files**，+868/−128 lines
- 捆绑：§15 eng_gov binding、`pre_knife_audit.py`、`agent-boot` 提醒、S7 ssot **41→36**、5 域 formalize tests
- 演示 **刀级合并可行**；§15 adoption **started**，非 **closed**

**Contrast verdict**：编排 **政策面 FIXED**；**行为面 PARTIAL** — 一刀示范存在，59-commit 窗口说明 **旧习惯仍占多数 session 墙钟**。

### 1.5 对 ANY model 仍慢的事（不可甩锅「模型慢」）

1. **读 owner 链 + boot + 窄回归** — 每 session 固定 ~10–15s + 任务文档
2. **L3 全 pytest ~2min** — 模型无关；换任何模型都要付
3. **Tier0 strangler 单位刀** — 单域 S7 = inventory + DataAccess + tests + legacy gate；非「改一行」
4. **≤40d 授权窗** — daily 2019→2026 = **~19× chunk**；日历设计，非 bug
5. **DuckDB 单写** — land/accept/derive 同库串行
6. **Subagent connection death** — goal 禁令；2 行 transcript = 人/编排税
7. **Rule 10 blocking** — 按刀一次仍要 **人/审查 latency**（分钟~小时），非 pytest 秒数
8. **WP6 shadow** — agent 过度保守（ceremony 未 flip）

---

## 2. Docs / process drift audit（misleading stubs）

| # | 路径 | 误导引用 | 处置 |
|---|---|---|---|
| 1 | `backend/scripts/build_agent_board.py` `_next_knives` | `"A→H next: F main_rally B0–B2…"` / `"or stop"` owner-choose 菜单 | **FIXED** → S7/E0/§15 投影 |
| 2 | `BOARD.md` / `data/board/agent_context.json` | 同上 stale next menu；track `a_to_h_resumed` | **REGEN** via build_agent_board |
| 3 | `analysis/data_foundation_modularity_gap_20260720.md` §0 | `"NOT SHIPPED"` 无 amend → agent 以为 S1–S6 未落地 | **FIXED** top amend + §8 指针 |
| 4 | `analysis/plan_reeval_evidence_pack_20260720.md` §3 | BOARD stale 仍当事实 | **FIXED** stale 标注 + fix 指针 |
| 5 | `analysis/forward_program_efgh_20260720.md` | 读作近端 P0+P1 菜单 | **FIXED** superseded banner |
| 6 | `analysis/plan_reeval_first_principles_20260720.md` §1.2 | 「四地基 READY → 编排 OK」**REJECT** 已写 — 保留作反例 | no change（已是 owner 裁决） |
| 7 | `analysis/data_foundation_modularity_gap` §3.1 | `capture_and_publish` 胶合点描述 | **保留** 作历史证据；header amend 指向 §8 |
| 8 | ledger `S3 NOT SHIPPED` (2339) | 历史条目 | **保留** ledger 不可改历史；本 note  supersede |

**未改（有意）**：`sync_runner.py` docstring「not default fused sync」= **正确** 技术说明；cutover false **测试/fixture** 叙述 = 合法。

---

## 3. Owner clarifications

### 3.1 「续减 ssot」是什么？

**定义**：S7 `legacy_raw_plane.yaml` + `check_legacy_raw_plane.py` 维护的 **46 域 inventory** 中，仍被分类为 **`role=ssot` 且 publication/serve 经 raw_tushare_*`** 的表 — 当前 **32/46**（1 fill，13 compatibility）。

**任务**：逐域 **formalize**（publication → accepted/mart/DataAccess）或 **sunset**（证据 + gate 降级）— **禁盲删 raw**。

**与 data-foundation 主线关系**：

- **不是** parallel hobby；是 **plan_reeval §0 transport strangler S7** 的 residual
- S1–S6（land/accept/sync/derive/serve）**FIXED** 后，**剩余模块化债 = raw 平行面 inventory**
- 对齐 `db_layering`：**E0 证据层** strangler — legacy raw 镜像 permanent until sunset，不是第二 DB

**近端 exemplars**：`dc_member` ssot；stock-flow drill L0；`stk_limit`/`daily_basic`/`suspend_d`/`margin_detail` **BLOCKED**（无诚实 publication）→ 不 fake FIXED

### 3.2 Foundation stage table（2026-07-21）

| Stage | 状态 | 一句话 |
|---|---|---|
| **S1** land-only | **FIXED** | `capture_and_land_*`；CLI `--land-only` |
| **S2** accept-from-landing | **FIXED** | 零 acquire 重焊 |
| **S3** sync caller-only | **FIXED** | default land→accept；无 `capture_and_publish_*` fan-in |
| **S4** acquire swappable | **FIXED** | `security_day_acquire` provider \| local-raw |
| **S5** derive from accepted | **FIXED** | `chunkyctl derive`；零 fused publish |
| **S6** serve via DataAccess | **FIXED** | pulse drill/members；router 零 serve-exempt |
| **S7** legacy raw plane | **PARTIAL** | inventory **32 ssot**；续减 ssot 主线 |
| **E0** disclosure transport | **PARTIAL** | stk/holders provider land OK；org **BLOCKED** |
| **E/F remeasure** | **paused** | F0–F3 protocol-complete reject；window unblocked 但 owner paused |
| **Acquire-filter docs** | **FIXED** | full-market acquire；ST/BSE/delist at universe read |
| **DB layering** | **authority live** | 逻辑 E0→R1；物理单 DuckDB；禁阶段拆库 |

---

## 4. Remaining optimizations（ranked；**不**放宽 PIT/Rule10/≤40d）

| Rank | 提案 | 预期 | 风险 |
|---:|---|---|---|
| **1** | **§15 行为闭合**：刀内合并 diff；`commits/knife ≤1.5`；禁 sync `gh watch` | **−30~40% session 墙钟** | 需 controller 纪律 |
| **2** | **刀前 `pre-knife` 一次**（已 ship 0.64s） | **−20% 返工** | 无 |
| **3** | **S7 按域排刀**（每周 −2~4 ssot） | 提高 merged 表/刀 | 单域仍 L3 全门 |
| **4** | **authorized chunk batching**（同 session 连续 ≤40d） | **−session 重启/boot 税** | 仍须 ≤40d |
| **5** | **父会话直做 / shell 子代理**（禁空转 subagent） | **−Multitask 空等** | 无 |
| **6** | **WP6 ceremony flip**（owner-gated） | **−过度保守** | 须 checklist |
| **7** | **owner-doc 读一次/任务**（§15.3 已有） | **−重复 MASTER 读** | 无 |
| **Reject** | `agent-boot --fast` | ~7s | 丢 moth 价值 |
| **Reject** | L2 含 `backend/services/` | ~11s/刀 | Tier0 假绿 |
| **Reject** | skip Rule 10 / shrink L3 pytest SSOT | 快但假绿 | 禁令 |

---

## 5. Status

**FIXED**（本 validation 交付）。

**Next verification（2026-07-28）**：采样一周 session — `commits/knife`、Rule10 次数/刀、是否仍 sync CI watch、S7 inventory delta、BOARD regen 是否仍与 goal 一致。

---

## 6. Evidence index

| 主题 | 位置 |
|---|---|
| T0 门耗时 | ledger L1862–1907；eng_gov §15 L306–311 |
| Throughput 诊断 | `analysis/throughput_bottleneck_diagnosis_20260721.md` |
| §15 knife-merge commit | `464e6edf9` |
| ci_pytest SSOT | `backend/config/ci_pytest_surface.yaml`；543c9e478 |
| S7 inventory | `legacy_raw_plane.yaml`；moth `legacy-raw-plane-inventory` |
| Foundation stages | `goal.md`；modularity gap §8；plan_reeval §0 |

# Gate 栈 Occam 重设计 — 985 tests ≠ 985 gates（2026-07-21）

> **生命周期**：evidence-only / owner 裁决输入（非 owner contract）  
> **Authority**：`AGENTS.md` → `goal.md` → `docs/engineering_governance.md` §14–§15 → `backend/config/commit_tiers.yaml` / `ci_pytest_surface.yaml`  
> **方法**：Mio（真金白银后果 + 目标量 vs 诊断量 + 流程根治）+ Occam（删实体前先证同等解释力）  
> **触发**：owner 挑战 — 「~980 pytest 像 ~1000 个 gate；门一直在但地基仍破 → 门设计可能错了」

---

## 0. TLDR（直说）

| 问 | 答 |
|---|---|
| **980 是 gate 吗？** | **不是。** 实测 **985 pytest 用例**，跑在 **1 个** `ci_pytest` 门里；整个仓库另有 **~1903** 用例，其中 **~918** 在 **90 个文件**里**根本不阻塞** commit/CI。 |
| **真实 blocking 面有几个？** | L3 `safe_commit` **~18 个具名门** + **Rule 10**（按刀，非按 test）+ GitHub CI **~6 步**；L1 **6 门**；L2 **17 门**（无 live continuity/grain）。 |
| **门多所以地基还破？** | **半对。** 门**多且层错位**：大量测 **mock/静态**，live readiness（continuity/grain/F6 广度）**与代码 commit 解耦**；**87/90** optional 文件 = **未 triage 的盲区**，含 tier12/PIT/sync_runner 合同测试。 |
| **Occam 方向** | **少 blocking 实体、多分层 signal** — 快面保 **contract + drift + moth**；重面 **nightly/async**；**提升**真正缺的 PIT/tier12 合同进 blocking；**降级** strategy-paused 的 B0–B2 大块 parametrization。**不**砍 Rule10/PIT/≤40d。 |
| **下周第一刀** | 给 `ci_pytest_surface.yaml` 加 **`blocking` / `nightly` 两档**（SSOT 不破）；strategy 暂停域移 nightly；tier12 publish contract **5 文件** promoted；continuity **脚本门保留 L3**，pytest 版进 nightly+live job。 |

**裁决标签**：**FIXED（#1）** — `blocking_paths`/`nightly_paths` + `--tier` + safe_commit/CI blocking **SHIPPED** 2026-07-21；#4 nightly schedule / #5–#6 triage **DEFER**。

---

## 1. 概念澄清：985 tests ≠ 985 independent product gates

### 1.1 实测数字（2026-07-21，本机）

| 度量 | 值 | 来源 |
|---|---:|---|
| CI/safe_commit **blocking** pytest 用例 | **985** | `run_ci_pytest.py --collect-only` |
| blocking **测试文件** | **61** / 151 (**40%**) | `ci_pytest_surface.yaml` `paths` |
| **未进** blocking 面的文件 | **90** | `ci_test_optional` |
| optional 内用例（collect） | **~918** | 同命令 collect optional paths |
| 全库用例（含 1 collect error） | **~1903** | `pytest tests --collect-only` |
| L3 `ci_pytest` 墙钟 | **~123s** | `process_efficiency_validation_20260721.md` |
| L3 全门（不含 Rule10 等人 latency） | **~27s** | eng_gov §15 T0 |
| L3 commit 真实墙钟 | **~2.5min** | pytest + 门 |

**心理账户谬误**：把 pytest 输出里的 `985 passed` 读成「985 次产品验收」= **把诊断量当目标量**（Mio #10）。实际是一次 **`ci_pytest` 门**跑完一整张离线面。

### 1.2 真实 gate 分层图

```
                    ┌─────────────────────────────────────────┐
                    │  Human / Codex Rule 10 (按「刀」, blocking) │
                    └────────────────────┬────────────────────┘
                                         │
     ┌───────────────────────────────────▼───────────────────────────────────┐
     │ safe_commit.sh — tier 机器分类 (commit_tiers.yaml, fail-closed→L3)      │
     ├──────────── L1 (~6 门, ~1.6s) ─────────────────────────────────────────┤
     │  project_index_sync, sandbox, agent_board, doc_drift, doc_governance,  │
     │  commit_msg — 无 moth / 无 pytest / 无 Rule10                          │
     ├──────────── L2 (~17 门, ~17s) ─────────────────────────────────────────┤
     │  L1 子集 + feature_map, moth, rule_compliance, ci_pytest(985),          │
     │  serve_read_layer, calendar, population_contract, lineage, dead_refs,   │
     │  config_refs, Rule10 — 无 continuity / 无 grain (live DB)               │
     ├──────────── L3 (all ≈18 门, ~27s + pytest ~123s) ──────────────────────┤
     │  L2 + grain_uniqueness + continuity (live DuckDB readiness 投影)        │
     └───────────────────────────────────┬───────────────────────────────────┘
                                         │
     ┌───────────────────────────────────▼───────────────────────────────────┐
     │ GitHub CI (.github/workflows/ci.yml) — server-side, L1 push skip       │
     │  ruff (continue-on-error) | run_ci_pytest (同 985) | static gates      │
     │  | check_foundation_done --skip-live | smoke import | dead_refs        │
     └───────────────────────────────────┬───────────────────────────────────┘
                                         │
     ┌─────────────── 非 commit blocking ─────────────────────────────────────┐
     │ pre-knife (moth impact + codegraph) — L3 纪律，~0.6s，不 hook           │
     │ chunkyctl doctor — FND-GATE 聚合 + moth + brick orphan 投影            │
     │ FND-GATE — check_foundation_done.py F1–F10 (CI --skip-live)            │
     │ brick/legacy — check_legacy_raw_plane.py (pytest + moth claim)         │
     │ continuity/grain 脚本 — L3 safe_commit 才跑 live DB                    │
     │ ~918 tests in 90 files — 默认不跑，drift test 仅防「新文件漏登记」       │
     └─────────────────────────────────────────────────────────────────────────┘
```

### 1.3 各层职责（一句话）

| 层 | 拦什么 | 不拦什么 |
|---|---|---|
| **L1/L2/L3 tier** | 按 staged 路径决定**跑哪些门** | 不保证 Tier0 live READY |
| **Rule 10** | 高风险 diff 的人审 | 不验证 DB 行数 |
| **moth assert/coupling** | 声明 vs 代码/配置漂移 | 不跑 provider |
| **ci_pytest (985)** | 离线 contract/regression | live DB、网络、perf |
| **continuity/grain (L3 only)** | live DB 重复/缺口/就绪 | L2 改测试可绕过 |
| **FND-GATE** | 阶段闭合 F1–F10 聚合 | `--skip-live` 时不查 F4/F6 DuckDB |
| **pre-knife** | 刀前 impact | 不 block commit |
| **doctor** | 运维可读健康投影 | WARN 可继续 |

---

## 2. 为什么「很多测试」仍抓不到 live foundation bugs

### 2.1 层错位（Occam 首选解释 — 假设最少、解释力最强）

| 现象 | 根因 | 证据 |
|---|---|---|
| 代码 commit 绿，continuity **BLOCKED** | **设计如此**：continuity = live readiness 投影，**不 block L2**；grain 同理仅 L3 | `commit_tiers.yaml` L13–15；eng_gov §14 |
| PIT/tier12 publish 回归漏了 | **合同测试在 optional**，不在 985 blocking 面 | `test_tier12_publish_*.py` 等 **8 文件**全在 `ci_test_optional` |
| sync_runner 行为漂移 | **6 个** `test_sync_runner_*.py` optional；blocking 面只有 policy/modularity 子集 | yaml optional 列表 |
| 「universe 换了测试还绿」 | 部分测 **fixture 宇宙**，未绑 registry snapshot hash；AGENTS §7 已写 false-green 禁令 | 治理文 + 未 wired 的 optional 审计测 |
| mock 绿、landing 真脏 | 测 **writer 逻辑**不测 **provider 响应保留**；Tier0 真相在 live sync | MASTER Tier0 landing purity |
| 985 绿但 foundation 仍 PARTIAL | FND-GATE **typed wall** 设计：23 ssot / org BLOCKED = **诚实 PASS**，非测试失败 | `foundation_done.yaml` |

**结论**：不是「门不够」，是 **blocking 面测错了层** + **该 block 的合同测试被丢在 optional 坟场**。

### 2.2  false green 机制（治理已识别，未完全收口）

1. **Obsolete universe/PIT** — 测的是旧表名/旧 cutover；绿 = 测仍通过，非 live 可用（AGENTS §7）。
2. **Optional 坟场** — **87/90** optional 理由 = 「2026-07-20 CI audit gap，未 triage」；不是「 intentionally 不需要」。
3. **safe_commit 历史债** — 2026-07-20 前本地 **零 pytest**；SSOT 已修，但 optional 大面积未回填。
4. **测 mock 不测 contract** — `integration` marker 默认跑，但 **realdb** 被 `pytest.ini` 全局 deselect；live 一致性在 optional。
5. **诊断量冒充安全感** — 985 passed 给 **floor 完整幻觉**；**ceiling**（live frontier、F6 overlap、S7 ssot 墙）在 doctor/FND-GATE/continuity。

### 2.3 什么测试**有用**（保留 / 加强）

| 类别 | 示例（已在 blocking） | 价值 |
|---|---|---|
| Transport formal boundary | `test_formal_boundaries`, `test_security_day_*`, `test_legacy_raw_plane_s7` | strangler 不回流 |
| Drift 防复发 | `test_ci_pytest_surface_drift`, `test_check_dead_references` | SSOT 不漂 |
| Dataset contract static | `test_dataset_contracts`, `test_population_scope` | Tier0 词汇 |
| Brick registry | `test_brick_registry_b5` | B5 lineage |
| FND-GATE self-test | `test_check_foundation_done` | 聚合门可测 |

| 类别 | 示例（**应在** blocking 但 **不在**） | 价值 |
|---|---|---|
| tier12 publish contract | `test_tier12_publish_contract/accept/writer/scope` | **PIT 发布链** |
| sync_runner integrity | `test_sync_runner_integrity`, `watermark_decouple` | landing→accept 原子性 |
| continuity **脚本** pytest | `test_check_continuity_integrity*` | verifier 自身不退化 |

| 类别 | 示例（应 **nightly** 非 pre-commit） | 理由 |
|---|---|---|
| Strategy B0–B2（paused） | `test_main_rally_b*`, `test_institution_follow_b*` 大块 | 非近端主线；param 多 |
| Perf | `test_perf_p1_trade_date` | 已标记 slow/perf |
| realdb | `test_real_data_consistency`, `test_system_routes` | 需本地 DuckDB |

---

## 3. Occam 重设计：更少 blocking 实体，更清晰 signal 分层

### 3.1 原则（Einstein corollary）

- ** as simple as possible **：commit 路径只保留 **快 + 高后果** contract。
- ** but no simpler **：PIT/tier12/moth/dead_ref **不能**为提速删掉，只能 **换档位**（blocking vs nightly）。
- **Enforcement 沉到提交者够不到处**（Mio #7）：blocking 面 = safe_commit L2/L3 + CI；nightly = server schedule，本地可选。
- **禁止**：agent 自降 tier、skip Rule10、L2 含 `backend/services/`、缩小 PIT 断言。

### 3.2 目标态：三档 pytest + 现有脚本门

| 档位 | 何时跑 | 内容（草案） | 墙钟目标 |
|---|---|---|---|
| **`blocking`** | L2/L3 safe_commit + CI push | formal contract、drift、moth、legacy S7 gate、**tier12 publish 核心**、FND-GATE self-test | **≤90s** pytest（从 123s 降下来靠移出 paused 域） |
| **`nightly`** | GitHub schedule / 手动 | strategy paused 套件、sync_runner 全量、calendar 细分、continuity pytest、optional 已 triage 的 integration | 15–30min 可接受 |
| **`live`** | 有 DuckDB 的 runner / doctor | realdb、FND-GATE full、continuity/grain 脚本（L3 仍保留脚本门） | 按需 |

**关键**：不是减 **测试总数**，是减 **pre-commit 上的实体数**（文件/门次数），把 signal 挪到对的层。

### 3.3 blocking 脚本门（保持 / 微调）

| 门 | 档位 | 动作 |
|---|---|---|
| moth assert/coupling | **blocking** L2+ | 保持 |
| dead_references | **blocking** L2+ | 保持 |
| population/calendar static | **blocking** L2+ | 保持 |
| continuity/grain **脚本** | **blocking L3 only** | 保持（live 不可假绿） |
| Rule 10 | **blocking L2+** | 保持；粒度=刀 |
| FND-GATE `--skip-live` | **CI blocking** | 保持 |
| ruff | **CI non-blocking** | 保持 continue-on-error 或移 nightly |
| doc_drift/doc_governance | **L1/L2 blocking** | docs 刀保留；可考虑 nightly 扫全 repo 减 L2 重复 |

### 3.4 删除 / 合并候选（需刀 + 证据，非本 commit）

| 候选 | 理由 | 风险 |
|---|---|---|
| 重复 calendar 测（5 optional + 部分 blocking） | 同契约多文件 | 删前 moth impact |
| `test_sync_runner_20260612_fixes` 等日期命名遗留 | 一次性 fix 命名 | 合并进 `test_sync_runner_integrity` |
| institution_follow B0–B4 与 main_rally 重叠断言 | strategy paused | 移 nightly 先，不删 |
| 87 个「gap」optional 中 **无 collect** 的 | `test_build_dc_industry_view` 已 collect error | 修或删 |

**明确 reject**：删 PIT tier12 断言、删 `test_legacy_raw_plane_s7`、删 `test_formal_boundaries`。

---

## 4. 下周 ranked 改动（可执行）

| Rank | 改动 | 预期收益 | 工作量 | 刀级 |
|---:|---|---|---|---|
| **1** | **`ci_pytest_surface.yaml` 两档**：`paths` → `blocking_paths` + `nightly_paths`；`run_ci_pytest.py --tier blocking\|nightly\|all` | pre-commit **−30~40s**；语义清晰 | 1 L3 刀 | 改 runner + CI workflow + drift test |
| **2** | **Promote tier12 publish 5 文件** → blocking（contract/accept/writer/scope + `test_tier12_project_universe`） | 关 PIT 发布链假绿 | 含在 #1 | 禁削断言 |
| **3** | **Demote strategy paused 8 文件** → nightly（main_rally b0–b2、institution_follow b0–b4） | blocking **−200~300** 用例量级 | 含在 #1 | 仅换档位 |
| **4** | **Nightly workflow** `ci-nightly.yml`：`run_ci_pytest --tier nightly` + optional triage 批次 | optional 坟场 **−10 文件/周** | 1 L2 刀 | 不 block merge |
| **5** | **Continuity pytest → nightly+live**；**保留** L3 `check_continuity_integrity.py` 脚本门 | verifier 不退化 + 不拖每 commit | 1 L3 刀 | 脚本门不动 |
| **6** | **Optional triage SOP**：每周 5 文件 — offline-safe → nightly；需 DB → live；obsolete → 删 | 90 optional **8 周内清完** | 持续 | pre-knife each |
| **7** | **Gate 地图进 `goal.md`**（本分析指针） | 少误解 985=gate | 本 commit | L1 |
| **8** | **§15 行为**（已 PASS）维持；**不** sync `gh watch` | 墙钟 **−30%** session | 0 code | 纪律 |

**Reject 榜**（Occam 删掉的高假设解释）：

- 「再加 500 tests 就能抓 live bug」— 层错了加量无用。
- 「L2 开放 services 提速」— Tier0 假绿，ledger 已 reject。
- 「skip continuity 因为慢」— 高后果 live 洞，只能分档不能删。

---

## 5. 与现有交付的关系

| 已有 | 关系 |
|---|---|
| WP1 tiered safe_commit | **保留**；本设计补 pytest **子分层**，不改 tier 语义 |
| ci_pytest SSOT (543c9e478) | **演进**为 blocking/nightly SSOT，防 drift test 继续生效 |
| FND-GATE (eefd19e53) | **保留**；F8 §15 PASS 不依赖 985 数量 |
| process_efficiency / throughput 诊断 | **一致**：pytest tax 是 L3 墙钟主因；本设计直接动 tax |
| S7 23 ssot typed wall | **不碰**；legacy gate 留 blocking |
| §15 knife-merge | 改 `ci_pytest_surface.yaml` = **单 L3 刀** + pre-knife + Rule10 |

---

## 6. 验收（2026-07-21 #1 SHIPPED；原目标窗 2026-07-28 部分提前）

| 检查 | 通过标准 | 实测 2026-07-21 |
|---|---|---|
| blocking pytest 墙钟 | **≤90s** 本机（strategy 域 nightly 后） | **~18s** / 950 passed（`--tier blocking`） |
| blocking 用例数 | **≤750** 且 **含** tier12 publish 5 文件 | **950**（含 tier12；strategy 已出 blocking；≤750 未达 — optional triage 续降） |
| nightly workflow | 至少 **1 次** schedule 绿 | **DEFER**（`--tier nightly` 可用） |
| optional 未 triage | **≤82**（−5/周） | 未动（#6 DEFER） |
| PIT 测试 | blocking 或 nightly **有跑**，不得 silent optional | tier12 publish **5** 文件 in **blocking** |
| 误删 regression | `test_legacy_raw_plane_s7` + `test_formal_boundaries` 仍在 blocking | 保持 |

**Owner Q2/Q3（同刀文档化）**：orphan blanket pre-accept=**NO** + watchlist + DataAccess thin FAIL；period domains manual update=**incremental-only**（缺才拉；禁 ~830k refresh）。

---

## 7. Evidence index

| 主题 | 位置 |
|---|---|
| pytest SSOT | `backend/config/ci_pytest_surface.yaml` |
| runner | `backend/scripts/run_ci_pytest.py` |
| commit tiers | `backend/config/commit_tiers.yaml` |
| safe_commit 门集 | `scripts/safe_commit.sh` |
| CI | `.github/workflows/ci.yml` |
| FND-GATE | `backend/scripts/check_foundation_done.py` |
| 墙钟实测 | ~~`analysis/process_efficiency_validation_20260721.md`~~ **已被 `2d8f1dbb9`（2026-07-23 doc governance 删 62 份）删除，内容见 git history** |
| 吞吐诊断 | ~~`analysis/throughput_bottleneck_diagnosis_20260721.md`~~ **已被 `2d8f1dbb9`（2026-07-23 doc governance 删 62 份）删除，内容见 git history** |
| §15 / F8 | `analysis/section15_verify_20260721.md` |
| eng_gov §14–§15 | `docs/engineering_governance.md` |

---

**状态**：**#1 FIXED / SHIPPED**（2026-07-21）— blocking/nightly SSOT + `--tier` + L2/L3/CI=`blocking`；tier12 promote + strategy demote。**#4–#6 DEFER**（nightly schedule + optional triage）。

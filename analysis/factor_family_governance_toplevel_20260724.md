# 因子族治理 · 顶层设计（2026-07-24）

> **生命周期**：evidence-only · **非** owner bible  
> **Authority chain**：`goal.md` → `docs/MASTER_TOPLEVEL_DESIGN.md` → `docs/strategy_validation_contract.md` → `analysis/data_brick_architecture_20260721.md`  
> **Companion stub**：`backend/config/factor_family_inventory.yaml`（声明面；门未接线）  
> **Moth**：`moth inspect --repo . --task-kind architecture_orchestration` @ 2026-07-24（见 §7）  
> **Verdict**：**FIXED（设计）** · 实现 = 下一刀 inventory+gate 脚本（RX 前）

---

## 0. Executive（回答 owner 三问）

### Q1 — 今天的底座能否支撑「因子族管理」？数据分层是不是一回事？要不要在 RX 前做 inventory+gates？

| 概念 | 是什么 | 不是什么 |
|---|---|---|
| **数据分层**（口语多义） | (a) **传输/积木轴**：L0 证据 → L1 接受 → L2 原语 → L3 组合砖（`data_brick_architecture`）；(b) **遗留物理层**：`data_layers.yaml` 的 L0_source / L1_foundation / L2_feature(wiped) 等 | 策略 B0–B5 的消融登记本 |
| **因子族登记** | Tier3 **治理行**：按 **语义族**（价量、状态、感知、资金代理、披露/机构、公式）列出 **FeatureBlock / brick 绑定**、**频率与 availability 轴**、**可堆叠门**、**daily_update 责任面** | 另一套「第五产品」或 panel god 表 |

**结论（诚实）**：

1. **同一栈、不同行类型**：因子族 **坐在** L2/L3 + sync/domain 契约 **之上**，不替代 Tier0 或 brick_registry；它回答「研究冻结 snapshot 时，哪些族可 intersect、谁 owner 刷新、堆叠前缺什么」。
2. **能力 today vs gap**：
   - **已有**：`sync_registry.yaml`（域频率/轴/`data_start`）、`brick_registry.yaml`（L2 primitive + L3 FeatureBlock + Type-B edge）、`serve_derive_closed_loop.yaml`（serve→derive 接线）、`check_brick_registry.py` **PASS**、formal daily **`20190102`→frontier**、B0–B4 代码块与 frozen disclosure snapshot 路径。
   - **缺失**：**跨族 gate matrix**（同一 `DatasetSnapshot` 的 intersect 规则、族级 `coverage_start` vs 诚实 `inconclusive`）、**因子族 id → brick/domain 的唯一 SSOT**（今天散落在 registry + services 命名）、**RX 前 prereg 的族清单**（防 Optuna/堆叠无 owner）。
3. **顺序**：`goal.md` 已裁定 foundation exit **MET**；**仍应在 owner 显式「开 RX」之前** 完成 **inventory + frequency gate matrix**（本设计 + 下一刀 gate 脚本）。否则 B0–B5 会在「窗不对齐」上争论 implementation 而非 verdict。**不**改 `goal.md` 开 RX（除非 owner 写「开 RX」）。

### Q2 — 拉齐 vs 缩短窗：何时必须诚实 `inconclusive`？

Owner 立场：**应对齐历史（拉齐）**，不是默认缩短公共窗。与项目禁令一致：**禁止** pad 缺失年为 0、禁止 invent early years、禁止 mass org backfill。

**决策树（研究 snapshot 公共 `[start, end]`）**：

```text
For each factor-family F with declared axis A_F and frequency f_F:

1) Provider / contract never supplied A_F before date D0?
   (sync_registry.data_start, API 实测空, 或轴本身在 D0 才存在)
   → 公共窗 start = max(requested, D0) 或族 F 标记 OUT_OF_FAMILY for t<D0
   → 若 B0 需要 F 且无法替代 → ExperimentVerdict=inconclusive（不是 pad 0）

2) Same A_F, same f_F, local holes/truncation (bug, pagination cap, drain lag,
   accept reject, DUPLICATE_GRAIN, ops 未跑 bounded catchup)?
   → 拉齐（repair）：holders notice catchup, holdernumber forward backfill,
     org fill_older_period N=1, type_b fact publish catchup — 有界、可审计
   → 拉齐完成后才允许该族进入 snapshot intersect

3) Different frequency or different semantic axis (report_date vs notice_date,
   quarterly org vs daily K)?
   → 禁止「拉齐成日频全历史」；snapshot intersect 在 **决策时点语义** 上对齐
   → 缺族用 NULL/unknown + gate 计数；verdict 可 inconclusive，不得 fake 日频

4) Historical depth never acquired by policy (org 中间季 log-not-fill, legacy orphan)?
   → 不是 class-A 错轴 → DEFER / 族级 coverage 声明
   → 研究若依赖该族全历史 → inconclusive 或缩小到「有 accepted 证据的区间」
   → 禁止 mass ~830k / by-date invent 去「拉齐」

5) Misaligned block starts in B0–B5 code (block A from 2020, block B from 2019)?
   → 先查 (2) 是否 repairable；否则 (1)(3)(4)
   → 禁止 silent shorten 仅为了 lift；必须 prereg 写 common window + 族 exclusion 理由
```

**典型映射（live 证据）**：

| 场景 | 拉齐 | 缩短 / inconclusive |
|---|---|---|
| holders notice 洞、holdernumber drain lag | **拉齐**（`data_axis_frequency_review_20260724` A1/A2 FIXED） | — |
| moneyflow/limit fact max < raw（Type-B publish lag） | **拉齐**（type_b catchup，非改轴） | 若 catchup 未跑完且 block 硬依赖 → **inconclusive** |
| margin accepted UNTRUSTED、缺日 | 产品 gating；**不**冒充 project_universe 统计 | B3 若硬依赖 margin 全历史 → **inconclusive** 或 block 不含该族 |
| org_holding 仅 3 期、中间季洞 | period N=1 + ops drain（**拉齐有界**） | 指望 2019 全季无 landing → **不能 invent**；全历史机构 block → **inconclusive** |
| vendor 2010 无 moneyflow | — | **start≥data_start**；禁止 0-pad |
| qfq 分析序列 vs 名义成交 | qfq **非** execution truth | L4 纸面仍名义价；拉齐 qfq 不改变 B0 名义窗 |

### Q3 — 从 `goal.md` 目的到 B0–B5 的顶层设计（非 panel 复兴）

**目的链**（MASTER §1 + goal Tier 表）：

```text
Tier0 可审计事实 → Tier1 状态 → Tier2 感知 → Tier3 冻结 snapshot 上 B0→B5 消融
→ Tier4 StrategyRelease / 纸面（仅 released）
```

**设计原则**：

1. **一块 = module+data+config+contract+evidence**；策略只加 **命名 FeatureBlock**（`strategy_validation_contract`），不复活 `fact_feature_panel`。
2. **L4 只在 frozen `DatasetSnapshot` 上**；族 inventory 定义 snapshot **输入闭包**（哪些 L2/L3/域 partition 进入 hash）。
3. **首包** `institution_follow`：B0 裸 K → B1 状态 → B2 感知 → B4 机构（B3 可选）；与 `brick_registry.feature_blocks` 已有 id 对齐。
4. **daily_update** 只刷新 **族 inventory 标记 `refresh_owner: daily_process|acquire`** 的 derive/acquire；研究列 **不** 默认全量重算（L4 wipeable / artifact）。

---

## 1. 数据分层 vs 因子族登记（同栈、不同行）

| 层/登记 | 粒度 | 消费者 |
|---|---|---|
| MASTER Tier 0–4 | 业务依赖 | 全项目 |
| 传输轴 landing→serve | 每 **数据集** | sync/accept/derive |
| 积木 L0–L3（`brick_registry`） | 每 **brick / FeatureBlock** | derive、Tier12 publish、研究块 |
| `data_layers.yaml` | 每 **物理表** + asset_class A/B | 遗留 audit、Type-A/B 列纯度 |
| **`factor_family_inventory`** | 每 **语义族** + gate 行 | RX prereg、snapshot intersect、continuity 解释 |

因子族 **聚合** 多个 brick/domain 行，并附带 **B 阶梯角色**（B0 价量、B1 状态…）与 **stack_gate**（堆叠 Bk 前必须 PASS 的检查）。

---

## 2. Capability today vs gap

### 2.1 已有（证据）

| 能力 | 位置 | 状态 |
|---|---|---|
| 域频率/轴/data_start | `sync_registry.yaml` | FIXED（轴评审 `data_axis_frequency_review_20260724`） |
| L2/L3/FeatureBlock 登记 | `brick_registry.yaml` + `check_brick_registry.py` | **PASS**（0 violations） |
| B1–B4 FeatureBlock ids | `institution_follow_b*.py`, `main_rally_b*.py` | declared in registry |
| Serve→derive 闭环 | `serve_derive_closed_loop.yaml` | wired（pulse/form/org/type_b） |
| Formal daily frontier | goal + accepted canonical | `20190102`→`20260721+` |
| Strategy 立法 | `strategy_validation_contract.md` | B0–B5、PIT、inconclusive |
| RX 门 | `STRATEGY_EXECUTION_PLAN.md` | **BLOCKED** until goal 显式 RX |

### 2.2 缺口（RX 前应收口）

| Gap | 风险 | 下一刀 |
|---|---|---|
| 无 `factor_family_inventory` SSOT | 堆叠时窗/轴口头争论 | stub YAML + `check_factor_family_inventory.py` |
| 无 gate matrix 机器可读 | 静默 shorten / 假 green snapshot | `gate_matrix` 节 + pytest 固定 B0–B4 闭包 |
| 族级 continuity 未投影 | Continuity WARN 与 research gate 脱节 | 复用 `data_frontier_detection_system` 输出族 frontier |
| Type-B publish lag（moneyflow/limit） | B3 inconclusive 未 prereg | gate 行 `type_b_fact_publish` DEFER→PASS 条件 |
| org 历史季洞 | B4 全历史 claim 过强 | 族 `org_disclosure` 声明 `coverage_mode: bounded_fill` |

---

## 3. 提议：inventory schema + gate matrix + daily_update ownership

**文件**：`backend/config/factor_family_inventory.yaml`（v1 stub 已建）

### 3.1 `families` 最小字段

| 字段 | 含义 |
|---|---|
| `family_id` | 稳定语义 id（如 `price_volume_daily`） |
| `b_block` | B0…B5 主角色（可多族映射同一 block） |
| `frequency` | `daily` / `event` / `quarterly_period` / `on_demand` |
| `availability_axis` | 与 registry/brick 一致（如 `trade_date`, `notice_date_and_available_at`） |
| `sync_domains` | `sync_registry` 域 id 列表（可空，纯 derive 族） |
| `bricks` | `brick_registry` brick_id / feature_block id |
| `refresh_owner` | `acquire` / `daily_process` / `daily_acquire_catchup` / `manual_only` |
| `coverage_start_policy` | `registry_data_start` / `accepted_frontier` / `bounded_fill` / `honest_sparse` |
| `stack_eligibility` | `ready` / `defer` / `blocked` + typed reason |

### 3.2 `gate_matrix`（堆叠前）

每行：`gate_id`, `requires_families[]`, `check`（脚本/id）, `on_fail`（`block_stack` | `inconclusive_only` | `warn`）

示例逻辑（stub 已列）：

- **G0**：formal daily + nominal accepted frontier ≥ snapshot.end − 1 交易日  
- **G1**：B1 前 `tier1_stock_state` + form derive wired  
- **G2**：B2 前 `MarketContextSnapshot` decision_time 路径  
- **G4**：B4 前 holders canonical notice 洞 = 0（或可 prereg 的 canary 窗）  
- **G3**：B3 前 type_b moneyflow fact 与 raw 前沿对齐或 prereg 排除 B3  

### 3.3 daily_update ownership（与闭环法一致）

| 族 | refresh_owner | 证据 |
|---|---|---|
| 价量/日历 | acquire formal daily + qfq derive | sync_registry + S5 |
| 状态 B1 | `daily_process` technical_states | serve_derive `stock_form` |
| 感知 B2 | `daily_process` market_pulse + tier12 | serve_derive `market_pulse_panels` |
| 披露 B4 | acquire holders incremental + dossier derive | serve_derive `institution_profile_dossier` |
| 机构持仓季频 | acquire org period gap N=1 | serve_derive `org_holding_formal` |
| Type-B 日频 fact | `daily_acquire_catchup` | serve_derive `type_b_fact_publish` |
| L4 实验列 | **manual_only** / RX runner | 禁止 daily 默认全量 |

---

## 4. 执行序列（foundation → inventory → RX → B0…）

```text
[Done] Foundation exit MET (F1–F10, §6, 100% usable no class-A)

[Next knives — RX 前]
  K1  factor_family_inventory.yaml 完整族行 + check 脚本 + blocking pytest 面（L2）
  K2  gate_matrix 与 frozen snapshot builder 对齐（只读验证，不改 B0 逻辑先）
  K3  族 frontier 投影（continuity → family defer 理由），org/margin 诚实 DEFER 入账
  K4  STRATEGY_EXECUTION_PLAN 引用 inventory exit criteria

[Owner gate]
  goal.md 显式「schedule RX / E-F remeasure」

[RX 后]
  S1 RX-E institution_follow 同 protocol
  S2 RX-F main_rally
  … STRATEGY_EXECUTION_PLAN §4
```

**提议 `goal.md` 子弹（不自动写入）**：

> 近端：完成 `factor_family_inventory` + gate matrix 门（`analysis/factor_family_governance_toplevel_20260724.md`）；**然后** owner 显式 schedule RX。

**硬 ban 重申**：无 `fact_feature_panel`；无 0-pad；无松 PIT；无 owner RX 则无 Optuna/holdout 放宽。

---

## 5. B0–B5 ↔ 因子族映射（首包 institution_follow）

| Block | 因子族 id（提议） | FeatureBlock / 来源 |
|---|---|---|
| B0 | `price_volume_daily` | accepted nominal OHLCV + volume |
| B1 | `stock_state_form` | `stock_state_stage_pattern_v0` + Tier1 publish |
| B2 | `market_sensing_breadth` | `market_sensing_project_breadth_v0` |
| B3 | `vendor_flow_proxy` | moneyflow Type-B（可选；gate G3） |
| B4 | `disclosure_holders_event` | `institution_event_holders_disclosure_v0` |
| B5 | `formula_single` | 公式包（RX 后 G 轨） |

同一 RX run：**一个** `DatasetSnapshot` hash 覆盖上表闭包；消融只 **+1 FeatureBlock**。

---

## 6. 架构合理性（first principles 自检）

| 检验 | 结果 |
|---|---|
| 依赖只向下 | 族登记只 **引用** Tier0–2 产物，不写 canonical |
| 传输 vs 变量正交 | 族不混 landing 与 FeatureBlock |
| 无第五产品 | inventory = config + gate，非新 DB |
| 与 panel 路线切割 | L3 Type-B edge 在 feature_store；无 god panel |
| Strategy 立法 | inconclusive/unknown 合法；pad 0 非法 |
| §15 | inventory 刀 = L2 yaml + check + pytest |

---

## 7. Moth inspect 摘要（architecture_orchestration）

**命令**：`cd` repo · `moth inspect --repo . --task-kind architecture_orchestration --format json`  
**时间**：2026-07-24（agent 会话）

| 子系统 | Verdict | 说明 |
|---|---|---|
| `orchestration.guidance` | PASS | mio + architect-controller 已 DISCOVERED（未 CLAIM，不阻断设计） |
| `orchestration.registry` | PASS | — |
| `snapshot.codegraph` | PASS | index up to date |
| `snapshot.complexity` | PASS | 80 high hotspots；baseline 未配置 → 非 regression |
| `snapshot.coupling` | PASS | 0 fail |
| `project_model.architecture` | OBSERVED / NOT_DECLARED | 无 declared desired 架构图；**无 drift violation** |
| `check_brick_registry.py`（claim） | PASS | 与 B5 设计一致 |
| **`snapshot.assertions` / overall** | **FAIL** | **环境/运维**，非本设计反证 |

**FAIL 根因（read-only 结论）**：

1. **DuckDB 锁**：PID 17876 占用 `smartmoney.duckdb`（与 live daily_update/org repair 一致）→ 多条 smartmoney claims **error**  
2. **smartmoney-size-band**：fail（~8.2GB band）— 容量 hygiene，非因子族架构  
3. **data-layer-integrity**：error（JSON 解析失败，可能同锁或脚本 stderr）  
4. **dirty worktree**：4 untracked（playwright + analysis logs）

**架构 orchestration 解读**：在 claims 可重放且无 DB 锁时，**brick/registry/耦合 PASS**；因子族 inventory **不**与 moth FAIL 冲突——FAIL 不否定「在 brick+sync 之上加 governance 层」的方向。下一刀 gate 应挂 `.moth/assertions` 可选 claim（inventory PASS），勿与 smartmoney size 混谈。

---

## 8. Next knife list（给 parent）

| # | Knife | Tier | Exit |
|---|---|---|---|
| 1 | `check_factor_family_inventory.py` + ci surface | L2 | stub 族全必填；gate_matrix 引用存在 |
| 2 | 族 frontier 只读报告（continuity 投影） | L2 | org/margin/type_b DEFER 进 inventory |
| 3 | Snapshot builder 读 inventory（validate only） | L3 | pre-knife；RX 前 |
| 4 | goal.md RX schedule | owner | 显式一句 |

**Deliverable label**：**FIXED（设计+K1 gate）** · v1 结构门已接线（非 live frontier 门）

**Git**：见 commit SHA（`safe_commit` 后填充 · inventory gate 刀）

---

## 9. References

- `goal.md` · `docs/MASTER_TOPLEVEL_DESIGN.md` · `docs/strategy_validation_contract.md`  
- `analysis/data_brick_architecture_20260721.md` · `analysis/data_axis_frequency_review_20260724.md`  
- `analysis/STRATEGY_EXECUTION_PLAN.md` · `analysis/serve_derive_closed_loop_law_20260723.md`  
- `backend/config/brick_registry.yaml` · `sync_registry.yaml` · `serve_derive_closed_loop.yaml`

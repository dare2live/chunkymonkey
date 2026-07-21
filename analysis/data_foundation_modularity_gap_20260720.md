# 数据地基模块化缺口诊断（2026-07-20）

> 状态：evidence-only / owner diagnosis（含 2026-07-20 业主二次澄清）
> 范围：daily + stock_st formal path；对照「模块边界 + 编排器」需求 vs 已交付实现
> 禁令：不写 Optuna/StrategyRelease/cutover 翻转；本笔记不启动 pipeline rewrite

## 0. 一句话裁决（业主澄清后，不稀释）

### 模块化编排需求是否已 shipped？

**NO。**

近期 daily/ST 路径**没有**交付「独立模块 + 编排器依次触发」；交付的是 **fetch 焊死在 accept 上的一条龙函数**（`capture_and_publish_*`），由 `sync_runner` 直接调用。一键 sync **可以**作为系统级编排存在，但现状不是编排——是融合实现。

库内 `land_*` / `accept_*` 函数缝、landing≠canonical 表 = **正确性基建 partial**，**不算**该需求 shipped。业主「近期从未真正实现」——**确认（confirm）**。

---

## 1. 业主澄清后的需求（唯一验收语义）

| 要 | 不要 |
|---|---|
| 数据管理**模块化**：acquire / process（派生）/ compute / display（serve）**各自边界**，互不焊死 | 把这些 concern **融进一个函数/流水线**，fetch 焊 accept，无法单独跑、无法换源 |
| **一键 / 智能 sync 最新数据 OK**——但是 **系统级编排**：只 *触发* 独立模块按序执行 | 编排器内部再实现一整条「拉数+验收」龙，模块无法被单独调用 |
| 本地 raw / 替代源可进 **acquire→landing**，不必重写整条龙 | 换源 = 改整条 sync_runner 龙 / 改 canonical 读契约 |

对照 MASTER 散文（§3.1 transport、§6.1 stage→validate→accept、§9 adapter 目标态）与上表一致；**验收以「可独立调用的模块边界」为准，不以「文档写了分层 / 函数文件拆开了」为准。**

## 2. Verdict（contract vs shipped）

| 轴 | 结论 |
|---|---|
| **Contract intent** | 分层 lifecycle + 可换 adapter（目标态）已立法 |
| **Shipped（daily/ST）** | `chunkyctl sync --domain daily\|stock_st` → `run_domain` → `_publish_security_day_accepted_partition` → **`capture_and_publish_*`（fetch→land→accept 单调用）** |
| **与业主验收语义** | **未实现**。一键入口存在，但是 **fused pipeline**，不是 orchestrator-of-modules |
| **勿稀释的 partial** | land/accept **函数**与表已分——仅测试/库内可调；**无**运营级独立节点；**无** from-landing / 本地 raw 喂 landing 的正式路径 |

标签：**REQUIREMENT NOT SHIPPED**（operable modular orchestration）。旁注：formal accept **正确性**切片已落地 ≠ 本需求。

## 3. Coupling map（daily / ST）— `sync_runner` 接线证据

### 3.1 胶合点（编排器假装模块，实为焊死）

```text
chunkyctl sync / daily_update → sync_runner.run_domain(daily|stock_st)
  → _publish_security_day_accepted_partition
       # docstring: "Fetch one trade_date and publish accepted …"
       adapter = _adapter(spec["source"])          # LIVE_ADAPTER=tushare only
       def _fetch_rows(...): adapter.fetch_raw(...)
       → capture_and_publish_authorized_*_partition(fetch_rows=_fetch_rows)
            → capture_security_day_provider_rows(...)   # acquire
            → publish_accepted_*_partition(...)         # land + accept 同调用栈
                 → land_*_batch
                 → accept_*_batch
```

硬证据：

1. `sync_runner.py` `_publish_security_day_accepted_partition`：生产路径内联 `_fetch_rows` 后**只**调 `capture_and_publish_*`，不调独立 `publish_accepted_*` / `accept_*`。
2. `nominal_ohlcv_runtime.py` / `stock_st_runtime.py`：`capture_and_publish_*` docstring =「fetch → land → accept one trade_date」——**融合动词是公开 API**。
3. `moth coupling`：`capture_and_publish_authorized_nominal_ohlcv_partition` 生产 fan-in = `sync_runner`；`publish_accepted_nominal_ohlcv_partition` 生产 fan-in **仅**被前者调用（外加测试）。
4. `_adapter()`：多源 registry 已于 2026-07-07 物删；`require_live_adapter` 仅 `tushare`。
5. CLI：无 `--land-only` / `--accept-batch` / `--from-landing` / 本地 raw 注入。

**因此**：`chunkyctl sync` 今日 = **融合实现的入口**，不是「只触发独立 acquire / accept 模块的编排器」。

### 3.2 已有（勿夸大成需求已交付）

| 缝 | 证据 | 相对业主需求 |
|---|---|---|
| landing ≠ canonical 表 | `landing_tushare_daily` / `canonical_nominal_ohlcv_daily`（ST 同理） | 存储分了；**调用未分**；表名仍绑供应商 |
| `land_*` / `accept_*` 函数 | `*_acceptance.py`、`security_day_partition.accept_security_day_batch` | 库内可测；**无编排入口** |
| `security_day_capture` 独立文件 | 注释称与 land→accept 分开 | helper，非可调度模块 |
| qfq 在 clean | `pipeline/clean.py` → `build_price_kline_qfq_tushare.py` | 派生阶段标签存在；日更仍常与 sync 一把跑 |
| `acquire/clean/process/store` 标签 | `stage_status.STAGE_ORDER` | **标签 ≠ 边界**；formal daily 的 acquire 仍=一条龙 sync |
| ≤40d / 禁 mass backfill | `AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS = 40` | 硬门已落地，保留 |

### 3.3 明确未实现

- 可单独运行的 acquire（只到 LANDED）
- 可单独运行的 publish/accept（只吃 landing / 已造型 batch）
- 编排器只做顺序触发（sync = caller-only）
- 本地 raw / 替代源 → landing，而不重写 dragon
- display/serve 与 acquire 解耦（读契约已有 resolver 方向，但本条不洗绿「模块化编排已交付」）

## 4. 先前需求是否落地？诚实结论

- **设计散文**：承诺了分层与可换 adapter（目标态）。
- **近期工程**：交付了 formal landing/canonical/accepted_partition + PIT/≤40d 墙——**正确性**；同时把运营路径做成 `capture_and_publish`，并删掉未用的多源 registry。
- **相对业主本次澄清的验收标准**：**从未实现（NOT SHIPPED）**。
  「函数文件拆开了」≠「模块边界可编排」。不稀释：partial 正确性 **不能**改写为「需求已部分交付为编排」——编排维度是 **零**。

## 5. Forward program：「数据地基做好」（strangler）

目标态：**模块各有边界与入口；`chunkyctl sync` / `daily_update` = 编排器（caller-only），按序触发；本地 raw/替代源只进 acquire→landing；accepted 为项目真相；派生/计算/展示不回写 acquire。**

| # | 切片 | 退出条件 | 禁做 |
|---:|---|---|---|
| **S0** | 本笔记 + goal 指针（立法） | 已入 git | 不改 runner |
| **S1** | **Acquire/evidence 模块**：只 capture→LANDED；同 contract/≤40d/eligibility | 可单独 land；不写 canonical | 第二 DB / plugin bus |
| **S2** | **Publish/accept 模块**：只 from-landing（或已造型 batch） | 可单独 accept；失败不 fetch | accept 时调 provider |
| **S3** | **编排器瘦身**：`sync` / `daily_update` = 依次调用 S1→S2（→派生）；保留一键 UX | 一键仍可用；每步可单独重跑 | 在 sync_runner 内再焊新龙 |
| **S4** | **Acquire 可换源**：本地 raw / 假 adapter / 第二源 → 同一 landing 投影 | 不改 canonical 读契约即可喂 landing | 复活旧 fallback 框架；改 Tier1–4 |
| **S5** | **Derive（process）**：qfq/form 独立入口，只读 accepted（+ 授权 legacy fill 日落） | 无新 provider 可重跑派生 | qfq 进 accept 事务 |
| **S6** | **Compute / display（serve）**：只读 resolver/accepted；失败不回写 landing | dual-track residual 保持 NONE | Optuna / Release / cutover 翻 |

硬约束（全程）：PIT / landing purity / ≤40d / 无授权禁 mass backfill / 无第二 DB / 无 plugin bus / strangler 非 greenfield / 不翻转 cutover。

## 6. 与 A→H 关系

- 本债 = Tier0 **可编排模块化**，正交于 frontier current / F reject / 自然 sync 币值。
- 升为地基优先时：插在 forward program **P0 旁路（S1–S3）**，先于 D1/G/H；**不**洗绿策略轨。

## 7. Evidence index

| 项 | 路径 |
|---|---|
| Sync 焊点 | `backend/services/data_sources/sync_runner.py` (`_publish_security_day_accepted_partition`, `run_domain`) |
| 融合 API | `nominal_ohlcv_runtime.py`, `stock_st_runtime.py` (`capture_and_publish_*`) |
| 库内分缝（非编排） | `security_day_partition.py`, `*_acceptance.py`, `security_day_capture.py` |
| 单源硬墙 | `formal_boundaries.py` (`LIVE_ADAPTER`) |
| 多源收口史 | `analysis/data_sources_registry_retirement_20260707.md` |
| Registry | `backend/config/sync_registry.yaml` (`daily`, `stock_st`) |
| 派生 | `backend/services/pipeline/clean.py`, `build_price_kline_qfq_tushare.py` |
| 阶段标签 | `backend/services/pipeline/stage_status.py` |
| 契约 intent | `docs/MASTER_TOPLEVEL_DESIGN.md` §3.1, §6.1, §9, §12 |

## 8. Label

**FIXED**（2026-07-21）— S1–S4 transport strangler **运营路径 shipped**（CLI + default sync caller-only + swappable land acquire + TDD + moth）。

| 切片 | 状态 | 证据 |
|---|---|---|
| S1 | **FIXED** | `capture_and_land_*`；`--land-only`；红测不写 canonical |
| S2 | **FIXED** | `accept_*_from_landing`；`--accept-from-landing`；零 `_adapter`/auth |
| local-raw acquire→landing | **FIXED** | `--from-local-raw` + `materialize_security_day_landing_from_legacy_raw_rows` |
| thin land→accept | **FIXED** | `--land-then-accept` / `land_then_accept_authorized_security_day` |
| S3 sync caller-only | **FIXED** | default `_publish_*` → land→accept；`sync_runner` 无 `capture_and_publish_*` |
| S4 acquire swappable | **FIXED** | `security_day_acquire` modes `provider_tushare`/`local_legacy_raw_materialize`；land+default sync via resolve；accept 零 acquire；TDD `test_security_day_acquire_s4.py` |
| S5 derive | **FIXED** | `chunkyctl derive qfq|form --from-accepted`；`derive_runtime`；form/qfq canonical-only nominal；TDD `test_derive_runtime_s5.py`；零 acquire |
| S6 serve | **FIXED** | `market_pulse_serve_read` + DataAccess entities；router 零 `# serve-exempt:`；D5 OK |

Residual owner：S7 legacy `raw_tushare_*`；optional daily-only expand `<20220104`（ST floor）。
Live expand evidence（2026-07-21）：accepted daily+ST **`20220104`→`20260720`（1099d）**.
Next verification：S7 strangler knives；E/F same-protocol remeasure when scheduled（window unblocked）。

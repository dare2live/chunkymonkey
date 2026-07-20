# 数据地基模块化缺口诊断（2026-07-20）

> 状态：evidence-only / owner diagnosis  
> 范围：daily + stock_st formal path；对照 transport 契约 vs 已交付实现  
> 禁令遵守：不写 Optuna/StrategyRelease/cutover 翻转；不启动 pipeline rewrite

## 1. Verdict

**业主判断：substantially right（对已交付实现）；contract intent 已立法、尚未可操作落地。**

| 轴 | 结论 |
|---|---|
| **Contract intent** | `docs/MASTER_TOPLEVEL_DESIGN.md` §3.1 / §6.1 / §9 已写清：landing→validate→accepted canonical→serve；名义/因子/qfq 三分；Provider=可替换 adapter，业务真相在 accepted；「契约可换」显式标为**目标态** |
| **Shipped implementation** | daily/ST 的唯一活入口 `chunkyctl sync --domain daily|stock_st` 走 `capture_and_publish_*`：**fetch→land→accept 一条龙**；无 land-only / accept-from-landing / from-raw CLI；live adapter 硬编码 TuShare；landing 表名仍带 `tushare` |
| **业主原话对齐** | 「节点没分开」「数据源可换 / 获取·加工·计算互不影响 没实现」「该先做数据地基模块化」——对 **operable boundaries** 成立；对「库内完全没有 land/accept 函数」则过强（函数缝已在，入口未切开） |

标签：**PARTIAL landed library seams + BLOCKED operable modularity**（非「设计错了」，也非「已做完」）。

## 2. Coupling map（daily / ST）

### 2.1 胶合点（fetch 粘在 land/accept）

```text
chunkyctl sync --domain daily|stock_st
  → sync_runner.run_domain
  → _publish_security_day_accepted_partition   # docstring: "Fetch one trade_date and publish…"
       adapter = _adapter(spec["source"])     # 仅 TuShare 单例
       fetch_rows = adapter.fetch_raw(...)
       → capture_and_publish_authorized_*_partition
            → capture_security_day_provider_rows(fetch_rows=…)
            → publish_accepted_*_partition
                 → land_*_batch
                 → accept_*_batch
```

证据：

- `backend/services/data_sources/sync_runner.py` `_publish_security_day_accepted_partition`（~1856–1954）：唯一生产路径，内联 `_fetch_rows` 后调用 `capture_and_publish_*`。
- `nominal_ohlcv_runtime.py` / `stock_st_runtime.py`：`capture_and_publish_*` docstring =「fetch → land → accept one trade_date」。
- `moth coupling --impact capture_and_publish_authorized_nominal_ohlcv_partition`：fan-in = `sync_runner` + runtime 自身 + 测试；**无**独立 CLI/脚本消费 `publish_accepted_*`。
- `publish_accepted_nominal_ohlcv_partition` 生产 fan-in **仅** runtime 内部（被 capture_and_publish 调用）；测试可直接 land→accept，但运营入口不能。

### 2.2 已分开（库内 / 契约层）

| 缝 | 证据 | 备注 |
|---|---|---|
| landing 表 ≠ canonical 表 | `landing_tushare_daily` / `canonical_nominal_ohlcv_daily`；ST 同理 `landing_tushare_stock_st` / `canonical_stock_st_daily` | 物理分表已有；**命名仍绑定供应商** |
| land vs accept 函数 | `land_*_batch` / `accept_*_batch`；`security_day_partition.accept_security_day_batch` 从 landing 读回再 validate→canonical | 可单测；无运营节点 |
| capture shaping 模块 | `security_day_capture.py` 注释：「Kept separate from land→accept mechanics」 | 仅 helper，非入口 |
| formal inventory | `formal_boundaries.py` 为 daily/ST 分别登记 `landing_writer` / `canonical_writer` | 审计清单 ≠ 可调度阶段 |
| 禁止 legacy raw 写 | formal daily/ST `legacy_raw_write=forbidden`；runtime `refuse_legacy_*_raw_write` | 正确硬墙 |
| qfq = 派生 | `pipeline/clean.py` + `build_price_kline_qfq_tushare.py`：名义 preferred=canonical ∪ legacy raw fill；× `raw_tushare_adj_factor` | 已在 clean，非 accept 原子链内 |
| pipeline 阶段标签 | `acquire / clean / process / store`（`stage_status.STAGE_ORDER`） | **标签存在**；acquire 对 formal daily 仍等于一条龙 sync |
| ≤40d / 禁 mass backfill | `AUTHORIZED_SECURITY_DAY_MAX_WINDOW_DAYS = 40`；拒 `--backfill` | 硬约束已落地 |
| 名义 vs qfq | MASTER §6.1 + source_policy analysis_relation | 语义分轨成立 |

### 2.3 未分开 / 未实现

| 缺口 | 证据 |
|---|---|
| **无 from-landing / from-raw 再 accept 运营入口** | sync CLI 只有 domain+window；无 `--land-only` / `--accept-batch` / replay-from-landing |
| **无 accept-time 可插拔 source adapter** | `_adapter()` 注释：多源 registry 2026-07-07 物删；`require_live_adapter` 仅允许 `tushare`（`formal_boundaries.LIVE_ADAPTER`） |
| **landing 身份仍供应商品牌** | 表名 `landing_tushare_*`；换源会迫使改 schema 身份或再叠映射层 |
| **「获取/加工/计算互不影响」未成边界** | 换源必须改 acquire+registry+（可能）landing 名；qfq/form 仍偶合「日更一把跑」；不能独立「只重 accept」「只重派生」而不触 provider |
| **第二源路径** | `sources/` 仅 `tushare.py`；aif10 披露仍 NONCONFORMING（绕 transport） |

## 3. 先前需求是否落地失败？诚实对照

### 设计散文承诺（intent）

1. **MASTER §3.1**：每个外部域同一生命周期 `provider → landing → validate → accepted canonical → serve`；landing=供应商事实，canonical=项目接受事实。
2. **MASTER §6.1**：最小原子链 `stage → validate → canonical → accepted_partition`；watermark 从 AcceptedPartition 投影。
3. **MASTER §9**：「Provider 是可替换 adapter：业务真相在 accepted/canonical，不绑定单一供应商。……契约可换……是**目标态**，不是……现状声明。」
4. **goal.md 已裁决**：多源=契约可换 adapter（目标态）；积木=`module+data+config+contract+evidence`；landing 保留供应商响应；演进=strangler 非 greenfield。

### 代码实际交付（implementation）

- Phase A 交付了 **formal land/accept 机制 + accepted partitions + ≤40d 门 + legacy raw 墙**——这是地基**正确性**切片，不是地基**可组合性**切片。
- 运营与控制面仍把「同步」定义为 **capture_and_publish** 单动词；节点在文档/函数层切开，在 **CLI/调度/可替换 acquire** 层未切开。
- 2026-07-07 `analysis/data_sources_registry_retirement_20260707.md`：**主动删除**未使用的多源 registry/fallback——当时正确（0 消费方），副作用是「可换 adapter」连死框架都没了，只剩 TuShare 直连。这不是 silent 背叛契约，而是 **目标态被诚实降级为单源，且未另开 strangler 把「acquire 边界可换」做成最小活缝**。

**结论**：需求没有「写错」；**部分落地（表/函数/证据链）+ 关键部分未落地（可调度节点分离 + acquire 可换）**。业主感知「只能 TuShare 一条龙到 accepted」与生产入口证据一致。

## 4. 「数据地基做好」应指的程序（strangler，非 greenfield）

目标态一句话：**证据可换源获取；项目真相只在 accepted；派生与计算只读 accepted（或显式兼容 fill），互不改对方 writer。**

有序切片（只动 transport/派生边界；不动策略/Optuna/Release）：

| Slice | 做什么 | 退出条件（窄） | 明确不做 |
|---:|---|---|---|
| **S0** | 立法冻结本笔记 + goal 指针；盘点 daily/ST 入口与 fan-in | 本文入 git；goal 有 current-focus 指针 | 不改 runner |
| **S1 Acquire/Evidence** | 把「只 capture→landing（LANDED）」做成可调用入口（同 contract、同 ≤40d、同 eligibility）；landing 行保留 provider 原样 | CLI/API：`land` 成功且不写 canonical；坏例：空页/超窗/未来日红 | 不引入第二 DB；不做 plugin bus |
| **S2 Validate/Accept（publication）** | `accept` 只消费已 LANDED batch（from-landing）；失败不碰 provider | 可对已落地 batch 重 accept；duplicate/min_rows/hash 门保持 | 禁止 accept 时再 fetch |
| **S3 解耦运营动词** | sync 拆成显式阶段或保留 `sync=land+accept` 但允许 `--from-landing`；文档与 chunkyctl 一致 | 日更可「源挂了仍能对昨日 landing 重试 accept」 | 不 bulk backfill |
| **S4 Acquire 可换（边界内）** | 第二 adapter **只**实现 `fetch_raw`→同一 `SecurityDayLandingBatch` 投影；registry/source 字段复活为**最小**映射（非旧 fallback 框架） | 契约测试：假 adapter 能 land；canonical 字段/读契约不变 | 不改 Tier1–4 读面；landing 表逐步去供应商品牌（兼容视图可暂留） |
| **S5 Derived process** | qfq/form 只挂 accepted（+ 书面授权的 legacy fill 日落条款）；clean 可独立于 acquire 重跑 | 无新 accepted 时重跑 qfq 不触 TuShare | 不把 qfq 塞进 accept 事务 |
| **S6 Compute/serve** | Tier1/2/研究读面只经 resolver / accepted；process 失败不回写 landing | 既有 dual-track residual=NONE 保持 | 不开策略寻优 |

程序原则：每片坏例先红→最小绿→窄回归；`live_readiness` 不因代码缝切开而升级。

## 5. Hard constraints（不可破）

- **PIT / availability**：`available_at`、typed `same_day_at`、manual vs automatic trigger 语义不变。
- **Landing purity**：landing 前不按 universe 丢行；供应商响应当证据。
- **≤40 trading days** 授权窗；无 owner 书面授权禁止 mass backfill。
- **无第二 DB / 无 plugin bus / 无万能 DAG**（MASTER §12）。
- **Strangler**：旧入口可暂留为 `land+accept` 别名；禁止 greenfield 重写 transport。
- **单一 writer / 一数据集一 contract snapshot**；formal 域禁止回流 legacy `_write_batch`。
- **不翻转** cutover yaml；本程序不含 Optuna / StrategyRelease / E 松门。

## 6. 与当前 A→H / forward program 的关系

- 本缺口是 **Tier0 transport 可组合性债**，与「frontier current / F0–F3 reject / P0 自然 sync」正交。
- 不阻塞「收盘后 sync 一日」的币值动作；但阻塞「换源 / 只重放落地证据 / 获取与派生解耦」类需求——业主把后者升为地基优先时，应插入 forward program 的 **P0 旁路地基切片（S0–S2）**，仍先于 D1/G/H。
- 披露域 E0（aif10 NONCONFORMING）是平行债：同一 transport 立法，勿 silent merge。

## 7. Evidence index（路径）

| 项 | 路径 |
|---|---|
| Transport vs tiers | `docs/MASTER_TOPLEVEL_DESIGN.md` §3, §6.1, §9, §12 |
| Sync glue | `backend/services/data_sources/sync_runner.py` |
| Capture+publish | `nominal_ohlcv_runtime.py`, `stock_st_runtime.py` |
| Land/accept mechanics | `security_day_partition.py`, `security_day_capture.py`, `*_acceptance.py` |
| Formal walls | `formal_boundaries.py` |
| Registry 单源收口 | `analysis/data_sources_registry_retirement_20260707.md` |
| Registry YAML | `backend/config/sync_registry.yaml` (`daily`, `stock_st`) |
| qfq derived | `backend/services/pipeline/clean.py`, `backend/scripts/build_price_kline_qfq_tushare.py` |
| Pipeline stages | `backend/services/pipeline/stage_status.py` |

## 8. Label

**PARTIAL** — 正确性向 formal accept 已落地；模块化（可调度节点 + acquire 可换）未落地。  
Residual owner：Tier0 transport strangler（S1–S4）。  
Next verification：S1 最小 CLI/测试 —— land-only 不写 canonical；accept-from-landing 不调 `_adapter`。

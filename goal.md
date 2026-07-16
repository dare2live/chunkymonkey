# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-16
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

停止继续对旧架构做局部补洞，按 `docs/MASTER_TOPLEVEL_DESIGN.md` 建立可组合、可替换、可审计的数据与策略系统。

近期只做两件事：

1. Phase 0：收口文档、AGENTS、skills、Moth/CodeGraph、真实 CLI 和文档门；
2. Phase 1：用现有未提交的数据完整性修复作为第一个 Tier0 迁移切片，建立 `IngestBatch/AcceptedPartition`、landing/canonical 边界和原子验收。

Tier0 未闭合前，不启动大规模公式寻优、付费计算、生产候选或自动跑批。

## 产品层级（已裁决）

| 层 | 目的 | 首个正式输出 |
|---|---|---|
| Tier 0A 市场数据 | 正确获取日历、身份、名义 K 线、公司行动、复权与供应商事实 | accepted canonical partition |
| Tier 0B 分类 | 版本化行业树、概念标签、成员快照和证据化 crosswalk | taxonomy node + membership |
| Tier 1 股票状态 | 描述当前阶段、形态和事件，不预测未来 | stock state + pattern event |
| Tier 2 市场感知 | 描述活跃度、不平衡代理、广度和价格响应 | market context snapshot |
| Tier 3 研究/策略 | 裸 K → 状态 → 市场 → 机构/公式逐层消融 | experiment verdict + strategy spec |
| Tier 4 决策/产品 | 只消费已发布策略，生成候选和纸面成交证据 | strategy release + decision batch |

依赖只能向下。Ops/Governance 观察全部层级，但不拥有业务事实。

## 架构硬决定

1. “积木”是 `module + data + config + contract + evidence`，不是目录加 YAML。
2. landing 保留供应商原始响应；universe/business filter 不得发生在 landing 前。
3. 名义 OHLCV 是成交真相；qfq 是带方法/as-of/lineage 的派生分析视图。
4. 分类统一契约、不统一值域：申万、东财行业、东财概念保持 namespace。
5. “资金去哪”只表达活动度、方向性成交不平衡代理、参与广度和价格响应，不宣称资金守恒流转。
6. 股票状态与未来标签分离；主升浪 ground truth 永不进入 Tier 1 输入。
7. 一数据集一 writer；只持久化跨模块、昂贵、需审计或正式发布的输出。
8. 配置只保存稳定政策；运行状态、分类成员、未来模块和 code topology 不进 active YAML。
9. 采用 strangler 迁移：契约先行、旧新并跑、对账、切消费者、最后删除旧路径。
10. 数据更新保持 `manual_only`，不恢复 cron/launchd/隐藏后台触发。

## Live 证据与 P0 缺陷

现有资产可保留：交易日历和数据源适配、K 线、技术状态、市场脉搏、机构画像、主升浪 ground truth、BestChoice 公式 challenger。

已确认的 P0/P1 架构缺口：

- `sync_runner` 在写 provider 表前执行 universe filter，landing 契约不纯；
- target data 与 watermark/failure outcome 分库写，缺少同一 accepted-partition 原子证据；
- qfq 每日以最新因子全史重算，serving view 的 factor/batch/ingested_at 是占位，不能兼任名义成交和血缘真相；
- 旧 `v_dc_industry_pit` 只有 first-seen、没有 `out_date/content_type`；writer 已退役，live DB 残留 view 待 Phase 1 只读核验后清理；
- 东财行业与申万按名称对齐，实测存在成员和名称差异，不能称同一桶；
- market pulse 的 namespace/content_type/grain 已修并于 2026-07-16 原子重建；仍缺 `available_at/method/config_hash/coverage`，只能用于展示，不能直接做历史特征；
- stock state 和 market regime 输出缺 definition/config/input snapshot 版本证据；
- 研究层没有统一 snapshot/experiment/release，paper surface 不是正式执行模型。
- 旧 storage-retention/legacy-flow 门存在空库存或读取失败仍放行的假绿，已退役；正式 dataset lifecycle/retention contract 尚待 Phase 1 建立；
- 当前 holdout helper 只守训练边界，不具备原子 prereg、全局 single-touch、参数冻结或并发证据。

## 当前工作树冻结面

以下 8 个 tracked 文件与 2 个 untracked WIP 是进入本轮前已有的独立 Tier0 dirty slice，
Phase 0 不得覆盖、还原或混合提交：

```text
backend/config/sync_registry.yaml
backend/scripts/check_continuity_integrity.py
backend/scripts/update_watermark_sla.py
backend/services/data_sources/sync_runner.py
backend/tests/scripts/test_check_continuity_integrity.py
backend/tests/scripts/test_update_watermark_sla.py
backend/tests/services/test_sync_runner_20260612_fixes.py
backend/tests/services/test_sync_runner_integrity.py
```

两个 untracked WIP basename 为 `batch_integrity.py` 与 `test_batch_integrity.py`；精确位置以 live
`git status` 为准，它们刻意不进入 Phase 0 index。

现状：`main...origin/main [ahead 4]`；不 push。

## 执行计划

### Phase 0 — 控制面收口（完成；本提交固化）

- [x] 现场代码、DB、Moth、CodeGraph 与三路对抗架构审计；
- [x] 重写顶层架构、研究验证和工程治理 owner；
- [x] 压缩 `AGENTS.md`、`PROJECT_INDEX.md`、docs map；
- [x] 合并删除旧 constitution/data/quickstart/archive 和已吸收设计稿；
- [x] 修退役 chunkyctl 命令、文档 gate、Rule 10 和 lineage 同名跨库假绿；
- [x] 退役 storage-retention/legacy-flow 假绿，holdout 降为诚实的边界 helper，修 qfq shadow-truth、taxonomy namespace 与 paper/institution 过度声称；
- [x] BestChoice 收缩为 hash/shape 可校验的冻结 challenger，删除第二 control plane、旧 App/runners 和伪发布证据；
- [x] 同步 `.moth/profile.yaml`、assertions、项目专属 skills 和本地 hook；
- [x] focused/full tests、Moth、CodeGraph、doc gates、diff check；
- [x] Rule 10 双独立终审 `APPROVE_WITH_NOTES`；与冻结 Tier0 patch 分离进入本提交。

Phase 0 退出条件：

- 活的人类 owner 只剩 `AGENTS.md`、`goal.md` 和 docs 三份契约；
- 文档 gate `fails=0 warns=0`，本地 link/CLI lifecycle 无漂移；
- FEATURE_MAP 不把 retired 命令列为 active；
- Moth 不再用包含 WARN 的 PASS 字符串制造 29/0/0；
- skills/Moth/AGENTS 指向同一架构和真实命令；
- 除冻结 Tier0 slice 外无未知 dirty residue。

### Phase 1 — Tier0 accepted partition

1. 为当前 K 线或一个小型交易数据域定义 typed `DatasetContract`；
2. 分离 landing 与 canonical filter；
3. 建立 `IngestBatch` 与 `AcceptedPartition`；
4. 做 fetch/validate/publish/accept kill-point 测试；
5. 从 accepted state 投影 watermark/SLA/failure；
6. 旧新路径 shadow-run 与逐 partition 对账；
7. 通过后再推广到其他域。

### 后续

Tier0 K 线/分类 → 版本化 stock state → market context → 主升浪 B0/B1/B2 → 机构跟随和公式逐个 feature package → strategy release/decision/paper/product。

## 当前 blocker / 禁止误报

- Phase 0 控制面已闭合；下一执行面是冻结 Tier0 slice 的独立复审与 Phase 1 accepted-partition 迁移；
- 2026-07-16 手工链已验证核心行情到日，但 `margin_detail` 7 月 10—15 供应商仅返沪市半批；冻结 Tier0 patch 仍待整体复审和独立提交；
- continuity 未按域使用 `available_after`、all-due 对 `unsupported` 域不计 bad，均属冻结 Tier0 verifier 缺口；
- 当前没有发布策略、正式候选或可信当前 KPI；
- `doctor --fast` 的 data health 绿不等于架构闭合；alert flag 和 failure queue 仍需按真实数据验证；
- 任何 `retired` 子命令 exit 0、WARN 被 Moth 当 PASS、fixture 与真实 schema 不同，都视为 verifier defect，不是可接受绿灯。

# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-18
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

停止继续对旧架构做局部补洞，按 `docs/MASTER_TOPLEVEL_DESIGN.md` 建立可组合、可替换、可审计的数据与策略系统。

当前继续 Phase 1 的 `margin` Tier0 tracer。两日 canary 已闭合；全历史读取前置门也已把
accepted-state/reconcile 收敛为固定 6 条主库只读查询和一次分区级权威证明。下一既定
rollout 是受控历史迁移：先 grill 成本、边界和 checkpoint，再由 manual-only 公共入口分批
回放并逐分区验收。全史 parity 前不切业务消费者，不扩到第二域；Phase 0 证据在 ledger 和
git history。

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

## Live 证据与剩余架构缺口

2026-07-17—18 `margin` canary 的当前事实：

- contract v2 把 publication eligibility 定义为
  `trading_day / next_trading_session_at / 09:00`，并纳入 config/contract hash；coverage 为
  `20260715—20260716`，两日各 3 个 provider fragment、3 行 canonical、BSE/SSE/SZSE 齐全，
  当前 `AcceptedPartition` 正好 2 条；
- 两日 content hash 为 `ab6703…0a5`、`f47e04…d76`，逐分区 reconcile 均 `PARITY`；
- accepted frontier/watermark 均为 `20260716 / 3`，parser=`margin_accepted_contract_2`，
  open margin failure=0、fallback=false；幂等重跑 gap/refill/rows 均为 0；
- `20260718` 周六实盘证伪裸 `t+1` 无法表达日历轴：供应商 `20260717` 当时仅 SSE=1、
  SZSE/BSE=0；两次 v1 批均拒绝且未写 accepted/legacy/watermark。正式 margin 改用 typed
  availability 后已通过受支持入口重发两日 v2 批，旧 v1/rejected 批只保留为不可变历史、当前
  pointer 均为 0；普通 sync 与 drain 共用同一 eligibility resolver，均只认 expected 到
  `20260716`、零 provider call，failure queue 已闭合；
- provider 的 `20260716/BSE/rqmcl=NULL` 按 nullable 契约原样保留，禁止补 0；
- 同一次执行只从同一 registry snapshot 派生一个 immutable contract 对象，并沿 runner、
  acceptance/recovery、accepted state、reconcile/projection、pipeline、continuity/SLA 全链透传；
  publication 重证由低层 validation owner 统一实现，read model 不再反向依赖 write orchestrator；
- formal transport 的 batch mode、date parameter、write mode 和 split groups，以及 drain/on-demand/
  full-refresh 的请求形状，都在 calendar、writer lock、provider adapter 和目标 DB 之前 fail closed；
- 受管 provider runtime 完整 backend suite 为 `1080 passed / 8 deselected`；
- 正式表只有 2 日/6 行；legacy margin 有 1827 日/4485 行，业务消费者仍只读 legacy，
  因此 canary 通过不等于历史迁移或 Tier0 全局闭合。
- accepted-state、readiness 与 reconcile 现共用一个 immutable set-based evidence snapshot：
  schema inventory 加 accepted/batch/landing/canonical/legacy 共固定 6 条主库查询，N=1 与
  N=20 均为 `(6, 6)`；交易日历只预处理一次，逐分区 cutoff 用二分。公开 reconcile、accepted
  state 和 readiness 不再接收 snapshot/proof/state 注入，scope 夹带、schema drift、landing
  lineage、premature publication、跨连接/跨代证据和单分区故障污染均有 fail-closed 反例门；
  live 两分区仍 `PARITY`；完整 suite 为 `1107 passed / 8 deselected`。

仍存在的架构缺口：

- 只有 margin tracer 具备纯 landing、accepted partition 和原子 Ops 投影；其他 legacy sync 域尚未迁移；
- 其余 16 个 legacy `available_after=t+1` 域混有 trading-day、announcement、period 和
  by-security 语义；保持旧行为但不得再把 transport/batch mode 当 availability 轴，须逐域迁移到 typed policy；
- qfq 每日以最新因子全史重算，serving view 的 factor/batch/ingested_at 是占位，不能兼任名义成交和血缘真相；
- 东财行业与申万按名称对齐，实测存在成员和名称差异，不能称同一桶；
- market pulse 仍读 legacy margin，canary 后未纳入本 rollout 重建；其 `20260716` 物化行的
  `rzrqye/rzrqye_chg` 仍为 NULL，且缺 `available_at/method/config_hash/coverage`，不能作为
  本次 Tier0 验收或历史特征证据；
- stock state 和 market regime 输出缺 definition/config/input snapshot 版本证据；
- 研究层没有统一 snapshot/experiment/release，paper surface 不是正式执行模型。
- 当前 holdout helper 只守训练边界，不具备原子 prereg、全局 single-touch、参数冻结或并发证据。

## 执行计划

### Phase 0 — 控制面收口

已完成；完成项不在 goal 重复维护，以 ledger、owner docs 和 git history 为证。

### Phase 1 — Tier0 accepted partition

- [x] 为 margin 定义 typed `DatasetContract`、不可变 schema/hash 和唯一 writer；
- [x] 建立 provider landing、validate/publish/accept 与 recovery 原子边界；
- [x] 从 accepted state 投影 watermark/SLA/failure，并让 Tier0 失败阻断下游；
- [x] 建立 manual-only `chunkyctl sync` 公共入口，保留授权/日历/writer lock；
- [x] 完成 20260715—16 live canary、逐分区 parity、no-refetch 和 acquire gate；
- [x] 消除全历史前约 `6 + 13N` 的读取路径，建立固定 6-query 与 calendar operation-count 门；
- [x] 闭合 post-fix、最终 Rule 10、Moth/CodeGraph、owner docs 和本地安全提交。

### Phase 1.1 — margin 受控历史迁移

- [ ] 执行前 grill：确认目标消费者、历史边界、provider 成本、可复用 checkpoint、停止与回滚条件；
- [ ] 只用 `chunkyctl sync --domain margin --backfill` 分批回放，不安装自动任务；
- [ ] 每批核对 accepted batch/config hash、正行数、期望交易日和逐分区 parity；
- [ ] 全史闭合后才单独规划 consumer shadow/cutover；第二数据域和 legacy 删除仍不混入本 rollout。

### 后续

Tier0 K 线/分类 → 版本化 stock state → market context → 主升浪 B0/B1/B2 → 机构跟随和公式逐个 feature package → strategy release/decision/paper/product。

## 当前 blocker / 禁止误报

- margin canary 没有未闭合数据缺口，但 2 日 cross-section 只能 `skipped_insufficient_history`，不能声称历史稳定；
- 正式 margin 历史仍未迁移；固定查询门只解除执行前置阻塞，不等于允许无 checkpoint 一次性拉取
  1827 日，也不等于 consumer cutover；
- 第二正式数据域前必须先抽出 runner 拥有的 outcome-to-loop policy；禁止在现有超过 2000 行的 `sync_runner.py` 继续复制 dataset-specific 分支，也不借机发明通用插件/DAG；
- legacy `source_watermarks` standalone helper 的 failure-queue DROP/recreate 仍缺 outer transaction；margin 已有外层事务，不受此 P1 影响；
- 全局 SLA 仍有 `lhb_daily`、`qfii_holding_quarterly` 无 mapping 及 `sync:margin_detail` stale 三个告警；strict continuity 另判 `cyq_perf` 超 SLA，而 SLA 报告却标 `OK`，该 verifier 口径分歧阻断全局 READY；不能用 margin 单域通过洗绿全链，也不能反向抹掉该单域证据；
- 当前没有发布策略、正式候选或可信当前 KPI；
- 正式 margin 历史尚未迁移，任何 business consumer cutover、raw+canonical 混读或 legacy 删除均禁止；
- legacy market-pulse 当前物化未随 canary 刷新；其重建会写 Tier1/Tier2 共享输出，归消费者
  rollout 串行验收，不得为美化本次 Tier0 报告越界执行；
- 任何 `retired` 子命令 exit 0、WARN 被 Moth 当 PASS、fixture 与真实 schema 不同，都视为 verifier defect，不是可接受绿灯。

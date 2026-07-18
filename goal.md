# ChunkyMonkey Goal

> 状态：live controller board
> 更新：2026-07-18
> 只保存当前 objective、已裁决事项、blocker 和下一步。完成证据追加到 `analysis/project_state_ledger.md`。

## 当前 objective

停止继续对旧架构做局部补洞，按 `docs/MASTER_TOPLEVEL_DESIGN.md` 建立可组合、可替换、可审计的数据与策略系统。

当前继续 Phase 1 的 `margin` Tier0 tracer。两日 canary 已闭合；全历史读取前置门也已把
accepted-state/reconcile 收敛为固定 6 条主库只读查询和一次分区级权威证明。Phase 1.1
Grill 已裁决全史目标为 `20190102—live eligible frontier`，但也证伪现有 backfill 会重拉已
accepted 分区、先覆盖 legacy 再自证 parity、首错后继续烧请求且没有可复用结果契约。下一
既定 rollout 因此不是直接拉数，而是先补窄 `margin_history` 执行门；门通过后才由
manual-only 公共入口分批回放并逐分区验收。全史 parity 前不提升 v3 coverage、不切业务
消费者、不扩到第二域；Phase 0 证据在 ledger 和 git history。

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

2026-07-17—18 `margin` canary 的详细证据已固化在 ledger；当前控制面只保留决策所需事实：

- v2 availability 为 `trading_day / next_trading_session_at / 09:00`；`20260715—16` 两日
  accepted/canonical 各 3 行、逐分区 `PARITY`，frontier/watermark=`20260716 / 3`；
- 周六反例已证伪裸 `t+1`；旧 v1/rejected 批不改写，nullable `rqmcl=NULL` 不补 0；
- 单次 registry snapshot 派生一个 immutable contract；transport/request shape 在任何写锁、
  provider adapter 或目标 DB 前 fail closed；
- accepted-state/readiness/reconcile 共用固定 6-query evidence snapshot 和一次日历预处理；
- 正式表仍仅 2 日/6 行，legacy 为 1827 日/4485 行且消费者未切，故 Tier0 全史未闭合。

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

- [x] 执行前 grill：目标为正式 canonical 历史预迁移，legacy consumer 暂不切；边界为
  `20190102—eligible frontier`、当前精确 1827 个交易日/4485 个 provider fragment；accepted
  pointer + `PARITY` 是可复用 checkpoint，LANDED 是零 refetch 恢复点；首错停、legacy 冲突
  零覆盖；
- [x] 在 provider 历史调用前补 `margin_history` typed request/plan/result：显式
  start/end/max-dates、oldest-first cap、accepted+PARITY skip、compare-before-write、首错停和
  稳定 evidence hash；
- [ ] 只用 `chunkyctl sync --domain margin --backfill --start ... --end ... --max-dates ...`
  分批回放，不安装自动任务；
- [ ] 每批核对 accepted batch/config hash、正行数、期望交易日和逐分区 parity；
- [ ] 全史闭合后才把 v2 历史证据按精确 predecessor proof 原子提升为 v3 full-coverage，再单独
  规划 consumer shadow/cutover；第二数据域和 legacy 删除仍不混入本 rollout。

### 后续

Tier0 K 线/分类 → 版本化 stock state → market context → 主升浪 B0/B1/B2 → 机构跟随和公式逐个 feature package → strategy release/decision/paper/product。

## 当前 blocker / 禁止误报

- margin canary 没有未闭合数据缺口，但 2 日 cross-section 只能 `skipped_insufficient_history`，不能声称历史稳定；
- 正式 margin 历史仍未迁移；固定查询门只解除执行前置阻塞，不等于允许无 checkpoint 一次性拉取
  1827 日，也不等于 consumer cutover；
- 当前 provider 授权有效期为 `2026-06-17 10:48:58+08:00—2026-08-12 15:43:00+08:00`，但
  服务端剩余额度未知、磁盘仅约 9.8 GiB；runner 门和单日 canary 未通过前仍为历史执行 NO-GO；
- `coverage_start=20260715` 是 v2 当前服务义务，不是历史准入下限。预迁移期间冻结 v2 hash；
  不得中途改 coverage 洗绿或使 checkpoint 漂移。全史闭合后才能以兼容 payload 证明和原子
  generation head 提升 v3，旧 v2 batch/pointer/canonical 不改写、不重标；
- 第二正式数据域前必须先抽出 runner 拥有的 outcome-to-loop policy；禁止在现有超过 2000 行的 `sync_runner.py` 继续复制 dataset-specific 分支，也不借机发明通用插件/DAG；
- legacy `source_watermarks` standalone helper 的 failure-queue DROP/recreate 仍缺 outer transaction；margin 已有外层事务，不受此 P1 影响；
- 全局 SLA 仍有 `lhb_daily`、`qfii_holding_quarterly` 无 mapping 及 `sync:margin_detail` stale 三个告警；strict continuity 另判 `cyq_perf` 超 SLA，而 SLA 报告却标 `OK`，该 verifier 口径分歧阻断全局 READY；不能用 margin 单域通过洗绿全链，也不能反向抹掉该单域证据；
- 当前没有发布策略、正式候选或可信当前 KPI；
- 正式 margin 历史尚未迁移，任何 business consumer cutover、raw+canonical 混读或 legacy 删除均禁止；
- legacy market-pulse 当前物化未随 canary 刷新；其重建会写 Tier1/Tier2 共享输出，归消费者
  rollout 串行验收，不得为美化本次 Tier0 报告越界执行；
- 任何 `retired` 子命令 exit 0、WARN 被 Moth 当 PASS、fixture 与真实 schema 不同，都视为 verifier defect，不是可接受绿灯。

# 工作台增量编排 + 进度 UX 计划（2026-07-22）

> Status: evidence-only plan（P0 已落 1 刀：通知合并）
> Authority chain: `AGENTS.md` → `goal.md` → `docs/README.md` owners →
> `analysis/architecture_fix_treadmill_first_principles_20260722.md`（三时钟控制面）→ 本文件
> Skills applied: `$mio`（真金白银 / 消费者锚定 / 别把 ops 残差翻成代码刀 / unknown≠stale）·
> `$thinking-occams-razor` · `$architect-controller`（立法→控制→执行，触发式不预建 DAG）
> 关系：本文件是 treadmill 控制面 §C1（编排）+ §C4（产品齿轮）在「工作台一键更新」这条具体链上的续作，
> **不重开**地基闭合裁决（`foundation_done.yaml` + FND-GATE 仍是唯一 acceptor）。

---

## 0. Owner 三问的白话回答（executive）

1. **等时钟/软观测（`soft_waiting_clock`）是啥？** 一次日更「跑完了、没有硬故障，但有几项在**等发布时钟**或**只能观测不能行动**」的 typed 收尾态。它既不是 `success`（全绿），也不是 `hard_fail`（真挂了要马上修），是第三态。它的存在是为了**不让「时钟没到」被冒充成「坏了」**（旧 exit-code 把两者压成同一个非零 = load-bearing lie）。
2. **为什么空增量还在加工？** 一半是**设计正确**（market_pulse 每次回补最近 N 个交易日的 t+1 迟到列——两融/龙虎榜/涨跌停常常昨天写行、今天才补齐；这正是 owner 说的「derive lag 不许跳」），一半是**真有优化空间**（DC 行业当前快照 `build_dc_industry_view` 每次全量重建，即便东财源前沿没前进）。segments / 形态两步**已经**在「无缺日」时秒退。所以「一刀切跳过 process」**不安全**（会让迟到列永远 stale），正确解是**delta 感知的选择性加工**（下方 P1）。
3. **通知怎么处理？** 根因是**每跑一次软收尾就弹一次 macOS 横幅**，owner 空点几次「一键更新」就叠几条。已修（P0）：**同一软签名只弹一次**，空点重跑自动合并不再刷屏；软态变化（新增降级项）或恢复成功后再变软，才会重新弹一次。
4. **下一步产品怎么走？** 把「推进」从「又清一个 residual」换轴到「**用已 ship 面 + 让一键更新说人话的增量真相**」：P0 通知真话（已落）→ P1 空增量不做无谓重算（delta manifest）→ P2 更丰富的进度 UX（每域改了啥/从哪拉/瀑布日志/进度条）→ P3 状态变更传感器（ST 戴帽摘帽 / 股东比例变化）。

---

## 1. 软观测（`soft_waiting_clock`）权威解释

### 1.1 三态词表（真相源 = `data/reports/daily_*.json.run_outcome`）

| outcome | 含义 | exit | UI | macOS 通知 |
|---|---|---|---|---|
| `success` | 目标分区已 accepted，无降级 | 0 | 绿 | 静默 |
| `soft_waiting_clock` | 跑完，无硬故障；有「等时钟」或「只能观测」的降级项 | 1 | 琥珀「等时钟 / 软观测（非 FAIL）」 | **至多 1 条**观测横幅（本刀后按签名合并） |
| `hard_fail` | AUTH / PREFLIGHT / TIER0 / WRITER 硬阻断，现在可行动 | 2–5 | 红「硬失败」 | 1 条 `job FAIL`（wrapper 拥有） |

派生单一计算点：`backend/services/pipeline/run_outcome.py::derive_run_outcome`。
分类规则：`AUTH|PREFLIGHT|TIER0|WRITER BLOCK` → hard；`pending_publish / pre_available_after_zero_rows / same_day_vendor_vacuum / still_failed=[…] / drain 有残余缺口` → soft-named；其余非硬降级（continuity / SLA）→ 归入 soft 桶（reason=`ops_observe_non_hard_degraded`），**故意**不让诚实的 ops 降级被画成红 FAIL。

### 1.2 与「SLA stale=miaoxiang_fact, aif10_lhb, aif10_qfii, tushare」的关系

截图里那 3 项 + tushare 就是 `soft_waiting_clock` 的**软观测明细来源之一**（`sla_warn=true` → 观测横幅带 `SLA stale=…` 摘要）。逐条查 `data/audit/watermark_sla_20260722.json` 后的真相（**没有一项是「今天 K 线没进库」**）：

| 源 | data_domain | status | 真相 | 该不该弹 FAIL |
|---|---|---|---|---|
| `miaoxiang_fact` | holders_top10_float_legacy_observer | `NO_QUERY_MAPPING` | strangler **观察者**（`source_watermarks.py` 明写「不作发布真相，只做 dual-path 诊断」），SLA 检查器没给它 probe query → 当「无法核实」当成 alert | 否（unknown≠stale） |
| `aif10_lhb` | lhb_daily | `NO_QUERY_MAPPING` | LHB **2026-06-29 已退役**（切 tushare top_list/top_inst）。`DOMAIN_SPECS` 已删该域，但 `mart_data_source_watermark` 里**残留一条墓碑行**，watermark 冻在 2026-06-26，每跑必 alert | 否（墓碑，应清） |
| `aif10_qfii` | qfii_holding_quarterly | `NO_QUERY_MAPPING` | QFII **季频**（SLA 100d）；watermark=Q1 报告期 2026-03-31，113d，等 Q2（8/31 前披露）——真·等时钟。且无 probe query，走 no_mapping | 否（季频 clock-wait） |
| `tushare` | sync:margin | `DATA_STALE_VS_SLA` | **margin 产品冻结 / on_demand**，watermark 2026-07-16（6d>2d）。冻结域诚实 stale，非 actionable | 否（冻结域诚实） |

**结论**：4 项 SLA alert 全是「等时钟 / 冻结 / 退役墓碑 / 无法核实」，**没有一项是 hard**。所以 `soft_waiting_clock` 的裁决是**对的**；问题在**通知重复**（P0 已修）与**SLA 语义把 unknown 当 stale**（P0/P1 计划，见 §4）。

### 1.3 为什么会「响 3 次 / 反复响」

- **单次跑内三通道**（历史刷屏根因，`daily_update_notification_spam_triage_20260722.md` 已治）：store degraded 摘要 + dispatcher + wrapper FAIL，三条 → 已收敛成 outcome-keyed 单渲染。
- **跨次跑重复响（本刀新修）**：`store._outcome_summary_banner` 每次软收尾都 `osascript display notification` 一次。owner 在 drain/加工期间**空点几次「一键更新」**，每次跑完都弹一条**内容一样**的「soft_waiting_clock · 3 项」——截图 17:35/17:48/20:48 就是三次独立跑。这不是脚本坏了，是我们主动弹了三次一样的横幅。

---

## 2. Deliverable A — 通知刷屏修复（P0，已落 code）

### 根因
`_outcome_summary_banner` 无条件对每次 `soft_waiting_clock` 弹一条 macOS 横幅；空增量 re-click 产生**内容完全相同**的软态，却各弹一条。

### 修复（`backend/services/pipeline/store.py`）
- 新增 `_soft_banner_signature(output)`：对（date + outcome + reason + 排序后的 `run_outcome_classified` 原始 msg + SLA stale 源 + sla_warn）做 **timestamp-free** 指纹。
  - 关键：用 `run_outcome_classified` 的**原始降级文案**（无每跑时间戳前缀），不用 `DEGRADED_FLAG` 行（那些带 `[HH:MM:SS]` 会让签名每跑都变）→ 保证同内容同签名。
- 新增 per-day marker `chunkymonkey_soft_banner_<date>.marker`（落在 `DEGRADED_FLAG` 同目录，测试隔离一致）。
- 逻辑：软态 → 若 marker 内容 == 本次签名 → **合并**（写日志「与上次软观测一致，不重复弹窗」，不弹），否则弹 1 条 + 写签名。
- `success` → 清 marker（恢复正常后，下次转软再提醒一次）；`hard_fail` → 不动（wrapper 拥有 FAIL 横幅）。

### 效果
- 空点重跑（同软签名）：**首次弹 1 条，之后静默**。
- 软态真变化（新增/减少降级项、SLA 源变化）：**重新弹 1 条**。
- 软→成功→软：成功清 marker，转软再弹 1 条。

### 测试
`backend/tests/services/test_run_outcome.py`
- `test_soft_banner_coalesces_identical_reclick`：两次同签名 → 只 1 条 osascript。
- `test_soft_banner_renotifies_after_change`：签名变 → 2 条；成功清 marker；再软 → 3 条。
- 回归：`test_run_outcome.py` + `test_notification_dispatcher_skip_macos.py` + `test_manual_job_wrapper.py` = **19 passed**。

### 未在本刀（转 §4 计划，避免 mio 改前无审计的 DB 突变 / 反 residual-whack）
- **清 `aif10_lhb` 墓碑 watermark 行**（DuckDB 写；需 pre-knife + single-writer 序列化）。
- **给 `qfii` / `miaoxiang_fact` 真 probe 或 typed no_probe**（让 SLA 停止把 unknown 当 alert；`update_watermark_sla.py::_probe_gate` 的 `no_mapping→alert=True` 对**已知退役/观察者/季频**域是误报）。

---

## 3. Deliverable B — 空增量为何仍加工（根因 + 裁决）

### 3.1 当前链（`services/pipeline/run.py`）
`preflight → acquire → clean → process → store`，**固定顺序、无短路**。acquire 找不到新增量时，clean/process/store 照跑。

### 3.2 process 四步逐一体检（真相源 = 各 `build_latest`）

| 步 | 空增量时行为 | 成本 | 判定 |
|---|---|---|---|
| `build_dc_industry_view.py` | **每次全量重建** DC 行业/概念当前快照（CREATE __next → validate → swap），即使 `raw_tushare_dc_index/member` 前沿未前进 | 中（全市场当前快照 + 校验） | **真冗余**：源前沿没动时结果逐 bit 相同 → 可加 frontier guard |
| `segments.build_latest` | `missing` 缺日为空 → `return {"added_days":0}` | 极低（一条 NOT IN 查询） | **已良好**（幂等秒退） |
| `market_pulse.build_latest` | 无缺日仍**回补最近 `lookback_late_days` 个源日**（DELETE+重插） | 中高（滚窗全史重算这 N 日） | **设计正确**：t+1 迟到列（margin/龙虎榜/limit）昨天空、今天补——**owner 明说「derive lag 不许跳」** |
| `technical_states.build_latest` | watermark 无缺日 → `return {"added_days":0}` | 极低 | **已良好**（幂等秒退） |

### 3.3 Occam 裁决

- **process-always-runs 是「半设计半冗余」，不是纯 bug。** 两步已秒退；一步（pulse 迟到回补）是**correctness 机制不能跳**；一步（dc_view 全量重建）是**可优化的真冗余**。
- **一刀切「acquire 空 → 跳 clean/process/store」= 不安全**，因为它会杀掉 pulse 的 t+1 迟到列自愈（正是 owner 点名的「ST/holder 状态变化 / derive lag 不许跳」这类：即便没有新 trade_date，**旧日的行仍可能有迟到数据要补**）。这违反 mio 真金白银 / insurance accuracy。
- **本轮不实现一刀切短路**（低风险门未过）。改为：**delta manifest 驱动的选择性加工**（P1），把「跳过」限定在**可证明无变化**的步，且**永不**跳过 pulse 迟到窗与状态变更消费者。
- 兼容 treadmill 护栏：ops 残差默认非刀；但这条是 **owner 显式产品意图 + 真 correctness/成本优化**，属 §C1 编排续作的正当刀（named consumer = 一键更新 UX + 迟到列正确性），不是「清 PARTIAL」。

### 3.4 P1 安全短路（delta manifest）设计 + kill criteria

**目标**：acquire 产出一个 typed **delta manifest**（本轮哪些 accepted 分区前进了 / 哪些 late-window 源日内容变了 / 哪些状态域有变更），process 消费它，只重算真受影响的派生 + **恒定**重算 pulse 迟到窗。

- acquire 收尾写 `ctx.delta`：`{advanced_partitions:[…], late_window_source_changed:bool, state_changes:{stock_st:…, holders:…}}`（来源已有：formal on_demand outcomes、drain per-domain evidence、org gap report）。
- process：
  - `build_dc_industry_view`：DC 源前沿未前进 → **skip**（typed log），否则重建。
  - `segments` / `form`：维持 build_latest 自带的「无缺日秒退」（已安全）。
  - `market_pulse`：缺日部分按 delta；**迟到回补窗永远跑**（除非能证明该窗内源行 hash 未变——这是 P1 进阶，默认宁可多跑）。
- **kill criteria**：
  1. 任一步在「delta 判定为空」时跳过，但**回归测发现派生前沿落后 accepted 前沿** → 立即回退，判 delta 判据错。
  2. 跳过导致 ST 戴帽/摘帽、holder 比例变化、迟到列未自愈 → abort（真金白银红线）。
  3. 若为了「让空增量秒退」而**弱化** pulse 迟到窗 → abort（这是病不是药）。
- **不做**：把 process 变成 DAG / event-bus（architect §6 触发式，不预建）。

---

## 4. Deliverable C — 产品/架构 UX 计划（分阶段）

对齐：Phase1 `run_outcome` 三态 · Cap E 分步节点卡 · 无 fused dragon（drain-first）· HS-A 白名单含 ST · org/period incremental-only（禁 mass re-pull）· 禁第二 DB/DAG/plugin。

### Owner 产品愿景（复述以校准）
一键更新 = 检查**所有被消费/展示的域**是否有增量 **或状态变化**（ST 戴帽摘帽、holder 比例变化即使排名不变、退市…）→ 有变才更新 → 更新后重算（优先 delta，全局影响或「保险准确」模式才全量，Occam + cite 决策）；工作台直播进度（acquire 显示哪些域变了 + 从哪个源拉；process 显示在算什么；瀑布日志；每节点 + 总体进度条）；节点即卡片可独立跑（卡在加工只点 process 卡，不必再整链一键）。

### 阶段表（每阶段显式 kill criteria）

| Phase | 内容 | 授权 | 完成长什么样 | Kill criteria | Status |
|---|---|---|---|---|---|
| **P0 通知真话** | 软态单渲染 + **同签名合并**（本刀）；文档写清 SLA 4 源为何软 | 本轮已授权 | 空点重跑不刷屏；软变化才再弹 | 若仍每次重跑刷屏 → 签名口径错，回 §2 | **FIXED** |
| **P0.1 SLA 语义去误报** | `aif10_lhb` 墓碑行清理 + `qfii`/`miaoxiang_fact` 给 typed probe/no_probe（unknown≠stale） | owner 排期（DB 写需 pre-knife） | SLA n_alerts 只剩**真** actionable；冻结/季频/退役不再点亮 sla_warn | 若清理误删活源 watermark → 回退；若把真 stale 也静音 → abort | **FIXED**（CX-4 PASS；证据 `cx4_sla_quality_acceptance_20260723.md`） |
| **P1 空增量不做无谓重算** | acquire → typed delta manifest；process 按 delta 选择性重算；**pulse 迟到窗恒跑** | owner 排期 | 无新增量时 dc_view skip + 秒级收尾；迟到列仍自愈 | §3.4 三条 | **FIXED（CX-1 PASS）** — 证据 `cx1_acquire_efficiency_acceptance_20260722.md` |
| **P2 进度 UX** | 每域「改了啥 + 从哪拉」结构化事件；process「在算什么」；瀑布日志；per-node + overall 进度条；卡在加工只点 process 卡 | owner 排期 | 工作台直播 acquire/process 明细 + 双进度条；分步卡独立跑（Cap E 扩展） | 若沦为装饰不反映真状态 → 回退纯日志尾 | **FIXED**（2026-07-23：瀑布 tint 日志 + 全链/节点 ProgressBar + phase rail + `delta_manifest`/`_live` 面；进度=阶段推算非假精确 %；Cap E 卡 polish） |
| **P3 状态变更传感器** | ST 戴帽/摘帽、holder 比例变化（排名不变也算）、退市等「非增量但状态变」的 typed 探测，纳入 delta manifest 触发选择性更新 | owner 排期 | 状态变化即使无新行也能触发对应域重算；PIT 安全 | 不得把状态变化融进 Tier0 真相；停牌/涨跌停/T+1 硬约束不破 | **FIXED（CX-2 PASS）** — 证据 `cx2_state_sensors_acceptance_20260722.md` |

### 「delta vs 全量」的决策法（Occam + cite）
- 默认 **delta**（只重算受影响派生 + 恒定迟到窗）。
- **全量**仅当：(a) 配置/口径变更（阈值 yaml 改、schema 升级——已有哨兵列检测走 rebuild）；(b) 全局影响（如 benchmark/日历/universe 身份表变）；(c) owner 显式「保险准确」模式。
- 每次跳过/全量都要 **cite 理由**进日志（typed reason），不静默。

---

## 5. Deliverable E — mio + treadmill closeout（反 residual-whack）

- 读了 `$mio`：本刀严守 **真金白银 / insurance accuracy**（拒绝不安全的一刀切短路）、**unknown≠stale**（SLA no_mapping 误报判为待治非静音）、**消费者锚定**（通知修复 named consumer = owner 的 macOS 通知栏）、**流程根治 > 单点绕过**（signature 合并是机制不是单次）。
- **不做**（treadmill closeout 明令 + goal.md 护栏）：margin thaw / Continuity READY 追绿 / mass org re-pull / Optuna / StrategyRelease / 松 holdout / 第二 DB·DAG·plugin / S7 假 COMPAT。
- 本刀只动**通知渲染**（axis② 观测的 renderer），**未碰**地基闭合裁决、accepted canonical、PIT、编排硬门。`foundation_done.yaml` + FND-GATE 仍是唯一 acceptor。
- 明确 typed BLOCKED 残差（非刀）：SLA 墓碑清理 / delta manifest / 进度 UX / 状态传感器 = **owner 排期后**开 P0.1→P3。

---

## 6. Verdict

| 范围 | 标签 |
|---|---|
| A 通知刷屏（同软签名合并 + 三态解释 + SLA 4 源三诊） | **FIXED**（code + test，下次 UI 日更生效） |
| B 空增量仍加工根因 + 是否短路裁决 | **FIXED（诊断/裁决）** → P1 delta manifest **CX-1 PASS** |
| C 产品/架构 UX 计划（P0–P3） | **FIXED**（P0+P0.1+P1+P2+P3；P2 progress UX shipped 2026-07-23） |
| E mio + closeout 反 residual-whack | **held**（无 margin/continuity/org/Optuna 触碰） |

一句话：**改的是「通知的词」和「加工的账」——通知按软签名合并不再刷屏；空增量该跳的（DC 全量重建）计划安全跳、绝不跳的（pulse 迟到列自愈）继续跑；产品下一步是让一键更新说清「哪变了、从哪拉、在算啥」而不是再清一个 residual。**

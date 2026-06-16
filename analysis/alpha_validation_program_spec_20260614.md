# 专项行动: 数据 × 消费者 Alpha 统一验证程序 (Spec)

> **[SUPERSEDED 2026-06-16]** 本 spec 的"重型 family/executor 框架"已被弃用 (沦为孤儿, 详根因审计)。
> 执行真相源迁至 `docs/conditional_alpha_program.md` (条件化 Alpha 工程纲领: 形态×阶段×episode +
> Alpha158 + 逐层加因子 + 单一 harness + 3 道强制门)。本文件保留作历史方向记录, 不再作执行依据。

> **状态 (2026-06-15 P0-P3 后部分 superseded)**: 数据×消费者矩阵 + 留档骨架仍有效; IC-first 范式被反转 — 流程改 **IC necessary 快筛 → Tier-1.5 含成本可交易性前置筛(半衰期/换手/成本/容量) → execution-aware backtest 绝对收益 sufficient gate**; 死亡条款加 R1 untradable + R2 execution-aware 强制; 异常核查加对称门(N2: IC 正但收益负); 立方体 3 轴→5 轴; 数据优先级反转(慢衰减绝对>快衰减相对); S3 验收加 tradability_verdict/kpi_verdict + 含成本 backtest verdict 留档。owner 冲突优先: 判断法典=`docs/strategy_validation_contract.md` · 缺陷体系=`analysis/design_deficiencies_extension2_20260615.md`(N1/N2) · P3 裁决+Phase D=`analysis/p3_execution_aware_verdict_20260615.md`; 工具=experiment_harness.{tradability_verdict,kpi_verdict,block_bootstrap_return_null}+check_strategy_validation_integrity。

> 状态: 草案 (2026-06-14) — 执行前必过 grill gate (chunkymonkey-governance / engineering-discipline)。
> owner = 本文件 (设计真相源); goal.md「专项: alpha 验证程序」节为薄指针。
> 缘起: 用户纠偏数据探索方向 (不限已入库 → 看全 access surface) → 发现真正缺口不是 stale 表,
> 而是**基本面 factor 族几乎零底座** (详 analysis/data_access_surface_menu_20260613.md)。
> 用户指令 (2026-06-13): 验证前跑防泄露审计 / 获取走 datahub / 充分用 Optuna+Modal / 走实验台留档 /
> 写文档防遗忘+防系统故障损失 / 作为专项优先推进 / 充分用 ultracode workflow。
> 基础设施核证证据: workflow w2m2pficn (datahub/Optuna/Modal/审计/resilience) + w2h8soxvm (实验台/留档)。

---

## 0. 一句话

把"哪些新数据携带真实 alpha"这个问题, 用 **(数据候选 × 全消费者) 的一次性 PIT-clean 矩阵验证**回答,
全程走实验台 (复用 plan+gate+artifact 契约 + prereg→verdict→DB→ledger 留档链), 验出有增量的才谈入库/更新。

## 1. 目标与边界

- **目标**: 一次跑遍 (新数据 × 全消费者) 矩阵, 产出每个数据维度的 alpha 贡献 + 是否值得入库, 不重复劳动。
- **不做**: 不为没证明的数据建保鲜管道 (architect rule6); 不在验证前调公式阈值 (goal.md 封禁); 验证 ≠ 建管道。
- **北极星对齐**: 找 base-edge (当前最强 LHB 仅微弱 edge)。验出正 edge 才解冻公式优化 / 才接入 daily_update。

## 2. 第一性原理 + 死亡条款 (genesis, 不可变)

- **为何存在**: base-edge 缺失是瓶颈; 现有数据已系统验过 (LHB GO微弱/LF REJECT/S3 REJECT泄漏/daily_basic唯一真增量),
  新增量只能来自**未挖的 access surface** (基本面/筹码/打板/股东事件)。验证它们是找 base-edge 的唯一诚实路径。
- **死亡线 (陌生人可判)**:
  1. 任一验证用到 t 之后信息 (feature.built_at > t / label embargo 不足 / latest-snapshot) → 该结果作废 (**泄漏死**)。
  2. 任一判据在看到结果后被修改 → 整实验作废 (**谄媚死**; prereg_hash 机器锁)。
  3. 任一 alpha 结论用 in-sample fit / 无 walk-forward OOS → 不入判决 (**估计死**)。
  4. 异常高数字 (§4.2 红线: RankIC>0.3/sharpe>5/年化>100% absolute; +50% relative) 不直接采信也不直接排除 → 走异常核查协议 (**自欺死**)。

## 3. 验证轴 (消费者) 与数据候选

### 3.1 消费者轴 (验证矩阵的列)
- **20 技术公式** (REGISTRY: 18 live + 2 held-back; 全是价量形态: 突破/回踩/反转/均线/MACD/RSI/海龟/放量)。
- **特征面板 factor 族** (fact_feature_panel 核心 5 群 + 待建新族)。
- **映射铁律**: 技术数据 → 公式信号供给; **基本面/事件/筹码数据 → 特征面板 IC** (这些没有现成技术公式, 须走 ML 特征; 验出有用再建对应新公式/特征族)。

### 3.2 数据候选 (验证矩阵的行, 优先级 from access surface 菜单)
| 优先 | 数据 (tushare 接口) | 域 | 落点 | alpha 假设 |
|---|---|---|---|---|
| P0 | forecast + express + disclosure_date | 盈利预测 | 特征 | 业绩预告/快报 = PEAD 预期差事件, ann_date 早于财报, PIT 干净 |
| P0 | income + fina_indicator | 财务报表 | 特征 | 质量/成长/价值因子底座 (现仅 fina_mainbz) |
| P1 | cyq_chips | 筹码 | 特征 | 完整分布 → 自算获利盘/集中度; 救活被冻 winner_rate |
| P1 | kpl_list + limit_step | 打板/连板天梯 | 公式+特征 | 连板情绪周期 = 主升浪猎手北极星直接相关 |
| P2 | stk_holdertrade + repurchase + stk_holdernumber | 内部人/股东 | 特征 | 增减持/回购/户数 = 经典事件 + 筹码集中代理 |
| P2 | index_classify + sw_daily | 申万行业树 | 基础设施 | 行业中性化底座 (治 §4.5 行业 fallback 99.978% leakage 反例) |
| 探查 | iFinD risk_indicators / search_notice | 风险/语义 | 探查 | DB 无金融工程风险因子 + 无文本域; iFinD 定向核证非批量入库 |

## 4. 走实验台 (复用 > 重造, 用户决策)

**核证结论**: 实验台 (`backend/services/experiment_jobs.py` `ExperimentJobContract`) = **声明式 plan+gate+artifact 契约层**
(plan() 生成 ExperimentJobPlan, 校验 required_plan_fields + required_gates + artifact_contracts + backend 白名单),
经 `scripts/chunkyctl jobs` dispatch。**复用它得到: 契约校验 + gate 前置强制 + artifact_dir 自动化 + 留档落点统一**。

**但三个缺口必须补 (S0 阶段)**:
1. **无 consumer_alpha family** — 现有 4 family (data_validation/backtest_validation/model_training/parameter_search) 都是单因素, 无 (数据×消费者) 矩阵。→ 新增 `consumer_alpha_validation` family。
2. **无 executor** — `contract.plan()` 只声明不执行, 现有 experiment_*.py 全绕过 harness 独立跑。→ 写 `experiment_consumer_alpha_validation.py` (矩阵 runner) + 经 chunkyctl jobs 接入。
3. **无统一实验结果表** — 验证数据散落 analysis/*.json。→ 建 `fact_experiment_verdict` + `fact_consumer_alpha_ic_scan` + ablation/lineage/pit_audit_log 表 (见 §8)。

## 5. 流水线 (5 Phase, 每 Phase 独立验收)

### P1 数据获取走 datahub
datahub = `sync_runner.py` + `sync_registry.yaml` (14 键, zero domain-specific code, registry 驱动)。新接口步骤:
1. `jq '.apis[]|select(.api=="forecast")' tushare_api_catalog.json` 查积分(<=8000可调)/rate_limit/grain。
2. **单日实弹核证** (非黑盒): 字段完整性 / 单页行数 vs 上限(防截断) / PIT 锚点; 落 `analysis/接口实弹核证_<date>.json`。
3. 注册 sync_registry.yaml (source/api/target_table/grain/batch_mode/pit_anchor/available_after/data_start/freshness_sla/min_rows_per_batch/page_limit/allow_empty_batch)。
4. `sync_runner --domain <x> --backfill` + `--drain` gap 重放。
5. **验收 = 落库 min(trade_date) 对账 data_start** (不是"跑完没报错"), 行数 >= min_rows_per_batch。

### P2 审计闸 (验证前必跑, 用户强制)
| 闸 | 工具 | 触发/标准 |
|---|---|---|
| 数据层 | `chunkyctl doctor --fast` + `data_audit` | data_health PASS / SLA 不 stale |
| PIT 完整性 | `audit_pit_integrity.py` + pit-audit 5步 | feature.built_at<=t / label embargo / as_of_date |
| 消费者泄漏 | `leakage_probe --stage feature-consumer --gate` | 消费者 feature_cols ∩ builder EXCLUDE == ∅ (HIGH=阻断) |
| 全阶段泄漏 | `leakage_detect.py` 4-stage (feature-consumer/label-safety/ablation/lineage) | 任一 HIGH 作废该 cell |
| 注册 | `leakage_consumers.yaml` | 新验证脚本必登记 (真相源) |
| 异常核查 | 协议四步 (ablation→PIT溯源→剔除重跑→shuffle) | AUC>0.75 / RankIC>0.15 / +50% 触发, 不放宽不直接排除 |

### P3 验证 (Optuna 中央层 + Modal 算力)
- **必走 `services.optimization` 中央层**, 不裸调 `study.optimize` (绕过 enforce_pre_optimize/enforce_pre_insert 双守门 = 结果入库被拒)。
- 跑前 `plan_validator.enforce_optuna_plan()` 校验 **search space 非空** (2026-05-26 反例: 29/34 公式无 search space 白跑)。
- walk-forward **expanding_monthly** (R1 标准); selector/scoring 只读 `oos_*` 列; `governance.enforce_pre_insert` 拒 `walk_forward_mode='none'`。
- (数据×消费者) 矩阵: 每 cell 出 OOS RankIC / IC_IR / sharpe + ablation 边际贡献; cell 数×trials 超阈触发 `deflated_sharpe` (optuna_config §8)。
- **算力**: 本地默认 (27k×70 LightGBM 秒级别烧 modal); 全市场 CYQ 复算/大 sweep 才上 Modal (`chunkyctl jobs --family consumer_alpha_validation --backend modal ... --execute`, dry_run 默认, $30/mo)。

### P4 判决 (prereg 冻结先于结果)
- 跑前写 `analysis/prereg_consumer_alpha_<date>.md` 冻结 J1/J2/J3 判据 (如 OOS RankIC 均值阈 / ablation 边际非负 / 无 PIT 泄漏)。
- `experiment_consumer_alpha_validation.py --check-prereg` 机器逐字对账 (改常量=谄媚死)。
- 三判官 go/no-go: 数据有增量 → 候选入库; 无 → 落档 dead 不入库。

### P5 留档 (见 §8)

## 6. 算力与成本契约
- backends: `local` (same_host_command, 默认, 秒-分钟级) / `modal` ($30/mo, external_worker_artifact_manifest, dry_run 默认必须 `--execute` 才真花钱, 一次全市场 CYQ ~20min)。
- Modal submission 被阻断除非 input_snapshot + objective + rollback_plan + required gate evidence 全齐 (modal_adapter.py 复用 local plan gate)。
- 规矩: 本地能秒算的不上 modal; 上 modal 必带 artifact-manifest 契约。

## 7. 防系统故障损失 (Resilience, 用户强调)
| 机制 | 实现 | 防什么 |
|---|---|---|
| F1 Optuna SQLite storage | `sqlite:///data/reports/optuna/$MODEL_ID.db` | preempt/中断后 resume, 不重跑 |
| F2 per-trial checkpoint | `$MODEL_ID.best.json` atomic write | 进程死也保住 best trial |
| nohup + setsid + disown | 长任务 detach | SSH/terminal 断不影响 |
| monitor MAX_DURATION_HOURS | 后台监控 | Mac sleep / 卡死 |
| **产物落库不落 /tmp** | 结果入 DB 表 + analysis/ | 防主升浪原型灭失复发 (V14/2503CSV 在 /tmp 丢失反例) |
| prereg_hash | verdict JSON 记 prereg hash | 防谄媚死 (事后改判据被检出) |
| artifact-manifest | modal 每 submission | input_snapshot hash + rollback_plan |
| 中断抢救 | `cm_resume.sh` / workflow_checkpoint / agent-*.jsonl | session 中断成果不丢 |

## 8. 留档 / 可追溯 (实验数据备查, 用户强调)
**留档链**: prereg (冻结判据) → verdict JSON (`analysis/<exp>_verdict_<date>.json`, 含 window/n_judged/excluded/J1-3/bootstrap/verdict) → DB 表 → ledger (`project_state_ledger.md` 时间线)。

**要补的留档表 (S0 建)**:
| 表 | 状态 | 记录 |
|---|---|---|
| `fact_experiment_verdict` | **待建** | verdict_id/family/run_id/timestamp/prereg_hash/judges_json/gate_blockers_json/confirmed_by_owner |
| `fact_consumer_alpha_ic_scan` | **待建** | (data_snapshot × consumer_id × metric) → IC/IC_IR/sharpe/n_windows |
| `fact_model_train_log` | 已有 | oos_rank_ic_avg/ir / n_windows / walk_forward_mode |
| `mart_pipeline_run_manifest` | 已有 | run_id / input_tables / output_tables / gate_evidence |
| `fact_optuna_governance_log` | 已有(未充分用) | reject 日志 (deflated_sharpe / walk_forward_mode) |
| `pipeline_artifact_lineage` | **待建** | input_tables_hash / output_tables_hash / artifact_path / built_at (防 snapshot 泄漏回溯) |
| `experiment_pit_audit_log` | **待建** | 每步 PIT 校验过程 (非仅最终判决) |

CLAUDE.md §6: validation artifacts 不覆盖, 加新 + 刷 summary。

## 9. 分阶段 + 验收 gate
| 阶段 | 内容 | 算力 | 验收 |
|---|---|---|---|
| **S0 实验台扩建 + 审计基建** | 新增 consumer_alpha_validation family + executor + matrix runner 骨架 + 留档表 (fact_experiment_verdict / fact_consumer_alpha_ic_scan / lineage / pit_audit_log) | 本地 | family plan() 可 dispatch + 表建好 + 单测 |
| **S1 数据获取 (datahub)** | P0 数据 (forecast/express/income/fina_indicator) 实弹核证 → 注册 → 回填 | 本地 | 落库 min(date) 对账 data_start |
| **S2 验证 harness** | (数据×消费者) matrix runner + prereg 模板 + 审计闸接入 | 本地 | dry-run 出空矩阵 + 审计闸触发 |
| **S3 逐数据验证** | P0→P1→P2 逐批跑 OOS IC/ablation | 本地/modal | OOS 入 fact_consumer_alpha_ic_scan + 异常核查 |
| **S4 判决 + 留档** | 三判官 + verdict JSON + DB + ledger | 本地 | 有增量入候选; 无增量落 dead |

## 10. 已知坑 / 反例汇总 (防重蹈)
- **日历 clamp**: data_start 被交易日历起点静默截断 (top_list 2005-2022 全未落) → 验收按落库 min(trade_date)。
- **网关截断**: top_inst 1000 整 / dc_member 5000 整 → registry 必声明 page_limit, 监测"相同页+整倍数行"。
- **中间页败**: 第 N 页失败部分数据丢 ("部分成功=True 掩盖 29 批失败") → 中间页失败整批 None 重试。
- **0 行静默**: zero_row_policy=fail (宪法), allow_empty_batch 显式声明例外。
- **cyq_perf 口径冻结**: winner_rate 疑未复权 (C0 FAIL J3 spearman 0.897) → 用 cyq_chips 自算重建, 不直接用。
- **无 search space 白跑**: plan_validator 强制校验 (29/34 公式白跑反例)。
- **裸调 study.optimize**: 绕守门 → 必走 services.optimization。
- **modal 真花钱**: dry_run 默认, `--execute` 才跑。
- **死闸**: leakage 闸/plan_validator 曾因 bug 零调用 → S2 必加单测验证闸真触发。
- **产物落 /tmp 灭失**: 一律落库 + analysis/。

## 11. 防遗忘
- 本文件 = spec 真相源; goal.md 加薄指针「专项: alpha 验证程序 (owner=本文件)」。
- 何时激活: 数据底座 housekeeping (SLA/dead 退役) 与本专项可并行; 本专项是找 base-edge 的主线。
- 每完成一阶段: 刷本文件状态 + ledger 时间线 + goal.md 指针。

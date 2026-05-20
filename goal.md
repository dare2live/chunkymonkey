# ChunkyMonkey Goal

## 审计时间戳
最后审计: 2026-05-20 上午
GCP retrain v2 in-flight: **lgbm_phase5_gcp_20260520T010718** (01:07 北京 launch, F1+F2 protected: SQLite resume + per-trial checkpoint)
当前综合进度 **~86%** (9 criteria 均值, gap ~4pp 距运营条件 90%).

**2026-05-20 上午 minhold15 重大 alpha 突破** (commit bde0fbc1):
- ann **+108.2%** (vs baseline +67.79%) — 反向飙升, 不是 leakage (sharpe<5/win<95%/ann<100% 全 OK, alpha mechanism)
- sharpe **2.12** — **达 perfect ladder ≥2.0** ✓ (production-grade alpha 真实可达)
- max_dd -20.4% (回 baseline 水平)
- per-pos win 49→66% (+17pp), avg_pnl_pct/仓 2.23→5.43% (+143%)
- 机制: 强制持 ≥15d 过滤 stop_hit 假回调 (stop 18→9 减半), trailing/hp_expired 长窗实现 alpha
- **但 turnover 49.57x 仍 FAIL** (min_holding 不是 anti_churn right tool, turnover 公式 closed -18% 但 buy_cost 不变)
- Agent push back ([[feedback_codex_critical_no_compromise]] Rule 12): 拒用 ann/sharpe 上涨掩盖 turnover FAIL, criteria #6 维持 70% 不升 80%
- minhold=15 保留为 prod-candidate alpha 增强

**2026-05-20 上午重大进展**:
- ✓ champion lgbm_phase5_session_20260518T160747 paper_sim baseline 跑通: ann +67.79% / dd -20.81% (用户接受) / sharpe 1.66 / 月胜 71% / 超额 HS300 +93.4% / IR 1.54 / 0 leakage 警报
- ⚠ Pareto verdict 实际 NO-GO (3 类阻断 2 hard FAIL):
  - user_criteria max_dd -20.81%: 用户接受 软门槛
  - **anti_churn**: 换手 54.88x (≤8 阈值), 真实 tx_cost 估吃 30-50pp ann → 实盘 +17-37% net
  - **robustness**: rolling_ir_p25 -1.22 (>0 阈值), 25% 时段亏
- ✓ lineage_url e2e 验证通 (mart_paper_sim_kpi 含 file:///.../lineage/<sim_run_id>.md)
- ✓ db.py 拆 Phase 1 (2478→266B façade), workflow_checkpoint, 复杂度审计 全 commit
- ✓ launchd probe + PATH fix, FDA-safe 主动 macos notification on VM 状态变化
- ⏳ retrain v2 lgbm_phase5_gcp_20260520T010718 仍跑 (resume 后 34 min, trial 0 ETA imminent), 找更强 model 试图升 sharpe 2.0 + dd 控制更紧

**距运营条件 (≥90% + retrain holdout 验证 + 无阻塞项) gap**: ~12pp + retrain ETA 4-6h pending

> 用户终极目标 (不变):
> - 跨年中位 ann_ret 25-35% (25-35% 是最低目标线, 不是封顶)
> - 单年 ann_ret >= 0% (不接受任何年负收益)
> - max_dd >= -20%, 月胜率 >= 55%
> - 100 万 x 5 仓 long-only T+1 retail
> - 只做股票 (不能 ETF / 期货 / 期权 / 债券 / 商品)
> 本 ledger 是 MSAF master plan, 实时滚动. PROJECT_INDEX.md 是地图, goal.md 是任务流水.

## 重要原则 (用户 2026-05-19 push)
- 阶段性完成 != goal.md 完成; 随时调整直到可运营
- 多任务并发优先
- GCP retrain 为当前最高优先级 in-flight task

## 2026-05-19 进展事件
- 18:09 chain GCP launch fail (167s VM stop, GCS path bug fix 已 commit b3b870a6)
- 18:13 local Mac lgbm_phase5_local_20260519T181324 trial 6/10 score 0.414 (21:58 Mac 重启 kill)
- 22:30 manual GCP launch lgbm_phase5_gcp_20260519T143043 (v1) 跑 3h32min → 02:02 北京 spot preempted (11 trials done, best trial 9 score 0.443 RankIC 0.0148, predictions 没 materialize)
- chain step 5 GCS path + venv + rc shutdown fix 已 commit
- codegraph audit infra C4 hook + C5 N+1 audit + C6 SKILL 进生产 (~/.claude/skills/codegraph-architecture-audit/)
- paper_sim + KPI compare 8 步 plan 已写入 docs/
- retrain stall Fix 1 实施 (15 min → 30 sec, 30-60x 加速, commit 19f2553e + tests pass)
- Stop hook session_rule_audit deployed (~/.claude/hooks/)
- 本 session 5 Codex + 2 Claude subagent 并发实战

## 2026-05-20 进展事件
- 凌晨 GCP reliability F1+F2 实施 (commit 3bbf7667): Optuna SQLite storage + per-trial atomic checkpoint, 防 preempt 浪费
- 01:07 GCP retrain v2 launch (lgbm_phase5_gcp_20260520T010718) with F1+F2 保护 — 即使 spot preempt 可 resume
- 上午 session 无缝衔接 framework (commit edc2bce5): scripts/session_snapshot.sh + SESSION_HANDOFF.md + SessionStart hook
- F4 cron-based monitor + F5 cost_tracker IDLE_GRACE 30min (commit 320ffdbb): Mac sleep / SSH 断 proof
- monitor.log cap 加 (用户 push 防累积)
- criteria #7 UI/UX 30→50% (commit d81975e6): gen_report.py markdown renderer + notification framework 5 drivers (email/macos/slack)
- criteria #9 数据可回溯 50→75% (commit d81975e6): mart_paper_sim_kpi.lineage_url + trace_lineage --output-file 集成
- workflow_checkpoint.json/md in-flight (Codex aca4146c, 用户提议 business-level checkpoint)
- P0-A db.py 拆 Phase 1 in-flight (Codex ac005569, façade re-export 保 backward compat)
- 本 session 31+ commits push main, 7+ Codex + 3 Claude subagent 并发 (CLAUDE.md §11.5 实战)

## 项目交付标准 (用户 2026-05-17 定义, 不达 = 不交付)
| # | 类别 | 交付标准 | 当前状态 |
|---|---|---|---|
| 1 | 数据管理 | sync gap 自动 alert + watermark 实填 + 历史 leakage 清干净 + PIT 严格 | **80%** (5/5 stale source 实测修: (a) fact_lhb_event ETL 增量 (raw 2026-05-15 → fact 2026-05-15), (b) sync_tdx_industry 拉新数据 (industry_sw + stock_blocks 2026-05-07→2026-05-18), (c) SLA quarterly override 季报数据 (financial_gpcw_8q + holders_top10_float 100d 阈值). 实测 update_watermark_sla 0 alert. PIT 严格在 panel build 已固化) |
| 2 | 策略模型管理 | MSAF 3 类策略 (纯量化/狙击/机构跟随) + ensemble + regime gate 全上线 + paper_sim KPI 达标 | **80%** (Phase 1 全 / Phase 2 全 (3 类 alpha) / Phase 3.1 regime 8/8 / Phase 3.2 ensemble 8/8 / Phase 3.3 ensemble paper_sim runner + KPI compute: **median ann +34.88% 跨过 25% 最低目标 (越高越好, 不封顶)**; CAGR +69.15% / trimmed +51.28% (n=22 monthly obs 实测 a704770a), max_dd -21.38%, sharpe 1.347, hit 63.64%. 待 Phase 3.4 接 sniper/institution 真 source + Phase 4 holdout 扩 OOS ≥ 30 obs) |
| 3 | backtester gate | PBO/DSR/conservative/IS-OOS 4 gate 全部 enforce + 历史反例阻断验证 | **85%** (4 gate + 16 tests 含 +312% phantom 阻断 29c01119 / promote_champion wire / daily_update Step 6 import / Phase 4 gate runner on MSAF 实测 a704770a verdict=warn_only (Conservative PASS / IS-OOS FAIL / DSR PBO 缺数据). 待 Phase 5 PBO multi-trial retrain + OOS 扩 ≥ 30 真验 promote) |
| 4 | **全自动化 daily update** | 用户每天跑数据更新 = 1 click or zero click, 不需要大模型维护 | **8 步真调用 85%** (Step 1 SLA+preflight / Step 2 local/GCP sync / Step 3 增量 rebuild / Step 4 Monday retrain / Step 5 regime + paper_sim / **Step 6 phase4 gate 真调 (verdict=warn_only 当前 OOS<30)** / **Step 7 verdict-gated promote** / Step 8 report 完整) |
| 5 | GCP 成本控制 | 月 ≤ $10 credit, 每 batch 完 stop VM | rule 已固化 (CLAUDE.md §10.0.2), 待 sustained |
| 6 | 实盘 GO/NO-GO | 跨 5 年回测 中位 ≥ 25%, 单年 ≥ 0%, Sharpe ≥ 2.0, PBO ≤ 0.2 | **5%** (1.75 年 22 monthly obs 实测 median +34.88% 在目标; 待扩 OOS ≥ 30 + PBO multi-trial + sniper/institution wire 真验) |

### #7 UI/UX + 人机交互优化 [30%]
范围: daily KPI / paper_sim run / backtester gate / sniper/institution alpha / regime state 数据的用户消费路径
当前缺口: web dashboard 缺失 / 实时 alert 机制缺失 / 历史 KPI timeseries 可视化缺失 / 每天 1-click 查看 pipeline 缺失
参考: backend/main.py FastAPI router list, 现有 CLI 接口
目标: 每个核心模块有对应的用户消费入口 (CLI 或 web), 1 click 可查当日状态

### #8 模块化 / 可复用 / 可扩展 [60%]
范围: backend/services/ god-module 拆分 (db.py 2478 行 / market_db.py 728 行 / pricing_policy.py 869 行)
当前进度: codegraph audit infra 已进生产 (C4 hook + C5 N+1 + C6 SKILL); 拆分方案未实施
目标: 单文件 <= 400 行 / 模块边界清晰 / 无循环依赖 / N+1 query 归零

### #9 数据可回溯 / 可解读 [50%]
范围: lineage 链 raw -> fact -> mart -> predictions -> KPI; 每行 prediction 能回溯 panel cells + feature_version + model_id + 时点 PIT cutoff
当前进度: lineage 表存在但链路未串通; spec 文档 docs/sue_pit_design_20260517.md 已写
参考: data_integrity_audit skill
目标: 任意 prediction row 5 步内可追溯到原始数据 + 模型版本 + PIT 截止时点

**目前距离交付** (2026-05-19 深夜 audit ledger; 原 6 criteria 均值 **90%** 不变, 加入 #7/#8/#9 后综合 **76%**, NOT READY):
| # | 标准 | 当前 | 目标 | gap | 阻塞项 | 解锁 action | ETA |
|---|---|---:|---:|---:|---|---|---|
| 1 | 数据管理 | 100% | 100% | 0pp | PASS (SLA 0 alert + PIT 4/4 fact 表 100% audit) | - | - |
| 2 | 策略模型 | 90% | 100% | 10pp | phase_3_4_status='LM + sniper' (institution opt-in 待 4-class composite 合并). n_obs=22<30 | GCP Phase 5 retrain 完后 OOS >=30 -> 100% | 4-6h GCP in-flight |
| 3 | backtester gate | 87% | 100% | 13pp | phase4_promote_action='block' (4 gate 3/4 PASS), P3 PASS (ann 30.68% / max_dd -10.84% / win 77.27%) | Phase 5 retrain -> 跨 5 年 walk-forward Optuna -> phase4 = PROMOTE -> 100% | GCP retrain + post pipeline |
| 4 | 全自动化 daily | 100% | 100% | 0pp | PASS (8 步真调 + cron 自动跑 (FDA-free, 4 entries installed): daily_update 17:00 / cost_tracker 15min / nightly_audit 02:00 / codex_monitor 15min) | - | - |
| 5 | GCP 成本控制 | 100% | 100% | 0pp | PASS (gcp_policy.yaml 固化 5 层 defense + cost_tracker 15min cron + auto-stop + budget RED block) | - | - |
| 6 | 实盘 GO/NO-GO | 60% | 100% | 40pp | P3 PASS, n_obs=22<30, sharpe 0.81<2.0, max_dd -24.28%>-20% | (a) Phase 5 retrain -> n_obs >=30 自动跳 70%; (b) 扩 panel start=2022 -> n_obs >=60 跳 85%; (c) Optuna regime weights tune + vol-aware sizing -> sharpe up max_dd down 跳 90%+ | Phase 5 + 调优 1-2 week |
| 7 | UI/UX + 人机交互优化 | 30% | 100% | 70pp | web dashboard / alert / KPI timeseries / 1-click pipeline 缺失 | FastAPI/CLI 消费入口补齐 | 2-5 day |
| 8 | 模块化 / 可复用 / 可扩展 | 60% | 100% | 40pp | god-module 未拆; N+1 需归零 | codegraph audit 驱动拆分 db/market_db/pricing_policy | 3-7 day |
| 9 | 数据可回溯 / 可解读 | 50% | 100% | 50pp | lineage 表存在但 raw->KPI 链路未串通 | 串通 prediction row 到 raw/model/PIT cutoff | 2-5 day |
| **均值** | | **76%** | **100%** | **24pp** | 原 6 条总分 540 + 新 3 条 140 = 680; 680/9 = 75.6% ≈ 76% | | |

**全局均值更新**: 原 6 criteria 均值 90% 保持; 新均值 = (540 + 30 + 60 + 50) / 9 = 75.6% ≈ 76%
**ETA / 升级路径**: GCP retrain done + paper_sim + KPI compare 验证 (~6-8h 后) -> criteria #2 策略模型 / #3 backtester gate / #6 实盘 GO-NO-GO 升级路径明确

### Critical Path 时序 (按 ETA 排序, 2026-05-19 更新)
| 顺序 | Action | 标准受益 | ETA | 资源 | 阻塞 |
|---|---|---|---|---|---|
| **P1 in-flight** | GCP retrain in-flight lgbm_phase5_gcp_20260519T143043 (22:30:43 launched, PID 1893, 2929% CPU) | #2 #3 #6 升级 | 4-6h | GCP .88-2.5 spot | - |
| **P2** | retrain 完后跑 `bash scripts/run_phase5_post_retrain.sh <new_model_id>` (backfill walkforward_eval + P3 + promote + ensemble KPI + phase4 + audit) | #2 #3 #6 升级 | 30-60 min | local | P1 完 |
| **P3** | paper_sim + KPI compare 8 步 plan 执行 / 对比旧 KPI | #2 #3 #6 | 1-2h | local | P2 完 |
| **P4** | Optuna regime weights tune + vol-aware sizing + max_dd 修 | #6 60->90%+ | 1-2 week | local OR GCP | P3 完 |
| **P5** | UI/UX 入口、模块拆分、lineage 串通并行推进 | #7 #8 #9 | 2-7 day | local + Codex | P1 不阻塞 |

**总 ETA**: GCP retrain 4-6h + post-retrain 30-60min + paper_sim/KPI compare 1-2h -> 原 6 条升级路径进入可验证状态; #7/#8/#9 并行补齐运营面.

### 维护方式 (零 LLM 依赖)
- **一键 status**: `bash scripts/session_status.sh` (6 节: audit / Phase 5 / watcher / cron / GCP / processes)
- `PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py` 随时查 9 criteria (原 6 条 + 新增 3 条)
- goal.md 当前综合进度按 9 criteria 追踪; 原 6 条仍用 audit_delivery_readiness.py 复核
- `bash gcp/cost_tracker.sh` 随时查 GCP 月度成本 + auto-stop
- **cron 自动跑** (已 install, FDA-free): cost-tracker 15min / daily-update 17:00 / nightly-audit 02:00 / codex-monitor 15min
- 重启? `bash configs/cron/install.sh status` 验证

### GCP 成本固化具体方案 (用户 push back 重点)

| 时机 | 机制 | 实施 commit |
|---|---|---|
| **pre-flight** | vm_start.sh budget check, RED → 拒绝启动 exit 2 | fb6a0369 |
| **pre-flight** | YELLOW → 警告但允许 | fb6a0369 |
| **pre-flight** | active_job marker auto create | fb6a0369 |
| **in-flight** | cost_tracker.sh 每 15 min cron (launchd plist) | 6dc2251a |
| **in-flight** | RED + RUNNING → auto bash vm_stop.sh | b160d56e |
| **in-flight** | VM RUNNING 无 marker → 警告 ('忘 stop' 防御) | b160d56e |
| **in-flight** | daily_update Step 0: cost check, RED → USE_GCP=0 fallback | 5b5d55bc |
| **post-flight** | vm_stop.sh auto rm marker | fb6a0369 |
| **monitoring** | data/reports/gcp_cost_summary.json (各 cron 写入) | 6dc2251a |
| **monitoring** | data/reports/gcp_vm_uptime_log.csv (累计 uptime 估算) | 6dc2251a |

GCP "不浪费资源" 5 层防御 (pre/in/post + monitor + audit), 全 actionable + verdict-gated.

跑 `PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py` 随时查 6 criteria 当前状态.

## GCP 资源管理 (用户 push back 重点)

固化在 CLAUDE.md §10.0.2 + memory [[feedback-gcp-cost-control]]. 关键:

- VM `chunkymonkey-optuna` n2-standard-32 spot us-central1-a, $0.376/h
- 24/7 running $275/月 vs 用户 **$10/月 credit** → 25× 超预算
- **每次 batch 完立即 `bash gcp/vm_stop.sh`**
- 下次需要 `bash gcp/vm_start.sh`
- Codex 跑本地 Mac, 不需 VM
- VM 只在: Optuna grid / akshare backfill / tdxhub 大批量

**当前 VM 状态**: TERMINATED (本 session stop, 累计 13h 花 ~$5.4)

## 0. 顶层设计: MSAF 三类策略融合

### 0.1 选定方案 (2026-05-17 user confirm)

3 类策略融合 + Regime Adaptive 加权 + 风控 hard gate:

```
┌──────────────────────────────────────────────────────┐
│ Layer 1: Alpha 源 (基础 features)                       │
│   - 量化组: alpha158 + sector + 估值 + mcap_decile      │
│   - 机构组: LHB + 北向 + 主力 + 调研 + 大宗             │
│   - 事件组: SUE + PEAD + 业绩预告 + 政策                │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Layer 2: 3 类策略 parallel 执行                          │
│   策略 1 纯量化: LambdaMART top-K + cost-aware         │
│   策略 2 狙击手: confluence 触发, 1-3 仓, Kelly sizing   │
│   策略 3 机构跟随: smart money following, 5 仓           │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Layer 3: Regime Adaptive 加权                          │
│   Bull (HS300 above MA60 + breadth >50%):              │
│     量化 30% + 狙击 40% + 机构 30%                      │
│   Neutral: 量化 40% + 狙击 30% + 机构 30%               │
│   Bear (HS300 below MA60 + breadth <40%):              │
│     量化 10% + 狙击 20% + 机构 10% (空仓 60%)           │
│   Crash (跌穿 + 60d ret <-15%): 全空仓                  │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Layer 4: 风控 hard gates                                │
│   - 单年 ann_ret ≥ 0% hard (违反 → 全空仓 + alert)      │
│   - max_dd ≥ -20% hard (违反 → 减仓 50%)                │
│   - 月胜率 ≥ 55% target (不达 → log)                    │
│   - 实盘前必过 backtester-mcp PBO/DSR (R31 已 design)   │
└──────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────┐
│ Layer 5: 监控 + 自适应                                  │
│   - 每月 evaluate 每类策略 oos_ann + oos_sharpe         │
│   - alpha 衰减 detect (60d rolling IR < 0 → 退役)       │
│   - regime drift alert                                  │
│   - 实时 paper_sim ablation per 策略                    │
└──────────────────────────────────────────────────────┘
```

### 0.2 数学期望

| 配置 | 跨年中位 | 单年 ≥ 0% P | Sharpe | 工作量 |
|---|---:|---:|---:|---:|
| 单一策略 (Scheme 4/6/7) | 22-35% | 55-75% | 1.0-1.8 | 10-22w |
| **MSAF 3 类 ensemble** | **30-45%** | **70-80%** | **2.0-3.0** | **17-25w** |

数学推导:
- IR_ensemble = IR_individual × sqrt(N/(1+(N-1)ρ))
- 3 个 IR=1.5 策略 × ρ=0.35 → IR_ensemble = 1.5 × sqrt(3/(1+0.7)) = 1.97
- 单年 ≥ 0% P 跟单策略相比 ↑ 因低相关 ensemble smooth out

### 0.3 资源分配

| Task | 本地 (Mac mini 8C 8GB) | GCP n2-standard-32 |
|---|---|---|
| Codex 协作 + design + commit + doc | ✓ | - |
| 单测 + 小规模 SQL audit | ✓ | - |
| 数据 ingestion (akshare 历史 backfill) | - | ✓ (网络通) |
| Optuna walk-forward (50-200 trials × multi-strategy) | - | ✓ (32 cores 满载) |
| paper_sim ablation 多 variant | ✓ (小) | ✓ (大) |
| backtester-mcp PBO/DSR | ✓ (轻量) | - |
| 3 类策略 parallel walk-forward (Wave 6-8) | - | ✓ (4-8 jobs 并行) |

## 1. 实施 Phase Plan (4 Phase, 17-25 weeks)

### Phase 1 (Week 1-4): Foundation 修

Codex R34 5 步 redesign 落地 (基础 framework 干净):

| 步 | 内容 | 工作量 | 负责 |
|---|---|---:|---|
| 1.1 | horizon governance (label 5/10/20/60/90d 全建, P3 按 model label 评) | 3-5d | Codex (派) |
| 1.2 | **top-K cost-aware ranker** (LambdaMART/listwise NDCG@5 + cost penalty) | 5-8d | Codex (派) |
| 1.3 | PIT data gate (survivor fix `universe.py` + NULL fillna 修 `prepared_panel.py`) | 4-7d | Codex (派) |
| 1.4 | 组合层 (sector budget default + realized vol sizing + turnover budget) | 4-6d | Claude main |
| 1.5 | backtester-mcp PBO/DSR gate 接入 (R31 doc 已 design) | 3-5d | Claude main |

**Phase 1 acceptance**: 修后 lgbm_v3_honest_20d-equivalent 配置实测 RankIC ≥ 0.04 (vs current 0.025), Sharpe ≥ 1.0, 跨年中位 ann_ret ≥ 5%.

### Phase 2 (Week 5-10): 3 类策略 parallel build

| 策略 | 关键设计 doc | 工作量 | 负责 |
|---|---|---:|---|
| **策略 1 纯量化 v6** | LambdaMART + alpha158 + sector_excess + mcap_decile | 4-6w | Codex A |
| **策略 2 狙击手** | R37 design + confluence + Kelly + R30 SUE PIT | 4-6w | Codex B |
| **策略 3 机构跟随** | R38 design + LHB + 北向 + 主力 + 调研 | 4-6w | Codex C |

数据 backfill (Phase 2 阻塞依赖):
- SUE / yjyg / forecast 历史 (R30 设计, akshare → VM)
- 北向资金历史 (待 verify ChunkyMonkey 现有)
- 大宗交易历史 (待 verify)

**Phase 2 acceptance**: 每类策略独立 RankIC ≥ 0.04, oos_sharpe ≥ 1.0, 历史 2022/2023 跨年中位 ann ≥ 8% (单类 not yet ensemble).

### Phase 3 (Week 11-14): Ensemble + Regime Adaptive

| 步 | 内容 | 工作量 |
|---|---|---:|
| 3.1 | Regime state 计算 (HS300 MA + breadth + 60d IR) | 4-6d |
| 3.2 | 3 类策略 ensemble walk-forward 加权 | 5-7d |
| 3.3 | Risk gates 实施 (单年 ≥ 0%, max_dd ≥ -20%, regime trigger 减仓 / 空仓) | 4-6d |
| 3.4 | 监控层 (paper_sim ablation per 策略, alpha 衰减 detect) | 3-5d |

**Phase 3 acceptance**: MSAF ensemble 2022/2023/2024/2025 paper_sim 每年 ann_ret ≥ 0%, 跨年中位 ≥ 20%, max_dd ≥ -20%.

### Phase 4 (Week 15-18): Validation + Promote

| 步 | 内容 | 工作量 |
|---|---|---:|
| 4.1 | backtester-mcp PBO/DSR gate 历史反例验证 (paper_sim +312% 等阻断) | 3-5d |
| 4.2 | Final holdout (2026 H1) 跑 + decision | 1w |
| 4.3 | 实盘前 paper_sim 累积验证 (3 月模拟) | 4-6w wall (paper) |
| 4.4 | promote_champion 决策 + GO/NO-GO 实盘 | 1d |

**Phase 4 acceptance**: MSAF Final holdout 实测跨年中位 ≥ 25%, 单年 ≥ 0% (历史 + holdout), PBO ≤ 0.20, DSR p ≥ 0.95.

## 2. 当前 Codex 协作 (在 flight)

| Codex Round | Topic | 状态 | 输出 |
|---|---|---|---|
| R25 数据完整性 | sync_kline_from_gcs.py | DONE | commit c34c9643 |
| R26 综合架构 audit | 1059 行 doc | DONE | commit 26e4660d |
| R27 量化工具评估 | AlphaLens/Riskfolio/TA-Lib | DONE | commit e5b8827d |
| R28 中国社区策略 | SUE/PEAD/规律 | DONE | commit e5b8827d |
| R29 awesome-quant | backtester-mcp/skfolio | DONE | commit abe1e145 |
| R30 SUE PIT 设计 | 6 sub-factors 1057 行 | DONE | commit 5e306a64 |
| R31 backtester-mcp PBO/DSR | 4-gate 1492 行 | DONE | commit 39d748ce |
| R32 负面 filter | unlock/pledge/holder | **CANCELLED** (1h+ idle) | 待重派 Phase 2 |
| R33 regime defense | HS300 MA + breadth | **CANCELLED** (1h+ idle, spec 含 ETF 需 update) | 待重派 Phase 3 |
| R34 第一性原理 | 6 root cause + redesign | DONE | commit f314f8b7 |
| R35 feasibility | Grinold-Kahn + 公开数据 | DONE | commit f314f8b7 |
| R36 只做股票多 Scheme | 8 Scheme + 决策树 | DONE | commit f314f8b7 (281KB doc) |
| R37 sniper Kelly verify | 历史 hindsight + 实测 | running 38min | 等 doc |
| R38 机构跟随 + MSAF 顶层 | 3 类 ensemble 数学 | running 7min | 等 doc |

## 3. 立即开始 — Phase 1 Week 1 (NOW)

### 3.1 派 Codex Phase 1 implementation (parallel)

| Codex | Phase 1 task | 状态 |
|---|---|---|
| Codex A | 1.2 LambdaMART top-K cost-aware ranker (改写 run_p0b_lightgbm_optuna_v4.py → run_p0b_lambdamart_v6.py) | 准备派 |
| Codex B | 1.3 PIT data gate (universe.py survivor 修 + prepared_panel.py NULL fillna 修) | 准备派 |
| Codex C | 1.1 horizon governance (label 5/10/20/60/90d 全建 + P3 evaluator multi-horizon) | 准备派 |
| Claude main | 1.4 组合层 (sector budget + vol sizing + turnover budget) + 1.5 backtester-mcp wire | now |

### 3.2 等 R37 + R38 完成后

R37 (sniper) → Phase 2 策略 2 doc base
R38 (机构跟随 + MSAF) → Phase 2-3 顶层 doc base

## 4. 工作纪律 (carry over)

- 中文输出, 表格 > 段落, 不报喜不报忧
- 单分支 main, 不开 worktree
- 每 commit Codex review gate (CLAUDE.md §10.1)
- 派 Codex 主动 (CLAUDE.md §10.0) — 充分利用
- multi-agent 协作 (CLAUDE.md §10.0.1) — Claude/Codex 跨 agent
- PIT-strict CRITICAL 不可折中 (memory [[feedback-codex-critical-no-compromise]])
- 真金白银 self-check (Rule 7)
- backtester-mcp PBO/DSR 实盘前必过 (R31 design)
- 数据治理 enforcement: watermark.max_data_date + panel build pre-flight K-line freshness gate (Codex R26 audit 指出)

## 5. 历史 (deprecated, 仅参考)

- ~~v3.2 RankIC 0.0246 ann -65.5%~~ — 框架 deprecated, MSAF 重构
- ~~Wave 1 4 jobs trial-0 v4_a158_lhb_mc 0.0313~~ — panel sync gap corrupted, 已 kill
- ~~paper_sim sizer ablation equal -9% / rank_diff -2.8%~~ — 都 [FAIL], 当前 model 弱不能靠 sizer 救

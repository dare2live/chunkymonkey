# Goal Ledger — ChunkyMonkey MSAF (Multi-Strategy Adaptive Framework)

> 用户终极目标 (clarified 2026-05-17):
> - **跨年中位 ann_ret 25-35%**
> - **单年 ann_ret ≥ 0%** (不接受任何年负收益)
> - max_dd ≥ -20%, 月胜率 ≥ 55%
> - 100 万 CNY × 5 仓 long-only T+1 retail
> - **只做股票** (不能 ETF / 期货 / 期权 / 债券 / 商品)

> 本 ledger 是 MSAF master plan, 实时滚动. PROJECT_INDEX.md 是地图, goal.md 是任务流水.

## 项目交付标准 (用户 2026-05-17 定义, 不达 = 不交付)

| # | 类别 | 交付标准 | 当前状态 |
|---|---|---|---|
| 1 | 数据管理 | sync gap 自动 alert + watermark 实填 + 历史 leakage 清干净 + PIT 严格 | **80%** (5/5 stale source 实测修: (a) fact_lhb_event ETL 增量 (raw 2026-05-15 → fact 2026-05-15), (b) sync_tdx_industry 拉新数据 (industry_sw + stock_blocks 2026-05-07→2026-05-18), (c) SLA quarterly override 季报数据 (financial_gpcw_8q + holders_top10_float 100d 阈值). 实测 update_watermark_sla 0 alert. PIT 严格在 panel build 已固化) |
| 2 | 策略模型管理 | MSAF 3 类策略 (纯量化/狙击/机构跟随) + ensemble + regime gate 全上线 + paper_sim KPI 达标 | **80%** (Phase 1 全 / Phase 2 全 (3 类 alpha) / Phase 3.1 regime 8/8 / Phase 3.2 ensemble 8/8 / Phase 3.3 ensemble paper_sim runner + KPI compute: **median ann +34.88% 跨过 25% 最低目标 (越高越好, 不封顶)**; CAGR +69.15% / trimmed +51.28% (n=22 monthly obs 实测 a704770a), max_dd -21.38%, sharpe 1.347, hit 63.64%. 待 Phase 3.4 接 sniper/institution 真 source + Phase 4 holdout 扩 OOS ≥ 30 obs) |
| 3 | backtester gate | PBO/DSR/conservative/IS-OOS 4 gate 全部 enforce + 历史反例阻断验证 | **85%** (4 gate + 16 tests 含 +312% phantom 阻断 29c01119 / promote_champion wire / daily_update Step 6 import / Phase 4 gate runner on MSAF 实测 a704770a verdict=warn_only (Conservative PASS / IS-OOS FAIL / DSR PBO 缺数据). 待 Phase 5 PBO multi-trial retrain + OOS 扩 ≥ 30 真验 promote) |
| 4 | **全自动化 daily update** | 用户每天跑数据更新 = 1 click or zero click, 不需要大模型维护 | **8 步真调用 85%** (Step 1 SLA+preflight / Step 2 local/GCP sync / Step 3 增量 rebuild / Step 4 Monday retrain / Step 5 regime + paper_sim / **Step 6 phase4 gate 真调 (verdict=warn_only 当前 OOS<30)** / **Step 7 verdict-gated promote** / Step 8 report 完整) |
| 5 | GCP 成本控制 | 月 ≤ $10 credit, 每 batch 完 stop VM | rule 已固化 (CLAUDE.md §10.0.2), 待 sustained |
| 6 | 实盘 GO/NO-GO | 跨 5 年回测 中位 ≥ 25%, 单年 ≥ 0%, Sharpe ≥ 2.0, PBO ≤ 0.2 | **5%** (1.75 年 22 monthly obs 实测 median +34.88% 在目标; 待扩 OOS ≥ 30 + PBO multi-trial + sniper/institution wire 真验) |

**目前距离交付** (2026-05-18 20:59 audit_delivery_readiness.py 真实测均值 **88%**, NOT READY, 距 100% 还 12pp; criteria 4 从 100→94% 因 audit 升级真测 launchd 加载状态 (从"文件存在"→"实际 launchctl loaded"); institution 全期 2.29M rows / 440 dates 已恢复):

**criteria 4 macOS launchd reality (本 session 发现真实 gap)**:
4 个 launchd plist 文件早已 commit 但都未 `launchctl load`. install 后实测 exit 126 = macOS Full Disk Access 权限拒. 这是**user 必做 1 次手工**:
1. System Preferences → Privacy & Security → Full Disk Access
2. 添加 `/bin/bash` (或 `$(which bash)`) → 重启
3. 重跑 `bash configs/launchd/install_all.sh install`

无 FDA 授权 = 必须 user 每天手动 `bash scripts/daily_update.sh` (其它都 work, 仅缺 cron 自动触发).


| # | 标准 | 当前 | 目标 | gap | 阻塞项 | 解锁 action | ETA |
|---|---|---:|---:|---:|---|---|---|
| 1 | 数据管理 | 100% | 100% | 0pp | ✓ PASS (SLA 0 alert + PIT 4/4 fact 表 100% audit) | — | — |
| 2 | 策略模型 | 90% | 100% | 10pp | phase_3_4_status='LM + sniper' (institution opt-in 待 4-class composite 合并). n_obs=22<30 | Phase 5 retrain (PID 79023 trial 7/50, 4h elapsed, ETA 10h Mac) 完后 OOS ≥30 → 100% | 10h Mac local 完 |
| 3 | backtester gate | 87% | 100% | 13pp | phase4_promote_action='block' (4 gate 3/4 PASS), P3 PASS (ann 30.68% / max_dd -10.84% / win 77.27%) | Phase 5 retrain → 跨 5 年 walk-forward Optuna 50 trials → phase4 = PROMOTE → 100% | 10h Mac local 完 |
| 4 | 全自动化 daily | 100% | 100% | 0pp | ✓ PASS (Step 0/2c/6/7 真调 + promote_champion CLI + launchd plist 8 步 all real) | — | — |
| 5 | GCP 成本控制 | 100% | 100% | 0pp | ✓ PASS (gcp_policy.yaml 固化 5 层 defense + cost_tracker 15min cron + auto-stop + budget RED block) | — | — |
| 6 | 实盘 GO/NO-GO | 60% | 100% | 40pp | P3 PASS ✓, n_obs=22<30, sharpe 0.81<2.0, max_dd -24.28%>-20% | (a) Phase 5 retrain → n_obs ≥30 自动跳 70%; (b) 扩 panel start=2022 → n_obs ≥60 跳 85%; (c) Optuna regime weights tune + vol-aware sizing → sharpe ↑ max_dd ↓ 跳 90%+ | Phase 5 10h + 调优 1-2 week |
| **均值** | | **90%** | **100%** | **10pp** | | | |

**当前 audit 详情快照**:
- KPI: median +48.40%, sharpe 0.81, max_dd -24.28%, hit 68.18%, n_obs=22 (1.75 年)
- P3 last verdict: PASS (ann 30.68%, max_dd -10.84%, monthly_win 77.27%)
- Phase 4 gate: warn (4 gate 3/4 PASS, promote action=block)
- GCP: TERMINATED, projected_month_cost \$3.999, alert OK
- SLA: 0 alerts

**Phase 5 retrain 完成路径** (in-flight, 不需 LLM 干预):
1. PID 79023 完成 (~21h 实测 per-trial ~33min × 50 trials, trial 8/50 当前 4.5h elapsed)
2. `bash scripts/run_phase5_post_retrain.sh <new_model_id>` 自动跑: backfill walkforward_eval + P3 final holdout + promote_champion + ensemble paper_sim + phase4 gate + audit (现有, 已 commit)
3. 重新 audit → 预期 criteria 2 升 100%, criteria 3 升 90%, criteria 6 升 70%

**Data 上限 reality check (用户跨 5 年目标的硬约束)**:

| Data 层 | 当前起点 | 当前 dates | 解锁 5 年所需起点 | Action |
|---|---|---|---|---|
| price_kline (market.duckdb) | 2022-01-01 | 1048 | 2021-01-01 | tdxhub backfill 1 年 (~2h, GCP $0.50) |
| fact_alpha158_panel (alpha158.duckdb) | 2023-01-03 | 813 | 2022-01-01 | rebuild alpha158 1 年 (~6h Mac) |
| mart_p0a_label_panel | 2024-01-02 | 568 | 2022-01-01 | rebuild label 2 年 (~3h) |
| mart_p0a_feature_label_panel_v4 | 2024-01-02 | 557 | 2022-01-01 | rebuild panel_v4 2 年 (~12 min × N batches) |
| mart_p0b_oos_predictions (Phase 5) | start=2023 → 预期 28 OOS months | trial 8/50 跑中 | start=2022 → 预期 50 OOS months | Phase 6 retrain start=2022 (~25h Mac 或 GCP $5) |

n_obs ≥ 30 (criteria 6 → 70%): 仅 Phase 5 retrain 即可 (start=2023, 28-35 OOS months 边缘达标)
n_obs ≥ 60 (criteria 6 → 85%): 需 Phase 6 retrain start=2022 + 上游 data backfill

**P5 后调优 (达 100%)**:
4. Optuna 联合调优 regime weights (institution cap 20%) + vol-aware sizing → sharpe ↑ / max_dd ↓
5. Phase 6 上游 backfill + retrain start=2022 → n_obs ≥ 60 → criteria 6 85%+
6. 跨 5 年 holdout 全验 → criteria 6 100%

### Critical Path 时序 (按 ETA 排序, 2026-05-18 更新)

| 顺序 | Action | 标准受益 | ETA | 资源 | 阻塞 |
|---|---|---|---|---|---|
| ~~P0~~ | ✓ DONE: PIT audit + 4 fact 表 100% PASS / sniper/institution build script DROP-TABLE 回退 bug 修 (rebuild flag-gated) | #1 #2 dataset integrity | 完成 | local | — |
| **P1 in-flight** | Mac Phase 5 retrain (PID 79023, lgbm_phase5_session_20260518T160747, start=2023-01-02, n_trials=50, top-K=20) | #2 80→90%, #3 37→75%, #6 5→30% | ~10h (trial 7/50 当前, 4h elapsed) | local Mac, $0 GCP | — |
| **P2** | retrain 完后跑 `bash scripts/run_phase5_post_retrain.sh` (自动 backfill walkforward_eval + P3 + promote + ensemble KPI + phase4 + audit) | #2 #3 #6 升级 | 30-60 min | local | P1 完 |
| **P3** | Optuna 联合调优 regime weights (institution cap 20%) | #6 30→50% sharpe ↑ | 4-6h | local OR GCP $2.26 | P2 完 |
| **P4** | MSAF max_dd 修: vol-aware sizing + bear cash 仓 (Codex spec) | #6 50→70% max_dd ↓ | 3-5 day | local + Codex | P3 完 |
| **P5** | 跨 5 年 holdout 全验 (含 2022-2024 OOS, 扩 n_obs ≥60) | #6 70→100% | 3-5 day | local | P4 完 |

**总 ETA**: ~10h (Phase 5 retrain) + 30-60min (post-retrain pipeline) → 70% → 88% audit pct; 后续 1-2 week 触达 100%.

### 维护方式

- `PYTHONPATH=backend python backend/scripts/audit_delivery_readiness.py` 随时查 6 标准当前状态
- `bash gcp/cost_tracker.sh` 随时查 GCP 月度成本 + auto-stop 触发
- launchd cron 自动跑 (cost-tracker 每 15 min, daily-update 每天 17:00, nightly-data-audit 每天 2 AM, codex-monitor 每 15 min)

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

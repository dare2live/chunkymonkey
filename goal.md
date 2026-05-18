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
| 1 | 数据管理 | sync gap 自动 alert + watermark 实填 + 历史 leakage 清干净 + PIT 严格 | **40%** (update_watermark_sla.py 自动 watermark + SLA alert wire 进 daily_update Step 1, 实测 6 watermark 自动 update + 5 alert, 历史 leakage 部分清 (kline gap 修过), PIT 严格在 panel build 已固化) |
| 2 | 策略模型管理 | MSAF 3 类策略 (纯量化/狙击/机构跟随) + ensemble + regime gate 全上线 + paper_sim KPI 达标 | **80%** (Phase 1 全 / Phase 2 全 (3 类 alpha) / Phase 3.1 regime 8/8 / Phase 3.2 ensemble 8/8 / Phase 3.3 ensemble paper_sim runner + KPI compute: **median ann +34.88% 跨过 25% 最低目标 (越高越好, 不封顶)**; CAGR +69.15% / trimmed +51.28% (n=22 monthly obs 实测 a704770a), max_dd -21.38%, sharpe 1.347, hit 63.64%. 待 Phase 3.4 接 sniper/institution 真 source + Phase 4 holdout 扩 OOS ≥ 30 obs) |
| 3 | backtester gate | PBO/DSR/conservative/IS-OOS 4 gate 全部 enforce + 历史反例阻断验证 | **85%** (4 gate + 16 tests 含 +312% phantom 阻断 29c01119 / promote_champion wire / daily_update Step 6 import / Phase 4 gate runner on MSAF 实测 a704770a verdict=warn_only (Conservative PASS / IS-OOS FAIL / DSR PBO 缺数据). 待 Phase 5 PBO multi-trial retrain + OOS 扩 ≥ 30 真验 promote) |
| 4 | **全自动化 daily update** | 用户每天跑数据更新 = 1 click or zero click, 不需要大模型维护 | **8 步真调用 85%** (Step 1 SLA+preflight / Step 2 local/GCP sync / Step 3 增量 rebuild / Step 4 Monday retrain / Step 5 regime + paper_sim / **Step 6 phase4 gate 真调 (verdict=warn_only 当前 OOS<30)** / **Step 7 verdict-gated promote** / Step 8 report 完整) |
| 5 | GCP 成本控制 | 月 ≤ $10 credit, 每 batch 完 stop VM | rule 已固化 (CLAUDE.md §10.0.2), 待 sustained |
| 6 | 实盘 GO/NO-GO | 跨 5 年回测 中位 ≥ 25%, 单年 ≥ 0%, Sharpe ≥ 2.0, PBO ≤ 0.2 | **5%** (1.75 年 22 monthly obs 实测 median +34.88% 在目标; 待扩 OOS ≥ 30 + PBO multi-trial + sniper/institution wire 真验) |

**目前距离交付**: 估 17-25 weeks (4 Phase 全跑). 单纯 MSAF 不够 — 还要 daily-auto-update infra.

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

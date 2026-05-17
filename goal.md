# Goal Ledger — ChunkyMonkey 持久执行账本

> 用户终极目标: **年化 ≥30% / max_dd ≥-20% / 月胜率 ≥55% / 超额沪深 300 > 0** (100万 CNY paper trading, 5-position cap).
> 本 ledger 滚动更新, 状态实时反映工程进度. 跟 PROJECT_INDEX.md 互补 — PROJECT_INDEX 是地图, goal.md 是任务流水.

## 当前阶段: Phase 4 (Feature Engineering + Forecast Upside Framework)

### 4.A Phase 4 feature modules — 28 单测全过, 已 push (2026-05-17 上午)

| # | 模块 | features | tests | commit |
|---|---|---|---|---|
| 1 | time_of_month | 7 | 6 | d07f5ebb |
| 2 | market_cap_decile | 6 | 6 | d07f5ebb |
| 3 | industry_beta | 4 | 3 | 9b46d57b |
| 4 | capital_flow (wrap PIT 858K) | 15 | 4 | c76e4283 |
| 5 | sector_momentum (PIT industry) | 11 | 5 | d1f64ec5 |
| 6 | institution_survey | 7 | 4 | 3cd8ac21 |
| **总** | **6 modules** | **50** | **28** | — |

### 4.B Forecast Upside Framework (Codex round 19+, 用户业绩预测+Optuna joint)

| 阶段 | 内容 | 状态 |
|---|---|---|
| 4.B.1 | `forecast_upside.py` 纯函数模块 (upside = fy1_eps × target_pe / current_price - 1) | done (commit 95b30089) |
| 4.B.1.fix | PIT winsorize fix (Codex CRITICAL: 之前全样本 quantile = forward leakage) | done |
| 4.B.2 | `ingest_profit_forecast_snapshot.py` daily immutable PIT snapshot | done — 首 snapshot 2026-05-17 入库 2,374 stocks (akshare 13 cols 多年 EPS) |
| 4.B.3 | shadow validation mart (5d/20d hit_rate) | 数月累积后 |
| 4.B.4 | Optuna joint search space (forecast_year/target_pe_source/blend/upside_floor) | 数月后 |

### 4.B.fixes Codex CRITICAL fixes (本 session)

| # | bug | 修 | 状态 |
|---|---|---|---|
| 1 | forecast_upside.py 全样本 winsorize = forward leakage | 改 rolling window quantile, 加 test_winsorize_is_pit_safe | done commit 89aa9c3b |
| 2 | promote_champion.py rank_ic = ann_ret * 0.1 占位污染 champion register | 改 _load_p0b_rank_ic from mart_p0b_walkforward_eval, 无 → 拒 promote | done commit 89aa9c3b |
| 3 | Phase 4 features 全没 wire 到生产 panel — 设计 build_p0a_feature_panel_v4.py | feature_join_v4.py + driver script done (code only, 等 Optuna 完跑) | done code |
| 4 | run_v3_2_full_chain.py:97 silent bug (cmd 没传 --feature-panel v2 实际走 v1) | 整删 (v1/v2 chain 都 deprecated) | done commit a4b37574 |
| 5 | Codex round 21 实测 24-day Optuna 根因 (Phase 1-6 perf 没 wire 到 Optuna 脚本) | run_p0b_lightgbm_optuna_v4.py perf-wired (PreparedPanel + MedianPruner + per-trial persist) | done code |

### 4.C Cleanup (Codex 重排: P0→P2, 因 train script 仍硬编码 v1)

| 优先级 | 任务 | 状态 |
|---|---|---|
| P2 | feature_join_v1 (0 fn caller) — defer 等 train_p0b_lightgbm.py:119 / run_p1_ablation.py:82 默认迁 v3 | defer |
| P2 | feature_join_v2 (1 caller orphan chain) — defer 同上 | defer |
| P1 | paper_sim score_loader 3 → 1 主 loader + strategy param | 待 |
| P1 | paper_sim_*.yaml 12 → 3 (active base + conservative + experiment) | 待 |
| P1 | run_v3_2_full_chain.py:97 silent bug 修 (cmd 没传 --feature-panel v2) | 待 |
| P2 | 物理 DROP v3 panel leakage 残列 (inst_quality_* / sector_ret_*) | 等 Optuna 完 |

### 4.D 决策出口闭环 (codegraph audit P0 gap)

| 任务 | 状态 |
|---|---|
| `scripts/run_daily_decision_pipeline.py` 串 sync→panel→train→sim→champion→alert | 待 |

## Optuna 当前: PID 25088 — 性能问题决策中

- run_id: `p0b_optuna200_governance_v1_20260517T085523`
- 启动: 2026-05-17 08:55, 现 11:50 (2h55m)
- 进度: trial 1 window 16/16 (即将完成 trial 1)
- **实测速度**: trial 1 总 ~2h54m. 200 trials = ~24 天 (远超原 11 天估算)
- 模型: 92 features (governance v1 PIT clean), label fwd_cost_after_20d
- baseline RankIC: 0.0246 (governance v1 honest)
- 目标: 优化超参 看能否 push RankIC > 0.03

**Codex 实测根因 (round 21)**:
- Phase 1-6 perf 模块 wire 在 train_p0b_lightgbm.py, 不在 run_p0b_lightgbm_optuna_v3.py
- df.to_dict("records") 一次 14.5 min, 每 trial 切窗 31 min × 16 win 后 LGBM fit 2 min/win
- 加 MedianPruner 不生效因为 objective 没 trial.report() + should_prune()

**用户决策**: 上 GCP — Codex round 21 GCP 方案讨论中 (后台 a0737e36f10dc9294)

## 本 session 持续工作汇总 (2026-05-17 06:00 起, 56 commits push origin/main `58ebf777`)

| 阶段 | 输出 |
|---|---|
| Phase 4 features | 7 modules / 50 features / 41 tests pass |
| Codex CRITICAL fixes (round 19) | forecast_upside PIT winsorize + champion rank_ic 占位 |
| v4 panel | mart_p0a_feature_label_panel_v4 2.9M × 143 cols (229s build) |
| Optuna v4 perf-wired | PreparedPanel + MedianPruner + per-trial persist (vs v3 24 天) |
| Forecast EPS PIT | 2,374 stocks 首 snapshot (akshare 13-col 多年 EPS) |
| Forecast upside live | 2,313 stocks 入 mart_forecast_upside_live SHADOW |
| GCP Batch + GCS scaffolding | 12 文件 (Codex round 22) + setup_all.sh 一键 setup |
| paper_sim sizer ablation | yaml + driver 脚本 |
| Daily launchd cron | Mon-Fri 19:00 forecast EPS 自动 ingest |
| Cleanup orphans | v1/v2 module + v3.2 chain 删除 |
| Monitor tools | monitor_optuna_v4.py / run_post_optuna_v4_chain.sh |
| goal.md ledger | 持续维护含 P0 issues 跟优先级 |

## Optuna v4 进展 (12:11 启动)

- **PID 25088 (v3) 已 cancel** (in-memory trials 都丢, trial 0 +0.005 / trial 1 -0.029 远低于 baseline 0.0246, 损失低)
- **v4 panel built 229s** (3m49s): mart_p0a_feature_label_panel_v4 2,901,970 rows × 143 cols
- **v4 coverage audit**: mcap_decile 97.7% / beta_60d 97.6% / **sector_momentum 0%** (Codex round 20 警告: industry_pit 99.8% fallback, observed_snapshot filter 导致空) / survey 8.8% (v3 已有) / tom 100%
- **Optuna v4 PID 47508 启动** (n_trials=50 + MedianPruner + PreparedPanel + per-trial persist)
- **实测 (54m 后)**: trial 0 完成 mean_ic=0.0191 (低于 baseline 0.0246), score=-0.0297; trial 1 win 9/16 进行中
- **每 trial 实测 ~32 min** (16 windows × 2 min/win, LGBM 训练 bottleneck), 比 v3 80 min/trial 快 2.5x
- 估时修正: 50 trials × 32 min × pruner factor 0.6 = ~16h (vs v3 24 天)
- 监控: `tail -f data/audit/logs/optuna_v4_20260517T121145.log`
- 修 schema: ALTER mart_p1_optuna_trials ADD user_attrs_json + pruned_at_window (v4 callback 需)
- **15:24 trial 3 完成 mean_ic=0.0123 (低于 0/1/2, 持续下降)**
- **15:30 决定 cancel — 4 trials 全 < baseline 0.0246, Phase 4 features 不带 alpha 证据充分**
- **15:30 Retrain LGBM PID 63919 启动** (trial 1 best params, mart_p0a_feature_label_panel_v4, model_id=lgbm_v4_optbest_7fed34)
- 估时 retrain: ~30-60 min (单次 train, 非 walk-forward)
- **16:10 Retrain windows 1-14 完成 mean RankIC 0.0092, std 0.0504** (低于 baseline, 14/16 windows): -0.049/+0.003/+0.037/+0.119/+0.072/-0.002/+0.016/+0.022/+0.009/+0.040/-0.035/+0.004/-0.082/-0.025
- **16:12 用户指令 暂停所有计算 + 查 GCP project**
- **16:12 Kill retrain PID 63921** (windows 1-14 已 walk-forward 但未完, 16/16 中 14 done)
- **16:12 GCP project 确认**: gen-lang-client-0821344445 (ChunkyMonkey) — 空 (仅 Gemini API), gen-lang-client-0274784341 (Gemini API project) 有 e2-micro VM 太小用不了
- **16:18 待用户 confirm 创新 VM**: 在 ChunkyMonkey project 跑 setup_ssh_vm.sh — 需先 enable billing

## 2026-05-17 临时 critical issues 发现

### Data sync gap (v_price_kline_qfq market.duckdb)
- 2026-04-30 之前: ~5,150 codes/day (full universe)
- 2026-05-06: 5,202 codes (tdxhub_218.6.170.47:7709_raw_incremental 5101 + tdxhub 101)
- 2026-05-07-12: 仅 101 codes (tdxhub base, no incremental)
- 2026-05-13-15: 仅 32 codes
- 影响: forecast_upside_live close JOIN 仅 45/2313 stocks (2%); daily live trading 无法跑
- 不影响: Optuna v4 训练 (v3 panel cutoff 2026-04-13, 早于断点)
- 根因 (smoke test 2026-05-17): tdxhub raw_incremental servers (218.85.139.19 / 218.85.139.20 / 58.23.131.163 等 10 IP) **全部 TimeoutError**, 不只是 rate limit
- 待修 (下 session P0):
  1. 跟用户确认服务器列表是否变化
  2. 改 services/tdx_source.py 重新发现服务器 OR 换 akshare 一线源
  3. 重 sync 2026-05-07 ~ 2026-05-16 历史
  4. 修 cron daily sync 防回退

## 长期 v3.2 状态 (η+++++++ +45.4% baseline, 含 leakage 历史)

- [FAIL] +45.4% baseline 含 stage_optimal in-sample fit leakage (Codex acf48d35 标 CRITICAL)
- [OK] governance v1 框架部署完成 (yaml + sop + audit + check + lint + DELETE 16.5M leaked rows)
- [OK] Phase 3 honest verdict: RankIC=0.0246, ann=-65.5%, P3 FAIL (干净 PIT 实测)
- ⏳ Phase 4 alpha 提升: 50 new features (in progress) → 期望 RankIC 0.025 → 0.035+
- ⏳ score_rank_diff_v1 sizer 差异化仓位 (commit 71bb2189) — 待 paper_sim ablation

## 工作纪律

- 小步快跑, 每完成子项立即 commit + push (用户 [[feedback_git_commit_frequency]])
- 中文输出, 表格 > 段落, 不报喜不报忧
- Codex review gate: 每代码 commit 前 codex:rescue --model gpt-5.5 --effort xhigh
- PIT/leakage CRITICAL 不允许折中 (用户 [[feedback_codex_critical_no_compromise]])
- Phase 4 feature 模块拉完 → 待 Optuna 完成 → wire 进 panel → retrain → paper_sim ablation

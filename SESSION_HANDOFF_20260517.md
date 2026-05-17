# Session Handoff — 2026-05-17

Comprehensive status as of 14:38, 57 commits push origin/main `629133b0`.

## What's Done (57 commits)

### Phase 4 Feature Engineering
- 7 modules (50 features), 41 tests pass
  - time_of_month (7), market_cap_decile (6), industry_beta (4)
  - capital_flow (15, wrap PIT 858K), sector_momentum (11, PIT industry filter)
  - institution_survey (7), forecast_upside (6, pure-function PIT-safe)
- All wired into `mart_p0a_feature_label_panel_v4` (2.9M × 143 cols, 229s build)

### Codex CRITICAL Fixes (round 19-22)
- forecast_upside PIT winsorize (rolling quantile, not global)
- promote_champion rank_ic 占位 (real OOS from mart_p0b_walkforward_eval)
- v3 panel leakage cols excluded (inst_quality_* / sector_ret_*)
- v1/v2 module + orphan v3.2 chain deleted (verify 0 callers)

### Optuna v4 Perf-Wired (Codex Path Z)
- `run_p0b_lightgbm_optuna_v4.py`: PreparedPanel + MedianPruner + per-trial persist + governance enforce
- vs v3 24-day, estimated 22h (实测 trial-level 45 min/trial)

### Forecast EPS PIT Accumulation
- `ingest_profit_forecast_snapshot.py`: akshare 13-col 多年 EPS daily snapshot
- 首次 ingest: 2,374 stocks (2026-05-17), 100% this/next-year EPS coverage
- launchd plist Mon-Fri 19:00 自动

### Forecast Upside Live Preview
- `compute_forecast_upside_live.py`: 4-tier target_pe (self / industry / blend / consensus_pe)
- 实跑 2,313 stocks → mart_forecast_upside_live SHADOW (Top: 000528 +211% 上升空间)

### GCP Batch + GCS Scaffolding (Codex round 22)
- 13 files in `gcp/`: experiment_config.yaml / generate_jobs.py / run_rankic_experiment.py / pull_results_to_duckdb.py / Dockerfile / 5 shell scripts
- `gcp/setup_all.sh` 一键 setup (4 args)
- Architecture: Cloud Batch + GCS (per-experiment ephemeral VM), Mac mini = orchestrator

### Tools
- `monitor_optuna_v4.py`: ETA + baseline gap + top trials
- `run_post_optuna_v4_chain.sh`: 5-step post-Optuna chain (gate + retrain + ablation + KPI)
- `run_paper_sim_sizer_ablation.py`: equal vs score_rank_diff_v1 driver
- 2 paper_sim yaml configs (equal + rank_diff)

## What's Still Running

- **Optuna v4 PID 47508**: 2h27m elapsed, trial 3 in progress, 3 trials completed
  - Best mean_ic 0.0196 (-20.3% vs baseline 0.0246)
  - 平均 48.9 min/trial × 47 remaining = ~38h ETA
  - 监控: `PYTHONPATH=backend python backend/scripts/monitor_optuna_v4.py`
  - 日志: `data/audit/logs/optuna_v4_20260517T121145.log`

## What User Needs to Do

### Option A: GCP Setup (推荐用户当前路径)
1. `gcloud auth login` (一次, 浏览器)
2. Enable billing for project (GCP Console GUI 1-click)
3. Tell me 4 values: `PROJECT_ID BUCKET_NAME REGION EMAIL`
4. 我跑 `gcp/setup_all.sh` + Docker build + sync data + submit batch + pull results

### Option B: Continue Local Optuna v4 (~38h ETA)
- 不做任何事, Optuna v4 跑完后我接管 retrain + paper_sim
- 风险: 当前 3 trials 都低于 baseline, 可能 Phase 4 features 未带 alpha 提升

### Option C: Cancel + Lighter Optuna (--full off, 5-8h)
- 损失 2h27m 已跑进度 (3 trials 都没显著)
- 改 n_estimators 2000 → 300 (4x speedup), 估 5-8h 完
- 牺牲模型精度换速度

### Option D: Market sync gap fix (P0, 用户需调研)
- tdxhub server pool 10 IP 全 TimeoutError (服务器列表 stale)
- akshare push2his.eastmoney.com 在用户网络 block (确认)
- 用户需更新 tdxhub server 列表或找替代源 (qmt / 实时行情 vendor)

## Queue (按优先级)

| P | 任务 | 阻塞条件 |
|---|---|---|
| P1 | retrain LGBM with v4 panel (best Optuna params) | 等 Optuna v4 完 |
| P1 | paper_sim sizer ablation (equal vs rank_diff) | 等 retrain |
| P1 | promote champion (gate 0.0246 RankIC + paper_sim KPI) | 等 ablation |
| P0 | market sync gap fix | 用户调研 |
| P1 | GCP migration | 用户 gcloud auth + 4 values |
| P3 | forecast EPS PIT 累积数月后 backtest | 时间累积 |

## Critical PIT Risks Outstanding (Codex round 20 漏看)

- v3 panel 物理表仍含 inst_quality_* / sector_ret_* leakage 残列 (训练已 exclude 但物理未 DROP) — 等 Optuna 完 cleanup_leakage_data.py 物理 DROP
- mart_stock_industry_pit 99.8% fallback — sector_momentum 实测 0% 覆盖 (LGBM ignore)

## 文件清单

新增 (本 session):
- `backend/services/features/{time_of_month,market_cap_decile,industry_beta,capital_flow,sector_momentum,institution_survey,forecast_upside}.py`
- `backend/tests/features/test_*.py` (7 files)
- `backend/services/labels/feature_join_v4.py`
- `backend/scripts/build_p0a_feature_panel_v4.py`
- `backend/scripts/run_p0b_lightgbm_optuna_v4.py`
- `backend/scripts/ingest_profit_forecast_snapshot.py`
- `backend/scripts/compute_forecast_upside_live.py`
- `backend/scripts/run_paper_sim_sizer_ablation.py`
- `backend/scripts/run_post_optuna_v4_chain.sh`
- `backend/scripts/monitor_optuna_v4.py`
- `backend/scripts/launchd/com.chunkymonkey.forecast_eps.plist`
- `backend/config/paper_sim_ml_score_governance_v1_rank_diff.yaml`
- `gcp/*` (13 files Codex round 22 + 1 setup_all.sh)
- `goal.md`
- `SESSION_HANDOFF_20260517.md` (this file)

删除:
- `backend/services/labels/feature_join.py` (v1 module, 0 callers)
- `backend/services/labels/feature_join_v2.py` (v2 module, orphan)
- `backend/scripts/run_v3_2_full_chain.py` (silent bug + deprecated)

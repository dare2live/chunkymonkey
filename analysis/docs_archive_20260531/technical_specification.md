# ChunkyMonkey Technical Specification

> Status: reference, not the current execution plan. For active priorities use
> `goal.md` and `docs/implementation_plan.md`; for non-negotiable rules use
> `docs/PROJECT_CONSTITUTION.md`.

## 1. ARCHITECTURE

**Authoritative operating path** (from goal): `raw data -> formula signals + PIT factors -> Optuna -> mart -> paper_sim selector -> simulate_trade -> NAV/KPI`. 

**Layer model (from engineering rules summary):**

| Layer | Boundaries | Enforced artifacts |
|---|---|---|
| L0 Infra | calendars, data sources, policy, pipeline manifests, storage retention | `backend/config/pipeline_performance_policy.yaml`, `backend/config/data_sources.yaml`, `backend/config/gcp_policy.yaml`, `backend/config/storage_retention.yaml`, `backend/config/panel_pipeline_manifest.yaml`, `backend/config/paper_sim_config.yaml` |
| L1 Formulas | formula bank configs + bank adapters + preflight validation | `backend/config/formula_*.yaml`, `backend/config/field_dictionary.yaml` |
| L2 Signals | panel build, PIT joins, candidate universe filtering | `backend/config/panel_pipeline_manifest.yaml`, `backend/config/field_dictionary.yaml`, `backend/config/recommendation_universe.yaml`, `backend/config/universe_rules.yaml` |
| L3 Strategy | portfolio policy, Optuna life-cycle, paper_sim policy family | `backend/config/optuna_config.yaml`, `backend/config/model_search.yaml`, `backend/config/paper_sim_*.yaml`, `backend/config/pricing_label_policy.yaml` |
| L4 UI | recommendation retrieval, status + evidence views | `analysis/workflow_checkpoint.md`, `goal.md`, `SESSION_HANDOFF.md`, `assets/js/app.js`, stock graph APIs (from index)` |

```text
Data -> panel manifest DAG -> mart tables -> paper_sim mode configs -> execution simulator -> results registry -> gate -> promote/kill
```

| Gate rule | Location |
|---|---|
| PIT correctness | `factor/feature policy` + `pricing_label_policy` + `selection` keys |
| No hardcode, YAML-driven | project-wide coding policy + all strategy thresholds in YAML |
| Audit-first evidence | `analysis/*` + `goal.md` + `SESSION_HANDOFF.md` |

## 2. MODULE MANAGEMENT

| Module | Scope | Owner config |
|---|---|---|
| Data registry & universe | stock_code, trading calendar, universe filters | `data_sources.yaml`, `universe_rules.yaml`, `recommendation_universe.yaml`, `pricing_label_policy.yaml` |
| Formula pipeline | formula parameter families and signal keys | `formula_*.yaml` family |
| Panel + mart build | source DAG + dependency timing + rebuild mode | `panel_pipeline_manifest.yaml` |
| Optimization | run templates, constraints, search space, outputs | `optuna_config.yaml`, `model_search.yaml` |
| Strategy simulation | paper_sim modes and selector/exit/risk params | `paper_sim_*.yaml`, `paper_sim_config.yaml` |
| Governance & policy | train/validation/promotion policy | `pricing_label_policy.yaml`, `field_dictionary.yaml`, `pipeline_performance_policy.yaml` |
| Infrastructure policy | GCP spend, budget, machine, explicit latch | `gcp_policy.yaml` |
| Retention & protection | protected artifacts, delete protections | `storage_retention.yaml` |

**Module invariants (summary rules):**
- single DuckDB writer
- connection via `services/duck_adapter.connect`
- never `duckdb.connect` in new code paths
- YAML-first: no hardcoded thresholds in business logic
- layer-local config keys only; cross-layer coupling via manifest/config, not inlined globals

## 3. CODE WRITING WORKFLOW

```yaml
workflow:
  branch:
    task_scope: small + layer-local
    evidence: docs + checkpoints
  changes:
    - config-first edits
    - add/adjust policy gates explicitly
    - update checkpoint docs if execution state changes
  pre_commit:
    - project-index-sync
    - rule-compliance
    - self-check
    - ruff
    - no-emoji
  guardrails:
    - codex_review_gate: required before commit (except typo/markdown/rename)
    - safe_commit.sh required (no raw commit)
    - git status check before edits (per AGENTS)
  large_change:
    - codegraph status -> codegraph sync -> codegraph context/query
    - complexity scan
```

| Required follow-up | Notes |
|---|---|
| Config/schema change | `goal.md` + `SESSION_HANDOFF.md` + `analysis/workflow_checkpoint.md` update when state moves |
| New tables/views | add/adjust in manifest + dictionary + manifest preflight gates |
| Promotion or doc-only change | still needs checkpoint evidence chain |
| Testing | only where behavior/claim changes (no blanket requirement in spec) |

## 4. DATA PIPELINE

```yaml
# Confirmed manifest DAG (depth order)
pipeline:
  - depth: 1
    output: fact_alpha158_panel
  - depth: 2
    output: mart_p0a_label_panel
  - depth: 3
    output: mart_p0a_feature_label_panel_v3
  - depth: 4
    output: mart_p0a_feature_label_panel_v4
  - depth: 5
    outputs:
      - mart_p0b_oos_predictions
      - mart_sniper_score_daily
      - mart_institution_score_daily
```

| Stage | Inputs | Policy control |
|---|---|---|
| Source sync | `v_price_kline_qfq`, `fact_risk_factors`, `fact_capital_flow_pit_daily`, `fact_sector_momentum_daily`, etc. | `data_sources.yaml`, `calendar` gates in manifest |
| Build | `rebuild_p0a_label_panel.py`, `build_p0a_feature_panel_v4.py`, retrain lambdamart v6 | `panel_pipeline_manifest.yaml` rebuild modes |
| Feature / score tables | `mart_p0a_feature_label_panel_v4`, `mart_p0b_oos_predictions`, `mart_sniper_score_daily`, `mart_institution_score_daily` | preflight gate requirements in manifest |
| Mart outputs for strategy | `mart_per_stock_stage_strategy_optimal`, `mart_per_stock_strategy_optimal`, `mart_per_formula_stage_optimal`, `mart_ensemble_optimal` | `optuna_config.output`, `field_dictionary.yaml` |

`panel_manifest.preflight_gates` requires date coverage and row thresholds before build; this is a blocking gate.

| Calendar policy | `kline_write_close_hour: 15`, `kline_write_close_minute: 5`, `default_close_hour: 16` |
|---|---|---|
| PIT behavior | `data_sources.kline_daily.require_fallback_lineage: true`, `max_primary_lag_trading_days: 1` |

## 5. DATA VALIDATION

| Domain | Validation policy |
|---|---|
| PIT rule | `selector` only uses `built_at <= t` and built-at fields are part of contract (`fact_signal_context.built_at` noted in dictionary) |
| Data quality | `pricing_label_policy.yaml` explicit gates: calendar preflight, no silent nulls, no missing trade keys, explicit duplicate handling |
| Universe correctness | `recommendation_universe.yaml` and `universe_rules.yaml` filters (`board_prefixes`, delisted/no-trade windows, `tdxhub` kline truth source) |
| Feature governance | `field_dictionary` units/null/outlier policies + PIT conventions |
| Outlier/anomaly | `rankic > 0.3`, `oos_sharpe > 5`, `win_rate > 0.95`, annual return > 100% marked as leakage (engineering rules summary)
| Strategy split | walk-forward required in optimization and evaluation |
| Coverage gates | `field_dictionary` explicitly tracks table coverage and row counts per table |

```yaml
validation_focus:
  preflight: panel_pipeline_manifest.preflight_gates
  labeling: pricing_label_policy.yaml
  trading: pricing_label_policy.universe and tradability
  policy: pipeline_performance_policy + no-silent-null
```

**OOS fields** (minimum contractual family): `oos_sharpe`, `oos_win_rate`, `oos_avg_ret`, `oos_n_traded` (from mart dictionaries), and `oos_period_*` per engineering rules summary.

## 6. BACKTESTING

| Mode config | Key selectors | Default hard gate |
|---|---|---|
| `paper_sim_config.yaml` | `selection.mode=backtest`, tier gate, liquidity gate | `data.start_date`, `benchmark`, swap + risk caps |
| `paper_sim_ml_score*.yaml` | `selection.mode=ml_score`, `ml_score_model_id`, `candidate_source` | same strategy/exit/swap/risk blocks as shared family |
| `paper_sim_formula.yaml` | formula ranker and resonance params | `walk_forward_mode` in backtest, auto tx cost from paper_sim_config |
| `paper_sim_hybrid.yaml` | `hybrid_model_id`, `hybrid_w_ml`, `hybrid_q60_min_stage` | shared risk/tx gate |
| `paper_sim_ensemble.yaml` | `ensemble_alphas`, `regime_gate`, `per_stock_stage` | `validation` + anti-churn + robustness + ablation thresholds |
| `paper_sim_cross_formula.yaml` | cross-formula ablation entrypoints | `formula_whitelist`, stage-based ordering |

**Cost/exit framework (shared):**
- commission, stamp duty, transfer fee, exchange fee, slippage, large-order surcharge
- default `risk.daily_dd_warning`, `risk.max_dd_hard_stop`, `risk.hard_stop_freeze_days`
- `swap` with severity/gap/holding constraints and per-day max swaps

**KPI checks (from configs):**
- `annual_return_min`, `max_dd_min`, `excess_vs_hs300_min`, `monthly_win_rate_min`
- anti-churn: holding days, turnover, tx_cost ratio, swap uplift
- robustness med/p25/segment gates; ablation uplift and sensitivity sweeps

## 7. FORMULA LIFECYCLE

**Lifecycle (authoritative):**

```yaml
YAML config -> Search space -> local test -> GCP Optuna -> Evaluate -> Promote/Kill
```

| Stage | Config evidence |
|---|---|
| Authoring | `formula_*.yaml` + `field_dictionary` + feature manifests |
| Discovery/space | `model_search.yaml` + `optuna_config.search_space` |
| Pre-test | `paper_sim` backtests + local validation splits |
| Remote optimization | `gcp_policy.yaml` + `gcp` scripts + Optuna (`min_n_trials:50`, `max_n_trials:500`, `require_sampler_seed`)|
| Evaluation | `validation` blocks in paper_sim configs + anomaly/ablation gates |
| Promotion | `pricing_label_policy` + `champion` policy flags + gate outcomes |

**Optuna guardrails:**
- walk-forward required: `expanding_monthly` default, train windows min 6 months
- reproducibility via fixed seed requirement
- hard caps for unrealistically good metrics (realism bounds)
- objective family based on rank/cost/robustness mix in `optuna_config.composite`

## 8. CONFIGURATION

| File | Main purpose | Key top-level families |
|---|---|---|
| `backend/config/paper_sim_config.yaml` | strategy default base profile | `portfolio`, `selection`, `exit`, `swap`, `risk`, `validation`, `data` |
| `backend/config/paper_sim_*.yaml` | strategy variants | `portfolio`, `selection`, `exit`, `swap`, `risk`, `tx_cost`, `validation`, `data` |
| `backend/config/optuna_config.yaml` | optimization policy and constraints | `governance`, `walk_forward`, `search_space`, `constraints`, `execution`, `output` |
| `backend/config/model_search.yaml` | ordered research tasks and runtime schedule | `defaults`, `ranker_policy`, `research_schedule` |
| `backend/config/gcp_policy.yaml` | GCP admission and cost governance | `mode`, `budget`, `vm`, `usage_policy`, `monitoring` |
| `backend/config/panel_pipeline_manifest.yaml` | data DAG and rebuild contract | `sources`, `pipelines`, `preflight_gates`, `known_gaps` |
| `backend/config/pricing_label_policy.yaml` | pricing, tradability, label, evaluation policy | `announcement_policy`, `signal_policy`, `risk_policy`, `promotion_gate`, `reproducibility_policy` |
| `backend/config/pipeline_performance_policy.yaml` | runtime and long-job policy | `pipeline_duration_budgets_s`, heartbeat, long-run flags |
| `backend/config/storage_retention.yaml` | lifecycle protection and delete policy | protected model/panel/artifact definitions |
| `backend/config/field_dictionary.yaml` | canonical schema/data-contract dictionary | tables, fields, null/outlier conventions |

**Rule codification:** all strategy thresholds and routing weights must be externalized to YAML; avoid embedded constants in strategy execution paths.

## 9. MAINTENANCE

| Duty | Required process |
|---|---|
| Project state | keep `goal.md`, `SESSION_HANDOFF.md`, `analysis/workflow_checkpoint.md` synced on state change |
| Daily ops | follow `SESSION_HANDOFF` snapshot usage and resume commands |
| Governance evidence | never delete validated artifacts without replacement or archive path |
| GCP hygiene | `CHUNKYMONKEY_GCP_EXPLICIT_OK=1` required; cost policy from `gcp_policy.yaml`; alert/monitor before resume |
| Repository hygiene | remove scratch outputs; keep evidence artifacts named and referenced |
| Recovery readiness | run checkpoints before/after long jobs; respect continuation from handoff |

**Release-readiness checklist (non-exhaustive):**
- codegraph sync complete
- complexity scan on substantial edits
- pipeline state and checkpoint docs updated
- 3-doc suite coherent (goal + handoff + checkpoint)
- post-change retention policy checked
- explicit `safe_commit.sh` before merge

## 10. EXTENSION

**Safe extension path:**

| Extension | Minimum edits |
|---|---|
| New formula family | add `formula_*.yaml`, include in formula registry/manifest, add smoke + preflight gate evidence |
| New selection strategy | add a variant under `paper_sim_*.yaml` (portfolio/selection/validation scoped) |
| New model candidate | add `model_id` + policy hash and outputs table in `optuna`/result mart workflow |
| New feature source | add source entry in `panel_pipeline_manifest.yaml`, `field_dictionary.yaml`, upstream checks |
| New policy change | update `pricing_label_policy.yaml` + corresponding manifest gate + checkpoint note |

**Hard extension constraints:**
- no raw SQL or service changes that bypass PIT policy
- no hardcoded thresholds outside config
- no new table names without dictionary/manifest update
- no new path outside confirmed modules unless documented in `goal.md`
- if requirement is not in checked config/project-index sources, use `TBD — see goal.md`

## 11. HANDOFF

| 规则 | 说明 |
|---|---|
| 单一真相源 | `goal.md` 是唯一的状态文档. handoff 文件只是指针 ("看 goal.md §日期") |
| 实时更新 | 做完一个子任务就更新 goal.md, 不攒到 session 末尾 |
| 必记内容 | 决策 + 原因, 关键数字 (score/行数/成本), 失败尝试 + 根因, 用户原话, 下一步具体操作 |
| 工具验证 | session 结束前跑 `scripts/session_handoff_audit.py` — 扫 commits vs goal.md 覆盖度 |
| 三重触发 | Stop hook (正常结束) + SessionStart hook (兜底查上次) + 手动跑 |
| 完整性标准 | 自动: 主题覆盖 + 文件提及. 人工: 5 项 checklist (next step 具体? 数字记录? 失败原因? 用户指令? 能接着干?) |

## 12. DEVELOPMENT SCHEDULING

| 优先级 | 定义 | 例子 |
|---|---|---|
| P0 | 阻塞用户核心目标, 当天做 | 300616 公式优化, 数据修复 |
| P1 | 影响下一步但不阻塞当前 | 前端接入, GCP 重跑 |
| P2 | 技术债, 有空做 | God module 拆分, YAML 合并 |

| 原则 | 说明 |
|---|---|
| Grill 前置 | 执行前 `/engineering-discipline` Step 4 拷问 |
| 成本估算 | GCP 任务必须算 wall time + $ 成本 |
| 依赖排序 | 先基础设施后业务, 先数据后公式, 先验证后跑批 |
| 中断恢复 | goal.md 有足够细节让新 session 不问用户就接着干 |
| 不攒批 | 每完成一个子任务 commit + push + 更新 goal.md |

## 13. PROGRESS TRACKING

| 机制 | 怎么用 |
|---|---|
| goal.md | 每个任务有: 状态 (DONE/进行中/待做) + 关键数字 + 下一步 |
| commit message | 含数字证据 ("28/28 tests PASS", "score=53.04") |
| analysis/ | 分析结果存 JSON/MD, goal.md 引用不复制 |
| 完成标准 | 代码写完 ≠ 完成. 完成 = 数据端到端验证通过 + 审计通过 + 用户能看到结果 |

## 14. COLLABORATION (Claude + Codex)

| 场景 | 规则 |
|---|---|
| 何时派 Codex | 代码 review, 大范围搜索, 架构评审, 第二意见 |
| 分工 | Claude 做主线, Codex 做并行研究/review. 不重复做同一件事 |
| 合并 | Codex 结果必须 review 后合并, 不盲信 |
| 超时 | Codex > 30 min 无新 command → cancel + resume |
| Review gate | commit 前 Codex review (非 trivial 改动), 或显式 bypass + 原因 |

## 15. QUALITY GATES

| Gate | 触发时机 | 检查数 | 不通过 |
|---|---|---|---|
| `backtest_preflight` | 回测前 | 8 项 (universe/板块/成本/新鲜度/walk-forward/PIT/code-scan/sample) | raise |
| `plan_validator` | 跑批前 | 8 项 (search-space/trial/runnable/cost/param-scope/sample-size/board/output) | exit 2 |
| `data_audit` | sync 后 | 7 项 (kline 完整/一致/板块/日期/量价/smartmoney/跨表) | raise (strict) 或 log (warn) |
| `preflight_gcp_launch` | GCP 启动前 | 7 项 (VM/SSH/remote-plan/remote-data/grill/leakage/budget) | exit 1 |
| `grill_stamp` | GCP 脚本 Step 0 | 文件存在检查 | exit 5 |
| `code_leakage_scan` | preflight 内 | 静态扫 bank 源码 future-index | FAIL |
| `session_handoff_audit` | session 结束 | 主题覆盖 + 文件提及 + 人工 checklist | WARNING |
| `/engineering-discipline` | 任何改动前 | 6 步 (第一性原理/奥卡姆/教训/拷问/代码/架构) | 人工判断 |

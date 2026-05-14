# PLAN_V3.md — v3 退役 v2 实施计划

> **创建**: 2026-05-14
> **状态**: 待用户拍板
> **背景**: paper_sim per_stock_stage ceiling test (-26.5%) 证伪 v2 ensemble 路线; 用户重新明确产品愿景 = "机构跟随是手段, 公式独立也行, 终极目标资产增值"; 退役 v2 ensemble 拼权重, 上位 v3 两路合并架构.

---

## 0. 产品愿景 (锚)

**终极目标** (用户原话): "短期内资产最大幅度增值不缩水"

**3 PASS 标准**:
1. 年化 ≥ 30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0
4. (补) 月胜率 ≥ 55%

**baseline**: 2023-01-03 起 100 万本金, HS300 benchmark, 不算现金利息.

**当前实测 baseline (2026-05-14, 含真实 tx_cost)**:
- paper_sim 13-alpha hp=15: ann **+3.78%** / mdd -30.1% / sharpe +0.29  ← 距目标 -26pp
- per_stock_stage ceiling: ann **-26.5%** ← 路线证伪
- portfolio_walk_forward +45.4% / +205.4% 超额 ← 理想化, 不算 tx_cost/T+1/流动性 (Rule 9 反例)

---

## 1. v3 两路合并架构

```
┌──────────────────────────────────────────────────────────┐
│  A 路: 机构跟随 (用户产品最初愿景)                       │
│    机构事件 (fact_institution_event/lhb_event)           │
│    ↓ JOIN mart_institution_industry_stat                 │
│      (擅长判定: win_rate / avg_gain / sample 阈值上 yaml │
│       + Optuna 寻最优阈值)                               │
│    ↓ 触发 institution_follow formula (T+1 vwap 入场)     │
└──────────────────────────────────────────────────────────┘
                            ↘
┌──────────────────────────────────────────────────────────┐
│  B 路: 公式独立                                          │
│    全市场扫 fact_technical_trigger 当日触发              │
│    ↓ (turtle_20/55, macd_above/below, reversal_1w/1m, ma)│
│    ↓ JOIN mart_per_stock_stage_strategy_optimal_v3       │
│      (per-stock × formula × stage 最优 params, OOS)      │
└──────────────────────────────────────────────────────────┘
                            ↘
                ┌──────────────────────┐
                │  Optuna 学习两路权重 │
                │  alpha_w_inst        │
                │  + alpha_w_formula   │
                │  (∑ = 1.0)           │
                └──────────────────────┘
                            ↓
                ┌──────────────────────────────┐
                │  三目标 + 约束综合排序       │
                │  max(ann_ret)                │
                │  min(|max_dd|)               │
                │  min(avg_hold_days)          │
                │  constraint: sharpe>0        │
                │           OR calmar>0.5      │
                └──────────────────────────────┘
                            ↓
                    top 5 → paper_sim 5 仓
                    (tx_cost + T+1 + 流动性 真实)
                            ↓
                    mart_paper_sim_kpi → 复盘 + 再训练
                            ↓
                    UI: 机构 / 股票 / 公式 视图
```

---

## 2. 四类清单

### 退役

| 项 | 原因 | 动作 |
|---|---|---|
| `paper_sim_ensemble.yaml` 13-alpha `ensemble_alphas` 拼权重 | 跟用户产品愿景错位 (拼权重不是机构跟随+公式) | yaml 删 ensemble_alphas 段, 保留 PIT alpha 表给 Optuna feature 用 |
| `selector.load_today_candidates_ensemble` | ensemble_score 排序模式 | 函数标 deprecated, paper_sim mode 默认切 v3 |
| `selection.per_stock_stage.enabled = true` | ceiling test -26.5% 证伪 | yaml 改 false + 注释解释; mart 表保留作历史 |
| `Phase ψ.δ.1 sector_pred` 14th alpha | 实测 hurt 21pp | weight=0 已 disable, v3 path 不接入 |
| `selection.vol_aware.enabled` | 拍脑袋 sigma 参数 (Rule 6 反例) | 默认 off 锁住 |
| `portfolio_walk_forward +45.4%` 作主要 KPI | 理想化, 不含 tx_cost | goal.md 顶部标"理想化基准 — 不作决策依据", 移到 §历史 |

### 保留 (按用户拍板 v3 退役边界)

| 项 | 价值 |
|---|---|
| `mart_per_stock_stage_strategy_optimal` (17,663 行) | 寻优表是 v3 B 路的核心数据资产 |
| `formula_engine` 7 公式 + `candle_pattern/` 形态识别 | v3 B 路核心 |
| `event_simulator.py` | production-ready, v3 A 路核心引擎 |
| `mart_institution_industry_stat` (4,807) / `mart_institution_profile` (231) | v3 A 路 "机构擅长"核心表 |
| `fact_alpha158_panel` (4M 行) | v3 P1b 接入 |
| Optuna governance + walk_forward + audit_end_to_end | 跨 Phase 基础设施 |
| paper_sim driver / exit_rules / tx_cost / swap_rules | 引擎主体, 只换 selector |

### 改造

| 项 | 改造点 |
|---|---|
| `composite.py` + `optuna_config.yaml.composite` | 7 目标 → 3 目标 (ret/dd/hp) + 约束 (sharpe>0 OR calmar>0.5) |
| `selector.py` | 新增 v3 mode 函数 `load_today_candidates_v3` (两路合并) |
| `paper_sim_ensemble.yaml` | 默认 mode 切 v3, 加 alpha_w_inst/alpha_w_formula |
| `FormulaBase.compute_signals` | 加 `events_by_code: Optional[dict] = None` 参数 |
| 数据覆盖率 stage=1/3/4 偏低 (50/191/89 行) | 找上游写坏路径 backfill, **不放松 n_traded 阈值** |

### 新建

| 项 | 用途 |
|---|---|
| `services/formula_engine/institution_follow.py` | institution_follow formula 实现 |
| `services/optimization/objectives_v3.py` | 3 目标 + 约束实现 |
| `services/paper_sim/selector_v3.py` (或同文件加函数) | v3 mode dispatch |
| `mart_per_stock_stage_strategy_optimal_v3` 新表 | 3 目标 objective 寻优结果 |
| `mart_institution_excellence` (可选) | 机构擅长行业判定结果 (阈值 + Optuna 寻优后) |
| `backend/scripts/optimize_per_stock_stage_strategy_v3.py` | v3 寻优脚本 (3 目标 + 候选池精调) |
| `backend/scripts/build_institution_follow_signals.py` | 构建 institution_follow 历史信号 |
| `routers/v3_views.py` 接线 | 机构/股票/公式三视图 API |

---

## 3. Phase 路线图

### P0a — Git 清理 (30 min, 无依赖)

**动作**:
1. 读 working tree 未提交模块 (`deflated_sharpe.py` / `data_governance/` / `diagnose_alpha_ic.py` / `check_deflated_sharpe.py`) 看是 v2 残留还是 v3 资产; 报告用户决定 commit / 丢弃
2. `git add design/` (Chunky Monkey v3.html + 16 个 v3-*.jsx) 一次性入 git
3. commit 当前 4 modified (`optuna_config.yaml` / `backfill_risk_factors_history.py` / `config.py` / `governance.py`) — message 标 "Phase ψ in-flight, pre-v3 freeze"
4. `git push origin feature/reversal-factor`
5. 创建 PR `feature/reversal-factor` → `main`, merge (用户授权)
6. 删 5 多余分支: `codex/chunkymonkey-data-champion-20260506`, `claude/bold-kowalevski-a7d9fc`, `claude/loving-sutherland-ff7b74`, `claude/mystifying-benz-29d9e0`, `codex/chunkymonkey-data-champion-20260506` (本地+远端)
7. main 上新开 `feature/v3-arch` 作 v3 工作分支

**Acceptance**:
- [ ] `git status` 0 modified, 0 untracked (除 data/ 数据库)
- [ ] `git branch -a` 只剩 main + feature/v3-arch + remotes/origin/{main, feature/v3-arch}
- [ ] design/ 全部入 git (16 v3-*.jsx + Chunky Monkey v{2,3}.html)
- [ ] HANDOFF.md / goal.md 已 push 到 main

**回滚**: 删错分支 → `git reflog` 找 SHA + `git update-ref refs/heads/<name> <SHA>` 恢复

### P0b — Composite 改 3 目标 (3-4 hr 含跑批)

**动作**:
1. 改 `services/optimization/composite.py`:
   - 新 `CompositeWeightsV3 = {ret_w, dd_w, hp_w}`, ∑=1.0
   - 新 `composite_score_v3(obj, weights)` 函数:
     ```
     raw = obj.annual_ret * ret_w - abs(obj.max_dd) * dd_w - obj.avg_hold_days/60 * hp_w
     sample_w = log(1 + obj.n_traded)
     # 约束: 不通过 raw = -inf
     if obj.sharpe <= 0 and obj.calmar <= 0.5: raw = -1e9
     return raw * sample_w
     ```
2. 改 `optuna_config.yaml`:
   - 加 `composite_v3:` 段 (ret_w=0.5 / dd_w=0.3 / hp_w=0.2, 约束阈值)
   - 加 `objective_version:` 字段, default = "3obj_ret_dd_hp_v1"
3. 单测加: `test_composite_v3.py` 覆盖 (a) 高 ret 但 dd 极差→ 排序低 (b) 约束触发返回 -inf (c) 权重 ∑=1.0 校验
4. 写 `optimize_per_stock_stage_strategy_v3.py`:
   - 复用现 `optimize_per_stock_stage_strategy.py` 主体
   - 改 objective 调 `composite_score_v3`
   - 入新表 `mart_per_stock_stage_strategy_optimal_v3` (同 schema + `objective_version` 列)
5. 数据覆盖率 self-check: stage_filter 各分段统计 n_signals, 若 stage=1/3/4 < 500 个 signal → backfill (拉数据不跳过)
6. 8 workers fork 跑 v3 寻优 (~60 min), 入 `_v3` 后缀新表
7. 跑 audit_end_to_end → 0 FAIL
8. 跑 1402 baseline pytest → 全绿

**Acceptance**:
- [ ] `composite_v3` 单测 ≥ 3 cases 全绿
- [ ] `mart_per_stock_stage_strategy_optimal_v3` 行数 ≥ 15,000 (跟旧版同量级)
- [ ] 新表 OOS sharpe > 0 行数占比 ≥ 旧版 (用 SQL 对比)
- [ ] audit_end_to_end 0 FAIL, 1402 pytest 全绿
- [ ] commit message 含 "Phase v3.P0b: composite 3-obj + ret/dd/hp + 约束 sharpe>0|calmar>0.5; 实测 17K rows backfill; OOS sharpe > 0 占比 +X%"

**回滚**: 新表 DROP, composite_v3 文件删, yaml 还原 — 旧表 17K 行不动

### P0c — institution_follow 包为 formula (1-2 day)

**动作**:
1. 扩展 `FormulaBase.compute_signals` 加 `events_by_code: Optional[dict[str, list[dict]]] = None`
   - 7 现有 formula 不传, 不受影响
   - 加单测验证向后兼容: 旧 formula 不传 events_by_code 应 work
2. 新建 `services/formula_engine/institution_follow.py`:
   - `class InstitutionFollowFormula(FormulaBase)`
   - metadata.formula_id = "institution_follow"
   - 内部读 `fact_institution_event` 或外部传入 events_by_code
   - 擅长判定: JOIN `mart_institution_industry_stat` WHERE win_rate_90d ≥ threshold_w AND sample_events ≥ threshold_s AND avg_gain_90d ≥ threshold_g
   - 阈值 threshold_w / threshold_s / threshold_g 上 yaml `formula_engine.institution_follow.thresholds`
   - 加 Optuna 寻优可选 (P0d 时启用)
3. 新建 `backend/scripts/build_institution_follow_signals.py`:
   - 扫历史 fact_institution_event, 按擅长判定生成 fact_technical_trigger 行 (formula_id=institution_follow)
   - 入 `fact_technical_trigger` (现有表), `formula_variant` = institution_follow (单 variant 起步)
4. 加 institution_follow 进 P0b v3 寻优 pipeline (跑 8 workers fork 1 hr)
5. 单测:
   - test_institution_follow_signal_gen (5+ cases: 擅长判定 / 阈值边界 / 重复 event 去重 / 多机构同股 / 行业 fallback)
   - test_event_simulator_with_institution_follow (集成测试)
6. 数据覆盖率 self-check: stage_filter × institution_follow 行数 ≥ 200, 否则 backfill

**Acceptance**:
- [ ] FormulaBase 兼容性单测全绿 (7 现有 formula 不受影响)
- [ ] InstitutionFollowFormula 单测 5+ cases 全绿
- [ ] `fact_technical_trigger` 含 institution_follow 行 ≥ 5000 (按擅长判定生成)
- [ ] `mart_per_stock_stage_strategy_optimal_v3` 含 formula_id=institution_follow 行 ≥ 300
- [ ] audit_end_to_end 0 FAIL (含新 formula 检查项)
- [ ] commit message 含 "Phase v3.P0c: institution_follow as 8th formula; 擅长判定 yaml + Optuna; 阈值寻优实测 X trials; mart_v3 入 X 行"

**回滚**: institution_follow.py 删 + fact_technical_trigger DELETE formula_id=institution_follow + mart_v3 DELETE — FormulaBase 改动需要保留 (向后兼容, 不影响其他)

### P0d — selector v3 mode (1 day)

**动作**:
1. 新建 `selector.load_today_candidates_v3` 函数:
   - A 路: 当日 fact_institution_event JOIN excellence 判定 → institution_follow 候选
   - B 路: 当日 fact_technical_trigger (排除 institution_follow, 即"公式独立路径") → 公式候选
   - JOIN mart_per_stock_stage_strategy_optimal_v3 取每候选 best params (按 stock × formula × stage)
   - 两路 score 加权: `final_score = alpha_w_inst * score_A + alpha_w_formula * score_B` (∑=1.0)
   - 加权后三目标排序 top 5
2. yaml `paper_sim_ensemble.yaml`:
   - mode: "v3" (新增)
   - 删 ensemble_alphas 段 (退役)
   - alpha_w_inst: 0.5 / alpha_w_formula: 0.5 (P0d 默认, P0e 实测后 Optuna 寻优)
3. 流动性 / T+1 滑点 / max_positions=5 / regime gate 保留
4. 单测:
   - test_selector_v3_two_path_merge (两路合并去重)
   - test_selector_v3_regime_gate
   - test_selector_v3_liquidity_filter
   - test_selector_v3_top_n
5. paper_sim integration smoke (5 天 walk-forward) 跑通

**Acceptance**:
- [ ] selector_v3 单测 4+ cases 全绿
- [ ] paper_sim smoke run 5 天跑通, KPI 入 mart_paper_sim_kpi
- [ ] audit_end_to_end 0 FAIL
- [ ] commit message 含 "Phase v3.P0d: selector v3 mode 两路合并; yaml alpha_w 寻优起步"

**回滚**: yaml 改回 mode=ensemble + 删 selector_v3 函数 — paper_sim 引擎主体不动

### P0e — paper_sim v3 验证 (30 min + 完整跑 ~30 min)

**动作**:
1. paper_sim 完整 walk-forward 跑 v3 mode (2024-04-01 → 2026-05-12, 509 天)
2. KPI 入 mart_paper_sim_kpi (sim_run_id=v3_baseline_YYYYMMDD_HHMMSS)
3. 对比表:
   - v2 baseline 13-alpha hp=15: ann +3.78% / mdd -30.1%
   - v2 ceiling per_stock_stage: ann -26.5%
   - **v3 baseline 两路合并 50/50**: ann ? / mdd ?
4. Optuna 寻 alpha_w_inst / alpha_w_formula 最优 (50 trials, 各 30 min smoke run)
5. 用户拍板: 若 v3 ann ≥ +10%/mdd ≥ -25% → 继续 P1; 若 < 0 → 暂停 P1 alpha158, 改先 P1a 数据覆盖率补强

**Acceptance**:
- [ ] paper_sim 完整跑通, KPI 入库
- [ ] v3_baseline 对比 v2 baseline 数字表入 PLAN_V3.md §6 KPI Tracking
- [ ] Rule 9.1 真金白银 self-check: 数字含 leakage / 估算 / 假设吗? 答 "否"
- [ ] commit message 含 "Phase v3.P0e: paper_sim v3 baseline ann=X% mdd=Y% sharpe=Z; alpha_w 寻优最优 inst=A formula=B; 对比 v2 baseline 差/超 Cpp"

**回滚**: 不需要 — P0e 是测量 + 报告, 不改代码

**P0e 是 P0 阶段的 user checkpoint**. 数字出来后用户拍板 P1 优先级.

### P1a — 数据覆盖率补强 (0.5-1 day, 条件依赖 P0e 结果)

**动作**:
1. stage_filter 各分段 signal 数量统计:
   - 目前 stage=1: 50 / 1.5: 145 / 2: 1699 / 3: 191 / 4: 89
2. 找首次写坏路径: stage=1/3/4 为什么少? 是 fact_signal_context.technical_stage 划分本身 skew, 还是 fact_technical_trigger 过滤?
3. backfill 路径: 看 build_signal_context.py 是否漏写历史数据 / quality_filter 是否过严
4. 数据补到各 stage ≥ 500 行后, 重跑 P0b/P0c 寻优
5. 不放松 n_traded ≥ 3/5 阈值 — Rule 5 不打补丁

**Acceptance**:
- [ ] 各 stage_filter signal 数量 ≥ 500 (除非业务上 stage=1 本身极少)
- [ ] mart_per_stock_stage_strategy_optimal_v3 各 stage 行数翻倍 (or 给出业务解释为何不能)
- [ ] commit message 含 "Phase v3.P1a: 数据覆盖率补强; stage=X 从 N → M; 根因 = ..."

**回滚**: backfill 是只增不减, 无需回滚 (新增数据不影响旧行)

### P1b — alpha158 接入 (作 feature 过滤器, 2 day)

**动作**:
1. alpha158 PIT 验证: `fact_alpha158_panel` 158 columns 每个 trailing-only 验证, 无 forward leak
2. IC 筛选 top-20: 用 `run_optuna_feature_elimination.py` 或独立脚本算 158 因子 IC, 取 top-20
3. 加进 `candle_pattern.search_space`: 加 `alpha158_filter_threshold` 维度 (Optuna 寻每 stage 最优阈值)
4. formula_engine 各 formula 加 alpha158_filter (可选): 触发后用 alpha158 top-N IC 因子做二次过滤
5. 重跑 P0b v3 寻优 (含 alpha158 filter), 入 `mart_per_stock_stage_strategy_optimal_v3_alpha158`
6. paper_sim ablation: v3 baseline vs v3 + alpha158, 对比

**Acceptance**:
- [ ] alpha158 PIT 单测全绿 (trailing-only 校验)
- [ ] IC 筛选 top-20 列表 commit 到 yaml
- [ ] mart_v3_alpha158 行数 ≥ mart_v3
- [ ] paper_sim ablation 入 mart_paper_sim_kpi
- [ ] commit message 含 "Phase v3.P1b: alpha158 top-20 IC 接入; v3+alpha158 ann=X% (vs v3 baseline Y%)"

**回滚**: alpha158 filter 默认 off, 不强加; 老 mart_v3 不动

### P2 — v3 UI 接线 (2-3 day)

**动作**:
1. 调研 `routers/v3_views.py` / `v3_picture.py` / `v3_portfolio_builder.py` 当前提供的 API
2. 缺什么补什么:
   - 机构视图: `mart_institution_industry_stat` + `mart_institution_profile` 聚合 → API
   - 股票视图: `mart_per_stock_stage_strategy_optimal_v3` × `fact_signal_context` × `fact_technical_trigger` → 单股 detail API
   - 公式视图: `mart_per_stock_stage_strategy_optimal_v3` GROUP BY formula_id → 公式 portfolio API
3. design/v3-page-*.jsx 真实接线 (从 mock 改 fetch 真 API)
4. end-to-end smoke (打开浏览器手动验证三视图)

**Acceptance**:
- [ ] 三视图 API 跑通 (curl 测试)
- [ ] 前端三页面真实数据 (不是 mock)
- [ ] commit message 含 "Phase v3.P2: 三视图 UI 接线 + design jsx 入 git"

### P3 — bestchoice 合并 (1 day)

**动作**:
1. 读 bestchoice/compute.py 完整逻辑 (52KB)
2. 对比 services/formula_engine/macd_golden_cross.py
3. 决策: bestchoice MACD 包成第 9 个 formula variant (macd_golden_cross_bestchoice) 还是退役
4. bestchoice 数据库 cache_*.duckdb 是否保留 (策略参数 cache, 可能是寻优历史)
5. main.py FastAPI 服务化逻辑是否合并入 chunky-monkey backend

**Acceptance**:
- [ ] bestchoice/compute.py 核心逻辑明确归宿 (formula 化 / 退役 / 独立保留)
- [ ] 无重复实现 (一致接口走 formula_engine)
- [ ] commit message 含 "Phase v3.P3: bestchoice 合并方案 = X"

### P4 — 复盘闭环 (2 day)

**动作**:
1. paper_sim 日志 → 训练数据 pipeline:
   - mart_paper_sim_kpi 行 → mlflow run metric
   - paper_sim 交易日志 → mart_walkforward_eval (复活停摆 3-4 周的 v3 治理表)
2. champion 治理:
   - mart_champion_model 表建 (memory v3 计划里, 当前不存在)
   - RankIC ≥ 0.05 gate (memory 提到, 当前 0.037 未达)
3. 自动调参循环: paper_sim KPI 进 mlflow → trigger Optuna 重跑
4. 复盘看板: 历史 paper_sim runs 在 UI 一栏展示

**Acceptance**:
- [ ] mart_walkforward_eval / mart_champion_model 表建好
- [ ] mlflow run 跑通 (paper_sim KPI 进 mlflow)
- [ ] champion gate 阈值在 yaml
- [ ] commit message 含 "Phase v3.P4: 复盘闭环; champion gate RankIC = X (vs target ≥0.05)"

---

## 4. 工程纪律 (每 Phase 必走)

### 4.1 单测要求

- 改核心逻辑必有单测 / 集成测
- 改 perf 必有 benchmark test 防回退
- 测试基线 1402 passed 必须保持, 新加只增不减
- 单测覆盖率: 新模块 coverage ≥ 80%

### 4.2 Optuna 真跑标准

- n_trials ∈ [50, 500], 固定 seed
- 走 services.optimization.governance.enforce_pre_optimize
- 走 walk_forward.split_dispatch (R1 expanding_monthly)
- best params 入库前走 governance.enforce_pre_insert
- 入库 mart 表必须有 oos_* 字段 + walk_forward_mode != 'none'
- 拒绝 "快速验证 < 50 trials"

### 4.3 数据覆盖率不够 → 拉数据

- stage / formula / industry 任一分段覆盖偏低 (< 500) → 找首次写坏路径
- 禁止: 放松 n_traded 阈值 / try-except skip / --skip-step / --end YYYY-MM-DD 钉死
- 修源头不打补丁 (Rule 5 根因)

### 4.4 5-question commit hook

每 commit 前 self-check:
1. PROJECT_INDEX.md 同步了吗? (Pre-commit hook 强制)
2. 测试新加了吗?
3. 数据/跑批数字写进 commit message 了吗?
4. CLAUDE.md / Rule 9 反例表加了吗? (本次踩的新坑)
5. Rule 9.1 真金白银 self-check: 含 leakage/估算/假设? 数字穿透到 forward 期望?

### 4.5 PROJECT_INDEX / CLAUDE.md / goal.md 同步

每 Phase 完结:
- PROJECT_INDEX.md: 新表 §2 / 新 service §3 / 新 yaml §6 / 解决坑 §8 标 ✅ / 新坑 §11 + Rule 9 反例
- goal.md: 滚动 ledger 顶部追加 "### YYYY-MM-DD Phase v3.PX — 内容" 段
- CLAUDE.md Rule 6 / Rule 9 反例表: 新坑沉淀

### 4.6 失败先承认 (Rule 9.4)

- 数字告诉我们什么就报什么
- 0 STRONG_BUY / 数据滞后 / 实验 fail 必须**先讲**
- 不要因 "已经花了 X 小时调它" 硬要正向结论
- 拒绝包装

---

## 5. KPI Tracking 表 (实测 baseline + 各 Phase 目标)

| Phase | KPI 指标 | 现状 / baseline | Phase 目标 | 实测后填 |
|---|---|---|---|---|
| baseline | paper_sim 13-alpha hp=15 ann | +3.78% | — | (已知) |
| baseline | paper_sim 13-alpha hp=15 mdd | -30.1% | — | (已知) |
| baseline | paper_sim 13-alpha hp=15 sharpe | +0.29 | — | (已知) |
| P0b | mart_v3 OOS sharpe > 0 行数占比 | 旧表 ~30% | ≥ 30% (不退) | TBD |
| P0c | fact_technical_trigger institution_follow 行数 | 0 | ≥ 5,000 | TBD |
| P0c | mart_v3 含 institution_follow 行数 | 0 | ≥ 300 | TBD |
| P0e | v3 baseline 50/50 ann | — | ≥ +10% (期望)/ ≥ 0% (底线) | TBD |
| P0e | v3 baseline 50/50 mdd | — | ≥ -25% (期望) / ≥ -30% (底线) | TBD |
| P0e | Optuna 最优 alpha_w_inst | — | (无预期, 数据告诉) | TBD |
| P1a | stage=1 signal 数量 | 50 | ≥ 500 | TBD |
| P1a | stage=4 signal 数量 | 89 | ≥ 500 | TBD |
| P1b | v3+alpha158 ann vs v3 baseline | — | 至少不退 | TBD |
| 终极 | paper_sim ann (含 tx_cost/T+1) | +3.78% | ≥ +30% | TBD |
| 终极 | paper_sim mdd | -30% | ≥ -20% | TBD |
| 终极 | paper_sim 超额 vs HS300 | — | > 0 | TBD |

---

## 6. 风险 & 回滚

| 风险 | 检测 | 回滚动作 |
|---|---|---|
| P0b composite_v3 寻优出来 OOS 比旧版差 | mart_v3 OOS sharpe > 0 占比 < 旧版 | 不切换 selector v3 mode, 排查 ret/dd/hp 权重比例 |
| P0c institution_follow 信号数太少 | fact_technical_trigger < 5000 行 | 排查擅长判定阈值是否过严, 拉数据 backfill |
| P0e v3 baseline ann < 0 | paper_sim KPI | 暂停 P1 alpha158, 先排查 selector v3 逻辑 / 数据 |
| P1a backfill 后数据仍少 | stage_filter 计数 | 业务上承认 stage=1 本身极少, 在 yaml 加业务约束注释 |
| P1b alpha158 接入后 hurt | paper_sim ablation | alpha158_filter 默认 off, 不切主流程 |
| alpha 整体仍弱 (ann < +10%) | P0e baseline + P1b ablation 都 < +10% | 用户拍板: 调目标 (+15%/-15%) 或 改 alpha 根本 (sentiment / 概念板块 / ML) |

---

## 7. /goal 命令格式 (闭环复现)

在 `goal.md` 顶部追加段格式:

```markdown
### 2026-MM-DD Phase v3.PX — <标题>

**状态**: in_progress / completed / blocked

**输入**:
- 上一 Phase 输出 (KPI / mart 表行数 / commit SHA)
- 本 Phase 起步条件

**Acceptance**: (从 PLAN_V3.md 抄)
- [ ] 验收点 1
- [ ] ...

**实测结果**:
- KPI X = Y (vs target Z)
- commit: <SHA>

**下一步**: 
- 满足 Acceptance → 启动 Phase v3.P(X+1)
- 失败 → 走 §6 风险回滚
```

用户 `/goal` 命令时, Claude:
1. 读 `goal.md` 顶部最新 Phase ledger 段
2. 读 `PLAN_V3.md` 找当前 Phase Acceptance
3. 状态 in_progress → 继续执行
4. 状态 completed → 启动下一 Phase
5. 状态 blocked → 报告用户决策点

---

## 8. 启动 checklist (用户拍板 PLAN_V3.md 后)

- [ ] 用户确认 PLAN_V3.md (本文件)
- [ ] 创建 P0a-P4 TaskList 进 TaskCreate
- [ ] commit PLAN_V3.md 到 main (走 5-question hook)
- [ ] 开 feature/v3-arch 分支
- [ ] 启动 P0a Git 清理

---

**End of PLAN_V3.md**.

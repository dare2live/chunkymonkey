# ChunkyMonkey 第一性原理深度诊断报告

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


日期: 2026-05-17  
范围: `backend/` 训练、特征、label、paper_sim、成本、universe。诚实基线: RankIC=0.0246；equal 年化 -9.0%；`score_rank_diff_v1` 年化 -2.8%（+6.2pp）；60d IR median=-0.65~-0.52；月胜率 50%；年化换手 30.86x；100 万 CNY × 5 仓。

总 verdict: **框架级换**。Claude 的 B+E 判断成立，但不完整。当前系统的数学上限被 `IC=0.0246 × 5仓 × 高换手 × A股成本` 锁死，minor fixes 只能把负收益修到低个位数正收益，无法支撑 30% 年化。

## 第 1 节: Claude A-F 验证

| ID | 结论 | 代码证据与片段 | 定量判断 |
|---|---|---|---|
| A. pointwise vs top-K | **CONFIRMED / MAJOR** | `backend/services/ml_ranking/lightgbm_walkforward.py:1-5` 写 `pointwise`、`regression on fwd_cost_after`；`:202` 为 `lgb.LGBMRegressor(...)`。LambdaMART 仅对照: `lambdamart_walkforward.py:1-9`。 | RankIC=0.0246 < gate 0.03（`:103-107`），缺口 0.0054。top-5 应优化 `mean(return_top5)-cost-turnover_penalty`，当前优化全截面回归误差。 |
| B. 20d horizon × 高 cost | **CONFIRMED / CRITICAL** | label 只建 5/10/20d: `labels/build.py:59-66`；20d model: `paper_sim_ml_score_governance_v1_rank_diff.yaml:51`；driver 每日 exit/swap: `paper_sim/driver.py:312-327`。 | `net_SR=IC×sqrt(252/H)×sqrt(breadth)-cost_drag`；`annual_cost=round_trip×252/H`。IC=0.0246、breadth=5、27.282bps: H=5/20/60 gross IR=0.390/0.195/0.113，成本=13.75%/3.44%/1.15%。40bps 时=20.16%/5.04%/1.68%。实际换手 30.86x，40bps cost=12.34%/年。 |
| C. 50M ADV 滤掉小盘 alpha | **PARTIAL / MAJOR** | 门槛: `...rank_diff.yaml:40`；执行: `portfolio_walk_forward/liquidity.py:16,39-40`。size 有效: `features/AUDIT_2026_05_17.md:13,34`，`mc_decile corr 0.074`。ablation 仍是计划: `run_phase4_universe_ablation.sh:2-9,97-100`。 | 100万 5仓单仓约14万，25% cap=25万；50M ADV 的 3% 阈值=150万，执行上偏严。可能切掉 size alpha，但放开会增停牌/跌停/冲击成本。 |
| D. 5仓集中且 sector neutral 默认关闭 | **CONFIRMED / MAJOR** | 5仓: `...rank_diff.yaml:20-31`；sector budget 默认 `False`: `paper_sim/config.py:94-100`；仅开启才检查: `paper_sim/driver.py:440-468`；cap 40%: `sector_budget.py:5-8,84-106`。 | equal 单仓约14%；rank_diff 单仓最高25%。同 sector 两只可 >40%。实测 max_dd 约 -22.25%，破 -20%。 |
| E. 成本啃 alpha | **CONFIRMED / CRITICAL** | 成本模型: `paper_sim/tx_cost.py:3-7`；label 扣成本: `labels/cost_after.py:31-48`；配置: `...rank_diff.yaml:74-83`。 | 基础往返 `0.0027282=27.282bps`。实际换手 30.86x: 成本 8.42%/年；40bps: 12.34%/年。换手降至 8x 可省 +6.24pp；成本每降 10bps 可省 +3.09pp。 |
| F. 训练窗口缺完整牛市 | **PARTIAL / MAJOR** | Optuna 默认 `2024-01-01` 至 `2026-04-13`: `run_p0b_lightgbm_optuna_v4.py:105-107`；v4 panel 默认从 2024-01: `build_p0a_feature_panel_v4.py:44-45`。DB: 2024-01-02 至 2026-04-23。 | 约 27.5 个月，不覆盖完整牛市。实测牛市 ann +2.5%、熊市 0.0%、震荡 +127.1% small sample，不支持跨 regime 30%。 |

## 第 2 节: Claude 漏掉的 root cause

| ID | 结论 | 代码证据 | 定量影响 |
|---|---|---|---|
| G. Survivorship bias 被显式接受 | **CONFIRMED / MAJOR** | `universe.py:5-8` 写 “生存者偏差仍存在（已显式接受）”；`:31-35` 只按前缀，不查 delisted；`security_master.py:5-12` 用当前可交易主数据。DB: active=5512，ever=7138，inactive=882，`dim_listing_status=0`。 | survivor bias 让回测偏乐观；若缺失尾部概率 1%、尾损 50%，年化可高估 0.5-1pp。 |
| H. 训练/评估 horizon 治理错位 | **CONFIRMED / MAJOR** | policy primary 是 `follow_net_return_60d`: `pricing_label_policy.yaml:90-106`；P0a 只产 5/10/20d: `labels/build.py:59-66`；P3 固定 `fwd_cost_after_10d`: `run_p3_final_holdout.py:76-87`。 | 20d model 被 10d KPI 评价。若 10d/20d RankIC 差 0.005，年化估差约 `0.005/0.0246×5.9%=1.2pp`，且可能错 promote/reject。 |
| K. Optuna 多重检验未闭环 | **PARTIAL / MAJOR** | objective: `run_p0b_lightgbm_optuna_v4.py:199-254`；DSR 关闭: `optuna_config.yaml:101-103`；DB `mart_p1_optuna_trials` 仅 3 行，低于 min 50。 | 选择偏差粗略上界 `sigma_ic×sqrt(2lnN)`；若 std=0.09、N=50，上界约 0.25 trial-score。 |
| L. position sizing 不是风险模型 | **CONFIRMED / MAJOR** | `paper_sim/sizer.py:55-68` rank tilt；`:94-99` 用 `optimal_stop_pct` 近似 vol；YAML `:27-31` cap 25%。 | +6.2pp 仍 max_dd 约 -22.25%。25% 单仓 -10% 即 -2.5% NAV。 |
| M. NULL/CONST 特征入模且 fillna(0) | **CONFIRMED / MAJOR** | `perf/prepared_panel.py:107-110` `fillna(0)`；`features/AUDIT_2026_05_17.md:9-15`；`analysis/v4_panel_feature_audit_20260517.md:10-15`。DB: `sm_ret_60d`、`holder_count_change_q_pct` non-null=0/2,901,970；survey 8.8%。 | 0 填充混淆缺数据和真实 0。drop dead/noise 预期只 +0~0.0007 IC，但必须清理。 |

## 第 3 节: 三档 verdict

### 3.1 修补能救（ROI > 0）

| Fix | 位置 | 预期 delta |
|---|---|---:|
| 降低往返成本 10bps | 成本配置/券商实盘 | `10bps×30.86=+3.09pp/年` |
| 换手从 30.86x 降至 8x | `swap_rules.py`, `exit_rules.py`, `driver.py` | 节省 `(30.86-8)*27.282bps=+6.24pp/年` |
| 默认启用 sector 40% cap | `paper_sim/sector_budget.py`, YAML | max_dd 改善约 2-5pp，收益不保证 |
| drop NULL/CONST 特征 | `run_p0b_lightgbm_optuna_v4.py` exclude | RankIC +0~0.0007，年化 +0~1pp |
| P3 按模型 label 评价 | `run_p3_final_holdout.py` | 收益 delta 0，但诊断可信度提升 |

这些修补可把 -2.8% 拉到约 0%-8%，但不足以到 30%。

### 3.2 框架级换（必须 redesign）

Grinold-Kahn:

`ann_ret = IC × sqrt(N_positions × N_cycles) × vol × TC_adjustment`  
`TC_adjustment = 1 - cost_drag/gross_return`

用 `IC=0.0246`、`N_positions=5`、`H=20 => N_cycles=12.6`、`vol=30%`、40bps 往返:

- `gross_return = 0.0246 × sqrt(5×12.6) × 30% = 5.86%`
- `cost_drag = 0.40% × 12.6 = 5.04%`
- `TC_adjustment = 1 - 5.04/5.86 = 0.14`
- `net_return ≈ 0.82%`

若按实际换手 30.86x:

- `gross_return = 0.0246 × sqrt(5×30.86) × 30% = 9.17%`
- `cost_drag = 0.40% × 30.86 = 12.34%`
- `net_return ≈ -3.17%`，接近实测 -2.8%。

要在 H=20、5 仓、vol=30%、40bps 下达 30%:

`required_IC = (30% + 5.04%) / (sqrt(63) × 30%) = 0.147`

这是当前 IC 的 6.0 倍。继续调参、drop 几列、换 sizer，不可能填平。

必须 redesign 的点: pointwise 不等于 top-5 net utility；20d label 与 30.86x 执行不一致；P3/训练/paper_sim horizon 不统一；数据 gate 缺位；5 仓缺默认风险预算。

### 3.3 目标本身问题（push back）

| 情景 | IC | 换手/周期 | 估计净年化 | 说明 |
|---|---:|---:|---:|---|
| 当前系统 | 0.0246 | 30.86x | -5%~+2% | 与 -2.8% 一致 |
| minor fixes 后 | 0.025~0.03 | 8~15x | 0%~8% | 主要来自成本/换手 |
| redesign 成功 | 0.05~0.07 | 8~12x | 8%~18% | 需 top-K objective + 数据治理 |
| stretch case | 0.08~0.10 | 6~10x | 15%~25% | 需新 alpha 源 |
| 30% 目标 | 0.11~0.15+ | <=8~12x | 30% | 当前证据不支持 |

因此 30% 不是逻辑上永远不可能，但不是当前系统的可交付目标。下一阶段更诚实目标: RankIC >=0.05、年化 10%-18%、max_dd >=-20%、月胜率接近 55%、换手 <=8x。

## 第 4 节: Minimal viable redesign plan

| 步骤 | 改什么 | 为什么不是 patchwork | 人天 | 预期 ROI | 风险 |
|---|---|---|---:|---:|---|
| 1. 统一 label/horizon | 扩 `labels/build.py` 到 5/10/20/60/90；P3 按 model label；对齐 policy | 20d 训练、10d P3、60d policy 冲突 | 3-5 | +0~4pp | 60/90d 成熟慢 |
| 2. top-K cost-aware ranker | `ml_ranking/` 以 LambdaMART/listwise NDCG@5 为主，objective 加 cost/turnover | pointwise MSE 不是组合目标 | 5-8 | +3~8pp | IC 仍可能不足 |
| 3. PIT active-ever + data gate | 改 `universe.py`；补 `dim_listing_status`；fail on high-null/CONST | survivorship 与 NULL fill 是数据层错误 | 4-7 | +1~4pp | 主数据补齐成本高 |
| 4. 组合层重写 | 默认 sector budget；`sizer.py` 用 realized vol/cov；swap 加 turnover budget | 5 仓风险不能靠 alpha 兜底 | 4-6 | +4~8pp | 降换手可能丢 alpha |
| 5. Regime/horizon ensemble + 新 alpha | 分 regime 训练；接 PIT SUE/forecast/sentiment；final holdout freeze | 2024+ regime 不全，Phase4 多数噪音 | 8-15 | +5~15pp | PIT/覆盖率可能失败 |

顺序: 先修 horizon/评估，再换 objective，再做数据 gate 和组合层；否则会继续优化错误指标。

## 第 5 节: 诊断优先级矩阵

| Root Cause | Severity | Evidence Quality | Fix Cost | Expected Delta | Priority |
|---|---|---|---|---:|---|
| B. Horizon 与执行周期错位 | CRITICAL | 高: label/driver/config | 中 | +3~10pp | P0 |
| E. 成本 + 30.86x 换手 | CRITICAL | 高: 成本公式+实测 | 中 | +6~12pp | P0 |
| A. pointwise vs top-K | MAJOR | 高: `LGBMRegressor` | 中高 | +3~8pp | P0 |
| H. P3/label horizon 错位 | MAJOR | 高: P3 固定 10d | 低 | 防错判 | P0 |
| M. NULL/CONST + fillna(0) | MAJOR | 高: audit+DB | 低中 | +0~1pp | P1 |
| D. 5仓无默认 sector/risk budget | MAJOR | 高: 默认关闭 | 中 | DD +2~5pp | P1 |
| L. sizing 非风险模型 | MAJOR | 高: stop_pct 代 vol | 中 | +0~4pp | P1 |
| F. 训练窗口缺完整牛市 | MAJOR | 中高: 默认 2024+ | 高 | +2~8pp | P1 |
| J. LGBM 模型族弱信噪比 | MAJOR | 中: `lightgbm_walkforward.py:202`，LambdaMART 未主线 | 中高 | +0~6pp | P1 |
| C. 50M ADV 过滤小盘 alpha | MAJOR | 中: size corr 强但缺 ablation | 中 | -2~+5pp | P2 |
| G. Survivorship bias | MAJOR | 高: 代码显式接受 | 中高 | 修偏差 | P2 |
| K. Optuna 多重检验未闭环 | MAJOR | 中: DSR off，trial 表不完整 | 中 | 降 false positive | P2 |
| I. feature neutralization 缺位 | MINOR/MAJOR | 中: `pricing_label_policy.yaml:151` 仅 optional，未见强制 | 中 | +0~3pp | P3 |

最终判断: **不要继续把目标写成“调参后 30% 年化”。先把系统重设为 cost-aware top-K ranking + horizon governance + turnover/risk budget。当前 IC=0.0246 的第一性原理上限不支持用户终极目标。**

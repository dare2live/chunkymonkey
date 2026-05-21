# Backtester MCP Integration Scaffold

| 项 | 值 |
|---|---|
| 文档日期 | 2026-05-17 |
| 项目 | ChunkyMonkey |
| 工作目录 | `/Users/dp/Documents/M/stock/chunkymonkey` |
| 目标文件 | `docs/backtester_mcp_integration_20260517.md` |
| 输出语言 | 中文 |
| 设计优先级 | 表格优先, 数字优先, 证据优先 |
| 覆盖范围 | Part A 到 Part E |
| 代码动作 | 本文只给 scaffold 和 stub, 不创建 Python 模块 |
| 主目标 | 阻断过拟合、非显著、成交不保守、IS-OOS 断裂的策略 promote 实盘 |
| 关键结论 | promote 前必须同时通过 PBO、DSR、保守成交、IS-OOS gap 四道 hard gate |

## 来源索引

| 来源 ID | 类型 | 用途 | 关键数字 / 结论 | 链接或本地路径 |
|---:|---|---|---|---|
| 1 | 论文 | PBO / CSCV 公式 | `lambda=logit(omega)`, `PBO=Pr(lambda<0)` | Lopez de Prado 2014 The False Strategy Theorem / Combinatorially Symmetric Cross-Validation; Bailey, Borwein, Lopez de Prado, Zhu, The Probability of Backtest Overfitting |
| 2 | 论文 | DSR 公式 | DSR Eq.(2), expected max SR Eq.(6), 95% confidence gate | Bailey & Lopez de Prado 2014, The Deflated Sharpe Ratio |
| 3 | 官方包页 | backtester-mcp 版本和能力 | PyPI `0.1.0`, Released `2026-04-14`, Apache-2.0, Python >=3.10 | https://pypi.org/project/backtester-mcp/ |
| 4 | GitHub | backtester-mcp CLI / MCP tools | CLI `backtest/optimize/report`, MCP stdio, 13 tools | https://github.com/bcosm/backtester-mcp |
| 5 | 本地 DDL | paper_sim 表字段 | `mart_paper_sim_nav`, `fact_paper_sim_trade`, `mart_paper_sim_kpi` | `backend/services/paper_sim/ddl.py` |
| 6 | 本地 DDL | Optuna trials 表字段 | `mart_p1_optuna_trials` has `rank_ic_mean`, `params_json`, `user_attrs_json` | `gcp/run_rankic_experiment.py` |
| 7 | 本地历史 KPI | 2026-05-17 DuckDB 读数 | `mart_paper_sim_kpi=37 rows`, `all_kpi_pass=0`, ann range `[-80.56%, 114.15%]` | `data/smartmoney.duckdb` read-only query |
| 8 | 本地反例 | 异常高数字红线 | `RankIC > 0.3`, `Sharpe > 5`, 年化 `>100%`, 相对提升 `>=50%` 触发 leakage 审计 | `CLAUDE.md` |
| 9 | 本地反例 | `+312%` 假象 | `mart_per_stock_stage_strategy_optimal.sharpe` 全期 IS fit 导致事后选强 | `CLAUDE.md`, `PROJECT_INDEX.md` |
| 10 | 本地反例 | clean baseline | P0b clean RankIC `0.0108-0.0203`; roadmap baseline `0.0246` | `CLAUDE.md`, `docs/phase4_alpha_root_cause_roadmap.md` |
| 11 | 本地反例 | VWAP 单位错误 | akshare 股 vs tdxhub 手; 旧 `/(volume*100)` 对股单位会得 `0.11` 元级错误 | `backend/tests/paper_sim/test_vwap.py` |
| 12 | 本地反例 | stage optimal PIT broken | `mart_per_stock_stage_strategy_optimal.built_at` 单一时间戳 | DuckDB read-only query, `PROJECT_INDEX.md` |

| 本文术语 | 精确定义 |
|---|---|
| `p_conf` | DSR 置信概率, 即 `Phi(z)`; promote 要求 `p_conf >= 0.95` |
| `p_value` | 若使用传统右尾 p-value, `p_value = 1 - p_conf`; promote 要求 `p_value <= 0.05` |
| `PBO` | Probability of Backtest Overfitting, CSCV logit rank 小于 0 的比例 |
| `IS` | in-sample, 调参 / 选择使用的历史窗口 |
| `OOS` | out-of-sample, walk-forward / paper_sim 未参与选择的窗口 |
| `promote` | 将 challenger 写成可用于实盘或默认推荐的 champion |
| `hard block` | gate 失败时直接阻断 promote commit / registry 写入 |
| `warn only` | 不阻断 research run, 但不能 promote live |
| `force retrain` | 需要重新训练 / 重跑 walk-forward 后才能重新申请 promote |

| 表名契约 | 当前 repo 实际名 | 本文处理 |
|---|---|---|
| `mart_paper_sim_kpi` | `mart_paper_sim_kpi` | 直接读取 |
| `mart_paper_sim_trades` | `fact_paper_sim_trade` | 建议 view alias, gate 内部读 canonical trade view |
| `mart_paper_sim_daily_nav` | `mart_paper_sim_nav` | 建议 view alias, gate 内部读 canonical nav view |
| `mart_p1_optuna_trials` | `mart_p1_optuna_trials` | 直接读取 |
| `mart_per_stock_stage_strategy_optimal` | `mart_per_stock_stage_strategy_optimal` | 仅用于反例校验, 不作为 promote 正向证据 |

```sql
-- 兼容 view scaffold: 文档设计, 不在本次任务落库执行
CREATE OR REPLACE VIEW mart_paper_sim_daily_nav AS
SELECT *
FROM mart_paper_sim_nav;

CREATE OR REPLACE VIEW mart_paper_sim_trades AS
SELECT *
FROM fact_paper_sim_trade;
```

## Part A: Gate 设计

### A.1 总体判定矩阵

| Gate ID | 风险 | 指标 | 阈值 | 失败 action | promote 影响 |
|---:|---|---|---:|---|---|
| 1 | in-sample 过拟合 | `PBO` | `<= 0.20` | block promote + force retrain | hard block |
| 2 | Sharpe 非显著 | `DSR p_conf` | `>= 0.95` | block promote + force retrain | hard block |
| 3 | 成交不保守 | `conservative_ann_ret` | `>= 0.00` | block promote | hard block |
| 4 | IS-OOS 断裂 | `relative_gap` | `<= 0.30` | block promote + force retrain | hard block |
| 5 | 极端好看红旗 | `ann_ret`, `sharpe`, `win_rate`, `RankIC uplift` | 见 A.7 | block promote + leakage audit | hard block |
| 6 | 输入不足 | required mart rows | 见 A.8 | block promote | hard block |
| 7 | 数据 freshness | `period_end`, `built_at`, `snapshot_at` | promote 日前最新完整回测 | warn research, block live | hard block for live |

| Gate ID | 公式来源 | 阈值来源 | 本地校准证据 |
|---:|---|---|---|
| 1 | 来源 1 PBO/CSCV: `lambda=logit(omega)`, `PBO=Pr(lambda<0)` | 论文给概率语义, `0.5` 表示更可能过拟合; live 采用更严 `0.20` | `+312%` 假象、PIT broken、37 KPI rows 中 all pass 为 0 |
| 2 | 来源 2 DSR Eq.(2), Eq.(6) | 论文例子按 `95% confidence` 接受 / 拒绝; 本文要求 `p_conf>=0.95` | Optuna trial count 影响 search inflation; 当前 trials 表仅 3 rows 时必须标注不足 |
| 3 | 来源 4 execution scenarios + 本地 paper_sim 交易现实 | 本地 live 资金门槛: 保守情景年化不得为负 | 历史 negative annual_return 最低 `-80.56%`; VWAP 单位 bug 会虚假止损 |
| 4 | Walk-forward / OOS 选择原则 | 本地 `30% relative gap` 作为 promote 上限 | clean RankIC `0.0108-0.0203`, baseline `0.0246`; `+50%` uplift 是 leakage 红线 |
| 5 | 本地治理规则 | `ann_ret>100%`, `Sharpe>5`, `win_rate>95%`, `RankIC relative uplift>=50%` | `CLAUDE.md` 明确列为异常好看红旗 |

### A.2 Gate 1: PBO

| 项 | 设计 |
|---|---|
| 目标 | 防止从大量 trial 中挑出 IS 最优但 OOS 退化的参数 / 策略 |
| 公式 1 | 将 T 个时间点切成 `S` 个连续子块, `S` 为偶数 |
| 公式 2 | 对每个组合 `c`, IS 取 `S/2` 子块, OOS 取补集 |
| 公式 3 | `n_star(c)=argmax_n performance(IS_c, strategy_n)` |
| 公式 4 | `r_oos(c)=rank(performance(OOS_c, n_star), among all n)` |
| 公式 5 | `omega_c=(r_oos(c)-0.5)/N`, rank 从 1 到 N, 1 为最差, N 为最好 |
| 公式 6 | `lambda_c=log(omega_c/(1-omega_c))` |
| 公式 7 | `PBO = mean(lambda_c < 0)` |
| 论文依据 | 来源 1: CSCV / PBO 以 OOS 相对 rank logit 的负区间概率度量过拟合 |
| promote 阈值 | `PBO <= 0.20` |
| 阈值解释 | `PBO=0.20` 表示 IS winner 在 CSCV OOS 中跌到中位数以下的组合比例不得超过 20% |
| 失败 action | `PBO > 0.20` hard block promote, 写 gate verdict, 强制 retrain 或缩小搜索空间 |
| warning action | `0.10 < PBO <= 0.20` 可 research 通过, live promote 仍需 DSR 和 conservative 同时通过 |

| 输入字段 | mart 表 | 当前 repo 字段 | 备注 |
|---|---|---|---|
| strategy trial id | `mart_p1_optuna_trials` | `run_id`, `trial_number` | 每个 Optuna trial 是一条候选策略 |
| trial params | `mart_p1_optuna_trials` | `params_json` | 用于 replay / perturbation |
| trial score | `mart_p1_optuna_trials` | `value`, `rank_ic_mean`, `rank_ic_std` | 非收益矩阵时只能排序, 不能替代 PBO returns |
| trial windows | `mart_p1_optuna_trials` | `user_attrs_json`, `n_windows` | 若含 per-window score, 可构建 `T_window x N_trial` score matrix |
| selected daily returns | `mart_paper_sim_daily_nav` | `daily_ret`, `total_value` | 当前实际表为 `mart_paper_sim_nav` |
| selected KPI | `mart_paper_sim_kpi` | `sharpe`, `annual_return`, `max_dd` | 用于 gate report |
| trade replay | `mart_paper_sim_trades` | `date`, `type`, `price`, `tx_cost`, `net_amount` | 当前实际表为 `fact_paper_sim_trade` |

| PBO 数据优先级 | 方法 | 是否可 promote | 说明 |
|---:|---|---|---|
| 1 | 从每个 trial replay 生成 daily returns matrix | 是 | 精确 CSCV, 首选 |
| 2 | 从 `user_attrs_json.windows` 取 per-window OOS score matrix | 是, 但 verdict 标 `score_matrix` | 可用于 RankIC 型策略筛选 |
| 3 | 对 selected strategy 做参数扰动 `±20%` 生成 N 条 returns | 是, 但 verdict 标 `perturbation_pbo` | 对单策略可用, 与 backtester-mcp 说明一致 |
| 4 | 只有 winner NAV, 无 trial / perturbation | 否 | 输入不足, hard block |

| PBO 阈值校准项 | 数字 | 来源 | 决策 |
|---|---:|---|---|
| 论文语义中位线 | `0.50` | 来源 1, `lambda<0` 表示 OOS rank 低于中位数 | 不作为 live 阈值, 太宽 |
| live hard gate | `0.20` | 本文设计 + 历史假 alpha 校准 | `>0.20` block |
| warning band | `0.10-0.20` | 风险预警 | research 可继续, promote 需其它 gate 全绿 |
| current trials rows | `3` | 来源 7, DuckDB query | 不足以宣称 CSCV 稳健; 需 replay / perturbation |
| stage optimal built_at distinct | `1` | 来源 12 | PIT broken 反例应使 PBO 显著升高 |

### A.3 Gate 2: Deflated Sharpe Ratio

| 项 | 设计 |
|---|---|
| 目标 | 防止 Sharpe 因多重检验、短样本、偏度、峰度而虚高 |
| observed SR | `SR_hat = mean(r) / std(r)` 使用 daily frequency |
| annualized SR | `SR_ann = SR_hat * sqrt(252)` 只用于 report, DSR 内部保持 daily unit |
| skew | `gamma3 = E[((r-mu)/sigma)^3]` |
| kurtosis | `gamma4 = E[((r-mu)/sigma)^4]` |
| expected max SR | `SR_star = E[max(SR_trials)]`, 来源 2 Eq.(6) |
| DSR z | `z = (SR_hat - SR_star) * sqrt(T-1) / sqrt(1 - gamma3*SR_hat + ((gamma4-1)/4)*SR_hat^2)` |
| DSR probability | `p_conf = Phi(z)` |
| promote 阈值 | `p_conf >= 0.95` |
| 等价 p-value | `1 - p_conf <= 0.05` |
| 用户原始表述 | “DSR p < 0.95 非显著”在本文解释为 `p_conf < 0.95` 非显著 |
| 失败 action | `p_conf < 0.95` hard block promote, 强制 retrain 或减少 trials |

| 输入字段 | mart 表 | 当前 repo 字段 | 用途 |
|---|---|---|---|
| selected returns | `mart_paper_sim_daily_nav` | `daily_ret` | 计算 SR、T、skew、kurtosis |
| selected NAV | `mart_paper_sim_daily_nav` | `total_value` | `daily_ret` 缺失时重算 |
| selected KPI | `mart_paper_sim_kpi` | `sharpe`, `annual_return`, `n_days` | report 和交叉校验 |
| trial count | `mart_p1_optuna_trials` | `COUNT(*)`, `state` | 多重检验惩罚 |
| trial score dispersion | `mart_p1_optuna_trials` | `rank_ic_mean`, `value` | 只作 search breadth proxy |
| trial returns | replay artifact | returns matrix | 精确 `SR_trials` 首选 |
| effective N | `mart_p1_optuna_trials` + corr | `N_eff` | 若 trial 高相关, 用 implied independent trials |

| DSR 阈值校准项 | 数字 | 来源 | 决策 |
|---|---:|---|---|
| DSR confidence | `0.95` | 来源 2: 论文例子使用 95% confidence 判断 legitimate discovery | `p_conf<0.95` block |
| p-value 等价 | `0.05` | `1 - 0.95` | 传统统计报告可显示 |
| 当前 KPI rows | `37` | 来源 7 | 可提供 selected NAV 样本, 不能替代 trials |
| 当前 Optuna rows | `3` | 来源 7 | 搜索强度记录不足, `input_quality` 必须写入 verdict |
| 极端 Sharpe 红线 | `5.0` | 来源 8 | 即使 DSR 过, 也触发 leakage hard block |

| DSR 输入质量 | 条件 | verdict 字段 | promote |
|---|---|---|---|
| exact | 有 trial returns matrix | `dsr_input_quality=exact` | 可 promote |
| replay | 从 `params_json` replay 得到 trial returns | `dsr_input_quality=replay` | 可 promote |
| perturbation | winner 参数扰动生成 trial returns | `dsr_input_quality=perturbation` | 可 promote, 需 PBO 同源 |
| proxy | 只有 trial RankIC / objective 分布 | `dsr_input_quality=proxy` | 不可 promote, hard block |
| missing | NAV 或 trials 不足 | `dsr_input_quality=missing` | hard block |

### A.4 Gate 3: Conservative Scenario

| 项 | 设计 |
|---|---|
| 目标 | 防止 close 成交、低估滑点、涨跌停可交易性过宽导致的实盘收益虚高 |
| conservative 1 | `slippage_bps_conservative = slippage_bps_base * 1.50` |
| conservative 2 | 入场 / 出场参考价使用 VWAP 替代 close |
| conservative 3 | 若 VWAP 异常, 使用更差侧 fallback: buy 用 `max(open, close)`, sell 用 `min(open, close)` |
| conservative 4 | 涨跌停 mask 加强: 一字板、接近涨停买入、接近跌停卖出、无量异常全部 reject |
| conservative 5 | 成交额 / volume unit sanity: 候选 VWAP 必须落在 `[low*0.95, high*1.05]` |
| promote 阈值 | `conservative_ann_ret >= 0.00` |
| 失败 action | `conservative_ann_ret < 0` hard block promote |
| 备注 | 这是 live 资金保护阈值, 不宣称策略优秀, 只要求保守情景不亏 |

| 输入字段 | mart 表 | 当前 repo 字段 | 用途 |
|---|---|---|---|
| base NAV | `mart_paper_sim_daily_nav` | `date`, `total_value`, `daily_ret` | base 对照 |
| trades | `mart_paper_sim_trades` | `date`, `type`, `price`, `gross_amount`, `tx_cost`, `net_amount` | 成交重估 |
| KPI | `mart_paper_sim_kpi` | `annual_return`, `max_dd`, `sharpe` | base vs conservative gap |
| kline | `market.v_price_kline_qfq` 或现有 kline view | `open`, `high`, `low`, `close`, `volume`, `amount` | VWAP / limit mask |
| config snapshot | `mart_paper_sim_kpi` | `config_snapshot` | base slippage / fee 读取 |

| conservative 阈值校准项 | 数字 | 来源 | 决策 |
|---|---:|---|---|
| slippage stress | `+50%` | 用户契约 + live 保守资金要求 | 必跑 |
| annual return floor | `0.00` | 用户契约 | `<0` block |
| VWAP sanity lower | `low*0.95` | 来源 11 单测 | 低于则 close fallback / reject |
| VWAP sanity upper | `high*1.05` | 来源 11 单测 | 高于则 close fallback / reject |
| historical min ann | `-80.56%` | 来源 7 | negative strategies 必须被拦 |
| historical max ann | `114.15%` | 来源 7 | `>100%` 需 leakage red flag |

### A.5 Gate 4: IS-OOS Gap

| 项 | 设计 |
|---|---|
| 目标 | 防止 IS 指标显著高于 OOS, 说明参数选择依赖历史噪声 |
| primary metric | 优先 `Sharpe`, 次选 `annual_return`, ML 层可附加 `RankIC` |
| gap 公式 | `gap_rel = max(0, metric_is - metric_oos) / max(abs(metric_is), eps)` |
| eps | `1e-9` |
| promote 阈值 | `gap_rel <= 0.30` |
| 失败 action | `gap_rel > 0.30` hard block promote + force retrain |
| calibration | `RankIC` 相对 baseline 提升 `>=50%` 是 leakage 红线; `30%` promote gap 更保守 |

| 输入字段 | mart 表 | 当前 repo 字段 | 用途 |
|---|---|---|---|
| IS score | `mart_p1_optuna_trials` | `value`, `rank_ic_mean`, `params_json` | trial selection score |
| IS windows | `mart_p1_optuna_trials` | `n_windows`, `user_attrs_json` | train / validation split |
| OOS KPI | `mart_paper_sim_kpi` | `sharpe`, `annual_return`, `calmar` | promoted candidate paper_sim |
| OOS NAV | `mart_paper_sim_daily_nav` | `daily_ret`, `total_value` | recompute OOS metrics |
| historical guard | `mart_per_stock_stage_strategy_optimal` | `sharpe`, `oos_sharpe`, `walk_forward_mode` | 反例检测 |

| Gap 阈值校准项 | 数字 | 来源 | 决策 |
|---|---:|---|---|
| promote relative gap | `30%` | 用户契约 | `>30%` block |
| leakage relative uplift red flag | `50%` | 来源 8 | `>=50%` block + audit |
| clean P0b range | `0.0108-0.0203` RankIC | 来源 10 | 超出过多需证明 |
| roadmap baseline | `0.0246` RankIC | 来源 10 | promote 需解释和验证 uplift |
| false v3 chain | `+60%` RankIC | 用户反例 | 必须被 block |

### A.6 Gate output schema

| 字段 | 类型 | 示例 | 必填 | 说明 |
|---|---|---|---|---|
| `gate_run_id` | TEXT | `bt_gate_20260517_001` | 是 | 一次 gate 执行 ID |
| `candidate_id` | TEXT | model / sim / run id | 是 | 待 promote 候选 |
| `sim_run_id` | TEXT | paper_sim run | 是 | 对应 paper_sim |
| `optuna_run_id` | TEXT | p1 run | 是 | 对应调参 run |
| `verdict` | TEXT | `PASS` / `BLOCK` | 是 | 综合结论 |
| `blockers_json` | TEXT | `["pbo"]` | 是 | hard block 原因 |
| `warnings_json` | TEXT | `["pbo_warning"]` | 是 | warning 原因 |
| `pbo` | DOUBLE | `0.36` | 是 | PBO |
| `pbo_threshold` | DOUBLE | `0.20` | 是 | PBO 阈值 |
| `dsr_p_conf` | DOUBLE | `0.91` | 是 | DSR confidence |
| `dsr_threshold` | DOUBLE | `0.95` | 是 | DSR 阈值 |
| `conservative_ann_ret` | DOUBLE | `-0.04` | 是 | 保守情景年化 |
| `conservative_threshold` | DOUBLE | `0.00` | 是 | 保守阈值 |
| `is_oos_gap_rel` | DOUBLE | `0.42` | 是 | 相对 gap |
| `is_oos_gap_threshold` | DOUBLE | `0.30` | 是 | gap 阈值 |
| `input_quality_json` | TEXT | `{"pbo":"replay"}` | 是 | 输入质量 |
| `source_rows_json` | TEXT | `{"nav":432}` | 是 | mart 行数 |
| `computed_at` | TIMESTAMP | now | 是 | 计算时间 |
| `formula_version` | TEXT | `bt_gate_v1` | 是 | gate 版本 |

### A.7 极端好看红旗

| Red Flag ID | 条件 | 数字来源 | action | 说明 |
|---:|---|---|---|---|
| 1 | `annual_return > 1.00` | 来源 8, 来源 7 max `1.1415` | block promote | 年化超 100% 对当前 A 股 5 仓策略视为 leakage audit |
| 2 | `sharpe > 5.00` | 来源 8 | block promote | 即使 DSR 计算异常通过也不放行 |
| 3 | `win_rate > 0.95` | 来源 8 | block promote | 历史 100% 胜率来自倒推买卖点 |
| 4 | `RankIC_abs > 0.30` | 来源 8 | block promote | 绝对异常 |
| 5 | `RankIC_uplift_rel >= 0.50` | 来源 8 | block promote | 相对 baseline 异常 |
| 6 | `total_return > 3.00` 且 `n_days<500` | 来源 9 + 本地 KPI max total_return `4.058` | block promote | `+312%` 类型反例 |
| 7 | `walk_forward_mode='none'` | 来源 8/9 | block promote | IS-OOS leakage |

### A.8 输入不足规则

| 检查项 | 最低要求 | 来源 | 失败 action |
|---|---:|---|---|
| NAV rows | `>= 60` trading days | DSR skew/kurtosis 和 rolling 可靠性 | block promote |
| closed trades | `>= 30` trades | bootstrap / fill realism | block promote |
| Optuna trials | `>= 30` trials 或 perturbation variants `>=30` | DSR / PBO 多重检验 | block promote |
| PBO splits `S` | `>= 8`, even | CSCV 组合数量 | block promote |
| CSCV combos | `>= 20` | PBO 稳定性 | block promote |
| OOS months | `>= 6` | live promote minimum | block promote |
| KPI row | exactly `1` for `(sim_run_id, variant)` | reproducibility | block promote |
| params_json | not null for selected run | replay | block promote |

### A.9 Gate DDL scaffold

```sql
-- scaffold only: 建议后续落库, 本文不执行
CREATE TABLE IF NOT EXISTS mart_backtest_validation_gate (
    gate_run_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    sim_run_id TEXT NOT NULL,
    optuna_run_id TEXT,
    verdict TEXT NOT NULL,
    blockers_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    pbo DOUBLE,
    pbo_threshold DOUBLE NOT NULL DEFAULT 0.20,
    dsr_p_conf DOUBLE,
    dsr_threshold DOUBLE NOT NULL DEFAULT 0.95,
    conservative_ann_ret DOUBLE,
    conservative_threshold DOUBLE NOT NULL DEFAULT 0.00,
    is_oos_gap_rel DOUBLE,
    is_oos_gap_threshold DOUBLE NOT NULL DEFAULT 0.30,
    input_quality_json TEXT NOT NULL,
    source_rows_json TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    computed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### A.10 Verification SQL

```sql
-- A.10.1 当前 paper_sim KPI 历史分布 + 输入行数
SELECT
    COUNT(*) AS n_runs,
    MIN(annual_return) AS min_ann,
    MAX(annual_return) AS max_ann,
    AVG(annual_return) AS avg_ann,
    MIN(sharpe) AS min_sharpe,
    MAX(sharpe) AS max_sharpe,
    SUM(CASE WHEN all_kpi_pass THEN 1 ELSE 0 END) AS pass_runs
FROM mart_paper_sim_kpi;

SELECT sim_run_id, COUNT(*) AS nav_rows, MIN(date) AS start_date, MAX(date) AS end_date
FROM mart_paper_sim_nav
WHERE sim_run_id = $sim_run_id
GROUP BY sim_run_id
HAVING COUNT(*) >= 60;

SELECT sim_run_id, COUNT(*) AS trade_rows, COUNT(DISTINCT position_id) AS positions
FROM fact_paper_sim_trade
WHERE sim_run_id = $sim_run_id
GROUP BY sim_run_id
HAVING COUNT(*) >= 30;

SELECT run_id, COUNT(*) AS trial_rows, MIN(rank_ic_mean) AS min_rank_ic, MAX(rank_ic_mean) AS max_rank_ic
FROM mart_p1_optuna_trials
WHERE run_id = $optuna_run_id
  AND state = 'COMPLETE'
GROUP BY run_id
HAVING COUNT(*) >= 30;
```

## Part B: Integration Architecture

### B.1 模块总览

| 模块 | 文件路径 | 责任 | core 行数目标 | 输入 | 输出 |
|---|---|---|---:|---|---|
| PBO | `backend/services/backtest_validation/pbo.py` | CSCV / perturbation PBO | `<100` | returns matrix | `PBOResult` |
| DSR | `backend/services/backtest_validation/dsr.py` | DSR probability | `<100` | selected returns + trial SRs | `DSRResult` |
| Conservative | `backend/services/backtest_validation/conservative.py` | 保守成交重估 | `<150` | trades + kline + config | `ScenarioResult` |
| Gate | `backend/services/backtest_validation/gate.py` | 综合 verdict | `<180` | mart ids | `GateVerdict` |

### B.2 Package layout

| 路径 | 类型 | 本文状态 | 后续动作 |
|---|---|---|---|
| `backend/services/backtest_validation/__init__.py` | package init | scaffold | export dataclasses |
| `backend/services/backtest_validation/pbo.py` | module | stub in doc | implement |
| `backend/services/backtest_validation/dsr.py` | module | stub in doc | implement |
| `backend/services/backtest_validation/conservative.py` | module | stub in doc | implement |
| `backend/services/backtest_validation/gate.py` | module | stub in doc | implement |
| `backend/tests/backtest_validation/test_pbo.py` | test | fixture design | implement |
| `backend/tests/backtest_validation/test_dsr.py` | test | fixture design | implement |
| `backend/tests/backtest_validation/test_conservative.py` | test | fixture design | implement |
| `backend/tests/backtest_validation/test_gate.py` | test | fixture design | implement |

### B.3 Shared dataclasses

| Dataclass | 字段 | 说明 |
|---|---|---|
| `PBOResult` | `pbo`, `lambdas`, `n_combinations`, `threshold`, `passed`, `input_quality` | PBO 明细 |
| `DSRResult` | `p_conf`, `z`, `sr`, `sr_star`, `n_obs`, `n_trials_eff`, `passed`, `input_quality` | DSR 明细 |
| `ScenarioResult` | `ann_ret`, `total_return`, `max_dd`, `sharpe`, `n_rejected_fills`, `passed` | conservative 明细 |
| `GateVerdict` | `verdict`, `blockers`, `warnings`, `metrics`, `source_rows` | 综合 verdict |

### B.4 `pbo.py` stub

```python
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    lambdas: list[float]
    n_combinations: int
    threshold: float = 0.20
    passed: bool = False
    input_quality: str = "returns_matrix"


def _score(x: np.ndarray, method: str) -> np.ndarray:
    if method == "mean":
        return np.nanmean(x, axis=0)
    if method == "sharpe":
        mu = np.nanmean(x, axis=0)
        sd = np.nanstd(x, axis=0, ddof=1)
        return np.divide(mu, sd, out=np.zeros_like(mu), where=sd > 0)
    raise ValueError(f"unsupported score method: {method}")


def pbo_cscv(
    returns_matrix: np.ndarray,
    *,
    n_splits: int = 8,
    score_method: str = "sharpe",
    threshold: float = 0.20,
) -> PBOResult:
    """Lopez de Prado CSCV PBO core.

    returns_matrix shape: (time, strategies).
    Lower PBO is better. Promote requires pbo <= threshold.
    """
    x = np.asarray(returns_matrix, dtype=float)
    if x.ndim != 2:
        raise ValueError("returns_matrix must be 2D: time x strategies")
    n_time, n_strat = x.shape
    if n_strat < 2:
        raise ValueError("PBO requires at least 2 strategies or perturbations")
    if n_splits < 4 or n_splits % 2:
        raise ValueError("n_splits must be an even integer >= 4")
    if n_time < n_splits:
        raise ValueError("not enough rows for n_splits")

    blocks = np.array_split(np.arange(n_time), n_splits)
    lambdas: list[float] = []
    half = n_splits // 2
    for is_blocks in combinations(range(n_splits), half):
        is_idx = np.concatenate([blocks[i] for i in is_blocks])
        oos_idx = np.concatenate([blocks[i] for i in range(n_splits) if i not in is_blocks])
        is_scores = _score(x[is_idx], score_method)
        oos_scores = _score(x[oos_idx], score_method)
        winner = int(np.nanargmax(is_scores))
        order = np.argsort(oos_scores)
        rank_1_worst = int(np.where(order == winner)[0][0]) + 1
        omega = (rank_1_worst - 0.5) / n_strat
        omega = min(max(omega, 1e-12), 1.0 - 1e-12)
        lambdas.append(float(np.log(omega / (1.0 - omega))))

    pbo = float(np.mean(np.asarray(lambdas) < 0.0))
    return PBOResult(
        pbo=pbo,
        lambdas=lambdas,
        n_combinations=len(lambdas),
        threshold=threshold,
        passed=pbo <= threshold,
    )
```

| Fixture | 输入 | 期望 |
|---|---|---|
| positive stable | 8 splits, 40 perturbations, OOS rank stable | `pbo <= 0.20`, `passed=True` |
| negative overfit | 8 splits, IS winner OOS below median most combos | `pbo > 0.20`, `passed=False` |
| invalid single | 1 strategy only | `ValueError` |
| invalid splits | odd `n_splits=7` | `ValueError` |

### B.5 `dsr.py` stub

```python
from __future__ import annotations

from dataclasses import dataclass
from math import e, sqrt
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True)
class DSRResult:
    p_conf: float
    z: float
    sr: float
    sr_star: float
    n_obs: int
    n_trials_eff: float
    threshold: float = 0.95
    passed: bool = False
    input_quality: str = "exact"


def _moments(x: np.ndarray) -> tuple[float, float]:
    y = x[np.isfinite(x)]
    mu = float(np.mean(y))
    sd = float(np.std(y, ddof=1))
    if sd <= 0:
        return 0.0, 3.0
    z = (y - mu) / sd
    return float(np.mean(z**3)), float(np.mean(z**4))


def expected_max_sr(trial_srs: np.ndarray, n_eff: float | None = None) -> float:
    srs = np.asarray(trial_srs, dtype=float)
    srs = srs[np.isfinite(srs)]
    if len(srs) < 2:
        raise ValueError("DSR requires at least 2 trial Sharpe ratios")
    n = float(n_eff or len(srs))
    n = max(n, 2.0)
    mu = float(np.mean(srs))
    sigma = float(np.std(srs, ddof=1))
    nd = NormalDist()
    gamma = 0.5772156649015329
    max_z = (1.0 - gamma) * nd.inv_cdf(1.0 - 1.0 / n)
    max_z += gamma * nd.inv_cdf(1.0 - 1.0 / (n * e))
    return mu + sigma * max_z


def deflated_sharpe_ratio(
    daily_returns: np.ndarray,
    trial_daily_srs: np.ndarray,
    *,
    n_eff: float | None = None,
    threshold: float = 0.95,
    input_quality: str = "exact",
) -> DSRResult:
    r = np.asarray(daily_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 30:
        raise ValueError("DSR requires at least 30 observations")
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        raise ValueError("daily returns variance is zero")

    sr = float(np.mean(r) / sd)
    sr_star = expected_max_sr(np.asarray(trial_daily_srs, dtype=float), n_eff)
    skew, kurt = _moments(r)
    denom_var = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    denom_var = max(denom_var, 1e-12)
    z = (sr - sr_star) * sqrt(len(r) - 1.0) / sqrt(denom_var)
    p_conf = NormalDist().cdf(z)
    return DSRResult(
        p_conf=float(p_conf),
        z=float(z),
        sr=sr,
        sr_star=float(sr_star),
        n_obs=len(r),
        n_trials_eff=float(n_eff or len(trial_daily_srs)),
        threshold=threshold,
        passed=p_conf >= threshold,
        input_quality=input_quality,
    )
```

| Fixture | 输入 | 期望 |
|---|---|---|
| positive significant | selected SR 高, trial SR dispersion 低, T>=252 | `p_conf >= 0.95` |
| negative multiple testing | selected SR 一般, trial count 500, dispersion 高 | `p_conf < 0.95` |
| negative short sample | T<30 | `ValueError` |
| negative proxy | only RankIC objective, no trial SR | gate 层 block, 不调用 exact DSR |

### B.6 `conservative.py` stub

```python
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ScenarioResult:
    ann_ret: float
    total_return: float
    max_dd: float
    sharpe: float
    n_rejected_fills: int
    threshold: float = 0.0
    passed: bool = False


def _vwap(row: pd.Series) -> float:
    close = float(row.get("close") or 0.0)
    high = float(row.get("high") or close)
    low = float(row.get("low") or close)
    amount = float(row.get("amount") or 0.0)
    volume = float(row.get("volume") or 0.0)
    if amount <= 0 or volume <= 0:
        return close
    candidates = [amount / volume, amount / (volume * 100.0)]
    for px in candidates:
        if low * 0.95 <= px <= high * 1.05:
            return float(px)
    return close


def _blocked_by_limit(row: pd.Series, side: str) -> bool:
    close = float(row.get("close") or 0.0)
    high = float(row.get("high") or close)
    low = float(row.get("low") or close)
    volume = float(row.get("volume") or 0.0)
    if close <= 0 or volume <= 0:
        return True
    near_up = high > 0 and close / high >= 0.995
    near_down = low > 0 and close / low <= 1.005
    if side == "BUY" and near_up:
        return True
    if side in {"SELL", "SWAP_OUT"} and near_down:
        return True
    return False


def _metrics(nav: pd.Series) -> tuple[float, float, float, float]:
    rets = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    ann = float((1.0 + total) ** (252.0 / max(len(rets), 1)) - 1.0)
    peak = nav.cummax()
    max_dd = float((nav / peak - 1.0).min())
    sd = float(rets.std(ddof=1))
    sharpe = float(rets.mean() / sd * np.sqrt(252.0)) if sd > 0 else 0.0
    return ann, total, max_dd, sharpe


def conservative_reprice(
    base_nav: pd.DataFrame,
    trades: pd.DataFrame,
    kline: pd.DataFrame,
    *,
    base_slippage_bps: float,
    threshold: float = 0.0,
) -> ScenarioResult:
    """Reprice fills with +50% slippage, VWAP reference, stronger limit mask."""
    k = kline.set_index(["date", "stock_code"]) if "stock_code" in kline.columns else kline
    penalty = 0.0
    rejected = 0
    slip = base_slippage_bps * 1.5 / 10000.0
    for tr in trades.itertuples(index=False):
        key = (getattr(tr, "date"), getattr(tr, "stock_code", ""))
        row = k.loc[key] if key in k.index else None
        if row is None:
            rejected += 1
            penalty += abs(float(getattr(tr, "gross_amount", 0.0))) * slip
            continue
        side = str(getattr(tr, "type"))
        if _blocked_by_limit(row, side):
            rejected += 1
            penalty += abs(float(getattr(tr, "gross_amount", 0.0))) * 0.01
            continue
        px = _vwap(row)
        old_px = float(getattr(tr, "price"))
        shares = float(getattr(tr, "shares"))
        worse = max(px - old_px, 0.0) if side == "BUY" else max(old_px - px, 0.0)
        penalty += shares * worse + abs(shares * px) * slip

    nav = base_nav.sort_values("date")["total_value"].astype(float).copy()
    nav.iloc[-1] = max(nav.iloc[-1] - penalty, 1.0)
    ann, total, max_dd, sharpe = _metrics(nav)
    return ScenarioResult(
        ann_ret=ann,
        total_return=total,
        max_dd=max_dd,
        sharpe=sharpe,
        n_rejected_fills=rejected,
        threshold=threshold,
        passed=ann >= threshold,
    )
```

| Fixture | 输入 | 期望 |
|---|---|---|
| positive robust | base ann 30%, slippage+50% 后 ann 8% | `passed=True` |
| negative fragile | base ann 20%, conservative ann -5% | `passed=False` |
| vwap unit股 | amount/volume 落在 high-low | 使用 raw vwap |
| vwap unit手 | amount/(volume*100) 落在 high-low | 使用 hand vwap |
| limit reject | buy close 接近 high / 涨停 | `n_rejected_fills>0` |

### B.7 `gate.py` stub

```python
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class GateVerdict:
    verdict: str
    blockers: list[str]
    warnings: list[str]
    metrics: dict
    source_rows: dict


def _block_if(condition: bool, blockers: list[str], name: str) -> None:
    if condition:
        blockers.append(name)


def evaluate_backtest_gate(
    *,
    pbo_result,
    dsr_result,
    conservative_result,
    is_metric: float,
    oos_metric: float,
    source_rows: dict,
    red_flags: dict | None = None,
) -> GateVerdict:
    blockers: list[str] = []
    warnings: list[str] = []

    gap = max(0.0, is_metric - oos_metric) / max(abs(is_metric), 1e-9)
    _block_if(pbo_result.pbo > 0.20, blockers, "pbo_gt_0_20")
    _block_if(dsr_result.p_conf < 0.95, blockers, "dsr_p_conf_lt_0_95")
    _block_if(conservative_result.ann_ret < 0.0, blockers, "conservative_ann_ret_lt_0")
    _block_if(gap > 0.30, blockers, "is_oos_gap_gt_0_30")

    for key, bad in (red_flags or {}).items():
        _block_if(bool(bad), blockers, f"red_flag_{key}")

    for key, min_rows in {"nav": 60, "trades": 30, "trials": 30}.items():
        if int(source_rows.get(key, 0)) < min_rows:
            blockers.append(f"input_rows_{key}_lt_{min_rows}")

    if 0.10 < pbo_result.pbo <= 0.20:
        warnings.append("pbo_warning_band_0_10_0_20")
    if 0.90 <= dsr_result.p_conf < 0.95:
        warnings.append("dsr_near_miss_0_90_0_95")

    metrics = {
        "pbo": pbo_result.pbo,
        "dsr_p_conf": dsr_result.p_conf,
        "conservative_ann_ret": conservative_result.ann_ret,
        "is_oos_gap_rel": gap,
        "pbo_result": asdict(pbo_result),
        "dsr_result": asdict(dsr_result),
        "conservative_result": asdict(conservative_result),
    }
    return GateVerdict(
        verdict="BLOCK" if blockers else "PASS",
        blockers=blockers,
        warnings=warnings,
        metrics=metrics,
        source_rows=source_rows,
    )
```

| Fixture | 输入 | 期望 |
|---|---|---|
| all pass | PBO 0.08, DSR 0.98, conservative ann 0.04, gap 0.12 | `verdict=PASS` |
| pbo reject | PBO 0.36 | `BLOCK`, blocker contains `pbo_gt_0_20` |
| dsr reject | DSR 0.91 | `BLOCK`, blocker contains `dsr_p_conf_lt_0_95` |
| conservative reject | ann -0.01 | `BLOCK`, blocker contains `conservative_ann_ret_lt_0` |
| gap reject | gap 0.42 | `BLOCK`, blocker contains `is_oos_gap_gt_0_30` |
| row reject | trials 3 | `BLOCK`, blocker contains `input_rows_trials_lt_30` |

### B.8 Module IO table

| 模块 | 输入文件 / 表 | 输入 rows | 输出 object | 落库建议 |
|---|---|---:|---|---|
| `pbo.py` | returns matrix replay artifact | `T x N` | `PBOResult` | `mart_backtest_validation_gate.pbo` |
| `dsr.py` | `mart_paper_sim_nav`, trial returns | `T + N` | `DSRResult` | `mart_backtest_validation_gate.dsr_p_conf` |
| `conservative.py` | `fact_paper_sim_trade`, kline, nav | trades + daily bars | `ScenarioResult` | `mart_backtest_validation_gate.conservative_ann_ret` |
| `gate.py` | all above | 1 candidate | `GateVerdict` | full gate row |

### B.9 Test fixture schema

| Fixture table | 最小字段 | 用途 |
|---|---|---|
| `fixture_nav` | `sim_run_id,date,total_value,daily_ret` | DSR / conservative NAV |
| `fixture_trades` | `sim_run_id,position_id,stock_code,date,type,price,shares,gross_amount,tx_cost,net_amount` | conservative repricing |
| `fixture_trials` | `run_id,trial_number,state,rank_ic_mean,params_json,user_attrs_json` | PBO / DSR trial metadata |

## Part C: backtester-mcp 集成

### C.1 已验证事实

| 项 | 已验证值 | 来源 | 集成影响 |
|---|---|---|---|
| package name | `backtester-mcp` | 来源 3 | pip install 可用 |
| import name | `backtester_mcp` | 来源 3 quick start | Python import 用下划线 |
| latest verified version | `0.1.0` | 来源 3 | 本文按 `0.1.0` 设计 |
| PyPI release date | `2026-04-14` | 来源 3 | 与用户“2026-04 release”一致 |
| GitHub latest release | `v0.1.0`, `2026-04-12` | 来源 4 | tag 与 PyPI 日期差 2 天 |
| license | `Apache-2.0` | 来源 3/4 | 可集成 |
| Python requirement | `>=3.10` | 来源 3 | repo 环境需满足 |
| core deps | numpy, numba, duckdb, pyarrow, pandas, optuna | 来源 3/LobeHub metadata | 与当前 repo 技术栈相近 |
| MCP extra | `mcp>=1.0` optional | 来源 3/LobeHub metadata | MCP server 非必需 |
| persistence | local DuckDB registry | 来源 4 | 可和本项目 DuckDB 分离 |

### C.2 安装方式评估

| 方式 | 命令 | 成熟度 | 风险 | 建议 |
|---|---|---|---|---|
| pip | `pip install backtester-mcp` | 已验证 PyPI | supply-chain / version pin | 二期试验可用, 必须 pin hash |
| pip with extra | `pip install "backtester-mcp[mcp]"` | PyPI exposes extra `mcp` | MCP deps 变动 | 仅 MCP mode 需要 |
| git clone | `git clone https://github.com/bcosm/backtester-mcp` | GitHub public | main branch drift | 用 tag `v0.1.0` |
| local editable | `pip install -e .` inside clone | 可开发调试 | 污染 env | 只在 sandbox venv |
| docker | 官方 Docker image 未在 README/PyPI 明确看到 | 待验证 | 不可编造 image | 一期不采用 |
| vendoring | 复制公式代码 | 可控 | 维护成本 | PBO/DSR 采用 standalone 更稳 |

### C.3 调用 API 评估

| API | 已知接口 | 来源 | 适配成本 | 本项目建议 |
|---|---|---|---:|---|
| CLI backtest | `backtester-mcp backtest -s strategy.py -d data.parquet` | 来源 3/4 | 中 | research sandbox 可试 |
| CLI robustness | `--robustness --execution-scenarios --walk-forward` | 来源 3/4 | 中 | 可对照验证 |
| CLI optimize | `backtester-mcp optimize -s ... -p fast:5:50` | 来源 3/4 | 高 | 不替代现有 Optuna |
| CLI report | `backtester-mcp report ... -o report.html` | 来源 3/4 | 低 | 可产审计 HTML |
| Python lib | `from backtester_mcp import backtest` | 来源 3/4 | 中 | API 细节需 pin version 测试 |
| MCP stdio | `backtester-mcp serve --transport stdio` | 来源 3/4 | 高 | 不进 promote critical path |
| HTTP | README/PyPI 未明确 HTTP server | 待验证 | 高 | 一期不采用 |

### C.4 MCP tools schema

| Tool | 来源说明 | 可替代本项目模块 | 风险 |
|---|---|---|---|
| `backtest_strategy` | run backtest | 不替代 paper_sim | A 股 T+1 / 涨跌停 / 手续费不一致 |
| `validate_strategy` | full validation verdict | 可作为旁路 oracle | verdict 阈值不可直接用于 promote |
| `validate_robustness` | Bootstrap Sharpe CI + DSR + PBO | 可对照 PBO/DSR | 需要验证公式实现 |
| `optimize_parameters` | Bayesian search + PBO | 不替代 mart_p1_optuna_trials | 数据 / universe / PIT 不一致 |
| `compare_strategies` | compare metrics | 可报告 | 非阻断路径 |
| `register_dataset` | file path / CSV / base64 | 可上传 exported data | 大数据不适合 base64 |
| `profile_dataset` | data quality stats | 可补充 | 不懂 A 股交易约束 |
| `save_run` | persist results | 不采用 | 避免双 registry |
| `list_runs` | list local runs | 不采用 | 与本项目无关 |
| `load_run` | load result | 不采用 | 与本项目无关 |
| `compare_runs` | compare saved runs | 可旁路 | 非阻断路径 |
| `generate_report` | HTML report | 可审计 | report 不能当 gate |
| `strategy_template` | template | 不采用 | 策略不是 plain MA crossover |

### C.5 输入数据格式

| 格式 | backtester-mcp 支持状态 | 本项目导出方式 | 一期建议 |
|---|---|---|---|
| CSV | README/PyPI 提到 file path / CSV | DuckDB `COPY (...) TO 'x.csv'` | 小样本 smoke test |
| Parquet | README/PyPI 示例 `datasets/spy_daily.parquet` | DuckDB `COPY (...) TO 'x.parquet'` | 首选 |
| DuckDB | README architecture 写 Data CSV/Parquet/DuckDB | 直接读需 API 验证 | 待验证 |
| base64 | MCP `register_dataset` 提到 base64 | 不适合大回测 | 不用 |
| numpy arrays | Python quick start | Python bridge | 适合 formula smoke |

### C.6 输出 schema 映射

| backtester-mcp 输出字段 | 本项目字段 | 用途 | 是否阻断 |
|---|---|---|---|
| `verdict` | `external_verdict` | 旁路记录 | 否 |
| `reasons` | `external_reasons_json` | 审计 | 否 |
| `metrics.sharpe` | `sharpe` | 对照 | 否 |
| `metrics.max_drawdown` | `max_dd` | 对照 | 否 |
| `metrics.cagr` | `annual_return` | 对照 | 否 |
| `pbo.pbo` | `pbo` | 可比对 | 是, 仅公式验证后 |
| `scenarios.conservative` | `conservative_ann_ret` | 可比对 | 否, A 股 simulator 优先 |
| `bootstrap` | `bootstrap_ci` | 可增强 DSR | 否 |
| `manifest` | `manifest_json` | 审计 | 否 |

### C.7 集成决策

| 决策项 | 结论 | 原因 |
|---|---|---|
| promote critical path | 不依赖外部 MCP 服务 | 网络 / MCP server / package drift 不应影响实盘 gate |
| PBO/DSR | 本项目 standalone 实现 | 公式短, 可单测, 可审计 |
| backtester-mcp | 作为旁路 oracle | 对照验证和 HTML report 有价值 |
| conservative scenario | 本项目自研 | A 股 VWAP、涨跌停、T+1、volume unit 必须本地化 |
| CLI | 可二期接入 | 先导出 Parquet 后跑 `validate_robustness` |
| HTTP | 不采用 | 待验证 |
| Docker | 不采用 | 待验证 |

### C.8 Standalone PBO/DSR implementation

```python
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import e, log, sqrt
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True)
class RobustnessReport:
    pbo: float
    dsr_p_conf: float
    dsr_z: float
    selected_daily_sr: float
    sr_star: float
    n_obs: int
    n_trials: int
    pbo_pass: bool
    dsr_pass: bool


def _sharpe(x: np.ndarray) -> np.ndarray:
    mu = np.nanmean(x, axis=0)
    sd = np.nanstd(x, axis=0, ddof=1)
    return np.divide(mu, sd, out=np.zeros_like(mu), where=sd > 0)


def compute_pbo(returns_matrix: np.ndarray, n_splits: int = 8) -> tuple[float, list[float]]:
    x = np.asarray(returns_matrix, dtype=float)
    if x.ndim != 2:
        raise ValueError("returns_matrix must be time x strategy")
    t, n = x.shape
    if n < 2:
        raise ValueError("need at least two strategies")
    if n_splits < 4 or n_splits % 2:
        raise ValueError("n_splits must be even and >=4")
    if t < n_splits:
        raise ValueError("not enough observations")
    blocks = np.array_split(np.arange(t), n_splits)
    lambdas: list[float] = []
    for is_blocks in combinations(range(n_splits), n_splits // 2):
        is_idx = np.concatenate([blocks[i] for i in is_blocks])
        oos_idx = np.concatenate([blocks[i] for i in range(n_splits) if i not in is_blocks])
        is_scores = _sharpe(x[is_idx])
        oos_scores = _sharpe(x[oos_idx])
        winner = int(np.nanargmax(is_scores))
        order = np.argsort(oos_scores)
        rank = int(np.where(order == winner)[0][0]) + 1
        omega = min(max((rank - 0.5) / n, 1e-12), 1.0 - 1e-12)
        lambdas.append(log(omega / (1.0 - omega)))
    return float(np.mean(np.asarray(lambdas) < 0.0)), lambdas


def _skew_kurt(x: np.ndarray) -> tuple[float, float]:
    y = x[np.isfinite(x)]
    mu = float(np.mean(y))
    sd = float(np.std(y, ddof=1))
    if sd <= 0:
        return 0.0, 3.0
    z = (y - mu) / sd
    return float(np.mean(z**3)), float(np.mean(z**4))


def expected_max_sr(trial_srs: np.ndarray, n_eff: float | None = None) -> float:
    sr = np.asarray(trial_srs, dtype=float)
    sr = sr[np.isfinite(sr)]
    if len(sr) < 2:
        raise ValueError("need at least two trial SRs")
    n = max(float(n_eff or len(sr)), 2.0)
    mu = float(np.mean(sr))
    sigma = float(np.std(sr, ddof=1))
    nd = NormalDist()
    gamma = 0.5772156649015329
    max_z = (1.0 - gamma) * nd.inv_cdf(1.0 - 1.0 / n)
    max_z += gamma * nd.inv_cdf(1.0 - 1.0 / (n * e))
    return mu + sigma * max_z


def compute_dsr(selected_returns: np.ndarray, trial_returns_matrix: np.ndarray) -> tuple[float, float, float, float]:
    r = np.asarray(selected_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 30:
        raise ValueError("DSR requires >=30 observations")
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        raise ValueError("zero variance selected returns")
    selected_sr = float(np.mean(r) / sd)
    trial_srs = _sharpe(np.asarray(trial_returns_matrix, dtype=float))
    sr_star = expected_max_sr(trial_srs)
    skew, kurt = _skew_kurt(r)
    denom = max(1.0 - skew * selected_sr + ((kurt - 1.0) / 4.0) * selected_sr**2, 1e-12)
    z = (selected_sr - sr_star) * sqrt(len(r) - 1.0) / sqrt(denom)
    return float(NormalDist().cdf(z)), float(z), selected_sr, float(sr_star)


def robustness_report(
    selected_returns: np.ndarray,
    trial_returns_matrix: np.ndarray,
    *,
    n_splits: int = 8,
    pbo_threshold: float = 0.20,
    dsr_threshold: float = 0.95,
) -> RobustnessReport:
    matrix = np.asarray(trial_returns_matrix, dtype=float)
    pbo, _ = compute_pbo(matrix, n_splits=n_splits)
    dsr_p, dsr_z, sr, sr_star = compute_dsr(selected_returns, matrix)
    return RobustnessReport(
        pbo=pbo,
        dsr_p_conf=dsr_p,
        dsr_z=dsr_z,
        selected_daily_sr=sr,
        sr_star=sr_star,
        n_obs=int(len(selected_returns)),
        n_trials=int(matrix.shape[1]),
        pbo_pass=pbo <= pbo_threshold,
        dsr_pass=dsr_p >= dsr_threshold,
    )
```

| Standalone 行为 | 设计 |
|---|---|
| 外部服务依赖 | 无 MCP server |
| 统计依赖 | `numpy`, Python stdlib `statistics.NormalDist` |
| PBO 输入 | `time x strategy` daily returns matrix |
| DSR 输入 | selected daily returns + trial returns matrix |
| 输出 | `RobustnessReport` |
| promote 适用 | 是, 若 returns matrix 来自 PIT replay |

### C.9 backtester-mcp smoke command

```bash
# 旁路 smoke, 不进入 promote critical path
python -m pip install "backtester-mcp==0.1.0"
backtester-mcp backtest \
  -s /tmp/chunkymonkey_strategy_stub.py \
  -d /tmp/chunkymonkey_export.parquet \
  --robustness \
  --execution-scenarios
```

```bash
# MCP stdio 配置示例, 仅供验证
backtester-mcp serve --transport stdio
```

## Part D: 5 步 Execution Plan

### D.1 Step 总览

| Step | 步骤名 | 主要路径 | 预计工时(h) | 验收数字 |
|---:|---|---|---:|---|
| 1 | 实现 PBO + DSR 公式 | `backend/services/backtest_validation/pbo.py`, `dsr.py` | 6 | unit pass, PBO reject >0.20, DSR reject <0.95 |
| 2 | conservative scenario simulator | `backend/services/backtest_validation/conservative.py` | 8 | slippage +50%, VWAP sanity, ann_ret floor |
| 3 | gate.py 综合 verdict | `backend/services/backtest_validation/gate.py` | 5 | all blockers deterministic |
| 4 | wire 进 promote_champion.py 阻断 commit | `backend/scripts/promote_champion.py` 或现有 promote entry | 5 | BLOCK 时不写 champion |
| 5 | 回测历史 corrupt strategies | test fixtures + SQL | 8 | 4 个反例全部 reject |
| 合计 | scaffold 到 promote gate |  | 32 | 0 个反例漏放 |

### D.2 Step 1: PBO + DSR

| 项 | 内容 |
|---|---|
| 步骤名 | 实现 PBO + DSR 公式 |
| code file path | `backend/services/backtest_validation/pbo.py` |
| code file path | `backend/services/backtest_validation/dsr.py` |
| test path | `backend/tests/backtest_validation/test_pbo.py` |
| test path | `backend/tests/backtest_validation/test_dsr.py` |
| 输入 mart | `mart_paper_sim_nav`, `mart_p1_optuna_trials` |
| 输入 rows | NAV `>=60`, trials or variants `>=30` |
| 输出 | `PBOResult`, `DSRResult` |
| 验收数字 1 | PBO stable fixture `<=0.20` |
| 验收数字 2 | PBO overfit fixture `>0.20` |
| 验收数字 3 | DSR significant fixture `>=0.95` |
| 验收数字 4 | DSR non-significant fixture `<0.95` |
| 工作量 | `6h` |

| Positive case | 数据 | 预期 |
|---|---|---|
| stable perturbations | 504 daily rows, 50 variants, OOS rank stable | `pbo <= 0.20`, `dsr_p_conf >= 0.95` |
| exact daily returns | selected return in top quartile across CSCV | `Gate PASS for Step 1` |

| Negative reject case | 数据 | 预期 |
|---|---|---|
| overfit winner | IS best becomes OOS below median in >20% combos | `pbo > 0.20`, reject |
| multiple testing inflation | 500 trial SRs, selected SR not above `SR_star` | `dsr_p_conf < 0.95`, reject |
| insufficient trials | current `mart_p1_optuna_trials=3` | reject input insufficient |

```sql
-- Step 1 source row verification
SELECT
    (SELECT COUNT(*) FROM mart_paper_sim_nav WHERE sim_run_id = $sim_run_id) AS nav_rows,
    (SELECT COUNT(*) FROM mart_p1_optuna_trials WHERE run_id = $optuna_run_id AND state = 'COMPLETE') AS trial_rows;
```

```bash
rg -n "def pbo_cscv|def deflated_sharpe_ratio|PBOResult|DSRResult" \
  backend/services/backtest_validation backend/tests/backtest_validation
```

### D.3 Step 2: Conservative Scenario Simulator

| 项 | 内容 |
|---|---|
| 步骤名 | conservative scenario simulator |
| code file path | `backend/services/backtest_validation/conservative.py` |
| test path | `backend/tests/backtest_validation/test_conservative.py` |
| 输入 mart | `fact_paper_sim_trade`, `mart_paper_sim_nav`, kline view |
| 输入 rows | trades `>=30`, NAV `>=60`, kline coverage `>=99% trade dates` |
| 输出 | `ScenarioResult` |
| 验收数字 1 | `slippage_bps = base * 1.50` |
| 验收数字 2 | VWAP in `[low*0.95, high*1.05]` |
| 验收数字 3 | conservative `ann_ret >= 0` 才 pass |
| 工作量 | `8h` |

| Positive case | 数据 | 预期 |
|---|---|---|
| robust fills | base ann 20%, conservative ann 4% | pass |
| mixed volume unit | akshare 股 + tdxhub 手 | VWAP 选择合理候选 |

| Negative reject case | 数据 | 预期 |
|---|---|---|
| fragile fills | close 成交盈利, VWAP+滑点后 ann -2% | reject |
| limit blocked | buy near upper limit, sell near lower limit | rejected fills >0, conservative ann 下降 |
| vwap broken | amount/volume 和 amount/(volume*100) 都越界 | fallback / reject, 不用异常价 |

```sql
-- Step 2 trade/kline coverage scaffold
WITH trades AS (
    SELECT DISTINCT date, position_id
    FROM fact_paper_sim_trade
    WHERE sim_run_id = $sim_run_id
),
k AS (
    SELECT DISTINCT date
    FROM market.v_price_kline_qfq
)
SELECT
    COUNT(*) AS trade_dates,
    SUM(CASE WHEN k.date IS NOT NULL THEN 1 ELSE 0 END) AS covered_dates,
    SUM(CASE WHEN k.date IS NOT NULL THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS coverage
FROM trades t
LEFT JOIN k ON k.date = t.date;
```

```bash
rg -n "_vwap|conservative_reprice|blocked_by_limit|slippage.*1.5" \
  backend/services/backtest_validation backend/tests/paper_sim/test_vwap.py
```

### D.4 Step 3: gate.py 综合 verdict

| 项 | 内容 |
|---|---|
| 步骤名 | gate.py 综合 verdict |
| code file path | `backend/services/backtest_validation/gate.py` |
| test path | `backend/tests/backtest_validation/test_gate.py` |
| 输入 | `PBOResult`, `DSRResult`, `ScenarioResult`, IS/OOS metrics |
| 输出 | `GateVerdict` |
| 验收数字 1 | `PBO > 0.20` 必 block |
| 验收数字 2 | `DSR p_conf < 0.95` 必 block |
| 验收数字 3 | `conservative_ann_ret < 0` 必 block |
| 验收数字 4 | `gap_rel > 0.30` 必 block |
| 工作量 | `5h` |

| Positive case | 数据 | 预期 |
|---|---|---|
| clean candidate | PBO 0.08, DSR 0.98, conservative 0.03, gap 0.12 | `PASS`, blockers empty |

| Negative reject case | 数据 | 预期 |
|---|---|---|
| one hard fail | 任一 gate fail | `BLOCK` |
| red flag fail | ann 1.14 or Sharpe 5.1 | `BLOCK` |
| row fail | trials rows 3 | `BLOCK` |

```sql
-- Step 3 gate row verification scaffold
SELECT
    gate_run_id,
    candidate_id,
    verdict,
    pbo,
    dsr_p_conf,
    conservative_ann_ret,
    is_oos_gap_rel,
    blockers_json
FROM mart_backtest_validation_gate
WHERE candidate_id = $candidate_id
ORDER BY computed_at DESC
LIMIT 5;
```

### D.5 Step 4: Wire into promote

| 项 | 内容 |
|---|---|
| 步骤名 | wire 进 promote_champion.py 阻断 commit |
| code file path | `backend/scripts/promote_champion.py` |
| fallback path | `backend/services/portfolio/champion.py::register_champion` |
| 当前实际 promote 线索 | `backend/services/portfolio/champion.py` 有 champion registry |
| 输入 | candidate id, sim_run_id, optuna_run_id |
| 输出 | promote success or exception |
| 验收数字 1 | `verdict=BLOCK` 时 `mart_champion_model.is_current_champion` 不变化 |
| 验收数字 2 | `verdict=PASS` 时写入 promoted_reason 包含 gate_run_id |
| 工作量 | `5h` |

| Positive case | 数据 | 预期 |
|---|---|---|
| gate pass | gate verdict PASS | promote writes champion |
| idempotent pass | same gate_run_id rerun | no duplicate current champion |

| Negative reject case | 数据 | 预期 |
|---|---|---|
| gate block | PBO 0.36 | promote command exits non-zero |
| missing gate | no gate row for candidate | promote exits non-zero |
| stale gate | gate computed before latest sim run | promote exits non-zero |

```bash
rg -n "register_champion|is_current_champion|promote|promoted_reason" \
  backend/scripts backend/services/portfolio backend/tests
```

```sql
-- Step 4 promote no-write verification
SELECT champion_id, model_id, is_current_champion, promoted_at, promoted_reason
FROM mart_champion_model
ORDER BY built_at DESC
LIMIT 10;
```

### D.6 Step 5: Corrupt strategy replay

| 项 | 内容 |
|---|---|
| 步骤名 | 回测历史 corrupt strategies 看 gate 是否阻断 |
| code file path | `backend/tests/backtest_validation/test_historical_corrupt_rejects.py` |
| SQL path | `docs/backtester_mcp_integration_20260517.md` Part E SQL |
| 输入 | 4 类历史假 alpha / leakage fixture |
| 输出 | `GateVerdict.BLOCK` |
| 验收数字 1 | 4/4 反例被 block |
| 验收数字 2 | 每个反例至少命中 1 个 hard blocker |
| 验收数字 3 | PBO 反例输出 `>0.20` 或 DSR `<0.95` 或 conservative `<0` |
| 工作量 | `8h` |

| Positive case | 数据 | 预期 |
|---|---|---|
| known clean | PIT walk-forward, no red flags | gate pass if all numeric gates pass |

| Negative reject case | 数据 | 预期 |
|---|---|---|
| all-period Optuna | IS=OOS leakage | reject |
| latest snapshot institution | `win_rate_60d` no as-of | reject |
| volume unit leakage | vwap x100 / /100 false fill | reject |
| v3 chain latest leak | RankIC +60% | reject |

```bash
rg -n "mart_per_stock_stage_strategy_optimal|win_rate_60d|volume|vwap|inst_path_a|RankIC|\\+312|0\\.0353|0\\.0246" \
  CLAUDE.md PROJECT_INDEX.md docs backend
```

## Part E: 反例校验

### E.1 反例总表

| 反例 ID | 历史假 alpha | 主要泄漏 | 必须命中的 gate | 期望输出 |
|---:|---|---|---|---|
| 1 | `mart_per_stock_stage_strategy_optimal` 全期 Optuna fit | IS=OOS leakage | PBO, IS-OOS gap, red flag | `BLOCK` |
| 2 | `mart_institution_profile.win_rate_60d` latest snapshot | latest-snapshot leakage | DSR, IS-OOS gap, red flag | `BLOCK` |
| 3 | `volume_unit` leakage | VWAP `x100` 或 `/100` 错价 | conservative | `BLOCK` |
| 4 | v3 chain RankIC +60% | `inst_path_a` latest snapshot leak | IS-OOS gap, red flag, PBO | `BLOCK` |

### E.2 反例 1: 全期 Optuna fit

| 项 | 值 |
|---|---|
| 表 | `mart_per_stock_stage_strategy_optimal` |
| 泄漏方式 | `sharpe` 使用全期 in-sample fit, selector 用历史未来最佳参数 |
| 本地证据 | `built_at` distinct = `1`, 当前 rows = `1725` |
| 历史后果 | paper_sim `+312%` / 高胜率假象 |
| gate 期望 | `PBO > 0.20` 或 `gap_rel > 0.30` |
| action | block promote + force retrain with walk-forward |

```sql
-- E.2.1 检测 stage optimal 是否单批 built_at
SELECT
    COUNT(*) AS rows,
    MIN(built_at) AS min_built_at,
    MAX(built_at) AS max_built_at,
    COUNT(DISTINCT built_at) AS distinct_built_at
FROM mart_per_stock_stage_strategy_optimal;
```

```sql
-- E.2.2 检测 walk_forward_mode 和 OOS 字段
SELECT
    walk_forward_mode,
    COUNT(*) AS rows,
    AVG(sharpe) AS avg_is_sharpe,
    AVG(oos_sharpe) AS avg_oos_sharpe,
    AVG(sharpe - COALESCE(oos_sharpe, 0)) AS avg_gap
FROM mart_per_stock_stage_strategy_optimal
GROUP BY walk_forward_mode
ORDER BY rows DESC;
```

```bash
rg -n "ORDER BY .*sharpe|COALESCE\\(oos_sharpe|walk_forward_mode='none'|mart_per_stock_stage_strategy_optimal" \
  backend/services backend/scripts backend/tests CLAUDE.md PROJECT_INDEX.md
```

| Gate 输出校准 | 目标数字 |
|---|---:|
| `pbo` | `>0.20` |
| `is_oos_gap_rel` | `>0.30` |
| `red_flag_walk_forward_none` | `true` |
| `verdict` | `BLOCK` |

### E.3 反例 2: Institution latest snapshot leakage

| 项 | 值 |
|---|---|
| 表 | `mart_institution_profile` |
| 字段 | `win_rate_60d`, `buy_win_rate_60d`, `quality_score` |
| 泄漏方式 | 用 latest snapshot 的机构胜率回填历史 signal_date |
| 历史后果 | chain 实跑 RankIC +60% 假象 |
| gate 期望 | DSR `p_conf < 0.95` 或 IS-OOS gap `>0.30` |
| action | block promote + freeze PIT feature source |

```sql
-- E.3.1 检查 profile 是否只有 updated_at/latest_notice_date 而无 signal_date/as_of_date 粒度
SELECT
    COUNT(*) AS rows,
    MIN(updated_at) AS min_updated_at,
    MAX(updated_at) AS max_updated_at,
    MIN(latest_notice_date) AS min_latest_notice_date,
    MAX(latest_notice_date) AS max_latest_notice_date
FROM mart_institution_profile;

-- E.3.2 查训练面板中 inst_path_a / institution profile 衍生列
SELECT column_name
FROM information_schema.columns
WHERE table_name IN ('mart_p0a_feature_label_panel_v3', 'mart_p0a_feature_label_panel_v4')
  AND (
      column_name LIKE 'inst_%'
      OR column_name LIKE '%win_rate_60d%'
      OR column_name LIKE '%quality%'
  )
ORDER BY column_name;
```

```bash
rg -n "mart_institution_profile|win_rate_60d|buy_win_rate_60d|inst_path_a|latest snapshot|latest-snapshot" \
  backend docs CLAUDE.md PROJECT_INDEX.md
```

| Gate 输出校准 | 目标数字 |
|---|---:|
| `dsr_p_conf` | `<0.95` |
| `is_oos_gap_rel` | `>0.30` |
| `red_flag_rankic_uplift_rel` | `true` when `>=0.50` |
| `verdict` | `BLOCK` |

### E.4 反例 3: volume_unit leakage

| 项 | 值 |
|---|---|
| 表 / 函数 | kline, `_vwap`, `pricing_sql.qfq_vwap_expr` |
| 泄漏方式 | akshare volume=股, tdxhub volume=手, 错用固定 `/100` 或不 `/100` |
| 历史后果 | VWAP `0.11` 元级错误, stop_hit 假信号, NAV 大幅错杀 |
| gate 期望 | conservative `ann_ret < 0` 或 fill reject |
| action | block promote + price sanity audit |

```sql
-- E.4.1 找 VWAP 明显脱离 high/low 的 K 线
SELECT
    date,
    stock_code,
    open,
    high,
    low,
    close,
    volume,
    amount,
    amount / NULLIF(volume, 0) AS vwap_raw,
    amount / NULLIF(volume * 100.0, 0) AS vwap_hand
FROM market.v_price_kline_qfq
WHERE volume > 0
  AND amount > 0
  AND NOT (
      amount / NULLIF(volume, 0) BETWEEN low * 0.95 AND high * 1.05
      OR amount / NULLIF(volume * 100.0, 0) BETWEEN low * 0.95 AND high * 1.05
  )
LIMIT 100;
```

```bash
rg -n "_vwap|volume.*100|amount / volume|amount/volume|vwap_raw|vwap_hand" \
  backend/services backend/tests
```

| Gate 输出校准 | 目标数字 |
|---|---:|
| `conservative_ann_ret` | `<0` for corrupt fixture |
| `n_rejected_fills` | `>0` |
| `red_flag_vwap_out_of_range` | `true` |
| `verdict` | `BLOCK` |

### E.5 反例 4: v3 chain RankIC +60%

| 项 | 值 |
|---|---|
| 反例 | v3 chain RankIC +60% |
| 泄漏源 | `inst_path_a` latest snapshot leak |
| 本地 baseline | clean P0b RankIC `0.0108-0.0203`, roadmap `0.0246` |
| red flag | relative uplift `>=0.50` |
| gate 期望 | `is_oos_gap_rel > 0.30` 或 `red_flag_rankic_uplift_rel=true` |
| action | block promote + feature ablation / PIT freeze |

```sql
-- E.5.1 RankIC uplift 检测 scaffold
WITH baseline AS (
    SELECT 0.0246::DOUBLE AS rank_ic_baseline
),
candidate AS (
    SELECT AVG(rank_ic_mean) AS rank_ic_candidate
    FROM mart_p1_optuna_trials
    WHERE run_id = $optuna_run_id
      AND state = 'COMPLETE'
)
SELECT
    c.rank_ic_candidate,
    b.rank_ic_baseline,
    (c.rank_ic_candidate - b.rank_ic_baseline) / NULLIF(ABS(b.rank_ic_baseline), 0) AS uplift_rel,
    CASE
        WHEN (c.rank_ic_candidate - b.rank_ic_baseline) / NULLIF(ABS(b.rank_ic_baseline), 0) >= 0.50
        THEN 'BLOCK'
        ELSE 'PASS_NUMERIC_ONLY'
    END AS red_flag_verdict
FROM candidate c
CROSS JOIN baseline b;
```

```bash
rg -n "0\\.0246|0\\.0108|0\\.0203|0\\.0353|RankIC|inst_path_a|relative.*50|\\+60%" \
  CLAUDE.md docs backend gcp
```

| Gate 输出校准 | 目标数字 |
|---|---:|
| `rank_ic_uplift_rel` | `>=0.50` |
| `is_oos_gap_rel` | `>0.30` if OOS fails to reproduce |
| `pbo` | `>0.20` after perturbation / CSCV |
| `verdict` | `BLOCK` |

### E.6 Corrupt replay acceptance matrix

| 反例 ID | Fixture name | PBO | DSR p_conf | Conservative ann | Gap | Red flag | Verdict |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | `all_period_optuna_fit` | `>0.20` | any | any | `>0.30` | `walk_forward_none` | `BLOCK` |
| 2 | `institution_latest_snapshot` | any | `<0.95` | any | `>0.30` | `rankic_uplift` | `BLOCK` |
| 3 | `volume_unit_vwap_corrupt` | any | any | `<0.00` | any | `vwap_out_of_range` | `BLOCK` |
| 4 | `v3_inst_path_a_chain` | `>0.20` | `<0.95` | any | `>0.30` | `rankic_uplift` | `BLOCK` |

### E.7 Evidence-only verification

| 验证目标 | 命令类型 | 通过条件 |
|---|---|---|
| PBO code exists | `rg` | 找到 `pbo_cscv` |
| DSR code exists | `rg` | 找到 `deflated_sharpe_ratio` |
| conservative code exists | `rg` | 找到 `conservative_reprice` |
| gate code exists | `rg` | 找到 `evaluate_backtest_gate` |
| SQL row evidence | DuckDB query | 输出 rows / metrics |
| corrupt rejects | pytest | 4/4 reject |
| promote block | SQL diff | champion row 不变化 |

```bash
# 不接受 checkbox 自报: grep + pytest 必须给出机器可读证据
rg -n "pbo_cscv|deflated_sharpe_ratio|conservative_reprice|evaluate_backtest_gate" \
  backend/services/backtest_validation backend/tests/backtest_validation

PYTHONPATH=backend pytest -q \
  backend/tests/backtest_validation/test_pbo.py \
  backend/tests/backtest_validation/test_dsr.py \
  backend/tests/backtest_validation/test_conservative.py \
  backend/tests/backtest_validation/test_gate.py \
  backend/tests/backtest_validation/test_historical_corrupt_rejects.py
```

```sql
-- 不接受 checkbox 自报: gate 结果必须落库可查
SELECT
    candidate_id,
    verdict,
    blockers_json,
    pbo,
    dsr_p_conf,
    conservative_ann_ret,
    is_oos_gap_rel,
    source_rows_json,
    computed_at
FROM mart_backtest_validation_gate
WHERE candidate_id = $candidate_id
ORDER BY computed_at DESC
LIMIT 1;
```

### E.8 Final promote rule

| Rule | 条件 | 结果 |
|---:|---|---|
| 1 | `PBO <= 0.20` | 继续 |
| 2 | `DSR p_conf >= 0.95` | 继续 |
| 3 | `conservative_ann_ret >= 0.00` | 继续 |
| 4 | `IS-OOS gap_rel <= 0.30` | 继续 |
| 5 | 无 red flag | 继续 |
| 6 | NAV rows `>=60`, trades `>=30`, trials/variants `>=30` | 继续 |
| 7 | gate row 新于 candidate sim/kpi | 继续 |
| 8 | 全部继续 | `PASS` |
| 9 | 任一不满足 | `BLOCK` |

| promote verdict | 允许操作 | 禁止操作 |
|---|---|---|
| `PASS` | 写 champion registry, reason 包含 gate_run_id | 无 gate id 的人工 promote |
| `BLOCK` | 写 gate audit, 输出 blockers | 写 `is_current_champion=TRUE` |
| `WARN` | research report | live promote |
| `INPUT_MISSING` | 补数据 / replay | promote |

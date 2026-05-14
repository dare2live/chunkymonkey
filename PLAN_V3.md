# PLAN_V3.md — ML Ranking 主导实施计划 (v3.2 共识版)

> 创建：2026-05-14 (Claude × Codex 三轮讨论后共识版)
> 状态：待启动 P-1
> 目标：真实成本、T+1、停牌/涨跌停、流动性/容量约束下，paper_sim 年化 ≥30%、max_dd ≥-20%、超额 vs HS300 >0、月胜率 ≥55%
> 核心变更：废弃 V3 两路合并，改为 ML ranking 主导；公式与机构逻辑降为特征、baseline、解释层
> Codex 历史 agent: `a15203724858923e8` (后续 review 可 `--resume` 复用)

---

## §0 基线与废弃项

### 0.1 当前实测基线

| 项 | 实测 | 结论 |
|---|---:|---|
| paper_sim 13-alpha hp=15 | ann +3.78% / mdd -30.1% / sharpe +0.29 | 当前真钱基线, 未达标 |
| per_stock_stage ceiling test | ann -26.5% | v2 per-stock/stage ensemble 路线证伪 |
| portfolio_walk_forward | +45.4%, 不含 tx_cost/T+1/流动性 | 假 winner, 禁止作为决策依据 |
| 终极目标差距 | +3.78% → +30% = +26.22pp | 必须修 alpha, 不修目标 |

### 0.2 废弃项

| 项 | 原因 | 动作 |
|---|---|---|
| `paper_sim_ensemble.yaml` 的 `ensemble_alphas` | 拼权重不是 alpha, 且未证明成本后有效 | 退役, 不进入 v3.2 主路径 |
| A 路机构 + B 路公式 + Optuna 权重合并 | 二次过拟合风险高, 无法隔离 alpha 来源 | 退役 |
| `portfolio_walk_forward +45.4%` | 不含真实交易约束 | 标注为历史反例 |
| per-stock/stage 作为主选择器 | ceiling ann -26.5% | 只保留为特征/benchmark |
| 公式独立主导买入 | 未证明收益能力 | 降级为 baseline/解释层 |

### 0.3 保留资产

| 资产 | 行数/状态 | v3.2 用途 |
|---|---:|---|
| `fact_alpha158_panel` | 4,022,758 行 | ML ranking 核心特征 |
| `fact_institution_event` | 35K | 事件特征 |
| `fact_lhb_event` | 52K | 事件/情绪/异动特征 |
| `mart_institution_industry_stat` | 4,807 行 | 机构×行业质量特征 |
| `mart_institution_profile` | 231 行 | 机构画像特征 |
| `mart_per_stock_stage_strategy_optimal` | 17,663 行 | 公式历史表现特征/benchmark |
| `event_simulator.py` | 已有 | 事件特征生成与回放 |
| paper_sim | 已有 | 成本后真实验证引擎 |
| Optuna governance / walk_forward | 已有 | R1 expanding_monthly 验证标准 |

---

## §1 数据窗口 + walk-forward expanding_monthly 详细 schema

### 1.1 标准切分

固定 24 月训练 / 11 月验证 / 6 月 holdout 废弃。v3.2 统一使用 `walk_forward.expanding_monthly`, 即每个月月末重训, 下一月 OOS。

| 类型 | 定义 | 用途 |
|---|---|---|
| Train | 从起始交易日到 OOS 前一月最后交易日 | 训练模型, 拟合 scaler/encoder |
| OOS | 下一自然月全部交易日 | 生成预测, paper_sim, RankIC |
| Validation stitched OOS | 非 final 的多个 OOS 月拼接 | ablation, Optuna, P0/P1/P2 gate |
| Final holdout stitched OOS | 最近 6 个 OOS 月拼接 | P3 最终验收, 只读一次 |

### 1.2 月度 walk-forward timeline

| 轮次 | 训练窗口 | OOS 窗口 | 输出 |
|---|---|---|---|
| R1-M01 | start → 2024-03 月末 | 2024-04 | score / RankIC / paper_sim |
| R1-M02 | start → 2024-04 月末 | 2024-05 | score / RankIC / paper_sim |
| R1-M03 | start → 2024-05 月末 | 2024-06 | score / RankIC / paper_sim |
| ... | 每月 expanding | 下一月 | stitched OOS |
| Final-M01 | start → final 前一月末 | 最近第 6 个月 | final OOS 片段 |
| Final-M06 | start → final 前一月末 | 最近第 1 个月 | final OOS 片段 |

> 具体日期由交易日历生成, 不硬编码。final holdout = 最近 6 个 OOS 月 stitched; P3 前不得用于调参。

### 1.3 结果表 schema

| 字段 | 类型 | 要求 |
|---|---|---|
| `run_id` | string | 唯一, 包含 phase/model/seed |
| `walk_forward_mode` | string | 必须为 `expanding_monthly` |
| `train_start` | date | 起始交易日 |
| `train_end` | date | OOS 前一月最后交易日 |
| `oos_start` | date | OOS 月首个交易日 |
| `oos_end` | date | OOS 月最后交易日 |
| `is_final_holdout` | bool | 最近 6 个 OOS 月为 true |
| `model_version` | string | 特征集 + label + 模型 + seed hash |
| `feature_version` | string | 特征生成版本 |
| `label_version` | string | label 生成版本 |
| `rank_ic` | float | 当月横截面 RankIC |
| `ann_ret_cost_after` | float | stitched 后计算 |
| `max_dd_cost_after` | float | stitched 后计算 |
| `turnover` | float | 月换手 |
| `tx_cost_pct` | float | 成本占资产比例 |
| `capacity_concentration` | float | 容量/集中度惩罚输入 |

---

## §2 Phase 路线

### P-1 数据审计

| 项 | 内容 |
|---|---|
| 目标 | 证明训练数据可用于真钱模拟 |
| 动作 | 新增/修复 PIT、退市、ST、停牌、涨跌停、复权、上市首日、事件 timestamp、股票池覆盖审计 |
| 脚本 | `audit_pit_integrity.py`、`audit_survivorship.py`、`audit_tradeability.py`、`audit_event_timestamp.py`、`audit_universe_coverage.py` |
| Universe | **用户硬编码 KEEP universe** = active 60/00/30/68 (沪深主板+创业板+科创板), 由 `services/universe.py::is_active_a_share` 守门; 个人散户 5 仓位场景接受生存者偏差换简化, 不交易退市/三板; ETF (15/51/56/58) 后续单独 enable, **不硬编码进 EXCLUDED** |
| Go metric | PIT FAIL=0; 不可交易状态覆盖率=100%; KEEP universe 历史 K 线 coverage ≥ 99%; 事件 timestamp 非空率 ≥99.5% |
| No-go | 任一 PIT FAIL; 停牌/涨跌停未进入 paper_sim 过滤; KEEP universe coverage < 95% (active 股 K 线缺口) |
| 估时 | 2 天 |

### P0a 特征与 Label 闭环

| 项 | 内容 |
|---|---|
| 特征 | alpha158 + 事件特征 + LHB/机构行为 + 流动性 + 风险因子 + 公式触发哑变量 |
| Label | T+1 可成交入场后, 未来 5/10/20 日成本后收益 |
| 成本 | 佣金、印花税、过户费、滑点、不可成交 mask |
| 机构路径 A | stock-date 粒度: 持仓/相关机构列表 JOIN `mart_institution_industry_stat`, 生成 `inst_quality_avg/max/count` |
| 机构路径 B | event-date-stock 粒度: `fact_institution_event` 触发时 JOIN 机构×行业质量, 生成 `event_quality_score/event_decay` |
| 公式用途 | 公式触发、历史 OOS 表现、stage 最优参数作为特征; 不直接主导买入 |
| Go metric | feature panel 可复现; label 已扣成本; 不可成交样本 mask 生效; 核心特征 PIT audit 通过 |
| No-go | label 未扣成本; 机构 join 无法追溯; 核心特征存在前视 |
| 估时 | 1.5-2 天 |

### P0b ML Ranking + R1 Walk-forward

| 项 | 内容 |
|---|---|
| 模型 | LightGBM pointwise 首跑; LambdaMART pairwise 做同特征 ablation |
| 验证 | 恢复 `walk_forward.expanding_monthly`, 每月重训、下一月 OOS |
| 输出 | 月度 OOS score、RankIC、分位收益、换手、成本后 paper_sim |
| Go metric | validation stitched OOS RankIC ≥0.03; 成本后 ann > +3.78%; mdd 不差于 -30.1% |
| 阈值依据 | RankIC 0.03 作为可交易横截面模型下限; 收益必须击败当前真钱基线 |
| No-go | RankIC <0.03; 成本后不胜基线; 收益集中于单月/单行业 |
| 估时 | 2-3 天 |

### P0c paper_sim Selector Refactor

| 项 | 内容 |
|---|---|
| 决策 | 采用 Option A: ML score 替换 selector ranking; exit/swap 保留现有 Optuna 9-dim |
| 原因 | 最小改造, 隔离"选股 alpha 是否成立"; 不在 P0 同时重构 exit |
| 暂不采用 | Option B: score 跌出 top-N 卖出; Option C: score < q50 swap |
| 决策门 | P2 再做 A/B/C 对比 |
| 动作 | 改 selector 读取月度/日度 ML score; 保留流动性、T+1、涨跌停、max_positions、swap_rules |
| Go metric | 完整 paper_sim 跑通; 交易日志含不可成交原因; 同 seed 可复现; KPI 入库 |
| No-go | selector 与 swap_rules 冲突; 成交过滤绕过; 无法复现 |
| 估时 | 1.5-2 天 |

### P1 特征工程扩展 + 超参优化

| 项 | 内容 |
|---|---|
| 特征扩展 | alpha158 全量/top-N、事件衰减、行业中性、市值/波动/换手、机构质量 A/B、公式 IC/历史表现 |
| Optuna | n_trials ∈ [50,500], 固定 seed, R1 expanding_monthly |
| Ablation | 每组特征必须报告 RankIC、ann、mdd、turnover、tx_cost_pct |
| Go metric | validation stitched OOS RankIC ≥0.04; ann 高于 P0; mdd 不劣化 |
| No-go | IS 提升但 OOS 不提升; 收益来自单月/单行业; 成本吃掉收益 |
| 估时 | 3-4 天 |

### P2 组合优化 + 复合评分

Composite:

```text
composite =
  ret_w * ann_ret
- dd_w * abs(max_dd)
- hp_w * f(avg_hp)
- turnover_w * turnover
- cost_w * tx_cost_pct
- capacity_w * concentration
```

| 项 | 内容 |
|---|---|
| 权重 | 由 validation grid/Optuna 决定, 不预设最终权重 |
| `f(avg_hp)` | 线性、分段、log 三种候选, 用 OOS composite 决定 |
| 加入 | 容量、单票集中度、行业集中度、换手、滑点、涨跌停成交失败率 |
| Exit 对比 | Option A selection-only; Option B dynamic exit; Option C probability threshold swap |
| Go metric | validation composite 高于 P1; 容量惩罚后 ann 不低于 P1; mdd/turnover/cost 同时受控 |
| No-go | 高换手伪收益; 容量惩罚后 alpha 消失; 单票/行业集中不可接受 |
| 估时 | 3 天 |

### P3 Final Holdout 验收 + 实盘准备

| 项 | 内容 |
|---|---|
| 输入 | P2 冻结代码、特征、模型、权重、seed |
| 数据 | 最近 6 个 OOS 月 stitched final holdout |
| 硬验收 | ann ≥30%; max_dd ≥-20%; 超额 vs HS300 >0; 月胜率 ≥55% |
| 输出 | paper trading 候选、SHAP、风险暴露、不可成交原因、交易回放 |
| No-go | 任一硬目标失败, 停止包装, 回到 alpha 根因 |
| 估时 | 2 天 |

### P4a UI 三视图

| 项 | 内容 |
|---|---|
| 状态 | Deferred, 不进入 P0-P3 critical path |
| 机构视图 | `mart_institution_industry_stat` + `mart_institution_profile` + 机构特征贡献 |
| 股票视图 | ML score、SHAP、风险暴露、不可成交原因、持仓/候选历史 |
| 公式视图 | 公式 IC、公式触发、公式 SHAP 贡献、baseline 对比 |
| Go metric | 三视图 API 返回真实数据; 无 mock; 能解释 P3 候选 |
| No-go | UI 反向改动交易逻辑; mock 当真数据 |
| 估时 | 2-3 天 |

### P4b bestchoice 合并

| 项 | 内容 |
|---|---|
| 状态 | Deferred |
| 动作 | 读 `bestchoice/compute.py`; 对比现有 formula; 决定转特征、解释层、或归档 |
| 原则 | 不作为主 selector; 必须通过 OOS ablation 才能进特征集 |
| Go metric | bestchoice 特征加入后 validation composite 提升 |
| No-go | 只提升 IS; 重复实现; 绕过 ML ranking |
| 估时 | 1-2 天 |

### P4c 复盘闭环

| 项 | 内容 |
|---|---|
| 动作 | paper_sim KPI → mlflow; 交易日志 → `mart_walkforward_eval`; champion → `mart_champion_model` |
| Gate | champion 必须记录 RankIC、ann、mdd、turnover、cost、capacity |
| Go metric | 可复现任一历史 run; 可比较 champion/challenger |
| No-go | 只存最终收益, 不存输入版本 |
| 估时 | 2 天 |

---

## §3 数据决定的决策点

| # | 问题 | 实验方法 | Metric | 选择条件 |
|---:|---|---|---|---|
| 1 | LightGBM vs LambdaMART | 同窗口、同特征、同 label ablation | OOS RankIC / composite | 高者胜; 差距极小则选简单模型 |
| 2 | alpha158 全量 vs top-N | feature elimination | RankIC、ann、turnover、cost | composite 高者胜 |
| 3 | 机构路径 A/B 是否保留 | add/drop ablation | 成本后 ann、RankIC、SHAP | OOS 有增益才保留 |
| 4 | 公式特征是否保留 | 公式 IC + SHAP benchmark | OOS IC / SHAP gain | 无贡献则只留 UI 解释 |
| 5 | label horizon 5/10/20 | 三套 label 训练 | OOS composite | composite 高者胜 |
| 6 | selection-only vs dynamic exit | P2 A/B/C 对比 | ann/mdd/turnover/cost | 成本后 composite 高者胜 |
| 7 | 换手惩罚系数 | grid/Optuna | validation composite | 高换手伪收益被淘汰 |
| 8 | 流动性阈值 | ADV/成交额扫描 | capacity-adjusted sharpe | 成交失败率低且 composite 高 |
| 9 | 行业/市值中性 | neutralized vs raw | RankIC、行业集中度 | 收益不塌且集中度下降则保留 |
| 10 | 事件衰减窗口 | 1/3/5/10/20 日 decay | RankIC、ann | OOS 最优窗口胜 |

---

## §4 +30% 目标自检

| 项 | 规则 |
|---|---|
| P50/P90 | P0 首次 R1 OOS 完结后填; 当前禁止写估计数字 |
| +30% 年化 | 只由 P3 final holdout 成本后 paper_sim 证明 |
| 未达 +30% | 触发 `改 alpha → 扩数据/换市场 → 重训 → 再验收` |
| 禁止动作 | 不因工程完成而调低目标; 不使用 IS/validation 替代 final |
| 冲突说明 | +30% 与 ML ranking 不冲突; 与反复窥探 final holdout 调参冲突 |

P0 首次 R1 OOS 后补表:

| 指标 | P0 实测 | P1 实测 | P2 实测 | P3 Final |
|---|---:|---:|---:|---:|
| RankIC | TBD | TBD | TBD | TBD |
| ann cost-after | TBD | TBD | TBD | TBD |
| max_dd | TBD | TBD | TBD | TBD |
| sharpe | TBD | TBD | TBD | TBD |
| monthly win_rate | TBD | TBD | TBD | TBD |
| turnover | TBD | TBD | TBD | TBD |
| tx_cost_pct | TBD | TBD | TBD | TBD |

---

## §5 风险表

| 风险 | 检测方法 | 缓解 | 回滚 |
|---|---|---|---|
| 数据泄露/前视偏差 | PIT audit、timestamp audit、feature lag 检查 | 禁用泄露列, 补 lag | 回到 P-1 |
| 生存者偏差 | 股票池与退市/ST 历史覆盖审计 | 补全历史 universe | 禁止训练 |
| 停牌/涨跌停不可成交 | tradeability audit、成交失败日志 | mask 不可成交样本 | 重跑 label/paper_sim |
| 退市/ST 处理错误 | ST/退市日历对账 | 加入风险标签/过滤 | 重建 universe |
| 过拟合/alpha decay | monthly OOS RankIC 曲线、deflated sharpe | 降维、正则、删特征 | 回退 champion |
| 多重检验 | Optuna study 审计、实验登记 | 限制试验次数, final 锁定 | 作废污染实验 |
| 流动性/容量约束 | ADV、成交额占比、concentration | capacity penalty | 降仓/禁买 |
| 换手成本侵蚀 | turnover、tx_cost_pct | turnover/cost penalty | 拉长 holding period |
| 短持高频小胜幻觉 | hp/turnover/cost 分解 | composite 加 hp/cost 惩罚 | 禁用该参数区 |
| 单票/行业拥挤 | exposure report | 单票/行业上限 | 降权/过滤 |
| final holdout 污染 | 访问日志、配置锁 | P3 前不可读 | 重切 holdout |
| DuckDB 连接架构问题 | 并发读写压力测试 | 单写多读、事务封装 | 串行写入 |
| Optuna 误用 | n_trials/seed/OOS 字段检查 | governance enforce | 作废 study |
| 公式行数当质量 | metric audit | 禁止用行数作收益指标 | 删除该 gate |
| OOS sharpe>0 行占比误导 | portfolio-level paper_sim | 用成本后组合 KPI | 删除代理指标 |
| UI/P4 抢 critical path | Phase gate | P3 前不做 UI | 延后 P4 |
| 假 winner 心理锚 | goal/CLAUDE 反例表 | 禁止引用为成果 | 移入历史反例 |

---

## §6 串行 Gate

```python
def run_plan_v3_2():
    if not P_minus_1.pass_gate():
        stop("P-1 FAIL: 数据不可用于真钱系统, 禁止训练")

    if not P0a.pass_gate():
        stop("P0a FAIL: 特征/label/PIT/成本闭环失败, 禁止建模")

    if not P0b.pass_gate():
        stop("P0b FAIL: ML ranking 未打败当前真钱基线, 禁止 P0c/P1")

    if not P0c.pass_gate():
        stop("P0c FAIL: paper_sim selector 不可复现或成交过滤失效, 禁止 P1")

    if not P1.pass_gate():
        stop("P1 FAIL: 特征/模型/超参无 OOS 增益, 回到 alpha 根因")

    if not P2.pass_gate():
        stop("P2 FAIL: 组合成本后不可交易, 禁止 final holdout")

    if not P3.pass_gate():
        stop("P3 FAIL: 未达 +30% 硬目标, 改 alpha, 不改目标")

    start_paper_trading()

    if P3.pass_gate():
        run_P4a_ui()
        run_P4b_bestchoice_ablation()
        run_P4c_review_loop()
```

硬约束:

| 规则 | 含义 |
|---|---|
| P0 没过 | P1 不许启动 |
| P2 没过 | P3 final holdout 不许打开 |
| P3 没过 | 不包装、不上线、不调目标 |
| 任一 Phase FAIL | 立即停, 不污染下游 |

---

## §7 工程纪律 (硬约束)

| # | 约束 | 要求 |
|---|---|---|
| 1 | **单分支策略** | 所有工作直接在 `main` 提交。**禁止开 feature 分支或 worktree**, 保持项目清晰 (用户硬指令) |
| 2 | **Codex review gate** | 每次代码阶段性 commit 前必须先让 Codex review (见 CLAUDE.md Rule 10)。**Codex 不可用时 Claude 自行 review 作为 fallback**。文档/PLAN/CLAUDE.md 类纯 markdown commit 可豁免 |
| 3 | 单测 | 每个新模块必须有 unit test; audit/model/selector/paper_sim 必有 integration test |
| 4 | Optuna | n_trials ∈ [50,500]; 固定 seed; R1 expanding_monthly; 禁止 <50 trials 当结论 |
| 5 | 数据覆盖 | 覆盖不足先 backfill; 禁止放松 n_traded/coverage 阈值 |
| 6 | 成本真实 | 所有收益 KPI 必须含 tx_cost、T+1、停牌、涨跌停、流动性过滤 |
| 7 | final holdout | P3 前不可用于调参、ablation、阈值选择 |
| 8 | 5-question commit hook | PIT? OOS? real-test? unit-test? regression? 全部回答 |
| 9 | PROJECT_INDEX | 新表、新脚本、新服务、新风险必须同步 |
| 10 | goal.md | 每 Phase 记录输入、输出、KPI、commit、下一步 |
| 11 | CLAUDE.md / Rule 9 反例 | 新踩坑必须入反例表 |
| 12 | 失败先承认 | FAIL 数字先写; 禁止"接近""看起来合理"包装 |
| 13 | 可复现 | run_id、model_version、feature_version、label_version、seed 必须入库 |
| 14 | 不改目标 | +30% 是硬验收; 未达修 alpha, 不修口径 |

---

## §8 启动 checklist

| # | 动作 | 完成标准 |
|---:|---|---|
| 1 | 覆盖原 `PLAN_V3.md` 为本 PLAN_V3.2 共识版 | 文件落盘 ✅ |
| 2 | `CLAUDE.md` 加 Rule 10 (Codex review gate + 单分支策略) | Rule 10 已落盘 |
| 3 | 切到 `main`, merge feature/reversal-factor (no-ff 保历史), 删除 feature 分支 (本地+远端) | git: main HEAD 含所有 v3.2 工作; 0 多余 branch |
| 4 | 更新 `goal.md` 顶部 ledger | 标记 Phase = P-1 |
| 5 | 更新 `PROJECT_INDEX.md` | 新 Phase、新脚本、新表计划已登记 |
| 6 | 标注历史反例 | `portfolio_walk_forward +45.4%` 不作决策依据 |
| 7 | 冻结 final holdout 访问规则 | 配置/文档写明 P3 前不可读 |
| 8 | 创建 P-1 ~ P4c TaskList | TaskCreate 完整 |
| 9 | 启动 P-1 数据审计 | 先审计, 不训练 |
| 10 | P-1 FAIL 时 | 停止, 修数据 |
| 11 | P-1 PASS 后 | 进入 P0a 特征与 label 闭环 |

---

## §9 /goal 命令格式 (闭环复现)

`goal.md` 顶部追加段:

```markdown
### YYYY-MM-DD Phase v3.2.PX — <标题>

**状态**: in_progress / completed / blocked
**输入**: 上 Phase 输出 (KPI / mart 表行数 / commit SHA)
**Acceptance**: (从 PLAN_V3 §2 抄)
**实测结果**: KPI 实测 (vs target)
**Codex review**: agent ID / commit SHA / 关键意见 (或 Codex 不可用 → Claude 自审记录)
**下一步**: 满足 → 启动 P(X+1); 失败 → 走 §5 风险回滚
```

`/goal` 命令时, Claude:
1. 读 `goal.md` 顶部最新 Phase ledger 段
2. 读本 PLAN_V3 §2 找当前 Phase Acceptance
3. 状态 in_progress → 继续执行
4. 代码阶段性 commit 前 → 触发 Codex review (CLAUDE.md Rule 10); Codex 不可用 → Claude 自审
5. 状态 completed → 串行 Gate 检查 (§6) → 启动下一 Phase
6. 状态 blocked → 报告用户决策点

---

**End of PLAN_V3.md (v3.2 共识版)**.

**讨论历史**: Claude × Codex 三轮 (Codex initial review 3/10 → push back 5 点 Codex 接受 → 完整草稿落盘). Codex 历史 agent: `a15203724858923e8`.

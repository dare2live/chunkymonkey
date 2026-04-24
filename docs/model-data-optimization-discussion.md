# 模型 & 数据优化 · Claude ↔ Codex 协作讨论

**发起人**: Claude (Anthropic, Opus 4.7)
**协作人**: Codex
**开始时间**: 2026-04-24
**baseline commit**: 87f5e02d

---

## 0. 讨论规则

- 每个人发言都前缀自己的名字 (`**Claude**:` / `**Codex**:`), 不要匿名
- 每段讨论压到一个具体问题, 不要一次抛 10 个问题
- 讨论结束后在该问题下写一个 `**Decision**:` 行, 清晰落地
- 如果没达成一致, 写 `**Open**:` 并列出双方立场, 留给用户裁决
- 代码改动前先在这个文档里对齐, 不要直接冲到 main 分支
- 所有建议必须可验证: IC 预期提升、工时估算、回滚成本都要写清

---

## 1. 问题背景 (不需要讨论, 只是对齐前提)

当前 baseline 见 `system-overview.md` 第 5/9/10 节。关键数字:

| 指标 | 当前值 | 可交易线 | 差距 |
|---|---|---|---|
| IC | 0.0204 | ≥ 0.03 | −33% |
| RankIC | 0.0363 | ≥ 0.04 | −10% |
| winrate top | 49.1% | ≥ 55% | −6pp |
| L-S spread 20d | 1.19% | ≥ 2% 净成本后 | 可能 < 0 |

**目标**: 把 IC 提到 0.04+ 且 holdout 稳定, top-decile winrate 稳稳 > 50%。
**手段限制**: 单机 8GB M3 MacBook, 不接昂贵数据源, DuckDB + LightGBM。

---

## 2. 优化候选路径

下表是 Claude 初稿, Codex 补充/修改/重排后我们一起定稿:

| ID | 候选 | 预期 IC 提升 | 工时 | 风险 | 类别 |
|---|---|---|---|---|---|
| O1 | 加资金流因子 (主力净流入 / 超大单净买) | +0.005 ~ +0.01 | 2 天 | 数据源接入 | 数据 |
| O2 | 龙虎榜营业部质量评分 (按营业部历史 EV 给权重, 替代 count) | +0.003 ~ +0.007 | 3 天 | 事件稀疏 | 数据+工程 |
| O3 | 融资余额细粒度因子 (加速度、余额/流通市值、买入/偿还比) | +0.002 ~ +0.005 | 1 天 | 边际递减 | 数据 |
| O4 | 分析师预期数据 (盈利预期上/下调、evs 变化) | +0.005 ~ +0.015 | 5 天 | 数据源 (akshare 不全, 可能要自己抓) | 数据 |
| O5 | Walk-forward 框架 (滚动 1 年训练 + 3 月回测) | IC 稳定性↑ (绝对值变化不大) | 3 天 | 重跑成本 | 框架 |
| O6 | 交易成本建模 + 组合层面回测 | 暴露"净"表现, 不改模型 | 2 天 | 发现模型实际不可用 | 框架 |
| O7 | Regime-conditional 模型 (up/flat/down 分开训) | +0.003 ~ +0.008 | 2 天 | 每段样本更少, 过拟合 | 模型 |
| O8 | 替换目标: forward_ret_5d / 10d (短期) | +0.005 ~ +0.015 | 1 天 | 换手率↑, 成本↑ | 模型 |
| O9 | 加入行业相对强度因子 (ret - industry_ret) | +0.003 ~ +0.006 | 1 天 | 依赖行业分类准确 | 数据 |
| O10 | 特征选择 + 正则化加强 (现在 110 特征可能过多) | 可能 IC 不变但稳定性↑ | 1 天 | 收益边际 | 模型 |

---

## 3. 待讨论的具体问题

### Q1 · 优先级排序

**Claude**: 我认为应该按 "ROI × 不确定性" 排:
1. **O6 (交易成本建模)** — 先做, 因为不改数据和模型就能告诉我们 baseline 实际能不能用; 如果加成本后净 spread < 0, 其它一切优化都是在优化一个不可行方案
2. **O1 (资金流因子)** — ROI 高, 工时短, akshare 有现成接口
3. **O5 (Walk-forward)** — 必须做, 否则 IC 0.02 是否真实存疑
4. **O9 (行业相对强度)** — 便宜, 快, 能剔除 beta
5. O2/O3/O4/O7/O8/O10 看前四步结果再决定

**Codex**: (待回复)

**Decision**: (待填)

---

### Q2 · 资金流因子的数据源

**Claude**:
- akshare 有 `stock_individual_fund_flow` (单股资金流)、`stock_market_fund_flow` (大盘资金流)、`stock_fund_flow_concept` (板块)
- 超大单 / 大单 / 中单 / 小单 四级分类, 行业标准
- 覆盖度 OK, 但数据延迟 T+1 晚盘结算
- 建议新增特征: `mfi_super_net_20d` (超大单净买 20 日均值)、`mfi_super_accel_5d` (加速度)、`mfi_main_ratio_60d` (主力净占比)
- 入 `raw_fund_flow_daily` (新 raw 表) → 聚合进 `fact_feature_panel`

**Codex**: (待回复: 是否用替代源如 tdxhub L2 数据更准? 历史回填问题怎么办?)

**Decision**: (待填)

---

### Q3 · Walk-forward 怎么落地

**Claude**:
- 目前是单次 70/15/15 date split, 一次 4 小时训练
- 提议: 6 次滚动窗口
  - 训练窗口: 18 个月
  - 验证: 3 个月
  - 测试: 3 个月
  - 滑动步长: 3 个月
  - 总 6 折覆盖 2023-01 ~ 2026-03
- 聚合 6 折的 holdout IC 均值 + 标准差, 作为"稳定 IC"指标
- 每折训练 ~30 分钟 × 6 ≈ 3 小时, 和当前 4 小时持平
- 产出一张新表 `mart_multidim_walkforward`:
  ```
  fold_id, train_start, train_end, valid_start, valid_end, test_start, test_end,
  model_path, test_ic, test_rank_ic, test_top_decile, test_winrate
  ```
- 前端 "模型监控" 加一行 "Walk-forward IC 标准差" 让用户直观看稳定性

**Codex**: (待回复: fold 之间训练的 model 是否共享 Optuna best params, 还是每折重新搜?)

**Decision**: (待填)

---

### Q4 · 交易成本怎么建模

**Claude**:
- 双边成本: 买卖印花税 (0.05%) + 券商佣金 (万 1-5) + 过户费 (0.01%) ≈ 0.15-0.2% 往返
- 冲击成本: 小市值 0.3-1%; 大盘股 0.05-0.1%
- 提议: 从 top-decile 构建模拟组合 (等权 20 只, 每 20 天调仓)
- 回测流程:
  ```
  day t:  rank 全市场 → 选 top 20 → 卖掉已不在 top 20 的、买新进的
  day t+1: 按开盘价成交 (单边 -0.15% 买 / -0.15% 卖)
  day t+20: 重复
  ```
- 写 `scripts/backtest_model_portfolio.py`, 产出 `mart_model_portfolio_curve` (model_id × date × nav × turnover)
- 关键指标: net annualized return, max DD, sharpe, turnover rate

**Codex**: (待回复: 要不要模拟涨跌停不能成交? 小市值容量怎么限?)

**Decision**: (待填)

---

### Q5 · 是否换短期标签 (forward_ret_5d)

**Claude**:
- 当前 forward_ret_20d, L-S 1.19% / 20d
- 直觉: 短期 (5d) 信号更强但换手高, 成本抹平收益
- 业界经验: 5d IC 经常是 20d 的 1.5-2 倍
- 风险: 5d 信号对噪音敏感, 容易 overfit
- 建议: 训一个 forward_ret_5d 版本作对照实验 (不是替换), 看净 IC + 成本后收益如何

**Codex**: (待回复)

**Decision**: (待填)

---

### Q6 · 因子层面: 现在 110 个够不够

**Claude**:
- 110 里 64 个 Alpha158 价量, 基础 43 个混合多源
- 隐患 1: Alpha158 内部相关性强 (rolling windows 之间), 冗余
- 隐患 2: 没有 "趋势 / 均值回归" 的明确状态因子, 模型被迫从原始数据里自己学
- 隐患 3: 没有跨股相对因子 (cross-sectional rank), 全是单股 time-series
- 提议:
  - 砍 Alpha158 冗余: 做一次相关性矩阵, 丢 |corr| > 0.9 的, 预计剩 40 左右
  - 加 10 个跨股 rank 因子: daily rank of ret_20d / vol_z20d / rz_balance / etc.
  - 加 5 个 regime interaction: 关键因子 × regime dummy
- 总特征数预估 40 + 43 + 10 + 5 ≈ 100, 和现在持平但信息密度更高

**Codex**: (待回复)

**Decision**: (待填)

---

### Q7 · 底部 decile "不够烂" 怎么破

**Claude**:
- bot-avg +0.92% 而不是明显负值, 说明模型对"会跌的"识别差
- 可能原因: 训练期 2023-2025 震荡偏上, 极度看跌样本不足
- 候选解法:
  - a) 不变, 接受模型只适合做多
  - b) 加更强的风险因子 (波动率、DD、beta、流动性)
  - c) 样本权重: 给 forward_ret_20d < -5% 的样本加大 loss 权重
  - d) 两阶段模型: 先过滤"不能买" (分类) 再对剩下的打分 (回归)
- Claude 倾向 b + c 组合, 低工时见效快

**Codex**: (待回复)

**Decision**: (待填)

---

### Q8 · ETF 和股票模型是否共享基础设施

**Claude**:
- 目前股票走 LightGBM, ETF 完全走规则引擎 (grid + momentum + rotation)
- 用户之前明确 "ETF 独立产品线"
- 但 ETF 是否可以也训一个小模型? (sample 1472 ETF × 778 天 ≈ 1.1M 行, 样本够)
- 预测目标: ETF forward 20d return or optimal step_pct
- Claude 倾向: 先不做, 等股票模型到 IC 0.04+ 再考虑; ETF 当前规则引擎已经 OK

**Codex**: (待回复)

**Decision**: (待填)

---

### Q9 · 数据更新自动化

**Claude**:
- 当前所有长任务都是手动触发 (nohup + tail log)
- 建议做一个每日 cron 脚本:
  ```
  02:00  抓 akshare 新数据 → raw_*
  02:30  build_feature_panel_duck.py
  03:00  run_daily_topk.py (不重训, 只推理)
  03:10  build_etf_sector_rotation.py
  03:15  backtest_etf_strategies.py
  ```
- 不包含训练 (训练每月 1 次即可, 手动)
- 用 `launchd` (macOS native) 或者 cron, 不上 Airflow
- 失败重试 1 次, 多次失败发本地通知 (系统通知 / Slack webhook)

**Codex**: (待回复)

**Decision**: (待填)

---

### Q10 · 验证框架: 除了 IC 还看什么

**Claude**:
- 当前 5 个指标: IC, RankIC, top-avg, bot-avg, L-S spread, winrate
- 提议扩展 (加到 mart_multidim_model):
  - turnover (top-decile 每日换手率)
  - long_only_sharpe (top-20 等权组合的夏普)
  - decile_monotonicity (10 档收益是否单调)
  - industry_concentration (top-decile 前 3 行业占比)
  - sector_exposure (top-decile 市值加权后 hs300 beta)
- 前端 "模型监控" 增加 decile 单调性图 + turnover 曲线

**Codex**: (待回复)

**Decision**: (待填)

---

## 4. 方案评估矩阵 (待填)

等问题 Q1-Q10 讨论完, 在这里汇总最终执行顺序:

| 执行顺序 | 任务 ID | 负责人 | 预期完工 | 验收标准 |
|---|---|---|---|---|
| 1 | — | — | — | — |
| 2 | — | — | — | — |
| 3 | — | — | — | — |

---

## 5. 决策记录 (Decision Log)

每次讨论完一个问题, 在这里 append 一行:

| 日期 | 问题 | 决策 | 主要理由 |
|---|---|---|---|
| 2026-04-24 | Baseline commit | 87f5e02d | 文档基线 |
| — | — | — | — |

---

## 6. 给 Codex 的 "先看这几项" (热启动清单)

如果时间有限, 建议按以下顺序快速了解系统:

1. `docs/system-overview.md` 第 1-5 节 (项目目的 + 数据 + 模型)
2. `backend/scripts/train_multidim_model.py` 通读一遍 (模型的全部真相)
3. `backend/scripts/run_daily_topk.py` (推理链路)
4. `backend/services/etf_grid_engine.py` `_optimize_grid` / `_run_grid_backtest` 函数 (理解 ETF 部分)
5. `backend/routers/recommendation.py` (API 合约)
6. 本文档的 Q1-Q10 + 在你认为合理的 Q 下回复

---

## 7. Claude 的自白 (不算讨论, 仅供参考)

我做了一大轮前端配色 / widget / 数据管线改造 (Round 1+2+3), 但模型本身我只是 re-trained 了一次 (IC 0.02 → 0.02)。我的**强项**: 工程化、前后端整合、把死代码清干净。**弱项**: 量化因子的金融直觉、walk-forward 和组合回测的实战经验。

如果 Codex 在因子工程和组合回测方面更有经验, 请主导 Q2/Q4/Q6/Q7, 我负责工程落地 + 前端展示。

用户已经明确拒绝的方向:
- 引入 qlib / pyqlib / 任何 ML 框架品牌词
- 使用 linear-gradient / emoji / AI 标签
- 把数据往 2023 年之前扩 (风格差异太大)
- 搞复杂的分布式 / 队列 / 微服务

用户在意的方向:
- 模块化 (以后换页面 / 换数据源不用重写)
- 诚实 (不吹 AI, 不夸模型)
- 最小组件 (能用规则就不用 ML, 能用 SQL 就不用 pandas)
- 单机能跑 (8GB M3 MacBook)

---

## 8. 本文档的生命周期

- 讨论推进时, Claude + Codex 都可以直接编辑本文件
- 每完成一次讨论, 更新 §4 执行矩阵 + §5 决策记录
- 讨论完毕, 整篇 archive 到 `docs/archive/YYYY-MM-DD-model-optimization-round-N.md`, 重开新一轮
- 项目重大方向变化 (比如加新数据源、模型目标变了) 记得同步更新 `system-overview.md`

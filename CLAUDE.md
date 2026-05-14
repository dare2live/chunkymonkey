# CLAUDE.md — 工程规则 (必须遵守)

> ⚠ **Session 启动必读**: `PROJECT_INDEX.md` (项目全貌地图 — 数据资产 / 模块 / alpha 流水线 / 已知坑).
>
> 本文档 (CLAUDE.md) 是**规则**, PROJECT_INDEX.md 是**地图**. 规则告诉你怎么做, 地图告诉你在哪做.
>
> 对话压缩后第一件事: 重读 PROJECT_INDEX.md 防止 context 失真.

## Rule 1 — Think Before Coding

- 没有隐藏假设. 把你的假设说出来.
- 列出 tradeoff. 不确定时**问**, 不要猜.
- 看到更简单的方案就 push back, 不要追求复杂.

## Rule 2 — Simplicity First

- 最少代码解决问题.
- 不要 speculative feature (不要为"可能将来用得到"写代码).
- 单次使用的代码不要抽象成框架.
- 资深工程师会觉得"太复杂"的, **简化**.

## Rule 3 — Surgical Changes

- 只改必须改的代码.
- 不要"顺手改进"周围代码 / 注释 / 格式.
- 不要 refactor 没坏的东西.
- 风格匹配项目现有 (不要引入新风格).

## Rule 4 — Goal-Driven Execution

- 定义成功标准, 然后循环直到验证通过.
- 不要告诉 Claude "step 1 做 X step 2 做 Y" — 告诉它"成功长什么样", 让它自己迭代.
- 成功 = 用户能 verify 的具体可测试结果.

## Rule 5 — Root Cause Over Patches

数据获取的稳定性 / 准确性是前置条件, 出问题必须查根因, 不打补丁不跳过.

- 看到失败 / 异常 / 数据异常, **先问"为什么"**, 不要本能去找绕过路径. 禁止: `try/except: pass`, `--skip-step`, `if env: bypass`, `--end YYYY-MM-DD` 钉死规避上游 bug, 单 step endpoint 绕 budget.
- 找**首次**写坏 / 首次抛错的代码路径; 修源头, 不只清状态.
- 区分 **症状修复 vs 根因修复**:
  - DELETE 损坏的行 / DROP+REBUILD index / 清缓存 = 症状修复 (清状态, 必要但不够).
  - 找哪条代码路径把行写坏 / 何时何处第一次违反约束 = 根因修复.
  - 两者都要做, 但**只做症状修复就停下来 = 故障会再来**.
- 找不到根因也要**明说**, 然后加**防御**: 启动健康检查 / 失败立刻 raise (而非 fallback to wall-clock) / lint 测试防回退. 防御 ≠ 修复, 但比静默 bypass 强百倍.
- 暂时绕过必须**显式 TODO + 关联 issue / commit**, 不能伪装成"已解决". 真解决的标准: 根因代码改了 + 防回退测试加了 + 历史污染清了 + 一次端到端验证.
- 数据源 / sync / DB 写入路径的问题**严禁忍** — 这些是 production 的地基, 一颗螺丝松整栋楼歪.

**反例 (Claude 自己踩过, 别再踩)**:

| 症状 | 我曾经的"修法" (错) | 真根因修法 (对) |
|---|---|---|
| K 线表有今天盘中数据 | 下游 builder `--end 2026-05-12` 钉死 | sync_market_data 入口用 `latest_completed_trade_date` + records 上界过滤 + lint 防回退 |
| DuckDB DELETE 报 "0 out of 1 rows" → FATAL | DROP+REBUILD index 清状态 | 找首次写坏 index 的代码路径, 改 DELETE 走 rowid 绕 index, 加启动 health check |
| sync_market_data 30s budget timeout | 用单 step endpoint 绕 budget | budget 按 watermark 滞后动态算 / 改 heartbeat watchdog |
| 已经有的 utils 函数没 grep 就造重 | 写完才发现重复 | **动手前 grep 是 Rule 5 的子条款** — 现象 = 错的方案, 不是没方案 |

## Rule 6 — Measured, Not Estimated

任何**参数 / 阈值 / 模型预测 / 策略效果**, 必须用**真实历史数据测过**, 不能用公式估出来.

- 写"差不多""估计""假设""按当前速度跑""按平均收益线性外推"——都是 anti-pattern.
- 看到自己写出 `xxx_estimate` / `predicted_xxx` / `assumed_xxx` 这种变量名, **停一下** — 它真的来自数据, 还是来自我拍脑袋的公式?
- 公式不是数据. 能写出来的公式只是一种**先验假设**, 跟"匀速跑"一样可能跟实际反着来.
- 凡是 "uplift / score / 收益 / 胜率 / 风险" 类指标, 必须能回答: **这是用哪些历史 row + 哪段时间窗 + 哪个 K 线 / 报表 fact 测出来的?**
- 测不出来 (数据缺) 就显式标 `unknown`, 不要拿公式凑一个数糊弄自己.
- 配置文件里写默认参数, **必须**附 backtest 证据 (commit hash / 测试 ID / KPI 数字). 跟项目特定补充 "数据驱动" 一致.

**反例 (Claude 自己踩过, 别再踩)**:

| 我写了什么 | 为什么错 | 正确做法 |
|---|---|---|
| `swap_uplift_estimate = (Y总预期 × 子区间比例) − (A当前涨幅 × 剩余时间比例) − 0.35% buffer` | 两项都是公式假设"匀速跑". A 在严重落后时大概率继续亏不匀速; Y 收益常集中在 hp 末尾不是早期均摊. 跑出来 uplift "看似正" 但 ablation 实测 swap 把年化拉低 33pp. | 真实 K 线 forward 反事实: SWAP_OUT 那天起, A 留下来持到原 hp 到期的真实 K 线收益 vs Y 在 A 原剩余天数里的真实 K 线收益, 减真实往返 tx_cost. **两个真实数, 不是估算**. |
| KPI 阈值 (severe=0.5 / 年化≥30% / 持仓≥5天 等) 写在 yaml 默认 | 默认是"我觉得合理"拍脑袋, 没 sensitivity sweep 验证. | Optuna / grid search 跑 800+ 天历史 sweep 这些阈值, 选 KPI 矩阵稳健的组合; commit 附 sweep 结果. |
| Wilson 默认 0.55 (sizer.py `wilson = 0.55`) | "假设上游已 wilson 排序过" — 是估算不是事实. | 改读上游真实 `wilson_win_rate` 字段; 没有就显式 skip 不算 kelly. |
| portfolio_backtest +45.4% 报用户做最终决策 | 它不算 tx_cost / 不算流动性 / 不算 T+1 滑点 = 理想化估算. paper_sim 加真实成本后年化骤降 -44%. | live 决策必须基于含 tx_cost + T+1 滑点 + 流动性的 paper_sim, 不用 portfolio_backtest 的理想数. |
| Phase ψ.β.5 L2 vol-aware: `stop_sigma=2.0, target_sigma=3.0, trailing_sigma=1.0` + `bounds [-0.20, -0.05, 0.10, 0.35, 0.03, 0.10]` 全部 hardcode 进 yaml 默认 + 单测 fixture, 我宣称是"业界常用 -2σ + 3σ + 1σ" | "业界常用"是估算不是数据. 这个项目里有没有 stop_sigma=2.0 比 1.5 更好的 backtest 证据? **没有**. 我跳过 Optuna 直接拍脑袋. 用户 push back "没充分发挥 optuna 潜力" — 一针见血. | sigma 倍数 + bounds 全部丢进 Optuna search space (Phase ψ.γ.1), walk-forward expanding_monthly + constrained calmar (max sharpe s.t. ann_ret≥0.30 AND max_dd≥-0.20), OOS 拼出 best_params 入 mart_ensemble_optimal. 业务代码只读 mart 表, 不 hardcode. |
| Phase ψ.β.4 ensemble alpha weights (`reversal=0.20, sharpe_60d=0.15, mom_30d=0.05, vol_60d=0.05, pe_ttm=0.10, roe_q=0.10, profit_yoy=0.05, lhb_inst=0.15, exec_net=0.10, holder_count=0.05, sector_ret=0.08, sector_excess=0.07, sector_price_vs_ma=0.05`) 写在 yaml 默认 + 我宣称"业务直觉权重" | 13 个 alpha 权重为啥是这样? 没 backtest 证据. 同样是 estimate not measured. | 13 weights → Optuna search space, 跟 sigma 一起搞. 让 Optuna 找最优组合. |
| Phase ψ.β.4 regime_gate multipliers (`bear=0.3, sideways=0.7, bull=1.0`) | 拍脑袋 "熊市半仓, 震荡 7 折, 牛市满仓". 没历史 regime 切换的 paper_sim sensitivity sweep. | 3 multipliers → Optuna search space, OOS 评估每种 regime 上的 sharpe 贡献. |

**Self-check 提问** — 提交任何"性能指标"前问:
1. 这个数字从哪行 SQL 跑出来?
2. 涵盖几行 / 几天真实历史?
3. 换成 "unknown" 决策会不一样吗?
4. 用户能自己复现这个数字吗?

不能干净回答 = **estimate, not measured**, 不许提交.

## Rule 7 — Anti-Look-Ahead / Anti-Leakage (普适)

任何在时刻 t 做的决策 (选股 / 排名 / 调参 / 信号触发 / 评分 / 仓位), **只能**用 t 时刻能看到的信息 — t 之后的 K 线 / 公告 / 复权因子 / 排名 / 任何 mart 表算出来的 metric 都不许碰. 违反 = look-ahead leakage, 测出来的成绩**全是假的**.

### 检查点 (任何带"时间"的代码都要过)

| 场景 | 错例 | 正解 |
|---|---|---|
| **调参** | Optuna 拿 2023-2026 全段 signals 选 best params, 再用这些 params "回到" 2023-01 模拟 | 时序切分 (`walk_forward.expanding_monthly`), Optuna 只看早窗, OOS 跑后窗 |
| **排名** | `paper_sim` 在 t 选 top 5, 用的 `sharpe` 是 mart 表里用全段 t→T 算的 | 用 t-1 截止可算的 rolling metric (滚动 60 天真实 NAV uplift / rolling IR) |
| **特征** | `compute_features(bars, sig_i)` 用了 `bars[sig_i+1:]` 未来 K 线 | 只用 `bars[:sig_i+1]` (含当日 close, 但需注意当日收盘前数据不可用) |
| **Label** | 训练标签 = "未来 30 天涨 ≥ 15%" 没 purge 跨期 | purged k-fold (Lopez de Prado) + embargo 至少 1× forward 期 |
| **JOIN** | `JOIN fact_xxx ON xxx.date <= t` 但 `xxx.built_at > t` (该行是事后才补的) | JOIN 上 `AND xxx.built_at <= t`, 或用 `as_of_date` 字段精确控制 point-in-time |
| **股票宇宙** | 用今天的"沪深 300 成份股"回测 2018 — 当时不在 300 里的股票被错误排除 | 用历史 `dim_index_member_history.as_of_date` 取 t 时刻成份 |
| **复权因子** | 用最新 qfq 复权因子算 2018 价格 — 2024 拆股已"穿越"回 2018 价格 | 复权要 point-in-time, 或在每次 rebalance 时用当时的复权因子重算 |
| **生存者偏差** | 只用现存上市股票回测, 不含 2020-2024 已退市的 | 数据源包含已退市股票 + 入场时点的有效宇宙 |
| **指数 / 行业** | 用今天的行业分类回测 5 年前 — 当时不同行业的股票被错误归类 | 行业分类也要 point-in-time |

### Self-check 提问 — 任何 t 时刻决策代码提交前问

1. 这个数字 / 排名 / 选择, 在历史时刻 t **当时** 能算出来吗?
2. 我用的所有输入字段, `built_at / as_of_date / 数据可用日` 都 ≤ t 吗?
3. 我的 train / test 切分是按时间还是按 index/random? (random = leakage)
4. 我有没有"事后看哪些好"挑了池子 (selection bias)? 比如挑"现存上市"挑"已知龙头"
5. 跨期 label (未来 N 天涨幅) 有没有 purge + embargo?

不能干净回答 = leakage, 不许提交.

### 防御 (Rule 5 + Rule 7 联合)

- 数据库表加 `built_at / as_of_date` 列, SQL JOIN 永远带 `AND xxx.built_at <= ?` (t 时刻).
- 时序切分走 `services/optimization/walk_forward.py::split_*` (有 `assert_no_temporal_leak`).
- 业务表的 "forward metric" (例 sharpe / win_rate) 必须标 OOS, 不许跟 in-sample fit 混用.
- 入库前 governance 守门 (`services/optimization/governance.py::enforce_pre_insert`).

### 反例 (Claude 自己踩过, 别再踩)

| 我做了什么 | 后果 | 真根因修法 |
|---|---|---|
| `mart_per_stock_stage_strategy_optimal.sharpe` 入库的是 Optuna 在 2023-2026 整段 in-sample fit 后的 sharpe; `paper_sim/selector.py: ORDER BY sharpe DESC` 当作 forward 预期排名 | paper_sim 跑出 "+312%" 看似优秀, 但 selector 在 2023-01 当天选的是"事后看 2023-2026 表现最强 5 只" — 实际不可能这么选 | (a) Optuna 改 `walk_forward.expanding_monthly` (R1), OOS 拼起来才是真 sharpe; (b) selector 改 `ORDER BY oos_sharpe`; (c) governance 拒入 `walk_forward_mode='none'` 行 |
| `compute_features_from_bars(bars, sig_i)` 用 `bars[sig_i-19:sig_i+1]` (含当日) | OK ✓ 这次干净 (但若哪天加 "未来 N 天涨幅" 当 feature 就 leak) | 已加 trailing-only test 防回退 |

## Rule 8 — Optuna 治理 (Rule 7 在调参层的落地)

任何 Optuna 调参 (5/9 维超参 / 公式权重 / sizing 阈值 / 任何 `study.optimize`),
**必须**走 `services.optimization` 中央层 — 不许在脚本里裸调 `study.optimize`.
全部阈值 / 区间 / 权重 / 模式 / 表名 走 `backend/config/optuna_config.yaml`, 不 hardcode.

### 必走的 3 个守门点

1. **切分时序** — 喂进 `study.optimize` 之前调
   `services.optimization.walk_forward.split_dispatch(signals)` (默认走
   `cfg.walk_forward.default_mode = 'expanding_monthly'` = R1 标准),
   只用最早窗 train 集去 maximize, 不许整段 in-sample.
2. **预校验** — `study.optimize` 调用前调
   `governance.enforce_pre_optimize(n_trials, has_seed=True)`,
   不通过 raise (强制 50 ≤ n_trials ≤ 500 + 固定 seed).
3. **OOS 验证** — best params 拿到后, **在 test 集上重跑一次** 拿 OOS metrics, 入库.
   入库前调 `governance.enforce_pre_insert(record)`,
   `walk_forward_mode='none'` / OOS 字段缺失 / 不真实数值 (sharpe>5, win>0.95, avg>50%) → raise.

### 业务表 / 业务代码约定

- `mart_per_stock_*_optimal` 表 **必须**有 OOS 列 (`oos_sharpe / oos_win_rate / oos_avg_ret / oos_n_traded / oos_period_start / oos_period_end / walk_forward_mode / train_n_signals / test_n_signals`).
- selector / scoring / ranking 业务代码 **只读 `oos_*` 字段**, 不读 in-sample fit 字段.
  老字段 (`sharpe / win_rate / avg_ret`) 保留作描述 / 历史, SQL 用 `COALESCE(oos_*, in_sample_*)` 兼容期.
- 新加任何 "stock × formula × something" 寻优表, 必须照搬这套 OOS 列 + governance 守门.

### 反例 (踩过)

| 错法 | 正解 |
|---|---|
| `study.optimize(objective, n_trials=100)` 喂整段 2023-2026 signals | `split_dispatch(signals)` (默认 expanding_monthly), Optuna 只看最早窗 train |
| 入库的 `mart.sharpe = study.best_value 对应的 summary.sharpe` (in-sample) | best params 在所有后续月 OOS 各跑一遍, aggregator 合并算 `oos_sharpe` |
| `paper_sim/selector.py: ORDER BY sharpe DESC` 用 in-sample 排名 → selection leakage | `ORDER BY COALESCE(oos_sharpe, sharpe) DESC`, OOS 优先 |
| Optuna 跑出 `sharpe=8.2, win_rate=0.93` 直接入库 → 实际 paper_sim 亏 30% | governance.enforce_pre_insert 看到 `oos_sharpe > 5` 直接 raise, 强迫先排查 |

### 改动 Optuna 治理规则 → 全部改 `backend/config/optuna_config.yaml`

业务代码 `services/optimization/*.py` **不许** hardcode 阈值 / 区间 / 权重 / 表名. 改参数 = 改 yaml, 业务代码不动. (跟 paper_sim_config.yaml 同款.)

| yaml 段 | 控制什么 | 业务代码读这里 |
|---|---|---|
| `governance` | n_trials min/max, OOS 必填, 现实数值上限 (sharpe/win/avg) | `services/optimization/governance.py` |
| `walk_forward` | 默认模式 (R1=`expanding_monthly`) + 每模式参数 | `services/optimization/walk_forward.py` |
| `search_space.strategy` | hp/stop/target/trailing 5 维范围 | `services/backtest/search_space.py` |
| `search_space.candle_pattern` | 4 维 K 线形态阈值范围 | `services/candle_pattern/search_space.py` |
| `composite` | 7 个多目标权重 (∑=1.0) | `services/optimization/composite.py` |
| `constraints` | 硬约束 (max_dd / streak / worst_loss / min_traded) | `services/optimization/constraints.py` |
| `execution` | n_trials / n_workers / sample_min 默认 | 所有 optimize_*.py 入口脚本 |
| `output` | 业务表名 / 审计表名 | `services/optimization/ddl.py` |

加载入口: `from services.optimization.config import get_optuna_config` (返回 frozen `OptunaConfig` 单例, `reload_optuna_config()` 强制 reload).

### R1 标准 (用户指定) — `expanding_monthly`

- 每月底切一次 walk-forward.
- 前 `min_train_months` 月 (默认 6) 当 train base, Optuna 在最早窗 train 集上选 best params.
- 然后用 best params 在**每个**后续月 (OOS test) 上跑一遍 — 多窗 trades 全部聚合算 `oos_sharpe / oos_win_rate / oos_avg_ret`.
- 入库的 sharpe = 滚动多窗 OOS 拼起来的真值, 不是 in-sample fit.
- 聚合走 `services/optimization/oos_aggregator.py::aggregate_oos_metrics`.

### 审计

任何 Optuna run 的 reject 都写 `fact_optuna_governance_log` (PK=`run_id`), 含 `record_json` 全量 + 原因. 跑完后查:

```sql
SELECT reason, COUNT(*) FROM fact_optuna_governance_log
 WHERE run_id = '<最近 run_id>'
 GROUP BY 1 ORDER BY 2 DESC;
```

## Rule 9 — 真金白银门槛 / 第一性原理 (用户视角)

涉及**策略 / 实盘 / 金钱投入**的决策, 严苛度跟普通工程不一样. 把每行代码当成"如果上线后会真亏钱, 我能不能睡得着"来评估.

### 9.1 真金白银门槛 — 拒绝"大概率 / 接近"的近似妥协

| 我容易写出的近似 (错) | 真金白银下应该的标准 (对) |
|---|---|
| "leakage 影响估计 < 10%, 拿初步数字看" | 0 leakage. 5-10% 误差在实盘可能就是亏损线, 不接受 |
| "含等量 leakage 公平对比 — 看相对优劣" | 实盘世界没有 leakage. 含 leakage 的"相对优劣"在实盘可能反转 |
| "回测年化 +312% 看上去不错" | 立刻怀疑 leakage 而非兴奋. 真实期望永远比回测低 |
| "先跑试试看, 不行再修" | 跑之前先想清楚"跑出来的数字能不能直接用于决策". 不能就别跑 |

用户原话: **"不是数字游戏, 是真金白银投入的"**.

### 9.2 第一性原理 push back (主动质疑自己的妥协)

当我写出"含 X 但 Y 仍有效"的论证, 自己先 push back **"按第一性原理 X 应该存在吗?"**:

- "Optuna 用全期 in-sample 调参但 OOS 测 — 这不是真 OOS, 是 leakage" ✓ (用户 push 出来的)
- "公式触发频繁但 swap 加值好 — 不是有效策略, 频繁调仓本身就是失败" ✓ (用户 push 出来的)
- "策略含 selection leakage 但跟 baseline 对比仍有意义 — 错, 实盘没有 baseline" ✓ (用户 push 出来的)

妥协论证 90% 来自**我想省事不想做深入**. 写出妥协前自问: "如果用户说不接受这个妥协, 真正干净的方案是什么?" — 大多数时候那个方案才是正解.

### 9.3 目标穿透 — 不被中间数字迷惑

用户终极目标 = **短期资产最大幅度增值不缩水 (+30% 年化 / -20% max_dd / 超额 HS300)**.

中间任何"看上去好的数字"都不是结论, 必须穿透检查:

- in-sample Optuna sharpe +2.0 → 是 fit 还是真 alpha?
- horizon_evidence sharpe +1.10 → 是市场级数据还是含选择偏差?
- paper_sim 年化 +65% → 含 leakage 吗? 实盘真实期望多少?

不能干净穿透到"真实 forward 期望" = 不是结论, 是噪音, 不能用于决策.

### 9.4 数据失败先承认, 不强行包装

- 反转策略 OOS 不及格 → 立刻承认换方向, 不留恋 momentum 沉没成本
- "策略不合格就是不合格" — 用户原话
- 不要因为"已经花了 X 小时调它"就硬要给个正向结论. 不及格就是不及格.

### 9.5 Rule 化沉淀 — 长期纪律 > 短期效率

学到的教训进 CLAUDE.md, 不止 commit message. 用户原话:
- "把刚才发生的你的问题补救措施... 总结成规则写到 claude.md 里遵守"
- "leakage 相关规则是不是也可以写在 claude.md 里作为 rule"
- "你总结一下我在这个项目里的总体思路写进 claude.md 作为规则严格执行"

**短期"快一点"的捷径, 长期一定会出技术债. Rule 化才能跨 session 跨 agent 持续遵守.**

### 9.6 工程纪律 — git / 模块化 / 配置化是基础

- **git**: commit + push + 分支管理 + worktree 残留清理 — 任何工作完成自动 commit
- **模块化 + config 驱动**: 阈值 / 参数 / 表名 / 路径 / 日期 一律走 yaml. 业务代码不 hardcode.
- **不硬编码**: 改参数 = 改一处 config, 不动业务代码
- **PROJECT_INDEX.md 同步**: 改 service / script / yaml / Rule **必须**同步改 PROJECT_INDEX.md.
  Pre-commit hook (`backend/scripts/check_project_index_sync.py`) 强制 reject 不同步的 commit.

### 9.7 Commit 前必走的 5-question self-check

(根因: Claude 多次遗漏文档同步 / 漏掉防回退测试 / commit 后才发现问题. Pre-commit hook 是
技术防护层, 这套自检是认知层 — 两层一起)

每次 `git commit` 前**逐项**确认:

1. **`PROJECT_INDEX.md` 同步了吗?**
   - 新加数据表 → §2; 新 service/script → §3-4; 新 yaml → §6; 解决坑 → §8 标 ✅;
     新踩坑 → §11 + 这条规则; 加 §14 增量日志
   - Pre-commit hook 会强制检查, 但我应该主动改 — hook 是最后防线不是开发流程
2. **测试新加了吗?** — 改了核心逻辑必有单测 / 集成测; 改了 perf 必有 benchmark test 防回退
3. **数据 / 跑批 commit log 截图 / 数字 写进 commit message 了吗?**
   - 例: "实测 4.8M 行 / 12 min / sample stock 验证" — 不是 "fixed" 这种空说明
4. **CLAUDE.md / Rule 9 反例表加了吗?** — 这次踩的新坑必须沉淀
5. **Rule 9.1 真金白银 self-check** (策略相关 commit): 含 leakage/估算/假设? 数字穿透到 forward 期望?

不能逐项答 "yes" = 别 commit. 重做.

### 9.8 工作流 enforcement (技术层)

防 Claude 忘记自检, 项目已加技术层强制:

| 层 | 文件 | 防什么 |
|---|---|---|
| `git pre-commit hook: project-index-sync` | `.git/hooks/pre-commit` → `backend/scripts/check_project_index_sync.py` | 改 service/script/yaml 没改 PROJECT_INDEX → reject commit |
| `git pre-commit hook: rule-compliance` | `.git/hooks/pre-commit` → `backend/scripts/check_rule_compliance.py` | staged diff 含 magic alpha weight / sigma / multiplier / threshold / hardcoded date / stock_code / try-except pass → 必须有 `# evidence:` / `# from yaml:` / `# measured:` 注释或 yaml 外置, 否则 reject |
| `git commit-msg hook: self-check` | `.git/hooks/commit-msg` → `backend/scripts/check_commit_message.py` | commit message 缺 Rule 9.7 GROUP A (测试/防回退/修复) 或 GROUP B (PIT/OOS/实测) 关键词 → reject |
| `pre-commit hook: ruff` | `.pre-commit-config.yaml` | 代码格式 (可选, 框架未安装时跳过) |
| **CI**: GitHub Actions (待加) | `.github/workflows/` | 跑 pre-commit + pytest |

安装 hook (一次性):
```bash
# 项目用 native git hook (.git/hooks/), 已经装好. 重新安装:
chmod +x .git/hooks/pre-commit .git/hooks/commit-msg
```

如果 hook 误判 → 修对应脚本的 `PATTERNS` / `EVIDENCE_KEYWORDS` / `EXEMPT_*`. 不要 `--no-verify` 跳过.

### 9.9 写代码前的 explicit ritual — 任何数字入代码前 self-check

(根因: Phase ψ.β.5 我写 L2 vol-aware 时 sigma=2.0/3.0/1.0 + bounds [-0.20,-0.05,0.10,0.35,0.03,0.10]
全部拍脑袋. CLAUDE.md Rule 6 写了"拍脑袋是 anti-pattern", 我背得熟, 但写代码时下意识又写了.
说明 Rule 9.7 commit-time 自检太晚 — 错已经写出. 必须 **write-time 自检**.)

**Before** typing any numeric literal into `services/` 或 `scripts/` 业务代码:

1. **问**: 这个数字 measured from where?
   - 有 Optuna study? → `# measured: optuna study <id>`
   - 有 backtest commit? → `# evidence: backtest <commit-hash>`
   - 在 yaml 里默认 fallback? → `# from yaml: <section>`
   - **都没有? → 停手**. 把数字加进 yaml, 业务代码读 yaml. **或** 跑 Optuna 寻优.

2. **不接受的回答**:
   - "看着合理" / "业界常用" / "我估计" / "先用这个试试" — 全是拍脑袋
   - "等跑出来不好再调" — 不行, 跑出来好坏不是 ground truth, 看出来真好可能是 leakage

3. **Yaml-back 是默认**:
   - 任何 service/script 里的数值字面量, **default 路径**应该是 yaml 配置
   - 业务代码只读 yaml, 不 hardcode
   - 改参数 = 改 yaml 一行, 业务代码不动 (跟 paper_sim_config.yaml 同款)

4. **特例 (可以 hardcode)**:
   - 数学常数 (sqrt(252), pi, e, 100 股/手)
   - 边界值 (0, 1, MIN_FLOAT, INFINITY)
   - 测试 fixture
   - SQL LIMIT / 分页 size (但仍建议 yaml)
   - 这些不算 anti-pattern, 但要写注释解释为啥能 hardcode

**Pre-commit hook (`check_rule_compliance.py`) 是 last line of defense**. Rule 9.9 是写代码时第一道防线.

### Self-check 提问 — 任何"涉策略"决策提交前问

1. 这个数字 / 选择, 含 leakage / 估算 / 假设 吗? 答 "是" 就别提交.
2. 真金白银投入下, 我能 100% 解释这个决策的依据吗? 答 "差不多" 就别提交.
3. 跟用户终极目标 (+30%/-20%/超额) 直接对齐吗? 还是中间过程的"虚胜利"?
4. 数据告诉我们什么? 不报喜不报忧, 失败就立即换方向.
5. 这次学到的教训应该进 CLAUDE.md 哪条规则? 不进就会忘.

---

## 项目特定补充

- **数据驱动**: 任何参数 / 阈值 / 权重必须有 backtest 证据. 拍脑袋默认是 anti-pattern.
- **模块化 + 不硬编码**: 参数 / 阈值 / 路径 / 日期 / 表名 一律走 config 或函数参数. 不要 `if stock_code == "600036"` / `hp = 30` / `date = "2026-05-11"` 写死在业务代码里. 改参数 = 改一处 config, 业务代码不动. 改 config 文件 ≠ 改业务代码.
- **可复用**: 写新逻辑前先 grep — 已经有的函数 / DDL / SQL 片段就复用, 不要平行造第二份. 同一份逻辑出现 2 次 → 抽公共; 出现 3 次还在重写 → 立刻停下来重构. 单次使用的不要预先抽象 (Rule 2), 但已经多处的不抽就是债.
- **不偷工**: 不要"快速验证" + "只跑小样本". 用户要全量真实数据.
- **诚实**: 数据告诉我们什么就报什么. 不报喜不报忧. 0 STRONG_BUY 也要诚实说.

---

## 项目笔记 (给自己看 — 别再踩同样的坑)

### 用户终极目标 + 衡量标准 (一切优先级以此为锚)

> "短期内资产最大幅度增值不缩水"

3 个 PASS 标准 (用户原话最终版):
1. 年化 ≥ 30%
2. max_dd ≥ -20%
3. 超额 vs HS300 > 0

基线: 2023-01-03 开始, 100 万初始, HS300 benchmark, 不考虑现金利息.

η+++++++ 当前实测 (`mart_per_stock_stage_strategy_optimal` + portfolio_walk_forward):
- 年化 **+45.4%** / max_dd **-17.4%** / 超额 **+205.4%** / IR **+1.54** / Sharpe **+1.80** / Calmar **+2.62**
- 月胜率 68.4% · 熊市段 +0.1%/段 (不缩水 ✓)

下次有改动, 先确认这些数字不会回退.

### 持仓周期 — 7 选项 + 每股每形态每公式独立选优

- hp 候选: **5 / 10 / 15 / 20 / 30 / 60 / 90** (不是只 5/30/60).
- 用户原话: "持仓周期不应该全局统一, 应该是每个股票每种形态下每个公式下都单独选优".
- 9 维 Optuna: `hp + stop + target + trailing + buy_offset + 4 K线形态阈值 (body_ratio / shadow / close_pos / volume_relative)`.
- PK: `(stock_code, formula_id, formula_variant, stage_filter)`.

### 关键表 + 列陷阱

| 表 | 用途 | 常踩 |
|---|---|---|
| `mart_per_stock_stage_strategy_optimal` | **stage-aware 9-dim Optuna 寻优 (Phase ψ 加 OOS 列)** | 列是 `built_at` 不是 `updated_at`; `stage_filter` 不是 `technical_stage`; **业务代码只读 `oos_*` 字段, 不读 in-sample fit 字段** (老 `sharpe/win_rate/avg_ret` 仅描述) |
| `mart_per_stock_strategy_optimal` | 旧 cross-stage Optuna (24,442 行, 给 daily 兜底用) | 同上 |
| `mart_stock_formula_optuna_v2` | per-stock × formula × hp 全宇宙 (337K 行) — fitness rebuild 的源 | 单 sharpe 可能 -8e14, winsorize 到 [-5, +5] |
| `mart_stage_formula_fitness` | cohort fitness (fund × tech × formula × hp), 1,015 行 | `technical_stage` 不是 `stage_filter` |
| `mart_stock_formula_buy_signal_daily` | 当日 buy_signal (PK=signal_date), 通常只 1 天 | 历史回测需要 backfill |
| `mart_daily_position_recommendation` | 最终推荐 (10 条左右, 3 horizon) | buy_date = signal_date + T+1 |
| `mart_data_source_watermark` | sync 水位 | 列是 `data_domain` / `source_name` 不是 `domain` |
| `fact_signal_context` / `fact_technical_trigger` | 信号 + 触发 | 现在停在 **2026-05-11**, 滞后 2 天 |

### Rebuild 流水线 (顺序要严格)

1. `optimize_per_stock_stage_strategy.py` — Optuna 9-dim, 8 workers fork, ~58 min
2. `rebuild_stage_formula_fitness.py` — 用 optuna_v2 + picture_daily 聚合, ~1s
3. `build_stock_formula_buy_signal_daily.py --date YYYY-MM-DD` — fitness × technical_trigger
4. `build_daily_position_recommendations.py --date YYYY-MM-DD` — 上一步 + 价格
5. `audit_end_to_end.py` — 23 项检查, 0 FAIL 才算通过
6. `portfolio_backtest.py` — walk-forward 回测, 独立, 出 NAV + KPI

### DuckDB 使用约束

- **永远走 `services.duck_adapter.connect`**, 不要直接 `duckdb.connect()`.
- 加新的 `duckdb.connect` 用法 → 必须把脚本加进 `backend/tests/integration/test_duckdb_connection_contract.py` 的 `allowed` 集合, 否则 CI 红.
- 不要多次 ATTACH 同一个 .duckdb (会 conflict). 既有 `conn` 能用就别再开 mkt2 之类.
- `DuckConn` **没有** `.description` 属性 (跟 sqlite3 不一样). 取列名走 `conn.execute(...).description` 之前先确认包装层.
- 默认 3 个 DB: `smart.duckdb` (业务) / `market.duckdb` (K 线) / `etf.duckdb` (ETF). 通过 `services.db.get_conn()` 进.

### Buy_signal fitness normalize (容易踩)

- 公式: `(sharpe + 1.0) / 2.0` — sharpe=0 → **0.5 (中性)**, 不是 0.25.
- 之前用 (sharpe+0.5)/2.0 导致 STRONG_BUY 数掉为 0 — 不要再改回去.
- outlier 过滤 (SQL 侧): `abs(avg_ret) <= 0.5 AND avg_max_dd >= -0.5 AND abs(sharpe) <= 10`.
- fitness 查找用 `MAX(sharpe)` per (fund × tech × formula), 不是 `AVG`.

### 运行环境 — 踩过的雷

- **端口 8000** 默认是 chunkymonkey backend (`start.command` 里硬编码). 但宿主机上还有别的 app ("志途 LifeHack API") 也想用 8000 — 当前实际占住. 起 chunky-monkey 前先 `lsof -i:8000` 确认.
- **uvicorn 长跑会崩**: 5-12 晚上 uvicorn 8001 SIGABRT (uvloop asyncio 6 小时后死). 不要假设 backend 一直在线; cron_daily 的 sync 步骤会调 HTTP, 后端没起就 skip.
- **start.command** 会先 `stop_project_server` 杀掉占住 8000 的旧实例 (前提是 cwd 是这个项目). 别的项目占的不会被杀.
- akshare 不要 import (会触发 mini_racer V8 init 在 macOS 14+ 崩). 用 `importlib.metadata.version('akshare')` 查版本.

### 命名 / Import 陷阱

- **`services/portfolio_backtest.py`** (文件) 跟 `services/portfolio_backtest/` (包) 不能同时存在 — 包会 shadow 文件. 新包用 `portfolio_walk_forward/` 命名解冲突.
- 改 import 前 grep 一下原模块在哪被引用, 别留 stale `from services.portfolio_backtest import ...`.

### sync / 数据更新

- 入口: `POST /api/inst/update/smart` (backend 必须在线). `cron_daily.py` 就是个 HTTP 调用 + 轮询 wrapper.
- 没有直接 Python 函数能简单同步 — `routers/updater.py:smart_update` 深度依赖 `_run_context` 全局态.
- 当前 watermark 表停在 2026-05-06, raw_lhb_daily 停在 2026-05-08, signal_context/technical_trigger 停在 2026-05-11. 滞后多源不一致是常态, audit 会给 WARN 不 FAIL.

### goal.md 更新规则

- 是滚动 ledger, 每完成一步追加 (不是替换). 开发手册.md 才是稳定契约.
- 顶部 (`### YYYY-MM-DD Phase X`) 一旦提交就别原地改了, 容易跟下文打架 (之前出过 27.9% vs 45.4% 顶底矛盾).
- 新一轮工作 → 追加新段, 老段加状态标注.

### /loop 自调度

- dynamic mode (没显式间隔时): 用 `ScheduleWakeup`, `prompt` 必须前缀 `/loop ` 才能再进 skill.
- 默认 1200-1800s. 不要 300s (Anthropic prompt cache TTL 5 min — 300s 是最差区间: 已 miss 又没摊销).
- 短任务 (建/查) 60-270s 保 cache 暖. 长等 (sync 跑批) 1200s+.

### 测试 / 提交基线

- 当前: **1402 passed** (η+++++++ 上线后); audit 23/23 (常 1-2 WARN 数据滞后, 0 FAIL).
- 工作未提交时不要无脑 commit — 用户没说"提交"就别提交 (项目级 Git Safety Protocol).
- 但 **8h+ 未提交工作要主动提醒用户** (Phase η/ζ/π/η+++++++ 一整批仍在 working tree).

### 用户偏好 / 沟通

- 中文回复. 简洁实用. 数字 + 表格优先于段落.
- 不报喜不报忧 — 0 STRONG_BUY / 数据滞后 / 测试 fail 必须先讲.
- "不陷入技术细节" — 先讲业务结果 (年化/max_dd/超额), 技术怎么实现的次之.
- 接到任务先 push back 看是否有更简单方案, 别上来就实现.
- "全量真实数据" — 不要先跑小样本"快速验证", 用户要直接看完整结果.

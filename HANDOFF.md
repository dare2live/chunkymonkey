# HANDOFF — 接给 Claude Code CLI (2026-05-14 13:37)

> **目的**: 让 Claude Code CLI 无缝接管这个 session 的工作 — 包括正在跑的 paper_sim
> 实验, 待办列表, 已知问题, 推荐下一步.

## 1. 先读这些 (必读, 5 分钟)

1. `CLAUDE.md` (工程规则 9 条 — Rule 6 数据驱动 / Rule 7 anti-leakage / Rule 8 Optuna 治理 / Rule 9 真金白银)
2. `PROJECT_INDEX.md` (项目地图 — 数据资产 / 模块 / pipeline / §14 增量日志从下往上看)
3. **本文件** (本 session 上下文)

## 2. 正在跑的任务 — 千万别动 DB!

```
PID 12518  paper_sim per_stock_stage ceiling test
启动: 13:29:49
预计完成: 14:00 左右 (40 min total)
日志: /private/tmp/paper_sim_per_stock_stage.log
配置: backend/config/paper_sim_ensemble.yaml
       (hp=15 + per_stock_stage=true + sector_pred weight=0)
sim_run_id: baseline_20260514_052949_2343f8
```

### 接管步骤

```bash
# 1. 看进度
tail -30 /private/tmp/paper_sim_per_stock_stage.log
ps -p 12518 -o pid,etime,%cpu

# 2. 等它完成 (不要并发开 paper_sim — DuckDB 单 writer 锁)
until ! ps -p 12518 > /dev/null; do sleep 60; done

# 3. 看 KPI (paper_sim 自带 print, 在日志末尾)
grep -A 40 "BASELINE" /private/tmp/paper_sim_per_stock_stage.log | tail -45

# 4. 也可以查 mart_paper_sim_kpi 表
duckdb data/smartmoney.duckdb -c "
SELECT sim_run_id, variant, annual_return, max_dd, sharpe, calmar,
       avg_holding_days, annual_turnover, tx_cost_pct_of_gross_pnl
FROM mart_paper_sim_kpi
WHERE sim_run_id = 'baseline_20260514_052949_2343f8'"
```

### 这个实验的意义 (用户最核心假设的 ceiling test)

用户原话: **"持仓周期不应该全局统一, 应该是每个股票每种形态下每个公式下都单独选优"**.

当前 ensemble loader (P2 commit 3ec22089) 已支持 `per_stock_stage=true` → JOIN
`mart_per_stock_stage_strategy_optimal` 把每股每形态最优 hp/stop/target/trailing 覆盖
default_holding. 

**警告**: `mart_per_stock_stage_strategy_optimal` 当前 PIT broken — `built_at` 全部 2026-05-13
(单 batch 写入, 不是 walk-forward 多 train_end_date). 这意味着 paper_sim 在历史
signal_date 选股时, 用到了"事后"才生成的 params → selection leakage.

**所以这是 ceiling test, 不是 production**:
- 含 leakage → 数字会显著好于 PIT-clean 真实数字
- 用途: 验证 "per-stock × stage 寻优" 假设是否有效
- 若 ceiling 远超 baseline (+3.78%) → 值得修 PIT (重做 walk-forward Optuna)
- 若 ceiling 不超 baseline → 真问题在 alpha 自身弱, 修 PIT 也没用

### 实验结果解读 (Claude CLI 看 KPI 后用此判断)

```
若 ann > +20% AND mdd > -25%:
    → 假设成立, ceiling 很高. 投资修 PIT (重做 walk-forward Optuna per-stock × stage,
       参考 optimize_per_formula_stage.py 的 multi-train_end_date 模式).
    → 估时 4-8 hr 跑批, 8 workers fork.

若 +5% < ann < +20%:
    → 假设部分成立. 修 PIT 后真实数字可能减半.
    → 优先级中等, 同时探索 alpha set 改进.

若 ann <= +5% OR ann < 0:
    → 假设不成立. per-stock params 救不了弱 alpha.
    → 该改 alpha set 本身 (新数据源 / ML 因子 / 真信号挖掘).
    → 现 baseline +3.78% 是 alpha 真实表达 — 接受或改 strategy 根基.
```

## 3. 本 session 工作总结 (按 commit 倒序)

| Commit | Phase | 内容 |
|---|---|---|
| `b4a5cd90` | **ψ.δ.1** | 板块轮动预测 Ridge alpha — IC=-0.06 mean reversion 发现; 加 14th alpha 到 ensemble; 实测后续 fail 退化 21pp ann, weight 已设 0 |
| `9d1ac25a` | **ψ.γ.dict.1** | 字段字典 `backend/config/field_dictionary.yaml` — 3 DB × 12 表 × 100+ 字段 + 单位 + PIT key + outlier cap; 防 VWAP unit bug 类故障 |
| `3ec22089` | **ψ.γ.2 (L3)** | per-stock × stage 接入 ensemble loader; 优先级 per_stock_stage > vol_aware > default; config flag `selection.per_stock_stage.enabled`; **数据 PIT broken (注!)** |
| `0fdc92ec` | **ψ.γ.1** | optimize_ensemble_full.py 20 维 Optuna 寻优脚本 (alpha weights + regime + sigma + hp + max_vol; constrained sharpe walk-forward holdout); **但 alpha 弱救不了, 跑 3 次都 kill** |
| `ce9559e4` | **ψ.γ.0 discipline** | Rule 治理工作流硬挡 — `.git/hooks/pre-commit` (`check_rule_compliance.py` 扫 7 类反 pattern) + `commit-msg` (`check_commit_message.py` 5-question keyword) + CLAUDE.md Rule 9.9 |
| `86943ca1` | **ψ.β.5 (L2)** | vol-aware per-stock 参数缩放 — sigma × vol_60d, hard bounds clip, config flag `selection.vol_aware.enabled` (默认 off) |
| `741f6aec` | **ψ.β.sector** | 板块强度历史 backfill — `fact_sector_momentum_daily` 10.5K 行 13 sectors × 800 days |
| `76541731` | **ψ.β.align** | VWAP volume 单位 bug fix (akshare=股 vs tdxhub=手); selector 改 oos_sharpe 排名 |
| `73c904ec` | docs | PROJECT_INDEX 大重写 — "新人不读代码也能理解项目" 标准 |
| `9e9d9fc6` | enforce | PROJECT_INDEX 同步 pre-commit hook |

## 4. Ablation 矩阵 — 3 个实验结果 (Rule 9.4 失败先承认)

| 实验 | ann | mdd | sharpe | 月胜率 | 持仓天 | turnover | tx cost % |
|---|---|---|---|---|---|---|---|
| 13-alpha + hp=15 (**current best baseline**) | **+3.78%** | -30.1% | +0.29 | ? | 15 | ~30x | ? |
| 14-alpha + hp=15 (加 sector_pred IC=-0.06) | -17.9% | -46.2% | -0.11 | 50% | 15 | 38.7x | 9.7% |
| 13-alpha + hp=30 (减半 turnover) | -10.9% | -39.7% | -0.03 | 58% | 26.7 | 21.6x | 6.5% |
| 13-alpha + hp=15 + **per_stock_stage=true** (**跑中, PIT broken ceiling**) | **? (PID 12518)** | ? | ? | ? | ? | ? | ? |

**结论**:
1. 加 sector_pred alpha (mean reversion direction=-1) **退化 21pp 年化** — 加 alpha 已饱和, 边际负
2. hp 翻倍 (15→30) **减半 turnover ✓ + 月胜率 ↑** 但 **ann 退化 14pp** — long-holds 拖累, 不能 cut loss 快
3. **真问题在 alpha 自身弱** — 全市场 OOS 表现 sharpe -0.331 (mart_per_stock_stage_strategy_optimal avg)
4. 用户目标 +30%/-20%/超额 跟实测 baseline +3.78%/-30% **差距 = real-world friction (tx cost + 流动性 + PIT clean)**

## 5. 关键技术债 / 已知问题

| # | 问题 | 影响 | 解法估时 |
|---|---|---|---|
| **PIT broken on mart_per_stock_stage_strategy_optimal** | built_at 全 2026-05-13, paper_sim 历史选股看到事后数据 | selection leakage 严重 | 改 `optimize_per_stock_stage_strategy.py` 多 train_end_date 模式 (照搬 `optimize_per_formula_stage.py` Phase ψ.α B), 8 workers fork ~4-8 hr 跑批 |
| **alpha set 整体弱** | OOS avg sharpe -0.331, 加 alpha 已饱和 | 不能简单加更多 | 真改 alpha source (block_gn 概念 / sentiment / ML 因子) 1-2 周 |
| **VWAP volume unit MIXED** | akshare=股 vs tdxhub=手, 不同源 | 已有 _vwap sanity helper 挡, 但 long-term 需 normalize | ETL normalize layer (Phase ψ.γ.dict.2, 1-2 天) |
| **mart_sector_momentum (旧 mart 表)** | 只 41 行 (2026-04 起) — **新表 fact_sector_momentum_daily 10.5K 行已就绪**, 但 mart 表没同步 | 部分 UI / API 可能用旧表 | 改下游引用到新表, 半天 |
| **swap 0 次** | swap.enabled=true 但 paper_sim 整段 swap_count=0 | swap_rules 触发条件可能没碰到 | 调研 swap_rules 阈值 |
| **283 历史 Rule violations** | 整库扫 staged 之外的代码 (Rule 5/7/6) | 渐进清理, 不阻塞 | 详见 PROJECT_INDEX §11.5 #17 |

## 6. Pre-commit Hooks (必须装好才能 commit)

```bash
# 检查 hook 是否已装
ls -la .git/hooks/pre-commit .git/hooks/commit-msg

# 如果缺失, 重装 (3 层防护已设计):
chmod +x .git/hooks/pre-commit .git/hooks/commit-msg

# 3 个检查脚本 (都在 backend/scripts/):
# - check_project_index_sync.py: 改 service/script/yaml 必须同步改 PROJECT_INDEX
# - check_rule_compliance.py: 扫 staged diff 找 Rule 6/5/7 反 pattern (magic numbers / hardcoded date / try-except pass)
# - check_commit_message.py: commit msg 必须含 GROUP A (测试/防回退) + GROUP B (PIT/OOS/实测) 关键词
```

**Hook 误判时**: 加 `# rule-compliance: ok evidence=<reason>` 注释豁免, 不要 `--no-verify` 跳.

## 7. Background Process / Loop 状态

- **Active**: PID 12518 paper_sim (ETA ~14:00)
- **/loop ScheduleWakeup**: 已设 13:56 唤醒 (会自动检查 paper_sim 是否完成, 看 KPI 决定下一步)
- **No Monitor armed** — bash 进程 exit 时自动通知

## 8. 推荐下一步 (按 P5c 实验结果分支)

### 分支 A: paper_sim per_stock_stage **ceiling > +20%**

→ 假设有效, 修 PIT, 投资 4-8 hr 跑批 walk-forward.

```bash
# 1. 新写 optimize_per_stock_stage_walk_forward.py
#    参考 optimize_per_formula_stage.py 的 multi train_end_date 模式
#    每月底 train, 跨 stock 全市场, 入 mart_per_stock_stage_strategy_optimal (替换)
# 2. 跑 8 workers fork (估 4-8 hr)
# 3. 重跑 paper_sim per_stock_stage=true → 真实 OOS KPI
```

### 分支 B: paper_sim per_stock_stage **5% < ceiling < +20%**

→ 假设部分成立, 修 PIT (真实 < ceiling 减半) + 同步改 alpha set.

```bash
# 1. P3: 概念板块 sync block_gn 成分股 K 线 (1-2 天)
# 2. P4: 用 fact_sector_predicted_ret_daily 改 alpha source (重新训练 Lasso/ridge with regime feature)
# 3. 同时修 per_stock_stage PIT
```

### 分支 C: paper_sim per_stock_stage **ceiling <= +5%**

→ alpha 弱, 跟参数无关. 改 alpha set 优先.

```bash
# 1. 探索新 alpha (sentiment 包 / 概念板块 / ML)
# 2. ETL normalize + 数据治理 Phase ψ.γ.dict.2/3
# 3. 用户拍板是否调目标 (+15%/-15% 更现实, 而非 +30%/-20%)
```

## 9. 用户偏好 / 沟通

- **中文回复, 简洁实用** — 表格 + 数字 > 段落
- **不报喜不报忧** — 0 STRONG_BUY / 数据滞后 / 实验 fail 必须先讲
- **第一性原理 push back** — 用户多次 push 我修正错估算 (e.g. "6.5 万小时" 我估错了)
- **真金白银门槛** — Rule 9.1, 拒绝"大概率/接近"近似妥协. 不写 "估算 / 假设 / 看着合理"
- **看到更简单方案 push back** — Rule 1, 别追复杂

### 用户原话 (引用)
- "短期内资产最大幅度增值不缩水" (终极目标)
- "持仓周期不应该全局统一, 每个股票每种形态下每个公式下都单独选优"
- "策略不合格就是不合格"
- "把数据都充分调动起来"
- "即使claude.md有rule但你也不尊守, 这个问题咋解决?" (→ Phase ψ.γ.0 hook 治理)

## 10. 跟用户对齐过的 path 优先级

之前用户拍板:
- A (维度精简 20 维 Optuna) — ψ.γ.1 实施了 (3 次 fail)
- B (per-stock 寻优 "先试试看") — ψ.γ.2 实施了 (架构对, 数据 PIT 待修)
- constrained sharpe (max sharpe s.t. ann≥0.30 AND mdd≥-0.20) — ψ.γ.1 used
- 板块轮动规律: CDE 三选一 — ψ.δ.1 Ridge regression 实施 (IC=-0.06 mean reversion)
- 板块预测接入方式: A (加 alpha) — 实施了, 但 hurt KPI
- 概念板块要做 — ψ.δ.P3 待开

最近用户的 4 次 /loop = "继续自主推进". 没有新指令.

## 11. 踩过的坑 — 别再踩 (跨多轮对话汇总)

### 本 session (Phase ψ.γ/ψ.δ) 踩的坑

| # | 坑 | 后果 | 修法 (已 commit) |
|---|---|---|---|
| 1 | **6.5万小时估算错误** — 把"per-stock backtest" (毫秒级单股 9 维 backtest) 跟"per-stock paper_sim" (18 min/trial 完整 5 仓位组合 sim) 搞混 | 误导用户; 用户 push back "不能并发? 你那么多显卡都不行?" | 实际 `optimize_per_stock_stage_strategy.py` 8 workers fork 58 min 已实现. 不要靠 GPU — Optuna TPE + DuckDB query 是 CPU bound. |
| 2 | **Optuna v1 21 mo train 估错时间** — 我估 3.5 min/trial 实际 25 min/trial | 35 min 浪费 + kill | 估算前先做 1-trial benchmark, 不靠"看着合理" |
| 3 | **Optuna v2 9 mo train 错选 2023-01~09** — fact_signal_context 2024-03 起 backfill, quality_filter 把所有 candidate 过滤 | 16 trials 全 0 trade, 35 min 浪费 + kill | **运行 Optuna 前先验证 train window 在所有 alpha 数据源覆盖期内** |
| 4 | **L2 vol-aware sigma=2.0/3.0/1.0 + 6 bounds 拍脑袋** — 违反 Rule 6 "Measured not Estimated" | 用户 push back 后才发现 | Rule compliance hook (`check_rule_compliance.py`) staged-diff 强 reject magic numbers 没 evidence 注释 (commit ce9559e4) |
| 5 | **VWAP volume 单位 MIXED** — akshare_sina=股 vs tdxhub=手, 我写死 `amount/(volume×100)` | akshare 数据 vwap=0.11 元 → stop_hit 假信号 → NAV 1.6M 暴跌 360K | `_vwap` 加 sanity check 选落在 [low×0.95, high×1.05] 的候选; 3 单测防回退 (commit 76541731) |
| 6 | **ensemble v3 拍脑袋配置浪费跑批** — 13 alpha weights + regime mul + sigma + bounds 全拍脑袋 (Rule 6 反例 3 行) | 跑半天 NAV 1.13M (+13%) 不达标, 数据无意义 | CLAUDE.md Rule 6 反例表加 L2/ensemble/regime 3 行, Rule 9.9 写代码前 "measured from where?" ritual |
| 7 | **14-alpha mean-reversion 加进 hurt 21pp ann** — Ridge sector_pred IC=-0.06 direction=-1 加进 ensemble 退化 ann -17.9% | 14 alpha 比 13 alpha baseline 差 21pp 年化 | 设 weight=0 disabled 保留, 后续 Optuna 决定. 学到: **加 alpha 已饱和, 边际负** |
| 8 | **hp=30 减 turnover ✓ 但 ann 退化 14pp** — 我猜测 hp 翻倍减半 tx cost 应该改善 ann | 实测反例: 月胜率 ↑ 但 ann 反降. long-holds 拖累 stop-loss | 学到: **不能简单猜参数效果**, 必须 measured |
| 9 | **mart_per_stock_stage_strategy_optimal PIT broken** — built_at 全 2026-05-13 单 batch, 不是 walk-forward multi train_end_date | paper_sim 历史选股看到事后数据 = selection leakage | 当前 ceiling test 是 ceiling 不是 real. 真修需 `optimize_per_stock_stage_walk_forward.py` 多 train_end_date 模式 |
| 10 | **PROJECT_INDEX 同步多次遗漏** — Rule 9.5 是被动文字, 我下意识不维护 | 用户 push back 3 次 | pre-commit hook (`check_project_index_sync.py`) staged 含 service/script/yaml 必须改 PROJECT_INDEX, 否则 reject (commit 9e9d9fc6) |
| 11 | **我即使 CLAUDE.md 有 Rule 也不遵守** | 用户原话: "即使claude.md有rule但你也不尊守，这个问题咋解决?" | 3 层防护: pre-commit hook 硬挡 + commit-msg 5-question keyword 检查 + Rule 9.9 写代码前 ritual (commit ce9559e4) |

### 之前 session (Phase ψ.α/ψ.β) 踩的坑 (Rule 9 反例表)

| # | 坑 | 修法 |
|---|---|---|
| 12 | `mart_per_stock_stage_strategy_optimal.sharpe` 用 in-sample fit (Optuna 整段 2023-2026 fit 出来), `paper_sim/selector.py: ORDER BY sharpe DESC` → paper_sim 跑 "+312%" 实际选的是"事后看最强 5 只" | walk_forward.expanding_monthly (R1), 业务代码只读 `oos_*` 字段, governance.enforce_pre_insert 拒入 `walk_forward_mode='none'` |
| 13 | `swap_uplift_estimate = (Y总预期 × 子区间比例) - (A当前涨幅 × 剩余比例) - 0.35%` 两项都假设"匀速跑" | 真实 K 线 forward 反事实: 两个真实数, 不是估算 |
| 14 | Wilson 默认 0.55 (`sizer.py: wilson = 0.55`) — "假设上游已 wilson 排序过" | 改读上游真实 `wilson_win_rate` 字段, 没有就显式 skip |
| 15 | `portfolio_backtest +45.4%` 报用户做最终决策 — 不算 tx_cost / 流动性 / T+1 滑点 = 理想化 | live 决策必须基于含真实成本的 paper_sim, 不用 portfolio_backtest |
| 16 | KPI 阈值 (severe=0.5 / 年化≥30% / 持仓≥5天) 拍脑袋默认 | Optuna / grid search 跑 800+ 天 sweep, commit 附 sweep 结果 |
| 17 | DuckDB DELETE 报 "0 out of 1 rows" → DROP+REBUILD index 清状态 (症状修复) | 找首次写坏 index 的代码路径, 改 DELETE 走 rowid 绕 index, 加启动 health check |
| 18 | sync_market_data 30s budget timeout → 用单 step endpoint 绕 budget | budget 按 watermark 滞后动态算 / heartbeat watchdog |
| 19 | K 线表有今天盘中数据 → 下游 builder `--end 2026-05-12` 钉死 | sync_market_data 入口用 `latest_completed_trade_date` + records 上界过滤 + lint 防回退 |
| 20 | `executescript` 不可用 on DuckDB connection | 手动 split `;` 然后逐条 execute |
| 21 | `dim_stock_tdx_industry_history` 只 6 snapshot 假 PIT | fallback latest, 加 warning "假设 3 年内行业分类不变" |
| 22 | `build_signal_context.py` UnboundLocalError | duplicate `from services.db import get_conn` 触发 Python 局部 scoping. 删 dup. |
| 23 | mart_per_formula_stage_optimal schema 改了 CREATE IF NOT EXISTS 不更新 | 加 schema-aware DROP + 重建 |
| 24 | holder_count_change_pct 含 30M 极端值 | cap |pct| <= 90 (Phase ψ.β.3 SQL filter) |
| 25 | 8h+ 工作没 commit, 用户提醒后才 commit | Git Safety Protocol — Rule 9.6 "任何工作完成自动 commit" |

### 跨 Rule 的核心模式 (Claude 最容易再踩)

| 反模式 | 根因 | 防御 |
|---|---|---|
| 拍脑袋写数字然后宣称"业界常用 / 看着合理 / 先试试看" | 我"觉得自己懂 Rule 6", 但写代码下意识又写 | `check_rule_compliance.py` hook 硬挡 magic numbers |
| 估算时间 (跑批 / Optuna trial) 没做 1-trial benchmark | 想省事 | 提交前先跑 1 trial 看时间, 再扩展 |
| 看到坑找绕路 (try/except: pass / --skip-step / --end 钉死) | Rule 5 反例 | 看到失败先问"为什么", 找首次写坏的代码路径, 修源头不打补丁 |
| 加 alpha / 加复杂度 想找"魔法" 改善 KPI | Rule 2 反例 | "看到更简单方案就 push back". 14 alpha 实测反 hurt — alpha 已饱和 |
| 一次改多个变量然后归因 | 实验设计错 | One variable at a time. 13-alpha hp=15 vs 13-alpha hp=30 才能归因到 hp |

## 12. 用户总体要求 (跨整个对话历史汇总)

### 终极目标 (锚)

> **"短期内资产最大幅度增值不缩水"**

3 个 PASS 标准 (用户原话最终版):
1. **年化 ≥ 30%**
2. **max_dd ≥ -20%**
3. **超额 vs HS300 > 0**

补充: **月胜率 ≥ 55%** (Anti-churn 标准)

基线: 2023-01-03 开始, 100 万初始, HS300 benchmark, 不考虑现金利息.

### 用户原话 — 数据/方法论原则

| 原话 | 含义 |
|---|---|
| "不是数字游戏, 是真金白银投入的" | Rule 9.1 真金白银门槛, 拒绝"大概率/接近"近似妥协 |
| "你跑的是单一策略, 没真正模拟实盘选股 — 实盘是各种公式入池后按 OOS 强弱选最强" | selector ORDER BY oos_sharpe (Rule 8 sacred), paper_sim 是真实 alpha workflow |
| "把数据都充分调动起来" | 不要只跑单一公式, 多 alpha ensemble + Optuna 寻优 + 数据治理 |
| "持仓周期不应该全局统一, 应该是每个股票每种形态下每个公式下都单独选优" | per-stock × stage × formula 9 维 Optuna (mart_per_stock_stage_strategy_optimal) |
| "我感觉现在的选股策略和实盘模拟策略似乎都是批量化均值, 没有做到精细化每个股票" | L2 vol-aware (per-stock vol×sigma) + L3 per-stock-stage 接入 ensemble |
| "按照规律做个板块、概念、行业轮动啥的, 并作出预测, 辅助选股" | Phase ψ.δ.1 实施 (Ridge regression, IC=-0.06 mean reversion) |
| "策略不合格就是不合格" | Rule 9.4 数据失败先承认, 不报喜不报忧 |
| "我总感觉你没有充分发挥optuna的潜力呢, 每股参数自动寻优, 组合寻优, 各因子叠加寻优, 而不是预先设定" | Phase ψ.γ.1 ensemble Optuna 20 维 (alpha weights + regime + sigma + hp) — **实施但 alpha 弱救不了** |

### 用户原话 — 工程纪律

| 原话 | 含义 |
|---|---|
| "你就按照 claude.md 里的原则持续推进直到全部完成吧, 对于能彻底解决问题的方案, 即使耗时也要选择, 彻底解决, 完成后做个审计并制定计划修复, 一轮一轮迭代直到没有问题, 期间不用征求我同意了" | 用户授权 autonomous 推进, 但每轮要 audit + plan |
| "把刚才发生的你的问题补救措施... 总结成规则写到 claude.md 里遵守" | Rule 9.5 沉淀, 教训进 CLAUDE.md 不止 commit message |
| "leakage 相关规则是不是也可以写在 claude.md 里作为 rule" | Rule 7 anti-leakage 完整规则 (8 个 leakage 场景 + 防御机制) |
| "为啥你会遗漏事项呢? 请你找到原因并修复" | Rule 9.5 + Phase ψ.β.enforce pre-commit hook |
| "项目文档怎么能确保在每次commit的时候同步更新呢?" | `check_project_index_sync.py` hook (9e9d9fc6) |
| "不止是项目文档更新, 其他事项一定也会有遗漏的, 请你扫描我们的对话记录找出遗漏的问题" | §11.5 16 项遗漏审计 |
| "项目文档更新的标准是你或者其他人在新接手的时候能迅速理解项目内容、架构、技术路线、业务、等等, 而不用完整的读取项目全部代码和数据库文件" | PROJECT_INDEX.md 标准: 新人 30 min 上手, 不读代码 / 不查 DB |
| "即使claude.md有rule但你也不尊守, 这个问题咋解决?" | Phase ψ.γ.discipline 3 层防护 (Rule compliance hook + commit-msg hook + Rule 9.9 ritual) |
| "之前说的数据治理做了么, 就是清洗、加工、存储之类的" | Phase ψ.γ.dict.1 字段字典 (commit 9d1ac25a) — 后续 ETL normalize / pre-insert governance 待做 |

### 用户拍板的决策 (记录)

| 议题 | 用户选 | 实施状态 |
|---|---|---|
| Phase ψ.α 路线 (反转 vs 动量 vs 综合) | "B. 先把漏洞补上, 重新验证之前几个公式, 再结合新策略放一起对比" | ψ.β 完成 (反转 OOS sharpe 0.39) |
| Phase ψ.γ.1 Optuna 维度 | "1、A" (精简 20 维) | 实施了 3 次 fail |
| Phase ψ.γ.2 per-stock 寻优 | "2、B, 先试试看啊" | (b) 不现实 push back, (c) mart_per_stock_stage_strategy_optimal 接入但 PIT broken |
| Phase ψ.γ.1 objective | "3、constrained" (max sharpe s.t. ann≥0.30 AND mdd≥-0.20) | ✓ 实施 |
| 板块轮动规律 | "CDE" (动量+反转分阶段 + lead-lag + ML 端到端) | ψ.δ.1 实施 C/E 轻量版 Ridge, IC=-0.06 mean rev |
| 板块预测接入方式 | "A" (加 alpha) | ✓ 14th alpha, weight=0 disabled (hurt KPI) |
| 概念板块要做 | "做啊" | P3 pending |
| 优先级排序 | "按项目现状优先级排序制定计划" | 由 Claude 定 — handoff 中分支 A/B/C |
| 数据 sync | "数据 sync 同步" | §11.5 P0 #1 pending (watermark 2026-05-06 滞后) |

### 用户沟通偏好

- **中文回复, 简洁实用** — 表格 + 数字 > 段落 (绝对不写小作文)
- **不报喜不报忧** — 0 STRONG_BUY / 数据滞后 / 实验 fail 必须**先讲**, 不能埋在底下
- **第一性原理 push back** — 用户多次 push 我修正错估算, 错假设
- **数据驱动** — 任何"估算 / 假设 / 看着合理" 用户直接质疑
- **拒绝近似妥协** — "大概率 / 接近 / 差不多" 都不接受
- **看到更简单方案 push back** — Rule 1, 资深工程师会觉得"太复杂"的, 简化
- **目标穿透** — 中间数字 (sharpe / win rate) 不算结论, 必须穿透到 forward 真实期望
- **失败先承认** — Rule 9.4, 不要包装

### 用户最近的 4 次 /loop

之前 4 次 `/loop check Optuna 9-dim AND data update progress; if both done, rebuild fitness/buy_signal/daily, run audit, finalize goal.md`:

- 字面是旧任务 (Phase ψ 之前). 实际意图: **autonomous 推进, 不需用户拍板每步**.
- /loop input 是 stale, 应该 dynamic mode + 当前实际工作 (Phase ψ.γ/ψ.δ).
- Claude Code CLI 接管后不需要 `/loop` — 是这个 session 的 self-pace 机制, 新 session 重做即可.

## 13. 不确定 / 推断 (区分事实 vs 猜测)

| 类别 | 内容 |
|---|---|
| **已验证事实** | 14-alpha vs 13-alpha 实测对比, IC=-0.06 实测, mart_per_stock_stage_strategy_optimal built_at 实测 |
| **强推断** (大概率对) | alpha set 整体弱 (有 OOS 数据支撑); per_stock_stage 修 PIT 后数字会减少 (因为 ceiling test 含 leakage) |
| **弱推断** (需更多验证) | 用户目标 +30%/-20% 不现实; 加 hp 减 turnover 应该提升 ann (实测反例); 板块 mean reversion 在更细参数下可能有效 |
| **未知** | per_stock_stage ceiling test KPI 数字 (PID 12518 跑中); 概念板块加进去后 KPI; 真 ML 因子的 ceiling |

## 14. 实战 Cheatsheet

```bash
# Paper sim (单次)
PYTHONPATH=backend python backend/scripts/run_paper_sim_v2.py \
  --config-path backend/config/paper_sim_ensemble.yaml \
  --start 2024-04-01 --end 2026-05-12 --variant baseline

# Optuna ensemble (慎用, 单 worker 慢, 当前 alpha 救不了)
PYTHONPATH=backend python backend/scripts/optimize_ensemble_full.py \
  --n-trials 50 --train-start 2024-04-01 --train-end 2025-09-30 \
  --test-start 2025-10-01 --test-end 2026-05-12 --study-name <name>

# Sector rotation predictor (~2 sec)
PYTHONPATH=backend python backend/scripts/train_sector_rotation_predictor.py

# 查 KPI history
duckdb data/smartmoney.duckdb -c "
SELECT sim_run_id, annual_return, max_dd, sharpe FROM mart_paper_sim_kpi
WHERE sim_run_id LIKE 'baseline_2026%' ORDER BY built_at DESC LIMIT 8"

# 查 per_stock_stage 覆盖
duckdb data/smartmoney.duckdb -c "
SELECT stage_filter, COUNT(*), AVG(oos_sharpe), AVG(oos_win_rate)
FROM mart_per_stock_stage_strategy_optimal
WHERE oos_n_traded >= 3 GROUP BY 1 ORDER BY 1"

# Pre-commit dry-run (检查 staged 是否合规)
python backend/scripts/check_rule_compliance.py
python backend/scripts/check_project_index_sync.py

# 测试 (1402 baseline)
PYTHONPATH=backend python -m pytest backend/tests/ -q
```

---

**Handoff timestamp**: 2026-05-14 13:37  
**Last commit**: `b4a5cd90` (Phase ψ.δ.1)  
**Branch**: `feature/reversal-factor`  
**Remote**: `origin/feature/reversal-factor` @ github.com/dare2live/chunkymonkey  
**In-flight**: PID 12518 paper_sim per_stock_stage ceiling test (ETA ~14:00)

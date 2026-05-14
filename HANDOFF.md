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

## 11. 不确定 / 推断 (区分事实 vs 猜测)

| 类别 | 内容 |
|---|---|
| **已验证事实** | 14-alpha vs 13-alpha 实测对比, IC=-0.06 实测, mart_per_stock_stage_strategy_optimal built_at 实测 |
| **强推断** (大概率对) | alpha set 整体弱 (有 OOS 数据支撑); per_stock_stage 修 PIT 后数字会减少 (因为 ceiling test 含 leakage) |
| **弱推断** (需更多验证) | 用户目标 +30%/-20% 不现实; 加 hp 减 turnover 应该提升 ann (实测反例); 板块 mean reversion 在更细参数下可能有效 |
| **未知** | per_stock_stage ceiling test KPI 数字 (PID 12518 跑中); 概念板块加进去后 KPI; 真 ML 因子的 ceiling |

## 12. 实战 Cheatsheet

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
**Remote**: `origin/feature/reversal-factor` @ github.com/dare2live/chunky-monkey-v2  
**In-flight**: PID 12518 paper_sim per_stock_stage ceiling test (ETA ~14:00)

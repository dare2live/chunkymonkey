# CLAUDE.md — 工程规则

> 配套 `PROJECT_INDEX.md` (项目地图: 数据 / 模块 / 已知坑 / 用户终极目标). 本文档是规则.
> 压缩后第一件事: 重读 PROJECT_INDEX.md 防 context 失真.

---

## 1. Think Before Coding

- 没有隐藏假设, 说出来 + 列 tradeoff. 不确定就**问**.
- 最少代码解决问题. 不写 speculative feature (不为"将来可能"写). 单次代码不抽象成框架.
- 只改必须改的, 不"顺手优化". 风格匹配现有, 不引新风格.
- 资深工程师觉得太复杂的 → 简化.

## 2. Goal-Driven Execution

- 定义"成功长什么样" + 循环到验证通过, 不告诉 Claude "step 1 X, step 2 Y".
- 成功 = 用户能 verify 的可测试结果.

## 3. Root Cause (数据/sync/DB 写入**严禁忍**)

**禁止**: `try/except: pass` · `--skip-step` · `if env: bypass` · `--end YYYY-MM-DD` 钉死规避上游 bug · 单 step endpoint 绕 budget.

**必做**:
- 找**首次**写坏 / 抛错的代码路径, 修源头 — 不只清状态.
- 症状修复 (DELETE 坏行 / DROP+REBUILD) ≠ 根因修复 (找写坏路径). 两者都做, 只症状修复 = 故障会再来.
- 找不到根因**明说**, 加防御 (启动 health check / 失败 raise / lint 防回退). 防御 ≠ 修复, 但比静默 bypass 强百倍.
- 暂时绕过必须 TODO + 关联 commit, 不能伪装"已解决". 真解决 = 根因修 + 防回退测试 + 历史污染清 + 端到端验.

**数据源可信度** (用户原话):
- **tdxhub / miaoxiang**: 100%, 缺失 = 自己 sync 路径 bug
- **akshare**: 不稳定 (限频/接口变), 缺失可能上游
- "上市公司数据不会真缺失" — 不假设 upstream 缺, 优先 tdxhub/miaoxiang 重拉

**反例 (踩过)**:
- K 线含盘中数据 → 不要 `--end 2026-05-12` 钉死, 改 sync 入口 `latest_completed_trade_date` + lint 防回退
- DuckDB DELETE FATAL → DROP+REBUILD index 清状态不够, 找首次写坏路径 + health check
- `fact_shareholder_plan.announce_date` 47% NULL → 不放松 audit 阈值, 查 ingest 路径 (commit 69371838: 写 7034 placeholder 行 + DELETE 历史 + 防回退)
- 已有 utils 没 grep 就造重 — 动手前 grep 是子条款

## 4. Measured Not Estimated

任何**参数/阈值/模型预测/策略效果**必须真实历史测过, 不能公式估.

**禁止**: "差不多" "估计" "假设" "按当前速度跑" "线性外推" — 全 anti-pattern. 变量名含 `_estimate / predicted_ / assumed_` 停下问: 真来自数据还是公式拍脑袋?

**必做**:
- "uplift/score/收益/胜率/风险" 类指标必能答: 哪些历史 row + 时间窗 + 哪个 fact 测出来?
- 测不出来 (数据缺) → 显式标 `unknown`, 不公式糊弄.
- yaml 默认必附 backtest 证据 (commit hash / 测试 ID / KPI 数字).

**Self-check (性能数字提交前)**:
1. 这数字从哪行 SQL 跑?
2. 涵盖几行 / 几天真实历史?
3. 换 `unknown` 决策会不一样吗?
4. 用户能复现吗?

不能干净答 = estimate not measured, 不许提交.

**反例 (踩过)**:
- `swap_uplift_estimate = (Y总预期 × 比例) − (A涨幅 × 剩余) − 0.35% buffer` 全公式假设 → 实测 swap 拉低年化 33pp. 改真实 K 线 forward 反事实, 两个真实数.
- vol-aware `stop_sigma=2.0/target=3.0/trailing=1.0` + 6 bounds 全 hardcode "业界常用 -2σ+3σ+1σ" → 丢 Optuna search space (Phase ψ.γ.1), walk-forward 拼 OOS 入 mart.
- ensemble 13 weights "业务直觉" 写 yaml → 全丢 Optuna.
- regime_gate `bear=0.3/sideways=0.7/bull=1.0` 拍脑袋 → 历史 regime sensitivity sweep.
- `portfolio_backtest +45.4%` 当最终决策 → 不含 tx_cost/T+1, paper_sim 加成本骤降. live 必须用含成本 paper_sim.

## 5. Anti-Leakage / PIT (Rule 7)

时刻 t 的决策**只能**用 ≤ t 信息. 违反 = 数字全是假.

| 场景 | 错例 | 正解 |
|---|---|---|
| 调参 | Optuna 全段 in-sample | walk_forward.expanding_monthly, Optuna 看早窗 |
| 排名 | 用全段 sharpe | OOS rolling 60d NAV uplift / rolling IR |
| 特征 | `bars[sig_i+1:]` 未来 K 线 | `bars[:sig_i+1]` (含当日 close 注意盘前可用性) |
| Label | 未来 N 日涨幅无 purge | purged k-fold (Lopez de Prado) + embargo ≥ 1× forward 期 |
| JOIN | `x.date <= t` 但 `x.built_at > t` | `AND x.built_at <= t` 或 `as_of_date` |
| 宇宙 | 今天 HS300 回测 2018 | `dim_index_member_history.as_of_date` |
| 复权 | 最新 qfq 算 2018 价 | PIT 复权 / rebalance 时用当时因子 |
| 生存者 | 只用现存上市股 | 含已退市 + 入场时点宇宙 |

**Self-check** (t 时刻决策提交前):
1. 这数字在历史 t **当时**能算出来吗?
2. 输入字段 `built_at/as_of_date/数据可用日` 都 ≤ t?
3. train/test 按时间还是 random? (random = leakage)
4. 有 selection bias (挑"现存上市"/"已知龙头")?
5. 跨期 label 有 purge + embargo?
6. **数字异常好看了吗?** **absolute**: RankIC > 0.3 / sharpe > 5 / win_rate > 0.95 / 年化 > 100% / 胜率 100% → **立刻怀疑 leakage, 不是兴奋**. 真实 forward 期望永远比回测低. **relative (2026-05-15 加, 跟绝对同重要)**: 相对 baseline 提升 ≥ +50% (e.g. v1 RankIC 0.02 → v3 0.035 是 +75%) → 也算异常, 必须 ablation 验证每 col 群 PIT 干净度. 反例: 我 v3 102 features 0.0353 没触发 absolute 但触发 relative — chain 跑出来含 inst_path_a (latest snapshot) + sector (99.978% fallback) leakage. 详 [[feedback-codex-critical-no-compromise]].

不能干净答 = leakage, 不许提交.

**异常高数字 = leakage 警报信号** (用户原话: "之前一版本 100% 胜率, 收益超高, 因为 Optuna 读完整 3 年 K 线倒推买卖点"):
- paper_sim 跑出 +312% / 高胜率 → 几乎一定 leakage. 历史根因: `mart.sharpe` 是 Optuna 全期 in-sample fit, selector ORDER BY 等于"事后挑最强"
- 修法三件套: (a) Optuna 改 walk_forward.expanding_monthly Optuna 只看早窗; (b) 入库 `oos_sharpe` (多窗 OOS 拼接), selector ORDER BY `COALESCE(oos_sharpe, sharpe)`; (c) `governance.enforce_pre_insert` 拒 `walk_forward_mode='none'` + sharpe > 5 / win > 0.95 raise
- v3.2 P0b 实测 RankIC 0.0108-0.0203 (跨 5/10/20 horizon) = 干净 PIT 下的诚实数字; 跟历史 +312% 假象**完全相反**, 这是好事

**防御**:
- 表加 `built_at/as_of_date` 列, JOIN 永远带 `AND xxx.built_at <= ?`
- 时序切分走 `services/optimization/walk_forward.py::split_*` (含 `assert_no_temporal_leak`)
- 业务表 forward metric (sharpe/win_rate) 必须标 OOS, 不跟 in-sample 混
- 入库前 `services/optimization/governance.py::enforce_pre_insert` 守门

**反例 (踩过)**:
- `mart_per_stock_stage_strategy_optimal.sharpe` 是全期 in-sample fit, `selector ORDER BY sharpe DESC` 当 forward 排名 → paper_sim "+312%" 假象. 改 expanding_monthly 拼 OOS + selector `ORDER BY COALESCE(oos_sharpe, sharpe)` + governance 拒 `walk_forward_mode='none'`.
- v3.2 feature_join `stage_opt_per_stock` MAX(oos_sharpe) GROUP BY stock_code 给每 signal_date 用未来 Optuna → systemic leakage (Codex acf48d35 review Q1 critical fix, commit 5cc47987).

## 6. Optuna 治理 (Rule 8, Rule 7 在调参层落地)

**必走 `services.optimization` 中央层**, 不裸调 `study.optimize`. 全部阈值/区间/权重/表名走 `backend/config/optuna_config.yaml`, 不 hardcode.

**3 守门点**:
1. **时序切分**: `walk_forward.split_dispatch(signals)` (默认 R1=`expanding_monthly`), Optuna 看早窗 train
2. **预校验**: `governance.enforce_pre_optimize(n_trials, has_seed=True)` (强制 50 ≤ n_trials ≤ 500 + 固定 seed)
3. **OOS 验证**: best params 在 test 集重跑 → `governance.enforce_pre_insert(record)` (`walk_forward_mode='none'` / OOS 字段缺 / sharpe>5 / win>0.95 → raise)

**业务表约定**:
- `mart_per_stock_*_optimal` 必须有 OOS 列 (`oos_sharpe/oos_win_rate/oos_avg_ret/oos_n_traded/oos_period_*/walk_forward_mode/train_n_signals/test_n_signals`)
- selector/scoring **只读 `oos_*`**, 老字段 (`sharpe/win_rate/avg_ret`) 仅描述/兼容
- 新加寻优表必须照搬这套

**R1 expanding_monthly 标准**:
- 每月底切, 前 `min_train_months` (默认 6) 月当 train base
- Optuna 在最早窗选 best params, 然后用 best 在每个后续 OOS 月跑
- 多窗 trades 聚合算 `oos_sharpe/win_rate/avg_ret` (走 `oos_aggregator.aggregate_oos_metrics`)
- 入库 sharpe = 多窗 OOS 真值, 不是 in-sample fit

**审计**: 任何 reject 写 `fact_optuna_governance_log` (PK=`run_id`, `record_json` 全量 + 原因)

**Optuna 不用未来函数 — 3 道防线**:
1. **数据切分**: `walk_forward.split_expanding_monthly(signals)` 严格 train_months[0..k-1] / test_month[k] 切分, Optuna 只看 train 集 fit, **不参与决定 test 内容**
2. **搜索空间**: Optuna 搜的是**策略行为参数** (hp/stop/target/trailing/形态阈值), 不是数据查找. 不会"看未来 K 线选参数"
3. **入库守门**: `enforce_pre_insert` 拒 `walk_forward_mode='none'` / OOS 字段缺 / sharpe>5 / win>0.95 → raise (100% 胜率会被拦)

**反例 (踩过, 别重)**: Phase ψ.γ 之前 `mart_per_stock_stage_strategy_optimal.sharpe` 入库 in-sample fit, `selector ORDER BY sharpe DESC` 等于"事后选最强 5 只", paper_sim "+312%" 高胜率假象. 修法见 Rule 5 反例.

## 7. 真金白银 — 策略 / 实盘 / 钱投入

把每行代码当"上线后亏钱我能睡着吗"评估. 用户原话: **"不是数字游戏, 是真金白银投入的"**.

**拒绝近似妥协**:
- "leakage 影响估计 < 10%" → **0 leakage**. 5-10% 误差实盘是亏损线.
- "含等量 leakage 公平对比" → 实盘没 baseline.
- "回测 +312% 看上去不错" → 立刻怀疑 leakage 不兴奋. 真实 < 回测.
- "先跑试试" → 跑前想清"出数能直接决策吗", 不能别跑.

**第一性原理 push back** (主动质疑自己妥协):
- 写出"含 X 但 Y 仍有效"时, 自问"按第一性原理 X 应该存在吗?"
- 妥协论证 90% 来自想省事. 真正干净的方案才是正解.

**目标穿透** (用户终极目标: 年化≥30% / max_dd≥-20% / 超额 HS300>0 / 月胜率≥55%):
- in-sample sharpe +2.0 → fit 还是真 alpha?
- horizon_evidence sharpe +1.10 → 含 selection bias?
- paper_sim +65% → 含 leakage? 真实 forward 期望多少?

不能穿透 = 噪音, 不能决策.

**失败先承认**: 反转 OOS 不及格 → 立刻换方向, 不留恋. "不合格就是不合格" (用户原话).

## 8. 工程纪律 — git / 模块化 / 配置化

- **git**: 任何工作完成自动 commit + push. 单分支 `main`, 不开 feature/worktree. 紧急 hotfix 也在 main.
- **模块化 + 不硬编码**: 数值/阈值/路径/日期/表名走 yaml. 改参数 = 改 yaml 一行, 业务代码不动. `if stock_code == "600036"` / `hp = 30` 写死禁止.
- **复用 (动手前 grep)**: 同逻辑出现 2 次 → 抽公共; 3 次还重写 → 立即停下重构. 单次不预先抽象 (Rule 1).
- **PROJECT_INDEX 同步**: 改 service/script/yaml/Rule 必须同步改 PROJECT_INDEX. Pre-commit hook reject 不同步.
- **doc 自维护** (用户原话: "每次修改 claude.md / memory 时直接做优化更新"): 每次编辑 `CLAUDE.md` / `memory/*.md` 时**顺手做整理**, 不要被动等用户提醒. 检查:
  - **过期**: 状态描述跟现状不符 → 改/删 (e.g. v3 plan 已被 v3.2 取代, 删旧建新)
  - **冗余**: 同一概念在多处重复 → 合并到一处 + `[[name]]` 链接 (e.g. 反例 commit hash 集中到 status 文件, 不在多 memory 重复贴)
  - **结构**: 长 file (> 200 行 CLAUDE.md / > 100 行 memory) 该分组/层次化, 用 section header
  - **链接**: `MEMORY.md` 索引按类型/重要度分组, `[[name]]` 串联相关
  - **deprecation**: 早期方向 / 已偏离的方案不删, 加 "⚠ 状态: 已偏离 / deprecated" 头注 + 仍可复用部分指引
- **不偷工**: 用户要全量真实数据, 不"快速验证小样本".
- **诚实**: 数据告诉什么就报什么, 不报喜不报忧.

## 9. Self-Check — 写代码 + Commit 双层

### 9.1 Write-time (打数字字面量进代码**前**)

1. 这数字 measured from where?
   - Optuna study → `# measured: optuna study <id>`
   - backtest commit → `# evidence: backtest <commit-hash>`
   - yaml fallback → `# from yaml: <section>`
   - **都没有 → 停手**, 加 yaml 或跑 Optuna
2. **不接受**: "看着合理" "业界常用" "我估计" "先用这个试" "跑出来不好再调"
3. **yaml-back 默认**, 业务代码只读 yaml
4. **可 hardcode (写注释解释)**: 数学常数 (sqrt(252), pi, 100 股/手) / 边界 (0, 1, MIN_FLOAT) / 测试 fixture / SQL LIMIT

### 9.2 Commit-time (`git commit` **前**逐项)

1. **PROJECT_INDEX.md 同步了?** — 新数据表/service/script/yaml/反例都要同步. hook 是最后防线.
2. **测试加了?** — 核心逻辑单测; perf 改有 benchmark 防回退测试.
3. **commit message 含数字证据?** — "实测 4.8M 行 / 12 min / sample 验证", 不是 "fixed".
4. **反例表加了?** — 这次踩的新坑沉淀到 Rule 5/6/7/9 反例.
5. **真金白银 self-check** (策略 commit): 含 leakage/估算/假设? 穿透 forward 期望?
6. **改了 CLAUDE.md / memory 时顺手优化了吗?** — 顺便检查过期/冗余/结构 (见 §8 doc 自维护). 不要被动等用户提醒"再整理一下".
7. **本次 fix 产生 stale artifact 都清了吗?** (用户 2026-05-15 push back "我不问你也不想着"). commit 含 fix/bug/leakage/cleanup/drop/remove/revert 关键词 OR 改 SQL/schema/_META_FIELDS/kill process → **强制 invoke `/post-fix-audit`** 走 5 步:
  □ 列举 touched files / tables / configs (git diff)
  □ 每 artifact 主动想 downstream stale (旧 model_id / panel cols / paper_sim run / cache)
  □ DB residue check (`cleanup_leakage_data.py` dry-run)
  □ Process / cache / tmp file 清单
  □ Execute cleanup + verify 0 residue
  反模式: kill 进程 + 修代码 + restart 就以为完事, 没清 DB row / 没 ALTER DROP COLUMN / 没标 downstream model stale. 反例见 [[feedback-leakage-cleanup]]: Codex a8c34359a 标 CRITICAL → 我修代码 → commit → 但 panel 含 inst_path_a 5 cols 物理数据没清, paper_sim 若误读会 leakage.

不能逐项 yes = 别 commit. 重做.

### 9.3 技术层 enforcement

| Hook | 防什么 |
|---|---|
| pre-commit `project-index-sync` | 改 service/yaml 没改 INDEX → reject |
| pre-commit `rule-compliance` | magic 数字 / hardcoded date 无 `# evidence:` 注释 → reject |
| commit-msg `self-check` | message 缺关键词 (测试/防回退/PIT/OOS/实测) → reject |
| pre-commit `ruff` | 代码格式 (可选) |

误判 → 改对应脚本 `PATTERNS` / `EVIDENCE_KEYWORDS`. **不要 `--no-verify` 跳**.

## 10. Codex 协作 — Review Gate + 主动任务分配

> **用户原则 (2026-05-17 push back)**: "充分利用 Codex 各种能力, 增加对话轮次, 分配更多任务, 请固化". Codex **不只是 review 工具**, 是 capability-rich 协作 agent. 不只在 commit gate 用; 主动给 Codex 派任务 — 设计 / 调研 / 数据 audit / SQL 重构 / 长 doc 写作 / PIT 严格化 / factor spec / yaml schema 都该 dispatch.

### 10.0 主动派 Codex 任务的场景 (新加 2026-05-17)

| Codex 适合做的事 | 例 |
|---|---|
| **架构 / 设计文档** | architecture audit (Round 26), SUE PIT 设计 doc (Round 30) — 1000+ 行, 含 DDL/SQL/decorator/test 模板 |
| **第三方工具调研** | awesome-quant 评估 (Round 29), 量化工具+社区策略 (Round 27/28) |
| **数据 integrity / sync 修复** | 数据完整性 audit (Round 25), K线 GCS sync 脚本 |
| **PIT-strict 设计** | 任何新 mart 表 schema, ASOF JOIN template, snapshot vs available_date 边界 |
| **SQL 性能 / 重构** | LATERAL → conditional aggregate (Round 等), big rewrite |
| **factor spec / feature 设计** | 6-8 sub-factor 公式 + |Spearman| 预估 + horizon 失败模式 + 5 步 execution plan |
| **negative finding / 第二意见** | PIT leakage Codex 标 CRITICAL 强制 [[feedback-codex-critical-no-compromise]] |
| **review (已有)** | commit gate, 见 §10.1-10.3 |

派任务模板 (`codex:rescue --fresh --model gpt-5.5 --effort xhigh`):
1. **明确背景**: 项目当前状态 + stack + 数据 inventory
2. **明确任务**: 设计 X / 调研 Y / 修 Z, 给具体输出格式 (表格 + 数字 + grep verification 路径)
3. **明确约束**: 中文 / 无 emoji / PIT-strict / 单分支 main / license 政策
4. **明确反例**: 项目踩过的坑 (Rule 5/7 反例) — 让 Codex 不重蹈
5. **明确禁忌**: TODO 折中 / "估计影响小" / 单测跳

并行派多个 Codex (run_in_background=true) — 不同主题不冲突, 各自跑各自 thread. 完成自动通知, 不 poll.

### 10.0.1 Claude 自己也派 multi-agents + Claude/Codex 跨 agent 多轮沟通 (新加 2026-05-17)

> **用户原则 2 (2026-05-17 push back)**: "对于你自己, 也要在相关位置写上可以指派多 agents 完成任务, 你的 agent 和 codex 的 agent 之间也可以做多轮次沟通, 多维度充分发挥你们两个合作的最大能力".

**Claude 自己的 multi-agent 用法**:

| Claude agent 类型 | 适合做的事 |
|---|---|
| **Explore** | 大代码库探索, find 文件/symbol/keyword (3+ 次 grep 替代) |
| **Plan** | 复杂多步骤实施 plan, 不只是当前 task |
| **general-purpose** | 兜底 — 多步任务 + 不确定路径 |
| **claude-code-guide** | Claude Code / Anthropic SDK 自身问题 |

派 Claude agent 触发条件:
- 探索 / 调研 > 3 个 grep — Explore (read-only)
- 实施 plan > 5 步 — Plan
- 复杂 multi-step 修复 — general-purpose
- 各自工作不互相 file conflict — 可并行 background

**Claude/Codex 跨 agent 多轮沟通**:

模式 1: **Claude (调度) → Codex (深度设计) → Claude (实施 + 测试) → Codex (review)**
- 调度: Claude 拆任务 + 写规范
- 设计: Codex 出方案 (PIT spec, factor 公式, DDL, etc)
- 实施: Claude 写代码 (按 Codex spec, 不再问 Codex 细节)
- review: Codex 审 Claude 写完的代码, 找 PIT / leakage / edge case

模式 2: **多 Codex 并行 → Claude 综合 verdict → Codex resume 单点深挖**
- 例: Codex Round 27/28/29 三 round 评估不同工具源 (量化包 / 社区策略 / awesome-quant)
- Claude 综合 3 个 round 给 unified verdict (matrix), commit doc
- 跟某个具体工具 (e.g. backtester-mcp) 再开 Codex --resume 深挖 spec

模式 3: **Claude 实施时遇到 unclear → Codex 探索 → Claude 收 + 继续**
- 不阻塞 Claude session, Codex 异步出 spec 后 Claude resume
- 不一次性派 完整任务给 Codex (太大), 拆 phase 派

**不要做的事**:
- Claude agent 跟 Codex agent **冲突 file** — 并行前确认无 overlap (CLAUDE.md §11.4 并发前必查)
- Codex 中途 cancel 不归 Claude — Codex thread 卡的 cancel 是分开决策 ([[feedback-codex-thread-stuck]] 30 min idle 阈值)
- Claude 子 agent 报告进度 — 直接拿结果, 不让 sub-agent 做 review/audit (它没你的 context)

**实战已用例 (2026-05-17 session)**:
- Codex Round 25-30 + Round 31-33 (本轮派的 3 个) = 8 个 Codex 跑 background 并行
- 同时 Claude 自己 main session 跑 Wave 1 status / sizer ablation / forecast_upside rebuild
- 多 Codex 完成自动通知 Claude 处理 → commit / wire

固化路径: memory [[feedback-multi-agent-collab]] (新建).

### 10.1 Review Gate (commit-time)

**触发**: 任何代码阶段性 commit 必须先 Codex review.

**豁免**: 纯 markdown commit · 改名/路径替换 · 修错别字.

**执行**:
1. 写完代码 + 单测后, **不立刻 commit**
2. `codex:rescue` (默认 fresh thread, 长流程 `--resume`) 提交 diff + 上下文
3. Codex 输出意见 → **逐条评估 + 协作沟通找最佳方案**, 不全盘接受也不全盘忽略
4. 修代码 → 再 review (循环) 或自审 OK → commit
5. commit message 引用 Codex agent ID + 关键意见 / 关键修改 / **你拒绝/折中的项 + 理由**

**逐条评估原则** (用户原话 2026-05-14: "不要全盘接受 codex 反馈, 要结合实际提出自己的想法, 通过沟通找出最佳方案, 前提是遵循原则和我设定的目标"):

不全盘接受 ≠ 不接受. 每个 finding 走这个评估:

| 维度 | 评估问 |
|---|---|
| **原则一致** | 跟 Rule 5 (PIT) / Rule 7 (真金白银) / Rule 4 (measured) 一致吗? |
| **用户目标** | 改了能让 年化≥30% / max_dd≥-20% / 月胜率≥55% / 超额 HS300>0 更可信吗? |
| **代价 vs 收益** | 修这个让代码复杂多少? 真减 leakage / 真涨 alpha 多少 (能 measure 吗)? |
| **现状妥协** | 项目其它地方已经接受同 trade-off 吗 (e.g. industry_pit fallback)? |
| **现实数据** | 我们这里数据可不可行 (e.g. mart_institution_profile 没 as_of_date 字段)? |

**三档反应**:
1. **完全接受**: 跟原则一致, 代价低收益高, 现实可行 → 直接修. 加单测固化.
2. **折中**: 接受 spirit 但实施不同 (e.g. Codex 建议 PIT snapshot 表, 我加 _NOT_PIT 注释 + TODO + 测试 NULL case). 必须在 commit message **写明分歧 + 我选了什么 + 理由**.
3. **拒绝**: 跟用户原则冲突 / 偏离目标 / 代价过高 / 数据不支持 → push back. 必须**写明拒绝理由**, 不能"看了忽略".

**⛔ CRITICAL 红线 (用户 2026-05-15 push back)**: Codex 标 **CRITICAL** 涉及 PIT/leakage/真金白银 (违反 Rule 5/Rule 7) 时, **只能选"完全接受+立刻修+test verified"**. 不允许折中, 不允许"注释 TODO 留以后", 不允许 "sequencing 等其它前置". 反例 (2026-05-15): Codex a8c34359a 标 mart_institution_profile latest snapshot 为 CRITICAL "freeze and test it", 我选 "注释 TODO" 折中没 test, chain 实跑用了 leakage 数据 RankIC +60% 假象. 详 [[feedback-codex-critical-no-compromise]].

**单测设计模板 (PIT 类)**: mock data 必须含 "历史 signal row + 未来 updated_at profile" 时序冲突 case, 验证 training **不 leak future**. 不只 ASOF 取最新 — 是要验证**时序对齐**: latest snapshot 表给历史 signal 用时, 单测应能 detect 模型从未来数据获益.

**反例 (踩过)**: 2026-05-14 我对 Codex 7 finding 全接受没 push back 任何条 — 用户 push back: "全盘接受 ≠ 协作". 正解: 至少标出哪些是折中/我有补充判断 (e.g. C1 institution_profile 我选"接受 spirit + 注释 + 测试"而不是"建表 PIT snapshot 立刻做"; M1 fallback 我选"暴露 confidence 让下游 filter"而不是"严格 PIT 排除").

**协作沟通**: 复杂 finding (e.g. 设计取舍) Codex 一次反馈不够, 应 `codex:rescue --resume` 接着追问"为啥推荐 X 而不是 Y?" "我提议 Z 你看可行吗?" — 不是 review-modify-commit 一次性, 是 review-discuss-iterate.

**Codex 三态**:
| 状态 | 判定 | 处理 |
|---|---|---|
| 可用 | `setup` ready=True | 走 review gate (默认) |
| **单 thread 慢** | thread > 30 min `progressPreview` 不更新 | `cancel <task_id>` + `codex:rescue --fresh` 起新 thread, **不走 fallback** |
| 真不可用 | `setup` ready=false / `task` 调用本身 fail / 用户明说 | 走 9.4 self-审 fallback |

**9.4 Self-审 fallback** (Codex 真不可用时, 5 项必写入 commit message):
1. PIT: 用未来信息了?
2. OOS: walk-forward OOS 还是 in-sample fit?
3. 单测: 覆盖正常 + 边界 + 异常?
4. 真金白银: leakage/估算/假设?
5. 反例: 跟 Rule 5-7 反例对照过?

## 10.0.4 Codex 主动监控 + commit pre-flight (2026-05-18 用户 push back)

> **用户原话**: "8 个 Codex 全 idle 9-11 小时... 这不就是时间浪费了吗", "commit retry 几小时? 你想个办法以后不再发生"

### 反例 (踩过, 不能再)

| 反例 | 浪费 | 防止 |
|---|---|---|
| 8 Codex companion 9-11 小时 idle, doc 早 deliver 但状态 misreport | ~$0 (Codex token 不收), 但用户时间感知 | `scripts/codex_monitor.sh` 每 15 min auto-cancel idle > 30min |
| commit retry hook reject 几次 (PROJECT_INDEX 不同步 / commit-msg keyword 缺) | 5-10 min × N 次 | `scripts/safe_commit.sh` pre-flight 跑所有 hook |

### 强制规则

**1. 派 Codex 后**:
- 不被动等 task-notification (companion 可能 misreport)
- 主动: 每完成 main 任务 check 一次 `codex-companion status --json`
- 任何 idle > 30 min → cancel + 检查 doc/code 是否已 deliver

**2. Commit 前**:
- 用 `bash scripts/safe_commit.sh "message"` (pre-flight 跑 PROJECT_INDEX + rule_compliance + commit-msg keyword check, 任一 fail 提前 abort + 提示修法)
- **禁止** 直接 `git commit` 后被 reject 重试

**3. 长期 background**:
- `scripts/codex_monitor.sh` 每 15 min cron 跑, auto-cancel idle Codex
- launchd plist 在 `configs/launchd/com.chunkymonkey.codex-monitor.plist`
- 安装: `cp configs/launchd/... ~/Library/LaunchAgents/ && launchctl load ...`

## 10.0.3 高频 commit + push + codegraph sync (2026-05-17 用户 push back)

> **用户原话**: "注意提高本地 git 和推送 github 的频率, 防止 terminal 总挂掉和 python 无缘无故退出"

### 强制规则

| 行为 | 频率 |
|---|---|
| **commit** | 每完成一个子任务立即 (e.g. 改一个 file + 测试 → commit) |
| **push** | commit 后立即 (不攒 batch) |
| **codegraph sync** | 每次 commit 后跑 `codegraph sync` (索引代码图) |
| **WIP commit** | 即使没测完, 用 `wip:` 前缀也 commit + push (防止丢失) |

### 流程模板

```bash
# 1. 改完一个 substantial 变更
git add <files>
git commit -m "..."   # 含 self-审 + rule-compliance 关键词
git push              # 立刻 push, 不攒

# 2. Codegraph sync (代码 graph 索引)
codegraph sync        # ~1-2 秒, 防 query 用 stale index

# 3. 下一个子任务
```

### 反例 (踩过)

- 改 5 个 files + 攒一次 commit → terminal 崩 = 全丢
- Python long script 跑 1 小时 → crash → 没 commit = 全丢
- Codegraph stale → query 找不到新加 symbol = 误判 "未定义"

### Codex 协作下的 commit

派 Codex 任务**前** commit current state:
- Codex 写完后, 用户/Claude 跟代码 race condition → 先 commit baseline
- Codex stream output 长 → Claude 边读边 commit (don't wait)

## 10.0.2 GCP 资源管理 (2026-05-17 用户 push back)

> **用户原话**: "把谷歌云的使用当个重点问题固化, 不要浪费资源并给出具体解决方案"

### GCP VM 当前配置

- VM: `chunkymonkey-optuna` n2-standard-32 spot, us-central1-a
- Spot rate: $0.376/h (76% off vs on-demand $1.553/h)
- Disk: 100 GB pd-standard ($0.04/GB-月 = $4/月)
- GCS: ~25 GB (smartmoney 21.4 + market 1.5 + alpha158 1.86 + delta) ($0.020/GB-月 = $0.50/月)
- 用户预算: **$10/月 GCP credit**

### 成本对比

| 策略 | 月费 USD | 备注 |
|---|---:|---|
| 24/7 spot running | $275 | 巨亏, 不要 |
| 6h/天 (8h × 0.75) | $73 | 仍超预算 |
| 1.5h/天 (batch only) | $22 | 仍超 |
| **0.5h/天 (1 weekly batch)** | **$10** | 卡 $10 credit |
| Stop VM keep disk | $4.5 | idle 长期 |
| Delete VM, GCS only | $0.5 | 完全冷冻, 重启需 reinstall |

### 强制规则

**每次 batch 任务完 → 立即 stop VM**:

| 触发 | 工具 | 验证 |
|---|---|---|
| Codex compute / Optuna grid / panel build / kline fetch / data backfill 完 | `bash gcp/vm_stop.sh` | `gcloud compute instances describe chunkymonkey-optuna --zone us-central1-a --format='value(status)'` 应 = TERMINATED |
| 下次需要 batch | `bash gcp/vm_start.sh` | 自动等 SSH ready, 5-10 秒 |

**禁止**:
- VM 24/7 running (浪费 \$271/月)
- 跑完任务忘 stop (空跑 1 小时浪费 \$0.376)
- "等会儿用" 不 stop (Claude 自己也容易 forget)

**预算监控**:
- 每月初查 `gcloud billing accounts list` + 项目 billing 用量
- 跑任何 batch 前估 wall time + 成本 (e.g. Optuna 50 trials × 4 jobs ≈ 12-15h × \$0.376 = \$4.5-5.6)
- 超 \$8/月 (80% credit) 立即检查 / 优化

**Claude main session 自检 (每次操作 VM 前)**:
1. 真要用 VM 吗? (Codex 本地能跑就别 VM)
2. 任务时长估算? (< 30 min 单点小任务考虑本地; > 1h batch 才上 VM)
3. 跑完会 stop 吗? (写 nohup 时同时计划 stop)
4. 数据 final output 在 GCS 还是 VM disk? (GCS 永久, disk 同 VM 周期)

### Codex 跑 background 不上 VM

Codex companion 跑本地 Mac, 不占 VM. 派 Codex 任务不需要 VM start.

VM 只在以下场景需要:
- Wave 1/2/3 Optuna grid (32 cores 并行)
- akshare 数据 backfill (国内网络 block, VM 端通)
- tdxhub 大批量历史拉取
- 多策略 walk-forward 并行 build

## 11. 并发 vs 串行

### 11.1 串行硬约束

PLAN_V3 §6 phase gate (上游 FAIL 立即停) · 同文件 Edit/Write · 同 DuckDB 表写 · 同 Optuna study · 同 paper_sim run · commit/push 序列 · 同 output path · 同 cache/artifact 目录 · shared config/env · 并发 audit 期间 sync 写主库 · INDEX/CLAUDE/goal.md 序列.

### 11.2 可并发场景

- read-only audit (互相无依赖)
- 独立特征源 / 独立 ablation (不同 model_id)
- Codex review 按模块 (不同 module 不同 thread)
- doc + code 并行 (一次 commit)

### 11.3 实现

- 单消息发 N 个 `Agent` calls → 默认并行 (max 5)
- `Bash run_in_background: true` 跑独立 shell
- `Agent run_in_background: true` 长 subagent 后台

### 11.4 并发前必查

- [ ] 无文件冲突 · 无 DB 写冲突 (DuckDB 单 writer; 读 OK)
- [ ] DB 连接显式 `read_only=True` (不能用默认 get_conn)
- [ ] 唯一 output path · 无共享 cache/artifact
- [ ] 任务结果不互相依赖 · deterministic input snapshot (固定 seed + DB snapshot)
- [ ] 数据 sync 任务停止 · 失败不影响其他任务
- [ ] 完成串行汇总 (main agent 验 Acceptance + commit, 不能并发汇总)

**反模式**:
- 5 agent 同改公共 audit_*.py → main 先写公共, 再分派
- 5 agent 同跑 Optuna 同 study name → 串行 / 唯一化 study_name
- P-1 PASS 没等就启 P0a → 严守 phase gate
- 并发完不汇总直接下一步 → 必须 main 汇总

### 11.5 Multi-agent max-并发 workflow (2026-05-19 用户 push back 固化)

> 用户原话: "建一个 agent 管理机制", "不要只写文档要真实应用", "卡着红线并发", "Codex + Claude subagents 混合也可以多并发".

**Rule**: 每次需 research / audit / review 时, **一次 message 派 max 5 agents 并行混合**:
- 1-3 个 Codex (codex:codex-rescue): code review / systematic audit / design spec
- 1-2 个 Claude subagent (Explore / general-purpose / Plan): file search / CLI inspection / multi-step research
- 总数 ≤ 5 (Agent tool 并行硬上限)

**配套机制 (已 deploy)**:
- `~/.codex_monitor/codex_monitor.sh` (launchd 每 15 min auto-cancel idle > 30 min stuck Codex)
- `scripts/agents_status.sh` (主动 poll list running + idle alarm + recent finished)
- 装 launchd plist 在 FDA-safe 路径 (`~/.codex_monitor/` 而非 Documents/), 避 cron `Operation not permitted`

**并发 scope orthogonal 原则**:
- 各 agent 不同 file scope, 防 conflict
- 读-only research 无 DB write 冲突
- 主要 sync / write 必须 sequential (DuckDB 单 writer)

**Triage**: 收到 notification 不 passive 等下一个 — 主动跑 `bash scripts/agents_status.sh` 看 0 running 立即 dispatch 下一批.

**Codex thread stuck 处理** (2026-05-19 实战):
- Thread > 30 min progressPreview 不更新 = cancel + `--fresh`
- 收紧 scope 起新 thread (上次 thread 33min 卡 33min on `nl -ba ... | sed` over-explore; 新 thread 收紧 4 个 Q 短答 2min 完成)
- 见 [[feedback-codex-thread-stuck]]

## 12. 用户偏好 / 沟通

- **中文回复**. 简洁实用. **表格 > 段落**. 数字优先.
- **不报喜不报忧** — 0 STRONG_BUY / 数据滞后 / 测试 fail / Gate FAIL 先讲.
- 先讲业务结果 (年化/max_dd/超额), 技术次之.
- 接任务先 **push back** 看更简单方案, 别上来就实现.

---

## 附录 — 详细信息见 PROJECT_INDEX.md

- 用户终极目标 + 当前实测基线 (η+++++++)
- 关键表 + 列陷阱 (mart_per_stock_stage_strategy_optimal / mart_stock_formula_optuna_v2 / ...)
- Rebuild 流水线顺序
- DuckDB 使用约束 (走 services.duck_adapter.connect; 不多次 ATTACH)
- 运行环境踩雷 (port 8000 / uvicorn 长跑崩 / akshare import 崩)
- 命名/Import 陷阱 (portfolio_backtest 文件 vs 包 shadow)
- sync 路径 (POST /api/inst/update/smart)
- goal.md 滚动 ledger 规则
- /loop ScheduleWakeup 配置 (1200-1800s, 避开 300s)
- 测试基线 (1402 passed)

# CLAUDE.md — 工程规则

> 配套 `PROJECT_INDEX.md` (数据/模块/坑/用户终极目标). 本文档是规则.
> 压缩后第一件事: 重读 PROJECT_INDEX.md 防 context 失真.

## 目录

| § | 内容 |
|---:|---|
| 1 | 思维原则 — think / measured / 真金白银 / 失败先承认 |
| 2 | 接手前 First Actions |
| 3 | 编码风格 — 最少代码 / 模块化 / 复用 |
| 4 | 数据 / 策略安全 — PIT / leakage / 异常数字警报 / 数据可信度 / Root Cause |
| 5 | Optuna 治理 — walk-forward / governance / OOS 守门 |
| 6 | 文档纪律 — 三件套 / 分层 / Repository Hygiene / 任务完成 ≠ 代码写完 |
| 7 | Git + Commit — 高频 / safe_commit / hook / **codegraph+complexity 双扫** |
| 8 | Self-Check — write-time / commit-time (9 项含 codegraph+complexity) |
| 9 | GCP 资源管理 — controlled use / $15 budget (alert-only) / hygiene / checkpoint reuse |
| 10 | 并发 — 串行硬约束 / 可并发 / 实现 |
| 11 | Codex 协作 — ⏸ 当前暂停 (2026-05-21~) |
| 12 | 用户偏好 / 沟通 |
| 附 | PROJECT_INDEX.md 入口 |

---

## 1. 思维原则

### 1.1 Think Before Coding

- 没有隐藏假设, 说出来 + 列 tradeoff. 不确定就**问**.
- 最少代码解决问题. 不写 speculative feature (不为"将来可能"写). 单次代码不抽象成框架.
- 只改必须改的, 不"顺手优化". 风格匹配现有, 不引新风格.
- 资深工程师觉得太复杂的 → 简化.

### 1.2 Goal-Driven Execution

- 定义"成功长什么样" + 循环到验证通过, 不告诉 Claude "step 1 X, step 2 Y".
- 成功 = 用户能 verify 的可测试结果.

### 1.3 Measured Not Estimated

任何**参数/阈值/模型预测/策略效果**必须真实历史测过, 不能公式估.

- **禁止**: "差不多" / "估计" / "假设" / "按当前速度跑" / "线性外推". 变量名含 `_estimate / predicted_ / assumed_` 停下问: 真来自数据还是公式拍脑袋?
- **必做**: uplift/score/收益/胜率/风险 必能答 "哪些历史 row + 时间窗 + 哪个 fact 测出". 测不出 → 标 `unknown`, 不公式糊弄. yaml 默认必附 backtest 证据 (commit hash / KPI 数字).
- **Self-check** (性能数字提交前): (1) 从哪行 SQL 跑? (2) 涵盖几行/几天真实历史? (3) 换 `unknown` 决策会不一样吗? (4) 用户能复现吗? 不能干净答 = estimate not measured, 不许提交.

### 1.4 真金白银 — 实盘投入

每行代码当"上线后亏钱我能睡着吗"评估. 用户原话: **"不是数字游戏, 是真金白银投入的"**.

**拒绝近似妥协**:
- "leakage 影响估计 < 10%" → **0 leakage**. 5-10% 误差实盘是亏损线.
- "含等量 leakage 公平对比" → 实盘没 baseline.
- "回测 +312% 看上去不错" → 立刻怀疑 leakage 不兴奋. 真实 < 回测.
- "先跑试试" → 跑前想清"出数能直接决策吗", 不能别跑.

**第一性原理 push back**: 写出"含 X 但 Y 仍有效"时, 自问"按第一性原理 X 应该存在吗?". 妥协论证 90% 来自想省事. 真正干净的方案才是正解.

**目标穿透** (用户终极目标: 年化≥30% / max_dd≥-20% / 超额 HS300>0 / 月胜率≥55%):
- in-sample sharpe +2.0 → fit 还是真 alpha?
- horizon_evidence sharpe +1.10 → 含 selection bias?
- paper_sim +65% → 含 leakage? 真实 forward 期望多少?

不能穿透 = 噪音, 不能决策.

### 1.5 失败先承认

反转 OOS 不及格 → 立刻换方向, 不留恋. "不合格就是不合格" (用户原话).

---

## 2. 接手前 First Actions

任务开始 / Mac 重启 / session 接续时必走:

1. **读三件套**: `goal.md` + `SESSION_HANDOFF.md` + `analysis/workflow_checkpoint.md`.
2. **`git status --short`** 看脏 worktree. **不擅自 revert 别人 (Codex / 上一 session) 的改动**, 不确定就先问.
3. 优先 `rg` / `codegraph` / targeted test / read-only DuckDB inspection — 别盲猜.
4. 任务前 commit 当前 state, 防 race condition.

---

## 3. 编码风格

- **模块化 + 不硬编码**: 数值/阈值/路径/日期/表名走 yaml. 改参数 = 改 yaml 一行, 业务代码不动. `if stock_code == "600036"` / `hp = 30` 写死禁止.
- **复用 (动手前 grep)**: 同逻辑出现 2 次 → 抽公共; 3 次还重写 → 立即停下重构. 单次不预先抽象.
- **可 hardcode (写注释解释)**: 数学常数 (sqrt(252), pi, 100 股/手) / 边界 (0, 1, MIN_FLOAT) / 测试 fixture / SQL LIMIT.
- **不偷工**: 用户要全量真实数据, 不"快速验证小样本".
- **诚实**: 数据告诉什么就报什么, 不报喜不报忧.

---

## 4. 数据 / 策略安全 (核心红线)

### 4.1 PIT / Anti-Leakage

时刻 t 的决策**只能**用 ≤ t 信息. 违反 = 数字全是假.

| 场景 | 错例 | 正解 |
|---|---|---|
| 调参 | Optuna 全段 in-sample | walk_forward.expanding_monthly, Optuna 看早窗 |
| 排名 | 用全段 sharpe | OOS rolling 60d NAV uplift / rolling IR |
| 特征 | `bars[sig_i+1:]` 未来 K 线 | `bars[:sig_i+1]` (含当日 close 注意盘前可用性) |
| Label | 未来 N 日涨幅无 purge | purged k-fold + embargo ≥ 1× forward 期 |
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
6. **数字异常好看了吗?** 见 §4.2.

不能干净答 = leakage, 不许提交.

**防御**:
- 表加 `built_at/as_of_date` 列, JOIN 永远带 `AND xxx.built_at <= ?`
- 时序切分走 `services/optimization/walk_forward.py::split_*` (含 `assert_no_temporal_leak`)
- 业务表 forward metric (sharpe/win_rate) 必须标 OOS, 不跟 in-sample 混
- 入库前 `services/optimization/governance.py::enforce_pre_insert` 守门

### 4.2 异常高数字 = leakage 警报

用户原话: "之前一版本 100% 胜率, 收益超高, 因为 Optuna 读完整 3 年 K 线倒推买卖点".

- **Absolute 红线**: RankIC > 0.3 / sharpe > 5 / win_rate > 0.95 / 年化 > 100% / 胜率 100% → **立刻怀疑 leakage, 不是兴奋**. 真实 forward 期望永远比回测低.
- **Relative 红线** (2026-05-15 加, 跟绝对同重要): 相对 baseline 提升 ≥ +50% (e.g. v1 RankIC 0.02 → v3 0.035 是 +75%) → 也算异常, 必须 ablation 验证每 col 群 PIT 干净度.
- **paper_sim 干净参考**: v3.2 P0b 实测 RankIC 0.0108-0.0203 (跨 5/10/20 horizon) = 干净 PIT 下诚实数字; 跟历史 +312% 假象**完全相反**, 这是好事.

### 4.3 数据可信度 (用户原话)

- **tdxhub / miaoxiang**: 100% 可信. 缺失 = 自己 sync 路径 bug, 不假设上游缺.
- **akshare**: 不稳定 (限频/接口变), 缺失可能上游.
- "上市公司数据不会真缺失" — 优先 tdxhub/miaoxiang 重拉.

### 4.4 Root Cause — 严禁忍

**禁止**: `try/except: pass` · `--skip-step` · `if env: bypass` · `--end YYYY-MM-DD` 钉死规避上游 bug · 单 step endpoint 绕 budget.

**必做**:
- 找**首次**写坏 / 抛错的代码路径, 修源头 — 不只清状态.
- 症状修复 (DELETE 坏行 / DROP+REBUILD) ≠ 根因修复. 两者都做, 只症状修复 = 故障会再来.
- 找不到根因**明说**, 加防御 (启动 health check / 失败 raise / lint 防回退). 防御 ≠ 修复, 但比静默 bypass 强百倍.
- 暂时绕过必须 TODO + 关联 commit, 不能伪装"已解决". 真解决 = 根因修 + 防回退测试 + 历史污染清 + 端到端验.

### 4.5 反例汇总

- **K 线含盘中数据** → 不要 `--end 2026-05-12` 钉死, 改 sync 入口 `latest_completed_trade_date` + lint 防回退.
- **DuckDB DELETE FATAL** → DROP+REBUILD index 清状态不够, 找首次写坏路径 + health check.
- **`fact_shareholder_plan.announce_date` 47% NULL** → 不放松 audit 阈值, 查 ingest 路径 (commit 69371838: 写 7034 placeholder 行 + DELETE 历史 + 防回退).
- **`mart_per_stock_stage_strategy_optimal.sharpe`** 全期 in-sample fit + `selector ORDER BY sharpe DESC` → paper_sim "+312%" 假象. 修法: walk_forward.expanding_monthly + selector ORDER BY `COALESCE(oos_sharpe, sharpe)` + `governance.enforce_pre_insert` 拒 `walk_forward_mode='none'`.
- **v3.2 `stage_opt_per_stock`** MAX(oos_sharpe) GROUP BY stock_code 给每 signal_date 用未来 Optuna → systemic leakage (Codex acf48d35 review Q1 critical, commit 5cc47987).
- **v3 102 features RankIC 0.0353** → relative +75% 没触发 absolute 但触发 relative, chain 含 inst_path_a (latest snapshot) + sector (99.978% fallback) leakage. 详 [[feedback-codex-critical-no-compromise]].
- **`swap_uplift_estimate` 公式估算** → 实测 swap 拉低年化 33pp. 改真实 K 线 forward 反事实.
- **vol-aware stop/target/trailing hardcode "业界常用"** → 丢 Optuna search space, walk-forward 拼 OOS 入 mart.
- **ensemble 13 weights 业务直觉写 yaml** → 全丢 Optuna.
- **regime_gate `bear/sideways/bull` 拍脑袋** → 历史 regime sensitivity sweep.
- **portfolio_backtest +45.4% 当最终决策** → 不含 tx_cost/T+1, paper_sim 加成本骤降. live 必须用含成本 paper_sim.

---

## 5. Optuna 治理

**必走 `services.optimization` 中央层**, 不裸调 `study.optimize`. 全部阈值/区间/权重/表名走 `backend/config/optuna_config.yaml`, 不 hardcode.

**3 守门点**:
1. **时序切分**: `walk_forward.split_dispatch(signals)` (默认 R1=`expanding_monthly`), Optuna 看早窗 train.
2. **预校验**: `governance.enforce_pre_optimize(n_trials, has_seed=True)` (强制 50 ≤ n_trials ≤ 500 + 固定 seed).
3. **OOS 验证**: best params 在 test 集重跑 → `governance.enforce_pre_insert(record)` (`walk_forward_mode='none'` / OOS 字段缺 / sharpe>5 / win>0.95 → raise).

**业务表约定**:
- `mart_per_stock_*_optimal` 必须有 OOS 列 (`oos_sharpe/oos_win_rate/oos_avg_ret/oos_n_traded/oos_period_*/walk_forward_mode/train_n_signals/test_n_signals`).
- selector/scoring **只读 `oos_*`**, 老字段 (`sharpe/win_rate/avg_ret`) 仅描述/兼容.
- 新加寻优表必须照搬这套.

**R1 expanding_monthly 标准**:
- 每月底切, 前 `min_train_months` (默认 6) 月当 train base.
- Optuna 在最早窗选 best params, 然后用 best 在每个后续 OOS 月跑.
- 多窗 trades 聚合算 `oos_sharpe/win_rate/avg_ret` (`oos_aggregator.aggregate_oos_metrics`).
- 入库 sharpe = 多窗 OOS 真值, 不是 in-sample fit.

**审计**: 任何 reject 写 `fact_optuna_governance_log` (PK=`run_id`, `record_json` 全量 + 原因).

**Optuna 不用未来函数 — 3 道防线**:
1. **数据切分**: `walk_forward.split_expanding_monthly(signals)` 严格 train_months[0..k-1] / test_month[k] 切分.
2. **搜索空间**: Optuna 搜的是**策略行为参数** (hp/stop/target/trailing/形态阈值), 不是数据查找.
3. **入库守门**: `enforce_pre_insert` 拒 `walk_forward_mode='none'` / OOS 字段缺 / sharpe>5 / win>0.95.

---

## 6. 文档纪律

### 6.1 三件套 (current state, 实时更新)

- `goal.md` — 用户终极目标 / 滚动 ledger / criteria
- `SESSION_HANDOFF.md` — cron 5min 自动更新, session 中断恢复入口
- `analysis/workflow_checkpoint.md` — 业务 pipeline 跟踪 (pull/audit/paper_sim/KPI/gate/decision)

**实时更新触发** (任一项变化立即同步, 不堆到 session 末尾):
- delivery state (readiness % / promotable verdict / 阻断项变化)
- GCP state (VM status / active model / cost % budget)
- validation evidence (新 audit / Phase4 gate / paper_sim KPI 落地)
- next action (上一步刚完成 / 下一个 resume command)

### 6.2 文档分层 (Repository Hygiene)

- **current state** → `goal.md` / `SESSION_HANDOFF.md` / `analysis/workflow_checkpoint.md` (三件套)
- **durable design / audit reference** → `docs/`
- **dated evidence / session archive** → `analysis/` (带日期或 model_id 前缀)
- **PROJECT_INDEX 同步**: 改 service/script/yaml/Rule 必须同步改 PROJECT_INDEX. Pre-commit hook reject 不同步.

**清扫规则**:
- 删除前 `rg` 查引用. 保留 audit evidence / lineage / reproducibility / historical validation.
- 旧 handoff / 重复 status / obsolete plan **迁完有用信息就删**, 不留 anonymous clutter.
- **不为 one-off 工作建新目录**. 复用 `analysis/` / `docs/` / `data/reports/` 或 module-local test 路径.
- 临时 scratch script / debug dump / 一次性 notebook / partial export **不要进 repo**. 验证 artifact 必须 reference from current ledger 或 dated analysis doc, 否则就删.
- **substantial change 后**: `git status --short` 看 generated/report 目录. 要么 commit intentional artifacts 带清晰命名 + ledger 引用, 要么 handoff 前删掉. 不留临时 `.json` / `.log` / `.csv` 漂着.

### 6.3 任务完成 ≠ 代码写完

以**可运行结果 / 接口真实返回 / 图表表现 / 关键样例真实数据抽查 / 测试通过**为完成依据. py_compile pass 或 lint pass **不算完成**.

单分数 improve **不算 delivery** — 要 audit script + evidence artifact **同时**同意 (`audit_delivery_readiness.py` / Phase4 gate / probe_frontier).

之前 validation artifacts **不允许覆盖或删除**, 加新证据 + 刷新 summary JSON.

### 6.4 doc 自维护

用户原话: "每次修改 claude.md / memory 时直接做优化更新". 编辑 `CLAUDE.md` / `memory/*.md` 时**顺手做整理**:

- **过期**: 状态描述跟现状不符 → 改/删 (e.g. v3 plan 已被 v3.2 取代, 删旧建新).
- **冗余**: 同一概念多处重复 → 合并到一处 + `[[name]]` 链接.
- **结构**: 长 file (> 200 行 CLAUDE.md / > 100 行 memory) 该分组/层次化, 用 section header.
- **链接**: `MEMORY.md` 索引按类型/重要度分组, `[[name]]` 串联相关.
- **deprecation**: 早期方向 / 已偏离的方案不删, 加 "⚠ 状态: 已偏离 / deprecated" 头注 + 仍可复用部分指引.

---

## 7. Git + Commit

### 7.1 高频 commit + push + codegraph sync

用户原话: "提高本地 git 和推送 github 的频率, 防止 terminal 总挂掉和 python 无缘无故退出".

| 行为 | 频率 |
|---|---|
| **commit** | 每完成一个子任务立即 (e.g. 改一个 file + 测试 → commit) |
| **push** | commit 后立即, 不攒 batch |
| **codegraph sync** | 每次 commit 后跑 `codegraph sync` (索引代码图, 1-2s) |
| **WIP commit** | 没测完用 `wip:` 前缀也 commit + push, 防丢 |

**单分支 `main`**, 不开 feature/worktree. 紧急 hotfix 也在 main.

**反例**:
- 改 5 个 files + 攒一次 commit → terminal 崩 = 全丢.
- Python long script 跑 1 小时 → crash → 没 commit = 全丢.
- Codegraph stale → query 找不到新加 symbol = 误判 "未定义".

### 7.2 safe_commit pre-flight

**禁止**直接 `git commit` 后被 hook reject 重试. 用 `bash scripts/safe_commit.sh "message"`:
- pre-flight 跑 PROJECT_INDEX 同步 + rule_compliance + commit-msg keyword check
- 任一 fail 提前 abort + 提示修法
- 反例: 用户 push back "commit retry 几小时? 你想个办法以后不再发生".

### 7.3 hook enforcement

| Hook | 防什么 |
|---|---|
| pre-commit `project-index-sync` | 改 service/yaml 没改 INDEX → reject |
| pre-commit `rule-compliance` | magic 数字 / hardcoded date 无 `# evidence:` 注释 → reject |
| commit-msg `self-check` | message 缺关键词 (测试/防回退/PIT/OOS/实测) → reject |
| pre-commit `ruff` | 代码格式 (可选) |
| pre-commit `no-emoji` | emoji 进代码/docs → reject (PASS/FAIL/警告 marker 走文字, 不走 emoji VS16) |

误判 → 改对应脚本 `PATTERNS` / `EVIDENCE_KEYWORDS`. **不要 `--no-verify` 跳**.

### 7.4 Codegraph + Complexity 双扫 (每次代码改动后强制)

> **用户 2026-05-21 push back**: "这应该在每次代码改动后都跑一遍, 防止代码庞大后修改成本太大".

代码库已 14k+ nodes / 154k+ edges / 900+ files, 不做结构验证 = 改动盲飞. 每次 substantial change (非 doc-only / 非 typo) 必走:

**Step 1 — Codegraph 索引 + 入口验证**:
```bash
codegraph status .                  # 看 pending 改动数, 确认 worktree 干净
codegraph sync .                    # 索引新加 symbol / 改 signature, 1-2s
codegraph query "<symbol>"          # 改 service/script 前: 看 caller 数 + signature 影响范围
codegraph context "<task>"          # 改路径前: 列入口 + related test + 依赖边界
```

**Step 2 — Complexity 全扫**:
```bash
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey --format markdown
```

输出读法:
- HIGH 数 + 文件分布: 新增 HIGH 在你改的文件 → 立刻修, 不留下次.
- 历史 HIGH (e.g. `assets/js/app.js` 16 个) 已知遗留, 不阻 commit 但要跟.
- N+1 / nested-loop / sort-in-loop / io-in-loop 优先, 涉数据量大的路径必修.

**Step 3 — 改完再 sync**:
- 大改 (拆 god-module / 加新 service / 改 import 链) → `codegraph sync .` 再跑一次, 防下次 query 用 stale index.

**强制触发** (满足任一):
- 新增 service / script / router / router 子模块
- 改 LOC > 50 单文件 OR 改文件数 > 5
- 拆 god-module / 重命名公开 API / 改 SQL JOIN 路径
- commit 含 feat/refactor/perf 关键词
- 用户问 "影响范围怎样" / "callers 都是谁"

**豁免**:
- 纯 doc / md 改 / typo / 注释
- 单测 fixture only 改
- 单行 config flag 改
- 注释加 evidence 注释 (e.g. `# rule-compliance: ok evidence=...`)

**反例 (踩过)**: Codex 2026-05-20 拆 god-module workbench.py 成 ~30 个 workbench_*_read services 是大改, **没跑 codegraph context** → tests baseline 跑出 2 个 regression (calendar_gate + duckdb_contract allowlist) — 这两个本可通过 `codegraph query "v3_market_perception"` + `codegraph context "duckdb.connect contract"` 提前发现. 反例固化: 2026-05-21 我接手 fix 才补做 codegraph 验证.

**workflow 示例** (2026-05-21 BestChoice Phase 0 + tests baseline):
```bash
# 改动前先看入口
codegraph query "build_signal_context"            # 确认 4 脚本 self-contained
codegraph context "BestChoice POC entry"          # 入口 + related test

# 改完跑全扫
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey --format markdown | grep "^## "

# 改完同步
codegraph sync .                                  # safe_commit.sh 自动跑
```

---

## 8. Self-Check

### 8.1 Write-time (数字字面量进代码**前**)

1. 这数字 measured from where?
   - Optuna study → `# measured: optuna study <id>`
   - backtest commit → `# evidence: backtest <commit-hash>`
   - yaml fallback → `# from yaml: <section>`
   - **都没有 → 停手**, 加 yaml 或跑 Optuna.
2. **不接受**: "看着合理" "业界常用" "我估计" "先用这个试" "跑出来不好再调".
3. **yaml-back 默认**, 业务代码只读 yaml.
4. **可 hardcode (写注释)**: 数学常数 / 边界 / 测试 fixture / SQL LIMIT.

### 8.2 Commit-time (`git commit` **前**逐项)

1. **PROJECT_INDEX.md 同步了?** — 新数据表/service/script/yaml/反例都要同步. hook 是最后防线.
2. **测试加了?** — 核心逻辑单测; perf 改有 benchmark 防回退测试.
3. **commit message 含数字证据?** — "实测 4.8M 行 / 12 min / sample 验证", 不是 "fixed".
4. **反例表加了?** — 这次踩的新坑沉淀到 §4.5 / §5 反例.
5. **真金白银 self-check** (策略 commit): 含 leakage/估算/假设? 穿透 forward 期望?
6. **改了 CLAUDE.md / memory 顺手优化了吗?** — 检查过期/冗余/结构 (见 §6.4). 不要被动等用户提醒.
7. **本次 fix 产生 stale artifact 都清了吗?** (用户 2026-05-15 push back "我不问你也不想着"). commit 含 fix/bug/leakage/cleanup/drop/remove/revert 关键词 OR 改 SQL/schema/_META_FIELDS/kill process → **强制 invoke `/post-fix-audit`** 走 5 步:
   - 列举 touched files / tables / configs (git diff)
   - 每 artifact 主动想 downstream stale (旧 model_id / panel cols / paper_sim run / cache)
   - DB residue check (`cleanup_leakage_data.py` dry-run)
   - Process / cache / tmp file 清单
   - Execute cleanup + verify 0 residue
8. **三件套同步了?** — delivery/GCP/validation/next action 任一变化即更新 (§6.1).
9. **Codegraph + Complexity 跑过了?** — substantial change 必走 §7.4 双扫. 改前 `codegraph query <symbol>` / `codegraph context <task>` 看影响范围, 改完 `codegraph sync .` + complexity scan 看是否引入 HIGH hotspot. 反例: 2026-05-20 Codex 拆 god-module 没跑 → 2 个 regression 漏到下一 session.

反模式: kill 进程 + 修代码 + restart 就以为完事, 没清 DB row / 没 ALTER DROP COLUMN / 没标 downstream model stale. 反例: Codex a8c34359a 标 CRITICAL → 我修代码 → commit → 但 panel 含 inst_path_a 5 cols 物理数据没清, paper_sim 若误读会 leakage.

不能逐项 yes = 别 commit. 重做.

### 8.3 Codex 不可用时的 self-审 fallback

§11 暂停期间默认走此. 5 项必写入 commit message:
1. PIT: 用未来信息了?
2. OOS: walk-forward OOS 还是 in-sample fit?
3. 单测: 覆盖正常 + 边界 + 异常?
4. 真金白银: leakage/估算/假设?
5. 反例: 跟 §4.5 / §5 反例对照过?

---

## 9. GCP 资源管理

### 9.1 政策: Controlled Use

> 2026-05-21 用户澄清: GCP 可用于大计算 / Optuna 寻优 / 长 replay / 主项目 + BestChoice 综合寻优 等本地执行明显拖慢的任务. **不是默认禁用**.

启动 GCP 任务前**必须说明**:
- objective + 命令族
- 预计 wall time + 粗略成本/风险
- 输入数据/source snapshot
- 输出路径 (GCS / local repo) + artifact 保存方式
- monitor / stop / rollback plan

不要为**小测试 / 普通代码编辑 / 轻量审计 / 只读 DuckDB 盘点 / 本地能快速完成**的任务上云.

若 scope / 成本 / 数据移动 / 运行时长**明显变化** → 暂停 + 重新说明计划.

### 9.2 安全 latch

所有 GCP 命令 (`gcloud` / `gsutil` / `gcp/*` / SSH / GCS / billing / monitor / probe / Batch) **必须显式设** `CHUNKYMONKEY_GCP_EXPLICIT_OK=1`. `scripts/lib/gcp_guard.sh` 统一拦截.

历史 GCP artifacts 可本地读. **刷新或替换**算 GCP 工作, 必须走 controlled-use.

### 9.3 当前 VM 配置 + 预算

| 项 | 值 |
|---|---|
| VM | `chunkymonkey-optuna` n2-standard-32 spot, us-central1-a |
| Spot rate | $0.376/h (76% off vs $1.553/h on-demand) |
| Disk | 100 GB pd-standard ($0.04/GB-月 = $4/月) |
| GCS | ~25 GB (smartmoney 21.4 + market 1.5 + alpha158 1.86 + delta) ($0.020/GB-月 = $0.50/月) |
| **月预算** | **$50** (2026-05-23 用户放宽 from $15) |
| Alert 阈值 | 80% YELLOW / 100% RED — **仅日志, 不 auto-stop** (用户 2026-05-21 明确) |

cost tracker: `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/cost_tracker.sh`.

配置点 (改 budget 同步改 3 处):
- `backend/config/gcp_policy.yaml` (`budget.monthly_usd`, `enforcement.in_flight.red_running_auto_stop=false`)
- `gcp/cost_tracker.sh` (`BUDGET=15.0` 默认)
- 本节 §9.3 文档

### 9.4 Execution Hygiene

GCP 大任务**不允许** fragile one-line SSH. 必须 wrapper script / heredoc + syntax-check + 显式 lifecycle.

**每次 GCP compute job 最小 checklist**:
- 取消 stale shutdown: `sudo shutdown -c || true`
- venv + `PYTHONPATH=backend`
- 写 `current.pid` / `current.logpath` / `current.artifact` / `current.gcs_dir` 到 job report 目录, 再 background.
- 日志流到 stable file, 记 exit code.
- 小 artifact 导 JSON/CSV, **不拉/推 25GB DuckDB** 如果只要小报告.
- 上传 artifact + log 到 GCS, 再 shutdown.
- shutdown 只在 wrapper finalization 路径调度; fallback TTL 在 job 确认 running 后才加.
- 用 read-only monitor `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 TAIL_LINES=80 bash scripts/gcp_stability_status.sh` 替代 ad hoc SSH 轮询.
- SSH/IAP fail 后先 check VM status + GCS artifact 再重启 (VM 可能已停或 job 已上传结果).
- code sync 到 dirty worktree VM: 先 backup 远端 + scope 拷贝, **不 `git pull` 强覆**.

GCP run 浪费时间或没产 artifact → 记 root cause + 防回退到 `docs/gcp_controlled_execution_runbook.md` 再重试.

### 9.5 Long-Run Checkpoint Reuse

不要因为 long job 被中断就 rerun 已完成工作. Optuna / replay / 参数 sweep / train-log backfill 必须设计 reusable verified checkpoint.

**完成不能从日志行推断**. Checkpoint 可复用必证明:
- 同一 model / input snapshot / 稳定 params/config hash
- 同一 expected window/trial/entity key + date boundary
- 正 train/test row count (适用时)
- 可解析 metrics/artifact JSON
- `checkpoint_status='complete'` 或等价 terminal state
- observed COMPLETE count = expected count, 才写 aggregate/promotion row

**LambdaMART train-log replay**: `--train-log-only --resume-train-log`. 完成 window 存 `fact_model_train_log_window` (grain: `model_id + replay_id + window_key`). 重启只 skip verified matching window. `fact_model_train_log` 只在所有 expected window verify 通过后写 aggregate row.

### 9.6 不浪费

- 跑完 batch 任务 → 立即 `bash gcp/vm_stop.sh` (空跑 1h = $0.376).
- "等会儿用" 不 stop → 反例: Claude 自己也容易 forget.
- VM 24/7 = 浪费 ~$275/月. 长 idle keep disk only = $4.5/月.

---

## 10. 并发 vs 串行

### 10.1 串行硬约束

同文件 Edit/Write · 同 DuckDB 表写 · 同 Optuna study · 同 paper_sim run · commit/push 序列 · 同 output path · 同 cache/artifact 目录 · shared config/env · 并发 audit 期间 sync 写主库 · INDEX/CLAUDE/goal.md 序列 · PLAN phase gate (上游 FAIL 立即停).

### 10.2 可并发场景

- read-only audit (互相无依赖)
- 独立特征源 / 独立 ablation (不同 model_id)
- doc + code 并行 (一次 commit)
- 多源 DuckDB **只读** inspection

### 10.3 实现

- 单消息发 N 个 `Agent` calls → 默认并行 (max 5)
- `Bash run_in_background: true` 跑独立 shell
- `Agent run_in_background: true` 长 subagent 后台

### 10.4 并发前必查

- [ ] 无文件冲突 · 无 DB 写冲突 (DuckDB 单 writer; 读 OK)
- [ ] DB 连接显式 `read_only=True` (不用默认 get_conn)
- [ ] 唯一 output path · 无共享 cache/artifact
- [ ] 任务结果不互相依赖 · deterministic input snapshot (固定 seed + DB snapshot)
- [ ] 数据 sync 任务停止 · 失败不影响其他任务
- [ ] 完成串行汇总 (main agent 验 Acceptance + commit, 不能并发汇总)

**反模式**:
- 5 agent 同改公共 audit_*.py → main 先写公共, 再分派.
- 5 agent 同跑 Optuna 同 study name → 串行 / 唯一化 study_name.
- 并发完不汇总直接下一步 → 必须 main 汇总.

---

## 11. Codex 协作 — ⏸ 当前暂停 (2026-05-21~)

> **用户 2026-05-21 push back**: "派 Codex 的事儿可以暂停了, 现阶段由你全面接手, 直到我下一次让你重启对 codex 的调用再说".

**当前规则**:
- **不主动** `codex:rescue` / `codex:setup` / 多 Codex 派活.
- commit gate 临时改走 §8.3 self-审 fallback (5 项写入 commit message).
- 用户重启 Codex 后恢复下面历史规则.
- `~/.codex_monitor/codex_monitor.sh` launchd 暂可保留 (历史 Codex thread 仍 auto-cancel idle > 30min, 防 stuck).
- 历史 Codex thread 已知 ID / commit hash 仍可作为反例引用 (e.g. acf48d35 / a8c34359a).

**重启后历史规则要点** (压缩自原版):

| 场景 | 规则 |
|---|---|
| commit review gate | 写完代码不立即 commit, 先 `codex:rescue` 提交 diff → 逐条评估 (接受/折中/拒绝) → 修 → review 循环 → commit + 引用 Codex agent ID + 拒绝理由 |
| 豁免 | 纯 markdown / 改名 / 修错别字 |
| 全盘接受 ≠ 协作 | 每 finding 走 5 维评估 (原则一致 / 用户目标 / 代价收益 / 现状妥协 / 现实数据) + 3 档反应 (接受/折中/拒绝) |
| ⛔ CRITICAL 红线 | Codex 标 CRITICAL 涉及 PIT/leakage/真金白银 → 只能"完全接受+立刻修+test verified". 反例: 2026-05-15 我选"注释 TODO"折中, chain leakage RankIC +60% 假象. 详 [[feedback-codex-critical-no-compromise]] |
| 主动派任务 | 架构 doc / SUE PIT 设计 / 第三方调研 / 数据 integrity / SQL 重构 / factor spec / negative finding |
| 多 agent 并发 | 一次 message max 5 (1-3 Codex + 1-2 Claude subagent), 不同 file scope orthogonal |
| Thread stuck | > 30 min `progressPreview` 不更新 → cancel + `--fresh`, **不走 fallback**. 收紧 scope 重派 (反例: 33min 卡 `nl -ba ... | sed` over-explore) |
| Codex 跑 background 不上 VM | Codex companion 在本地 Mac, 不占 VM |

---

## 12. 用户偏好 / 沟通

- **中文回复**. 简洁实用. **表格 > 段落**. 数字优先.
- **不报喜不报忧** — 0 STRONG_BUY / 数据滞后 / 测试 fail / Gate FAIL 先讲.
- 先讲业务结果 (年化/max_dd/超额), 技术次之.
- 接任务先 **push back** 看更简单方案, 别上来就实现.

---

## 附录 — 详细信息见 PROJECT_INDEX.md

- 用户终极目标 + 当前实测基线
- 关键表 + 列陷阱 (mart_per_stock_stage_strategy_optimal / mart_stock_formula_optuna_v2 / ...)
- Rebuild 流水线顺序
- DuckDB 使用约束 (走 services.duck_adapter.connect; 不多次 ATTACH)
- 运行环境踩雷 (port 8000 / uvicorn 长跑崩 / akshare import 崩)
- 命名/Import 陷阱 (portfolio_backtest 文件 vs 包 shadow)
- sync 路径 (POST /api/inst/update/smart)
- goal.md 滚动 ledger 规则
- /loop ScheduleWakeup 配置 (1200-1800s, 避开 300s)
- 测试基线 (1402 passed)

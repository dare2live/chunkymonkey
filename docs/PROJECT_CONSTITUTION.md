# 项目宪法 — ChunkyMonkey

> 本文件是最高权威. 代码/配置/流程与本文件冲突时, 本文件优先, 代码必须改.
> 修改本文件需用户明确同意.

## 第一条: 真相源

每个判断只有一个真相源. 不允许中间派生表参与业务判断.

| 判断 | 唯一真相源 | 禁止 |
|---|---|---|
| 股票是否在交易 | K 线有数据 | 快照表/活跃列表/手工标记 |
| 日期是否交易日 | 交易日历 | 硬编码日期/weekday 推断 |
| 涨跌停幅度 | `universe_rules.yaml` | 代码 hardcode/多处定义 |
| 是否 ST | `dim_active_a_stock.stock_name` | 各处自行 LIKE |
| 交易成本 | `paper_sim_config.yaml` | hardcode 15 bps |
| 公式参数 | 各公式 `formula_*.yaml` | 代码内默认值 |

**WHY**: dim_all_ever_listed 快照误标 573 只; get_limit_up_pct 两处重复; ST 检查 10+ 处各写各的. 中间层越多, 出错概率越高.

## 第二条: 奥卡姆剃刀

能删的必须删. 删不掉要说明为什么.

- 多一张表 → 多一个 sync 失败点. 必须证明"不建这张表不行".
- 多一个函数 → 多一个被 bypass 的可能. 必须证明"现有函数改不了".
- 多一个配置文件 → 多一个不一致的来源. 同类配置合并到一个 YAML.
- 同一逻辑出现两次 → 立即合并. 不等以后.

**WHY**: 98 处引用 dim_active_a_stock, 42 处 bypass get_active_universe. 冗余就是 bug 的温床.

## 第三条: 模块 + 数据表 + 配置文件

所有功能必须按此模式实现:

```
config/*.yaml   — 规则和参数 (改参数不动代码)
services/*.py   — 逻辑模块 (读 config, 查真相源)
真相源          — 只读 (K线/交易日历)
dim_* 表        — 仅缓存/映射 (不参与业务判断)
```

- 新功能: 先写 YAML, 再写模块, 最后连真相源
- 改参数: 只改 YAML, 不改代码
- 查数据: 通过模块 API, 不直接 SQL JOIN dim 表

**WHY**: 42 处直接 JOIN dim_active_a_stock 做 universe 过滤, 改一个漏一个. 统一入口才能统一行为.

## 第四条: 分层架构

```
L0 基础设施  → 交易日历 / K线 / 配置 / 审计
L1 公式引擎  → 59 公式 + YAML 配置 + search space
L2 信号处理  → 共振评分 / 画像 / SmartMoney
L3 策略执行  → 股票池 / 回测 / 交易模型
L4 展示      → API / 前端
```

- 每层只依赖下层, 不反向依赖
- 跨层数据通过函数调用, 不通过直接查表
- 新模块必须明确属于哪一层

**WHY**: updater.py 5136 行什么都做. 没有分层 = 改哪都可能炸.

## 第五条: 验证前置

不验证不执行. 所有执行动作前必须有对应 gate.

| 动作 | 前置 gate | 不通过 |
|---|---|---|
| 写代码 | `/engineering-discipline` Step 1-3 | 停, 不写 |
| commit | 测试 + 审计工具 | reject |
| 跑回测 | `backtest_preflight` 8 项 | raise |
| 跑 GCP | `plan_validator` + `grill_stamp` + `preflight_gcp_launch` | exit |
| 数据 sync 后 | `data_audit` 7 项 | raise (strict) |
| session 结束 | `session_handoff_audit` | WARNING |

**WHY**: 29/34 无 search space 白跑 GCP $1.5; 200 只全深主板; 573 只误标. 都是没验证就执行.

## 第六条: 审计工具体系

审计不是"跑一下看看", 是基础设施. 每个 gate 有明确的:
- **输入**: 什么触发它
- **检查项**: 具体查什么
- **输出**: PASS/FAIL + 详情
- **阻断**: FAIL 时怎么拦 (raise / exit / WARNING)
- **扩展**: 怎么加新检查项
- **维护**: 谁更新, 什么时候更新

### 6.1 数据审计 (`data_audit.py`)

```
触发: 每次数据 sync 后自动跑 (daily_update.sh 每步后调)
位置: backend/services/data_audit.py
配置: 检查项硬编码 (应改为 YAML, 待做)

检查项:
  kline_completeness    — 逐股票 vs 交易日历, 缺天 = FAIL
  kline_consistency     — 重复/gap > 5 天 = FAIL
  board_coverage        — 四板块全覆盖
  date_range            — 匹配交易日历 max_date
  volume_sanity         — 无负值无全零
  smartmoney_freshness  — 关键表新鲜度 vs 交易日历
  cross_table_consistency — K线股票 vs universe + 误标退市检查

输出: data/reports/data_audit_latest.json
阻断: strict 模式 raise, warn 模式 log
扩展: 加新检查 = 加 _check_xxx 函数 + 注册到 run_post_sync_audit
维护: 新数据源接入时同步加检查项
```

### 6.2 回测前置审计 (`backtest_preflight.py`)

```
触发: 每次回测/Optuna/验证前, 在入口函数调 enforce_backtest_preflight()
位置: backend/services/backtest_preflight.py

检查项:
  universe_clean        — stock_codes 全在 active universe
  limit_pct_per_board   — 多板块区分 (不允许全用同一阈值)
  cost_model            — tx_cost_bps >= 10 (from paper_sim_config.yaml)
  data_freshness        — K线 max_date vs 交易日历
  walk_forward          — 必须显式声明模式 (不传 = FAIL)
  signal_pit_spotcheck  — 截断未来数据重跑, 信号消失 = FAIL
  code_leakage_scan     — 静态扫 bank 源码 future-index 模式

阻断: raise BacktestPreflightError
扩展: 加 _check_xxx 函数 + 注册到 run_backtest_preflight
维护: 新公式/新数据源接入时检查是否需要新检查项
```

### 6.3 计划验证 (`plan_validator.py`)

```
触发: GCP 跑批前, formula_local_optuna_batch.py main() 入口
位置: backend/services/bc_absorbed/plan_validator.py

检查项:
  search_space          — 每个公式有非空 Optuna search space
  trial_value           — N trials 不是重复跑同参数
  formula_runnable      — 每个公式能 import + 小数据跑通
  cost_efficiency       — 成本 vs 产出合理
  param_scope           — per-stock 属性不在 global search space
  sample_size_coverage  — 全量 universe (max_stocks=0) + 四板块覆盖
  output_usable         — 结果有下游消费方

阻断: raise PlanValidationError 或 exit 2
扩展: 加 _check_xxx 函数 + 注册到 validate_optuna_plan
维护: 新公式加入时自动被 search_space 检查覆盖
```

### 6.4 GCP 启动前置 (`preflight_gcp_launch.sh`)

```
触发: GCP 跑批脚本执行前, 手动跑
位置: gcp/preflight_gcp_launch.sh

检查项:
  vm_running            — VM 状态 RUNNING
  ssh_reachable         — SSH 能连通
  remote_plan_validator — VM 上 plan_validator PASS
  remote_data_integrity — VM 上 data verify OK
  grill_stamp           — grill_stamp 文件存在
  leakage_scan          — 本地 code leakage scan PASS
  budget                — GCP 月预算未超

阻断: exit 1
扩展: 加新检查 = 加 shell 函数 + 调 check()
维护: VM 配置变更时同步更新
```

### 6.5 Session Handoff (`session_handoff_audit.py`)

```
触发: session 结束时 (Stop hook) + 下次启动时 (SessionStart hook) + 手动
位置: scripts/session_handoff_audit.py

检查项:
  topic_coverage        — commits 提取的主题在 goal.md 中覆盖
  file_mention          — 新/改 Python 文件在文档中提及
  human_checklist       — 5 项人工确认 (next step/数字/失败原因/用户指令/能接着干)

阻断: WARNING (advisory, 不阻断)
扩展: 加新 keyword 模式到 keywords_map
维护: 每次发现遗漏模式时补充
```

### 6.6 工程纪律 (`/engineering-discipline`)

```
触发: 任何代码改动/架构决策/跑批前, 人工调用或自动提示
位置: ~/Documents/M/engineering-discipline/skills/engineering-discipline.md

检查项:
  Step 1 — 第一性原理 (3 问)
  Step 2 — 奥卡姆剃刀 (最简方案)
  Step 3 — 教训查验 (4 层 16 条)
  Step 4 — 计划拷问 (5 问)
  Step 5 — 代码审查 (5 问)
  Step 6 — 架构检验 (4 问)

阻断: 人工判断 (skill 不自动阻断, 靠纪律)
扩展: 踩新坑 → 加到 Step 3 教训列表
维护: 跨项目共享, ~/Documents/M/engineering-discipline/ 独立 git
```

### 6.7 审计体系设计原则

1. **每个 gate 必须有代码实现** — 不靠人记, 靠工具拦
2. **FAIL 必须阻断** — 不允许 WARNING 然后继续 (除了 handoff audit)
3. **新增功能 = 新增检查** — 加公式 → plan_validator 自动覆盖; 加数据源 → data_audit 加检查
4. **审计覆盖运行时** — 不只查前置条件 (DB 有数据), 也查运行时 (runner 实际加载了多少)
5. **审计结果可追溯** — 写 JSON 报告到 data/reports/, git 跟踪

## 第七条: 完成标准

"完成" = 以下全部满足:
1. 代码写完 + 测试通过
2. 审计工具跑过 (data_audit / preflight / plan_validator)
3. 端到端数据验证 (不只是 import OK, 是结果数字正确)
4. goal.md 已更新 (含数字/决策/下一步)
5. 用户能看到/验证结果

缺任何一条 = 没完成. 不说"完成了".

**WHY**: LHB fact 没重建说"完成了"; stage 没更新说"完成了"; handoff 漏 6 项说"完成了".

## 第八条: 教训即规则

踩过的坑自动升级为规则, 写入 `/engineering-discipline` skill Step 3.
规则一旦写入, 同类错误不允许再犯. 再犯 = 工具没拦住, 修工具.

**WHY**: 同一个错误 (不验证就执行) 在这个 session 犯了 5 次. 记住不够, 工具拦截才行.

## 第九条: 配置驱动

所有阈值/参数/规则必须在 YAML 配置文件中, 不在代码里.

| 配置文件 | 管什么 |
|---|---|
| `universe_rules.yaml` | 板块前缀 + ST + 涨跌停 + 退市天数 |
| `paper_sim_config.yaml` | 交易成本 + 持仓规则 |
| `paper_sim_formula.yaml` | 公式策略股票池参数 |
| `formula_*.yaml` | 各公式参数默认值 |
| `optuna_config.yaml` | Optuna 治理 (trials/seed/walk-forward) |
| `gcp_policy.yaml` | GCP 预算 + VM 配置 |

改参数 = 改 YAML. 改代码 = 改逻辑. 两者分离.

**WHY**: 公式参数 hardcode 导致不能调参; 交易成本 hardcode 15 bps 到处散落.

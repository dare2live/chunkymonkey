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

## 第六条: 完成标准

"完成" = 以下全部满足:
1. 代码写完 + 测试通过
2. 审计工具跑过 (data_audit / preflight / plan_validator)
3. 端到端数据验证 (不只是 import OK, 是结果数字正确)
4. goal.md 已更新 (含数字/决策/下一步)
5. 用户能看到/验证结果

缺任何一条 = 没完成. 不说"完成了".

**WHY**: LHB fact 没重建说"完成了"; stage 没更新说"完成了"; handoff 漏 6 项说"完成了".

## 第七条: 教训即规则

踩过的坑自动升级为规则, 写入 `/engineering-discipline` skill Step 3.
规则一旦写入, 同类错误不允许再犯. 再犯 = 工具没拦住, 修工具.

**WHY**: 同一个错误 (不验证就执行) 在这个 session 犯了 5 次. 记住不够, 工具拦截才行.

## 第八条: 配置驱动

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

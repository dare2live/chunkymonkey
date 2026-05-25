# BestChoice 全公式审计报告 — 2026-05-25

## 审计范围

bc_absorbed/ (18 文件) + bestchoice/ (17 文件) = 35 文件

## 审计结果

### 1. 涨停阈值按板块 — [WARN] 部分通过

| 模块 | 状态 | 详情 |
|---|---|---|
| bestchoice/execution_model.py | [PASS] | 已有 limit_pct_for_code: 30→20%, 688→20%, 其余→10% |
| bc_absorbed/ | [FAIL] | 无 limit_pct_for_code, 未从 universe.py import |
| 主项目 services/universe.py | [PASS] | get_limit_up_pct 已实现 (本次新增) |

**修法**: bc_absorbed 统一用 services/universe.py::get_limit_up_pct, 删除 bestchoice/execution_model.py 里的重复实现.

### 2. Universe 排除 — [FAIL] 均未接入

| 模块 | 状态 |
|---|---|
| bc_absorbed/ 全部脚本 | [FAIL] 无 get_active_universe |
| bestchoice/ 全部脚本 | [FAIL] 无 |
| formula_parameter_search.py | [FAIL] Optuna 跑在全量未过滤 universe |
| macd_optuna_backtest.py | [FAIL] 同上 |

**修法**: 所有 bc_absorbed 回测脚本入口加 load_clean_backtest_data() 或手动 enforce_backtest_preflight().

### 3. backtest_preflight 接入 — [FAIL] 均未接入

0/35 文件接入了 enforce_backtest_preflight.

### 4. 硬编码涨停 0.097 — [PASS] 未发现

bc_absorbed 和 bestchoice 没有硬编码 0.097. 但也没有动态取值.

### 5. 成本模型 — [FAIL] 不完整

| 模块 | 状态 |
|---|---|
| bestchoice/execution_model.py | 有 build_fixed_holding_trades 但成本模型在调用方 |
| bc_absorbed/compute.py | 回测用的成本参数分散在各处 |

## 修复优先级

| P | 修复项 | 影响面 |
|---|---|---|
| P0 | bc_absorbed Optuna 脚本加 universe 排除 | 所有 BestChoice 公式回测结果可能被 12% 污染 |
| P0 | bc_absorbed 涨停阈值用 get_limit_up_pct | 创业板/科创板公式行为错误 |
| P1 | 全部回测脚本接入 preflight gate | 防止未来新脚本遗漏 |
| P2 | 统一 limit_pct_for_code 到 universe.py | 消除重复实现 |
| P3 | 成本模型标准化 | 收益率可比性 |

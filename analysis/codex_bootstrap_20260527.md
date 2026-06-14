# ChunkyMonkey Codex Bootstrap — 2026-05-27

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


## A. 项目身份 (Project Identity)

| 项目 | 内容 |
|---|---|
| 定位 | **ChunkyMonkey**：A 股量化选股与回测系统。目标是从数据采集、公式信号、策略执行到 API 展示形成闭环。 |
| 用户终极目标 | 年化收益率 >= **30%**；max_dd drawdown >= **-20%**；超额 HS300 > **0**；月胜率 >= **55%** |
| 技术栈 | **Python + DuckDB + FastAPI + Optuna + GCP**（另有 bash 脚本链路与前端） |

## B. 当前架构 (Architecture)

### B1. L0-L4 分层（含模块示例）

| 层 | 职责 | 示例模块 |
|---|---|---|
| L0 基础设施 | 真相源接入、日历、审计、配置 | `services/calendar.py`、`services/kline_client.py`、`services/*.py`、`backend/services/backtest_preflight.py` |
| L1 公式引擎 | 59 公式与参数/搜索空间管理 | `backend/services/bc_absorbed/derived_formulas.py`、`backend/services/formula_bank.py`、`backend/config/formula_*.yaml` |
| L2 信号处理 | 特征/画像/共振评分/聚合 | `services/stock_profiler.py`、`backend/services/signal_ranker.py`、`backend/services/smartmoney_adapter.py` |
| L3 策略执行 | 组合池/回测/交易模型 | `backend/services/portfolio_pool.py`、`backend/services/daily_formula_picks.py`、`backend/services/paper_sim/selector.py` |
| L4 展示 | API 与展示 | `backend/routers/*.py`、`v3/*` 页面 |

### B2. 真相源与降级规则

| 判断 | 唯一真相源 | 现状 |
|---|---|---|
| 交易在否 | K 线是否有数据 | `price_kline` 系列为交易真相源 |
| 日期真值 | 交易日历 | `services/calendar.py` 为唯一交易日判断 |
| 涨跌停真值 | `universe_rules.yaml`（按板块） | 不再在代码硬编码 |
| 是否 ST/退市 | `dim_active_a_stock.stock_name`（仅映射/展示）+ PIT 方法 | 不再做 universe 过滤主逻辑 |
| 成本 | `paper_sim_config.yaml` | 含佣金/印花税/规费/滑点统一口径 |
| 参数/公式 | 各 `formula_*.yaml` | 代码不持久化业务参数 |

### B3. `dim_active_a_stock` 定位

| 表 | 实际用途 |
|---|---|
| `dim_active_a_stock` | **仅 cache**：code→name 与展示映射，不作为 universe 过滤主来源 |

### B4. 配置驱动模式

| 层级 | 文件/模块 | 说明 |
|---|---|---|
| Rule/参数 | `config/*.yaml` | 政策、阈值、交易成本、公式默认值 |
| 业务入口 | `services/*.py` | 读取 config 并产出统一服务能力 |
| 真相消费 | 其他 service/router | 统一通过 service API，不直接 SQL JOIN `dim_active_a_stock` |

### B5. 硬编码治理

| 类型 | 默认 owner | 强制口径 |
|---|---|---|
| 规则 / 阈值 / 参数 / 开关 | YAML/config + loader 校验 | 不散落在业务 Python；确需留代码时写明为什么 config/table 更差 |
| 数据源优先级 / source catalog / 迁移建议 | 数据表或稳定配置 | 不能靠脚本内列表长期维护 |
| 观测事实 / lineage / gate evidence / runtime status | 数据表或稳定 artifact | 代码只负责读写和校验，不伪造事实 |
| fallback 顺序 / typed access / validation | service module | router/updater 不复制业务策略 |
| 测试夹具 / 数学常量 / schema enum / SQL DDL | Python 可接受 | 不得升级为生产策略或跨处复制 |

新增或修改业务值前必须先回答: 它的唯一真相源是谁、谁维护、谁校验、如何回滚。

## C. 审计工具与 Gate（命令/触发/检查项/FAIL 含义）

| 工具 | 触发条件 | exact command | 检查项 | FAIL 含义 |
|---|---|---|---|---|
| 1) `check_universe_filter.py` | 改 universe 过滤相关代码；提交前/CI 前 | `PYTHONPATH=backend python backend/scripts/check_universe_filter.py --staged` 或 `--all` | 扫描 .py 中 `dim_active_a_stock` direct use，不允许无 `get_active_universe()` 或同一行 `rule-compliance: ok evidence=...` | 非 0：返回违规列表（应在同一行补 evidence 或改用 `services.universe.get_active_universe`）|
| 2) `data_audit.py` | 每次数据 sync 后，或在 daily_update step3 后手工触发；release 前核验 | `PYTHONPATH=backend python backend/services/data_audit.py --step <step_name> --strict` | 7 类审计：`kline_completeness`、`kline_consistency`、`board_coverage`、`date_range`、`volume_sanity`、`smartmoney_freshness`、`cross_table_consistency` | `strict` 模式失败直接抛异常；需先修复再继续 |
| 3) `backtest_preflight.py` | 每次回测/验证/Optuna 入口前（包括本地和脚本） | `PYTHONPATH=backend python -c "from services.backtest_preflight import enforce_backtest_preflight; enforce_backtest_preflight(stock_codes=[...], conn=conn, walk_forward_mode='expanding_monthly', signal_context={...})"` | 7+ 检查：universe_clean、board 规则差异、成本模型、K 线新鲜度、walk-forward 模式、PIT spot-check、code leakage scan |
| 4) `plan_validator.py` | Optuna 批量前置；GCP 跑批前 | `PYTHONPATH=backend python -c "from services.bc_absorbed.plan_validator import enforce_optuna_plan; enforce_optuna_plan(['gs_raw_buy'], trials=100, output_path='results/gs_raw_buy.csv')"`（或 `formula_local_optuna_batch.py` 的执行前逻辑） | search space、trial 有效性、formula 可跑、成本可行性、参数作用域、样本覆盖、结果可消费 |
| 5) `preflight_gcp_launch.sh` | GCP 启动前 | `CHUNKYMONKEY_GCP_EXPLICIT_OK=1 bash gcp/preflight_gcp_launch.sh` | VM/SSH、远端 plan-validator、远端数据完整性、grill stamp、leakage scan、本地 budget |
| 6) `session_handoff_audit.py` | session 结束/新 session 启动时 | `PYTHONPATH=backend python scripts/session_handoff_audit.py --since 2026-05-27`（或自动时间） | topics/数字/新文件是否落入 goal/session 文档 | 输出 INCOMPLETE -> session 收尾不达标（警示） |
| 7) `check_rule_compliance.py` | 提交前 pre-commit（staged diff） | `PYTHONPATH=backend python backend/scripts/check_rule_compliance.py` | magic number（alpha/sigma/multiplier/threshold）、`try/except: pass`、hardcoded date、stock code | 非 0：阻断提交（可用同一行或前一行 `rule-compliance` evidence） |
| 8) `check_project_index_sync.py` | 提交前 pre-commit | `PYTHONPATH=backend python backend/scripts/check_project_index_sync.py` | 是否同步 `PROJECT_INDEX.md` 与服务/脚本/yaml/CLAUDE 变更 |
| 9) `safe_commit.sh` | 每次 commit（取代裸 commit） | `bash scripts/safe_commit.sh "<commit msg>"` | 步骤：status、project index、rule compliance、commit-msg keyword、泄漏审计（条件触发）、commit/push、codegraph sync |
| 10) codegraph sync/query/context | 修改前后影响分析与变更后校验（要求每次代码改动） | `codegraph status .`、`codegraph sync .`、`codegraph query "<symbol>"`、`codegraph context "<task>"` | 结构引用闭环、影响范围、调用链、反向依赖异常 |
| 11) complexity 脚本 | 每次实质代码改动后（LOC>50 / 文件数>5 / 新增 service / 拆模块） | `python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py /Users/dp/Documents/M/stock/chunkymonkey --format markdown` | HIGH hotspot/循环/排序内循环/io in loop/N+1 |

### C0. 硬编码治理门禁

| 检查 | PASS | BLOCK / REVISE |
|---|---|---|
| owner | 业务规则、阈值、目录、优先级已归属 config/table/service | Python 内新增长期业务列表或 magic threshold |
| 单一真相源 | 同一规则只在一处定义，其他地方通过 loader/service 读取 | YAML、SQL、Python 多处重复表达 |
| 奥卡姆 | 确认不是为了一个局部常量新建配置/表 | 配置膨胀成隐藏 DSL 或无人维护的平行账本 |
| evidence | 规则变更有 focused test / audit / schema 校验 | 只靠口头说明或 warn-only |

### C1. 跑批验证前的三道 Gate 详解

跑 Optuna / 回测 / GCP 批量前，三道 gate 必须依次通过。以下是**每项检查的具体逻辑**:

#### Gate 1: backtest_preflight (8 项, 回测/验证/Optuna 入口)

| # | 检查项 | 具体逻辑 | FAIL 含义 |
|---|--------|----------|-----------|
| 1 | `universe_clean` | stock_codes 全在 `get_active_universe()` 返回集内 (K 线 90 天有交易 + 前缀 60/00/30/68) | 含退市/三板/北交所股票 |
| 2 | `limit_pct_per_board` | 至少覆盖 2 种涨跌停幅度 (10% 沪深主板 + 20% 创业板/科创板), 不允许全用同一阈值 | 板块偏差 — 只有一种板块的股票参与 |
| 3 | `cost_model` | `tx_cost_bps >= 10` (从 `paper_sim_config.yaml` 读) | 交易成本低估 → 回测虚高 |
| 4 | `data_freshness` | K 线 max_date vs 交易日历最新交易日, 滞后 ≤ 5 天 | K 线数据陈旧 |
| 5 | `walk_forward` | 必须显式声明模式 (expanding_monthly / rolling_quarterly / 等), 不传 = FAIL | 没有 walk-forward = in-sample fit |
| 6 | `signal_pit_spotcheck` | 随机取 1 只股票, 截断未来数据重跑公式, 信号必须 survive | PIT 泄漏 — 信号依赖未来数据 |
| 7 | `code_leakage_scan` | 静态扫 bank 公式源码, 检测 `[i+1]` / `[idx+1]` / `shift(-1)` 等 future-index 模式 | 公式代码含未来函数 |
| 8 | `excluded_stocks` | stock_codes 不含 `excluded_stocks` 表中的股票 | 含手动排除股票 (ST/异常/流动性不足) |

```bash
# 调用方式 (脚本入口自动调, 也可手动):
PYTHONPATH=backend python -c "
from services.backtest_preflight import enforce_backtest_preflight
enforce_backtest_preflight(
    stock_codes=['000001','300750','688001'],
    conn=conn,
    walk_forward_mode='expanding_monthly',
    tx_cost_bps=10.4
)
"
# 任一项 FAIL → raise BacktestPreflightError, 阻断执行
```

#### Gate 2: plan_validator (8 项, Optuna 跑批前)

| # | 检查项 | 具体逻辑 | FAIL 含义 |
|---|--------|----------|-----------|
| 1 | `search_space` | 每个公式在 `_suggest_params()` 有非空搜索空间 | 无参数可搜 = 白跑 |
| 2 | `trial_value` | N trials 不重复跑同参数 (seed 固定 + 参数差异度检查) | Optuna 没在真正搜索 |
| 3 | `formula_runnable` | 每公式能 import + 小数据跑通不报错 | 公式代码有 bug |
| 4 | `cost_efficiency` | trials × 公式数 × 估计单 trial 时间 < 预算容忍度 | 花费不合理 |
| 5 | `param_scope` | per-stock 属性 (如 limit_up_pct) 不在 global search space | 作用域错配 |
| 6 | `sample_size_coverage` | 全量 universe (max_stocks=0) + 四板块都有股票 | 板块偏差 |
| 7 | `board_coverage` | 60/00/30/68 四前缀都有 ≥ 1 只股票 | 缺某板块 |
| 8 | `output_usable` | 结果有下游消费方 (Layer 2 ranker 或 paper_sim) | 跑完没人用 |

```bash
# 调用方式:
PYTHONPATH=backend python -c "
from services.bc_absorbed.plan_validator import enforce_optuna_plan
enforce_optuna_plan(
    formula_ids=['gs_raw_buy','ma_base_breakout','pullback_doji'],
    trials=100,
    output_path='results/'
)
"
# FAIL → raise PlanValidationError 或 exit 2
```

#### Gate 3: data_audit (7 项, 数据 sync 后)

| # | 检查项 | 具体逻辑 | FAIL 含义 |
|---|--------|----------|-----------|
| 1 | `kline_completeness` | 逐股票 vs 交易日历, 缺天 > 阈值 = FAIL | K 线数据缺失 |
| 2 | `kline_consistency` | 无重复日期 + gap ≤ 5 天 | K 线数据不一致 |
| 3 | `board_coverage` | 四板块 (60/00/30/68) 全覆盖, 每板块 ≥ 阈值只 | 某板块数据缺失 |
| 4 | `date_range` | K 线 max_date 匹配交易日历 max_date | 数据滞后 |
| 5 | `volume_sanity` | 无负成交量 + 无全零行 | 数据异常 |
| 6 | `smartmoney_freshness` | 关键表 (LHB/机构/两融) 新鲜度 vs 交易日历 | SmartMoney 数据陈旧 |
| 7 | `cross_table_consistency` | K 线股票 vs universe + **误标退市检查** (573 只教训) | 跨表不一致 |

```bash
# 配置: backend/config/data_audit_rules.yaml (改参数不改代码)
# 调用:
PYTHONPATH=backend python -c "
from services.data_audit import run_post_sync_audit
result = run_post_sync_audit('manual_check', strict=True)
print(result)
"
```

#### 涨跌幅和排除表的具体验证链路

```
universe_rules.yaml          backtest_preflight._check_limit_pct_coverage()
  limit_up_pct:              → 验证 stock_codes 含 ≥ 2 种 limit_up_pct
    "60": 0.10               → 沪主板 10%
    "00": 0.10               → 深主板 10%
    "30": 0.20               → 创业板 20%
    "68": 0.20               → 科创板 20%

excluded_stocks 表           backtest_preflight._check_universe()
  stock_code + category       → stock_codes 不含排除表中的股票
  + reason                   → 排除原因: ST/异常/流动性不足/手动

dim_active_a_stock           check_universe_filter.py
  stock_code + stock_name     → 只能做 code→name 映射
                              → 不能做 universe 过滤 (lint 拦截)
```

### C2. Codegraph + Complexity 强制工作流（每次代码改动，不批量）

> 用户明确要求（2026-05-27）: "修改过程中要持续使用codegraph和complexity审计，还有各种工具实时监督审计"。**不得批量延后。**

**改动前**（动手写代码前）:
```bash
codegraph query "<要改的 symbol>"      # 看调用链 + caller 数
codegraph context "<task 描述>"        # 看入口 + 相关测试 + 依赖边界
```

**改动后**（每次 Edit/Write .py 后立即）:
```bash
codegraph sync .                       # 更新索引，1-2s
python /Users/dp/.agents/skills/complexity-optimizer/scripts/analyze_complexity.py \
  /Users/dp/Documents/M/stock/chunkymonkey/backend --format markdown \
  | grep "^## HIGH" | wc -l            # 要求: 0 新增 HIGH (已知遗留除外)
```

**然后才能进入**: Codex review (codex:codex-rescue) → safe_commit.sh

**完整序列**: `codegraph query` → 写代码 → `codegraph sync` → complexity scan → verify 0 新 HIGH → Codex review → commit

**豁免**: 纯 markdown / typo / 注释 / 单行 config flag
**强制触发** (满足任一): LOC > 50 单文件 / 改文件数 > 5 / 新增 service 或 router / 拆 god-module / 改公开 API / feat/refactor/perf commit

### C3. 日常操作手册 (新 agent 最容易卡住的 7 件事)

#### 1. 两个 DuckDB、连接方式、单 writer 陷阱

```
data/smartmoney.duckdb  — 业务库 (机构/公式/策略/审计, ~21 GB)
data/market.duckdb      — 行情库 (K 线/复权, ~1.5 GB)
```

**连接方式** (必须走 duck_adapter, 不裸连):
```python
from services.duck_adapter import connect
from services.db import get_conn          # → smartmoney.duckdb (读写)
from services.market_db import get_market_conn  # → market.duckdb (读写)
```

**单 writer 陷阱**: DuckDB 同一时间只允许 1 个写连接。并发读 OK (`read_only=True`)。两个进程同时写 = 第二个会 hang 或报错。并发审计/查询必须显式 `read_only=True`。

#### 2. Backend 怎么启动

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey
source .venv/bin/activate
PYTHONPATH=backend uvicorn routers.main:app --host 0.0.0.0 --port 8000
# 端口 8000 陷阱: 如果已有进程占用, 会静默失败
# 检查: lsof -i :8000
```

#### 3. 测试怎么跑

```bash
PYTHONPATH=backend pytest backend/tests/ -x -q   # 快速 (排除 realdb/perf/network)
PYTHONPATH=backend pytest backend/tests/ -x -q -m realdb  # 真实 DB 测试
# 基线: 1402 passed (不回退 = 验收条件之一)
```

#### 4. paper_sim 怎么跑

```bash
# BC 公式 paper_sim:
PYTHONPATH=backend python backend/scripts/run_paper_sim.py --config backend/config/paper_sim_formula.yaml

# ML model paper_sim (LambdaMART v6):
PYTHONPATH=backend python backend/scripts/run_paper_sim_lambdamart_v6_compare.py --lambdamart-model-id <model_id>
```

#### 5. 数据 sync (daily_update)

```bash
# 全量 sync (手动, 需网络):
PYTHONPATH=backend bash scripts/daily_update.sh

# 单步:
PYTHONPATH=backend bash scripts/daily_update.sh --step sync_kline
```

cron 已配置但**实际执行取决于 Mac 是否开机 + FDA 权限**。`crontab -l` 查看。

#### 6. akshare 不稳定

akshare 客户端 (K 线/财报/龙虎榜) 有限频 + 接口变动风险。`import akshare` 本身可能崩 (monkey-patch side effect)。如果 akshare 报错:
- 优先用 tdxhub (本地通达信, 100% 可靠)
- akshare 缺失假设上游问题, 不假设本地 bug

#### 7. session 恢复 (Mac 重启 / terminal 崩后)

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey
bash scripts/cm_resume.sh          # 1 命令出当前 state
claude                              # SessionStart hook 自动注入 SESSION_HANDOFF.md
```

## D. Skill 调用（exact command + 触发）

| Skill | 触发时机 | exact command |
|---|---|---|
| `/grill-with-docs` | 需执行计划前（GCP、Optuna、架构改造、新模块） | `/grill-with-docs` |
| `/grill-me` | 非代码架构/策略方向决策 | `/grill-me` |
| `/engineering-discipline` | 代码改动/架构决策前置门 | `/engineering-discipline` |
| `/diagnose` | 硬 bug、异常结果、性能回退 | `/diagnose` |
| `/tdd` | 新公式或关键路径开发 | `/tdd` |
| `/to-issues` | 大任务切 issue（可并发执行前） | `/to-issues` |
| `/handoff` | 交接/会话结束 | `/handoff` |
| `codex:codex-rescue`（Rule 10 Mandatory for .py） | 每次 `.py` 改动后，尤其提交前 | `Agent(subagent_type='codex:codex-rescue', ...)`（等价执行口径：`codex:codex-rescue`） |

## E. Hooks（文件/作用/是否阻断）

| 文件 | Hook 时机 | 作用 |
|---|---|---|
| `~/.claude/hooks/session_start_handoff.sh` | SessionStart | 读取 `SESSION_HANDOFF.md` 注入上下文，必要时异步刷新快照；**不阻断** |
| `~/.claude/hooks/check_pending_work.sh` | UserPromptSubmit | 提醒未完成工作/未提交改动；**不阻断** |
| `~/.claude/hooks/codegraph_pre_edit.sh` | PreToolUse | Edit/Write .py 前记录 baseline lines/nodes；**不阻断** |
| `~/.claude/hooks/codex_consult_check.sh` | PreToolUse | 提醒缺少近期 `codex` 约束（业务 .py 改动）；**不阻断** |
| `~/.claude/hooks/codegraph_complexity_check.sh` | PostToolUse | .py 修改后异步运行 codegraph sync 与复杂度扫描；复杂度告警输出 systemMessage；**不阻断** |
| `~/.claude/hooks/py_compile_check.sh` | PostToolUse | 编辑 .py 后执行 `py_compile`；失败只上报；**不阻断** |
| `~/.claude/hooks/rule10_reminder.sh` | PostToolUse | 提醒 Rule 10，强制顺序提醒；**不阻断** |
| `~/.claude/hooks/session_rule_audit.sh` | Stop | 检查行为类违规（multi-agent/连续模式等），警告；**不阻断** |
| `~/.claude/hooks/plan_grill_gate.sh` | Stop | 检查是否有执行动作但无 grill 迹象，warning；**不阻断** |
| `~/.claude/hooks/session_handoff_check.sh` | Stop + 手动 | 检查 handoff 完整性；warning；**不阻断** |

> 实际阻断规则中最关键的 `Rule 10` 已进入 `scripts/safe_commit.sh` Step 4.5；`.py` staged commit message 缺 `Codex-Reviewed:` 或 `codex-review: skipped reason=...` 时应阻断。

## F. 红线（Red Lines）

| # | 红线 |
|---|---|
| 1 | 架构优先于业务：先搭结构再写策略代码，不允许先上业务 |
| 2 | Codex review before commit（Rule 10），`.py` 变更必须有 `codex:codex-rescue` 或明确 bypass 说明 |
| 3 | 每次代码改动后 `codegraph + complexity` 复扫，不能跳过 |
| 4 | 每个决策应用**第一性原理 + 奥卡姆剃刀** |
| 5 | 只允许已测数据（measured），不允许估算替代 |
| 6 | 严控真金白银：实盘风险优先于“漂亮结果” |
| 7 | 与用户沟通及输出优先中文，表格重于段落 |
| 8 | PIT 与防泄露为零容忍（未来信息、时间错位一律不可接受） |

## G. 当前工作状态（从 handoff 文件提取）

### G1. DONE

| 项目 | 状态 |
|---|---|
| `dim_active_a_stock` 治理主线 | 已做批次 1：`get_active_universe` 改造（17 files）+ `rule-compliance` 标注（12 files）+ 5 处同一行标注修正 |
| 审计/治理策略 | `check_universe_filter --all` 默认 production-code only 已 CLEAN；`--include-tests` 保留 41 个 fixture 引用审计 |
| 计划文档同步 | 宪法/架构层已落地；`goal.md`/implementation/handoff 已记录 updater infra/calendar/steps/connectivity/sync/calc/runtime/audit/status/reset/institution/trends/profiles/market_data/lifeboat/plan/execution/launcher/completeness、DAG 查询 helper、K 线连通性预检 helper、stale-running step_status 清理 helper、step_status catalog 同步 helper、source failure queue 状态 helper、update status payload/response helper、smart-update 计划/交易日历 preflight helper、smart/full/single/group background launcher 依赖包、launcher callback bundle、audit snapshot refresh helper、audit route payload helper、group pipeline 执行循环 helper、full DAG 执行循环 helper、single-step chain 执行循环 helper、smart plan 执行循环 helper、reset route payload/connection lifecycle 与 sync_industry body 与 update status response 与 smart-plan response 与 step_status priming connection lifecycle、smart-update plan connection lifecycle、run context/noop/finish/heartbeat helper、group route request scheduling helper 五十八刀 |
| 多项复盘门禁 | Codex 回顾通过（`APPROVE_WITH_NOTES`）|
| 系统 hygiene | Git/代码结构同步、session handoff 自动化仍在跑 |
| `updater.py` 拆分 | 五十八刀完成：`updater_execution.py` 823 LOC，承接 group pipeline、full DAG、single-step chain、smart plan 执行循环 helper；`updater_launcher.py` 278 LOC，承接 `UpdaterExecutionDeps`、background task failure/cleanup launcher helper、smart/full/single/group background launcher helper 与 group route request scheduling helper；`updater_status.py` 593 LOC，承接 smart-update 计划/交易日历 preflight helper、`/update/smart` plan connection lifecycle、update status/status-plan response connection lifecycle 与 run context/noop/finish/heartbeat helper；`updater_reset.py` 161 LOC，承接 reset table 清理与 reset route payload/connection lifecycle；`updater_institution.py` 533 LOC，承接 match_inst/exclusion、sync_industry body 与 industry_stat sync body；`updater.py` 5136 -> 723 LOC；既有 `updater_infra.py` 258 LOC、`updater_calendar.py` 157 LOC、`updater_steps.py` 232 LOC、`updater_connectivity.py` 156 LOC、`updater_sync.py` 443 LOC、`updater_calc.py` 196 LOC、`updater_runtime.py` 34 LOC、`updater_trends.py` 303 LOC、`updater_profiles.py` 455 LOC、`updater_market_data.py` 765 LOC、`updater_lifeboat.py` 88 LOC、`updater_plan.py` 130 LOC、`updater_audit.py` 53 LOC、`updater_completeness.py` 108 LOC；第三十八刀迁出 `/update/status` payload helper 到 `build_update_status_payload`，第三十九刀迁出 audit snapshot refresh helper 到 `updater_audit.py`，第四十刀迁出 `/update/audit` payload helper 到 `build_update_audit_payload`，第四十一刀迁出 group pipeline 执行循环到 `run_group_steps`，第四十二刀迁出 full DAG 执行循环到 `run_all_steps`，第四十三刀迁出 single-step chain 执行循环到 `run_single_steps`，第四十四刀迁出 smart plan 执行循环到 `run_smart_steps`，第四十五刀迁出 full/smart/single/group 后台任务失败落账与 cleanup 到 `run_background_update_task`，第四十六刀迁出 smart-update 计划/交易日历 preflight 组装到 `prepare_smart_update_plan`，第四十七刀迁出 smart background launcher 参数注入到 `UpdaterExecutionDeps` + `run_smart_update_background`，第四十八刀新增 `updater_launcher.py` 并迁出 launcher 层测试到 `backend/tests/test_updater_launcher.py`，第四十九刀将 full/group/single launcher 参数注入迁入 `run_full_update_background` / `run_group_update_background` / `run_single_update_background`，第五十刀将 reset-derived/reset-industry response payload/connection lifecycle 迁入 `updater_reset.py`，第五十一刀将 `sync_industry` body/gap queue/progress JSON 迁入 `updater_institution.py::_step_sync_industry_with_hooks`，第五十二刀将 `/update/status` 连接生命周期与 step_status catalog sync 迁入 `updater_status.py::build_update_status_response`；第五十三刀将 `/update/smart-plan` 连接生命周期与 plan budget response 迁入 `updater_status.py::build_smart_plan_response`；第五十四刀将 `/update/reset-derived` 与 `/update/reset-industry-derived` 连接生命周期迁入 `updater_reset.py::build_reset_derived_response` / `build_reset_industry_response`；第五十五刀将启动前 step_status priming 连接生命周期迁入 `updater_steps.py::prime_run_step_status_for_steps`；第五十六刀将 `/update/smart` 计划构建连接生命周期迁入 `updater_status.py::build_smart_update_plan`；第五十七刀将 run context/noop/finish/heartbeat helper 迁入 `updater_status.py`；第五十八刀将 group route request scheduling 迁入 `updater_launcher.py::launch_group_update_request` |

### G2. IN PROGRESS（剩余范围）

| 序号 | 范围 | 内容 |
|---|---|---|
| 1 | data-sync/schema/meta/name-lookup | 35 个 non-test `dim_active_a_stock` 直接引用已加同一行 evidence 或改模块 API |
| 2 | tests/fixtures | 41 个 test fixture 引用默认不阻断；需要时用 `--include-tests` 人工审计 |
| 3 | `scripts/safe_commit.sh` | Rule 10 Step 4.5 已实现并通过语法/场景校验 |
| 4 | `updater.py` 剩余拆分 | infra/helper + calendar/date-truth + DAG metadata/query helper + execution orchestration helper + launcher callback bundle + full/group/smart/single 状态账本 + group pipeline 执行循环 helper + full DAG 执行循环 helper + single-step chain 执行循环 helper + smart plan 执行循环 helper + smart-update 计划/交易日历 preflight helper + smart/full/single/group background launcher helper + group route request scheduling helper + background task failure/cleanup launcher helper + run-start helper + step-status priming helper/connection lifecycle + stale-running step_status 清理 helper + step_status catalog 同步 helper + step-result apply helper + stop/hard-dependency helper + running/stopped/failed transition helper + K 线不可用 skip helper + K 线连通性预检 helper + runner managed connection helper + data_completeness 校准 helper + runtime + status/plan + connectivity + reset helper/payload + standalone external sync/calc + sync_financial body + institution match + sync_industry + industry_stat sync body + build_trends body + build_profiles body + sync_market_data body + lifeboat endpoints 已抽；route/status glue 和 market-data 边界待继续 |

### G3. NEXT（优先级）

| 优先级 | 下一步 |
|---|---|
| P0 | 保持 `implementation_plan.md` / `goal.md` / handoff 与最新门禁结果一致 |
| P1 | 继续推进 `updater.py` 剩余拆分（full/group/single launcher 参数注入已迁入 `updater_launcher.py`；下一步优先剩余 route/status glue 或 market-data daily/monthly/xdxr 边界）|
| P2 | 输出用户要求的“架构全貌介绍” |

### G4. 未提交文件清单（原主线分类，需用 `git status --short` 复核）

下表是原 handoff 的主线分类；当前工作树还包括本轮新增/修改的 `updater_infra.py`、checker 测试、计划文档等。不要按本表直接 stage。

| 文件 | 内容摘要 |
|---|---|
| `backend/services/screening_engine.py` | universe 相关策略查询改为新入口 |
| `backend/services/risk_factors.py` | 与 universe 规则接入联动 |
| `backend/services/audit.py` | 审计管线更新 |
| `backend/scripts/build_picture_daily.py` | 周期构建脚本变更 |
| `backend/scripts/build_signal_context.py` | 信号上下文构建更新 |
| `backend/scripts/build_feature_panel_duck.py` | 选股特征面板构建联动 |
| `backend/routers/institution.py` | institution API 名称映射标注改造 |
| `backend/routers/recommendation.py` | recommendation 查询与 name 映射/标注 |
| `backend/routers/v3_meta.py` | meta 接口与 universe 输出一致化 |
| `backend/routers/v3_selection.py` | 选股接口一致性 |
| `backend/services/stock_detail_read.py` | stock detail 读数入口更新 |
| `backend/services/stock_graph_read.py` | 股票图谱读 API 与映射行为统一 |
| `backend/services/stock_trends_read.py` | 趋势读取接口更新 |
| `backend/services/external_attention.py` | 外部注意力数据读取更新 |
| `backend/services/recommendation_universe.py` | 推荐 universe 规则标注 |
| `backend/scripts/build_price_kline_tdxhub.py` | data-sync 遗留注释/来源标注 |
| `backend/scripts/ingest_holders_tdxhub.py` | data-sync 标注 |
| `SESSION_HANDOFF.md` | 自动/手工 handoff 现场快照 |
| `analysis/workflow_checkpoint.json` | session checkpoint 机器可读状态 |
| `analysis/workflow_checkpoint.md` | session checkpoint 人工可读状态 |

### G5. 历史剩余违规标注（已清零）

状态: 下表是历史修复清单，当前 `check_universe_filter.py --all` 默认 production-code only 为 CLEAN (764 files checked)。`--include-tests` 仍保留 41 个测试夹具引用供人工审计。

| 类别 | 文件（示例行） | evidence 码 |
|---|---|---|
| data-sync | `build_price_kline_tdxhub.py` : 168,174,179,183,189,201,208 | `data-sync-enumeration` |
| data-sync | `cron_daily.py:668` | `freshness-check-table-list` |
| data-sync | `ingest_holders_tdxhub.py:1221` | `lineage-metadata` |
| data-sync | `financial_client.py:1044,1166,1175` | `data-sync-enumeration` |
| data-sync | `financial_indicator_client.py:155` | `data-sync-enumeration` |
| data-sync | `capital_client.py:483` | `data-sync-enumeration` |
| data-sync | `aif10_capability_client.py:350,363` | `data-sync-enumeration` |
| data-sync | `institution_write.py:110` | `data-sync-enumeration` |
| data-sync | `data_quality.py:3429` | `table-exists-check` |
| name-lookup | `run_daily_topk.py:490` | `code-to-name-mapping` |
| schema/meta | `schema_core.py:479` | `schema-definition` |
| schema/meta | `schema_migrations.py:297` | `schema-definition` |
| schema/meta | `security_master.py:6,58,65,101,104` | `table-writer-itself` |
| schema/meta | `seed_dim_data_asset.py:107` | `metadata-registry` |
| schema/meta | `data_lineage/registry.py:58,81` | `lineage-metadata` |
| schema/meta | `labels/universe.py:4` | `docstring-reference` |
| schema/meta | `audit_stale_references.py:131` | `audit-metadata` |
| data-audit | `data_audit.py:120,151,370` | `audit-config-reference` |
| schema/meta | `block_client.py:153` | `error-message-string` |
|  | 其余 | 历史工具报告项已清完；当前 non-test=0 |

### G6. Rule 10 阻断门状态

| 项目 | 状态 |
|---|---|
| 规则本体 | 强制性（.py 变更需 Codex 评审） |
| 生效落地 | `scripts/safe_commit.sh` 已包含 Step 4.5 blocking gate |
| 现状 | staged `.py` commit message 必须含 `Codex-Reviewed:` 或 `codex-review: skipped reason=...`；禁止裸 `git commit` |

### G7. `updater.py` 拆分计划

| 现状 | 计划 |
|---|---|
| 当前 | 五十八刀已完成：`updater.py` 723 LOC，`updater_execution.py` 823 LOC，`updater_launcher.py` 278 LOC，`updater_completeness.py` 108 LOC，`updater_plan.py` 130 LOC，`updater_lifeboat.py` 88 LOC，`updater_market_data.py` 765 LOC，`updater_infra.py` 258 LOC，`updater_calendar.py` 157 LOC，`updater_steps.py` 232 LOC，`updater_connectivity.py` 156 LOC，`updater_sync.py` 443 LOC，`updater_calc.py` 196 LOC，`updater_runtime.py` 34 LOC，`updater_audit.py` 53 LOC，`updater_status.py` 593 LOC，`updater_reset.py` 161 LOC，`updater_institution.py` 533 LOC，`updater_trends.py` 303 LOC，`updater_profiles.py` 455 LOC |
| 目标 | `updater_infra.py`、`updater_steps.py`、`updater_calendar.py`、`updater_runtime.py`、`updater_sync.py`、`updater_calc.py`、`updater_institution.py`、`updater_plan.py`、`updater_execution.py`、`updater.py`（8+ 模块） |
| 约束 | 每刀前 Codex/skill grill；需 `codegraph query` + `codegraph context` 预评估；每刀后 `codegraph sync .` + complexity + targeted tests |

## H. Top 10 Pitfalls（来自 CLAUDE §4.5 anti-example）

| PITFALL | FIX |
|---|---|
| PITFALL: 看到盘中报错就用 `--end` 固定日期 | FIX: 改 sync 入口为 `latest_completed_trade_date`，加 lint 防回退 |
| PITFALL: 先 `DELETE/DROP` 清理脏数据当修复策略 | FIX: 找首个坏写路径并加健康检查，保留防回退回归 |
| PITFALL: `announce_date` 大量 NULL 就放宽告警 | FIX: 回溯 ingest 路径并清理污染，不可静默 |
| PITFALL: 用 future-ordered `selector ORDER BY sharpe` 或 `MAX(oos)` 泄漏 | FIX: 强制 walk-forward 并仅用 `oos` 字段，修 formula 层输出 |
| PITFALL: 全局 formula 100% 领先假设直接采纳（`R>5`/`win>95%`） | FIX: 做 relative/absolute leak 警报 + ablation + 真实 walk-forward |
| PITFALL: 把 `swap_uplift_estimate` 当真实值 | FIX: 改用真实 K 线 forward 仿真成本收益 |
| PITFALL: `vol_aware` 参数硬编码（hardcode sigma/阈值） | FIX: 参数纳入 yaml + Optuna 范围检验 |
| PITFALL: ensemble 权重/regime multiplier 人工拍脑袋 | FIX: 全部参数上 YAML + 搜索空间 + governance 入库 |
| PITFALL: 买卖信号未来函数（`close[idx+1]`） | FIX: 严格静态与 PIT spot-check，禁止未来 index |
| PITFALL: `max_stocks=200` 导致板块覆盖缺失 | FIX: `validate_loaded_stocks` 做四板块覆盖和规模下限

## I. 首次必须运行的 5 条命令（按顺序）

| 步序 | 命令 |
|---|---|
| 1 | `cd /Users/dp/Documents/M/stock/chunkymonkey` |
| 2 | `git status --short` |
| 3 | `cat goal.md` |
| 4 | `cat SESSION_HANDOFF.md` |
| 5 | `cat analysis/workflow_checkpoint.md` |

## J. Continuity Assessment

### J1. Part 1（8 文件）——能否直接继续

| 文件 | 结论 |
|---|---|
| ` /tmp/chunkymonkey_handoff_20260527.md` | I can continue without asking |
| `/Users/dp/Documents/M/stock/chunkymonkey/SESSION_HANDOFF.md` | I can continue without asking |
| `/Users/dp/Documents/M/stock/chunkymonkey/CLAUDE.md` | I can continue without asking |
| `/Users/dp/Documents/M/stock/chunkymonkey/docs/PROJECT_CONSTITUTION.md` | I can continue without asking |
| `/Users/dp/Documents/M/stock/chunkymonkey/docs/implementation_plan.md` | NEEDS-CLARIFICATION: 当前条目与实际执行有历史状态不一致（已完成项仍标“待做”） |
| `/Users/dp/Documents/M/stock/chunkymonkey/PROJECT_INDEX.md` | I can continue without asking |
| `~/.claude/hooks/` 列表 | I can continue without asking |
| `~/.claude/settings.json` | I can continue without asking |

### J2. 按你的要求的 5 文件（精简）

| 文件 | 结论 |
|---|---|
| `/tmp/chunkymonkey_handoff_20260527.md` | CLEAR |
| `/Users/dp/Documents/M/stock/chunkymonkey/SESSION_HANDOFF.md` | CLEAR |
| `/Users/dp/Documents/M/stock/chunkymonkey/CLAUDE.md` | CLEAR |
| `/Users/dp/Documents/M/stock/chunkymonkey/docs/PROJECT_CONSTITUTION.md` | CLEAR |
| `/Users/dp/Documents/M/stock/chunkymonkey/docs/implementation_plan.md` | NEEDS-CLARIFICATION: 需确认“待做/已完成”是否已与 handoff 交叉校准 |

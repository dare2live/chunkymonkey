# CLAUDE.md — 工程规则 (必须遵守)

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
| `mart_per_stock_stage_strategy_optimal` | **stage-aware 9-dim Optuna 寻优结果 (17,663 行)** | 列是 `built_at` 不是 `updated_at`; `stage_filter` 不是 `technical_stage` |
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

- **端口 8000** 默认是 chunky-monkey-v2 backend (`start.command` 里硬编码). 但宿主机上还有别的 app ("志途 LifeHack API") 也想用 8000 — 当前实际占住. 起 chunky-monkey 前先 `lsof -i:8000` 确认.
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

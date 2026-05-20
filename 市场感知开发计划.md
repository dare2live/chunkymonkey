# 市场感知开发计划

> 当前版本: 2026-05-20  
> 依据: `docs/market_perception_codex_handoff.md`, `docs/market_regime_framework.md`, `CLAUDE.md`, CodeGraph 审计, complexity-optimizer 审计, 当前真实 DuckDB 状态。  
> 原则: 本文档是滚动计划。每个阶段完成、数据现实变化、性能瓶颈或 PIT 风险暴露后，先更新本文档，再继续实施。

## 1. 目标边界

市场感知模块的目标是输出每日 market context features，回答“现在这个市场环境下，哪种类型的股票好”。它不直接给买卖信号，不改现有 ranker / panel / paper_sim，不改既有 `fact_*` / `mart_*` 表 schema。

当前消费侧仍保持隔离：P1-P7 只生产 `mart_market_perception_*`，未来接入 ensemble / sniper / institution / paper_sim 必须另开阶段，先做 OOS 验证和 PIT audit。

### 1.1 抽象层级

本模块不是“产业链扩散研究”，而是更高一层的市场状态理解系统。产业链扩散只是其中一个子引擎。上层问题从“这只股票该不该买”改为：

```text
当前市场处在什么阶段？
资金偏好什么？
主线在哪里？
风险在哪里？
这只股票的信号在当前环境下该加权还是降权？
```

最终输出给模型的不是一句买入/卖出，而是一组 context features：

```text
market_regime_score
emotion_cycle_state
theme_lifecycle_stage
theme_score
chain_diffusion_score
fund_anomaly_score
under_reaction_score
leader_follow_score
crowding_risk_score
style_fit_score
resonance_score
```

这些 features 只能作为上层上下文，不直接覆盖现有 ranker 输出。任何“加权/降权”接入都必须后续单独做 OOS 证据和 ablation。

### 1.2 研究模块归并

用户给出的 22 个方向归并为 9 个可实施模块：

| 模块 | 覆盖方向 | 核心输出 |
|---|---|---|
| MarketEmotionCycle | 市场情绪周期、涨停生态、市场宽度与集中度 | `risk_on/risk_off`, 赚钱效应, 追强/低吸/降仓位环境 |
| ThemeLifecycle | 主题生命周期、主线 vs 支线、板块内部结构 | 主题阶段、主线强度、扩散/退潮状态 |
| LeaderFollowerNetwork | 龙头-跟随者关系、横截面相对强弱 | 龙头、跟随路径、滞后补涨候选 |
| FundFlowPath | 资金路径、资金切换、资金承接 | 资金流入/流出/背离/承接 |
| UnderReactionAlpha | 未充分反应、人气变化 > 价格变化、业务暴露度 | 预期差候选、资金动但价格未反应 |
| StyleRotation | 风格轮动、大小盘/成长价值/防守进攻 | `style_fit_score` |
| CrowdingAndReflexivity | 人气拥挤、过热反转、产业链风险传导 | 拥挤风险、退潮风险、龙头断板风险 |
| ChainDiffusion | 产业链扩散、冷启动、正/负向传导 | 链路扩散节点、产业链风险 |
| StockContext | 以上模块聚合到 stock × date | 个股上下文 feature dataframe |

优先级不再按“先产业链”排序，而按短线模型最需要的总开关排序：情绪周期 → 主题生命周期 → 资金/未反应 → 龙头/扩散 → 拥挤/风格 → 个股聚合。

## 2. 当前真实状态

### 2.1 已完成

P1 MarketRegimeEngine MVP 已落地并 push：

| 项 | 证据 |
|---|---|
| DDL | `mart_market_perception_daily` 已在 `backend/services/schema_marts.py` 创建，迁移在 `schema_migrations.py` 幂等补列 / index |
| Service | `backend/services/market_perception/regime_engine.py` 提供 `compute_regime_for_date/range` |
| Builder | `backend/scripts/build_market_perception_daily.py` 写入 `mart_market_perception_daily` |
| Router | `/api/v3/market_perception/snapshot`, `/history`, `/health` 读真实 mart |
| UI | `design/v3-page-market-perception.jsx` 展示 4 个卡片、90 日曲线、7 engine status |
| Tests | `PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -v` = 4/4 passed |
| Backfill | 373 rows, `2024-11-01` -> `2026-05-19`, `regime_score=[-0.403087, 0.567833]` |
| Leakage guard | `|regime_score| > 0.95` = 0 rows |

最近相关提交：

| Commit | 内容 |
|---|---|
| `2d084a95` | schema / DDL |
| `89a932f5` | service / builder / tests |
| `ef281f67` | router live endpoints |
| `8384e899` | UI |
| `21df24f1` | backfill/API/UI 验证证据 |

### 2.2 与 handoff 的现实差异

handoff 指定可读 `mart_index_daily` / `fact_stock_kline_daily`，但当前 `smartmoney.duckdb` 中这两张表不存在。P1 已按项目现有主行情源实现兼容：

- 优先读 mock / 未来可能出现的 `mart_index_daily`、`fact_stock_kline_daily`。
- 真实库 fallback 到 `data/market.duckdb` 的 `market.v_price_kline_qfq`。
- 主库只写新增 mart 表；行情库只读 attach。

这个差异必须保留在后续阶段计划中，不能假设 handoff 表已经存在。

## 3. 审计结果

### 3.1 CodeGraph 结构审计

本机 CodeGraph `0.6.8` 无 `hotspots` 子命令，已使用 `codegraph status/sync/context` 替代。

结果：

- 索引规模: 819 files, 12,632 nodes, 43,270 edges。
- 已同步市场感知新增文件。
- 市场感知当前链路为:
  - DDL: `schema_marts.ensure_mart_schema`
  - 迁移: `schema_migrations.SCHEMA_MAINTENANCE_SQL`
  - Service: `services.market_perception.regime_engine`
  - Builder: `scripts/build_market_perception_daily.py`
  - Router: `routers/v3_market_perception.py`
  - UI: `design/v3-page-market-perception.jsx`

CodeGraph 对新 market perception 符号召回不如 `rg` 精准，后续审计默认组合使用：

```bash
codegraph sync .
codegraph context -p . -n 120 -c 20 "market perception ..."
rg -n "market_perception|mart_market_perception|MarketRegimeEngine" backend design docs
```

### 3.2 Complexity 审计

全仓 complexity 扫描主要命中旧 `assets/js/app.js`，与本模块无关。

定向扫描 `backend/services/market_perception` 仅报：

| 位置 | 结论 |
|---|---|
| `regime_engine.py:339` | `_first_existing()` 的 `name in cols`，右侧是 set，实际 O(1)，属于可接受误报 |

人工审计发现真正需要后续优化的是 `compute_regime_for_range()` 当前逐日调用 `compute_regime_for_date()`，真实 373 行 backfill 可跑通但重复扫描 130 日窗口。P2 前应先做 P1.1 hardening，把 range 计算改成一次性加载区间数据、滚动窗口向量化，降低回填和未来 nightly run 成本。

## 4. 硬性开发规则

1. 任何阶段都不改 `mart_p0a_feature_label_panel_v4`、`services/ranker/*`、`services/paper_sim/*`、`services/strategies/sniper/*`、`services/strategies/institution/*`。
2. 新输出只写 `mart_market_perception_*`。
3. 所有 snapshot 必须 PIT-strict:
   - `snapshot_date < today`
   - `dim_trading_calendar.is_trading = 1`
   - `built_at/as_of_date/source_available_date <= snapshot_date after close`
4. 不允许拍脑袋阈值。阈值必须来自历史 quantile、walk-forward/OOS sweep 或明确标 `unknown`。
5. 任何异常好数字必须停:
   - `|regime_score| > 0.95`
   - Sharpe > 5
   - win_rate > 0.95
   - annual return > 100%
   - RankIC > 0.3
   - 相对 baseline uplift >= 50%
6. 每阶段必须有单测、真实库 smoke、API/UI 验证和 commit message 数字证据。

## 5. 阶段计划

### P1.1 MarketRegimeEngine hardening

目标: 把 P1 MVP 从“可用”推进到“可日常跑批”。

当前进度：

- 已完成配置化: `backend/config/market_perception.yaml` 接管 lookback、score weights、vol bucket、sentiment phase 和 leakage guard 阈值；每个阈值带 evidence，未校准项标 `unknown`。
- 已完成构建审计: 新增 `mart_market_perception_audit_log`，builder 记录 trading days、rows written、missing days、score range、guard status 和输入/输出行数。
- 已完成 API 可观测性: `/health` 输出 latest snapshot date、latest built_at、trading-day lag、score guard status、latest audit summary 和 latest snapshot 是否被最新成功 audit 覆盖。
- 已完成 range 批量化: `compute_regime_for_range()` 一次加载扩展窗口内 HS300、breadth、LHB，使用 pandas shift/rolling 计算 60d return、20d volatility、90d breadth p75。
- 已补 range 等价测试、LHB no-lookahead range 测试、缺 HS300/breadth fail-fast 测试。
- 数据源优先级已明确: P1 K 线/指数走 tdxhub-backed `market.duckdb`，妙想 F10 (`/stock/miaoxiang`, 用户口径 xiaoxiang) 后续用于 F10/主题/业务暴露，不作为 K 线源；AkShare 只能补充，不能作为默认补洞。
- 真实库 smoke: `2026-05-01` -> `2026-05-19` 写入 10/10 trading days，score [-0.024959, 0.210000]，audit guard `ok`；`/health` 返回 `MarketRegimeEngine=live`, latest lag 0, score guard `ok`。
- 全量可复现 backfill: `2024-11-01` -> `2026-05-19` 现有 373 rows；`2026-05-19` HS300 已用 tdxhub `tdxhub_index` 补齐，P1 单日重建 `regime_score=0.151538`，guard `ok`。
- 已关闭 `2026-05-19` 输入数据缺口: `dim_trading_calendar` 标记当日为交易日，`sync_hs300_benchmark_kline.py --code 000300 --start 20260519 --end 20260519` 从 tdxhub 写入 1 行，AkShare fallback 0 行；`market.v_price_kline_qfq` 当日 `000300 close=4852.88 source_name=tdxhub_index`。
- 已完成显式 source-max 截断: `--clamp-to-source-max` 会把 end 显式截到 tdxhub-backed core input max date，剪掉 requested range 内不可复现的 stale mart 行，并在 audit notes 记录 requested/effective/source_max。
- 前端已展示 `/health` 的 latest lag、score guard、latest snapshot audit status 和 latest audit end。

任务：

1. 性能重构: [done]
   - `compute_regime_for_range()` 改为批量加载 HS300、全市场 breadth、LHB 事件。
   - 用 pandas rolling / DuckDB window 一次性计算 60d return、20d volatility、90d breadth p75。
   - 保持 `compute_regime_for_date()` 作为单日接口和单测入口。
2. 配置化: [done]
   - 新增 `backend/config/market_perception.yaml`。
   - 将 `TRADING_DAYS_FOR_RET=60`、`TRADING_DAYS_FOR_VOL=20`、`TRADING_DAYS_FOR_BREADTH_P75=90`、score weights、vol bucket 边界移入 yaml。
   - yaml 每个阈值写 evidence 字段；暂时没有实测证据的标 `unknown`，不得伪装已校准。
3. 数据质量: [partial]
   - 增加 `mart_market_perception_audit_log` 或 builder run summary，记录输入行数、缺失天数、score range、guard 结果。
   - builder 对缺少市场数据的交易日 fail fast，不静默跳过。
4. API: [done]
   - `/health` 增加 latest snapshot lag、latest built_at、score guard status。
5. 测试: [done]
   - 现有 4 个测试保持。
   - 新增 range 等价测试: 批量 range 结果必须等于逐日 date 结果。
   - 新增 stale data test: 缺 HS300 / breadth 时 raise，不写半成品。

验收：

```bash
PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -v
PYTHONPATH=backend python backend/scripts/build_market_perception_daily.py --start 2024-11-01 --end 2026-05-19
PYTHONPATH=backend python - <<'PY'
from services.db import get_conn
with get_conn() as conn:
    print(conn.execute("SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date), MIN(regime_score), MAX(regime_score) FROM mart_market_perception_daily").fetchone())
    print(conn.execute("SELECT COUNT(*) FROM mart_market_perception_daily WHERE regime_score > 0.95 OR regime_score < -0.95").fetchone())
PY
```

### P2 MarketEmotionCycle / 涨停生态扩展

目标: 把 P1 的“指数 + 广度 + 波动”市场温度计升级为短线情绪仪表盘，回答现在适合追强、低吸还是降低仓位。

当前进度：

- 已新增 `mart_market_perception_emotion_daily`，独立输出短线情绪 context，不接入 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/emotion_engine.py` 和 `backend/scripts/build_market_perception_emotion_daily.py`。
- 当前可计算字段: `market_breadth`, `up_count`, `down_count`, `limit_up_count`, `limit_down_count`, `turnover_concentration`, `lhb_event_count`, `emotion_score`, `emotion_state`, `action_bias`, `cycle_phase`。
- 当前不可可靠取得字段: `first_board_count`, `second_board_count`, `third_plus_count`, `promotion_rate_1_to_2`, `promotion_rate_2_to_3`, `open_board_rate`, `next_day_premium`；这些字段写 `NULL`，并在 `unknown_metrics` 明确列出，不用 0 伪装。
- 真实库 smoke: `2024-11-01` -> `2026-05-19` 现有 373 rows；`2026-05-19` 单日重建 `emotion_score=0.414475`, `emotion_state=分化震荡`，guard_rows 0。
- `/health` 已新增 `MarketEmotionCycle=live` 当且仅当 `mart_market_perception_emotion_daily` rows > 0。
- 已新增 API: `/api/v3/market_perception/emotion/snapshot` 和 `/api/v3/market_perception/emotion/history?days=N`，返回最新短线情绪快照和 N 个交易日历史。
- 前端 `design/v3-page-market-perception.jsx` 已接入 EmotionCycle: 顶部双卡显示 Regime + Emotion，90 日曲线叠加 `emotion_score`，Engine Status 显示 regime/emotion rows。
- API/HTML 验收: FastAPI TestClient 返回 emotion snapshot `2026-05-19`, `emotion_score=0.414475`; emotion history 最新到 `2026-05-19`; `/health` 返回 `MarketEmotionCycle=live`, `MarketRegimeEngine=live`, emotion/regime rows 均为 373，latest lag 0。受当前会话 Browser Node REPL 工具未暴露限制，已完成 localhost HTTP/HTML 验收；后续已用 Headless Chrome DevTools 补齐截图。
- 已新增代表股票敏感度审计脚本 `backend/scripts/audit_market_perception_sensitivity.py`。样本覆盖大盘/中盘和白酒、保险、电气设备、通信设备、元器件、半导体；当前诊断显示 6 个代表股 same-day return vs emotion_score 相关系数约 0.197 -> 0.385，risk-off 样本 30 日，risk-on 样本 128-131 日；此结果只作敏感度/准确性 sanity，不作为交易规则。
- 已完成历史 quantile 标定: `emotion_score` p10=-0.3064643, p75=0.4546955, p90=0.6108906 写入 `backend/config/market_perception.yaml`；状态分布为分化震荡 241 日、赚钱效应扩张 93 日、亏钱效应扩散 38 日，`cycle_phase` 中主升扩散 38 日、退潮 38 日。

候选输入：

- `market.v_price_kline_qfq`: 个股收益、振幅、换手 proxy、成交额。
- `fact_lhb_event`: 龙虎榜事件密度、机构/游资事件 proxy。
- `fact_lhb_event`: 游资/机构事件密度。
- 如可用，补充涨停/跌停、炸板率、连板高度、晋级率、昨日涨停溢价；不可用则标 `unknown`，不造假。

输出表：

- `mart_market_perception_emotion_daily`
- 汇总字段再进入 `mart_market_perception_daily.source_engines`

核心指标：

- `limit_up_count`, `limit_down_count`
- `first_board_count`, `second_board_count`, `third_plus_count`
- `promotion_rate_1_to_2`, `promotion_rate_2_to_3`
- `open_board_rate`
- `next_day_premium`
- `market_breadth`
- `theme_concentration`
- `turnover_concentration`

状态输出：

```text
赚钱效应扩张 / 亏钱效应扩散
追强有效 / 低吸有效 / 降低仓位
新周期试错 / 主升扩散 / 高潮 / 退潮
```

验收：

- PIT 单测覆盖涨停生态输入可用日。
- 每个无法取得的字段明确 `unknown`，不能用零填充伪装。
- 历史 quantile 标定高温/退潮阈值。[done: p10/p75/p90]
- 只输出 context，不接入交易权重。
- 每次回测/回填必须跑代表股票敏感度审计，至少覆盖大盘/中盘/小盘或说明当前样本缺口，并覆盖不同行业；输出 same-day sensitivity 和 next-day diagnostic，不得把诊断误写成交易 alpha。

### P3 ThemeLifecycleEngine

目标: 识别主题生命周期和主线/支线，判断题材处于潜伏、启动、确认、主升、扩散、高潮、分歧、退潮、反抽中的哪个阶段。

当前进度：

- 已新增 `mart_market_perception_theme_daily`，P3 MVP 把 TDX L1 行业作为第一版主题边界，不接入 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/theme_lifecycle_engine.py` 和 `backend/scripts/build_market_perception_theme_daily.py`。
- PIT 成分约束: 只使用 `mart_stock_industry_pit.confidence_level='observed_snapshot'` 的区间；`current_label_fallback` 不参与历史主题生命周期回填。
- 当前可覆盖区间受真实 observed PIT 行业快照限制: `2026-04-27` -> `2026-05-19`，写入 168 rows / 14 trading days；更早历史暂不回填，避免用今日行业成分解释过去。
- 当前输出字段: `theme_score`, `lifecycle_stage`, `mainline_rank`, `is_mainline`, `diffusion_state`, `sector_breadth`, `sector_ret_20d/60d`, `sector_excess_20d/60d`, `price_vs_ma20/60`, `limit_up_count`, `top3_turnover_share`。
- `theme_score` 用同日横截面 percentile rank 合成并缩放到 `[-0.9, 0.9]`，不使用绝对涨幅拍脑袋阈值；真实库 smoke 范围 `[-0.7200, 0.9000]`，`ABS(theme_score)>0.95` 为 0。
- `/health` 已新增 `ThemeLifecycleEngine=live` 当且仅当 `mart_market_perception_theme_daily` rows > 0。
- 已新增 API: `/api/v3/market_perception/theme/snapshot` 和 `/api/v3/market_perception/theme/history?days=N&top_n=M`。
- API 验收: latest snapshot `2026-05-19` 返回 12 themes，Top1 `信息产业` score `0.84`, lifecycle `高潮`, diffusion `板块扩散`; history `days=5&top_n=3` 返回 15 rows。
- 前端 `design/v3-page-market-perception.jsx` 已接入 ThemeLifecycle: 顶部主题卡显示主线 / 阶段 / 广度 / 20 日超额，表格展示主题主线/结构/score，14 日 history 展示主线、分歧、退潮时间带。
- localhost 验收: `/theme/snapshot` 返回 12 rows，Top1 `2026-05-19 信息产业 0.84 高潮/板块扩散`; `/theme/history?days=14&top_n=5` 返回 70 rows，`2026-04-27` -> `2026-05-19`; `/health` 返回 `ThemeLifecycleEngine=live`, `theme_rows=168`; `/v3/Chunky%20Monkey%20v3.html` 200 且包含市场感知 tab 和 `v3-page-market-perception.jsx`。
- 3 个 case study sanity:
  - 信息产业: `2026-04-27` score `0.84` 高潮/板块扩散，`2026-05-08` score `0.72` 确认/结构分化，`2026-05-19` score `0.84` 高潮/板块扩散；20 日超额分别约 `+15.76%`, `+31.30%`, `+19.83%`。
  - 装备制造: `2026-04-27` score `0.69` 主升/板块扩散，`2026-05-08` score `0.4725` 启动/结构分化，`2026-05-19` score `0.60` 确认/结构分化；20 日超额分别约 `+10.62%`, `+21.77%`, `+16.86%`。
  - 金融: `2026-04-27` score `-0.525` 反抽/结构分化，`2026-05-08` score `-0.63` 反抽/结构分化，`2026-05-19` score `-0.42` 分歧/板块扩散；20 日超额从 `-0.76%` 到 `+0.45%` 再到 `-1.43%`，未被误标为主线。
- 单测已覆盖 mainline+diffusion、拒绝 current-label fallback、today/future PIT 排除；`PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -v` = 16/16 passed。

前置数据审计：

- tdxhub blocks 是否有 PIT history。
- `dim_stock_tdx_block` 是否只有 latest。如果只有 latest，历史回测不能直接用；需先建 `dim_stock_tdx_block_history` 或标记 research-only。

输出：

- `mart_market_perception_theme_daily`
- `mart_market_perception_theme_stock_daily` 如需要下钻到股票。

验收：

- 不允许用今日 block 成分回测历史。[done for MVP: only observed `mart_stock_industry_pit`, fallback excluded]
- UI 增加主题主线 / 支线 / 退潮视图。[done]
- 至少 3 个历史主题 case study，人工 sanity check。[done]
- 输出必须区分“龙头独涨”和“板块扩散”，不能只看板块涨幅排名。[done for MVP: `diffusion_state` uses sector breadth/top3 turnover plus theme score]

### P4 FundFlowEngine + UnderReactionAlpha

目标: 资金路径、资金/价格背离和“未充分反应” alpha。重点不是找已经涨最多的股票，而是找相关度高、资金已动、价格尚未完全反应的候选。

当前进度：

- 已新增 `mart_market_perception_under_reaction_daily`，P4 MVP 输出 stock × date 的 research-only 资金异动但价格未充分反应候选，不接入 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/under_reaction_engine.py` 和 `backend/scripts/build_market_perception_under_reaction_daily.py`。
- 第一版只使用 `fact_capital_flow_pit_daily` + tdxhub-backed `market.v_price_kline_qfq` + P3 `mart_market_perception_theme_daily`。`fact_hsgt_daily` / `fact_dzjy_event` 当前主要是 AkShare 补充且 `built_at` 为后补写盘时间，暂不纳入 P4 MVP。
- PIT 口径: `fact_capital_flow_pit_daily` 已在 `services/features/capital_flow.py` 标注为 trailing <= signal_date 的 PIT 特征，`built_at` 是 backfill 写盘日，不作为逻辑 as_of_date；P4 join 使用 `trade_date = snapshot_date`，单测覆盖未来 capital_flow 行不入。
- 当前输出字段: `under_reaction_score`, `fund_anomaly_score`, `price_reaction_score`, `capital_flow_score`, `amount_expansion_score`, `crowding_penalty`, `ret_5d/20d`, `amount_ratio_5_20`, LHB/exec/holder 原始特征和主题上下文。
- 真实库 smoke: `2026-05-12` -> `2026-05-19`, `top_n=50`, 写入 300 rows / 6 trading days；`under_reaction_score=[-0.5141, 0.6236]`, `ABS(score)>0.95` 为 0。
- API/health: `/api/v3/market_perception/under_reaction/snapshot?limit=5` 返回 5 rows，latest `2026-05-19` Top1 `600748` score `0.515319`; `/health` 返回 `FundFlowEngine=live`, `under_reaction_rows=300`。
- 前端 `design/v3-page-market-perception.jsx` 已接入 UnderReaction: 展示 stock_code、theme、lifecycle、under/fund/price score、5d/20d return 和 LHB count。
- localhost 验收: `/under_reaction/snapshot?limit=20` 返回 20 rows，Top1 `2026-05-19 600748 0.515319`; `/health` 返回 `FundFlowEngine=live`, `under_reaction_rows=300`; `/v3/Chunky%20Monkey%20v3.html` 200 且包含市场感知 tab 和 `v3-page-market-perception.jsx`。
- 3 个 candidate sanity:
  - `600748`: score `0.515319`, fund `0.732204`, price `0.182944`, 5d `-11.05%`, 20d `-2.79%`, 建筑地产/启动，资金异常高但价格仍回落。
  - `600539`: score `0.501899`, fund `0.700932`, price `0.132343`, 5d `-19.04%`, 20d `-5.10%`, 信息产业/高潮，主题强但个股价格未跟上。
  - `002229`: score `0.477600`, fund `0.701404`, price `0.173943`, 5d `-4.02%`, 20d `-16.08%`, 社会服务/分歧，资金信号强但价格仍弱。
- 单测已覆盖资金强但价格未反应优先、未来 capital_flow 行不入、today/future PIT 排除；`PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -v` = 19/19 passed。

候选输入：

- `fact_capital_flow_pit_daily` [done for MVP]
- `fact_hsgt_daily` [deferred: AkShare supplementary, built_at/as_of not suitable yet]
- `fact_lhb_event` [indirectly in `fact_capital_flow_pit_daily`]
- `fact_dzjy_event` [deferred: AkShare supplementary]
- `fact_executive_trade_event` [indirectly in `fact_capital_flow_pit_daily`]

输出：

- `mart_market_perception_fund_flow_daily` [pending]
- `mart_market_perception_under_reaction_daily` [done]

核心因子：

```text
资金首次异动
资金连续流入
资金流入但价格未涨
资金流入后缩量不跌
板块资金扩散
龙头分歧后资金承接
产业链相关度 - 个股涨幅
成交额放大 - 个股涨幅
板块热度 - 个股涨幅
人气排名变化 - 股价变化
```

PIT 重点：

- 北向 / 龙虎榜 / 大宗交易必须按实际可用日，不按事件日裸 join。
- 所有 `built_at/source_available_date` 都要进入 join 条件。

验收：

- `test_no_lookahead_fund_flow` [done as `test_under_reaction_no_lookahead_capital_flow`]
- `test_tplus1_lhb_availability`
- OOS 诊断只输出 evidence，不调权重。
- `under_reaction_score` 必须惩罚“价格已经暴涨 / 换手过热 / 人气过度拥挤”，避免把追高伪装成预期差。[done for price reaction/crowding penalty MVP]

### P5 LeaderFollowerEngine + ChainDiffusionEngine

目标: 在主题/行业/产业链内部识别“谁先动、谁后动、谁经常带动谁”，构建交易行为驱动的题材传播图。静态产业链图只能作为先验，真正排序要来自市场实际跟随关系。

前置依赖：

- P3 主题边界。
- PIT 行业/主题成员。
- 涨停时间、封单等数据如不可用，则先做相对强度简化版。
- 产业链静态图谱和 F10 业务暴露度数据。

当前进度：

- 已新增 `mart_market_perception_leader_follower_daily`，P5 MVP 只做相对强弱版 leader/follower 行为边，不引入静态产业链图谱，不接 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/leader_follower_engine.py` 和 `backend/scripts/build_market_perception_leader_follower_daily.py`。
- 数据源: tdxhub-backed `market.v_price_kline_qfq` 或 mock `fact_stock_kline_daily` + `mart_stock_industry_pit.confidence_level='observed_snapshot'` + P3 `mart_market_perception_theme_daily`。`dim_trading_calendar.is_trading=1` 是最优先日期规则；请求区间先落到交易日历，today/future 直接拒绝。
- 算法口径: 每个 snapshot_date × theme 内用当日及以前 close/amount 计算 1/3/5/20 日收益、5/20 日成交额比；leader 用 5 日相对强度 + 成交额 rank，follower 用低 5 日累计涨幅 + 当日/3 日响应 + 成交额 rank，构造 `leader_stock_code -> follower_stock_code` 同板块边。
- 不使用事后“龙头”标签，不使用 post-snapshot forward return；`ChainDiffusionEngine` 在 `/health` 标记为 `research_mvp`，不等同完整产业链图谱。
- 真实库 smoke: `2026-05-12` -> `2026-05-19`, `top_n=5`, 写入 390 rows / 6 trading days；`diffusion_score=[0.3134,0.8806]`, `ABS(score)>0.95` 为 0；与 `dim_trading_calendar` left join 后 `non_trading_rows=0`。
- API/health: `/api/v3/market_perception/leader_follower/snapshot?limit=5` 返回 5 rows，latest `2026-05-19` Top1 `信息产业 688507 -> 688584 score 0.788758`; `/health` 返回 `LeaderFollowerEngine=live`, `ChainDiffusionEngine=research_mvp`, `leader_follower_rows=390`。
- 前端 `design/v3-page-market-perception.jsx` 已接入 LeaderFollower: 展示 theme/lifecycle/leader/follower/diffusion/leader 5d/follower 1d/5d/amount。
- 单测已覆盖相对强 leader -> lagging responder、拒绝 `current_label_fallback` 成员、交易日历优先日期门、today/future PIT 排除；`PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -q` = 23/23 passed。
- CodeGraph 已同步 8 个变更文件；complexity 定向扫描对 `_build_edges()` 报 nested-loop，人工审计为按 theme group 后只保留 `top_n<=5` follower 的有界循环，真实 smoke 390 rows 可接受，后续若扩到全市场全边再向量化。

输出：

- `mart_market_perception_leader_follower_daily` [done]
- `mart_market_perception_chain_diffusion_daily` [pending: 需要产业链/F10 图谱版本化]

边权重：

```text
历史跟随次数
平均滞后时间
平均补涨幅度
胜率
回撤
关系类型: 同板块 / 上游 / 下游 / 供应商 / 客户 / 同资金偏好
```

风险：

- 图谱质量决定上限。
- 不做 GNN 起步；先用层级 + lag model。

验收：

- 不用“事后已知龙头”标签。[done for MVP]
- 每个主题内只用当日及以前相对强度。[done for MVP]
- 做 5 个历史主题人工 review。
- 图谱版本化。[pending for full chain diffusion]
- 每个 edge 有来源和更新时间。[done for leader/follower MVP via `source_engines` + `built_at`]
- `chain_diffusion_score` research-only，直到 OOS 证据通过。

### P6 StyleRotation + CrowdingAndReflexivity

目标: 识别市场偏好的风格、拥挤程度、反身性风险和板块内部结构，决定一个信号在当前环境下应该被加权还是降权。

当前进度：

- 已新增 `mart_market_perception_style_daily`，P6 MVP 输出日度 style/crowding context，不接 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/style_rotation_engine.py` 和 `backend/scripts/build_market_perception_style_daily.py`。
- 数据源: tdxhub-backed `market.v_price_kline_qfq` 或 mock `fact_stock_kline_daily`；`dim_trading_calendar.is_trading=1` 是最优先日期规则。`fact_market_cap_decile_daily` 已补到 `2026-05-19`，5 月 P6 smoke 使用 `market_cap_decile`，不再降级为 `amount_liquidity_proxy`。
- 当前输出字段: `style_rotation_score`, `style_bias`, `size_preference_score`, `trend_preference_score`, `crowding_risk_score`, `overheat_reversal_risk`, small/mid/large/trend/reversal 1 日收益、top decile turnover share、hot stock share、emotion context。
- 算法口径: 每个交易日用当日及以前价格/成交额计算 1d/20d return 和 20d amount；style decile 用 market cap decile 或 liquidity decile；小盘 vs 大盘、趋势 vs 超跌用横截面篮子差异构造连续分数；拥挤风险用热股成交额占比 + top decile 成交额占比，不用单一“高热度=风险”硬标签。
- 真实库 smoke: `2026-05-12` -> `2026-05-19` 写入 6 rows / 6 trading days；`style_rotation_score=[0.0275,0.1356]`, `crowding_risk_score=[0.4211,0.4505]`, guard_rows=0, non_trading_rows=0；latest `2026-05-19` 为 `大盘/趋势`, style `0.054629`, crowding `0.442519`, source `market_cap_decile`。
- API/health/UI: `/api/v3/market_perception/style/snapshot` 返回 latest `2026-05-19 大盘/趋势`; `/health` 返回 `StyleRotationEngine=research_mvp`, `CrowdingRiskEngine=research_mvp`, `style_rows=6`; 前端已接入 `StyleRotation · CrowdingRisk` 卡片。
- 单测已覆盖小盘偏好识别、市值分位缺失时 fallback 到 liquidity proxy、交易日历优先日期门、today/future PIT 排除；`PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -q` = 31/31 passed。
- Complexity 扫描已跑全仓，当前新增脚本是单条 SQL 批处理，未新增 Python 层 N+1；扫描热点主要集中在既有 `assets/js/app.js` nested loop/sort-in-loop。

子模块：

| 子模块 | 研究问题 | 输出 |
|---|---|---|
| StyleRotation | 偏大票/小票、成长/价值、趋势/超跌、防守/进攻 | `style_fit_score` |
| BoardStructure | 板块是龙头独涨还是集体扩散 | `theme_internal_breadth`, `leader_concentration` |
| CrowdingRisk | 人气过高、成交额过热、换手过热 | `crowding_risk_score` |
| ReflexivityRisk | 强者恒强何时变成强者过热 | `overheat_reversal_risk` |
| NegativeSpillover | 龙头炸板/高位退潮的负向传导 | `chain_negative_spillover` |

验收：

- 高热度不能简单等于风险，必须结合主题生命周期阶段解释。
- 主升初期高热度可作为确认；高潮/退潮期高热度才作为风险。
- 所有阈值走历史 quantile 或 OOS sweep。

### P7 StockContextEngine

目标: 把 P1-P6 聚合到 stock × date context features。

当前进度：

- 已新增 `mart_market_perception_stock_context_daily`，P7 MVP 先以 `mart_market_perception_under_reaction_daily` 的 top seed 作为个股候选池，聚合 P1-P6 上下文；不接 ranker / panel / paper_sim。
- 已新增 `backend/services/market_perception/stock_context_engine.py` 和 `backend/scripts/build_market_perception_stock_context_daily.py`。
- 聚合字段: `context_score`, `context_state`, `market_regime_score`, `emotion_score/state`, `theme_score/lifecycle`, `under_reaction_score`, `fund_anomaly_score`, `leader_follow_score`, `chain_diffusion_score`, `style_rotation_score/bias`, `crowding_risk_score`, `overheat_reversal_risk`, `data_completeness_score`, `missing_context_fields`。
- PIT 口径: 所有 seed 先 join `dim_trading_calendar.is_trading=1`；所有 engine 输出只按同一 `snapshot_date` 左连，不做向前填充。`2026-05-19` P1/P2 补齐后，stock context 已重建，`market_regime_score` / `emotion_score` 缺失计数为 0；仍只把缺失 `leader_follow_score` 等真实缺口写入 `missing_context_fields`。
- 真实库 smoke: `2026-05-12` -> `2026-05-19`, `limit=50`, 写入 300 rows / 6 trading days；P6 市值分位与 P1/P2 最新日更新后重建为 `context_score=[-0.1383,0.3329]`, `data_completeness_score=[0.8571,1.0000]`, guard_rows=0, non_trading_rows=0。
- API/health/UI: `/api/v3/market_perception/stock_context/snapshot?limit=5` 返回 latest `2026-05-19` 5 rows，Top1 `600539 context_score=0.305132 completeness=0.857143`; `/health` 返回 `StockContextEngine=research_mvp`, `stock_context_rows=300`; 前端已接入 `StockContext · Research Only` 表格。已修复缺失 `leader_stock_code` 在 API/UI 中显示成 `nan` 的问题，缺失值统一显示 `—`。
- 单测已覆盖多 engine 聚合、缺全局上下文不前填且记录 missing fields、交易日历优先日期门、today/future PIT 排除；`PYTHONPATH=backend python -m pytest backend/tests/services/market_perception/ -q` = 31/31 passed。
- CodeGraph 已同步 8 个变更文件；complexity 扫描对 P7 列存在性检查仍报 membership-in-loop，代码已改为 `set(out.columns)`，属于静态误报；P5 `_build_edges()` 有界 nested-loop 仍保留后续优化项。

输出：

- `mart_market_perception_stock_context_daily` [done for under-reaction seed MVP]

硬约束：

- 本阶段之前仍不接 ranker。
- 接 ranker 前必须完成:
  - feature PIT audit
  - OOS RankIC / uplift
  - ablation
  - relative uplift >= 50% 时强制逐列 PIT review

## 6. UI 计划

当前 UI 已有 P1 卡片和 90 日图。

后续 UI 只展示已验证数据，不展示空故事：

| 阶段 | UI |
|---|---|
| P1.1 | health 增加数据新鲜度、score guard、latest lag |
| P2 | 短线情绪仪表盘：涨停生态、赚钱效应、追强/低吸/降仓位状态 |
| P3 | 主题生命周期 tab，主线/支线/退潮列表，板块内部结构 |
| P4 | 资金异动与未充分反应 tab，资金/价格背离 chart |
| P5 | 龙头/跟随 + 产业链扩散视图 |
| P6 | 风格轮动、拥挤风险、退潮风险 |
| P7 | 个股 context drawer，复用 `v3-drawer-stock.jsx` 模式 |

UI 验证要求：

- 打开 `http://127.0.0.1:8000/v3/Chunky%20Monkey%20v3.html`
- 市场感知 tab 不得显示 stub。
- 控制台无 fetch error。
- 小屏不重叠；图表有真实点位。

## 7. 每阶段标准流程

1. 更新本文档的“当前真实状态”和阶段 TODO。
2. CodeGraph:
   ```bash
   codegraph sync .
   codegraph context -p . -n 120 -c 20 "market perception <phase>"
   ```
3. Complexity:
   ```bash
   python3 /Users/dp/.codex/skills/complexity-optimizer/scripts/analyze_complexity.py backend/services/market_perception --format markdown
   ```
4. 数据审计:
   - DESCRIBE 输入表。
   - 统计 row count / date range / null rate / built_at coverage。
   - 明确不可用字段，不用替代假数据。
5. 实施:
   - 新 engine 新 package 文件。
   - 新 mart 表 DDL。
   - 新 builder 或扩展 builder。
   - router/UI 只在数据真实存在时显示 live。
6. 测试:
   - 单测 mock DB。
   - 真实 DB smoke。
   - API curl。
   - UI 浏览器验证。
7. Commit + push:
   - commit message 含 `# PIT-strict`
   - 含测试数字、backfill 行数、score/guard evidence。

## 8. 当前下一步

P1.1 与 P2 MarketEmotionCycle MVP 已可日常读取。当前下一步按优先级推进：

1. P3 后续增强: 当前 MVP 只覆盖 observed PIT 行业快照区间，不回填 fallback 历史；下一步要找 tdxhub block / 妙想主题的 PIT 历史源，把主题边界从 TDX L1 行业扩展到概念/产业链。
2. P4 下一步把 `fact_hsgt_daily` / `fact_dzjy_event` 的 source_available_date 问题解决后再纳入 FundFlowEngine；UnderReaction 仍保持 research-only。
3. P5 下一步做 5 个历史主题人工 review，并接入妙想/F10 或 tdxhub block 的版本化产业链关系后再写 `mart_market_perception_chain_diffusion_daily`。
4. P6 下一步结合 ThemeLifecycle 阶段细化“主升确认 vs 高潮风险”的拥挤解释，并继续做不同市值/行业样本敏感度审查。
5. P7 下一步把 stock context 从 UnderReaction seed 扩到更完整的候选池，并补真实 drawer 级交互；接交易权重前必须另做 OOS / ablation / PIT audit。

## 9. 滚动调整记录

| 日期 | 调整 | 原因 | 证据 |
|---|---|---|---|
| 2026-05-20 | P1 已完成，新增 P1.1 hardening 作为 P2 前置 | 真实库缺 handoff 两张输入表；range 逐日查询可跑但后续不宜扩展 | backfill 373 rows, score [-0.403087, 0.567833], complexity 定向扫描 |
| 2026-05-20 | 将“产业链扩散”上抽象为市场理解层；重排 P2-P6 优先级 | 用户补充 22 个研究方向，明确情绪周期/主题生命周期/资金未反应优先于单独产业链扩散 | 本计划 §1.1/§1.2 和 P2-P6 调整 |
| 2026-05-20 | P1.1 完成配置化、构建审计和 health guard/freshness | 先解决阈值 hardcode 与生产可观测性，range 向量化单独推进 | `backend/config/market_perception.yaml`, `mart_market_perception_audit_log`, `/health` latest lag + guard |
| 2026-05-20 | P1.1 smoke 通过但全量 backfill 超时 | 短区间验证证明 audit/health 生效；全量卡在逐日重复扫描，确认性能重构是下一阻塞项 | smoke 10/10 rows, score [-0.024959, 0.210000], guard ok; mart 373 rows, global score [-0.403087, 0.567833], guard_rows 0 |
| 2026-05-20 | P1.1 range 批量化完成；全量可复现回填到 source max date | 当前 HS300 source max date 是 2026-05-18，2026-05-19 缺输入需 fail fast | 2024-11-01 -> 2026-05-18 写 372/372 rows in 4.126s; 2026-05-19 fail-fast audit; tests 8/8 |
| 2026-05-20 | 数据源优先级写入 P1.1；新增显式 source-max 截断和前端 audit 状态 | 用户明确 tdxhub/miaoxiang 优先，AkShare 只补充；不能为了补 5/19 静默切 AkShare | `--clamp-to-source-max` 写 372/373 rows, prune 1 stale row, health latest_snapshot_audit_status ok, tests 9/9 |
| 2026-05-20 | P2 MarketEmotionCycle MVP + quantile 阈值标定 | 用可得的 tdxhub-backed K 线/LHB 先输出短线情绪 context；连板/炸板未知字段保留 NULL | 372 rows, emotion_score [-0.874850, 0.888275], p10=-0.3064643, p75=0.4546955, p90=0.6108906, guard_rows 0, tests 13/13 |
| 2026-05-20 | P2 EmotionCycle API/UI 接入 | 短线情绪必须在市场感知页直接可见，health 只在 mart 有 rows 时 live | `/emotion/snapshot` 2026-05-18 score 0.127541; `/emotion/history?days=90` rows 90; `/health` Regime/Emotion live, rows 372/372; tests 13/13 |
| 2026-05-20 | P3 ThemeLifecycle MVP + API/health 接入 | 主题生命周期只能用 observed PIT 行业成分；fallback 历史必须拒绝 | 168 rows, 14 trading days, 2026-04-27 -> 2026-05-19, score [-0.7200,0.9000], guard_rows 0, `/theme/snapshot` Top1 信息产业 score 0.84 高潮/板块扩散, tests 16/16 |
| 2026-05-20 | P3 ThemeLifecycle UI + 3 case studies | 市场感知页必须显示主线/分歧/退潮视图，不能只停在 API | UI 接入 `/theme/snapshot` + `/theme/history?days=14&top_n=5`; localhost HTML/API 200; case studies: 信息产业、装备制造、金融; tests 16/16 |
| 2026-05-20 | P4 UnderReaction MVP + API/health 接入 | 资金异动但价格未反应先用已 PIT 化 capital_flow，AkShare 补充源暂缓 | 300 rows, 6 trading days, 2026-05-12 -> 2026-05-19, score [-0.5141,0.6236], guard_rows 0, `/under_reaction/snapshot` Top1 600748 score 0.515319, tests 19/19 |
| 2026-05-20 | P4 UnderReaction UI + 3 candidate sanity | 预期差候选必须可视化，且要验证不是追高 | UI 接入 `/under_reaction/snapshot?limit=20`; localhost HTML/API 200; cases 600748/600539/002229 均 fund 高、price reaction 低或负收益、crowding 0; tests 19/19 |
| 2026-05-20 | P5 LeaderFollower MVP + API/UI 接入 | 先用交易日历 + observed PIT 成员 + tdxhub-backed 价格做相对强弱版行为传播边，静态产业链图谱暂缓 | 390 rows, 6 trading days, 2026-05-12 -> 2026-05-19, diffusion_score [0.3134,0.8806], guard_rows 0, non_trading_rows 0, `/leader_follower/snapshot` Top1 信息产业 688507->688584 score 0.788758, tests 23/23 |
| 2026-05-20 | P6 StyleRotation/Crowding MVP + API/UI 接入 | 当前市值分位表只到 2026-04-23，5 月先用 tdxhub-backed liquidity decile 明示 fallback，不伪装市值 | 6 rows, 6 trading days, 2026-05-12 -> 2026-05-19, style [0.0260,0.1638], crowding [0.4080,0.4410], guard_rows 0, non_trading_rows 0, `/style/snapshot` latest 大盘/趋势 score 0.071452, tests 27/27 |
| 2026-05-20 | P7 StockContext MVP + API/UI 接入 | 先聚合 UnderReaction 候选池，不向前填充缺失 engine 输出，明确 data completeness | 300 rows, 6 trading days, 2026-05-12 -> 2026-05-19, context [-0.1377,0.3362], completeness [0.5714,1.0000], guard_rows 0, non_trading_rows 0, `/stock_context/snapshot` Top1 600539 score 0.251004 completeness 0.571429, tests 31/31 |
| 2026-05-20 | P6 市值分位输入补到最新交易日 | 用户要求交易日历优先，P6 不能继续用成交额代理替代真实市值分位 | `fact_market_cap_decile_daily` 3,509,364 rows / 571 trading days / latest 2026-05-19 / missing_after_max 0; P6 latest source `market_cap_decile`; P7 rebuilt context [-0.1383,0.3329]; tests 31/31 |
| 2026-05-20 | 市场感知真实浏览器 UI 验收补齐 | 计划要求截图/控制台证据，且页面必须无 stub、无 fetch error、无明显数据占位 | Headless Chrome DevTools 打开 `http://127.0.0.1:8000/v3/Chunky%20Monkey%20v3.html` 并点击“市场感知”；截图 `/tmp/chunkymonkey-market-ui-after.png`; 7 个模块均可见，`market_cap_decile`/`600539` 可见，stub=false, API 异常=false, visible_nan=false, relevant console/runtime events=0 |
| 2026-05-20 | HS300 `2026-05-19` tdxhub 缺口关闭并重建 P1/P2/P7 | 交易日历优先，不能因指数缺口让最新交易日退回 5/18，也不能静默切 AkShare 补洞 | `sync_hs300_benchmark_kline.py` 写入 `000300` 1 行，`tdx_rows=1`, `fallback_rows=0`, close=4852.88; P1 `regime_score=0.151538`; P2 `emotion_score=0.414475`; `/health` mart/emotion rows=373/373, latest lag=0, guard violations=0; P7 300 rows, context [-0.1383,0.3329], completeness [0.8571,1.0000] |

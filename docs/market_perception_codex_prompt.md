# Codex Prompt — 市场感知模块 MVP (MarketRegimeEngine)

> 用户使用方法: 把下面"PROMPT 正文"全文复制到 Codex 窗口发送即可. Codex 会读 handoff doc 后开始实施.
> 推荐 Codex 启动方式: `codex --model gpt-5.5 --effort xhigh --fresh`

---

## PROMPT 正文 (复制此分隔线下文整体)

---

任务: 实施 ChunkyMonkey 项目的 **市场感知模块** P1 阶段 (MarketRegimeEngine MVP).

### 上下文

ChunkyMonkey 是一个 A 股量化系统 (Python + DuckDB + FastAPI + React/JSX), 现行选股链路是 **孤立个股打分**, 缺市场状态理解. 用户要求加一个独立模块输出 daily market context features. **不动现有 ranker / panel / paper_sim**, 数据库表只读不改.

### 第一步: 读 3 份文档

1. `docs/market_perception_codex_handoff.md` — **主任务交接文档** (完整范围 / 约束 / 输出契约 / 单测要求)
2. `docs/market_regime_framework.md` — 26KB 完整设计文档 (P1 你只做 MarketRegimeEngine 一部分)
3. `CLAUDE.md` — **必读** 第 5 节 PIT / 第 7 节真金白银 / 第 8 节工程纪律

### 第二步: 用 codegraph 跑结构 audit (可选, 但推荐)

```bash
codegraph hotspots --filter market_perception
```

了解我已 stake 的入口点 (Claude 占位 stub 文件清单):
- 前端: `design/v3-page-market-perception.jsx` (79 行 stub)
- 前端集成: `design/Chunky Monkey v3.html` (TABS 数组已加 'market', 已 mount `<PageMarketPerception/>`)
- 后端: `backend/routers/v3_market_perception.py` (113 行 stub, 3 endpoints)
- 后端集成: `backend/main.py` (已 include_router prefix=/api/v3/market_perception)
- 任务文档: `docs/market_perception_codex_handoff.md`

### 第三步: 实施清单 (P1 MVP)

按以下顺序, 每完成一项 commit + push:

**3.1 数据层 (~ 1h)**
- 在 `backend/services/schema_marts.py` 加 DDL `mart_market_perception_daily` (DDL 模板见 handoff doc §3.3)
- 在 `backend/services/schema_migrations.py` 加 idempotent ALTER (commit-safe re-run)
- 实测: `python -c "from services.db import get_conn; from services.schema_marts import ensure_schema; ensure_schema(get_conn())"`

**3.2 Service 层 (~ 3h)**
- 新建 `backend/services/market_perception/__init__.py`
- 新建 `backend/services/market_perception/regime_engine.py`
- 接口: `compute_regime_for_date(conn, snapshot_date) -> dict` (返 4 个 features + PIT 守门)
- 接口: `compute_regime_for_range(conn, start, end) -> pd.DataFrame`
- **PIT 严格**: `snapshot_date` 必须 < today (用 `dim_trading_calendar.is_trading=1`); 龙虎榜 built_at <= snapshot_date 收盘后
- **不拍脑袋阈值**: breadth `健康扩散` 阈值 = HS300 历史 90d rolling p75 (非 0.6 hardcode)

**3.3 Build script (~ 1h)**
- 新建 `backend/scripts/build_market_perception_daily.py`
- CLI: `python build_market_perception_daily.py --start 2024-11-01 --end 2026-05-19`
- 用 `services.duck_adapter.connect()`, 不裸 `duckdb.connect`
- 跑完写 `built_at = now` UTC

**3.4 Router 扩展 (~ 1h)**
- 编辑 `backend/routers/v3_market_perception.py`:
  - `/snapshot`: 移除 stub, 改为查 `mart_market_perception_daily ORDER BY snapshot_date DESC LIMIT 1`
  - `/history?days=N`: 真实返 N 日时序 (用 `dim_trading_calendar` 计算 N 个 trading_day)
  - `/health`: `MarketRegimeEngine` 状态从 `stub` → `live` 当且仅当 `mart_market_perception_daily` rows > 0
- 不改 endpoint 路径 (前端 jsx 已 fetch)

**3.5 前端扩展 (~ 2h)**
- 编辑 `design/v3-page-market-perception.jsx`:
  - 加 90 日 regime_score / breadth / volatility 时序图 (复用 `window.CMV3.UI` chart 组件; 看 `design/v3-page-portfolio.jsx` 的 NAV 曲线学样板)
  - 显示 `built_at` (数据新鲜度)
  - 7 engine status badge (调 `/api/v3/market_perception/health`)
- 不改 html tab list (已 stake)

**3.6 单测 (~ 1.5h)**
- 新建 `backend/tests/services/market_perception/test_regime_engine.py`
- ≥ 4 tests pass (mock data, 不需真 DB):
  - `test_regime_score_risk_on` — HS300 60d +20% / vol 12% / breadth 70% → score > 0.3
  - `test_regime_score_risk_off` — HS300 60d -15% / vol 35% / breadth 25% → score < -0.3
  - `test_pit_strict_today_excluded` — input today → ValueError or skip
  - `test_no_lookahead` — `built_at > snapshot_date` 数据被排除
- 跑: `pytest backend/tests/services/market_perception/ -v` 全部 pass

**3.7 实测 backfill (~ 30min)**
```bash
PYTHONPATH=backend python backend/scripts/build_market_perception_daily.py \
    --start 2024-11-01 --end 2026-05-19
```
- 预期 ~370 行 (~1.5 年 trading days)
- 验证: regime_score 跨 [-0.8, +0.7] 之间 (历史有真实大起落)
- 触发警报检查 (CLAUDE.md Rule 5): 任何 regime_score 异常 > +0.95 OR < -0.95 必须停下来检查 leakage

**3.8 E2E UI 验证**
- 启动 backend: `cd backend && uvicorn main:app --reload --port 8000`
- 浏览器: `http://localhost:8000/v3/Chunky%20Monkey%20v3.html`
- 点 "市场感知" tab — 应该看到真数据 (4 个卡片 + 时序图), 不是 stub 占位
- 截图 / 控制台 log 贴回 commit message

### 关键约束 (硬性, 违反停)

| [X] 禁忌 | [OK] 正解 |
|---|---|
| 改 `mart_p0a_feature_label_panel_v4` | 只 READ |
| 改 `services/ranker/*` | 不动 |
| `try: ... except: pass` 静默吞错 | `except Exception as e: logger.warning(f"reason: {e}")` |
| `regime_score = 0.85` 拍脑袋阈值 | 用历史 quantile 标定, commit message 写明 evidence |
| `walk_forward_mode = 'none'` | 用 walk_forward.split_expanding_monthly 或 OOS 验 |
| `# TODO: 以后修 PIT` | PIT 类违规立刻修 + 加单测, 不留 TODO |
| 跳过单测 / `pytest --no-verify` | 单测必须真 pass |

### 完成标准

- [ ] 6 个新文件 created (service / build / router fix / jsx fix / test / DDL)
- [ ] backfill 跑通 ~370 rows in `mart_market_perception_daily`
- [ ] pytest 4 tests pass
- [ ] 前端 tab "市场感知" 显示真数据 (regime_score 数字 / 时序图)
- [ ] /api/v3/market_perception/health 返 `MarketRegimeEngine: live`
- [ ] git commit + push (单 commit OR 多 commit 都行, 推荐分 6 个 commits 对应 6 步)
- [ ] 每个 commit message 含 `# PIT-strict` + 数字 evidence

### 沟通

- 实施中有 ambiguity → `codex --resume` 接着问, 不假设
- 不确定阈值 / quantile → 显式跑历史 SQL audit 再定, 写 evidence 进 commit message
- 任何 leakage 警报 (absolute: sharpe > 5 / win > 95% / ann > 100% / RankIC > 0.3) 立即停, 不交付
- 主 Claude session 在跑别的, 不会并发改此模块. 你做完 push 后, 主 Claude 看到 commit 自然接续

### 估时

| 步骤 | ETA |
|---|---|
| 3.1 DDL | 1h |
| 3.2 service | 3h |
| 3.3 build script | 1h |
| 3.4 router | 1h |
| 3.5 frontend | 2h |
| 3.6 tests | 1.5h |
| 3.7 backfill | 0.5h |
| 3.8 E2E | 0.5h |
| **总计** | **~10.5h** (1-2 天单人 focused) |

开始吧.

---

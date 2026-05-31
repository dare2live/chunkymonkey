# 市场感知模块 (Market Perception) — Codex 实施任务交接

> **日期**: 2026-05-20
> **任务来源**: 用户 vision (2026-05-19) — "判断现在这个市场环境下哪种类型的股票好"
> **设计文档**: `docs/market_regime_framework.md` (26KB, 7 engines × 20 研究方向)
> **状态**: Stub 入口已 stake (前端 tab + 后端 router + 占位 jsx), 待 Codex 扩展
> **执行者**: 由用户单独开 Codex 窗口接手 (主 Claude session 不并发改此模块)

---

## 1. 任务总览

ChunkyMonkey 现行选股链路 (sniper / institution / lambdamart) 是 **孤立个股打分**.
缺少 **市场理解层**: 同样的 features 在 risk_on 主升浪 vs risk_off 退潮期, 真实 alpha 差异 > 3x.

**目标**: 实施一个 **独立模块**, 输出每日 market context features, 不动现有 ranker / panel / paper_sim.
未来由其他模块按需消费 (本任务不负责消费侧).

**一句话**: 不判断"这只股票好不好", 判断"现在这个市场环境下, 哪种类型的股票好".

---

## 2. 模块独立性 (硬约束)

| 不可触碰 | 原因 |
|---|---|
| `mart_p0a_feature_label_panel_v4` | GCP retrain v2 在跑 (2-4h ETA), 改 panel = 重训冲突 |
| `services/ranker/*` / LambdaRank model | 模型层不动, 这次不接入 ensemble |
| `services/paper_sim/*` | KPI / lineage 已固化 |
| `services/strategies/sniper/*` / `institution/*` | Alpha 三大类已上线 |
| 任何现有 `fact_*` / `mart_*` 表的 ALTER | 不动 schema |

| 可读 (READ-only) | 用途 |
|---|---|
| `mart_index_daily` | HS300 / SH50 / 中证500 OHLCV — regime / volatility / breadth 基础 |
| `fact_stock_kline_daily` | 全市场个股 K 线 — breadth 涨跌家数 / 涨停统计 / 振幅 |
| `fact_lhb_event` | 龙虎榜事件 — 游资 / 一线游资活跃度 → 情绪温度 |
| `mart_data_source_watermark` | 数据新鲜度 — 避免读到 stale 数据 |
| `dim_trading_calendar` | 交易日历 — PIT 守门 |
| `fact_index_member` (如存在) | 指数成份 — 板块归类 |

| 可创建 (新表) | 命名约束 |
|---|---|
| `mart_market_perception_daily` | 日级 snapshot 主表 |
| `mart_market_perception_*` | 子引擎 daily 输出 (理论上每 engine 一张) |
| `mart_market_perception_audit_log` | 引擎运行日志 |

---

## 3. 已 stake 的入口 (Claude 占位)

### 3.1 前端入口

**文件**: `design/v3-page-market-perception.jsx` (79 行, stub)

- Tab 名: "市场感知" (label) / "Market" (sub)
- Tab id: `market` (在 `design/Chunky Monkey v3.html` 第 74 行附近 TABS 数组)
- 注册位置: `window.CMV3.PageMarketPerception`
- 入口路由 (App.jsx 内): `{tab==='market' && <PageMarketPerception/>}`
- 占位字段: regime_score / breadth_state / volatility_state / sentiment_phase
- 数据 fetch: `/api/v3/market_perception/snapshot`

**Codex 扩展点**:
1. 增加子 tab (情绪 / 广度 / 主题 / 资金 / 风格 / 龙头 / 拥挤 — 对应 7 engines)
2. 时序图组件 (近 90 日 regime_score / breadth / volatility) — 可复用已有 v3-ui.jsx 内 chart 组件
3. 主题生命周期看板 (主线 / 支线 / 退潮)
4. 单独 stock context drawer (复用 v3-drawer-stock.jsx 模式)

### 3.2 后端入口

**文件**: `backend/routers/v3_market_perception.py` (113 行, stub)

- 注册位置: `backend/main.py` 第 164 行附近
- Prefix: `/api/v3/market_perception`
- 现有 endpoints:
  - `GET /snapshot` — 最新 1 行 (stub fallback / 真实从 mart_market_perception_daily)
  - `GET /history?days=90` — 时序 (stub, 待实施)
  - `GET /health` — 7 engine status (stub_only / spec_only)

**Codex 扩展点**:
1. 实施 service: `backend/services/market_perception/` (新建独立 package)
2. 实施 build script: `backend/scripts/build_market_perception_daily.py`
3. router 加更多 endpoint (per-engine breakdown / themes / fund_flow / etc)

### 3.3 数据层入口

**新表 DDL 模板** (Codex 实施时按需精确化):

```sql
CREATE TABLE IF NOT EXISTS mart_market_perception_daily (
    snapshot_date     DATE NOT NULL,
    regime_score      DOUBLE,           -- -1.0 ~ +1.0 (risk_off → risk_on)
    breadth_state     VARCHAR,          -- 健康扩散 / 分化 / 杀跌
    volatility_state  VARCHAR,          -- low / normal / high / extreme
    sentiment_phase   VARCHAR,          -- init / spread / climax / fade
    -- 数据来源标识
    n_obs_days        INTEGER,          -- 计算用了多少历史交易日
    source_engines    VARCHAR,          -- JSON [{engine, score, weight}, ...]
    -- PIT 守门
    pit_cutoff_date   DATE NOT NULL,    -- = snapshot_date 当日 close 之后
    built_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_mmp_date ON mart_market_perception_daily(snapshot_date);
```

---

## 4. MVP scope (Codex 第一阶段 — MarketRegimeEngine)

7 engines 全做 4-8 week. 第一阶段只做 1 engine: **MarketRegimeEngine** (情绪温度计).

### 4.1 输入

| 特征 | 来源 | 计算 |
|---|---|---|
| HS300 60d 累计收益 | `mart_index_daily` (`hs300`) | `close[t] / close[t-60] - 1` |
| HS300 20d realized volatility | `mart_index_daily` | `std(log_ret_1d, window=20) * sqrt(252)` |
| 全市场涨跌家数 | `fact_stock_kline_daily` | 每日 COUNT(WHERE pct_change > 0) / COUNT(*) |
| 涨停股数 | `fact_stock_kline_daily` | 每日 COUNT(WHERE pct_change > 9.5) (科创板 19.5) |
| 龙虎榜事件密度 | `fact_lhb_event` | 每日 COUNT(*) |

### 4.2 输出 (4 个 features)

| Feature | 值域 | 解释 |
|---|---|---|
| `regime_score` | [-1.0, +1.0] | 综合得分 (risk_off → risk_on); 加权融合 HS300 60d 趋势 + 波动率 + breadth |
| `breadth_state` | enum | "杀跌" (上涨家数 < 30%) / "分化" (30-60%) / "健康扩散" (> 60%) |
| `volatility_state` | enum | "low" (< 15% 年化) / "normal" / "high" (25-40%) / "extreme" (> 40%) |
| `sentiment_phase` | enum | "init" / "spread" / "climax" / "fade" — 基于 60d 趋势导数 + breadth 拐点 |

### 4.3 PIT 严格

**时刻 t 的 snapshot 只能用 close 价已发生的数据**:
- `snapshot_date = T` 必须 `T < today` AND `T` 是 `dim_trading_calendar.is_trading = 1`
- breadth 涨跌家数 用 T 当天 close (盘后可用)
- 龙虎榜 `fact_lhb_event.trade_date <= T` 且 `built_at <= T_after_market_close`
- 写入前用 `services.optimization.governance.enforce_pre_insert` 守门 (拒 `sharpe > 5` / `win_rate > 0.95` 等绝对异常)

### 4.4 单测要求

| 测试 | Mock 数据 | Assert |
|---|---|---|
| `test_regime_score_risk_on` | HS300 60d +20% / vol 12% / breadth 70% | `regime_score > 0.3` |
| `test_regime_score_risk_off` | HS300 60d -15% / vol 35% / breadth 25% | `regime_score < -0.3` |
| `test_pit_strict_today_excluded` | input `snapshot_date = today` | raise ValueError 或 skip |
| `test_no_lookahead` | `fact_lhb_event.built_at > snapshot_date` 必须被排除 | breadth 计算不含未来 |

测试位置: `backend/tests/services/market_perception/test_regime_engine.py`

### 4.5 验证

跑完 MVP 后, Codex 必须执行:

```bash
# 1. 全量 backfill 1.5 年 (2024-11 ~ 2026-05-19)
PYTHONPATH=backend python backend/scripts/build_market_perception_daily.py \
    --start 2024-11-01 --end 2026-05-19

# 2. 验证 row count = 交易日数 (跨 ~370 天)
PYTHONPATH=backend python -c "
import duckdb
con = duckdb.connect('data/smartmoney.duckdb', read_only=True)
r = con.execute('SELECT COUNT(*), MIN(snapshot_date), MAX(snapshot_date) FROM mart_market_perception_daily').fetchone()
print(r)
"

# 3. 通过前端 tab "市场感知" 看 UI 真数据 (非 stub)
# 启动 backend: cd backend && uvicorn main:app --reload --port 8000
# 浏览器开 http://localhost:8000/v3/Chunky%20Monkey%20v3.html
```

预期: snapshot_date 跨 ~370 trading days, regime_score 跨度 [-0.8, +0.7] 之间 (2024 年小回调 → 2025 反弹).

---

## 5. 后续阶段 (本任务不实施, 仅 spec)

| 阶段 | Engine | ETA | 依赖 |
|---|---|---|---|
| P1 (本任务) | MarketRegimeEngine | 1 week | 上面 MVP |
| P2 | CrowdingRiskEngine (拥挤度) | 1 week | P1 done; READ mart_industry_returns_daily |
| P3 | ThemeLifecycleEngine | 2 week | tdxhub `stock_blocks` 主题归类 |
| P4 | FundFlowEngine | 1 week | READ `fact_capital_flow_*` |
| P5 | LeaderFollowerEngine | 2 week | READ `fact_index_member` |
| P6 | ChainDiffusionEngine | 2 week | 产业链上下游 graph 建模 |
| P7 | StockContextEngine | 1 week | 把以上 6 engine 输出聚合到 per-stock context |

---

## 6. 输出契约 (P1 完成时)

- [ ] 新 service: `backend/services/market_perception/__init__.py` + `regime_engine.py`
- [ ] 新 build script: `backend/scripts/build_market_perception_daily.py`
- [ ] 新表: `mart_market_perception_daily` (DDL 在 `backend/services/schema_marts.py` 或新文件)
- [ ] backfill: 2024-11 ~ 2026-05-19 (~370 行)
- [ ] router 扩展: `/snapshot` / `/history` 返真实数据 (不再是 stub)
- [ ] 前端 jsx: 拉到真数据后渲染时序图 (近 90 日 regime_score 线)
- [ ] 单测: ≥ 4 tests pass (含 PIT 守门 + risk_on/off + no_lookahead)
- [ ] commit message 含: `# PIT-strict` + 实测 backfill 数字 + leakage absence verdict

---

## 7. 禁忌 (Codex 不能做)

| 禁忌 | 反例 (踩过, 见 CLAUDE.md Rule 5/7) |
|---|---|
| 改 `mart_p0a_feature_label_panel_v4` schema | GCP retrain v2 在跑, 改 panel 触发模型 retrain 重启 |
| `try/except: pass` 静默吞错 | 用 `try/except Exception as e: logger.warning(...)` 显式记录 |
| 拍脑袋阈值 (e.g. "breadth > 0.6 算扩散") | 必须用历史 quantile 标定 (e.g. P75) + 写 evidence |
| In-sample fit `regime_score` 然后看 forward alpha | 必须 walk-forward / OOS 验; PIT 守门 |
| `# TODO: 以后改` 折中 PIT | Rule 7 真金白银 — PIT 类不容折中, 必须立刻测试验证 |
| sharpe > 5 / win > 0.95 / ann > 100% / RankIC > 0.3 | 立即怀疑 leakage, 不庆祝; absolute 红线触发必须 ablation 验证 |

---

## 8. 关联

- 设计源文档: `docs/market_regime_framework.md` (7 engines × 20 directions full design)
- 已 deprecated 早期方向: 用户 push back "先不用把市场研究并入主线" (2026-05-15 conversation history)
- 关联未实施 spec: `docs/block_trade_alpha_spec.md` (大宗交易判机构成本) — 可在 P3 之后接入 StockContextEngine

## 9. 集成方式 (本任务 NOT 实施, 仅说明)

未来 MarketRegimeEngine 输出会按以下方式被消费:

1. **Ensemble weight 调节**: 现行 `mart_ensemble_weights` 加入 `regime_modifier` 列
2. **Sniper / Institution alpha 门控**: regime_score < -0.5 时 sniper alpha 权重 × 0.5
3. **Paper_sim regime_gate**: 已有 `regime_state.py` 复用此 `regime_score` 替代 60d HS300 简陋判断

集成由后续任务负责, **不在本任务范围**.

---

## 10. 联系

- 问题反馈: 跟 Codex 用 `--resume` 接着追问
- 主 Claude session 不并发改此模块 (用户 push: "我单独开窗口让 Codex 做")
- 完成后通过 git commit + push 同步, Claude session 看到 commit 后接续后续阶段

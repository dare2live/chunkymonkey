# UI/UX 与人机交互优化 Plan (ChunkyMonkey, 2026-05-19)

> Plan agent a6ed1e1f, ~300 行, 不实施代码, 只 plan. 落盘以备后续 phased rollout 引用.

## 0. 背景与目标

用户 push (深夜): "结合数据 + 流程 + 用户使用习惯优化 UI 和人机交互, 模块化可复用可扩展".

本 plan 聚焦 **人机交互层** (UI / CLI / 报告 / 告警), 不重复 lineage spec (那是 aeb8ea53 agent 范围), 不实施代码, 只 plan. 设计前提:

- 单用户 (个人 retail A 股, 不做 SaaS / 多租户)
- 100 万 × 5 仓 long-only T+1, 月度调仓为主, 日内监控为辅
- 已有: FastAPI v3 路由 + `design/v3-*.jsx` React CDN 设计稿 + `daily_update.sh` 全自动 + JSON 报告 + 多个 CLI 脚本 (`model_monitor_dashboard.py` / `audit_live_dashboard.py`)
- 缺: 没有 "一页搞定" 的 daily decision dashboard; CLI 输出零散; 缺主动 alert (邮件/Slack/桌面 notification); v3 React 设计稿尚未串通真实 API/lineage; 关键决策 (retrain / promote / paper_sim) 没有 one-click 触发 + 进度回流

## 1. 用户 persona + 5 个核心日常 scenario

**Persona**: 个人量化操盘手 (单人项目 owner), 全天有正职, 量化是兼职决策. 不愿天天读 log, 愿意每天看 1 屏 + 周末 30 min review. 极度厌恶: 重复操作 / 自己找数据源 / 多窗口切换.

| Scenario | 时段 | 频次 | 用户 want |
|---|---|---|---|
| S1 早盘决策窗口 | 08:30-09:25 | 每交易日 | 1 屏看到: 今日 top-5 推荐 (signal_score / 行业 / 上次 RankIC) + 当前持仓 + 是否需要换仓 + 数据 sync 是否完整 |
| S2 盘中监控 | 10:00 / 14:00 | 每交易日 (手机) | 持仓盈亏一眼 + 触发止损/swap 时主动 push 告警, 不主动开 dashboard 不打扰 |
| S3 盘后复盘 | 16:00-17:00 | 每交易日 | 今日 paper_sim trade log + NAV 增量 + 今日预测 hit-rate + 与基准 HS300 差 |
| S4 周末回顾 | 周六/日 | 每周 | KPI 月度趋势 (ann_ret/sharpe/max_dd) + retrain 是否需触发 + 上周 model decay + leakage 红牌 |
| S5 月底治理 | 月底 1 天 | 每月 | sync gap audit / lineage / promote 决策 + 与 baseline (前 champion) 对比 + GCP cost 月结 |

设计目标: S1 必须 < 30 秒拿到决策; S2 必须 push 不 pull; S3-S5 1 click 触达; 所有 view 共享同一 component 库.

## 2. 现状盘点

### 2.1 已有 (可复用)

**FastAPI 路由** (mounted on `/api/...`):
- `/api/v3/paper/nav | holdings | kpis | signal-ic | pl-attr`
- `/api/v3/selection/log | history | summary | board | blended | weights`
- `/api/v3/portfolio/recommendations | buy-signals | backtest | profiles | factors`
- `/api/v3/picture/{stock} | trade-plan/{stock}`
- `/api/v3/view/stock | formula | institution`
- `/api/v3/meta/run-meta | health | fitness | selections`
- `/api/inst/update/all | status | smart | step/{id}`
- `/api/inst/lifeboat/run | status | report`
- `/api/data_sources/*` — health / cache / dag plan / portfolio_backtest / data_audit
- `/api/workbench/overview | research | champion | data-sources | pipelines | features | storage | recommendations`
- `/api/rec/daily-topk | model-performance | model-comparison | model-history`

**前端**:
- `index.html` (588 行) — 旧 vanilla, Phase ζ 退役, redirect 到 v3
- `design/Chunky Monkey v3.html` (143 行) + 16 个 `v3-*.jsx` React CDN 设计稿 (zero toolchain). 含: today / lab / portfolio / portfolio-builder / stock-view / formula-view / institution-view / admin / research / data-live
- `design/tokens.css` — 设计 token (颜色/字体)

**CLI / 脚本**:
- `scripts/daily_update.sh` — 8 步全流程 (preflight → sync → panel → retrain → paper_sim → gate → promote → report)
- `backend/scripts/model_monitor_dashboard.py` — 模型监控台. 已支持 `--json`
- `backend/scripts/audit_live_dashboard.py` — 实时 audit dashboard
- `backend/scripts/gen_report.py` / `p0b_final_report.py` — 报告生成
- `scripts/session_status.sh` / `agents_status.sh` / `codex_monitor.sh` — agent 状态
- `scripts/monitor_phase5_gcp_retrain.sh` / `watch_phase5_retrain_and_post.sh` — retrain 监控
- `scripts/gcp_stability_retrain.sh` — GCP controlled-use stability retrain 手动触发

**Report artifacts** (`data/reports/`):
- `daily_YYYYMMDD.json` / `msaf_ensemble_YYYYMMDD.json` / `phase4_gate_result.json` / `gcp_cost_summary.json`

### 2.2 Gap (缺什么)

| Gap | 后果 | 优先级 |
|---|---|---|
| 没有 "一页 daily decision" view (Today 页虽存在但未串真实 API + 缺持仓状态合并) | S1 用户要打开 N 个 endpoint / 跑 N 个 CLI | P0 |
| 没有主动 push 告警 (邮件 / 桌面 notification / Slack) | S2 用户必须手动开 dashboard | P0 |
| KPI 月度时序无可视化 (只有 JSON snapshot) | S4 看不到 trend | P1 |
| `daily_YYYYMMDD.json` 没有人类可读 markdown / HTML 渲染 | S3 复盘需要解析 JSON | P0 |
| Retrain 进度无实时回流 UI | S4-S5 手动触发后不知道进度 | P1 |
| 数据 sync gap 状态无红/黄/绿 status badge | S5 audit 必须跑 CLI | P1 |
| Leakage 警报 (RankIC>0.3 / sharpe>5) 无 dashboard surface | S4 leakage 风险后置发现 | P1 |
| v3 React 设计稿大量 mock data, 真 API wiring 不全 | S1-S5 用户看到 mock 数字误判 | P0 |
| 模块未分层 (UI 组件 = 数据 fetch + 业务逻辑 + 渲染 三合一) | 重构 / 测试 / 复用 难 | P1 |

## 3. UI/UX 优化方向 (按优先级)

### O1 [P0] Today 一屏决策页串通真实 API + Markdown 渲染
串 `/api/v3/selection/board` + `/api/v3/paper/holdings` + `/api/v3/paper/nav` + `/api/inst/update/status` + `/api/data_sources/health` + `data/reports/daily_*.json`. 1 屏含 4 区: 今日 top-5 推荐 | 当前持仓 | 数据 sync status | 今日 KPI. 同时提供 `daily_<DATE>.md` 输出邮件友好.

### O2 [P0] Push 告警 channel
Trigger: 早 09:00 推 Today 摘要 / 盘中触发止损/swap / 数据 sync gap > 3d / leakage 红牌 / GCP budget > 80%. 实现: `backend/services/notification/` 模块 (3 driver: email / macos-osascript / slack-webhook). 模板化 `configs/notification/*.yaml`.

### O3 [P0] CLI 增强 (rich + interactive prompt + 单一 entry)
`python -m chunkymonkey` (或 `bash scripts/cm.sh`), 子命令: `cm today` / `cm holdings` / `cm kpi --period 30d` / `cm sync status` / `cm retrain --dry` / `cm promote --list-candidates`. 复用现有 dashboard CLI 内部逻辑.

### O4 [P1] KPI 月度时序可视化 (Streamlit MVP)
`frontend/streamlit_app.py`, 1 click 启动. 月度 ann_ret / sharpe / max_dd / win_rate 折线 + champion 切换 marker + HS300 baseline overlay. 数据源: `mart_p0b_walkforward_eval` + `mart_p3_acceptance_result` + JSON reports.

### O5 [P1] Retrain trigger UI (one-button + 实时进度 + 估时)
新增 `POST /api/v3/retrain/launch` + `GET /api/v3/retrain/status`. UI: progress bar (n_trials_done / total + ETA), 完成自动跑 P3 acceptance + 弹 promote 候选 dialog.

### O6 [P1] 数据 sync gap status badge + lineage 入口
v3 全局 nav bar 永久 status badge (绿/黄/红). 点击弹 drawer: 列每个 source watermark + 滞后天数 + 上次 sync exit code + 1 click "resync this source" 按钮. 单一 link `/v3/lineage` 入 aeb8ea53 agent 的 lineage page.

### O7 [P1] Leakage 警报 dashboard
Top nav 永久 "Risk" 红牌, 列最近 7 天 `fact_optuna_governance_log` reject + RankIC>0.3 / sharpe>5 hit. 关联 [[feedback_leakage_red_flag]]. 1 click "ablation drilldown" → 跑 phase4 gate.

## 4. 人机交互模式选择

| Option | 优点 | 缺点 | 适合 |
|---|---|---|---|
| A. Markdown 邮件/桌面 | 0 维护, 主动 push, 离线可读 | 不可交互 | S1 早 09:00 摘要 + S2 告警 |
| B. FastAPI + React (v3 已有) | 已 90% in-place, 可交互 | mock data 多, wiring 工作量 | S1-S5 主交互, S3/S4 drilldown |
| C. CLI + rich | 极快, ssh 友好, 0 依赖 | 单人用, 图表弱 | S3-S5 自动化 + power-user |
| D. Streamlit/Notebook | 1 文件出 chart | 不适合长期 monitoring | S4-S5 周末 review |
| E. Grafana + DuckDB datasource | 监控行业事实, alert 强 | 配置重, DuckDB plugin 不成熟 | 远期 P3 |

**推荐组合**: **B (React) + A (邮件) + C (CLI rich)** 主线, **D (Streamlit)** 周末 KPI 时序辅, **E (Grafana)** 远期.

理由:
- B 已有 v3 React CDN 设计稿, 沉没成本不浪费, 串真实 API 即可上
- A 0 维护, 解决 S2 push 痛点, 不用守 dashboard
- C 自动化场景 (cron / launchd / Codex 协作) 用得最多, rich 表格远胜 print
- D 周末 review 一次性, 不需常驻
- E 远期, 现阶段 ROI 不够

## 5. 模块化设计 (UI 层 + service 层)

### 5.1 分层

```
frontend/
  v3/                              ← React CDN (已有 design/v3-*.jsx 演进)
    components/                    ← 通用组件 (KPITable / StatusBadge / TrendChart / AlertBanner)
    pages/                         ← 页面 (Today / Portfolio / Lab / StockView ...)
    data/
      api_client.js                ← 唯一 fetch wrapper (含错误处理 / cache / retry)
      hooks/                       ← useToday / useHoldings / useKPI ... (React hook)
  streamlit/                       ← Streamlit KPI trend
  cli/                             ← cm.sh + python -m chunkymonkey
    commands/                      ← today / kpi / sync / retrain / promote ...
    renderers/                     ← rich_table / markdown_email

backend/
  routers/                         ← 已有, 不改 path, 仅补 v3/retrain
  services/
    notification/                  ← 新加 (email / macos / slack driver)
    ui_aggregator/                 ← 新加 (1 layer 聚合多 service 给 Today 页, 防 N+1)
    business_logic/                ← KPI compute 复用 (抽出 KPI 计算到独立模块)
```

### 5.2 组件级复用

| Component | 复用页面 |
|---|---|
| `<KPITable>` | Today / Portfolio / Lab / Streamlit |
| `<StatusBadge color={red/yellow/green}>` | Today nav / Sync drawer / Risk dashboard |
| `<TrendChart series>` | KPI 月度 / OOS rank_ic 时序 / NAV 曲线 |
| `<AlertBanner severity>` | Today 顶部 / 邮件 markdown / CLI rich panel |
| `<DrilldownDrawer>` | Stock view / Inst view / Formula view |

CSS token 已在 `design/tokens.css` 统一, 继续走.

### 5.3 数据层 (data_layer / business_logic / view 三分)

- **data_layer** (`backend/services/duck_adapter.py`, 强制走): DuckDB query, `read_only=True`, 不写
- **business_logic** (新抽 `backend/services/business_facts.py` + `analytics.py`): KPI compute / regime verdict / alpha decay 检测. 可被 CLI / FastAPI / Streamlit 共用
- **view** (`backend/routers/` + `frontend/`): 只做 serialize + render

防 N+1 query: 用 `ui_aggregator` service 一次 query 聚合 (audit_n_plus_one.py 已 deploy lint).

## 6. 数据 + 流程整合 (page → API → service → DB)

### Today 页 flow

```
User opens /v3/today
  → React Today
    → GET /api/v3/selection/board → ui_aggregator.today_board → mart_p0b_oos_predictions
    → GET /api/v3/paper/holdings → paper_engine.holdings → paper_sim_holdings
    → GET /api/v3/paper/nav → paper_engine.nav → paper_sim_nav
    → GET /api/inst/update/status → updater.status → source_watermarks
    → GET /api/data_sources/health → data_sources.health → source_watermarks
```

### Retrain trigger flow

```
User clicks 'retrain'
  → POST /api/v3/retrain/launch
    → backend/services/ml_lifecycle/retrain_job
      → nohup retrain_lambdamart_v6.py → /tmp/retrain_<date>.log
  → GET /api/v3/retrain/status (polls every 10s)
    → React progress bar
  → On done: AlertBanner + email push via O2
```

### 数据 sync gap alert flow

```
cron daily_update.sh
  → update_watermark_sla.py
    → data/audit/watermark_sla_YYYYMMDD.json
      → StatusBadge in v3 nav (color = green/yellow/red)
      → if any source > 3d: notification.send(email + macos)
```

## 7. 实施 ETA + Phase

| Phase | ETA | 内容 | 验收 |
|---|---|---|---|
| P0a | 1 day | gen_report.py 加 markdown renderer + 邮件 driver + macOS osascript | cron 17:00 后邮箱收到 daily_<DATE>.md + 桌面通知 |
| P0b | 2 day | Today 页串真实 API (替换 v3 mock) + 顶部 StatusBadge + 持仓合并 | 浏览器 /v3/today < 30s 拿到决策, mock 全去除 |
| P0c | 1 day | cm CLI 单一 entry + today/holdings/kpi/sync 4 子命令 | `cm today` 输出表格 = Today 页内容 |
| P1a | 3 day | Streamlit KPI 月度 trend + champion marker | 周末 streamlit run 一键启 |
| P1b | 3 day | Retrain trigger UI (1 button + progress + estimate) | UI 点 retrain, log 实时回流, promote 弹窗 |
| P1c | 2 day | Sync gap drawer + 1-click resync per source | 红色 source 1 click resync 不离开 UI |
| P2a | 1 week | Leakage 警报 dashboard + ablation drilldown | RankIC>0.3 立即红牌, 1 click 跑 phase4 gate |
| P2b | 1 week | lineage view 入口 wiring (aeb8ea53 agent 落地后) | status badge 跳 lineage page |
| P3 | 2-3 week | Grafana 远期 (DuckDB → Parquet → plugin) | 长期 monitoring + alert rule 迁出 cron |

## 8. 跟现有 codegraph + audit infra 协同

| Audit hook | 用法 |
|---|---|
| `audit_n_plus_one.py` | 任何新 router / aggregator 必跑, 防 Today 页串 6 API 各发 N 次 query |
| `codegraph-architecture-audit` skill | P0b / P1b / P2a 大改 PR (>20 files) 跑 |
| `pit-audit` skill | 涉及 paper_sim / NAV / KPI 显示的 endpoint 必跑 |
| `post-fix-audit` skill | 任何 fix Today/sync/KPI 后强制 5 步 |
| `parallel-grid-runner` skill | retrain UI launch 后台跑时复用其 DuckDB single-writer 防 lock 经验 |
| `data-integrity-audit` skill | sync gap drawer 触发 1-click resync 时强制走 |
| `update-config` skill | 加 launchd 邮件 cron 时用 |

## 9. 模块化 + 可扩展 self-check

| 检查项 | 措施 |
|---|---|
| 不写死参数 | 邮件收件人 / Slack webhook / 告警阈值 全走 `configs/notification/*.yaml` |
| driver 可插拔 | notification 走 3 driver pattern, 加新 channel (微信/钉钉) 改 yaml + 加 1 driver |
| 复用现有 | KPI compute 走 `business_facts.py` + `analytics.py` |
| 测试 | pytest backend; CLI snapshot test; React 暂不写单测 (CDN 工具链成本高) |
| PROJECT_INDEX 同步 | 新加 service / route / yaml / launchd plist 都进 INDEX (pre-commit hook 强制) |
| rule-compliance | 任何告警阈值数字必须 yaml-back + `# evidence:` 注释 |

## 10. Risk + Open question

| Risk | 缓解 |
|---|---|
| v3 React CDN 无 build, 调试体验差 | 仍走 CDN, 调试用浏览器 devtools + 加 `?debug=1` toggle mock/live |
| 邮件 push 被垃圾过滤 | 本地 SMTP relay (gmail app password) + 主题加 `[CM-Daily-YYYYMMDD]` prefix |
| Retrain UI 触发后浏览器关掉 | 后台 nohup, status 走 DB / file marker (`data/reports/retrain_*.marker`), UI 重连 poll |
| Streamlit 启动慢 (5-10s) | 不做常驻, 周末手动启 |
| 大量 mock data 替换风险污染 paper_sim | 先 read-only 串 GET, write 操作 (preset/save) 后置 P2 |

**Open question** (需用户确认):
- Q1: notification channel 优先级? (邮件 vs macos vs 微信/Slack)
- Q2: 是否接受 Streamlit 作为 KPI trend 主面板 (vs 全部嵌 React Today 页)?
- Q3: lineage page 由 aeb8ea53 agent 落地后, 入口 URL 约定? (`/v3/lineage` 还是 `/v3/admin/lineage`)
- Q4: GCP cost 月结是否纳入 Today 页 status badge (vs 单独 admin 页)?

## Critical Files for Implementation

- `backend/main.py` (router mount, 新增 retrain/notification router)
- `design/v3-page-today.jsx` (Today 页串真实 API)
- `design/v3-data-live.jsx` (mock → live data wiring 中心)
- `backend/scripts/gen_report.py` (markdown renderer + 邮件输出)
- `scripts/daily_update.sh` (Step 8 接 notification driver)

---

Plan agent: a6ed1e1f123e0b41f (Claude Plan).
不实施代码, ETA Phase 0 ~4 day, Phase 1 ~8 day, Phase 2 ~2 week, Phase 3 ~2-3 week.

# 市场感知数据接入实施 Spec — 2026-05-29

> 由 workflow `market-perception-data-onboarding`(7 agent / 548K tokens / 19min)生成。
> 6 个调研 agent 实测 akshare/tushare 接口 + 读项目 DB, 综合 agent 出实施方案。
> 配套文档: market_perception_data_requirements_20260529.md(需求评估) + market_perception_optimization_plan_20260529.md(优化方案)

---

# 市场感知数据接入实施 Spec

> 综合 6 个数据接入点调研, 给开发直接照做. 所有 DB 状态/接口/文件路径已对照代码库实测核验 (2026-05-29).
> 核验命令样例: `duckdb data/smartmoney.duckdb (read_only)` + `grep daily_update.sh` + `ls backend/services/`.

---

## 1. 执行摘要 (先行结论)

**核心判断**: 这 6 个接入点不是 6 个独立问题, 而是**"每日 PIT 快照落库纪律"一个命门的多个实例 + 1 个已被项目更优方案取代的伪需求**。标杆模式 (`dim_stock_tdx_industry_history` + `daily_update.sh` Step 2j) 已存在并验证, 正确做法是**推广标杆 + 加监控**, 不是各写各的。

**实测三句话结论**:
1. **真正紧迫 (P0, 不可逆)**: `attention` 断更 14 天 (停在 2026-05-15)、`profit_forecast` 只跑过 1 次 (1 个 snapshot)、`tdx_block` 概念层每日 DELETE-ALL 销毁历史。这三者**接口都活着、sync 代码都现成**, 纯属"有 schema 没积累"。append-only 数据每拖一天永久丢一天, 必须立即接 `daily_update.sh` + 加 watermark 监控防再次静默断更。
2. **免费且干净 (P1-P2)**: 融资融券 (akshare, 免费, **14 年真 PIT 历史可 backfill**) 是少见的"既有历史又干净"源, 填项目杠杆资金空白; 概念主题层 (东财 datacenter-web REST / 同花顺) 是用户确认的"真 alpha 所在层", 但纯 current snapshot, T+0 起步需累积数月才可验证。
3. **不做 (伪需求)**: AIF10 4 表 — `raw_aif10_peer_valuation` 100% NULL 空壳, `raw_aif10_valuation_quantile` 无 date 列接历史 = leakage; 两个目标因子 (估值分位/景气度) 项目已用 PIT-safe 的 `pe_ttm_z_1y`/`fact_financial_pit_daily.profit_yoy` 实现, 复用即可。

**PIT 红线 (真金白银)**: 概念/覆盖/关注度成分全是 current snapshot, 用今天成分回测历史 = survivorship + look-ahead leakage (项目 2026-05-15 inst_path_a 同款坑)。所有快照表强制 `source_available_date = fetch 当天`, JOIN 永远 `AND source_available_date <= signal_date`, **严禁回填历史日期**。

---

## 2. 六接入点汇总表

| # | 数据源 | 接口状态 (实测) | 历史可得性 | 成本 | 工作量 | 优先级 | 推荐 |
|---|---|---|---|---|---|---|---|
| 1 | 东财概念板块 (datacenter-web REST / 同花顺 THS) | 半可用: akshare `_em`/push2 本环境 100% 被代理墙 (HTTP 000); **datacenter-web REST 可用 (89981 行股-板对)**; THS 名录可用 (375 概念) | 纯 current snapshot, 无历史成员. T+0 起步 | 免费 | 2.5-4 天 | **P1 接入 / P2 消费** | 推荐, 走 datacenter-web 不走 `_em` |
| 2 | 同花顺概念 (tushare ths_index/ths_member) | 不可用: tushare 未装 + 需付费 6000 积分 (~600元/年) + 无 token | `ths_member` 的 in_date/out_date **官方标"暂无"= 无 PIT**; 仅 `dc_member` (东财) 可按 trade_date 回溯 (同价) | 付费 600元/年 | 1-1.5 天 (买了之后) | **P3 缓议** | 不推荐现在买 — 600元买不到"历史 PIT", 只买到"质量更高" |
| 3 | 融资融券 RZRQ (akshare) | **实测可用** (akshare 1.18.58 已装): detail_sse/szse + account_info 全 PASS, 仅 retry 包装 | **优秀: 可 backfill 到 2010-03-31 (14 年)**, 自带"信用交易日期"= 天然 PIT-safe | 免费 | 1.5-2 天 (+backfill IO) | **P2** | 推荐, 但先 IC 实证 (偏风控因子) |
| 4 | 每日快照纪律 (attention / profit_forecast / tdx_block) | **实测全活**: attention 5185行~27s, profit_forecast --dry 2374行真返 EPS, tdx_block tdxhub .dat 可用 | append-only, 历史补不回. attention 断14天/forecast 1天/block 0历史 | 免费 | 1-1.5 天 | **P0 (接线+SLA) / P1 (block改造)** | 强烈推荐立即做 |
| 5 | AIF10 同业估值+景气度 (4 表) | **不可用**: `raw_aif10_peer_valuation` 估值字段 **0 非空 (空壳)**; `profit_yoy` 0% 填充; quantile/consensus 表无 date 列 | 空壳/单快照/无日期. 替代源 `fact_financial_pit_daily` 有 2023→2026 真 PIT | 免费 | 估值 0天 / 景气度 1-1.5天 / 接AIF10=不做 | **N/A (已完成) / P2 (景气度 forward-fill)** | **不接 AIF10 表** — 复用 `pe_ttm_z_1y` + `fact_financial_pit_daily.profit_yoy` |
| 6A | 自建产业链图谱 (供应链 + 分析师覆盖) | 供应链免费接口**不存在** (akshare/tushare 都无); **分析师研报可用** `stock_research_report_em` (2017→今, 单股 759 行) | 覆盖边可回填 2017+; 真供应链无免费历史 | 免费(覆盖边) / 付费或爬年报(供应链) | 覆盖边 ~3天 / 真供应链 2-3周 | **A-覆盖边 P1 / A-真供应链 P3** | 覆盖边推荐; 真供应链/Neo4j 暂不推荐 (DuckDB 关系表足够) |
| 6B | 舆情 NLP (热度/情绪/文本) | 热度可用 (hot_rank_em/hot_follow_xq/comment_scrd); **原始文本受限** (news_em 仅最新10条无历史); 多接口不稳 (JSONError/超时/ProxyError) | 热度=current snapshot; 个股文本历史攒不回; news_cctv 可回填(宏观) | 免费(akshare) / 自爬(雪球股吧) / 开源NLP | Phase1攒数据 2-3天 / Phase2 NLP 3-5天 | **B-Phase1 P0 / B-Phase2 P2** | Phase1 强烈推荐立即攒原始数据 (不可逆) |

**已核验 DB 实测值** (与各调研 JSON 完全一致):

| 表 | 实测 (rows / dates / max_date) | 结论 |
|---|---|---|
| `dim_stock_tdx_industry_history` | 50489 / 9 / 2026-05-26 | 标杆, 在 Step 2j 累积中 |
| `fact_stock_attention_snapshot` | 68877 / 13 / **2026-05-15** | 断更 14 天, 不在 daily_update |
| `raw_profit_forecast_snapshot_daily` | 2374 / **1** / 2026-05-17 | launchd 未 load, 等于没历史 |
| `dim_stock_tdx_block` | 8813 / 无 snapshot_date | DELETE-ALL 覆盖, 0 历史 |
| `raw_aif10_peer_valuation` (估值非空) | **0** | 空壳, 接它=接空气 |
| `raw_aif10_valuation_quantile` | 93011 / 无 date 列 | latest-snapshot, 接历史=leakage |
| `fact_financial_pit_daily` (profit_yoy 填充) | 3.69M / 8.1% | 真 PIT 替代源, 待 forward-fill |
| `mart_p0a_feature_label_panel_v3` (pe_ttm_z_1y) | 4.24M / 2.77M 填充 | 估值分位**已完成** |
| margin / rzrq 表 | **0** | net-new, 真空白 |

---

## 3. 按优先级实施步骤 (P0 → P3)

> **统一前置约定** (所有步骤适用):
> - DB 连接走 `services.duck_adapter.connect`, **不裸 `duckdb.connect`** (项目宪法 / DuckDB 单 writer).
> - 所有快照表必带 `snapshot_date` (或 trade_date) + `source_available_date` + `source_raw_hash` (sha256 去重) + `fetched_at`。
> - 写入幂等: `INSERT OR REPLACE` (可变快照) 或 `INSERT OR IGNORE` (immutable), 不用裸 INSERT。
> - `daily_update.sh` 新 step 全部 **WARN-only 不 fatal** (akshare 不稳, 失败不阻主 pipeline), 紧跟现有 Step 2i/2j 同款 heredoc + DRY=0 守门。
> - 落库模板: `backend/services/tdx_industry_client.py:298-387` (CREATE history → INSERT OR REPLACE → as-of 查询)。

---

### P0-1 — attention 快照接线 (零新代码)

| 项 | 内容 |
|---|---|
| **做什么** | `fact_stock_attention_snapshot` sync 函数现成 (`services/external_attention.py::sync_external_attention_snapshot`, 已封装成 `routers/updater_calc.py::_step_build_external_attention`), 但从未在 daily_update 跑 → 断更 14 天。加一行调用即可。 |
| **落库表** | 已存在, PK=(snapshot_date, stock_code), 含 comment_*/survey_* 关注度指标。**不改 schema**。 |
| **PIT 保证** | sync 内部 `_latest_closed()` 算交易日当 snapshot_date, `source_available_date=snapshot_date` (写盘当日可用); 消费方 label 须 embargo ≥1 天 (T+1)。 |
| **工作量** | 0.5h |

**命令** (插入 `daily_update.sh` Step 2j 之后, 新增 Step 2k):
```bash
log "--- Step 2k: external_attention snapshot (累积 PIT 关注度) ---"
PYTHONPATH=backend python - <<'PYEOF' >> "$LOG" 2>&1 || log "WARN: attention sync 失败"
from services.duck_adapter import connect as duck_connect
from services.external_attention import sync_external_attention_snapshot
conn = duck_connect("data/smartmoney.duckdb")
try:
    n = sync_external_attention_snapshot(conn)
    r = conn.execute("SELECT COUNT(DISTINCT snapshot_date), MAX(snapshot_date) FROM fact_stock_attention_snapshot").fetchone()
    print(f"attention: rows={n} history_dates={r[0]} latest={r[1]}")
finally:
    conn.close()
PYEOF
```

---

### P0-2 — profit_forecast 快照接线 (零新代码) + 删 orphan plist

| 项 | 内容 |
|---|---|
| **做什么** | `backend/scripts/ingest_profit_forecast_snapshot.py` 现成可跑 (--dry-run 实测 2374 行真返 EPS), 但 launchd plist **NOT loaded** (实测 `launchctl list` 空) → 只跑过 1 次。归口到 daily_update。 |
| **落库表** | `raw_profit_forecast_snapshot_daily`, PK=(snapshot_date, stock_code, source), immutable。 |
| **PIT 保证** | `INSERT OR IGNORE`, 同日重跑 skip。`as_of_date='latest'` 是诚实占位 (akshare 不暴露真 PIT), 消费方按 snapshot_date 当 PIT key。 |
| **清理** | 删 orphan `backend/scripts/launchd/com.chunkymonkey.forecast_eps.plist` (避免双跑/混乱, 已确认未 load 且现归口 daily_update)。 |
| **工作量** | 0.5h |

**命令** (新增 Step 2l):
```bash
log "--- Step 2l: profit_forecast EPS snapshot (immutable PIT) ---"
PYTHONPATH=backend python backend/scripts/ingest_profit_forecast_snapshot.py >> "$LOG" 2>&1 || log "WARN: profit_forecast sync 失败"
```

---

### P0-3 — watermark SLA + data_audit 注册 (防再次静默断更)

| 项 | 内容 |
|---|---|
| **做什么** | **配套必做, 否则接了也会再次断更** (反例: attention 断 14 天没人发现, 因不在 SLA 监控, 见 memory `feedback-data-sync-silent-failure`)。把新接入的快照表加进 watermark SLA + data_audit 新鲜度检查。 |
| **改文件** | `backend/config/data_audit_rules.yaml` 的 `smartmoney_freshness.tables` (line 24-42 现成 list) 直接追加; `backend/scripts/update_watermark_sla.py` 注册新表。 |
| **PIT 保证** | N/A (监控层)。 |
| **工作量** | 0.5 天 |

**改 `data_audit_rules.yaml`** (照现有 6 条 entry 格式追加):
```yaml
smartmoney_freshness:
  tables:
    # ... 现有 6 条 ...
    - table: fact_stock_attention_snapshot
      date_column: snapshot_date
      max_lag_days: 3          # akshare 不稳, 放宽到 3
    - table: raw_profit_forecast_snapshot_daily
      date_column: snapshot_date
      max_lag_days: 3
```
> tdx_block_history / margin / 概念表落地后同样追加 (见对应步骤)。

---

### P1-1 — tdx_block 概念层 PIT 改造 (唯一要写代码, ~0.5 天)

| 项 | 内容 |
|---|---|
| **做什么** | 现 `sync_tdx_blocks` 是 DELETE-ALL 覆盖、无 snapshot_date → **概念成分 (gn 4436 行) 历史每日被物理销毁**, 而用户明确"真 alpha 在概念主题层"。新建 history 表 append, current 表保留给 UI (类比 `dim_active_a_stock` 定位)。**这是修一个 active leakage 风险, 不是接线**。 |
| **落库表 (新)** | `dim_stock_tdx_block_history` (stock_code, block_category, block_name, snapshot_date, source_raw_hash, source_available_date, fetched_at), **PK=(stock_code, block_category, block_name, snapshot_date)** — 完全仿 `tdx_industry_history`。 |
| **改 4 处** | (1) `block_client.py` 加 history 表 + sync 末尾 `INSERT OR REPLACE` 到 history (用 latest_closed snapshot_date), current 表保持 DELETE-ALL 兼容 UI; (2) 新增 router step `_step_sync_tdx_block` (仿 `_step_build_external_attention`, 传 active_codes 复用 industry sync 同源逻辑); (3) `daily_update.sh` 加 Step 2m 调用; (4) `data_audit_rules.yaml` 注册 history 表。 |
| **PIT 保证** | **current 表只做 code→block 映射, 不用于回测**; 回测只读 history 表 `WHERE snapshot_date <= t`; 绝不动 DELETE-ALL 兼容路径 (UI 依赖)。 |
| **工作量** | 0.5 天 (模式完全照搬 `tdx_industry_history`, 风险低) |
| **Grill 点** | current 表要不要保留值得 grill: 若 UI 只需 code→concept 映射 → 保留 (类比 dim_active_a_stock); 若回测要用 → 必须只读 history 带 snapshot_date<=t。 |

---

### P1-2 — 东财概念板块接入 (datacenter-web REST, 2.5-4 天)

| 项 | 内容 |
|---|---|
| **做什么** | 现有 TDX 仅 22 粗概念 + 无历史; 东财 300+ 细粒度热点概念 (华为昇腾/钠离子电池/CAR-T) 正是 alpha 目标层。**数据源选 datacenter-web REST (本环境实测可用 89981 行), 不用 akshare `_em`** (push2 本环境 100% 被代理墙)。作为**新增补充源, 不替换 TDX block** (TDX 走 tdxhub 100% 可信)。 |
| **数据源** | `curl https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_CORETHEME_BOARDTYPE&columns=ALL` → 200, 字段 BOARD_NAME/NEW_BOARD_CODE(BK0xxx)/SECURITY_CODE/BOARD_RANK。 |
| **落库表 (新)** | `fact_concept_membership_snapshot` PK=(stock_code, concept_code, snapshot_date): stock_code, concept_code(BK0xxx), concept_name, board_rank, snapshot_date, source_label('eastmoney_datacenter'), source_raw_hash, source_available_date, fetched_at。配套 `raw_concept_membership_snapshot` (存原始 JSON 审计)。可选 `dim_concept_catalog` (BK→类型映射, 剔除指数/风格板)。 |
| **新建** | `backend/services/concept_client.py` (照搬 `tdx_industry_client.py`): datacenter-web REST 优先, push2/akshare `_em` 作 GCP VM 备选; `INSERT OR CONFLICT DO UPDATE` 幂等。`daily_update.sh` Step 2k 风格接入 (本地 network block 时走 GCP VM 路径)。 |
| **配置化** | 概念源 host / reportName / **BK→类型过滤规则** → `backend/config/*.yaml` (不 hardcode, 宪法 §3)。 |
| **PIT 保证** | (1) 纯 current snapshot, 强制只读 `snapshot_date<=t`, 接入前历史段标 unknown; (2) datacenter F10 成员每日重算, 当日盘后才稳, `source_available_date<=t`; (3) **BK 类型污染**: report 混 HS300/大盘股指数, 须 BK→类型过滤只留纯概念 (否则重蹈"行业无 alpha"); (4) hist_em 历史 OHLC 含未来成分, **严禁当 PIT 板块收益**; (5) 失败日不写 snapshot (留空 ≠ 移除成员)。 |
| **工作量** | 2.5-4 天 (本环境 push2 不通是额外成本: 固定走 datacenter-web 或上 GCP VM, +0.5 天验证) |

---

### P2-1 — 融资融券 RZRQ 接入 (1.5-2 天, 免费 + 14 年历史)

| 项 | 内容 |
|---|---|
| **做什么** | 项目当前 **0 融资融券表** (实测确认 net-new)。填杠杆资金维度空白 (现有 capital_flow 北向/主力 + LHB 游资, 缺杠杆)。**少见的既有历史又干净的源**。 |
| **数据源** | `ak.stock_margin_detail_sse(date)` (1772行) + `ak.stock_margin_detail_szse(date)` (1831行) + `ak.stock_margin_account_info()` (全市场情绪, 仅滞后1天)。全 PASS, 仅需 retry 包装 (akshare 间歇 JSONError)。 |
| **落库表 (新)** | (1) `fact_margin_detail_daily` grain=trade_date+stock_code: rz_balance/rz_buy/rq_volume/rq_balance/rzrq_balance/exchange + raw_json (防字段漂移), union SSE+SZSE 缺列填 NULL 不强算; (2) `raw_margin_account_info_daily` grain=date (13 列全市场杠杆情绪)。DDL 照 `ingest_profit_forecast_snapshot.py`。 |
| **新建** | `backend/scripts/ingest_margin_rzrq.py` (照 profit_forecast 模板): fetch_sse/szse + retry(3) + normalize 列映射 + `INSERT OR IGNORE on (trade_date, stock_code)`; 支持 `--date` (daily 增量) + `--start/--end` (backfill 2010→今) + `--dry-run`。 |
| **daily 接入** | `daily_update.sh` 新 step (紧跟 capital flow PIT backfill): `--date $(date +%Y%m%d)`。注意两融数据 T 日盘后 18-21 点才出, 早跑取 T-1 (与 LHB/capital_flow 同节奏)。 |
| **PIT 保证 (核心优势)** | margin detail **自带"信用交易日期"= 真实交易日, 非 snapshot 当天** → 可安全 backfill 14 年无 survivorship leakage (与概念/profit_forecast 本质不同)。消费: `AND margin.trade_date <= signal_date`; live 推理标 lag≥1 (T 日明细 T 日盘后才发)。融券余量 0 是真实 0 不当缺失; universe 仅 ~65% (大中盘) NULL 当稀疏特征, **不可均值 fillna 造假信号**。 |
| **工作量** | 核心人力 1.5 天 + backfill ~2-4h 挂后台 (一次性 IO, 非人力工时) |
| **Push back** | 先做 IC 实证 (类似行业轮动证伪 IC-0.065) 再进 panel — 价值更可能在"风险/拥挤度过滤"而非选股 alpha, 不要直接当强信号。 |

---

### P2-2 — 景气度因子 forward-fill (复用 fact_financial_pit_daily, 1-1.5 天)

| 项 | 内容 |
|---|---|
| **做什么** | **不接 AIF10 表**。目标景气度因子用项目已有真 PIT 源 `fact_financial_pit_daily.profit_yoy/revenue_yoy`, 但当前仅 **8.1% 填充** (实测 297547/3.69M)。修 backfill: 对 profit_yoy 做 ASOF forward-fill (取 announce_date<=trade_date 的最新季报值), 填充率应到 90%+。 |
| **改文件** | 查 `backend/scripts/backfill_financial_pit.py` 为何覆盖低 (疑似只季报披露日有值未 forward-fill) → 加 ASOF forward-fill → 重建 panel 列 → PIT audit。 |
| **落库** | 复用现有 `fact_financial_pit_daily` + `feature_join_v3.py` SQL CTE ASOF JOIN (`announce_date<=signal_date`)。 |
| **PIT 保证** | 正确 PIT 边界 = `announce_date` (非 report_date 报告期, 否则提前用未来财报); forward-fill 后 91.9%→90%+ 填充, 剩余 NULL **不可全局均值 fillna** (= 用未来分布 leakage)。 |
| **工作量** | 1-1.5 天 |

---

### P1/P0 — 6A 分析师覆盖边 + 6B 舆情 Phase1 (激活孤儿脚本)

| 项 | 内容 |
|---|---|
| **做什么 (B-Phase1 P0)** | 激活孤儿脚本 `build_akshare_panel.py` (实测 `daily_update.sh`/`cron_daily.py`/`updater.py` **0 引用**, 故 `fact_research_report`/`fact_hot_rank_daily`/`fact_stock_sentiment_snapshot` 在 DB 中**不存在**)。先**修 cols_map bug** (用了"报告标题/研报机构", akshare 实返"报告名称/机构") + 接入 daily_update。个股舆情/热度历史**不从今天存就永远没有** (news_em 仅10条, comment focus 仅30天)。 |
| **做什么 (A-覆盖边 P1)** | `stock_research_report_em` 实测可用且**有 2017+ 深历史** (单股 759 行/39 机构)。落 `fact_research_report` 后自 JOIN 生成 `fact_analyst_coverage_edge` (同窗口共同覆盖 = 共现边), 这部分**可立即回填**。 |
| **落库表 (新)** | `fact_stock_sentiment_snapshot` PK=(snapshot_date, stock_code) 照搬 attention 模式 (hot_rank/xq_follow/focus_index/news_count_1d + src_available_date); `raw_news_text` (原始文本越早存越好); `raw_news_cctv` (宏观可回填 2-3 年); `fact_analyst_coverage_edge` (由 research_report 自 JOIN)。**图存 DuckDB 关系表, 不上 Neo4j** (奥卡姆剃刀)。 |
| **PIT 保证** | 概念/覆盖边 current snapshot + "先炒后纳入"滞后 → 只能做 universe/共振过滤, **不当领先信号**; 每日 append 回测取 ≤t 边; 区分 src_available_date vs snapshot_date; 供应链 lead-lag 若 RankIC>0.3 立即查未来边 leakage; akshare 多接口实测不稳 (index_news_sentiment_scope JSONError / cls 超时), 缺失可能上游不假设 sync bug。 |
| **工作量** | B-Phase1 攒数据 2-3 天 (激活+修bug+建表+接入+news_cctv回填) / A-覆盖边 ~3 天 / B-Phase2 NLP 打分 3-5 天 (Phase 2 再做) |

---

### P3 — 缓议项 (不阻塞, 待验证后决策)

| 项 | 决策 | 理由 |
|---|---|---|
| 同花顺 tushare 付费 (600元/年) | **暂不买** | 实测 `ths_member` in_date/out_date 官方标"暂无"= 无 PIT 历史, 600元买不到"历史回测能力"。若一定买: 优先级 `dc_member`(唯一可按trade_date回溯) > `ths_daily`(指数行情) > `ths_member`(纯snapshot)。 |
| 真供应链边 (Wind 3.9-6万 / 爬年报) | **暂不做** | 免费侧 akshare 完全无供应链接口; 工程量 2-3 周 (实体对齐+NLP)。先用覆盖边验证 lead-lag 假设成立再投入。 |
| B-Phase2 舆情 NLP 打分 | **Phase 1 之后** | snowNLP/FinBERT 开源免费, 但先攒原始文本 (Phase1) 才有料可打分。 |

---

## 4. PIT 快照落库总纲 (命门)

> **第一性原理 (宪法 §1.0)**: 真正命门是"每日快照落库纪律", 不是"买哪个源"。标杆 (`tdx_industry_history` Step 2j) 已存在, 推广它即可。

**统一 PIT-safe 设计 (所有快照表强制)**:

| 要素 | 规则 | 反例 (踩过) |
|---|---|---|
| **真相日 vs 可用日** | 表带 `snapshot_date` (或 trade_date) + `source_available_date`。JOIN 永远 `AND source_available_date <= signal_date` | inst_path_a latest snapshot 用今天数据回测历史 (2026-05-15, RankIC +60% 假象) |
| **绝不回填历史** | current snapshot 拿到的是"今天成分", `source_available_date = fetch 当天`, **任何代码回填历史日期 = 前视+生存者偏差** | dim_all_ever_listed 快照比对误标 573 活跃股 |
| **失败不污染** | sync 失败日**不写 snapshot** (留空 ≠ 移除成员); 区分"真退出"vs"sync 失败" (akshare 缺失可能上游, 宪法 §4.3) | — |
| **去重审计** | `source_raw_hash` (sha256) + `raw_*` 表存原始 JSON/payload (防字段漂移 + 审计 lineage) | — |
| **幂等写入** | 可变快照 `INSERT OR REPLACE`; immutable `INSERT OR IGNORE`。同日重跑安全, 跨日 append 新 date | — |
| **as-of 查询** | `WHERE stock_code=? AND snapshot_date <= ? ORDER BY snapshot_date DESC LIMIT 1` (模板 `tdx_industry_client.py:386`) | — |
| **异常 alpha 警报** | 接入后回测 RankIC > baseline+50% 或胜率异常 → **大概率 snapshot 回填污染**, 必走 pit-audit 5 步, 不是兴奋 (宪法 §4.2) | v3 102 features RankIC 0.0353 relative +75% = chain leakage |
| **历史不可逆** | append-only 数据每拖一天永久丢一天。用此表的回测窗须 ≥ 首个 snapshot_date。概念/舆情接入后**至少累积 3-6 个月**才够验证 alpha | — |
| **不可回灌** | 这些快照"今天起"才 PIT-clean, 入 training/backtest 前等数月 + 单独 PIT audit, **不补历史** | profit_forecast docstring 已写明"数月后才进 training" |

**落库标准模板** (照 `backend/services/tdx_industry_client.py:298-387`):
1. `CREATE TABLE IF NOT EXISTS <fact>_history (... snapshot_date, source_raw_hash, source_available_date, fetched_at, PRIMARY KEY (key, snapshot_date))`
2. `INSERT OR REPLACE INTO <fact>_history ... (snapshot_date = latest_closed)`
3. 配套 `raw_*_snapshot` 存原始 payload (PK snapshot_date + raw_hash)
4. router step + `daily_update.sh` 接线 (WARN-only)
5. `data_audit_rules.yaml::smartmoney_freshness.tables` 注册 + watermark SLA

---

## 5. 需要用户授权的决策点清单

> **项目宪法: 严格 read-only, 不写 production DB。以下全部需用户明确授权后开发才能执行写操作。**

| # | 决策点 | 类型 | 影响 | 默认建议 |
|---|---|---|---|---|
| D1 | **写 production DB** (新建 6+ 张表 + 改 block_client sync + backfill 14 年 margin) | DB 写 (不可逆于历史) | 本任务调研阶段严格 read-only; 实施需解除 | 分批授权: 先 P0 接线 (改 daily_update 不动 schema 风险最低) |
| D2 | **改 `daily_update.sh`** 加 Step 2k-2m (5+ 个新 sync step) | cron / 定时任务 | 每日 17:00 窗口 +约 1-3 分钟 (attention 27s + forecast 15s + block 30s + margin) | 授权 — 全 WARN-only 不阻主 pipeline |
| D3 | **删 orphan plist** `com.chunkymonkey.forecast_eps.plist` | 系统配置 | 避免双跑; 已确认未 load | 授权 (低风险, 归口 daily_update) |
| D4 | **tushare 付费 600元/年** (6000 积分) | 付费预算 | 解锁 ths/dc 全套, 但买不到 PIT 历史 | **不授权 (P3 缓议)** — 先免费路径 |
| D5 | **GCP VM 跑概念 fetch** (若本地 push2 被墙固定走云) | GCP 预算 (月 $50) | 每日几分钟可忽略 ($0.376/h spot); 走 controlled-use | 优先 datacenter-web 本地直连 (0 成本); GCP 仅 fallback |
| D6 | **backfill margin 14 年跑批** (~3500 交易日 × 2 接口) | 长任务 (~2-4h) | 一次性, 挂后台; akshare 限频 | 授权 (本地, 非 GCP) |
| D7 | **概念 BK→类型过滤规则** (剔除指数/风格板的 yaml) | 配置决策 | 不过滤会把指数当概念 (重蹈无 alpha) | 需 review 过滤清单 |
| D8 | **激活孤儿 build_akshare_panel.py** (修 cols_map bug + 接 daily_update) | DB 写 + cron | 新建 research_report/hot_rank/sentiment 表 | 授权 P0 (历史不可逆) |

---

## 6. 不推荐做的事 + 理由

| 不做 | 理由 (实测/第一性原理) |
|---|---|
| **接 `raw_aif10_peer_valuation`** | 实测估值字段 **0 非空 (100% NULL 空壳)**, security_name 字段是"行业平均"垃圾值。抓取早已失败, 接它=接空气。 |
| **接 `raw_aif10_valuation_quantile` / `forecast_consensus` 进 panel** | 有数据但**无 date 列** (PK=secucode+cycle+index_type)。用今天分位回测历史 = latest-snapshot leakage。`feature_join_v3.py:9-10` 已明确拒绝并改用 `fact_financial_pit_daily` rolling z-score。仅可用于实时盘前展示 (picture daily 已在用)。 |
| **重建估值分位因子** | `mart_p0a_feature_label_panel_v3` 已有 `pe_ttm_z_1y`/`pb_z_1y` (PIT-safe rolling 1Y z-score, 2.77M 填充)。**已完成, 0 额外工作**, 直接做 Optuna search space。 |
| **现在买 tushare 600元** | `ths_member` in_date/out_date 官方"暂无"= 无 PIT 历史。"历史攒不回来"这个命门**付费同样不解决**, 只买到"质量更高 + 省抓 EM 被墙"。 |
| **用 akshare `_em` 概念接口** (本环境) | push2.eastmoney.com 对本 IP/代理硬拒 (curl 0/10, HTTP 000)。**改走 datacenter-web REST** (实测可用 89981 行) 或 GCP VM。 |
| **用 hist_em 历史 OHLC 当 PIT 板块收益** | 这是"用今天成分算出的指数", 历史 OHLC 本身含未来成分 leakage。真 PIT 板块收益须用 snapshot 成员 + 个股 PIT K 线当时重算。 |
| **把估值/景气因子聚合到行业再做轮动** | 项目已实证行业分类轮动无 alpha (IC-0.065 均值回归)。保持个股 feature 粒度进 ml_ranking, 不做行业聚合轮动。 |
| **补历史快照** | append-only 数据历史补不回, 当前样本太短 (industry 仅 1 月)。强行补 = 回填污染。等数月累积。 |
| **现在把概念/舆情快照喂进 training** | PIT 历史不足数月, 违宪法 §1.3 measured 红线 + leakage 风险。先攒够 3-6 个月。 |
| **上 Neo4j/Nebula 建产业链图** | DuckDB 关系表足够 (奥卡姆剃刀)。networkx/neo4j 项目零依赖零引用。 |
| **真供应链边一上来就建** | 免费侧 akshare 无供应链接口, 工程量 2-3 周。先用免费分析师覆盖边验证 lead-lag 假设成立再投入。 |
| **4 表各写各的 sync** | 本质是"PIT 快照纪律"一个问题的多个实例。推广标杆 (`tdx_industry_history`) + 加监控, 不重复造轮子。 |
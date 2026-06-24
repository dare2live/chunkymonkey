# M2 ETF K线 → tushare 迁移 — scoping + 实弹验证 (2026-06-25)

> owner=task#35(M2)。本 doc = architect 先行 scoping, 实弹核证后的迁移方案。执行待焦点 pass (含长回填 + 物删 escalate)。

## 前提纠正 (task#35 措辞错, 实测纠)
task#35 写 "ETF → tushare + 删 akshare price_kline"。**实测 ETF K线源不是 akshare**:
- `etf.duckdb.etf_price_kline` (929,334行/1622 ETF/2023-2026): source = **mootdx 96.3% (通达信) + tx 3.7% (腾讯)**; akshare 仅 `fetch_etf_kline` 降级兜底 (circuit-breaker, 无常态写)。
- 拉取链: `akshare_client.fetch_etf_kline` 先 tdxhub(mootdx) 主 → 失败回退 akshare 链 (写 tx/其他)。
- → §4.3 迁移成立, 但**删的旧源 = mootdx/tx (tdxhub 体系)**, 非 akshare。akshare ETF 兜底也一并退 (§4.3 无热备)。

## tushare 覆盖 + 复权口径 (实弹核证 GO)
- `fund_daily` 覆盖 ETF (510300.SH 实测返 OHLCV+vol+amount, catalog 10年+ vs mootdx 3年)。
- `fund_adj` 提供复权因子 (510300 近期 adj=1.267 恒定)。
- **qfq 可建 = fund_daily(raw) × fund_adj, 同 A股 `build_price_kline_qfq_tushare.py` 模式** (raw_tushare_daily × adj_factor → qfq)。
- 实弹对账 510300 vs mootdx qfq: 04-09/08/07/03 **close 精确匹配**, 04-10 差 0.17% (数据修订级)。复权口径对齐 ✓。

## 消费方 fan-in (repoint 范围小, 3 处 SQL 直读 etf_price_kline)
1. `etf_engine.calc_etf_momentum` (etf_engine.py:410) — ETF 动量/振幅因子 → snapshot/mining。
2. `etf_snapshot_manager` (:117) — mart_etf_snapshot_latest 快照 → 前端 /etf/snapshot。
3. `etf_mining_engine` (:44,238) — mart_etf_strategy_comparison 挖矿 → 前端 /etf/opportunity。
写方: `etf_db.upsert_price_rows` (etf_engine.py:326 调 fetch_etf_kline)。sync_hs300_benchmark_kline 复用 etf_db 写函数 (非读 etf_price_kline)。前端端点经 services 读层透传 (routers.etf 已退役 2026-06-24)。

## 迁移分阶段 (可逆自主 A-D; E 物删 escalate, 同 §9 Stage E 模式)
| Stage | 动作 | 可逆? |
|---|---|---|
| A 注册 | sync_registry 加 tushare `fund_daily` + `fund_adj` 域 (ETF universe filter: 场内前缀 51/15/56/58/56x; ts_code .SH/.SZ); 实弹核单页上限/grain/min_rows (防 top10_floatholders 截断坑) | 可逆 |
| B 回填 | fund_daily + fund_adj 10年×1622 ETF → tushare_raw.duckdb (by_trade_date 按日批 or by_ts_code; 0行当失败重试; 限流120/200/2) | 可逆 (重sync) |
| C 建qfq | mirror build_price_kline_qfq_tushare: fund_daily×adj → etf qfq 表 (etf.duckdb 或 market; 全量CTAS); 复权口径单测 vs mootdx | 可逆 |
| D repoint+双轨 | 3 消费方 SQL 指向 tushare etf qfq + 双轨核对 (vs mootdx, 期望近期精确匹配, 历史 adj 对齐); daily_update ETF 步切 tushare | 可逆 (revert SQL) |
| E 物删 | 物删 mootdx/tx ETF 拉取链 (fetch_etf_kline tdxhub分支 + akshare兜底) + 旧 source 行; deletion_record | **不可逆, escalate 用户确认** |

## 坑 (实测/审计识别)
1. **场内 vs 场外**: fund_daily 含场外开放式基金 (无交易) → universe filter 必须 (ts_code 前缀白名单 51/15/56/58 场内 ETF), 否则记录虚高。
2. **复权口径**: qfq 必须 fund_daily × fund_adj (非直用 fund_daily raw), 否则历史 (有分红的 adj 变化段) 错位。近期 adj 恒定故 raw≈qfq 易误判"直用 raw 即可" — 历史段会炸。
3. **跨库**: etf_price_kline 在 etf.duckdb; raw_tushare_fund_daily 在 tushare_raw.duckdb → C 建 qfq 落哪库 + D 消费方跨库读 (mirror A股: qfq 落 market 或 etf, 消费方 ATTACH)。
4. **覆盖扩展**: tushare 10年 vs mootdx 3年 → 迁移后 ETF 历史变长 (benefit, 但下游因子/回测窗口变 = 注意不可比段, 同行业taxonomy切源教训)。

## 1-ETF 验证结果 (510300 全史, 2026-06-25 实弹) — GO, 且 M2 = 质量修复非仅整合

**重大发现: tushare ETF qfq 正确, mootdx 有 ETF 分红未复权 bug**。
- 建 qfq = fund_daily.close × fund_adj.adj_factor / latest_adj (mirror A股), 与 mootdx etf_price_kline 双轨**对收益** (rebase 常数在收益抵消, 同 build_price_kline_qfq_tushare 约定)。
- 重叠 789 日 (2023-2026): max_ret_diff=0.025, **5 日 >1e-4**, 其余精确。
- 5 差异日 worst 3 = **2024-01-18 / 2025-06-18 / 2026-01-19**: tushare ret≈0, mootdx ret≈-2.5%。
- **铁证 (fund_div)**: 510300 除息日精确 = 20240118(div 0.069)/20250618(0.088)/20260119(0.123); fund_adj 在这 3 日精确跳变(+0.026/+0.027/+0.032)。
- 定论: **tushare fund_daily×fund_adj 正确把 ETF 分红调成总收益(ex日 ret≈0); mootdx 没调(显原始除息跌)。tushare 更对** → M2 不只源整合, 是**修 mootdx ETF 分红未复权 bug** (当前 ETF 动量/因子在~年度分红日算错, 影响 etf_engine/mining 消费方)。
- **验证口径修正**: 双轨 vs mootdx **不能期望 ≈0** (mootdx 才错); 正确验证 = tushare adj 跳变 vs fund_div 除息日+金额 (510300 已证)。

**Stage A 实测确认 (by_trade_date + universe filter)**:
- batch_mode = **by_trade_date** (实测 fund_daily/fund_adj trade_date=单日返全基金 ~1940/1960 行 < 2000 cap, 非截断; **by_ts_code 单只全史 fund_adj 截断到 2000** = top10_floatholders 坑, 故必 by_trade_date 同 A股 adj_factor)。
- **universe_filter 坑**: 现 `universe_filter: true` = A股前缀白名单(60/00/30/68) → **会滤光所有 ETF(51/15/56/58)**! ETF 域**不能复用 A股 filter**, 须 ETF 专属前缀(51/15/56/58, 场内)— 新机制 (services.universe 加 ETF 前缀集 OR writer 层 ETF filter OR registry prefix config)。当日全基金返回含 16/50/52/18(LOF/分级等)需滤到场内 ETF。
- min_rows_per_batch ~1200 (场内ETF ~1406, 留余 + 防截断); data_start 待定(tushare ETF 史 510300 到 20120528, 全市场起点核)。

## NEXT (焦点执行)
**Stage A [DONE 2026-06-25]**: (a) sync_runner 加 config-driven `universe_filter_prefixes` 覆盖 (默认A股60/00/30/68; ETF域覆盖15/51/56/58; 不污染services.universe个股真相源) (b) sync_registry 注册 fund_daily+fund_adj (by_trade_date/universe_filter_prefixes=[15,51,56,58]/min_rows 1200/data_start 20190102)。验证: 20 sync_runner单测绿(含2新: ETF前缀覆盖只留15/51/56/58 + 默认A股不变回归保护); registry解析2域OK; py_compile+YAML合法。
**B 回填 [DONE 2026-06-25]**: fund_daily+fund_adj --backfill 后台串行 (~20min)。覆盖核证 (对交易日历, 第一手源): 各 1811 distinct trade_date == dim_trading_calendar[2019-01-02..2026-06-24] 1811 (**0 缺日**), ~1826 codes, fund_daily 1.36M行 / fund_adj 1.40M行。**ok=False/failed_batches(1109/931)=min_rows误报已修**: 实测历史ETF universe增长(2019首日仅323→2026~1406), min_rows 1200(校2026)误判早年合法批; 最小batch 2019-01-03=323(>0无真空批, 数据"仍写入"完整)。修 min_rows 1200→300(floor<2019的323; 覆盖完整性靠日历gap核证非min_rows; 上限2000cap另年检)。 → **C 建 etf qfq**(fund_daily×adj mirror A股 + 复权口径单测: adj 跳变 vs fund_div) → **D repoint 3 消费方 + 验证**(vs fund_div 非 vs mootdx) → **E 物删 mootdx/tx 链 = escalate**。premise 纠正(mootdx/tx 非 akshare)+ mootdx 分红 bug 已通知用户; M3/M4 任务措辞同样需核实际源。

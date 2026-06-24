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

## NEXT (焦点执行)
Stage A (注册域, 可逆 config + 实弹核单页) → B 回填 (长任务, 可后台) → C qfq 建+单测 → D repoint+双轨 → E 物删 escalate。premise 纠正 (mootdx/tx 非 akshare) 已通知用户; M3/M4 任务定义可能同样需核 (task 措辞 vs 实际源)。

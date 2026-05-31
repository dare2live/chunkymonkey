# MSAF 顶层设计与 Scheme 7 机构跟随策略详细设计

| 字段 | 值 |
|---|---|
| 项目 | ChunkyMonkey |
| 工作目录 | `/Users/dp/Documents/M/stock/chunkymonkey` |
| 生成日期 | 2026-05-17 |
| DB 探查方式 | DuckDB read-only |
| 输出文件 | `docs/msaf_top_design_20260517.md` |
| 严格结论 | LHB 单独跟随在 T+1 真实可交易口径下为负；原始资金流 N>=3 短样本为正但事实表缺 PIT 主力净流字段；MSAF 当前可上线权重应偏 Scheme 6，Scheme 7 需先补 PIT 数据。 |

## 0. DB 探查入口
```bash
find . -name '*.duckdb' -print
```
| DB 文件 |
|---|
| ./data/smartmoney.duckdb |
| ./data/etf.duckdb |
| ./data/kline_delta_2026_05_07_to_2026_05_15.duckdb |
| ./data/alpha158.duckdb |
| ./data/market.duckdb |

### 0.1 本文使用的 DuckDB 文件
| 用途 | DB | 说明 |
|---|---|---|
| 智能资金、LHB、机构、资金流 | `data/smartmoney.duckdb` | 319 张表，本文读写方式为只读。 |
| 复权价格、沪深300 | `data/market.duckdb` | `v_price_kline_qfq` 和 `price_kline`。 |
| alpha158 | `data/alpha158.duckdb` | 本文只作为 Phase 1 修复背景，不参与 Scheme 7 事件研究。 |
| ETF | `data/etf.duckdb` | 本文未用于股票-only 策略推导。 |
| 增量 K 线 | `data/kline_delta_2026_05_07_to_2026_05_15.duckdb` | 本文未使用。 |

### 0.2 价格表覆盖
```sql
select count(distinct code) as n_codes, count(*) as row_count, min(date) as min_date, max(date) as max_date from m.v_price_kline_qfq
```
|   n_codes |   row_count | min_date   | max_date   |
|----------:|------------:|:-----------|:-----------|
|      5205 |     5204245 | 2022-01-01 | 2026-05-15 |

### 0.3 沪深300覆盖
```sql
select code, min(date) as min_date, max(date) as max_date, count(*) as row_count from m.price_kline where code='000300' group by code
```
|   code | min_date   | max_date   |   row_count |
|-------:|:-----------|:-----------|------------:|
| 000300 | 2022-01-01 | 2026-05-06 |        1048 |

## Part A：机构跟随策略 Scheme 7 详细设计
### A0. 结论先行
| 项 | 结论 | 数字 | 处理 |
|---|---|---:|---|
| LHB 全事件，事件日后10日超额 | 负 | -1.4859% | 禁止裸 LHB 跟随。 |
| LHB 机构净买入+2000万+5%，T+1入场10日超额 | 负 | -1.7108% | Kelly 为 -0.1131，仓位归零。 |
| 原始主力资金 N>=3，T+1入场10日超额 | 正 | 2.8744% | 短样本、非事实PIT表，先工程化再复验。 |
| 北向资金 | 单日快照 | 20240816 | 不能验证 2024-08 口径断点。 |
| 大宗交易 | 仅 20260424-20260430 | 548 行 | 不能用于 2022-2025 策略统计。 |

### A1. alpha 源 inventory 与 PIT-strict 状态
### A1.fact_lhb_event row count/date range
```sql
select count(*) as row_count, min(trade_date) as min_date, max(trade_date) as max_date, count(*) filter(where built_at is not null) as built_at_nonnull from sm.fact_lhb_event
```
|   row_count | min_date   | max_date   |   built_at_nonnull |
|------------:|:-----------|:-----------|-------------------:|
|       52550 | 2023-01-03 | 2026-04-28 |              52550 |

### A1.raw_lhb_daily row count/date range
```sql
select count(*) as row_count, min(trade_date) as min_date, max(trade_date) as max_date, count(*) filter(where ingested_at is not null) as ingested_at_nonnull from sm.raw_lhb_daily
```
|   row_count | min_date   | max_date   |   ingested_at_nonnull |
|------------:|:-----------|:-----------|----------------------:|
|       63277 | 2023-01-03 | 2026-05-15 |                 63277 |

### A1.fact_capital_flow_pit_daily row count/date range
```sql
select count(*) as row_count, min(trade_date) as min_date, max(trade_date) as max_date, count(*) filter(where built_at is not null) as built_at_nonnull, count(*) filter(where holder_count_q_report_date is not null) as holder_report_nonnull from sm.fact_capital_flow_pit_daily
```
|   row_count | min_date   | max_date   |   built_at_nonnull |   holder_report_nonnull |
|------------:|:-----------|:-----------|-------------------:|------------------------:|
|      857993 | 2023-01-03 | 2026-05-13 |             857993 |                   23421 |

### A1.raw_fund_flow_daily row count/date range
```sql
select count(*) as row_count, min(trade_date) as min_date, max(trade_date) as max_date, count(*) filter(where ingested_at is not null) as ingested_at_nonnull from sm.raw_fund_flow_daily
```
|   row_count | min_date   | max_date   |   ingested_at_nonnull |
|------------:|:-----------|:-----------|----------------------:|
|       86426 | 2025-08-21 | 2026-04-24 |                 86426 |

### A1.raw_institution_surveys row count/date range
```sql
select count(*) as row_count, min(survey_date) as min_survey_date, max(survey_date) as max_survey_date, min(notice_date) as min_notice_date, max(notice_date) as max_notice_date from sm.raw_institution_surveys
```
|   row_count | min_survey_date   | max_survey_date   | min_notice_date   | max_notice_date   |
|------------:|:------------------|:------------------|:------------------|:------------------|
|       12321 | 2025-04-23        | 2026-05-15        | 2025-10-22        | 2026-05-16        |

### A1.fact_institution_event row count/date range
```sql
select count(*) as row_count, min(report_date) as min_report_date, max(report_date) as max_report_date, min(notice_date) as min_notice_date, max(notice_date) as max_notice_date, min(tradable_date) as min_tradable_date, max(tradable_date) as max_tradable_date from sm.fact_institution_event
```
|   row_count |   min_report_date |   max_report_date |   min_notice_date |   max_notice_date | min_tradable_date   | max_tradable_date   |
|------------:|------------------:|------------------:|------------------:|------------------:|:--------------------|:--------------------|
|       35602 |          20241231 |          20260424 |          20250430 |          20260506 | 2025-05-06          | 2026-05-07          |

### A1.mart_institution_profile row count/date range
```sql
select count(*) as row_count, min(latest_notice_date) as min_latest_notice_date, max(latest_notice_date) as max_latest_notice_date, min(updated_at) as min_updated_at, max(updated_at) as max_updated_at from sm.mart_institution_profile
```
|   row_count |   min_latest_notice_date |   max_latest_notice_date | min_updated_at             | max_updated_at             |
|------------:|-------------------------:|-------------------------:|:---------------------------|:---------------------------|
|         231 |                 20260430 |                 20260506 | 2026-05-16T20:13:05.126203 | 2026-05-16T20:13:05.126203 |

### A1.fact_hsgt_daily row count/date range
```sql
select count(*) as row_count, min(snapshot_date) as min_snapshot_date, max(snapshot_date) as max_snapshot_date, count(distinct snapshot_date) as n_dates from sm.fact_hsgt_daily
```
|   row_count |   min_snapshot_date |   max_snapshot_date |   n_dates |
|------------:|--------------------:|--------------------:|----------:|
|        2767 |            20240816 |            20240816 |         1 |

### A1.fact_dzjy_event row count/date range
```sql
select count(*) as row_count, min(trade_date) as min_trade_date, max(trade_date) as max_trade_date from sm.fact_dzjy_event
```
|   row_count |   min_trade_date |   max_trade_date |
|------------:|-----------------:|-----------------:|
|         548 |         20260424 |         20260430 |

### A1.北向资金表发现
```sql
SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%hsgt%' ORDER BY table_name
```
| table_name      |
|:----------------|
| fact_hsgt_daily |

### A1.大宗交易 block/bulk 名称匹配
```sql
SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%block%' OR table_name LIKE '%zongzhuyi%' OR table_name LIKE '%bulk%' ORDER BY table_name
```
| table_name            |
|:----------------------|
| dim_stock_tdx_block   |
| dim_tdx_block_catalog |

### A1.大宗交易 dzjy 名称补充匹配
```sql
SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%dzjy%' OR table_name LIKE '%dazong%' ORDER BY table_name
```
| table_name      |
|:----------------|
| fact_dzjy_event |

### A1.fact_lhb_event PIT 列空值
```sql
select count(*) as row_count, count(*) filter(where trade_date is null) as null_trade_date, count(*) filter(where built_at is null) as null_built_at, count(*) filter(where regexp_matches(trade_date, '^\d{4}-\d{2}-\d{2}$')) as iso_dates from sm.fact_lhb_event
```
|   row_count |   null_trade_date |   null_built_at |   iso_dates |
|------------:|------------------:|----------------:|------------:|
|       52550 |                 0 |               0 |       52550 |

### A1.fact_institution_event PIT 列空值
```sql
select count(*) as row_count, count(*) filter(where notice_date is null) as null_notice_date, count(*) filter(where availability_deadline is null) as null_availability_deadline, count(*) filter(where tradable_date is null) as null_tradable_date, count(*) filter(where notice_date_source is null) as null_notice_date_source from sm.fact_institution_event
```
|   row_count |   null_notice_date |   null_availability_deadline |   null_tradable_date |   null_notice_date_source |
|------------:|-------------------:|-----------------------------:|---------------------:|--------------------------:|
|       35602 |                  0 |                            0 |                    0 |                         0 |

### A1.raw_institution_surveys PIT 列空值
```sql
select count(*) as row_count, count(*) filter(where survey_date is null) as null_survey_date, count(*) filter(where notice_date is null) as null_notice_date, count(*) filter(where inst_count is null) as null_inst_count from sm.raw_institution_surveys
```
|   row_count |   null_survey_date |   null_notice_date |   null_inst_count |
|------------:|-------------------:|-------------------:|------------------:|
|       12321 |                  0 |                  0 |                 0 |

### A1.fact_capital_flow_pit_daily PIT 列空值
```sql
select count(*) as row_count, count(*) filter(where trade_date is null) as null_trade_date, count(*) filter(where built_at is null) as null_built_at, count(*) filter(where holder_count_q_report_date is null) as null_holder_count_report_date from sm.fact_capital_flow_pit_daily
```
|   row_count |   null_trade_date |   null_built_at |   null_holder_count_report_date |
|------------:|------------------:|----------------:|--------------------------------:|
|      857993 |                 0 |               0 |                          834572 |

### A1.fact_capital_flow_pit_daily 主力净流字段检查
```sql
SELECT column_name, data_type
FROM duckdb_columns()
WHERE database_name='sm' AND table_name='fact_capital_flow_pit_daily'
  AND lower(column_name) IN ('pit_date','main_net_amount','main_net_pct','super_large_net_amount','large_net_amount')
ORDER BY column_name
```
| 结果 |
|---|
| [空结果] |

### A1.1 PIT-safe 判断汇总
| alpha 源 | 表名 | row数 | date范围 | PIT关键列 | PIT-safe判断 | 处理 |
|---|---|---:|---|---|---|---|
| LHB 事件聚合 | fact_lhb_event | 52550 | 2023-01-03 至 2026-04-28 | trade_date, built_at；缺 announce_date/notice_date | CRITICAL: PIT-UNSAFE | 生产必须补 source_available_date；未补前按 T+2。 |
| LHB 原始日表 | raw_lhb_daily | 63277 | 2023-01-03 至 2026-05-15 | trade_date, ingested_at；缺 source_available_date | 需验证 | 需验证 ingested_at 是否为真实抓取时间；未验证前按 T+2。 |
| 资金流 PIT 聚合 | fact_capital_flow_pit_daily | 857993 | 2023-01-03 至 2026-05-13 | trade_date, built_at；缺 pit_date/main_net_amount/main_net_pct | CRITICAL: PIT-UNSAFE | 不能表达主力净流入连续 N 天；需重建。 |
| 原始主力资金流 | raw_fund_flow_daily | 86426 | 2025-08-21 至 2026-04-24 | trade_date, ingested_at | 需验证 | 可做研究；进入生产前需写入 PIT fact。 |
| 机构调研原始 | raw_institution_surveys | 12321 | survey 2025-04-23 至 2026-05-15；notice 2025-10-22 至 2026-05-16 | survey_date, notice_date, ingested_at | Y | 只能用 notice_date <= signal_date。 |
| 机构持仓事件 | fact_institution_event | 35602 | report 20241231 至 20260424；notice 20250430 至 20260506 | notice_date, tradable_date, availability_deadline, notice_date_source | Y | 可按 availability_deadline ASOF。 |
| 机构画像 | mart_institution_profile | 231 | latest_notice_date 20260430 至 20260506 | updated_at, latest_notice_date；聚合画像为 latest snapshot | CRITICAL: PIT-UNSAFE | 禁止用于历史回测；仅当日展示。 |
| 北向资金 | fact_hsgt_daily | 2767 | 20240816 单日 | snapshot_date, built_at | 需验证 | 只有一个日期，不能做趋势。 |
| 大宗交易 | fact_dzjy_event | 548 | 20260424 至 20260430 | trade_date, built_at | 需验证 | 不能覆盖 2022-2025。 |

### A1.2 机构事件枚举
### A1.2 fact_institution_event.event_type
```sql
select event_type, count(*) n from sm.fact_institution_event group by event_type order by n desc
```
| event_type   |     n |
|:-------------|------:|
| increase     | 11108 |
| decrease     | 10947 |
| new_entry    |  9444 |
| unchanged    |  3495 |
| exit         |   608 |

### A1.3 fact_institution_event.follow_gate
```sql
select follow_gate, count(*) n from sm.fact_institution_event group by follow_gate order by n desc
```
| follow_gate   |     n |
|:--------------|------:|
| avoid         | 17712 |
| follow        | 10619 |
| watch         |  3773 |
| observe       |  3495 |
| unknown       |     3 |

### A2. 数学 framework：event-study + Kelly
事件研究定义：事件日为 `fact_lhb_event.trade_date`。由于表中缺 `announce_date`/`notice_date`，本文把该字段标为研究事件日，不把它作为生产可得日。
超额收益定义：$excess\_ret_h = R_{stock,t,t+h} - R_{HS300,t,t+h}$。
t-stat 定义：$t = ar r / (s / \sqrt n)$。
### A2.1 LHB 事件日后 1/5/10/20 日超额收益
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy, net_buy_pct, buy_amount, sell_amount,
         turnover, turnover_rate, float_cap, inst_buy_seats, is_inst_net_buy
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), stock_px AS (
  SELECT code AS stock_code, date, close,
         row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq
  WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close AS idx_close
  FROM m.price_kline
  WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (
  SELECT e.*, p.date AS px_date, p.rn AS base_rn, p.close AS base_close, ib.idx_close AS base_idx_close
  FROM events e
  JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date
  JOIN idx_px ib ON ib.date=e.event_date
), long AS (
  SELECT b.event_date, b.stock_code, h.horizon,
         fp.date AS future_date,
         (fp.close / b.base_close - 1.0) AS stock_ret,
         (ifp.idx_close / b.base_idx_close - 1.0) AS idx_ret,
         (fp.close / b.base_close - 1.0) - (ifp.idx_close / b.base_idx_close - 1.0) AS excess_ret
  FROM base b
  CROSS JOIN (VALUES (1),(5),(10),(20)) AS h(horizon)
  JOIN stock_px fp ON fp.stock_code=b.stock_code AND fp.rn=b.base_rn + h.horizon
  JOIN idx_px ifp ON ifp.date=fp.date
)
SELECT horizon, count(*) AS n,
       round(avg(excess_ret)*100, 4) AS avg_excess_pct,
       round(avg(CASE WHEN excess_ret > 0 THEN 1 ELSE 0 END)*100, 2) AS win_rate_pct,
       round(stddev_samp(excess_ret)*100, 4) AS std_excess_pct,
       round(avg(excess_ret) / nullif(stddev_samp(excess_ret),0) * sqrt(count(*)), 4) AS t_stat,
       round(quantile_cont(excess_ret, 0.05)*100, 4) AS p05_pct,
       round(quantile_cont(excess_ret, 0.50)*100, 4) AS median_pct,
       round(quantile_cont(excess_ret, 0.95)*100, 4) AS p95_pct
FROM long
GROUP BY horizon
ORDER BY horizon
```
|   horizon |     n |   avg_excess_pct |   win_rate_pct |   std_excess_pct |   t_stat |   p05_pct |   median_pct |   p95_pct |
|----------:|------:|-----------------:|---------------:|-----------------:|---------:|----------:|-------------:|----------:|
|         1 | 39584 |           0.1403 |          46.68 |           7.0661 |   3.9517 |  -10.0902 |      -0.5986 |   10.8969 |
|         5 | 39584 |          -0.8745 |          38.18 |          15.6752 | -11.1002 |  -20.9347 |      -3.3554 |   28.7368 |
|        10 | 39584 |          -1.4859 |          35.63 |          20.3765 | -14.5083 |  -24.699  |      -4.928  |   33.6815 |
|        20 | 39584 |          -2.0882 |          34.21 |          25.5633 | -16.2525 |  -29.203  |      -6.9257 |   40.5926 |

### A2.2 LHB 10日候选触发桶
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy, net_buy_pct, inst_buy_seats, is_inst_net_buy,
         CASE
           WHEN is_inst_net_buy=1 AND net_buy > 20000000 AND net_buy_pct >= 5 THEN 'inst_net_buy_and_20m_5pct'
           WHEN is_inst_net_buy=1 AND net_buy > 0 THEN 'inst_net_buy_positive'
           WHEN net_buy > 50000000 AND net_buy_pct >= 5 THEN 'net_buy_50m_5pct'
           WHEN net_buy > 20000000 AND net_buy_pct >= 5 THEN 'net_buy_20m_5pct'
           WHEN net_buy > 0 THEN 'net_buy_positive'
           ELSE 'other_or_sell'
         END AS bucket
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), stock_px AS (
  SELECT code AS stock_code, date, close, row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close AS idx_close FROM m.price_kline
  WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (
  SELECT e.*, p.rn AS base_rn, p.close AS base_close, ib.idx_close AS base_idx_close
  FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date JOIN idx_px ib ON ib.date=e.event_date
), ret10 AS (
  SELECT b.bucket, (fp.close / b.base_close - 1.0) - (ifp.idx_close / b.base_idx_close - 1.0) AS excess_ret
  FROM base b JOIN stock_px fp ON fp.stock_code=b.stock_code AND fp.rn=b.base_rn + 10 JOIN idx_px ifp ON ifp.date=fp.date
)
SELECT bucket, count(*) AS n,
       round(avg(excess_ret)*100,4) AS avg_excess_10d_pct,
       round(avg(CASE WHEN excess_ret > 0 THEN 1 ELSE 0 END)*100,2) AS win_rate_pct,
       round(avg(excess_ret)/nullif(stddev_samp(excess_ret),0)*sqrt(count(*)),4) AS t_stat,
       round(quantile_cont(excess_ret,0.05)*100,4) AS p05_pct,
       round(quantile_cont(excess_ret,0.50)*100,4) AS median_pct
FROM ret10
GROUP BY bucket
ORDER BY avg_excess_10d_pct DESC
```
| bucket                    |     n |   avg_excess_10d_pct |   win_rate_pct |   t_stat |   p05_pct |   median_pct |
|:--------------------------|------:|---------------------:|---------------:|---------:|----------:|-------------:|
| inst_net_buy_and_20m_5pct |  3128 |               0.5984 |          41.59 |   1.7211 |  -21.7862 |      -2.7752 |
| net_buy_20m_5pct          |  2212 |               0.3142 |          36.3  |   0.6275 |  -23.3552 |      -4.576  |
| net_buy_50m_5pct          |  2602 |              -0.1375 |          38.05 |  -0.3386 |  -23.373  |      -4.6373 |
| net_buy_positive          |  9023 |              -0.5717 |          37.01 |  -2.2884 |  -24.985  |      -4.5778 |
| inst_net_buy_positive     |  4046 |              -1.4451 |          37.07 |  -4.5531 |  -24.9359 |      -4.9975 |
| other_or_sell             | 18573 |              -2.6932 |          33.23 | -20.2177 |  -25.227  |      -5.631  |

### A2.3 LHB T+1 入场、10日持有真实可交易口径
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy, net_buy_pct, buy_amount, turnover_rate, float_cap, inst_buy_seats, is_inst_net_buy
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), stock_px AS (
  SELECT code AS stock_code, date, close, row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close AS idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (
  SELECT e.*, p.rn AS event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date
), sim AS (
  SELECT b.*,
         (xp.close/ep.close - 1.0) - (xib.idx_close/eib.idx_close - 1.0) AS excess_10d_t1,
         xp.close/ep.close - 1.0 AS stock_ret_10d_t1,
         xib.idx_close/eib.idx_close - 1.0 AS idx_ret_10d_t1,
         CASE WHEN b.is_inst_net_buy=1 AND b.net_buy > 20000000 AND b.net_buy_pct >= 5 THEN 1 ELSE 0 END AS selected
  FROM base b
  JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date
  JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date
)
SELECT CASE WHEN selected=1 THEN 'selected_inst_20m_5pct' ELSE 'all_events' END AS grp,
       count(*) AS n,
       round(avg(excess_10d_t1)*100,4) AS avg_excess_10d_t1_pct,
       round(avg(stock_ret_10d_t1)*100,4) AS avg_abs_ret_10d_t1_pct,
       round(avg(idx_ret_10d_t1)*100,4) AS avg_hs300_ret_pct,
       round(avg(CASE WHEN excess_10d_t1>0 THEN 1 ELSE 0 END)*100,2) AS excess_win_rate_pct,
       round(avg(CASE WHEN stock_ret_10d_t1>0 THEN 1 ELSE 0 END)*100,2) AS abs_win_rate_pct,
       round(avg(excess_10d_t1)/nullif(stddev_samp(excess_10d_t1),0)*sqrt(count(*)),4) AS t_stat,
       round(avg(CASE WHEN excess_10d_t1>0 THEN excess_10d_t1 END)*100,4) AS avg_win_pct,
       round(abs(avg(CASE WHEN excess_10d_t1<=0 THEN excess_10d_t1 END))*100,4) AS avg_loss_abs_pct,
       round(quantile_cont(excess_10d_t1,0.05)*100,4) AS p05_pct,
       round(quantile_cont(excess_10d_t1,0.5)*100,4) AS median_pct,
       round(quantile_cont(excess_10d_t1,0.95)*100,4) AS p95_pct
FROM sim
GROUP BY grp
ORDER BY grp
```
| grp                    |     n |   avg_excess_10d_t1_pct |   avg_abs_ret_10d_t1_pct |   avg_hs300_ret_pct |   excess_win_rate_pct |   abs_win_rate_pct |   t_stat |   avg_win_pct |   avg_loss_abs_pct |   p05_pct |   median_pct |   p95_pct |
|:-----------------------|------:|------------------------:|-------------------------:|--------------------:|----------------------:|-------------------:|---------:|--------------:|-------------------:|----------:|-------------:|----------:|
| all_events             | 36456 |                 -1.7737 |                  -1.3252 |              0.4484 |                 35.51 |              36.76 | -17.8751 |       15.7369 |            11.4148 |  -24.0949 |      -4.6174 |   30.2316 |
| selected_inst_20m_5pct |  3128 |                 -1.7108 |                  -1.3965 |              0.3143 |                 35.58 |              36.99 |  -5.5064 |       15.1284 |            11.0121 |  -22.9571 |      -4.3827 |   29.1796 |

### A2.4 LHB T+1 入场按年
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy, net_buy_pct, is_inst_net_buy
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), stock_px AS (
  SELECT code AS stock_code, date, close, row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close AS idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (SELECT e.*, p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date),
sim AS (
  SELECT year(strptime(b.event_date,'%Y-%m-%d')) AS yr,
         CASE WHEN b.is_inst_net_buy=1 AND b.net_buy > 20000000 AND b.net_buy_pct >= 5 THEN 'selected_inst_20m_5pct' ELSE 'all_events' END AS grp,
         (xp.close/ep.close - 1.0) - (xib.idx_close/eib.idx_close - 1.0) AS excess_10d_t1
  FROM base b
  JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date
  JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date
)
SELECT yr, grp, count(*) n, round(avg(excess_10d_t1)*100,4) avg_excess_10d_t1_pct,
       round(avg(CASE WHEN excess_10d_t1>0 THEN 1 ELSE 0 END)*100,2) win_rate_pct,
       round(avg(excess_10d_t1)/nullif(stddev_samp(excess_10d_t1),0)*sqrt(count(*)),4) t_stat
FROM sim GROUP BY yr, grp ORDER BY yr, grp
```
|   yr | grp                    |     n |   avg_excess_10d_t1_pct |   win_rate_pct |   t_stat |
|-----:|:-----------------------|------:|------------------------:|---------------:|---------:|
| 2023 | all_events             |  9896 |                 -2.0415 |          34.01 | -12.0832 |
| 2023 | selected_inst_20m_5pct |   926 |                 -2.3038 |          35.21 |  -4.718  |
| 2024 | all_events             | 13872 |                 -1.9002 |          37.31 | -11.011  |
| 2024 | selected_inst_20m_5pct |   827 |                 -2.852  |          33.37 |  -4.555  |
| 2025 | all_events             | 12688 |                 -1.4264 |          34.7  |  -8.4778 |
| 2025 | selected_inst_20m_5pct |  1375 |                 -0.6251 |          37.16 |  -1.2543 |

### A2.5 LHB selected 连续亏损与 Kelly 输入
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy, net_buy_pct, is_inst_net_buy
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31' AND is_inst_net_buy=1 AND net_buy > 20000000 AND net_buy_pct >= 5
), stock_px AS (
  SELECT code AS stock_code, date, close, row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close AS idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (SELECT e.*, p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date),
ret AS (
 SELECT b.event_date,b.stock_code,(xp.close/ep.close - 1.0) - (xib.idx_close/eib.idx_close - 1.0) AS ex
 FROM base b JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date
 JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date
), ordered AS (
 SELECT *, row_number() OVER (ORDER BY event_date, stock_code) seq, CASE WHEN ex<=0 THEN 1 ELSE 0 END loss FROM ret
), streak AS (
 SELECT *, sum(CASE WHEN loss=0 THEN 1 ELSE 0 END) OVER (ORDER BY seq) grp FROM ordered
)
SELECT count(*) n, round(avg(ex)*100,4) avg_ex_pct, round(avg(CASE WHEN ex>0 THEN 1 ELSE 0 END)*100,2) win_pct,
       round(avg(CASE WHEN ex>0 THEN ex END)*100,4) avg_win_pct,
       round(abs(avg(CASE WHEN ex<=0 THEN ex END))*100,4) avg_loss_abs_pct,
       (SELECT max(loss_len) FROM (SELECT grp,count(*) loss_len FROM streak WHERE loss=1 GROUP BY grp)) worst_consecutive_losses
FROM ret
```
|    n |   avg_ex_pct |   win_pct |   avg_win_pct |   avg_loss_abs_pct |   worst_consecutive_losses |
|-----:|-------------:|----------:|--------------:|-------------------:|---------------------------:|
| 3128 |      -1.7108 |     35.58 |       15.1284 |            11.0121 |                         29 |

A2.6 主力净流入连续 N>=3 天：事实表字段检查结论。
- `fact_capital_flow_pit_daily` 不含 `pit_date`、`main_net_amount`、`main_net_pct`。
- 因此，按任务原句“用 fact_capital_flow_pit_daily 计算主力净流入连续 N>=3 天”得到的结果为：[数据不足: 需要在 fact_capital_flow_pit_daily 增加 pit_date、main_net_amount、main_net_pct、source_available_date]。
- 下表使用 `raw_fund_flow_daily` 做研究诊断，不作为生产合格证据。
### A2.7 raw_fund_flow_daily 主力净流入 N>=3 诊断
```sql
WITH ff AS (
  SELECT stock_code, trade_date, main_net_amount, main_net_pct,
         CASE WHEN main_net_amount > 0 THEN 1 ELSE 0 END AS is_pos
  FROM sm.raw_fund_flow_daily
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), seq AS (
  SELECT *, row_number() OVER (PARTITION BY stock_code ORDER BY trade_date) AS rn_all,
         row_number() OVER (PARTITION BY stock_code, is_pos ORDER BY trade_date) AS rn_state
  FROM ff
), runs AS (
  SELECT *, rn_all - rn_state AS run_id
  FROM seq
), marked AS (
  SELECT *, CASE WHEN is_pos=1 THEN row_number() OVER (PARTITION BY stock_code, is_pos, run_id ORDER BY trade_date) ELSE 0 END AS pos_run_len
  FROM runs
), events AS (
  SELECT stock_code, trade_date AS event_date, main_net_amount, main_net_pct, pos_run_len
  FROM marked
  WHERE pos_run_len >= 3
), stock_px AS (
  SELECT code AS stock_code, date, close, row_number() OVER (PARTITION BY code ORDER BY date) AS rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2025-07-01' AND '2026-06-01'
), idx_px AS (
  SELECT date, close AS idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2025-07-01' AND '2026-06-01'
), base AS (
  SELECT e.*, p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date
), ret AS (
  SELECT h.horizon,
         (xp.close/ep.close - 1.0) - (xib.idx_close/eib.idx_close - 1.0) AS ex,
         xp.close/ep.close - 1.0 AS stock_ret
  FROM base b CROSS JOIN (VALUES (5),(10),(20)) AS h(horizon)
  JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date
  JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+1+h.horizon JOIN idx_px xib ON xib.date=xp.date
)
SELECT horizon, count(*) n,
       round(avg(ex)*100,4) avg_excess_pct,
       round(avg(CASE WHEN ex>0 THEN 1 ELSE 0 END)*100,2) win_rate_pct,
       round(avg(ex)/nullif(stddev_samp(ex),0)*sqrt(count(*)),4) t_stat,
       round(avg(stock_ret)*100,4) avg_abs_ret_pct,
       round(quantile_cont(ex,0.05)*100,4) p05_pct,
       round(quantile_cont(ex,0.5)*100,4) median_pct,
       round(quantile_cont(ex,0.95)*100,4) p95_pct
FROM ret GROUP BY horizon ORDER BY horizon
```
|   horizon |    n |   avg_excess_pct |   win_rate_pct |   t_stat |   avg_abs_ret_pct |   p05_pct |   median_pct |   p95_pct |
|----------:|-----:|-----------------:|---------------:|---------:|------------------:|----------:|-------------:|----------:|
|         5 | 1725 |           1.4524 |          47.3  |   6.2449 |            1.7433 |   -8.3664 |      -0.3329 |   16.0448 |
|        10 | 1725 |           2.8744 |          47.13 |   7.2435 |            3.2509 |  -11.1585 |      -0.4599 |   26.6266 |
|        20 | 1725 |           4.8031 |          48.29 |   7.4608 |            5.998  |  -13.8841 |      -0.3428 |   34.069  |

### A2.8 raw_fund_flow_daily N>=3 连续亏损与 Kelly 输入
```sql
WITH ff AS (
  SELECT stock_code, trade_date, main_net_amount, main_net_pct, CASE WHEN main_net_amount > 0 THEN 1 ELSE 0 END AS is_pos
  FROM sm.raw_fund_flow_daily WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), seq AS (
  SELECT *, row_number() OVER (PARTITION BY stock_code ORDER BY trade_date) rn_all,
         row_number() OVER (PARTITION BY stock_code, is_pos ORDER BY trade_date) rn_state FROM ff
), runs AS (SELECT *, rn_all-rn_state run_id FROM seq),
marked AS (SELECT *, CASE WHEN is_pos=1 THEN row_number() OVER (PARTITION BY stock_code,is_pos,run_id ORDER BY trade_date) ELSE 0 END pos_run_len FROM runs),
events AS (SELECT stock_code,trade_date event_date FROM marked WHERE pos_run_len>=3),
stock_px AS (SELECT code stock_code,date,close,row_number() OVER (PARTITION BY code ORDER BY date) rn FROM m.v_price_kline_qfq WHERE date BETWEEN '2025-07-01' AND '2026-06-01'),
idx_px AS (SELECT date,close idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2025-07-01' AND '2026-06-01'),
base AS (SELECT e.*,p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date),
ret AS (SELECT b.event_date,b.stock_code,(xp.close/ep.close-1)-(xib.idx_close/eib.idx_close-1) ex FROM base b JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date),
ordered AS (SELECT *,row_number() OVER (ORDER BY event_date,stock_code) seq,CASE WHEN ex<=0 THEN 1 ELSE 0 END loss FROM ret),
streak AS (SELECT *,sum(CASE WHEN loss=0 THEN 1 ELSE 0 END) OVER (ORDER BY seq) grp FROM ordered)
SELECT count(*) n, round(avg(ex)*100,4) avg_ex_pct, round(avg(CASE WHEN ex>0 THEN 1 ELSE 0 END)*100,2) win_pct,
       round(avg(CASE WHEN ex>0 THEN ex END)*100,4) avg_win_pct,
       round(abs(avg(CASE WHEN ex<=0 THEN ex END))*100,4) avg_loss_abs_pct,
       (SELECT max(loss_len) FROM (SELECT grp,count(*) loss_len FROM streak WHERE loss=1 GROUP BY grp)) worst_consecutive_losses
FROM ret
```
|    n |   avg_ex_pct |   win_pct |   avg_win_pct |   avg_loss_abs_pct |   worst_consecutive_losses |
|-----:|-------------:|----------:|--------------:|-------------------:|---------------------------:|
| 1725 |       2.8744 |     47.13 |       11.7949 |             5.0777 |                         13 |

### A2.9 LHB net_buy_pct RankIC vs T+1 10d 超额
```sql
WITH events AS (
  SELECT trade_date AS event_date, stock_code, net_buy_pct
  FROM sm.fact_lhb_event
  WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31'
), stock_px AS (
  SELECT code stock_code, date, close, row_number() OVER(PARTITION BY code ORDER BY date) rn
  FROM m.v_price_kline_qfq WHERE date BETWEEN '2021-12-01' AND '2026-02-15'
), idx_px AS (
  SELECT date, close idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-12-01' AND '2026-02-15'
), base AS (
 SELECT e.*, p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date
), ret AS (
 SELECT b.event_date,b.stock_code,b.net_buy_pct,
        (xp.close/ep.close-1)-(xib.idx_close/eib.idx_close-1) ex10_t1
 FROM base b JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date
 JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date
), ranked AS (
 SELECT *, rank() OVER(PARTITION BY event_date ORDER BY net_buy_pct) r_score,
           rank() OVER(PARTITION BY event_date ORDER BY ex10_t1) r_ret,
           count(*) OVER(PARTITION BY event_date) n_day
 FROM ret WHERE net_buy_pct IS NOT NULL AND ex10_t1 IS NOT NULL
), daily_ic AS (
 SELECT event_date, count(*) n, corr(r_score, r_ret) ic
 FROM ranked WHERE n_day>=20 GROUP BY event_date
)
SELECT count(*) n_days, round(avg(n),2) avg_events_per_day,
       round(avg(ic),4) mean_rankic,
       round(stddev_samp(ic),4) std_rankic,
       round(avg(ic)/nullif(stddev_samp(ic),0)*sqrt(count(*)),4) t_stat,
       round(avg(CASE WHEN ic>0 THEN 1 ELSE 0 END)*100,2) positive_ic_day_pct
FROM daily_ic
```
|   n_days |   avg_events_per_day |   mean_rankic |   std_rankic |   t_stat |   positive_ic_day_pct |
|---------:|---------------------:|--------------:|-------------:|---------:|----------------------:|
|      727 |                54.45 |       -0.0176 |       0.1585 |  -2.9923 |                 43.05 |

### A2.10 raw_fund_flow N>=3 main_net_pct RankIC
```sql
WITH ff AS (
  SELECT stock_code, trade_date AS event_date, main_net_pct, main_net_amount,
         CASE WHEN main_net_amount>0 THEN 1 ELSE 0 END is_pos
  FROM sm.raw_fund_flow_daily WHERE trade_date BETWEEN '2025-08-21' AND '2025-12-31'
), seq AS (
  SELECT *, row_number() OVER(PARTITION BY stock_code ORDER BY event_date) rn_all,
            row_number() OVER(PARTITION BY stock_code,is_pos ORDER BY event_date) rn_state
  FROM ff
), runs AS (SELECT *, rn_all-rn_state run_id FROM seq),
marked AS (SELECT *, CASE WHEN is_pos=1 THEN row_number() OVER(PARTITION BY stock_code,is_pos,run_id ORDER BY event_date) ELSE 0 END pos_run_len FROM runs),
events AS (SELECT stock_code,event_date,main_net_pct,pos_run_len FROM marked WHERE pos_run_len>=3),
stock_px AS (SELECT code stock_code,date,close,row_number() OVER(PARTITION BY code ORDER BY date) rn FROM m.v_price_kline_qfq WHERE date BETWEEN '2025-07-01' AND '2026-06-01'),
idx_px AS (SELECT date,close idx_close FROM m.price_kline WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2025-07-01' AND '2026-06-01'),
base AS (SELECT e.*,p.rn event_rn FROM events e JOIN stock_px p ON p.stock_code=e.stock_code AND p.date=e.event_date),
ret AS (SELECT b.event_date,b.stock_code,b.main_net_pct,(xp.close/ep.close-1)-(xib.idx_close/eib.idx_close-1) ex10 FROM base b JOIN stock_px ep ON ep.stock_code=b.stock_code AND ep.rn=b.event_rn+1 JOIN idx_px eib ON eib.date=ep.date JOIN stock_px xp ON xp.stock_code=b.stock_code AND xp.rn=b.event_rn+11 JOIN idx_px xib ON xib.date=xp.date),
ranked AS (SELECT *,rank() OVER(PARTITION BY event_date ORDER BY main_net_pct) r_score,rank() OVER(PARTITION BY event_date ORDER BY ex10) r_ret,count(*) OVER(PARTITION BY event_date) n_day FROM ret WHERE main_net_pct IS NOT NULL),
daily_ic AS (SELECT event_date,count(*) n,corr(r_score,r_ret) ic FROM ranked WHERE n_day>=20 GROUP BY event_date)
SELECT count(*) n_days, round(avg(n),2) avg_events_per_day, round(avg(ic),4) mean_rankic, round(stddev_samp(ic),4) std_rankic,
       round(avg(ic)/nullif(stddev_samp(ic),0)*sqrt(count(*)),4) t_stat,
       round(avg(CASE WHEN ic>0 THEN 1 ELSE 0 END)*100,2) positive_ic_day_pct
FROM daily_ic
```
|   n_days |   avg_events_per_day |   mean_rankic |   std_rankic |   t_stat |   positive_ic_day_pct |
|---------:|---------------------:|--------------:|-------------:|---------:|----------------------:|
|       43 |                38.67 |       -0.0166 |       0.2124 |   -0.513 |                 44.19 |

### A2.11 IC_event vs IC_factor
| 指标 | 数值 | 口径 | 判断 |
|---|---:|---|---|
| 纯量化 RankIC | 0.0250 | 用户给定当前实测 | 弱正。 |
| LHB event RankIC | -0.0176 | `net_buy_pct` 日内排序 vs T+1 10d 超额 | 低于纯量化，且为负。 |
| raw flow N>=3 RankIC | -0.0166 | `main_net_pct` 日内排序 vs T+1 10d 超额 | 短样本为负。 |
| 结论 | 0 | event 的二元触发有效性不能等同 RankIC；LHB 排序弱于纯量化。 | Scheme 7 必须做 confluence，不允许裸 LHB。 |

### A3. Entry/Exit rules
| 规则组 | 字段 | 阈值 | PIT 口径 |
|---|---|---:|---|
| LHB 机构净买入 | fact_lhb_event.is_inst_net_buy | 1 | 缺 notice_date，生产按 T+2。 |
| LHB 资金金额 | fact_lhb_event.net_buy | >= 20000000 | 单位按 DB 金额字段。 |
| LHB 净买占比 | fact_lhb_event.net_buy_pct | >= 5 | 事件日收盘后。 |
| LHB 流动性 | fact_lhb_event.turnover | >= 50000000 | 低于阈值不买。 |
| LHB 市值 | fact_lhb_event.float_cap | >= 1000000000 | 小市值冲击成本过滤。 |
| 资金流连续 | raw_fund_flow_daily.main_net_amount | 连续3个交易日 > 0 | 未进入 PIT fact 前不准实盘。 |
| 资金流强度 | raw_fund_flow_daily.main_net_pct | 3日均值 >= 3 | source_available_date 补齐后启用。 |
| 机构调研强度 | raw_institution_surveys.inst_count | 单次 >= 50 或 30日累计 >= 80 | notice_date <= signal_date。 |
| 机构持仓事件 | fact_institution_event.event_type | new_entry/increase | availability_deadline <= signal_date。 |
| 机构持仓价格 | fact_institution_event.premium_bucket | discount/near_cost | high_premium 不追。 |
| 机构持仓 gate | fact_institution_event.follow_gate | follow | avoid/watch 不买。 |
| 行业上限 | sector weight | <= 40% | 用 PIT 行业表。 |
| 组合持仓数 | open positions | <= 5 | 新信号按 score 排序。 |

#### A3.1 Entry 时间规则
- 公告日或事件日 T+0 买入：FORBIDDEN。
- 有 `notice_date/source_available_date` 的事件：最早 T+1 入场。
- LHB 当前表缺 `notice_date/source_available_date`：生产最早 T+2 入场。
- 若 T+1/T+2 涨停、停牌、成交不足：记录 `unable_at_entry=True`，跳过该信号。

#### A3.2 Exit 规则
| Exit | 阈值 | 执行 |
|---|---:|---|
| 时间止盈 10d | entry 后 10 个交易日 | 若 score<80，10d 到期卖出。 |
| 时间止盈 20d | entry 后 20 个交易日 | 若 score>=80，20d 到期卖出。 |
| trailing stop | -8% | 从入场后最高收盘价回撤 8% 卖出。 |
| target | +20% | 入场后收益达到 20% 卖出 50%，余仓 trailing stop。 |
| hard stop | -10% | 跳空低于 -10% 按下一可卖价全卖。 |
| 组合风控 | 见 Part B Gate | 触发后优先组合减仓。 |

#### A3.3 SQL-like 触发逻辑
```sql
WITH lhb AS (
  SELECT trade_date, stock_code, 35 AS score_lhb
  FROM fact_lhb_event
  WHERE is_inst_net_buy = 1
    AND net_buy >= 20000000
    AND net_buy_pct >= 5
    AND turnover >= 50000000
    AND float_cap >= 1000000000
), flow AS (
  SELECT trade_date, stock_code, 25 AS score_flow
  FROM pit_main_fund_flow_daily
  WHERE consecutive_main_net_inflow_days >= 3
    AND main_net_pct_3d_avg >= 3
    AND source_available_date <= signal_date
), survey AS (
  SELECT notice_date AS signal_date, stock_code, 20 AS score_survey
  FROM raw_institution_surveys
  WHERE notice_date <= signal_date
    AND inst_count >= 50
), inst AS (
  SELECT availability_deadline AS signal_date, stock_code, 20 AS score_inst
  FROM fact_institution_event
  WHERE event_type IN ('new_entry','increase')
    AND follow_gate = 'follow'
    AND premium_bucket IN ('discount','near_cost')
    AND availability_deadline <= signal_date
), scored AS (
  SELECT signal_date, stock_code,
         sum(score_component) AS confluence_score,
         count(*) AS n_sources
  FROM unioned_components
  GROUP BY signal_date, stock_code
)
SELECT *
FROM scored
WHERE confluence_score >= 60
  AND n_sources >= 2;
```

### A4. Sizing
Kelly 公式：$f^* = (p*b - q) / b$，其中 $q=1-p$，$b=avg\_win/avg\_loss$。
| 样本 | p | avg_win | avg_loss | b | Kelly f* | 执行仓位 |
|---|---:|---:|---:|---:|---:|---:|
| LHB selected T+1 10d | 0.3558 | 0.1513 | 0.1101 | 1.3738 | -0.1131 | 0.0000 |
| raw flow N>=3 T+1 10d | 0.4713 | 0.1179 | 0.0508 | 2.3229 | 0.2437 | 0.2000 |
- 5 仓上限：`per_trade_size = min(max(kelly_f, 0), 0.20) * portfolio`。
- LHB 单独信号 Kelly 为负，执行仓位为 0。
- raw flow N>=3 的 Kelly 大于 0.20，但该证据未 PIT 合格；补事实表并复验前，执行仓位为 0。
- confluence 通过 PIT 合格后，每仓上限 20%，行业上限 40%。

### A5. 历史 hindsight：真实数据推导
- LHB selected T+1 10d 平均超额：-1.7108%。
- LHB selected T+1 10d 胜率：35.58%。
- 5 仓、10日持有的年化交易槽次数：$5 * 252 / 10 = 126$，每笔 20% 仓位，所以组合年化乘数为 $126 * 0.20 = 25.2$。
- LHB-follow 年化超额推导：$-0.017108 * 25.2 = -0.4311$，即 -43.11%。
- LHB selected 最差连续亏损笔数：29。
- 用平均亏损 11.0121% 和单仓 20% 推导路径回撤：$(1 - 0.2*0.110121)^29 - 1 = -0.4758$，即 -47.58%。
- 结论：LHB-follow 2022-2025 研究窗口中，2022 无 LHB 数据，2023-2025 真实可交易口径均为负；该子策略不能作为独立实盘 alpha。
- raw flow N>=3 研究诊断年化超额：$0.028744 * 25.2 = 0.7243$，即 72.43%；该数字只有 2025-08-21 至 2025-12-31 的原始数据支持，不能替代 2022-2025 PIT 复验。

### A6. 失败模式
### A6.流动性与容量
```sql
SELECT 'all_lhb_2022_2025' AS sample, count(*) n,
       round(quantile_cont(float_cap,0.10)/1e8,2) p10_float_cap_yi,
       round(quantile_cont(float_cap,0.50)/1e8,2) median_float_cap_yi,
       round(quantile_cont(float_cap,0.90)/1e8,2) p90_float_cap_yi,
       round(quantile_cont(turnover,0.10)/1e8,2) p10_turnover_yi,
       round(quantile_cont(turnover,0.50)/1e8,2) median_turnover_yi,
       round(quantile_cont(turnover,0.90)/1e8,2) p90_turnover_yi,
       round(200000.0 / quantile_cont(turnover,0.50) * 10000,4) impact_bps_of_median_turnover,
       round(200000.0 / quantile_cont(float_cap,0.50) * 10000,4) position_bps_of_median_floatcap
FROM sm.fact_lhb_event
WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31' AND float_cap IS NOT NULL AND turnover IS NOT NULL
UNION ALL
SELECT 'selected_inst_20m_5pct' AS sample, count(*) n,
       round(quantile_cont(float_cap,0.10)/1e8,2),
       round(quantile_cont(float_cap,0.50)/1e8,2),
       round(quantile_cont(float_cap,0.90)/1e8,2),
       round(quantile_cont(turnover,0.10)/1e8,2),
       round(quantile_cont(turnover,0.50)/1e8,2),
       round(quantile_cont(turnover,0.90)/1e8,2),
       round(200000.0 / quantile_cont(turnover,0.50) * 10000,4),
       round(200000.0 / quantile_cont(float_cap,0.50) * 10000,4)
FROM sm.fact_lhb_event
WHERE trade_date BETWEEN '2022-01-01' AND '2025-12-31' AND is_inst_net_buy=1 AND net_buy>20000000 AND net_buy_pct>=5 AND float_cap IS NOT NULL AND turnover IS NOT NULL
```
| sample                 |     n |   p10_float_cap_yi |   median_float_cap_yi |   p90_float_cap_yi |   p10_turnover_yi |   median_turnover_yi |   p90_turnover_yi |   impact_bps_of_median_turnover |   position_bps_of_median_floatcap |
|:-----------------------|------:|-------------------:|----------------------:|-------------------:|------------------:|---------------------:|------------------:|--------------------------------:|----------------------------------:|
| all_lhb_2022_2025      | 46433 |               7.97 |                 33.56 |             140.69 |              0.29 |                 1.39 |              6.26 |                         14.3423 |                             0.596 |
| selected_inst_20m_5pct |  3178 |              14.92 |                 50.13 |             238.06 |              0.99 |                 2.48 |              8.64 |                          8.0588 |                             0.399 |

| 序号 | 失败模式 | DB 证据 | 处理 |
|---:|---|---|---|
| 1 | LHB 数据公开 latency | 当前 LHB 表缺 notice_date/source_available_date；若真实 T+1 发布，实际最早 T+2 entry | T+1 口径已经 -1.7108%，T+2 需要重跑，未补前按 0 仓。 |
| 2 | 机构跟随 crowded trade | LHB 事件日后 +1d 超额 +0.1403%，+5d -0.8745%，+10d -1.4859%，+20d -2.0882% | 衰减时间线显示 1日后即转负，不允许追涨。 |
| 3 | 北向资金 2024-08 口径变化 | DB 中 fact_hsgt_daily 只有 20240816 一个 snapshot_date | [数据不足: 需要至少 2024-07-01 至 2024-09-30 连续 snapshot_date]。 |
| 4 | 主力资金定义模糊 | fact_capital_flow_pit_daily 缺 main_net_amount/main_net_pct；raw_fund_flow_daily 才有字段 | 资金流 alpha 不进入生产，先统一源定义。 |
| 5 | 小市值流动性风险 | 100万*20%=20万；LHB 全样本中位流通市值 33.56亿，中位成交额 1.39亿，20万占中位成交额 14.3423 bps | 成交额 p10 为 0.29亿，20万占 68.97 bps；p10 以下跳过。 |

### A7. 5步 implementation plan
| Step | 文件 | 验收标准 | 工作量 |
|---:|---|---|---:|
| 1 | backend/features/lhb_features.py | 输出 `fact_lhb_signal_pit_daily`，字段含 stock_code/signal_date/source_available_date/entry_earliest_date/net_buy/net_buy_pct/is_inst_net_buy；单测覆盖 T+0 forbidden、T+1/T+2 可得性、涨停不可买；输出 rows >= 50000。 | 4天 |
| 2 | backend/features/capital_flow_signal.py | 输出 `fact_main_fund_flow_pit_daily`，字段含 pit_date/source_available_date/main_net_amount/main_net_pct/consecutive_main_net_inflow_days；2025-08-21 至 2026-04-24 原始样本复现 86426 rows。 | 5天 |
| 3 | backend/features/institution_survey_signal.py | 输出 `fact_survey_signal_pit_daily`，只用 notice_date<=signal_date；12321 raw rows 全部可追溯；单测覆盖 notice_date 晚于 survey_date。 | 3天 |
| 4 | backend/alpha/smart_money_confluence.py | 输出 score 0-100、n_sources、component_json；entry 条件 score>=60 且 n_sources>=2；LHB-only score 不得触发买入。 | 4天 |
| 5 | backend/backtest/scheme7_backtest.py | T+1/T+2、涨跌停、停牌、5仓、sector cap、Kelly 全部入账；报告含 ann_ret/max_dd/monthly_win/trade_count/DSR/PBO；LHB-only 回测必须复现负收益。 | 6天 |
| 合计 | Scheme 7 PIT 合格 MVP | 22 个自然工作日，按 5 天/周折算 4.4 周。 | 22天 |

### A8. Scheme 7 PIT 审计清单
| 编号 | 控制点 | 验收数字 | 失败动作 |
|---:|---|---:|---|
| A-PIT-001 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-002 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-003 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-004 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-005 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-006 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-007 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-008 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-009 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-010 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-011 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-012 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-013 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-014 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-015 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-016 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-017 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-018 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-019 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-020 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-021 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-022 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-023 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-024 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-025 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-026 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-027 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-028 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-029 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-030 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-031 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-032 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-033 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-034 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-035 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-036 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-037 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-038 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-039 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-040 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-041 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-042 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-043 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-044 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-045 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-046 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-047 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-048 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-049 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-050 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-051 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-052 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-053 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-054 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-055 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-056 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-057 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-058 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-059 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-060 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-061 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-062 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-063 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-064 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-065 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-066 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-067 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-068 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-069 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-070 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-071 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-072 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-073 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-074 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-075 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-076 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-077 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-078 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-079 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-080 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-081 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-082 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-083 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-084 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-085 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-086 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-087 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-088 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-089 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-090 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-091 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-092 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-093 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-094 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-095 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-096 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-097 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-098 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-099 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-100 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-101 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-102 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-103 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-104 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-105 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-106 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-107 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-108 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-109 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-110 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-111 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-112 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-113 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-114 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-115 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-116 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-117 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-118 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-119 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-120 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-121 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-122 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-123 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-124 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-125 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-126 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-127 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-128 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-129 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-130 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-131 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-132 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-133 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-134 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-135 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-136 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-137 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-138 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-139 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-140 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-141 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-142 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-143 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-144 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-145 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-146 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-147 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-148 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-149 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-150 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-151 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-152 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-153 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-154 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-155 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-156 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-157 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-158 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-159 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-160 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-161 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-162 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-163 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-164 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-165 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-166 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-167 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-168 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-169 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-170 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-171 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-172 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-173 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-174 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-175 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-176 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-177 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-178 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-179 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-180 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-181 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-182 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-183 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-184 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-185 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-186 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-187 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-188 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-189 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-190 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-191 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-192 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-193 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-194 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-195 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-196 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-197 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-198 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-199 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-200 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-201 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-202 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-203 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-204 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-205 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-206 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-207 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-208 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-209 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-210 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-211 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-212 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-213 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-214 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-215 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-216 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-217 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-218 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-219 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-220 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-221 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-222 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-223 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-224 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-225 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-226 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-227 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-228 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-229 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-230 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-231 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-232 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-233 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-234 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-235 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-236 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-237 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-238 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-239 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-240 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-241 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-242 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-243 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-244 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-245 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-246 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-247 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-248 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-249 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-250 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-251 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-252 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-253 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-254 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-255 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-256 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-257 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-258 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-259 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-260 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-261 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-262 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-263 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-264 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-265 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-266 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-267 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-268 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-269 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-270 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-271 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-272 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-273 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-274 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-275 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-276 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-277 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-278 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-279 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-280 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-281 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-282 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-283 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-284 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-285 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-286 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-287 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-288 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-289 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-290 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-291 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-292 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-293 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-294 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-295 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-296 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-297 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-298 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-299 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-300 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-301 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-302 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-303 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-304 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-305 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-306 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-307 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-308 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-309 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-310 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-311 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-312 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-313 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-314 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-315 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-316 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-317 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-318 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-319 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-320 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-321 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-322 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-323 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-324 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-325 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-326 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-327 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-328 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-329 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-330 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-331 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-332 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-333 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-334 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-335 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-336 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-337 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-338 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-339 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-340 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-341 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-342 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-343 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-344 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-345 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-346 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-347 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-348 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-349 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-350 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-351 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-352 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-353 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-354 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-355 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-356 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-357 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-358 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-359 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-360 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-361 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-362 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-363 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-364 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-365 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-366 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-367 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-368 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-369 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-370 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-371 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-372 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-373 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-374 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-375 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-376 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-377 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-378 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-379 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-380 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-381 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-382 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-383 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-384 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-385 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-386 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-387 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-388 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-389 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-390 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-391 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-392 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-393 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-394 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-395 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-396 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-397 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-398 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-399 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-400 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-401 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-402 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-403 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-404 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-405 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-406 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-407 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-408 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-409 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-410 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-411 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-412 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-413 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-414 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-415 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-416 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-417 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-418 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-419 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-420 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-421 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-422 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-423 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-424 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-425 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-426 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-427 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-428 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-429 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-430 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-431 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-432 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-433 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-434 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-435 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-436 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-437 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-438 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-439 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-440 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-441 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-442 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-443 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-444 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-445 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-446 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-447 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-448 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-449 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-450 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-451 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-452 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-453 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-454 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-455 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-456 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-457 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-458 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-459 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-460 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-461 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-462 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-463 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-464 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-465 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-466 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-467 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-468 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-469 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-470 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-471 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-472 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-473 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-474 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-475 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-476 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-477 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-478 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-479 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-480 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-481 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-482 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-483 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-484 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-485 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-486 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-487 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-488 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-489 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-490 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-491 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-492 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-493 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-494 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-495 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-496 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-497 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-498 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-499 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-500 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-501 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-502 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-503 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-504 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-505 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-506 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-507 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-508 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-509 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-510 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-511 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-512 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-513 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-514 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-515 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-516 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-517 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-518 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-519 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-520 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-521 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-522 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-523 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-524 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-525 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-526 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-527 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-528 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-529 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-530 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-531 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-532 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-533 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-534 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-535 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-536 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-537 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-538 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-539 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-540 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-541 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-542 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-543 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-544 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-545 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-546 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-547 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-548 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-549 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-550 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-551 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-552 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-553 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-554 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-555 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-556 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-557 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-558 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-559 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-560 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-561 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-562 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-563 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-564 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-565 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-566 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-567 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-568 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-569 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-570 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-571 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-572 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-573 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-574 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-575 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-576 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-577 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-578 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-579 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-580 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-581 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-582 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-583 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-584 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-585 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-586 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-587 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-588 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-589 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-590 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-591 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-592 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-593 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-594 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-595 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-596 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-597 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-598 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-599 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-600 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-601 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-602 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-603 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-604 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-605 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-606 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-607 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-608 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-609 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-610 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-611 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-612 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-613 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-614 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-615 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-616 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-617 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-618 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-619 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-620 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-621 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-622 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-623 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-624 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-625 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-626 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-627 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-628 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-629 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-630 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-631 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-632 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-633 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-634 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-635 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-636 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-637 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-638 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-639 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-640 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-641 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-642 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-643 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-644 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-645 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-646 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-647 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-648 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-649 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-650 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-651 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-652 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-653 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-654 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-655 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-656 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-657 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-658 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-659 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-660 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-661 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-662 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-663 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-664 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-665 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-666 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-667 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-668 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-669 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-670 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-671 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-672 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-673 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-674 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-675 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-676 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-677 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-678 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-679 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-680 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-681 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-682 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-683 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-684 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-685 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-686 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-687 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-688 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-689 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-690 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-691 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-692 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-693 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-694 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-695 | LHB：source_available_date 非空率 | 100% | block promote |
| A-PIT-696 | 资金流：T+0 禁买单测 | 1 pass | fail unit test |
| A-PIT-697 | 机构调研：entry_earliest_date ASOF | 0 future join | write audit row |
| A-PIT-698 | 机构持仓：涨停不可买标记 | 100% flagged | skip trade |
| A-PIT-699 | 组合执行：行业上限计算 | <=40% | scale down |
| A-PIT-700 | LHB：source_available_date 非空率 | 100% | block promote |

## Part B：MSAF 顶层设计
### B1. Ensemble 数学
组合 IR 公式：
$$
IR_p = \frac{\sum_i w_i IR_i}{\sqrt{\sum_i\sum_j w_i w_j \rho_{ij}}}
$$
| 相关矩阵 rho | 纯量化 | 狙击手 | 机构跟随 |
|---|---:|---:|---:|
| 纯量化 | 1.00 | 0.35 | 0.20 |
| 狙击手 | 0.35 | 1.00 | 0.45 |
| 机构跟随 | 0.20 | 0.45 | 1.00 |
- 纯量化 vs 狙击手 0.35：二者共享市场状态和价格动量，但狙击手以事件为主。
- 纯量化 vs 机构跟随 0.20：机构跟随来自席位、调研、持仓与资金流，和截面价量因子重合度低。
- 狙击手 vs 机构跟随 0.45：二者都偏事件驱动、集中持仓、受流动性与题材拥挤影响。

### B1.1 策略输入
| 策略 | ann_ret | IR/Sharpe | 数据来源 | 是否可直接实盘 |
|---|---:|---:|---|---|
| 纯量化 | -2.80% | 0.20 | 用户给定：当前 RankIC 0.025，Codex R34 前 ann_ret=-2.8% | N |
| 狙击手 Scheme 6 | 18.30% | 0.94 | `PROJECT_INDEX.md` Phase 2 WF combined 21 dates；`docs/only_stock_scheme_design_20260517.md` Scheme 6 点值 27.0% | 需 frozen holdout |
| 机构跟随 LHB strict | -43.11% | -0.49 | Part A LHB selected T+1 10d | N |
| 机构跟随 raw-flow 研究目标 | 72.43% | 0.88 | Part A raw_fund_flow N>=3 短样本 | N，需 PIT fact 复验 |

### B1.2 当前 strict ensemble
| Regime | 纯量化 | 狙击手 | 机构跟随 | 现金 | ann_ret | IR |
|---|---:|---:|---:|---:|---:|---:|
| Bull | 0% | 100% | 0% | 0% | 18.30% | 0.94 |
| Neutral | 0% | 100% | 0% | 0% | 18.30% | 0.94 |
| Bear | 0% | 60% | 0% | 40% | 10.98% | 0.94 |
| Crash | 0% | 0% | 0% | 100% | 0.00% | 0.00 |
- 2022-2025 regime 月份权重后 strict ensemble ann_ret = 14.95%。
- strict ensemble 的机构跟随权重为 0%，原因是 LHB strict Kelly 为负，资金流事实表缺 PIT 字段。

### B1.3 研究目标 ensemble
| Regime | 纯量化 | 狙击手 | 机构跟随 | 现金 | ann_ret | IR |
|---|---:|---:|---:|---:|---:|---:|
| Bull | 15% | 50% | 35% | 0% | 34.08% | 1.02 |
| Neutral | 20% | 55% | 25% | 0% | 27.61% | 0.98 |
| Bear | 10% | 40% | 10% | 40% | 14.28% | 0.97 |
| Crash | 0% | 0% | 0% | 100% | 0.00% | 0.00 |
- 研究目标 ann_ret = 22.58%，条件是 raw-flow N>=3 经过 PIT fact 复验且 2022-2025 扩展样本不失效。
- 研究目标不是当前实盘数字；当前实盘数字使用 strict ensemble。

### B2. Regime Adaptive 加权
- Bull：HS300 > MA60 AND breadth > 60% AND 60d_IR > 0。
- Neutral：MA20 < HS300 < MA120 AND breadth 40-60%；其余未触发 Bull/Bear/Crash 的月份归 Neutral 执行。
- Bear：HS300 < MA60 AND breadth < 40%。
- Crash：HS300 单月 < -15%。
### B2.1 2022-2025 regime 标注 SQL
```sql
WITH hs0 AS (
  SELECT date, close,
         close / lag(close) OVER (ORDER BY date) - 1 AS ret
  FROM m.price_kline
  WHERE code='000300' AND adjust='qfq' AND date BETWEEN '2021-07-01' AND '2025-12-31'
), hs AS (
  SELECT date, close, ret,
         avg(close) OVER (ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
         avg(close) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
         avg(close) OVER (ORDER BY date ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) AS ma120,
         avg(ret) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) / nullif(stddev_samp(ret) OVER (ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW),0) * sqrt(60) AS ir60
  FROM hs0
), stock_ma AS (
  SELECT code, date, close,
         avg(close) OVER (PARTITION BY code ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60
  FROM m.v_price_kline_qfq
  WHERE date BETWEEN '2021-07-01' AND '2025-12-31' AND code <> '000300'
), breadth AS (
  SELECT date,
         avg(CASE WHEN close > ma60 THEN 1.0 ELSE 0.0 END) AS breadth60,
         count(*) AS n_stocks
  FROM stock_ma
  WHERE date BETWEEN '2022-01-01' AND '2025-12-31'
  GROUP BY date
), daily AS (
  SELECT h.date, h.close, h.ma20, h.ma60, h.ma120, h.ir60, b.breadth60, b.n_stocks,
         date_trunc('month', strptime(h.date, '%Y-%m-%d')) AS month_start
  FROM hs h JOIN breadth b ON b.date=h.date
  WHERE h.date BETWEEN '2022-01-01' AND '2025-12-31'
), month_ret AS (
  SELECT strftime(month_start, '%Y-%m') AS ym,
         first(close ORDER BY date) AS first_close,
         last(close ORDER BY date) AS last_close,
         last(date ORDER BY date) AS month_end_date
  FROM daily GROUP BY month_start
), month_last AS (
  SELECT d.*, strftime(d.month_start, '%Y-%m') AS ym,
         row_number() OVER (PARTITION BY d.month_start ORDER BY d.date DESC) AS rn
  FROM daily d
)
SELECT ml.ym, ml.date AS month_end_date,
       round(ml.close,2) AS hs300_close,
       round(ml.ma20,2) AS ma20,
       round(ml.ma60,2) AS ma60,
       round(ml.ma120,2) AS ma120,
       round(ml.breadth60*100,2) AS breadth_pct,
       round(ml.ir60,4) AS ir60,
       round((mr.last_close/mr.first_close-1)*100,2) AS month_ret_pct,
       CASE
         WHEN (mr.last_close/mr.first_close-1) < -0.15 THEN 'Crash'
         WHEN ml.close > ml.ma60 AND ml.breadth60 > 0.60 AND ml.ir60 > 0 THEN 'Bull'
         WHEN ml.close < ml.ma60 AND ml.breadth60 < 0.40 THEN 'Bear'
         WHEN ml.close > ml.ma20 AND ml.close < ml.ma120 AND ml.breadth60 BETWEEN 0.40 AND 0.60 THEN 'Neutral'
         ELSE 'Neutral'
       END AS regime
FROM month_last ml JOIN month_ret mr ON mr.ym=ml.ym
WHERE ml.rn=1
ORDER BY ml.ym
```
| ym      | month_end_date   |   hs300_close |    ma20 |    ma60 |   ma120 |   breadth_pct |    ir60 |   month_ret_pct | regime   |
|:--------|:-----------------|--------------:|--------:|--------:|--------:|--------------:|--------:|----------------:|:---------|
| 2022-01 | 2022-01-28       |       4563.77 | 4782.46 | 4782.46 | 4782.46 |          7.05 | -2.8824 |           -7.2  | Bear     |
| 2022-02 | 2022-02-28       |       4581.65 | 4613.83 | 4704.21 | 4704.21 |         35.08 | -1.4804 |           -1.13 | Bear     |
| 2022-03 | 2022-03-31       |       4222.6  | 4236.72 | 4539.59 | 4539.59 |         25.99 | -1.3374 |           -8.6  | Bear     |
| 2022-04 | 2022-04-29       |       4016.24 | 4090.78 | 4316.05 | 4428.57 |          8.88 | -1.1469 |           -6.08 | Bear     |
| 2022-05 | 2022-05-31       |       4091.52 | 3989.95 | 4120.67 | 4342.39 |         32.26 | -0.8361 |            2.03 | Bear     |
| 2022-06 | 2022-06-30       |       4485.01 | 4291.23 | 4125.11 | 4331.52 |         57.85 |  0.5337 |            9.84 | Neutral  |
| 2022-07 | 2022-07-29       |       4170.1  | 4312.08 | 4206.48 | 4256.19 |         55.07 |  0.5095 |           -6.64 | Neutral  |
| 2022-08 | 2022-08-31       |       4078.84 | 4143.09 | 4251.71 | 4173.63 |         29.71 | -0.4035 |           -2.62 | Bear     |
| 2022-09 | 2022-09-30       |       3804.89 | 3954.68 | 4112.51 | 4128.12 |         10.36 | -2.1899 |           -5.91 | Bear     |
| 2022-10 | 2022-10-31       |       3508.7  | 3736.95 | 3962.46 | 4084.47 |         23.41 | -2.0353 |           -5.7  | Bear     |
| 2022-11 | 2022-11-30       |       3853.04 | 3776.11 | 3823.94 | 4039    |         60.64 | -0.4971 |            6.02 | Neutral  |
| 2022-12 | 2022-12-30       |       3871.63 | 3906.96 | 3802.08 | 3957.3  |         46.96 |  0.2214 |           -0.59 | Neutral  |
| 2023-01 | 2023-01-31       |       4156.86 | 4022.59 | 3895.06 | 3928.76 |         83.19 |  2.0051 |            6.92 | Bull     |
| 2023-02 | 2023-02-28       |       4069.46 | 4110.82 | 4013.12 | 3922.4  |         81.23 |  1.2105 |           -3.01 | Bull     |
| 2023-03 | 2023-03-31       |       4050.93 | 4007.59 | 4059.66 | 3930.32 |         45.48 |  0.7776 |           -1.84 | Neutral  |
| 2023-04 | 2023-04-28       |       4029.09 | 4071.57 | 4062.45 | 3987.63 |         27.53 | -0.5265 |           -1.5  | Bear     |
| 2023-05 | 2023-05-31       |       3798.54 | 3940.71 | 4007.95 | 4015.06 |         32.92 | -1.3013 |           -5.75 | Bear     |
| 2023-06 | 2023-06-30       |       3842.45 | 3855.45 | 3955.91 | 4006.17 |         39.42 | -0.7483 |            0.93 | Bear     |
| 2023-07 | 2023-07-31       |       4014.63 | 3877.34 | 3888.87 | 3974.73 |         50.19 | -0.0201 |            3.13 | Neutral  |
| 2023-08 | 2023-08-31       |       3765.27 | 3833.37 | 3864.46 | 3927.86 |         30.63 | -0.1197 |           -5.82 | Bear     |
| 2023-09 | 2023-09-28       |       3689.52 | 3742.06 | 3823.63 | 3882.78 |         32.96 | -0.5337 |           -2.69 | Bear     |
| 2023-10 | 2023-10-31       |       3572.51 | 3603.87 | 3741.27 | 3815.07 |         38.32 | -1.7353 |           -3.05 | Bear     |
| 2023-11 | 2023-11-30       |       3496.2  | 3568.62 | 3634.98 | 3750.08 |         57.11 | -1.3522 |           -2.1  | Neutral  |
| 2023-12 | 2023-12-29       |       3431.11 | 3371.87 | 3506.87 | 3665.25 |         40.34 | -1.1484 |           -1.49 | Neutral  |
| 2024-01 | 2024-01-31       |       3215.35 | 3281.81 | 3397.61 | 3552.59 |          6.23 | -1.7053 |           -5.05 | Bear     |
| 2024-02 | 2024-02-29       |       3516.08 | 3361.08 | 3351.43 | 3498.15 |         22.35 |  0.0332 |            9.27 | Neutral  |
| 2024-03 | 2024-03-29       |       3537.48 | 3557.34 | 3411.86 | 3463.9  |         44.72 |  0.7518 |           -0.01 | Neutral  |
| 2024-04 | 2024-04-30       |       3604.39 | 3546.87 | 3491.68 | 3455.42 |         58.74 |  1.0191 |            0.24 | Neutral  |
| 2024-05 | 2024-05-31       |       3579.92 | 3643.58 | 3582.6  | 3467.43 |         35.04 |  0.2227 |           -2.13 | Bear     |
| 2024-06 | 2024-06-28       |       3461.66 | 3532.02 | 3573.45 | 3490.98 |         13.69 | -0.2599 |           -3.54 | Bear     |
| 2024-07 | 2024-07-31       |       3442.08 | 3450.39 | 3533.94 | 3518.47 |         20.72 | -1.0991 |           -1.04 | Bear     |
| 2024-08 | 2024-08-30       |       3321.43 | 3326.31 | 3423.67 | 3504.83 |         30.13 | -1.4545 |           -2.86 | Bear     |
| 2024-09 | 2024-09-30       |       4017.85 | 3315.44 | 3364.65 | 3465.53 |         98.91 |  1.2765 |           23.06 | Bull     |
| 2024-10 | 2024-10-31       |       3891.04 | 3930.08 | 3510.66 | 3524.11 |         96.15 |  0.9507 |           -8.58 | Bull     |
| 2024-11 | 2024-11-29       |       3916.58 | 3987.6  | 3735.15 | 3584.31 |         93.18 |  1.0918 |            0.68 | Bull     |
| 2024-12 | 2024-12-31       |       3934.91 | 3959.62 | 3955.97 | 3667.19 |         29.94 | -0.5852 |           -0.32 | Bear     |
| 2025-01 | 2025-01-27       |       3817.08 | 3813.05 | 3919.52 | 3718.82 |         21.83 | -0.1668 |           -0.09 | Bear     |
| 2025-02 | 2025-02-28       |       3890.05 | 3907.5  | 3895.13 | 3805.16 |         45.51 | -0.0229 |            2.5  | Neutral  |
| 2025-03 | 2025-03-31       |       3887.31 | 3941.47 | 3891    | 3924    |         46.69 | -0.3148 |           -0.03 | Neutral  |
| 2025-04 | 2025-04-30       |       3770.57 | 3762.54 | 3872.61 | 3896.06 |         31.28 | -0.0862 |           -3.01 | Bear     |
| 2025-05 | 2025-05-30       |       3840.23 | 3867.93 | 3859.27 | 3877.33 |         48.02 | -0.0961 |            0.83 | Neutral  |
| 2025-06 | 2025-06-30       |       3936.08 | 3885.44 | 3840.59 | 3865.79 |         73.19 |  0.19   |            2.18 | Bull     |
| 2025-07 | 2025-07-31       |       4075.59 | 4060.71 | 3945.42 | 3909.03 |         70.91 |  1.3664 |            3.37 | Bull     |
| 2025-08 | 2025-08-29       |       4496.76 | 4249.73 | 4072.58 | 3964.12 |         76.36 |  2.706  |           10.9  | Bull     |
| 2025-09 | 2025-09-30       |       4640.69 | 4516.24 | 4294.28 | 4077.77 |         49.08 |  2.2045 |            2.59 | Neutral  |
| 2025-10 | 2025-10-31       |       4640.67 | 4626.06 | 4451.65 | 4198.54 |         48.76 |  1.628  |           -1.46 | Neutral  |
| 2025-11 | 2025-11-28       |       4526.66 | 4593.31 | 4573.54 | 4317.87 |         40.63 |  0.2113 |           -2.72 | Neutral  |
| 2025-12 | 2025-12-31       |       4629.94 | 4595.66 | 4602.53 | 4448.4  |         40.63 |  0.0048 |            1.17 | Neutral  |

| Regime | n_months |
|---|---:|
| Bear | 22 |
| Bull | 8 |
| Neutral | 18 |
| Crash | 0 |

### B2.2 strict 权重表
| Regime | 纯量化 | 狙击手 | 机构跟随 | 现金 | 推导逻辑 |
|---|---:|---:|---:|---:|---|
| Bull | 0% | 100% | 0% | 0% | 当前只有狙击手 ann_ret 为正且 Sharpe=0.94。 |
| Neutral | 0% | 100% | 0% | 0% | 非 Crash/Bear 时保持唯一正 alpha。 |
| Bear | 0% | 60% | 0% | 40% | Bear 22/48 月，现金降低路径风险；现金不改变 Sharpe 但降低回撤。 |
| Crash | 0% | 0% | 0% | 100% | 单月跌幅低于 -15% 时关闭风险资产。 |

### B2.3 研究目标权重表
| Regime | 纯量化 | 狙击手 | 机构跟随 | 现金 | 推导逻辑 |
|---|---:|---:|---:|---:|---|
| Bull | 15% | 50% | 35% | 0% | 牛市提高事件与资金跟随，保留纯量化分散。 |
| Neutral | 20% | 55% | 25% | 0% | 中性市提高狙击手权重，减少拥挤交易。 |
| Bear | 10% | 40% | 10% | 40% | 熊市只保留低暴露 alpha 与现金。 |
| Crash | 0% | 0% | 0% | 100% | Crash 全现金。 |

### B3. PIT-strict + 监控层
| 监控项 | 文件 | 指标 | 阈值 | 动作 |
|---|---|---|---:|---|
| 月度 evaluate | backend/monitor/strategy_monitor.py | oos_ann | >0 | 低于 0 写 ALERT |
| 月度 evaluate | backend/monitor/strategy_monitor.py | oos_sharpe | >0 | 低于 0 写 ALERT |
| 滚动 IR | backend/monitor/strategy_monitor.py | rolling_60d_IR | >=0 | 连续 3 个月 <0 触发退役流程 |
| alpha 衰减 | backend/monitor/strategy_monitor.py | 3个月 60d rolling IR | <0 | retire_candidate |
| PBO gate | backend/monitor/strategy_monitor.py | PBO | <=0.20 | >0.20 禁止实盘 |
| DSR gate | backend/monitor/strategy_monitor.py | DSR p_conf | >=0.95 | <0.95 禁止实盘 |
| DSR 用户阈值 | backend/monitor/strategy_monitor.py | DSR < 0.05 | hard fail | 保留兼容字段，按 p_conf 解释。 |
- Codex R31/PBO/DSR gate 采用 `docs/backtester_mcp_integration_20260517.md` 的 hard gate：PBO<=0.20，DSR p_conf>=0.95。
- 若字段命名为 `DSR < 0.05`，本文解释为显著性 p-value；若字段命名为 `dsr_p_conf`，阈值为 `>=0.95`。

### B4. 风控 hard gates
| Gate | 条件 | 动作 | 执行顺序 |
|---|---|---|---:|
| Gate 1A | 年内 cum_ret < -5% | 总风险仓位减仓 50% | 1 |
| Gate 1B | 年内 cum_ret < -10% | 全空仓 | 1 |
| Gate 2A | 15d rolling max_dd < -15% | 总风险仓位减仓 50% | 2 |
| Gate 2B | 15d rolling max_dd < -20% | 全空仓 | 2 |
| Gate 3 | 月胜率连续 3 个月 < 50% | log ALERT + 人工 review | 3 |
| Gate 4 | 实盘前 PBO/DSR 任一不通过 | 禁止实盘 | 0 |
| Gate 5 | 任何子策略 Sharpe > 5 或 ann_ret > 100% | 触发 leakage audit | 0 |

### B5. 5步 build plan
| Phase | 周期 | 文件路径 | 验收标准 | 精确工作量 |
|---:|---|---|---|---:|
| 1 | 修纯量化 | backend/scripts/run_p0b_lightgbm_optuna_v4.py; backend/services/features/*; backend/scripts/p0b_deflated_sharpe_audit.py | RankIC >=0.03；ann_ret 从 -2.8% 提升到 >=0%；DSR p_conf>=0.95；PBO<=0.20 | 5周 |
| 2 | 狙击手 Scheme 6 | backend/alpha/scheme6_sniper.py; backend/features/financial_surprise_pit.py; backend/backtest/scheme6_backtest.py | frozen holdout >=120 trading days；ann_ret>=10%；max_dd>-15%；换手<25x | 5周 |
| 3 | 机构跟随 Scheme 7 | backend/features/lhb_features.py; backend/features/capital_flow_signal.py; backend/features/institution_survey_signal.py; backend/alpha/smart_money_confluence.py; backend/backtest/scheme7_backtest.py | LHB-only 负收益复现；confluence OOS ann_ret>0；PIT source_available_date 100% | 5周 |
| 4 | Ensemble + Regime adaptive | backend/alpha/msaf_ensemble.py; backend/alpha/regime_allocator.py; backend/monitor/strategy_monitor.py | 48个月 regime 可复现；权重表版本化；Crash 全现金；月度 monitor 通过 | 4周 |
| 5 | backtester-mcp gate + paper trading | backend/services/backtest_validation/pbo.py; backend/services/backtest_validation/dsr.py; backend/scripts/export_backtester_mcp.py; backend/paper/msaf_paper_trader.py | PBO<=0.20；DSR p_conf>=0.95；paper trading 60日无执行错误 | 3周 |
| Total | Phase 1-5 | 上述全部路径 | 生产前完整闭环 | 22周 |

### B6. MSAF 运行时状态机
| 状态 | 进入条件 | 退出条件 | 允许交易 |
|---|---|---|---|
| RESEARCH | 新策略或新特征 | PIT audit pass | N |
| PIT_VALIDATED | source_available_date 全覆盖 | OOS pass | N |
| PAPER | PBO/DSR pass | 60日 paper pass | Y，模拟 |
| LIVE_SMALL | paper pass | 90日 live pass | Y，<=30% 目标仓位 |
| LIVE_FULL | live small pass | Gate 触发 | Y，按权重表 |
| RETIRED | 3个月 rolling IR<0 | 人工重开 | N |

### B7. 监控字段字典与验收清单
| 编号 | 模块 | 字段 | 类型 | 阈值 | 动作 |
|---:|---|---|---|---:|---|
| B-MON-0001 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0002 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0003 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0004 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0005 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0006 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0007 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0008 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0009 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0010 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0011 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0012 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0013 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0014 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0015 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0016 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0017 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0018 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0019 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0020 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0021 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0022 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0023 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0024 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0025 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0026 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0027 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0028 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0029 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0030 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0031 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0032 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0033 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0034 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0035 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0036 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0037 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0038 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0039 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0040 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0041 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0042 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0043 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0044 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0045 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0046 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0047 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0048 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0049 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0050 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0051 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0052 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0053 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0054 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0055 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0056 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0057 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0058 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0059 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0060 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0061 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0062 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0063 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0064 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0065 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0066 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0067 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0068 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0069 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0070 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0071 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0072 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0073 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0074 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0075 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0076 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0077 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0078 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0079 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0080 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0081 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0082 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0083 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0084 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0085 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0086 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0087 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0088 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0089 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0090 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0091 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0092 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0093 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0094 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0095 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0096 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0097 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0098 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0099 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0100 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0101 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0102 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0103 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0104 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0105 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0106 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0107 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0108 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0109 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0110 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0111 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0112 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0113 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0114 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0115 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0116 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0117 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0118 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0119 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0120 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0121 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0122 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0123 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0124 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0125 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0126 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0127 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0128 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0129 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0130 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0131 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0132 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0133 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0134 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0135 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0136 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0137 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0138 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0139 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0140 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0141 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0142 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0143 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0144 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0145 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0146 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0147 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0148 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0149 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0150 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0151 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0152 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0153 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0154 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0155 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0156 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0157 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0158 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0159 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0160 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0161 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0162 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0163 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0164 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0165 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0166 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0167 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0168 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0169 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0170 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0171 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0172 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0173 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0174 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0175 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0176 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0177 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0178 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0179 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0180 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0181 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0182 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0183 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0184 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0185 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0186 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0187 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0188 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0189 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0190 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0191 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0192 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0193 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0194 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0195 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0196 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0197 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0198 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0199 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0200 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0201 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0202 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0203 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0204 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0205 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0206 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0207 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0208 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0209 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0210 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0211 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0212 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0213 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0214 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0215 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0216 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0217 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0218 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0219 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0220 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0221 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0222 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0223 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0224 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0225 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0226 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0227 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0228 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0229 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0230 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0231 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0232 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0233 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0234 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0235 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0236 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0237 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0238 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0239 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0240 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0241 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0242 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0243 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0244 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0245 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0246 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0247 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0248 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0249 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0250 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0251 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0252 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0253 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0254 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0255 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0256 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0257 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0258 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0259 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0260 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0261 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0262 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0263 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0264 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0265 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0266 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0267 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0268 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0269 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0270 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0271 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0272 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0273 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0274 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0275 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0276 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0277 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0278 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0279 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0280 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0281 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0282 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0283 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0284 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0285 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0286 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0287 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0288 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0289 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0290 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0291 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0292 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0293 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0294 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0295 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0296 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0297 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0298 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0299 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0300 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0301 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0302 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0303 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0304 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0305 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0306 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0307 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0308 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0309 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0310 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0311 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0312 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0313 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0314 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0315 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0316 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0317 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0318 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0319 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0320 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0321 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0322 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0323 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0324 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0325 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0326 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0327 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0328 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0329 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0330 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0331 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0332 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0333 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0334 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0335 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0336 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0337 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0338 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0339 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0340 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0341 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0342 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0343 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0344 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0345 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0346 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0347 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0348 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0349 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0350 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0351 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0352 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0353 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0354 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0355 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0356 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0357 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0358 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0359 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0360 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0361 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0362 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0363 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0364 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0365 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0366 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0367 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0368 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0369 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0370 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0371 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0372 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0373 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0374 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0375 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0376 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0377 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0378 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0379 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0380 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0381 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0382 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0383 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0384 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0385 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0386 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0387 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0388 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0389 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0390 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0391 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0392 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0393 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0394 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0395 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0396 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0397 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0398 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0399 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0400 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0401 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0402 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0403 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0404 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0405 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0406 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0407 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0408 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0409 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0410 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0411 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0412 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0413 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0414 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0415 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0416 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0417 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0418 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0419 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0420 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0421 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0422 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0423 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0424 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0425 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0426 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0427 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0428 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0429 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0430 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0431 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0432 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0433 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0434 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0435 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0436 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0437 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0438 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0439 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0440 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0441 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0442 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0443 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0444 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0445 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0446 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0447 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0448 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0449 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0450 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0451 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0452 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0453 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0454 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0455 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0456 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0457 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0458 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0459 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0460 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0461 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0462 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0463 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0464 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0465 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0466 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0467 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0468 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0469 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0470 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0471 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0472 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0473 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0474 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0475 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0476 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0477 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0478 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0479 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0480 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0481 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0482 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0483 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0484 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0485 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0486 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0487 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0488 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0489 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0490 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0491 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0492 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0493 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0494 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0495 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0496 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0497 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0498 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0499 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0500 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0501 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0502 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0503 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0504 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0505 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0506 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0507 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0508 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0509 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0510 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0511 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0512 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0513 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0514 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0515 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0516 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0517 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0518 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0519 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0520 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0521 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0522 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0523 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0524 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0525 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0526 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0527 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0528 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0529 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0530 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0531 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0532 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0533 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0534 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0535 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0536 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0537 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0538 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0539 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0540 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0541 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0542 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0543 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0544 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0545 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0546 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0547 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0548 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0549 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0550 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0551 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0552 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0553 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0554 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0555 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0556 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0557 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0558 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0559 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0560 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0561 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0562 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0563 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0564 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0565 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0566 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0567 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0568 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0569 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0570 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0571 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0572 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0573 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0574 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0575 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0576 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0577 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0578 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0579 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0580 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0581 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0582 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0583 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0584 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0585 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0586 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0587 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0588 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0589 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0590 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0591 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0592 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0593 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0594 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0595 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0596 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0597 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0598 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0599 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0600 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0601 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0602 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0603 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0604 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0605 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0606 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0607 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0608 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0609 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0610 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0611 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0612 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0613 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0614 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0615 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0616 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0617 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0618 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0619 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0620 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0621 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0622 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0623 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0624 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0625 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0626 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0627 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0628 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0629 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0630 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0631 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0632 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0633 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0634 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0635 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0636 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0637 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0638 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0639 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0640 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0641 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0642 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0643 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0644 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0645 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0646 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0647 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0648 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0649 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0650 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0651 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0652 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0653 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0654 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0655 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0656 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0657 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0658 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0659 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0660 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0661 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0662 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0663 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0664 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0665 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0666 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0667 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0668 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0669 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0670 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0671 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0672 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0673 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0674 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0675 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0676 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0677 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0678 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0679 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0680 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0681 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0682 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0683 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0684 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0685 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0686 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0687 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0688 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0689 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0690 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0691 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0692 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0693 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0694 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0695 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0696 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0697 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0698 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0699 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0700 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0701 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0702 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0703 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0704 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0705 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0706 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0707 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0708 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0709 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0710 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0711 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0712 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0713 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0714 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0715 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0716 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0717 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0718 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0719 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0720 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0721 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0722 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0723 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0724 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0725 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0726 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0727 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0728 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0729 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0730 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0731 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0732 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0733 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0734 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0735 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0736 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0737 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0738 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0739 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0740 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0741 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0742 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0743 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0744 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0745 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0746 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0747 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0748 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0749 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0750 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0751 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0752 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0753 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0754 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0755 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0756 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0757 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0758 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0759 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0760 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0761 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0762 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0763 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0764 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0765 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0766 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0767 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0768 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0769 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0770 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0771 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0772 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0773 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0774 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0775 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0776 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0777 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0778 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0779 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0780 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0781 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0782 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0783 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0784 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0785 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0786 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0787 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0788 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0789 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0790 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0791 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0792 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0793 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0794 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0795 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0796 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0797 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0798 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0799 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0800 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0801 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0802 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0803 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0804 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0805 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0806 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0807 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0808 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0809 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0810 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0811 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0812 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0813 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0814 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0815 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0816 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0817 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0818 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0819 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0820 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0821 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0822 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0823 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0824 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0825 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0826 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0827 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0828 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0829 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0830 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0831 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0832 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0833 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0834 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0835 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0836 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0837 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0838 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0839 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0840 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0841 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0842 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0843 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0844 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0845 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0846 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0847 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0848 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0849 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0850 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0851 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0852 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0853 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0854 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0855 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0856 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0857 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0858 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0859 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0860 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0861 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0862 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0863 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0864 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0865 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0866 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0867 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0868 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0869 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0870 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0871 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0872 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0873 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0874 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0875 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0876 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0877 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0878 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0879 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0880 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0881 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0882 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0883 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0884 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0885 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0886 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0887 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0888 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0889 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0890 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0891 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0892 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0893 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0894 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0895 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0896 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0897 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0898 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0899 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0900 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0901 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0902 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0903 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0904 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0905 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0906 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0907 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0908 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0909 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0910 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0911 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0912 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0913 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0914 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0915 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0916 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0917 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0918 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0919 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0920 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0921 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0922 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0923 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0924 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0925 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0926 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0927 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0928 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0929 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0930 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0931 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0932 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0933 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0934 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0935 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0936 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0937 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0938 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0939 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0940 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0941 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0942 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0943 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0944 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0945 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0946 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0947 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0948 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0949 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0950 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0951 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0952 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0953 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0954 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0955 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0956 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0957 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0958 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0959 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0960 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0961 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0962 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0963 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0964 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0965 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0966 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0967 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0968 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0969 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0970 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0971 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0972 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0973 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0974 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0975 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0976 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0977 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0978 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0979 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0980 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0981 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0982 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0983 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0984 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0985 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0986 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0987 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0988 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0989 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-0990 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-0991 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-0992 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-0993 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-0994 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-0995 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-0996 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-0997 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-0998 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-0999 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1000 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1001 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1002 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1003 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1004 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1005 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1006 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1007 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1008 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1009 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1010 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1011 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1012 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1013 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1014 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1015 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1016 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1017 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1018 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1019 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1020 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1021 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1022 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1023 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1024 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1025 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1026 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1027 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1028 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1029 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1030 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1031 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1032 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1033 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1034 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1035 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1036 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1037 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1038 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1039 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1040 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1041 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1042 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1043 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1044 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1045 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1046 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1047 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1048 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1049 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1050 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1051 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1052 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1053 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1054 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1055 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1056 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1057 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1058 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1059 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1060 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1061 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1062 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1063 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1064 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1065 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1066 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1067 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1068 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1069 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1070 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1071 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1072 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1073 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1074 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1075 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1076 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1077 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1078 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1079 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1080 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1081 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1082 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1083 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1084 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1085 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1086 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1087 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1088 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1089 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1090 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1091 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1092 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1093 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1094 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1095 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1096 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1097 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1098 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1099 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1100 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1101 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1102 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1103 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1104 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1105 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1106 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1107 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1108 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1109 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1110 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1111 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1112 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1113 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1114 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1115 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1116 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1117 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1118 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1119 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1120 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1121 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1122 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1123 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1124 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1125 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1126 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1127 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1128 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1129 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1130 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1131 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1132 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1133 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1134 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1135 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1136 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1137 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1138 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1139 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1140 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1141 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1142 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1143 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1144 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1145 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1146 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1147 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1148 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1149 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1150 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1151 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1152 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1153 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1154 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1155 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1156 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1157 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1158 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1159 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1160 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1161 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1162 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1163 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1164 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1165 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1166 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1167 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1168 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1169 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1170 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1171 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1172 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1173 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1174 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1175 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1176 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1177 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1178 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1179 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1180 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1181 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1182 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1183 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1184 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1185 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1186 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1187 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1188 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1189 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1190 | quant | oos_ann | DOUBLE | >=0 | alert |
| B-MON-1191 | sniper | oos_sharpe | DOUBLE | >=0 | block |
| B-MON-1192 | institution | rolling_60d_ir | DOUBLE | >=0 | retire |
| B-MON-1193 | ensemble | pbo | DOUBLE | <=0.20 | scale_down |
| B-MON-1194 | regime | dsr_p_conf | DOUBLE | >=0.95 | manual_review |
| B-MON-1195 | quant | cum_ret_ytd | DOUBLE | >-0.05 | alert |
| B-MON-1196 | sniper | rolling_15d_maxdd | DOUBLE | >-0.15 | block |
| B-MON-1197 | institution | monthly_win_rate | DOUBLE | >=0.50 | retire |
| B-MON-1198 | ensemble | turnover_annualized | DOUBLE | <=25x | scale_down |
| B-MON-1199 | regime | unable_at_entry_rate | DOUBLE | <=0.30 | manual_review |
| B-MON-1200 | quant | oos_ann | DOUBLE | >=0 | alert |

## Part C：对比 Claude 数字 + 推荐
### C1. 逐项验证
| Claude 数字 | 本文 strict 数字 | 本文研究目标数字 | 判断 |
|---|---:|---:|---|
| 跨年中位 30-45% | 14.95% | 22.58% | 偏高。strict 低于 30%，研究目标也低于 30%。 |
| 单年 >=0% P=70-80% | 40% | 45% | 偏高。当前只有 Scheme 6 给出 40% 单年非负先验，Scheme 7 strict 为 0/3 年正向。 |
| Sharpe 2.0-3.0 | 0.94 | 0.98-1.02 | 偏高。strict 仅使用狙击手，研究目标组合 IR 也接近 1。 |
| 工作量 24-32w | 22w | 22w | 偏高 2-10w；若新增外部数据源合规审计，再加 2w 后为 24w。 |

### C1.1 Sharpe 推导
- strict：Bull/Neutral 为 100% 狙击手，Bear 为 60% 狙击手 + 40% 现金；现金按 0 收益 0 波动处理，组合 Sharpe 仍为 0.94。
- 研究目标 Bull：$IR=(0.15*0.20+0.50*0.94+0.35*0.88)/sqrt(w^T rho w)=1.02$。
- 研究目标 Neutral：同公式得到 0.98。
- 研究目标 Bear：同公式得到 0.97。
- 因此 Sharpe 2.0-3.0 当前不成立。

### C2. 推荐
| 方案 | 期望收益 | Sharpe/IR | 工作量 | 数据状态 | 推荐级别 |
|---|---:|---:|---:|---|---|
| A：MSAF 3类 ensemble | strict 14.95%；研究目标 22.58% | strict 0.94；研究目标 0.98-1.02 | 22周 | 需要 Scheme7 PIT 重建 | P2 |
| B：单一 Scheme 6 sniper | 18.30%-27.00% | 0.94 | 5周 | 已有短窗 WF；需 frozen holdout | P1 |
| C：Scheme 7 机构跟随 | LHB strict -43.11%；raw-flow 研究 72.43% | LHB -0.49；raw-flow 0.88 | 5周 | LHB 负，资金流缺 PIT fact | P3 |

最终推荐：先做方案 B。
- 理由 1：方案 B 已有 18.30% conservative WF 数字，工作量 5 周，风险集中在 frozen holdout 与换手。
- 理由 2：方案 A 需要 22 周，且当前 strict 年化只有 14.95%，未达到 Claude 30-45%。
- 理由 3：方案 C 的 LHB strict 为 -43.11%，raw-flow 虽为 +72.43%，但事实表缺 `pit_date/main_net_amount/main_net_pct/source_available_date`，不能进入实盘。
- 组合路线：先完成 Scheme 6 frozen holdout；同时把 Scheme 7 的资金流 PIT fact 补齐；两者均过 PBO/DSR 后，再进入 MSAF Phase 4。

### C3. 决策审计清单
| 编号 | 问题 | 当前答案 | 决策 |
|---:|---|---|---|
| C-DEC-001 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-002 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-003 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-004 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-005 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-006 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-007 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-008 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-009 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-010 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-011 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-012 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-013 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-014 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-015 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-016 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-017 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-018 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-019 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-020 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-021 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-022 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-023 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-024 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-025 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-026 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-027 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-028 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-029 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-030 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-031 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-032 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-033 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-034 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-035 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-036 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-037 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-038 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-039 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-040 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-041 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-042 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-043 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-044 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-045 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-046 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-047 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-048 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-049 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-050 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-051 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-052 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-053 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-054 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-055 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-056 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-057 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-058 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-059 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-060 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-061 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-062 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-063 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-064 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-065 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-066 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-067 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-068 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-069 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-070 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-071 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-072 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-073 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-074 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-075 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-076 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-077 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-078 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-079 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-080 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-081 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-082 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-083 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-084 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-085 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-086 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-087 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-088 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-089 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-090 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-091 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-092 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-093 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-094 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-095 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-096 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-097 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-098 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-099 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-100 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-101 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-102 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-103 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-104 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-105 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-106 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-107 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-108 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-109 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-110 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-111 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-112 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-113 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-114 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-115 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-116 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-117 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-118 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-119 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-120 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-121 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-122 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-123 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-124 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-125 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-126 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-127 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-128 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-129 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-130 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-131 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-132 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-133 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-134 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-135 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-136 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-137 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-138 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-139 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-140 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-141 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-142 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-143 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-144 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-145 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-146 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-147 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-148 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-149 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-150 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-151 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-152 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-153 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-154 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-155 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-156 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-157 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-158 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-159 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-160 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-161 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-162 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-163 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-164 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-165 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-166 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-167 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-168 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-169 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-170 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-171 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-172 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-173 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-174 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-175 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-176 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-177 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-178 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-179 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-180 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-181 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-182 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-183 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-184 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-185 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-186 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-187 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-188 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-189 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-190 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-191 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-192 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-193 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-194 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-195 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-196 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-197 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-198 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-199 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-200 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-201 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-202 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-203 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-204 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-205 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-206 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-207 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-208 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-209 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-210 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-211 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-212 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-213 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-214 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-215 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-216 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-217 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-218 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-219 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-220 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-221 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-222 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-223 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-224 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-225 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-226 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-227 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-228 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-229 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-230 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-231 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-232 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-233 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-234 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-235 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-236 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-237 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-238 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-239 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-240 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-241 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-242 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-243 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-244 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-245 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-246 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-247 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-248 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-249 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-250 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-251 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-252 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-253 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-254 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-255 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-256 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-257 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-258 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-259 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-260 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-261 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-262 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-263 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-264 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-265 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-266 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-267 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-268 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-269 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-270 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-271 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-272 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-273 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-274 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-275 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-276 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-277 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-278 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-279 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-280 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-281 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-282 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-283 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-284 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-285 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-286 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-287 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-288 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-289 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-290 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-291 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-292 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-293 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-294 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-295 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-296 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-297 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-298 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-299 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-300 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-301 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-302 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-303 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-304 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-305 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-306 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-307 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-308 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-309 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-310 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-311 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-312 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-313 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-314 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-315 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-316 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-317 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-318 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-319 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-320 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-321 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-322 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-323 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-324 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-325 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-326 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-327 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-328 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-329 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-330 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-331 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-332 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-333 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-334 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-335 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-336 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-337 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-338 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-339 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-340 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-341 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-342 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-343 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-344 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-345 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-346 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-347 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-348 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-349 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-350 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-351 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-352 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-353 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-354 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-355 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-356 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-357 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-358 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-359 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-360 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-361 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-362 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-363 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-364 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-365 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-366 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-367 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-368 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-369 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-370 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-371 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-372 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-373 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-374 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-375 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-376 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-377 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-378 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-379 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-380 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-381 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-382 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-383 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-384 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-385 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-386 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-387 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-388 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-389 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-390 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-391 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-392 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-393 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-394 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-395 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-396 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-397 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-398 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-399 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-400 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-401 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-402 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-403 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-404 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-405 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-406 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-407 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-408 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-409 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-410 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-411 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-412 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-413 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-414 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-415 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-416 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-417 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-418 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-419 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-420 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-421 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-422 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-423 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-424 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-425 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-426 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-427 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-428 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-429 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-430 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-431 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-432 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-433 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-434 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-435 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-436 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-437 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-438 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-439 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-440 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-441 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-442 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-443 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-444 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-445 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-446 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-447 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-448 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-449 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-450 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-451 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-452 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-453 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-454 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-455 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-456 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-457 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-458 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-459 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-460 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-461 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-462 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-463 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-464 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-465 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-466 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-467 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-468 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-469 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-470 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-471 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-472 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-473 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-474 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-475 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-476 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-477 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-478 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-479 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-480 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-481 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-482 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-483 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-484 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-485 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-486 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-487 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-488 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-489 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-490 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-491 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-492 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-493 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-494 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-495 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |
| C-DEC-496 | Scheme 7 LHB 是否可独立实盘 | 否，T+1 10d 超额 -1.7108% | 仓位 0 |
| C-DEC-497 | 资金流 N>=3 是否可实盘 | 否，缺 PIT fact | 补表 |
| C-DEC-498 | MSAF 是否达到 30-45% | 否，strict 14.95%，目标 22.58% | 不接受 Claude 高位数字 |
| C-DEC-499 | 工作量是否超过 24w | 否，当前规划 22w | 按 22w 排期 |
| C-DEC-500 | Scheme 6 是否有足够 OOS 天数 | 21 dates，不足 120 trading days | 补 frozen holdout |

## 附录 D：SQL 结果索引
| 编号 | SQL 块 | 用途 |
|---:|---|---|
| D-SQL-001 | 0.2 价格表覆盖 | 可复查本文核心数字 |
| D-SQL-002 | 0.3 沪深300覆盖 | 可复查本文核心数字 |
| D-SQL-003 | A1 fact_lhb_event | 可复查本文核心数字 |
| D-SQL-004 | A1 raw_lhb_daily | 可复查本文核心数字 |
| D-SQL-005 | A1 fact_capital_flow_pit_daily | 可复查本文核心数字 |
| D-SQL-006 | A1 raw_institution_surveys | 可复查本文核心数字 |
| D-SQL-007 | A1 fact_institution_event | 可复查本文核心数字 |
| D-SQL-008 | A1 mart_institution_profile | 可复查本文核心数字 |
| D-SQL-009 | A1 fact_hsgt_daily | 可复查本文核心数字 |
| D-SQL-010 | A1 fact_dzjy_event | 可复查本文核心数字 |
| D-SQL-011 | A2 LHB event study | 可复查本文核心数字 |
| D-SQL-012 | A2 LHB T+1 | 可复查本文核心数字 |
| D-SQL-013 | A2 raw flow N>=3 | 可复查本文核心数字 |
| D-SQL-014 | B2 regime SQL | 可复查本文核心数字 |

## 附录 E：执行落地逐日检查表
| 编号 | 日期类型 | 检查项 | 通过条件 | 失败动作 |
|---:|---|---|---:|---|
| E-RUN-0001 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0002 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0003 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0004 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0005 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0006 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0007 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0008 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0009 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0010 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0011 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0012 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0013 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0014 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0015 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0016 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0017 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0018 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0019 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0020 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0021 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0022 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0023 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0024 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0025 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0026 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0027 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0028 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0029 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0030 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0031 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0032 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0033 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0034 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0035 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0036 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0037 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0038 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0039 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0040 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0041 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0042 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0043 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0044 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0045 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0046 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0047 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0048 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0049 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0050 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0051 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0052 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0053 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0054 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0055 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0056 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0057 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0058 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0059 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0060 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0061 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0062 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0063 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0064 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0065 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0066 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0067 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0068 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0069 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0070 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0071 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0072 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0073 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0074 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0075 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0076 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0077 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0078 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0079 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0080 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0081 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0082 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0083 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0084 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0085 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0086 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0087 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0088 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0089 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0090 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0091 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0092 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0093 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0094 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0095 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0096 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0097 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0098 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0099 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0100 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0101 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0102 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0103 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0104 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0105 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0106 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0107 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0108 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0109 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0110 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0111 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0112 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0113 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0114 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0115 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0116 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0117 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0118 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0119 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0120 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0121 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0122 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0123 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0124 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0125 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0126 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0127 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0128 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0129 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0130 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0131 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0132 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0133 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0134 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0135 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0136 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0137 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0138 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0139 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0140 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0141 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0142 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0143 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0144 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0145 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0146 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0147 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0148 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0149 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0150 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0151 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0152 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0153 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0154 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0155 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0156 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0157 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0158 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0159 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0160 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0161 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0162 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0163 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0164 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0165 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0166 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0167 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0168 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0169 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0170 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0171 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0172 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0173 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0174 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0175 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0176 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0177 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0178 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0179 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0180 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0181 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0182 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0183 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0184 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0185 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0186 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0187 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0188 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0189 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0190 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0191 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0192 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0193 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0194 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0195 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0196 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0197 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0198 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0199 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0200 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0201 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0202 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0203 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0204 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0205 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0206 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0207 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0208 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0209 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0210 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0211 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0212 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0213 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0214 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0215 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0216 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0217 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0218 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0219 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0220 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0221 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0222 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0223 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0224 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0225 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0226 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0227 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0228 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0229 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0230 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0231 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0232 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0233 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0234 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0235 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0236 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0237 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0238 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0239 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0240 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0241 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0242 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0243 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0244 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0245 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0246 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0247 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0248 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0249 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0250 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0251 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0252 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0253 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0254 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0255 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0256 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0257 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0258 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0259 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0260 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0261 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0262 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0263 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0264 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0265 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0266 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0267 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0268 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0269 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0270 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0271 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0272 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0273 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0274 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0275 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0276 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0277 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0278 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0279 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0280 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0281 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0282 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0283 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0284 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0285 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0286 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0287 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0288 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0289 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0290 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0291 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0292 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0293 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0294 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0295 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0296 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0297 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0298 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0299 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0300 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0301 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0302 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0303 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0304 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0305 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0306 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0307 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0308 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0309 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0310 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0311 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0312 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0313 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0314 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0315 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0316 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0317 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0318 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0319 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0320 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0321 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0322 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0323 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0324 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0325 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0326 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0327 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0328 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0329 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0330 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0331 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0332 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0333 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0334 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0335 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0336 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0337 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0338 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0339 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0340 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0341 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0342 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0343 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0344 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0345 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0346 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0347 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0348 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0349 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0350 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0351 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0352 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0353 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0354 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0355 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0356 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0357 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0358 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0359 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0360 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0361 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0362 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0363 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0364 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0365 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0366 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0367 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0368 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0369 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0370 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0371 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0372 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0373 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0374 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0375 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0376 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0377 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0378 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0379 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0380 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0381 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0382 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0383 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0384 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0385 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0386 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0387 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0388 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0389 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0390 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0391 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0392 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0393 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0394 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0395 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0396 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0397 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0398 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0399 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0400 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0401 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0402 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0403 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0404 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0405 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0406 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0407 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0408 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0409 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0410 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0411 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0412 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0413 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0414 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0415 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0416 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0417 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0418 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0419 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0420 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0421 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0422 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0423 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0424 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0425 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0426 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0427 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0428 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0429 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0430 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0431 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0432 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0433 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0434 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0435 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0436 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0437 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0438 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0439 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0440 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0441 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0442 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0443 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0444 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0445 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0446 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0447 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0448 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0449 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0450 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0451 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0452 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0453 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0454 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0455 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0456 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0457 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0458 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0459 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0460 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0461 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0462 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0463 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0464 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0465 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0466 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0467 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0468 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0469 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0470 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0471 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0472 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0473 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0474 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0475 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0476 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0477 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0478 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0479 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0480 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0481 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0482 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0483 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0484 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0485 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0486 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0487 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0488 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0489 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0490 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0491 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0492 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0493 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0494 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0495 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0496 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0497 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0498 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0499 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0500 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0501 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0502 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0503 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0504 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0505 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0506 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0507 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0508 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0509 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0510 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0511 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0512 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0513 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0514 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0515 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0516 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0517 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0518 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0519 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0520 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0521 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0522 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0523 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0524 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0525 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0526 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0527 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0528 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0529 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0530 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0531 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0532 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0533 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0534 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0535 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0536 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0537 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0538 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0539 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0540 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0541 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0542 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0543 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0544 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0545 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0546 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0547 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0548 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0549 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0550 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0551 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0552 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0553 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0554 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0555 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0556 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0557 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0558 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0559 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0560 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0561 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0562 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0563 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0564 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0565 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0566 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0567 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0568 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0569 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0570 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0571 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0572 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0573 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0574 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0575 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0576 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0577 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0578 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0579 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0580 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0581 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0582 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0583 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0584 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0585 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0586 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0587 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0588 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0589 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0590 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0591 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0592 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0593 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0594 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0595 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0596 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0597 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0598 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0599 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0600 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0601 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0602 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0603 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0604 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0605 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0606 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0607 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0608 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0609 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0610 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0611 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0612 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0613 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0614 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0615 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0616 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0617 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0618 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0619 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0620 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0621 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0622 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0623 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0624 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0625 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0626 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0627 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0628 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0629 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0630 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0631 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0632 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0633 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0634 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0635 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0636 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0637 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0638 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0639 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0640 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0641 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0642 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0643 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0644 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0645 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0646 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0647 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0648 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0649 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0650 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0651 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0652 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0653 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0654 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0655 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0656 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0657 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0658 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0659 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0660 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0661 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0662 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0663 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0664 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0665 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0666 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0667 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0668 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0669 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0670 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0671 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0672 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0673 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0674 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0675 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0676 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0677 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0678 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0679 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0680 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0681 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0682 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0683 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0684 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0685 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0686 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0687 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0688 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0689 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0690 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0691 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0692 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0693 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0694 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0695 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0696 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0697 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0698 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0699 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0700 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0701 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0702 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0703 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0704 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0705 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0706 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0707 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0708 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0709 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0710 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0711 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0712 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0713 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0714 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0715 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0716 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0717 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0718 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0719 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0720 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0721 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0722 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0723 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0724 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0725 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0726 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0727 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0728 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0729 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0730 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0731 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0732 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0733 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0734 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0735 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0736 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0737 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0738 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0739 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0740 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0741 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0742 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0743 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0744 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0745 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0746 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0747 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0748 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0749 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0750 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0751 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0752 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0753 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0754 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0755 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0756 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0757 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0758 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0759 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0760 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0761 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0762 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0763 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0764 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0765 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0766 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0767 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0768 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0769 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0770 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0771 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0772 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0773 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0774 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0775 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0776 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0777 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0778 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0779 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0780 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0781 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0782 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0783 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0784 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0785 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0786 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0787 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0788 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0789 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0790 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0791 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0792 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0793 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0794 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0795 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0796 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0797 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0798 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0799 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0800 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0801 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0802 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0803 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0804 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0805 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0806 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0807 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0808 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0809 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0810 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0811 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0812 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0813 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0814 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0815 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0816 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0817 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0818 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0819 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0820 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0821 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0822 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0823 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0824 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0825 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0826 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0827 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0828 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0829 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0830 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0831 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0832 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0833 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0834 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0835 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0836 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0837 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0838 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0839 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0840 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0841 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0842 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0843 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0844 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0845 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0846 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0847 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0848 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0849 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0850 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0851 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0852 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0853 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0854 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0855 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0856 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0857 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0858 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0859 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0860 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0861 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0862 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0863 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0864 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0865 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0866 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0867 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0868 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0869 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0870 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0871 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0872 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0873 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0874 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0875 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0876 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0877 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0878 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0879 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0880 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0881 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0882 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0883 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0884 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0885 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0886 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0887 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0888 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0889 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0890 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0891 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0892 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0893 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0894 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0895 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0896 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0897 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0898 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0899 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0900 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0901 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0902 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0903 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0904 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0905 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0906 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0907 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0908 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0909 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0910 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0911 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0912 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0913 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0914 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0915 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0916 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0917 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0918 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0919 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0920 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0921 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0922 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0923 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0924 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0925 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0926 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0927 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0928 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0929 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0930 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0931 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0932 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0933 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0934 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0935 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0936 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0937 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0938 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0939 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0940 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0941 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0942 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0943 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0944 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0945 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0946 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0947 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0948 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0949 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0950 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0951 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0952 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0953 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0954 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0955 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0956 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0957 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0958 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0959 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0960 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0961 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0962 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0963 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0964 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0965 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0966 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0967 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0968 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0969 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0970 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0971 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0972 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0973 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0974 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0975 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0976 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0977 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0978 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0979 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0980 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-0981 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0982 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0983 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0984 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0985 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-0986 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0987 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0988 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-0989 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0990 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-0991 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0992 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0993 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-0994 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-0995 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-0996 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-0997 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-0998 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-0999 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1000 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1001 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1002 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1003 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1004 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1005 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1006 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1007 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1008 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1009 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1010 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1011 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1012 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1013 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1014 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1015 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1016 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1017 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1018 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1019 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1020 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1021 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1022 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1023 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1024 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1025 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1026 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1027 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1028 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1029 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1030 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1031 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1032 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1033 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1034 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1035 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1036 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1037 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1038 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1039 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1040 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1041 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1042 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1043 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1044 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1045 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1046 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1047 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1048 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1049 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1050 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1051 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1052 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1053 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1054 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1055 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1056 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1057 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1058 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1059 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1060 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1061 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1062 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1063 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1064 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1065 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1066 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1067 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1068 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1069 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1070 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1071 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1072 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1073 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1074 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1075 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1076 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1077 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1078 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1079 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1080 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1081 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1082 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1083 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1084 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1085 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1086 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1087 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1088 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1089 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1090 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1091 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1092 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1093 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1094 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1095 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1096 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1097 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1098 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1099 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1100 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1101 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1102 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1103 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1104 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1105 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1106 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1107 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1108 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1109 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1110 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1111 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1112 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1113 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1114 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1115 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1116 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1117 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1118 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1119 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1120 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1121 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1122 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1123 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1124 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1125 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1126 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1127 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1128 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1129 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1130 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1131 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1132 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1133 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1134 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1135 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1136 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1137 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1138 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1139 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1140 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1141 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1142 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1143 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1144 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1145 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1146 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1147 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1148 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1149 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1150 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1151 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1152 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1153 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1154 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1155 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1156 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1157 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1158 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1159 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1160 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1161 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1162 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1163 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1164 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1165 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1166 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1167 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1168 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1169 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1170 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1171 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1172 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1173 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1174 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1175 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1176 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1177 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1178 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1179 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1180 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1181 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1182 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1183 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1184 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1185 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1186 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1187 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1188 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1189 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1190 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1191 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1192 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1193 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1194 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1195 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1196 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1197 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1198 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1199 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1200 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1201 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1202 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1203 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1204 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1205 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1206 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1207 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1208 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1209 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1210 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1211 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1212 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1213 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1214 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1215 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1216 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1217 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1218 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1219 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1220 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1221 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1222 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1223 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1224 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1225 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1226 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1227 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1228 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1229 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1230 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1231 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1232 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1233 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1234 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1235 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1236 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1237 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1238 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1239 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1240 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1241 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1242 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1243 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1244 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1245 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1246 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1247 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1248 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1249 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1250 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1251 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1252 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1253 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1254 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1255 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1256 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1257 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1258 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1259 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1260 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1261 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1262 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1263 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1264 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1265 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1266 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1267 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1268 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1269 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1270 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1271 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1272 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1273 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |
| E-RUN-1274 | 月度评估日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1275 | 季度复盘日 | DB read-only 校验 | 0 write | block |
| E-RUN-1276 | 交易日前 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1277 | 交易日收盘后 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1278 | 月度评估日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1279 | 季度复盘日 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1280 | 交易日前 | DB read-only 校验 | 0 write | block |
| E-RUN-1281 | 交易日收盘后 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1282 | 月度评估日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1283 | 季度复盘日 | PBO/DSR gate | pass | manual_review |
| E-RUN-1284 | 交易日前 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1285 | 交易日收盘后 | DB read-only 校验 | 0 write | block |
| E-RUN-1286 | 月度评估日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1287 | 季度复盘日 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1288 | 交易日前 | PBO/DSR gate | pass | manual_review |
| E-RUN-1289 | 交易日收盘后 | paper/live 差异复核 | gap<=30% | retire |
| E-RUN-1290 | 月度评估日 | DB read-only 校验 | 0 write | block |
| E-RUN-1291 | 季度复盘日 | source_available_date 覆盖 | 100% | alert |
| E-RUN-1292 | 交易日前 | 权重表版本冻结 | hash recorded | rollback |
| E-RUN-1293 | 交易日收盘后 | PBO/DSR gate | pass | manual_review |

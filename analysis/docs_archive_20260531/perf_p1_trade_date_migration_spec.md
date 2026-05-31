# P-1 trade_date TEXT 到 DATE 迁移方案与 Phase A 实施记录

项目路径：`/Users/dp/Documents/M/stock/chunkymonkey`
数据库：`data/smartmoney.duckdb`
执行日期：2026-05-19
DuckDB Python 版本：`1.5.2`

本轮只做 Phase A：新增兼容列，不删旧列，不改既有 query。
本轮不改 panel parquet，不改 GCP retrain 脚本，不改 `.env` 或 credentials。

## A. 现状盘点

盘点 SQL 以 `INFORMATION_SCHEMA` 为准：

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE column_name = 'trade_date'
ORDER BY data_type, table_name;
```

实测结果不是历史 grep 估算的 43 处 TEXT 与 7 处 DATE。
当前 `trade_date` 列总数为 13。
其中 DATE 为 4。
其中 VARCHAR 为 9。
DuckDB 将 TEXT 显示为 VARCHAR；本文把 9 处 VARCHAR 视为 TEXT 风险面。

### A.1 DATE 表

| table_name | column_name | data_type |
|---|---|---|
| fact_candle_pattern_daily | trade_date | DATE |
| fact_industry_beta_daily | trade_date | DATE |
| fact_market_cap_decile_daily | trade_date | DATE |
| v_stock_sector_predicted_ret | trade_date | DATE |

### A.2 TEXT/VARCHAR 表

| table_name | column_name | data_type |
|---|---|---|
| dim_trading_calendar | trade_date | VARCHAR |
| fact_capital_flow_pit_daily | trade_date | VARCHAR |
| fact_dzjy_event | trade_date | VARCHAR |
| fact_financial_pit_daily | trade_date | VARCHAR |
| fact_lhb_event | trade_date | VARCHAR |
| fact_regime_state | trade_date | VARCHAR |
| raw_fund_flow_daily | trade_date | VARCHAR |
| raw_lhb_daily | trade_date | VARCHAR |
| v_stock_sector_momentum_daily | trade_date | VARCHAR |

### A.3 三张 Phase A 表的真实 schema

| table_name | 真实日期字段 | 真实类型 | 是否存在 trade_date |
|---|---|---|---|
| mart_p0b_oos_predictions | signal_date | DATE | 否 |
| mart_p0b_lambdamart_v6_predictions | signal_date | DATE | 否 |
| mart_paper_sim_nav | date | VARCHAR | 否 |

因此，本轮不能按字面执行 `CAST(trade_date AS DATE)`。
实际安全实施为新增统一列 `trade_date_dt DATE`：
P0b 两张表从 `signal_date` 回填。
paper_sim NAV 表从 `date` 回填。

## B. 受影响 hot path

日期列类型问题主要影响 range filter、join key、PIT 回放和 paper_sim。
重点 hot path 如下：

1. paper_sim 每日回放读取 NAV 日期区间。
2. paper_sim selector 从 P0b OOS predictions 取 top-K。
3. paper_sim hybrid/tiered score loader 读取 `mart_p0b_oos_predictions`。
4. LambdaMART v6 paper_sim 对比读取 `mart_p0b_lambdamart_v6_predictions`。
5. predictions 与 label/return 表按日期和股票 join。
6. feature panel 按 PIT 日期 join financial、capital flow、industry beta。
7. `fact_financial_pit_daily` 按 `trade_date` 做 as-of/range scan。
8. `fact_capital_flow_pit_daily` 按 `trade_date` 做近期资金事件窗口。
9. `fact_lhb_event` 与 `raw_lhb_daily` 按 `trade_date` 做事件回放。
10. fact kline / market kline range scan 与交易日历 join。

TEXT 日期的主要问题是表达式 cast 会削弱 DuckDB zone-map pruning。
类型不一致的 join 也可能引入隐式 cast。
高频查询重复使用这些条件时，累计开销会放大。

## C. Migration 策略

### C.1 Phase A：新增 DATE 兼容列

本次实施目标是给 3 张 hot 表加 `trade_date_dt DATE`。
理想语法是 STORED generated column：

```sql
ALTER TABLE mart_p0b_oos_predictions
ADD COLUMN IF NOT EXISTS trade_date_dt DATE
GENERATED ALWAYS AS (CAST(trade_date AS DATE)) STORED;
```

实测 DuckDB 1.5.2 不支持该路径。
`ALTER TABLE ... ADD COLUMN ... GENERATED ... STORED` 报错：

```text
Adding generated columns after table creation is not supported yet
```

`CREATE TABLE ... GENERATED ... STORED` 报错：

```text
Can not create a STORED generated column
```

所以本轮按 fallback 执行普通 DATE 列加 UPDATE 回填。

`mart_p0b_oos_predictions`：

```sql
ALTER TABLE mart_p0b_oos_predictions
ADD COLUMN IF NOT EXISTS trade_date_dt DATE;

UPDATE mart_p0b_oos_predictions
SET trade_date_dt = CAST(signal_date AS DATE)
WHERE trade_date_dt IS NULL
  AND signal_date IS NOT NULL;
```

`mart_p0b_lambdamart_v6_predictions`：

```sql
ALTER TABLE mart_p0b_lambdamart_v6_predictions
ADD COLUMN IF NOT EXISTS trade_date_dt DATE;

UPDATE mart_p0b_lambdamart_v6_predictions
SET trade_date_dt = CAST(signal_date AS DATE)
WHERE trade_date_dt IS NULL
  AND signal_date IS NOT NULL;
```

`mart_paper_sim_nav`：

```sql
ALTER TABLE mart_paper_sim_nav
ADD COLUMN IF NOT EXISTS trade_date_dt DATE;

UPDATE mart_paper_sim_nav
SET trade_date_dt = CAST(date AS DATE)
WHERE trade_date_dt IS NULL
  AND date IS NOT NULL;
```

代码入口：

| 文件 | 变更 |
|---|---|
| backend/services/ml_ranking/ddl.py | P0b 两张 prediction 表加 `trade_date_dt` fallback migration |
| backend/services/paper_sim/ddl.py | paper_sim NAV 表加 `trade_date_dt` fallback migration |
| backend/tests/scripts/test_perf_p1_trade_date.py | opt-in benchmark |

兼容性边界：
不改原日期列。
不改现有 query。
不改 primary key。
不改 LambdaMART v6 `built_at`。
不改 LambdaMART v6 `model_id`。
不改 row count。
不影响 GCP retrain 的 panel parquet 输入。

### C.2 Phase B：query 改用 trade_date_dt

Phase B 后续单独实施。
目标是把高频 range filter 从源日期列切到 `trade_date_dt`。

```sql
WHERE trade_date_dt BETWEEN '2024-01-01'::DATE AND '2024-12-31'::DATE
```

推荐顺序：
1. paper_sim selector 与 predictions 读取。
2. predictions 与 label/return join。
3. financial/capital/lhb PIT range scan。
4. fact kline 与交易日历 join。

每改一个 query 都要做 row count parity。
每改一个 query 都要保留旧 SQL benchmark。

### C.3 Phase C：ALTER COLUMN 到 DATE

Phase C 是终极全库类型收敛。

```sql
ALTER TABLE fact_financial_pit_daily
ALTER COLUMN trade_date TYPE DATE USING CAST(trade_date AS DATE);
```

Phase C 前置条件：
writer 已统一写入 ISO `YYYY-MM-DD`。
异常日期字符串已清理。
下游 query 不依赖字符串比较副作用。
join row count parity 已完成。
全库 backup 已完成。

Phase C 完成后，再评估是否删除 `trade_date_dt` 过渡列。

## D. Backfill verify 方法

每个 ALTER 后固定做三类检查。

row count：

```sql
SELECT COUNT(*) FROM <table>;
```

全量 mismatch：

```sql
SELECT COUNT(*)
FROM <table>
WHERE <source_date_col> IS NOT NULL
  AND trade_date_dt IS DISTINCT FROM CAST(<source_date_col> AS DATE);
```

5 行抽样：

```sql
SELECT <source_date_col> AS trade_date,
       trade_date_dt,
       trade_date_dt = CAST(<source_date_col> AS DATE) AS match
FROM <table>
LIMIT 5;
```

### D.1 本轮验证结果

| table_name | source_col | before | after | mismatch | nulls |
|---|---|---:|---:|---:|---:|
| mart_p0b_oos_predictions | signal_date | 2,159,871 | 2,159,871 | 0 | 0 |
| mart_p0b_lambdamart_v6_predictions | signal_date | 2,159,871 | 2,159,871 | 0 | 0 |
| mart_paper_sim_nav | date | 11,618 | 11,618 | 0 | 0 |

### D.2 抽样结果

`mart_p0b_oos_predictions` 前 5 行均为：

| trade_date | trade_date_dt | match |
|---|---|---|
| 2024-07-01 | 2024-07-01 | TRUE |

`mart_p0b_lambdamart_v6_predictions` 前 5 行均为：

| trade_date | trade_date_dt | match |
|---|---|---|
| 2024-07-01 | 2024-07-01 | TRUE |

`mart_paper_sim_nav` 前 5 行：

| trade_date | trade_date_dt | match |
|---|---|---|
| 2023-01-03 | 2023-01-03 | TRUE |
| 2023-01-04 | 2023-01-04 | TRUE |
| 2023-01-05 | 2023-01-05 | TRUE |
| 2023-01-06 | 2023-01-06 | TRUE |
| 2023-01-09 | 2023-01-09 | TRUE |

## E. Rollback 方案

Phase A 是纯 additive 变更。
Rollback 只删除新增缓存列，不丢失业务原始数据。

```sql
ALTER TABLE mart_p0b_oos_predictions DROP COLUMN trade_date_dt;
ALTER TABLE mart_p0b_lambdamart_v6_predictions DROP COLUMN trade_date_dt;
ALTER TABLE mart_paper_sim_nav DROP COLUMN trade_date_dt;
```

Rollback 后现有 query 仍然可运行。
原因是本轮没有切任何 query 到 `trade_date_dt`。
如果 Phase B 已上线，必须先恢复 Phase B query。
如果 Phase C 已上线，不能再按 Phase A rollback 处理。

## F. 估加速与 benchmark

原始全局估算：TEXT 日期收敛到 DATE 后，range scan 与 join hot path 可能加速 20% 到 40%。
该估算主要适用于仍为 VARCHAR 的 9 处 `trade_date` 列。
本轮三张指定表里，两张 P0b 表源列已经是 `signal_date DATE`。
因此本轮 benchmark 只作为 Phase A smoke benchmark，不外推为全局收益。

benchmark 文件：

```text
backend/tests/scripts/test_perf_p1_trade_date.py
```

运行命令：

```bash
PYTHONPATH=backend python -m pytest -q -s backend/tests/scripts/test_perf_p1_trade_date.py -m perf
```

连接方式：

```python
duckdb.connect("data/smartmoney.duckdb", read_only=True)
```

Q1：

```sql
SELECT COUNT(*)
FROM mart_p0b_oos_predictions
WHERE signal_date BETWEEN '2024-01-01' AND '2024-12-31';
```

Q2：

```sql
SELECT COUNT(*)
FROM mart_p0b_oos_predictions
WHERE trade_date_dt BETWEEN '2024-01-01'::DATE AND '2024-12-31'::DATE;
```

实测结果：

| query | elapsed_s | row_count |
|---|---:|---:|
| Q1 source date column | 0.001861 | 628,438 |
| Q2 trade_date_dt DATE | 0.000901 | 628,438 |

单次 speedup 约为 2.07x。
由于 Q1 源列本身是 DATE，该数字只说明新增列可用且 parity 成立。
Phase B/C 应在 `fact_financial_pit_daily` 或 `fact_capital_flow_pit_daily` 等真实 VARCHAR 表上做 5 次 warm run median。

## G. 后续清单

Phase A 已完成。
下一步先挑一条 paper_sim 或 PIT hot path 做 Phase B 小切片。
Phase B 小切片只改一个 query。
Phase B 小切片必须记录旧/新 row count parity。
Phase C 暂不建议在 GCP retrain 期间执行。
Phase C 触碰原列类型，应安排在可停写窗口。

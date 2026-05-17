# ChunkyMonkey P0: SUE / 业绩超预期 PIT factor 完整设计文档 + 执行 plan

| 项 | 值 |
|---|---:|
| 文档日期 | 2026-05-17 |
| 输出文件 | `docs/sue_pit_design_20260517.md` |
| 工作目录 | `/Users/dp/Documents/M/stock/chunkymonkey` |
| PIT 等级 | CRITICAL，不允许折中 |
| 现有接入点 | `backend/services/labels/feature_join_v4.py` |
| 现有财务 PIT 表 | `fact_financial_pit_daily` |
| 禁止复用反例 | `latest snapshot`、`current_label_fallback`、全期 in-sample fit |

| PIT 硬约束 | 数字/规则 |
|---|---:|
| 财报/预告/快报可用边界 | `notice_date <= signal_date`；若信号在开盘前生成，强制 `notice_date < signal_date` |
| 时间戳边界 | `source_ts < signal_ts`，不用 `<=` |
| 机构预测边界 | `forecast_date <= signal_date` 且 `forecast_snapshot_ts < signal_ts` |
| 最小机构数 | `consensus_n >= 3`，否则 SUE 为 `NULL` |
| 标准差下限 | `expected_eps_std > 0`，否则 SUE 为 `NULL` |
| 业绩预告/快报 stale window | `date_diff('day', latest_yj_notice_date, signal_date) <= N` |
| PIT audit 验收 | future-row injection 后因子值变化数 `= 0` |

## Part A: PIT 严格化设计

### A.0 PIT 边界定义

| 字段 | 含义 | 可参与 JOIN 的条件 | PIT 失败条件 |
|---|---|---|---|
| `signal_date` | 产生因子的交易日 | date-only 源满足 `source_date <= signal_date` | `source_date > signal_date` |
| `signal_ts` | 产生因子的精确时间 | timestamp 源满足 `source_ts < signal_ts` | `source_ts >= signal_ts` |
| `notice_date` | 财报/预告/快报公告日期 | `notice_date <= signal_date` | 用 `MAX(notice_date)` 无边界 |
| `forecast_date` | 机构预测发布日期 | `forecast_date <= signal_date` | 用最新一致预期快照 |
| `forecast_snapshot_ts` | 预测记录可见时间戳 | `forecast_snapshot_ts < signal_ts` | 用采集时 `updated_at=今日` 当历史快照 |

| [PIT 不安全] 场景 | 处理 |
|---|---|
| 只有 `updated_at=今日`，没有原始公告/预测发布日期 | 该字段不得进入因子，写入 `NULL` |
| 同日公告但信号在开盘前生成 | 不允许 `notice_date = signal_date`，必须用 `notice_date < signal_date` |
| AkShare/东方财富接口只返回当前聚合值，没有历史预测明细 | 不得 backfill 成历史一致预期，必须保留原始 `forecast_date` 明细 |
| `current_label_fallback` 或历史值跨 N 天延续 | 超过窗口直接 `NULL` |

### A.1 DDL schema 设计：3 张 period fact 表

#### `fact_yjyg_period`：业绩预告

```sql
CREATE TABLE IF NOT EXISTS fact_yjyg_period (
    stock_code VARCHAR NOT NULL,
    stock_name VARCHAR,
    report_period DATE NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER NOT NULL,
    notice_date DATE NOT NULL,
    notice_ts TIMESTAMP,
    source_event_id VARCHAR,
    forecast_type VARCHAR,
    forecast_summary VARCHAR,
    forecast_net_profit_min DOUBLE,
    forecast_net_profit_max DOUBLE,
    forecast_np_yoy_min DOUBLE,
    forecast_np_yoy_max DOUBLE,
    forecast_eps_min DOUBLE,
    forecast_eps_max DOUBLE,
    currency VARCHAR DEFAULT 'CNY',
    source_system VARCHAR NOT NULL DEFAULT 'eastmoney_yjyg',
    source_query_date DATE NOT NULL,
    source_url VARCHAR,
    ingest_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_hash VARCHAR NOT NULL,
    PRIMARY KEY (stock_code, report_period, notice_date, row_hash)
);

CREATE INDEX IF NOT EXISTS idx_yjyg_pit
ON fact_yjyg_period(stock_code, notice_date, report_period);

CREATE INDEX IF NOT EXISTS idx_yjyg_period
ON fact_yjyg_period(report_period, stock_code);
```

#### `fact_yjkb_period`：业绩快报

```sql
CREATE TABLE IF NOT EXISTS fact_yjkb_period (
    stock_code VARCHAR NOT NULL,
    stock_name VARCHAR,
    report_period DATE NOT NULL,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER NOT NULL,
    notice_date DATE NOT NULL,
    notice_ts TIMESTAMP,
    source_event_id VARCHAR,
    revenue DOUBLE,
    operating_profit DOUBLE,
    total_profit DOUBLE,
    net_profit DOUBLE,
    net_profit_yoy DOUBLE,
    basic_eps DOUBLE,
    diluted_eps DOUBLE,
    roe_weighted DOUBLE,
    total_assets DOUBLE,
    equity_attributable DOUBLE,
    source_system VARCHAR NOT NULL DEFAULT 'eastmoney_yjkb',
    source_query_date DATE NOT NULL,
    source_url VARCHAR,
    ingest_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_hash VARCHAR NOT NULL,
    PRIMARY KEY (stock_code, report_period, notice_date, row_hash)
);

CREATE INDEX IF NOT EXISTS idx_yjkb_pit
ON fact_yjkb_period(stock_code, notice_date, report_period);

CREATE INDEX IF NOT EXISTS idx_yjkb_period
ON fact_yjkb_period(report_period, stock_code);
```

#### `fact_profit_forecast_period`：机构盈利预测历史

```sql
CREATE TABLE IF NOT EXISTS fact_profit_forecast_period (
    stock_code VARCHAR NOT NULL,
    stock_name VARCHAR,
    institution_id VARCHAR NOT NULL,
    institution_name VARCHAR NOT NULL,
    analyst_name VARCHAR,
    report_period DATE NOT NULL,
    forecast_year INTEGER NOT NULL,
    forecast_date DATE NOT NULL,
    forecast_snapshot_ts TIMESTAMP NOT NULL,
    eps_forecast DOUBLE,
    net_profit_forecast DOUBLE,
    revenue_forecast DOUBLE,
    operating_profit_forecast DOUBLE,
    rating VARCHAR,
    target_price DOUBLE,
    source_record_id VARCHAR,
    source_system VARCHAR NOT NULL DEFAULT 'eastmoney_profit_forecast',
    source_symbol VARCHAR NOT NULL,
    ingest_ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_hash VARCHAR NOT NULL,
    PRIMARY KEY (stock_code, institution_id, report_period, forecast_date, row_hash)
);

CREATE INDEX IF NOT EXISTS idx_profit_forecast_pit
ON fact_profit_forecast_period(stock_code, forecast_date, report_period, institution_id);

CREATE INDEX IF NOT EXISTS idx_profit_forecast_snapshot
ON fact_profit_forecast_period(stock_code, forecast_snapshot_ts, report_period);
```

### A.2 规则 1：报表口径 PIT 化

| 要求 | SQL 条件 | 验收数字 |
|---|---|---:|
| 财报 JOIN 不得看未来公告 | `notice_date <= signal_date` | violation count `= 0` |
| 开盘前信号 | `notice_date < signal_date` | violation count `= 0` |
| timestamp 源 | `notice_ts < signal_ts` | violation count `= 0` |

```sql
-- DuckDB ASOF JOIN：按 signal_date 取当时已经公告的最近一份财报。
WITH panel AS (
    SELECT
        stock_code,
        CAST(signal_date AS DATE) AS signal_date,
        CAST(signal_ts AS TIMESTAMP) AS signal_ts
    FROM mart_p0a_label_panel
),
financial AS (
    SELECT
        stock_code,
        CAST(notice_date AS DATE) AS notice_date,
        CAST(report_period AS DATE) AS report_period,
        actual_eps,
        net_profit,
        revenue
    FROM fact_financial_pit_daily
    WHERE notice_date IS NOT NULL
)
SELECT
    p.stock_code,
    p.signal_date,
    f.report_period,
    f.notice_date,
    f.actual_eps,
    f.net_profit,
    f.revenue
FROM panel p
ASOF LEFT JOIN financial f
  ON p.stock_code = f.stock_code
 AND p.signal_date >= f.notice_date;
```

```sql
-- strict boundary：适用于开盘前生成信号，或者只允许前一日已知信息。
SELECT
    p.stock_code,
    p.signal_date,
    f.report_period,
    f.notice_date,
    f.actual_eps
FROM mart_p0a_label_panel p
LEFT JOIN fact_financial_pit_daily f
  ON p.stock_code = f.stock_code
 AND CAST(f.notice_date AS DATE) < CAST(p.signal_date AS DATE)
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.stock_code, p.signal_date
    ORDER BY CAST(f.notice_date AS DATE) DESC, CAST(f.report_period AS DATE) DESC
) = 1;
```

```sql
-- PIT audit：任何非 0 都直接失败。
SELECT
    COUNT(*) AS pit_violation_rows
FROM joined_financial_features
WHERE notice_date > signal_date;
```

### A.3 规则 2：预期值 PIT 化

| 要求 | 实现 |
|---|---|
| 只用 `signal_date` 前已发布预测 | `pf.forecast_date <= p.signal_date` |
| 每机构只取当时 latest snapshot | `ROW_NUMBER() ... ORDER BY forecast_date DESC, forecast_snapshot_ts DESC` |
| 禁止 latest-snapshot leakage | 不允许 `MAX(forecast_date)` 脱离 `signal_date` |

```sql
WITH panel AS (
    SELECT
        stock_code,
        CAST(signal_date AS DATE) AS signal_date,
        CAST(signal_ts AS TIMESTAMP) AS signal_ts,
        CAST(target_report_period AS DATE) AS target_report_period
    FROM sue_signal_grid
),
forecast_candidates AS (
    SELECT
        p.stock_code,
        p.signal_date,
        p.target_report_period,
        pf.institution_id,
        pf.institution_name,
        pf.analyst_name,
        pf.eps_forecast,
        pf.net_profit_forecast,
        pf.forecast_date,
        pf.forecast_snapshot_ts,
        ROW_NUMBER() OVER (
            PARTITION BY
                p.stock_code,
                p.signal_date,
                p.target_report_period,
                pf.institution_id
            ORDER BY
                pf.forecast_date DESC,
                pf.forecast_snapshot_ts DESC,
                pf.row_hash DESC
        ) AS rn
    FROM panel p
    JOIN fact_profit_forecast_period pf
      ON p.stock_code = pf.stock_code
     AND p.target_report_period = pf.report_period
     AND pf.forecast_date <= p.signal_date
     AND pf.forecast_snapshot_ts < p.signal_ts
    WHERE pf.eps_forecast IS NOT NULL
)
SELECT *
FROM forecast_candidates
WHERE rn = 1;
```

```sql
-- [PIT 不安全] 检测：预测发布日期晚于 signal_date，不允许进入任何 consensus。
SELECT
    COUNT(*) AS future_forecast_rows
FROM latest_forecast_per_institution
WHERE forecast_date > signal_date
   OR forecast_snapshot_ts >= signal_ts;
```

### A.4 规则 3：多机构一致预期 avg/median/std

| 输出字段 | 公式 | 最小样本 |
|---|---|---:|
| `expected_eps_mean` | `AVG(eps_forecast)` | `consensus_n >= 3` |
| `expected_eps_median` | `MEDIAN(eps_forecast)` | `consensus_n >= 3` |
| `expected_eps_std` | `STDDEV_SAMP(eps_forecast)` | `consensus_n >= 3` 且 `> 0` |
| `forecast_inst_count` | `COUNT(DISTINCT institution_id)` | `>= 3` |

```sql
WITH latest_forecast_per_inst AS (
    SELECT *
    FROM latest_forecast_per_institution
    WHERE rn = 1
)
SELECT
    stock_code,
    signal_date,
    target_report_period AS report_period,
    COUNT(DISTINCT institution_id) AS forecast_inst_count,
    AVG(eps_forecast) AS expected_eps_mean,
    MEDIAN(eps_forecast) AS expected_eps_median,
    STDDEV_SAMP(eps_forecast) AS expected_eps_std,
    AVG(net_profit_forecast) AS expected_net_profit_mean,
    STDDEV_SAMP(net_profit_forecast) AS expected_net_profit_std
FROM latest_forecast_per_inst
GROUP BY stock_code, signal_date, target_report_period
HAVING COUNT(DISTINCT institution_id) >= 3
   AND STDDEV_SAMP(eps_forecast) > 0;
```

```sql
-- coverage audit：样本不足必须 NULL，不能 fallback 到 1-2 家机构均值。
SELECT
    COUNT(*) AS bad_consensus_rows
FROM sue_consensus_daily
WHERE forecast_inst_count < 3
  AND expected_eps_mean IS NOT NULL;
```

### A.5 规则 4：SUE 公式

| 因子 | 公式 | NULL 条件 |
|---|---|---|
| `sue_raw` | `(actual_eps - expected_eps_mean) / expected_eps_std` | `expected_eps_std <= 0` |
| `sue` | cross-sectional winsor + zscore of `sue_raw` | 当日有效样本 `< 30` |
| `eps_surprise_pct` | `actual_eps / expected_eps_mean - 1` | `expected_eps_mean = 0` |

```sql
WITH sue_raw AS (
    SELECT
        a.stock_code,
        a.signal_date,
        a.report_period,
        a.actual_eps,
        c.expected_eps_mean,
        c.expected_eps_median,
        c.expected_eps_std,
        c.forecast_inst_count,
        CASE
            WHEN c.forecast_inst_count >= 3
             AND c.expected_eps_std > 0
            THEN (a.actual_eps - c.expected_eps_mean) / c.expected_eps_std
            ELSE NULL
        END AS sue_raw,
        CASE
            WHEN c.forecast_inst_count >= 3
             AND c.expected_eps_mean IS NOT NULL
             AND ABS(c.expected_eps_mean) > 1e-9
            THEN a.actual_eps / c.expected_eps_mean - 1.0
            ELSE NULL
        END AS eps_surprise_pct
    FROM pit_actual_eps_daily a
    JOIN sue_consensus_daily c
      ON a.stock_code = c.stock_code
     AND a.signal_date = c.signal_date
     AND a.report_period = c.report_period
)
SELECT
    *,
    CASE
        WHEN COUNT(sue_raw) OVER (PARTITION BY signal_date) >= 30
        THEN (
            LEAST(GREATEST(sue_raw, q01), q99)
            - AVG(LEAST(GREATEST(sue_raw, q01), q99)) OVER (PARTITION BY signal_date)
        ) / NULLIF(STDDEV_SAMP(LEAST(GREATEST(sue_raw, q01), q99)) OVER (PARTITION BY signal_date), 0)
        ELSE NULL
    END AS sue
FROM (
    SELECT
        sue_raw.*,
        QUANTILE_CONT(sue_raw, 0.01) OVER (PARTITION BY signal_date) AS q01,
        QUANTILE_CONT(sue_raw, 0.99) OVER (PARTITION BY signal_date) AS q99
    FROM sue_raw
) x;
```

### A.6 规则 5：业绩预告优先级

| 优先级 | 来源 | 公告日字段 | actual EPS 口径 |
|---:|---|---|---|
| 1 | `fact_yjyg_period` | `notice_date` | `forecast_eps_min/max` 中点；无 EPS 时用净利润同比方向，不强造 EPS |
| 2 | `fact_yjkb_period` | `notice_date` | `basic_eps` |
| 3 | `fact_financial_pit_daily` | `notice_date` | `actual_eps` |

```sql
WITH event_actuals AS (
    SELECT
        stock_code,
        report_period,
        notice_date,
        notice_ts,
        'yjyg' AS source_type,
        1 AS source_priority,
        CASE
            WHEN forecast_eps_min IS NOT NULL AND forecast_eps_max IS NOT NULL
            THEN (forecast_eps_min + forecast_eps_max) / 2.0
            ELSE NULL
        END AS actual_eps_pit,
        forecast_net_profit_min,
        forecast_net_profit_max
    FROM fact_yjyg_period

    UNION ALL

    SELECT
        stock_code,
        report_period,
        notice_date,
        notice_ts,
        'yjkb' AS source_type,
        2 AS source_priority,
        basic_eps AS actual_eps_pit,
        net_profit AS forecast_net_profit_min,
        net_profit AS forecast_net_profit_max
    FROM fact_yjkb_period

    UNION ALL

    SELECT
        stock_code,
        report_period,
        notice_date,
        NULL AS notice_ts,
        'formal_report' AS source_type,
        3 AS source_priority,
        actual_eps AS actual_eps_pit,
        net_profit AS forecast_net_profit_min,
        net_profit AS forecast_net_profit_max
    FROM fact_financial_pit_daily
    WHERE actual_eps IS NOT NULL
),
pit_candidates AS (
    SELECT
        p.stock_code,
        p.signal_date,
        e.report_period,
        e.notice_date,
        e.source_type,
        e.source_priority,
        e.actual_eps_pit,
        ROW_NUMBER() OVER (
            PARTITION BY p.stock_code, p.signal_date, e.report_period
            ORDER BY e.source_priority ASC, e.notice_date DESC
        ) AS rn
    FROM sue_signal_grid p
    JOIN event_actuals e
      ON p.stock_code = e.stock_code
     AND e.notice_date <= p.signal_date
     AND (p.signal_ts IS NULL OR e.notice_ts IS NULL OR e.notice_ts < p.signal_ts)
)
SELECT *
FROM pit_candidates
WHERE rn = 1;
```

```sql
-- priority audit：同一 stock/report/signal 下若 yjyg 可用，不允许 yjkb/formal 覆盖。
WITH available AS (
    SELECT stock_code, signal_date, report_period, MIN(source_priority) AS best_priority
    FROM pit_candidates
    GROUP BY stock_code, signal_date, report_period
)
SELECT COUNT(*) AS priority_violation_rows
FROM pit_actual_eps_daily a
JOIN available b USING (stock_code, signal_date, report_period)
WHERE a.source_priority <> b.best_priority;
```

### A.7 规则 6：fallback 规则

| 规则 | 实现 | 禁止 |
|---|---|---|
| 最近 yjyg/yjkb 距 `signal_date` 超过 N 天 | 因子值 `NULL` | fallback 到上一期历史值 |
| 没有 yjyg/yjkb | event-drift 类因子 `NULL` | 用正式财报补事件漂移 |
| N 参数 | `5/10/20/30` 按因子显式传入 | 默认无限期延续 |

```sql
WITH latest_yj_event AS (
    SELECT
        p.stock_code,
        p.signal_date,
        e.report_period,
        e.notice_date AS latest_yj_notice_date,
        e.source_type,
        ROW_NUMBER() OVER (
            PARTITION BY p.stock_code, p.signal_date
            ORDER BY e.notice_date DESC, e.source_priority ASC
        ) AS rn
    FROM sue_signal_grid p
    JOIN (
        SELECT stock_code, report_period, notice_date, 'yjyg' AS source_type, 1 AS source_priority
        FROM fact_yjyg_period
        UNION ALL
        SELECT stock_code, report_period, notice_date, 'yjkb' AS source_type, 2 AS source_priority
        FROM fact_yjkb_period
    ) e
      ON p.stock_code = e.stock_code
     AND e.notice_date <= p.signal_date
),
bounded AS (
    SELECT
        stock_code,
        signal_date,
        report_period,
        latest_yj_notice_date,
        source_type,
        CASE
            WHEN DATE_DIFF('day', latest_yj_notice_date, signal_date) <= :max_stale_days
            THEN 1
            ELSE 0
        END AS within_stale_window
    FROM latest_yj_event
    WHERE rn = 1
)
SELECT
    f.stock_code,
    f.signal_date,
    CASE WHEN b.within_stale_window = 1 THEN f.sue ELSE NULL END AS sue_event_bounded,
    CASE WHEN b.within_stale_window = 1 THEN f.eps_surprise_pct ELSE NULL END AS eps_surprise_pct_event_bounded
FROM sue_feature_raw f
LEFT JOIN bounded b
  ON f.stock_code = b.stock_code
 AND f.signal_date = b.signal_date;
```

```sql
-- stale fallback audit：超过 N 天仍有值就是失败。
SELECT
    COUNT(*) AS stale_non_null_rows
FROM sue_feature_daily
WHERE latest_yj_notice_date IS NOT NULL
  AND DATE_DIFF('day', latest_yj_notice_date, signal_date) > :max_stale_days
  AND (
      sue_event_bounded IS NOT NULL
      OR eps_surprise_pct_event_bounded IS NOT NULL
      OR yjyg_announcement_drift_5d IS NOT NULL
  );
```

### A.8 规则 7：历史 backfill 范围

| 表 | 范围 | 当前运行截止 | 期望 rows | 关键约束 |
|---|---|---:|---:|---|
| `fact_yjyg_period` | 2020-01-01 到 2026-12-31 已发布事件 | 2026-05-17 | `> 500,000` | GCP VM 获取，分批限频，保留公告日 |
| `fact_yjkb_period` | 2020-01-01 到 2026-12-31 已发布事件 | 2026-05-17 | `> 80,000` | 同一 `stock/report/notice/hash` 幂等 |
| `fact_profit_forecast_period` | 全 A 股约 5,000 股 | 2026-05-17 | `> 100,000` | 每机构、每预测发布日期明细 |

```python
from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterable

import akshare as ak
import duckdb
import pandas as pd


RUN_DATE = dt.date(2026, 5, 17)
REPORT_PERIODS = [
    f"{year}{month:02d}{day:02d}"
    for year in range(2020, 2027)
    for month, day in [(3, 31), (6, 30), (9, 30), (12, 31)]
]


def fetch_yjyg_periods(periods: Iterable[str], sleep_sec: float = 1.2) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for period in periods:
        df = ak.stock_yjyg_em(date=period)
        if df.empty:
            time.sleep(sleep_sec)
            continue
        df["source_query_date"] = pd.to_datetime(period).date()
        df["ingest_ts"] = pd.Timestamp.utcnow()
        frames.append(df)
        time.sleep(sleep_sec)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def reject_future_announcements(df: pd.DataFrame, notice_col: str = "notice_date") -> pd.DataFrame:
    notice_date = pd.to_datetime(df[notice_col]).dt.date
    bad = df.loc[notice_date > RUN_DATE, ["stock_code", notice_col]].head(10)
    if not bad.empty:
        raise ValueError(f"PIT violation: future yjyg notice_date found: {bad.to_dict('records')}")
    return df


def write_yjyg(db_path: str, df: pd.DataFrame) -> None:
    conn = duckdb.connect(db_path, read_only=False)
    conn.register("stage_yjyg", reject_future_announcements(df))
    conn.execute("""
        INSERT OR REPLACE INTO fact_yjyg_period
        SELECT
            stock_code,
            stock_name,
            report_period,
            fiscal_year,
            fiscal_quarter,
            notice_date,
            notice_ts,
            source_event_id,
            forecast_type,
            forecast_summary,
            forecast_net_profit_min,
            forecast_net_profit_max,
            forecast_np_yoy_min,
            forecast_np_yoy_max,
            forecast_eps_min,
            forecast_eps_max,
            'CNY' AS currency,
            'eastmoney_yjyg' AS source_system,
            source_query_date,
            source_url,
            ingest_ts,
            row_hash
        FROM stage_yjyg
    """)
```

### A.9 PIT-as-code：Python decorator 模板

| 能力 | 行为 |
|---|---|
| date boundary | 任一 `date_cols` 大于 `signal_date` 直接 raise |
| timestamp boundary | 任一 `timestamp_cols` 大于等于 `signal_ts` 直接 raise |
| 输出约束 | 函数返回 DataFrame 后自动检查 |
| 错误信息 | 打印前 10 条违规样本 |

```python
from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import wraps
from typing import ParamSpec, TypeVar

import pandas as pd


P = ParamSpec("P")
R = TypeVar("R")


class PITViolation(ValueError):
    """Raised when a feature function emits rows that use future information."""


def _as_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def enforce_pit(
    *,
    signal_date_col: str = "signal_date",
    signal_ts_col: str = "signal_ts",
    date_cols: Iterable[str] = ("notice_date", "forecast_date"),
    timestamp_cols: Iterable[str] = ("notice_ts", "forecast_snapshot_ts"),
) -> Callable[[Callable[P, pd.DataFrame]], Callable[P, pd.DataFrame]]:
    def decorator(fn: Callable[P, pd.DataFrame]) -> Callable[P, pd.DataFrame]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> pd.DataFrame:
            out = fn(*args, **kwargs)
            if not isinstance(out, pd.DataFrame):
                raise TypeError(f"{fn.__name__} must return pandas.DataFrame for PIT enforcement")

            if signal_date_col not in out.columns:
                raise PITViolation(f"{fn.__name__}: missing {signal_date_col}")

            signal_date = _as_datetime(out[signal_date_col]).dt.normalize()
            for col in date_cols:
                if col not in out.columns:
                    continue
                source_date = _as_datetime(out[col]).dt.normalize()
                mask = source_date.notna() & signal_date.notna() & (source_date > signal_date)
                if mask.any():
                    sample = out.loc[mask, ["stock_code", signal_date_col, col]].head(10)
                    raise PITViolation(
                        f"{fn.__name__}: {col} > {signal_date_col}: {sample.to_dict('records')}"
                    )

            if signal_ts_col in out.columns:
                signal_ts = _as_datetime(out[signal_ts_col])
                for col in timestamp_cols:
                    if col not in out.columns:
                        continue
                    source_ts = _as_datetime(out[col])
                    mask = source_ts.notna() & signal_ts.notna() & (source_ts >= signal_ts)
                    if mask.any():
                        sample = out.loc[mask, ["stock_code", signal_ts_col, col]].head(10)
                        raise PITViolation(
                            f"{fn.__name__}: {col} >= {signal_ts_col}: {sample.to_dict('records')}"
                        )

            return out

        return wrapper

    return decorator


@enforce_pit(
    date_cols=("notice_date", "forecast_date", "latest_yj_notice_date"),
    timestamp_cols=("notice_ts", "forecast_snapshot_ts"),
)
def load_sue_pit_features(
    conn: object,
    *,
    start_date: str,
    end_date: str,
    max_stale_days: int,
) -> pd.DataFrame:
    return conn.execute(
        """
        SELECT
            stock_code,
            signal_date,
            signal_ts,
            notice_date,
            notice_ts,
            forecast_date,
            forecast_snapshot_ts,
            latest_yj_notice_date,
            sue,
            sue_rolling_4q,
            eps_surprise_pct,
            forecast_upward_revision_30d,
            yjyg_announcement_drift_5d,
            yjyg_announcement_drift_10d,
            yjyg_announcement_drift_20d,
            profit_forecast_consensus_std_30d
        FROM fact_sue_pit_daily
        WHERE signal_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
          AND (
              latest_yj_notice_date IS NULL
              OR DATE_DIFF('day', latest_yj_notice_date, signal_date) <= ?
          )
        """,
        [start_date, end_date, max_stale_days],
    ).fetchdf()
```

### A.10 DuckDB JOIN SQL 模板：ASOF + strict boundary

| 模板 | 使用场景 | 边界 |
|---|---|---|
| ASOF date | 日频收盘后信号 | `source_date <= signal_date` |
| strict date | 次日开盘前信号 | `source_date < signal_date` |
| timestamp | 有精确时间戳 | `source_ts < signal_ts` |

```sql
-- ASOF date template。
SELECT
    p.stock_code,
    p.signal_date,
    s.source_date,
    s.value
FROM signal_panel p
ASOF LEFT JOIN source_period_fact s
  ON p.stock_code = s.stock_code
 AND p.signal_date >= s.source_date;
```

```sql
-- strict date template。
SELECT
    p.stock_code,
    p.signal_date,
    s.source_date,
    s.value
FROM signal_panel p
LEFT JOIN source_period_fact s
  ON p.stock_code = s.stock_code
 AND s.source_date < p.signal_date
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.stock_code, p.signal_date
    ORDER BY s.source_date DESC
) = 1;
```

```sql
-- timestamp template。
SELECT
    p.stock_code,
    p.signal_ts,
    s.source_ts,
    s.value
FROM signal_panel p
LEFT JOIN source_period_fact s
  ON p.stock_code = s.stock_code
 AND s.source_ts < p.signal_ts
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY p.stock_code, p.signal_ts
    ORDER BY s.source_ts DESC
) = 1;
```

## Part B: 因子 spec

| 因子 | 公式 | 数据源 | JOIN 路径 | 预期 \|Spearman\| | horizon | 失败模式 |
|---|---|---|---|---:|---|---|
| `sue` | `zscore_cs(winsor((actual_eps_pit - expected_eps_mean) / expected_eps_std, 1%, 99%))` | `fact_yjyg_period`、`fact_yjkb_period`、`fact_financial_pit_daily`、`fact_profit_forecast_period` | `signal_panel -> pit_actual_eps_daily -> sue_consensus_daily`，所有源满足 `notice_date/forecast_date <= signal_date` | `0.03-0.08`，若接近 `0.10` 可与 v4 Top 15 中期反转同量级 | `20d/60d` 为主，`5d` 次要 | 机构数不足导致 coverage 低；EPS std 过小；公告日同日开盘泄漏；正式财报覆盖预告优先级 |
| `sue_rolling_4q` | `AVG(sue_raw) OVER last 4 report_periods` 或 `slope(last_4q_sue_raw)` 后截面 zscore | 同 `sue`，按 `report_period` 串联 | `sue_raw_period -> rolling by stock_code/report_period -> signal_date ASOF` | `0.03-0.07` | `60d` 为主，`20d` 次要 | 季度缺失导致趋势错位；跨年 Q4/Q1 period 排序错误；用未来季度补齐 |
| `eps_surprise_pct` | `actual_eps_pit / expected_eps_mean - 1`，截面 winsor+zscore | 同 `sue` | `pit_actual_eps_daily JOIN sue_consensus_daily` | `0.02-0.06` | `5d/20d` | `expected_eps_mean` 接近 0 放大噪声；亏损股符号反转；未处理 EPS 为负 |
| `forecast_upward_revision_30d` | `count(inst EPS forecast up in last 30d) / count(inst with comparable forecast)` | `fact_profit_forecast_period` | `signal_panel -> forecasts where forecast_date in [signal_date-30, signal_date] -> compare per institution latest vs previous` | `0.02-0.06` | `20d/60d` | 把新覆盖机构误算为上修；预测发布日期缺失；机构名称未标准化 |
| `yjyg_announcement_drift_Nd` | `excess_return(stock, notice_date+1..notice_date+N) - benchmark/industry return`，N=`5/10/20`，再 ASOF 到 `signal_date` 且 stale window 内 | `fact_yjyg_period`、`fact_yjkb_period`、`v_price_kline_qfq`、行业 PIT 表 | `yj event -> next tradable date -> return window -> signal_panel ASOF` | `0.02-0.05`，事件子样本中可更高 | `5d/10d/20d` | 用公告后收益预测公告前样本；停牌/涨跌停不可交易；行业标签 current fallback |
| `profit_forecast_consensus_std_30d` | `STDDEV_SAMP(latest eps_forecast by institution in last 30d) / ABS(mean)`，方向默认高不确定性为负向 | `fact_profit_forecast_period` | `signal_panel -> latest per institution where forecast_date <= signal_date and >= signal_date-30` | `0.01-0.04` | `20d/60d` | 分母接近 0；机构样本太少；高分歧在反转和成长股中方向不同 |

| 对比基准 | 数字解释 |
|---|---|
| v4 panel USEFUL Top 15 中期反转 | \|Spearman\| ~0.10 量级 |
| SUE 单因子目标 | 不要求单因子稳定超过 `0.10`；若 `20d/60d` OOS \|Spearman\| >= `0.03` 且 PIT audit 全 0，可进入 ablation |
| SUE feature group 目标 | 6 个 sub-factor 组合后，LightGBM/LambdaMART ablation 的 OOS uplift `> 0`，且 rank IC 不靠单日尖峰 |

## Part C: 执行 plan

| 步骤 | 步骤名 | DDL/代码 file path | 输入 source + 期望 rows | PIT-strict 单测案例 | 验收数字 | 工作量(h) |
|---:|---|---|---|---|---:|---:|
| 1 | backfill `fact_yjyg_period` | `backend/scripts/backfill_yjyg_period.py`；DDL 可放 `backend/services/features/sue_pit.py` 或后续 migration | `akshare ak.stock_yjyg_em(date='YYYYMMDD')`；2020-2026 已发布事件；`> 500,000` rows | 注入 `notice_date = signal_date + 1`，构造 panel 后 yjyg 因子仍为 `NULL` | duplicate key `= 0`；future notice violations `= 0`；rows `> 500,000` | `8-12` |
| 2 | backfill `fact_profit_forecast_period` | `backend/scripts/backfill_profit_forecast_period.py`；复用 `backend/scripts/ingest_profit_forecast_snapshot.py` 可用解析逻辑 | `akshare ak.stock_profit_forecast_em(symbol)`；全 A 股约 5,000 股；`> 100,000` rows | 对同一机构注入未来上修，`forecast_upward_revision_30d` 在注入前后完全相同 | rows `> 100,000`；`forecast_date > RUN_DATE` 为 `0`；机构数 `> 20` | `10-16` |
| 3 | 实现 `sue_pit.py` | `backend/services/features/sue_pit.py`；测试 `backend/tests/features/test_sue_pit.py` | `fact_yjyg_period`、`fact_yjkb_period`、`fact_profit_forecast_period`、`fact_financial_pit_daily` | 每个函数带 `signal_date/signal_ts/max_stale_days`；future data injection 后因子 hash 不变 | 6 个 sub-factor 非空列存在；PIT decorator violation tests 通过；coverage 日报输出 | `12-18` |
| 4 | wire 进 feature_join_v4 或 v5 | `backend/services/labels/feature_join_v4.py`；driver `backend/scripts/build_p0a_feature_panel_v4.py`；若升 v5 则新建 v5 owner | `sue_pit.py` 输出列 JOIN 到 `mart_p0a_feature_label_panel_v4` 或 v5 | 在 feature join mock DB 中加入未来 forecast/yjyg，panel 的 `sue_*` 列不变 | 新增 6 列；原 v4 列数只增不破坏；PIT audit `= 0` | `6-10` |
| 5 | unit test + PIT audit + ablation | `backend/tests/features/test_sue_pit.py`；`backend/tests/labels/test_feature_join_v4.py`；`backend/scripts/run_feature_group_ablation.py` | v4 panel + SUE group；5d/20d/60d labels | 至少 1 个 strict test：注入 future row，所有因子值不变；再注入合法历史 row，因子值按预期改变 | future injection changed rows `= 0`；OOS \|Spearman\| 有效样本 `>= 30` 日；ablation uplift 记录入库 | `12-16` |

### C.1 Step 1 详细说明：backfill `fact_yjyg_period`

| 项 | 设计 |
|---|---|
| 运行位置 | GCP VM，本地 `push2his.eastmoney.com` 被 block，不在本地抓取 |
| 分批 | 按报告期 `20200331` 到 `20261231`，实际只写入 `notice_date <= 2026-05-17` |
| 限频 | `sleep >= 1.2s`，失败指数退避，单批失败重试 `<= 3` |
| 幂等 | `PRIMARY KEY(stock_code, report_period, notice_date, row_hash)` |
| 输出 | period fact 表，不直接写 daily panel |

```bash
PYTHONPATH=backend python backend/scripts/backfill_yjyg_period.py \
  --db data/market.duckdb \
  --start-report-period 20200331 \
  --end-report-period 20261231 \
  --run-date 2026-05-17 \
  --sleep-sec 1.2 \
  --source eastmoney_yjyg
```

```sql
SELECT
    COUNT(*) AS rows,
    COUNT(DISTINCT stock_code) AS stocks,
    MIN(notice_date) AS min_notice_date,
    MAX(notice_date) AS max_notice_date,
    SUM(CASE WHEN notice_date > DATE '2026-05-17' THEN 1 ELSE 0 END) AS future_notice_rows
FROM fact_yjyg_period;
```

### C.2 Step 2 详细说明：backfill `fact_profit_forecast_period`

| 项 | 设计 |
|---|---|
| 股票池 | security master 全 A 股，期望约 `5,000` 只 |
| 粒度 | `stock_code + institution_id + report_period + forecast_date + row_hash` |
| 预测边界 | 只认 `forecast_date` 和 `forecast_snapshot_ts`，不认采集当天 `updated_at` |
| [PIT 不安全] | 如果接口只给当前一致预期且无机构预测发布日期，该记录不得进入历史 period fact |

```bash
PYTHONPATH=backend python backend/scripts/backfill_profit_forecast_period.py \
  --db data/market.duckdb \
  --universe all_a \
  --run-date 2026-05-17 \
  --sleep-sec 0.8 \
  --source eastmoney_profit_forecast
```

```sql
SELECT
    COUNT(*) AS rows,
    COUNT(DISTINCT stock_code) AS stocks,
    COUNT(DISTINCT institution_id) AS institutions,
    MIN(forecast_date) AS min_forecast_date,
    MAX(forecast_date) AS max_forecast_date,
    SUM(CASE WHEN forecast_date > DATE '2026-05-17' THEN 1 ELSE 0 END) AS future_forecast_rows
FROM fact_profit_forecast_period;
```

### C.3 Step 3 详细说明：实现 `backend/services/features/sue_pit.py`

| 函数 | 输入 | 输出 | PIT 参数 |
|---|---|---|---|
| `build_pit_actual_eps_daily` | yjyg/yjkb/formal report | `actual_eps_pit`、`source_type`、`notice_date` | `signal_date`、`signal_ts` |
| `build_forecast_consensus_daily` | forecast period fact | `expected_eps_mean/median/std`、`forecast_inst_count` | `signal_date`、`signal_ts` |
| `compute_sue` | actual + consensus | `sue` | `min_inst_count=3` |
| `compute_sue_rolling_4q` | `sue_raw` period series | `sue_rolling_4q` | `max_quarter_gap=4` |
| `compute_forecast_revision_30d` | forecast period fact | `forecast_upward_revision_30d` | `lookback_days=30` |
| `compute_yjyg_drift_nd` | yjyg/yjkb + kline | `yjyg_announcement_drift_5d/10d/20d` | `N`、`max_stale_days` |
| `compute_consensus_std_30d` | forecast period fact | `profit_forecast_consensus_std_30d` | `lookback_days=30` |

```python
def compute_sue(
    conn: object,
    *,
    start_date: str,
    end_date: str,
    signal_boundary: str = "close",
    min_inst_count: int = 3,
    max_stale_days: int = 30,
) -> object:
    if signal_boundary not in {"close", "next_open", "timestamp"}:
        raise ValueError(f"unsupported signal_boundary={signal_boundary}")

    boundary_predicate = {
        "close": "a.notice_date <= p.signal_date AND c.forecast_date <= p.signal_date",
        "next_open": "a.notice_date < p.signal_date AND c.forecast_date < p.signal_date",
        "timestamp": "a.notice_ts < p.signal_ts AND c.forecast_snapshot_ts < p.signal_ts",
    }[signal_boundary]

    sql = f"""
        SELECT
            p.stock_code,
            p.signal_date,
            p.signal_ts,
            a.report_period,
            a.notice_date,
            a.notice_ts,
            c.forecast_date,
            c.forecast_snapshot_ts,
            c.forecast_inst_count,
            CASE
                WHEN c.forecast_inst_count >= ?
                 AND c.expected_eps_std > 0
                THEN (a.actual_eps_pit - c.expected_eps_mean) / c.expected_eps_std
                ELSE NULL
            END AS sue_raw
        FROM sue_signal_grid p
        JOIN pit_actual_eps_daily a
          ON p.stock_code = a.stock_code
         AND p.signal_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
        JOIN sue_consensus_daily c
          ON p.stock_code = c.stock_code
         AND p.signal_date = c.signal_date
         AND a.report_period = c.report_period
        WHERE {boundary_predicate}
          AND (
              a.latest_yj_notice_date IS NULL
              OR DATE_DIFF('day', a.latest_yj_notice_date, p.signal_date) <= ?
          )
    """
    return conn.execute(sql, [min_inst_count, start_date, end_date, max_stale_days]).fetchdf()
```

### C.4 Step 4 详细说明：wire 进 feature_join_v4 或 v5

| 接入点 | 处理 |
|---|---|
| `backend/services/labels/feature_join_v4.py` | 在 `_FEATURE_JOIN_SQL_V4` 中 LEFT JOIN SUE daily feature CTE |
| `backend/scripts/build_p0a_feature_panel_v4.py` | build 前确保 `sue_pit.py` 输出已 materialize 或 CTE 可读 |
| v5 条件 | 若 v4 已冻结，则创建 v5 并登记 `schema_versions.py` owner |
| 输出列 | `sue`、`sue_rolling_4q`、`eps_surprise_pct`、`forecast_upward_revision_30d`、`yjyg_announcement_drift_5d/10d/20d`、`profit_forecast_consensus_std_30d` |

```sql
LEFT JOIN fact_sue_pit_daily sue
  ON base.stock_code = sue.stock_code
 AND base.signal_date = sue.signal_date
```

```sql
-- feature_join PIT audit：SUE daily 表内保留源边界列供审计。
SELECT
    COUNT(*) AS pit_violation_rows
FROM fact_sue_pit_daily
WHERE notice_date > signal_date
   OR forecast_date > signal_date
   OR (signal_ts IS NOT NULL AND forecast_snapshot_ts >= signal_ts);
```

### C.5 Step 5 详细说明：unit test + PIT audit + ablation

| 测试 | 断言 |
|---|---|
| future yjyg injection | 注入 `notice_date = signal_date + 1` 后，`sue`、`eps_surprise_pct` 不变 |
| future forecast injection | 注入未来 EPS 上修后，`expected_eps_mean` 和 `forecast_upward_revision_30d` 不变 |
| legal historical injection | 注入 `forecast_date = signal_date - 1` 后，对应因子按公式变化 |
| stale fallback | `date_diff > N` 时 event bounded 因子为 `NULL` |
| priority | yjyg/yjkb/formal 同时存在时，source priority 为 `1` |

```python
def test_future_forecast_does_not_change_sue(conn):
    before = compute_sue(conn, start_date="2026-05-10", end_date="2026-05-10")
    conn.execute("""
        INSERT INTO fact_profit_forecast_period (
            stock_code, stock_name, institution_id, institution_name,
            report_period, forecast_year, forecast_date, forecast_snapshot_ts,
            eps_forecast, source_symbol, row_hash
        )
        VALUES (
            '600000', '浦发银行', 'FUTURE_INST', 'Future Inst',
            DATE '2026-06-30', 2026, DATE '2026-05-11',
            TIMESTAMP '2026-05-11 09:00:00',
            999.0, '600000', 'future-row'
        )
    """)
    after = compute_sue(conn, start_date="2026-05-10", end_date="2026-05-10")
    assert before.sort_index(axis=1).equals(after.sort_index(axis=1))
```

```bash
PYTHONPATH=backend pytest \
  backend/tests/features/test_sue_pit.py \
  backend/tests/labels/test_feature_join_v4.py \
  -q
```

### C.6 DuckDB 并行读取约束

| 场景 | 命令/代码 |
|---|---|
| Python 只读并行审计 | `duckdb.connect("data/market.duckdb", read_only=True)` |
| CLI 只读无持久化 | `duckdb --readonly --no-persist data/market.duckdb` |
| 写入 backfill | 单 writer，禁止并行 writer 争锁 |

```python
import duckdb

def open_readonly_conn(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)
```

```bash
duckdb --readonly --no-persist data/market.duckdb \
  "SELECT COUNT(*) FROM fact_profit_forecast_period"
```

## grep verification 路径

```bash
rg -n "fact_yjyg_period|fact_yjkb_period|fact_profit_forecast_period" \
  backend/scripts backend/services backend/tests
```

```bash
rg -n "PITViolation|enforce_pit|notice_date <= signal_date|forecast_snapshot_ts < signal_ts|forecast_date <= signal_date" \
  backend/services/features/sue_pit.py backend/tests/features/test_sue_pit.py
```

```bash
rg -n "sue|sue_rolling_4q|eps_surprise_pct|forecast_upward_revision_30d|yjyg_announcement_drift|profit_forecast_consensus_std_30d" \
  backend/services/features/sue_pit.py backend/services/labels/feature_join_v4.py backend/scripts/build_p0a_feature_panel_v4.py
```

```bash
rg -n "read_only=True|--no-persist|duckdb.connect\\(.*read_only=True" \
  backend/scripts backend/services backend/tests
```

```bash
rg -n "current_label_fallback|MAX\\(updated_at\\)|MAX\\(.*notice_date\\)|updated_at\\s*=\\s*CURRENT|latest_snapshot" \
  backend/scripts backend/services backend/tests
```

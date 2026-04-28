"""fact_holder_event 派生表构建.

输入: fact_top10_holder_period (canonical 事实表).
输出: fact_holder_event (派生事实, 全市场股东状态变化流).

按 CLAUDE.md 「派生层做部分覆盖时清空该层及下游, 从上游重算」原则,
本模块的 ``rebuild_holder_events`` 是 idempotent 的全量重建:
  1. DELETE FROM fact_holder_event
  2. 用 lag() over (partition by stock_code, holder_name_norm, share_class)
     对 fact_top10_holder_period 派生 new_entry / increase / decrease /
     unchanged 四种事件
  3. 直接从 is_exit_row=TRUE 的行提 exit 事件
  4. INSERT 全部进 fact_holder_event

Tolerance: 持股变化绝对值 ≤ max(prev * 0.0001, 100) 算 unchanged
(覆盖 4 位小数显示精度的舍入误差).

调用入口:
  - 命令行: python backend/scripts/rebuild_holder_events.py
  - 模块: from services.holders_event import rebuild_holder_events
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("cm-api")


# 持股变化容忍区间: 4 位小数显示精度对 6.81 亿持仓约损失 ~3000 股,
# 万分之一 + 100 股 baseline 兜底小持仓.
TOLERANCE_RATIO = 0.0001
TOLERANCE_FLOOR_SHARES = 100


# 重建主 SQL. 用 4 步 CTE:
# 1. primary_rows: 排除 exit 行和 secondary class (避免 A/H 双 leg 重算)
# 2. with_lag: 在 (stock, holder_norm, share_class) 内按 report_date 排序, 拿前期值
# 3. classified: 用 CASE 决出 new_entry/increase/decrease/unchanged
# 4. exits: 单独从 is_exit_row=TRUE 行抽 exit 事件
# 最后 INSERT UNION ALL.
_REBUILD_SQL = """
WITH primary_rows AS (
    SELECT
        stock_code, stock_name,
        holder_name, holder_name_norm,
        -- share_class 在 fact_top10_holder_period 可空 (单 A 股不写 'A' 时也是 NULL).
        -- fact_holder_event PK 要求非空 → 用 '_' 占位.
        COALESCE(share_class, '_') AS share_class,
        report_date,
        shares_approx,
        hold_ratio_float, hold_ratio_total,
        holder_type,
        holder_set,
        source, source_tier, raw_hash
    FROM fact_top10_holder_period
    WHERE NOT is_exit_row
      AND NOT is_secondary_class
),
with_lag AS (
    SELECT
        *,
        LAG(report_date) OVER w AS prev_report_date,
        LAG(shares_approx) OVER w AS prev_shares,
        LAG(hold_ratio_float) OVER w AS prev_ratio_float,
        LAG(hold_ratio_total) OVER w AS prev_ratio_total
    FROM primary_rows
    WINDOW w AS (
        PARTITION BY stock_code, holder_name_norm, COALESCE(share_class,'_'), holder_set
        ORDER BY report_date
    )
),
classified AS (
    SELECT
        stock_code, stock_name,
        holder_name, holder_name_norm, share_class,
        report_date, prev_report_date,
        prev_shares AS shares_before,
        shares_approx AS shares_after,
        CAST(shares_approx AS BIGINT) - CAST(COALESCE(prev_shares, 0) AS BIGINT) AS shares_delta,
        prev_ratio_float AS ratio_float_before,
        hold_ratio_float AS ratio_float_after,
        prev_ratio_total AS ratio_total_before,
        hold_ratio_total AS ratio_total_after,
        holder_type, holder_set,
        source, source_tier, raw_hash,
        CASE
            WHEN prev_shares IS NULL THEN 'new_entry'
            WHEN ABS(shares_approx - prev_shares) <= GREATEST(prev_shares * ?, ?)
                THEN 'unchanged'
            WHEN shares_approx > prev_shares THEN 'increase'
            WHEN shares_approx < prev_shares THEN 'decrease'
            ELSE 'unchanged'
        END AS event_type
    FROM with_lag
),
exits AS (
    -- 退出行: shares_approx 是退出前最后一期的持股 (TDX F10 退出表给的就是退出值)
    SELECT
        stock_code, stock_name,
        holder_name, holder_name_norm,
        COALESCE(share_class, '_') AS share_class,
        report_date,
        NULL AS prev_report_date,
        CAST(shares_approx AS BIGINT) AS shares_before,
        CAST(NULL AS BIGINT) AS shares_after,
        CAST(-COALESCE(shares_approx, 0) AS BIGINT) AS shares_delta,
        hold_ratio_float AS ratio_float_before,
        CAST(NULL AS DOUBLE) AS ratio_float_after,
        hold_ratio_total AS ratio_total_before,
        CAST(NULL AS DOUBLE) AS ratio_total_after,
        holder_type, holder_set,
        source, source_tier, raw_hash,
        'exit' AS event_type
    FROM fact_top10_holder_period
    WHERE is_exit_row = TRUE
      AND NOT is_secondary_class
)
INSERT INTO fact_holder_event(
    stock_code, stock_name, holder_name, holder_name_norm, share_class,
    report_date, prev_report_date, event_type,
    shares_before, shares_after, shares_delta,
    ratio_float_before, ratio_float_after,
    ratio_total_before, ratio_total_after,
    holder_type, holder_set, source, source_tier, raw_hash, created_at
)
SELECT
    stock_code, stock_name, holder_name, holder_name_norm, share_class,
    report_date, prev_report_date, event_type,
    shares_before, shares_after, shares_delta,
    ratio_float_before, ratio_float_after,
    ratio_total_before, ratio_total_after,
    holder_type, holder_set, source, source_tier, raw_hash,
    ?  -- created_at
FROM classified
UNION ALL
SELECT
    stock_code, stock_name, holder_name, holder_name_norm, share_class,
    report_date, prev_report_date, event_type,
    shares_before, shares_after, shares_delta,
    ratio_float_before, ratio_float_after,
    ratio_total_before, ratio_total_after,
    holder_type, holder_set, source, source_tier, raw_hash,
    ?  -- created_at
FROM exits
"""


def rebuild_holder_events(conn, *, holder_set: Optional[str] = None) -> dict[str, int]:
    """全量重建 fact_holder_event.

    Args:
        conn: DuckDB 连接.
        holder_set: 可选, 'free' / 'all'. None 表示重建两个 set.

    Returns:
        dict: 各 event_type 的行数 + 总行数.
    """

    logger.info("[holders_event] 开始重建 fact_holder_event ...")
    now_iso = datetime.utcnow().isoformat(timespec="seconds")

    if holder_set is None:
        # 全量清空, 全量重建
        conn.execute("DELETE FROM fact_holder_event")
    else:
        conn.execute(
            "DELETE FROM fact_holder_event WHERE holder_set = ?", (holder_set,)
        )

    params = (TOLERANCE_RATIO, TOLERANCE_FLOOR_SHARES, now_iso, now_iso)
    if holder_set is not None:
        # 加 WHERE holder_set = ?
        sql = _REBUILD_SQL.replace(
            "FROM primary_rows",
            f"FROM primary_rows WHERE holder_set = '{holder_set}'",
        ).replace(
            "FROM fact_top10_holder_period\n    WHERE is_exit_row = TRUE",
            f"FROM fact_top10_holder_period\n    WHERE is_exit_row = TRUE\n      AND holder_set = '{holder_set}'",
        )
    else:
        sql = _REBUILD_SQL
    conn.execute(sql, params)
    conn.commit()

    # 统计
    rows = conn.execute(
        "SELECT event_type, holder_set, COUNT(*) AS n "
        "FROM fact_holder_event GROUP BY event_type, holder_set"
    ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        key = f"{r['event_type']}:{r['holder_set']}"
        out[key] = r["n"]
    out["total"] = sum(out.values())
    logger.info("[holders_event] 重建完成: %s", out)
    return out

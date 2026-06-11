#!/usr/bin/env python3
"""Nightly data governance audit for K-line labels and downstream marts."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "audit" / "nightly_data_audit_latest.json"

LOT_SIZE_SHARES = 100.0
# from yaml: configs/data_governance.yaml ingestion_lint.reject.invalid_vwap_close_ratio (governance v1)
VWAP_CLOSE_MIN = 0.5
VWAP_CLOSE_MAX = 1.5
# from yaml: configs/data_governance.yaml periodic_audit.checks.single_source_proportion_drift (governance v1)
TIER1_RATIO_MIN = 0.999
# from yaml: configs/data_governance.yaml periodic_audit.checks.fwd_cost_after_outlier_count (governance v1)
# 2026-06-11 语义修正: 1.0 (100%) 是 WARN 级观察阈值, 不是数据错误判据 —
# 创业板 20cm 连板复利可真实超过它 (实证: 300085 2024-09-13 起 8 个精确 20.0% 连板,
# 10 日 +453%, 逐日验证无复权断层 = 924 行情真实历史)。
FWD_ABS_BLOCK_THRESHOLD = 1.0
# block 判据 = 超物理复利极限 (全市场最宽 30cm 北交所, 1.3^n - 1):
# 超过它的 forward 收益在涨跌停制度下不可能发生 = 必然复权断层/坏 K 线。
# rule-compliance: ok evidence=limit-board-compound-bound-1.3^n, 300085 case verified 2026-06-11
FWD_PHYSICAL_BOUNDS = {
    "fwd_cost_after_5d": 1.3 ** 5 - 1,    # 2.71
    "fwd_cost_after_10d": 1.3 ** 10 - 1,  # 12.79
    "fwd_cost_after_20d": 1.3 ** 20 - 1,  # 189.0
}
# from yaml: configs/data_governance.yaml deprecation.allowed_fallback_codes (CSI 300 index 000300, tier-2 benchmark allowlist)
ALLOWED_FALLBACK_CODES = {"000300"}
LABEL_COLUMNS = ("fwd_cost_after_5d", "fwd_cost_after_10d", "fwd_cost_after_20d")


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _scalar(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value


def _rows_as_dicts(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [desc[0] for desc in con.description]
    return [
        {column: _scalar(value) for column, value in zip(columns, row)}
        for row in con.fetchall()
    ]


def _connect(smartmoney_db: Path, market_db: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(smartmoney_db), read_only=True)
    con.execute(f"ATTACH '{_sql_path(market_db)}' AS market (READ_ONLY)")
    return con


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = ?
        LIMIT 1
        """,
        [table_name],
    ).fetchone()
    return row is not None


def _max_canonical_date(con: duckdb.DuckDBPyConnection) -> date:
    row = con.execute("SELECT MAX(CAST(date AS DATE)) FROM market.v_price_kline_qfq").fetchone()
    if not row or row[0] is None:
        raise RuntimeError("market.v_price_kline_qfq has no rows")
    return row[0]


def check_vwap_close_ratio(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    summary = con.execute(
        """
        WITH ratios AS (
            SELECT
                code,
                date,
                source_name,
                source_tier,
                is_fallback,
                -- 2026-06-11 口径修正: amount/volume 是原始价口径, close 是 qfq 价;
                -- 乘 factor 对齐 (raw_vwap * factor = qfq_vwap), 否则近期除权股
                -- (factor<0.67) 的固有偏离 1/factor 会假突破 1.5 阈值 (实测 002245
                -- 1.5082 -> 1.0153)。
                amount / NULLIF(volume * ?, 0)
                    * COALESCE(NULLIF(factor, 0), 1.0)
                    / NULLIF(close, 0) AS vwap_close_ratio
            FROM market.v_price_kline_qfq
            WHERE freq = 'daily'
              AND adjust = 'qfq'
              AND CAST(date AS DATE) >= ?
              -- 停牌行 (含 denormal 清洗后的 volume=0) 无 vwap 语义, 不进 ratio 检查;
              -- 停牌可执行性由 tradability.is_suspended 把守
              AND volume * ? >= 1
        )
        SELECT
            COUNT(*) AS checked_rows,
            SUM(CASE WHEN vwap_close_ratio NOT BETWEEN ? AND ? OR vwap_close_ratio IS NULL THEN 1 ELSE 0 END) AS anomaly_rows,
            SUM(CASE WHEN source_tier = 1 AND (vwap_close_ratio NOT BETWEEN ? AND ? OR vwap_close_ratio IS NULL) THEN 1 ELSE 0 END) AS tier1_anomaly_rows,
            COUNT(DISTINCT CASE WHEN vwap_close_ratio NOT BETWEEN ? AND ? OR vwap_close_ratio IS NULL THEN code END) AS anomaly_codes,
            MIN(vwap_close_ratio) AS min_ratio,
            MAX(vwap_close_ratio) AS max_ratio
        FROM ratios
        """,
        [
            LOT_SIZE_SHARES,
            cutoff,
            LOT_SIZE_SHARES,  # 停牌行过滤 volume*lot >= 1
            VWAP_CLOSE_MIN,
            VWAP_CLOSE_MAX,
            VWAP_CLOSE_MIN,
            VWAP_CLOSE_MAX,
            VWAP_CLOSE_MIN,
            VWAP_CLOSE_MAX,
        ],
    ).fetchone()
    columns = [desc[0] for desc in con.description]
    result = {column: _scalar(value) for column, value in zip(columns, summary)}

    con.execute(
        """
        WITH ratios AS (
            SELECT
                code,
                date,
                source_name,
                source_tier,
                is_fallback,
                -- 2026-06-11 口径修正: amount/volume 是原始价口径, close 是 qfq 价;
                -- 乘 factor 对齐 (raw_vwap * factor = qfq_vwap), 否则近期除权股
                -- (factor<0.67) 的固有偏离 1/factor 会假突破 1.5 阈值 (实测 002245
                -- 1.5082 -> 1.0153)。
                amount / NULLIF(volume * ?, 0)
                    * COALESCE(NULLIF(factor, 0), 1.0)
                    / NULLIF(close, 0) AS vwap_close_ratio
            FROM market.v_price_kline_qfq
            WHERE freq = 'daily'
              AND adjust = 'qfq'
              AND CAST(date AS DATE) >= ?
              -- 停牌行 (含 denormal 清洗后的 volume=0) 无 vwap 语义, 不进 ratio 检查;
              -- 停牌可执行性由 tradability.is_suspended 把守
              AND volume * ? >= 1
        )
        SELECT
            source_name,
            source_tier,
            is_fallback,
            COUNT(*) AS anomaly_rows,
            COUNT(DISTINCT code) AS anomaly_codes,
            MIN(date) AS min_date,
            MAX(date) AS max_date,
            MIN(vwap_close_ratio) AS min_ratio,
            MAX(vwap_close_ratio) AS max_ratio
        FROM ratios
        WHERE vwap_close_ratio NOT BETWEEN ? AND ?
           OR vwap_close_ratio IS NULL
        GROUP BY source_name, source_tier, is_fallback
        ORDER BY anomaly_rows DESC
        """,
        [LOT_SIZE_SHARES, cutoff, LOT_SIZE_SHARES, VWAP_CLOSE_MIN, VWAP_CLOSE_MAX],
    )
    breakdown = _rows_as_dicts(con)

    severity = "ok"
    if int(result["anomaly_rows"] or 0) > 0:
        severity = "critical"
    return {
        "name": "vwap_close_ratio_anomaly_count",
        "severity": severity,
        "threshold": {"min": VWAP_CLOSE_MIN, "max": VWAP_CLOSE_MAX, "block_count_gt": 0},
        "summary": result,
        "breakdown": breakdown,
    }


def check_source_proportion(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    con.execute(
        """
        SELECT
            date,
            COUNT(*) AS total_rows,
            SUM(CASE WHEN source_tier = 1 AND is_fallback = FALSE THEN 1 ELSE 0 END) AS tier1_rows,
            SUM(CASE WHEN is_fallback THEN 1 ELSE 0 END) AS fallback_rows,
            SUM(CASE WHEN is_fallback AND code NOT IN ('000300') THEN 1 ELSE 0 END) AS non_allowlist_fallback_rows,
            ROUND(SUM(CASE WHEN source_tier = 1 AND is_fallback = FALSE THEN 1 ELSE 0 END)::DOUBLE / COUNT(*), 6) AS tier1_ratio,
            ROUND(SUM(CASE WHEN is_fallback THEN 1 ELSE 0 END)::DOUBLE / COUNT(*), 6) AS fallback_ratio
        FROM market.v_price_kline_qfq
        WHERE freq = 'daily'
          AND adjust = 'qfq'
          AND CAST(date AS DATE) >= ?
        GROUP BY date
        HAVING tier1_ratio < ?
            OR non_allowlist_fallback_rows > 0
        ORDER BY date DESC
        """,
        [cutoff, TIER1_RATIO_MIN],
    )
    breaches = _rows_as_dicts(con)
    summary = {
        "breach_days": len(breaches),
        "max_non_allowlist_fallback_rows": max(
            (int(row["non_allowlist_fallback_rows"] or 0) for row in breaches),
            default=0,
        ),
        "min_tier1_ratio": min(
            (float(row["tier1_ratio"]) for row in breaches if row.get("tier1_ratio") is not None),
            default=1.0,
        ),
    }
    severity = "critical" if breaches else "ok"
    return {
        "name": "single_source_proportion_drift",
        "severity": severity,
        "threshold": {
            "tier1_ratio_min": TIER1_RATIO_MIN,
            "allowed_fallback_codes": sorted(ALLOWED_FALLBACK_CODES),
            "block_non_allowlist_fallback_rows_gt": 0,
        },
        "summary": summary,
        "breaches": breaches,
    }


def check_fwd_cost_after(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    tables = [
        table
        for table in ("mart_p0a_label_panel", "mart_p0b_oos_predictions")
        if _table_exists(con, table)
    ]
    rows: list[dict[str, Any]] = []
    for table in tables:
        for column in LABEL_COLUMNS:
            bound = FWD_PHYSICAL_BOUNDS[column]
            sql = f"""
            SELECT
                '{table}' AS table_name,
                '{column}' AS column_name,
                SUM(CASE WHEN {column} IS NOT NULL AND isfinite({column}) THEN 1 ELSE 0 END) AS finite_rows,
                SUM(CASE WHEN {column} IS NOT NULL AND isnan({column}) THEN 1 ELSE 0 END) AS nan_rows,
                MIN(CASE WHEN isfinite({column}) THEN {column} END) AS min_value,
                MAX(CASE WHEN isfinite({column}) THEN {column} END) AS max_value,
                SUM(CASE WHEN isfinite({column}) AND ABS({column}) > ? THEN 1 ELSE 0 END) AS abs_gt_threshold_rows,
                SUM(CASE WHEN isfinite({column}) AND ABS({column}) > ? THEN 1 ELSE 0 END) AS physical_bound_rows
            FROM {table}
            """
            con.execute(sql, [FWD_ABS_BLOCK_THRESHOLD, bound])
            rows.extend(_rows_as_dicts(con))

    total_outliers = sum(int(row["abs_gt_threshold_rows"] or 0) for row in rows)
    total_physical = sum(int(row["physical_bound_rows"] or 0) for row in rows)
    total_nan = sum(int(row["nan_rows"] or 0) for row in rows)
    # critical = 物理不可能 (复权断层/坏K线) 或 NaN; 仅超 1.0 观察阈值 = warn (真实牛市连板可达)
    if total_physical > 0 or total_nan > 0:
        severity = "critical"
    elif total_outliers > 0:
        severity = "warn"
    else:
        severity = "ok"
    return {
        "name": "fwd_cost_after_outlier_count",
        "severity": severity,
        "threshold": {
            "warn_abs_value_gt": FWD_ABS_BLOCK_THRESHOLD,
            "block_abs_value_gt": {k: round(v, 2) for k, v in FWD_PHYSICAL_BOUNDS.items()},
            "block_count_gt": 0,
            "block_nan_count_gt": 0,
        },
        "summary": {
            "outlier_rows": total_outliers,
            "physical_bound_rows": total_physical,
            "nan_rows": total_nan,
        },
        "tables": rows,
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    con = _connect(args.smartmoney_db, args.market_db)
    try:
        max_date = _max_canonical_date(con)
        effective_lookback = 900 if args.training_window_audit else args.lookback_days
        cutoff = max_date - timedelta(days=max(0, effective_lookback))
        checks = [
            check_vwap_close_ratio(con, cutoff),
            check_source_proportion(con, cutoff),
            check_fwd_cost_after(con),
        ]
    finally:
        con.close()

    severity_order = {"ok": 0, "warn": 1, "critical": 2}
    max_severity = max((check["severity"] for check in checks), key=lambda value: severity_order[value])
    generated_at = datetime.now(UTC)
    return {
        "audit_id": f"nightly_data_audit_{generated_at.strftime('%Y%m%d_%H%M%S')}",
        "policy_id": "kline_governance_v1_tdxhub_primary",
        "generated_at_utc": generated_at.isoformat(timespec="seconds"),
        "smartmoney_db": str(args.smartmoney_db),
        "market_db": str(args.market_db),
        "lookback_days": effective_lookback,
        "training_window_audit": args.training_window_audit,
        "canonical_max_date": max_date.isoformat(),
        "cutoff_date": cutoff.isoformat(),
        "severity": max_severity,
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run nightly K-line data governance audit")
    parser.add_argument("--smartmoney-db", type=Path, default=DEFAULT_SMARTMONEY_DB)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    # Codex round 17 Q8.1 FIX: governance v1 训练 window 2024-01-01 起, 30 天不够覆盖
    # default 改 900 (~2.5 年, 覆盖 2024-01 ~ now), 加 --training-window-audit flag 触发 full window
    parser.add_argument("--lookback-days", type=int, default=30,
                        help="nightly default 30 (recent drift); 加 --training-window-audit 覆盖全训练 window")
    parser.add_argument("--training-window-audit", action="store_true",
                        help="Codex Q8.1: 跑全训练 window (2024-01-01 起), 等同 --lookback-days 900")
    parser.add_argument("--write-json", type=Path, default=None)
    parser.add_argument("--write-default-json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    output_path = DEFAULT_OUTPUT if args.write_default_json else args.write_json
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    if report["severity"] == "critical":
        return 2
    # warn 不走非零退出码: launchd wrapper 把 rc!=0 一律当 FAIL 弹通知,
    # 每天为观察级 warn 弹"失败"= 告警疲劳, 会淹没真 critical (狼来了效应)。
    # warn 详情在 JSON 报告 severity 字段, chunkyctl doctor 负责呈现。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

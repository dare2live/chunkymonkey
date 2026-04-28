"""数据健康快照 — 每天 09:30 跑一次, 写 mart_data_health.

为每张登记在 dim_data_asset 里的表实测:
  - row_count: 当前行数
  - last_data_date: MAX(<date_column>) — 自动找日期列
  - null_rate_pct: 关键字段 NULL 比例 (粗采样)
  - source_tier_dist: 若表有 source_tier 列, 各 tier 行数分布
  - freshness_hours: 当前数据距 now() 时长
  - severity: green / yellow / red

Severity 规则:
  - 表行数 = 0 且 expected_freshness != 'static': red (writer 没跑 / 上游断)
  - last_data_date 距 now > sla_hours * 2: red
  - last_data_date 距 now > sla_hours: yellow
  - 否则: green

退出码: 任一表 red → exit 1 (CI 守门)

可重复跑: 每次 INSERT (table_name, snapshot_at) 一行, 保留历史.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("data-health")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
from services.db import get_conn  # noqa: E402


# 日期列优先级 (从前到后查, 第一个存在的用作 last_data_date)
DATE_COLUMN_CANDIDATES = [
    "report_date", "trade_date", "notice_date", "change_date",
    "snapshot_date", "as_of_date", "effective_date",
    "fetched_at", "page_update_date", "created_at", "updated_at",
    "last_data_at", "last_writer_at", "snapshot_at",
    "date", "ts",
]

# 看 source_tier 列是否存在的统一名
SOURCE_TIER_COL = "source_tier"


def get_table_columns(con, table: str) -> list[str]:
    try:
        rows = con.execute(f'DESCRIBE "{table}"').fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def find_date_column(columns: list[str]) -> Optional[str]:
    cols_lower = [c.lower() for c in columns]
    for c in DATE_COLUMN_CANDIDATES:
        if c in cols_lower:
            # 找回原始大小写
            return columns[cols_lower.index(c)]
    return None


def parse_date_value(raw) -> Optional[datetime]:
    """容忍 'YYYY-MM-DD' / 'YYYYMMDD' / datetime / int."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    fmts = ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ")
    for fmt in fmts:
        try:
            return datetime.strptime(s[:len(fmt) + 4], fmt)
        except (ValueError, TypeError):
            continue
    # 再容忍 ISO with timezone
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def compute_health_for_table(con, asset: dict, now: datetime) -> dict:
    """对单表计算健康指标. asset 是 dim_data_asset 行 dict."""

    table = asset["table_name"]
    layer = asset["layer"]
    expected_freshness = asset.get("expected_freshness") or "on-demand"
    sla_hours = asset.get("sla_hours") or 48

    # 安全的表名引用
    quoted = f'"{table}"'

    # row_count
    try:
        row_count = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    except Exception as e:
        return {
            "table_name": table, "row_count": None,
            "last_data_date": None, "last_writer_at": None,
            "null_rate_pct": None, "source_tier_dist": None,
            "freshness_hours": None, "freshness_ok": False,
            "severity": "red",
            "issue_summary": f"COUNT(*) failed: {type(e).__name__}: {e}",
        }

    columns = get_table_columns(con, table)

    # last_data_date
    date_col = find_date_column(columns)
    last_data_date = None
    last_data_dt = None
    if date_col and row_count > 0:
        try:
            row = con.execute(f"SELECT MAX({date_col}) FROM {quoted}").fetchone()
            if row and row[0] is not None:
                last_data_date = str(row[0])
                last_data_dt = parse_date_value(row[0])
        except Exception:
            pass

    # source_tier 分布
    source_tier_dist = None
    if SOURCE_TIER_COL in columns and row_count > 0:
        try:
            rows = con.execute(
                f"SELECT {SOURCE_TIER_COL}, COUNT(*) FROM {quoted} "
                f"GROUP BY 1 ORDER BY 1"
            ).fetchall()
            source_tier_dist = json.dumps(
                {str(r[0]) if r[0] is not None else "null": r[1] for r in rows}
            )
        except Exception:
            pass

    # freshness
    freshness_hours = None
    freshness_ok = None
    if last_data_dt is not None:
        # 如果 last_data_dt 是 naive, 当作 UTC 比较
        if last_data_dt.tzinfo is None:
            last_data_dt = last_data_dt.replace(tzinfo=timezone.utc)
        delta = now.replace(tzinfo=timezone.utc) - last_data_dt
        freshness_hours = round(delta.total_seconds() / 3600, 2)
        freshness_ok = freshness_hours <= sla_hours

    # severity 判定
    issues = []
    severity = "green"
    if row_count == 0 and expected_freshness != "static":
        severity = "red"
        issues.append("0 rows but expected to be populated")
    elif freshness_hours is not None:
        if freshness_hours > sla_hours * 2:
            severity = "red"
            issues.append(f"data is {freshness_hours:.1f}h old (SLA {sla_hours}h)")
        elif freshness_hours > sla_hours:
            severity = "yellow"
            issues.append(f"data is {freshness_hours:.1f}h old (SLA {sla_hours}h)")
    elif row_count > 0 and date_col is None:
        # 有数据但找不到日期列 — 无法判 freshness, 留 yellow 提示
        severity = "yellow"
        issues.append("no date column found for freshness check")

    # writer 缺失 + 有数据 = orphan_no_writer (致命)
    if asset.get("writer_module") is None and row_count > 0:
        severity = "red"
        issues.append("orphan_no_writer (data but no active writer)")

    # writer 存在但 0 行 = stale_empty
    if asset.get("writer_module") is not None and row_count == 0 and expected_freshness != "static":
        severity = "red"
        issues.append("stale_empty (writer registered but never produced rows)")

    return {
        "table_name": table,
        "row_count": row_count,
        "last_data_date": last_data_date,
        "last_writer_at": None,  # TODO: 接 step_status 推
        "null_rate_pct": None,   # TODO: 关键字段采样
        "source_tier_dist": source_tier_dist,
        "freshness_hours": freshness_hours,
        "freshness_ok": freshness_ok,
        "severity": severity,
        "issue_summary": "; ".join(issues) if issues else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-history", type=int, default=30,
                        help="保留多少天历史快照 (其余删, 默认 30)")
    args = parser.parse_args()

    con = get_conn()
    now = datetime.utcnow()

    # 拉所有登记的资产
    rows = con.execute("""
        SELECT table_name, layer, purpose, writer_module, reader_modules,
               upstream_source, source_tier, expected_freshness, sla_hours,
               consumed_by_views
        FROM dim_data_asset
        ORDER BY layer, table_name
    """).fetchall()
    log.info("scanning %d assets", len(rows))

    snapshots: list[dict] = []
    severity_count = {"green": 0, "yellow": 0, "red": 0}
    for asset_row in rows:
        # row_factory 默认返 Row
        asset = dict(asset_row)
        snap = compute_health_for_table(con, asset, now)
        snap["snapshot_at"] = now.isoformat(timespec="seconds")
        snapshots.append(snap)
        severity_count[snap["severity"]] = severity_count.get(snap["severity"], 0) + 1

    if args.dry_run:
        log.info("[dry] severity counts: %s", severity_count)
        for s in snapshots:
            if s["severity"] != "green":
                log.info("  %s %s: %s", s["severity"], s["table_name"], s["issue_summary"])
        return 0

    # 写库
    for s in snapshots:
        con.execute("""
            INSERT INTO mart_data_health (
                table_name, snapshot_at, row_count, last_data_date, last_writer_at,
                null_rate_pct, source_tier_dist, freshness_hours, freshness_ok,
                severity, issue_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (table_name, snapshot_at) DO UPDATE SET
                row_count = EXCLUDED.row_count,
                last_data_date = EXCLUDED.last_data_date,
                source_tier_dist = EXCLUDED.source_tier_dist,
                freshness_hours = EXCLUDED.freshness_hours,
                freshness_ok = EXCLUDED.freshness_ok,
                severity = EXCLUDED.severity,
                issue_summary = EXCLUDED.issue_summary
        """, (
            s["table_name"], s["snapshot_at"], s["row_count"], s["last_data_date"],
            s["last_writer_at"], s["null_rate_pct"], s["source_tier_dist"],
            s["freshness_hours"], s["freshness_ok"], s["severity"], s["issue_summary"],
        ))

    # 修剪历史
    cutoff = (now - timedelta(days=args.keep_history)).isoformat()
    con.execute("DELETE FROM mart_data_health WHERE snapshot_at < ?", (cutoff,))
    con.commit()

    log.info("=== snapshot done ===")
    log.info("  total tables: %d", len(snapshots))
    log.info("  🟢 green:   %d", severity_count.get("green", 0))
    log.info("  🟡 yellow:  %d", severity_count.get("yellow", 0))
    log.info("  🔴 red:     %d", severity_count.get("red", 0))

    # 打印 red 列表
    red_list = [s for s in snapshots if s["severity"] == "red"]
    if red_list:
        log.info("\n=== red tables (%d) ===", len(red_list))
        for s in red_list[:30]:
            log.info("  🔴 %s — %s", s["table_name"], s["issue_summary"])
        if len(red_list) > 30:
            log.info("  ... +%d more", len(red_list) - 30)

    con.close()
    # CI 守门: 任一 red → exit 1
    return 1 if red_list else 0


if __name__ == "__main__":
    raise SystemExit(main())

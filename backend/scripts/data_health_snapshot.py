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
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("data-health")

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
from services.db import get_conn  # noqa: E402
from services.data_sources.clients_registry import get_table_metadata  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402


# 日期列优先级 (从前到后查, 第一个存在的用作 last_data_date)
DATA_DATE_COLUMN_CANDIDATES = [
    "report_date", "trade_date", "notice_date", "change_date", "calc_date",
    "snapshot_date", "as_of_date", "effective_date",
    "page_update_date", "last_data_at",
    "date", "ts",
]

WRITER_DATE_COLUMN_CANDIDATES = [
    "last_writer_at", "ingested_at", "fetched_at", "built_at",
    "parsed_at", "profiled_at", "recorded_at", "audited_at",
    "validated_at", "computed_at", "deleted_at", "started_at", "ended_at",
    "first_seen_at", "last_seen_at", "heartbeat_at", "released_at",
    "updated_at", "created_at", "snapshot_at",
]

# 看 source_tier 列是否存在的统一名
SOURCE_TIER_COL = "source_tier"
NON_EXPIRING_FRESHNESS = {"static", "on-demand", "derived"}
OPTIONAL_EMPTY_FRESHNESS = NON_EXPIRING_FRESHNESS
TRADING_CADENCE_FRESHNESS = {"t+0", "t+1", "event"}
PERIODIC_OR_EVENT_ASSET_TOKENS = {
    "periodic",
    "periodic_or_event",
    "periodic_report",
    "periodic_report_after_listing",
    "event",
    "event_driven",
    "sparse_event",
}
NON_EXPIRING_ASSET_TOKENS = {
    "on_demand",
    "workflow_dependent",
    "only_when_active_challenger_exists",
    "empty_allowed_without_active_challenger",
}
QUALITY_GATE_SEVERITY_CAPS = {
    "monitor_only": "yellow",
    "warning": "yellow",
}


def get_table_columns(con, table: str) -> list[str]:
    try:
        rows = con.execute(f'DESCRIBE "{table}"').fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _find_column(columns: list[str], candidates: list[str]) -> Optional[str]:
    cols_lower = [c.lower() for c in columns]
    for c in candidates:
        if c in cols_lower:
            # 找回原始大小写
            return columns[cols_lower.index(c)]
    return None


def find_date_column(columns: list[str]) -> Optional[str]:
    return _find_column(columns, DATA_DATE_COLUMN_CANDIDATES)


def find_writer_date_column(columns: list[str]) -> Optional[str]:
    return _find_column(columns, WRITER_DATE_COLUMN_CANDIDATES)


def ensure_asset_deprecation_columns(con) -> None:
    for ddl in (
        "ALTER TABLE dim_data_asset ADD COLUMN deprecation_status TEXT DEFAULT 'active'",
        "ALTER TABLE dim_data_asset ADD COLUMN replacement_table TEXT",
    ):
        try:
            con.execute(ddl)
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate" not in msg and "already exists" not in msg:
                raise


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
            "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ",
            "%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S")
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


def _normalize_date_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def latest_required_trade_date(con, now: datetime) -> Optional[str]:
    """Return the last completed market date from the local trading calendar.

    This keeps daily source health stable during weekends and exchange holidays.
    If the calendar is missing, callers fall back to wall-clock age.
    """

    anchor = now.strftime("%Y-%m-%d")
    try:
        row = con.execute(
            """
            SELECT MAX(trade_date) AS d
              FROM dim_trading_calendar
             WHERE is_trading = 1
               AND trade_date <= ?
            """,
            (anchor,),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        return row["d"]
    except Exception:
        return row[0]


def trading_lag_hours(con, last_dt: datetime, required_trade_date: Optional[str]) -> Optional[float]:
    if required_trade_date is None:
        return None
    last_key = _normalize_date_key(last_dt)
    if last_key >= required_trade_date:
        return 0.0
    try:
        row = con.execute(
            """
            SELECT COUNT(*) AS n
              FROM dim_trading_calendar
             WHERE is_trading = 1
               AND trade_date > ?
               AND trade_date <= ?
            """,
            (last_key, required_trade_date),
        ).fetchone()
    except Exception:
        return None
    try:
        lag_days = int(row["n"] or 0)
    except Exception:
        lag_days = int(row[0] or 0)
    return float(lag_days * 24)


def _max_column_datetime(con, table: str, column: Optional[str]) -> tuple[Optional[str], Optional[datetime]]:
    if not column:
        return None, None
    try:
        row = con.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()
    except Exception:
        return None, None
    if not row or row[0] is None:
        return None, None
    raw = row[0]
    return str(raw), parse_date_value(raw)


def _coerce_db_timestamp(raw) -> Optional[datetime]:
    """Normalize timestamp-like values before writing them into TIMESTAMP columns."""
    return parse_date_value(raw)


def _owner_hint_for_table(table: str, asset: dict) -> dict[str, Any]:
    meta = get_table_metadata(table)
    if meta is not None:
        client, write_spec = meta
        sync_step_id = client.sync_step_id
        prompt_parts = [
            f"owner={client.client_id}",
            f"writer={client.module}",
        ]
        if sync_step_id:
            prompt_parts.append(f"sync_step={sync_step_id}")
        prompt_parts.append(f"source={client.upstream_source}")
        if write_spec and write_spec.purpose:
            prompt_parts.append(f"purpose={write_spec.purpose}")
        return {
            "writer_client_id": client.client_id,
            "writer_module": client.module,
            "writer_sync_step_id": sync_step_id,
            "writer_upstream_source": client.upstream_source,
            "writer_client_description": client.description,
            "writer_prompt": " · ".join(prompt_parts),
        }

    prompt_parts = []
    writer_module = asset.get("writer_module")
    upstream_source = asset.get("upstream_source")
    source_tier = asset.get("source_tier")
    if writer_module:
        prompt_parts.append(f"writer={writer_module}")
    if upstream_source:
        prompt_parts.append(f"source={upstream_source}")
    if source_tier is not None:
        prompt_parts.append(f"tier={source_tier}")
    return {
        "writer_module": writer_module,
        "writer_upstream_source": upstream_source,
        "writer_prompt": " · ".join(prompt_parts) if prompt_parts else None,
    }


def _severity_rank(severity: str) -> int:
    return {"green": 0, "yellow": 1, "red": 2}.get(severity, 0)


def _max_severity(left: str, right: str) -> str:
    return left if _severity_rank(left) >= _severity_rank(right) else right


def _is_periodic_or_event_asset(asset: dict) -> bool:
    values = [
        asset.get("asset_cadence"),
        asset.get("asset_grain"),
        asset.get("coverage_policy"),
        asset.get("intended_use"),
    ]
    text = " ".join(str(value or "").lower() for value in values)
    return any(token in text for token in PERIODIC_OR_EVENT_ASSET_TOKENS)


def _is_non_expiring_asset(asset: dict) -> bool:
    values = [
        asset.get("asset_cadence"),
        asset.get("coverage_policy"),
        asset.get("null_policy"),
    ]
    text = " ".join(str(value or "").lower() for value in values)
    return any(token in text for token in NON_EXPIRING_ASSET_TOKENS)


def _cap_severity_by_quality_gate(asset: dict, severity: str) -> str:
    gate = str(asset.get("quality_gate_level") or "").strip().lower()
    cap = QUALITY_GATE_SEVERITY_CAPS.get(gate)
    if cap is None:
        return severity
    return severity if _severity_rank(severity) <= _severity_rank(cap) else cap


def compute_health_for_table(con, asset: dict, now: datetime) -> dict:
    """对单表计算健康指标. asset 是 dim_data_asset 行 dict."""

    table = asset["table_name"]
    owner_hint = _owner_hint_for_table(table, asset)
    layer = asset["layer"]
    expected_freshness = asset.get("expected_freshness") or "on-demand"
    sla_hours = asset.get("sla_hours") or 48
    non_expiring = expected_freshness in NON_EXPIRING_FRESHNESS or _is_non_expiring_asset(asset)
    issues = []
    severity = "green"

    if asset.get("deprecation_status") == "deprecated":
        row_count = None
        try:
            row_count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except Exception:
            pass
        replacement = asset.get("replacement_table")
        suffix = f"; replacement={replacement}" if replacement else ""
        return {
            "table_name": table, "row_count": row_count,
            "last_data_date": None, "last_writer_at": None,
            "null_rate_pct": None, "source_tier_dist": None,
            "freshness_hours": None, "freshness_ok": True,
            "severity": "green",
            "issue_summary": f"deprecated asset{suffix}",
            **owner_hint,
        }

    # 安全的表名引用
    quoted = f'"{table}"'

    # row_count
    try:
        row_count = con.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    except Exception as e:
        severity = _cap_severity_by_quality_gate(asset, "red")
        return {
            "table_name": table, "row_count": None,
            "last_data_date": None, "last_writer_at": None,
            "null_rate_pct": None, "source_tier_dist": None,
            "freshness_hours": None, "freshness_ok": False,
            "severity": severity,
            "issue_summary": f"COUNT(*) failed: {type(e).__name__}: {e}",
            **owner_hint,
        }

    columns = get_table_columns(con, table)

    # last_data_date: event/report/trading date. last_writer_at: ingest/build time.
    date_col = find_date_column(columns)
    writer_date_col = find_writer_date_column(columns)
    last_data_date, last_data_dt = _max_column_datetime(con, table, date_col) if row_count > 0 else (None, None)
    last_writer_at, last_writer_dt = _max_column_datetime(con, table, writer_date_col) if row_count > 0 else (None, None)

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

    # freshness. Raw/event source tables should be judged by writer recency
    # because "no new event rows" is not a stale-source signal by itself.
    freshness_hours = None
    freshness_ok = None
    freshness_dt = last_data_dt
    freshness_basis = "data"
    prefer_writer = (
        last_writer_dt is not None
        and (
            layer == "raw"
            or expected_freshness == "event"
            or last_data_dt is None
            or (
                expected_freshness in TRADING_CADENCE_FRESHNESS
                and _is_periodic_or_event_asset(asset)
            )
        )
    )
    if prefer_writer:
        freshness_dt = last_writer_dt
        freshness_basis = "writer"

    if row_count > 0 and writer_date_col and last_writer_at is not None and last_writer_dt is None:
        severity = _max_severity(severity, "yellow")
        issues.append(f"writer timestamp format unsupported: {writer_date_col}={last_writer_at}")

    if freshness_dt is not None and not non_expiring:
        # 如果 last_data_dt 是 naive, 当作 UTC 比较
        comparable_dt = freshness_dt
        if comparable_dt.tzinfo is None:
            comparable_dt = comparable_dt.replace(tzinfo=timezone.utc)
        trade_hours = None
        if expected_freshness in TRADING_CADENCE_FRESHNESS:
            trade_hours = trading_lag_hours(con, comparable_dt, latest_required_trade_date(con, now))
        if trade_hours is not None:
            freshness_hours = round(trade_hours, 2)
        else:
            delta = now.replace(tzinfo=timezone.utc) - comparable_dt
            freshness_hours = round(delta.total_seconds() / 3600, 2)
        freshness_ok = freshness_hours <= sla_hours

    # severity 判定
    if row_count == 0 and expected_freshness not in OPTIONAL_EMPTY_FRESHNESS and not non_expiring:
        severity = "red"
        issues.append("0 rows but expected to be populated")
    elif freshness_hours is not None:
        if freshness_hours > sla_hours * 2:
            severity = "red"
            issues.append(f"{freshness_basis} is {freshness_hours:.1f}h old (SLA {sla_hours}h)")
        elif freshness_hours > sla_hours:
            severity = "yellow"
            issues.append(f"{freshness_basis} is {freshness_hours:.1f}h old (SLA {sla_hours}h)")
    elif row_count > 0 and date_col is None and writer_date_col is None and not non_expiring:
        # 有数据但找不到日期列 — 无法判 freshness, 留 yellow 提示
        severity = "yellow"
        issues.append("no date column found for freshness check")

    # writer 缺失 + 有数据 = registry metadata issue. It is critical only for
    # active, freshness-governed external assets; static/on-demand/research
    # artifacts should not turn source health red.
    if asset.get("writer_module") is None and row_count > 0 and not non_expiring:
        severity = _max_severity(severity, "yellow")
        issues.append("orphan_no_writer (data but no active writer)")

    # writer 存在但 0 行 = stale_empty
    if (
        asset.get("writer_module") is not None
        and row_count == 0
        and expected_freshness not in OPTIONAL_EMPTY_FRESHNESS
        and not non_expiring
    ):
        severity = "red"
        issues.append("stale_empty (writer registered but never produced rows)")

    severity = _cap_severity_by_quality_gate(asset, severity)

    return {
        "table_name": table,
        "row_count": row_count,
        "last_data_date": last_data_date,
        "last_writer_at": last_writer_at,
        "null_rate_pct": None,   # TODO: 关键字段采样
        "source_tier_dist": source_tier_dist,
        "freshness_hours": freshness_hours,
        "freshness_ok": freshness_ok,
        "severity": severity,
        "issue_summary": "; ".join(issues) if issues else None,
        **owner_hint,
    }


def _snapshot_brief(snapshot: dict) -> dict[str, Any]:
    brief = {
        "table_name": snapshot.get("table_name"),
        "severity": snapshot.get("severity"),
        "quality_gate_level": snapshot.get("quality_gate_level"),
        "writer_client_id": snapshot.get("writer_client_id"),
        "writer_sync_step_id": snapshot.get("writer_sync_step_id"),
        "writer_upstream_source": snapshot.get("writer_upstream_source"),
        "writer_client_description": snapshot.get("writer_client_description"),
        "writer_prompt": snapshot.get("writer_prompt"),
        "issue_summary": snapshot.get("issue_summary"),
        "row_count": snapshot.get("row_count"),
        "last_data_date": snapshot.get("last_data_date"),
        "last_writer_at": snapshot.get("last_writer_at"),
        "freshness_hours": snapshot.get("freshness_hours"),
        "freshness_ok": snapshot.get("freshness_ok"),
    }
    return {key: value for key, value in brief.items() if value is not None}


def build_health_snapshot_report(
    snapshots: list[dict],
    severity_count: dict[str, int],
    *,
    now: datetime,
    run_started_at: str,
    keep_history: int,
    dry_run: bool,
) -> dict[str, Any]:
    red_tables = [_snapshot_brief(snapshot) for snapshot in snapshots if snapshot.get("severity") == "red"]
    yellow_tables = [_snapshot_brief(snapshot) for snapshot in snapshots if snapshot.get("severity") == "yellow"]
    blocking_yellow_tables = [
        _snapshot_brief(snapshot)
        for snapshot in snapshots
        if snapshot.get("severity") == "yellow"
        and str(snapshot.get("quality_gate_level") or "").strip().lower() == "blocking"
    ]
    summary = {
        "total": len(snapshots),
        "green": int(severity_count.get("green", 0)),
        "yellow": int(severity_count.get("yellow", 0)),
        "red": int(severity_count.get("red", 0)),
        "blocking_yellow": len(blocking_yellow_tables),
    }
    verdict = "FAIL" if red_tables else ("WARN" if yellow_tables else "PASS")
    return {
        "schema_version": 1,
        "command": "data_health_snapshot",
        "run_started_at": run_started_at,
        "snapshot_at": now.isoformat(timespec="seconds"),
        "dry_run": bool(dry_run),
        "keep_history": int(keep_history),
        "summary": summary,
        "verdict": verdict,
        "red_tables": red_tables,
        "yellow_tables": yellow_tables,
        "blocking_yellow_tables": blocking_yellow_tables,
        "blockers": [item["table_name"] for item in red_tables if item.get("table_name")],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--keep-history", type=int, default=30,
                        help="保留多少天历史快照 (其余删, 默认 30)")
    args = parser.parse_args()
    run_started_at = utc_now_iso()
    run_t0 = time.perf_counter()

    con = get_conn()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ensure_asset_deprecation_columns(con)

    # 拉所有登记的资产
    asset_columns = set(get_table_columns(con, "dim_data_asset"))
    base_cols = [
        "table_name", "layer", "purpose", "writer_module", "reader_modules",
        "upstream_source", "source_tier", "expected_freshness", "sla_hours",
        "consumed_by_views", "deprecation_status", "replacement_table",
    ]
    optional_cols = [
        "asset_grain", "asset_cadence", "coverage_policy", "null_policy",
        "pit_policy", "intended_use", "model_eligibility",
        "strategy_eligibility", "frontend_visibility", "quality_gate_level",
    ]
    select_cols = list(base_cols)
    select_cols.extend(
        col if col in asset_columns else f"NULL AS {col}"
        for col in optional_cols
    )
    rows = con.execute(
        f"""
        SELECT {', '.join(select_cols)}
        FROM dim_data_asset
        ORDER BY layer, table_name
        """
    ).fetchall()
    log.info("scanning %d assets", len(rows))

    snapshots: list[dict] = []
    severity_count = {"green": 0, "yellow": 0, "red": 0}
    for asset_row in rows:
        asset = dict(asset_row)
        snap = compute_health_for_table(con, asset, now)
        snap["snapshot_at"] = now.isoformat(timespec="seconds")
        snap["quality_gate_level"] = asset.get("quality_gate_level")
        snapshots.append(snap)
        severity_count[snap["severity"]] = severity_count.get(snap["severity"], 0) + 1

    report = build_health_snapshot_report(
        snapshots,
        severity_count,
        now=now,
        run_started_at=run_started_at,
        keep_history=args.keep_history,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        log.info("[dry] severity counts: %s", severity_count)
        blocking_yellow_list = [
            s for s in snapshots
            if s["severity"] == "yellow"
            and str(s.get("quality_gate_level") or "").strip().lower() == "blocking"
        ]
        blocking_yellow_names = {str(s["table_name"]) for s in blocking_yellow_list}
        if blocking_yellow_list:
            log.info("\n=== blocking yellow tables (%d) ===", len(blocking_yellow_list))
            for s in blocking_yellow_list[:30]:
                owner = s.get("writer_prompt")
                suffix = f" | {owner}" if owner else ""
                log.info("  [WARN][blocking] %s — %s%s", s["table_name"], s["issue_summary"], suffix)
            if len(blocking_yellow_list) > 30:
                log.info("  ... +%d more", len(blocking_yellow_list) - 30)
        for s in snapshots:
            if s["severity"] != "green" and not (
                s["severity"] == "yellow" and str(s.get("table_name") or "") in blocking_yellow_names
            ):
                owner = s.get("writer_prompt")
                suffix = f" | {owner}" if owner else ""
                log.info("  %s %s: %s%s", s["severity"], s["table_name"], s["issue_summary"], suffix)
        con.close()
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if report["verdict"] == "FAIL" else 0

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
            _coerce_db_timestamp(s["last_writer_at"]), s["null_rate_pct"], s["source_tier_dist"],
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
            owner = s.get("writer_prompt")
            suffix = f" | {owner}" if owner else ""
            log.info("  [FAIL] %s — %s%s", s["table_name"], s["issue_summary"], suffix)
        if len(red_list) > 30:
            log.info("  ... +%d more", len(red_list) - 30)

    yellow_list = [s for s in snapshots if s["severity"] == "yellow"]
    record_pipeline_run(
        con,
        run_id=f"data_health_snapshot_{now.strftime('%Y%m%d_%H%M%S')}",
        pipeline_name="data_health_snapshot",
        status="success" if not red_list else "failed",
        started_at=run_started_at,
        ended_at=utc_now_iso(),
        duration_s=time.perf_counter() - run_t0,
        commit_sha=git_commit_sha(REPO),
        input_tables=["dim_data_asset"],
        output_tables=["mart_data_health"],
        gate_result="pass" if not red_list else "fail",
        blockers=[s["table_name"] for s in red_list],
        perf_summary={
            "total": len(snapshots),
            "green": severity_count.get("green", 0),
            "yellow": severity_count.get("yellow", 0),
            "red": severity_count.get("red", 0),
            "yellow_tables": [s["table_name"] for s in yellow_list[:30]],
            "red_tables": [s["table_name"] for s in red_list[:30]],
        },
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    con.close()
    # CI 守门: 任一 red → exit 1
    return 1 if red_list else 0


if __name__ == "__main__":
    raise SystemExit(main())

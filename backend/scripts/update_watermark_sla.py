#!/usr/bin/env python3
"""Watermark SLA updater + sync gap auto-alert.

ChunkyMonkey 交付标准 #1 数据管理: watermark 实填 + sync gap auto-alert.

每个 data source 跑 actual max date 跟 watermark.last_data_date 对比:
- actual > watermark → 自动 update watermark
- 有 registry 完整性契约时，watermark > verified complete frontier → 显式纠错回落
- actual - current_date > SLA threshold → alert (log + JSON report)
- watermark stale > SLA threshold → alert (即使没数据 update)

调用 ways:
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --dry-run
  PYTHONPATH=backend python backend/scripts/update_watermark_sla.py --json-output /tmp/sla.json

被 daily_update.sh Step 1 调用.

SLA per source_tier (Codex R26 architecture audit doc §4.d 设计; 2026-07-02 批7 源名同步 —
tdxhub/akshare 已全退役, 现役源 = tushare + aif10/miaoxiang):
- tier 1 (tushare/miaoxiang 主源): SLA 1 trading day
- tier 2 (aif10 季频域): SLA 2 trading days
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect as duck_connect
from services.data_sources.batch_integrity import (  # noqa: E402
    VerifiedBatchFrontier,
    latest_complete_batch,
)

DEFAULT_SMARTMONEY_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_MARKET_DB = REPO_ROOT / "data" / "market.duckdb"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "audit" / "watermark_sla_latest.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watermark_sla")


@dataclass(frozen=True)
class ActualFrontierProbe:
    actual_date: str | None
    verified_frontier: VerifiedBatchFrontier | None
    state: str
    error: str | None = None

# SLA threshold by source_tier (trading days)
SLA_DAYS = {1: 1, 2: 2, 3: 3}

# Override: 季度数据 (报告期 quarterly) 单独配置 SLA, 不走 tier
# 季报披露窗: Q1=4/30 / Q2=8/31 / Q3=10/31 / Q4=4/30(年报)
# QFII 用 report_date 水位: Q1→Q2 最坏跨度 ≈ Mar31→Aug31 披露截止 ≈ 153d → 160d 覆盖
# holders 仍 100d (notice_date / page 更密)。rule-compliance: ok evidence=a-share-disclosure-window
SLA_DAYS_OVERRIDE = {
    # financial_gpcw_8q override 已删 2026-06-28 (fact_financial_derived U4 退役, DATA_SOURCE_QUERIES 条目同删)
    "holders_top10_float": 100,      # top10 股东季报
    "qfii_holding_quarterly": 160,   # qfii 季报 (report_date → next disclosure deadline)
}

# CX-4 / P0.1: retired DOMAIN_SPECS rows that still haunt mart_data_source_watermark.
# Allowlist-only delete — never invent tombstones from NO_QUERY_MAPPING alone.
RETIRED_WATERMARK_TOMBSTONES = frozenset(
    {
        # LHB 2026-06-29 → tushare top_list/top_inst; DOMAIN_SPECS entry removed.
        ("lhb_daily", "aif10_lhb"),
    }
)

# data_domain → actual table + date column
DATA_SOURCE_QUERIES = {
    "kline_daily": {
        "db": "market",
        "query": "SELECT MAX(CAST(date AS VARCHAR)) FROM v_price_kline_qfq WHERE adjust='qfq' AND freq='daily'",
    },
    # (退役域条目已清, 详 ledger + git史)
    "holders_top10_float": {
        "db": "smartmoney",
        "query": "SELECT MAX(CAST(report_date AS VARCHAR)) FROM fact_top10_holder_period",
    },
    # Strangler observer only — not publication truth. Typed no_probe so unknown≠alert.
    "holders_top10_float_legacy_observer": {
        "db": "smartmoney",
        "no_probe": "legacy_observer_not_publication_truth",
    },
    "qfii_holding_quarterly": {
        "db": "smartmoney",
        "query": (
            "SELECT MAX(CAST(report_date AS VARCHAR)) FROM raw_qfii_holding_quarterly"
        ),
    },
    "industry_dc": {
        # 2026-06-23 单一供应商=东财 (Stage②): 查东财 serving 表 dim_stock_dc_industry.updated_at
        #   (daily_update Step 2.96c build_dc_industry_view 每日刷新; 东财行业=申万对齐)。
        "db": "smartmoney",
        "query": "SELECT CAST(MAX(updated_at) AS VARCHAR) FROM dim_stock_dc_industry",
    },
    # stock_blocks 域已删 (2026-06-23): 原查通达信表, 源退役; 申万行业新鲜度由 industry_sw 域跟踪。
}


class SyncRegistrySLAError(RuntimeError):
    """The registry cannot prove the complete sync-domain SLA inventory."""


def _assert_manual_domain_sla_inventory() -> None:
    """Every live DOMAIN_SPECS domain must have probe or typed no_probe."""
    from services.source_watermarks import DOMAIN_SPECS

    missing = sorted(
        {
            str(spec["data_domain"])
            for spec in DOMAIN_SPECS
            if str(spec.get("data_domain") or "") not in DATA_SOURCE_QUERIES
        }
    )
    if missing:
        raise SyncRegistrySLAError(
            "DOMAIN_SPECS missing SLA mapping (probe or no_probe): "
            + ", ".join(missing)
        )


def _sync_registry_queries(*, registry_path: Path | None = None) -> dict[str, dict]:
    """sync:* 域 SLA 查询从 sync_registry.yaml 自动生成 — registry 驱动, 不手维护.

    复审 HIGH (2026-06-11): sync:* 域无 DATA_SOURCE_QUERIES 条目 → actual=None →
    永不告警 = audit 防线对全部新数据域盲区 (静默腐烂同型)。registry 注册即自动入防线。
    """
    import yaml

    out: dict[str, dict] = {}
    reg_path = registry_path or REPO_ROOT / "backend" / "config" / "sync_registry.yaml"
    try:
        reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(reg, dict):
            raise TypeError("registry root must be a mapping")
        domains = reg.get("domains")
        if not isinstance(domains, dict) or not domains:
            raise ValueError("registry domains must be a non-empty mapping")
        defaults = reg.get("defaults") or {}
        if not isinstance(defaults, dict):
            raise TypeError("registry defaults must be a mapping")
        from services.data_sources.formal_boundaries import formal_boundary
        from services.data_sources.margin_ingest import contract_for_spec

        for name, spec in domains.items():
            if not isinstance(name, str) or not name or not isinstance(spec, dict):
                raise TypeError("registry domain entries must be named mappings")
            mode = spec.get("batch_mode")
            contract_spec = dict(defaults)
            contract_spec.update(spec)
            contract_spec["domain"] = name
            margin_contract = contract_for_spec(contract_spec)
            boundary = formal_boundary(name)
            if margin_contract is not None:
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "accepted_margin": True,
                    "_margin_contract": margin_contract,
                    "sla_days": spec.get("freshness_sla_trading_days"),
                    "parser_version": f"margin_accepted_contract_{margin_contract.contract_version}",
                }
            elif (
                boundary is not None
                and isinstance(spec.get("security_day_partition"), dict)
                and boundary.dataset_id
            ):
                # Formal daily/ST: SLA judges accepted_partition, not legacy raw MAX.
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "query": (
                        "SELECT MAX(partition_value) FROM accepted_partition "
                        f"WHERE dataset_id = '{boundary.dataset_id}'"
                    ),
                    "sla_days": spec.get("freshness_sla_trading_days"),
                    "formal_accepted_frontier": True,
                    "parser_version": f"security_day_accepted_{boundary.dataset_id}",
                }
            elif spec.get("freshness_no_probe"):
                # 季报/事件域 (forecast/income/dividend): ann_date 淡季数周无新数据=正常, 日频 SLA 会
                # 误报疲劳 (mythos§10); 完整性靠 gap drain 覆盖, 不做日频新鲜度探测。
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": spec.get("freshness_no_probe")}
            elif mode in ("by_trade_date", "by_date_range"):
                # 2026-06-22 P0-6: 日期列从 registry 读 (freshness_date_column 缺省 trade_date) —
                # 旧硬编 trade_date 撞 report_rc(report_date)/stk_surv(surv_date) 等无该列域
                # → BinderException 被 try/except 吞 = SLA 永久盲区。
                date_col = spec.get("freshness_date_column", "trade_date")
                out[f"sync:{name}"] = {
                    "db": "tushare_raw",
                    "query": f'SELECT MAX(CAST("{date_col}" AS VARCHAR)) FROM "{spec["target_table"]}"',
                    "sla_days": spec.get("freshness_sla_trading_days"),
                }
                if (
                    spec.get("batch_completeness")
                    or int(spec.get("min_rows_per_batch", 0)) > 0
                ):
                    out[f"sync:{name}"]["verified_complete_spec"] = {
                        **spec,
                        "domain": name,
                    }
            else:
                # 季度 (by_ts_code) / 日历 (full_refresh) 无日频新鲜度语义 — 显式标注, 不静默当 OK
                out[f"sync:{name}"] = {"db": "tushare_raw", "no_probe": f"batch_mode={mode}"}

            # Any disabled domain: observe lag, do not light actionable sla_warn.
            # Key = execution_policy.mode only (on_demand enabled domains stay alertable).
            entry = out.get(f"sync:{name}")
            exec_pol = contract_spec.get("execution_policy") or {}
            if (
                isinstance(entry, dict)
                and isinstance(exec_pol, dict)
                and exec_pol.get("mode") == "disabled"
            ):
                entry["observe_only"] = True
                entry["observe_reason"] = str(
                    exec_pol.get("reason") or "execution_disabled"
                )
    except Exception as exc:  # noqa: BLE001 — incomplete inventory is a blocking SLA failure
        raise SyncRegistrySLAError(
            f"sync_registry unverified: {type(exc).__name__}"
        ) from exc
    return out


def _query_actual_frontier(
    conns: dict, queries: dict, data_domain: str
) -> ActualFrontierProbe:
    spec = queries.get(data_domain)
    if not spec:
        return ActualFrontierProbe(None, None, "no_mapping")
    if spec.get("no_probe"):
        return ActualFrontierProbe(None, None, "no_probe")
    conn = conns.get(spec["db"])
    if conn is None:
        return ActualFrontierProbe(None, None, "db_unavailable")
    try:
        if spec.get("accepted_margin"):
            from services.data_sources.margin_state import (
                load_margin_accepted_state,
            )

            frontier = load_margin_accepted_state(
                conn, contract=spec.get("_margin_contract")
            ).frontier
            if frontier is None:
                return ActualFrontierProbe(None, None, "no_complete_batch")
            return ActualFrontierProbe(frontier.last_date, frontier, "verified")
        if spec.get("verified_complete_spec"):
            frontier = latest_complete_batch(conn, spec["verified_complete_spec"])
            if frontier is None:
                return ActualFrontierProbe(None, None, "no_complete_batch")
            return ActualFrontierProbe(frontier.last_date, frontier, "verified")
        r = conn.execute(spec["query"]).fetchone()
        actual = r[0] if r and r[0] else None
        return ActualFrontierProbe(actual, None, "observed" if actual else "no_data")
    except Exception as e:
        log.warning(f"  query failed for {data_domain}: {e}")
        return ActualFrontierProbe(None, None, "probe_error", str(e)[:300])


def _query_actual_max_date(conns: dict, queries: dict, data_domain: str) -> str | None:
    return _query_actual_frontier(conns, queries, data_domain).actual_date


def _probe_gate(state: str) -> tuple[str | None, bool]:
    """Map probe evidence to a fail-closed status before reconciliation/SLA checks."""
    return {
        "no_mapping": ("NO_QUERY_MAPPING", True),
        "no_probe": ("NO_PROBE_RULE", False),
        # Preflight runs before the writer lease and Store reruns after release;
        # either boundary must fail closed if accepted evidence cannot be read.
        "db_unavailable": ("DB_LOCKED_UNVERIFIED", True),
        "no_complete_batch": ("NO_COMPLETE_BATCH", True),
        "no_data": ("NO_DATA", True),
        "probe_error": ("PROBE_ERROR", True),
    }.get(state, (None, False))


def _accepted_projection_drift(
    *,
    watermark_date: str | None,
    watermark_row_count: int | None,
    watermark_parser_version: str | None,
    frontier: VerifiedBatchFrontier,
    expected_parser_version: str,
) -> list[str]:
    """Audit the formal margin projection without becoming a second writer."""
    drift: list[str] = []
    compact_watermark = str(watermark_date or "").replace("-", "")
    compact_accepted = str(frontier.last_date or "").replace("-", "")
    if compact_watermark != compact_accepted:
        drift.append(f"last_data_date={compact_watermark or None}!={compact_accepted}")
    if int(watermark_row_count or 0) != int(frontier.row_count):
        drift.append(f"row_count={int(watermark_row_count or 0)}!={frontier.row_count}")
    if str(watermark_parser_version or "") != expected_parser_version:
        drift.append(
            f"parser_version={watermark_parser_version!r}!={expected_parser_version!r}"
        )
    return drift


def _registered_domain_without_watermark_result(
    conns: dict,
    queries: dict,
    data_domain: str,
    qspec: dict,
    today: date,
) -> dict:
    """Probe a registered domain even when no watermark row exists."""
    probe = _query_actual_frontier(conns, queries, data_domain)
    status, alert = _probe_gate(probe.state)
    if probe.state in ("verified", "observed"):
        status, alert = "MISSING_WATERMARK", True
    if qspec.get("observe_only") and alert:
        status = f"FROZEN_{status or 'NEVER_SYNCED'}_OBSERVED"
        alert = False
    actual_days = _days_since(probe.actual_date, today)
    return {
        "data_domain": data_domain,
        "source_name": "tushare",
        "source_tier": 2,
        "watermark_date": None,
        "actual_date": probe.actual_date,
        "actual_days_ago": actual_days,
        "watermark_days_ago": None,
        "sla_days": qspec.get("sla_days") or SLA_DAYS[2],
        "status": status or "NEVER_SYNCED",
        "alert": alert,
        "verified_complete_frontier": probe.verified_frontier is not None,
        "watermark_reconcile": None,
        "probe_state": probe.state,
        "probe_error": probe.error,
        "observe_only": bool(qspec.get("observe_only")),
    }


def _watermark_reconcile_direction(
    watermark_date: str | None,
    actual_date: str | None,
    *,
    verified_complete: bool,
) -> str | None:
    """Forward is always safe; rollback requires an explicit verified frontier."""
    if not watermark_date or not actual_date:
        return None
    watermark = str(watermark_date)[:10].replace("-", "")
    actual = str(actual_date)[:10].replace("-", "")
    if watermark < actual:
        return "forward"
    if watermark > actual and verified_complete:
        return "rollback"
    return None


def _apply_watermark_reconcile(
    conn,
    *,
    data_domain: str,
    source_name: str,
    source_tier: int,
    actual_date: str,
    verified_frontier: VerifiedBatchFrontier | None,
) -> None:
    if verified_frontier is not None:
        conn.execute(
            "UPDATE mart_data_source_watermark "
            "SET last_data_date = ?, row_count = ?, "
            "last_success_at = CAST(? AS TIMESTAMP), "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE data_domain = ? AND source_name = ? AND source_tier = ?",
            [
                verified_frontier.last_date,
                verified_frontier.row_count,
                verified_frontier.last_success_at,
                data_domain,
                source_name,
                source_tier,
            ],
        )
        return
    conn.execute(
        "UPDATE mart_data_source_watermark "
        "SET last_data_date = ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE data_domain = ? AND source_name = ? AND source_tier = ?",
        [actual_date, data_domain, source_name, source_tier],
    )


def _days_since(date_str: str | None, today: date) -> int | None:
    if not date_str:
        return None
    s = str(date_str)[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            d = datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return (today - d).days


def _purge_retired_watermark_tombs(
    conn, *, dry_run: bool = False
) -> list[dict[str, str | int]]:
    """Delete allowlisted retired watermark PKs only (CX-4 / P0.1).

    Never deletes from live DOMAIN_SPECS or invents tombs from probe status.
    """
    from services.source_watermarks import DOMAIN_SPECS

    live_keys = {
        (str(spec["data_domain"]), str(spec["source_name"])) for spec in DOMAIN_SPECS
    }
    purged: list[dict[str, str | int]] = []
    for data_domain, source_name in sorted(RETIRED_WATERMARK_TOMBSTONES):
        if (data_domain, source_name) in live_keys:
            raise RuntimeError(
                f"refusing tombstone purge for live DOMAIN_SPECS entry "
                f"{data_domain}/{source_name}"
            )
        rows = conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date "
            "FROM mart_data_source_watermark "
            "WHERE data_domain = ? AND source_name = ?",
            [data_domain, source_name],
        ).fetchall()
        if not rows:
            continue
        if not dry_run:
            conn.execute(
                "DELETE FROM mart_data_source_watermark "
                "WHERE data_domain = ? AND source_name = ?",
                [data_domain, source_name],
            )
        for row in rows:
            purged.append(
                {
                    "data_domain": str(row[0]),
                    "source_name": str(row[1]),
                    "source_tier": int(row[2]),
                    "last_data_date": str(row[3]) if row[3] is not None else "",
                    "action": "would_delete" if dry_run else "deleted",
                }
            )
            log.info(
                "  [TOMBSTONE:%s] %s/%s tier=%s last_data_date=%s",
                "dry-run" if dry_run else "purge",
                row[0],
                row[1],
                row[2],
                row[3],
            )
    return purged


def main() -> int:
    parser = argparse.ArgumentParser(description="Watermark SLA auto-update + alert")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--smartmoney-db", default=str(DEFAULT_SMARTMONEY_DB))
    parser.add_argument("--market-db", default=str(DEFAULT_MARKET_DB))
    args = parser.parse_args()

    try:
        _assert_manual_domain_sla_inventory()
        registry_queries = _sync_registry_queries()
    except Exception as exc:  # noqa: BLE001 — never retain a stale green artifact on inventory loss
        try:
            Path(args.json_output).unlink(missing_ok=True)
        except OSError as unlink_exc:
            log.error(
                "cannot remove stale SLA artifact after registry failure: %s",
                type(unlink_exc).__name__,
            )
        log.error("sync_registry SLA inventory blocked: %s", exc)
        return 3
    queries = {**DATA_SOURCE_QUERIES, **registry_queries}

    today = date.today()  # rule-compliance: ok evidence=SLA staleness age 度量(对wall-clock计天龄, 非交易决策)
    log.info(f"=== watermark SLA check @ {today} ===")

    smart_conn = duck_connect(args.smartmoney_db, read_only=False)  # need write
    market_conn = duck_connect(args.market_db, read_only=True)
    # tushare_raw: 回填链持写锁时 read_only 也连不上 (DuckDB 排他) — 显式置 None,
    # 对应域标 DB_LOCKED_UNVERIFIED 而非静默 OK
    raw_conn = None
    try:
        from services.database_manifest import get_database_manifest

        raw_conn = duck_connect(str(get_database_manifest().path_for("tushare_raw")), read_only=True)
    except Exception as e:  # noqa: BLE001 — 锁竞争是回填期常态, 显式降级不挡 SLA 主流程
        log.warning(f"tushare_raw 不可达 (回填链占锁?): {e}")
    conns = {"market": market_conn, "smartmoney": smart_conn, "tushare_raw": raw_conn}
    try:
        tombstone_purges = _purge_retired_watermark_tombs(
            smart_conn, dry_run=args.dry_run
        )

        watermark_rows = smart_conn.execute(
            "SELECT data_domain, source_name, source_tier, last_data_date, updated_at, "
            "row_count, parser_version "
            "FROM mart_data_source_watermark ORDER BY data_domain, source_name"
        ).fetchall()
        log.info(f"  watermark rows: {len(watermark_rows)}")

        results: list[dict] = []
        n_update = 0
        n_alert = 0
        for row in watermark_rows:
            (
                data_domain,
                source_name,
                source_tier,
                watermark_date,
                updated_at,
                watermark_row_count,
                watermark_parser_version,
            ) = row

            probe = _query_actual_frontier(
                conns, queries, data_domain
            )
            actual_date = probe.actual_date
            verified_frontier = probe.verified_frontier
            actual_days = _days_since(actual_date, today)
            watermark_days = _days_since(watermark_date, today)
            # SLA 优先序: registry per-domain > 季度 override > tier 默认
            qspec = queries.get(data_domain) or {}
            sla = (qspec.get("sla_days")
                   or SLA_DAYS_OVERRIDE.get(data_domain)
                   or SLA_DAYS.get(source_tier, 3))

            probe_status, alert = _probe_gate(probe.state)
            status = probe_status or "OK"
            if alert:
                log.warning(
                    f"  [ALERT] {data_domain}/{source_name}: probe={probe.state} "
                    f"error={probe.error or '-'}"
                )

            # 1. watermark 与 actual 对账。普通 raw MAX 只允许前推；只有 registry 完整性
            # 契约算出的 verified frontier 才能证明现水位无效并安全回落。
            reconcile = None
            projection_drift: list[str] = []
            if qspec.get("accepted_margin") and verified_frontier is not None:
                projection_drift = _accepted_projection_drift(
                    watermark_date=watermark_date,
                    watermark_row_count=watermark_row_count,
                    watermark_parser_version=watermark_parser_version,
                    frontier=verified_frontier,
                    expected_parser_version=qspec["parser_version"],
                )
                if projection_drift:
                    if qspec.get("observe_only"):
                        status = "FROZEN_PROJECTION_DRIFT_OBSERVED"
                        alert = False
                        log.info(
                            f"  [OBSERVE] {data_domain}/{source_name}: accepted "
                            f"projection drift={projection_drift} "
                            f"(observe_only={qspec.get('observe_reason')})"
                        )
                    else:
                        status = "ACCEPTED_PROJECTION_DRIFT"
                        alert = True
                        log.warning(
                            f"  [ALERT] {data_domain}/{source_name}: accepted projection "
                            f"drift={projection_drift}"
                        )
            elif not qspec.get("accepted_margin"):
                reconcile = _watermark_reconcile_direction(
                    watermark_date,
                    actual_date,
                    verified_complete=verified_frontier is not None,
                )
                if reconcile:
                    status = (
                        "INVALID_WATERMARK_FRONTIER"
                        if reconcile == "rollback"
                        else "STALE_WATERMARK"
                    )
                    if not args.dry_run:
                        _apply_watermark_reconcile(
                            smart_conn,
                            data_domain=data_domain,
                            source_name=source_name,
                            source_tier=source_tier,
                            actual_date=str(actual_date),
                            verified_frontier=verified_frontier,
                        )
                        n_update += 1
                        log.info(
                            f"  [UPDATE:{reconcile}] {data_domain}/{source_name}: "
                            f"{watermark_date} → {actual_date}"
                        )

            # 2. actual data stale vs SLA
            if actual_days is not None and actual_days > sla:
                # 注意: 周末 / 节假日 不 alert. 简单 SLA 不区分.
                if actual_days > sla + 3:  # 3 day buffer for weekend
                    if qspec.get("observe_only"):
                        # Frozen/disabled domain: record lag, do not light sla_warn.
                        status = "FROZEN_STALE_OBSERVED"
                        alert = False
                        log.info(
                            f"  [OBSERVE] {data_domain}/{source_name}: actual "
                            f"{actual_date} ({actual_days}d ago) > SLA {sla}d "
                            f"(observe_only={qspec.get('observe_reason')})"
                        )
                    else:
                        status = "DATA_STALE_VS_SLA"
                        alert = True
                        log.warning(
                            f"  [ALERT] {data_domain}/{source_name}: actual {actual_date} "
                            f"({actual_days}d ago) > SLA {sla}d (tier {source_tier})"
                        )

            if alert:
                n_alert += 1
            results.append({
                "data_domain": data_domain,
                "source_name": source_name,
                "source_tier": source_tier,
                "watermark_date": str(watermark_date) if watermark_date else None,
                "actual_date": actual_date,
                "actual_days_ago": actual_days,
                "watermark_days_ago": watermark_days,
                "sla_days": sla,
                "status": status,
                "alert": alert,
                "verified_complete_frontier": verified_frontier is not None,
                "watermark_reconcile": reconcile,
                "probe_state": probe.state,
                "probe_error": probe.error,
                "projection_drift": projection_drift,
                "observe_only": bool(qspec.get("observe_only")),
            })

        # registry 域 ∪ watermark 行: 从未成功 sync 的注册域没有 watermark 行 →
        # 不进上面循环 = 注册后一直没跑成会永远隐形。显式补 NEVER_SYNCED 行。
        seen_domains = {r[0] for r in watermark_rows}
        for qd, qspec in queries.items():
            if qd.startswith("sync:") and qd not in seen_domains:
                missing_result = _registered_domain_without_watermark_result(
                    conns, queries, qd, qspec, today
                )
                if missing_result["alert"]:
                    n_alert += 1
                results.append(missing_result)

        # Write JSON report
        Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "today": str(today),
                "dry_run": args.dry_run,
                "n_updates": n_update,
                "n_alerts": n_alert,
                "tombstone_purges": tombstone_purges,
                "sources": results,
            }, f, ensure_ascii=False, indent=2)
        log.info(f"=== SLA check done: {n_update} watermark updated, {n_alert} alerts ===")
        log.info(f"  Report: {args.json_output}")
        return 2 if n_alert > 0 else 0
    finally:
        try:
            market_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"market_conn close failed: {e}")
        try:
            smart_conn.close()
        except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
            log.warning(f"smart_conn close failed: {e}")
        if raw_conn is not None:
            try:
                raw_conn.close()
            except Exception as e:  # rule-compliance: ok evidence=cleanup-best-effort
                log.warning(f"raw_conn close failed: {e}")


if __name__ == "__main__":
    sys.exit(main())

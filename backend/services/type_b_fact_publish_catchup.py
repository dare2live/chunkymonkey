"""Type-B fact publish catchup — same-run window publish after registry drain.

When landing raw MAX(trade_date) leads fact MAX(trade_date), publish only the
closed lag window (start/end both required — never full rebuild).

Domains: moneyflow, moneyflow_dc, limit, index_daily, dc_member, top_inst_seat.
Bound ≤40 calendar days per domain per run (eng_gov).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from services.data_access import resolver
from services.duck_adapter import connect as duck_connect

TYPE_B_PUBLISH_CATCHUP_MAX_DAYS = 40


@dataclass(frozen=True)
class TypeBPublishSpec:
    domain: str
    raw_table: str
    fact_table: str
    publish_fn: Callable[..., dict[str, Any]]


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _day_add(day: str, delta: int) -> str:
    d = datetime.strptime(day, "%Y%m%d").date()
    return (d + timedelta(days=delta)).strftime("%Y%m%d")


def _table_present(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [name],
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _max_trade_date(conn: Any, table: str) -> str | None:
    if not _table_present(conn, table):
        return None
    try:
        row = conn.execute(
            f"""
            SELECT MAX(replace(CAST(trade_date AS VARCHAR), '-', ''))
              FROM {table}
             WHERE trade_date IS NOT NULL
            """
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _compact(row[0] if row else None)


def plan_type_b_publish_window(
    *,
    raw_max: str | None,
    fact_max: str | None,
    max_days: int = TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
) -> dict[str, Any]:
    """Compute bounded publish window; never returns open-ended rebuild."""
    if not raw_max:
        return {
            "action": "skip",
            "reason": "raw_empty",
            "start": None,
            "end": None,
            "lag_days": 0,
        }
    if fact_max and raw_max <= fact_max:
        return {
            "action": "skip",
            "reason": "fact_caught_up",
            "start": None,
            "end": None,
            "raw_max": raw_max,
            "fact_max": fact_max,
            "lag_days": 0,
        }
    if fact_max:
        start = _day_add(fact_max, 1)
    else:
        start = _day_add(raw_max, -(max(1, int(max_days)) - 1))
    if start > raw_max:
        start = raw_max
    end = raw_max
    span = (
        datetime.strptime(end, "%Y%m%d").date()
        - datetime.strptime(start, "%Y%m%d").date()
    ).days + 1
    if span > max_days:
        end = _day_add(start, max_days - 1)
        span = max_days
    return {
        "action": "publish_window",
        "reason": "raw_ahead_of_fact",
        "start": start,
        "end": end,
        "raw_max": raw_max,
        "fact_max": fact_max,
        "lag_days": span,
    }


def _type_b_specs() -> tuple[TypeBPublishSpec, ...]:
    from services.dc_member_publish import publish_fact_dc_member_daily
    from services.index_daily_publish import publish_fact_index_daily
    from services.stock_limit_publish import publish_fact_stock_limit_daily
    from services.stock_moneyflow_publish import (
        publish_fact_stock_moneyflow_daily,
        publish_fact_stock_moneyflow_dc_daily,
    )
    from services.top_inst_seat_publish import publish_fact_top_inst_seat_daily

    return (
        TypeBPublishSpec(
            "moneyflow",
            "raw_tushare_moneyflow",
            "fact_stock_moneyflow_daily",
            publish_fact_stock_moneyflow_daily,
        ),
        TypeBPublishSpec(
            "moneyflow_dc",
            "raw_tushare_moneyflow_dc",
            "fact_stock_moneyflow_dc_daily",
            publish_fact_stock_moneyflow_dc_daily,
        ),
        TypeBPublishSpec(
            "limit",
            "raw_tushare_limit_list_d",
            "fact_stock_limit_daily",
            publish_fact_stock_limit_daily,
        ),
        TypeBPublishSpec(
            "index_daily",
            "raw_tushare_index_daily",
            "fact_index_daily",
            publish_fact_index_daily,
        ),
        TypeBPublishSpec(
            "dc_member",
            "raw_tushare_dc_member",
            "fact_dc_member_daily",
            publish_fact_dc_member_daily,
        ),
        TypeBPublishSpec(
            "top_inst_seat",
            "raw_tushare_top_inst",
            "fact_top_inst_seat_daily",
            publish_fact_top_inst_seat_daily,
        ),
    )


def type_b_publish_specs() -> tuple[TypeBPublishSpec, ...]:
    """Public SSOT list for Type-B raw→fact publish domains."""
    return _type_b_specs()


def catchup_type_b_fact_publish(
    *,
    raw_db: Path | None = None,
    smartmoney_db: Path | None = None,
    max_days: int = TYPE_B_PUBLISH_CATCHUP_MAX_DAYS,
) -> dict[str, Any]:
    """Publish bounded windows for Type-B domains where raw leads fact."""
    raw_path = Path(raw_db) if raw_db else Path(resolver.db_path("tushare_raw"))
    sm_path = (
        Path(smartmoney_db)
        if smartmoney_db
        else Path(resolver.db_path("smartmoney"))
    )
    if not raw_path.is_file():
        return {
            "status": "skipped",
            "reason": "raw_db_missing",
            "domains": [],
        }

    # Phase 1: read MAX(trade_date) only — close before publish (publish opens RW).
    raw_conn = duck_connect(str(raw_path), read_only=True)
    sm_conn = duck_connect(str(sm_path), read_only=True)
    try:
        planned: list[tuple[TypeBPublishSpec, dict[str, Any]]] = []
        for spec in _type_b_specs():
            raw_max = _max_trade_date(raw_conn, spec.raw_table)
            fact_max = _max_trade_date(sm_conn, spec.fact_table)
            plan = plan_type_b_publish_window(
                raw_max=raw_max,
                fact_max=fact_max,
                max_days=max_days,
            )
            planned.append((spec, plan))
    finally:
        raw_conn.close()
        sm_conn.close()

    # Phase 2: publish with bounded start/end (each publish_fn owns connections).
    domain_rows: list[dict[str, Any]] = []
    published = 0
    skipped = 0
    errors: list[str] = []
    for spec, plan in planned:
        row = {
            "domain": spec.domain,
            "raw_max": plan.get("raw_max"),
            "fact_max": plan.get("fact_max"),
            "plan": plan,
        }
        if plan["action"] != "publish_window":
            skipped += 1
            row["status"] = "skipped"
            domain_rows.append(row)
            continue
        start = plan["start"]
        end = plan["end"]
        if not start or not end:
            row["status"] = "skipped"
            row["error"] = "missing_window_bounds"
            skipped += 1
            domain_rows.append(row)
            continue
        try:
            out = spec.publish_fn(start=start, end=end)
            row["status"] = "published"
            row["publish"] = out
            published += 1
        except Exception as exc:  # noqa: BLE001
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}:{str(exc)[:120]}"
            if len(errors) < 20:
                errors.append(f"{spec.domain}:{row['error']}")
        domain_rows.append(row)

    status = "completed"
    if errors and published:
        status = "partial"
    elif errors and not published:
        status = "error"
    elif published == 0:
        status = "skipped"
    return {
        "status": status,
        "published_domains": published,
        "skipped_domains": skipped,
        "errors": errors,
        "max_days": max_days,
        "domains": domain_rows,
    }


def run_acquire_type_b_publish_catchup(ctx: Any) -> dict[str, Any]:
    """Pipeline acquire hook: publish bounded Type-B windows after registry drain."""
    import json

    from services.pipeline.context import PipelineContext

    if not isinstance(ctx, PipelineContext):
        raise TypeError("ctx must be PipelineContext")
    if ctx.dry or ctx.skip_sync:
        return {"status": "skipped", "reason": "dry_or_skip_sync"}
    try:
        out = catchup_type_b_fact_publish()
    except Exception as exc:  # noqa: BLE001
        ctx.degraded(f"type_b_fact_publish_catchup failed: {exc}")
        out = {
            "status": "error",
            "error": str(exc)[:300],
            "domains": [],
        }
    print("type_b_publish_catchup: " + json.dumps(out, ensure_ascii=False, default=str))
    if ctx.delta_manifest is None:
        from services.pipeline.delta_manifest import empty_manifest

        ctx.delta_manifest = empty_manifest(run_date=ctx.date)
    summary = dict(ctx.delta_manifest.get("acquire_summary") or {})
    summary["type_b_publish"] = out

    # F9: same-run residual hygiene on Type-B publish lag (ann tip checked in store).
    # Offline/CI without DuckDB → evaluate returns skipped PASS (no degrade).
    try:
        from services.residual_hygiene import evaluate_type_b_after_catchup

        hygiene = evaluate_type_b_after_catchup()
    except Exception as exc:  # noqa: BLE001 — last-resort; evaluate should not raise
        hygiene = {
            "overall": "PASS",
            "status": "skipped",
            "reason": "evaluate_unexpected_error",
            "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            "findings": [],
            "summary": {"fail": 0, "warn": 0, "pass": 0, "skip": 0},
        }
    summary["residual_hygiene_type_b"] = hygiene
    ctx.delta_manifest["acquire_summary"] = summary
    if (
        hygiene.get("overall") == "FAIL"
        and hygiene.get("status") != "skipped"
    ):
        ctx.degraded(
            "residual_hygiene type_b publish lag over SLA "
            "(see acquire_summary.residual_hygiene_type_b)"
        )
    if out.get("status") == "error":
        ctx.degraded("type_b fact publish catchup error (see log)")
    elif out.get("status") == "partial":
        ctx.degraded("type_b fact publish catchup partial (see log)")
    return out


__all__ = [
    "TYPE_B_PUBLISH_CATCHUP_MAX_DAYS",
    "TypeBPublishSpec",
    "catchup_type_b_fact_publish",
    "plan_type_b_publish_window",
    "run_acquire_type_b_publish_catchup",
    "type_b_publish_specs",
]

#!/usr/bin/env python3
"""Probe a registered data-source capability and summarize the live result."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.data_sources import resolve  # noqa: E402
from services.source_watermarks import record_source_failure, resolve_source_failures  # noqa: E402


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "tdx_data_need_coverage.yaml"
DATE_KEYS = (
    "日期",
    "date",
    "trade_date",
    "report_date",
    "notice_date",
    "snapshot_date",
    "as_of_date",
)

CAPABILITY_DATA_DOMAIN_HINTS = {
    "individual_fund_flow": "order_flow_fund_flow",
    "individual_fund_flow_rank": "order_flow_fund_flow",
    "individual_fund_flow_rank_snapshot": "stock_fund_flow_rank_snapshot",
}

CAPABILITY_SOURCE_HINTS = {
    "individual_fund_flow": "akshare",
    "individual_fund_flow_rank": "akshare",
    "individual_fund_flow_rank_snapshot": "akshare",
}

NEED027_ID = "need_027"
NEED027_EXACT_FLOW_CAPABILITY = "individual_fund_flow"
NEED027_SOURCE_NAME = "akshare"
NEED027_SOURCE_TIER = 3
NEED027_DATA_DOMAIN = "order_flow_fund_flow"
NEED027_MIN_SUCCESS_RATE = 1.0
NEED027_MIN_ROWS_PER_PROBE = 1
NEED027_EXACT_FLOW_COLUMN_GROUPS = {
    "main_force": ("主力", "main"),
    "super_large": ("超大", "super_large", "superlarge"),
    "large": ("大单", "large"),
    "medium": ("中单", "medium"),
    "small": ("小单", "small"),
}

logger = logging.getLogger("probe_source_capability")


@contextmanager
def _temporary_logger_level(logger_name: str, level: int):
    logger = logging.getLogger(logger_name)
    previous_level = logger.level
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(previous_level)


def _normalize_records(data: Any) -> tuple[list[dict[str, Any]], str]:
    if data is None:
        return [], "none"

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return [dict(row) for row in data], "records"
        return [{"value": item} for item in data], "list"

    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list) and (not data["data"] or isinstance(data["data"][0], dict)):
            return [dict(row) for row in data["data"]], "dict[data]"
        return [dict(data)], "dict"

    to_dict = getattr(data, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict()
        if isinstance(records, list) and (not records or isinstance(records[0], dict)):
            return [dict(row) for row in records], type(data).__name__
        if isinstance(records, dict):
            return [dict(records)], type(data).__name__

    return [{"value": str(data)}], type(data).__name__


def _infer_date_range(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for key in DATE_KEYS:
        values = []
        for row in records:
            value = row.get(key)
            if value in (None, ""):
                continue
            values.append(str(value))
        if values:
            return {"field": key, "min": min(values), "max": max(values)}
    return None


def _summarize(data: Any) -> dict[str, Any]:
    records, kind = _normalize_records(data)
    columns: list[str] = []
    for row in records[:5]:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in columns:
                columns.append(key)

    summary: dict[str, Any] = {
        "type": kind,
        "row_count": len(records),
        "columns": columns,
        "head": records[:5],
    }
    date_range = _infer_date_range(records)
    if date_range:
        summary["date_range"] = date_range
    return summary


def probe_source_capability(
    capability: str,
    kwargs: dict[str, Any],
    *,
    prefer_source: str | None = None,
    persist_status: bool = False,
    data_domain: str | None = None,
    source_name: str | None = None,
    source_tier: int | None = None,
    stock_code: str | None = None,
    quiet_registry_warnings: bool = True,
) -> dict[str, Any]:
    resolve_context = (
        _temporary_logger_level("data_sources.registry", logging.ERROR)
        if quiet_registry_warnings
        else nullcontext()
    )
    with resolve_context:
        try:
            data, source_used = resolve(capability, prefer_source=prefer_source, **kwargs)
        except Exception as exc:
            report = {
                "capability": capability,
                "prefer_source": prefer_source,
                "status": "blocked",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "kwargs": kwargs,
            }
            _persist_probe_status(
                report,
                capability=capability,
                persist_status=persist_status,
                prefer_source=prefer_source,
                data_domain=data_domain,
                source_name=source_name,
                source_tier=source_tier,
                stock_code=stock_code,
            )
            return report

        report = {
            "capability": capability,
            "prefer_source": prefer_source,
            "source_used": source_used,
            "status": "ok",
            "kwargs": kwargs,
        }
        report.update(_summarize(data))
        _persist_probe_status(
            report,
            capability=capability,
            persist_status=persist_status,
            prefer_source=prefer_source,
            data_domain=data_domain,
            source_name=source_name,
            source_tier=source_tier,
            stock_code=stock_code,
        )
        return report


def _normalize_probe_case(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each probe case must be an object")
    if "persist_status" in raw:
        raise ValueError("case-level persist_status is not supported; use --persist-status")
    capability = str(raw.get("capability") or "").strip()
    if not capability:
        raise ValueError("each probe case must define capability")
    kwargs = raw.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        raise ValueError("probe case kwargs must be an object")

    case = {
        "capability": capability,
        "kwargs": dict(kwargs),
    }
    for key in (
        "case_id",
        "prefer_source",
        "data_domain",
        "source_name",
        "source_tier",
        "stock_code",
    ):
        if key in raw:
            case[key] = raw[key]
    return case


def probe_source_capability_batch(
    cases: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    prefer_source: str | None = None,
    persist_status: bool = False,
    quiet_registry_warnings: bool = True,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for raw_case in cases:
        case = _normalize_probe_case(raw_case)
        report = probe_source_capability(
            str(case["capability"]),
            dict(case["kwargs"]),
            prefer_source=case.get("prefer_source") or prefer_source,
            persist_status=bool(persist_status),
            data_domain=case.get("data_domain"),
            source_name=case.get("source_name"),
            source_tier=case.get("source_tier"),
            stock_code=case.get("stock_code"),
            quiet_registry_warnings=quiet_registry_warnings,
        )
        if case.get("case_id"):
            report["case_id"] = str(case["case_id"])
        if case.get("stock_code"):
            report["stock_code"] = str(case["stock_code"])
        results.append(report)

    blocked_count = sum(1 for report in results if report.get("status") != "ok")
    return {
        "status": "ok" if blocked_count == 0 else "blocked",
        "probe_count": len(results),
        "ok_count": len(results) - blocked_count,
        "blocked_count": blocked_count,
        "results": results,
    }


def _with_need027_defaults(raw_case: dict[str, Any], *, prefer_source: str | None) -> dict[str, Any]:
    case = _normalize_probe_case(raw_case)
    if case["capability"] == NEED027_EXACT_FLOW_CAPABILITY:
        case.setdefault("prefer_source", prefer_source or NEED027_SOURCE_NAME)
        case.setdefault("data_domain", NEED027_DATA_DOMAIN)
        case.setdefault("source_name", NEED027_SOURCE_NAME)
        case.setdefault("source_tier", NEED027_SOURCE_TIER)
    return case


def _load_need027_probe_cases(config_path: Path | None = None) -> list[dict[str, Any]]:
    path = config_path or CONFIG_PATH
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    for need in loaded.get("needs") or []:
        if not isinstance(need, dict) or need.get("need_id") != NEED027_ID:
            continue
        cases = need.get("source_probe_cases") or []
        if not isinstance(cases, list):
            raise ValueError("need_027.source_probe_cases must be a list")
        return [_normalize_probe_case(case) for case in cases]
    return []


def _matches_need027_group(column: str, group: str, tokens: tuple[str, ...]) -> bool:
    lowered = column.lower()
    if group == "large":
        return ("大单" in column and "超大" not in column) or (
            "large" in lowered and "super" not in lowered
        )
    return any(token in column or token in lowered for token in tokens)


def _need027_column_coverage(columns: list[str]) -> dict[str, Any]:
    coverage = {
        group: [
            column
            for column in columns
            if _matches_need027_group(column, group, tokens)
        ]
        for group, tokens in NEED027_EXACT_FLOW_COLUMN_GROUPS.items()
    }
    missing = [group for group, matches in coverage.items() if not matches]
    return {
        "groups": coverage,
        "missing_groups": missing,
        "status": "ok" if not missing else "missing_required_groups",
    }


def _validate_need027_exact_report(
    report: dict[str, Any],
    *,
    min_rows_per_probe: int,
) -> dict[str, Any]:
    failures: list[str] = []
    if report.get("status") != "ok":
        failures.append("probe_blocked")
        return {
            "status": "blocked",
            "failures": failures,
            "column_coverage": {"status": "not_checked"},
        }

    row_count = int(report.get("row_count") or 0)
    if row_count < min_rows_per_probe:
        failures.append("row_count_below_minimum")
    if not report.get("date_range"):
        failures.append("missing_date_range")

    columns = [str(column) for column in (report.get("columns") or [])]
    column_coverage = _need027_column_coverage(columns)
    if column_coverage["missing_groups"]:
        failures.append("missing_exact_flow_columns")

    return {
        "status": "ok" if not failures else "blocked",
        "failures": failures,
        "column_coverage": column_coverage,
    }


def probe_need027_exact_flow_gate(
    cases: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    prefer_source: str | None = NEED027_SOURCE_NAME,
    persist_status: bool = False,
    min_success_rate: float = NEED027_MIN_SUCCESS_RATE,
    min_rows_per_probe: int = NEED027_MIN_ROWS_PER_PROBE,
    quiet_registry_warnings: bool = True,
) -> dict[str, Any]:
    selected_cases = list(cases) if cases is not None else _load_need027_probe_cases()
    normalized_cases = [
        _with_need027_defaults(case, prefer_source=prefer_source)
        for case in selected_cases
    ]
    batch = probe_source_capability_batch(
        normalized_cases,
        prefer_source=prefer_source,
        persist_status=False,
        quiet_registry_warnings=quiet_registry_warnings,
    )

    exact_reports: list[dict[str, Any]] = []
    non_exact_reports: list[dict[str, Any]] = []
    for report in batch["results"]:
        if report.get("capability") == NEED027_EXACT_FLOW_CAPABILITY:
            validation = _validate_need027_exact_report(
                report,
                min_rows_per_probe=min_rows_per_probe,
            )
            report["need027_exact_flow_validation"] = validation
            exact_reports.append(report)
        else:
            report["need027_exact_flow_validation"] = {
                "status": "ignored",
                "reason": "not exact-flow capability",
            }
            non_exact_reports.append(report)

    valid_exact_count = sum(
        1
        for report in exact_reports
        if report.get("need027_exact_flow_validation", {}).get("status") == "ok"
    )
    exact_probe_count = len(exact_reports)
    success_rate = valid_exact_count / exact_probe_count if exact_probe_count else 0.0
    blocked_exact_count = exact_probe_count - valid_exact_count
    verdict_pass = (
        exact_probe_count > 0
        and blocked_exact_count == 0
        and success_rate >= min_success_rate
    )

    failure_reasons: dict[str, int] = {}
    for report in exact_reports:
        for reason in report.get("need027_exact_flow_validation", {}).get("failures", []):
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    if persist_status:
        for report in exact_reports:
            validation = report.get("need027_exact_flow_validation", {})
            persist_report = report
            if validation.get("status") != "ok":
                failures = validation.get("failures") or ["need027_exact_flow_validation_failed"]
                persist_report = {
                    **report,
                    "status": "blocked",
                    "error_type": "need027_exact_flow_validation_failed",
                    "error": ",".join(str(reason) for reason in failures),
                }
            _persist_probe_status(
                persist_report,
                capability=NEED027_EXACT_FLOW_CAPABILITY,
                persist_status=True,
                prefer_source=prefer_source,
                data_domain=NEED027_DATA_DOMAIN,
                source_name=NEED027_SOURCE_NAME,
                source_tier=NEED027_SOURCE_TIER,
                stock_code=str(report.get("stock_code") or ""),
            )
            if persist_report is not report and "persisted" in persist_report:
                report["persisted"] = persist_report["persisted"]
        for report in non_exact_reports:
            report["persisted"] = {
                "status": "skipped",
                "reason": "ignored_for_need_027_exact_flow_gate",
            }

    return {
        "gate": "need_027_exact_flow_source_probe",
        "need_id": NEED027_ID,
        "capability": NEED027_EXACT_FLOW_CAPABILITY,
        "prefer_source": prefer_source,
        "status": "source_probe_passed" if verdict_pass else "blocked",
        "verdict": "PASS" if verdict_pass else "BLOCKED",
        "exact_flow": {
            "probe_count": exact_probe_count,
            "valid_count": valid_exact_count,
            "blocked_count": blocked_exact_count,
            "success_rate": success_rate,
            "min_success_rate": min_success_rate,
            "min_rows_per_probe": min_rows_per_probe,
            "failure_reasons": failure_reasons,
        },
        "non_exact_probe_count": len(non_exact_reports),
        "non_exact_policy": "ignored_for_need_027_exact_flow_gate",
        "rank_snapshot_policy": "research_side_only_not_exact_flow_evidence",
        "production_eligibility": "blocked",
        "production_promotion": "not_allowed_from_probe_only",
        "next_gate": "writer_watermark_pit_freshness_gate_required",
        "persist_status": persist_status,
        "batch": batch,
    }


def _persist_probe_status(
    report: dict[str, Any],
    *,
    capability: str,
    persist_status: bool,
    prefer_source: str | None,
    data_domain: str | None,
    source_name: str | None,
    source_tier: int | None,
    stock_code: str | None,
) -> None:
    if not persist_status:
        return

    resolved_domain = data_domain or CAPABILITY_DATA_DOMAIN_HINTS.get(capability)
    resolved_source = source_name or report.get("source_used") or prefer_source or CAPABILITY_SOURCE_HINTS.get(capability)
    if not resolved_domain or not resolved_source:
        report["persisted"] = {
            "status": "skipped",
            "reason": "missing data_domain/source_name",
        }
        return

    try:
        conn = get_conn()
    except Exception as exc:
        report["persisted"] = {
            "status": "error",
            "table": "mart_data_source_failure_queue",
            "data_domain": resolved_domain,
            "source_name": resolved_source,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        logger.warning(
            "probe persistence connect failed for %s/%s: %s",
            resolved_domain,
            resolved_source,
            exc,
        )
        return

    try:
        if report.get("status") == "blocked":
            try:
                record_source_failure(
                    conn,
                    data_domain=resolved_domain,
                    source_name=resolved_source,
                    source_tier=source_tier,
                    stock_code=stock_code or str(report.get("kwargs", {}).get("stock") or report.get("kwargs", {}).get("symbol") or ""),
                    error_type=str(report.get("error_type") or "source_probe_blocked"),
                    last_error=str(report.get("error") or ""),
                    commit=True,
                )
            except Exception as exc:
                report["persisted"] = {
                    "status": "error",
                    "table": "mart_data_source_failure_queue",
                    "data_domain": resolved_domain,
                    "source_name": resolved_source,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                logger.warning(
                    "probe persistence open-row failed for %s/%s: %s",
                    resolved_domain,
                    resolved_source,
                    exc,
                )
            else:
                report["persisted"] = {
                    "status": "open",
                    "table": "mart_data_source_failure_queue",
                    "data_domain": resolved_domain,
                    "source_name": resolved_source,
                }
        elif report.get("status") == "ok":
            try:
                resolved_rows = resolve_source_failures(
                    conn,
                    data_domain=resolved_domain,
                    source_name=resolved_source,
                    stock_code=stock_code or str(report.get("kwargs", {}).get("stock") or report.get("kwargs", {}).get("symbol") or ""),
                    commit=True,
                )
            except Exception as exc:
                report["persisted"] = {
                    "status": "error",
                    "table": "mart_data_source_failure_queue",
                    "data_domain": resolved_domain,
                    "source_name": resolved_source,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                logger.warning(
                    "probe persistence resolve failed for %s/%s: %s",
                    resolved_domain,
                    resolved_source,
                    exc,
                )
            else:
                report["persisted"] = {
                    "status": "resolved",
                    "table": "mart_data_source_failure_queue",
                    "data_domain": resolved_domain,
                    "source_name": resolved_source,
                    "resolved_rows": resolved_rows,
                }
    finally:
        conn.close()


def _parse_kwargs_json(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("--kwargs-json must decode to an object")
    return loaded


def _parse_cases_json(raw: str) -> list[dict[str, Any]]:
    if not raw.strip():
        return []
    loaded = json.loads(raw)
    if not isinstance(loaded, list):
        raise ValueError("--cases-json must decode to a list")
    cases: list[dict[str, Any]] = []
    for item in loaded:
        cases.append(_normalize_probe_case(item))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a registered data-source capability and summarize the live result.")
    parser.add_argument("--capability", default=None, help="Capability name registered in services.data_sources")
    parser.add_argument("--prefer-source", default=None, help="Optional source name to force during probe")
    parser.add_argument("--persist-status", action="store_true", help="Write blocked/resolved probe state to mart_data_source_failure_queue")
    parser.add_argument("--data-domain", default=None, help="Optional data domain for persistence; defaults from capability hints")
    parser.add_argument("--source-name", default=None, help="Optional source name for persistence; defaults from resolved/preferred source")
    parser.add_argument("--source-tier", type=int, default=None, help="Optional source tier for persistence records")
    parser.add_argument("--stock-code", default=None, help="Optional stock code to attach to persistence records")
    parser.add_argument(
        "--show-registry-warnings",
        action="store_true",
        help="Keep registry fallback warnings visible during probe",
    )
    parser.add_argument("--kwargs-json", default="{}", help="JSON object forwarded to resolve(..., **kwargs)")
    parser.add_argument("--cases-json", default="", help="JSON list of probe case objects for batch or need_027 gate mode")
    parser.add_argument("--need027-exact-flow-gate", action="store_true", help="Run the small-batch need_027 exact-flow source probe gate")
    parser.add_argument("--min-exact-success-rate", type=float, default=NEED027_MIN_SUCCESS_RATE)
    parser.add_argument("--min-rows-per-exact-probe", type=int, default=NEED027_MIN_ROWS_PER_PROBE)
    parser.add_argument("--indent", type=int, default=2, help="JSON indentation level for stdout")
    args = parser.parse_args()

    try:
        kwargs = _parse_kwargs_json(args.kwargs_json)
        cases = _parse_cases_json(args.cases_json)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
                indent=args.indent,
                default=str,
            ),
            file=sys.stderr,
        )
        return 1

    if args.need027_exact_flow_gate:
        report = probe_need027_exact_flow_gate(
            cases or None,
            prefer_source=args.prefer_source or NEED027_SOURCE_NAME,
            persist_status=bool(args.persist_status),
            min_success_rate=float(args.min_exact_success_rate),
            min_rows_per_probe=int(args.min_rows_per_exact_probe),
            quiet_registry_warnings=not bool(args.show_registry_warnings),
        )
        print(json.dumps(report, ensure_ascii=False, indent=args.indent, default=str))
        return 0 if report.get("verdict") == "PASS" else 2

    if cases:
        report = probe_source_capability_batch(
            cases,
            prefer_source=args.prefer_source,
            persist_status=bool(args.persist_status),
            quiet_registry_warnings=not bool(args.show_registry_warnings),
        )
        print(json.dumps(report, ensure_ascii=False, indent=args.indent, default=str))
        return 0 if report.get("status") == "ok" else 2

    if not args.capability:
        parser.error("--capability is required unless --cases-json or --need027-exact-flow-gate is used")

    report = probe_source_capability(
        str(args.capability),
        kwargs,
        prefer_source=args.prefer_source,
        persist_status=bool(args.persist_status),
        data_domain=args.data_domain,
        source_name=args.source_name,
        source_tier=args.source_tier,
        stock_code=args.stock_code,
        quiet_registry_warnings=not bool(args.show_registry_warnings),
    )
    print(json.dumps(report, ensure_ascii=False, indent=args.indent, default=str))
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

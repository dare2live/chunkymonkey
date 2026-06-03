#!/usr/bin/env python3
"""Build the TDX-first data need coverage and source priority tables."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.data_sources import get_registry  # noqa: E402
from services.db import current_db_paths, get_conn  # noqa: E402
from services.duck_adapter import connect as duck_connect  # noqa: E402

logger = logging.getLogger("tdx_data_need_coverage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "tdx_data_need_coverage.yaml"
NEED_FIELDS = (
    "need_id",
    "need_name",
    "consumer",
    "grain",
    "pit_key",
    "freshness_sla",
    "evidence_status",
    "production_eligibility",
    "current_source",
    "tdxhub_capability",
    "tdx_coverage_level",
    "preferred_source",
    "fallback_source",
    "action",
    "notes",
)
PRIORITY_FIELDS = ("data_domain", "preferred_source", "fallback_1", "fallback_2", "reason")
REASSIGNMENT_FIELDS = (
    "table_name",
    "current_source",
    "proposed_primary_source",
    "fallback_source",
    "migration_required",
    "risk",
    "reason",
)
EVIDENCE_STATUSES = {"production", "proxy", "research", "unknown"}
PRODUCTION_ELIGIBILITIES = {"eligible", "blocked", "research_only", "proxy_only", "unknown"}
UNKNOWN_VALUES = {"unknown", "n/a", "none", "null"}
BLOCKED_ELIGIBILITIES = {"blocked", "unknown"}
FAILURE_QUEUE_TABLE = "mart_data_source_failure_queue"


DDL = """
CREATE TABLE IF NOT EXISTS mart_tdx_data_need_coverage (
    need_id TEXT PRIMARY KEY,
    need_name TEXT NOT NULL,
    consumer TEXT,
    grain TEXT NOT NULL,
    pit_key TEXT NOT NULL,
    freshness_sla TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    production_eligibility TEXT NOT NULL,
    current_source TEXT,
    tdxhub_capability TEXT,
    tdx_coverage_level TEXT,
    preferred_source TEXT NOT NULL,
    fallback_source TEXT,
    action TEXT NOT NULL,
    notes TEXT,
    built_at TEXT
);

ALTER TABLE mart_tdx_data_need_coverage ADD COLUMN IF NOT EXISTS grain TEXT;
ALTER TABLE mart_tdx_data_need_coverage ADD COLUMN IF NOT EXISTS pit_key TEXT;
ALTER TABLE mart_tdx_data_need_coverage ADD COLUMN IF NOT EXISTS freshness_sla TEXT;
ALTER TABLE mart_tdx_data_need_coverage ADD COLUMN IF NOT EXISTS evidence_status TEXT;
ALTER TABLE mart_tdx_data_need_coverage ADD COLUMN IF NOT EXISTS production_eligibility TEXT;

CREATE TABLE IF NOT EXISTS dim_data_source_priority (
    data_domain TEXT PRIMARY KEY,
    preferred_source TEXT NOT NULL,
    fallback_1 TEXT,
    fallback_2 TEXT,
    reason TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS mart_data_source_reassignment_proposal (
    table_name TEXT PRIMARY KEY,
    current_source TEXT,
    proposed_primary_source TEXT NOT NULL,
    fallback_source TEXT,
    migration_required BOOLEAN DEFAULT FALSE,
    risk TEXT,
    reason TEXT,
    built_at TEXT
);
"""


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        conn.execute(DDL)


def _load_yaml(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _as_clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        [table_name],
    ).fetchone()
    return row is not None


def _validate_need(item: dict[str, Any], index: int) -> None:
    for field in ("grain", "pit_key", "freshness_sla"):
        value = _as_clean_text(item.get(field))
        if not value:
            raise ValueError(f"needs[{index}] {field} cannot be empty")
        normalized = value.lower()
        if normalized in {"n/a", "none", "null"}:
            raise ValueError(f"needs[{index}] {field} must use unknown, not {value!r}")
        if normalized in UNKNOWN_VALUES and _as_clean_text(item.get("production_eligibility")) == "eligible":
            raise ValueError(f"needs[{index}] eligible need cannot use unknown {field}")

    evidence_status = _as_clean_text(item.get("evidence_status"))
    if evidence_status not in EVIDENCE_STATUSES:
        allowed = ", ".join(sorted(EVIDENCE_STATUSES))
        raise ValueError(f"needs[{index}] invalid evidence_status={evidence_status!r}; expected one of: {allowed}")

    production_eligibility = _as_clean_text(item.get("production_eligibility"))
    if production_eligibility not in PRODUCTION_ELIGIBILITIES:
        allowed = ", ".join(sorted(PRODUCTION_ELIGIBILITIES))
        raise ValueError(
            f"needs[{index}] invalid production_eligibility={production_eligibility!r}; "
            f"expected one of: {allowed}"
        )

    if production_eligibility == "eligible" and evidence_status != "production":
        raise ValueError(f"needs[{index}] eligible production need requires evidence_status=production")


def _rows(raw: dict[str, Any], key: str, fields: tuple[str, ...]) -> list[tuple[Any, ...]]:
    values = raw.get(key)
    if not isinstance(values, list):
        raise ValueError(f"{key} must be a list in {CONFIG_PATH.name}")

    rows = []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{key}[{index}] must be a mapping")
        missing = [field for field in fields if field not in item]
        if missing:
            raise ValueError(f"{key}[{index}] missing required fields: {', '.join(missing)}")
        if key == "needs":
            _validate_need(item, index)
        rows.append(tuple(item[field] for field in fields))
    return rows


def _need_record(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(NEED_FIELDS, row))


def _registered_source_names() -> list[str]:
    registry = get_registry()
    return sorted(src.name for src in registry.list_sources())


def _registered_sources_by_name() -> dict[str, Any]:
    registry = get_registry()
    return {src.name: src for src in registry.list_sources()}


# Source labels and registry source names are not strictly one-to-one:
# `miaoxiang` aliases to the `aif10` family, while `tdxhub_*` labels collapse to
# the canonical `tdxhub` registry source.
def _canonical_source_family(source_name: Any) -> str:
    text = _as_clean_text(source_name).lower()
    if not text or text in UNKNOWN_VALUES:
        return ""
    if text.startswith("tdxhub"):
        return "tdxhub"
    if text.startswith("miaoxiang") or text.startswith("aif10"):
        return "aif10"
    if text.startswith("akshare"):
        return "akshare"
    return text


def _source_name_registered(source_name: Any, registered_names: set[str]) -> bool:
    text = _as_clean_text(source_name).lower()
    return bool(text) and text not in UNKNOWN_VALUES and text in registered_names


def _source_capabilities(source_name: Any, registered_sources: dict[str, Any]) -> list[str]:
    text = _as_clean_text(source_name).lower()
    if not text or text in UNKNOWN_VALUES:
        return []
    candidates = [text]
    family = _canonical_source_family(text)
    if family and family not in candidates:
        candidates.append(family)
    selected_caps: list[str] | None = None
    for candidate in candidates:
        src = registered_sources.get(candidate)
        if src is not None:
            selected_caps = [cap.name for cap in src.capabilities]
            break
    if selected_caps is None:
        return []
    return sorted(selected_caps)


def _source_registration_summary(
    preferred_source: Any,
    fallback_source: Any,
    *,
    registered_source_names: list[str],
    registered_source_name_set: set[str],
    registered_sources: dict[str, Any],
) -> dict[str, Any]:
    preferred_family = _canonical_source_family(preferred_source)
    fallback_family = _canonical_source_family(fallback_source)
    preferred_capabilities = _source_capabilities(preferred_source, registered_sources)
    fallback_capabilities = _source_capabilities(fallback_source, registered_sources)
    return {
        "registered_source_names": registered_source_names,
        "preferred_source_registered": _source_name_registered(preferred_source, registered_source_name_set),
        "preferred_source_family": preferred_family or None,
        "preferred_source_family_registered": bool(
            preferred_family and preferred_family in registered_source_name_set
        ),
        "fallback_source_registered": _source_name_registered(fallback_source, registered_source_name_set),
        "fallback_source_family": fallback_family or None,
        "fallback_source_family_registered": bool(
            fallback_family and fallback_family in registered_source_name_set
        ),
        "preferred_source_capabilities": preferred_capabilities,
        "fallback_source_capabilities": fallback_capabilities,
        "preferred_source_supports_individual_fund_flow": "individual_fund_flow" in preferred_capabilities,
        "fallback_source_supports_individual_fund_flow": "individual_fund_flow" in fallback_capabilities,
    }


def _blocked_need_summary(
    record: dict[str, Any],
    *,
    evidence_status: str,
    eligibility: str,
    source_registration: dict[str, Any],
    failure_queue_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "need_id": record.get("need_id"),
        "need_name": record.get("need_name"),
        "consumer": record.get("consumer"),
        "current_source": record.get("current_source"),
        "tdxhub_capability": record.get("tdxhub_capability"),
        "pit_key": record.get("pit_key"),
        "evidence_status": evidence_status,
        "production_eligibility": eligibility,
        "preferred_source": record.get("preferred_source"),
        "fallback_source": record.get("fallback_source"),
        "source_registration": source_registration,
        "failure_queue_snapshot": failure_queue_snapshot,
        "action": record.get("action"),
        "notes": record.get("notes"),
    }


def _failure_queue_snapshot(
    conn: Any,
    *,
    source_name: str | None,
    domain_like: str | None = None,
) -> dict[str, Any] | None:
    if not source_name or not _table_exists(conn, FAILURE_QUEUE_TABLE):
        return None

    clauses = ["lower(source_name) = lower(?)"]
    params: list[Any] = [source_name]
    if domain_like:
        clauses.append("lower(data_domain) LIKE lower(?)")
        params.append(f"%{domain_like}%")

    cursor = conn.execute(
        f"""
        SELECT failure_id, data_domain, source_name, source_tier, stock_code,
               error_type, last_error, status, first_seen_at, last_seen_at,
               retry_after, occurrence_count, resolved_at
          FROM {FAILURE_QUEUE_TABLE}
         WHERE {' AND '.join(clauses)}
         ORDER BY coalesce(last_seen_at, first_seen_at, resolved_at) DESC, failure_id DESC
        """,
        params,
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    columns = [desc[0] for desc in cursor.description or []]
    records = [dict(zip(columns, row)) for row in rows]
    status_counts: dict[str, int] = {}
    for record in records:
        status = _as_clean_text(record.get("status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    latest_open_row = next((record for record in records if _as_clean_text(record.get("status")) == "open"), None)
    latest_resolved_row = next(
        (record for record in records if _as_clean_text(record.get("status")) == "resolved"),
        None,
    )
    return {
        "source_name": source_name,
        "domain_like": domain_like,
        "row_count": len(records),
        "status_counts": status_counts,
        "latest_open_row": latest_open_row,
        "latest_resolved_row": latest_resolved_row,
        "sample_rows": records[:5],
    }


def _summarize_need_gaps(conn: Any, needs: list[tuple[Any, ...]]) -> dict[str, Any]:
    records = [_need_record(row) for row in needs]
    eligibility_counts = {}
    blocked_needs = []
    registered_source_names = _registered_source_names()
    registered_source_name_set = set(registered_source_names)
    registered_sources = _registered_sources_by_name()

    for record in records:
        eligibility = _as_clean_text(record.get("production_eligibility"))
        evidence_status = _as_clean_text(record.get("evidence_status"))
        preferred_source = record.get("preferred_source")
        fallback_source = record.get("fallback_source")
        eligibility_counts[eligibility] = eligibility_counts.get(eligibility, 0) + 1
        if eligibility in BLOCKED_ELIGIBILITIES or evidence_status == "unknown":
            failure_queue_snapshot = _failure_queue_snapshot(
                conn,
                source_name=_as_clean_text(preferred_source) or None,
                domain_like="fund_flow" if record.get("need_id") == "need_027" else None,
            )
            source_registration = _source_registration_summary(
                preferred_source,
                fallback_source,
                registered_source_names=registered_source_names,
                registered_source_name_set=registered_source_name_set,
                registered_sources=registered_sources,
            )
            blocked_needs.append(
                _blocked_need_summary(
                    record,
                    evidence_status=evidence_status,
                    eligibility=eligibility,
                    source_registration=source_registration,
                    failure_queue_snapshot=failure_queue_snapshot,
                )
            )

    return {
        "need_count": len(records),
        "eligibility_counts": eligibility_counts,
        "blocked_need_count": len(blocked_needs),
        "blocked_needs": blocked_needs,
        "registered_source_names": registered_source_names,
    }


def _resolve_input_paths(raw_paths: Any) -> list[Path]:
    if not isinstance(raw_paths, list):
        raise ValueError(f"input_paths must be a list in {CONFIG_PATH.name}")
    paths = []
    for index, raw_path in enumerate(raw_paths, start=1):
        if not isinstance(raw_path, str):
            raise ValueError(f"input_paths[{index}] must be a string")
        path = Path(raw_path)
        paths.append(path if path.is_absolute() else ROOT / path)
    return paths


def load_tdx_data_need_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or CONFIG_PATH
    raw = _load_yaml(path)
    return {
        "config_path": str(path),
        "input_paths": _resolve_input_paths(raw.get("input_paths")),
        "needs": _rows(raw, "needs", NEED_FIELDS),
        "priorities": _rows(raw, "priorities", PRIORITY_FIELDS),
        "reassignments": _rows(raw, "reassignments", REASSIGNMENT_FIELDS),
    }


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_input_inventory(input_paths: list[Path]) -> list[dict[str, Any]]:
    inventory = []
    for path in input_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        inventory.append(
            {
                "path": _relative_path(path),
                "bytes": len(text.encode("utf-8")),
                "lines": text.count("\n") + 1,
            }
        )
    return inventory


def _delete_obsolete_rows(conn: Any, table_name: str, key_column: str, current_keys: list[Any]) -> None:
    if not current_keys:
        conn.execute(f"DELETE FROM {table_name}")
        return
    placeholders = ", ".join(["?"] * len(current_keys))
    conn.execute(
        f"DELETE FROM {table_name} WHERE {key_column} NOT IN ({placeholders})",
        current_keys,
    )


def audit_tdx_data_need_coverage(conn: Any, config_path: Path | None = None) -> dict[str, Any]:
    ensure_tables(conn)
    config = load_tdx_data_need_config(config_path)
    needs = config["needs"]
    priorities = config["priorities"]
    reassignments = config["reassignments"]
    need_gap_summary = _summarize_need_gaps(conn, needs)
    input_inventory = _read_input_inventory(config["input_paths"])
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn.execute("BEGIN TRANSACTION")
    try:
        _delete_obsolete_rows(
            conn,
            "mart_tdx_data_need_coverage",
            "need_id",
            [row[0] for row in needs],
        )
        _delete_obsolete_rows(
            conn,
            "dim_data_source_priority",
            "data_domain",
            [row[0] for row in priorities],
        )
        _delete_obsolete_rows(
            conn,
            "mart_data_source_reassignment_proposal",
            "table_name",
            [row[0] for row in reassignments],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_data_need_coverage
            (need_id, need_name, consumer, grain, pit_key, freshness_sla,
             evidence_status, production_eligibility, current_source, tdxhub_capability,
             tdx_coverage_level, preferred_source, fallback_source, action, notes, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*row, built_at) for row in needs],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO dim_data_source_priority
            (data_domain, preferred_source, fallback_1, fallback_2, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(*row, built_at) for row in priorities],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_data_source_reassignment_proposal
            (table_name, current_source, proposed_primary_source, fallback_source,
             migration_required, risk, reason, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*row, built_at) for row in reassignments],
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_exc:
            logger.warning("tdx data need coverage rollback failed: %s", rollback_exc)
        raise
    return {
        "coverage_rows": len(needs),
        "priority_rows": len(priorities),
        "reassignment_rows": len(reassignments),
        "need_gap_summary": need_gap_summary,
        "config_path": config["config_path"],
        "input_files_read": input_inventory,
        "built_at": built_at,
        "materialized": True,
    }


def summarize_tdx_data_need_coverage(conn: Any, config_path: Path | None = None) -> dict[str, Any]:
    config = load_tdx_data_need_config(config_path)
    needs = config["needs"]
    priorities = config["priorities"]
    reassignments = config["reassignments"]
    return {
        "coverage_rows": len(needs),
        "priority_rows": len(priorities),
        "reassignment_rows": len(reassignments),
        "need_gap_summary": _summarize_need_gaps(conn, needs),
        "config_path": config["config_path"],
        "input_files_read": _read_input_inventory(config["input_paths"]),
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "materialized": False,
    }


def get_read_only_conn(timeout: int = 60) -> Any:
    db_dir, db_path = current_db_paths()
    db_dir.mkdir(parents=True, exist_ok=True)
    return duck_connect(str(db_path), read_only=True, timeout=timeout)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Read config and failure-queue evidence without materializing coverage tables.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Emit a JSON summary for automation or a text log for interactive use.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    conn = get_read_only_conn() if args.summary_only else get_conn()
    try:
        result = (
            summarize_tdx_data_need_coverage(conn)
            if args.summary_only
            else audit_tdx_data_need_coverage(conn)
        )
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        else:
            logger.info("tdx data need coverage: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Central pricing and label policy helpers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing_label_policy.yaml"

DDL = """
CREATE TABLE IF NOT EXISTS mart_pricing_label_policy (
    policy_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    follow_entry_price_mode TEXT NOT NULL,
    follow_entry_ref_price_mode TEXT NOT NULL,
    transaction_cost_bps DOUBLE,
    policy_json TEXT NOT NULL,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_pricing_label_policy_gate (
    gate_run_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    blockers_json TEXT,
    warnings_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_pricing_label_data_readiness_gate (
    gate_run_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    gate_scope TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    feature_tables_json TEXT,
    required_labels_json TEXT,
    blockers_json TEXT,
    warnings_json TEXT,
    evidence_json TEXT,
    built_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mart_follow_return_label_build (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    price_adjustment TEXT NOT NULL,
    transaction_cost_bps DOUBLE NOT NULL,
    horizons_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    row_count BIGINT,
    label_non_null_json TEXT,
    label_coverage_json TEXT,
    min_date TEXT,
    max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table)
);

CREATE TABLE IF NOT EXISTS mart_follow_return_label_quality (
    run_id TEXT NOT NULL,
    feature_table TEXT NOT NULL,
    label_name TEXT NOT NULL,
    horizon_days INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    event_calc_version TEXT NOT NULL,
    row_count BIGINT NOT NULL,
    non_null_count BIGINT NOT NULL,
    null_count BIGINT NOT NULL,
    immature_null_count BIGINT NOT NULL,
    mature_null_count BIGINT NOT NULL,
    missing_signal_kline_count BIGINT NOT NULL,
    missing_entry_price_count BIGINT NOT NULL,
    missing_exit_price_count BIGINT NOT NULL,
    unclassified_null_count BIGINT NOT NULL,
    min_date TEXT,
    max_date TEXT,
    stock_max_date_min TEXT,
    stock_max_date_max TEXT,
    global_market_max_date TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_table, label_name)
);
"""


@dataclass(frozen=True)
class PricingLabelPolicy:
    policy_id: str
    version: int
    event_calc_version: str
    price_adjustment: str
    unknown_notice_time_execution: str
    same_day_execution_allowed: bool
    institution_cost_primary: str
    institution_cost_fallbacks: tuple[str, ...]
    follow_entry_primary: str
    follow_entry_fallbacks: tuple[str, ...]
    follow_volume_unit_guard: bool
    follow_volume_hand_adjustment_allowed: bool
    follow_exit_default: str
    follow_exit_needs_definition: bool
    alpha_forward_label_current: str
    alpha_forward_label_needs_migration_review: bool
    transaction_cost_bps: float
    transaction_cost_meaning: str
    stale_on_policy_change: bool
    require_policy_id_in_manifest: bool
    definition_sections: dict[str, Any]

    @property
    def follow_entry_price_mode(self) -> str:
        return self.follow_entry_primary

    @property
    def follow_entry_ref_price_mode(self) -> str:
        fallback = self.follow_entry_fallbacks[0] if self.follow_entry_fallbacks else "none"
        return f"{self.follow_entry_primary}_fallback_{fallback.replace('entry_day_', '').replace('_qfq', '')}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "event_calc_version": self.event_calc_version,
            "price_adjustment": self.price_adjustment,
            "announcement_policy": {
                "unknown_notice_time_execution": self.unknown_notice_time_execution,
                "same_day_execution_allowed": self.same_day_execution_allowed,
            },
            "institution_cost": {
                "primary": self.institution_cost_primary,
                "fallbacks": list(self.institution_cost_fallbacks),
            },
            "follow_entry": {
                "primary": self.follow_entry_primary,
                "fallbacks": list(self.follow_entry_fallbacks),
                "volume_unit_guard": self.follow_volume_unit_guard,
                "volume_hand_adjustment_allowed": self.follow_volume_hand_adjustment_allowed,
                "ref_price_mode": self.follow_entry_ref_price_mode,
            },
            "follow_exit": {
                "default": self.follow_exit_default,
                "needs_definition": self.follow_exit_needs_definition,
            },
            "alpha_forward_label": {
                "current": self.alpha_forward_label_current,
                "needs_migration_review": self.alpha_forward_label_needs_migration_review,
            },
            "portfolio_transaction_cost": {
                "default_bps": self.transaction_cost_bps,
                "meaning": self.transaction_cost_meaning,
            },
            "production_rules": {
                "stale_on_policy_change": self.stale_on_policy_change,
                "require_policy_id_in_manifest": self.require_policy_id_in_manifest,
            },
            "definition_sections": self.definition_sections,
        }

    def policy_hash(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def training_blockers(self, *, scope: str = "model_training") -> list[str]:
        blockers: list[str] = []
        if self.follow_exit_needs_definition:
            blockers.append("follow_exit_price_policy_unfrozen")
        if self.alpha_forward_label_needs_migration_review and scope in {
            "model_training",
            "optuna_search",
            "champion_gate",
            "full_research",
        }:
            blockers.append("alpha_forward_label_policy_unreviewed")
        if not self.institution_cost_primary:
            blockers.append("institution_cost_policy_missing")
        if not self.follow_entry_primary:
            blockers.append("follow_entry_policy_missing")
        if self.transaction_cost_meaning != "execution_friction_only_not_entry_price":
            blockers.append("transaction_cost_meaning_ambiguous")
        if self.price_adjustment != "qfq":
            blockers.append("price_adjustment_not_qfq")
        if self.require_policy_id_in_manifest is not True:
            blockers.append("manifest_policy_id_not_required")
        required_sections = (
            "signal_policy",
            "data_source_policy",
            "holding_period",
            "follow_return_label",
            "feature_policy",
            "data_quality_policy",
            "performance_policy",
            "ranking_policy",
            "portfolio_construction",
            "risk_policy",
            "benchmark",
            "evaluation_metrics",
            "validation_split",
            "model_training",
            "model_family_policy",
            "optuna",
            "explainability",
            "promotion_gate",
            "champion_policy",
            "reproducibility_policy",
        )
        for section in required_sections:
            if section not in self.definition_sections:
                blockers.append(f"{section}_definition_missing")
        return blockers

    def training_warnings(self) -> list[str]:
        warnings: list[str] = []
        signal_policy = self.definition_sections.get("signal_policy") or {}
        signal_generation_time = str(signal_policy.get("signal_generation_time") or "")
        if self.same_day_execution_allowed and "after_market_close" in signal_generation_time:
            warnings.append("same_day_execution_allowed_requires_intraday_notice_time")
        if not self.follow_volume_unit_guard:
            warnings.append("follow_vwap_volume_unit_guard_disabled")
        if not self.follow_volume_hand_adjustment_allowed:
            warnings.append("follow_vwap_hand_volume_adjustment_disabled")
        return warnings


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover - local runtime has PyYAML.
        raise RuntimeError("PyYAML is required to load pricing_label_policy.yaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return loaded if isinstance(loaded, dict) else {}


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return (str(value),)


def load_pricing_label_policy(path: str | Path | None = None) -> PricingLabelPolicy:
    raw = _load_yaml(Path(path) if path is not None else CONFIG_PATH)
    announcement = raw.get("announcement_policy") or {}
    institution = raw.get("institution_cost") or {}
    follow_entry = raw.get("follow_entry") or {}
    follow_exit = raw.get("follow_exit") or {}
    alpha_label = raw.get("alpha_forward_label") or {}
    tx_cost = raw.get("portfolio_transaction_cost") or {}
    rules = raw.get("production_rules") or {}
    definition_keys = (
        "announcement_policy",
        "signal_policy",
        "data_source_policy",
        "institution_cost",
        "follow_entry",
        "follow_exit",
        "holding_period",
        "alpha_forward_label",
        "follow_return_label",
        "portfolio_transaction_cost",
        "premium",
        "corporate_action_policy",
        "tradability",
        "universe",
        "feature_policy",
        "data_quality_policy",
        "performance_policy",
        "ranking_policy",
        "portfolio_construction",
        "risk_policy",
        "benchmark",
        "evaluation_metrics",
        "validation_split",
        "model_training",
        "model_family_policy",
        "optuna",
        "explainability",
        "promotion_gate",
        "champion_policy",
        "reproducibility_policy",
        "frontend_policy",
        "production_rules",
    )
    definition_sections = {
        key: raw.get(key)
        for key in definition_keys
        if raw.get(key) is not None
    }
    return PricingLabelPolicy(
        policy_id=str(raw.get("policy_id") or "pricing_label_policy_vwap_follow_v1"),
        version=int(raw.get("version") or 1),
        event_calc_version=str(raw.get("event_calc_version") or "v3_qfq_vwap_entry_dual_cost"),
        price_adjustment=str(raw.get("price_adjustment") or "qfq"),
        unknown_notice_time_execution=str(
            announcement.get("unknown_notice_time_execution") or "signal_day_vwap_when_signal_emitted"
        ),
        same_day_execution_allowed=bool(announcement.get("same_day_execution_allowed", False)),
        institution_cost_primary=str(institution.get("primary") or "report_period_daily_vwap_qfq"),
        institution_cost_fallbacks=_as_tuple(institution.get("fallbacks")),
        follow_entry_primary=str(follow_entry.get("primary") or "entry_day_vwap_qfq"),
        follow_entry_fallbacks=_as_tuple(follow_entry.get("fallbacks") or ("entry_day_open_qfq",)),
        follow_volume_unit_guard=bool(follow_entry.get("volume_unit_guard", True)),
        follow_volume_hand_adjustment_allowed=bool(follow_entry.get("volume_hand_adjustment_allowed", True)),
        follow_exit_default=str(follow_exit.get("default") or "horizon_end_close_qfq"),
        follow_exit_needs_definition=bool(follow_exit.get("needs_definition", True)),
        alpha_forward_label_current=str(
            alpha_label.get("current") or "signal_day_close_to_horizon_end_close_qfq"
        ),
        alpha_forward_label_needs_migration_review=bool(alpha_label.get("needs_migration_review", True)),
        transaction_cost_bps=float(tx_cost.get("default_bps", 10.0)),
        transaction_cost_meaning=str(tx_cost.get("meaning") or "execution_friction_only_not_entry_price"),
        stale_on_policy_change=bool(rules.get("stale_on_policy_change", True)),
        require_policy_id_in_manifest=bool(rules.get("require_policy_id_in_manifest", True)),
        definition_sections=definition_sections,
    )


def ensure_pricing_policy_table(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
        return
    for stmt in DDL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def record_pricing_label_policy(conn: Any, policy: PricingLabelPolicy | None = None) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    ensure_pricing_policy_table(conn)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    payload = policy.to_dict()
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_policy (
            policy_id, version, policy_hash, event_calc_version,
            follow_entry_price_mode, follow_entry_ref_price_mode,
            transaction_cost_bps, policy_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            policy.policy_id,
            policy.version,
            policy.policy_hash(),
            policy.event_calc_version,
            policy.follow_entry_price_mode,
            policy.follow_entry_ref_price_mode,
            policy.transaction_cost_bps,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return payload


def record_pricing_label_policy_gate(
    conn: Any,
    *,
    policy: PricingLabelPolicy | None = None,
    gate_run_id: str | None = None,
    gate_scope: str = "model_training",
) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    ensure_pricing_policy_table(conn)
    record_pricing_label_policy(conn, policy)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    gate_run_id = gate_run_id or f"pricing_label_policy_gate_{gate_scope}_{built_at.replace(':', '').replace('-', '')}"
    blockers = policy.training_blockers(scope=gate_scope)
    warnings = policy.training_warnings()
    gate_status = "pass" if not blockers else "blocked"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_policy_gate (
            gate_run_id, policy_id, policy_hash, gate_scope, gate_status,
            blockers_json, warnings_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            policy.policy_id,
            policy.policy_hash(),
            gate_scope,
            gate_status,
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(warnings, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return {
        "gate_run_id": gate_run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "gate_scope": gate_scope,
        "gate_status": gate_status,
        "blockers": blockers,
        "warnings": warnings,
    }


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
          FROM information_schema.tables
         WHERE table_name = ?
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _row_value(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        return row[index]


def _table_columns(conn: Any, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {
        str(_row_value(row, "column_name", 0))
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _count_rows(conn: Any, table_name: str) -> int:
    if not _table_exists(conn, table_name):
        return 0
    row = conn.execute(f'SELECT COUNT(*) AS n FROM "{table_name}"').fetchone()
    return int(_row_value(row, "n", 0) or 0) if row else 0


def _required_follow_labels(policy: PricingLabelPolicy) -> list[str]:
    follow = policy.definition_sections.get("follow_return_label") or {}
    labels = follow.get("horizon_candidate_labels") or []
    return [str(label) for label in labels]


def _missing_labels_by_table(conn: Any, feature_tables: list[str], labels: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table_name in feature_tables:
        columns = _table_columns(conn, table_name)
        if not columns:
            out[table_name] = labels[:]
            continue
        missing = [label for label in labels if label not in columns]
        if missing:
            out[table_name] = missing
    return out


def _event_return_evidence(conn: Any, policy: PricingLabelPolicy) -> dict[str, Any]:
    table_name = "fact_institution_event"
    evidence: dict[str, Any] = {"table_exists": _table_exists(conn, table_name)}
    if not evidence["table_exists"]:
        evidence.update({"event_rows_with_notice": 0, "stale_rows": 0})
        return evidence
    columns = _table_columns(conn, table_name)
    if not {"notice_date", "calc_version", "calc_ref_price_mode"} <= columns:
        evidence.update({"event_rows_with_notice": 0, "stale_rows": 0, "missing_columns": True})
        return evidence
    event_row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM fact_institution_event
         WHERE notice_date IS NOT NULL AND notice_date != ''
        """
    ).fetchone()
    event_rows = _row_value(event_row, "n", 0)
    stale_row = conn.execute(
        """
        SELECT COUNT(*) AS n
          FROM fact_institution_event
         WHERE notice_date IS NOT NULL AND notice_date != ''
           AND (
               COALESCE(calc_version, '') != ?
            OR COALESCE(calc_ref_price_mode, '') != ?
           )
        """,
        (policy.event_calc_version, policy.follow_entry_ref_price_mode),
    ).fetchone()
    stale_rows = _row_value(stale_row, "n", 0)
    evidence.update({"event_rows_with_notice": int(event_rows or 0), "stale_rows": int(stale_rows or 0)})
    if {"price_entry", "price_entry_status"} <= columns:
        mature_missing_row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM fact_institution_event
             WHERE notice_date IS NOT NULL AND notice_date != ''
               AND COALESCE(calc_version, '') = ?
               AND (price_entry IS NULL OR price_entry = 0)
               AND COALESCE(price_entry_status, '') NOT IN ('future_signal_waiting')
            """,
            (policy.event_calc_version,),
        ).fetchone()
        future_unpriced_row = conn.execute(
            """
            SELECT COUNT(*) AS n
              FROM fact_institution_event
             WHERE notice_date IS NOT NULL AND notice_date != ''
               AND COALESCE(calc_version, '') = ?
               AND (price_entry IS NULL OR price_entry = 0)
               AND COALESCE(price_entry_status, '') = 'future_signal_waiting'
            """,
            (policy.event_calc_version,),
        ).fetchone()
        evidence["mature_missing_price_entry_rows"] = int(_row_value(mature_missing_row, "n", 0) or 0)
        evidence["future_unpriced_rows"] = int(_row_value(future_unpriced_row, "n", 0) or 0)
    return evidence


def _existing_artifact_evidence(conn: Any, table_name: str, policy_hash: str) -> dict[str, Any]:
    evidence = {"table_exists": _table_exists(conn, table_name), "rows": 0, "missing_policy_hash": False, "stale_rows": 0}
    if not evidence["table_exists"]:
        return evidence
    evidence["rows"] = _count_rows(conn, table_name)
    if evidence["rows"] == 0:
        return evidence
    columns = _table_columns(conn, table_name)
    if "pricing_policy_hash" not in columns:
        evidence["missing_policy_hash"] = True
        evidence["stale_rows"] = evidence["rows"]
        return evidence
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
          FROM "{table_name}"
         WHERE COALESCE(pricing_policy_hash, '') != ?
        """,
        (policy_hash,),
    ).fetchone()
    evidence["stale_rows"] = int(_row_value(row, "n", 0) or 0) if row else 0
    return evidence


def _latest_follow_label_build(
    conn: Any,
    table_name: str,
    *,
    policy: PricingLabelPolicy,
    required_labels: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "table_exists": _table_exists(conn, "mart_follow_return_label_build"),
        "build_exists": False,
        "policy_hash_match": False,
        "event_calc_version_match": False,
        "missing_labels_in_build": required_labels[:],
        "zero_non_null_labels": [],
        "run_id": None,
        "built_at": None,
    }
    if not evidence["table_exists"]:
        return evidence
    row = conn.execute(
        """
        SELECT *
          FROM mart_follow_return_label_build
         WHERE feature_table = ?
         ORDER BY built_at DESC
         LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    if not row:
        return evidence
    evidence["build_exists"] = True
    row_run_id = _row_value(row, "run_id", 0)
    row_policy_hash = _row_value(row, "policy_hash", 3)
    row_event_calc_version = _row_value(row, "event_calc_version", 4)
    row_count = int(_row_value(row, "row_count", 9) or 0)
    evidence["run_id"] = row_run_id
    evidence["built_at"] = _row_value(row, "built_at", 14)
    evidence["policy_hash"] = row_policy_hash
    evidence["event_calc_version"] = row_event_calc_version
    evidence["row_count"] = row_count
    evidence["policy_hash_match"] = row_policy_hash == policy.policy_hash()
    evidence["event_calc_version_match"] = row_event_calc_version == policy.event_calc_version
    try:
        labels = json.loads(_row_value(row, "labels_json", 8) or "[]")
    except Exception:
        labels = []
    try:
        non_null = json.loads(_row_value(row, "label_non_null_json", 10) or "{}")
    except Exception:
        non_null = {}
    evidence["missing_labels_in_build"] = [label for label in required_labels if label not in labels]
    evidence["zero_non_null_labels"] = [
        label for label in required_labels
        if row_count > 0 and int(non_null.get(label) or 0) <= 0
    ]
    evidence["label_non_null"] = non_null
    return evidence


def _latest_follow_label_quality(
    conn: Any,
    table_name: str,
    *,
    run_id: str | None,
    policy: PricingLabelPolicy,
    required_labels: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "table_exists": _table_exists(conn, "mart_follow_return_label_quality"),
        "quality_exists": False,
        "run_id": run_id,
        "missing_labels_in_quality": required_labels[:],
        "policy_hash_mismatch_labels": [],
        "event_calc_version_mismatch_labels": [],
        "mature_null_labels": [],
        "missing_signal_kline_labels": [],
        "missing_entry_price_labels": [],
        "missing_exit_price_labels": [],
        "unclassified_null_labels": [],
        "labels": {},
    }
    if not evidence["table_exists"] or not run_id:
        return evidence
    rows = conn.execute(
        """
        SELECT label_name,
               horizon_days,
               policy_hash,
               event_calc_version,
               row_count,
               non_null_count,
               null_count,
               immature_null_count,
               mature_null_count,
               missing_signal_kline_count,
               missing_entry_price_count,
               missing_exit_price_count,
               unclassified_null_count,
               global_market_max_date,
               built_at
          FROM mart_follow_return_label_quality
         WHERE feature_table = ?
           AND run_id = ?
        """,
        (table_name, run_id),
    ).fetchall()
    if not rows:
        return evidence
    evidence["quality_exists"] = True
    seen: set[str] = set()
    for row in rows:
        label = str(_row_value(row, "label_name", 0))
        seen.add(label)
        detail = {
            "horizon_days": int(_row_value(row, "horizon_days", 1) or 0),
            "policy_hash": _row_value(row, "policy_hash", 2),
            "event_calc_version": _row_value(row, "event_calc_version", 3),
            "row_count": int(_row_value(row, "row_count", 4) or 0),
            "non_null_count": int(_row_value(row, "non_null_count", 5) or 0),
            "null_count": int(_row_value(row, "null_count", 6) or 0),
            "immature_null_count": int(_row_value(row, "immature_null_count", 7) or 0),
            "mature_null_count": int(_row_value(row, "mature_null_count", 8) or 0),
            "missing_signal_kline_count": int(_row_value(row, "missing_signal_kline_count", 9) or 0),
            "missing_entry_price_count": int(_row_value(row, "missing_entry_price_count", 10) or 0),
            "missing_exit_price_count": int(_row_value(row, "missing_exit_price_count", 11) or 0),
            "unclassified_null_count": int(_row_value(row, "unclassified_null_count", 12) or 0),
            "global_market_max_date": _row_value(row, "global_market_max_date", 13),
            "built_at": _row_value(row, "built_at", 14),
        }
        evidence["labels"][label] = detail
        if detail["policy_hash"] != policy.policy_hash():
            evidence["policy_hash_mismatch_labels"].append(label)
        if detail["event_calc_version"] != policy.event_calc_version:
            evidence["event_calc_version_mismatch_labels"].append(label)
        if detail["mature_null_count"] > 0:
            evidence["mature_null_labels"].append(label)
        if detail["missing_signal_kline_count"] > 0:
            evidence["missing_signal_kline_labels"].append(label)
        if detail["missing_entry_price_count"] > 0:
            evidence["missing_entry_price_labels"].append(label)
        if detail["missing_exit_price_count"] > 0:
            evidence["missing_exit_price_labels"].append(label)
        if detail["unclassified_null_count"] > 0:
            evidence["unclassified_null_labels"].append(label)
    evidence["missing_labels_in_quality"] = [label for label in required_labels if label not in seen]
    return evidence


def record_pricing_label_data_readiness_gate(
    conn: Any,
    *,
    policy: PricingLabelPolicy | None = None,
    gate_run_id: str | None = None,
    gate_scope: str = "model_training",
    feature_tables: list[str] | None = None,
) -> dict[str, Any]:
    policy = policy or load_pricing_label_policy()
    feature_tables = feature_tables or ["fact_feature_panel", "fact_feature_panel_candidate"]
    ensure_pricing_policy_table(conn)
    record_pricing_label_policy(conn, policy)
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    gate_run_id = gate_run_id or f"pricing_label_data_readiness_{gate_scope}_{built_at.replace(':', '').replace('-', '')}"
    required_labels = _required_follow_labels(policy)
    blockers = policy.training_blockers(scope=gate_scope)
    warnings = policy.training_warnings()
    evidence: dict[str, Any] = {
        "definition_gate_blockers": blockers[:],
        "feature_tables": {},
        "event_returns": _event_return_evidence(conn, policy),
        "artifacts": {},
    }

    missing_labels = _missing_labels_by_table(conn, feature_tables, required_labels)
    for table_name in feature_tables:
        columns = _table_columns(conn, table_name)
        evidence["feature_tables"][table_name] = {
            "exists": bool(columns),
            "rows": _count_rows(conn, table_name),
            "missing_required_follow_labels": missing_labels.get(table_name, []),
        }
    if missing_labels:
        blockers.append("follow_return_labels_missing")

    for table_name in feature_tables:
        if missing_labels.get(table_name):
            continue
        build_evidence = _latest_follow_label_build(
            conn,
            table_name,
            policy=policy,
            required_labels=required_labels,
        )
        evidence["feature_tables"][table_name]["follow_label_build"] = build_evidence
        if not build_evidence["table_exists"] or not build_evidence["build_exists"]:
            blockers.append(f"{table_name}_follow_label_build_missing")
            continue
        if not build_evidence["policy_hash_match"]:
            blockers.append(f"{table_name}_follow_label_build_policy_hash_mismatch")
        if not build_evidence["event_calc_version_match"]:
            blockers.append(f"{table_name}_follow_label_build_calc_version_mismatch")
        if build_evidence["missing_labels_in_build"]:
            blockers.append(f"{table_name}_follow_label_build_missing_required_labels")
        if build_evidence["zero_non_null_labels"]:
            blockers.append(f"{table_name}_follow_label_build_zero_non_null_labels")
        quality_evidence = _latest_follow_label_quality(
            conn,
            table_name,
            run_id=build_evidence.get("run_id"),
            policy=policy,
            required_labels=required_labels,
        )
        evidence["feature_tables"][table_name]["follow_label_quality"] = quality_evidence
        if not quality_evidence["table_exists"] or not quality_evidence["quality_exists"]:
            blockers.append(f"{table_name}_follow_label_quality_missing")
            continue
        if quality_evidence["missing_labels_in_quality"]:
            blockers.append(f"{table_name}_follow_label_quality_missing_required_labels")
        if quality_evidence["policy_hash_mismatch_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_policy_hash_mismatch")
        if quality_evidence["event_calc_version_mismatch_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_calc_version_mismatch")
        if quality_evidence["mature_null_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_mature_nulls")
        if quality_evidence["missing_signal_kline_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_missing_signal_kline")
        if quality_evidence["missing_entry_price_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_missing_entry_price")
        if quality_evidence["missing_exit_price_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_missing_exit_price")
        if quality_evidence["unclassified_null_labels"]:
            blockers.append(f"{table_name}_follow_label_quality_unclassified_nulls")

    event_evidence = evidence["event_returns"]
    if event_evidence.get("missing_columns"):
        blockers.append("event_return_calc_columns_missing")
    if int(event_evidence.get("stale_rows") or 0) > 0:
        blockers.append("event_returns_stale_for_pricing_policy")
    if int(event_evidence.get("mature_missing_price_entry_rows") or 0) > 0:
        blockers.append("event_returns_mature_missing_price_entry")
    if int(event_evidence.get("event_rows_with_notice") or 0) == 0:
        warnings.append("no_institution_event_rows_with_notice_in_current_db")

    artifact_blocking_by_scope = {
        "fact_institution_follow_backtest": True,
        "mart_institution_profile": True,
        "mart_multidim_model": gate_scope in {"champion_gate", "promotion_gate", "full_research", "production"},
    }
    for table_name, is_blocking in artifact_blocking_by_scope.items():
        artifact = _existing_artifact_evidence(conn, table_name, policy.policy_hash())
        evidence["artifacts"][table_name] = artifact
        if artifact["rows"] and artifact["missing_policy_hash"]:
            message = f"{table_name}_missing_pricing_policy_hash"
            (blockers if is_blocking else warnings).append(message)
        elif artifact["stale_rows"]:
            message = f"{table_name}_stale_for_pricing_policy"
            (blockers if is_blocking else warnings).append(message)

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    gate_status = "pass" if not blockers else "blocked"
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_pricing_label_data_readiness_gate (
            gate_run_id, policy_id, policy_hash, gate_scope, gate_status,
            feature_tables_json, required_labels_json, blockers_json,
            warnings_json, evidence_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gate_run_id,
            policy.policy_id,
            policy.policy_hash(),
            gate_scope,
            gate_status,
            json.dumps(feature_tables, ensure_ascii=False, sort_keys=True),
            json.dumps(required_labels, ensure_ascii=False, sort_keys=True),
            json.dumps(blockers, ensure_ascii=False, sort_keys=True),
            json.dumps(warnings, ensure_ascii=False, sort_keys=True),
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            built_at,
        ),
    )
    return {
        "gate_run_id": gate_run_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash(),
        "gate_scope": gate_scope,
        "gate_status": gate_status,
        "required_labels": required_labels,
        "blockers": blockers,
        "warnings": warnings,
        "evidence": evidence,
    }

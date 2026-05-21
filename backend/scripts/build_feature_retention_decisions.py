#!/usr/bin/env python3
"""Build auditable keep/drop/watch decisions for candidate TDX features."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from scripts.build_candidate_feature_panel import CANDIDATE_FEATURE_SET_ID, CANDIDATE_FEATURES  # noqa: E402
from scripts.run_feature_group_ablation import (  # noqa: E402
    FEATURE_GROUPS,
    _candidate_features_for_set,
    _feature_group_map_for_set,
)

logger = logging.getLogger("feature_retention_decisions")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_retention_decision (
    decision_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT,
    decision TEXT NOT NULL,
    primary_reason TEXT,
    coverage_pct DOUBLE,
    pit_violation_rows INTEGER,
    mean_rank_ic DOUBLE,
    fold_same_sign_rate DOUBLE,
    group_ablation_delta DOUBLE,
    max_corr_with_kept_feature DOUBLE,
    corr_peer_feature TEXT,
    drift_status TEXT,
    notes TEXT,
    built_at TEXT,
    PRIMARY KEY (decision_run_id, feature_set_id, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_tdx_gpcw_auto_retention_decision (
    decision_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_family TEXT,
    decision TEXT NOT NULL,
    primary_reason TEXT,
    coverage_pct DOUBLE,
    pit_violation_rows INTEGER,
    mean_rank_ic DOUBLE,
    fold_same_sign_rate DOUBLE,
    notes TEXT,
    built_at TEXT,
    PRIMARY KEY (decision_run_id, feature_set_id, feature_name)
);

CREATE TABLE IF NOT EXISTS mart_feature_candidate_coverage (
    audit_run_id TEXT NOT NULL,
    feature_set_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    label_name TEXT NOT NULL,
    total_rows BIGINT,
    non_null_rows BIGINT,
    coverage_pct DOUBLE,
    pit_violation_rows INTEGER,
    production_ready BOOLEAN,
    source_tier_distribution_json TEXT,
    reason TEXT,
    built_at TEXT,
    PRIMARY KEY (audit_run_id, feature_set_id, feature_name, label_name)
);
"""


EVENT_LIKE_FEATURES = {
    "common_holder_network_count",
    "fund_holding_shares_tdx_f10",
    "fund_holding_float_a_ratio_tdx_f10",
    "fund_holding_market_value_tdx_f10",
    "top10_concentration_change",
}

PRODUCTION_MIN_COVERAGE_PCT = 60.0


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_get(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def ensure_tables(conn: Any) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for stmt in DDL.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)


def _feature_group(feature: str) -> str:
    for group, features in FEATURE_GROUPS.items():
        if feature in features:
            return group
    return "other"


def _latest_model_run(conn: Any, feature_set_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
        FROM mart_model_selection_run
        WHERE feature_set_id = ?
        ORDER BY built_at DESC
        LIMIT 1
        """,
        (feature_set_id,),
    ).fetchone()
    return row[0] if row else None


def _latest_ablation_run(conn: Any, feature_set_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT run_id
        FROM mart_feature_group_ablation
        WHERE feature_set_id = ?
        ORDER BY built_at DESC
        LIMIT 1
        """,
        (feature_set_id,),
    ).fetchone()
    return row[0] if row else None


def _corr_with_kept_by_feature(
    conn: Any,
    feature_set_id: str,
    features: list[str],
    kept: list[str],
) -> dict[str, tuple[float | None, str | None]]:
    aliases: dict[str, tuple[str, str]] = {}
    select_parts = []
    for feature in features:
        for peer in kept:
            if peer == feature:
                continue
            alias = f"c_{len(aliases)}"
            aliases[alias] = (feature, peer)
            select_parts.append(f"corr({_quote_ident(feature)}, {_quote_ident(peer)}) AS {_quote_ident(alias)}")
    if not select_parts:
        return {feature: (None, None) for feature in features}

    row = conn.execute(
        f"""
        SELECT {", ".join(select_parts)}
        FROM fact_feature_panel_candidate
        WHERE feature_set_id = ?
        """,
        (feature_set_id,),
    ).fetchone()

    best: dict[str, tuple[float | None, str | None]] = {feature: (None, None) for feature in features}
    if not row:
        return best
    for idx, (alias, (feature, peer)) in enumerate(aliases.items()):
        raw = _row_get(row, alias, idx)
        corr = abs(float(raw)) if raw is not None else None
        best_corr, _ = best[feature]
        if corr is not None and (best_corr is None or corr > best_corr):
            best[feature] = (corr, peer)
    return best


def _table_columns(conn: Any, table: str) -> set[str]:
    return {row[0] for row in conn.execute(f"DESCRIBE {table}").fetchall()}


def build_feature_candidate_coverage(
    conn: Any,
    *,
    feature_set_id: str,
    audit_run_id: str,
    label_name: str = "forward_ret_20d",
    min_coverage_pct: float = PRODUCTION_MIN_COVERAGE_PCT,
) -> dict[str, dict[str, Any]]:
    ensure_tables(conn)
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    table_cols = _table_columns(conn, "fact_feature_panel_candidate")
    candidate_features = _candidate_features_for_set(conn, feature_set_id)
    where = ["feature_set_id = ?"]
    params: list[Any] = [feature_set_id]
    if label_name in table_cols:
        where.append(f"{label_name} IS NOT NULL")
    where_sql = " AND ".join(where)

    if feature_set_id.startswith("tdx_gpcw_auto"):
        pit_rows = {
            row["feature_name"]: int(row["violations"] or 0)
            for row in conn.execute(
                """
                SELECT feature_name, SUM(violation_rows) AS violations
                FROM mart_tdx_gpcw_auto_pit_audit
                WHERE feature_set_id = ?
                GROUP BY feature_name
                """,
                (feature_set_id.replace("_pit", ""),),
            ).fetchall()
        }
    else:
        pit_rows = {
            row["feature_name"]: int(row["violations"] or 0)
            for row in conn.execute(
                """
                SELECT feature_name, SUM(violation_rows) AS violations
                FROM mart_feature_pit_audit
                WHERE feature_set_id = ?
                GROUP BY feature_name
                """,
                (feature_set_id,),
            ).fetchall()
        }

    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM fact_feature_panel_candidate WHERE {where_sql}",
        params,
    ).fetchone()["n"]
    source_tier_dist = None
    if "source_tier" in table_cols:
        rows = conn.execute(
            f"""
            SELECT source_tier, COUNT(*) AS n
              FROM fact_feature_panel_candidate
             WHERE {where_sql}
             GROUP BY source_tier
             ORDER BY source_tier
            """,
            params,
        ).fetchall()
        source_tier_dist = json.dumps({str(r["source_tier"]): int(r["n"] or 0) for r in rows}, ensure_ascii=False)

    out: dict[str, dict[str, Any]] = {}
    rows_to_write = []
    if candidate_features:
        coverage_select = [
            f"COUNT({_quote_ident(feature)}) AS {_quote_ident(feature)}"
            for feature in candidate_features
        ]
        coverage_count_row = conn.execute(
            f"""
            SELECT {", ".join(coverage_select)}
              FROM fact_feature_panel_candidate
             WHERE {where_sql}
            """,
            params,
        ).fetchone()
    else:
        coverage_count_row = None
    for idx, feature in enumerate(candidate_features):
        non_null = _row_get(coverage_count_row, feature, idx) if coverage_count_row else 0
        coverage_pct = float(non_null or 0) * 100.0 / float(total or 1)
        pit_violations = int(pit_rows.get(feature, 0))
        production_ready = coverage_pct >= min_coverage_pct and pit_violations == 0
        if pit_violations:
            reason = "pit_violation"
        elif coverage_pct < min_coverage_pct:
            reason = "coverage_below_production_threshold"
        else:
            reason = "production_ready"
        out[feature] = {
            "coverage_pct": coverage_pct,
            "pit_violation_rows": pit_violations,
            "production_ready": production_ready,
            "reason": reason,
        }
        rows_to_write.append((
            audit_run_id,
            feature_set_id,
            feature,
            label_name,
            int(total or 0),
            int(non_null or 0),
            coverage_pct,
            pit_violations,
            production_ready,
            source_tier_dist,
            reason,
            built_at,
        ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_candidate_coverage
        (audit_run_id, feature_set_id, feature_name, label_name, total_rows,
         non_null_rows, coverage_pct, pit_violation_rows, production_ready,
         source_tier_distribution_json, reason, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_write,
    )
    return out


def build_feature_retention_decisions(
    conn: Any,
    *,
    feature_set_id: str = CANDIDATE_FEATURE_SET_ID,
    decision_run_id: str = "feature_retention",
) -> dict[str, Any]:
    ensure_tables(conn)
    built_at = datetime.utcnow().isoformat(timespec="seconds")
    model_run_id = _latest_model_run(conn, feature_set_id)
    ablation_run_id = _latest_ablation_run(conn, feature_set_id)
    if not model_run_id:
        raise RuntimeError(f"no model selection run for feature_set_id={feature_set_id}")
    if not ablation_run_id:
        raise RuntimeError(f"no group ablation run for feature_set_id={feature_set_id}")

    score_rows = {
        row["feature_name"]: row
        for row in conn.execute(
            """
            SELECT feature_name, coverage_pct, rank_ic, fold_same_sign_rate,
                   selected, rejection_reason
            FROM mart_feature_candidate_score
            WHERE run_id = ?
            """,
            (model_run_id,),
        ).fetchall()
    }
    candidate_features = _candidate_features_for_set(conn, feature_set_id)
    feature_group_map = _feature_group_map_for_set(conn, feature_set_id)
    coverage_audit_run_id = f"{decision_run_id}_coverage"
    coverage_rows = build_feature_candidate_coverage(
        conn,
        feature_set_id=feature_set_id,
        audit_run_id=coverage_audit_run_id,
    )
    wf_rows = {
        row["feature_name"]: row
        for row in conn.execute(
            """
            SELECT feature_name,
                   AVG(rank_ic) AS mean_rank_ic,
                   AVG(CASE WHEN rank_ic > 0 THEN 1.0 ELSE 0.0 END) AS same_sign_rate
            FROM mart_candidate_walkforward_eval
            WHERE feature_set_id = ? AND label_name = 'forward_ret_20d'
            GROUP BY feature_name
            """,
            (feature_set_id,),
        ).fetchall()
    }
    if feature_set_id.startswith("tdx_gpcw_auto"):
        pit_rows = {
            row["feature_name"]: int(row["violations"] or 0)
            for row in conn.execute(
                """
                SELECT feature_name, SUM(violation_rows) AS violations
                FROM mart_tdx_gpcw_auto_pit_audit
                WHERE feature_set_id = ?
                GROUP BY feature_name
                """,
                (feature_set_id.replace("_pit", ""),),
            ).fetchall()
        }
    else:
        pit_rows = {
            row["feature_name"]: int(row["violations"] or 0)
            for row in conn.execute(
                """
                SELECT feature_name, SUM(violation_rows) AS violations
                FROM mart_feature_pit_audit
                WHERE feature_set_id = ?
                GROUP BY feature_name
                """,
                (feature_set_id,),
            ).fetchall()
        }
    ablation = {
        row["group_name"]: row["rank_ic_delta"]
        for row in conn.execute(
            """
            SELECT group_name, rank_ic_delta
            FROM mart_feature_group_ablation
            WHERE run_id = ?
            """,
            (ablation_run_id,),
        ).fetchall()
    }

    kept_features = [f for f, row in score_rows.items() if row["selected"]]
    corr_with_kept = (
        {}
        if feature_set_id.startswith("tdx_gpcw_auto")
        else _corr_with_kept_by_feature(conn, feature_set_id, candidate_features, kept_features)
    )
    output_rows = []
    for feature in candidate_features:
        stat = score_rows.get(feature)
        wf = wf_rows.get(feature)
        group = feature_group_map.get(feature, _feature_group(feature))
        coverage = float(stat["coverage_pct"]) if stat and stat["coverage_pct"] is not None else 0.0
        coverage_audit = coverage_rows.get(feature, {})
        production_ready = bool(coverage_audit.get("production_ready"))
        audited_coverage = coverage_audit.get("coverage_pct")
        if audited_coverage is not None:
            coverage = float(audited_coverage)
        mean_rank_ic = (
            float(wf["mean_rank_ic"])
            if wf and wf["mean_rank_ic"] is not None
            else float(stat["rank_ic"] or 0.0) if stat else None
        )
        same_sign = (
            float(wf["same_sign_rate"])
            if wf and wf["same_sign_rate"] is not None
            else float(stat["fold_same_sign_rate"] or 0.0) if stat else None
        )
        pit_violations = int(pit_rows.get(feature, 0))
        group_delta = ablation.get(group)
        if feature_set_id.startswith("tdx_gpcw_auto"):
            max_corr, corr_peer = None, None
        else:
            max_corr, corr_peer = corr_with_kept.get(feature, (None, None))

        decision = "watch"
        reason = "monitor"
        if pit_violations > 0:
            decision, reason = "drop", "pit_violation"
        elif not production_ready and stat and stat["selected"]:
            decision, reason = "watch", coverage_audit.get("reason") or "research_only_low_coverage"
        elif max_corr is not None and max_corr > 0.95 and feature not in kept_features:
            decision, reason = "drop", f"high_corr:{corr_peer}"
        elif coverage < 30.0 and feature not in EVENT_LIKE_FEATURES:
            decision, reason = "drop", "low_coverage"
        elif feature in EVENT_LIKE_FEATURES and coverage < 30.0:
            decision, reason = "watch", "sparse_event_feature"
        elif same_sign is not None and same_sign < 0.60:
            decision, reason = "watch", "unstable_walkforward_sign"
        elif mean_rank_ic is None or abs(mean_rank_ic) < 0.005:
            decision, reason = "drop", "low_walkforward_rank_ic"
        elif stat and stat["selected"] and production_ready and (group_delta is None or group_delta >= 0):
            decision, reason = "keep", "selected_stable_positive_group"
        elif stat and not stat["selected"]:
            decision, reason = "drop", stat["rejection_reason"] or "not_selected"

        output_rows.append([
            decision_run_id,
            feature_set_id,
            feature,
            group,
            decision,
            reason,
            coverage,
            pit_violations,
            mean_rank_ic,
            same_sign,
            group_delta,
            max_corr,
            corr_peer,
            "not_evaluated",
            (
                f"model_run={model_run_id}; ablation_run={ablation_run_id}; "
                f"coverage_audit_run={coverage_audit_run_id}; "
                f"production_ready={production_ready}"
            ),
            built_at,
        ])

    if output_rows and not any(row[4] == "keep" for row in output_rows):
        ranked = sorted(
            output_rows,
            key=lambda row: (
                row[5] != "pit_violation",
                abs(float(row[8] or 0.0)),
                float(row[6] or 0.0),
            ),
            reverse=True,
        )
        promoted = 0
        for row in ranked:
            feature_coverage = coverage_rows.get(row[2], {})
            if row[7] == 0 and row[8] is not None and feature_coverage.get("production_ready") and promoted < 5:
                row[4] = "keep"
                row[5] = "selected_best_available"
                promoted += 1
        if promoted == 0:
            logger.warning("no production-keep features for feature_set_id=%s", feature_set_id)

    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_retention_decision
        (decision_run_id, feature_set_id, feature_name, feature_group, decision,
         primary_reason, coverage_pct, pit_violation_rows, mean_rank_ic,
         fold_same_sign_rate, group_ablation_delta, max_corr_with_kept_feature,
         corr_peer_feature, drift_status, notes, built_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        output_rows,
    )
    if feature_set_id.startswith("tdx_gpcw_auto"):
        conn.executemany(
            """
            INSERT OR REPLACE INTO mart_tdx_gpcw_auto_retention_decision
            (decision_run_id, feature_set_id, feature_name, feature_family,
             decision, primary_reason, coverage_pct, pit_violation_rows,
             mean_rank_ic, fold_same_sign_rate, notes, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0], row[1], row[2], row[3], row[4], row[5],
                    row[6], row[7], row[8], row[9], row[14], row[15],
                )
                for row in output_rows
            ],
        )
    from services.schema_versions import record_actual_version
    record_actual_version(conn, "mart_feature_retention_decision")
    record_actual_version(conn, "mart_feature_candidate_coverage")
    conn.commit()
    counts = {}
    for row in output_rows:
        counts[row[4]] = counts.get(row[4], 0) + 1
    return {
        "decision_run_id": decision_run_id,
        "feature_set_id": feature_set_id,
        "features": len(output_rows),
        "counts": counts,
        "built_at": built_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-set-id", default=CANDIDATE_FEATURE_SET_ID)
    parser.add_argument("--decision-run-id", default="feature_retention")
    args = parser.parse_args()

    conn = get_conn()
    try:
        result = build_feature_retention_decisions(
            conn,
            feature_set_id=args.feature_set_id,
            decision_run_id=args.decision_run_id,
        )
        logger.info("feature retention decisions: %s", result)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

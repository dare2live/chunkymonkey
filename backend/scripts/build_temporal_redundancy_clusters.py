#!/usr/bin/env python3
"""Build redundancy clusters for temporal research features."""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


DDL = """
CREATE TABLE IF NOT EXISTS mart_feature_redundancy_pair (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    feature_a TEXT NOT NULL,
    feature_b TEXT NOT NULL,
    corr DOUBLE,
    abs_corr DOUBLE,
    redundant BOOLEAN,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_a, feature_b)
);
CREATE INDEX IF NOT EXISTS idx_feature_redundancy_pair_source
    ON mart_feature_redundancy_pair(source_run_id, abs_corr);

CREATE TABLE IF NOT EXISTS mart_feature_cluster_redundancy (
    run_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    representative_feature TEXT NOT NULL,
    representative_score DOUBLE,
    abs_corr_to_representative DOUBLE,
    max_abs_corr_in_cluster DOUBLE,
    cluster_size INTEGER,
    redundancy_status TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_cluster_redundancy_source
    ON mart_feature_cluster_redundancy(source_run_id, cluster_id, redundancy_status);
"""


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


def _columns(conn: Any, table_name: str) -> set[str]:
    return {
        str(row["column_name"])
        for row in conn.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = ?
            """,
            (table_name,),
        ).fetchall()
    }


def _safe_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _finite(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def latest_temporal_run_id(conn: Any) -> str | None:
    if not _table_exists(conn, "mart_temporal_research_panel_quality"):
        return None
    row = conn.execute(
        """
        SELECT run_id
          FROM mart_temporal_research_panel_quality
         ORDER BY built_at DESC NULLS LAST, run_id DESC
         LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def _features_from_quality(conn: Any, source_run_id: str) -> list[str]:
    row = conn.execute(
        """
        SELECT features_json
          FROM mart_temporal_research_panel_quality
         WHERE run_id = ?
         LIMIT 1
        """,
        (source_run_id,),
    ).fetchone()
    if not row:
        return []
    features = _safe_json(row["features_json"]) or []
    return [str(feature) for feature in features]


def _feature_scores(conn: Any, source_run_id: str, features: list[str]) -> dict[str, float]:
    if not features or not _table_exists(conn, "mart_feature_temporal_relevance"):
        return {feature: 0.0 for feature in features}
    rows = conn.execute(
        """
        SELECT feature_name,
               MAX(ABS(COALESCE(rank_ic, 0))) AS max_abs_rank_ic,
               MAX(ABS(COALESCE(directional_spread, 0))) AS max_abs_spread,
               MAX(COALESCE(stability_score, 0)) AS max_stability
          FROM mart_feature_temporal_relevance
         WHERE run_id = ?
           AND feature_name IN ({})
         GROUP BY feature_name
        """.format(", ".join(["?"] * len(features))),
        (source_run_id, *features),
    ).fetchall()
    scores = {feature: 0.0 for feature in features}
    for row in rows:
        scores[str(row["feature_name"])] = (
            _finite(row["max_abs_rank_ic"])
            + _finite(row["max_abs_spread"])
            + 0.25 * _finite(row["max_stability"])
        )
    return scores


def build_temporal_redundancy_clusters(
    conn: Any,
    *,
    source_run_id: str | None = None,
    run_id: str | None = None,
    features: list[str] | None = None,
    corr_threshold: float = 0.85,
) -> dict[str, Any]:
    ensure_tables(conn)
    source_run_id = source_run_id or latest_temporal_run_id(conn)
    if not source_run_id:
        raise RuntimeError("source_run_id is required and no temporal run exists")
    run_id = run_id or f"temporal_redundancy_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    started_at = utc_now_iso()
    built_at = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
    if not _table_exists(conn, "mart_temporal_research_panel"):
        raise RuntimeError("mart_temporal_research_panel is required")
    panel_cols = _columns(conn, "mart_temporal_research_panel")
    requested = features or _features_from_quality(conn, source_run_id)
    usable_features = [feature for feature in requested if feature in panel_cols]
    if len(usable_features) < 2:
        raise RuntimeError("at least two usable features are required for redundancy clustering")

    conn.execute("DELETE FROM mart_feature_redundancy_pair WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_feature_cluster_redundancy WHERE run_id = ?", (run_id,))
    pair_exprs = []
    pair_keys = []
    for idx, feature_a in enumerate(usable_features):
        for feature_b in usable_features[idx + 1 :]:
            alias = f"corr__{len(pair_keys)}"
            pair_exprs.append(
                f"CORR({_quote_ident(feature_a)}, {_quote_ident(feature_b)}) AS {_quote_ident(alias)}"
            )
            pair_keys.append((alias, feature_a, feature_b))
    corr_row = conn.execute(
        f"""
        SELECT {", ".join(pair_exprs)}
          FROM mart_temporal_research_panel
         WHERE run_id = ?
        """,
        (source_run_id,),
    ).fetchone()
    uf = UnionFind(usable_features)
    pair_rows = []
    threshold = abs(float(corr_threshold))
    corr_by_pair: dict[tuple[str, str], float] = {}
    for alias, feature_a, feature_b in pair_keys:
        corr = _finite(corr_row[alias]) if corr_row else 0.0
        abs_corr = abs(corr)
        redundant = abs_corr >= threshold
        if redundant:
            uf.union(feature_a, feature_b)
        corr_by_pair[(feature_a, feature_b)] = abs_corr
        corr_by_pair[(feature_b, feature_a)] = abs_corr
        pair_rows.append(
            (
                run_id,
                source_run_id,
                feature_a,
                feature_b,
                corr,
                abs_corr,
                redundant,
                built_at,
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_redundancy_pair (
            run_id, source_run_id, feature_a, feature_b, corr, abs_corr,
            redundant, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        pair_rows,
    )

    grouped: dict[str, list[str]] = {}
    for feature in usable_features:
        grouped.setdefault(uf.find(feature), []).append(feature)
    scores = _feature_scores(conn, source_run_id, usable_features)
    cluster_rows = []
    cluster_summaries = []
    for cluster_index, members in enumerate(sorted(grouped.values(), key=lambda items: sorted(items)[0]), start=1):
        members = sorted(members)
        representative = max(members, key=lambda feature: (scores.get(feature, 0.0), -members.index(feature), feature))
        cluster_id = f"cluster_{cluster_index:03d}"
        max_abs_corr = 0.0
        for idx, feature_a in enumerate(members):
            for feature_b in members[idx + 1 :]:
                max_abs_corr = max(max_abs_corr, corr_by_pair.get((feature_a, feature_b), 0.0))
        for feature in members:
            abs_corr_to_rep = 1.0 if feature == representative else corr_by_pair.get((feature, representative), 0.0)
            redundancy_status = "representative" if feature == representative else "redundant"
            cluster_rows.append(
                (
                    run_id,
                    source_run_id,
                    cluster_id,
                    feature,
                    representative,
                    scores.get(representative, 0.0),
                    abs_corr_to_rep,
                    max_abs_corr,
                    len(members),
                    redundancy_status,
                    built_at,
                )
            )
        cluster_summaries.append(
            {
                "cluster_id": cluster_id,
                "representative_feature": representative,
                "cluster_size": len(members),
                "members": members,
                "max_abs_corr_in_cluster": max_abs_corr,
            }
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_feature_cluster_redundancy (
            run_id, source_run_id, cluster_id, feature_name,
            representative_feature, representative_score, abs_corr_to_representative,
            max_abs_corr_in_cluster, cluster_size, redundancy_status, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cluster_rows,
    )
    for table in ("mart_feature_redundancy_pair", "mart_feature_cluster_redundancy"):
        record_actual_version(conn, table)
    redundant_feature_count = sum(1 for row in cluster_rows if row[9] == "redundant")
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_temporal_redundancy_clusters",
        status="success",
        started_at=started_at,
        ended_at=utc_now_iso(),
        commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
        input_tables=[
            "mart_temporal_research_panel",
            "mart_temporal_research_panel_quality",
            "mart_feature_temporal_relevance",
        ],
        output_tables=[
            "mart_feature_redundancy_pair",
            "mart_feature_cluster_redundancy",
        ],
        feature_group="temporal_synergy_research",
        perf_summary={
            "source_run_id": source_run_id,
            "feature_count": len(usable_features),
            "pair_count": len(pair_rows),
            "cluster_count": len(cluster_summaries),
            "redundant_feature_count": redundant_feature_count,
            "corr_threshold": threshold,
            "clusters": cluster_summaries[:20],
        },
    )
    conn.commit()
    return {
        "run_id": run_id,
        "source_run_id": source_run_id,
        "feature_count": len(usable_features),
        "pair_count": len(pair_rows),
        "cluster_count": len(cluster_summaries),
        "redundant_feature_count": redundant_feature_count,
    }


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--features", default=None)
    parser.add_argument("--corr-threshold", type=float, default=0.85)
    args = parser.parse_args()
    with get_conn() as conn:
        result = build_temporal_redundancy_clusters(
            conn,
            source_run_id=args.source_run_id,
            run_id=args.run_id,
            features=_parse_csv(args.features),
            corr_threshold=args.corr_threshold,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

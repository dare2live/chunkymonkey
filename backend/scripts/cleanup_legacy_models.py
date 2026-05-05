#!/usr/bin/env python3
"""Delete legacy model records and local pkl files after safety checks.

Policy:
- Never delete lifecycle champion or challenger models.
- Delete lifecycle retired models.
- Delete unregistered one-off cleanup/research models only when they are not
  the current cleanup_full challenger.
- Remove dependent rows first, then mart_multidim_model/lifecycle, then pkl.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402


PROTECTED_STATUSES = {"champion", "challenger"}
DEPENDENT_MODEL_ID_TABLES = [
    "mart_daily_recommendation",
    "mart_daily_recommendation_risk",
    "mart_multidim_prediction",
    "mart_model_walkforward_fold",
    "mart_model_portfolio_curve",
    "mart_feature_drift",
    "mart_prediction_outcome",
]


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        ).fetchall()
    }


def _model_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "data" / "multidim_models"


def lifecycle_statuses(conn) -> dict[str, str]:
    if not _table_exists(conn, "mart_model_lifecycle"):
        return {}
    return {
        row["model_id"]: row["status"]
        for row in conn.execute("SELECT model_id, status FROM mart_model_lifecycle").fetchall()
    }


def candidate_models(conn) -> list[dict]:
    statuses = lifecycle_statuses(conn)
    rows = conn.execute(
        "SELECT model_id, created_at, n_features FROM mart_multidim_model ORDER BY created_at"
    ).fetchall()
    candidates = []
    for row in rows:
        model_id = row["model_id"]
        status = statuses.get(model_id)
        reason = None
        if status == "retired":
            reason = "lifecycle_retired"
        elif status in PROTECTED_STATUSES:
            reason = None
        elif model_id.startswith("cleanup_recent_"):
            reason = "unregistered_cleanup_recent"
        elif model_id.startswith("multidim_v1_"):
            reason = "unregistered_legacy_v1"
        if reason:
            candidates.append({
                "model_id": model_id,
                "status": status or "unregistered",
                "created_at": row["created_at"],
                "n_features": row["n_features"],
                "reason": reason,
                "pkl_exists": (_model_dir() / f"{model_id}.pkl").exists(),
            })
    return candidates


def reference_counts(conn, model_id: str) -> dict[str, int]:
    refs: dict[str, int] = {}
    for table in DEPENDENT_MODEL_ID_TABLES:
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "model_id" not in cols:
            continue
        count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE model_id = ?", (model_id,)).fetchone()[0]
        if count:
            refs[f"{table}.model_id"] = int(count)
    if _table_exists(conn, "mart_tdx_keep_promotion_gate"):
        cols = _columns(conn, "mart_tdx_keep_promotion_gate")
        for col in ("challenger_model_id", "champion_model_id"):
            if col in cols:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM mart_tdx_keep_promotion_gate WHERE {col} = ?",
                    (model_id,),
                ).fetchone()[0]
                if count:
                    refs[f"mart_tdx_keep_promotion_gate.{col}"] = int(count)
    return refs


def delete_model(conn, model_id: str) -> dict[str, int | bool]:
    deleted: dict[str, int | bool] = {}
    if _table_exists(conn, "mart_model_walkforward_prediction") and _table_exists(conn, "mart_model_walkforward_fold"):
        run_ids = [
            row["run_id"]
            for row in conn.execute(
                "SELECT DISTINCT run_id FROM mart_model_walkforward_fold WHERE model_id = ?",
                (model_id,),
            ).fetchall()
        ]
        for run_id in run_ids:
            conn.execute("DELETE FROM mart_model_walkforward_prediction WHERE run_id = ?", (run_id,))
        if run_ids:
            deleted["mart_model_walkforward_prediction.run_id"] = len(run_ids)

    for table in DEPENDENT_MODEL_ID_TABLES:
        if not _table_exists(conn, table) or "model_id" not in _columns(conn, table):
            continue
        before = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE model_id = ?", (model_id,)).fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE model_id = ?", (model_id,))
        if before:
            deleted[f"{table}.model_id"] = int(before)

    if _table_exists(conn, "mart_tdx_keep_promotion_gate"):
        cols = _columns(conn, "mart_tdx_keep_promotion_gate")
        where = []
        params = []
        for col in ("challenger_model_id", "champion_model_id"):
            if col in cols:
                where.append(f"{col} = ?")
                params.append(model_id)
        if where:
            count = conn.execute(
                f"SELECT COUNT(*) FROM mart_tdx_keep_promotion_gate WHERE {' OR '.join(where)}",
                tuple(params),
            ).fetchone()[0]
            conn.execute(
                f"DELETE FROM mart_tdx_keep_promotion_gate WHERE {' OR '.join(where)}",
                tuple(params),
            )
            if count:
                deleted["mart_tdx_keep_promotion_gate"] = int(count)

    if _table_exists(conn, "mart_model_lifecycle"):
        count = conn.execute(
            "SELECT COUNT(*) FROM mart_model_lifecycle WHERE model_id = ?",
            (model_id,),
        ).fetchone()[0]
        conn.execute("DELETE FROM mart_model_lifecycle WHERE model_id = ?", (model_id,))
        if count:
            deleted["mart_model_lifecycle"] = int(count)

    count = conn.execute(
        "SELECT COUNT(*) FROM mart_multidim_model WHERE model_id = ?",
        (model_id,),
    ).fetchone()[0]
    conn.execute("DELETE FROM mart_multidim_model WHERE model_id = ?", (model_id,))
    deleted["mart_multidim_model"] = int(count)

    pkl_path = _model_dir() / f"{model_id}.pkl"
    if pkl_path.exists():
        pkl_path.unlink()
        deleted["pkl_deleted"] = True
    else:
        deleted["pkl_deleted"] = False
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    started_at = utc_now_iso()

    conn = get_conn()
    try:
        statuses = lifecycle_statuses(conn)
        protected = {model_id for model_id, status in statuses.items() if status in PROTECTED_STATUSES}
        candidates = candidate_models(conn)
        report = []
        for candidate in candidates:
            model_id = candidate["model_id"]
            if model_id in protected:
                raise RuntimeError(f"protected model selected unexpectedly: {model_id}")
            refs = reference_counts(conn, model_id)
            entry = {**candidate, "refs": refs}
            if args.execute:
                entry["deleted"] = delete_model(conn, model_id)
            report.append(entry)
        if args.execute:
            record_pipeline_run(
                conn,
                run_id=f"cleanup_legacy_models_{utc_now_iso().replace(':', '').replace('-', '')[:15]}",
                pipeline_name="cleanup_legacy_models",
                status="success",
                started_at=started_at,
                ended_at=utc_now_iso(),
                commit_sha=git_commit_sha(Path(__file__).resolve().parent.parent.parent),
                input_tables=[
                    "mart_model_lifecycle",
                    "mart_multidim_model",
                    *DEPENDENT_MODEL_ID_TABLES,
                ],
                output_tables=["mart_multidim_model", "mart_model_lifecycle"],
                blockers=[],
                perf_summary={
                    "deleted_models": [entry["model_id"] for entry in report],
                    "count": len(report),
                    "details": report,
                },
            )
        else:
            conn.commit()
        print(json.dumps({"execute": args.execute, "candidates": report}, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

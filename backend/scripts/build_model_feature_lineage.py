#!/usr/bin/env python3
"""Build mart_model_feature_lineage for a model's feature_cols_json."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn
from services.ml_lifecycle.registry import select_default_model_id
from services.model_feature_lineage import write_model_feature_lineage
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso
from services.schema_versions import record_actual_version


logger = logging.getLogger("model_feature_lineage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

REPO = Path(__file__).resolve().parent.parent.parent


def latest_model_id(conn) -> str:
    model_id, _fallback = select_default_model_id(conn)
    if model_id:
        return model_id
    row = conn.execute("SELECT model_id FROM mart_multidim_model ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("mart_multidim_model has no records")
    return row[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", default=None)
    args = parser.parse_args()

    started_at = utc_now_iso()
    conn = get_conn()
    try:
        model_id = args.model_id or latest_model_id(conn)
        result = write_model_feature_lineage(conn, model_id=model_id)
        try:
            record_actual_version(conn, "mart_model_feature_lineage")
        except Exception as exc:
            logger.warning("record schema version failed: %s", exc)
        ended_at = utc_now_iso()
        record_pipeline_run(
            conn,
            run_id=f"model_feature_lineage_{model_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            pipeline_name="build_model_feature_lineage",
            status="success" if result["status"] == "passed" else "failed",
            started_at=started_at,
            ended_at=ended_at,
            duration_s=(datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds(),
            commit_sha=git_commit_sha(REPO),
            input_tables=["mart_multidim_model"],
            output_tables=["mart_model_feature_lineage"],
            model_id=model_id,
            blockers=[] if result["status"] == "passed" else ["missing_feature_lineage"],
            perf_summary=result,
        )
        logger.info("model feature lineage: %s", result)
        return 0 if result["status"] == "passed" else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

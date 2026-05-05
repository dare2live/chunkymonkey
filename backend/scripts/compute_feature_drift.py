"""计算特征漂移并写入 mart_feature_drift.

用法:
  python3 backend/scripts/compute_feature_drift.py
  python3 backend/scripts/compute_feature_drift.py --recent-days 7 --train-days 180
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("compute-drift")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ml_lifecycle.drift import (
    compute_feature_drift,
    compute_feature_drift_with_histogram_cache,
    write_drift_snapshot,
)
from services.ml_lifecycle.registry import get_champion
from services.db import get_conn
from services.schema_versions import record_actual_version


def _model_feature_cols(model_id: str | None) -> list[str] | None:
    if not model_id:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT feature_cols_json FROM mart_multidim_model WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        if not row or not row["feature_cols_json"]:
            return None
        import json
        return [str(v) for v in json.loads(row["feature_cols_json"])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--model-id", default=None, help="显式模型; 默认 lifecycle champion")
    parser.add_argument("--train-days", type=int, default=365)
    parser.add_argument("--recent-days", type=int, default=30)
    parser.add_argument("--top-n", type=int, default=30, help="只算前 N 个特征 (按列序)")
    parser.add_argument("--no-cache", action="store_true", help="禁用 histogram cache, 执行全量 PSI 扫描")
    parser.add_argument("--refresh-baseline", action="store_true", help="强制重建 train histogram baseline")
    args = parser.parse_args()

    champ = get_champion()
    model_id = args.model_id or (champ["model_id"] if champ else None)
    log.info(f"computing drift for model_id={model_id} feature_table={args.feature_table}")
    feature_cols = _model_feature_cols(model_id)
    if feature_cols is not None and args.top_n > 0:
        feature_cols = feature_cols[: args.top_n]

    compute_fn = compute_feature_drift if args.no_cache else compute_feature_drift_with_histogram_cache
    kwargs = {
        "feature_table": args.feature_table,
        "feature_columns": feature_cols,
        "train_window_days": args.train_days,
        "recent_window_days": args.recent_days,
        "model_id": model_id,
    }
    if not args.no_cache:
        kwargs["refresh_baseline"] = args.refresh_baseline
    drift = compute_fn(**kwargs)
    if not drift:
        log.warning("no drift results — feature_table 可能没数据或没有日期列")
        return 1

    # 仅取 top-N (避免 mart_feature_drift 行爆炸)
    drift = drift[: args.top_n]
    n = write_drift_snapshot(drift, window_days=args.recent_days)
    with get_conn() as conn:
        record_actual_version(conn, "mart_feature_drift")
        if not args.no_cache:
            record_actual_version(conn, "mart_feature_drift_histogram")
        conn.commit()
    log.info(f"wrote {n} feature drift rows")
    if model_id and n:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE mart_model_lifecycle
                   SET drift_score = (
                       SELECT AVG(psi)
                         FROM mart_feature_drift
                        WHERE model_id = ?
                          AND snapshot_at = (
                              SELECT MAX(snapshot_at)
                                FROM mart_feature_drift
                               WHERE model_id = ?
                          )
                          AND psi IS NOT NULL
                   ),
                       updated_at = now()
                 WHERE model_id = ?
                """,
                (model_id, model_id, model_id),
            )
            conn.commit()

    sev_counts = {"ok": 0, "warn": 0, "critical": 0, "unknown": 0}
    for r in drift:
        sev_counts[r["severity"]] = sev_counts.get(r["severity"], 0) + 1
    log.info(f"severity: {sev_counts}")

    return 0 if sev_counts["critical"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

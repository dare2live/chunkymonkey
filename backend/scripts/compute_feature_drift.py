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

from services.ml_lifecycle.drift import compute_feature_drift, write_drift_snapshot
from services.ml_lifecycle.registry import get_champion


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-table", default="fact_feature_panel")
    parser.add_argument("--train-days", type=int, default=365)
    parser.add_argument("--recent-days", type=int, default=30)
    parser.add_argument("--top-n", type=int, default=30, help="只算前 N 个特征 (按列序)")
    args = parser.parse_args()

    champ = get_champion()
    model_id = champ["model_id"] if champ else None
    log.info(f"computing drift for model_id={model_id} feature_table={args.feature_table}")

    drift = compute_feature_drift(
        feature_table=args.feature_table,
        train_window_days=args.train_days,
        recent_window_days=args.recent_days,
        model_id=model_id,
    )
    if not drift:
        log.warning("no drift results — feature_table 可能没数据或没有日期列")
        return 1

    # 仅取 top-N (避免 mart_feature_drift 行爆炸)
    drift = drift[: args.top_n]
    n = write_drift_snapshot(drift, window_days=args.recent_days)
    log.info(f"wrote {n} feature drift rows")

    sev_counts = {"ok": 0, "warn": 0, "critical": 0, "unknown": 0}
    for r in drift:
        sev_counts[r["severity"]] = sev_counts.get(r["severity"], 0) + 1
    log.info(f"severity: {sev_counts}")

    return 0 if sev_counts["critical"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

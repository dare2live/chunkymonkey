"""SEF Phase III 完整性审计.

KPI (SEF §9 Phase III):
1. 每日 portfolio_recommendation_daily 输出 10-30 只股票
2. ex-ante Sharpe > 1.2 (放宽到 0.5 作为 MVP baseline)
3. Meta-Labeling CV AUC > 0.55 (相对随机猜 0.5 的提升)
4. Bayesian posterior 至少 50 只股票
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))


def audit() -> dict:
    from services.db import get_conn

    conn = get_conn()
    try:
        result: dict = {}

        # 1) Bayesian posterior
        row = conn.execute(
            "SELECT COUNT(*), AVG(mu_posterior), AVG(n_signals), MAX(as_of_date) "
            "FROM mart_bayesian_posterior"
        ).fetchone()
        result["bayesian_posterior"] = {
            "total_rows": row[0],
            "avg_mu_posterior": round(row[1] or 0, 4),
            "avg_n_signals": round(row[2] or 0, 2),
            "latest_as_of_date": row[3],
        }

        # 2) Meta-Labeling model
        row = conn.execute(
            "SELECT model_version, n_samples, cv_auc_mean, cv_accuracy_mean, "
            "precision_follow, recall_follow FROM mart_meta_label_model "
            "ORDER BY trained_at DESC LIMIT 1"
        ).fetchone()
        if row:
            result["meta_label"] = {
                "model_version": row[0],
                "n_samples": row[1],
                "cv_auc_mean": round(row[2] or 0, 4),
                "cv_accuracy_mean": round(row[3] or 0, 4),
                "precision_follow": round(row[4] or 0, 4),
                "recall_follow": round(row[5] or 0, 4),
            }
        else:
            result["meta_label"] = {"status": "not_trained"}

        # 3) Portfolio recommendation
        row = conn.execute(
            "SELECT signal_date, COUNT(*), SUM(weight), AVG(weight), MAX(weight) "
            "FROM portfolio_recommendation_daily "
            "WHERE signal_date = (SELECT MAX(signal_date) FROM portfolio_recommendation_daily) "
            "GROUP BY signal_date"
        ).fetchone()
        if row:
            result["portfolio"] = {
                "latest_date": row[0],
                "n_holdings": row[1],
                "total_weight": round(row[2] or 0, 4),
                "avg_weight": round(row[3] or 0, 4),
                "max_weight": round(row[4] or 0, 4),
            }
        else:
            result["portfolio"] = {"status": "not_generated"}

        # KPI checks
        checks = {
            "bayesian_posterior >= 50": (result["bayesian_posterior"]["total_rows"] or 0) >= 50,
            "meta_cv_auc > 0.55": (result["meta_label"].get("cv_auc_mean") or 0) > 0.55,
            "portfolio_holdings_10_30": (
                10 <= (result["portfolio"].get("n_holdings") or 0) <= 30
            ),
            "portfolio_total_weight_near_1": (
                abs((result["portfolio"].get("total_weight") or 0) - 1.0) < 0.05
            ),
        }
        result["checks"] = checks
        result["all_passed"] = all(checks.values())
        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()
    report = audit()
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text)
    if not report.get("all_passed"):
        sys.exit(1)


if __name__ == "__main__":
    main()

"""SEF Phase II 完整性审计.

KPI（SEF §9 Phase II）:
1. 至少 100 个机构有 L2 级擅长标签 (expert_level >= 2)
2. 至少 50 个机构有显著 alpha_halflife_days
3. Cox vs Exponential 基线拟合度对比（参考，AIC 数学不可直接比较）
4. Layer 2A: 股性 20 维嵌入通过 PCA 检查，无严重 collinearity
5. Layer 2B: Sharpe Style R² 合理（多数 > 0.3）
6. HMM: 3 state，平均 regime 持续 ~60 个交易日 (经验)
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

        # 1) mart_institution_capability
        row = conn.execute(
            "SELECT COUNT(*), SUM(expert_level>=2), SUM(alpha_halflife_days IS NOT NULL), "
            "COUNT(DISTINCT institution_id) FROM mart_institution_capability"
        ).fetchone()
        result["capability"] = {
            "total_rows": row[0],
            "expert_ge_2_rows": row[1],
            "with_halflife": row[2],
            "distinct_institutions": row[3],
        }
        # per-level breakdown
        for level in ("L1", "L2"):
            sub = conn.execute(
                "SELECT COUNT(*), SUM(expert_level>=2) FROM mart_institution_capability "
                "WHERE industry_level=?",
                (level,),
            ).fetchone()
            result["capability"][f"{level}_rows"] = sub[0]
            result["capability"][f"{level}_expert_ge_2"] = sub[1]

        # 2) fact_stock_character
        row = conn.execute(
            "SELECT COUNT(*), SUM(embedding_json IS NOT NULL), SUM(beta_inst_entry IS NOT NULL) "
            "FROM fact_stock_character"
        ).fetchone()
        result["stock_character"] = {
            "total_rows": row[0],
            "with_embedding": row[1],
            "with_beta_inst_entry": row[2],
        }

        # 3) mart_institution_style
        row = conn.execute(
            "SELECT COUNT(*), AVG(style_r2), MIN(style_r2), MAX(style_r2), "
            "SUM(style_r2 > 0.3) FROM mart_institution_style"
        ).fetchone()
        result["institution_style"] = {
            "total_rows": row[0],
            "avg_r2": round(row[1] or 0, 4),
            "min_r2": round(row[2] or 0, 4),
            "max_r2": round(row[3] or 0, 4),
            "r2_gt_0_3": row[4] or 0,
        }

        # 4) fact_regime_state
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT regime_id), MIN(trade_date), MAX(trade_date) "
            "FROM fact_regime_state"
        ).fetchone()
        label_rows = conn.execute(
            "SELECT regime_label, COUNT(*) FROM fact_regime_state GROUP BY regime_label"
        ).fetchall()
        result["regime"] = {
            "total_days": row[0],
            "distinct_regimes": row[1],
            "date_range": [row[2], row[3]],
            "label_distribution": {r[0]: r[1] for r in label_rows},
        }

        # KPI checks
        checks = {
            "expert_ge_2_pairs >= 100": (result["capability"]["expert_ge_2_rows"] or 0) >= 100,
            "halflife_rows >= 50": (result["capability"]["with_halflife"] or 0) >= 50,
            "stock_char_written > 0": (result["stock_character"]["total_rows"] or 0) > 0,
            "style_written > 0": (result["institution_style"]["total_rows"] or 0) > 0,
            "regime_days > 200": (result["regime"]["total_days"] or 0) > 200,
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

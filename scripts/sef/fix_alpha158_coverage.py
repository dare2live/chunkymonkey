"""修正 qlib_alpha158_index.coverage_pct（之前用错分母）。"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))


def main():
    from services.db import get_conn

    inst_file = _ROOT / "data" / "qlib_data" / "instruments" / "all.txt"
    total_stocks = sum(1 for _ in inst_file.open()) if inst_file.exists() else 0
    if total_stocks == 0:
        print("no instruments file — skip")
        return

    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT year_month, n_stocks FROM qlib_alpha158_index"
        ).fetchall()
        for ym, ns in rows:
            cov = round((ns or 0) / total_stocks * 100, 2)
            conn.execute(
                "UPDATE qlib_alpha158_index SET coverage_pct=? WHERE year_month=?",
                (cov, ym),
            )
        conn.commit()
        print(f"updated {len(rows)} rows; total instruments = {total_stocks}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

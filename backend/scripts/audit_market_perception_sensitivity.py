#!/usr/bin/env python3
"""Audit Market Perception sensitivity on representative stocks.

This is a diagnostic only. It does not feed ranker / panel / paper_sim.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.db_connection import DB_PATH  # noqa: E402
from services.duck_adapter import connect  # noqa: E402


CONFIG_PATH = ROOT / "backend" / "config" / "market_perception.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--json", action="store_true", help="emit compact JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    representative_stocks = _load_representative_stocks()
    codes = [item["code"] for item in representative_stocks]
    with connect(str(DB_PATH), timeout=300) as conn:
        conn.execute("ATTACH IF NOT EXISTS 'data/market.duckdb' AS market (READ_ONLY)")
        cursor = conn.execute(
            f"""
            WITH px AS (
                SELECT code, CAST(date AS DATE) AS snapshot_date, close,
                       close / LAG(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) - 1.0 AS ret_1d,
                       LEAD(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) / close - 1.0 AS next_ret_1d
                  FROM market.v_price_kline_qfq
                 WHERE freq = 'daily' AND adjust = 'qfq'
                   AND code IN ({",".join(["?"] * len(codes))})
                   AND CAST(date AS DATE) BETWEEN CAST(? AS DATE) - INTERVAL 7 DAY AND CAST(? AS DATE) + INTERVAL 7 DAY
            )
            SELECT p.code, CAST(p.snapshot_date AS VARCHAR) AS snapshot_date,
                   p.ret_1d, p.next_ret_1d,
                   r.regime_score, e.emotion_score, e.emotion_state, e.action_bias,
                   e.limit_up_count, e.limit_down_count, e.market_breadth
              FROM px p
              JOIN mart_market_perception_daily r USING (snapshot_date)
              JOIN mart_market_perception_emotion_daily e USING (snapshot_date)
             WHERE p.snapshot_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
             ORDER BY p.code, p.snapshot_date
            """,
            [*codes, args.start, args.end, args.start, args.end],
        )
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description]
        df = pd.DataFrame(rows, columns=columns)
    if df.empty:
        raise SystemExit(f"no sensitivity rows for {args.start} -> {args.end}")

    summaries = []
    by_code = {item["code"]: item for item in representative_stocks}
    for code, group in df.groupby("code"):
        item = by_code[code]
        risk_on = group[group["emotion_score"] >= 0.35]
        risk_off = group[group["emotion_score"] <= -0.35]
        summaries.append(
            {
                **item,
                "rows": int(len(group)),
                "corr_same_day_ret_emotion": _corr(group["ret_1d"], group["emotion_score"]),
                "corr_next_day_ret_emotion": _corr(group["next_ret_1d"], group["emotion_score"]),
                "avg_ret_risk_on": _mean(risk_on["next_ret_1d"]),
                "avg_ret_risk_off": _mean(risk_off["next_ret_1d"]),
                "risk_on_days": int(len(risk_on)),
                "risk_off_days": int(len(risk_off)),
                "worst_emotion_day": _row_snapshot(group.sort_values("emotion_score").head(1)),
                "best_emotion_day": _row_snapshot(group.sort_values("emotion_score", ascending=False).head(1)),
            }
        )
    payload = {
        "start": args.start,
        "end": args.end,
        "rows": int(len(df)),
        "stocks": summaries,
        "note": "diagnostic-only; same-day correlations test sensitivity, next-day averages are not a trading rule",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _corr(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.concat([left, right], axis=1).dropna()
    if len(frame) < 3:
        return None
    value = frame.iloc[:, 0].corr(frame.iloc[:, 1])
    return None if pd.isna(value) else round(float(value), 6)


def _load_representative_stocks() -> list[dict]:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    node = ((raw.get("sensitivity_audit") or {}).get("representative_stocks") or {}).get("value") or []
    if not node:
        raise ValueError("sensitivity_audit.representative_stocks.value is empty")
    return [
        {
            "code": str(item["code"]).zfill(6),
            "name": str(item["name"]),
            "size_bucket": str(item["size_bucket"]),
            "industry": str(item["industry"]),
        }
        for item in node
    ]


def _mean(series: pd.Series) -> float | None:
    series = series.dropna()
    if series.empty:
        return None
    return round(float(series.mean()), 6)


def _row_snapshot(frame: pd.DataFrame) -> dict | None:
    if frame.empty:
        return None
    row = frame.iloc[0]
    return {
        "date": str(row["snapshot_date"]),
        "emotion_score": round(float(row["emotion_score"]), 6),
        "regime_score": round(float(row["regime_score"]), 6),
        "ret_1d": None if pd.isna(row["ret_1d"]) else round(float(row["ret_1d"]), 6),
        "next_ret_1d": None if pd.isna(row["next_ret_1d"]) else round(float(row["next_ret_1d"]), 6),
    }


if __name__ == "__main__":
    raise SystemExit(main())

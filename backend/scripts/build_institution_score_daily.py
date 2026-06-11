#!/usr/bin/env python3
"""Build mart_institution_score_daily from PIT-strict institution alpha classes."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.duck_adapter import connect
from services.strategies.institution_follow._common import date_expr
from services.strategies.institution_follow.capital_flow_alpha import CapitalFlowAlpha
from services.strategies.institution_follow.lhb_alpha import LHBAlpha
from services.strategies.institution_follow.northbound_alpha import (
    NorthboundAlpha,
    _max_staleness_days,
)
from services.strategies.institution_follow.survey_alpha import SurveyAlpha


SMART_DB = REPO_ROOT / "data" / "smartmoney.duckdb"
DEFAULT_START_DATE = "2024-07-01"  # rule-compliance: ok evidence=p0b-walk-forward-起始
DEFAULT_END_DATE = "2026-04-13"    # rule-compliance: ok evidence=panel-cutoff

log = logging.getLogger("build_institution_score_daily")


@dataclass(frozen=True)
class ClassSpec:
    name: str
    alpha_cls: type
    source_tables: tuple[str, ...]
    score_column: str
    norm_column: str
    feature_columns: tuple[str, ...]


CLASS_SPECS: tuple[ClassSpec, ...] = (
    ClassSpec(
        name="lhb",
        alpha_cls=LHBAlpha,
        source_tables=("fact_lhb_event",),
        score_column="lhb_score",
        norm_column="lhb_score_norm",
        feature_columns=(
            "lhb_score",
            "lhb_score_norm",
            "lhb_event_count_30d",
            "lhb_inst_net_buy_amount_30d",
            "lhb_net_buy_pct_sum_30d",
            "lhb_recency_days",
        ),
    ),
    ClassSpec(
        name="capital_flow",
        alpha_cls=CapitalFlowAlpha,
        source_tables=("fact_capital_flow_pit_daily",),
        score_column="capital_flow_score",
        norm_column="capital_flow_score_norm",
        feature_columns=(
            "capital_flow_score",
            "capital_flow_score_norm",
            "main_inflow_5d",
            "main_inflow_ratio_5d",
            "sustained_buy_count_5d",
        ),
    ),
    ClassSpec(
        name="survey",
        alpha_cls=SurveyAlpha,
        source_tables=("raw_institution_surveys",),
        score_column="survey_score",
        norm_column="survey_score_norm",
        feature_columns=(
            "survey_score",
            "survey_score_norm",
            "inst_survey_count_30d",
            "inst_survey_quality_30d",
        ),
    ),
    ClassSpec(
        name="northbound",
        alpha_cls=NorthboundAlpha,
        source_tables=("fact_hsgt_daily",),
        score_column="northbound_score",
        norm_column="northbound_score_norm",
        feature_columns=(
            "northbound_score",
            "northbound_score_norm",
            "nb_holding_pct",
            "nb_holding_chg_30d",
        ),
    ),
)

OUTPUT_COLUMNS = [
    "signal_date",
    "stock_code",
    "lhb_score",
    "lhb_score_norm",
    "lhb_event_count_30d",
    "lhb_inst_net_buy_amount_30d",
    "lhb_net_buy_pct_sum_30d",
    "lhb_recency_days",
    "capital_flow_score",
    "capital_flow_score_norm",
    "main_inflow_5d",
    "main_inflow_ratio_5d",
    "sustained_buy_count_5d",
    "survey_score",
    "survey_score_norm",
    "inst_survey_count_30d",
    "inst_survey_quality_30d",
    "northbound_score",
    "northbound_score_norm",
    "nb_holding_pct",
    "nb_holding_chg_30d",
    "composite_score",
    "n_classes_eligible",
    "built_at",
]

MART_DDL = """
CREATE TABLE IF NOT EXISTS mart_institution_score_daily (
    signal_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    lhb_score DOUBLE,
    lhb_score_norm DOUBLE,
    lhb_event_count_30d DOUBLE,
    lhb_inst_net_buy_amount_30d DOUBLE,
    lhb_net_buy_pct_sum_30d DOUBLE,
    lhb_recency_days DOUBLE,
    capital_flow_score DOUBLE,
    capital_flow_score_norm DOUBLE,
    main_inflow_5d DOUBLE,
    main_inflow_ratio_5d DOUBLE,
    sustained_buy_count_5d DOUBLE,
    survey_score DOUBLE,
    survey_score_norm DOUBLE,
    inst_survey_count_30d DOUBLE,
    inst_survey_quality_30d DOUBLE,
    northbound_score DOUBLE,
    northbound_score_norm DOUBLE,
    nb_holding_pct DOUBLE,
    nb_holding_chg_30d DOUBLE,
    composite_score DOUBLE,
    n_classes_eligible INTEGER,
    built_at VARCHAR,
    PRIMARY KEY (signal_date, stock_code)
)
"""


def normalize_per_signal_date(
    df: pd.DataFrame,
    score_column: str,
    output_column: str | None = None,
) -> pd.DataFrame:
    """Min-max normalize one signal-date score column to [0, 1]."""
    out = df.copy()
    output_column = output_column or f"{score_column}_norm"
    if score_column not in out.columns:
        out[output_column] = np.nan
        return out

    scores = pd.to_numeric(out[score_column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    out[score_column] = scores
    out[output_column] = np.nan
    valid = scores.notna()
    if not bool(valid.any()):
        return out

    lo = float(scores[valid].min())
    hi = float(scores[valid].max())
    if hi - lo <= 1e-12:
        out.loc[valid, output_column] = 0.0
    else:
        out.loc[valid, output_column] = (scores[valid] - lo) / (hi - lo)
    return out


def compose_signal_date_scores(
    signal_date,
    universe: Sequence[str],
    class_frames: dict[str, pd.DataFrame],
    specs: Sequence[ClassSpec] = CLASS_SPECS,
    built_at: str | None = None,
) -> pd.DataFrame:
    """Merge normalized class scores and compute the per-stock composite."""
    signal = pd.to_datetime(signal_date).date()
    codes = [str(code) for code in universe]
    out = pd.DataFrame({"signal_date": [signal] * len(codes), "stock_code": codes})
    eligible_norm_cols: list[str] = []

    for spec in specs:
        frame = class_frames.get(spec.name)
        prepared = _prepare_class_frame(frame, spec)
        if prepared is None:
            for col in spec.feature_columns:
                if col not in out.columns:
                    out[col] = np.nan
            continue

        out = out.merge(prepared, on="stock_code", how="left")
        if out[spec.norm_column].notna().any():
            eligible_norm_cols.append(spec.norm_column)

    for spec in specs:
        for col in spec.feature_columns:
            if col not in out.columns:
                out[col] = np.nan

    if eligible_norm_cols:
        out["composite_score"] = out[eligible_norm_cols].mean(axis=1, skipna=True)
    else:
        out["composite_score"] = np.nan
    out["n_classes_eligible"] = len(eligible_norm_cols)
    out["built_at"] = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out[OUTPUT_COLUMNS]


def _prepare_class_frame(frame: pd.DataFrame | None, spec: ClassSpec) -> pd.DataFrame | None:
    if frame is None or frame.empty or "stock_code" not in frame.columns:
        return None
    if spec.score_column not in frame.columns:
        return None

    cols = ["stock_code", *[c for c in spec.feature_columns if c != spec.norm_column]]
    existing = [c for c in cols if c in frame.columns]
    prepared = frame[existing].copy()
    prepared["stock_code"] = prepared["stock_code"].astype(str)
    prepared = prepared.drop_duplicates("stock_code", keep="last")
    prepared = normalize_per_signal_date(prepared, spec.score_column, spec.norm_column)
    if not prepared[spec.norm_column].notna().any():
        return None

    for col in spec.feature_columns:
        if col not in prepared.columns:
            prepared[col] = np.nan
        elif col != "stock_code":
            prepared[col] = pd.to_numeric(prepared[col], errors="coerce")
    return prepared[["stock_code", *spec.feature_columns]]


def _table_row_count(conn, table_name: str) -> int | None:
    exists = conn.execute(
        """
        SELECT COUNT(*)
          FROM information_schema.tables
         WHERE table_name = ?
        """,
        [table_name],
    ).fetchone()[0]
    if not exists:
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)


def _source_available(conn, spec: ClassSpec) -> bool:
    available = False
    missing: list[str] = []
    empty: list[str] = []
    for table in spec.source_tables:
        count = _table_row_count(conn, table)
        if count is None:
            missing.append(table)
        elif count <= 0:
            empty.append(table)
        else:
            available = True
    if not available:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if empty:
            detail.append("empty: " + ", ".join(empty))
        log.warning("Skipping %s alpha; source unavailable (%s)", spec.name, "; ".join(detail))
    return available


def _northbound_source_is_stale(conn, end_date: str) -> bool:
    """True when fact_hsgt_daily latest snapshot is staler than the yaml budget.

    fact_hsgt_daily is a DEPRECATED source (disclosure rules changed 2024-08).
    A non-empty table passes _source_available, so we additionally gate on
    freshness here: if the latest snapshot is older than the build window end by
    more than northbound.max_staleness_days, drop the class from the build and
    warn loudly instead of materializing a column built on a ~2-year-old snapshot.
    """
    if not _table_row_count(conn, "fact_hsgt_daily"):
        return False
    dt = date_expr("snapshot_date")
    row = conn.execute(f"SELECT MAX({dt}) FROM fact_hsgt_daily").fetchone()
    latest = row[0] if row else None
    if latest is None:
        return False
    latest_dt = pd.to_datetime(latest).date()
    end_dt = pd.to_datetime(end_date).date()
    staleness_days = (end_dt - latest_dt).days
    max_days = _max_staleness_days()
    if staleness_days > max_days:
        log.warning(
            "fact_hsgt_daily stale across build window: end_date=%s latest_snapshot=%s "
            "staleness=%sd > max=%sd -> dropping northbound class (factor=unknown)",
            end_date,
            latest_dt.isoformat(),
            staleness_days,
            max_days,
        )
        return True
    return False


def _load_signal_dates(conn, start_date: str, end_date: str, limit: int = 0) -> list:
    limit_sql = f" LIMIT {int(limit)}" if limit and limit > 0 else ""
    rows = conn.execute(
        f"""
        SELECT signal_date
          FROM mart_p0a_feature_label_panel_v4
         WHERE signal_date >= CAST(? AS DATE)
           AND signal_date <= CAST(? AS DATE)
         GROUP BY signal_date
         ORDER BY signal_date
        {limit_sql}
        """,
        [start_date, end_date],
    ).fetchall()
    return [row[0] for row in rows]


def _load_universe(conn, signal_date) -> list[str]:
    rows = conn.execute(
        """
        SELECT stock_code
          FROM mart_p0a_feature_label_panel_v4
         WHERE signal_date = ?
         ORDER BY stock_code
        """,
        [signal_date],
    ).fetchall()
    return [str(row[0]) for row in rows]


def _insert_batch(conn, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    batch = frame[OUTPUT_COLUMNS].copy()
    conn.raw.register("_institution_batch", batch)
    try:
        conn.raw.execute(
            f"""
            INSERT OR REPLACE INTO mart_institution_score_daily
            SELECT {", ".join(OUTPUT_COLUMNS)}
              FROM _institution_batch
            """
        )
    finally:
        try:
            conn.raw.unregister("_institution_batch")
        except Exception as e:
            log.warning(f"unregister _institution_batch failed: {e}")


def _summarize(conn) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS row_count,
               COUNT(DISTINCT signal_date) AS signal_dates,
               MIN(signal_date) AS min_signal_date,
               MAX(signal_date) AS max_signal_date,
               AVG(composite_score) AS avg_composite,
               AVG(n_classes_eligible) AS avg_classes
          FROM mart_institution_score_daily
        """
    ).fetchone()
    coverage_selects = []
    for idx, spec in enumerate(CLASS_SPECS):
        coverage_selects.extend([
            f"COUNT({spec.norm_column}) * 100.0 / NULLIF(COUNT(*), 0) AS row_cov_{idx}",
            (
                f"COUNT(DISTINCT CASE WHEN {spec.norm_column} IS NOT NULL THEN signal_date END) "
                f"* 100.0 / NULLIF(COUNT(DISTINCT signal_date), 0) AS date_cov_{idx}"
            ),
        ])
    coverage_row = conn.execute(
        f"""
        SELECT {", ".join(coverage_selects)}
          FROM mart_institution_score_daily
        """
    ).fetchone()
    coverage = {
        spec.name: {
            "row_coverage_pct": float((coverage_row[idx * 2] if coverage_row else 0.0) or 0.0),
            "date_coverage_pct": float((coverage_row[idx * 2 + 1] if coverage_row else 0.0) or 0.0),
        }
        for idx, spec in enumerate(CLASS_SPECS)
    }
    return {
        "row_count": int(row[0] or 0),
        "signal_dates": int(row[1] or 0),
        "min_signal_date": row[2],
        "max_signal_date": row[3],
        "avg_composite": float(row[4]) if row[4] is not None else None,
        "avg_classes": float(row[5]) if row[5] is not None else None,
        "coverage": coverage,
    }


def build_institution_score_daily(
    *,
    smartmoney_db: str | Path = SMART_DB,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    dry_run_dates: int = 0,
    batch_size: int = 8,
    threads: int = 8,
    memory_limit: str = "6GB",
    rebuild: bool = False,
) -> dict[str, object]:
    """Materialize mart_institution_score_daily and return summary stats.

    Incremental by default — INSERT OR REPLACE preserves rows outside [start_date, end_date].
    Pass rebuild=True (or --rebuild via CLI) only for explicit full wipe.
    """
    conn = connect(str(smartmoney_db), read_only=False)
    try:
        conn.execute(f"PRAGMA threads={int(threads)}")
        conn.execute(f"PRAGMA memory_limit='{memory_limit}'")

        active_specs = [spec for spec in CLASS_SPECS if _source_available(conn, spec)]
        if _northbound_source_is_stale(conn, end_date):
            active_specs = [spec for spec in active_specs if spec.name != "northbound"]
        alphas = {spec.name: spec.alpha_cls(conn=conn) for spec in active_specs}
        dates = _load_signal_dates(conn, start_date, end_date, dry_run_dates)
        if not dates:
            raise RuntimeError(f"No signal dates found in {start_date} -> {end_date}")

        if dry_run_dates <= 0:
            if rebuild:
                log.warning("rebuild=True: dropping mart_institution_score_daily (full history wipe)")
                conn.execute("DROP TABLE IF EXISTS mart_institution_score_daily")
            conn.execute(MART_DDL)

        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pending: list[pd.DataFrame] = []
        dry_rows = 0
        t0 = time.time()
        for idx, signal_date in enumerate(dates, start=1):
            universe = _load_universe(conn, signal_date)
            class_frames: dict[str, pd.DataFrame] = {}
            for spec in active_specs:
                try:
                    class_frames[spec.name] = alphas[spec.name].get_features(signal_date, universe)
                except Exception as exc:
                    log.warning("Skipping %s on %s after alpha error: %s", spec.name, signal_date, exc)

            day = compose_signal_date_scores(
                signal_date=signal_date,
                universe=universe,
                class_frames=class_frames,
                specs=CLASS_SPECS,
                built_at=built_at,
            )
            dry_rows += len(day)

            if dry_run_dates > 0:
                coverage = {
                    spec.name: bool(day[spec.norm_column].notna().any())
                    for spec in CLASS_SPECS
                }
                log.info(
                    "dry-run %s rows=%s n_classes=%s composite_avg=%.6f coverage=%s",
                    signal_date,
                    f"{len(day):,}",
                    int(day["n_classes_eligible"].max() or 0),
                    float(day["composite_score"].mean(skipna=True) or 0.0),
                    coverage,
                )
                continue

            pending.append(day)
            if len(pending) >= batch_size:
                _insert_batch(conn, pd.concat(pending, ignore_index=True))
                pending.clear()

            if idx == 1 or idx % 25 == 0 or idx == len(dates):
                elapsed = time.time() - t0
                log.info("processed %s/%s dates elapsed=%.1fs", idx, len(dates), elapsed)

        if dry_run_dates > 0:
            return {
                "dry_run": True,
                "row_count": dry_rows,
                "signal_dates": len(dates),
                "active_classes": [spec.name for spec in active_specs],
            }

        if pending:
            _insert_batch(conn, pd.concat(pending, ignore_index=True))
        summary = _summarize(conn)
        summary["dry_run"] = False
        summary["active_classes"] = [spec.name for spec in active_specs]
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build mart_institution_score_daily")
    parser.add_argument("--smartmoney-db", default=str(SMART_DB))
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--dry-run", nargs="?", const=3, default=0, type=int)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="6GB")
    parser.add_argument("--rebuild", action="store_true",
                        help="Drop existing mart_institution_score_daily before rebuild (DESTRUCTIVE — full history wipe)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    t0 = time.time()
    result = build_institution_score_daily(
        smartmoney_db=args.smartmoney_db,
        start_date=args.start_date,
        end_date=args.end_date,
        dry_run_dates=args.dry_run,
        batch_size=args.batch_size,
        threads=args.threads,
        memory_limit=args.memory_limit,
        rebuild=args.rebuild,
    )
    elapsed = time.time() - t0

    if result.get("dry_run"):
        log.info(
            "Dry run done: dates=%s rows=%s active_classes=%s elapsed=%.1fs",
            result["signal_dates"],
            f"{result['row_count']:,}",
            ",".join(result["active_classes"]),
            elapsed,
        )
        return 0

    log.info(
        "Done: rows=%s dates=%s range=%s -> %s avg_composite=%.6f avg_classes=%.2f elapsed=%.1fs",
        f"{result['row_count']:,}",
        result["signal_dates"],
        result["min_signal_date"],
        result["max_signal_date"],
        result["avg_composite"] or 0.0,
        result["avg_classes"] or 0.0,
        elapsed,
    )
    for name, cov in result["coverage"].items():
        log.info(
            "coverage %s: row=%.2f%% date=%.2f%%",
            name,
            cov["row_coverage_pct"],
            cov["date_coverage_pct"],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

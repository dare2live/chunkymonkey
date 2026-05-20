"""PIT-strict TDX theme lifecycle features for Market Perception P3."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .regime_engine import _attach_market_if_available, _fetchall, _table_exists, _to_date

logger = logging.getLogger("cm-api.market-perception.theme")

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "backend" / "config" / "market_perception.yaml"


@dataclass(frozen=True)
class ThemeConfig:
    required_member_confidence: str
    ret_20d_weight: float
    excess_20d_weight: float
    excess_60d_weight: float
    breadth_weight: float
    price_vs_ma20_weight: float
    top_quantile: float
    extreme_quantile: float
    bottom_quantile: float
    min_stocks: int
    guard_abs_max: float


def compute_theme_lifecycle_for_date(conn, snapshot_date: str | date | datetime) -> dict[str, Any]:
    day = _to_date(snapshot_date)
    frame = compute_theme_lifecycle_for_range(conn, day, day)
    return {
        "snapshot_date": day.isoformat(),
        "themes": frame.to_dict("records"),
        "rows": int(len(frame)),
    }


def compute_theme_lifecycle_for_range(conn, start: str | date, end: str | date) -> pd.DataFrame:
    start_day = _to_date(start)
    end_day = _to_date(end)
    if start_day > end_day:
        raise ValueError(f"start {start_day} > end {end_day}")
    days = _trading_days(conn, start_day, end_day)
    if any(d >= date.today() for d in days):
        raise ValueError(f"range {start_day} -> {end_day} includes today/future; PIT requires snapshot_date < today")
    if not days:
        return pd.DataFrame()

    _validate_observed_pit_coverage(conn, start_day, end_day)
    momentum = _load_sector_momentum(conn, start_day, end_day)
    if momentum.empty:
        raise ValueError(f"fact_sector_momentum_daily is empty for {start_day} -> {end_day}")
    internals = _load_sector_internal_stats(conn, start_day, end_day)
    if internals.empty:
        raise ValueError(f"theme internal stats are empty for {start_day} -> {end_day}")

    frame = momentum.merge(internals, on=["snapshot_date", "theme_name"], how="inner")
    cfg = get_theme_config()
    frame = frame[frame["n_stocks"].fillna(0).astype(int) >= cfg.min_stocks].copy()
    if frame.empty:
        raise ValueError(f"no theme rows passed min_stocks={cfg.min_stocks} for {start_day} -> {end_day}")
    return _score_and_label(frame, cfg)


def _load_sector_momentum(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "fact_sector_momentum_daily"):
        raise ValueError("fact_sector_momentum_daily is required for ThemeLifecycleEngine")
    rows = _fetchall(
        conn,
        """
        SELECT CAST(date AS VARCHAR) AS snapshot_date,
               sector_name AS theme_name,
               CAST(ret_20d AS DOUBLE) AS sector_ret_20d,
               CAST(ret_60d AS DOUBLE) AS sector_ret_60d,
               CAST(excess_20d AS DOUBLE) AS sector_excess_20d,
               CAST(excess_60d AS DOUBLE) AS sector_excess_60d,
               CAST(price_vs_ma20 AS DOUBLE) AS price_vs_ma20,
               CAST(price_vs_ma60 AS DOUBLE) AS price_vs_ma60
          FROM fact_sector_momentum_daily
         WHERE CAST(date AS DATE) BETWEEN ? AND ?
         ORDER BY CAST(date AS DATE), sector_name
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_sector_internal_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    if not _table_exists(conn, "mart_stock_industry_pit"):
        raise ValueError("mart_stock_industry_pit is required for ThemeLifecycleEngine")
    if _table_exists(conn, "fact_stock_kline_daily"):
        return _load_fact_stock_internal_stats(conn, start_day, end_day)
    return _load_market_view_internal_stats(conn, start_day, end_day)


def _load_fact_stock_internal_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    cols = {r["column_name"] for r in _fetchall(conn, "DESCRIBE fact_stock_kline_daily")}
    date_col = _first_existing(cols, ["trade_date", "date", "snapshot_date"])
    code_col = _first_existing(cols, ["stock_code", "code", "symbol"])
    pct_col = _first_existing(cols, ["pct_change", "pct_chg", "change_pct", "return_1d"])
    amount_col = _first_existing(cols, ["amount", "turnover", "turnover_amount"], required=False)
    amount_expr = f"CAST(k.{amount_col} AS DOUBLE)" if amount_col else "NULL::DOUBLE"
    rows = _fetchall(
        conn,
        f"""
        WITH base AS (
            SELECT CAST(k.{date_col} AS VARCHAR) AS snapshot_date,
                   ip.tdx_l1_name AS theme_name,
                   CAST(k.{pct_col} AS DOUBLE) / 100.0 AS ret_1d,
                   {amount_expr} AS amount
              FROM fact_stock_kline_daily k
              JOIN mart_stock_industry_pit ip
                ON ip.stock_code = k.{code_col}
               AND CAST(k.{date_col} AS DATE) BETWEEN CAST(ip.effective_from AS DATE) AND CAST(ip.effective_to AS DATE)
               AND ip.confidence_level = ?
             WHERE CAST(k.{date_col} AS DATE) BETWEEN ? AND ?
               AND ip.tdx_l1_name IS NOT NULL
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY snapshot_date, theme_name ORDER BY amount DESC NULLS LAST) AS amount_rank
              FROM base
        )
        SELECT snapshot_date, theme_name,
               COUNT(*)::INTEGER AS n_stocks,
               AVG(CASE WHEN ret_1d > 0 THEN 1.0 ELSE 0.0 END) AS sector_breadth,
               SUM(CASE WHEN ret_1d >= 0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count,
               CASE
                   WHEN SUM(amount) IS NULL OR SUM(amount) <= 0 THEN NULL
                   ELSE SUM(CASE WHEN amount_rank <= 3 THEN amount ELSE 0 END) / SUM(amount)
               END AS top3_turnover_share
          FROM ranked
         GROUP BY 1, 2
         ORDER BY CAST(snapshot_date AS DATE), theme_name
        """,
        [get_theme_config().required_member_confidence, start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _load_market_view_internal_stats(conn, start_day: date, end_day: date) -> pd.DataFrame:
    _attach_market_if_available(conn)
    rows = _fetchall(
        conn,
        """
        WITH px AS (
            SELECT code, CAST(date AS VARCHAR) AS snapshot_date, close, amount,
                   LAG(close) OVER (PARTITION BY code ORDER BY CAST(date AS DATE)) AS prev_close
              FROM market.v_price_kline_qfq
             WHERE freq = 'daily' AND adjust = 'qfq'
               AND CAST(date AS DATE) BETWEEN CAST(? AS DATE) - INTERVAL 1 DAY AND CAST(? AS DATE)
               AND regexp_matches(code, '^(00|30|60|68)[0-9]{4}$')
               AND COALESCE(source_name, '') NOT LIKE 'tdxhub_index%'
        ),
        base AS (
            SELECT px.snapshot_date, ip.tdx_l1_name AS theme_name,
                   CASE WHEN px.prev_close > 0 THEN px.close / px.prev_close - 1.0 ELSE NULL END AS ret_1d,
                   px.amount
              FROM px
              JOIN mart_stock_industry_pit ip
                ON ip.stock_code = px.code
               AND CAST(px.snapshot_date AS DATE) BETWEEN CAST(ip.effective_from AS DATE) AND CAST(ip.effective_to AS DATE)
               AND ip.confidence_level = ?
             WHERE CAST(px.snapshot_date AS DATE) BETWEEN ? AND ?
               AND px.prev_close IS NOT NULL
               AND ip.tdx_l1_name IS NOT NULL
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY snapshot_date, theme_name ORDER BY amount DESC NULLS LAST) AS amount_rank
              FROM base
        )
        SELECT snapshot_date, theme_name,
               COUNT(*)::INTEGER AS n_stocks,
               AVG(CASE WHEN ret_1d > 0 THEN 1.0 ELSE 0.0 END) AS sector_breadth,
               SUM(CASE WHEN ret_1d >= 0.095 THEN 1 ELSE 0 END)::INTEGER AS limit_up_count,
               CASE
                   WHEN SUM(amount) IS NULL OR SUM(amount) <= 0 THEN NULL
                   ELSE SUM(CASE WHEN amount_rank <= 3 THEN amount ELSE 0 END) / SUM(amount)
               END AS top3_turnover_share
          FROM ranked
         GROUP BY 1, 2
         ORDER BY CAST(snapshot_date AS DATE), theme_name
        """,
        [start_day.isoformat(), end_day.isoformat(), get_theme_config().required_member_confidence, start_day.isoformat(), end_day.isoformat()],
    )
    return pd.DataFrame(rows)


def _score_and_label(frame: pd.DataFrame, cfg: ThemeConfig) -> pd.DataFrame:
    ranked = frame.copy()
    group = ranked.groupby("snapshot_date", group_keys=False)
    rank_cols = {
        "ret_20d_rank": "sector_ret_20d",
        "excess_20d_rank": "sector_excess_20d",
        "excess_60d_rank": "sector_excess_60d",
        "breadth_rank": "sector_breadth",
        "price_vs_ma20_rank": "price_vs_ma20",
    }
    for out_col, src_col in rank_cols.items():
        ranked[out_col] = group[src_col].rank(pct=True, method="average")

    score01 = (
        ranked["ret_20d_rank"] * cfg.ret_20d_weight
        + ranked["excess_20d_rank"] * cfg.excess_20d_weight
        + ranked["excess_60d_rank"] * cfg.excess_60d_weight
        + ranked["breadth_rank"] * cfg.breadth_weight
        + ranked["price_vs_ma20_rank"] * cfg.price_vs_ma20_weight
    )
    ranked["theme_score"] = ((score01 * 2.0 - 1.0) * 0.9).clip(-0.9, 0.9).round(6)
    ranked["mainline_rank"] = ranked.groupby("snapshot_date")["theme_score"].rank(ascending=False, method="first").astype(int)
    ranked["is_mainline"] = ranked["mainline_rank"] <= 3
    ranked["diffusion_state"] = ranked.apply(lambda row: _diffusion_state(row, cfg), axis=1)
    ranked["lifecycle_stage"] = ranked.apply(lambda row: _lifecycle_stage(row, cfg), axis=1)
    ranked["pit_member_confidence"] = cfg.required_member_confidence
    ranked["source_engines"] = ranked.apply(
        lambda row: json.dumps(
            [{"engine": "ThemeLifecycleEngine", "score": float(row["theme_score"]), "weight": 1.0}],
            ensure_ascii=False,
        ),
        axis=1,
    )
    ranked["pit_cutoff_date"] = ranked["snapshot_date"]
    for _, row in ranked.iterrows():
        _guard_theme_row(row, cfg)
    cols = [
        "snapshot_date", "theme_name", "theme_score", "lifecycle_stage", "mainline_rank",
        "is_mainline", "diffusion_state", "sector_breadth", "sector_ret_20d",
        "sector_ret_60d", "sector_excess_20d", "sector_excess_60d", "price_vs_ma20",
        "price_vs_ma60", "limit_up_count", "n_stocks", "top3_turnover_share",
        "pit_member_confidence", "source_engines", "pit_cutoff_date",
    ]
    return ranked[cols].sort_values(["snapshot_date", "mainline_rank", "theme_name"]).reset_index(drop=True)


def _diffusion_state(row: pd.Series, cfg: ThemeConfig) -> str:
    if row["breadth_rank"] >= cfg.top_quantile and _positive(row["sector_breadth"]):
        return "板块扩散"
    if row["theme_score"] > 0 and row["breadth_rank"] < cfg.bottom_quantile:
        return "龙头独涨"
    if row["top3_turnover_share"] is not None and _finite(row["top3_turnover_share"]) and row["top3_turnover_share"] >= 0.35:
        return "成交集中"
    return "结构分化"


def _lifecycle_stage(row: pd.Series, cfg: ThemeConfig) -> str:
    score = float(row["theme_score"])
    ret20 = _nullable_float(row["sector_ret_20d"]) or 0.0
    ret60 = _nullable_float(row["sector_ret_60d"]) or 0.0
    px20 = _nullable_float(row["price_vs_ma20"]) or 0.0
    breadth_rank = float(row["breadth_rank"])
    if score >= (cfg.extreme_quantile * 2 - 1) and row["breadth_rank"] >= cfg.top_quantile and ret20 > 0:
        return "高潮"
    if score >= (cfg.top_quantile * 2 - 1) and ret20 > 0 and ret60 > 0 and breadth_rank >= cfg.top_quantile:
        return "主升"
    if score >= (cfg.top_quantile * 2 - 1) and ret20 > 0:
        return "确认"
    if score > 0 and px20 > 0:
        return "启动"
    if score <= (cfg.bottom_quantile * 2 - 1) and ret20 < 0 and px20 < 0:
        return "退潮"
    if ret20 > 0 and ret60 < 0:
        return "反抽"
    return "分歧"


def _validate_observed_pit_coverage(conn, start_day: date, end_day: date) -> None:
    if not _table_exists(conn, "mart_stock_industry_pit"):
        raise ValueError("mart_stock_industry_pit is required for ThemeLifecycleEngine")
    cfg = get_theme_config()
    rows = _fetchall(
        conn,
        """
        SELECT COUNT(DISTINCT cal.trade_date) AS covered_days
          FROM dim_trading_calendar cal
         WHERE cal.is_trading = 1
           AND CAST(cal.trade_date AS DATE) BETWEEN ? AND ?
           AND EXISTS (
               SELECT 1
                 FROM mart_stock_industry_pit ip
                WHERE ip.confidence_level = ?
                  AND CAST(cal.trade_date AS DATE) BETWEEN CAST(ip.effective_from AS DATE) AND CAST(ip.effective_to AS DATE)
           )
        """,
        [start_day.isoformat(), end_day.isoformat(), cfg.required_member_confidence],
    )
    covered = int(rows[0]["covered_days"] if rows else 0)
    expected = len(_trading_days(conn, start_day, end_day))
    if covered != expected:
        raise ValueError(
            f"observed PIT industry coverage incomplete for {start_day} -> {end_day}: {covered}/{expected} trading days"
        )


def _trading_days(conn, start_day: date, end_day: date) -> list[date]:
    rows = _fetchall(
        conn,
        """
        SELECT CAST(trade_date AS VARCHAR) AS trade_date
          FROM dim_trading_calendar
         WHERE is_trading = 1
           AND CAST(trade_date AS DATE) BETWEEN ? AND ?
         ORDER BY CAST(trade_date AS DATE)
        """,
        [start_day.isoformat(), end_day.isoformat()],
    )
    return [_to_date(row["trade_date"]) for row in rows]


def _guard_theme_row(row: pd.Series, cfg: ThemeConfig) -> None:
    score = float(row["theme_score"])
    if not math.isfinite(score):
        raise ValueError(f"theme_score invalid for {row['snapshot_date']} {row['theme_name']}: {score}")
    if abs(score) > cfg.guard_abs_max:
        raise ValueError(f"theme_score guard failed for {row['snapshot_date']} {row['theme_name']}: {score}")


@lru_cache(maxsize=1)
def get_theme_config() -> ThemeConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    section = raw.get("theme_lifecycle_engine", {})
    source = section.get("source", {})
    scoring = section.get("scoring", {})
    weights = scoring.get("weights", {})
    return ThemeConfig(
        required_member_confidence=str(_cfg_value(source.get("required_member_confidence"), "observed_snapshot")),
        ret_20d_weight=float(_cfg_value(weights.get("ret_20d_rank"), 0.25)),
        excess_20d_weight=float(_cfg_value(weights.get("excess_20d_rank"), 0.25)),
        excess_60d_weight=float(_cfg_value(weights.get("excess_60d_rank"), 0.20)),
        breadth_weight=float(_cfg_value(weights.get("breadth_rank"), 0.20)),
        price_vs_ma20_weight=float(_cfg_value(weights.get("price_vs_ma20_rank"), 0.10)),
        top_quantile=float(_cfg_value(scoring.get("top_quantile"), 0.75)),
        extreme_quantile=float(_cfg_value(scoring.get("extreme_quantile"), 0.90)),
        bottom_quantile=float(_cfg_value(scoring.get("bottom_quantile"), 0.25)),
        min_stocks=int(_cfg_value(scoring.get("min_stocks"), 20)),
        guard_abs_max=float(_cfg_value(scoring.get("guard_abs_max"), 1.0)),
    )


def _cfg_value(node: Any, default: Any) -> Any:
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    if node is None:
        return default
    return node


def _first_existing(cols: set[str], names: list[str], *, required: bool = True) -> str | None:
    for name in names:
        if name in cols:
            return name
    if required:
        raise ValueError(f"none of {names} found in columns {sorted(cols)}")
    return None


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite(value: Any) -> bool:
    return _nullable_float(value) is not None


def _positive(value: Any) -> bool:
    number = _nullable_float(value)
    return number is not None and number > 0

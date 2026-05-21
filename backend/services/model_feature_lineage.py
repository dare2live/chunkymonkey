"""Model feature lineage metadata for production promotion audits."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable

from services.model_feature_schema import (
    BASE_FEATURE_COLS,
    DENSE_V2_FEATURE_COLS,
    REGIME_FEATURE_COLS,
    TDX_KEEP_FEATURE_COLS,
    feature_cols_from_json,
)


DDL = """
CREATE TABLE IF NOT EXISTS mart_model_feature_lineage (
    model_id TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_group TEXT NOT NULL,
    source_table TEXT NOT NULL,
    upstream_source TEXT,
    source_tier SMALLINT,
    source_date_col TEXT,
    available_date_col TEXT,
    parser_version TEXT,
    pit_required BOOLEAN,
    lineage_status TEXT NOT NULL,
    notes TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (model_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_model_feature_lineage_model
    ON mart_model_feature_lineage(model_id, lineage_status);
"""


@dataclass(frozen=True)
class FeatureLineageSpec:
    feature_name: str
    feature_group: str
    source_table: str
    upstream_source: str
    source_tier: int
    source_date_col: str
    available_date_col: str
    parser_version: str | None
    pit_required: bool
    lineage_status: str = "known"
    notes: str = ""


PRICE_FEATURES = {
    "ret_1d", "ret_5d", "ret_20d", "ret_60d",
    "vol_z20d", "ma_ratio_5", "ma_ratio_20", "ma_ratio_60", "ma_ratio_250",
    "kmid", "klen", "kup", "klow", "ksft",
    "vol_ratio_5_20", "vol_std_5d", "vol_std_20d",
    "range_pos_20", "range_pos_60", "momentum_diff", "amount_chg_5d",
    "ret_20d_rank", "ret_60d_rank", "vol_z20d_rank", "amount_chg_5d_rank",
    "ret_20d_tdx_l1_rel", "ret_60d_tdx_l1_rel",
    "vol_z20d_tdx_l1_rel", "amount_chg_5d_tdx_l1_rel",
    "hs300_ret_20d", "hs300_ret_60d",
}

MARGIN_FEATURES: set[str] = set()  # Phase ψ.5 emptied — raw_margin_daily 删除, 这些特征不再有数据源

EVENT_FEATURES = {
    "inst_event_count_30d": ("fact_institution_event", "derived:tdx_f10_holder_events", 99),
    "inst_event_count_60d": ("fact_institution_event", "derived:tdx_f10_holder_events", 99),
    "exec_buy_count_90d": ("fact_shareholder_trade", "tdxhub.holders", 1),
    "exec_buy_ge1_count_90d": ("fact_shareholder_trade", "tdxhub.holders", 1),
    "lhb_inst_buy_count_30d": ("fact_lhb_event", "tdxhub.lhb", 1),
    "lhb_inst_buy_count_60d": ("fact_lhb_event", "tdxhub.lhb", 1),
    "jgdy_count_60d": ("fact_jgdy_event", "akshare:stock_jgdy_tj_em", 3),
    "dzjy_count_60d": ("fact_dzjy_event", "akshare:stock_dzjy_mrmx", 3),
    "days_since_exec_buy": ("fact_shareholder_trade", "tdxhub.holders", 1),
    "days_since_lhb": ("fact_lhb_event", "tdxhub.lhb", 1),
}

BASE_FUNDAMENTAL_FEATURES = {
    "shareholder_count_qoq": ("fact_holder_count_period", "tdxhub.holders", 1),
    "inst_count_qoq": ("fact_top10_holder_period", "tdxhub.holders", 1),
    "fund_count_qoq": ("fact_top10_holder_period", "tdxhub.holders", 1),
    "qfii_count_qoq": ("fact_top10_holder_period", "tdxhub.holders", 1),
    "yjyg_lower_pct": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "yjyg_upper_pct": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "roe": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "eps_basic": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
}

TDX_KEEP_SOURCES = {
    "forecast_profit_yoy_mid": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "avg_float_shares_change_pct_tdx": ("fact_holder_count_period", "tdxhub.holders", 1),
    "ocf_to_profit_tdx": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "fund_shares_qoq": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
    "forecast_range_width": ("raw_gpcw_detail", "tdxhub.gpcw", 1),
}


def _known(
    feature_name: str,
    *,
    feature_group: str,
    source_table: str,
    upstream_source: str,
    source_tier: int,
    source_date_col: str,
    available_date_col: str,
    pit_required: bool,
    parser_version: str | None = None,
    notes: str = "",
) -> FeatureLineageSpec:
    return FeatureLineageSpec(
        feature_name=feature_name,
        feature_group=feature_group,
        source_table=source_table,
        upstream_source=upstream_source,
        source_tier=source_tier,
        source_date_col=source_date_col,
        available_date_col=available_date_col,
        parser_version=parser_version,
        pit_required=pit_required,
        notes=notes,
    )


def lineage_for_feature(feature_name: str) -> FeatureLineageSpec:
    for suffix, transform_name in (
        ("_xs_rank", "daily cross-sectional rank"),
        ("_xs_bucket5", "daily cross-sectional quintile bucket"),
    ):
        if feature_name.endswith(suffix):
            base_feature = feature_name[: -len(suffix)]
            base = lineage_for_feature(base_feature)
            if base.lineage_status != "missing":
                return FeatureLineageSpec(
                    feature_name=feature_name,
                    feature_group=base.feature_group,
                    source_table=base.source_table,
                    upstream_source=base.upstream_source,
                    source_tier=base.source_tier,
                    source_date_col=base.source_date_col,
                    available_date_col=base.available_date_col,
                    parser_version=base.parser_version,
                    pit_required=base.pit_required,
                    lineage_status=base.lineage_status,
                    notes=f"{base.notes} Derived from {base_feature} via {transform_name}.",
                )
    if feature_name in PRICE_FEATURES:
        return _known(
            feature_name,
            feature_group="price_technical",
            source_table="v_price_kline_qfq",
            upstream_source="tdxhub.kline:qfq_daily+fallback_gap_fill",
            source_tier=1,
            source_date_col="date",
            available_date_col="date",
            pit_required=False,
            notes="Derived from canonical daily qfq OHLCV. tdxhub is primary; fallback only fills missing primary keys.",
        )
    # MARGIN_FEATURES lineage block removed Phase ψ.5 — set 已清空, 不会再 match
    if feature_name in EVENT_FEATURES:
        source_table, upstream, tier = EVENT_FEATURES[feature_name]
        return _known(
            feature_name,
            feature_group="event",
            source_table=source_table,
            upstream_source=upstream,
            source_tier=tier,
            source_date_col="event_date",
            available_date_col="event_date",
            pit_required=True,
        )
    if feature_name in BASE_FUNDAMENTAL_FEATURES:
        source_table, upstream, tier = BASE_FUNDAMENTAL_FEATURES[feature_name]
        return _known(
            feature_name,
            feature_group="fundamental",
            source_table=source_table,
            upstream_source=upstream,
            source_tier=tier,
            source_date_col="report_date",
            available_date_col="available_date",
            pit_required=True,
            parser_version="v1",
        )
    if feature_name in TDX_KEEP_SOURCES:
        source_table, upstream, tier = TDX_KEEP_SOURCES[feature_name]
        return _known(
            feature_name,
            feature_group="tdx_keep",
            source_table=source_table,
            upstream_source=upstream,
            source_tier=tier,
            source_date_col="report_date",
            available_date_col="available_date",
            pit_required=True,
            parser_version="v1",
        )
    if feature_name in REGIME_FEATURE_COLS:
        return _known(
            feature_name,
            feature_group="regime",
            source_table="fact_regime_state",
            upstream_source="derived:market_index_state",
            source_tier=99,
            source_date_col="date",
            available_date_col="date",
            pit_required=False,
        )
    return FeatureLineageSpec(
        feature_name=feature_name,
        feature_group="unknown",
        source_table="unknown",
        upstream_source="unknown",
        source_tier=99,
        source_date_col="unknown",
        available_date_col="unknown",
        parser_version=None,
        pit_required=True,
        lineage_status="missing",
        notes="No feature lineage mapping registered.",
    )


def all_registered_feature_names() -> set[str]:
    return set(BASE_FEATURE_COLS) | set(DENSE_V2_FEATURE_COLS) | set(REGIME_FEATURE_COLS) | set(TDX_KEEP_FEATURE_COLS)


def build_lineage_rows(model_id: str, feature_cols: Iterable[str]) -> list[dict]:
    built_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for feature in feature_cols:
        spec = asdict(lineage_for_feature(str(feature)))
        spec["model_id"] = model_id
        spec["built_at"] = built_at
        rows.append(spec)
    return rows


def model_feature_cols(conn, model_id: str) -> list[str]:
    row = conn.execute(
        "SELECT feature_cols_json FROM mart_multidim_model WHERE model_id = ?",
        (model_id,),
    ).fetchone()
    if not row or not row["feature_cols_json"]:
        return []
    return feature_cols_from_json(row["feature_cols_json"])


def write_model_feature_lineage(conn, *, model_id: str, feature_cols: Iterable[str] | None = None) -> dict:
    if hasattr(conn, "executescript"):
        conn.executescript(DDL)
    else:
        for statement in DDL.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)
    cols = list(feature_cols) if feature_cols is not None else model_feature_cols(conn, model_id)
    rows = build_lineage_rows(model_id, cols)
    conn.execute("DELETE FROM mart_model_feature_lineage WHERE model_id = ?", (model_id,))
    if rows:
        insert_cols = [
            "model_id", "feature_name", "feature_group", "source_table",
            "upstream_source", "source_tier", "source_date_col", "available_date_col",
            "parser_version", "pit_required", "lineage_status", "notes", "built_at",
        ]
        conn.executemany(
            f"INSERT INTO mart_model_feature_lineage ({', '.join(insert_cols)}) VALUES ({', '.join(['?'] * len(insert_cols))})",
            [tuple(row.get(col) for col in insert_cols) for row in rows],
        )
    conn.commit()
    missing = sum(1 for row in rows if row["lineage_status"] == "missing")
    return {
        "model_id": model_id,
        "features": len(rows),
        "missing": missing,
        "status": "passed" if missing == 0 and rows else "failed",
        "feature_groups": sorted({row["feature_group"] for row in rows}),
    }


def lineage_json_for_feature(feature_name: str) -> str:
    return json.dumps(asdict(lineage_for_feature(feature_name)), ensure_ascii=False, sort_keys=True)

"""Regression tests for the fact_hsgt_daily staleness guard (Scheme 7).

WHY: fact_hsgt_daily is a deprecated source that stopped receiving rows after
the 2024-08-16 snapshot (HSGT per-stock disclosure rules changed 2024-08).
Before this guard, NorthboundAlpha picked MAX(snapshot_date) < signal_date and
silently fed a ~2-year-old snapshot into the live institution composite score
(health-check 2026-06-10 HIGH). The guard must set the factor to unknown (0.0)
when the freshest available snapshot is staler than the yaml budget, and must
NOT over-flag a fresh source.
"""

from __future__ import annotations

import logging

import duckdb
import numpy as np
import pandas as pd
import pytest

from scripts.build_institution_score_daily import _northbound_source_is_stale
from services.strategies.institution_follow.northbound_alpha import (
    NorthboundAlpha,
    _max_staleness_days,
)


def _make_hsgt_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE fact_hsgt_daily (
            snapshot_date VARCHAR,
            stock_code VARCHAR,
            hold_market_value DOUBLE,
            hold_pct_of_float DOUBLE
        )
        """
    )


@pytest.fixture
def conn():
    con = duckdb.connect(":memory:")
    try:
        yield con
    finally:
        con.close()


def test_yaml_threshold_loads_and_is_positive() -> None:
    # Rules-in-yaml: the threshold is config-owned, not hardcoded.
    assert _max_staleness_days() > 0


def test_stale_snapshot_yields_unknown_factor(conn, caplog) -> None:
    """2024-08 snapshot vs 2026 signal_date -> factor unknown (0.0) + warning."""
    _make_hsgt_table(conn)
    conn.executemany(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        [
            ("20240801", "600000", 100_000_000.0, 1.5),
            ("20240816", "600000", 120_000_000.0, 1.8),
        ],
    )

    alpha = NorthboundAlpha(conn=conn)  # yaml-back threshold
    with caplog.at_level(logging.WARNING, logger="northbound_alpha"):
        feats = alpha.get_features("2026-04-13", ["600000"])

    row = feats.iloc[0]
    # northbound_score 必须 NaN (unknown), 不是 0.0 — 否则 compose 会当合格类静默参与 (§4).
    assert pd.isna(row["northbound_score"]), (
        f"stale source 的 northbound_score 必须 NaN 让 compose 排除, 实得 {row['northbound_score']}"
    )
    # 其余 feature 列不得泄漏 2 年前的快照值
    for col in NorthboundAlpha.FEATURE_COLUMNS:
        if col == "northbound_score":
            continue
        assert row[col] == 0.0 or pd.isna(row[col]), f"{col} leaked stale data: {row[col]}"
    assert any("stale" in rec.getMessage() for rec in caplog.records), (
        "expected a staleness warning to be logged"
    )


def test_fresh_snapshot_flows_through(conn) -> None:
    """A recent snapshot (within budget) must produce real, non-zero features."""
    _make_hsgt_table(conn)
    conn.executemany(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        [
            ("20260301", "600000", 100_000_000.0, 1.5),
            ("20260410", "600000", 130_000_000.0, 2.4),
        ],
    )

    alpha = NorthboundAlpha(conn=conn)
    feats = alpha.get_features("2026-04-13", ["600000"])

    row = feats.iloc[0]
    assert row["nb_holding_pct"] == pytest.approx(2.4)
    assert row["northbound_hold_market_value"] == pytest.approx(130_000_000.0)
    # Guard did not over-flag a fresh source: at least one feature is non-zero.
    assert any(row[col] != 0.0 for col in NorthboundAlpha.FEATURE_COLUMNS)


def test_threshold_boundary_respects_yaml_override(conn) -> None:
    """Explicit small threshold flips a borderline-fresh snapshot to unknown."""
    _make_hsgt_table(conn)
    conn.execute(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        ["20260301", "600000", 130_000_000.0, 2.4],
    )

    # 2026-04-13 - 2026-03-01 = 43 calendar days.
    fresh = NorthboundAlpha(conn=conn, max_staleness_days=60)
    stale = NorthboundAlpha(conn=conn, max_staleness_days=10)

    fresh_row = fresh.get_features("2026-04-13", ["600000"]).iloc[0]
    stale_row = stale.get_features("2026-04-13", ["600000"]).iloc[0]

    assert fresh_row["nb_holding_pct"] == pytest.approx(2.4)
    assert stale_row["nb_holding_pct"] == 0.0


def test_no_snapshot_before_signal_yields_unknown(conn) -> None:
    """Only future snapshots exist -> unknown (PIT) without crashing."""
    _make_hsgt_table(conn)
    conn.execute(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        ["20260701", "600000", 130_000_000.0, 2.4],
    )

    alpha = NorthboundAlpha(conn=conn)
    feats = alpha.get_features("2026-04-13", ["600000"])

    assert feats.iloc[0]["nb_holding_pct"] == 0.0


def test_build_source_stale_detector_flags_old_snapshot(conn) -> None:
    _make_hsgt_table(conn)
    conn.execute(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        ["20240816", "600000", 120_000_000.0, 1.8],
    )

    assert _northbound_source_is_stale(conn, "2026-04-13") is True


def test_build_source_stale_detector_passes_fresh_snapshot(conn) -> None:
    _make_hsgt_table(conn)
    conn.execute(
        "INSERT INTO fact_hsgt_daily VALUES (?, ?, ?, ?)",
        ["20260410", "600000", 130_000_000.0, 2.4],
    )

    assert _northbound_source_is_stale(conn, "2026-04-13") is False


def test_build_source_stale_detector_handles_missing_table(conn) -> None:
    # No fact_hsgt_daily at all -> not stale (the source-available gate handles absence).
    assert _northbound_source_is_stale(conn, "2026-04-13") is False


# ──────────────────────────────────────────────────────────────────────
# Fable-5 复查防回退 (2026-06-11): unknown northbound 必须被 compose 完全排除,
# 不得以 0.0 静默参与 composite / 虚增 n_classes_eligible (§4 反模式)
# ──────────────────────────────────────────────────────────────────────

def test_unknown_northbound_excluded_from_composite_not_diluting():
    from scripts.build_institution_score_daily import compose_signal_date_scores

    universe = ["600000", "600001", "600002"]
    # northbound class: 全 unknown (score=NaN, 模拟 stale source 的 _unknown_features 产出)
    nb_frame = pd.DataFrame({"stock_code": universe, "northbound_score": [np.nan, np.nan, np.nan]})
    # lhb class: 有真实分化 score; capital_flow/survey 不提供 (None) → 自然不 eligible
    lhb_frame = pd.DataFrame({"stock_code": universe, "lhb_score": [1.0, 2.0, 3.0]})
    out = compose_signal_date_scores(
        "2026-04-13", universe,
        {"northbound": nb_frame, "lhb": lhb_frame},  # 默认 CLASS_SPECS
    )
    # northbound 全 NaN + capital_flow/survey 缺 → 只有 lhb 1 个合格类
    assert (out["n_classes_eligible"] == 1).all(), (
        f"unknown northbound 不得计入 eligible, 实得 {out['n_classes_eligible'].tolist()}"
    )
    # composite 应纯由 lhb 决定 (0/0.5/1.0), 不被 northbound 的 0.0 稀释
    comp = out.sort_values("stock_code")["composite_score"].tolist()
    assert comp == pytest.approx([0.0, 0.5, 1.0]), (
        f"composite 被 unknown northbound 稀释: {comp} (应纯 lhb 归一)"
    )

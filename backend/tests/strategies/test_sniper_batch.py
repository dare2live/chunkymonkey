"""Unit tests for the Phase 3.4 sniper SQL batch builder."""
from __future__ import annotations

from pathlib import Path

import duckdb

from scripts.build_sniper_score_daily import _main_capital_cte, build_sniper_score_daily


def _create_fixture_dbs(tmp_path: Path) -> tuple[Path, Path, Path]:
    smart_db = tmp_path / "smartmoney.duckdb"
    market_db = tmp_path / "market.duckdb"
    alpha_db = tmp_path / "alpha158.duckdb"

    sm = duckdb.connect(str(smart_db))
    try:
        sm.execute("""
            CREATE TABLE mart_p0a_feature_label_panel_v4 (
                signal_date DATE,
                stock_code VARCHAR,
                lhb_inst_buy_30d INTEGER,
                sector_ret_20d DOUBLE
            )
        """)
        sm.executemany(
            "INSERT INTO mart_p0a_feature_label_panel_v4 VALUES (?, ?, ?, ?)",
            [
                ("2024-01-10", "AAA", 1, 0.40),
                ("2024-01-10", "BBB", 0, 0.30),
                ("2024-01-10", "CCC", None, 0.20),
                ("2024-01-10", "DDD", 2, None),
                ("2024-01-10", "EEE", 0, 0.10),
                ("2024-01-10", "FFF", -1, 0.00),
            ],
        )
    finally:
        sm.close()

    mkt = duckdb.connect(str(market_db))
    try:
        mkt.execute("""
            CREATE TABLE v_price_kline_qfq (
                code VARCHAR,
                date VARCHAR,
                freq VARCHAR,
                adjust VARCHAR,
                close DOUBLE
            )
        """)
    finally:
        mkt.close()

    alpha = duckdb.connect(str(alpha_db))
    try:
        alpha.execute("""
            CREATE TABLE fact_alpha158_panel (
                stock_code VARCHAR,
                date DATE,
                a158_roc60 DOUBLE
            )
        """)
    finally:
        alpha.close()

    return smart_db, market_db, alpha_db


def _build_fixture(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    smart_db, market_db, alpha_db = _create_fixture_dbs(tmp_path)
    result = build_sniper_score_daily(
        smartmoney_db=smart_db,
        market_db=market_db,
        alpha158_db=alpha_db,
        start_date="2024-01-10",
        end_date="2024-01-10",
        threads=1,
        memory_limit="512MB",
    )
    assert result["row_count"] == 6
    return duckdb.connect(str(smart_db), read_only=True)


def test_r2_lhb_threshold_boundary_and_null_safety(tmp_path: Path) -> None:
    con = _build_fixture(tmp_path)
    try:
        rows = dict(
            con.execute("""
                SELECT stock_code, r2_lhb_hit
                FROM mart_sniper_score_daily
                ORDER BY stock_code
            """).fetchall()
        )
    finally:
        con.close()

    assert rows["AAA"] is True
    assert rows["DDD"] is True
    assert rows["BBB"] is False
    assert rows["EEE"] is False
    assert rows["FFF"] is False
    assert rows["CCC"] is None


def test_r4_sector_top_quartile_includes_quantile_boundary(tmp_path: Path) -> None:
    con = _build_fixture(tmp_path)
    try:
        rows = dict(
            con.execute("""
                SELECT stock_code, r4_sector_momentum_hit
                FROM mart_sniper_score_daily
                ORDER BY stock_code
            """).fetchall()
        )
    finally:
        con.close()

    assert rows["AAA"] is True
    assert rows["BBB"] is True
    assert rows["CCC"] is False
    assert rows["EEE"] is False
    assert rows["FFF"] is False
    assert rows["DDD"] is None


def test_missing_rule_inputs_decrement_eligible_count(tmp_path: Path) -> None:
    con = _build_fixture(tmp_path)
    try:
        rows = {
            code: (score, eligible, triggered)
            for code, score, eligible, triggered in con.execute("""
                SELECT stock_code, confluence_score, n_rules_eligible, triggered
                FROM mart_sniper_score_daily
                ORDER BY stock_code
            """).fetchall()
        }
    finally:
        con.close()

    assert rows["AAA"] == (2, 2, False)
    assert rows["BBB"] == (1, 2, False)
    assert rows["CCC"] == (0, 1, False)
    assert rows["DDD"] == (1, 1, False)


def test_main_capital_cte_does_not_use_deprecated_raw_fund_flow_only() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("""
            CREATE TABLE raw_fund_flow_daily (
                trade_date VARCHAR,
                stock_code VARCHAR,
                main_net_amount DOUBLE
            )
        """)

        sql = _main_capital_cte(con)
    finally:
        con.close()

    assert "raw_fund_flow_daily" not in sql
    assert "CAST(NULL AS BOOLEAN) AS r3_main_capital_hit" in sql

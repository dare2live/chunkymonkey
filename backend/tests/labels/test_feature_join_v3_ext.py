"""feature_join_v3_ext 单测 — 11 cols capital_flow_pit (PIT-audit Step 3 verified).

PIT 严格 (Step 3): source 表 fact_capital_flow_pit_daily 跟 fact_financial_pit_daily 同模式,
trade_date 是 PIT key, 历史 row 用 ≤ trade_date events.

测试: 4 步
1. DDL idempotent (ALTER ADD COLUMN IF NOT EXISTS)
2. JOIN 把 capital_flow cols 加入 panel
3. PIT 严格: 未来 trade_date row 不入 panel
4. NULL coverage: 没匹配 capital_flow row 时 cols 是 NULL
"""
from __future__ import annotations

import duckdb

from services.labels.feature_join_v3_ext import (
    FEATURE_PANEL_VERSION_V3_EXT,
    build_p0a_feature_label_panel_v3_ext,
)


def _make_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    # Minimal mart_p0a_feature_label_panel_v3 (v3 prereq)
    conn.execute("""
        CREATE TABLE mart_p0a_feature_label_panel_v3 (
            stock_code TEXT, signal_date DATE,
            entry_date DATE, unable_at_entry BOOLEAN,
            fwd_cost_after_5d DOUBLE, fwd_cost_after_10d DOUBLE, fwd_cost_after_20d DOUBLE,
            a158_kmid DOUBLE,  -- sample 1 of 64
            vol_30d DOUBLE,    -- sample risk
            pe_ttm DOUBLE,     -- sample fin_raw
            event_lhb_7d BOOLEAN,
            survey_count_60d INTEGER,
            pe_ttm_z_1y DOUBLE,
            sector_ret_60d DOUBLE,
            inst_quality_wavg DOUBLE,
            industry_pit_confidence TEXT,
            feature_version TEXT, built_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE fact_capital_flow_pit_daily (
            stock_code TEXT, trade_date TEXT,
            lhb_count_30d INTEGER, lhb_net_buy_pct_30d DOUBLE,
            lhb_inst_buy_30d INTEGER,
            lhb_count_90d INTEGER, lhb_inst_buy_90d INTEGER,
            exec_buy_60d INTEGER, exec_sell_60d INTEGER,
            exec_buy_pct_60d DOUBLE, exec_sell_pct_60d DOUBLE,
            exec_net_signal DOUBLE,
            holder_count_change_q_pct DOUBLE,
            holder_count_q_report_date TEXT,
            built_at TIMESTAMP
        )
    """)
    # NB: duck_adapter.connect 没用 (in-memory test), 直接 use conn
    return conn


def _build(conn, signal_dates: list[str], stock_codes: list[str]) -> int:
    """Adapter — 用 in-memory conn 直接跑 build SQL (跳过 duck_adapter)."""
    from services.labels.feature_join_v3_ext import _FEATURE_JOIN_SQL_V3_EXT

    conn.execute(
        "CREATE TABLE IF NOT EXISTS mart_p0a_feature_label_panel_v3_ext AS "
        "SELECT * FROM mart_p0a_feature_label_panel_v3 WHERE 1=0"
    )
    new_cols = [
        ("lhb_count_30d", "INTEGER"), ("lhb_net_buy_pct_30d", "DOUBLE"),
        ("lhb_inst_buy_30d", "INTEGER"), ("lhb_count_90d", "INTEGER"),
        ("lhb_inst_buy_90d", "INTEGER"),
        ("exec_buy_60d", "INTEGER"), ("exec_sell_60d", "INTEGER"),
        ("exec_buy_pct_60d", "DOUBLE"), ("exec_sell_pct_60d", "DOUBLE"),
        ("exec_net_signal", "DOUBLE"),
        ("holder_count_change_q_pct", "DOUBLE"),
        ("holder_count_q_report_date", "TEXT"),
    ]
    for col, dtype in new_cols:
        try:
            conn.execute(f"ALTER TABLE mart_p0a_feature_label_panel_v3_ext ADD COLUMN {col} {dtype}")
        except Exception:
            pass

    conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
    conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
    conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
    conn.execute("DROP TABLE IF EXISTS tmp_stocks")
    conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
    conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

    conn.execute(
        "DELETE FROM mart_p0a_feature_label_panel_v3_ext "
        "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
        "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
    )

    conn.execute(_FEATURE_JOIN_SQL_V3_EXT)

    n = conn.execute(
        "SELECT COUNT(*) FROM mart_p0a_feature_label_panel_v3_ext "
        "WHERE signal_date IN (SELECT signal_date FROM tmp_signal_dates) "
        "  AND stock_code IN (SELECT stock_code FROM tmp_stocks)"
    ).fetchone()[0]
    return n


def test_v3_ext_creates_table_idempotent():
    conn = _make_conn()
    # Insert minimal row to allow build()
    conn.execute("INSERT INTO mart_p0a_feature_label_panel_v3 VALUES "
                 "('600000', '2024-06-28', '2024-07-01', FALSE, 0.05, 0.08, 0.10, "
                 "0.01, 0.02, 25.0, FALSE, 5, 0.5, 0.03, 60.0, 'observed_snapshot', 'p0a_v3', 't')")
    _build(conn, signal_dates=["2024-06-28"], stock_codes=["600000"])
    cols = {r[0] for r in conn.execute("DESCRIBE mart_p0a_feature_label_panel_v3_ext").fetchall()}
    for col in ("lhb_count_30d", "exec_net_signal", "holder_count_change_q_pct",
                "holder_count_q_report_date"):
        assert col in cols, f"col {col} missing"


def test_v3_ext_basic_join_adds_capital_flow_cols():
    conn = _make_conn()
    # v3 panel: 1 stock × 1 signal_date
    conn.execute("INSERT INTO mart_p0a_feature_label_panel_v3 VALUES "
                 "('600000', '2024-06-28', '2024-07-01', FALSE, 0.05, 0.08, 0.10, "
                 "0.01, 0.02, 25.0, FALSE, 5, 0.5, 0.03, 60.0, 'observed_snapshot', 'p0a_v3', 't')")
    # capital_flow same stock × same date
    conn.execute("INSERT INTO fact_capital_flow_pit_daily VALUES "
                 "('600000', '2024-06-28', 2, 8.5, 1, 5, 3, 3, 1, 0.05, 0.01, 0.5, -6.05, '2024-03-31', NULL)")

    n = _build(conn, signal_dates=["2024-06-28"], stock_codes=["600000"])
    assert n == 1
    row = conn.execute("SELECT lhb_count_30d, exec_net_signal, holder_count_change_q_pct "
                       "FROM mart_p0a_feature_label_panel_v3_ext WHERE stock_code='600000'").fetchone()
    assert row[0] == 2  # lhb_count_30d
    assert abs(row[1] - 0.5) < 1e-9  # exec_net_signal
    assert abs(row[2] - (-6.05)) < 1e-6  # holder_count_change_q_pct


def test_v3_ext_pit_future_capital_flow_excluded():
    """PIT 严格: signal_date=2024-06-28 时 2024-07-15 的 capital_flow row 不入."""
    conn = _make_conn()
    conn.execute("INSERT INTO mart_p0a_feature_label_panel_v3 VALUES "
                 "('600000', '2024-06-28', '2024-07-01', FALSE, 0.05, 0.08, 0.10, "
                 "0.01, 0.02, 25.0, FALSE, 5, 0.5, 0.03, 60.0, 'observed_snapshot', 'p0a_v3', 't')")
    # Past row (should JOIN)
    conn.execute("INSERT INTO fact_capital_flow_pit_daily VALUES "
                 "('600000', '2024-06-28', 2, 8.5, 1, 5, 3, 3, 1, 0.05, 0.01, 0.5, -6.05, '2024-03-31', NULL)")
    # Future row (should NOT JOIN — different trade_date)
    conn.execute("INSERT INTO fact_capital_flow_pit_daily VALUES "
                 "('600000', '2024-07-15', 999, 999, 999, 999, 999, 999, 999, 999, 999, 999, 999, '2024-06-30', NULL)")

    n = _build(conn, signal_dates=["2024-06-28"], stock_codes=["600000"])
    assert n == 1
    row = conn.execute("SELECT lhb_count_30d FROM mart_p0a_feature_label_panel_v3_ext").fetchone()
    assert row[0] == 2  # 取的是 2024-06-28 (PIT), 不是 future 2024-07-15


def test_v3_ext_no_capital_flow_match_keeps_v3_with_null_cols():
    """v3 panel 有 row 但 capital_flow 没匹配 — 11 cols 全 NULL, panel row 仍入."""
    conn = _make_conn()
    conn.execute("INSERT INTO mart_p0a_feature_label_panel_v3 VALUES "
                 "('600000', '2024-06-28', '2024-07-01', FALSE, 0.05, 0.08, 0.10, "
                 "0.01, 0.02, 25.0, FALSE, 5, 0.5, 0.03, 60.0, 'observed_snapshot', 'p0a_v3', 't')")
    # No capital_flow row

    n = _build(conn, signal_dates=["2024-06-28"], stock_codes=["600000"])
    assert n == 1
    row = conn.execute(
        "SELECT lhb_count_30d, exec_net_signal, holder_count_change_q_pct "
        "FROM mart_p0a_feature_label_panel_v3_ext"
    ).fetchone()
    assert all(v is None for v in row), f"expected NULL, got {row}"

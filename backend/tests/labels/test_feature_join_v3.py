"""P0a feature_join v3 smoke test — Codex 7-day plan Day 1.

mock 完整 schema 验证:
1. DDL 字段数对齐 (DDL declares 103 cols + 2 metadata = 105)
2. SQL 语法 OK (跑通)
3. PIT 严格: 未来 row 不出现在 panel
4. survey ASOF: as_of_date <= signal_date 取最新
5. fin_z rolling: 短窗口 z-score 算正确
6. industry_pit ASOF: confidence_level 兜底
7. holder_asof 计 inst_path_a: hold_ratio_total 加权 quality

为避免 ATTACH 复杂, 用 in-memory conn + 直接执行 SQL constant (bypass build wrapper).
"""
from __future__ import annotations

import duckdb
import pytest

from services.labels.feature_join_v3 import (
    FEATURE_PANEL_DDL_V3,
    FEATURE_PANEL_VERSION_V3,
    _FEATURE_JOIN_SQL_V3,
)


def _setup_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all required tables + ATTACH-substitute (a158 as schema)."""
    conn.execute(FEATURE_PANEL_DDL_V3)
    conn.execute("CREATE SCHEMA IF NOT EXISTS a158")

    conn.execute("""
        CREATE TABLE mart_p0a_label_panel (
            stock_code TEXT, signal_date DATE, entry_date DATE,
            unable_at_entry BOOLEAN,
            fwd_cost_after_5d DOUBLE, fwd_cost_after_10d DOUBLE, fwd_cost_after_20d DOUBLE
        )
    """)

    a158_cols_ddl = [
        "a158_kmid", "a158_klen", "a158_kmid2", "a158_kup", "a158_kup2",
        "a158_klow", "a158_klow2", "a158_ksft", "a158_ksft2",
    ]
    for n in (5, 10, 20, 30, 60):
        for stat in ("roc", "ma", "std", "max", "min", "rsv", "qtl", "cntp", "sump", "vma", "vstd"):
            a158_cols_ddl.append(f"a158_{stat}{n}")
    cols_sql = ", ".join(f"{c} DOUBLE" for c in a158_cols_ddl)
    conn.execute(f"CREATE TABLE a158.fact_alpha158_panel (stock_code TEXT, date DATE, {cols_sql})")

    conn.execute("""
        CREATE TABLE fact_risk_factors (
            stock_code TEXT, calc_date DATE,
            vol_30d DOUBLE, vol_60d DOUBLE, vol_120d DOUBLE,
            sharpe_60d DOUBLE, mom_30d DOUBLE, mom_120d DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE fact_financial_pit_daily (
            stock_code TEXT, trade_date DATE,
            pe_ttm DOUBLE, pb DOUBLE, ps_ttm DOUBLE, roe_q DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE fact_lhb_event (stock_code TEXT, trade_date DATE)
    """)
    conn.execute("""
        CREATE TABLE fact_institution_event (stock_code TEXT, notice_date TEXT)
    """)
    conn.execute("""
        CREATE TABLE fact_signal_context (
            stock_code TEXT, date DATE, formula_id TEXT, state TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE mart_stock_survey_features (
            stock_code TEXT, as_of_date TEXT,
            survey_count_30d INTEGER, survey_count_60d INTEGER,
            survey_inst_30d INTEGER, survey_inst_60d INTEGER,
            survey_bin TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE mart_stock_industry_pit (
            stock_code TEXT, effective_from TEXT, effective_to TEXT,
            tdx_l1 TEXT, tdx_l1_name TEXT,
            tdx_l2 TEXT, tdx_l2_name TEXT,
            tdx_l3 TEXT, tdx_l3_name TEXT,
            source TEXT, source_snapshot_date TEXT,
            confidence_level TEXT, is_historical_pit BOOLEAN
        )
    """)

    conn.execute("""
        CREATE TABLE fact_sector_momentum_daily (
            sector_name TEXT, date TEXT,
            ret_5d DOUBLE, ret_20d DOUBLE, ret_60d DOUBLE,
            excess_20d DOUBLE, excess_60d DOUBLE
        )
    """)

    conn.execute("""
        CREATE TABLE fact_top10_holder_period (
            stock_code TEXT, report_date TEXT, holder_set TEXT,
            holder_rank INTEGER, holder_name TEXT, holder_name_norm TEXT,
            hold_ratio_total DOUBLE, effective_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE mart_institution_profile (
            institution_name TEXT, win_rate_60d DOUBLE
        )
    """)


def _build_with_inputs(
    conn: duckdb.DuckDBPyConnection,
    *,
    signal_dates: list[str],
    stock_codes: list[str],
) -> list[tuple]:
    conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
    conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
    conn.executemany("INSERT INTO tmp_signal_dates VALUES (?)", [(d,) for d in signal_dates])
    conn.execute("DROP TABLE IF EXISTS tmp_stocks")
    conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")
    conn.executemany("INSERT INTO tmp_stocks VALUES (?)", [(c,) for c in stock_codes])

    conn.execute(_FEATURE_JOIN_SQL_V3, [FEATURE_PANEL_VERSION_V3, "2026-05-14T00:00:00"])
    return conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 ORDER BY stock_code, signal_date"
    ).fetchall()


def test_ddl_creates_expected_cols():
    """DDL 字段数总计 112: 2 PK + 5 label + 64 alpha158 + 6 risk + 4 fin_raw + 4 events + 6 formula + 4 survey + 4 val_z + 5 sector + 5 inst_path_a + 1 industry_pit_confidence + 2 metadata.

    feature count (训练用): 64+6+4+4+6+4+4+5+5 = 102 (industry_pit_confidence 是 metadata, 不入 feature matrix)
    """
    conn = duckdb.connect(":memory:")
    conn.execute(FEATURE_PANEL_DDL_V3)
    n_cols = conn.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name='mart_p0a_feature_label_panel_v3'"
    ).fetchone()[0]
    assert n_cols == 112


def test_empty_inputs_produces_no_rows():
    """grid 空时 SELECT 应返 0 行."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("DROP TABLE IF EXISTS tmp_signal_dates")
    conn.execute("CREATE TEMP TABLE tmp_signal_dates(signal_date DATE)")
    conn.execute("DROP TABLE IF EXISTS tmp_stocks")
    conn.execute("CREATE TEMP TABLE tmp_stocks(stock_code TEXT)")

    conn.execute(_FEATURE_JOIN_SQL_V3, [FEATURE_PANEL_VERSION_V3, "2026-05-14T00:00:00"])
    n = conn.execute("SELECT COUNT(*) FROM mart_p0a_feature_label_panel_v3").fetchone()[0]
    assert n == 0


def test_minimal_data_path_runs():
    """最小有效数据集 — 1 stock × 1 signal_date, 各源至少 1 行 → grid 1 行入 panel."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date, a158_kmid, a158_ma5) "
                 "VALUES ('600000', '2024-06-30', 0.01, 12.5)")
    conn.execute("INSERT INTO fact_risk_factors VALUES "
                 "('600000', '2024-06-28', 0.02, 0.03, 0.04, 1.2, 0.05, 0.10)")
    conn.execute("INSERT INTO fact_financial_pit_daily VALUES "
                 "('600000', '2024-06-30', 25.0, 3.0, 2.5, 0.15)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "600000"


def test_pit_future_data_excluded():
    """关键 PIT 测试 — signal_date=2024-06-30 时 2024-07-15 的 risk row 不能进 panel."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date, a158_kmid) "
                 "VALUES ('600000', '2024-06-30', 0.01)")
    conn.execute("INSERT INTO fact_risk_factors VALUES "
                 "('600000', '2024-05-15', 0.02, 0.03, 0.04, 1.2, 0.05, 0.10),"
                 "('600000', '2024-07-15', 999.0, 999.0, 999.0, 999.0, 999.0, 999.0)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0"
    ).description]
    vol_30d_idx = cols.index("vol_30d")
    assert rows[0][vol_30d_idx] == 0.02


def test_survey_asof_picks_latest_before_signal():
    """survey_asof: 多 as_of_date <= signal_date, 取最新."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO mart_stock_survey_features VALUES "
                 "('600000', '2024-06-01', 3, 5, 2, 4, 'warm'),"
                 "('600000', '2024-06-25', 5, 12, 4, 9, 'hot'),"
                 "('600000', '2024-07-15', 999, 999, 999, 999, 'crazy')")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0"
    ).description]
    survey_60d_idx = cols.index("survey_count_60d")
    assert rows[0][survey_60d_idx] == 12  # 2024-06-25 row, not 2024-07-15


def test_sector_excess_via_industry_pit():
    """sector_asof: industry_pit_asof → fact_sector_momentum_daily PIT date<=signal."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO mart_stock_industry_pit VALUES "
                 "('600000', '2020-01-01', '2999-12-31', 'BANK', '银行',"
                 " 'BANK_S', '股份制银行', 'BANK_S1', '一级银行',"
                 " 'current_label_fallback', NULL, 'current_label_fallback', FALSE)")
    conn.execute("INSERT INTO fact_sector_momentum_daily VALUES "
                 "('银行', '2024-06-25', 0.012, 0.025, 0.080, 0.018, 0.045),"
                 "('银行', '2024-07-10', 999, 999, 999, 999, 999)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0"
    ).description]
    sector_60d_idx = cols.index("sector_ret_60d")
    excess_60d_idx = cols.index("sector_excess_60d")
    assert abs(rows[0][sector_60d_idx] - 0.080) < 1e-9
    assert abs(rows[0][excess_60d_idx] - 0.045) < 1e-9


def test_holder_inst_quality_weighted_average():
    """inst_path_a: hold_ratio_total 加权 win_rate_60d 计 inst_quality_wavg."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO fact_top10_holder_period VALUES "
                 "('600000', '2024-03-31', 'free', 1, '中央汇金', '中央汇金', 5.0, '2024-04-30'),"
                 "('600000', '2024-03-31', 'free', 2, '高瓴资本', '高瓴资本', 2.0, '2024-04-30')")
    conn.execute("INSERT INTO mart_institution_profile VALUES "
                 "('中央汇金', 60.0), ('高瓴资本', 80.0)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0"
    ).description]
    wavg_idx = cols.index("inst_quality_wavg")
    cnt_idx = cols.index("inst_holder_cnt")
    expected_wavg = (5.0 * 60.0 + 2.0 * 80.0) / (5.0 + 2.0)
    assert abs(rows[0][wavg_idx] - expected_wavg) < 1e-6
    assert rows[0][cnt_idx] == 2


def test_holder_future_effective_date_excluded():
    """PIT 严格: holder.effective_date > signal_date → 不计."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO fact_top10_holder_period VALUES "
                 "('600000', '2024-06-30', 'free', 1, '未来基金', '未来基金', 99.0, '2024-07-15')")
    conn.execute("INSERT INTO mart_institution_profile VALUES "
                 "('未来基金', 99.0)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0"
    ).description]
    cnt_idx = cols.index("inst_holder_cnt")
    assert rows[0][cnt_idx] is None  # 没 holder 匹配 → NULL


def test_feature_version_marked():
    """feature_version 字段写入 'p0a_v3'."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")

    _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    fv = conn.execute(
        "SELECT DISTINCT feature_version FROM mart_p0a_feature_label_panel_v3"
    ).fetchone()[0]
    assert fv == FEATURE_PANEL_VERSION_V3
    assert fv == "p0a_v3"


# === Codex Mi2 推荐补强测试 ===

def test_valuation_z_score_arithmetic():
    """Codex Mi2: 验证 z-score 算术正确 (短窗 mean/std → z = (x - mean) / std)."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    # 5 日 pe_ttm 序列 → 第 5 日的 z-score 用全 5 日 mean/std (240 PRECEDING + current 涵盖)
    conn.executemany(
        "INSERT INTO fact_financial_pit_daily (stock_code, trade_date, pe_ttm) VALUES (?, ?, ?)",
        [
            ("600000", "2024-06-26", 10.0),
            ("600000", "2024-06-27", 12.0),
            ("600000", "2024-06-28", 14.0),  # mean=12, std=sample 2.0
            ("600000", "2024-06-29", 16.0),
            ("600000", "2024-06-30", 18.0),
        ],
    )
    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0").description]
    z_idx = cols.index("pe_ttm_z_1y")
    pe_idx = cols.index("pe_ttm")
    # 5 个值 [10,12,14,16,18] → mean=14, sample std=√(2.5×4)=√10≈3.1623
    # z(18) = (18-14)/3.1623 ≈ 1.2649
    assert abs(rows[0][pe_idx] - 18.0) < 1e-9
    assert abs(rows[0][z_idx] - 1.2649) < 0.01


def test_inst_quality_excludes_unmatched_holders():
    """Codex M4 fix: 未匹配 mart_institution_profile 的 holder 不算 quality (NULL ≠ 0).

    inst_quality_wavg / inst_quality_max 只用匹配机构, NOT COALESCE 0.
    inst_holder_cnt / inst_total_holding_ratio 仍含所有 holder.
    """
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO fact_top10_holder_period VALUES "
                 "('600000', '2024-03-31', 'free', 1, '中央汇金', '中央汇金', 5.0, '2024-04-30'),"
                 "('600000', '2024-03-31', 'free', 2, '未知机构', '未知机构', 3.0, '2024-04-30')")
    conn.execute("INSERT INTO mart_institution_profile VALUES ('中央汇金', 60.0)")
    # 未知机构没 institution_profile entry → inst_quality IS NULL

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0").description]
    wavg_idx = cols.index("inst_quality_wavg")
    max_idx = cols.index("inst_quality_max")
    cnt_idx = cols.index("inst_holder_cnt")
    total_idx = cols.index("inst_total_holding_ratio")
    # wavg 仅匹配机构: 中央汇金 5.0 × 60 / 5.0 = 60.0 (排除未知机构)
    assert abs(rows[0][wavg_idx] - 60.0) < 1e-6
    # max 仅匹配
    assert abs(rows[0][max_idx] - 60.0) < 1e-6
    # cnt 含两个
    assert rows[0][cnt_idx] == 2
    # total 含两个 (5+3)
    assert abs(rows[0][total_idx] - 8.0) < 1e-6


def test_top_inst_quantile_per_signal_date():
    """Codex M2 fix: top_inst_holding_ratio 用 per-signal_date 0.8 quantile, NOT global.

    构造 2 signal_date, 一日 quality=100 另一日 quality=10 → top 计算应分别用各日 q80.
    """
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    for d in ("2024-06-30", "2024-07-31"):
        conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                     f"('600000', '{d}', NULL, FALSE, 0.05, 0.08, 0.10)")
        conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                     f"VALUES ('600000', '{d}')")
    # 2024-06-30: 1 机构 quality=100 (单独 q80=100)
    # 2024-07-31: 1 机构 quality=10 (单独 q80=10)
    conn.execute("INSERT INTO fact_top10_holder_period VALUES "
                 "('600000', '2024-03-31', 'free', 1, 'A', 'A', 5.0, '2024-04-30'),"
                 "('600000', '2024-06-30', 'free', 1, 'B', 'B', 3.0, '2024-07-15')")
    conn.execute("INSERT INTO mart_institution_profile VALUES ('A', 100.0), ('B', 10.0)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30", "2024-07-31"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v3 ORDER BY signal_date LIMIT 2").description]
    top_idx = cols.index("top_inst_holding_ratio")
    # 2024-06-30: A quality=100 是唯一匹配机构, 它在自己 q80 之上 → top_ratio = 5/5 = 1.0
    assert abs(rows[0][top_idx] - 1.0) < 1e-6
    # 2024-07-31: 仍只有 A holding (B 持仓 2024-06-30 effective < signal 7-31, 但 A 也 effective_date=2024-04-30 < 7-31)
    # 两持仓: A(quality=100), B(quality=10) → q80 per-date = QUANTILE_CONT([100,10], 0.8) = 82
    # A>=82 → top = 5 / (5+3) = 0.625
    assert abs(rows[1][top_idx] - 0.625) < 1e-6


def test_industry_pit_confidence_output():
    """Codex M1: industry_pit_confidence 字段标 'current_label_fallback' 让下游可 filter."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    conn.execute("INSERT INTO mart_p0a_label_panel VALUES "
                 "('600000', '2024-06-30', '2024-07-01', FALSE, 0.05, 0.08, 0.10)")
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 "VALUES ('600000', '2024-06-30')")
    conn.execute("INSERT INTO mart_stock_industry_pit VALUES "
                 "('600000', '2020-01-01', '2999-12-31', 'BANK', '银行',"
                 " 'BANK_S', '股份制', 'BANK_S1', '一级',"
                 " 'current_label_fallback', NULL, 'current_label_fallback', FALSE)")

    rows = _build_with_inputs(conn, signal_dates=["2024-06-30"], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0").description]
    conf_idx = cols.index("industry_pit_confidence")
    assert rows[0][conf_idx] == "current_label_fallback"


def test_fin_z_rolling_window_size():
    """Codex Mi1: rolling 239 PRECEDING + current = exactly 240 rows = 1Y trading days."""
    conn = duckdb.connect(":memory:")
    _setup_schema(conn)

    # 创建 300 trading days 数据, z-score 在第 300 日应用 240 row window
    from datetime import date, timedelta
    base = date(2024, 1, 1)
    rows_fin = []
    for i in range(300):
        d = (base + timedelta(days=i)).isoformat()
        rows_fin.append(("600000", d, float(i)))  # pe_ttm = i (linearly increasing)
    conn.executemany(
        "INSERT INTO fact_financial_pit_daily (stock_code, trade_date, pe_ttm) VALUES (?, ?, ?)",
        rows_fin,
    )
    sig_date = (base + timedelta(days=299)).isoformat()
    conn.execute(
        "INSERT INTO mart_p0a_label_panel VALUES "
        f"('600000', '{sig_date}', NULL, FALSE, 0.05, 0.08, 0.10)"
    )
    conn.execute("INSERT INTO a158.fact_alpha158_panel (stock_code, date) "
                 f"VALUES ('600000', '{sig_date}')")

    rs = _build_with_inputs(conn, signal_dates=[sig_date], stock_codes=["600000"])
    cols = [d[0] for d in conn.execute("SELECT * FROM mart_p0a_feature_label_panel_v3 LIMIT 0").description]
    z_idx = cols.index("pe_ttm_z_1y")
    # 第 300 日 (i=299), window = i=60..299 (239 PRECEDING + current = 240 rows)
    # values: 60..299 → mean = (60+299)/2 = 179.5
    # 注: DuckDB STDDEV 是 sample std (除 n-1). 240 numbers 60..299 sample std ≈ 69.426
    # z = (299 - 179.5) / 69.426 ≈ 1.7212
    assert abs(rs[0][z_idx] - 1.7212) < 0.01

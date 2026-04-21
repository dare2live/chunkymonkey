"""Unit test for qlib_follow_engine.extract_training_matrix — D1-D8 + one-hot."""

import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qlib_follow_engine import (
    FEATURE_COLUMNS,
    _TDX_L1_ONEHOT_CODES,
    _TDX_L1_ONEHOT_FEATURES,
    _yoy,
    extract_training_matrix,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE fact_institution_event (
            institution_id TEXT, stock_code TEXT, report_date TEXT, notice_date TEXT,
            event_type TEXT, premium_pct REAL, gain_60d REAL
        );
        CREATE TABLE dim_stock_sw_industry (
            stock_code TEXT PRIMARY KEY,
            sw_l1 TEXT, sw_l2 TEXT, sw_l3 TEXT,
            sw_l1_name TEXT, sw_l2_name TEXT, sw_l3_name TEXT
        );
        CREATE TABLE dim_financial_latest (
            stock_code TEXT PRIMARY KEY,
            roe REAL, debt_ratio REAL, gross_margin REAL,
            revenue_yoy REAL, profit_yoy REAL,
            ocf_to_profit REAL, contract_to_revenue REAL
        );
        CREATE TABLE raw_gpcw_detail (
            stock_code TEXT, report_date TEXT,
            holder_count REAL, contract_liabilities_wan REAL,
            forecast_profit_yoy_low REAL, forecast_profit_yoy_high REAL
        );
        CREATE TABLE raw_capital_unlock (
            event_id TEXT, snapshot_date TEXT, stock_code TEXT,
            unlock_date TEXT, unlock_ratio_float_mkt REAL
        );
        CREATE TABLE raw_institution_surveys (
            stock_code TEXT, survey_date TEXT, notice_date TEXT,
            inst_count INTEGER
        );
    """)
    return conn


def _make_mkt_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE price_kline (
            code TEXT, date TEXT, freq TEXT, adjust TEXT, close REAL
        );
    """)
    return conn


def _seed_common(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO dim_stock_sw_industry VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("600001", "T07", "T0701", "T070101", "信息技术", "半导体", "设计"),
    )
    conn.execute(
        "INSERT INTO dim_financial_latest VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("600001", 0.12, 0.45, 0.30, 15.0, 20.0, 1.1, 0.18),
    )


def test_feature_columns_contain_all_d_and_onehot() -> None:
    expected_d = {
        "holder_count_yoy", "contract_liabilities_yoy",
        "forecast_profit_yoy_mid", "future_unlock_ratio_180d",
        "inst_recent_ev_60d", "survey_count_90d",
    }
    missing = expected_d - set(FEATURE_COLUMNS)
    assert not missing, f"FEATURE_COLUMNS 缺少 D 系列: {missing}"
    onehot_missing = set(_TDX_L1_ONEHOT_FEATURES) - set(FEATURE_COLUMNS)
    assert not onehot_missing, f"FEATURE_COLUMNS 缺少 one-hot: {onehot_missing}"
    assert len(_TDX_L1_ONEHOT_CODES) == 13
    assert len(_TDX_L1_ONEHOT_FEATURES) == 13


def test_yoy_helper_handles_edge_cases() -> None:
    assert _yoy(120, 100) == 20.0
    assert _yoy(80, 100) == -20.0
    assert _yoy(None, 100) is None
    assert _yoy(100, None) is None
    assert _yoy(100, 0) is None        # 基数 0 返回 None (避免 inf)
    assert _yoy(100, -50) is None      # 负基数返回 None (防误算)


def test_extract_matrix_basic_fields_and_onehot() -> None:
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    conn.execute(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("inst_A", "600001", "2025-09-30", "2025-11-01", "new_entry", 2.5, 15.0),
    )
    conn.commit()

    out, feats = extract_training_matrix(conn, mkt_conn, window_start="2025-01-01", window_end="2026-01-01")
    assert len(out) == 1
    s = out[0]

    # 基础事件级
    assert s["premium_pct"] == 2.5
    assert s["gain_60d"] == 15.0
    assert s["event_type_is_new_entry"] == 1
    # 财务 join
    assert s["roe"] == 0.12
    # one-hot：T07 命中，其它为 0
    assert s["ind_t07"] == 1
    for code, feat in zip(_TDX_L1_ONEHOT_CODES, _TDX_L1_ONEHOT_FEATURES):
        if code != "T07":
            assert s[feat] == 0, f"{feat} 应为 0"
    # D 特征默认 None (未喂源数据)
    assert s["holder_count_yoy"] is None
    assert s["contract_liabilities_yoy"] is None
    assert s["forecast_profit_yoy_mid"] is None
    # 无源数据时 D5/D8 应为 0 或 None (实现上是 0)
    assert s["future_unlock_ratio_180d"] in (0.0, None)
    assert s["survey_count_90d"] == 0
    # FEATURE_COLUMNS 全部列都已填或置 None/0
    for col in feats:
        assert col in s, f"缺少 {col}"


def test_d1_d2_d3_from_gpcw_with_prev_year() -> None:
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    # 当季 + 去年同期
    conn.executemany(
        "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("600001", "2025-09-30", 12000, 500.0, 10.0, 30.0),  # curr
            ("600001", "2024-09-30", 10000, 400.0, None, None),  # prev
        ],
    )
    conn.execute(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("inst_A", "600001", "2025-09-30", "2025-11-01", "new_entry", 0.0, 5.0),
    )
    conn.commit()

    out, _ = extract_training_matrix(conn, mkt_conn, window_start="2025-01-01", window_end="2026-01-01")
    s = out[0]
    # D1: (12000-10000)/10000 * 100 = 20.0
    assert s["holder_count_yoy"] == 20.0
    # D2: (500-400)/400 * 100 = 25.0
    assert s["contract_liabilities_yoy"] == 25.0
    # D3: (10+30)/2 = 20.0
    assert s["forecast_profit_yoy_mid"] == 20.0


def test_d5_future_unlock_sums_only_future_window() -> None:
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    conn.execute(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("inst_A", "600001", "2025-09-30", "2025-11-01", "new_entry", 0.0, 5.0),
    )
    conn.executemany(
        "INSERT INTO raw_capital_unlock VALUES (?, ?, ?, ?, ?)",
        [
            ("e1", "2025-10-01", "600001", "2025-10-15", 0.5),    # 过去不算
            ("e2", "2025-10-01", "600001", "2025-12-01", 2.0),    # 在 180d 窗口内
            ("e3", "2025-10-01", "600001", "2026-03-01", 1.5),    # 在窗口末边界 (120d 后)
            ("e4", "2025-10-01", "600001", "2026-06-01", 10.0),   # 超出 180d
        ],
    )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="2025-01-01", window_end="2026-01-01")
    assert out[0]["future_unlock_ratio_180d"] == 3.5  # 2.0 + 1.5


def test_d7_inst_recent_ev_excludes_future_and_unmatured() -> None:
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    # 机构历史：3 个已成熟事件 (notice_date < 当前 - 60d) + 1 个未成熟 (太近)
    conn.executemany(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("inst_A", "600001", "2024-09-30", "2024-11-01", "new_entry", 0.0, 10.0),
            ("inst_A", "600001", "2025-03-31", "2025-05-01", "increase", 0.0, 20.0),
            ("inst_A", "600001", "2025-06-30", "2025-08-01", "increase", 0.0, 30.0),
            # 这个在当前事件 notice_date - 60d 之后，不算
            ("inst_A", "600001", "2025-09-30", "2025-10-15", "increase", 0.0, 999.0),
            # 当前事件
            ("inst_A", "600001", "2025-09-30", "2025-11-01", "new_entry", 0.0, 5.0),
        ],
    )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="2020-01-01", window_end="2026-01-01")
    # 找到当前事件行
    curr = [s for s in out if s["notice_date"] == "2025-11-01"][0]
    # 只 3 个已成熟事件：(10+20+30)/3 = 20
    assert curr["inst_recent_ev_60d"] == 20.0


def test_gpcw_join_handles_yyyymmdd_event_dates() -> None:
    """回归：fact_institution_event.report_date 是 YYYYMMDD，
    raw_gpcw_detail.report_date 是 YYYY-MM-DD，必须归一化后再匹配。"""
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    conn.executemany(
        "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("600001", "2025-09-30", 11000, 550.0, 15.0, 25.0),
            ("600001", "2024-09-30", 10000, 500.0, None, None),
        ],
    )
    conn.execute(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("inst_A", "600001", "20250930", "20251101", "new_entry", 0.0, 5.0),
    )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="20250101", window_end="20260101")
    assert len(out) == 1
    s = out[0]
    assert s["holder_count_yoy"] == 10.0          # (11000-10000)/10000
    assert s["contract_liabilities_yoy"] == 10.0  # (550-500)/500
    assert s["forecast_profit_yoy_mid"] == 20.0   # (15+25)/2


def test_industry_zscore_groups_by_industry_and_report_date() -> None:
    """Phase 4c: D1/D2/D3 的 _z 列按 (tdx_l1, report_date) 分组, 组内 ≥5 才算。"""
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    # 5 只 T07 行业股票, 同一季度, 各自不同 YoY
    for i in range(5):
        code = f"60000{i}"
        conn.execute(
            "INSERT INTO dim_stock_sw_industry VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, "T07", "T0701", "T070101", "信息技术", "半导体", "设计"),
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"inst_{i}", code, "20250930", f"2025110{i+1}", "new_entry", 0.0, 5.0),
        )
        # holder_count: 10000 -> 10000 * (1 + i*0.10) → YoY = 0, 10, 20, 30, 40
        curr = 10000 * (1.0 + i * 0.10)
        conn.execute(
            "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
            (code, "2025-09-30", curr, None, None, None),
        )
        conn.execute(
            "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
            (code, "2024-09-30", 10000, None, None, None),
        )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="20250101", window_end="20260101")
    assert len(out) == 5
    # YoY 序列 [0, 10, 20, 30, 40], 均值 20, 标准差 sqrt(200)≈14.14
    # 中位样本 (YoY=20) 的 z 应 ≈ 0
    z_vals = sorted([s["holder_count_yoy_z"] for s in out if s["holder_count_yoy_z"] is not None])
    assert len(z_vals) == 5
    # 序列应对称：最小/最大绝对值相等，中间 ≈ 0
    assert abs(z_vals[0]) > 1.3 and z_vals[0] < 0
    assert abs(z_vals[-1]) > 1.3 and z_vals[-1] > 0
    assert abs(z_vals[2]) < 0.01


def test_industry_zscore_skips_groups_below_min_count() -> None:
    """Phase 4c: 同组 < 5 样本时 z 列应为 None (避免小样本噪声)。"""
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    # 仅 3 只 T07 股票, 低于 5 的阈值
    for i in range(3):
        code = f"60000{i}"
        conn.execute(
            "INSERT INTO dim_stock_sw_industry VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, "T07", "T0701", "T070101", "信息技术", "半导体", "设计"),
        )
        conn.execute(
            "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"inst_{i}", code, "20250930", f"2025110{i+1}", "new_entry", 0.0, 5.0),
        )
        conn.execute(
            "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
            (code, "2025-09-30", 10000 * (1.0 + i * 0.10), None, None, None),
        )
        conn.execute(
            "INSERT INTO raw_gpcw_detail VALUES (?, ?, ?, ?, ?, ?)",
            (code, "2024-09-30", 10000, None, None, None),
        )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="20250101", window_end="20260101")
    for s in out:
        assert s["holder_count_yoy"] is not None      # 原值仍算出
        assert s["holder_count_yoy_z"] is None        # z 列小样本时不算


def test_d8_survey_count_90d_only_past_window() -> None:
    conn = _make_conn()
    mkt_conn = _make_mkt_conn()
    _seed_common(conn)
    conn.execute(
        "INSERT INTO fact_institution_event VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("inst_A", "600001", "2025-09-30", "2025-11-01", "new_entry", 0.0, 5.0),
    )
    conn.executemany(
        "INSERT INTO raw_institution_surveys VALUES (?, ?, ?, ?)",
        [
            ("600001", "2025-07-15", "2025-07-16", 3),  # 在 [2025-08-03, 2025-11-01) 外
            ("600001", "2025-08-10", "2025-08-11", 5),  # 在窗口内
            ("600001", "2025-09-20", "2025-09-21", 8),  # 在窗口内
            ("600001", "2025-11-05", "2025-11-06", 2),  # 未来不算
        ],
    )
    conn.commit()
    out, _ = extract_training_matrix(conn, mkt_conn, window_start="2025-01-01", window_end="2026-01-01")
    assert out[0]["survey_count_90d"] == 2  # 只 08-10 和 09-20 在 [08-03, 11-01) 内

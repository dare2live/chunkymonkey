import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.qlib_full_engine import (
    _build_handler_config,
    _find_saved_recorder,
    _inject_custom_factors_into_handler,
    _load_financial_factors,
    _load_institution_factors,
    _load_northbound_factors,
    _load_quality_factors,
    _load_stage_factors,
    backfill_qlib_backtest_state,
    cleanup_failed_qlib_models,
    cleanup_stale_training_qlib_models,
    get_default_model_id,
    get_training_date_range,
    _load_turtle_factors,
    _resolve_training_segments,
    _resolve_workflow_benchmark,
    _resolve_training_stock_codes,
    ensure_tables as ensure_qlib_tables,
    get_model_summary,
    rebuild_model_backtest,
)
import services.qlib_full_engine as qlib_full_engine
from services.stock_turtle_engine import ensure_tables as ensure_turtle_tables
from services.stock_validation import _load_qlib_summary


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_qlib_tables(conn)
    ensure_turtle_tables(conn)
    return conn


def test_load_turtle_factors_encodes_system_and_state_flags():
    conn = _make_conn()
    try:
        conn.execute(
            """
            INSERT INTO fact_stock_turtle_features (
                snapshot_date, stock_code, atr_14_pct, breakout_dist_20_pct, breakout_dist_55_pct,
                entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20,
                turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
                preferred_system, turtle_setup_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-13", "600001", 2.4, 1.2, 0.8, 1, 1, 0, 0, 79.0, 68.0, 74.0, "S2", "S2突破触发"),
        )
        conn.execute(
            """
            INSERT INTO fact_stock_turtle_features (
                snapshot_date, stock_code, atr_14_pct, breakout_dist_20_pct, breakout_dist_55_pct,
                entry_signal_20, entry_signal_55, exit_signal_10, exit_signal_20,
                turtle_breakout_score, turtle_risk_score, turtle_execution_score_v1,
                preferred_system, turtle_setup_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-04-14", "000001", 5.8, -9.0, None, 0, 0, 1, 1, 28.0, 31.0, 35.0, "S1", "20日退出触发"),
        )
        conn.commit()

        factors = _load_turtle_factors(conn, ["600001", "000001"])

        assert set(factors.columns) >= {
            "turtle_atr_14_pct",
            "turtle_breakout_score",
            "turtle_risk_score",
            "turtle_execution_score",
            "turtle_system_s1",
            "turtle_system_s2",
            "turtle_state_breakout",
            "turtle_state_watch",
            "turtle_state_exit",
        }
        assert factors.loc[(pd.Timestamp("2026-04-13"), "SH600001"), "turtle_system_s2"] == 1
        assert factors.loc[(pd.Timestamp("2026-04-13"), "SH600001"), "turtle_state_breakout"] == 1
        assert factors.loc[(pd.Timestamp("2026-04-14"), "SZ000001"), "turtle_system_s1"] == 1
        assert factors.loc[(pd.Timestamp("2026-04-14"), "SZ000001"), "turtle_state_exit"] == 1
    finally:
        conn.close()


def test_load_financial_factors_uses_notice_date_as_of_history():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_financial_derived (
                stock_code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                roe REAL,
                debt_ratio REAL,
                current_ratio REAL,
                gross_margin REAL,
                net_margin REAL,
                revenue_yoy REAL,
                profit_yoy REAL,
                ocf_to_profit REAL,
                PRIMARY KEY (stock_code, report_date)
            );

            CREATE TABLE raw_gpcw_financial (
                stock_code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                notice_date TEXT,
                PRIMARY KEY (stock_code, report_date)
            );
            """
        )
        conn.executemany(
            "INSERT INTO fact_financial_derived (stock_code, report_date, roe, debt_ratio, current_ratio, gross_margin, net_margin, revenue_yoy, profit_yoy, ocf_to_profit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600001", "2024-12-31", 0.11, 0.22, 1.3, 0.44, 0.18, 0.09, 0.1, 0.8),
                ("600001", "2025-03-31", 0.15, 0.25, 1.4, 0.5, 0.2, 0.12, 0.13, 0.9),
            ],
        )
        conn.executemany(
            "INSERT INTO raw_gpcw_financial (stock_code, report_date, notice_date) VALUES (?, ?, ?)",
            [
                ("600001", "20241231", "20250403"),
                ("600001", "20250331", "20250430"),
            ],
        )
        conn.commit()

        factors = _load_financial_factors(conn, ["600001"])

        assert factors.loc[(pd.Timestamp("2025-04-03"), "SH600001"), "fin_roe"] == 0.11
        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "fin_roe"] == 0.15
    finally:
        conn.close()


def test_load_institution_factors_builds_historical_trend_from_holdings():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE market_raw_holdings (
                holder_name TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                report_date TEXT NOT NULL,
                notice_date TEXT,
                hold_ratio REAL,
                hold_market_cap REAL
            );
            """
        )
        conn.executemany(
            "INSERT INTO market_raw_holdings (holder_name, stock_code, report_date, notice_date, hold_ratio, hold_market_cap) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("机构A", "600001", "20241231", "20250403", 1.2, 100.0),
                ("机构B", "600001", "20241231", "20250403", 0.8, 80.0),
                ("机构A", "600001", "20250331", "20250430", 1.4, 120.0),
                ("机构B", "600001", "20250331", "20250430", 0.9, 90.0),
                ("机构C", "600001", "20250331", "20250430", 0.7, 70.0),
            ],
        )
        conn.commit()

        factors = _load_institution_factors(conn, ["600001"])

        assert factors.loc[(pd.Timestamp("2025-04-03"), "SH600001"), "inst_count_t0"] == 2
        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "inst_count_t0"] == 3
        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "inst_count_t1"] == 2
        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "inst_trend"] == 1
        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "inst_hold_ratio_change"] == 1.0
    finally:
        conn.close()


def test_inject_custom_factors_updates_handler_data_with_as_of_forward_fill():
    class DummyHandler:
        def __init__(self, frame):
            self._data = frame

        def fetch(self, col_set="feature"):
            return self._data.copy()

    index = pd.MultiIndex.from_tuples(
        [
            (pd.Timestamp("2025-04-29"), "SH600001"),
            (pd.Timestamp("2025-04-30"), "SH600001"),
            (pd.Timestamp("2025-05-01"), "SH600001"),
            (pd.Timestamp("2025-05-02"), "SH600001"),
        ],
        names=["datetime", "instrument"],
    )
    handler = DummyHandler(
        pd.DataFrame(
            {("feature", "alpha"): [1.0, 1.1, 1.2, 1.3]},
            index=index,
        )
    )
    custom_factors = pd.DataFrame(
        {"fin_roe": [0.11, 0.15]},
        index=pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2025-04-30"), "SH600001"),
                (pd.Timestamp("2025-05-02"), "SH600001"),
            ],
            names=["datetime", "instrument"],
        ),
    )

    injected = _inject_custom_factors_into_handler(handler, custom_factors)
    fin_col = ("feature", "fin_roe")

    assert injected == 1
    assert fin_col in handler._data.columns
    assert pd.isna(handler._data.loc[(pd.Timestamp("2025-04-29"), "SH600001"), fin_col])
    assert handler._data.loc[(pd.Timestamp("2025-04-30"), "SH600001"), fin_col] == 0.11
    assert handler._data.loc[(pd.Timestamp("2025-05-01"), "SH600001"), fin_col] == 0.11
    assert handler._data.loc[(pd.Timestamp("2025-05-02"), "SH600001"), fin_col] == 0.15


def test_inject_custom_factors_returns_zero_when_handler_data_missing():
    class EmptyHandler:
        _data = pd.DataFrame()

    custom_factors = pd.DataFrame(
        {"fin_roe": [0.11]},
        index=pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2025-04-30"), "SH600001")],
            names=["datetime", "instrument"],
        ),
    )

    assert _inject_custom_factors_into_handler(EmptyHandler(), custom_factors) == 0


def test_load_quality_factors_reads_historical_quality_snapshots():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_stock_quality_features (
                snapshot_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                roe REAL,
                roa_ak REAL,
                gross_margin REAL,
                ocf_to_profit REAL,
                debt_ratio REAL,
                current_ratio REAL,
                contract_to_revenue REAL,
                revenue_growth_yoy_ak REAL,
                net_profit_growth_yoy_ak REAL,
                quality_profit_raw REAL,
                quality_cash_raw REAL,
                quality_balance_raw REAL,
                quality_margin_raw REAL,
                quality_contract_raw REAL,
                quality_freshness_raw REAL,
                quality_capital_raw REAL,
                quality_efficiency_raw REAL,
                quality_growth_raw REAL,
                quality_score_v1 REAL,
                PRIMARY KEY (snapshot_date, stock_code)
            );
            """
        )
        conn.executemany(
            "INSERT INTO fact_stock_quality_features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("2025-04-30", "600001", 0.14, 0.08, 0.43, 0.9, 0.25, 1.3, 0.12, 18.0, 22.0, 24.0, 18.0, 15.0, 8.0, 3.0, 4.0, 2.0, 9.0, 6.0, 89.0),
                ("2025-05-31", "600001", 0.16, 0.09, 0.45, 1.0, 0.24, 1.4, 0.10, 20.0, 25.0, 26.0, 19.0, 16.0, 9.0, 3.5, 4.5, 2.5, 10.0, 7.0, 92.0),
            ],
        )
        conn.commit()

        factors = _load_quality_factors(conn, ["600001"])

        assert factors.loc[(pd.Timestamp("2025-04-30"), "SH600001"), "qual_score_v1"] == 89.0
        assert factors.loc[(pd.Timestamp("2025-05-31"), "SH600001"), "qual_profit_raw"] == 26.0
    finally:
        conn.close()


def test_load_stage_factors_encodes_path_and_gate_flags():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_stock_stage_features (
                snapshot_date TEXT NOT NULL,
                stock_code TEXT NOT NULL,
                path_state TEXT,
                stock_gate TEXT,
                return_1m REAL,
                return_3m REAL,
                return_6m REAL,
                return_12m REAL,
                dist_ma120_pct REAL,
                dist_ma250_pct REAL,
                above_ma250 INTEGER,
                max_drawdown_60d REAL,
                amount_ratio_20_120 REAL,
                volatility_20d REAL,
                amplitude_20d REAL,
                generic_stage_raw REAL,
                stage_type_adjust_raw REAL,
                stage_score_v1 REAL,
                PRIMARY KEY (snapshot_date, stock_code)
            );
            """
        )
        conn.execute(
            "INSERT INTO fact_stock_stage_features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2025-05-06", "000001", "温和验证", "follow", 8.0, 15.0, 22.0, 30.0, 5.0, 12.0, 1, 9.0, 1.4, 2.5, 18.0, 63.0, 4.0, 67.0),
        )
        conn.commit()

        factors = _load_stage_factors(conn, ["000001"])

        assert factors.loc[(pd.Timestamp("2025-05-06"), "SZ000001"), "stage_score_v1"] == 67.0
        assert factors.loc[(pd.Timestamp("2025-05-06"), "SZ000001"), "stage_path_mild"] == 1
        assert factors.loc[(pd.Timestamp("2025-05-06"), "SZ000001"), "stage_gate_follow"] == 1
    finally:
        conn.close()


def test_load_northbound_factors_builds_historical_deltas():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE fact_northbound_daily (
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                hold_shares REAL,
                hold_market_cap REAL,
                hold_ratio REAL,
                change_shares REAL,
                trade_date TEXT NOT NULL,
                updated_at TEXT,
                PRIMARY KEY (stock_code, trade_date)
            );
            """
        )
        conn.executemany(
            "INSERT INTO fact_northbound_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("600001", "样本股", 100.0, 500.0, 1.2, 8.0, "2025-05-06", "2025-05-06T10:00:00"),
                ("600001", "样本股", 112.0, 560.0, 1.35, 12.0, "2025-05-07", "2025-05-07T10:00:00"),
            ],
        )
        conn.commit()

        factors = _load_northbound_factors(conn, ["600001"])

        assert round(factors.loc[(pd.Timestamp("2025-05-07"), "SH600001"), "nb_hold_ratio_change"], 6) == 0.15
        assert factors.loc[(pd.Timestamp("2025-05-07"), "SH600001"), "nb_hold_market_cap_change"] == 60.0
        assert factors.loc[(pd.Timestamp("2025-05-07"), "SH600001"), "nb_net_inflow_flag"] == 1
    finally:
        conn.close()


def test_get_default_model_id_prefers_active_model_rank():
    conn = _make_conn()
    try:
        conn.executemany(
            """
            INSERT INTO qlib_model_state (
                model_id, status, ic_mean, rank_ic_mean, test_top50_avg_return,
                created_at, backtest_status, backtest_benchmark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("model_old", "trained", 0.04, 0.05, 4.0, "2026-04-13T09:00:00", "success", "SH000300"),
                ("model_new", "trained", 0.02, 0.03, 1.0, "2026-04-14T09:00:00", "success", "SH000300"),
            ],
        )
        conn.executemany(
            "INSERT INTO qlib_backtest_result (model_id, backtest_id, strategy, sharpe_ratio, calmar_ratio, max_drawdown, annual_return, turnover, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("model_old", "bt_old", "TopK", 1.3, 1.1, -0.12, 12.0, 0.4, "{}", "2026-04-13T10:00:00"),
                ("model_new", "bt_new", "TopK", 0.6, 0.5, -0.20, 6.0, 0.5, "{}", "2026-04-14T10:00:00"),
            ],
        )
        conn.commit()

        assert get_default_model_id(conn) == "model_old"

        summary = get_model_summary(conn, model_id="model_old")
        assert summary["is_active"] is True
        assert summary["performance_rank"] is not None
    finally:
        conn.close()


def test_resolve_training_segments_supports_explicit_and_derived_starts():
    derived = _resolve_training_segments({
        "train_start": "2023-01-01",
        "train_end": "2025-03-31",
        "valid_end": "2025-09-30",
        "test_end": "2026-01-31",
    })
    explicit = _resolve_training_segments({
        "train_start": "2023-01-01",
        "train_end": "2025-03-31",
        "valid_start": "2025-04-15",
        "valid_end": "2025-09-30",
        "test_start": "2025-10-15",
        "test_end": "2026-01-31",
    })

    assert derived["valid"] == ("2025-04-01", "2025-09-30")
    assert derived["test"] == ("2025-10-09", "2026-01-31")
    assert explicit["valid"] == ("2025-04-15", "2025-09-30")
    assert explicit["test"] == ("2025-10-15", "2026-01-31")


def test_get_training_date_range_uses_calendar_boundaries(tmp_path):
    data_dir = tmp_path / "qlib_data"
    calendar_dir = data_dir / "calendars"
    calendar_dir.mkdir(parents=True)
    (calendar_dir / "day.txt").write_text(
        "\n".join(
            [
                "2025-01-02",
                "2025-01-03",
                "2025-01-06",
                "2025-01-07",
                "2025-01-08",
                "2025-01-09",
                "2025-01-10",
                "2025-01-13",
                "2025-01-14",
                "2025-01-15",
                "2025-01-16",
                "2025-01-17",
                "2025-01-20",
                "2025-01-21",
                "2025-01-22",
                "2025-01-23",
                "2025-01-24",
                "2025-01-27",
                "2025-01-28",
                "2025-01-29",
            ]
        ),
        encoding="utf-8",
    )

    date_range = get_training_date_range(str(data_dir))
    segments = _resolve_training_segments({}, data_dir=str(data_dir))

    assert date_range["source"] == "calendar"
    assert date_range["trading_days"] == 20
    assert date_range["calendar_start"] == "2025-01-02"
    assert date_range["calendar_end"] == "2025-01-29"
    assert date_range["train_start"] == "2025-01-02"
    assert date_range["train_end"] == "2025-01-21"
    assert date_range["valid_start"] == "2025-01-22"
    assert date_range["valid_end"] == "2025-01-24"
    assert date_range["test_start"] == "2025-01-27"
    assert date_range["test_end"] == "2025-01-29"
    assert segments == {
        "train": ("2025-01-02", "2025-01-21"),
        "valid": ("2025-01-22", "2025-01-24"),
        "test": ("2025-01-27", "2025-01-29"),
    }


def test_build_handler_config_marks_lightweight_base_stack_when_alpha158_disabled():
    handler_config = _build_handler_config(
        start_time="2025-01-02",
        end_time="2025-01-29",
        instruments=["SH600001"],
        use_alpha158=False,
    )

    assert handler_config == {
        "handler_kind": "ohlcv_light",
        "start_time": "2025-01-02",
        "end_time": "2025-01-29",
        "instruments": ["SH600001"],
    }


def test_get_model_summary_and_stock_validation_expose_turtle_feature_stack():
    conn = _make_conn()
    try:
        conn.execute(
            """
            INSERT INTO qlib_model_state (
                model_id, status, train_params_json, created_at, stock_count, factor_count,
                backtest_status, backtest_benchmark, backtest_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "model_turtle",
                "trained",
                json.dumps({
                    "use_alpha158": True,
                    "use_financial": True,
                    "use_institution": False,
                    "use_turtle": True,
                    "use_quality": True,
                    "use_stage": True,
                    "use_northbound": False,
                    "universe_source": "current_trend",
                }, ensure_ascii=False),
                "2026-04-13T09:00:00",
                2,
                3,
                "failed",
                "SZ159919",
                "benchmark missing",
            ),
        )
        conn.executemany(
            "INSERT INTO qlib_factor_importance (model_id, factor_name, importance, factor_group) VALUES (?, ?, ?, ?)",
            [
                ("model_turtle", "alpha_feature", 10.0, "alpha158"),
                ("model_turtle", "turtle_execution_score", 8.0, "turtle"),
            ],
        )
        conn.commit()

        summary = get_model_summary(conn, model_id="model_turtle")
        qlib_summary = _load_qlib_summary(conn)

        assert summary["train_params"]["use_turtle"] is True
        assert summary["train_params"]["use_quality"] is True
        assert summary["train_params"]["use_stage"] is True
        assert summary["train_params"]["universe_source"] == "current_trend"
        assert summary["train_params"]["use_benchmark"] is True
        assert summary["backtest_status"] == "failed"
        assert summary["backtest_benchmark"] == "SZ159919"
        assert summary["backtest_error"] == "benchmark missing"
        assert any(item["factor_group"] == "turtle" for item in summary["factor_groups"])
        assert qlib_summary["feature_stack_label"] == "Alpha158 + financial + turtle + quality + stage"
        assert qlib_summary["backtest_status"] == "failed"
    finally:
        conn.close()


def test_resolve_training_stock_codes_supports_current_trend_universe():
    conn = _make_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE dim_active_a_stock (
                stock_code TEXT PRIMARY KEY
            );

            CREATE TABLE excluded_stocks (
                stock_code TEXT PRIMARY KEY
            );

            CREATE TABLE mart_stock_trend (
                stock_code TEXT PRIMARY KEY
            );
            """
        )
        conn.executemany(
            "INSERT INTO dim_active_a_stock (stock_code) VALUES (?)",
            [("000001",), ("000002",), ("600001",)],
        )
        conn.executemany(
            "INSERT INTO mart_stock_trend (stock_code) VALUES (?)",
            [("000002",), ("600001",)],
        )
        conn.execute("INSERT INTO excluded_stocks (stock_code) VALUES (?)", ("000002",))
        conn.commit()

        assert _resolve_training_stock_codes(conn, universe_source="active_a_stock") == ["000001", "600001"]
        assert _resolve_training_stock_codes(conn, universe_source="current_trend") == ["600001"]
        assert _resolve_training_stock_codes(conn, universe_source="active_a_stock", sample_stock_limit=1) == ["000001"]

        try:
            _resolve_training_stock_codes(conn, universe_source="unknown")
        except ValueError as exc:
            assert "训练宇宙" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid universe_source")
    finally:
        conn.close()


def test_resolve_workflow_benchmark_defaults_to_available_fallback(tmp_path):
    data_dir = tmp_path / "qlib_data"
    instruments_dir = data_dir / "instruments"
    instruments_dir.mkdir(parents=True)
    (instruments_dir / "all.txt").write_text(
        "SZ159915\t2023-01-03\t2026-04-10\n"
        "SZ159919\t2023-01-03\t2026-04-10\n",
        encoding="utf-8",
    )

    assert _resolve_workflow_benchmark(str(data_dir), {}) == "SZ159919"
    assert _resolve_workflow_benchmark(str(data_dir), {"benchmark": "SZ159915"}) == "SZ159915"
    assert _resolve_workflow_benchmark(str(data_dir), {"use_benchmark": False}) is None


def test_backfill_qlib_backtest_state_deletes_failed_models_and_preserves_legacy_trained(tmp_path):
    conn = _make_conn()
    try:
        failed_model_path = tmp_path / "model_failed.pkl"
        failed_model_path.write_text("failed", encoding="utf-8")
        conn.executemany(
            """
            INSERT INTO qlib_model_state (
                model_id, status, train_params_json, created_at, stock_count, factor_count, error, model_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "model_missing",
                    "trained",
                    json.dumps({"use_alpha158": True}, ensure_ascii=False),
                    "2026-04-13T10:00:00",
                    10,
                    5,
                    None,
                    None,
                ),
                (
                    "model_failed",
                    "failed",
                    json.dumps({"use_benchmark": False}, ensure_ascii=False),
                    "2026-04-13T10:05:00",
                    0,
                    0,
                    "manual-stop",
                    str(failed_model_path),
                ),
            ],
        )
        conn.commit()

        updated = backfill_qlib_backtest_state(conn)
        missing_row = conn.execute(
            "SELECT backtest_status, backtest_benchmark, backtest_error FROM qlib_model_state WHERE model_id = ?",
            ("model_missing",),
        ).fetchone()
        failed_row = conn.execute(
            "SELECT backtest_status, backtest_benchmark, backtest_error FROM qlib_model_state WHERE model_id = ?",
            ("model_failed",),
        ).fetchone()

        assert updated == 2
        assert missing_row["backtest_status"] == "missing"
        assert missing_row["backtest_benchmark"] == "SH000300"
        assert missing_row["backtest_error"] is None
        assert failed_row is None
        assert not failed_model_path.exists()
    finally:
        conn.close()


def test_cleanup_failed_qlib_models_deletes_related_rows():
    conn = _make_conn()
    try:
        conn.execute(
            "INSERT INTO qlib_model_state (model_id, status, created_at) VALUES (?, ?, ?)",
            ("model_failed", "failed", "2026-04-13T10:05:00"),
        )
        conn.execute(
            "INSERT INTO qlib_predictions (model_id, stock_code, stock_name) VALUES (?, ?, ?)",
            ("model_failed", "000001", "平安银行"),
        )
        conn.execute(
            "INSERT INTO qlib_factor_importance (model_id, factor_name, importance, factor_group) VALUES (?, ?, ?, ?)",
            ("model_failed", "factor_x", 1.0, "alpha158"),
        )
        conn.execute(
            "INSERT INTO qlib_backtest_result (model_id, backtest_id, strategy) VALUES (?, ?, ?)",
            ("model_failed", "model_failed_default_day", "TopkDropoutStrategy"),
        )
        conn.commit()

        result = cleanup_failed_qlib_models(conn)
        remaining = conn.execute(
            "SELECT COUNT(*) AS cnt FROM qlib_model_state WHERE model_id = ?",
            ("model_failed",),
        ).fetchone()

        assert len(result) == 1
        assert result[0]["deleted_rows"]["qlib_predictions"] == 1
        assert result[0]["deleted_rows"]["qlib_factor_importance"] == 1
        assert result[0]["deleted_rows"]["qlib_backtest_result"] == 1
        assert result[0]["deleted_rows"]["qlib_model_state"] == 1
        assert remaining["cnt"] == 0
    finally:
        conn.close()


def test_cleanup_stale_training_qlib_models_deletes_only_orphan_rows(tmp_path):
    conn = _make_conn()
    try:
        stale_model_path = tmp_path / "stale_training.pkl"
        stale_model_path.write_text("stale", encoding="utf-8")
        conn.executemany(
            """
            INSERT INTO qlib_model_state (
                model_id, status, created_at, finished_at, model_path
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("stale_training", "training", "2026-04-09T14:28:39.092711", None, str(stale_model_path)),
                ("fresh_training", "training", "2026-04-13T14:28:39.092711", None, None),
            ],
        )
        conn.execute(
            "INSERT INTO qlib_predictions (model_id, stock_code, stock_name) VALUES (?, ?, ?)",
            ("fresh_training", "000001", "平安银行"),
        )
        conn.commit()

        result = cleanup_stale_training_qlib_models(conn, "2026-04-13T00:00:00")
        stale_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM qlib_model_state WHERE model_id = ?",
            ("stale_training",),
        ).fetchone()
        fresh_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM qlib_model_state WHERE model_id = ?",
            ("fresh_training",),
        ).fetchone()

        assert len(result) == 1
        assert result[0]["model_id"] == "stale_training"
        assert stale_row["cnt"] == 0
        assert fresh_row["cnt"] == 1
        assert not stale_model_path.exists()
    finally:
        conn.close()


def test_rebuild_model_backtest_reuses_existing_model_without_resync(monkeypatch):
    conn = _make_conn()
    try:
        conn.execute(
            """
            INSERT INTO qlib_model_state (
                model_id, status, train_start, train_end, valid_end, test_end,
                train_params_json, created_at, stock_count, factor_count, model_path,
                backtest_status, backtest_benchmark
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "model_replay",
                "trained",
                "2024-01-01",
                "2024-06-30",
                "2024-08-31",
                "2024-10-31",
                json.dumps(
                    {
                        "use_alpha158": True,
                        "use_financial": True,
                        "use_institution": False,
                        "use_turtle": True,
                        "use_benchmark": True,
                        "benchmark": "SH000300",
                        "universe_source": "current_trend",
                    },
                    ensure_ascii=False,
                ),
                "2026-04-13T10:00:00",
                3,
                10,
                "/tmp/model_replay.pkl",
                "missing",
                "SH000300",
            ),
        )
        conn.commit()

        monkeypatch.setattr(qlib_full_engine, "_QLIB_AVAILABLE", True)
        monkeypatch.setattr(qlib_full_engine, "init_qlib", lambda data_dir=None: True)

        captured = {}

        def fake_load_bundle(smart_conn, model_row, params):
            captured["loaded_model_id"] = model_row["model_id"]
            captured["loaded_params"] = dict(params)
            return object(), object()

        def fake_persist(smart_conn, *, model_id, dataset, model, params):
            captured["persist_model_id"] = model_id
            smart_conn.execute(
                """
                INSERT OR REPLACE INTO qlib_backtest_result (
                    model_id, backtest_id, strategy, annual_return, sharpe_ratio, max_drawdown, turnover, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    f"{model_id}_default_day",
                    "TopkDropoutStrategy(topk=50,n_drop=5)",
                    1.23,
                    2.34,
                    -0.12,
                    0.45,
                    "2026-04-13T10:10:00",
                ),
            )
            smart_conn.execute(
                """
                UPDATE qlib_model_state
                SET backtest_status = ?, backtest_benchmark = ?, backtest_error = NULL
                WHERE model_id = ?
                """,
                ("success", "SZ159919", model_id),
            )
            smart_conn.commit()
            return {"backtest_id": f"{model_id}_default_day", "backtest_error": None}

        monkeypatch.setattr(qlib_full_engine, "_load_saved_model_replay_bundle", fake_load_bundle)
        monkeypatch.setattr(qlib_full_engine, "_persist_workflow_records", fake_persist)

        result = rebuild_model_backtest(conn, model_id="model_replay", data_dir="/tmp/qlib_data")
        summary = get_model_summary(conn, model_id="model_replay")

        assert captured["loaded_model_id"] == "model_replay"
        assert captured["persist_model_id"] == "model_replay"
        assert captured["loaded_params"]["use_turtle"] is True
        assert result["backtest_id"] == "model_replay_default_day"
        assert result["latest_backtest"]["status"] == "ok"
        assert summary["backtest_status"] == "success"
        assert summary["backtest_benchmark"] == "SZ159919"
        assert summary["latest_backtest"]["backtest_id"] == "model_replay_default_day"
    finally:
        conn.close()


def test_find_saved_recorder_prefers_matching_model_id(tmp_path, monkeypatch):
    mlruns_root = tmp_path / "mlruns"
    run_dir = mlruns_root / "exp123" / "run456"
    (run_dir / "params").mkdir(parents=True)
    (run_dir / "params" / "model_id").write_text("model_replay", encoding="utf-8")
    (mlruns_root / "exp123" / "meta.yaml").write_text("name: ReplayExperiment\n", encoding="utf-8")

    monkeypatch.setattr(qlib_full_engine, "_candidate_mlruns_roots", lambda: [mlruns_root])

    result = _find_saved_recorder("model_replay")

    assert result == {
        "uri": str(mlruns_root),
        "experiment_id": "exp123",
        "experiment_name": "ReplayExperiment",
        "recorder_id": "run456",
    }
from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import duck_mem
from scripts import build_feature_panel_duck as subject


pytestmark = pytest.mark.pipeline


def _iso_days(start: date, count: int) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


def _yyyymmdd(day: str) -> str:
    return day.replace("-", "")


def _create_sources(con) -> None:
    subject.execute_script(
        con,
        """
        CREATE SCHEMA market;
        CREATE SCHEMA smartmoney;
        CREATE TABLE market.price_kline_tdxhub (
            code TEXT,
            date TEXT,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume DOUBLE,
            amount DOUBLE,
            freq TEXT,
            adjust TEXT,
            source_name TEXT,
            source_tier SMALLINT,
            is_fallback BOOLEAN
        );
        CREATE VIEW market.v_price_kline_qfq AS
            SELECT code, date, freq, adjust, open, high, low, close, volume, amount,
                   source_name, source_tier, is_fallback
            FROM market.price_kline_tdxhub;
        CREATE TABLE smartmoney.dim_trading_calendar (
            trade_date TEXT PRIMARY KEY,
            is_trading INTEGER
        );
        CREATE TABLE smartmoney.raw_margin_daily (
            stock_code TEXT,
            trade_date TEXT,
            rz_balance DOUBLE
        );
        CREATE TABLE smartmoney.fact_institution_event (
            stock_code TEXT,
            notice_date TEXT
        );
        CREATE TABLE smartmoney.fact_executive_trade_event (
            stock_code TEXT,
            notice_date TEXT,
            direction TEXT,
            total_change_pct_total DOUBLE
        );
        CREATE TABLE smartmoney.fact_lhb_event (
            stock_code TEXT,
            trade_date TEXT,
            is_inst_net_buy INTEGER
        );
        CREATE TABLE smartmoney.fact_shareholder_plan_tdx_f10 (
            stock_code TEXT,
            source_available_date TEXT,
            source_notice_date TEXT,
            direction TEXT,
            progress TEXT,
            target_amount_min BIGINT,
            target_amount_max BIGINT,
            fetched_at TEXT
        );
        CREATE TABLE smartmoney.fact_fundamental_quarterly (
            stock_code TEXT,
            report_date TEXT,
            shareholder_count DOUBLE,
            inst_count DOUBLE,
            fund_count DOUBLE,
            qfii_count DOUBLE,
            yjyg_lower_pct DOUBLE,
            yjyg_upper_pct DOUBLE,
            roe DOUBLE,
            eps_basic DOUBLE
        );
        CREATE TABLE smartmoney.dim_stock_tdx_industry (
            stock_code TEXT,
            tdx_l1 TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_fundamental_quarterly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "20251231", 100.0, 5.0, 2.0, 1.0, 0.1, 0.2, 0.12, 0.8),
            ("000002", "20251231", 120.0, 6.0, 3.0, 1.0, 0.2, 0.3, 0.10, 0.7),
        ],
    )
    con.executemany(
        "INSERT INTO smartmoney.dim_stock_tdx_industry VALUES (?, ?)",
        [("000001", "bank"), ("000002", "tech")],
    )


def _insert_days(con, start: date, count: int) -> list[str]:
    days = _iso_days(start, count)
    kline_rows = []
    margin_rows = []
    calendar_rows = []
    for idx, day in enumerate(days):
        calendar_rows.append((day, 1))
        for offset, code in enumerate(["000001", "000002", "510300"]):
            close = 10.0 + (start.toordinal() - date(2026, 1, 1).toordinal() + idx) * (0.2 + offset * 0.03) + offset
            kline_rows.append(
                (
                    code,
                    day,
                    close - 0.1,
                    close + 0.2,
                    close - 0.3,
                    close,
                    1000.0 + idx * 10 + offset,
                    1_000_000.0 + idx * 1000 + offset,
                    "daily",
                    "qfq",
                    "tdxhub",
                    1,
                    False,
                )
            )
            if code != "510300":
                margin_rows.append((code, day if idx % 2 else _yyyymmdd(day), 1000.0 + idx * 5 + offset))
    con.executemany("INSERT OR REPLACE INTO smartmoney.dim_trading_calendar VALUES (?, ?)", calendar_rows)
    con.executemany("INSERT INTO market.price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)
    con.executemany("INSERT INTO smartmoney.raw_margin_daily VALUES (?, ?, ?)", margin_rows)
    return days


def _seed_sources(con, day_count: int = 50) -> list[str]:
    _create_sources(con)
    days = _insert_days(con, date(2026, 1, 1), day_count)
    con.executemany(
        "INSERT INTO smartmoney.fact_institution_event VALUES (?, ?)",
        [("000001", "2026-01-03"), ("000002", "20260104")],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_executive_trade_event VALUES (?, ?, ?, ?)",
        [("000001", "2026-01-05", "buy", 1.5), ("000002", "20260106", "buy", 0.5)],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_lhb_event VALUES (?, ?, ?)",
        [("000001", "2026-01-07", 1), ("000002", "20260108", 1)],
    )
    con.executemany(
        "INSERT INTO smartmoney.fact_shareholder_plan_tdx_f10 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("000001", "2026-01-04", "2026-01-04", "增持计划", "完成", 3_000_000_000, 3_300_000_000, "2026-01-04T10:00:00"),
            ("000001", "2026-01-10", "2026-01-10", "减持计划", "进行中", None, 500_000_000, "2026-01-10T10:00:00"),
        ],
    )
    return days


def test_plan_incremental_window_expands_for_label_and_rolling_context():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=40)
        subject.execute_script(con, subject.PANEL_SCHEMA_DDL)
        con.execute(
            "INSERT INTO fact_feature_panel (stock_code, date, close) VALUES ('000001', ?, 1.0)",
            [days[29]],
        )

        plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)

        assert plan["noop"] is False
        assert plan["existing_max_date"] == days[29]
        assert plan["source_max_date"] == days[-1]
        assert plan["write_start_date"] == days[26]
        assert plan["read_start_date"] == days[21]
    finally:
        con.close()


def test_incremental_build_replaces_dirty_window_and_preserves_older_rows():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=45)
        summary = subject._build_panel_with_connection(con, days[0])
        assert summary["rows"] > 0
        con.execute(
            "UPDATE fact_feature_panel SET close = 777.0 WHERE stock_code = '000001' AND date = ?",
            [days[1]],
        )
        new_days = _insert_days(con, date.fromisoformat(days[-1]) + timedelta(days=1), 5)
        plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)

        incremental_summary = subject._build_panel_with_connection(
            con,
            str(plan["read_start_date"]),
            reset=False,
            write_start_date=str(plan["write_start_date"]),
        )
        preserved = con.execute(
            "SELECT close FROM fact_feature_panel WHERE stock_code = '000001' AND date = ?",
            [days[1]],
        ).fetchone()[0]
        max_date = con.execute("SELECT MAX(date) FROM fact_feature_panel").fetchone()[0]
        duplicate_count = con.execute(
            """
            SELECT COUNT(*)
              FROM (
                SELECT stock_code, date, COUNT(*) AS n
                  FROM fact_feature_panel
                 GROUP BY stock_code, date
                HAVING COUNT(*) > 1
              )
            """
        ).fetchone()[0]

        assert preserved == 777.0
        assert max_date == new_days[-1]
        assert duplicate_count == 0
        assert incremental_summary["rows"] >= summary["rows"]
    finally:
        con.close()


def test_validate_feature_panel_detects_duplicate_keys_and_low_close_coverage():
    con = duck_mem()
    try:
        con.execute(
            """
            CREATE TABLE fact_feature_panel (
                stock_code TEXT,
                date TEXT,
                close DOUBLE,
                forward_ret_20d DOUBLE
            )
            """
        )
        con.executemany(
            "INSERT INTO fact_feature_panel VALUES (?, ?, ?, ?)",
            [
                ("000001", "2026-01-01", None, 0.1),
                ("000001", "2026-01-01", 10.0, 0.1),
            ],
        )

        result = subject.validate_feature_panel(con, min_close_coverage=0.99)

        assert result["status"] == "failed"
        assert any("duplicate panel keys" in blocker for blocker in result["blockers"])
        assert any("close coverage" in blocker for blocker in result["blockers"])
    finally:
        con.close()


def test_record_feature_panel_validation_persists_audit_summary():
    con = duck_mem()
    try:
        _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, "2026-01-01")

        result = subject.validate_feature_panel(con)
        subject.record_feature_panel_validation(con, result, run_mode="unit")

        row = con.execute(
            """
            SELECT run_mode, status, rows, duplicate_keys, source_lineage_coverage,
                   source_fallback_ratio, source_distribution_json,
                   source_watermark_hash, source_watermarks_json,
                   feature_registry_json, blockers_json
              FROM mart_feature_panel_validation
             WHERE run_mode = 'unit'
            """
        ).fetchone()

        assert row["run_mode"] == "unit"
        assert row["status"] == "passed"
        assert row["rows"] > 0
        assert row["duplicate_keys"] == 0
        assert row["source_lineage_coverage"] == pytest.approx(1.0)
        assert row["source_fallback_ratio"] == pytest.approx(0.0)
        assert '"source_tier": 1' in row["source_distribution_json"]
        assert row["source_watermark_hash"]
        assert "kline_daily" in row["source_watermarks_json"]
        assert "registered_features" in row["feature_registry_json"]
        assert row["blockers_json"] == "[]"
    finally:
        con.close()


def test_validate_and_record_feature_panel_persists_validate_only_audit():
    con = duck_mem()
    try:
        _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, "2026-01-01")

        result = subject.validate_and_record_feature_panel(con, run_mode="validate-only")

        assert result["status"] == "passed"
        row = con.execute(
            """
            SELECT status, rows, duplicate_keys
              FROM mart_feature_panel_validation
             WHERE run_mode = 'validate-only'
            """
        ).fetchone()
        assert row["status"] == "passed"
        assert row["rows"] > 0
        assert row["duplicate_keys"] == 0
    finally:
        con.close()


def test_incremental_plan_rebuilds_when_non_kline_source_snapshot_changes():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, days[0])
        validation = subject.validate_feature_panel(con)
        subject.record_feature_panel_validation(con, validation, run_mode="unit")

        clean_plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)

        con.execute(
            "INSERT INTO smartmoney.raw_margin_daily VALUES ('000001', ?, 1234.0)",
            [days[-1]],
        )
        dirty_plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)

        assert clean_plan["noop"] is True
        assert dirty_plan["noop"] is False
        assert dirty_plan["source_max_date"] == days[-1]
        assert "margin_daily" in dirty_plan["changed_source_domains"]
        assert "changed feature-panel source snapshot" in dirty_plan["reason"]
        assert dirty_plan["write_start_date"] <= days[-1]
        assert dirty_plan["read_start_date"] <= dirty_plan["write_start_date"]
    finally:
        con.close()


def test_incremental_plan_uses_row_level_change_metadata_for_historical_source_update():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=80)
        con.execute("ALTER TABLE smartmoney.raw_margin_daily ADD COLUMN ingested_at TIMESTAMP")
        con.execute("UPDATE smartmoney.raw_margin_daily SET ingested_at = '2026-01-01T00:00:00'")
        subject._build_panel_with_connection(con, days[0])
        validation = subject.validate_feature_panel(con)
        subject.record_feature_panel_validation(con, validation, run_mode="unit")

        clean_plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)
        con.execute(
            """
            INSERT INTO smartmoney.raw_margin_daily
            (stock_code, trade_date, rz_balance, ingested_at)
            VALUES ('000001', ?, 4321.0, '2099-01-01T00:00:00')
            """,
            [days[10]],
        )
        dirty_plan = subject.plan_incremental_window(con, lookback_days=5, label_lookback_days=3)

        assert clean_plan["noop"] is True
        assert dirty_plan["noop"] is False
        assert dirty_plan["write_start_date"] == days[10]
        assert dirty_plan["read_start_date"] == days[5]
        assert "row-level dirty windows: margin_daily" in dirty_plan["reason"]
        assert dirty_plan["row_level_dirty_windows"][0]["domain"] == "margin_daily"
        assert dirty_plan["row_level_dirty_windows"][0]["changed_rows"] == 1
    finally:
        con.close()


def test_feature_group_scoped_backfill_updates_only_selected_group_columns():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, days[0])
        target_day = days[10]
        con.execute(
            """
            UPDATE fact_feature_panel
               SET ret_20d = 999.0,
                   inst_event_count_30d = 777
             WHERE stock_code = '000001'
               AND date = ?
            """,
            [target_day],
        )
        update_columns = subject.feature_group_columns(["event_activity"])

        summary = subject._build_panel_with_connection(
            con,
            days[0],
            reset=False,
            write_start_date=days[0],
            update_columns=update_columns,
        )
        row = con.execute(
            """
            SELECT ret_20d, inst_event_count_30d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = ?
            """,
            [target_day],
        ).fetchone()

        assert row["ret_20d"] == pytest.approx(999.0)
        assert row["inst_event_count_30d"] != 777
        assert summary["updated_rows"] > 0
        assert summary["updated_columns"] >= 1
    finally:
        con.close()


def test_feature_block_plan_limits_scoped_compute_dependencies():
    event_columns = subject.feature_group_columns(["event_activity"])
    cross_columns = subject.feature_group_columns(["cross_sectional"])

    assert subject.feature_block_plan(event_columns) == ["price_shape", "event_activity"]
    assert subject.feature_block_plan(cross_columns) == ["price_shape", "margin", "cross_sectional"]


def test_event_scoped_backfill_skips_unrelated_margin_source():
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, days[0])
        target_day = days[10]
        con.execute(
            """
            UPDATE fact_feature_panel
               SET inst_event_count_30d = 777
             WHERE stock_code = '000001'
               AND date = ?
            """,
            [target_day],
        )
        con.execute("DROP TABLE smartmoney.raw_margin_daily")

        summary = subject._build_panel_with_connection(
            con,
            days[0],
            reset=False,
            write_start_date=days[0],
            update_columns=subject.feature_group_columns(["event_activity"]),
        )
        row = con.execute(
            """
            SELECT inst_event_count_30d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
               AND date = ?
            """,
            [target_day],
        ).fetchone()

        assert row["inst_event_count_30d"] != 777
        assert summary["updated_rows"] > 0
    finally:
        con.close()


def test_large_scoped_backfill_uses_rewrite_strategy(monkeypatch: pytest.MonkeyPatch):
    con = duck_mem()
    try:
        days = _seed_sources(con, day_count=45)
        subject._build_panel_with_connection(con, days[0])
        monkeypatch.setattr(subject, "SCOPED_REWRITE_ROW_THRESHOLD", 1)

        summary = subject._build_panel_with_connection(
            con,
            days[0],
            reset=False,
            write_start_date=days[0],
            update_columns=subject.feature_group_columns(["labels"]),
        )
        row = con.execute(
            """
            SELECT forward_ret_5d, forward_ret_10d, forward_ret_20d, forward_ret_60d,
                   forward_ret_90d
              FROM fact_feature_panel
             WHERE stock_code = '000001'
             ORDER BY date
             LIMIT 1
            """
        ).fetchone()

        assert summary["update_strategy"] == "rewrite"
        assert row["forward_ret_5d"] is not None
        assert row["forward_ret_10d"] is not None
        assert row["forward_ret_20d"] is not None
        assert row["forward_ret_60d"] is None
        assert row["forward_ret_90d"] is None
    finally:
        con.close()


def test_feature_input_columns_exclude_pit_labels_and_identifiers():
    inputs = subject.feature_input_columns()

    assert "stock_code" not in inputs
    assert "date" not in inputs
    assert "kline_source_name" not in inputs
    assert "kline_source_tier" not in inputs
    assert "kline_is_fallback" not in inputs
    for label in ("forward_ret_5d", "forward_ret_10d", "forward_ret_20d", "forward_ret_60d", "forward_ret_90d"):
        assert label not in inputs
    assert "close" in inputs
    assert "ret_20d" in inputs

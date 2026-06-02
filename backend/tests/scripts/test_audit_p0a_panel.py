from __future__ import annotations

import duckdb

from scripts import audit_p0a_panel as subject


class _CountingConn:
    def __init__(self, conn) -> None:
        self._conn = conn
        self.execute_calls = 0

    def execute(self, *args, **kwargs):
        self.execute_calls += 1
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_check_v3_pit_confidence_batches_null_rate_queries() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_p0a_feature_label_panel_v3 (
                industry_pit_confidence TEXT,
                survey_count_60d DOUBLE,
                pe_ttm_z_1y DOUBLE,
                sector_ret_60d DOUBLE,
                inst_quality_wavg DOUBLE,
                inst_holder_cnt DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_p0a_feature_label_panel_v3 VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("current_label_fallback", 10.0, 1.0, 0.10, 0.90, 3.0),
                ("current_label_fallback", None, 2.0, None, 0.80, None),
                ("strict_pit", 20.0, None, 0.20, None, 5.0),
            ],
        )
        counting = _CountingConn(conn)

        results = subject.check_v3_pit_confidence(counting, "mart_p0a_feature_label_panel_v3")

        assert counting.execute_calls == 3
        assert len(results) == 6
        assert next(r for r in results if r.name == "industry_fallback_ratio").status == "WARN"
        assert next(r for r in results if r.name == "null_ratio_survey_count_60d").status == "PASS"
    finally:
        conn.close()


def test_check_v3_pit_confidence_warns_when_confidence_column_missing() -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_p0a_feature_label_panel_v3_missing_conf (
                survey_count_60d DOUBLE,
                pe_ttm_z_1y DOUBLE,
                sector_ret_60d DOUBLE,
                inst_quality_wavg DOUBLE,
                inst_holder_cnt DOUBLE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_p0a_feature_label_panel_v3_missing_conf
            VALUES (1.0, 2.0, 3.0, 4.0, 5.0)
            """
        )
        counting = _CountingConn(conn)

        results = subject.check_v3_pit_confidence(counting, "mart_p0a_feature_label_panel_v3_missing_conf")

        assert counting.execute_calls == 1
        assert len(results) == 1
        assert results[0].status == "WARN"
        assert "industry_pit_confidence" in results[0].detail
    finally:
        conn.close()

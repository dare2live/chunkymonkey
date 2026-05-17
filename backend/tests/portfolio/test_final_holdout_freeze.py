"""Tests for Codex Q8.8 final-holdout freeze + access guard."""
from __future__ import annotations

import pytest
import duckdb


def _mem_conn():
    return duckdb.connect(":memory:")


class TestHoldoutFreeze:
    def test_freeze_window_writes_record(self):
        from services.portfolio.final_holdout_freeze import freeze_window
        conn = _mem_conn()
        freeze_window(conn, "lgbm_test_v1", "2025-11-01", "2026-04-30", reason="unit test")
        rows = conn.execute("SELECT model_id, final_period_start, final_period_end FROM mart_p3_holdout_freeze").fetchall()
        assert len(rows) == 1
        assert rows[0] == ("lgbm_test_v1", "2025-11-01", "2026-04-30")

    def test_assert_no_holdout_leak_raises_on_overlap(self):
        from services.portfolio.final_holdout_freeze import freeze_window, assert_no_holdout_leak
        conn = _mem_conn()
        freeze_window(conn, "lgbm_test_v1", "2025-11-01", "2026-04-30")
        signal_dates = ["2024-06-01", "2025-12-15", "2026-02-15"]
        with pytest.raises(RuntimeError, match="governance v1 holdout leak"):
            assert_no_holdout_leak(conn, signal_dates, phase="P0b_optuna")

    def test_assert_no_holdout_leak_passes_when_outside(self):
        from services.portfolio.final_holdout_freeze import freeze_window, assert_no_holdout_leak
        conn = _mem_conn()
        freeze_window(conn, "lgbm_test_v1", "2025-11-01", "2026-04-30")
        # 所有 dates 都在 frozen window 之外
        signal_dates = ["2024-01-01", "2024-06-01", "2025-10-01"]
        assert_no_holdout_leak(conn, signal_dates, phase="P0b_optuna")  # 不 raise

    def test_assert_no_holdout_leak_skip_when_no_freeze(self):
        from services.portfolio.final_holdout_freeze import assert_no_holdout_leak
        conn = _mem_conn()
        # No freeze record — 任何 dates pass
        assert_no_holdout_leak(conn, ["2025-12-15"], phase="P0b_optuna")

    def test_record_holdout_access_appends_log(self):
        from services.portfolio.final_holdout_freeze import freeze_window, record_holdout_access
        conn = _mem_conn()
        freeze_window(conn, "lgbm_test_v1", "2025-11-01", "2026-04-30")
        record_holdout_access(conn, "lgbm_test_v1", "P3_acceptance_script", "run_p3_final_holdout.py")
        log = conn.execute(
            "SELECT access_log FROM mart_p3_holdout_freeze WHERE model_id='lgbm_test_v1'"
        ).fetchone()[0]
        assert "P3_acceptance_script" in log
        assert "run_p3_final_holdout.py" in log

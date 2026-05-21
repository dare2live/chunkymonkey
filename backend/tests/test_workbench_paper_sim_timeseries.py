import sys
from pathlib import Path

import duckdb


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.workbench_read import build_workbench_paper_sim_kpi_timeseries


def test_paper_sim_kpi_timeseries_orders_latest_and_parses_diff():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_paper_sim_kpi (
                sim_run_id TEXT PRIMARY KEY,
                variant TEXT,
                period_start TEXT,
                period_end TEXT,
                n_days INTEGER,
                annual_return DOUBLE,
                max_dd DOUBLE,
                sharpe DOUBLE,
                monthly_win_rate DOUBLE,
                annual_turnover DOUBLE,
                avg_holding_days DOUBLE,
                user_criteria_pass BOOLEAN,
                anti_churn_pass BOOLEAN,
                robustness_pass BOOLEAN,
                all_kpi_pass BOOLEAN,
                sim_config_hash TEXT,
                parent_sim_run_id TEXT,
                param_diff_json TEXT,
                lineage_url TEXT,
                built_at TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO mart_paper_sim_kpi (
                sim_run_id, variant, period_start, period_end, n_days,
                annual_return, max_dd, sharpe, monthly_win_rate,
                annual_turnover, avg_holding_days, user_criteria_pass,
                anti_churn_pass, robustness_pass, all_kpi_pass,
                sim_config_hash, parent_sim_run_id, param_diff_json,
                lineage_url, built_at
            )
            VALUES
                ('run_a', 'champion', '2026-01-01', '2026-01-31', 20,
                 0.25, -0.08, 1.2, 0.60, 12.0, 8.0, TRUE,
                 TRUE, FALSE, FALSE, 'hash_a', NULL, NULL,
                 'file:///tmp/run_a.md', TIMESTAMP '2026-02-01 10:00:00'),
                ('run_b', 'champion', '2026-02-01', '2026-02-28', 20,
                 0.35, -0.06, 1.8, 0.70, 10.0, 9.0, TRUE,
                 TRUE, TRUE, TRUE, 'hash_b', 'run_a', '{"portfolio":{"max_positions":[5,10]}}',
                 'file:///tmp/run_b.md', TIMESTAMP '2026-03-01 10:00:00'),
                ('run_other', 'baseline', '2026-02-01', '2026-02-28', 20,
                 0.10, -0.12, 0.8, 0.55, 20.0, 4.0, FALSE,
                 FALSE, FALSE, FALSE, 'hash_c', NULL, NULL,
                 NULL, TIMESTAMP '2026-03-02 10:00:00')
            """
        )

        payload = build_workbench_paper_sim_kpi_timeseries(conn, limit=5, variant="champion")

        assert payload["ok"] is True
        assert payload["meta"]["source_mode"] == "materialized_snapshot"
        assert [row["sim_run_id"] for row in payload["data"]] == ["run_b", "run_a"]
        assert payload["latest"]["sim_run_id"] == "run_b"
        assert payload["data"][0]["param_diff"] == {"portfolio": {"max_positions": [5, 10]}}
        assert payload["data"][0]["all_kpi_pass"] is True
    finally:
        conn.close()


def test_paper_sim_kpi_timeseries_missing_table_is_empty():
    conn = duckdb.connect(":memory:")
    try:
        payload = build_workbench_paper_sim_kpi_timeseries(conn)
        assert payload["ok"] is True
        assert payload["data"] == []
        assert payload["meta"]["source_mode"] == "missing_table"
    finally:
        conn.close()

from conftest import duck_mem
from scripts import run_walkforward_feature_eval as subject


def test_sql_walkforward_method_writes_candidate_eval_rows():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d DOUBLE,
                common_holder_network_count DOUBLE
            )
            """
        )
        rows = []
        for day in ("2026-05-01", "2026-05-02"):
            for idx in range(10):
                rows.append((
                    "tdx_f10_gpcw_v1",
                    f"000{idx:03d}",
                    day,
                    float(idx),
                    float(idx),
                ))
        conn.executemany("INSERT INTO fact_feature_panel_candidate VALUES (?, ?, ?, ?, ?)", rows)

        result = subject.run_walkforward_feature_eval(
            conn,
            feature_set_id="tdx_f10_gpcw_v1",
            folds=2,
            run_id="test_sql_wf",
            method="sql",
        )
        stored = conn.execute(
            """
            SELECT feature_name, rank_ic, label_name
            FROM mart_candidate_walkforward_eval
            WHERE run_id = 'test_sql_wf'
            """
        ).fetchall()

        assert result["method"] == "sql_corr"
        assert result["rows"] == 2
        assert {row["feature_name"] for row in stored} == {"common_holder_network_count"}
        assert all(row["rank_ic"] == 1.0 for row in stored)
    finally:
        conn.close()

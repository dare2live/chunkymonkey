from conftest import duck_mem
from scripts import run_feature_group_ablation as subject


def test_walkforward_group_ablation_uses_existing_eval_rows():
    conn = duck_mem()
    try:
        conn.execute(
            """
            CREATE TABLE fact_feature_panel_candidate (
                feature_set_id TEXT,
                stock_code TEXT,
                date TEXT,
                forward_ret_20d DOUBLE,
                common_holder_network_count DOUBLE,
                holder_count_change_pct_tdx DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE mart_candidate_walkforward_eval (
                feature_set_id TEXT,
                feature_name TEXT,
                label_name TEXT,
                rank_ic DOUBLE
            )
            """
        )
        conn.executemany(
            "INSERT INTO mart_candidate_walkforward_eval VALUES (?, ?, ?, ?)",
            [
                ("tdx_f10_gpcw_v1", "common_holder_network_count", "forward_ret_20d", 0.02),
                ("tdx_f10_gpcw_v1", "holder_count_change_pct_tdx", "forward_ret_20d", 0.04),
                ("tdx_f10_gpcw_v1", "common_holder_network_count", "forward_ret_5d", 0.01),
                ("tdx_f10_gpcw_v1", "holder_count_change_pct_tdx", "forward_ret_5d", 0.03),
            ],
        )

        result = subject.run_group_ablation(
            conn,
            feature_set_id="tdx_f10_gpcw_v1",
            run_id="test_wf_ablation",
            method="walkforward",
        )

        stored = conn.execute(
            """
            SELECT group_name, n_features, rank_ic_full, rank_ic_90d
            FROM mart_feature_group_ablation
            WHERE run_id = 'test_wf_ablation'
            ORDER BY group_name
            """
        ).fetchall()
        assert result["method"] == "walkforward_sql"
        assert result["rank_ic_full"] == 0.03
        assert {row["group_name"] for row in stored} == {"holder_count_chip", "ownership_tdx_f10"}
        assert "rank_ic_90d" in {row[0] for row in conn.execute("DESCRIBE mart_feature_group_ablation").fetchall()}
    finally:
        conn.close()

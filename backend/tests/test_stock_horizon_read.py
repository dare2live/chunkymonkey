from conftest import duck_mem
from services.stock_horizon_read import latest_stock_horizon_run_id, load_stock_horizon_evidence


def test_load_stock_horizon_evidence_uses_latest_selection_and_selected_effects():
    with duck_mem() as conn:
        conn.executescript(
            """
            CREATE TABLE mart_stock_horizon_selection (
                run_id TEXT,
                stock_code TEXT,
                baseline_label TEXT,
                baseline_horizon_days INTEGER,
                selected_label TEXT,
                selected_horizon_days INTEGER,
                selected_horizon_confidence DOUBLE,
                selected_horizon_score DOUBLE,
                baseline_horizon_score DOUBLE,
                score_advantage DOUBLE,
                avg_return_advantage DOUBLE,
                selected_max_drawdown DOUBLE,
                baseline_max_drawdown DOUBLE,
                selected_obs_count INTEGER,
                baseline_obs_count INTEGER,
                gate_status TEXT,
                fallback_reason TEXT,
                built_at TEXT
            );
            INSERT INTO mart_stock_horizon_selection VALUES
                ('old_run', '000001', 'follow_net_return_60d', 60,
                 'follow_net_return_60d', 60, 1.0, 0.10, 0.10, 0.0, 0.0,
                 -0.10, -0.10, 80, 80, 'baseline', 'old', '2026-05-05T10:00:00'),
                ('latest_run', '000001', 'follow_net_return_60d', 60,
                 'follow_net_return_90d', 90, 0.72, 0.18, 0.12, 0.06, 0.03,
                 -0.08, -0.13, 90, 90, 'selected', NULL, '2026-05-06T10:00:00');

            CREATE TABLE mart_stock_horizon_candidate_gate (
                run_id TEXT,
                stock_code TEXT,
                label_name TEXT,
                horizon_days INTEGER,
                obs_count INTEGER,
                avg_return DOUBLE,
                median_return DOUBLE,
                max_return DOUBLE,
                min_return DOUBLE,
                win_rate DOUBLE,
                volatility DOUBLE,
                downside_avg DOUBLE,
                compounded_return DOUBLE,
                max_drawdown DOUBLE,
                path_obs_count INTEGER,
                horizon_score DOUBLE,
                baseline_horizon_days INTEGER,
                baseline_horizon_score DOUBLE,
                baseline_avg_return DOUBLE,
                baseline_max_drawdown DOUBLE,
                baseline_obs_count INTEGER,
                score_advantage DOUBLE,
                avg_return_advantage DOUBLE,
                selection_confidence DOUBLE,
                candidate_status TEXT,
                reason_code TEXT,
                built_at TEXT
            );
            INSERT INTO mart_stock_horizon_candidate_gate VALUES
                ('latest_run', '000001', 'follow_net_return_60d', 60, 90,
                 0.010, 0.009, 0.030, -0.015, 0.52, 0.12, -0.02, 0.08, -0.13, 2,
                 0.12, 60, 0.12, 0.010, -0.13, 90, 0.0, 0.0, 0.5,
                 'baseline', 'baseline_60d', '2026-05-06T10:00:00'),
                ('latest_run', '000001', 'follow_net_return_90d', 90, 90,
                 0.040, 0.032, 0.120, -0.020, 0.60, 0.11, -0.01, 0.15, -0.08, 1,
                 0.18, 60, 0.12, 0.010, -0.13, 90, 0.06, 0.03, 0.72,
                 'candidate_pass', 'candidate_pass', '2026-05-06T10:00:00');

            CREATE TABLE mart_stock_horizon_feature_effect (
                run_id TEXT,
                stock_code TEXT,
                label_name TEXT,
                horizon_days INTEGER,
                feature_name TEXT,
                obs_count INTEGER,
                corr DOUBLE,
                abs_corr_rank INTEGER,
                effect_direction TEXT,
                built_at TEXT
            );
            INSERT INTO mart_stock_horizon_feature_effect VALUES
                ('latest_run', '000001', 'follow_net_return_90d', 90, 'ret_60d', 90, -0.40, 1, 'negative', '2026-05-06T10:00:00'),
                ('latest_run', '000001', 'follow_net_return_60d', 60, 'wrong_horizon', 90, 0.90, 1, 'positive', '2026-05-06T10:00:00');
            """
        )

        evidence = load_stock_horizon_evidence(conn, ["000001", "000002"])

        assert latest_stock_horizon_run_id(conn) == "latest_run"
        assert evidence["000001"]["run_id"] == "latest_run"
        assert evidence["000001"]["selected_horizon_days"] == 90
        assert evidence["000001"]["avg_return_advantage"] == 0.03
        assert evidence["000001"]["is_baseline"] is False
        assert [row["horizon_days"] for row in evidence["000001"]["horizon_comparison"]] == [60, 90]
        assert evidence["000001"]["horizon_comparison"][1]["candidate_status"] == "candidate_pass"
        assert evidence["000001"]["horizon_comparison"][1]["max_return"] == 0.120
        assert evidence["000001"]["horizon_comparison"][1]["min_return"] == -0.020
        assert evidence["000001"]["horizon_comparison"][1]["is_selected"] is True
        assert evidence["000001"]["top_feature_effects"][0]["feature_name"] == "ret_60d"
        assert all(row["feature_name"] != "wrong_horizon" for row in evidence["000001"]["top_feature_effects"])
        assert "000002" not in evidence

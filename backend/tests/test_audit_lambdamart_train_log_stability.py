import json

from scripts.audit_lambdamart_train_log_stability import (
    attach_strategy_returns,
    build_stability_payload,
    parse_window_metrics,
)


def _record() -> dict:
    return {
        "model_id": "model_a",
        "run_id": "run_a",
        "is_rank_ic": 0.10,
        "oos_rank_ic_avg": 0.04,
        "metrics_json": json.dumps(
            {
                "window_metrics": [
                    {
                        "window_idx": 0,
                        "train_start": "2025-01-01",
                        "train_end": "2025-06-30",
                        "test_start": "2025-07-01",
                        "test_end": "2025-07-31",
                        "n_train_rows": 100,
                        "n_test_rows": 20,
                        "train_metrics": {"rank_ic": 0.12, "top5_spread": 0.30, "ndcg10": 0.90},
                        "oos_metrics": {"rank_ic": 0.06, "top5_spread": 0.12, "ndcg10": 0.55},
                    },
                    {
                        "window_idx": 1,
                        "train_start": "2025-01-01",
                        "train_end": "2025-07-31",
                        "test_start": "2025-08-01",
                        "test_end": "2025-08-29",
                        "n_train_rows": 120,
                        "n_test_rows": 30,
                        "train_metrics": {"rank_ic": 0.10, "top5_spread": 0.20, "ndcg10": 0.88},
                        "oos_metrics": {"rank_ic": -0.02, "top5_spread": -0.03, "ndcg10": 0.48},
                    },
                ]
            }
        ),
    }


def test_parse_window_metrics_computes_rank_ic_drop():
    windows = parse_window_metrics(_record())

    assert len(windows) == 2
    assert windows[0]["window_idx"] == 0
    assert windows[0]["test_start"] == "2025-07-01"
    assert windows[0]["rank_ic_relative_drop"] == 0.5
    assert windows[1]["oos_rank_ic"] == -0.02
    assert windows[1]["rank_ic_relative_drop"] == 1.2


def test_build_stability_payload_marks_true_gate_failure():
    windows = parse_window_metrics(_record())
    payload = build_stability_payload(_record(), windows, relative_drop_threshold=0.30)

    assert payload["true_is_oos_gate"]["passes"] is False
    assert payload["true_is_oos_gate"]["relative_drop"] == 0.6
    assert payload["window_stability"]["n_negative_oos_rank_ic"] == 1
    assert payload["window_stability"]["oos_rank_ic"]["positive_rate"] == 0.5
    assert payload["worst_oos_rank_ic_windows"][0]["window_idx"] == 1
    assert payload["recommendations"]


def test_attach_strategy_returns_buckets_non_overlap_returns_by_test_window():
    windows = parse_window_metrics(_record())
    attach_strategy_returns(
        windows,
        [
            ("2025-07-03", 0.10),
            ("2025-07-15", -0.05),
            ("2025-08-15", 0.03),
            ("2025-09-02", 0.40),
        ],
    )

    assert round(windows[0]["strategy_return"], 6) == 0.045
    assert windows[0]["strategy_n_obs"] == 2
    assert round(windows[1]["strategy_return"], 6) == 0.03
    assert windows[1]["strategy_n_obs"] == 1

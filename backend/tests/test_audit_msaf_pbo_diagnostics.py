from __future__ import annotations

import numpy as np
import pytest

from scripts.audit_msaf_pbo_diagnostics import pbo_variant_diagnostics, variant_stats


def test_pbo_variant_diagnostics_attributes_oos_failures_by_selected_k() -> None:
    returns_matrix = np.array(
        [
            [0.04, 0.04, 0.04, 0.04, -0.04, -0.04, -0.04, -0.04],
            [0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
            [-0.01, -0.01, -0.01, -0.01, 0.05, 0.05, 0.05, 0.05],
        ],
        dtype=float,
    )

    diagnostics = pbo_variant_diagnostics(returns_matrix, [3, 5, 7], sub_periods=4)

    assert diagnostics["n_combos"] == 6
    assert 0.0 <= diagnostics["pbo"] <= 1.0
    assert {row["k"] for row in diagnostics["by_selected_k"]} == {3, 5, 7}
    assert sum(row["n_selected"] for row in diagnostics["by_selected_k"]) == 6
    assert any(row["n_oos_bottom_half"] > 0 for row in diagnostics["by_selected_k"])


def test_variant_stats_reports_annualized_sharpe_and_drawdown() -> None:
    returns_matrix = np.array(
        [
            [0.01, 0.02, -0.01, 0.03],
            [0.02, -0.04, 0.01, 0.01],
        ],
        dtype=float,
    )

    stats = variant_stats(returns_matrix, [3, 5], periods_per_year=50)

    assert stats[0]["k"] == 3
    assert stats[0]["n_obs"] == 4
    assert stats[0]["ann_sharpe"] > stats[0]["period_sharpe"]
    assert stats[1]["max_dd"] < 0


def test_pbo_variant_diagnostics_rejects_mismatched_k_values() -> None:
    with pytest.raises(ValueError, match="k_values length"):
        pbo_variant_diagnostics(np.zeros((3, 8)), [3, 5], sub_periods=4)

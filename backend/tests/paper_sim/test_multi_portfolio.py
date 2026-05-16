"""Paper Sim — multi-portfolio driver wrap 单测 (用户 2026-05-16: 3 组对比).

Verifies run_paper_sim_day_multi 调用现有 run_paper_sim_day for each portfolio with独立 sim_run_id.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from services.paper_sim.driver import run_paper_sim_day_multi


def test_multi_invokes_run_paper_sim_day_per_portfolio():
    """两 portfolio → 两次 run_paper_sim_day calls (独立 sim_run_id)."""
    mock_conn = MagicMock()
    mock_mkt = MagicMock()
    cfg_a = MagicMock()
    cfg_b = MagicMock()
    portfolios = {
        "A": ("live_A_2026-05-16", cfg_a),
        "B": ("live_B_2026-05-16", cfg_b),
    }
    with patch("services.paper_sim.driver.run_paper_sim_day") as mock_run:
        mock_run.side_effect = [
            {"total_value": 1_000_000, "n_buys": 1, "sim_run_id": "live_A_2026-05-16"},
            {"total_value": 1_050_000, "n_buys": 2, "sim_run_id": "live_B_2026-05-16"},
        ]
        results = run_paper_sim_day_multi(
            mock_conn, mock_mkt,
            today="2026-05-16",
            portfolios=portfolios,
        )

    assert mock_run.call_count == 2
    # 第一次 call: A
    a_call = mock_run.call_args_list[0]
    assert a_call.kwargs["sim_run_id"] == "live_A_2026-05-16"
    assert a_call.kwargs["cfg"] is cfg_a
    # 第二次 call: B
    b_call = mock_run.call_args_list[1]
    assert b_call.kwargs["sim_run_id"] == "live_B_2026-05-16"
    assert b_call.kwargs["cfg"] is cfg_b
    # 返回 dict 含 2 portfolios
    assert set(results.keys()) == {"A", "B"}
    assert results["A"]["total_value"] == 1_000_000
    assert results["B"]["total_value"] == 1_050_000


def test_multi_starting_cash_map_threaded():
    """starting_cash_map → 按 portfolio_id 传 starting_cash."""
    mock_conn = MagicMock()
    mock_mkt = MagicMock()
    portfolios = {
        "X": ("live_X_2026-05-16", MagicMock()),
        "Y": ("live_Y_2026-05-16", MagicMock()),
    }
    starting_cash_map = {"X": 1_000_000, "Y": 2_000_000}
    with patch("services.paper_sim.driver.run_paper_sim_day") as mock_run:
        mock_run.return_value = {"total_value": 1_000_000}
        run_paper_sim_day_multi(
            mock_conn, mock_mkt,
            today="2026-05-16",
            portfolios=portfolios,
            starting_cash_map=starting_cash_map,
        )
    assert mock_run.call_args_list[0].kwargs["starting_cash"] == 1_000_000
    assert mock_run.call_args_list[1].kwargs["starting_cash"] == 2_000_000


def test_multi_no_starting_cash_map_defaults_none():
    """starting_cash_map=None → starting_cash=None per call (driver 自己从 nav 推)."""
    mock_conn = MagicMock()
    mock_mkt = MagicMock()
    portfolios = {"Z": ("live_Z_2026-05-16", MagicMock())}
    with patch("services.paper_sim.driver.run_paper_sim_day") as mock_run:
        mock_run.return_value = {"total_value": 0}
        run_paper_sim_day_multi(
            mock_conn, mock_mkt,
            today="2026-05-16",
            portfolios=portfolios,
        )
    assert mock_run.call_args_list[0].kwargs["starting_cash"] is None


def test_multi_empty_portfolios_returns_empty():
    mock_conn = MagicMock()
    mock_mkt = MagicMock()
    with patch("services.paper_sim.driver.run_paper_sim_day") as mock_run:
        results = run_paper_sim_day_multi(
            mock_conn, mock_mkt,
            today="2026-05-16",
            portfolios={},
        )
    assert results == {}
    mock_run.assert_not_called()

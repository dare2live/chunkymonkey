"""Phase γ D2 — institution_signal 聚合单测。"""
from __future__ import annotations

import pytest

from services.picture.institution_signal import (
    aggregate_institution_signal,
    compute_avg_win_rate_score,
    compute_institution_score,
    compute_recent_increase_score,
    compute_tracked_count_score,
    top_institutions,
)


@pytest.fixture
def sample_holders():
    return [
        {
            "institution_id": "INST_A", "institution_name": "高瓴资本",
            "share_pct": 5.2, "share_change_qoq": 0.5, "is_tracked": True,
            "inst_win_rate_60d": 75.0,
        },
        {
            "institution_id": "INST_B", "institution_name": "易方达基金",
            "share_pct": 3.1, "share_change_qoq": -0.2, "is_tracked": True,
            "inst_win_rate_60d": 60.0,
        },
        {
            "institution_id": "INST_C", "institution_name": "中央汇金",
            "share_pct": 2.5, "share_change_qoq": 0.1, "is_tracked": False,
            "inst_win_rate_60d": None,
        },
    ]


class TestComputeRecentIncreaseScore:
    def test_two_of_three_increased(self, sample_holders):
        # A, C 增持; B 减持 → 2/3 = 66.67
        assert abs(compute_recent_increase_score(sample_holders) - 66.67) < 0.01

    def test_empty_returns_zero(self):
        assert compute_recent_increase_score([]) == 0.0


class TestComputeTrackedCountScore:
    def test_two_tracked_caps_at_30(self, sample_holders):
        # 2 tracked / 30 cap = 6.67
        assert abs(compute_tracked_count_score(sample_holders) - 6.67) < 0.01

    def test_saturation(self):
        # 50 个全跟踪 → 100 (饱和)
        big = [{"is_tracked": True} for _ in range(50)]
        assert compute_tracked_count_score(big) == 100.0


class TestComputeAvgWinRate:
    def test_only_non_null_counted(self, sample_holders):
        # A=75, B=60, C=None → avg = 67.5
        assert compute_avg_win_rate_score(sample_holders) == 67.5

    def test_all_null_returns_zero(self):
        h = [{"inst_win_rate_60d": None}, {"inst_win_rate_60d": None}]
        assert compute_avg_win_rate_score(h) == 0.0


class TestComputeInstitutionScore:
    def test_weighted_aggregation(self, sample_holders):
        # 0.5×66.67 + 0.3×6.67 + 0.2×67.5
        # = 33.33 + 2.00 + 13.50 = 48.83
        score = compute_institution_score(sample_holders)
        assert 48.5 <= score <= 49.0


class TestTopInstitutions:
    def test_top_3_by_share_pct(self, sample_holders):
        top = top_institutions(sample_holders, n=3)
        assert len(top) == 3
        # 排序: A (5.2) > B (3.1) > C (2.5)
        assert top[0]["name"] == "高瓴资本"
        assert top[0]["share_pct"] == 5.2
        assert top[1]["name"] == "易方达基金"
        assert top[2]["name"] == "中央汇金"

    def test_top_n_truncate(self, sample_holders):
        top = top_institutions(sample_holders, n=2)
        assert len(top) == 2
        assert top[0]["name"] == "高瓴资本"


class TestAggregate:
    def test_full_payload(self, sample_holders):
        out = aggregate_institution_signal(sample_holders)
        assert "institution_score" in out
        assert out["institution_n_insts"] == 3
        assert len(out["institution_top"]) == 3
        assert out["institution_top"][0]["name"] == "高瓴资本"

    def test_empty_holders_safe(self):
        out = aggregate_institution_signal([])
        assert out["institution_score"] == 0.0
        assert out["institution_n_insts"] == 0
        assert out["institution_top"] == []

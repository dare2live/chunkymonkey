from __future__ import annotations

from scripts.audit_survivorship import _sample_missing_codes


def test_sample_missing_codes_returns_lexicographically_smallest_subset() -> None:
    missing = {"600519", "000001", "300750", "002594", "000002", "601318"}

    assert _sample_missing_codes(missing) == ["000001", "000002", "002594", "300750", "600519"]


def test_sample_missing_codes_handles_empty_and_limit() -> None:
    assert _sample_missing_codes(set()) == []
    assert _sample_missing_codes({"600519", "000001"}, limit=1) == ["000001"]
    assert _sample_missing_codes({"600519", "000001"}, limit=0) == []

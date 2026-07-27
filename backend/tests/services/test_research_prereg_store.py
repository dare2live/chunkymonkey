"""Atomic prereg / param_hash / single-touch store."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.holdout_guard import (
    HoldoutSingleTouchViolation,
    consume_holdout_single_touch,
)
from services.research_prereg_store import (
    ResearchPreregError,
    compute_param_hash,
    consume_single_touch,
    load_prereg_by_token,
    register_prereg,
)


def test_register_computes_stable_param_hash(tmp_path: Path) -> None:
    body = {
        "hypothesis": "h",
        "primary_metric": "holdout_net_return",
        "search_space": [],
        "snapshot_id": "s1",
    }
    expected = compute_param_hash(body)
    reg = register_prereg(body, store_dir=tmp_path, single_touch_token="tok12345")
    assert reg.param_hash == expected
    assert reg.holdout_touched is False
    again = register_prereg(body, store_dir=tmp_path, single_touch_token="tok12345")
    assert again.param_hash == expected


def test_single_touch_consume_once(tmp_path: Path) -> None:
    body = {"hypothesis": "h", "snapshot_id": "s1"}
    reg = register_prereg(body, store_dir=tmp_path, single_touch_token="touchonce1")
    consume_single_touch(reg.single_touch_token, store_dir=tmp_path)
    loaded = load_prereg_by_token(reg.single_touch_token, store_dir=tmp_path)
    assert loaded.holdout_touched is True
    with pytest.raises(ResearchPreregError, match="already consumed"):
        consume_single_touch(reg.single_touch_token, store_dir=tmp_path)


def test_holdout_guard_wraps_single_touch(tmp_path: Path) -> None:
    body = {"hypothesis": "h", "snapshot_id": "s1"}
    reg = register_prereg(body, store_dir=tmp_path, single_touch_token="guardtok01")
    consume_holdout_single_touch(reg.single_touch_token, store_dir=tmp_path)
    with pytest.raises(HoldoutSingleTouchViolation, match="already consumed"):
        consume_holdout_single_touch(reg.single_touch_token, store_dir=tmp_path)


def test_token_conflict_different_hash_fails(tmp_path: Path) -> None:
    register_prereg(
        {"hypothesis": "a"}, store_dir=tmp_path, single_touch_token="conflict1"
    )
    with pytest.raises(ResearchPreregError, match="different param_hash"):
        register_prereg(
            {"hypothesis": "b"}, store_dir=tmp_path, single_touch_token="conflict1"
        )

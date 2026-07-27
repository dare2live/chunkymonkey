"""Atomic prereg / param_hash / single-touch store."""
from __future__ import annotations

from pathlib import Path
import json

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


def test_concurrent_single_touch_only_one_success(tmp_path: Path) -> None:
    """O_EXCL marker: concurrent consumers → exactly one SUCCESS."""
    import concurrent.futures

    reg = register_prereg(
        {"hypothesis": "race", "snapshot_id": "s-race"},
        store_dir=tmp_path,
        single_touch_token="racetoken01",
    )
    token = reg.single_touch_token
    results: list[str] = []

    def _consume() -> str:
        try:
            consume_single_touch(token, store_dir=tmp_path)
            return "ok"
        except ResearchPreregError as exc:
            if "already consumed" in str(exc):
                return "dup"
            raise

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_consume) for _ in range(8)]
        results = [f.result() for f in futs]
    assert results.count("ok") == 1
    assert results.count("dup") == 7
    loaded = load_prereg_by_token(token, store_dir=tmp_path)
    assert loaded.holdout_touched is True


def test_fresh_token_cannot_reset_same_global_holdout_scope(tmp_path: Path) -> None:
    body = {
        "hypothesis": "first",
        "strategy_package": "institution_follow_v1",
        "snapshot_content_hash": "snap-hash",
        "universe_id": "pit-cn-a",
        "fold_embargo": {"holdout_start": "20250601"},
    }
    first = register_prereg(
        body, store_dir=tmp_path, single_touch_token="globalscope01"
    )
    second = register_prereg(
        {
            **body,
            "hypothesis": "changed-after-seeing-results",
            "fold_embargo": {"holdout_start": "20250701"},
        },
        store_dir=tmp_path,
        single_touch_token="globalscope02",
    )
    assert first.holdout_scope_id == second.holdout_scope_id
    consume_single_touch(first.single_touch_token, store_dir=tmp_path)
    with pytest.raises(ResearchPreregError, match="already consumed"):
        consume_single_touch(second.single_touch_token, store_dir=tmp_path)


def test_post_replace_failure_keeps_scope_consumed(
    tmp_path: Path, monkeypatch
) -> None:
    """Record projection failure after marker publish must never reopen budget."""

    from services import research_prereg_store as store

    body = {
        "strategy_package": "institution_follow_v1",
        "snapshot_content_hash": "snap-hash",
        "universe_id": "pit-cn-a",
    }
    first = register_prereg(
        body, store_dir=tmp_path, single_touch_token="postreplace01"
    )
    second = register_prereg(
        {**body, "hypothesis": "retry"},
        store_dir=tmp_path,
        single_touch_token="postreplace02",
    )
    real_write = store._atomic_write_json

    def _write_then_raise(path, payload):
        real_write(path, payload)
        raise OSError("injected post-replace directory fsync failure")

    monkeypatch.setattr(store, "_atomic_write_json", _write_then_raise)
    with pytest.raises(OSError, match="post-replace"):
        consume_single_touch(first.single_touch_token, store_dir=tmp_path)
    assert list(tmp_path.glob("*.holdout_consumed"))
    with pytest.raises(ResearchPreregError, match="already consumed"):
        consume_single_touch(second.single_touch_token, store_dir=tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("param_hash", "forged", "param_hash mismatch"),
        ("single_touch_token", "other-token", "token mismatch"),
    ],
)
def test_tampered_outer_record_fails_closed(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    reg = register_prereg(
        {"hypothesis": "h", "snapshot_id": "s1"},
        store_dir=tmp_path,
        single_touch_token="tamperouter1",
    )
    path = Path(reg.path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ResearchPreregError, match=match):
        load_prereg_by_token(reg.single_touch_token, store_dir=tmp_path)


def test_tampered_payload_and_marker_projection_fail_closed(tmp_path: Path) -> None:
    reg = register_prereg(
        {"hypothesis": "h", "snapshot_id": "s1"},
        store_dir=tmp_path,
        single_touch_token="tamperbody01",
    )
    path = Path(reg.path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["payload"]["hypothesis"] = "forged"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ResearchPreregError, match="param_hash mismatch"):
        consume_single_touch(reg.single_touch_token, store_dir=tmp_path)

    clean = register_prereg(
        {"hypothesis": "clean", "snapshot_id": "s2"},
        store_dir=tmp_path,
        single_touch_token="markerstate1",
    )
    consume_single_touch(clean.single_touch_token, store_dir=tmp_path)
    clean_path = Path(clean.path)
    clean_raw = json.loads(clean_path.read_text(encoding="utf-8"))
    clean_raw["holdout_touched"] = False
    clean_path.write_text(json.dumps(clean_raw), encoding="utf-8")
    assert load_prereg_by_token(
        clean.single_touch_token, store_dir=tmp_path
    ).holdout_touched is True

"""persist_tier12_full_universe_accept cutover-aware post-accept gates."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.persist_tier12_full_universe_accept import assert_post_accept_cutover_gate


def test_post_accept_allows_cutover_on_when_resolver_accepted() -> None:
    cut_cfg = SimpleNamespace(cutover_allowed=True)
    decision = SimpleNamespace(
        cutover_allowed=True,
        status="ACCEPTED_CUTOVER",
        reasons=("gates_passed",),
    )
    assert_post_accept_cutover_gate(cut_cfg=cut_cfg, cut_decision=decision)


def test_post_accept_fails_when_cutover_on_but_resolver_blocked() -> None:
    cut_cfg = SimpleNamespace(cutover_allowed=True)
    decision = SimpleNamespace(
        cutover_allowed=False,
        status="BLOCKED",
        reasons=("config_hash_mismatch",),
    )
    with pytest.raises(ValueError, match="cutover_on_but_resolver_not_accepted"):
        assert_post_accept_cutover_gate(cut_cfg=cut_cfg, cut_decision=decision)


def test_post_accept_requires_legacy_when_cutover_off() -> None:
    cut_cfg = SimpleNamespace(cutover_allowed=False)
    decision = SimpleNamespace(
        cutover_allowed=False,
        status="LEGACY",
        reasons=("config_cutover_allowed_false",),
    )
    assert_post_accept_cutover_gate(cut_cfg=cut_cfg, cut_decision=decision)


def test_post_accept_fails_if_cutover_off_but_resolver_opts_in() -> None:
    cut_cfg = SimpleNamespace(cutover_allowed=False)
    decision = SimpleNamespace(
        cutover_allowed=True,
        status="ACCEPTED_CUTOVER",
        reasons=("gates_passed",),
    )
    with pytest.raises(ValueError, match="consumer_cutover_must_remain_false"):
        assert_post_accept_cutover_gate(cut_cfg=cut_cfg, cut_decision=decision)

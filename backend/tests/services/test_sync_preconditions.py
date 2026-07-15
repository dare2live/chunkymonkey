from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services.data_sources import sync_preconditions as sp
from services.writer_lock import AUTH_VERIFIED_LEASE_ENV, WriterLease


def _lease(*, inherited: bool, lease_id: str = "lease") -> WriterLease:
    return WriterLease(lease_id, "sync", inherited, Path("/tmp/lock"), 7)


def test_calendar_precondition_allows_only_trade_cal_bootstrap_without_gate():
    sp.ensure_calendar_foundation(
        ["trade_cal"], runner=lambda *_args, **_kwargs: pytest.fail("must not run gate")
    )


def test_calendar_precondition_fails_closed_for_regular_domains():
    with pytest.raises(sp.CalendarFoundationError, match="calendar_not_ready"):
        sp.ensure_calendar_foundation(
            ["daily"],
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
        )


def test_parent_auth_proof_requires_inherited_matching_lease(monkeypatch):
    monkeypatch.setenv(AUTH_VERIFIED_LEASE_ENV, "lease")
    result = sp.authorization_preflight(
        lease=_lease(inherited=True),
        adapter_factory=lambda _source: pytest.fail("valid proof must skip user()"),
        registry={"defaults": {"auth_probe_timeout_seconds": 20}},
    )
    assert result == {"inherited_authorization": True}


@pytest.mark.parametrize("inherited,proof", [(False, "lease"), (True, "forged")])
def test_direct_or_forged_auth_proof_still_probes(monkeypatch, inherited, proof):
    monkeypatch.setenv(AUTH_VERIFIED_LEASE_ENV, proof)
    calls = []

    class Source:
        def authorization_status(self):
            calls.append("user")
            return {"ok": True}

    result = sp.authorization_preflight(
        lease=_lease(inherited=inherited),
        adapter_factory=lambda _source: Source(),
        registry={"defaults": {"auth_probe_timeout_seconds": 20}},
    )
    assert result == {"ok": True}
    assert calls == ["user"]

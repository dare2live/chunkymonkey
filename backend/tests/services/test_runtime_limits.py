from __future__ import annotations

import argparse
import json
import threading

import pytest

from services.data_sources import sync_runner as sr
from services.data_sources.runtime_limits import fetch_socket_timeout_seconds


def _args(**overrides) -> argparse.Namespace:
    values = {
        "domain": "synthetic",
        "all_due": False,
        "backfill": False,
        "resume": False,
        "start": None,
        "end": None,
        "drain": False,
        "max_dates": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _registry(timeout=30) -> dict:
    return {
        "defaults": {
            "execution_policy": {"mode": "enabled", "reason": "manual_only"},
            "fetch_timeout_seconds": timeout,
        },
        "domains": {
            "synthetic": {
                "source": "synthetic",
                "batch_mode": "full_refresh",
            }
        },
    }


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"fetch_timeout_seconds": True},
        {"fetch_timeout_seconds": 0},
        {"fetch_timeout_seconds": -1},
        {"fetch_timeout_seconds": float("nan")},
        {"fetch_timeout_seconds": float("inf")},
        {"fetch_timeout_seconds": threading.TIMEOUT_MAX * 2},
    ],
)
def test_provider_timeout_rejects_missing_or_unbounded_values(spec: dict) -> None:
    with pytest.raises(ValueError, match="fetch_timeout_seconds"):
        fetch_socket_timeout_seconds(spec)


def test_cli_invalid_timeout_fails_before_calendar_lock_provider_or_db(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reg = _registry(timeout=float("inf"))
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: _args())
    monkeypatch.setattr(sr, "load_registry", lambda: reg)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("side-effect boundary reached")

    monkeypatch.setattr(sr, "_calendar_preflight", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)

    assert sr.main() == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "operation_window_blocked"
    assert "provider timeout invalid" in payload["reason"]


def test_generic_cli_exit_is_nonzero_when_a_batch_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda domain, **_kwargs: {
            "domain": domain,
            "failed_batches": 1,
            "ok": False,
        },
    )

    assert sr._main_unlocked(_args(), _registry(), ["synthetic"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {"domain": "synthetic", "failed_batches": 1, "ok": False}
    ]

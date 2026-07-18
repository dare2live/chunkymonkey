"""Fail-closed CLI gates for bounded formal-margin history replay.

These tests deliberately stop at the runner boundary.  A historical replay
request must be fully proven before the global writer lease, provider
authorization, adapter construction, or target database can be touched.
"""
from __future__ import annotations

import json
import threading
from argparse import Namespace
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services import writer_lock as writer_lock_module
from services.data_sources import sync_runner as sr


DATA_START = "20190102"
VALID_END = "20190103"
ELIGIBLE_END = "20260716"
CONFIGURED_MAX = 2
DATASET_ID = "tier0.market_data.margin_exchange_daily"


def _attach_history_result(error: BaseException) -> BaseException:
    result = sr.margin_history.MarginHistoryResult(
        dataset_id=DATASET_ID,
        contract_hash="contract-v3",
        config_hash="config-v3",
        plan_hash="plan-hash",
        window_dates=(DATA_START, VALID_END),
        attempted_dates=(DATA_START, VALID_END),
        skipped_dates=(),
        accepted_evidence=(
            sr.margin_history.MarginHistoryAcceptedEvidence(
                partition_value=DATA_START,
                batch_id="accepted-first-day",
                row_count=2,
                content_hash="accepted-content-hash",
            ),
        ),
        failures=(
            sr.margin_history.MarginHistoryFailure(
                partition_value=VALID_END,
                code="account_halt",
                detail=type(error).__name__,
                evidence_hash="e" * 64,
            ),
        ),
        deferred_dates=(),
        next_start=VALID_END,
    )
    setattr(error, "history_result", result)
    return error


def _registry() -> dict:
    return {
        "defaults": {"fetch_timeout_seconds": 120},
        "domains": {
            "margin": {
                "source": "tushare",
                "api": "margin",
                "sync_policy": "manual_only",
                "batch_mode": "by_trade_date",
                "data_start": DATA_START,
                "date_param": "trade_date",
                "target_table": "raw_tushare_margin",
                "history_replay": {
                    "max_partitions_per_run": CONFIGURED_MAX,
                },
                # The parser itself is replaced below with a small immutable
                # contract.  Keeping the registry marker makes the fixture
                # truthful to the production dispatch shape.
                "dataset_contract": {"dataset_id": DATASET_ID},
            },
            "daily": {
                "source": "tushare",
                "api": "daily",
                "sync_policy": "manual_only",
                "batch_mode": "by_trade_date",
                "data_start": DATA_START,
                "date_param": "trade_date",
                "target_table": "raw_tushare_daily",
            },
        },
    }


def _args(
    *,
    domain: str | None = "margin",
    all_due: bool = False,
    backfill: bool = True,
    start: str | None = DATA_START,
    end: str | None = VALID_END,
    max_dates: int | None = 1,
    resume: bool = False,
) -> Namespace:
    return Namespace(
        all_due=all_due,
        domain=domain,
        drain=False,
        max_dates=max_dates,
        backfill=backfill,
        start=start,
        end=end,
        resume=resume,
    )


@pytest.fixture(autouse=True)
def _deterministic_history_preflight(monkeypatch):
    formal_contract = SimpleNamespace(
        dataset_id=DATASET_ID,
        coverage_start=DATA_START,
        contract_hash="contract-v3",
        config_hash="config-v3",
    )
    monkeypatch.setattr(
        sr.margin_ingest,
        "contract_for_spec",
        lambda spec: formal_contract if spec.get("domain") == "margin" else None,
    )
    monkeypatch.setattr(
        sr,
        "eligible_end_date",
        lambda _spec: sr.DomainEligibility(ELIGIBLE_END, False, "test_frontier"),
    )

    # 2018-12-31 is deliberately a real session before data_start.  The two
    # dates just after data_start are real sessions; adjacent dates are not.
    sessions = (
        "20181231",
        DATA_START,
        VALID_END,
        ELIGIBLE_END,
        "20260717",
    )

    def fake_trading_days(start: str, end: str | None = None) -> list[str]:
        lower = str(start).replace("-", "")
        upper = str(end or start).replace("-", "")
        return [day for day in sessions if lower <= day <= upper]

    monkeypatch.setattr(sr, "trading_days", fake_trading_days)
    monkeypatch.setattr(sr, "_calendar_preflight", lambda _domains: None)


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(_args(start=None), id="missing-start"),
        pytest.param(_args(end=None), id="missing-end"),
        pytest.param(_args(max_dates=None), id="missing-max-dates"),
        pytest.param(_args(max_dates=0), id="zero-max-dates"),
        pytest.param(_args(max_dates=-1), id="negative-max-dates"),
        pytest.param(
            _args(max_dates=CONFIGURED_MAX + 1), id="above-configured-cap"
        ),
        pytest.param(_args(start="20181231"), id="before-data-start"),
        pytest.param(
            _args(start=VALID_END, end=DATA_START), id="reversed-window"
        ),
        pytest.param(_args(start="20190101"), id="start-not-trading-session"),
        pytest.param(_args(end="20190105"), id="end-not-trading-session"),
        pytest.param(
            _args(end="20260717"), id="after-live-eligibility-frontier"
        ),
        pytest.param(
            _args(domain=None, all_due=True), id="all-due-is-not-single-margin"
        ),
        pytest.param(_args(domain="daily"), id="non-margin-history-cap"),
        pytest.param(_args(resume=True), id="resume-is-not-history-semantics"),
        pytest.param(
            _args(start="20190101", end="20190101"),
            id="zero-trading-session-window",
        ),
    ],
)
def test_invalid_history_request_fails_before_any_side_effect(
    args: Namespace, monkeypatch
) -> None:
    registry = _registry()
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: args)
    monkeypatch.setattr(sr, "load_registry", lambda: registry)

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "invalid history request crossed the preflight boundary into a side effect"
        )

    monkeypatch.setattr(writer_lock_module, "writer_lock", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)
    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    assert sr.main() == 5


def test_missing_registry_timeout_fails_before_any_side_effect(monkeypatch) -> None:
    registry = _registry()
    del registry["defaults"]["fetch_timeout_seconds"]
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: _args())
    monkeypatch.setattr(sr, "load_registry", lambda: registry)

    def forbidden(*_args, **_kwargs):
        pytest.fail("missing provider timeout crossed the static preflight boundary")

    monkeypatch.setattr(writer_lock_module, "writer_lock", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)
    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    assert sr.main() == 5


def test_missing_registry_timeout_for_non_history_domain_fails_before_side_effects(
    monkeypatch,
) -> None:
    registry = _registry()
    del registry["defaults"]["fetch_timeout_seconds"]
    monkeypatch.setattr(
        sr,
        "_parse_cli_args",
        lambda: _args(
            domain="daily",
            backfill=False,
            start=None,
            end=None,
            max_dates=None,
        ),
    )
    monkeypatch.setattr(sr, "load_registry", lambda: registry)

    def forbidden(*_args, **_kwargs):
        pytest.fail("missing shared timeout crossed the static CLI preflight boundary")

    monkeypatch.setattr(sr, "_calendar_preflight", forbidden)
    monkeypatch.setattr(writer_lock_module, "writer_lock", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)
    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    assert sr.main() == 5


@pytest.mark.parametrize(
    "invalid_timeout",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        threading.TIMEOUT_MAX + 1,
    ],
)
def test_non_finite_registry_timeout_fails_before_any_side_effect(
    invalid_timeout: float, monkeypatch
) -> None:
    registry = _registry()
    registry["defaults"]["fetch_timeout_seconds"] = invalid_timeout
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: _args())
    monkeypatch.setattr(sr, "load_registry", lambda: registry)

    def forbidden(*_args, **_kwargs):
        pytest.fail("non-finite provider timeout crossed the static preflight boundary")

    monkeypatch.setattr(writer_lock_module, "writer_lock", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)
    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    assert sr.main() == 5


@pytest.mark.parametrize(
    ("max_dates", "message"),
    [
        (None, "max"),
        (0, "positive"),
        (-1, "positive"),
        (CONFIGURED_MAX + 1, "max"),
    ],
)
def test_run_domain_defensively_rejects_an_invalid_history_cap_before_io(
    max_dates: int | None, message: str, monkeypatch
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("run_domain opened provider or target DB before validating replay cap")

    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    with pytest.raises(sr.SyncWindowError, match=message):
        sr.run_domain(
            "margin",
            backfill=True,
            start=DATA_START,
            end=VALID_END,
            max_dates=max_dates,
            registry=_registry(),
        )


def test_run_domain_rejects_history_resume_before_io(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("history resume crossed the static request boundary")

    monkeypatch.setattr(sr, "_adapter", forbidden)
    monkeypatch.setattr(sr, "_target_conn", forbidden)

    with pytest.raises(sr.SyncWindowError, match="resume"):
        sr.run_domain(
            "margin",
            backfill=True,
            start=DATA_START,
            end=VALID_END,
            max_dates=1,
            resume=True,
            registry=_registry(),
        )


def test_formal_margin_backfill_passes_max_dates_to_run_domain(monkeypatch) -> None:
    registry = _registry()
    args = _args(max_dates=CONFIGURED_MAX)
    captured: dict = {}

    def fake_run_domain(domain: str, **kwargs) -> dict:
        captured.update({"domain": domain, **kwargs})
        return {
            "domain": domain,
            "batches": 1,
            "rows": 3,
            "failed_batches": 0,
            "ok": True,
        }

    monkeypatch.setattr(sr, "run_domain", fake_run_domain)

    assert sr._main_unlocked(args, registry, ["margin"]) == 0
    assert captured["domain"] == "margin"
    assert captured["backfill"] is True
    assert captured["start"] == DATA_START
    assert captured["end"] == VALID_END
    assert captured["max_dates"] == CONFIGURED_MAX


def test_duplicate_calendar_sessions_fail_before_writer_or_authorization(
    monkeypatch,
) -> None:
    registry = _registry()
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: _args())
    monkeypatch.setattr(sr, "load_registry", lambda: registry)
    monkeypatch.setattr(
        sr,
        "trading_days",
        lambda _start, _end: [DATA_START, DATA_START, VALID_END],
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("duplicate calendar evidence crossed the preflight boundary")

    monkeypatch.setattr(writer_lock_module, "writer_lock", forbidden)
    monkeypatch.setattr(sr, "_authorization_preflight", forbidden)

    assert sr.main() == 5


def test_authorization_halt_cli_exposes_typed_history_checkpoint(
    monkeypatch, capsys
) -> None:
    registry = _registry()
    monkeypatch.setattr(sr, "_parse_cli_args", lambda: _args())
    monkeypatch.setattr(sr, "load_registry", lambda: registry)
    monkeypatch.setattr(sr, "_authorization_preflight", lambda *_args, **_kwargs: None)

    @contextmanager
    def unlocked_writer(*_args, **_kwargs):
        yield SimpleNamespace()

    monkeypatch.setattr(writer_lock_module, "writer_lock", unlocked_writer)
    halt = _attach_history_result(
        sr.TuShareAuthorizationError("auth_denied")
    )
    monkeypatch.setattr(
        sr,
        "_main_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(halt),
    )

    assert sr.main() == 3
    payload = json.loads(capsys.readouterr().out)
    evidence = payload["history_result"]
    assert payload["status"] == "authorization_blocked"
    assert evidence["accepted_dates"] == [DATA_START]
    assert evidence["next_start"] == VALID_END
    assert evidence["accepted_evidence"][0]["batch_id"] == "accepted-first-day"
    assert evidence["failures"] == [
        {
            "partition_value": VALID_END,
            "code": "account_halt",
            "evidence_hash": "e" * 64,
        }
    ]
    assert len(evidence["result_hash"]) == 64


def test_quota_halt_cli_exposes_typed_history_checkpoint(
    monkeypatch, capsys
) -> None:
    halt = _attach_history_result(sr.QuotaExhaustedError("history quota wall"))
    monkeypatch.setattr(
        sr,
        "run_domain",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(halt),
    )

    assert sr._main_unlocked(_args(), _registry(), ["margin"]) == 2
    payload = json.loads(capsys.readouterr().out)
    evidence = payload[0]["history_result"]
    assert payload[0]["status"] == "quota_halt"
    assert evidence["accepted_dates"] == [DATA_START]
    assert evidence["next_start"] == VALID_END
    assert evidence["failures"][0]["code"] == "account_halt"
    assert evidence["failures"][0]["evidence_hash"] == "e" * 64

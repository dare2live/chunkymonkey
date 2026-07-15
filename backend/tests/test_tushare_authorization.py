"""TuShare authorization boundary tests (offline: no provider or database access)."""
from datetime import datetime
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_authorization_status_calls_parameterless_user_and_discards_secrets(monkeypatch):
    from services.data_sources.sources import tushare as mod

    calls = []

    class FakePro:
        def user(self):
            calls.append("user")
            return [{
                "code": "must-not-escape",
                "token": "must-not-escape",
                "week": "4",
                "addDate": "2026/06/17 10:48:58",
                "limitDate": "2026/08/12 15:43:00",
            }]

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())
    monkeypatch.setattr(
        mod,
        "_now_shanghai",
        lambda: datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    result = mod.TuShareSource().authorization_status()

    assert calls == ["user"]
    assert set(result) == {"opened_at", "expires_at", "remaining_weeks"}
    assert result["opened_at"].isoformat() == "2026-06-17T10:48:58+08:00"
    assert result["expires_at"].isoformat() == "2026-08-12T15:43:00+08:00"
    assert result["remaining_weeks"] == 4
    assert "must-not-escape" not in repr(result)


def test_authorization_status_maps_missing_token_to_safe_reason(monkeypatch):
    from services.data_sources.sources import tushare as mod

    for name in mod.TOKEN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "missing_token"
    assert str(caught.value) == "tushare authorization blocked: missing_token"


def test_authorization_status_maps_missing_package_to_safe_reason(monkeypatch):
    from services.data_sources.sources import tushare as mod

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: (_ for _ in ()).throw(ImportError("private")))

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "package_missing"
    assert "private" not in str(caught.value)


def test_authorization_status_translates_real_tinyshare_permission_error(monkeypatch):
    import tinyshare
    from services.data_sources.sources import tushare as mod

    class FakePro:
        def user(self):
            raise tinyshare.TinySharePermissionError("provider-secret-detail")

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "auth_denied"
    assert "provider-secret-detail" not in str(caught.value)
    assert caught.value.__context__ is None


def test_authorization_status_translates_permission_error_while_building_client(monkeypatch):
    import tinyshare
    from services.data_sources.sources import tushare as mod

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(
        mod,
        "_pro_api",
        lambda token: (_ for _ in ()).throw(
            tinyshare.TinySharePermissionError("provider-secret-detail")
        ),
    )

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "auth_denied"


def test_fetch_raw_translates_real_tinyshare_permission_error(monkeypatch):
    import tinyshare
    from services.data_sources.sources import tushare as mod

    class FakePro:
        def daily(self, **params):
            raise tinyshare.TinySharePermissionError("provider-secret-detail")

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().fetch_raw("daily", trade_date="20260715")

    assert caught.value.reason == "auth_denied"
    assert caught.value.__context__ is None


def test_authorization_status_fails_closed_when_probe_is_unavailable(monkeypatch):
    import tinyshare
    from services.data_sources.sources import tushare as mod

    class FakePro:
        def user(self):
            raise tinyshare.TinyShareConnectionError("network-private-detail")

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "auth_probe_unavailable"
    assert "network-private-detail" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"week": "4", "limitDate": "2026/08/12 15:43:00"}],
        [{
            "week": "four",
            "addDate": "2026/06/17 10:48:58",
            "limitDate": "2026/08/12 15:43:00",
        }],
        [{
            "week": "4",
            "addDate": "2026-06-17",
            "limitDate": "2026/08/12 15:43:00",
        }],
    ],
)
def test_authorization_status_rejects_missing_or_malformed_metadata(monkeypatch, payload):
    from services.data_sources.sources import tushare as mod

    class FakePro:
        def user(self):
            return payload

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "auth_metadata_invalid"


def test_authorization_status_rejects_expired_account_using_shanghai_time(monkeypatch):
    from services.data_sources.sources import tushare as mod

    class FakePro:
        def user(self):
            return [{
                "week": "0",
                "addDate": "2026/06/17 10:48:58",
                "limitDate": "2026/07/15 15:59:59",
            }]

    monkeypatch.setenv("TUSHARE_TOKEN", "secret")
    monkeypatch.setattr(mod, "_pro_api", lambda token: FakePro())
    monkeypatch.setattr(
        mod,
        "_now_shanghai",
        lambda: datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.TuShareSource().authorization_status()

    assert caught.value.reason == "auth_expired"


def test_probe_authorization_enforces_hard_timeout_and_sanitizes_it():
    from services.data_sources.sources import tushare as mod

    class HungSource:
        def authorization_status(self):
            time.sleep(1)
            raise AssertionError("deadline did not interrupt the probe")

    started = time.monotonic()
    with pytest.raises(mod.TuShareAuthorizationError) as caught:
        mod.probe_authorization(HungSource(), timeout_seconds=0.02)
    assert caught.value.reason == "auth_probe_unavailable"
    assert time.monotonic() - started < 0.5

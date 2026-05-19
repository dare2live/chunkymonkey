"""Codex review a750bd44 follow-up: shim import paths smoke test.

Verify backward-compat after services/calendar.py refactor (commit 180703de):
- `from services.utils import latest_completed_trade_date` still works
- `from services.market_db import _latest_completed_trade_date_for_write` still works
- `KlineWriteLintError` alias still raises/catches CalendarMissError instances

防 future refactor 静默破坏 backward compat.

rule-compliance: ok evidence=Codex-a750bd44-WARN-shim-coverage
"""
import pytest


def test_utils_shim_latest_completed_trade_date():
    """from services.utils import latest_completed_trade_date works after refactor."""
    from services.utils import latest_completed_trade_date
    from services.calendar import latest_completed_trade_date as canonical
    assert latest_completed_trade_date is canonical


def test_utils_shim_latest_closed_or_raise():
    """from services.utils import latest_closed_or_raise works."""
    from services.utils import latest_closed_or_raise
    from services.calendar import latest_closed_or_raise as canonical
    assert latest_closed_or_raise is canonical


def test_market_db_shim_latest_completed_for_write():
    """from services.market_db import _latest_completed_trade_date_for_write works."""
    from services.market_db import _latest_completed_trade_date_for_write
    from services.calendar import latest_completed_for_kline_write as canonical
    assert _latest_completed_trade_date_for_write is canonical


def test_market_db_kline_write_lint_error_alias():
    """KlineWriteLintError = CalendarMissError 同一 class, exception catch 仍工作."""
    from services.market_db import KlineWriteLintError
    from services.calendar import CalendarMissError
    assert KlineWriteLintError is CalendarMissError
    # raise/catch sanity
    with pytest.raises(KlineWriteLintError):
        raise CalendarMissError("test")
    with pytest.raises(CalendarMissError):
        raise KlineWriteLintError("test")

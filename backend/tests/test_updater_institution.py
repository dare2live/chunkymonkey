import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import updater


@pytest.mark.asyncio
async def test_step_sync_industry_delegates_to_institution_helper(monkeypatch):
    seen = {}

    async def _fake_sync_industry(conn, **kwargs):
        seen["conn"] = conn
        seen.update(kwargs)
        return 7

    sentinel_conn = object()
    monkeypatch.setattr(updater, "_step_sync_industry_with_hooks", _fake_sync_industry)

    result = await updater._step_sync_industry(sentinel_conn)

    assert result == 7
    assert seen["conn"] is sentinel_conn
    assert seen["tracked_stock_names"] is updater._tracked_stock_names
    assert seen["should_stop"] is updater._raise_if_stop
    assert seen["update_step"] is updater._update_step
    assert seen["open_conn"] is updater.get_conn

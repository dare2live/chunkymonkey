"""Phase ψ.5 根因 3 修复 — step budget 动态化测试.

验证:
  1. 无 watermark 表 / 无 conn → 返 base
  2. lag=0 → 返 base
  3. lag=N → 返 min(base + N*per_day, max)
  4. cap 起作用 (lag 巨大不会超 max)
  5. 未知 step → fallback / None
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.duck_adapter import connect
from routers.updater import (
    STEP_BUDGET_MODEL,
    _step_budget_seconds,
    _watermark_lag_days,
)


@pytest.fixture()
def wm_conn():
    conn = connect(":memory:")
    conn.execute(
        """CREATE TABLE mart_data_source_watermark (
            data_domain VARCHAR,
            source_name VARCHAR,
            source_tier VARCHAR,
            updated_at  TIMESTAMP,
            row_count   BIGINT
        )"""
    )
    yield conn
    conn.close()


def _set_wm(conn, domain: str, source: str, days_ago: int) -> None:
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat(sep=" ", timespec="seconds")
    conn.execute("DELETE FROM mart_data_source_watermark WHERE data_domain=? AND source_name=?",
                 (domain, source))
    conn.execute(
        "INSERT INTO mart_data_source_watermark (data_domain, source_name, updated_at) VALUES (?, ?, ?)",
        (domain, source, ts),
    )


def test_budget_returns_base_when_no_conn():
    """不传 conn → 不查 watermark, 返 base."""
    base = STEP_BUDGET_MODEL["sync_market_data"]["base"]
    got = _step_budget_seconds("sync_market_data")  # no conn
    assert got == base


def test_budget_returns_base_when_lag_zero(wm_conn):
    """lag=0 → 返 base."""
    _set_wm(wm_conn, "kline_daily", "tdxhub_quote", days_ago=0)
    got = _step_budget_seconds("sync_market_data", conn=wm_conn)
    assert got == STEP_BUDGET_MODEL["sync_market_data"]["base"]


def test_budget_scales_with_lag(wm_conn):
    """lag=5 天 → base + 5 * per_day."""
    model = STEP_BUDGET_MODEL["sync_market_data"]
    _set_wm(wm_conn, "kline_daily", "tdxhub_quote", days_ago=5)
    got = _step_budget_seconds("sync_market_data", conn=wm_conn)
    expected = model["base"] + 5 * model["per_day"]
    assert got == expected


def test_budget_capped_at_max(wm_conn):
    """lag 巨大不会超 max (防脏 watermark 跑死进程)."""
    model = STEP_BUDGET_MODEL["sync_market_data"]
    # 100 天滞后, 远超 (max - base) / per_day
    _set_wm(wm_conn, "kline_daily", "tdxhub_quote", days_ago=100)
    got = _step_budget_seconds("sync_market_data", conn=wm_conn)
    assert got == model["max"]


def test_budget_zero_per_day_step_ignores_lag(wm_conn):
    """派生 step (per_day=0) lag 多少都不动."""
    model = STEP_BUDGET_MODEL["refresh_today_signals"]
    assert model["per_day"] == 0
    # 即便配 lag 也没用 — 而且这 step 没在 STEP_SOURCE_DOMAINS 里
    got = _step_budget_seconds("refresh_today_signals", conn=wm_conn)
    assert got == model["base"]


def test_budget_unknown_step_returns_none():
    """未知 step → None (caller 走 unbounded)."""
    assert _step_budget_seconds("never_exists_step") is None


def test_watermark_lag_unknown_step_returns_zero(wm_conn):
    """未注册 STEP_SOURCE_DOMAINS 的 step → lag=0."""
    assert _watermark_lag_days(wm_conn, "calc_inst_scores") == 0


def test_watermark_lag_no_row_returns_zero(wm_conn):
    """watermark 表里没该 step 对应行 → lag=0 (不假设最坏)."""
    # 没插入任何 wm row
    assert _watermark_lag_days(wm_conn, "sync_market_data") == 0


def test_real_world_market_data_budget_at_typical_lag(wm_conn):
    """真实场景: watermark 滞后 7 天, sync_market_data budget 应远 > 旧 30s static."""
    model = STEP_BUDGET_MODEL["sync_market_data"]
    _set_wm(wm_conn, "kline_daily", "tdxhub_quote", days_ago=7)
    got = _step_budget_seconds("sync_market_data", conn=wm_conn)
    assert got >= 120  # 旧值只有 30, 现在至少 120 (base)
    assert got == model["base"] + 7 * model["per_day"]  # 120 + 7*60 = 540

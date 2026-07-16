"""Test K-line write-side calendar lint (AGENTS.md Tier0 calendar truth).

Codex review 2026-05-19 HIGH 3: 缺最小有效单测覆盖 future-date filter (现有 test 用
fake row VWAP-close mismatch 被 clean_price_rows 先 reject, lint 没机会跑).

本 test 用合法 VWAP rows + monkeypatch _latest_completed_trade_date_for_write 返回
固定日期, 验证 filter 确实 reject date > last_closed.

rule-compliance: ok evidence=incident-20260519-defense-in-depth-test
"""
from __future__ import annotations

import logging

import pytest

from conftest import duck_mem
from services.market_db import (
    KlineWriteLintError,
    filter_kline_rows_by_calendar,
)
# ANALYSIS_KLINE_QFQ_VIEW_DDL/PRICE_KLINE_TDXHUB_DDL/upsert_price_kline_tdxhub_rows import 已删
# (2026-06-27: 测它们的 e2e tdxhub upsert 测试已删, M3 退役该写入路径)


PRICE_KLINE_DDL = """
CREATE TABLE IF NOT EXISTS price_kline (
    code        TEXT NOT NULL,
    date        TEXT NOT NULL,
    freq        TEXT NOT NULL DEFAULT 'daily',
    adjust      TEXT NOT NULL DEFAULT 'qfq',
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    amount      REAL,
    source      TEXT,
    batch_id    TEXT,
    ingested_at TEXT,
    PRIMARY KEY (code, date, freq, adjust)
);
"""


def _valid_row(code: str, date: str, close: float = 10.0) -> dict:
    """Construct a row that passes clean_price_row (vwap lint 2026-07-03 已物删, 行本身仍合法)."""
    return {
        "code": code,
        "date": date,
        "freq": "daily",
        "adjust": "qfq",
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.97,
        "close": close,
        "volume": 100,  # 100 lots
        "amount": close * 100 * 100,
        "factor": 1.0,
    }


def test_filter_kline_rows_by_calendar_rejects_future_dates(monkeypatch):
    """Filter helper rejects rows with date > last_closed; keeps rows on or before."""
    monkeypatch.setattr(
        "services.market_db._latest_completed_trade_date_for_write",
        lambda *, raise_on_miss=True: "2026-05-18",
    )

    rows = [
        _valid_row("600000", "2026-05-15", close=10.0),
        _valid_row("600000", "2026-05-18", close=10.1),
        _valid_row("600000", "2026-05-19", close=10.2),  # future → reject
        _valid_row("600000", "2026-05-20", close=10.3),  # future → reject
    ]
    filtered = filter_kline_rows_by_calendar(rows, output_table="price_kline_tdxhub", batch_id="lint_test")
    assert len(filtered) == 2
    assert [r["date"] for r in filtered] == ["2026-05-15", "2026-05-18"]


def test_filter_kline_rows_logs_rejection_warning(monkeypatch, caplog):
    """Rejection emits a WARNING log so ops can audit accidental bypass attempts."""
    monkeypatch.setattr(
        "services.market_db._latest_completed_trade_date_for_write",
        lambda *, raise_on_miss=True: "2026-05-18",
    )

    rows = [
        _valid_row("600000", "2026-05-18"),
        _valid_row("600000", "2026-05-19"),  # future → reject
    ]
    with caplog.at_level(logging.WARNING):
        filter_kline_rows_by_calendar(rows, output_table="price_kline_tdxhub", batch_id="lint_test")

    matching = [m for m in caplog.messages if "rejected 1 rows with date > 2026-05-18" in m]
    assert matching, f"expected rejection warning, got: {caplog.messages}"


def test_filter_kline_rows_fail_closed_on_calendar_miss(monkeypatch):
    """calendar lookup failure raises KlineWriteLintError (Codex HIGH 1 fail-closed)."""
    def _raise(*, raise_on_miss=True):
        if raise_on_miss:
            raise KlineWriteLintError("simulated calendar miss")
        return None
    monkeypatch.setattr("services.market_db._latest_completed_trade_date_for_write", _raise)

    rows = [_valid_row("600000", "2026-05-18")]
    with pytest.raises(KlineWriteLintError):
        filter_kline_rows_by_calendar(rows, raise_on_miss=True)


def test_filter_kline_rows_bypass_env_skips_lint(monkeypatch):
    """KLINE_WRITE_LINT_BYPASS=1 returns None → filter no-op (audit any uses)."""
    monkeypatch.setattr(
        "services.market_db._latest_completed_trade_date_for_write",
        lambda *, raise_on_miss=True: None,  # bypass simulated
    )
    rows = [
        _valid_row("600000", "2026-05-18"),
        _valid_row("600000", "2026-05-19"),  # future, would normally reject
    ]
    filtered = filter_kline_rows_by_calendar(rows, raise_on_miss=False)
    # bypass → no filter applied
    assert len(filtered) == 2

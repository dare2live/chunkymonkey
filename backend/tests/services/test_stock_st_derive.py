"""``stock_st_derive`` adapter contracts. Offline only: every test injects a fake
name-rows provider (or a throwaway on-disk DuckDB file for the default-provider
wiring test) — no host DB, no network.

Regression fixtures below reproduce the 2026-08-31/09-01 validation done before
this adapter was written (see module docstring in
``services/data_sources/sources/stock_st_derive.py``):

- Recall: naive ``^(?:S)?\\*ST|^ST`` (the regex already living in
  ``calendar_identity_recon.name_flags_st``) misses 116/173,413 historical
  accepted-ST rows, all either XD/XR/DR ex-dividend/rights decoration prefixes
  or the legacy ``SST`` (no-asterisk) form. This module's regex fixes both;
  ``test_name_flags_st_historical_edge_cases`` hardcodes a representative
  sample of the exact miss set found in ``canonical_stock_st_daily``.
- Precision: same regex against the 2026-08-31 real 5563-row full-universe
  snapshot produced 0 false positives.
- The 000711.SZ / 002586.SZ staleness case (accepted table stuck on Friday
  2026-08-28, both names lost their ST prefix on Monday 2026-08-31 per ifind
  戴帽摘帽 event history) is reproduced as the staleness-guard tests below.
"""
from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from services.data_sources.sources.stock_st_derive import (
    ALIAS,
    API_STOCK_ST,
    StockSTDeriveError,
    StockSTDeriveSource,
    StockSTStaleSnapshotError,
    derive_st_rows,
    name_flags_st,
)

TODAY = date(2026, 9, 1)
TODAY_COMPACT = "20260901"


# ---------------------------------------------------------------------------
# name_flags_st
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "*ST美丽",
        "ST海王",
        "ST中",
        "S*ST佳通",  # legacy: 股改未完成 + 退市风险叠加
        "SST佳通",  # legacy: 股改未完成, 无星号 — 600182.SH 2022-01-04~02 实证 77 行
        "XD*ST龙净",  # 除权当日装饰前缀 + *ST
        "XR*ST文",  # 除息当日装饰前缀 + *ST
        "DR*ST天",  # 除权除息当日装饰前缀 + *ST
        "XDST泛微",  # 除权当日装饰前缀 + ST (无星号)
        "XDS*ST佳",  # 双前缀叠加实证样本 (XD + S*ST)
        "st小写",  # 大小写不敏感
        "  ST 有空格",  # 空格容错
    ],
)
def test_name_flags_st_historical_edge_cases(name):
    assert name_flags_st(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "贵州茅台",
        "京蓝科技",  # 000711.SZ 摘帽后新名 (2026-08-31 生效)
        "围海股份",  # 002586.SZ 摘帽后新名 (2026-08-31 生效)
        "中国石化",
        "万科A",
        "",
        None,
        "非ST概念",
    ],
)
def test_name_flags_st_rejects_non_st_names(name):
    assert name_flags_st(name) is False


def test_name_flags_st_is_prefix_only_by_design_matching_upstream_convention():
    """This adapter's regex matches a bare ``^ST``/``^\\*ST`` prefix — same design
    choice as the pre-existing ``calendar_identity_recon._ST_NAME_RE``, not a
    word-boundary match. A hypothetical name like "STAR科技" would therefore also
    flag True. This is intentional, not a gap: real A-share short names are
    almost always Chinese characters, and the 2026-08-31 precision check (this
    regex against the real 5563-row full-universe snapshot) found 0 false
    positives — no listed security's actual name has ever collided with this
    prefix. If that ever changes, the fix is a word-boundary tweak here, not in
    calendar_identity_recon.py (out of this adapter's edit scope).
    """
    assert name_flags_st("STAR科技") is True


def test_name_flags_st_naive_regex_would_have_missed_these():
    """Guard against regressing to the un-hardened regex: assert the exact
    failure modes documented in the module docstring stay fixed.
    """
    naive_misses = ["SST佳通", "XD*ST龙净", "XR*ST文", "DR*ST天", "XDST泛微", "XDS*ST佳"]
    for name in naive_misses:
        assert name_flags_st(name) is True, name


# ---------------------------------------------------------------------------
# derive_st_rows
# ---------------------------------------------------------------------------


def test_derive_st_rows_shape_matches_landing_payload():
    rows = derive_st_rows(
        [{"ts_code": "000010.sz", "name": "*ST美丽"}, {"ts_code": "600000.SH", "name": "浦发银行"}],
        trade_date=TODAY_COMPACT,
    )
    assert rows == [
        {
            "ts_code": "000010.SZ",
            "name": "*ST美丽",
            "trade_date": TODAY_COMPACT,
            "type": "ST",
            "type_name": "风险警示板",
        }
    ]


def test_derive_st_rows_dedupes_by_ts_code():
    rows = derive_st_rows(
        [
            {"ts_code": "000010.SZ", "name": "*ST美丽"},
            {"ts_code": "000010.SZ", "name": "*ST美丽"},  # 上游快照偶发重复行
        ],
        trade_date=TODAY_COMPACT,
    )
    assert len(rows) == 1


def test_derive_st_rows_skips_missing_fields():
    rows = derive_st_rows(
        [
            {"ts_code": "", "name": "*ST美丽"},
            {"ts_code": "000010.SZ", "name": None},
            {"name": "*ST美丽"},
            "not-a-dict",
        ],
        trade_date=TODAY_COMPACT,
    )
    assert rows == []


def test_derive_st_rows_rejects_malformed_trade_date():
    with pytest.raises(StockSTDeriveError):
        derive_st_rows([], trade_date="2026-09-01")


# ---------------------------------------------------------------------------
# StockSTDeriveSource.fetch_raw — fake provider (no DB, no network)
# ---------------------------------------------------------------------------


def _fake_provider(name_rows, snapshot_date):
    def provider():
        return list(name_rows), snapshot_date

    return provider


def test_fetch_raw_happy_path_same_day_snapshot():
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider(
            [
                {"ts_code": "000010.SZ", "name": "*ST美丽"},
                {"ts_code": "600000.SH", "name": "浦发银行"},
            ],
            TODAY,
        )
    )
    rows = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "000010.SZ"
    assert rows[0]["trade_date"] == TODAY_COMPACT


def test_fetch_raw_unknown_api_raises_keyerror():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], TODAY))
    with pytest.raises(KeyError):
        src.fetch_raw("not-stock-st", trade_date=TODAY_COMPACT)


def test_fetch_raw_missing_trade_date_raises():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], TODAY))
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST)


def test_fetch_raw_empty_snapshot_raises():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], TODAY))
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)


def test_fetch_raw_unparseable_built_at_raises():
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], None)
    )
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)


def test_fetch_raw_stale_snapshot_fails_closed():
    """Reproduces the 000711.SZ/002586.SZ direction of error: a snapshot that is
    NOT the same calendar day as the requested trade_date must not be silently
    used, in either direction (older or newer than requested).
    """
    stale = TODAY - timedelta(days=3)  # mirrors accepted-table lag Fri 08-28 -> Mon 08-31
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], stale)
    )
    with pytest.raises(StockSTStaleSnapshotError):
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)


def test_fetch_raw_stale_snapshot_newer_than_requested_also_fails_closed():
    newer = TODAY + timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], newer)
    )
    with pytest.raises(StockSTStaleSnapshotError):
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)


def test_fetch_raw_allow_stale_snapshot_escape_hatch():
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], stale)
    )
    rows = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT, allow_stale_snapshot=True)
    assert len(rows) == 1


def test_fetch_raw_respects_max_snapshot_age_days_constructor_param():
    stale = TODAY - timedelta(days=1)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], stale),
        max_snapshot_age_days=1,
    )
    rows = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert len(rows) == 1


def test_fetch_raw_pagination_limit_offset():
    rows_in = [
        {"ts_code": f"00000{i}.SZ", "name": f"ST股{i}"} for i in range(5)
    ]
    src = StockSTDeriveSource(name_rows_provider=_fake_provider(rows_in, TODAY))
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT, limit=2, offset=1)
    assert len(page) == 2


def test_fetch_raw_malformed_trade_date_raises():
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], TODAY)
    )
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST, trade_date="2026-09-01")


# ---------------------------------------------------------------------------
# Default provider wiring — throwaway on-disk DuckDB file, monkeypatched
# database_manifest (no host DB touched).
# ---------------------------------------------------------------------------


def test_default_name_rows_provider_reads_raw_tushare_stock_basic(tmp_path, monkeypatch):
    from services.data_sources.sources import stock_st_derive as mod

    db_path = tmp_path / "tushare_raw.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            "CREATE TABLE raw_tushare_stock_basic (ts_code VARCHAR, symbol VARCHAR, "
            "name VARCHAR, market VARCHAR, built_at VARCHAR)"
        )
        con.execute(
            "INSERT INTO raw_tushare_stock_basic VALUES "
            "('000010.SZ', '000010', '*ST美丽', 'SZ', '2026-09-01T00:05:00+00:00'), "
            "('920023.BJ', '920023', 'ST北交所样例', '北交所', '2026-09-01T00:05:00+00:00'), "
            "('600000.SH', '600000', '浦发银行', 'SH', '2026-09-01T00:05:00+00:00')"
        )
    finally:
        con.close()

    class _FakeSpec:
        def resolve_path(self, repo_root=None):
            return db_path

    class _FakeManifest:
        def path_for(self, alias):
            assert alias == "tushare_raw"
            return db_path

    monkeypatch.setattr(
        "services.database_manifest.get_database_manifest", lambda: _FakeManifest()
    )

    name_rows, snapshot_date = mod._default_name_rows_provider()
    codes = {r["ts_code"] for r in name_rows}
    assert codes == {"000010.SZ", "920023.BJ", "600000.SH"}  # 不按 market 过滤, 北交所在内
    assert snapshot_date == date(2026, 9, 1)


def test_stock_st_derive_source_name_attr_matches_alias():
    assert StockSTDeriveSource().name == ALIAS == "stock_st_derive"

"""K5 Fuyao adapter/registry contracts. Offline: no live REST."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services.data_sources import sync_runner as sr
from services.data_sources.formal_boundaries import LIVE_ADAPTER, require_live_adapter
from services.data_sources.fuyao_fund_eval import (
    FUND_ENDPOINT_PATHS,
    FUND_LANDING_TABLES,
    evaluate_fund_result,
)
from services.data_sources.fuyao_kline_recon import (
    ACCEPTED_K_TABLE,
    BANNED_BASELINE_TABLES,
    dump_catalog_status,
    probe_dump_kinds,
    reject_banned_baseline,
)
from services.data_sources.sources.fuyao import (
    LIMIT_UP_REQUIRED_FIELDS,
    FuyaoMissingFieldsError,
    FuyaoPaginationError,
    FuyaoRestError,
    FuyaoSource,
    auction_event_date,
    classify_fuyao_failure,
    flatten_limit_pool_items,
)
from services.data_sources.tdxhub_kline_recon import reject_tdx_adjust
from services.duck_adapter import connect

REPO = Path(__file__).resolve().parents[3]
REGISTRY = REPO / "backend" / "config" / "sync_registry.yaml"
FIXTURE = REPO / "backend" / "tests" / "fixtures" / "fuyao_limit_up_pool.json"
RETIRED_TUSHARE = {
    "daily_info",
    "dc_daily",
    "hm_detail",
    "hm_list",
    "kpl_list",
    "ths_hot",
}
FUYAO_DOMAINS = (
    "fuyao_limit_up_pool",
    "fuyao_limit_down_pool",
    "fuyao_limit_break_pool",
    "fuyao_auction_benchmark",
)


def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def test_live_adapter_freeze_is_still_tushare() -> None:
    assert LIVE_ADAPTER == "tushare"
    assert require_live_adapter("tushare", domain="daily") == "tushare"
    with pytest.raises(Exception, match="unsupported_live_adapter"):
        require_live_adapter("fuyao", domain="daily")


def test_adapter_dispatches_fuyao_without_live_adapter_freeze() -> None:
    sr._FUYAO_SOURCE = None
    src = sr._adapter("fuyao")
    assert isinstance(src, FuyaoSource)
    assert sr._adapter("fuyao") is src


def test_adapter_tushare_still_works() -> None:
    sr._TUSHARE_SOURCE = None
    src = sr._adapter("tushare")
    assert src.__class__.__name__ == "TuShareSource"
    assert sr._adapter("tushare") is src


def test_adapter_unknown_source_fails_closed() -> None:
    with pytest.raises(KeyError, match="未知 source"):
        sr._adapter("akshare")


def test_kpl_list_is_not_a_live_registry_domain() -> None:
    names = set((_registry().get("domains") or {}))
    assert names.isdisjoint(RETIRED_TUSHARE)
    assert "kpl_list" not in names
    for name in FUYAO_DOMAINS:
        spec = (_registry()["domains"][name])
        assert spec["source"] == "fuyao"
        assert spec["source"] != "tushare"
        assert "page_limit" not in spec
        assert spec.get("sync_policy") == "on_demand"
        assert str(spec.get("target_table", "")).startswith("raw_fuyao_")


def test_automatic_domains_exclude_fuyao_on_demand() -> None:
    auto = set(sr.automatic_domains(_registry()))
    assert set(FUYAO_DOMAINS).isdisjoint(auto)
    assert "moneyflow" in auto


def test_limit_offset_pagination_is_rejected() -> None:
    src = FuyaoSource(rest=lambda *a, **k: (_ for _ in ()).throw(AssertionError("rest")), api_key="x")
    with pytest.raises(FuyaoPaginationError, match="page/size"):
        src.fetch_raw("limit-up-pool", trade_date="20260827", limit=50, offset=0)
    with pytest.raises(FuyaoPaginationError, match="page/size"):
        src.fetch_raw("limit-down-pool", trade_date="20260827", limit=200)


def test_pool_fetch_uses_page_size_and_stamps_request_date() -> None:
    calls: list[dict] = []

    def rest(path, *, api_key, params, timeout=30.0):
        calls.append(dict(params))
        page = int(params["page"])
        if page == 1:
            return {
                "timestamp": 9999999999999,
                "pagination": {"total": 3, "pages": 2, "size": 2, "page": 1},
                "item": [
                    {
                        "thscode": "300841.SZ",
                        "is_st": False,
                        "seal_money": 1.0,
                        "limit_up_time": "09:30",
                        "continue_day_cnt": 2,
                    },
                    {
                        "thscode": "000001.SZ",
                        "is_st": True,
                        "seal_money": 2.0,
                        "limit_up_time": "10:00",
                        "continue_day_cnt": 1,
                    },
                ],
            }
        return {
            "timestamp": 9999999999999,
            "pagination": {"total": 3, "pages": 2, "size": 2, "page": 2},
            "item": [
                {
                    "thscode": "600000.SH",
                    "is_st": False,
                    "seal_money": 3.0,
                    "limit_up_time": "11:00",
                    "continue_day_cnt": 3,
                }
            ],
        }

    src = FuyaoSource(rest=rest, api_key="k")
    rows = src.fetch_raw("limit-up-pool", trade_date="20200701", size=2)
    assert [c["page"] for c in calls] == [1, 2]
    assert all(c["size"] == 2 for c in calls)
    assert all("limit" not in c and "offset" not in c for c in calls)
    assert all("date_ms" in c for c in calls)
    assert len(rows) == 3
    assert {r["trade_date"] for r in rows} == {"20200701"}
    assert all(r["trade_date"] != "9999999999999" for r in rows)
    for field in LIMIT_UP_REQUIRED_FIELDS:
        assert field in rows[0]


def test_missing_limit_up_fields_fail_closed() -> None:
    with pytest.raises(FuyaoMissingFieldsError, match="is_st"):
        flatten_limit_pool_items(
            [{"thscode": "000001.SZ", "seal_money": 1}],
            trade_date="20200701",
            require_limit_up_fields=True,
        )


def test_http_404_is_not_classified_offline() -> None:
    err = FuyaoRestError("http 404 /missing", http=404)
    assert classify_fuyao_failure(err) == "http_404"
    assert classify_fuyao_failure(err) != "offline"
    probes = probe_dump_kinds(
        lambda kind: (_ for _ in ()).throw(RuntimeError("download HTTP 404: gone"))
        if kind == "daily-k"
        else {"presigned_url": "https://example.test/x.parquet"}
    )
    by_kind = {p.kind: p.outcome for p in probes}
    assert by_kind["daily-k"] == "http_404"
    assert dump_catalog_status(probes) != "offline"
    assert dump_catalog_status(probes) == "partial_or_ready"


def test_auction_event_date_ignores_response_timestamp() -> None:
    payload = {
        "timestamp": 1787912740449,
        "date": "2026-08-27",
        "date_ms": 1787760000000,
        "item": [],
    }
    assert auction_event_date(payload) == "20260827"
    assert auction_event_date({"timestamp": 1787912740449, "item": []}) is None


def test_fixture_keeps_limit_up_vendor_fields() -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    row = sample["rows"][0]
    for field in LIMIT_UP_REQUIRED_FIELDS:
        assert field in row
    assert row["trade_date"] == "20200701"


def test_limit_up_rows_land_on_grain() -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    spec = {
        "domain": "fuyao_limit_up_pool",
        "source": "fuyao",
        "api": "limit-up-pool",
        "target_table": "raw_fuyao_limit_up_pool",
        "grain": ["ts_code", "trade_date"],
        "min_rows_per_batch": 0,
        "allow_empty_batch": True,
    }
    conn = connect(":memory:")
    sr._write_batch(conn, spec, sample["rows"])
    n, st, seal = conn.execute(
        "SELECT count(*), any_value(is_st), any_value(seal_money) "
        "FROM raw_fuyao_limit_up_pool"
    ).fetchone()
    assert n == 1
    assert st is False
    assert seal == 617238170.9


def test_qfq_is_not_kline_ssot() -> None:
    with pytest.raises(ValueError, match="banned"):
        reject_banned_baseline("price_kline_qfq_tushare")
    assert "price_kline_qfq_tushare" in BANNED_BASELINE_TABLES
    assert reject_banned_baseline(ACCEPTED_K_TABLE) == ACCEPTED_K_TABLE
    with pytest.raises(ValueError, match="banned tdx adjust"):
        reject_tdx_adjust("qfq")


def test_fund_28_eval_does_not_invent_landing_tables() -> None:
    assert len(FUND_ENDPOINT_PATHS) == 28
    assert FUND_LANDING_TABLES == {}
    ok = evaluate_fund_result(
        "/api/fund/portfolio/holdings",
        payload={"item": [{"thscode": "510300.SH"}]},
    )
    assert ok["status"] == "ok"
    assert ok["land"] is False
    empty = evaluate_fund_result(
        "/api/fund/news/article-list", payload={"item": []}
    )
    assert empty["status"] == "zero_rows"
    assert empty["land"] is False
    mismatch = evaluate_fund_result(
        "/api/fund/market/snapshot",
        error=FuyaoRestError("code=3004", http=200, code=3004),
    )
    assert mismatch["status"] == "product_mismatch"
    not_ready = evaluate_fund_result(
        "/api/fund/holders/detail",
        error=FuyaoRestError("code=3002", http=200, code=3002),
    )
    assert not_ready["status"] == "not_ready"

"""A5 adversarial tests for formal adapter/landing/canonical boundaries."""
from __future__ import annotations

import dataclasses

import pytest

from services.data_sources.formal_boundaries import (
    LIVE_ADAPTER,
    _FORMAL_BOUNDARIES,
    FormalBoundaryError,
    boundary_inventory,
    formal_domains,
    refuse_legacy_raw_write_for_formal_domain,
    require_live_adapter,
)
from services.data_sources import sync_runner as sr


def test_live_adapter_default_still_guards_unmigrated_domains() -> None:
    # 2026-09-01: tushare 授权 2026-09-10 到期不续期, formal 域已全部换源完毕:
    #   trade_cal -> calendar_rule (规则推导)   daily     -> tdxhub (通达信)
    #   stock_st  -> stock_st_derive (名称派生)  margin    -> 仍 tushare 但 retired_readonly
    # 即 **LIVE_ADAPTER 已无任何活跃取数域在用** —— 只剩 margin 这个已退役只读域挂着它。
    # 该常量与本守卫仍保留: 它挡的是"往 formal 域塞未授权 adapter"(如 akshare), 与哪个
    # 域用它无关; margin 是当前唯一还能取样的域。若日后 margin 也物理退役, 本测试应改为
    # 直接断言守卫行为而非取样某个真实域。
    assert require_live_adapter("tushare", domain="margin") == LIVE_ADAPTER
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="margin")


def test_trade_cal_live_adapter_is_calendar_rule_only() -> None:
    """2026-08-31 authorized source switch (baostock -> calendar_rule): trade_cal
    is the one formal domain whose adapter is no longer LIVE_ADAPTER (tushare) —
    see formal_boundaries.py _FORMAL_BOUNDARIES["trade_cal"]. This supersedes the
    2026-08-30 tushare->baostock cut: baostock got blacklisted by its own risk
    control mid-concurrency-probe on 2026-08-31, and turned out structurally
    incapable of returning future trade dates anyway (see
    services/data_sources/sources/calendar_rule.py module docstring for the
    three-source dead end that motivated calendar_rule). Both the retired
    LIVE_ADAPTER default and the retired baostock adapter must be rejected here.
    """
    assert require_live_adapter("calendar_rule", domain="trade_cal") == "calendar_rule"
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("tushare", domain="trade_cal")
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("baostock", domain="trade_cal")


def test_wildcard_domain_accepts_any_registered_formal_adapter() -> None:
    # domain="*" is the sync_runner._adapter(source_name) call site, which
    # only has a source name in hand, never a single domain. It must accept
    # any adapter declared by any registered formal domain.
    assert require_live_adapter("tushare", domain="*") == "tushare"


def test_wildcard_domain_rejects_unregistered_adapter() -> None:
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="*")


def test_unregistered_domain_falls_back_to_live_adapter_only() -> None:
    assert require_live_adapter("tushare", domain="no_such_domain") == LIVE_ADAPTER
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("akshare", domain="no_such_domain")


def test_per_domain_adapter_override_is_isolated_to_its_own_domain(monkeypatch) -> None:
    # trade_cal itself is now permanently calendar_rule (2026-08-31 authorized
    # source switch, baostock -> calendar_rule), so exercise the override
    # mechanism against "daily" instead — temporarily declare it using a
    # different adapter than its LIVE_ADAPTER default, and confirm
    # require_live_adapter enforces that per-domain, without leaking into
    # sibling domains (including trade_cal's own real, non-monkeypatched
    # calendar_rule adapter). "baostock" is used below purely as an arbitrary
    # distinct adapter name to prove the override mechanism — baostock is not
    # bound to any real domain any more (see
    # test_baostock_adapter.py::test_baostock_is_not_any_domains_source_after_calendar_rule_switch).
    # monkeypatch.setitem restores _FORMAL_BOUNDARIES["daily"] automatically
    # after the test.
    original = _FORMAL_BOUNDARIES["daily"]
    monkeypatch.setitem(
        _FORMAL_BOUNDARIES,
        "daily",
        dataclasses.replace(original, adapter="baostock"),
    )

    assert require_live_adapter("baostock", domain="daily") == "baostock"
    with pytest.raises(FormalBoundaryError, match="unsupported_live_adapter"):
        require_live_adapter("tushare", domain="daily")

    # Sibling domains are unaffected by daily's override: margin/stock_st stay
    # tushare-only, and trade_cal's real (unrelated) calendar_rule adapter
    # still works.
    assert require_live_adapter("tushare", domain="margin") == LIVE_ADAPTER
    assert require_live_adapter("calendar_rule", domain="trade_cal") == "calendar_rule"


def test_inventory_declares_three_boundaries_for_formal_domains() -> None:
    domains = set(formal_domains())
    assert {"margin", "trade_cal", "daily", "stock_st"} <= domains
    inventory = {item["domain"]: item for item in boundary_inventory()}
    assert inventory["margin"]["runtime_state"] == "retired_readonly"
    assert inventory["trade_cal"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["daily"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    assert inventory["stock_st"]["runtime_state"] == "accepted_runtime_ready_canary_pending"
    # 授权换源进行中 (tushare 2026-09-10 到期不续期), 每个域自己声明 adapter:
    #   2026-08-31 trade_cal -> calendar_rule (规则推导, 无供应商)
    #   2026-09-01 daily     -> tdxhub (通达信; 46872/46872 字段零差异 + 覆盖北交所)
    #   2026-09-01 stock_st  -> stock_st_derive (名称前缀派生, 读本域已换扶摇的 stock_basic)
    # margin 是唯一仍挂 tushare 的 formal 域。
    assert inventory["trade_cal"]["adapter"] == "calendar_rule"
    assert inventory["daily"]["adapter"] == "tdxhub"
    assert inventory["stock_st"]["adapter"] == "stock_st_derive"
    assert inventory["margin"]["adapter"] == "tushare"
    # 不变量: 任何仍指向 tushare 的 formal 域, 必须在日落台账 tushare_sunset.yaml 里有裁决。
    #
    # **本条最初写的是 `assert runtime_state == "retired_readonly"`, 那是错的** ——
    # margin 标着 retired_readonly, 但 2026-09-01 实测它**仍在活跃调 tushare**:
    #   raw_tushare_margin (legacy 镜像)        停在 20260716   ← 只有这半边退役了
    #   canonical_margin_exchange_daily.built_at 2026-08-28     ← 3 天前还在写
    #   margin_acceptance.py:83 source="tushare", 走 2026-07-23 解冻的 on_demand 有界追赶
    # 即 runtime_state 是**会过期的文档标签**, 拿它当事实等于用未经验证的东西做判据。
    # 改断言"台账里有裁决"——这是真正可验证的: 台账是人写的裁决记录, 不会因为某条
    # 通道被解冻而悄悄失真, 且日落门 check_tushare_sunset.py 会独立校验台账与 registry 一致。
    import yaml as _yaml
    from pathlib import Path as _Path
    _sunset = _yaml.safe_load(
        (_Path(__file__).resolve().parents[3] / "backend/config/tushare_sunset.yaml").read_text(encoding="utf-8")
    )
    _decided = set(_sunset.get("domains") or {})
    for item in inventory.values():
        if item["adapter"] == "tushare":
            assert item["domain"] in _decided, (
                f"formal 域 {item['domain']} 仍挂 tushare 但日落台账无裁决 "
                f"(授权 2026-09-10 到期不续期)"
            )
    for item in inventory.values():
        assert item["legacy_raw_write"] == "forbidden"
        assert item["landing_writer"]
        assert item["canonical_writer"]
        assert not str(item["landing_writer"]).startswith("pending:")
        assert not str(item["canonical_writer"]).startswith("pending:")


def test_trade_cal_and_margin_cannot_use_legacy_raw_writer() -> None:
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("trade_cal")
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("margin")


def test_daily_and_stock_st_cannot_use_legacy_raw_writer() -> None:
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("daily")
    with pytest.raises(FormalBoundaryError, match="formal_legacy_raw_write_forbidden"):
        refuse_legacy_raw_write_for_formal_domain("stock_st")


def test_enabled_trade_cal_uses_formal_publish_not_legacy_raw(monkeypatch) -> None:
    registry = sr.load_registry()
    monkeypatch.setattr(
        sr,
        "_publish_trade_cal_accepted_generation",
        lambda _spec: {
            "domain": "trade_cal",
            "status": "ok",
            "batches": 1,
            "rows": 1,
            "failed_batches": 0,
            "publication": "accepted_calendar_generation",
        },
    )
    monkeypatch.setattr(
        sr,
        "_write_batch",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("_write_batch")),
    )
    result = sr.run_domain("trade_cal", registry=registry)
    assert result["publication"] == "accepted_calendar_generation"


def test_trade_cal_is_the_sole_formal_domain_that_never_fetches_from_a_vendor() -> None:
    """New lock added with the 2026-08-31 authorized source switch (baostock ->
    calendar_rule). trade_cal's adapter is neither LIVE_ADAPTER (tushare) — the
    original source — nor baostock — the 2026-08-30 authorized replacement that
    got blacklisted by its own risk control mid-concurrency-probe on 2026-08-31
    and, per real-world testing of the three alternative sources, turned out
    structurally incapable of ever returning future trade dates (fuyao's
    calendar endpoint window is locked to [today-1y, today], tdxhub can only
    infer a calendar backward from K-line data that has already happened, and
    miaoxiang has no calendar product line at all) — but calendar_rule.

    trade_cal is the ONE formal domain in this entire project that never fetches
    from any vendor. Its correctness is guaranteed by
    backend/config/market_holidays.yaml (245 segments spanning 36 years of
    statutory holidays) plus a first-principles derivation rule (trading day =
    Monday-Friday minus statutory holidays), never by a vendor's response. This
    has been verified: 1990-2026, 13,162 days, zero field-level divergence
    against the in-database calendar's is_open/pretrade_date. See
    services/data_sources/sources/calendar_rule.py's module docstring for the
    full derivation and its documented "unconfirmed_years" upper-bound caveat
    for years missing from the holiday config.

    The value of this lock: if anyone in the future wants to switch the
    calendar back to fetching from a vendor (tushare/baostock/fuyao/tdxhub/
    whatever), they must first affirmatively break this assertion — it will not
    happen by accident.
    """
    boundary = _FORMAL_BOUNDARIES["trade_cal"]
    assert boundary.adapter == "calendar_rule"
    assert boundary.adapter != LIVE_ADAPTER
    assert boundary.adapter != "baostock"

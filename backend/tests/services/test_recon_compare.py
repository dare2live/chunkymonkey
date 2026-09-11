"""recon_compare: codeset overlap vs grain-declared row comparison.

Locks the split that fixed the assignment_gap_recon incident: the prior
single helper threw both sides into a Python ``set`` and reported
``identity`` regardless of whether the caller's keys matched the domain's
declared grain (block_trade compared without price/vol collapsed 13.56% of
rows into false identity). ``compare_codeset`` now never emits an
``identity`` key at all; ``compare_rows`` resolves grain from a real
registry (sync_registry.yaml / MART_GRAINS) and uses ``Counter`` so
row-collapse is visible and gates ``identity`` off.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from services.data_sources.recon_compare import (
    assert_report_identity_invariant,
    compare_codeset,
    compare_rows,
)
from services.data_sources.sync_runner import domain_spec


def _block_row(*, ts_code="600000.SH", trade_date="20260101", price=10.0,
                vol=100, buyer="b", seller="s"):
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "price": price,
        "vol": vol,
        "buyer": buyer,
        "seller": seller,
    }


def _stub_registry(domain, *, grain, multiplicity_index=None, source="miaoxiang"):
    """Minimal sync_registry-shaped dict for multiplicity-index tests.

    Not the production registry — grain 契约 S3 用例只测 recon_compare 自己的
    compare_key / identity 逻辑, 不读生产 yaml (规则见任务卡片第 3 条)。
    """
    entry: dict[str, Any] = {"source": source, "grain": list(grain)}
    if multiplicity_index is not None:
        # 契约耦合 (sync_runner._duplicate_policy): multiplicity_index 只能配
        # duplicate_rows=event + write_mode=replace_partition, 否则 domain_spec 拒绝。
        entry["multiplicity_index"] = multiplicity_index
        entry["duplicate_rows"] = "event"
        entry["write_mode"] = "replace_partition"
        entry["partition_by"] = [grain[0]]
    return {"domains": {domain: entry}}


# ---------------------------------------------------------------- codeset --

def test_compare_codeset_never_has_identity_key():
    out = compare_codeset(
        ["a", "b"], ["a", "b"], what="w", left_name="l", right_name="r"
    )
    assert "identity" not in out
    assert "same_product" not in out
    assert out["jaccard"] == 1.0
    assert out["comparison"] == "codeset"


def test_compare_codeset_empty_is_not_a_match():
    out = compare_codeset([], [], what="w", left_name="l", right_name="r")
    assert out["status"] == "empty_recon"
    assert out["jaccard"] is None
    assert "identity" not in out


# ------------------------------------------------------------------- rows --

def test_compare_rows_missing_grain_column_raises_with_column_name():
    # Uses a stub registry (not production block_trade) so this test doesn't
    # drift when S1 lands and adds `seq` + multiplicity_index to block_trade.
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    rows = [
        {
            "ts_code": "600000.SH",
            "trade_date": "20260101",
            "buyer": "b",
            "seller": "s",
        }
    ]
    with pytest.raises(KeyError, match="price|vol"):
        compare_rows(
            domain="blk6",
            left_rows=rows,
            right_rows=rows,
            left_name="l",
            right_name="r",
            registry=registry,
        )


def test_compare_rows_full_grain_mismatch_is_not_identity():
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    left = [_block_row(vol=100)]
    right = [_block_row(vol=200)]
    out = compare_rows(
        domain="blk6",
        left_rows=left,
        right_rows=right,
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["identity"] is False
    assert out["only_left"] == 1
    assert out["only_right"] == 1
    assert out["matched"] == 0


def test_compare_rows_left_collapse_is_visible_and_blocks_identity():
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    r = _block_row()
    out = compare_rows(
        domain="blk6",
        left_rows=[r, r],
        right_rows=[r],
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["left_collapse"] == 1
    assert out["matched"] == 1
    assert out["only_left"] == 1
    assert out["identity"] is False


def test_compare_rows_full_grain_match_is_identity():
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    r = _block_row()
    out = compare_rows(
        domain="blk6",
        left_rows=[r],
        right_rows=[r],
        left_name="l",
        right_name="r",
        registry=registry,
    )
    expected_grain = list(domain_spec(registry, "blk6")["grain"])
    assert out["identity"] is True
    assert out["grain"] == expected_grain
    assert out["grain_source"] == "sync_registry"
    assert out["left_collapse"] == 0
    assert out["right_collapse"] == 0


def test_compare_rows_unknown_domain_raises_key_error():
    with pytest.raises(KeyError):
        compare_rows(
            domain="no_such_domain_zz",
            left_rows=[],
            right_rows=[],
            left_name="l",
            right_name="r",
        )


def test_compare_rows_mart_grains_source(monkeypatch):
    # Stub MART_GRAINS (not production) so this test doesn't drift when S5
    # lands and changes fact_top_inst_seat_daily's grain to the new six-plus
    # event_seq shape.
    import scripts.check_grain_uniqueness as cgu

    monkeypatch.setattr(
        cgu,
        "MART_GRAINS",
        [("smartmoney", "fact_stub", ["trade_date", "ts_code", "exalter", "side"])],
    )
    out = compare_rows(
        domain="fact_stub",
        grain_source="mart_grains",
        left_rows=[],
        right_rows=[],
        left_name="l",
        right_name="r",
    )
    assert out["grain"] == ["trade_date", "ts_code", "exalter", "side"]


def test_compare_rows_bogus_grain_source_raises_value_error():
    with pytest.raises(ValueError):
        compare_rows(
            domain="block_trade",
            grain_source="bogus",
            left_rows=[],
            right_rows=[],
            left_name="l",
            right_name="r",
        )


def test_compare_rows_canonicalizes_date_and_decimal_representations():
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    left = {
        "ts_code": "600000.SH",
        "trade_date": datetime.date(2026, 8, 25),
        "price": Decimal("11.50"),
        "vol": 100,
        "buyer": "b",
        "seller": "s",
    }
    right = {
        "ts_code": "600000.SH",
        "trade_date": "20260825",
        "price": 11.5,
        "vol": 100,
        "buyer": "b",
        "seller": "s",
    }
    out = compare_rows(
        domain="blk6",
        left_rows=[left],
        right_rows=[right],
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["matched"] == 1
    assert out["identity"] is True


# ------------------------------------------------------- multiplicity index --
# grain 契约 S3: 一些域的 grain 末列是落地层派生的多重度索引 (如 block_trade
# 的 seq) —— 供应商侧行不会带这一列、也不该被要求带。比较键剔除该列; 两侧在
# 比较键上折叠对声明了 multiplicity_index 的域是预期行为, 不挡 identity。

def test_compare_rows_multiplicity_index_domain_is_identity_with_collapse():
    # R1
    registry = _stub_registry("evt", grain=["k", "seq"], multiplicity_index="seq")
    left = [{"k": 1, "seq": 1}, {"k": 1, "seq": 2}]
    right = [{"k": 1}, {"k": 1}]
    out = compare_rows(
        domain="evt",
        left_rows=left,
        right_rows=right,
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["identity"] is True
    assert out["matched"] == 2
    assert out["left_collapse"] == 1
    assert out["right_collapse"] == 1
    assert out["compare_key"] == ["k"]
    assert out["multiplicity_index"] == "seq"
    assert out["max_multiplicity_left"] == 2
    assert out["max_multiplicity_right"] == 2


def test_compare_rows_multiplicity_index_domain_right_short_is_not_identity():
    # R2
    registry = _stub_registry("evt", grain=["k", "seq"], multiplicity_index="seq")
    left = [{"k": 1, "seq": 1}, {"k": 1, "seq": 2}]
    right = [{"k": 1}]
    out = compare_rows(
        domain="evt",
        left_rows=left,
        right_rows=right,
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["identity"] is False
    assert out["only_left"] == 1


def test_assert_report_identity_invariant_allows_declared_multiplicity_collapse():
    # R4 (first half): declared multiplicity_index legitimizes the collapse.
    assert_report_identity_invariant(
        {
            "x": {
                "identity": True,
                "grain_source": "sync_registry",
                "multiplicity_index": "seq",
                "left_collapse": 1,
                "right_collapse": 1,
            }
        }
    )


def test_assert_report_identity_invariant_rejects_collapse_without_declared_index():
    # R4 (second half): same dict, multiplicity_index None -> still raises.
    with pytest.raises(ValueError):
        assert_report_identity_invariant(
            {
                "x": {
                    "identity": True,
                    "grain_source": "sync_registry",
                    "multiplicity_index": None,
                    "left_collapse": 1,
                    "right_collapse": 1,
                }
            }
        )


def test_compare_rows_multiplicity_index_ignores_extra_index_column_on_right():
    # R5: right side happens to carry its own "seq" values (e.g. a raw vendor
    # dump that still has some other ordinal) -- they must not leak into the
    # comparison key or change the result versus R1.
    registry = _stub_registry("evt", grain=["k", "seq"], multiplicity_index="seq")
    left = [{"k": 1, "seq": 1}, {"k": 1, "seq": 2}]
    right = [{"k": 1, "seq": 7}, {"k": 1, "seq": 7}]
    out = compare_rows(
        domain="evt",
        left_rows=left,
        right_rows=right,
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["identity"] is True
    assert out["matched"] == 2
    assert out["left_collapse"] == 1
    assert out["right_collapse"] == 1
    assert out["compare_key"] == ["k"]


# ------------------------------------------------------- report invariant --

def test_assert_report_identity_invariant_requires_grain_source():
    with pytest.raises(ValueError):
        assert_report_identity_invariant(
            {"x": {"identity": True, "left_collapse": 0, "right_collapse": 0}}
        )


def test_assert_report_identity_invariant_requires_zero_collapse():
    with pytest.raises(ValueError):
        assert_report_identity_invariant(
            {
                "x": {
                    "identity": True,
                    "grain_source": "sync_registry",
                    "left_collapse": 0,
                    "right_collapse": 1,
                }
            }
        )


def test_assert_report_identity_invariant_passes_a_rigorous_identity():
    assert_report_identity_invariant(
        {
            "miaoxiang": {
                "s": {
                    "identity": True,
                    "grain_source": "sync_registry",
                    "left_collapse": 0,
                    "right_collapse": 0,
                }
            }
        }
    )


# ------------------------------------------------- compare_index_closes --
# Lives in assignment_gap_recon.py (not this module) but is part of the same
# identity-rigor fix: a duplicate trade_date on the fuyao side must show up
# as right_collapse and block identity, not silently overwrite in a dict
# comprehension.

def test_compare_index_closes_fuyao_duplicate_trade_date_collapses():
    from services.data_sources.assignment_gap_recon import compare_index_closes

    acc = [{"trade_date": "20260825", "close": 4552.03}]
    fy = [
        {"trade_date": "20260825", "close": 4552.03},
        {"trade_date": "20260825", "close": 9999.0},
    ]
    body = compare_index_closes(acc, fy)
    assert body["right_collapse"] == 1
    assert body["identity"] is False


# ------------------------------------------- mutation-killers (2026-09-11) --
# 三条原测试锁不住的条件, 由变异验证实测发现 (删掉对应条件后原测试仍全绿):
#   M2 compare_rows 的 identity 不再要求两侧折叠为 0
#   M3 build_report 不再调用 assert_report_identity_invariant
#   M4 compare_index_closes 的 identity 不再要求折叠为 0
# 每条都构造成「其它条件全部满足、只有被测条件在起作用」, 否则会被别的条件顺带挡住而锁不住。

def test_compare_rows_equal_counters_with_collapse_are_not_identity():
    # R3: blk6 未声明 multiplicity_index，所以折叠必须挡 identity —— the
    # "leniency" R1 exercises is only earned by an explicit declaration.
    registry = _stub_registry(
        "blk6", grain=["ts_code", "trade_date", "price", "vol", "buyer", "seller"]
    )
    r = _block_row()
    out = compare_rows(
        domain="blk6",
        left_rows=[r, r],
        right_rows=[r, r],
        left_name="l",
        right_name="r",
        registry=registry,
    )
    assert out["matched"] == 2
    assert out["only_left"] == 0
    assert out["only_right"] == 0
    assert out["left_collapse"] == 1
    assert out["right_collapse"] == 1
    assert out["max_multiplicity_right"] == 2
    assert out["identity"] is False


def test_build_report_enforces_identity_invariant():
    from services.data_sources.assignment_gap_recon import build_report

    with pytest.raises(ValueError):
        build_report({"x": {"identity": True}})
    ok = build_report(
        {
            "x": {
                "identity": True,
                "grain_source": "sync_registry",
                "left_collapse": 0,
                "right_collapse": 0,
            }
        }
    )
    assert ok["x"]["identity"] is True


def test_compare_index_closes_duplicate_with_equal_close_still_blocks_identity():
    from services.data_sources.assignment_gap_recon import compare_index_closes

    row = {"trade_date": "20260825", "close": 4552.03}
    body = compare_index_closes([row], [row, dict(row)])
    assert body["close_mismatch"] == 0
    assert body["right_collapse"] == 1
    assert body["identity"] is False

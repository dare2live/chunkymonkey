"""``stock_st_derive`` adapter contracts. Offline only: every test injects a fake
name-rows provider and/or a fake reservoir reader (or a throwaway on-disk DuckDB
file for the default-provider wiring test) — no host DB, no network.

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

2026-09-18 (ST 契约 v2 刀3): 单水库"陈旧即拒" (``StockSTStaleSnapshotError``) 已
被双水库路径选择取代——非快照当天不再直接拒绝, 而是尝试 baostock 水库路径;
两条路径都答不出才拒 (``StockSTUnanswerableError``, ``SourceCannotAnswerDateError``
的子类)。``fetch_raw`` 现在返回 ``ProviderPage`` (``.rows`` / ``.request_meta``)
而不是裸 ``list[dict]``；``derive_st_rows`` 的落地行新增 ``st_origin`` 增补列。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
import yaml

from services.data_sources.fetch_verdict import SourceCannotAnswerDateError
from services.data_sources.baostock_daily_k_reservoir import ReservoirRow
from services.data_sources.security_day_capture import ProviderPage
from services.data_sources.sources.stock_st_derive import (
    ALIAS,
    API_STOCK_ST,
    DateAnswerability,
    StockSTDeriveError,
    StockSTDeriveSource,
    StockSTUnanswerableError,
    derive_st_rows,
    name_flags_st,
    snapshot_day_for,
)
from services.data_sources.stock_st_acquire_rules import load_stock_st_acquire_rules

TODAY = date(2026, 9, 1)
TODAY_COMPACT = "20260901"
_RULES = load_stock_st_acquire_rules()


def _aware(day: date, hour: int = 10) -> datetime:
    """构造一个 aware UTC 时刻, 落在该日的 Asia/Shanghai 当地 09:20 cutoff 之后
    (UTC 10:00 == CST 18:00, 同一个日历日) —— 单测的默认"新鲜快照"时刻。"""
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)


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
# derive_st_rows — v2: st_origin 增补列 (F12/spec §4.4)
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
            "st_origin": "derived_name_prefix",
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


def test_derive_st_rows_uses_injected_rules_not_a_code_literal(tmp_path):
    """C3 (参数无副本): tmp 副本 YAML 把 ``membership_labels.type_name`` 改成
    ``X`` 并注入 loader 路径 -> 派生行 ``type_name == "X"``。证明 type/type_name
    来自配置现读, 不是 ``derive_st_rows`` 里的代码字面量 (业主 09-16 参数规则:
    改一次覆盖范围/标签不该要求改代码)。``st_origin`` 不受影响 (它是结构性来源
    标签, 不是 YAML 里可配的东西)。"""
    from services.data_sources.stock_st_acquire_rules import (
        _CONFIG_PATH,
        load_stock_st_acquire_rules,
    )

    raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["membership_labels"] = {**raw["membership_labels"], "type_name": "X"}
    variant_path = tmp_path / "variant.yaml"
    variant_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    custom_rules = load_stock_st_acquire_rules(path=variant_path)

    rows = derive_st_rows(
        [{"ts_code": "000010.SZ", "name": "*ST美丽"}],
        trade_date=TODAY_COMPACT,
        rules=custom_rules,
    )
    assert rows[0]["type_name"] == "X"
    assert rows[0]["type"] == raw["membership_labels"]["type"]  # 未改的键不受影响
    assert rows[0]["st_origin"] == "derived_name_prefix"  # 结构性来源标签不经配置


# ---------------------------------------------------------------------------
# snapshot_day_for — F2 修法 (Asia/Shanghai + 09:20 cutoff, 见 C1)
# ---------------------------------------------------------------------------


def test_snapshot_day_for_after_cutoff_same_local_day():
    # 2026-09-17T15:30:00+00:00 == CST 23:30 (同一天, cutoff 之后)
    assert snapshot_day_for("2026-09-17T15:30:00+00:00", _RULES) == date(2026, 9, 17)


def test_snapshot_day_for_utc_date_bug_would_misattribute():
    """02:00 CST 09-18 (UTC 18:00 09-17) 早于 cutoff -> None (fail-closed)。旧的
    UTC-日期直取法会错误地把它归到 09-17 (F2 的原始 bug)——本测试锁住修法, 不
    锁住 bug。"""
    assert snapshot_day_for("2026-09-17T18:00:00+00:00", _RULES) is None


def test_snapshot_day_for_at_and_after_cutoff():
    assert snapshot_day_for("2026-09-18T01:30:00+00:00", _RULES) == date(2026, 9, 18)  # 09:30 CST
    assert snapshot_day_for("2026-09-18T01:00:00+00:00", _RULES) is None  # 09:00 CST, 早于 cutoff


def test_snapshot_day_for_naive_datetime_is_none():
    assert snapshot_day_for("2026-09-17 12:00:00", _RULES) is None
    assert snapshot_day_for(datetime(2026, 9, 17, 12, 0), _RULES) is None


# ---------------------------------------------------------------------------
# StockSTDeriveSource.fetch_raw — fake provider (no DB, no network)
# ---------------------------------------------------------------------------


def _fake_provider(name_rows, built_at):
    def provider():
        return list(name_rows), built_at

    return provider


class _FakeReservoirReader:
    def __init__(self, rows_by_date: dict[str, list[ReservoirRow]] | None = None):
        self._rows_by_date = rows_by_date or {}

    def latest_rows_for_date(self, trade_date: str):
        return list(self._rows_by_date.get(trade_date, []))


def _reservoir_row(ts_code: str, trade_date: str, isst: str, *, with_isst_field=True) -> ReservoirRow:
    fields_csv = "date,code,close,preclose,volume,tradestatus,isST" if with_isst_field \
        else "date,code,close,preclose,volume,tradestatus"
    return ReservoirRow(
        ts_code=ts_code,
        trade_date=trade_date,
        fetched_at=datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc),
        baostock_code="sh." + ts_code[:6] if ts_code.endswith(".SH") else "sz." + ts_code[:6],
        fields_csv=fields_csv,
        payload={"date": "2026-09-01", "isST": isst} if with_isst_field else {"date": "2026-09-01"},
        fetch_context="daily_adapter:20260901",
        request_start=trade_date,
        request_end=trade_date,
    )


def test_fetch_raw_happy_path_same_day_snapshot():
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider(
            [
                {"ts_code": "000010.SZ", "name": "*ST美丽"},
                {"ts_code": "600000.SH", "name": "浦发银行"},
            ],
            _aware(TODAY),
        )
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert isinstance(page, ProviderPage)
    assert len(page.rows) == 1
    assert page.rows[0]["ts_code"] == "000010.SZ"
    assert page.rows[0]["trade_date"] == TODAY_COMPACT
    assert page.rows[0]["st_origin"] == "derived_name_prefix"
    assert page.request_meta["membership_path"] == "name_snapshot"
    assert page.request_meta["st_origin"] == "derived_name_prefix"
    assert "BJ" in page.request_meta["coverage_exchanges"]


def test_fetch_raw_unknown_api_raises_keyerror():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], _aware(TODAY)))
    with pytest.raises(KeyError):
        src.fetch_raw("not-stock-st", trade_date=TODAY_COMPACT)


def test_fetch_raw_missing_trade_date_raises():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], _aware(TODAY)))
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST)


def test_fetch_raw_empty_snapshot_raises():
    src = StockSTDeriveSource(name_rows_provider=_fake_provider([], _aware(TODAY)))
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)


def _required(*codes: str):
    """构造一个 ``required_codes_provider`` 假实现: 忽略传入的 ``date``,
    恒返回给定的 required(D) 代码集合 (B1 修法, 2026-09-19 返修——覆盖判据的
    分母现在必须显式注入, 不能再让水库"有行就答")。"""

    frozen = frozenset(codes)
    return lambda _requested: frozen


def test_fetch_raw_unparseable_built_at_falls_through_to_reservoir_then_unanswerable():
    """v1: 不可解析的 built_at 直接 StockSTDeriveError。v2: snapshot_day_for 对
    不可解析输入返回 None (视同"不是快照当天"), 落到水库路径; required(D) 非空
    而水库一行都没有 -> 覆盖判据必然缺口 -> StockSTUnanswerableError
    (SourceCannotAnswerDateError 的子类), reason=reservoir_coverage_incomplete
    (B1 修法: 不再是 no_local_source_for_date —— 判据统一成"覆盖不全")。"""
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], None),
        reservoir_reader=_FakeReservoirReader({}),
        required_codes_provider=_required("000010.SZ"),
    )
    with pytest.raises(StockSTUnanswerableError) as excinfo:
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert excinfo.value.reason == "reservoir_coverage_incomplete"
    assert excinfo.value.detail == {"missing_count": 1, "missing_sample": ["000010.SZ"]}
    assert isinstance(excinfo.value, SourceCannotAnswerDateError)


def test_fetch_raw_non_snapshot_day_falls_back_to_reservoir_path():
    """C2: 非快照日 (built_at 是 3 天前) 但水库该日有含 isST 的行, 且覆盖了
    required(D) 的全部代码 -> 水库路径, name 结构性 None, st_origin
    provider_baostock_isst, coverage 不含 BJ。"""
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader(
            {TODAY_COMPACT: [_reservoir_row("000010.SZ", TODAY_COMPACT, "1")]}
        ),
        required_codes_provider=_required("000010.SZ"),
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert len(page.rows) == 1
    row = page.rows[0]
    assert row["name"] is None
    assert row["st_origin"] == "provider_baostock_isst"
    assert page.request_meta["membership_path"] == "baostock_reservoir"
    assert "BJ" not in page.request_meta["coverage_exchanges"]
    assert page.request_meta["reservoir_rows_for_date"] == 1


def test_fetch_raw_reservoir_coverage_incomplete_rejects_with_zero_accepted_rows():
    """B1-a (返修规格逐字对应): required(D) 比水库多一只停牌中的 ST 代码
    (000009.SZ, 上一分区是 ST 但当日没有 dump 行也没有补查行) -> 覆盖不全,
    typed 不可答, 不 accept 任何行 (page 根本不产生, fetch_raw 直接 raise)。"""
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader(
            {
                TODAY_COMPACT: [
                    _reservoir_row("000010.SZ", TODAY_COMPACT, "1"),
                    _reservoir_row("600000.SH", TODAY_COMPACT, "0"),
                ]
            }
        ),
        required_codes_provider=_required("000010.SZ", "600000.SH", "000009.SZ"),
    )
    with pytest.raises(StockSTUnanswerableError) as excinfo:
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert excinfo.value.reason == "reservoir_coverage_incomplete"
    assert excinfo.value.detail["missing_count"] == 1
    assert excinfo.value.detail["missing_sample"] == ["000009.SZ"]


def test_fetch_raw_reservoir_coverage_complete_when_suspended_code_has_a_row():
    """B1-b (返修规格逐字对应): 同上但水库里也有那只停牌股的行
    (isST=1, tradestatus=0) -> 覆盖判据满足, 可答, 派生成员含它 (不看
    tradestatus——停牌的 ST 仍是 ST)。"""
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader(
            {
                TODAY_COMPACT: [
                    _reservoir_row("000010.SZ", TODAY_COMPACT, "1"),
                    _reservoir_row("600000.SH", TODAY_COMPACT, "0"),
                    _reservoir_row("000009.SZ", TODAY_COMPACT, "1"),
                ]
            }
        ),
        required_codes_provider=_required("000010.SZ", "600000.SH", "000009.SZ"),
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    codes = {row["ts_code"] for row in page.rows}
    assert codes == {"000010.SZ", "000009.SZ"}  # isST==1 的两只; 600000.SH isST=0 不是成员


def test_fetch_raw_reservoir_isst_zero_is_not_a_member():
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader(
            {TODAY_COMPACT: [_reservoir_row("000010.SZ", TODAY_COMPACT, "0")]}
        ),
        required_codes_provider=_required("000010.SZ"),
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert page.rows == []
    assert page.request_meta["membership_path"] == "baostock_reservoir"


def test_fetch_raw_reservoir_rows_without_isst_field_is_unanswerable():
    """C2/B1: 水库该日有行但都不含 isST 字段 (契约升版前的旧行) -> 该代码不算
    "覆盖到", required(D) 里它仍然缺席 -> reason=reservoir_coverage_incomplete,
    不当作"0 个成员"悄悄放行。"""
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader(
            {TODAY_COMPACT: [_reservoir_row("000010.SZ", TODAY_COMPACT, "1", with_isst_field=False)]}
        ),
        required_codes_provider=_required("000010.SZ"),
    )
    with pytest.raises(StockSTUnanswerableError) as excinfo:
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert excinfo.value.reason == "reservoir_coverage_incomplete"


def test_fetch_raw_neither_path_available_is_unanswerable_source_cannot_answer():
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader({}),
        required_codes_provider=_required("600000.SH"),
    )
    with pytest.raises(SourceCannotAnswerDateError) as excinfo:
        src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert excinfo.value.reason == "reservoir_coverage_incomplete"
    assert excinfo.value.trade_date == TODAY_COMPACT
    assert excinfo.value.remedy  # 非空, 给人看的下一步动作


def test_fetch_raw_reservoir_coverage_vacuous_when_nothing_required():
    """隔离用例: required(D) 为空集合 (该日既无 canonical K 线也无更早的已
    accepted ST 分区——理论边界) -> 覆盖判据不作用, 水库路径答"0 个成员"而不是
    报不可答 (缺失集合为空, 不是"没查过")。"""
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "600000.SH", "name": "浦发银行"}], _aware(stale)),
        reservoir_reader=_FakeReservoirReader({}),
        required_codes_provider=_required(),
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert page.rows == []
    assert page.request_meta["membership_path"] == "baostock_reservoir"


def test_fetch_raw_allow_stale_snapshot_escape_hatch():
    stale = TODAY - timedelta(days=3)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], _aware(stale))
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT, allow_stale_snapshot=True)
    assert len(page.rows) == 1
    assert page.request_meta["membership_path"] == "name_snapshot"


def test_fetch_raw_respects_max_snapshot_age_days_constructor_param():
    stale = TODAY - timedelta(days=1)
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], _aware(stale)),
        max_snapshot_age_days=1,
    )
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT)
    assert len(page.rows) == 1


def test_fetch_raw_pagination_limit_offset():
    rows_in = [
        {"ts_code": f"00000{i}.SZ", "name": f"ST股{i}"} for i in range(5)
    ]
    src = StockSTDeriveSource(name_rows_provider=_fake_provider(rows_in, _aware(TODAY)))
    page = src.fetch_raw(API_STOCK_ST, trade_date=TODAY_COMPACT, limit=2, offset=1)
    assert len(page.rows) == 2


def test_fetch_raw_malformed_trade_date_raises():
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], _aware(TODAY))
    )
    with pytest.raises(StockSTDeriveError):
        src.fetch_raw(API_STOCK_ST, trade_date="2026-09-01")


# ---------------------------------------------------------------------------
# answerable_dates — C4
# ---------------------------------------------------------------------------


def test_answerable_dates_three_verdicts():
    """B1 修法 (2026-09-19 返修): 可答性现在按覆盖判据 (required(D) 是否被水库
    该日含 isST 字段的行覆盖), 不再是"有行即可"——本用例的 conn 因此还要备好
    ``canonical_nominal_ohlcv_daily`` (required(D) 的分母来源之一)。"""
    src = StockSTDeriveSource(
        name_rows_provider=_fake_provider([{"ts_code": "000010.SZ", "name": "*ST美丽"}], _aware(TODAY)),
        reservoir_reader=_FakeReservoirReader(
            {"20260831": [_reservoir_row("000010.SZ", "20260831", "1")]}
        ),
    )
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE raw_baostock_daily_k (ts_code VARCHAR, trade_date DATE, "
        "fetched_at TIMESTAMPTZ, baostock_code VARCHAR, fields_csv VARCHAR, "
        "payload_json VARCHAR, row_hash VARCHAR, fetch_context VARCHAR, "
        "request_start DATE, request_end DATE)"
    )
    conn.execute(
        "INSERT INTO raw_baostock_daily_k VALUES ('000010.SZ', '2026-08-31', "
        "'2026-09-01T18:00:00+00:00', 'sz.000010', "
        "'date,code,close,preclose,volume,tradestatus,isST', '{}', 'h', 'ctx', "
        "'2026-08-31', '2026-08-31')"
    )
    conn.execute("CREATE TABLE canonical_nominal_ohlcv_daily (ts_code VARCHAR, trade_date DATE)")
    conn.execute(
        "INSERT INTO canonical_nominal_ohlcv_daily VALUES "
        "('000010.SZ', '2026-08-31'), ('600000.SH', '2026-01-01')"
    )
    conn.execute("CREATE TABLE canonical_stock_st_daily (ts_code VARCHAR, trade_date DATE)")
    try:
        out = src.answerable_dates([TODAY_COMPACT, "20260831", "20260101"], conn=conn)
    finally:
        conn.close()
    assert out[TODAY_COMPACT] == DateAnswerability(True, "name_snapshot", None, None)
    assert out["20260831"].answerable is True
    assert out["20260831"].path == "baostock_reservoir"
    assert out["20260101"].answerable is False
    assert out["20260101"].reason == "reservoir_coverage_incomplete"
    assert out["20260101"].remedy
    assert out["20260101"].detail == {"missing_count": 1, "missing_sample": ["600000.SH"]}


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
            "('000010.SZ', '000010', '*ST美丽', 'SZ', '2026-09-01T10:05:00+00:00'), "
            "('920023.BJ', '920023', 'ST北交所样例', '北交所', '2026-09-01T10:05:00+00:00'), "
            "('600000.SH', '600000', '浦发银行', 'SH', '2026-09-01T10:05:00+00:00')"
        )
    finally:
        con.close()

    class _FakeManifest:
        def path_for(self, alias):
            assert alias == "tushare_raw"
            return db_path

    monkeypatch.setattr(
        "services.database_manifest.get_database_manifest", lambda: _FakeManifest()
    )

    name_rows, built_at = mod._default_name_rows_provider()
    codes = {r["ts_code"] for r in name_rows}
    assert codes == {"000010.SZ", "920023.BJ", "600000.SH"}  # 不按 market 过滤, 北交所在内
    assert built_at == datetime(2026, 9, 1, 10, 5, tzinfo=timezone.utc)


def test_stock_st_derive_source_name_attr_matches_alias():
    assert StockSTDeriveSource().name == ALIAS == "stock_st_derive"

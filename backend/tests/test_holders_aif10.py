"""holders_aif10 服务单测 (刀 B1 重写, 2026-09-26): change 解析 / 清洗 / 退出推导 /
K线范围过滤 / 按公告日观测模型 (差集 + merge_new_grains + 账本驱动的日更循环)。

fixture 用真实 aif10 RPT_F10_EH_FREEHOLDERS 字段形态 (mythos §12: 防字段方向反)。
按公告日的用例走真引擎 (``fetch_pages_strict``)、真 YAML 策略、真 accept 路径
(``land_then_accept_disclosure_partition``) —— 只假客户端 (``get_v1``), 不 fake
``aif10_scraper`` 模块本身 (spec_holders_pagination.md §7.5 约定)。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import pytest  # noqa: E402

from aif10_scraper import (  # noqa: E402
    AIF10ApiError,
    AIF10BlockedError,
    AIF10UnknownCodeError,
)
from services.data_sources.holders_top10_schema import (  # noqa: E402
    CANONICAL_ROW_FIELDS as HOLDERS_CANONICAL_ROW_FIELDS,
)
from services.holders_aif10 import (  # noqa: E402
    CANONICAL_TABLE,
    CARRY_FIELDS,
    DEFAULT_START_PERIOD,
    HoldersCarryFieldSchemaError,
    HoldersProviderProbeError,
    LocalIndex,
    LocalRow,
    _assert_carry_fields_in_canonical,
    _clean,
    _derive_exits,
    _derive_exits_against_canonical,
    _drop_vendor_excluded,
    _fetch_raw,
    _holder_key,
    _local_keys_elsewhere,
    _local_observation_index,
    _net_new_notice_since,
    _parse_change,
    _provider_newest_update_date,
    _rows_to_land,
    _share_class,
    _write,
    _write_with_outcome,
    build_rows,
    diff_notice_day,
    fetch_holders_notice_day,
    fetch_holders_top10_by_notice_date,
    formal_holders_watermark,
    reland_stock_in_day,
    recheck_notice_day,
    sync_holders_aif10,
    sync_holders_aif10_incremental,
    take_exit_derive_skips,
    UnknownHolderChangeStatusError,
)
from services.data_sources.vendor_scope import VendorScopeError  # noqa: E402


def _raw(secu, code, end_date, name, rank, hold_num, change, ratio=1.0, stype="A股",
         upd="2026-06-13", holder_code=None, is_holdorg=None):
    """真实形态 aif10 行.

    2026-09-07 补 IS_HOLDORG。它此前不在 fixture 里, 但真实 RPT_F10_EH_FREEHOLDERS
    响应**每行都有**(实测 2018-12-31 起 1,449,322 行零缺失), 而 canonical v3 用它解释
    holder_code 的 NULL。缺省值按实测的供应商行为推: 给了 holder_code 就是机构,
    没给就是个人 —— 实测两侧无例外 (机构 829,249 行 code 空 0 条; 个人 620,073 行
    code 空 620,073 条)。这不是为了让测试变绿而编的默认值, 是照抄供应商的真实边界。
    """
    row = {
        "SECUCODE": secu, "SECURITY_CODE": code, "SECURITY_NAME_ABBR": "测试股",
        "END_DATE": f"{end_date} 00:00:00", "HOLDER_NAME": name, "HOLDER_RANK": rank,
        "HOLD_NUM": hold_num, "HOLD_RATIO": ratio, "HOLD_NUM_CHANGE": change,
        "SHARES_TYPE": stype, "HOLDER_TYPE": "其它", "UPDATE_DATE": f"{upd} 00:00:00",
        "IS_HOLDORG": (1 if holder_code is not None else 0)
        if is_holdorg is None
        else is_holdorg,
    }
    if holder_code is not None:
        row["HOLDER_CODE"] = holder_code
    return row


def _b_share_row(**overrides):
    """真实形态 aif10 B股行 (spec_bshare_b2.md §1.1 P1 实测: SECURITY_TYPE_CODE=
    058001002, 沪 900910)。``_raw`` 不带这一列 (它的默认调用形态不需要类别字段),
    这里单独造一份不改 ``_raw`` 签名。"""
    row = _raw("900910.SH", "900910", "2026-09-17", "沪B股东", 1, 1000, "不变",
               upd="2026-09-17")
    row["SECURITY_TYPE_CODE"] = "058001002"
    row.update(overrides)
    return row


class _FakeStrictClient:
    """假客户端: 实现 ``get_v1`` (与 ``AIF10Client.get_v1`` 同签名/同返回契约),
    供 ``fetch_pages_strict`` 调用。返回顶层原始行 (未清洗), 按调用方给的
    ``page_size`` 自行切页。"""

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = 0
        self.page_sizes: list[int] = []

    def get_v1(self, report_name, *, page, page_size, sort_columns="", sort_types="",
               columns="ALL", secucode=None, extra_filters=None, filter_expr=None,
               extra_params=None):
        del report_name, sort_columns, sort_types, columns, secucode, extra_filters
        del filter_expr, extra_params
        self.calls += 1
        self.page_sizes.append(page_size)
        count = len(self._rows)
        if count == 0:
            return {"code": 9201, "message": "返回数据为空", "success": False,
                    "pages": 0, "data": [], "count": 0}
        pages = -(-count // page_size)
        start = (page - 1) * page_size
        chunk = self._rows[start:start + page_size]
        return {"code": 0, "message": "ok", "success": True,
                "pages": pages, "data": chunk, "count": count}


class _RaisingClient:
    """探针/取数异常注入用: get_v1 恒抛给定异常。"""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def get_v1(self, *_a, **_k):
        self.calls += 1
        raise self._exc


class _SequenceClient:
    """按调用序返回预置结果 (dict) 或抛出预置异常; 用于探针/多遍场景。"""

    def __init__(self, items):
        self._items = list(items)
        self.calls = 0

    def get_v1(self, *_a, **_k):
        self.calls += 1
        item = self._items[min(self.calls, len(self._items)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def _utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _cst(y, m, d, hh=12, mm=0):
    """返回一个 tz-aware UTC datetime, 使其上海日历日 (UTC+8, 无夏令时) 恰好是
    y-m-d —— 返修 blocking finding (B7b/B7d) 需要在 settled 的日期边界上精确
    落子, 用这个而不是手算 UTC 偏移, 才不会把边界算错。"""
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc) - timedelta(hours=8)


# ── _parse_change: HOLD_NUM_CHANGE 多态 ──────────────────────────────
def test_parse_change_polymorphic():
    assert _parse_change("新进") == ("新进", None)
    assert _parse_change("不变") == ("不变", 0)
    assert _parse_change(5281895) == ("增持", 5281895)      # 正数 = 增持
    assert _parse_change(-697100) == ("减持", -697100)      # 负数 = 减持
    assert _parse_change("5281895") == ("增持", 5281895)    # 字符串数字
    assert _parse_change(None) == ("未知", None)


@pytest.mark.parametrize("bad", ["未披露", "冻结", "部分转让", "维持"])
def test_parse_change_raises_on_unknown_status(bad):
    """闭合取值集 fail-closed: 供应商哪天多一种取值, 抛错而不是原样存进 canonical."""
    with pytest.raises(UnknownHolderChangeStatusError):
        _parse_change(bad)


def test_share_class():
    assert _share_class("A股") == "A"
    assert _share_class("H股") == "H"
    assert _share_class("B股") == "B"
    assert _share_class("") == "_"


# ── _clean: 字段映射 + K线范围过滤 ───────────────────────────────────
def test_clean_maps_fields_and_change():
    rows = [
        _raw("600388.SH", "600388", "2026-06-08", "紫金矿业", 1, 267764576, "不变", 21.08),
        _raw("600388.SH", "600388", "2026-06-08", "龙岩国资", 2, 117334400, 5281895, 9.23),
        _raw("600388.SH", "600388", "2026-06-08", "社保基金", 3, 1000000, "新进", 0.8),
    ]
    out = _clean(rows, start_period=DEFAULT_START_PERIOD)
    assert len(out) == 3
    by_name = {r["holder_name"]: r for r in out}
    assert by_name["紫金矿业"]["change_status"] == "不变"
    assert by_name["龙岩国资"]["change_status"] == "增持"
    assert by_name["龙岩国资"]["change_shares_approx"] == 5281895
    assert by_name["社保基金"]["change_status"] == "新进"
    assert by_name["紫金矿业"]["share_class"] == "A"
    assert by_name["紫金矿业"]["holder_set"] == "free"
    assert by_name["紫金矿业"]["source"] == "miaoxiang"
    assert by_name["紫金矿业"]["report_date"] == "20260608"
    assert by_name["紫金矿业"]["availability_source"] == "page_update_date"
    assert by_name["紫金矿业"]["page_update_date"] == "20260613"


def test_clean_filters_before_kline_start():
    """report_date < start_period (K线对齐) 的行被丢弃."""
    rows = [
        _raw("600388.SH", "600388", "2010-12-31", "老股东", 1, 1000, "不变"),  # K线前
        _raw("600388.SH", "600388", "2020-12-31", "新股东", 1, 2000, "不变"),  # K线内
    ]
    out = _clean(rows, start_period="20181231")
    assert len(out) == 1
    assert out[0]["report_date"] == "20201231"


# ── _derive_exits: period-diff (按股全史内存路径, 未改) ─────────────────
def test_derive_exits_period_diff():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "A机构", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-03-31", "B机构", 2, 90, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "A机构", 1, 100, "不变"),  # A 留, B 退出
    ], start_period=DEFAULT_START_PERIOD)
    exits = _derive_exits(base)
    assert len(exits) == 1
    e = exits[0]
    assert e["holder_name"] == "B机构"
    assert e["report_date"] == "20260608"
    assert e["is_exit_row"] is True
    assert e["change_status"] == "退出"
    assert e["change_shares_approx"] == -90


def test_derive_exits_no_exit_when_stable():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "A机构", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "A机构", 1, 100, "不变"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


def test_derive_exits_identity_same_code_different_name_is_not_exit():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "国泰君安", 1, 100, "不变",
             holder_code="ORG001"),
        _raw("600388.SH", "600388", "2026-06-08", "国泰海通", 1, 100, "不变",
             holder_code="ORG001"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


def test_derive_exits_identity_same_name_different_code_is_exit():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "张三", 1, 100, "不变",
             holder_code="IND001"),
        _raw("600388.SH", "600388", "2026-06-08", "张三", 1, 100, "不变",
             holder_code="IND002"),
    ], start_period=DEFAULT_START_PERIOD)
    exits = _derive_exits(base)
    assert len(exits) == 1
    assert exits[0]["holder_name"] == "张三"
    assert exits[0]["is_exit_row"] is True


def test_derive_exits_identity_falls_back_to_name_without_code():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "李四", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "李四", 1, 100, "不变"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


# ── formal_holders_watermark + 展示计数 ──────────────────────────────
def _wm_fixture():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN,
            holder_code VARCHAR, is_holder_org BOOLEAN,
            hold_ratio_float DOUBLE, shares_approx BIGINT
        )
        """
    )
    return con


def test_formal_watermark_prefers_canonical_notice_frontier():
    con = _wm_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row) VALUES "
        "('600519','20260630','20260722','机构甲',FALSE),"
        "('600519','20260331','20260425','机构甲',FALSE)"
    )
    wm, src = formal_holders_watermark(con)
    assert wm == "20260722"
    assert src == "canonical_notice_frontier"


def test_formal_watermark_empty_when_no_canonical():
    con = _wm_fixture()
    wm, src = formal_holders_watermark(con)
    assert wm is None
    assert src == "empty"


def test_net_new_notice_since_splits_amplification_from_new():
    con = _wm_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row) VALUES "
        "('600519','20260630','20260722','机构甲',FALSE),"
        "('600519','20260630','20260721','机构乙',FALSE),"
        "('600519','20260331','20260425','机构甲',FALSE)"
    )
    net_rows, parts = _net_new_notice_since(con, "20260717")
    assert net_rows == 2
    assert parts == 2


# ── fetch_holders_top10_by_notice_date / fetch_holders_notice_day ──────


def test_fetch_holders_top10_by_notice_date_maps_provider_shape(monkeypatch):
    rows = [
        _raw("600388.SH", "600388", "2026-03-31", "A机构", 1, 100, "不变", upd="2026-07-17"),
    ]
    fake = _FakeStrictClient(rows)
    monkeypatch.setattr("services.holders_aif10._daily_client", lambda: fake)

    out = fetch_holders_top10_by_notice_date("20260717")
    assert len(out) == 1
    assert out[0]["stock_code"] == "600388"
    assert out[0]["notice_date"] == "20260717"
    assert out[0]["is_exit_row"] is False


def test_fetch_holders_notice_day_requires_client_kwarg():
    """N9: client 必填, 调用方不许悄悄退回某个默认单例."""
    with pytest.raises(TypeError):
        fetch_holders_notice_day("20260717")  # type: ignore[call-arg]


def test_fetch_holders_notice_day_rejects_bad_date():
    with pytest.raises(ValueError):
        fetch_holders_notice_day("not-a-date", client=_FakeStrictClient([]))


# ── B1_notice_day_tie_drift_raises fixture (spec §7.5 B1; 返修补测) ──────
#
# 复刻 tests/test_aif10_pagination_strict.py::TieDriftClient 的并列漂移形态
# (probe §3.3 的真实供应商行为: 排序键并列时, 相邻两次翻页之间供应商内部顺序
# 会漂移), 补一个 UPDATE_DATE 字段让行能穿过 _clean 的 notice_date 过滤 ——
# B1 允许改的文件清单 (spec §4.0) 不含 test_aif10_pagination_strict.py,
# 不能直接 import 复用刀 A 那份 fixture, 只能在本文件里复刻等价实现。
_HOLDER_TIE_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6"]
_HOLDER_TIE_DRIFTED_ORDER = ["S1", "S2", "S4", "S3", "S5", "S6"]
_HOLDER_TIE_ROWS = {
    key: {
        "SECURITY_CODE": f"{i:06d}",
        "END_DATE": "2026-06-30",
        "UPDATE_DATE": "2026-07-01",
        "HOLDER_RANK": 1,
        "HOLDER_NAME": f"holder-{key}",
        "IS_HOLDORG": 0,
    }
    for i, key in enumerate(_HOLDER_TIE_ORDER, start=1)
}


class _HoldersTieDriftClient:
    """6 行全部 (END_DATE=2026-06-30, HOLDER_RANK=1) 并列。请求的
    ``sort_columns`` 不含 ``SECURITY_CODE`` 时, 供应商内部顺序在两次请求间
    漂移 (page2 用 ``_HOLDER_TIE_DRIFTED_ORDER`` 切片) —— S3 相邻页重复、
    S4 永远不出现, ``count``/``pages`` 全程稳定 (6/2)。含 ``SECURITY_CODE``
    时两次都按稳定顺序切片, 漂移消失。"""

    def __init__(self):
        self.calls: list[dict] = []

    def get_v1(self, report_name, *, page, page_size, sort_columns, sort_types,
               **kwargs):
        self.calls.append({"page": page, "sort_columns": sort_columns})
        drifts = "SECURITY_CODE" not in sort_columns
        if page == 1:
            keys = _HOLDER_TIE_ORDER[0:3]
        elif page == 2:
            keys = _HOLDER_TIE_DRIFTED_ORDER[3:6] if drifts else _HOLDER_TIE_ORDER[3:6]
        else:
            raise AssertionError("_HoldersTieDriftClient only serves 2 pages")
        return {
            "code": 0,
            "message": "ok",
            "success": True,
            "pages": 2,
            "count": 6,
            "data": [_HOLDER_TIE_ROWS[k] for k in keys],
        }


def test_b1_notice_day_tie_drift_raises(monkeypatch):
    """spec §7.5 B1_notice_day_tie_drift_raises (返修补测, blocking):
    生产策略 (``_POLICY``, 含 SECURITY_CODE 等四个 identity 列) 下不误报、
    返回 6 行完整数据; 把 ``_POLICY`` monkeypatch 成不含 SECURITY_CODE 的旧
    排序后, ``PaginationIntegrityError.reason == "identical_duplicates"``
    必须从 ``fetch_holders_notice_day`` 冒出——这是 B1 删除
    ``_dedupe_notice_rows_by_grain`` 之后唯一守着"万一有人恢复任何形式的
    批内折叠去重会被抓到"的用例, 变异 = 恢复批内折叠去重 -> 第二半绿转红。
    """
    from dataclasses import replace

    import services.holders_aif10 as mod
    from aif10_scraper.pagination import PaginationIntegrityError

    monkeypatch.setattr(mod, "PAGE_SIZE", 3)

    # (a) 生产策略: 不抛, 6 行完整数据。
    client = _HoldersTieDriftClient()
    result = fetch_holders_notice_day("20260701", client=client)
    assert len(result.rows) == 6
    assert {r["stock_code"] for r in result.rows} == {f"{i:06d}" for i in range(1, 7)}
    assert client.calls  # 代理确实被经过

    # (b) 旧排序 (无 SECURITY_CODE, identity_columns 随之清空以满足
    #     identity_columns ⊆ sort_columns.split(",") 的构造校验): 并列漂移
    #     -> identical_duplicates 从 fetch_holders_notice_day 冒出。
    legacy_policy = replace(
        mod._POLICY,
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
        identity_columns=(),
    )
    monkeypatch.setattr(mod, "_POLICY", legacy_policy)
    drift_client = _HoldersTieDriftClient()
    with pytest.raises(PaginationIntegrityError) as excinfo:
        fetch_holders_notice_day("20260701", client=drift_client)
    assert excinfo.value.reason == "identical_duplicates"


def test_b2_integrity_judged_before_exclusion_and_clean():
    """B2: 2 页各 500、count 1000; 600 行 B 股、300 行 K线范围外; 不抛, 返回 100 行.

    完整性判定在 _drop_vendor_excluded 与 _clean 之前: 供应商总数在剔除/过滤前
    就已经精确对上 (1000 == 1000), 剔除/过滤是判完之后的下一步。
    """
    rows = []
    for i in range(100):
        rows.append(_raw(f"00{i:04d}.SZ", f"00{i:04d}", "2026-06-30", f"H{i}", 1, 100,
                          "不变", upd="2026-07-01"))
    for i in range(600):
        row = _raw(f"90{i:04d}.SH", f"90{i:04d}", "2026-06-30", f"B{i}", 1, 100, "不变",
                    upd="2026-07-01")
        row["SECURITY_TYPE_CODE"] = "058001002"
        rows.append(row)
    for i in range(300):
        rows.append(_raw(f"60{i:04d}.SH", f"60{i:04d}", "2015-12-31", f"O{i}", 1, 100,
                          "不变", upd="2026-07-01"))
    assert len(rows) == 1000

    fake = _FakeStrictClient(rows)
    result = fetch_holders_notice_day("20260701", client=fake)
    assert len(result.rows) == 100
    assert result.ledger.count_declared == 1000
    assert result.ledger.raw_rows == 1000
    assert fake.calls == 2  # 2 页 (500/页)


def test_fetch_holders_notice_day_raises_on_short_page_mid_fetch():
    """引擎完整性判据靠真实策略执法 (容差 0): 第 2 页少一行 -> raw_rows_ne_count
    (中途短页) 由引擎抛出, 不静默丢行。"""
    from aif10_scraper.pagination import PaginationIntegrityError

    rows = [
        _raw(f"00{i:04d}.SZ", f"00{i:04d}", "2026-06-30", f"H{i}", 1, 100, "不变",
             upd="2026-07-05")
        for i in range(600)
    ]
    broken = list(rows)
    del broken[550]  # count 仍声明 600, 但只有 599 行可取 -> 末页短一行

    class _ShortLastPageClient(_FakeStrictClient):
        def get_v1(self, *a, **k):
            resp = super().get_v1(*a, **k)
            if resp.get("data"):
                resp = dict(resp, count=600, pages=2)  # 声明值与真实短页矛盾
            return resp

    fake = _ShortLastPageClient(broken)
    with pytest.raises(PaginationIntegrityError):
        fetch_holders_notice_day("20260705", client=fake)


# ── S1-A6 (vendor_scope B股排除, 未改) ───────────────────────────────
def test_fetch_holders_top10_by_notice_date_excludes_b_share(monkeypatch):
    a_row = _raw("600388.SH", "600388", "2026-09-17", "A机构", 1, 100, "不变",
                 upd="2026-09-17")
    b_row = _b_share_row()
    fake = _FakeStrictClient([a_row, b_row])
    monkeypatch.setattr("services.holders_aif10._daily_client", lambda: fake)

    rows = fetch_holders_top10_by_notice_date("20260917")
    assert {r["stock_code"] for r in rows} == {"600388"}


def test_fetch_holders_top10_by_notice_date_all_b_share_day_lands_empty(monkeypatch):
    fake = _FakeStrictClient([_b_share_row()])
    monkeypatch.setattr("services.holders_aif10._daily_client", lambda: fake)

    rows = fetch_holders_top10_by_notice_date("20260917")
    assert rows == []


def test_build_rows_excludes_pure_b_share_symbol(monkeypatch):
    row = _b_share_row()

    def fetch_all_pages(_report, *, secucode, page_size=500, max_pages=0, client=None):
        del _report, page_size, max_pages, client
        return [row] if str(secucode).split(".")[0] == "900910" else []

    fake_mod = __import__("types").ModuleType("aif10_scraper")
    fake_mod.fetch_all_pages = fetch_all_pages
    fake_mod.default_client = object()
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)

    out = build_rows(object(), "900910")
    assert out == []


def test_drop_vendor_excluded_does_not_swallow_vendor_scope_error(monkeypatch):
    def _raise(*_a, **_k):
        raise VendorScopeError("no disposition registered for 'aif10.holders_top10'")

    monkeypatch.setattr("services.data_sources.vendor_scope.vendor_exclusions", _raise)
    with pytest.raises(VendorScopeError):
        _drop_vendor_excluded([
            _raw("600388.SH", "600388", "2026-09-17", "A机构", 1, 100, "不变")
        ])


# ── diff_notice_day / _rows_to_land: 纯函数差集 (B3/B4/B5*/B25) ───────────


def _local_row(row_seq=1, holder_name="甲", holder_code="C1", is_holder_org=True,
               hold_ratio_float=10.0, shares_approx=1000, change_status="不变",
               hold_change_num=0.0, holder_type=None, share_class="A"):
    return LocalRow(row_seq=row_seq, holder_name=holder_name, holder_code=holder_code,
                     is_holder_org=is_holder_org, hold_ratio_float=hold_ratio_float,
                     shares_approx=shares_approx, change_status=change_status,
                     hold_change_num=hold_change_num, holder_type=holder_type,
                     share_class=share_class)


def _prov(stock="A", report="P", rank=3, name="甲", **kw):
    base = dict(stock_code=stock, report_date=report, holder_rank=rank, holder_name=name,
                holder_set="free", hold_ratio_float=10.0, shares_approx=1000,
                change_status="不变", hold_change_num=0.0, holder_type=None, share_class="A",
                holder_code="C1", is_holder_org=True, notice_date="20260722")
    base.update(kw)
    return base


def test_b3_missing_partial_rank_inserted_only():
    local = LocalIndex(
        by_key={(f"A", "P", i, f"H{i}"): (_local_row(row_seq=i, holder_name=f"H{i}"),)
                for i in range(1, 10)},
        group_max_seq={("A", "P", "free", i, False): i for i in range(1, 10)},
    )
    provider_rows = [_prov(rank=i, name=f"H{i}") for i in range(1, 11)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.missing_keys == {("A", "P", 10, "H10")}
    assert diff.partial_stocks == {"A"}
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert len(to_land) == 1
    assert to_land[0]["holder_rank"] == 10
    assert to_land[0]["row_seq"] == 1


def test_b4_missing_stock_touches_only_it():
    local = LocalIndex(
        by_key={("A", "P", 1, "H1"): (_local_row(holder_name="H1"),)},
        group_max_seq={("A", "P", "free", 1, False): 1},
    )
    provider_rows = [
        _prov(stock="A", rank=1, name="H1"),
        _prov(stock="B", rank=1, name="X1"),
    ]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.missing_keys == {("B", "P", 1, "X1")}
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert [r["stock_code"] for r in to_land] == ["B"]


def test_b5_surplus_reported_not_written():
    local = LocalIndex(
        by_key={
            **{("A", "P", i, f"HA{i}"): (_local_row(row_seq=i, holder_name=f"HA{i}"),)
               for i in range(1, 11)},
            **{("C", "P", i, f"HC{i}"): (_local_row(row_seq=i, holder_name=f"HC{i}"),)
               for i in range(1, 11)},
        },
        group_max_seq={},
    )
    provider_rows = [_prov(stock="A", rank=i, name=f"HA{i}") for i in range(1, 11)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.missing_keys == frozenset()
    assert len(diff.surplus_keys) == 10
    assert all(k[0] == "C" for k in diff.surplus_keys)
    assert diff.moved_keys == frozenset()
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert to_land == []


def test_b5b_moved_split_from_surplus():
    local = LocalIndex(
        by_key={("C", "P", i, f"HC{i}"): (_local_row(row_seq=i, holder_name=f"HC{i}"),)
                for i in range(1, 11)},
        group_max_seq={},
    )
    surplus_keys_elsewhere = frozenset(
        ("C", "P", i, f"HC{i}") for i in range(1, 11)
    )
    diff = diff_notice_day([], local, keys_elsewhere=surplus_keys_elsewhere)
    assert diff.surplus_keys == surplus_keys_elsewhere
    assert diff.moved_keys == diff.surplus_keys


def test_b5c_dup_reported_not_touched_by_daily():
    local = LocalIndex(
        by_key={
            ("A", "P", 7, "甲"): (_local_row(row_seq=1, holder_name="甲"),
                                  _local_row(row_seq=2, holder_name="甲")),
            **{("A", "P", i, f"H{i}"): (_local_row(row_seq=i, holder_name=f"H{i}"),)
               for i in range(1, 10) if i != 7},
        },
        group_max_seq={},
    )
    provider_rows = [_prov(rank=i, name=f"H{i}" if i != 7 else "甲") for i in range(1, 10)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.dup_groups == 1
    assert diff.dup_rows == 1
    assert diff.missing_keys == frozenset()
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert to_land == []  # dup 由 dedup_local(B2) 处理, 日更只报不动


def test_b5d_revised_row_landed_but_canonical_kept():
    local = LocalIndex(
        by_key={("A", "P", 3, "甲"): (_local_row(row_seq=5, holder_name="甲",
                                                  change_status="新进"),)},
        group_max_seq={},
    )
    provider_rows = [_prov(rank=3, name="甲", change_status="增持", hold_change_num=1000.0)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.revised_keys == {("A", "P", 3, "甲")}
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert len(to_land) == 1
    assert to_land[0]["row_seq"] == 5  # 沿用本地已有 row_seq -> accept 判定"已存在"而持有


def test_b5d_revised_row_landed_but_canonical_kept_end_to_end():
    """返修 blocking finding (spec §7.5 B5d): 上面那条用例只测了纯函数
    (diff_notice_day/_rows_to_land), 规格写明的端到端断言(写后 canonical 不动/
    landing 多一个批次/outcome held_rows/账本 revised_rows)一条都没落地 ——
    变异「_accept_merge_new_grains 的 held 分支加一句 UPDATE change_status,
    把修订施用进 canonical」在全部既有用例上都存活(施工返修记录: 155 passed)。
    这里走真 recheck_notice_day(真引擎/真 YAML/真 accept), 不 monkeypatch
    被测函数。"""
    con = duckdb.connect(":memory:")
    r0 = recheck_notice_day(
        con, "20260701",
        client=_FakeStrictClient([
            _raw("600388.SH", "600388", "2026-06-30", "甲", 3, 100, "新进",
                 upd="2026-07-01"),
        ]),
        run_kind="daily", write=True, now_fn=lambda: _utc(2026, 7, 1, 20, 0),
    )
    assert r0["outcome"] == "complete"
    assert r0["rows_inserted"] == 1
    b0 = r0["batch_ids"][0]

    row_sql = (
        "SELECT change_status, ingest_batch_id FROM canonical_top10_float_holders_period "
        "WHERE stock_code='600388' AND report_date='20260630' AND holder_rank=3 "
        "AND notice_date='20260701' AND is_exit_row=FALSE"
    )
    assert con.execute(row_sql).fetchone() == ("新进", b0)

    # 供应商今天说这行「增持」了(同一 GRAIN, 内容变了) -> revised, 不是 missing。
    r1 = recheck_notice_day(
        con, "20260701",
        client=_FakeStrictClient([
            _raw("600388.SH", "600388", "2026-06-30", "甲", 3, 1000, 1000,
                 upd="2026-07-01"),
        ]),
        run_kind="daily", write=True, now_fn=lambda: _utc(2026, 7, 2, 8, 0),
    )
    assert r1["outcome"] == "complete"
    assert r1["revised_rows"] == 1
    assert r1["rows_inserted"] == 0
    assert r1["held_rows"] == 1
    b1 = r1["batch_ids"][0]
    assert b1 != b0

    # canonical 一字未动: 仍是「新进」, ingest_batch_id 仍是 b0 (红线 4: 派生只能
    # 向下不能反向喂数据; 也是红线 1 as-of 承诺的一部分, B31)。
    assert con.execute(row_sql).fetchone() == ("新进", b0)

    # 修订行确实落了 landing(证据), 不是被吞掉。
    import json

    payloads = [
        json.loads(r[0])
        for r in con.execute(
            "SELECT payload_json FROM landing_miaoxiang_holders_top10 WHERE batch_id=?",
            [b1],
        ).fetchall()
    ]
    assert payloads
    assert any(p.get("change_status") == "增持" for p in payloads)

    from services.holders_notice_ledger import settled_notice_days  # noqa: F401 (sanity import)
    ledger_row = con.execute(
        "SELECT revised_rows, held_rows FROM holders_notice_fetch_ledger "
        "WHERE notice_date='20260701' ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    assert ledger_row == (1, 1)


def test_b5e_revised_ignores_identity_on_legacy_rows():
    """本地 is_holder_org=None (v2 遗留行) 时不比身份列, 只比六个观测列."""
    local = LocalIndex(
        by_key={("A", "P", 3, "甲"): (_local_row(row_seq=1, holder_name="甲",
                                                  holder_code=None, is_holder_org=None),)},
        group_max_seq={},
    )
    provider_rows = [_prov(rank=3, name="甲", holder_code="NEWCODE", is_holder_org=True)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.revised_keys == frozenset()


def test_b5f_revised_identity_on_v4_rows():
    """本地 is_holder_org=True 时身份列(holder_code)也参与比较."""
    local = LocalIndex(
        by_key={("A", "P", 3, "甲"): (_local_row(row_seq=1, holder_name="甲",
                                                  holder_code="X", is_holder_org=True),)},
        group_max_seq={},
    )
    provider_rows = [_prov(rank=3, name="甲", holder_code="Y", is_holder_org=True)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.revised_keys == {("A", "P", 3, "甲")}


def test_b5g_revised_float_tolerance():
    local = LocalIndex(
        by_key={("A", "P", 3, "甲"): (_local_row(row_seq=1, holder_name="甲",
                                                  hold_ratio_float=12.34),)},
        group_max_seq={},
    )
    provider_rows = [_prov(rank=3, name="甲", hold_ratio_float=12.3400000001)]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.revised_keys == frozenset()


def test_b25_row_seq_continuation_no_collision():
    local = LocalIndex(
        by_key={("A", "P", 7, "甲"): (_local_row(row_seq=1, holder_name="甲"),)},
        group_max_seq={("A", "P", "free", 7, False): 1},
    )
    provider_rows = [
        _prov(rank=7, name="甲"),
        _prov(rank=7, name="乙"),
    ]
    diff = diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())
    assert diff.missing_keys == {("A", "P", 7, "乙")}
    to_land = _rows_to_land(diff, {_holder_key(r): r for r in provider_rows}, local)
    assert len(to_land) == 1
    assert to_land[0]["holder_name"] == "乙"
    assert to_land[0]["row_seq"] == 2  # 续号, 不与「甲」的 1 撞


def test_diff_notice_day_raises_on_clean_key_collision():
    """V14: 清洗后键不唯一 (供应商同 rank/name 但引擎已判定整行不同, 理论上不该
    发生但要 fail-closed) -> RuntimeError, 归 clean_key_collision."""
    local = LocalIndex(by_key={}, group_max_seq={})
    provider_rows = [_prov(rank=1, name="甲"), _prov(rank=1, name="甲")]
    with pytest.raises(RuntimeError, match="clean_key_collision"):
        diff_notice_day(provider_rows, local, keys_elsewhere=frozenset())


def test_local_observation_index_and_keys_elsewhere_real_db():
    """真实 DB 读取 (不 monkeypatch 被测函数): _local_observation_index 只取
    给定 notice_date 的非退出行; _local_keys_elsewhere 找出同键在别的
    notice_date 下是否存在 (moved 判定的前置查询)。"""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_set VARCHAR, holder_rank INTEGER, row_seq INTEGER,
            holder_name VARCHAR, holder_code VARCHAR, is_holder_org BOOLEAN,
            hold_ratio_float DOUBLE, shares_approx BIGINT, change_status VARCHAR,
            hold_change_num DOUBLE, holder_type VARCHAR, share_class VARCHAR,
            is_exit_row BOOLEAN
        )
        """
    )
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_set, holder_rank, row_seq, "
        " holder_name, is_exit_row) VALUES "
        "('600388','20260630','20260722','free',1,1,'甲',FALSE),"
        "('600388','20260630','20260801','free',1,1,'甲',FALSE),"
        "('600388','20260630','20260722','free',2,1,'乙',FALSE)"
    )
    local_0722 = _local_observation_index(con, "20260722")
    assert set(local_0722.by_key.keys()) == {
        ("600388", "20260630", 1, "甲"), ("600388", "20260630", 2, "乙"),
    }
    elsewhere = _local_keys_elsewhere(con, "20260722", local_0722.by_key.keys())
    assert elsewhere == {("600388", "20260630", 1, "甲")}  # 「甲」在 0801 也有, 「乙」没有
    con.close()


# ── 完整写入路径 (真实 accept, merge_new_grains) ─────────────────────────


def _acquire_evidence(**overrides):
    base = {"acquire_path": "by_notice_date", "run_kind": "daily",
            "observation_kind": "delta_vs_canonical"}
    base.update(overrides)
    return base


def _canonical_row(stock="600388", report="20260630", notice="20260722", rank=1,
                    row_seq=1, name="甲", code="C1", is_org=True, ratio=10.0,
                    shares=1000, change_status="不变", change_num=0.0, is_exit=False,
                    when="2026-07-22T00:00:00+00:00"):
    return {
        "stock_code": stock, "report_date": report, "notice_date": notice,
        "holder_set": "free", "holder_rank": rank, "row_seq": row_seq,
        "holder_name": name, "holder_name_norm": name, "holder_code": code,
        "is_holder_org": is_org, "share_class": "A", "is_secondary_class": False,
        "is_exit_row": is_exit, "shares_text": None, "shares_approx": shares,
        "shares_precision": None, "hold_amount": float(shares),
        "hold_ratio_float": ratio, "hold_ratio_total": None, "hold_ratio": ratio,
        "hold_market_cap": None, "holder_type": None, "share_nature": None,
        "change_status": change_status, "change_shares_text": None,
        "change_shares_approx": None, "hold_change": change_status,
        "hold_change_num": change_num, "effective_date": None,
        "page_update_date": notice, "availability_source": "page_update_date",
        "source": "miaoxiang", "source_tier": 1, "raw_hash": None,
        "fetched_at": when, "created_at": when,
    }


def test_b9_write_records_acquire_evidence():
    con = duckdb.connect(":memory:")
    rows = [_canonical_row()]
    evidence = _acquire_evidence(provider_count=1, pages=1, raw_rows=1, unique_rows=1,
                                  passes=1, rows_new=1, rows_revised=0)
    _write_with_outcome(con, rows, delete_scope="merge_new_grains",
                        derive_exits_from_canonical=False, acquire_evidence=evidence)
    request_json = con.execute(
        "SELECT request_json FROM ingest_batch LIMIT 1"
    ).fetchone()[0]
    import json
    request = json.loads(request_json)
    assert request["acquire_path"] == "by_notice_date"
    assert request["run_kind"] == "daily"
    assert request["observation_kind"] == "delta_vs_canonical"
    assert request["rows_new"] == 1
    assert request["rows_revised"] == 0
    assert request["provider_count"] == 1


def test_b10_evidence_keys_absent_from_hash_inputs():
    from services.data_sources.holders_top10_acceptance import _canonical_content_hash

    row = _canonical_row()
    row1 = dict(row, available_at=_utc(2026, 7, 22), ingest_batch_id="b1",
                source_row_hash="h1", contract_version="5", config_hash="cfg",
                built_at=_utc(2026, 7, 22))
    row2 = dict(row1, ingest_batch_id="b2", source_row_hash="h2")
    assert row1["source_row_hash"] != row2["source_row_hash"]
    assert _canonical_content_hash([row1]) == _canonical_content_hash([row2])


def test_b11_request_extra_cannot_override_fixed_keys():
    from services.data_sources.disclosure_dual_write import (
        DisclosureDualWriteError,
        write_holders_top10_formal_then_mirror,
    )

    con = duckdb.connect(":memory:")
    with pytest.raises(DisclosureDualWriteError):
        write_holders_top10_formal_then_mirror(
            con, [_canonical_row()], delete_scope="merge_new_grains",
            request_extra={"api": "X"},
        )


def test_b25b_accept_rejects_row_seq_collision():
    """accept 拒批变成 DisclosureDualWriteError (dual_write 层 _require_accepted
    对任何非 ACCEPTED 状态一律抛出); 底层 ingest_batch 的 rejection_code 仍是
    ROW_SEQ_COLLISION, canonical 不变。"""
    from services.data_sources.disclosure_dual_write import DisclosureDualWriteError

    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [_canonical_row(name="甲")], delete_scope="merge_new_grains",
                        derive_exits_from_canonical=False, acquire_evidence=_acquire_evidence())
    # 同 GRAIN (row_seq=1) 但 holder_name 不同 -> 写方续号出错
    with pytest.raises(DisclosureDualWriteError):
        _write_with_outcome(
            con, [_canonical_row(name="乙")], delete_scope="merge_new_grains",
            derive_exits_from_canonical=False, acquire_evidence=_acquire_evidence(),
        )
    rejection_code = con.execute(
        "SELECT rejection_code FROM ingest_batch WHERE status = 'REJECTED'"
    ).fetchone()[0]
    assert rejection_code == "ROW_SEQ_COLLISION"
    names = {r[0] for r in con.execute(
        "SELECT holder_name FROM canonical_top10_float_holders_period").fetchall()}
    assert names == {"甲"}


def test_b26_late_row_recomputes_exit_rows_of_its_group_only():
    con = duckdb.connect(":memory:")
    # S 的 P_prev {a,b,c} 于 D0
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=1, row_seq=1, name="a", code="Ca"),
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=2, row_seq=1, name="b", code="Cb"),
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=3, row_seq=1, name="c", code="Cc"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    # S 的 P {a,b} 于 D + 退出行 c@P (D 派生, 之前的一次日更算出来的)
    out_p = _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=1, row_seq=1, name="a", code="Ca"),
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=2, row_seq=1, name="b", code="Cb"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("S", "20260201")}),
       acquire_evidence=_acquire_evidence())
    assert out_p.exit_rows_replaced == 0  # 首次没有旧退出行可替换
    exit_rows_first = con.execute(
        "SELECT holder_name FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND is_exit_row"
    ).fetchall()
    assert [r[0] for r in exit_rows_first] == ["c"]  # a、b 在榜, c 上一期在榜本期不在

    ab_before = con.execute(
        "SELECT ingest_batch_id FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND holder_name IN ('a','b') "
        "ORDER BY holder_name"
    ).fetchall()

    # T 在 D 有自己的退出行 (不该被这次写动)
    _write_with_outcome(con, [
        _canonical_row(stock="T", report="20260301", notice="20260102", rank=1, row_seq=1, name="x", code="Cx"),
        _canonical_row(stock="T", report="20260301", notice="20260102", rank=2, row_seq=1, name="y", code="Cy"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    out_t = _write_with_outcome(con, [
        _canonical_row(stock="T", report="20260401", notice="20260202", rank=1, row_seq=1, name="x", code="Cx"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("T", "20260401")}),
       acquire_evidence=_acquire_evidence())
    t_exit_batch_before = con.execute(
        "SELECT ingest_batch_id FROM canonical_top10_float_holders_period "
        "WHERE stock_code='T' AND report_date='20260401' AND is_exit_row"
    ).fetchall()

    # 迟到行 c@P 到达 (只这一行进批次, touched={(S,P)})
    out_late = _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=3, row_seq=1, name="c", code="Cc"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("S", "20260201")}),
       acquire_evidence=_acquire_evidence())

    assert out_late.exit_rows_replaced == 1  # 旧退出行 c@P 被删
    exit_rows_after = con.execute(
        "SELECT holder_name FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND is_exit_row"
    ).fetchall()
    assert exit_rows_after == []  # c 回来了, 不再是"上一期在榜本期不在"

    ab_after = con.execute(
        "SELECT ingest_batch_id FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND holder_name IN ('a','b') "
        "ORDER BY holder_name"
    ).fetchall()
    assert ab_after == ab_before  # a、b 的 ingest_batch_id 不变

    t_exit_batch_after = con.execute(
        "SELECT ingest_batch_id FROM canonical_top10_float_holders_period "
        "WHERE stock_code='T' AND report_date='20260401' AND is_exit_row"
    ).fetchall()
    assert t_exit_batch_after == t_exit_batch_before  # T 的退出行不受影响


def test_b26b_held_only_group_keeps_exit_rows():
    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=1, row_seq=1, name="a", code="Ca"),
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=2, row_seq=1, name="b", code="Cb"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=1, row_seq=1, name="a", code="Ca"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("S", "20260201")}), acquire_evidence=_acquire_evidence())
    exit_before = con.execute(
        "SELECT ingest_batch_id, COUNT(*) FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND is_exit_row GROUP BY ingest_batch_id"
    ).fetchall()
    assert len(exit_before) == 1

    # 只含 revised 行的 delta (S 无新观测) -> touched_groups 空
    out_revised = _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=1, row_seq=1, name="a",
                        code="Ca", change_status="增持"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset(), acquire_evidence=_acquire_evidence())
    assert out_revised.exit_rows_replaced == 0
    exit_after = con.execute(
        "SELECT ingest_batch_id, COUNT(*) FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260201' AND is_exit_row GROUP BY ingest_batch_id"
    ).fetchall()
    assert exit_after == exit_before


def test_b17c_late_row_no_false_exit_across_v2_and_coded_identity():
    """返修 blocking finding (holders_aif10.py ``_derive_exits_against_canonical``):
    当前期 D 分区里的机构行是 v2 遗留 (holder_code/is_holder_org 为 NULL, 身份
    当时只记了名字), 上一期是带 holder_code 的 v3/v4 行时, 旧的单一
    "code-or-name" token 比较 (上一期键 "C1" vs 当前期键 "甲机构") 永远对不上,
    会把仍在榜的机构误判成退出——即使它其实一直都在, 只是本期身份记录退化成
    了名字。"甲机构"/"乙" 降格成 NULL 是模拟 v3 迁移前就已经写进 canonical 的
    历史行 (不是这次写入产生的, accept 校验不允许新写入带 NULL is_holder_org,
    只能改库还原这个已知的生产现状, 见 holders_top10_schema.py:353)。"""
    con = duckdb.connect(":memory:")
    # 上一期 (v4, 带 code): 甲机构 code=C1, 乙(个人, 无 code)。
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260331", notice="20260425", rank=1,
                        row_seq=1, name="甲机构", code="C1", is_org=True),
        _canonical_row(stock="S", report="20260331", notice="20260425", rank=2,
                        row_seq=1, name="乙", code=None, is_org=False),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())

    # 本期 D=0630@0731: 先按正常路径落地(过 accept 校验), 再改库降格成 v2 ——
    # 模拟这两行其实是迁移前遗留, 现状就是 holder_code/is_holder_org 为 NULL。
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260630", notice="20260731", rank=1,
                        row_seq=1, name="甲机构", code="C1", is_org=True),
        _canonical_row(stock="S", report="20260630", notice="20260731", rank=2,
                        row_seq=1, name="乙", code=None, is_org=False),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    con.execute(
        "UPDATE canonical_top10_float_holders_period "
        "SET holder_code=NULL, is_holder_org=NULL "
        "WHERE stock_code='S' AND report_date='20260630' AND notice_date='20260731'"
    )

    # 迟到行「丙」到达, 只这一行进批次, touched={(S,20260630)}。
    out = _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260630", notice="20260731", rank=3,
                        row_seq=1, name="丙", code=None, is_org=False),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("S", "20260630")}),
       acquire_evidence=_acquire_evidence())

    assert out.exit_rows_replaced == 0
    exit_rows = con.execute(
        "SELECT holder_name FROM canonical_top10_float_holders_period "
        "WHERE stock_code='S' AND report_date='20260630' AND is_exit_row"
    ).fetchall()
    assert exit_rows == []  # 甲机构、乙都还在(身份记录退化成了名字), 不许判退出


def test_b27_exit_group_must_be_touched():
    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=1, row_seq=1, name="a", code="Ca"),
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=2, row_seq=1, name="b", code="Cb"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    # 手工构造一个"退出行"批次, 组 (S,P) 没有任何 GRAIN 不存在的观测行
    exit_row = _canonical_row(stock="S", report="20260101", notice="20260202", rank=1, row_seq=1,
                               name="b", is_exit=True)
    from services.data_sources.disclosure_dual_write import (
        DisclosureDualWriteError,
        write_holders_top10_formal_then_mirror,
    )

    with pytest.raises(DisclosureDualWriteError):
        write_holders_top10_formal_then_mirror(
            con, [exit_row], delete_scope="merge_new_grains",
            request_extra=_acquire_evidence(),
        )
    rejection_code = con.execute(
        "SELECT rejection_code FROM ingest_batch WHERE status = 'REJECTED'"
    ).fetchone()[0]
    assert rejection_code == "EXIT_GROUP_NOT_TOUCHED"


def test_b28_pointer_restamped_after_merge():
    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=1, row_seq=1, name="a", code="Ca"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=1, row_seq=1, name="b", code="Cb"),
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=2, row_seq=1, name="c", code="Cc"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=True,
       touched_groups=frozenset({("S", "20260201")}), acquire_evidence=_acquire_evidence())
    actual = con.execute(
        "SELECT COUNT(*) FROM canonical_top10_float_holders_period WHERE notice_date='20260202'"
    ).fetchone()[0]
    pointer = con.execute(
        "SELECT row_count FROM accepted_partition WHERE partition_value='20260202'"
    ).fetchone()[0]
    assert pointer == actual
    # db_invariants.holders_pointer_rowcount_matches_canonical 的判据复算: 差值必须 0
    assert pointer - actual == 0


def test_b30_contract_constants_unchanged():
    from services.data_sources.holders_top10_contract import load_holders_top10_contract
    from services.data_sources.holders_top10_schema import CONTRACT_VERSION, SCHEMA_HASH, SCHEMA_VERSION

    assert SCHEMA_VERSION == "4"
    assert CONTRACT_VERSION == "5"
    assert SCHEMA_HASH == (
        "25448e683ffb3f0945cd546a505ab1ce6997cae3f69139b4a72c0150cb19b020"
    )
    contract = load_holders_top10_contract()
    assert contract.config_hash == (
        "90aea6b4120d8b1f091e66e2331a6f3fca0e6bdc7af623145c5a9b034009b27a"
    )
    assert contract.contract_hash == (
        "3b79b496cea16dabea6d48bb675df3a5097dbd5d6934102c066661e9cd6244a0"
    )


def test_b31_asof_view_property():
    """经 merge_new_grains 写入的观测行满足 as-of 承诺: landed_at <= t1 看到的
    集合与 t1 当时逐字相同, n 行的 ingest_batch_id 后续取数不变。"""
    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=i, row_seq=1, name=f"H{i}",
                        code=f"C{i}")
        for i in range(1, 6)
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    t1 = con.execute("SELECT MAX(landed_at) FROM ingest_batch").fetchone()[0]

    def _asof(t):
        return con.execute(
            "SELECT c.stock_code, c.holder_rank, c.holder_name, c.ingest_batch_id "
            "FROM canonical_top10_float_holders_period c "
            "JOIN ingest_batch ib ON c.ingest_batch_id = ib.batch_id "
            "WHERE ib.landed_at <= ? ORDER BY c.holder_rank",
            [t],
        ).fetchall()

    snapshot_t1 = _asof(t1)
    assert len(snapshot_t1) == 5

    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=6, row_seq=1, name="H6",
                        code="C6"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())
    t2 = con.execute("SELECT MAX(landed_at) FROM ingest_batch").fetchone()[0]

    assert _asof(t1) == snapshot_t1  # 固定 t1, 加入未来取到的数据不改变历史输出
    assert len(_asof(t2)) == 6
    unchanged_batches = {r[3] for r in _asof(t1)}
    later_batches = {r[3] for r in _asof(t2) if r[1] != 6}
    assert unchanged_batches == later_batches  # 前 5 行的 batch_id 没有被换掉


class _ExecuteSpyConn:
    """真实 DuckDB 连接的薄代理: DuckDBPyConnection 是 C 扩展对象, 不能直接
    monkeypatch 它的 .execute 属性 (read-only slot), 所以用代理转发并记录
    SQL 文本。"""

    def __init__(self, real):
        self._real = real
        self.sql_calls: list[str] = []

    def execute(self, sql, *a, **k):
        self.sql_calls.append(sql)
        return self._real.execute(sql, *a, **k)

    def executemany(self, sql, *a, **k):
        self.sql_calls.append(sql)
        return self._real.executemany(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_b4b_merge_mode_never_reads_canonical_back():
    """merge 模式绝不读 canonical 回灌 landing (红线 4)."""
    con = duckdb.connect(":memory:")
    _write_with_outcome(con, [
        _canonical_row(stock="S", report="20260101", notice="20260102", rank=1, row_seq=1, name="a", code="Ca"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())

    spy = _ExecuteSpyConn(con)
    _write_with_outcome(spy, [
        _canonical_row(stock="S", report="20260201", notice="20260202", rank=1, row_seq=1, name="b", code="Cb"),
    ], delete_scope="merge_new_grains", derive_exits_from_canonical=False,
       acquire_evidence=_acquire_evidence())

    # 指纹 = 整分区读回查询自己的 SELECT 列清单 (", ".join(CANONICAL_ROW_FIELDS)),
    # 不是子串 "stock_code" —— CANONICAL_ROW_FIELDS[0] == "stock_code", 任何选出
    # 该分区行的查询 (含不构成 B4b 违规的 partition_pointer_stats, 它选 _HASH_FIELDS
    # 不是 CANONICAL_ROW_FIELDS) 文本里都恒含这个子串, 旧写法这个过滤器永远是空列表。
    readback_select_list = ", ".join(HOLDERS_CANONICAL_ROW_FIELDS)
    partition_readback = [
        c for c in spy.sql_calls
        if "FROM canonical_top10_float_holders_period" in c
        and "WHERE notice_date = ?" in c
        and readback_select_list in c
    ]
    assert partition_readback == []
    assert spy.sql_calls  # 代理确实被经过(不是断言一个空列表永远成立)


def test_b21_daily_and_reland_paths_only_merge_mode(monkeypatch):
    """N1 (真路径, 返修 blocking finding): 旧版只自证"传 merge_new_grains 进
    _write 会被原样转发"和 sync_holders_aif10 的 stocks_in_batch, 从没真跑过
    日更/回补真正硬编码 delete_scope 的那两处调用点 —— recheck_notice_day
    (holders_aif10.py:1284) 与 reland_stock_in_day (:1399)。把 1284 改成
    "partition"、1399 改成 "partition" 或 "stocks_in_batch", 159 条用例仍全绿
    (返修 blocking finding)。改成 spy 住 ddw.write_holders_top10_formal_then_mirror
    (不 monkeypatch 被测函数本身), 真跑三条路径:
      路径一 recheck_notice_day (日更 / by_day 共用同一个函数, 真引擎+真 accept)
      路径二 reland_stock_in_day (by_stock 回补, 只桩采集层 _fetch_raw)
      路径三 sync_holders_aif10 (按股全史替换, 结构上不是日更/回补)
    前两条必须记到 merge_new_grains, 第三条必须是 stocks_in_batch。"""
    calls: list[str] = []
    from services.data_sources import disclosure_dual_write as ddw
    import services.holders_aif10 as mod

    real = ddw.write_holders_top10_formal_then_mirror

    def spy(conn, rows, *, delete_scope="partition", **kw):
        calls.append(delete_scope)
        return real(conn, rows, delete_scope=delete_scope, **kw)

    monkeypatch.setattr(ddw, "write_holders_top10_formal_then_mirror", spy)

    # 路径一: 日更 / by_day 共用的 recheck_notice_day (真严格分页引擎 + 真
    # land→accept, 只假客户端)。
    con_daily = duckdb.connect(":memory:")
    daily_rows = [_raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变",
                        upd="2026-07-01")]
    out_daily = recheck_notice_day(con_daily, "20260701", client=_FakeStrictClient(daily_rows),
                                    run_kind="daily", write=True,
                                    now_fn=lambda: _utc(2026, 7, 2))
    assert out_daily["outcome"] == "complete"
    assert calls == ["merge_new_grains"]

    # 路径二: by_stock 回补 reland_stock_in_day —— 只桩采集层 _fetch_raw (不
    # monkeypatch 被测的 reland_stock_in_day 本身), diff/写入走真代码。
    calls.clear()
    reland_rows = [_raw("600389.SH", "600389", "2026-06-30", "乙", 1, 200, "不变",
                         upd="2026-07-01")]

    def fake_fetch_raw(client, symbol):
        del client, symbol
        return reland_rows

    monkeypatch.setattr(mod, "_fetch_raw", fake_fetch_raw)
    con_reland = duckdb.connect(":memory:")
    out_reland = reland_stock_in_day(con_reland, "20260701", "600389", client=object(),
                                      write=True, now_fn=lambda: _utc(2026, 7, 2))
    assert out_reland["outcome"] == "complete"
    assert calls == ["merge_new_grains"]

    # 路径三: 按股全史替换 (结构上不是日更/回补, sync_holders_aif10 唯一保留的
    # 替换路径), 仍必须是 stocks_in_batch。
    calls.clear()
    rows = [_raw("600000.SH", "600000", "2024-12-31", "股东甲", 1, 1000, "新进")]

    def fetch_all_pages(_report, *, secucode, page_size=500, max_pages=0, client=None):
        del _report, page_size, max_pages, client
        return rows

    fake = __import__("types").ModuleType("aif10_scraper")
    fake.fetch_all_pages = fetch_all_pages
    fake.default_client = object()
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake)
    con2 = duckdb.connect(":memory:")
    sync_holders_aif10(con2, symbols=["600000"])
    assert calls == ["stocks_in_batch"]


# ── recheck_notice_day: 日更循环 (真引擎 + 真账本, 假客户端) ──────────────


def _clean_key_collision_raw_rows():
    """两条原始行只差 HOLDER_NAME 首尾空白: 翻页引擎的 identity_columns 按
    **原始字符串**分组 (\"甲 \" != \"甲\"), 不会判定它们冲突/重复; _clean 用
    _safe_text 去空白后两者的 _holder_key 才会撞在一起 —— 这正是 V14 说的
    "清洗后键不唯一"。"""
    return [
        _raw("600388.SH", "600388", "2026-06-30", "甲 ", 1, 100, "不变", upd="2026-07-01"),
        _raw("600388.SH", "600388", "2026-06-30", "甲", 1, 200, "不变", upd="2026-07-01"),
    ]


def test_recheck_notice_day_clean_key_collision_fails_this_day_only():
    """V14: diff_notice_day 的 RuntimeError 被 recheck_notice_day 归为账本
    failed reason=clean_key_collision, 只让这一天失败, 返回 {"error": ...}
    而不是让异常冒出去中断整次日更循环。"""
    con = duckdb.connect(":memory:")
    out = recheck_notice_day(
        con, "20260701", client=_FakeStrictClient(_clean_key_collision_raw_rows()),
        run_kind="daily", write=True, now_fn=lambda: _utc(2026, 7, 2),
    )
    assert "error" in out
    assert "clean_key_collision" in out["error"]
    row = con.execute(
        "SELECT outcome, reason FROM holders_notice_fetch_ledger WHERE notice_date='20260701'"
    ).fetchone()
    assert row == ("failed", "clean_key_collision")
    # 没有任何 canonical 写入残留 (整条路径在 diff 阶段就短路了)。
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}
    assert "canonical_top10_float_holders_period" not in tables


def test_run_due_notice_days_continues_past_clean_key_collision():
    """归入 failed 之后, 到期集合里其它日期照常继续 (不因一天冲突拖垮整批)。"""
    from services.holders_notice_catchup import run_due_notice_days

    con = duckdb.connect(":memory:")
    good_rows = [_raw("600519.SH", "600519", "2026-06-30", "乙", 1, 100, "不变",
                       upd="2026-07-02")]

    # run_due_notice_days 只接收一个 client, 两天的取数走同一个假客户端实例;
    # 用调用计数分辨"第几次调用" (第一天冲突, 第二天正常), 验证 run_due_notice_days
    # 把它们分别归类进 failed_partitions / landed_partitions, 不因第一天异常
    # 中断第二天。
    class _StatefulClient:
        def __init__(self):
            self.n = 0

        def get_v1(self, *_a, **_k):
            self.n += 1
            if self.n == 1:
                return {"code": 0, "message": "ok", "success": True, "pages": 1,
                        "data": _clean_key_collision_raw_rows(), "count": 2}
            return {"code": 0, "message": "ok", "success": True, "pages": 1,
                    "data": good_rows, "count": 1}

    out = run_due_notice_days(
        con, ["20260701", "20260702"], client=_StatefulClient(), run_kind="daily",
        now_fn=lambda: _utc(2026, 7, 3),
    )
    assert out["failed_partitions"] == ["20260701"]
    assert out["landed_partitions"] == ["20260702"]
    assert any("clean_key_collision" in e for e in out["errors"])


def test_b6_blocked_reraises_and_stops():
    con = duckdb.connect(":memory:")
    client = _RaisingClient(AIF10BlockedError("banned"))
    now_fn = lambda: _utc(2026, 7, 2)  # noqa: E731
    with pytest.raises(AIF10BlockedError):
        recheck_notice_day(con, "20260701", client=client, run_kind="daily",
                            write=True, now_fn=now_fn)
    row = con.execute(
        "SELECT outcome, reason FROM holders_notice_fetch_ledger WHERE notice_date='20260701'"
    ).fetchone()
    assert row == ("failed", "blocked")


def test_b6_two_day_due_set_stops_before_second_day():
    """B6 两天循环 (返修 blocking finding): 之前 test_b6_blocked_reraises_and_stops
    只对单日直接调 ``recheck_notice_day``, 既没跑循环也没断言第二天未被请求——
    M25 (``run_due_notice_days`` 里把 ``recheck_notice_day`` 调用包一层
    ``try/except Exception`` 收进 errors 继续下一天) 跑出来全绿, 因为压根没有
    用例经过这个循环体。这里改走真循环 ``run_due_notice_days(due=[d1,d2])``:
    d1 假客户端抛 ``AIF10BlockedError`` -> 断言 ``sync`` 侧原样抛出精确类、
    d2 从未被请求(客户端调用计数钉住, 不是只看 due 列表)、账本 d1 一行
    failed reason=blocked。M25 变异下 d2 会被请求, calls 从 1 变成 2, 本用例
    必须变红。"""
    from services.holders_notice_catchup import run_due_notice_days

    con = duckdb.connect(":memory:")
    client = _RaisingClient(AIF10BlockedError("banned"))
    now_fn = lambda: _utc(2026, 7, 3)  # noqa: E731

    with pytest.raises(AIF10BlockedError):
        run_due_notice_days(
            con, ["20260701", "20260702"], client=client, run_kind="daily", now_fn=now_fn,
        )

    assert client.calls == 1  # d2=20260702 从未被请求 (第一天 Blocked 就地停住)

    row = con.execute(
        "SELECT outcome, reason FROM holders_notice_fetch_ledger WHERE notice_date='20260701'"
    ).fetchone()
    assert row == ("failed", "blocked")
    assert con.execute(
        "SELECT COUNT(*) FROM holders_notice_fetch_ledger WHERE notice_date='20260702'"
    ).fetchone()[0] == 0


def test_b15_dry_run_reads_only():
    con = duckdb.connect(":memory:")
    rows = [_raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变", upd="2026-07-01")]
    now_fn = lambda: _utc(2026, 7, 2)  # noqa: E731

    dry = recheck_notice_day(con, "20260701", client=_FakeStrictClient(rows),
                              run_kind="daily", write=False, now_fn=now_fn)
    ledger_n = con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0] \
        if "holders_notice_fetch_ledger" in {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()} else 0
    canonical_n = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?",
        ["canonical_top10_float_holders_period"],
    ).fetchone()[0]
    assert ledger_n == 0
    assert canonical_n == 0  # dry-run 从未建表 (从未写)

    execute = recheck_notice_day(con, "20260701", client=_FakeStrictClient(rows),
                                  run_kind="daily", write=True, now_fn=now_fn)
    assert dry["diff"] == execute["diff"]
    assert execute["rows_inserted"] == 1
    assert con.execute("SELECT COUNT(*) FROM holders_notice_fetch_ledger").fetchone()[0] == 1


class _B7RouterClient:
    """B7_failed_day_stays_due (返修 blocking finding, sync 层真链): 按
    ``extra_filters`` 里的 ``UPDATE_DATE`` 路由。探针调用 (走 ``filter_expr``,
    没有 ``extra_filters``) 恒返回固定的 ``provider_max``；``drift_iso`` 那天
    每次整日取数都在第 2 页把 ``count`` 声明成跟第 1 页不同 (count_drift),
    ``drift_refetch=1`` 的重取从第 1 页重来一遍仍是同样的漂移, 两遍都失败;
    其它日期按登记的行正常整日返回。"""

    def __init__(self, drift_iso: str, provider_max_iso: str,
                 normal_rows_by_iso: dict[str, list[dict]]):
        self._drift_iso = drift_iso
        self._provider_max_iso = provider_max_iso
        self._normal_rows = dict(normal_rows_by_iso)
        self.calls_by_iso: dict[str, int] = {}

    def get_v1(self, report_name, *, page=1, page_size=1, sort_columns="",
               sort_types="", columns="ALL", secucode=None, extra_filters=None,
               filter_expr=None, extra_params=None):
        del report_name, sort_columns, sort_types, columns, secucode
        if extra_filters is None:
            # 探针路径 (_provider_newest_update_date 用 filter_expr, 不用
            # extra_filters)。
            return {"code": 0, "message": "ok", "success": True, "pages": 1,
                    "count": 1,
                    "data": [{"UPDATE_DATE": f"{self._provider_max_iso} 00:00:00"}]}
        iso = extra_filters[0].split("'")[1]
        self.calls_by_iso[iso] = self.calls_by_iso.get(iso, 0) + 1
        if iso == self._drift_iso:
            if page == 1:
                return {"code": 0, "message": "ok", "success": True, "pages": 2,
                        "count": 4, "data": [
                            _raw("600001.SH", "600001", "2026-06-30", "甲", 1, 100,
                                 "不变", upd=self._drift_iso),
                            _raw("600002.SH", "600002", "2026-06-30", "乙", 2, 100,
                                 "不变", upd=self._drift_iso),
                        ]}
            # 第 2 页声明的 count 跟第 1 页不一致 -> count_drift (供应商这一天
            # 还在进行, 不是翻页坏了; drift_refetch=1 会整日重取一次, 但本假
            # 客户端每一遍都复现同样的漂移, 两遍都失败)。
            return {"code": 0, "message": "ok", "success": True, "pages": 2,
                    "count": 5, "data": [
                        _raw("600003.SH", "600003", "2026-06-30", "丙", 3, 100,
                             "不变", upd=self._drift_iso),
                        _raw("600004.SH", "600004", "2026-06-30", "丁", 4, 100,
                             "不变", upd=self._drift_iso),
                    ]}
        rows = self._normal_rows.get(iso, [])
        if not rows:
            return {"code": 9201, "message": "返回数据为空", "success": False,
                    "pages": 0, "data": [], "count": 0}
        pages = -(-len(rows) // page_size)
        start = (page - 1) * page_size
        chunk = rows[start:start + page_size]
        return {"code": 0, "message": "ok", "success": True,
                "pages": pages, "data": chunk, "count": len(rows)}


def _seed_settled_days(con, start_yyyymmdd: str, end_yyyymmdd_exclusive: str) -> None:
    """给 [start, end) 半开区间的每个日历日写一条 settled 的账本行(scope='day',
    outcome='empty', missing_rows=0, fetched_at 远晚于 D), 让
    plan_due_notice_days 的到期集合只剩测试真正关心的那几天 —— 不然从
    EXPOSURE_START (20260701) 起裸跑会把中间几十个日历日全部拖进 due。"""
    from uuid import uuid4

    from services.holders_notice_ledger import LedgerRow, append_ledger, ensure_holders_notice_ledger

    ensure_holders_notice_ledger(con)
    d = datetime.strptime(start_yyyymmdd, "%Y%m%d").date()
    end = datetime.strptime(end_yyyymmdd_exclusive, "%Y%m%d").date()
    while d < end:
        nd = d.strftime("%Y%m%d")
        fetched = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=3)
        append_ledger(con, LedgerRow(
            ledger_id=uuid4().hex, notice_date=nd, fetched_at=fetched,
            run_kind="daily", scope="day", outcome="empty", missing_rows=0,
        ))
        d += timedelta(days=1)


def test_b7_failed_day_stays_due():
    """spec §7.5 B7_failed_day_stays_due (R3, 返修 blocking finding): 之前
    只有账本层的 test_failed_day_stays_due_forever(手写账本行), 没有覆盖
    recheck_notice_day -> 账本 failed -> plan_due_notice_days 这条真链
    (经 sync_holders_aif10_incremental 两次真跑)。运行 1: d1 两遍取数都
    count_drift(耗尽 drift_refetch=1 的重取预算)、d2 正常; 运行 2 (同一个
    client、同一个 now_fn、同一个 provider_max=d2): due == [d1], d1 被再次
    请求 —— 失败的日子谁都盖不掉, 不会被 d2 的成功"越过"。"""
    from uuid import uuid4

    con = duckdb.connect(":memory:")
    floor = "20260701"
    d1, d1_iso = "20260820", "2026-08-20"
    d2, d2_iso = "20260821", "2026-08-21"
    _seed_settled_days(con, floor, d1)  # [floor, d1) 全部预置为 settled

    # d2 用「空的一天」(9201, 没有本地已有行) 而不是真行: 一条真行第一次被看到
    # 必然 missing_rows=1, 要到*下一次*干净复核才能 settle(B7b 的道理), 那样
    # d2 在运行 1 里就不会 settle, 测不出"运行 2 due 收窄成只剩 d1"这件事 ——
    # 空天 + local_rows_before=0 一次就满足 settled_notice_days 的 V1 条件。
    client = _B7RouterClient(
        drift_iso=d1_iso, provider_max_iso=d2_iso, normal_rows_by_iso={},
    )
    now_fn = lambda: _utc(2026, 8, 22, 10, 0)  # noqa: E731 — 时钟 d2+1, 两次运行相同

    import services.holders_aif10 as mod
    orig_client, orig_page_size = mod._daily_client, mod.PAGE_SIZE
    mod._daily_client = lambda: client
    mod.PAGE_SIZE = 2  # 让 4/5 行的假响应落在 2 个整页上, 不撞 pages_count_inconsistent
    try:
        out1 = sync_holders_aif10_incremental(con, now_fn=now_fn)
        assert out1["due_days"] == 2
        assert any(e.startswith(f"{d1}:") for e in out1["errors"])
        assert any("count=4" in e and "count=5" in e for e in out1["errors"])
        assert out1["failed_partitions"] == 1
        assert out1["empty_partitions"] == 1  # d2 正常完成(空天), 不是 failed

        d1_ledger = con.execute(
            "SELECT outcome, reason FROM holders_notice_fetch_ledger "
            "WHERE notice_date=? ORDER BY fetched_at DESC LIMIT 1", [d1],
        ).fetchone()
        assert d1_ledger == ("failed", "PaginationIntegrityError")
        d2_ledger = con.execute(
            "SELECT outcome FROM holders_notice_fetch_ledger "
            "WHERE notice_date=? ORDER BY fetched_at DESC LIMIT 1", [d2],
        ).fetchone()
        assert d2_ledger == ("empty",)

        calls_d1_after_run1 = client.calls_by_iso.get(d1_iso, 0)
        assert calls_d1_after_run1 > 0

        out2 = sync_holders_aif10_incremental(con, now_fn=now_fn)
        assert out2["due_days"] == 1  # d2 已 settled, 只剩 d1
        assert any(e.startswith(f"{d1}:") for e in out2["errors"])
        assert client.calls_by_iso.get(d1_iso, 0) > calls_d1_after_run1  # d1 再次被请求
    finally:
        mod._daily_client = orig_client
        mod.PAGE_SIZE = orig_page_size


@pytest.mark.parametrize("tz", ["Asia/Shanghai", "UTC"])
def test_b7b_settle_requires_clean_post_settle_fetch(tz):
    """R1/S1 (真路径, 返修 blocking finding): 旧版直接摆四个人工挑的 UTC 时间点
    硬调 recheck_notice_day, 从没问过 plan_due_notice_days「这天到期吗」——
    (M1) >= 改成 >、(M2) 丢 missing_rows=0 判据、(M3) 让 settle_days 不参与判定
    这三个变异全部绿过, 因为旧测试从不检验"到期集合是否已经把这天摘掉"这件事
    本身。改成每一步先真调 plan_due_notice_days 问到期(镜像
    run_due_notice_days 的真实调度: 到期才发请求), 用请求次数序列证明只有
    【日历日到达 D+settle_days 且这次取数干净】才会让 D 从到期集合摘掉:
      stage1 D 当天首次取数(首见一行, missing_rows=1)     -> 到期, 发请求
      stage2 D 当天(同一 CST 日)干净重取(missing_rows=0,
             但未过 settle_days)                            -> 仍到期(时间判据未到) —— 抓 M3
      stage3 CST 日恰好等于 D+settle_days 的干净重取        -> settle —— 抓 M1
      stage4 之后任意一天                                   -> 已不到期, 不发请求
    请求次数序列 1,1,1,0。

    T4 (返修 blocking finding, 与上面的 M1/M2/M3 是不同编号体系——那三个是
    settle_days/missing_rows 判据的变异, 这里的 T4 是账本会话时区): 按 ``tz``
    参数化跑 Asia/Shanghai 与 UTC 两遍会话, 断言整条真链路(plan_due_notice_days
    → recheck_notice_day → settled_notice_days)在两种会话下结果一致——这条用例
    自己选的边界时刻(10 点/23:30 上海时间)不跨 UTC 午夜, 不是抓「删 AT TIME
    ZONE」那个回归 (它专门跨 D 23:40 UTC = D+1 07:40 CST 这条午夜线) 的用例,
    那条钉在 test_holders_notice_ledger.py::test_settled_requires_clean_fetch_after_settle_days
    的 tz 参数化里(UTC 那组变红); 这里加 UTC 会话是 §7.5 T4 明确要求的「B7b/B7d
    用的 recheck 连接同样要在 UTC 会话下再跑一遍」, 确认整条路径本身不隐含依赖
    会话默认时区。"""
    from services.holders_aif10 import _RECHECK
    from services.holders_notice_ledger import plan_due_notice_days, settled_notice_days

    con = duckdb.connect(":memory:")
    con.execute(f"SET TimeZone='{tz}'")
    floor = "20260701"
    D = "20260820"
    settle_days = _RECHECK.settle_days
    D_date = datetime.strptime(D, "%Y%m%d")
    boundary_date = D_date + timedelta(days=settle_days)
    after_date = boundary_date + timedelta(days=1)
    _seed_settled_days(con, floor, D)
    row_v1 = [_raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变", upd="2026-08-20")]

    def due_now(now_fn) -> bool:
        return D in plan_due_notice_days(
            con, provider_max=D, settle_days=settle_days, floor=floor, max_days=40,
        )

    stages = [
        (lambda: _cst(D_date.year, D_date.month, D_date.day, 10), True, 1),
        (lambda: _cst(D_date.year, D_date.month, D_date.day, 23, 30), True, 0),
        (lambda: _cst(boundary_date.year, boundary_date.month, boundary_date.day, 10), True, 0),
        (lambda: _cst(after_date.year, after_date.month, after_date.day, 10), False, None),
    ]
    request_counts = []
    for now_fn, expect_due, expect_missing in stages:
        is_due = due_now(now_fn)
        assert is_due is expect_due
        client = _FakeStrictClient(row_v1)
        if is_due:
            out = recheck_notice_day(con, D, client=client, run_kind="daily",
                                      write=True, now_fn=now_fn)
            assert out["missing_rows"] == expect_missing
        request_counts.append(client.calls)

    assert request_counts == [1, 1, 1, 0]
    assert D in settled_notice_days(con, settle_days=settle_days)


def test_b7c_empty_day_settles_after_settle_days():
    con = duckdb.connect(":memory:")
    from services.holders_notice_ledger import settled_notice_days

    # UTC 2:00 = Beijing 10:00, 仍是 D 当天 (UTC 傍晚会跨进 Beijing 次日, 见
    # test_b8c_provider_max_upper_bound 的教训)。
    r1 = recheck_notice_day(con, "20260901", client=_FakeStrictClient([]),
                             run_kind="daily", write=True,
                             now_fn=lambda: _utc(2026, 9, 1, 2, 0))
    assert r1["outcome"] == "empty"
    assert "20260901" not in settled_notice_days(con, settle_days=1)

    recheck_notice_day(con, "20260901", client=_FakeStrictClient([]),
                        run_kind="daily", write=True,
                        now_fn=lambda: _utc(2026, 9, 1, 23, 40))
    assert "20260901" in settled_notice_days(con, settle_days=1)


@pytest.mark.parametrize("tz", ["Asia/Shanghai", "UTC"])
def test_b7d_late_row_after_settle_day_needs_second_clean_fetch(tz):
    """S1/性质(iii) (真路径, 返修 blocking finding): 跟 B7b 一样先真问
    plan_due_notice_days 再决定要不要发请求, 这里专测"内容脏"这半边 ——
    哪怕日历日已经过了 D+settle_days, 只要这次取数还发现新行(迟到行), 就不能
    settle(性质 iii: 那次正在接住迟到的行, 不是什么都没发现)。旧版直接连打
    四次 recheck_notice_day, 第二次就已经把 D settle 掉了(因为两次用的是同一份
    rows_v1, 没有新行), 第三/四次是生产里根本不会再到期的强行调用(返修
    blocking finding: "这一天在生产里根本不会再到期")。改成先问到期再发请求,
    并让新行分三次陆续冒出来:
      stage1 D 当天首见一行(甲)                   -> missing_rows=1, 到期
      stage2 到了 D+settle_days 当天又冒出一行(乙) -> missing_rows=1, 仍到期
             (若丢掉 missing_rows=0 判据, 这一步就会被误判 settle —— 抓 M2)
      stage3 再晚一天又冒出一行(丙)                -> missing_rows=1, 仍到期
      stage4 同样三行原样重取(不再新增)             -> missing_rows=0, 真正 settle
      stage5 之后任意一天                          -> 已不到期, 不发请求
    请求次数序列 1,1,1,1,0。

    T4 (返修 blocking finding): 同 B7b——按 ``tz`` 参数化跑 Asia/Shanghai 与
    UTC 两种会话, 确认这条真链路不隐含依赖会话默认时区; 这条用例自己的边界
    时刻同样不跨 UTC 午夜, 抓「删 AT TIME ZONE」回归的用例钉在
    test_holders_notice_ledger.py 的 tz 参数化里(见 B7b 的详细说明)。"""
    from services.holders_aif10 import _RECHECK
    from services.holders_notice_ledger import plan_due_notice_days, settled_notice_days

    con = duckdb.connect(":memory:")
    con.execute(f"SET TimeZone='{tz}'")
    floor = "20260701"
    D = "20260820"
    settle_days = _RECHECK.settle_days
    D_date = datetime.strptime(D, "%Y%m%d")
    boundary_date = D_date + timedelta(days=settle_days)
    after_date = boundary_date + timedelta(days=1)
    later_date = after_date + timedelta(days=1)
    _seed_settled_days(con, floor, D)

    row_a = _raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变", upd="2026-08-20")
    row_b = _raw("600388.SH", "600388", "2026-06-30", "乙", 2, 90, "不变", upd="2026-08-20")
    row_c = _raw("600388.SH", "600388", "2026-06-30", "丙", 3, 80, "不变", upd="2026-08-20")

    def due_now(now_fn) -> bool:
        return D in plan_due_notice_days(
            con, provider_max=D, settle_days=settle_days, floor=floor, max_days=40,
        )

    stages = [
        (lambda: _cst(D_date.year, D_date.month, D_date.day, 10), [row_a], True, 1),
        (lambda: _cst(boundary_date.year, boundary_date.month, boundary_date.day, 10),
         [row_a, row_b], True, 1),
        (lambda: _cst(after_date.year, after_date.month, after_date.day, 10),
         [row_a, row_b, row_c], True, 1),
        (lambda: _cst(after_date.year, after_date.month, after_date.day, 20),
         [row_a, row_b, row_c], True, 0),
        (lambda: _cst(later_date.year, later_date.month, later_date.day, 10),
         [row_a, row_b, row_c], False, None),
    ]
    request_counts = []
    for now_fn, content, expect_due, expect_missing in stages:
        is_due = due_now(now_fn)
        assert is_due is expect_due
        client = _FakeStrictClient(content)
        if is_due:
            out = recheck_notice_day(con, D, client=client, run_kind="daily",
                                      write=True, now_fn=now_fn)
            assert out["missing_rows"] == expect_missing
        request_counts.append(client.calls)

    assert request_counts == [1, 1, 1, 1, 0]
    assert D in settled_notice_days(con, settle_days=settle_days)


def test_b8_probe_fail_closed_variants():
    for exc in (
        AIF10ApiError(9501, "结构性错误"),
        AIF10UnknownCodeError(1234, "unknown"),
    ):
        with pytest.raises(HoldersProviderProbeError):
            _provider_newest_update_date(_RaisingClient(exc))

    with pytest.raises(HoldersProviderProbeError):
        _provider_newest_update_date(_FakeStrictClient([]))  # code 0, data []


def test_b8b_probe_blocked_reraises():
    with pytest.raises(AIF10BlockedError):
        _provider_newest_update_date(_RaisingClient(AIF10BlockedError("banned")))


def test_b8c_provider_max_upper_bound():
    from zoneinfo import ZoneInfo

    # 上界锚在上海日历日 (T5 的判据也是), 用同一时区基准算"超出", 不受运行本测试
    # 时那一刻 UTC/上海是否跨零点影响 (2026-09-25 22:23 UTC = 09-26 06:23 CST
    # 就曾经因为用 UTC today 算出的 +3 天恰好等于 Shanghai today+2, 测不出超出)。
    today_shanghai = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    future = (today_shanghai + timedelta(days=3)).strftime("%Y-%m-%d")
    resp = {"code": 0, "message": "ok", "success": True, "pages": 1,
            "data": [{"UPDATE_DATE": f"{future} 00:00:00"}], "count": 1}
    with pytest.raises(HoldersProviderProbeError):
        _provider_newest_update_date(_SequenceClient([resp]))


def test_sync_incremental_probe_failed_is_skipped_not_raised():
    con = duckdb.connect(":memory:")
    client = _RaisingClient(AIF10ApiError(9501, "结构性错误"))

    import services.holders_aif10 as mod
    orig = mod._daily_client
    mod._daily_client = lambda: client
    try:
        out = sync_holders_aif10_incremental(con, now_fn=lambda: _utc(2026, 9, 1))
    finally:
        mod._daily_client = orig
    assert out["skipped"] is True
    assert out["skip_reason"] == "provider_probe_failed"
    assert client.calls == 1


def test_b23_floor_guard_from_incremental():
    con = duckdb.connect(":memory:")
    from services.holders_notice_ledger import (
        HoldersLedgerFloorError,
        LedgerRow,
        append_ledger,
        ensure_holders_notice_ledger,
    )
    from uuid import uuid4

    ensure_holders_notice_ledger(con)
    append_ledger(con, LedgerRow(
        ledger_id=uuid4().hex, notice_date="20260630", fetched_at=_utc(2026, 7, 1),
        run_kind="daily", scope="day", outcome="failed", reason="blocked",
    ))

    import services.holders_aif10 as mod
    fake = _FakeStrictClient([
        _raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变", upd="2026-09-01")
    ])
    orig = mod._daily_client
    mod._daily_client = lambda: fake
    try:
        with pytest.raises(HoldersLedgerFloorError):
            sync_holders_aif10_incremental(con, now_fn=lambda: _utc(2026, 9, 2))
    finally:
        mod._daily_client = orig


def test_reland_stock_in_day_scope_stock_ledger(monkeypatch):
    con = duckdb.connect(":memory:")
    raw = [_raw("600388.SH", "600388", "2026-06-30", "甲", 1, 100, "不变", upd="2026-07-01")]

    # 只桩 _fetch_raw (外部依赖的采集边界), 不 fake aif10_scraper 整个模块 ——
    # 后者会污染 sys.modules, 让本函数内部 `from aif10_scraper import
    # AIF10BlockedError` 在假模块上找不到该名字而炸。
    monkeypatch.setattr(
        "services.holders_aif10._fetch_raw", lambda client, symbol: raw
    )
    out = reland_stock_in_day(con, "20260701", "600388", client=object(),
                               write=True, now_fn=lambda: _utc(2026, 7, 3))
    assert out["outcome"] == "complete"
    row = con.execute(
        "SELECT scope, stock_code, run_kind FROM holders_notice_fetch_ledger"
    ).fetchone()
    assert row == ("stock", "600388", "reland")


# ── B16: 死符号清扫 (AST 静态钉) ─────────────────────────────────────────


def test_b16_no_dead_symbols_left():
    """规格 B16 原文按源码全文(含 docstring/注释)判定「不含」这些已删符号名,
    只有 stocks_in_batch 允许用 AST 名字判 —— 返修 blocking finding: 旧版对
    全部名字都只扫 ast.Name/Attribute/FunctionDef, docstring 里的纯文本提及
    (墓碑) 能直接绕过去, 是假通过。改成对源码全文做子串检查, 与规格一致。"""
    dead_names = {
        "_dedupe_notice_rows_by_grain", "HoldersDuplicateGrainConflictError",
        "_local_stock_codes_for_notice_date", "_notice_row_stock_codes",
        "land_holders_notice_partitions_forward",
        "catchup_missing_holders_notice_partitions",
        "list_missing_notice_partitions_from_fact", "decide_frontier",
        "provider_max_unknown_no_mass", "recheck_floor",
        "fetch_all_pages_with_ledger",
    }
    for path in ("services/holders_aif10.py", "services/holders_notice_catchup.py"):
        src = Path(__file__).resolve().parents[1].joinpath(path).read_text(encoding="utf-8")
        hit = {name for name in dead_names if name in src}
        assert not hit, f"{path} 仍引用已删符号(含注释/docstring): {hit}"

    src = Path(__file__).resolve().parents[1].joinpath(
        "services/holders_aif10.py"
    ).read_text(encoding="utf-8")
    # "stocks_in_batch" 只允许出现在 sync_holders_aif10 的签名/docstring 里。
    occurrences = [i for i in range(len(src)) if src.startswith("stocks_in_batch", i)]
    sig_start = src.index("def sync_holders_aif10(")
    sig_end = src.index('"""编排 获取')
    doc_end = src.index('"""', src.index('"""', sig_end) + 3)
    allowed_span = (sig_start, doc_end)
    for pos in occurrences:
        assert allowed_span[0] <= pos <= allowed_span[1], (
            f"'stocks_in_batch' 出现在 sync_holders_aif10 签名/docstring 之外, offset={pos}"
        )


def test_b22_sync_result_keys_and_acquire_print():
    con = duckdb.connect(":memory:")
    fake = _FakeStrictClient([])
    import services.holders_aif10 as mod

    orig = mod._daily_client
    mod._daily_client = lambda: fake
    try:
        out = sync_holders_aif10_incremental(con, now_fn=lambda: _utc(2026, 9, 1))
    finally:
        mod._daily_client = orig
    expected_keys = {
        "watermark", "net_new_notice_rows", "notice_partitions_touched", "errors",
        "due_days", "rechecked", "landed_partitions", "empty_partitions",
        "failed_partitions", "settled_after_run", "rows_inserted",
        "rows_revised_recorded",
    }
    assert expected_keys <= set(out)

    acquire_src = Path(__file__).resolve().parents[1].joinpath(
        "services/pipeline/acquire.py"
    ).read_text(encoding="utf-8")
    assert "rewrite_amplification_rows" not in acquire_src
    assert "notice_partition_forward" not in acquire_src


# ── CARRY_FIELDS / _assert_carry_fields_in_canonical (touched_groups 化) ──


def _canon_with_identity():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN,
            holder_code VARCHAR, is_holder_org BOOLEAN,
            hold_ratio_float DOUBLE, shares_approx BIGINT
        )
        """
    )
    return con


def test_derive_exits_against_canonical_requires_touched_groups():
    """默认(不传 touched_groups) 不产出任何退出行 -- fail-safe 默认, 不是
    "自动发现哪些组"(旧 covered 启发式已随重写删除)。"""
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) VALUES "
        "('600388','20260331','20260425','A机构',FALSE,'10000001',TRUE),"
        "('600388','20260331','20260425','B机构',FALSE,'10000002',TRUE)"
    )
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630",
        "notice_date": "20260722", "holder_name": "A机构", "is_exit_row": False,
        "holder_code": "10000001", "is_holder_org": True,
    }]
    assert _derive_exits_against_canonical(con, new_rows) == []


def test_derive_exits_against_canonical_finds_gone_holder_when_touched():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) VALUES "
        "('600388','20260331','20260425','A机构',FALSE,'10000001',TRUE),"
        "('600388','20260331','20260425','B机构',FALSE,'10000002',TRUE)"
    )
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630",
        "notice_date": "20260722", "holder_name": "A机构", "is_exit_row": False,
        "holder_code": "10000001", "is_holder_org": True,
    }]
    exits = _derive_exits_against_canonical(
        con, new_rows, touched_groups=frozenset({("600388", "20260630")})
    )
    assert len(exits) == 1
    assert exits[0]["holder_name"] == "B机构"
    assert exits[0]["is_exit_row"] is True
    assert exits[0]["report_date"] == "20260630"
    assert exits[0]["change_status"] == "退出"


def test_derive_exits_against_canonical_no_prior_period_no_op():
    con = _canon_with_identity()
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630",
        "notice_date": "20260722", "holder_name": "A机构", "is_exit_row": False,
        "holder_code": "10000001", "is_holder_org": True,
    }]
    assert _derive_exits_against_canonical(
        con, new_rows, touched_groups=frozenset({("600388", "20260630")})
    ) == []


def test_b17_exit_derive_uses_only_prev_period_published_by_d():
    """R4 (返修 blocking finding, 夹具重写): S 有 P1(report=2025-12-31,
    notice=2026-06-30,{a,b}) 与 P2(report=2026-03-31, notice=2026-08-15
    晚于 D,{c}); 批次 S 的 P3(report=2026-06-30, notice=D=2026-07-27,{a})
    -> 退出行只用 P1, 不含 c。

    旧夹具把 P2 的 report_date 写成 '20260815'(比批次的 report_date
    '20260727' 还晚), 于是 ``report_date<?`` 这一层过滤本身就已经把 P2
    排掉, 根本轮不到 ``notice_date<=?`` 出场——删掉 notice_date 过滤的变异
    (M3) 测不出来, 因为候选集合里从来只有 P1 一个。现在把 P2 的 report_date
    改成 2026-03-31(比 P1 的 2025-12-31 新、比批次的 2026-06-30 早), 让它先
    通过 report_date 过滤、只靠 notice_date(晚于 D)被挡在外——``ORDER BY
    report_date DESC`` 会优先选 report_date 更新的 P2, 只有 notice_date<=D
    这道过滤把它排除后, 才轮到 P1 被选中当"上一期"。"""
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) VALUES "
        "('600388','20251231','20260630','a',FALSE,'CA',TRUE),"
        "('600388','20251231','20260630','b',FALSE,'CB',TRUE),"
        "('600388','20260331','20260815','c',FALSE,'CC',TRUE)"  # report_date 比 P1 新, notice_date 晚于 D=07-27
    )
    cur = [{
        "stock_code": "600388", "report_date": "20260630", "notice_date": "20260727",
        "page_update_date": "20260727", "holder_name": "a",
        "holder_code": "CA", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600388", "20260630")})
    )
    assert {e["holder_name"] for e in exits} == {"b"}


def test_b17b_exit_derive_uses_version_visible_at_d():
    """R4: P1 两版 -- 06-30{a,b}、重述 08-01{a,x}; 批次 P2 D=07-27{a} -> 退出行=={b}."""
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) VALUES "
        "('600388','20260630','20260630','a',FALSE,'CA',TRUE),"
        "('600388','20260630','20260630','b',FALSE,'CB',TRUE),"
        "('600388','20260630','20260801','a',FALSE,'CA',TRUE),"
        "('600388','20260630','20260801','x',FALSE,'CX',TRUE)"
    )
    cur = [{
        "stock_code": "600388", "report_date": "20260727", "notice_date": "20260727",
        "page_update_date": "20260727", "holder_name": "a",
        "holder_code": "CA", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600388", "20260727")})
    )
    assert {e["holder_name"] for e in exits} == {"b"}


def test_same_batch_two_periods_chain_derivation():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) "
        "VALUES ('600000','20230930','20231020','A',FALSE,'CA',TRUE),"
        "       ('600000','20230930','20231020','B',FALSE,'CB',TRUE)"
    )

    def r(period, name, code):
        return {
            "stock_code": "600000", "report_date": period, "notice_date": "20240425",
            "page_update_date": "20240425", "holder_name": name,
            "holder_code": code, "is_holder_org": True,
            "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
        }

    rows = [r("20231231", "A", "CA"), r("20231231", "C", "CC"),
            r("20240331", "A", "CA"), r("20240331", "B", "CB")]
    got = sorted(
        (e["report_date"], e["holder_name"])
        for e in _derive_exits_against_canonical(
            con, rows, touched_groups=frozenset({("600000", "20231231"), ("600000", "20240331")})
        )
    )
    assert got == [("20231231", "B"), ("20240331", "C")]


def test_exit_derive_skips_when_prev_period_predates_identity_columns():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row) VALUES "
        "('600388','20260331','20260425','老机构甲',FALSE),"
        "('600388','20260331','20260425','老机构乙',FALSE)"
    )
    cur = [{
        "stock_code": "600388", "report_date": "20260630", "notice_date": "20260722",
        "page_update_date": "20260722", "holder_name": "老机构甲",
        "holder_code": "10000001", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600388", "20260630")})
    )
    assert exits == []


def test_exit_derive_still_works_when_prev_period_is_v3():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) "
        "VALUES ('600388','20260331','20260425','机构甲',FALSE,'10000001',TRUE),"
        "       ('600388','20260331','20260425','机构乙',FALSE,'10000002',TRUE)"
    )
    cur = [{
        "stock_code": "600388", "report_date": "20260630", "notice_date": "20260722",
        "page_update_date": "20260722", "holder_name": "机构甲",
        "holder_code": "10000001", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600388", "20260630")})
    )
    assert len(exits) == 1 and exits[0]["holder_name"] == "机构乙"
    assert exits[0]["holder_code"] == "10000002"
    assert exits[0]["is_holder_org"] is True


def test_derived_exit_row_carries_the_departed_holders_own_code():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) "
        "VALUES ('600887','20220630','20220820','香港中央结算有限公司',FALSE,'10671586',TRUE),"
        "       ('600887','20220630','20220820','胡利平',FALSE,NULL,FALSE)"
    )
    cur = [{
        "stock_code": "600887", "report_date": "20220930", "notice_date": "20221028",
        "page_update_date": "20221028", "holder_name": "香港中央结算有限公司",
        "holder_code": "10671586", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600887", "20220930")})
    )
    assert len(exits) == 1, exits
    e = exits[0]
    assert e["holder_name"] == "胡利平"
    assert e["holder_code"] is None
    assert e["is_holder_org"] is False


def test_rename_with_same_code_is_not_a_false_exit():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, is_holder_org) "
        "VALUES ('601211','20220630','20220820','国泰君安证券股份有限公司',FALSE,'10099999',TRUE)"
    )
    cur = [{
        "stock_code": "601211", "report_date": "20220930", "notice_date": "20221028",
        "page_update_date": "20221028", "holder_name": "国泰海通证券股份有限公司",
        "holder_code": "10099999", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("601211", "20220930")})
    )
    assert exits == []


def test_derived_exit_row_carries_last_known_position():
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, "
        " is_holder_org, hold_ratio_float, shares_approx) "
        "VALUES ('600000','20240331','20240425','留守',FALSE,'C1',TRUE,0.11,1000),"
        "       ('600000','20240331','20240425','离场',FALSE,'C2',TRUE,0.05,2000)"
    )
    cur = [{
        "stock_code": "600000", "report_date": "20240630", "notice_date": "20240820",
        "page_update_date": "20240820", "holder_name": "留守",
        "holder_code": "C1", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(
        con, cur, touched_groups=frozenset({("600000", "20240630")})
    )
    assert len(exits) == 1 and exits[0]["holder_name"] == "离场"
    e = exits[0]
    assert e["hold_ratio_float"] == 0.05
    assert e["shares_approx"] == 2000
    assert e["change_status"] == "退出"
    assert e["hold_change_num"] is None


def test_carry_fields_matches_real_canonical_columns():
    con = _canon_with_identity()
    try:
        _assert_carry_fields_in_canonical(con)
    finally:
        con.close()
    assert CARRY_FIELDS == ("hold_ratio_float", "shares_approx")


def test_assert_carry_fields_raises_typed_error_on_missing_column():
    con = _canon_with_identity()
    try:
        with pytest.raises(HoldersCarryFieldSchemaError) as exc_info:
            _assert_carry_fields_in_canonical(
                con, fields=("hold_ratio_float", "hold_amount")
            )
    finally:
        con.close()
    assert "hold_amount" in str(exc_info.value)
    assert "hold_ratio_float" not in str(exc_info.value).split("hold_amount")[0]


def test_derive_exits_against_canonical_raises_on_carry_field_drift(monkeypatch):
    import services.holders_aif10 as holders_aif10_mod

    monkeypatch.setattr(
        holders_aif10_mod, "CARRY_FIELDS", ("hold_ratio_float", "hold_market_cap")
    )
    con = _canon_with_identity()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period "
        "(stock_code, report_date, notice_date, holder_name, is_exit_row, holder_code, "
        " is_holder_org, hold_ratio_float, shares_approx) "
        "VALUES ('600000','20240331','20240425','留守',FALSE,'C1',TRUE,0.11,1000),"
        "       ('600000','20240331','20240425','离场',FALSE,'C2',TRUE,0.05,2000)"
    )
    cur = [{
        "stock_code": "600000", "report_date": "20240630", "notice_date": "20240820",
        "page_update_date": "20240820", "holder_name": "留守",
        "holder_code": "C1", "is_holder_org": True,
        "holder_set": "free", "holder_rank": 1, "row_seq": 1, "is_exit_row": False,
    }]
    try:
        with pytest.raises(HoldersCarryFieldSchemaError, match="hold_market_cap"):
            _derive_exits_against_canonical(
                con, cur, touched_groups=frozenset({("600000", "20240630")})
            )
    finally:
        con.close()


# ── sync_holders_aif10 真执行 (按股全史手动路径, 未改) ─────────────────────


def _fake_scraper(monkeypatch, rows_by_symbol):
    import types

    def fetch_all_pages(_report, *, secucode, page_size=500, max_pages=0, client=None):
        del _report, page_size, max_pages, client
        code = str(secucode).split(".")[0]
        return list(rows_by_symbol.get(code, []))

    fake = types.ModuleType("aif10_scraper")
    fake.fetch_all_pages = fetch_all_pages
    fake.default_client = object()
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake)


def test_sync_holders_aif10_actually_runs_and_writes(monkeypatch):
    rows = [
        _raw("600000.SH", "600000", "2024-12-31", "股东甲", 1, 1000, "新进"),
        _raw("600000.SH", "600000", "2024-12-31", "股东乙", 2, 900, "不变"),
    ]
    _fake_scraper(monkeypatch, {"600000": rows})
    conn = duckdb.connect(":memory:")
    try:
        out = sync_holders_aif10(conn, symbols=["600000"])
    finally:
        conn.close()

    assert out["errors"] == [], out["errors"]
    assert out["ok"] == 1 and out["fail"] == 0
    assert out["rows_written"] > 0, "报告成功却没写入任何行"


def test_sync_holders_aif10_per_stock_landing_writes_stay_linear(monkeypatch):
    from services.holders_aif10 import CANONICAL_TABLE as _CT
    from services.data_sources.holders_top10_schema import LANDING_TABLE

    notice = "2025-04-20"
    stocks = [f"60000{i}" for i in range(6)]
    _fake_scraper(
        monkeypatch,
        {
            s: [_raw(f"{s}.SH", s, "2024-12-31", f"股东{s}", 1, 1000, "新进", upd=notice)]
            for s in stocks
        },
    )

    conn = duckdb.connect(":memory:")
    try:
        sync_holders_aif10(conn, symbols=stocks)
        landing = conn.execute(f"SELECT COUNT(*) FROM {LANDING_TABLE}").fetchone()[0]
        canonical = conn.execute(f"SELECT COUNT(*) FROM {_CT}").fetchone()[0]
        codes = conn.execute(
            f"SELECT COUNT(DISTINCT stock_code) FROM {_CT}"
        ).fetchone()[0]
    finally:
        conn.close()

    n = len(stocks)
    quadratic = n * (n + 1) // 2
    assert landing == n, (
        f"落地写入 {landing} 行, 线性应为 {n} 行; 整分区替换会写 {quadratic} 行 —— "
        "delete_scope 退回了 partition"
    )
    assert canonical == n and codes == n, (canonical, codes)


def test_per_stock_path_does_not_touch_canonical_exit_derivation(monkeypatch):
    def _must_not_be_called(*_a, **_k):
        raise AssertionError("按股路径不该调 _derive_exits_against_canonical")

    monkeypatch.setattr(
        "services.holders_aif10._derive_exits_against_canonical", _must_not_be_called
    )
    rows = [
        _raw("600000.SH", "600000", "2024-12-31", "甲", 1, 1000, "新进", holder_code="C1"),
        _raw("600000.SH", "600000", "2025-06-30", "乙", 1, 900, "新进", holder_code="C2"),
    ]
    _fake_scraper(monkeypatch, {"600000": rows})
    conn = duckdb.connect(":memory:")
    try:
        out = sync_holders_aif10(conn, symbols=["600000"])
    finally:
        conn.close()
    assert out["errors"] == [], out["errors"]
    assert out["ok"] == 1
    assert out["exit_rows"] > 0
    assert out["exit_derive_skipped"] == 0


def _run_cli(monkeypatch, result):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ingest_holders_aif10",
        Path(__file__).resolve().parents[1] / "scripts" / "ingest_holders_aif10.py",
    )
    cli = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "get_conn", lambda: duckdb.connect(":memory:"))
    monkeypatch.setattr(cli, "sync_holders_aif10", lambda *_a, **_k: result)
    monkeypatch.setattr(sys, "argv", ["ingest_holders_aif10.py", "--symbols", "600000"])
    return cli.main()


def test_cli_returns_nonzero_when_every_stock_failed(monkeypatch):
    rc = _run_cli(
        monkeypatch,
        {"ok": 0, "fail": 5447, "rows_written": 0, "errors": ["600000: NameError: ..."]},
    )
    assert rc == 1


def test_cli_returns_nonzero_when_success_count_and_rows_disagree(monkeypatch):
    rc = _run_cli(monkeypatch, {"ok": 100, "fail": 0, "rows_written": 0, "errors": []})
    assert rc == 1


def test_cli_returns_zero_on_real_success(monkeypatch):
    rc = _run_cli(monkeypatch, {"ok": 100, "fail": 2, "rows_written": 4321, "errors": []})
    assert rc == 0

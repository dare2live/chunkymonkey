"""holders_aif10 服务单测: change 解析 / 清洗 / 退出推导 / K线范围过滤.

fixture 用真实 aif10 RPT_F10_EH_FREEHOLDERS 字段形态 (mythos §12: 防字段方向反).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402
import pytest  # noqa: E402

from services.holders_aif10 import (  # noqa: E402
    _parse_change,
    _share_class,
    _clean,
    _derive_exits,
    _derive_exits_against_canonical,
    _dedupe_notice_rows_by_grain,
    _local_stock_codes_for_notice_date,
    _net_new_notice_since,
    _write,
    catchup_missing_holders_notice_partitions,
    fetch_holders_top10_by_notice_date,
    formal_holders_watermark,
    land_holders_notice_partitions_forward,
    list_missing_notice_partitions_from_fact,
    sync_holders_aif10_incremental,
    DEFAULT_START_PERIOD,
    HoldersDuplicateGrainConflictError,
    UnknownHolderChangeStatusError,
)


def _raw(secu, code, end_date, name, rank, hold_num, change, ratio=1.0, stype="A股",
         upd="2026-06-13", holder_code=None):
    """真实形态 aif10 行."""
    row = {
        "SECUCODE": secu, "SECURITY_CODE": code, "SECURITY_NAME_ABBR": "测试股",
        "END_DATE": f"{end_date} 00:00:00", "HOLDER_NAME": name, "HOLDER_RANK": rank,
        "HOLD_NUM": hold_num, "HOLD_RATIO": ratio, "HOLD_NUM_CHANGE": change,
        "SHARES_TYPE": stype, "HOLDER_TYPE": "其它", "UPDATE_DATE": f"{upd} 00:00:00",
    }
    if holder_code is not None:
        row["HOLDER_CODE"] = holder_code
    return row


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
    # PIT 可用日锚: 披露日存在 → availability_source='page_update_date' (event_engine 据此算可用日)
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


# ── _derive_exits: period-diff ───────────────────────────────────────
def test_derive_exits_period_diff():
    """上期在榜/本期不在 = 退出; 跟踪机构投资周期."""
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "A机构", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-03-31", "B机构", 2, 90, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "A机构", 1, 100, "不变"),  # A 留, B 退出
    ], start_period=DEFAULT_START_PERIOD)
    exits = _derive_exits(base)
    assert len(exits) == 1
    e = exits[0]
    assert e["holder_name"] == "B机构"
    assert e["report_date"] == "20260608"        # 退出登记在本期
    assert e["is_exit_row"] is True
    assert e["change_status"] == "退出"
    assert e["change_shares_approx"] == -90        # 清掉上期持仓


def test_derive_exits_no_exit_when_stable():
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "A机构", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "A机构", 1, 100, "不变"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


# ── _derive_exits: 身份键 holder_new = COALESCE(HOLDER_CODE, HOLDER_NAME) ────
def test_derive_exits_identity_same_code_different_name_is_not_exit():
    """同一 HOLDER_CODE 改名(如 国泰君安→国泰海通) 不是退出, 是同一人换了写法."""
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "国泰君安", 1, 100, "不变",
             holder_code="ORG001"),
        _raw("600388.SH", "600388", "2026-06-08", "国泰海通", 1, 100, "不变",
             holder_code="ORG001"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


def test_derive_exits_identity_same_name_different_code_is_exit():
    """同名不同码是两个不同主体; 上一个真退出了, 不能被同名巧合掩盖."""
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
    """个人股东没有 HOLDER_CODE, 按 holder_name 判同一人 (既有行为不变)."""
    base = _clean([
        _raw("600388.SH", "600388", "2026-03-31", "李四", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-08", "李四", 1, 100, "不变"),
    ], start_period=DEFAULT_START_PERIOD)
    assert _derive_exits(base) == []


# ── 0r.5b: formal holders watermark + split ops counters ─────────────
def _wm_fixture():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    return con


def test_formal_watermark_prefers_canonical_notice_frontier():
    con = _wm_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
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
    # Full-history rewrite would touch 20260331 too; net-new only counts > wm.
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600519','20260630','20260722','机构甲',FALSE),"
        "('600519','20260630','20260721','机构乙',FALSE),"
        "('600519','20260331','20260425','机构甲',FALSE)"
    )
    net_rows, parts = _net_new_notice_since(con, "20260717")
    assert net_rows == 2          # 20260722 + 20260721 rows only
    assert parts == 2             # two distinct notice partitions


def test_fetch_holders_top10_by_notice_date_maps_provider_shape(monkeypatch):
    """E0 formal acquire: full-market by UPDATE_DATE → provider land rows."""

    import types

    captured: dict = {}

    def fake_fetch_all_pages(report, **kwargs):
        captured["report"] = report
        captured["filters"] = list(kwargs.get("extra_filters") or [])
        return [
            _raw(
                "600388.SH",
                "600388",
                "2026-03-31",
                "A机构",
                1,
                100,
                "不变",
                upd="2026-07-17",
            ),
            # Wrong-day row must be dropped after clean.
            _raw(
                "000001.SZ",
                "000001",
                "2026-03-31",
                "B机构",
                1,
                50,
                "不变",
                upd="2026-07-16",
            ),
        ]

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.fetch_all_pages = fake_fetch_all_pages
    fake_mod.default_client = object()
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)

    rows = fetch_holders_top10_by_notice_date("20260717")
    assert captured["report"] == "RPT_F10_EH_FREEHOLDERS"
    assert captured["filters"] == ["(UPDATE_DATE='2026-07-17')"]
    assert len(rows) == 1
    assert rows[0]["stock_code"] == "600388"
    assert rows[0]["notice_date"] == "20260717"
    assert rows[0]["is_exit_row"] is False


def test_fetch_holders_top10_by_notice_date_dedupes_paged_duplicates(monkeypatch):
    """翻页不稳导致的逐字重复被折叠, 不重复落地; 不同股/不同持有人不受影响."""
    import types

    def fake_fetch_all_pages(report, **kwargs):
        del report, kwargs
        return [
            _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, "不变",
                 upd="2026-07-22"),
            # 同一条记录跨页重复拉到 (分页边界随机)
            _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, "不变",
                 upd="2026-07-22"),
            _raw("600388.SH", "600388", "2026-06-30", "机构乙", 2, 50, "不变",
                 upd="2026-07-22"),
        ]

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.fetch_all_pages = fake_fetch_all_pages
    fake_mod.default_client = object()
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)

    rows = fetch_holders_top10_by_notice_date("20260722")
    assert len(rows) == 2
    assert {r["holder_name"] for r in rows} == {"机构甲", "机构乙"}


# ── _dedupe_notice_rows_by_grain: 幂等去重 + 观测 + 矛盾拒绝 ─────────────
def test_dedupe_notice_rows_by_grain_collapses_verbatim_duplicates():
    rows = _clean([
        _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, "不变"),  # 逐字重复
        _raw("600388.SH", "600388", "2026-06-30", "机构乙", 2, 50, "不变"),
    ], start_period=DEFAULT_START_PERIOD)
    deduped, removed = _dedupe_notice_rows_by_grain(rows)
    assert removed == 1
    assert len(deduped) == 2
    assert {r["holder_name"] for r in deduped} == {"机构甲", "机构乙"}
    # 幂等: 对已去重的结果再跑一遍不应再丢行
    again, removed_again = _dedupe_notice_rows_by_grain(deduped)
    assert removed_again == 0
    assert len(again) == 2


def test_dedupe_notice_rows_by_grain_raises_on_conflicting_content():
    """同 GRAIN 两行内容不同(非逐字重复) → 数据矛盾, 抛错不许任选一行."""
    rows = _clean([
        _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, 20000, ratio=10.0),
        _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 200, -5000, ratio=12.0),
    ], start_period=DEFAULT_START_PERIOD)
    with pytest.raises(HoldersDuplicateGrainConflictError):
        _dedupe_notice_rows_by_grain(rows)


def test_dedupe_notice_rows_by_grain_keeps_legitimate_same_rank_tie():
    """assign_unique_holders_row_seq docstring 自陈的合法现象: 同 rank 两个不同
    持有人不能被当成"重复冲突"炸掉 (去重键含 holder_name, 天然不会撞上这种情况)."""
    rows = _clean([
        _raw("600388.SH", "600388", "2026-06-30", "机构甲", 1, 100, "不变"),
        _raw("600388.SH", "600388", "2026-06-30", "机构乙", 1, 80, "不变"),  # 同 rank 不同人
    ], start_period=DEFAULT_START_PERIOD)
    deduped, removed = _dedupe_notice_rows_by_grain(rows)
    assert removed == 0
    assert len(deduped) == 2


# ── _derive_exits_against_canonical: 日更单日落地退出派生 ────────────────
def _canonical_holders_fixture():
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    return con


def test_derive_exits_against_canonical_finds_gone_holder():
    con = _canonical_holders_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600388','20260331','20260425','A机构',FALSE),"
        "('600388','20260331','20260425','B机构',FALSE)"
    )
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630",
        "notice_date": "20260722", "holder_name": "A机构", "is_exit_row": False,
    }]
    exits = _derive_exits_against_canonical(con, new_rows)
    assert len(exits) == 1
    assert exits[0]["holder_name"] == "B机构"
    assert exits[0]["is_exit_row"] is True
    assert exits[0]["report_date"] == "20260630"
    assert exits[0]["change_status"] == "退出"


def test_derive_exits_against_canonical_no_prior_period_no_op():
    """该股 canonical 里没有更早的期 (首次落地) → 没有基准可 diff, 不产生退出行."""
    con = _canonical_holders_fixture()
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630",
        "notice_date": "20260722", "holder_name": "A机构", "is_exit_row": False,
    }]
    assert _derive_exits_against_canonical(con, new_rows) == []


def test_derive_exits_against_canonical_skips_when_caller_already_derived():
    """批次里这 (stock, report_date) 已经自带退出行 (全量按股重跑路径) → 不重算/不冲突."""
    con = _canonical_holders_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600388','20260331','20260425','A机构',FALSE),"
        "('600388','20260331','20260425','B机构',FALSE)"
    )
    rows = [
        {"stock_code": "600388", "report_date": "20260630", "notice_date": "20260722",
         "holder_name": "A机构", "is_exit_row": False},
        {"stock_code": "600388", "report_date": "20260630", "notice_date": "20260722",
         "holder_name": "B机构", "is_exit_row": True},  # 调用方已经自己算过了
    ]
    assert _derive_exits_against_canonical(con, rows) == []


def test_write_merges_canonical_derived_exits(monkeypatch):
    """_write 是所有落地路径的收口点: 日更批次没带退出行时在这里补上."""
    con = _canonical_holders_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600388','20260331','20260425','A机构',FALSE),"
        "('600388','20260331','20260425','B机构',FALSE)"
    )
    captured: dict = {}

    class _FakeOutcome:
        def __init__(self, n):
            self.canonical_rows = n

    def fake_write_formal(_conn, rows, **_k):
        rows = list(rows)
        captured["rows"] = rows
        return _FakeOutcome(len(rows))

    monkeypatch.setattr(
        "services.data_sources.disclosure_dual_write.write_holders_top10_formal_then_mirror",
        fake_write_formal,
    )
    new_rows = [{
        "stock_code": "600388", "report_date": "20260630", "notice_date": "20260722",
        "holder_name": "A机构", "is_exit_row": False,
    }]
    written = _write(con, new_rows)
    assert written == 2
    names = {r["holder_name"] for r in captured["rows"]}
    assert names == {"A机构", "B机构"}
    exit_rows = [r for r in captured["rows"] if r.get("is_exit_row")]
    assert len(exit_rows) == 1
    assert exit_rows[0]["holder_name"] == "B机构"


def _patch_aif10_probe(monkeypatch, update_date: str) -> None:
    """Newest-date probe only. Daily incremental must not page UPDATE_DATE>=."""
    import types

    class _Client:
        def get_v1(self, *_a, **kwargs):
            filt = str(kwargs.get("filter_expr") or "")
            assert "UPDATE_DATE>=" in filt
            assert kwargs.get("page_size") == 1
            return {"data": [{"UPDATE_DATE": f"{update_date} 00:00:00"}], "pages": 1}

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.default_client = _Client()
    fake_mod.fetch_all_pages = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("daily incremental must not fetch_all_pages by ts_code")
    )
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)


def test_incremental_skips_when_provider_strictly_behind_wm(monkeypatch):
    """Re-click must not rewrite when provider max is strictly behind formal wm."""
    _patch_aif10_probe(monkeypatch, "2026-07-21")
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not per-stock")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "watermark_unchanged"
    assert out["rows_written"] == 0
    assert out["rewrite_amplification_rows"] == 0
    assert out["provider_max_update_date"] == "20260721"


def test_incremental_same_day_skips_when_local_coverage_complete(monkeypatch):
    """Equal wm: exact-day by_notice codes; skip when none missing locally."""
    _patch_aif10_probe(monkeypatch, "2026-07-22")
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10.fetch_holders_top10_by_notice_date",
        lambda nd: [
            {"stock_code": "600346", "notice_date": nd},
            {"stock_code": "688116", "notice_date": nd},
        ],
    )
    monkeypatch.setattr(
        "services.holders_aif10._local_stock_codes_for_notice_date",
        lambda _conn, notice: {"600346", "688116"}
        if notice == "20260722"
        else set(),
    )
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not per-stock")),
    )
    monkeypatch.setattr(
        "services.holders_aif10._write",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not land")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "same_day_coverage_complete"
    assert out["same_day_missing_codes"] == 0
    assert out["same_day_provider_codes"] == 2
    assert out["rows_written"] == 0


def test_incremental_same_day_sparse_relends_notice_day_once(monkeypatch):
    """Equal wm late filers: re-land that notice_date once; never per-stock full history."""
    _patch_aif10_probe(monkeypatch, "2026-07-23")
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260723", "test_wm"),
    )
    day_rows = [
        {"stock_code": "600346", "notice_date": "20260723"},
        {"stock_code": "603659", "notice_date": "20260723"},
        {"stock_code": "688116", "notice_date": "20260723"},
    ]
    monkeypatch.setattr(
        "services.holders_aif10.fetch_holders_top10_by_notice_date",
        lambda nd: day_rows if nd == "20260723" else [],
    )
    monkeypatch.setattr(
        "services.holders_aif10._local_stock_codes_for_notice_date",
        lambda _conn, notice: {"600346", "688116"}
        if notice == "20260723"
        else set(),
    )
    written: list[list] = []

    def fake_write(_conn, rows):
        written.append(list(rows))
        return len(rows)

    monkeypatch.setattr("services.holders_aif10._write", fake_write)
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not per-stock")),
    )
    monkeypatch.setattr(
        "services.holders_aif10._net_new_notice_since",
        lambda _conn, _wm: (3, 1),
    )
    out = sync_holders_aif10_incremental(object())
    assert out.get("skipped") is not True
    assert out["same_day_sparse"] is True
    assert out["same_day_missing_codes"] == 1
    assert out["same_day_provider_codes"] == 3
    assert out["affected_stocks"] == 0
    assert out["rewrite_amplification_rows"] == 0
    assert out["rows_written"] == 3
    assert written == [day_rows]
    assert out["provider_max_update_date"] == "20260723"


def test_incremental_advance_window_is_by_notice_only(monkeypatch):
    """Provider ahead: forward by_notice partitions; never UPDATE_DATE>= per-stock rewrite."""
    _patch_aif10_probe(monkeypatch, "2026-07-26")
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260721", "test_wm"),
    )
    captured: dict = {}

    def fake_forward(_conn, *, from_exclusive, to_inclusive, **_k):
        captured["from_exclusive"] = from_exclusive
        captured["to_inclusive"] = to_inclusive
        return {
            "landed_partitions": ["20260722", "20260724"],
            "empty_partitions": ["20260723"],
            "errors": [],
        }

    monkeypatch.setattr(
        "services.holders_aif10.land_holders_notice_partitions_forward",
        fake_forward,
    )
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not per-stock")),
    )
    monkeypatch.setattr(
        "services.holders_aif10._net_new_notice_since",
        lambda _conn, _wm: (18, 2),
    )
    out = sync_holders_aif10_incremental(object())
    assert out.get("skipped") is not True
    assert captured == {"from_exclusive": "20260721", "to_inclusive": "20260726"}
    assert out["notice_partition_forward"]["landed_partitions"] == [
        "20260722",
        "20260724",
    ]
    assert out["affected_stocks"] == 0
    assert out["rewrite_amplification_rows"] == 0
    assert out["net_new_notice_rows"] == 18
    assert out["notice_partitions_touched"] == 2


def test_local_stock_codes_for_notice_date_reads_canonical():
    con = _wm_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600346','20260630','20260723','机构甲',FALSE),"
        "('600346','20260630','20260723','机构乙',FALSE),"
        "('688116','20260630','20260722','机构丙',FALSE)"
    )
    assert _local_stock_codes_for_notice_date(con, "20260723") == {"600346"}
    assert _local_stock_codes_for_notice_date(con, "20260722") == {"688116"}
    assert _local_stock_codes_for_notice_date(con, "20260721") == set()


def _notice_hole_fixture():
    """Canonical-only fixture for forward-fill tests (fact plane retired)."""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    return con


def test_list_missing_notice_partitions_from_fact_retired():
    """From-fact hole ledger retired with fact DROP."""
    con = _notice_hole_fixture()
    missing = list_missing_notice_partitions_from_fact(con, limit=40)
    assert missing == []


def test_catchup_from_fact_retired(monkeypatch):
    con = _notice_hole_fixture()
    out = catchup_missing_holders_notice_partitions(con)
    assert out["repaired_partitions"] == []
    assert out["retired"] is True
    assert out["catchup_source"] == "retired_local_fact_notice"


def test_forward_by_notice_lands_absent_days_only(monkeypatch):
    """Provider ahead: holdernumber-class by_notice, skip days already in canonical."""
    con = _notice_hole_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600519','20260331','20260722','机构甲',FALSE)"
    )
    fetched: list[str] = []
    written: list[str] = []

    def fake_fetch(nd: str):
        fetched.append(nd)
        if nd == "20260723":
            return [{
                "stock_code": "600388",
                "report_date": "20260608",
                "notice_date": nd,
                "holder_name": "紫金",
            }]
        return []

    def fake_write(_conn, rows):
        written.append(rows[0]["notice_date"])
        return len(rows)

    monkeypatch.setattr(
        "services.holders_aif10.fetch_holders_top10_by_notice_date", fake_fetch
    )
    monkeypatch.setattr("services.holders_aif10._write", fake_write)
    out = land_holders_notice_partitions_forward(
        con, from_exclusive="20260722", to_inclusive="20260724"
    )
    # 20260722 already canonical → not fetched; 23 lands; 24 empty
    assert "20260722" not in fetched
    assert fetched == ["20260723", "20260724"]
    assert out["landed_partitions"] == ["20260723"]
    assert out["empty_partitions"] == ["20260724"]
    assert written == ["20260723"]


def test_incremental_skip_does_not_run_retired_fact_catchup(monkeypatch):
    """watermark_unchanged skips; from-fact catchup no longer attached."""
    _patch_aif10_probe(monkeypatch, "2026-07-21")
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not per-stock")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "watermark_unchanged"
    assert "notice_partition_catchup" not in out

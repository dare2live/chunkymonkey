"""holders_aif10 服务单测: change 解析 / 清洗 / 退出推导 / K线范围过滤.

fixture 用真实 aif10 RPT_F10_EH_FREEHOLDERS 字段形态 (mythos §12: 防字段方向反).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from services.holders_aif10 import (  # noqa: E402
    _parse_change,
    _share_class,
    _clean,
    _derive_exits,
    _local_stock_codes_for_notice_date,
    _net_new_notice_since,
    catchup_missing_holders_notice_partitions,
    fetch_holders_top10_by_notice_date,
    formal_holders_watermark,
    land_holders_notice_partitions_forward,
    list_missing_notice_partitions_from_fact,
    sync_holders_aif10_incremental,
    DEFAULT_START_PERIOD,
)


def _raw(secu, code, end_date, name, rank, hold_num, change, ratio=1.0, stype="A股", upd="2026-06-13"):
    """真实形态 aif10 行."""
    return {
        "SECUCODE": secu, "SECURITY_CODE": code, "SECURITY_NAME_ABBR": "测试股",
        "END_DATE": f"{end_date} 00:00:00", "HOLDER_NAME": name, "HOLDER_RANK": rank,
        "HOLD_NUM": hold_num, "HOLD_RATIO": ratio, "HOLD_NUM_CHANGE": change,
        "SHARES_TYPE": stype, "HOLDER_TYPE": "其它", "UPDATE_DATE": f"{upd} 00:00:00",
    }


# ── _parse_change: HOLD_NUM_CHANGE 多态 ──────────────────────────────
def test_parse_change_polymorphic():
    assert _parse_change("新进") == ("新进", None)
    assert _parse_change("不变") == ("不变", 0)
    assert _parse_change(5281895) == ("增持", 5281895)      # 正数 = 增持
    assert _parse_change(-697100) == ("减持", -697100)      # 负数 = 减持
    assert _parse_change("5281895") == ("增持", 5281895)    # 字符串数字
    assert _parse_change(None) == ("未知", None)


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
    con.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code VARCHAR, report_date VARCHAR, page_update_date VARCHAR,
            source VARCHAR
        )
        """
    )
    return con


def test_formal_watermark_prefers_canonical_notice_frontier():
    con = _wm_fixture()
    # canonical is ahead (notice 20260722); legacy fact lags (20260717).
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600519','20260630','20260722','机构甲',FALSE),"
        "('600519','20260331','20260425','机构甲',FALSE)"
    )
    con.execute(
        "INSERT INTO fact_top10_holder_period VALUES "
        "('600519','20260331','20260717','miaoxiang')"
    )
    wm, src = formal_holders_watermark(con)
    assert wm == "20260722"
    assert src == "canonical_notice_frontier"


def test_formal_watermark_falls_back_to_legacy_when_no_canonical():
    con = _wm_fixture()
    con.execute(
        "INSERT INTO fact_top10_holder_period VALUES "
        "('600519','20260331','20260717','miaoxiang')"
    )
    wm, src = formal_holders_watermark(con)
    assert wm == "20260717"
    assert src == "legacy_fact_page_update_date"


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


def test_incremental_skips_when_provider_strictly_behind_wm(monkeypatch):
    """Re-click must not rewrite when provider max is strictly behind formal wm."""
    import types

    class _Client:
        def get_v1(self, *_a, **kwargs):
            # Empty filter must not be used (vendor returns 0); accept bounded probe.
            assert "UPDATE_DATE>=" in str(kwargs.get("filter_expr") or "")
            return {"data": [{"UPDATE_DATE": "2026-07-21 00:00:00"}], "pages": 1}

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.default_client = _Client()
    fake_mod.fetch_all_pages = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10._affected_stocks_since",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must skip before scan")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "watermark_unchanged"
    assert out["rows_written"] == 0
    assert out["provider_max_update_date"] == "20260721"


def test_incremental_same_day_skips_when_local_coverage_complete(monkeypatch):
    """Equal wm: probe same-day codes; skip only when none missing locally."""
    import types

    class _Client:
        def get_v1(self, *_a, **kwargs):
            assert "UPDATE_DATE>=" in str(kwargs.get("filter_expr") or "")
            return {"data": [{"UPDATE_DATE": "2026-07-22 00:00:00"}], "pages": 1}

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.default_client = _Client()
    fake_mod.fetch_all_pages = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10._affected_stocks_since",
        lambda _client, since: ["600346", "688116"]
        if since == "2026-07-22"
        else (_ for _ in ()).throw(AssertionError(f"unexpected since={since}")),
    )
    monkeypatch.setattr(
        "services.holders_aif10._local_stock_codes_for_notice_date",
        lambda _conn, notice: {"600346", "688116"}
        if notice == "20260722"
        else set(),
    )
    monkeypatch.setattr(
        "services.holders_aif10.sync_holders_aif10",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not sync")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "same_day_coverage_complete"
    assert out["same_day_missing_codes"] == 0
    assert out["same_day_provider_codes"] == 2
    assert out["rows_written"] == 0


def test_incremental_same_day_sparse_syncs_missing_codes_only(monkeypatch):
    """Equal wm late filers: sync only codes missing that notice locally (sparse)."""
    import types

    class _Client:
        def get_v1(self, *_a, **kwargs):
            assert "UPDATE_DATE>=" in str(kwargs.get("filter_expr") or "")
            return {"data": [{"UPDATE_DATE": "2026-07-23 00:00:00"}], "pages": 1}

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.default_client = _Client()
    fake_mod.fetch_all_pages = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260723", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10._affected_stocks_since",
        lambda _client, since: ["600346", "603659", "688116"]
        if since == "2026-07-23"
        else (_ for _ in ()).throw(AssertionError(f"unexpected since={since}")),
    )
    monkeypatch.setattr(
        "services.holders_aif10._local_stock_codes_for_notice_date",
        lambda _conn, notice: {"600346", "688116"}
        if notice == "20260723"
        else set(),
    )
    captured: dict = {}

    def fake_sync(_conn, *, symbols=None, **_k):
        captured["symbols"] = list(symbols or [])
        return {
            "ok": 1,
            "fail": 0,
            "rows_written": 12,
            "exit_rows": 0,
            "errors": [],
        }

    monkeypatch.setattr("services.holders_aif10.sync_holders_aif10", fake_sync)
    monkeypatch.setattr(
        "services.holders_aif10._net_new_notice_since",
        lambda _conn, _wm: (0, 0),
    )
    out = sync_holders_aif10_incremental(object())
    assert out.get("skipped") is not True
    assert out["same_day_sparse"] is True
    assert out["same_day_missing_codes"] == 1
    assert out["affected_stocks"] == 1
    assert captured["symbols"] == ["603659"]
    assert out["rows_written"] == 12
    assert out["provider_max_update_date"] == "20260723"


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
    """Canonical + fact with notice_date for behind-wm hole ledger tests."""
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE canonical_top10_float_holders_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            holder_name VARCHAR, is_exit_row BOOLEAN
        )
        """
    )
    con.execute(
        """
        CREATE TABLE fact_top10_holder_period (
            stock_code VARCHAR, report_date VARCHAR, notice_date VARCHAR,
            page_update_date VARCHAR, source VARCHAR,
            holder_set VARCHAR, holder_rank INTEGER, row_seq INTEGER,
            holder_name VARCHAR, hold_ratio_float DOUBLE, is_exit_row BOOLEAN,
            holder_name_norm VARCHAR, share_class VARCHAR, shares_approx DOUBLE,
            change_status VARCHAR, hold_change_num DOUBLE, holder_type VARCHAR
        )
        """
    )
    return con


def test_list_missing_notice_partitions_from_fact_newest_first():
    """Mid-period fact notices absent from canonical are the hole ledger."""
    con = _notice_hole_fixture()
    con.execute(
        "INSERT INTO canonical_top10_float_holders_period VALUES "
        "('600519','20260331','20260724','机构甲',FALSE)"
    )
    con.execute(
        "INSERT INTO fact_top10_holder_period VALUES "
        "('600388','20260608','20260613','20260613','miaoxiang',"
        " 'free',1,1,'紫金',21.0,FALSE,'紫金','A',1,'不变',0,'其它'),"
        "('002725','20260629','20260701','20260701','miaoxiang',"
        " 'free',1,1,'甲',1.0,FALSE,'甲','A',1,'不变',0,'其它'),"
        "('600519','20260331','20260724','20260724','miaoxiang',"
        " 'free',1,1,'乙',1.0,FALSE,'乙','A',1,'不变',0,'其它')"
    )
    missing = list_missing_notice_partitions_from_fact(con, limit=40)
    assert missing == ["20260701", "20260613"]


def test_catchup_accepts_only_missing_fact_partitions(monkeypatch):
    con = _notice_hole_fixture()
    con.execute(
        "INSERT INTO fact_top10_holder_period VALUES "
        "('600388','20260608','20260613','20260613','miaoxiang',"
        " 'free',1,1,'紫金',21.0,FALSE,'紫金','A',1,'不变',0,'其它')"
    )
    called: list[str] = []

    def fake_accept(_conn, notice_date: str):
        called.append(notice_date)
        return {"status": "ACCEPTED"}

    monkeypatch.setattr(
        "services.holders_aif10.accept_holders_top10_partition_from_legacy",
        fake_accept,
    )
    out = catchup_missing_holders_notice_partitions(con)
    assert out["repaired_partitions"] == ["20260613"]
    assert called == ["20260613"]
    assert out["catchup_source"] == "local_fact_notice"


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


def test_incremental_skip_still_runs_notice_hole_catchup(monkeypatch):
    """Even watermark_unchanged must repair behind-wm fact holes."""
    import types

    class _Client:
        def get_v1(self, *_a, **kwargs):
            return {"data": [{"UPDATE_DATE": "2026-07-21 00:00:00"}], "pages": 1}

    fake_mod = types.ModuleType("aif10_scraper")
    fake_mod.default_client = _Client()
    fake_mod.fetch_all_pages = lambda *_a, **_k: []
    monkeypatch.setitem(sys.modules, "aif10_scraper", fake_mod)
    monkeypatch.setattr(
        "services.holders_aif10.formal_holders_watermark",
        lambda _conn: ("20260722", "test_wm"),
    )
    monkeypatch.setattr(
        "services.holders_aif10.catchup_missing_holders_notice_partitions",
        lambda _conn, **_k: {
            "missing_partitions": ["20260613"],
            "repaired_partitions": ["20260613"],
            "errors": [],
            "catchup_source": "local_fact_notice",
        },
    )
    monkeypatch.setattr(
        "services.holders_aif10._affected_stocks_since",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must skip before scan")),
    )
    out = sync_holders_aif10_incremental(object())
    assert out["skipped"] is True
    assert out["skip_reason"] == "watermark_unchanged"
    assert out["notice_partition_catchup"]["repaired_partitions"] == ["20260613"]

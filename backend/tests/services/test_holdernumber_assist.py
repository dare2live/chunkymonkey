"""holder_number DataAccess + concentration assist (fail-closed)."""
from __future__ import annotations

from conftest import duck_mem

from services.data_access import DataAccess
from services.holdernumber_assist import _yyyymmdd, load_holdernumber_assist
from services.serve_read_views import ensure_serve_read_views


def test_holder_number_entity_pit():
    c = duck_mem()
    c.executescript(
        "CREATE TABLE raw_tushare_stk_holdernumber ("
        " ts_code TEXT, ann_date TEXT, end_date TEXT, holder_num VARCHAR);"
    )
    c.executemany(
        "INSERT INTO raw_tushare_stk_holdernumber VALUES (?,?,?,?)",
        [
            ("600519.SH", "20250328", "20250331", "200000"),
            ("600519.SH", "20250730", "20250630", "180000"),
            ("600519.SH", "20260430", "20260331", "170000"),
        ],
    )
    ensure_serve_read_views(c)
    res = DataAccess().get(
        "holder_number", codes=["600519"], as_of="2025-07-31", conn=c
    )
    ends = {_yyyymmdd(r["end_date"]) for r in res.rows}
    assert "20260331" not in ends
    assert "20250331" in ends and "20250630" in ends


def test_assist_concentration_with_stub_da():
    class _Stub:
        def get(self, entity, codes=None, start=None, as_of=None, conn=None):
            from services.data_access import DataResult

            if entity == "holder_number":
                return DataResult(
                    rows=[
                        {
                            "ts_code": "600519",
                            "ann_date": "2025-03-28",
                            "end_date": "2025-03-31",
                            "holder_num": 200000,
                        },
                        {
                            "ts_code": "600519",
                            "ann_date": "2025-04-30",
                            "end_date": "2025-03-31",
                            "holder_num": 195000,
                        },
                        {
                            "ts_code": "600519",
                            "ann_date": "2025-07-30",
                            "end_date": "2025-06-30",
                            "holder_num": 180000,
                        },
                    ],
                    provenance={"source_entity": "holder_number"},
                )
            if entity == "kline_qfq":
                return DataResult(
                    rows=[
                        {"code": "600519", "date": "2025-04-30", "close": 100.0},
                        {"code": "600519", "date": "2025-07-30", "close": 110.0},
                    ],
                    provenance={"source_entity": "kline_qfq"},
                )
            raise ValueError(entity)

    out = load_holdernumber_assist("600519", as_of="20250731", da=_Stub())
    assert out["status"] == "ok"
    assert out["latest"]["holder_num"] == 180000
    assert out["latest"]["end_date"] == "20250630"
    # revision on same end_date kept 195000 not 200000
    assert out["series"][0]["holder_num"] == 195000
    assert out["concentration"]["direction"] == "concentrating"
    assert out["vs_price"]["status"] == "ok"
    assert abs(out["vs_price"]["price_chg_pct"] - 0.1) < 1e-9


def test_assist_fail_closed_empty():
    class _Stub:
        def get(self, entity, codes=None, start=None, as_of=None, conn=None):
            from services.data_access import DataResult

            return DataResult(rows=[], provenance={"source_entity": entity})

    out = load_holdernumber_assist("600519", as_of="20250731", da=_Stub())
    assert out["status"] == "empty"
    assert out["reason"] == "holder_number_empty"
    assert out["concentration"]["direction"] == "unknown"
    assert out["vs_price"]["status"] == "unavailable"


def test_registry_entity_and_sync_axis():
    from services.data_access.spec import load_registry as load_access
    from services.data_sources.sync_runner import load_registry as load_sync

    ent = load_access().entity("holder_number")
    assert ent.table == "v_holder_number_period"
    assert ent.asof_col == "ann_date"
    sync = load_sync()["domains"]["stk_holdernumber"]
    assert sync["batch_mode"] == "by_ann_date"
    assert sync["date_param"] == "ann_date"

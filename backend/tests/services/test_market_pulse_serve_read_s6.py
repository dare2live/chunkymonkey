"""S6: pulse drill/members serve-read uses DataAccess entity tables, not router raw."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services import market_pulse as mp
from services import market_pulse_serve_read as serve
from services.data_access.spec import load_registry
from test_market_pulse import CFG, D, _fixture_conn


def test_serve_read_entities_resolve_physical_tables():
    reg = load_registry()
    for name in (
        "dc_member",
        "moneyflow",
        "moneyflow_dc",
        "limit_list_d",
        "daily",
        "margin",
    ):
        assert reg.entity(name).db == "tushare_raw"
        assert serve._table(name).startswith("raw_")
    # S7: SW membership publication = PIT view (not raw ssot)
    assert reg.entity("index_member_all").table == "v_sw_industry_pit"
    assert serve._table("index_member_all") == "v_sw_industry_pit"


def test_list_sector_members_dc_and_sw():
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        dc = serve.list_sector_members(c, chain=mp.CHAIN_DC_INDUSTRY, sector_code="BK0001.DC")
        assert dc["as_of"] == D[4]
        assert {m["con_code"] for m in dc["members"]} >= {"600001.SH", "600002.SZ"}
        sw = serve.list_sector_members(c, chain=mp.CHAIN_SW, sector_code="801010.SI")
        assert sw["as_of"] is None
        assert any(m["con_code"] == "600001.SH" for m in sw["members"])
    finally:
        c.close()


def test_drill_leaf_via_serve_read_sw():
    c = _fixture_conn()
    try:
        mp.rebuild_all(conn=c, cfg=CFG)
        rows = serve.drill_leaf_rows(
            c,
            CFG,
            mem_sql=serve.sw_member_mem_sql(),
            mem_params=["850111.SI", "99999999", "99999999"],
            flow_sql=serve.sw_flow_sql("99999999"),
            as_of="99999999",
        )
        by = {r["ts_code"]: r for r in rows}
        assert "600001.SH" in by
        assert by["600001.SH"]["cum_net"] is not None
    finally:
        c.close()

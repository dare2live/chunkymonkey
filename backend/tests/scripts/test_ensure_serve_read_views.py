"""K1 serve-read views: installer + shrinking raw-pointer allowlist."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from conftest import duck_mem

from services.data_access.spec import load_registry
from services.serve_read_views import (
    ColumnMap,
    ViewSpec,
    ensure_serve_read_views,
    load_specs,
    view_ddl,
)

REPO = Path(__file__).resolve().parents[3]

K1_VIEWS = {
    "cyq": ("v_cyq_stock_day", "raw_tushare_cyq_perf"),
    "share_float": ("v_share_float_event", "raw_tushare_share_float"),
    "holder_number": ("v_holder_number_period", "raw_tushare_stk_holdernumber"),
    "block_trade": ("v_block_trade_stock_day", "raw_tushare_block_trade"),
    "report_rc": ("v_report_rc_event", "raw_tushare_report_rc"),
    "fundamentals": ("v_fundamentals_period", "raw_tushare_fina_indicator"),
    "forecast": ("v_forecast_period", "raw_tushare_forecast"),
    "sw_daily": ("v_sw_daily_index_day", "raw_tushare_sw_daily"),
    "index_dailybasic": ("v_index_dailybasic_index_day", "raw_tushare_index_dailybasic"),
    "limit_cpt_list": ("v_limit_cpt_list_board_day", "raw_tushare_limit_cpt_list"),
    "top_list": ("v_top_list_stock_day", "raw_tushare_top_list"),
    "moneyflow_ind_dc": ("v_moneyflow_ind_dc_board_day", "raw_tushare_moneyflow_ind_dc"),
    "moneyflow_mkt_dc": ("v_moneyflow_mkt_dc_market_day", "raw_tushare_moneyflow_mkt_dc"),
    "daily": ("v_daily_stock_day", "raw_tushare_daily"),
    "margin": ("v_margin_exchange_day", "raw_tushare_margin"),
    "dc_index": ("v_dc_index_board_day", "raw_tushare_dc_index"),
    "institution_survey": ("v_institution_survey_event", "raw_tushare_stk_surv"),
}

_FORMAL_INV = (
    "version: 1\n"
    "membership_l0_entities: [dc_member, index_member_all]\n"
    "tables:\n"
    "  raw_tushare_daily:\n"
    "    role: fill\n"
    "    formal_domain: daily\n"
    "    write: forbidden\n"
    "  raw_tushare_stock_st:\n"
    "    role: compatibility\n"
    "    formal_domain: stock_st\n"
    "    write: forbidden\n"
    "  raw_tushare_trade_cal:\n"
    "    role: compatibility\n"
    "    formal_domain: trade_cal\n"
    "    write: forbidden\n"
    "  raw_tushare_margin:\n"
    "    role: compatibility\n"
    "    formal_domain: margin\n"
    "    write: forbidden\n"
)

_FORMAL_SYNC = (
    "domains:\n"
    "  daily: {target_table: raw_tushare_daily}\n"
    "  stock_st: {target_table: raw_tushare_stock_st}\n"
    "  trade_cal: {target_table: raw_tushare_trade_cal}\n"
    "  margin: {target_table: raw_tushare_margin}\n"
)


def _load_legacy_gate():
    path = REPO / "backend" / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gate_rejects_new_raw_pointer_when_allowlist_empty(tmp_path, monkeypatch):
    mod = _load_legacy_gate()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(_FORMAL_INV + "data_access_raw_entity_allowlist: []\n", encoding="utf-8")
    (tmp_path / "sync_registry.yaml").write_text(_FORMAL_SYNC, encoding="utf-8")
    da = tmp_path / "data_access.yaml"
    da.write_text("entities:\n  sneaky:\n    table: raw_foo\n    layer: L0\n", encoding="utf-8")
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", tmp_path / "sync_registry.yaml")
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert any(
        "sneaky" in v and "v_<domain>_<grain>" in v for v in viol
    ), viol


def test_gate_rejects_stale_allowlist_name(tmp_path, monkeypatch):
    mod = _load_legacy_gate()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
        _FORMAL_INV + "data_access_raw_entity_allowlist: [ghost_entity]\n",
        encoding="utf-8",
    )
    (tmp_path / "sync_registry.yaml").write_text(_FORMAL_SYNC, encoding="utf-8")
    da = tmp_path / "data_access.yaml"
    da.write_text("entities: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", tmp_path / "sync_registry.yaml")
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert any("stale" in v and "ghost_entity" in v for v in viol), viol


def test_live_data_access_has_zero_raw_table_entities():
    reg = load_registry()
    raw = {name: ent.table for name, ent in reg.entities.items() if ent.table.startswith("raw_")}
    assert raw == {}, raw
    mod = _load_legacy_gate()
    allow, err = mod.data_access_raw_entity_allowlist()
    assert err is None
    assert allow == set()
    assert mod.data_access_raw_tables() == set()


def test_ensure_serve_read_views_roundtrip_equals_source():
    c = duck_mem()
    c.execute(
        "CREATE TABLE raw_tushare_cyq_perf ("
        " ts_code TEXT, trade_date TEXT, winner_rate DOUBLE,"
        " cost_5pct DOUBLE, cost_50pct DOUBLE, cost_95pct DOUBLE, weight_avg DOUBLE)"
    )
    row = ("600519.SH", "20240102", 0.5, 1.0, 2.0, 3.0, 2.5)
    c.execute("INSERT INTO raw_tushare_cyq_perf VALUES (?,?,?,?,?,?,?)", list(row))
    installed = ensure_serve_read_views(c)
    assert "v_cyq_stock_day" in installed
    src = c.execute(
        "SELECT ts_code, trade_date, winner_rate, cost_5pct, cost_50pct, "
        "cost_95pct, weight_avg FROM raw_tushare_cyq_perf"
    ).fetchall()
    view = c.execute(
        "SELECT ts_code, trade_date, winner_rate, cost_5pct, cost_50pct, "
        "cost_95pct, weight_avg FROM v_cyq_stock_day"
    ).fetchall()
    assert [list(r) for r in view] == [list(r) for r in src]


def test_ensure_serve_read_views_skips_missing_source():
    c = duck_mem()
    assert ensure_serve_read_views(c) == []


def test_view_ddl_maps_source_column_and_never_select_star():
    spec = ViewSpec(
        name="v_foo_stock_day",
        db="tushare_raw",
        source_table="raw_foo",
        entity="foo",
        columns=(
            ColumnMap(out="trade_date", source="date_ms"),
            ColumnMap(out="ts_code", source="ts_code"),
        ),
    )
    ddl = view_ddl(spec)
    assert "SELECT *" not in ddl
    assert '"date_ms" AS "trade_date"' in ddl
    assert '"ts_code"' in ddl
    assert "FROM \"raw_foo\"" in ddl


def test_k1_view_registry_matches_data_access_columns():
    specs = {s.name: s for s in load_specs()}
    assert set(specs) == {view for view, _src in K1_VIEWS.values()}
    reg = load_registry()
    for entity, (view, source) in K1_VIEWS.items():
        spec = specs[view]
        ent = reg.entity(entity)
        assert spec.entity == entity
        assert spec.source_table == source
        assert spec.db == "tushare_raw"
        assert ent.table == view
        assert ent.layer == "L0"
        assert tuple(c.out for c in spec.columns) == ent.columns

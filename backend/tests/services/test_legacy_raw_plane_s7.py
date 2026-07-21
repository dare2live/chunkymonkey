"""S7 legacy raw plane: derive default accepted-only + inventory gate."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from services import derive_runtime as dr
from services import technical_states as ts

REPO = Path(__file__).resolve().parents[3]


def _load_qfq_mod():
    script = REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py"
    spec = importlib.util.spec_from_file_location("build_price_kline_qfq_tushare", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_check_mod():
    path = REPO / "backend" / "scripts" / "check_legacy_raw_plane.py"
    spec = importlib.util.spec_from_file_location("check_legacy_raw_plane", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_s7_derive_runtime_defaults_to_from_accepted() -> None:
    """S7: chunkyctl derive default = accepted-only (no silent legacy fill)."""

    sig = dr.run_derive.__defaults__
    # from_accepted is first kw-only default after target (positional-only via signature)
    assert dr.run_derive.__kwdefaults__["from_accepted"] is True


def test_s7_qfq_default_nominal_excludes_legacy_raw() -> None:
    mod = _load_qfq_mod()
    cte = mod.nominal_source_cte()  # default
    assert "canonical_nominal_ohlcv_daily" in cte
    assert "raw_tushare_daily" not in cte
    assert "UNION ALL" not in cte


def test_s7_qfq_allow_legacy_fill_restores_union() -> None:
    mod = _load_qfq_mod()
    cte = mod.nominal_source_cte(from_accepted=False)
    assert "raw_tushare_daily" in cte
    assert "UNION ALL" in cte


def test_s7_form_library_defaults_to_from_accepted() -> None:
    """S7: technical_states rebuild/build_latest/src_temp_sql default accepted-only."""

    assert ts.src_temp_sql.__kwdefaults__["from_accepted"] is True
    assert ts.rebuild_all.__kwdefaults__["from_accepted"] is True
    assert ts.build_latest.__kwdefaults__["from_accepted"] is True
    sql = ts.src_temp_sql()
    assert "raw_tushare_daily" not in sql
    assert "can.close AS raw_close" in sql
    fill = ts.src_temp_sql(from_accepted=False)
    assert "raw_tushare_daily" in fill


def test_s7_derive_form_path_excludes_legacy_raw_daily(monkeypatch) -> None:
    """S7 derive form default passes from_accepted=True into technical_states."""

    seen: dict[str, bool] = {}

    def _fake_build_latest(*, from_accepted: bool = True, **kwargs):
        seen["from_accepted"] = from_accepted
        return {"mode": "build_latest", "added_days": 0, "rows": 0}

    monkeypatch.setattr(ts, "build_latest", _fake_build_latest)
    out = dr.run_derive("form")
    assert seen["from_accepted"] is True
    assert out["from_accepted"] is True
    sql = ts.src_temp_sql()
    assert "raw_tushare_daily" not in sql
    assert "can.close AS raw_close" in sql


def test_s7_derive_cli_has_allow_legacy_fill() -> None:
    src = (REPO / "backend" / "scripts" / "derive_cli.py").read_text(encoding="utf-8")
    assert "allow-legacy-fill" in src
    assert "from_accepted" in src


def test_s7_pipeline_clean_defaults_from_accepted() -> None:
    """daily_update clean uses accepted-only qfq after daily expand to 20190102."""

    src = (REPO / "backend" / "services" / "pipeline" / "clean.py").read_text(
        encoding="utf-8"
    )
    assert "build_price_kline_qfq_tushare.py" in src
    assert '["--from-accepted"]' in src
    assert '["--allow-legacy-fill"]' not in src


def test_s7_pipeline_process_form_uses_from_accepted() -> None:
    """pipeline process form step pins accepted-only (matches library default)."""

    src = (REPO / "backend" / "services" / "pipeline" / "process.py").read_text(
        encoding="utf-8"
    )
    assert "build_latest(from_accepted=True)" in src


def test_s7_chunkyctl_documents_allow_legacy_fill() -> None:
    wrapper = (REPO / "scripts" / "chunkyctl").read_text(encoding="utf-8")
    assert "allow-legacy-fill" in wrapper


def test_s7_inventory_gate_green_on_live_config() -> None:
    mod = _load_check_mod()
    viol = mod.collect_violations()
    assert viol == [], viol


def test_s7_inventory_gate_flags_unclassified_sync_table(tmp_path, monkeypatch) -> None:
    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
        "version: 1\ntables:\n  raw_tushare_moneyflow:\n    role: ssot\n",
        encoding="utf-8",
    )
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(
        "domains:\n  moneyflow:\n    target_table: raw_tushare_moneyflow\n"
        "  daily:\n    target_table: raw_tushare_daily\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text("entities: {}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    monkeypatch.setattr(mod, "FORMAL_DOMAIN_RAW_TABLES", {
        "daily": "raw_tushare_daily",
        "stock_st": "raw_tushare_stock_st",
        "trade_cal": "raw_tushare_trade_cal",
        "margin": "raw_tushare_margin",
    })
    viol = mod.collect_violations()
    assert any("raw_tushare_daily" in v and "unclassified" in v for v in viol)


def test_s7_inventory_rejects_formal_domain_as_ssot(tmp_path, monkeypatch) -> None:
    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
        "version: 1\ntables:\n"
        "  raw_tushare_daily:\n    role: ssot\n    formal_domain: daily\n"
        "    write: forbidden\n",
        encoding="utf-8",
    )
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(
        "domains:\n  daily:\n    target_table: raw_tushare_daily\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text(
        "entities:\n  daily:\n    table: raw_tushare_daily\n    layer: L0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    monkeypatch.setattr(mod, "FORMAL_DOMAIN_RAW_TABLES", {
        "daily": "raw_tushare_daily",
        "stock_st": "raw_tushare_stock_st",
        "trade_cal": "raw_tushare_trade_cal",
        "margin": "raw_tushare_margin",
    })
    viol = mod.collect_violations()
    assert any("must not be role=ssot" in v for v in viol)


def test_s7_derive_runtime_still_bans_acquire_imports() -> None:
    src = (REPO / "backend" / "services" / "derive_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    assert "services.data_sources.sync_runner" not in imports
    assert "capture_and_publish" not in src


def test_s7_inventory_role_counts_after_derive_pulse_knife() -> None:
    """S7 inventory holds at 29 ssot after serve/multi-consumer probe (no fake cut)."""

    mod = _load_check_mod()
    counts = mod.role_counts()
    assert counts["ssot"] == 29, counts
    assert counts["fill"] == 1, counts
    assert counts["compatibility"] == 16, counts
    assert sum(counts.values()) == 46, counts


def test_s7_serve_multi_consumer_priority_stay_ssot() -> None:
    """Priority serve/multi-consumer tables: no honest COMPAT without leaf publication."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    reg = load_registry()
    expected = {
        "raw_tushare_limit_list_d": ("serve_l0_leaf", "limit_list_d"),
        "raw_tushare_moneyflow": ("serve_l0_leaf", "moneyflow"),
        "raw_tushare_moneyflow_dc": ("serve_l0_leaf", "moneyflow_dc"),
        "raw_tushare_dc_member": ("membership_l0", "dc_member"),
        "raw_tushare_top_inst": ("multi_consumer", "top_inst"),
        "raw_tushare_index_daily": ("multi_consumer", "index_daily"),
    }
    for table, (kind, entity) in expected.items():
        meta = inv["tables"][table]
        assert meta["role"] == "ssot", table
        assert meta.get("kind") == kind, table
        assert "verified 2026-07-21" in (meta.get("note") or ""), table
        ent = reg.entity(entity)
        assert ent.table == table, (entity, ent.table)


def test_s7_gate_rejects_serve_leaf_compat_without_data_access_redirect(
    tmp_path, monkeypatch
) -> None:
    """Forbid pulse-aggregate theater: serve_l0_leaf COMPAT needs DataAccess redirect."""

    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
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
        "  raw_tushare_moneyflow:\n"
        "    role: compatibility\n"
        "    kind: serve_l0_leaf\n"
        "    publication_surface: mart_sector_pulse_daily\n"
        "  raw_tushare_dc_member:\n"
        "    role: ssot\n"
        "    kind: membership_l0\n"
        "  raw_tushare_index_member_all:\n"
        "    role: compatibility\n"
        "    kind: membership_l0\n"
        "    publication_surface: v_sw_industry_pit\n",
        encoding="utf-8",
    )
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(
        "domains:\n"
        "  daily: {target_table: raw_tushare_daily}\n"
        "  stock_st: {target_table: raw_tushare_stock_st}\n"
        "  trade_cal: {target_table: raw_tushare_trade_cal}\n"
        "  margin: {target_table: raw_tushare_margin}\n"
        "  moneyflow: {target_table: raw_tushare_moneyflow}\n"
        "  dc_member: {target_table: raw_tushare_dc_member}\n"
        "  index_member_all: {target_table: raw_tushare_index_member_all}\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text(
        "entities:\n"
        "  moneyflow: {table: raw_tushare_moneyflow, layer: L0}\n"
        "  dc_member: {table: raw_tushare_dc_member, layer: L0}\n"
        "  index_member_all: {table: v_sw_industry_pit, layer: L1}\n"
        "  daily: {table: raw_tushare_daily, layer: L0}\n"
        "  margin: {table: raw_tushare_margin, layer: L0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert any(
        "raw_tushare_moneyflow" in v and "data_access entity" in v for v in viol
    ), viol


def test_s7_sw_membership_publication_is_pit_view() -> None:
    """Serve/derive SW membership entity points at L1 PIT view, not raw ssot."""

    from services.data_access.spec import load_registry

    ent = load_registry().entity("index_member_all")
    assert ent.table == "v_sw_industry_pit"
    assert ent.layer == "L1"
    assert "name" in ent.columns

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_index_member_all"]
    assert meta["role"] == "compatibility"
    assert meta["kind"] == "membership_l0"
    assert meta["publication_surface"] == "v_sw_industry_pit"


def test_s7_pulse_flow_builder_tables_are_compatibility() -> None:
    """Pulse builder inputs: mart owns display; raw = compat residual."""

    from services.data_access.spec import load_registry

    reg = load_registry()
    assert reg.entity("moneyflow_ind_dc").table == "raw_tushare_moneyflow_ind_dc"
    assert reg.entity("moneyflow_mkt_dc").table == "raw_tushare_moneyflow_mkt_dc"
    assert reg.entity("sw_daily").table == "raw_tushare_sw_daily"
    assert reg.entity("dc_index").table == "raw_tushare_dc_index"
    assert reg.entity("index_dailybasic").table == "raw_tushare_index_dailybasic"
    assert reg.entity("limit_cpt_list").table == "raw_tushare_limit_cpt_list"
    assert reg.entity("top_list").table == "raw_tushare_top_list"

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    for table, mart in (
        ("raw_tushare_moneyflow_ind_dc", "mart_sector_pulse_daily"),
        ("raw_tushare_moneyflow_mkt_dc", "mart_market_pulse_daily"),
        ("raw_tushare_sw_daily", "mart_sector_pulse_daily"),
        ("raw_tushare_dc_index", "mart_sector_pulse_daily"),
        ("raw_tushare_index_dailybasic", "mart_market_pulse_daily"),
        ("raw_tushare_limit_cpt_list", "mart_market_pulse_daily"),
        ("raw_tushare_top_list", "mart_market_pulse_daily"),
    ):
        meta = inv["tables"][table]
        assert meta["role"] == "compatibility", table
        assert meta.get("kind") == "pulse_flow_builder", table
        assert meta.get("publication_surface") == mart, table


def test_s7_daily_basic_and_stk_limit_are_derive_input() -> None:
    """S7: daily_basic → dim_stock_segment_daily; stk_limit → fact_stock_form_daily."""

    from services.data_access.spec import load_registry

    reg = load_registry()
    assert reg.entity("valuation").table == "dim_stock_segment_daily"
    assert reg.entity("valuation").layer == "L1"
    assert "stk_limit" not in reg.entities

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    basic = inv["tables"]["raw_tushare_daily_basic"]
    assert basic["role"] == "compatibility"
    assert basic["kind"] == "derive_input"
    assert basic["publication_surface"] == "dim_stock_segment_daily"
    lim = inv["tables"]["raw_tushare_stk_limit"]
    assert lim["role"] == "compatibility"
    assert lim["kind"] == "derive_input"
    assert lim["publication_surface"] == "fact_stock_form_daily"

    src = (REPO / "backend" / "services" / "market_pulse.py").read_text(encoding="utf-8")
    assert "FROM dim_stock_segment_daily seg" in src
    assert '_tr_entity("valuation")' not in src


def test_s7_hard_stop_kinds_documented_for_residual_ssot() -> None:
    """Residual ssot must carry typed kind + note (no fake FIXED)."""

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    allowed = {
        "membership_l0",
        "serve_l0_leaf",
        "serve_l0_declared",
        "multi_consumer",
        "blocked_no_publication",
        "sync_orphan",
    }
    for table, meta in inv["tables"].items():
        if meta.get("role") != "ssot":
            continue
        kind = meta.get("kind")
        assert kind in allowed, f"{table}: ssot missing typed kind ({kind!r})"
        assert meta.get("note"), f"{table}: ssot missing honest note"


def test_s7_pulse_builder_resolves_via_data_access_entity() -> None:
    """market_pulse builder must not hardcode reclassified pulse raw tables."""

    src = (REPO / "backend" / "services" / "market_pulse.py").read_text(encoding="utf-8")
    for banned in (
        "tr.raw_tushare_sw_daily",
        "tr.raw_tushare_dc_index",
        "tr.raw_tushare_index_dailybasic",
        "tr.raw_tushare_limit_cpt_list",
    ):
        assert banned not in src, banned
    assert '_tr_entity("sw_daily")' in src
    assert '_tr_entity("dc_index")' in src
    assert '_tr_entity("index_dailybasic")' in src
    assert '_tr_entity("limit_cpt_list")' in src
    assert '_tr_entity("top_list")' in src


def test_s7_index_daily_consumers_resolve_via_data_access() -> None:
    """technical_states + institution_profile must not hardcode index_daily SQL."""

    ts = (REPO / "backend" / "services" / "technical_states" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert 'FROM tr.raw_tushare_index_daily' not in ts
    assert 'entity("index_daily")' in ts

    ip = (REPO / "backend" / "services" / "institution_profile.py").read_text(
        encoding="utf-8"
    )
    assert "tr.raw_tushare_index_daily" not in ip
    assert "tr.raw_tushare_top_inst" not in ip
    assert '_tr_entity("index_daily")' in ip
    assert '_tr_entity("top_inst")' in ip


def test_s7_stock_basic_identity_publication_is_dim() -> None:
    """Identity publication = dim_active_a_stock; raw stock_basic = writer residual."""

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_stock_basic"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "identity_cache"
    assert meta.get("publication_surface") == "dim_active_a_stock"

    src = (REPO / "backend" / "services" / "rally_gt.py").read_text(encoding="utf-8")
    assert "ref.dim_active_a_stock" in src
    assert "FROM raw_tushare_stock_basic" not in src


def test_s7_adj_factor_derive_publication_is_qfq() -> None:
    """qfq table owns analysis surface; raw adj_factor = derive rebuild input."""

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_adj_factor"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "derive_input"
    assert meta.get("publication_surface") == "price_kline_qfq_tushare"


def test_s7_gate_allows_membership_compat_with_publication_surface(
    tmp_path, monkeypatch
) -> None:
    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
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
        "  raw_tushare_dc_member:\n"
        "    role: ssot\n"
        "    kind: membership_l0\n"
        "  raw_tushare_index_member_all:\n"
        "    role: compatibility\n"
        "    kind: membership_l0\n"
        "    publication_surface: v_sw_industry_pit\n",
        encoding="utf-8",
    )
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(
        "domains:\n"
        "  daily: {target_table: raw_tushare_daily}\n"
        "  stock_st: {target_table: raw_tushare_stock_st}\n"
        "  trade_cal: {target_table: raw_tushare_trade_cal}\n"
        "  margin: {target_table: raw_tushare_margin}\n"
        "  dc_member: {target_table: raw_tushare_dc_member}\n"
        "  index_member_all: {target_table: raw_tushare_index_member_all}\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text(
        "entities:\n"
        "  dc_member: {table: raw_tushare_dc_member, layer: L0}\n"
        "  index_member_all: {table: v_sw_industry_pit, layer: L1}\n"
        "  daily: {table: raw_tushare_daily, layer: L0}\n"
        "  margin: {table: raw_tushare_margin, layer: L0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert viol == [], viol


def test_s7_gate_rejects_membership_compat_without_publication_surface(
    tmp_path, monkeypatch
) -> None:
    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
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
        "  raw_tushare_dc_member:\n"
        "    role: ssot\n"
        "    kind: membership_l0\n"
        "  raw_tushare_index_member_all:\n"
        "    role: compatibility\n"
        "    kind: membership_l0\n",
        encoding="utf-8",
    )
    reg = tmp_path / "sync_registry.yaml"
    reg.write_text(
        "domains:\n"
        "  daily: {target_table: raw_tushare_daily}\n"
        "  stock_st: {target_table: raw_tushare_stock_st}\n"
        "  trade_cal: {target_table: raw_tushare_trade_cal}\n"
        "  margin: {target_table: raw_tushare_margin}\n"
        "  dc_member: {target_table: raw_tushare_dc_member}\n"
        "  index_member_all: {target_table: raw_tushare_index_member_all}\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text(
        "entities:\n"
        "  dc_member: {table: raw_tushare_dc_member, layer: L0}\n"
        "  index_member_all: {table: raw_tushare_index_member_all, layer: L0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert any("membership_l0" in v and "publication_surface" in v for v in viol)

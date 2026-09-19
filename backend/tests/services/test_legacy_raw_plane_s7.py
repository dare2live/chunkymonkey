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


def test_s7_qfq_nominal_source_is_canonical_only() -> None:
    """2026-09-08 换心后 OHLCV 恒来自 canonical, 不再有 legacy raw 兜底可切。

    原来这里是两条用例: 一条验默认排除 legacy raw, 一条验 from_accepted=False 会
    恢复 raw_tushare_daily UNION。后者测的机制当天连同 nominal_source_cte() 整个删了
    (增量路径 + legacy-fill 一并退役, builder 恒为 DROP+CTAS), 故随主语一起退役;
    前者的语义仍在, 换成对实际生成的 SQL 断言 —— 现在不存在"两态可切", 是结构性的
    单一来源, 比原来的 flag 断言更强。
    """
    mod = _load_qfq_mod()
    assert not hasattr(mod, "nominal_source_cte")
    sql = mod.build_select_sql(mod.load_config(), batch_id="t", ingested_at="1970-01-01T00:00:00Z")
    assert "canonical_nominal_ohlcv_daily" in sql
    assert "raw_tushare_daily" not in sql
    assert "raw_tushare_adj_factor" not in sql
    assert "UNION ALL" not in sql


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
    """S7 inventory: 14 ssot / 1 fill / 22 compat (holdernumber restored).

    2026-09-08 自算换心只动 raw_tushare_adj_factor 的 kind (derive_input → sync_orphan,
    它不再是任何 derive 的输入), role 仍是 compatibility —— 顶注定义 ssot 是
    「production still treats this raw table as truth」, 换源后生产恰恰不再拿它当真相,
    升 ssot 会把方向弄反。故本组计数不变。

    retired 计数不钉死数字 (2026-09-18 cut_lineage_drift §2.3): 已 DROP 的墓碑条目
    (express/fina_mainbz/stk_factor_pro) 同 commit 从本文件删除, "retired == N" 这种
    状态断言每次墓碑清理都要跟着改数字, 且不判任何东西——见下面
    test_s7_residual_ssot_map_is_typed_hard_stops_only 改成的不变量断言。
    """

    mod = _load_check_mod()
    counts = mod.role_counts()
    assert counts["ssot"] == 14, counts
    assert counts["fill"] == 1, counts
    assert counts["compatibility"] == 22, counts


def test_s7_residual_ssot_map_is_typed_hard_stops_only() -> None:
    """No safe COMPAT batch left: residual ssot = blocked + declared + orphan only.

    Do not reclassify these to compatibility without a non-raw publication
    surface + DataAccess redirect (or owner sunset). Fake COMPAT = forbidden.
    """

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    by_kind: dict[str, set[str]] = {}
    for table, meta in inv["tables"].items():
        if meta.get("role") != "ssot":
            continue
        kind = str(meta.get("kind") or "")
        short = table.removeprefix("raw_tushare_")
        by_kind.setdefault(kind, set()).add(short)
        assert meta.get("note"), f"{table}: missing honest note"

    expected = {
        "blocked_no_publication": {"margin_detail", "suspend_d"},
        "serve_l0_declared": {
            "block_trade",
            "cyq_perf",
            "fina_indicator",
            "forecast",
            "report_rc",
            "share_float",
            "stk_holdernumber",
            "stk_surv",
        },
        # adj_factor 不在此: 2026-09-08 换心后它的 kind 是 sync_orphan, 但 role 仍是
        # compatibility (本 map 只收 role=ssot 的), 见
        # test_s7_adj_factor_is_compatibility_sync_orphan_after_self_derive_switch。
        "sync_orphan": {
            "balancesheet",
            "dividend",
            "income",
            "moneyflow_hsgt",
        },
    }
    assert by_kind == expected, by_kind
    # (2026-09-08 删掉原来紧跟其后的 `sum(len(v) for v in by_kind.values()) == N`:
    #  上一行已经把成员钉死, 这个和就被 expected 完全决定, 它永远不可能独立失败 ——
    #  不判任何东西, 只会在每次成员变动时跟着要人改数字。)
    #
    # retired 集合改成不变量而不是钉死名单 (2026-09-18 cut_lineage_drift §2.3 F1):
    # legacy_raw_plane.yaml 现在是 lineage_catalog_drift 的第四个声明源, 已 DROP 的
    # 墓碑条目 (role=retired 但表已不在) 会被判成 orphan —— "钉死 9 个名字" 这种状态
    # 断言测不出这条新规则, 每次墓碑清理还要跟着手改数字。真正该守的不变量是:
    # 每个 role=retired 键必须有非空 note (为什么停更/停供), 且不能再是 sync_registry
    # 的 target_table (退役声明与仍在同步矛盾)。
    retired_tables = {
        table: meta
        for table, meta in inv["tables"].items()
        if meta.get("role") == "retired"
    }
    assert retired_tables, "S7 inventory 至少应有一张 retired 表 (K3 停更批)"
    sync_targets = mod.sync_registry_raw_tables()
    for table, meta in retired_tables.items():
        assert meta.get("note"), f"{table}: role=retired 必须有非空 note (为什么停更/停供)"
        assert table not in sync_targets, (
            f"{table}: role=retired 但仍是 sync_registry 的 target_table —— "
            "退役声明与仍在同步矛盾, 要么撤回 retired 要么从 sync_registry 摘掉这张表"
        )


def test_s7_limit_list_d_publication_is_fact_stock_limit_daily() -> None:
    """B2: limit_list_d serve leaf → fact_stock_limit_daily; raw = compatibility."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_limit_list_d"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "serve_l0_leaf"
    assert meta.get("publication_surface") == "fact_stock_limit_daily"
    ent = load_registry().entity("limit_list_d")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_stock_limit_daily"


def test_s7_moneyflow_publications_are_fact_stock_day() -> None:
    """B2: moneyflow + moneyflow_dc → fact_stock_moneyflow(_dc)_daily; raw = compatibility."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    reg = load_registry()
    for table, entity, surface in (
        ("raw_tushare_moneyflow", "moneyflow", "fact_stock_moneyflow_daily"),
        ("raw_tushare_moneyflow_dc", "moneyflow_dc", "fact_stock_moneyflow_dc_daily"),
    ):
        meta = inv["tables"][table]
        assert meta["role"] == "compatibility", table
        assert meta.get("kind") == "serve_l0_leaf", table
        assert meta.get("publication_surface") == surface, table
        ent = reg.entity(entity)
        assert ent.db == "smartmoney", entity
        assert ent.table == surface, entity


def test_s7_index_daily_publication_is_fact_index_daily() -> None:
    """B2: index_daily multi_consumer → fact_index_daily; raw = compatibility."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_index_daily"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "multi_consumer"
    assert meta.get("publication_surface") == "fact_index_daily"
    ent = load_registry().entity("index_daily")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_index_daily"
    assert ent.code_input == "ts_passthrough"


def test_s7_top_inst_seat_publication_is_fact_top_inst_seat_daily() -> None:
    """B2: top_inst multi_consumer → fact_top_inst_seat_daily; raw = compatibility."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_top_inst"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "multi_consumer"
    assert meta.get("publication_surface") == "fact_top_inst_seat_daily"
    ent = load_registry().entity("top_inst")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_top_inst_seat_daily"
    assert "sides" in ent.columns
    assert "board_window" in ent.columns
    assert "seat_kind" in ent.columns
    assert "exalter" in ent.columns


def test_s7_membership_l0_dc_member_is_compatibility() -> None:
    """B1: dc_member observation-date PIT published → raw membership_l0 COMPAT."""

    from services.data_access.spec import load_registry

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    reg = load_registry()
    meta = inv["tables"]["raw_tushare_dc_member"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "membership_l0"
    assert meta.get("publication_surface") == "fact_dc_member_daily"
    assert "observation-date" in (meta.get("note") or "")
    ent = reg.entity("dc_member")
    assert ent.db == "smartmoney"
    assert ent.table == "fact_dc_member_daily"
    assert "trade_date" in ent.columns
    assert "con_code" in ent.columns


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
    assert reg.entity("moneyflow_ind_dc").table == "v_moneyflow_ind_dc_board_day"
    assert reg.entity("moneyflow_mkt_dc").table == "v_moneyflow_mkt_dc_market_day"
    assert reg.entity("sw_daily").table == "v_sw_daily_index_day"
    assert reg.entity("dc_index").table == "v_dc_index_board_day"
    assert reg.entity("index_dailybasic").table == "v_index_dailybasic_index_day"
    assert reg.entity("limit_cpt_list").table == "v_limit_cpt_list_board_day"
    assert reg.entity("top_list").table == "v_top_list_stock_day"

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


def test_s7_sync_orphan_not_in_data_access_live() -> None:
    """Owner Q2 thin gate: sync_orphan raw must not be a DataAccess entity."""

    mod = _load_check_mod()
    viol = mod.collect_violations()
    assert viol == [], viol
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    da_raw = mod.data_access_raw_tables()
    orphans = {
        name
        for name, meta in inv["tables"].items()
        if isinstance(meta, dict) and meta.get("kind") == "sync_orphan"
    }
    assert orphans
    assert not (orphans & da_raw)
    watch = inv.get("publication_watchlist") or []
    assert isinstance(watch, list) and watch
    assert set(watch) <= orphans


def test_s7_gate_rejects_sync_orphan_in_data_access(tmp_path, monkeypatch) -> None:
    """Red case: registering a sync_orphan in DataAccess must fail the gate."""

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
        "    role: ssot\n"
        "    kind: membership_l0\n"
        "  raw_tushare_income:\n"
        "    role: ssot\n"
        "    kind: sync_orphan\n"
        "    note: orphan\n",
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
        "  index_member_all: {target_table: raw_tushare_index_member_all}\n"
        "  income: {target_table: raw_tushare_income}\n",
        encoding="utf-8",
    )
    da = tmp_path / "data_access.yaml"
    da.write_text(
        "entities:\n"
        "  dc_member: {table: raw_tushare_dc_member, layer: L0}\n"
        "  index_member_all: {table: raw_tushare_index_member_all, layer: L0}\n"
        "  income: {table: raw_tushare_income, layer: L0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "INVENTORY_YAML", inv)
    monkeypatch.setattr(mod, "SYNC_REGISTRY_YAML", reg)
    monkeypatch.setattr(mod, "DATA_ACCESS_YAML", da)
    viol = mod.collect_violations()
    assert any("sync_orphan must not appear in data_access" in v for v in viol), viol


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
    assert "tr.raw_tushare_index_daily" not in ts
    assert "FROM tr.{bench_tbl}" not in ts
    assert "_index_daily_rel" in ts
    assert 'entity("index_daily")' in ts

    ip = (REPO / "backend" / "services" / "institution_profile.py").read_text(
        encoding="utf-8"
    )
    assert "tr.raw_tushare_index_daily" not in ip
    assert "tr.raw_tushare_top_inst" not in ip
    assert '_tr_entity("index_daily")' in ip
    assert '_tr_entity("top_inst")' in ip
    assert 'ent.db == "smartmoney"' in ip


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


def test_s7_adj_factor_is_compatibility_sync_orphan_after_self_derive_switch() -> None:
    """2026-09-08 自算换心后 raw_tushare_adj_factor 的正确分类。

    kind 变了: derive_input → sync_orphan —— build_price_kline_qfq_tushare.py 已改自算
    (services.adjust_factor 从 canonical.pre_close 推 ratio), 不再 JOIN 本表, 它不再是
    任何 derive 的输入; 表也冻结在 20260828。

    role **没变**, 仍是 compatibility。legacy_raw_plane.yaml 顶注对 ssot 的定义是
    「production still treats this raw table as truth」—— 换源后生产恰恰不再拿它当真相,
    把它升成 ssot 是把方向弄反了 (本轮实际发生过这个误判, 连带 5 个计数断言与
    check_foundation_done 的墙计数一起转红, 是它们把方向错误顶了回来)。

    publication_surface 保留且比换源前更成立: 复权因子现在由
    price_kline_qfq_tushare.hfq_factor 列发布, 本表降为该事实的历史副本, 只被自检/对账
    读取 —— 那是读「供应商当年的说法」, 不是读真相。
    """

    mod = _load_check_mod()
    inv = mod._load_yaml(mod.INVENTORY_YAML)
    meta = inv["tables"]["raw_tushare_adj_factor"]
    assert meta["role"] == "compatibility"
    assert meta.get("kind") == "sync_orphan"
    assert meta.get("publication_surface") == "price_kline_qfq_tushare"

    da = mod._load_yaml(mod.DATA_ACCESS_YAML)
    entity_tables = {v.get("table") for v in (da.get("entities") or {}).values() if isinstance(v, dict)}
    assert "raw_tushare_adj_factor" not in entity_tables


def test_s7_gate_allows_membership_compat_with_publication_surface(
    tmp_path, monkeypatch
) -> None:
    mod = _load_check_mod()
    inv = tmp_path / "legacy_raw_plane.yaml"
    inv.write_text(
        "version: 1\n"
        "data_access_raw_entity_allowlist: [dc_member, daily, margin]\n"
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

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


def test_s7_derive_form_path_excludes_legacy_raw_daily(monkeypatch) -> None:
    """S7 derive form default passes from_accepted=True into technical_states."""

    seen: dict[str, bool] = {}

    def _fake_build_latest(*, from_accepted: bool = False, **kwargs):
        seen["from_accepted"] = from_accepted
        return {"mode": "build_latest", "added_days": 0, "rows": 0}

    monkeypatch.setattr(ts, "build_latest", _fake_build_latest)
    out = dr.run_derive("form")
    assert seen["from_accepted"] is True
    assert out["from_accepted"] is True
    sql = ts.src_temp_sql(from_accepted=True)
    assert "raw_tushare_daily" not in sql
    assert "can.close AS raw_close" in sql


def test_s7_derive_cli_has_allow_legacy_fill() -> None:
    src = (REPO / "backend" / "scripts" / "derive_cli.py").read_text(encoding="utf-8")
    assert "allow-legacy-fill" in src
    assert "from_accepted" in src


def test_s7_pipeline_clean_explicit_allow_legacy_fill() -> None:
    """daily_update clean keeps 2019 history via explicit escape (not silent default)."""

    src = (REPO / "backend" / "services" / "pipeline" / "clean.py").read_text(
        encoding="utf-8"
    )
    assert "allow-legacy-fill" in src
    assert "build_price_kline_qfq_tushare.py" in src


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

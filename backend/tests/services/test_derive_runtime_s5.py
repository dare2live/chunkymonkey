"""S5 derive surface: qfq/form from accepted/canonical, independent of acquire."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services import derive_runtime as dr
from services import technical_states as ts

REPO = Path(__file__).resolve().parents[3]


def test_s5_form_from_accepted_sql_excludes_legacy_raw_daily() -> None:
    default_sql = ts.src_temp_sql(from_accepted=False)
    accepted_sql = ts.src_temp_sql(from_accepted=True)

    assert "canonical_nominal_ohlcv_daily" in accepted_sql
    assert "raw_tushare_daily" not in accepted_sql
    assert "COALESCE(rd.close" not in accepted_sql
    assert "can.close AS raw_close" in accepted_sql
    # stk_limit remains a separate domain input (like adj_factor for qfq).
    assert "raw_tushare_stk_limit" in accepted_sql

    assert "raw_tushare_daily" in default_sql
    assert "COALESCE(rd.close, can.close)" in default_sql


def test_s5_derive_targets_are_qfq_and_form_only() -> None:
    assert set(dr.DERIVE_TARGETS) == {"qfq", "form"}


def test_s5_derive_rejects_unknown_target() -> None:
    with pytest.raises(ValueError, match="unknown derive target"):
        dr.run_derive("sync")


def test_s5_derive_runtime_source_has_no_acquire_or_fused_publish() -> None:
    """Derive must not re-fuse fetch→accept (no sync_runner / capture_and_publish)."""

    src = (REPO / "backend" / "services" / "derive_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    banned = (
        "services.data_sources.sync_runner",
        "services.data_sources.security_day_acquire",
        "services.data_sources.nominal_ohlcv_runtime",
        "services.data_sources.stock_st_runtime",
    )
    for mod in banned:
        assert mod not in imports, f"derive_runtime must not import {mod}"
    assert "capture_and_publish" not in src
    assert "resolve_security_day_acquire" not in src


def test_s5_chunkyctl_exposes_derive_command() -> None:
    wrapper = (REPO / "scripts" / "chunkyctl").read_text(encoding="utf-8")
    assert "derive)" in wrapper or 'derive)' in wrapper
    assert "chunkyctl derive" in wrapper
    assert "from-accepted" in wrapper
    assert "allow-legacy-fill" in wrapper

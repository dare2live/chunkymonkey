"""data_migration_status 单测 — 状态分类逻辑 (registry 驱动, 不臆造进度)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "data_migration_status.py"
SPEC = importlib.util.spec_from_file_location("data_migration_status", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def test_render_table_has_header_and_verdict():
    s = {
        "registered_domains": 2, "landed": 1, "never_synced": 1, "stale": 0,
        "domains_with_open_failures": 0, "total_rows": 100, "calendar_max": "20260611",
        "verdict": "WARN",
        "domains": [
            {"domain": "moneyflow", "status": "OK", "rows": 100, "last_data_date": "20260611",
             "sla_days": 1, "open_failures": 0},
            {"domain": "daily", "status": "NEVER_SYNCED", "rows": 0, "last_data_date": None,
             "sla_days": 1, "open_failures": 0},
        ],
    }
    out = mod._render_table(s)
    assert "verdict=WARN" in out
    assert "moneyflow" in out and "NEVER_SYNCED" in out
    assert "日历最新 20260611" in out


def test_collect_status_real_registry_shape():
    """真 registry + DB: 返回结构契约 (注册域 = registry domains, 不臆造)."""
    import yaml

    s = mod.collect_status()
    reg = yaml.safe_load(mod._REGISTRY.read_text(encoding="utf-8"))
    assert s["registered_domains"] == len(reg["domains"])
    assert s["landed"] + s["never_synced"] <= s["registered_domains"]
    assert s["verdict"] in ("PASS", "WARN")
    # 每域必带状态, NEVER_SYNCED 域行数必 0 (不臆造已落库)
    for d in s["domains"]:
        assert d["status"] in ("OK", "STALE", "NEVER_SYNCED", "LANDED_NO_DATE")
        if d["status"] == "NEVER_SYNCED":
            assert d["rows"] == 0

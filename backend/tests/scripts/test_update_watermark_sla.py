"""update_watermark_sla registry 驱动条目单测 — sync:* 域防线契约 (复审 HIGH 闭环)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_watermark_sla.py"
SPEC = importlib.util.spec_from_file_location("update_watermark_sla", SCRIPT_PATH)
sla = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = sla
SPEC.loader.exec_module(sla)


def test_sync_registry_queries_cover_all_domains():
    """registry 注册即入防线: 每个域必须有条目 (可 probe 或显式 no_probe), 零静默缺席."""
    import yaml

    reg = yaml.safe_load(
        (SCRIPT_PATH.resolve().parents[1] / "config" / "sync_registry.yaml").read_text())
    queries = sla._sync_registry_queries()
    for name in reg["domains"]:
        assert f"sync:{name}" in queries, f"sync:{name} 不在 SLA 防线 — 注册域静默缺席"


def test_daily_domains_probe_trade_date_quarterly_no_probe():
    queries = sla._sync_registry_queries()
    q = queries["sync:moneyflow"]
    assert "trade_date" in q["query"] and q["db"] == "tushare_raw"
    assert q["sla_days"] is not None  # registry per-domain SLA 优先于 tier 默认
    assert queries["sync:fina_mainbz"].get("no_probe")  # by_ts_code 季度域显式 no_probe


def test_query_actual_returns_none_when_db_unreachable():
    """库不可达 → None (调用方标 DB_LOCKED_UNVERIFIED), 不抛不伪装."""
    queries = {"sync:x": {"db": "tushare_raw", "query": "SELECT 1"}}
    assert sla._query_actual_max_date({"tushare_raw": None}, queries, "sync:x") is None

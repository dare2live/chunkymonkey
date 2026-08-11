"""FOUNDATION F9 residual hygiene SLA — unit + wire tests."""
from __future__ import annotations

from pathlib import Path

from services.residual_hygiene import (
    classify_lag,
    evaluate_residual_hygiene,
    load_policy,
    overall_status,
    trading_lag_days,
)


def test_trading_lag_days_counts_strict_after_through_to():
    days = ["20260720", "20260721", "20260722", "20260723", "20260724"]
    assert trading_lag_days(days, "20260720", "20260720") == 0
    assert trading_lag_days(days, "20260720", "20260721") == 1
    assert trading_lag_days(days, "20260720", "20260723") == 3
    assert trading_lag_days(days, "20260722", "20260720") == 0
    assert trading_lag_days(days, None, "20260720") is None


def test_classify_lag_pass_warn_fail():
    assert classify_lag(0, warn_trading_days=1, fail_trading_days=2) == "pass"
    assert classify_lag(1, warn_trading_days=1, fail_trading_days=2) == "pass"
    assert classify_lag(2, warn_trading_days=1, fail_trading_days=2) == "warn"
    assert classify_lag(3, warn_trading_days=1, fail_trading_days=2) == "fail"
    assert classify_lag(None, warn_trading_days=1, fail_trading_days=2) == "skip"


def test_overall_status_rollup():
    assert overall_status([{"status": "pass"}]) == "PASS"
    assert overall_status([{"status": "warn"}, {"status": "pass"}]) == "WARN"
    assert overall_status([{"status": "fail"}, {"status": "warn"}]) == "FAIL"


def test_load_policy_version_1():
    pol = load_policy()
    assert pol["version"] == 1
    assert pol["type_b_publish_lag"]["fail_trading_days"] == 2
    assert pol["ann_tip_lag"]["domains"][0]["domain"] == "stk_holdernumber"


class _FakeConn:
    def __init__(self, tables: dict[str, str | None]):
        self._tables = tables

    def execute(self, sql: str, params=None):
        sql_l = " ".join(sql.split()).lower()
        if "information_schema.tables" in sql_l:
            name = params[0] if params else None
            hit = name in self._tables

            class _R:
                def fetchone(self_inner):
                    return (1,) if hit else None

            return _R()
        # MAX query — pick table token after FROM
        for table, mx in self._tables.items():
            if f"from {table}" in sql_l:

                class _R2:
                    def fetchone(self_inner):
                        return (mx,) if mx is not None else (None,)

                return _R2()

        class _R3:
            def fetchone(self_inner):
                return None

        return _R3()

    def close(self):
        return None


def test_evaluate_type_b_fail_when_lag_over_sla():
    days = ["20260720", "20260721", "20260722", "20260723", "20260724"]
    policy = {
        "version": 1,
        "policy_id": "test",
        "type_b_publish_lag": {
            "enabled": True,
            "warn_trading_days": 1,
            "fail_trading_days": 2,
        },
        "ann_tip_lag": {"enabled": False},
    }
    raw = _FakeConn(
        {
            "raw_tushare_moneyflow": "20260724",
            "raw_tushare_moneyflow_dc": "20260724",
            "raw_tushare_limit_list_d": "20260724",
            "raw_tushare_index_daily": "20260724",
            "raw_tushare_dc_member": "20260724",
            "raw_tushare_top_inst": "20260724",
        }
    )
    fact = _FakeConn(
        {
            "fact_stock_moneyflow_daily": "20260720",  # lag=4 trading days → fail
            "fact_stock_moneyflow_dc_daily": "20260724",
            "fact_stock_limit_daily": "20260724",
            "fact_index_daily": "20260724",
            "fact_dc_member_daily": "20260724",
            "fact_top_inst_seat_daily": "20260724",
        }
    )
    out = evaluate_residual_hygiene(
        policy=policy,
        trading_days=days,
        raw_conn=raw,
        fact_conn=fact,
        type_b_only=True,
    )
    assert out["overall"] == "FAIL"
    money = next(f for f in out["findings"] if f["domain"] == "moneyflow")
    assert money["status"] == "fail"
    assert money["lag_trading_days"] == 4


def test_evaluate_type_b_pass_when_caught_up():
    days = ["20260720", "20260721", "20260722", "20260723", "20260724"]
    policy = {
        "version": 1,
        "policy_id": "test",
        "type_b_publish_lag": {
            "enabled": True,
            "warn_trading_days": 1,
            "fail_trading_days": 2,
        },
        "ann_tip_lag": {"enabled": False},
    }
    tables_raw = {
        "raw_tushare_moneyflow": "20260724",
        "raw_tushare_moneyflow_dc": "20260724",
        "raw_tushare_limit_list_d": "20260724",
        "raw_tushare_index_daily": "20260724",
        "raw_tushare_dc_member": "20260724",
        "raw_tushare_top_inst": "20260724",
    }
    tables_fact = {
        "fact_stock_moneyflow_daily": "20260724",
        "fact_stock_moneyflow_dc_daily": "20260724",
        "fact_stock_limit_daily": "20260724",
        "fact_index_daily": "20260724",
        "fact_dc_member_daily": "20260724",
        "fact_top_inst_seat_daily": "20260724",
    }
    out = evaluate_residual_hygiene(
        policy=policy,
        trading_days=days,
        raw_conn=_FakeConn(tables_raw),
        fact_conn=_FakeConn(tables_fact),
        type_b_only=True,
    )
    assert out["overall"] == "PASS"


def test_store_wires_residual_hygiene_after_continuity():
    """2026-08-11 P1 门重新分布后，接线的真相源是 governance_gates.yaml 的
    runtime_checks（store.py 只按登记表跑），故顺序与 alert flag 在登记表里断言。"""
    from services.governance_gates import load_registry

    src = (
        Path(__file__).resolve().parents[2] / "services" / "pipeline" / "store.py"
    ).read_text(encoding="utf-8")
    assert "run_system_health_checks" in src, "store 必须跑 system_health 组"

    checks = load_registry().runtime_checks
    ids = [c.id for c in checks]
    assert ids.index("residual_hygiene") > ids.index("continuity")
    hygiene = next(c for c in checks if c.id == "residual_hygiene")
    assert hygiene.script.endswith("check_residual_hygiene.py")
    assert "/tmp/chunkymonkey_ALERT_residual_hygiene.flag" in hygiene.args


def test_type_b_catchup_evaluates_residual_hygiene():
    backend = Path(__file__).resolve().parents[2]
    src = (backend / "services" / "type_b_fact_publish_catchup.py").read_text(
        encoding="utf-8"
    )
    assert "evaluate_type_b_after_catchup" in src
    assert "residual_hygiene_type_b" in src


def test_evaluate_type_b_after_catchup_skips_without_dbs(tmp_path, monkeypatch):
    from services import residual_hygiene as rh

    monkeypatch.setattr(
        "services.data_access.resolver.db_path",
        lambda alias: str(tmp_path / f"{alias}.duckdb"),
    )
    out = rh.evaluate_type_b_after_catchup()
    assert out["status"] == "skipped"
    assert out["overall"] == "PASS"
    assert out.get("reason") in {"db_missing", "reference_db_missing"}


def test_run_outcome_classifies_residual_hygiene_as_integrity():
    from services.pipeline.run_outcome import derive_run_outcome

    info = derive_run_outcome(["residual_hygiene FAIL — Type-B publish lag over SLA"])
    assert info["run_outcome"] == "integrity_observe"

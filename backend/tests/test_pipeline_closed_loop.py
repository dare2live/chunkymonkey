"""Serve→derive closed-loop unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_decide_institution_skip_when_frontier_matches(tmp_path):
    from services.pipeline.closed_loop import (
        decide_institution_profile_action,
        write_institution_as_of,
    )

    marker = tmp_path / "inst_as_of.json"
    write_institution_as_of("20260721", path=marker)
    from services.pipeline import closed_loop as cl

    decision = decide_institution_profile_action(
        holders_changed=False,
        holders_notice_frontier="20260721",
        previous_as_of=cl.read_institution_as_of(path=marker),
    )
    assert decision["action"] == "skip"
    assert decision["reason"] == "inst_frontier_unchanged"


def test_decide_institution_run_when_holders_advance(tmp_path):
    from services.pipeline.closed_loop import decide_institution_profile_action

    decision = decide_institution_profile_action(
        holders_changed=False,
        holders_notice_frontier="20260723",
        previous_as_of="20260721",
    )
    assert decision["action"] == "run"
    assert decision["reason"] == "holders_frontier_ahead_of_inst"


def test_decide_institution_run_on_holders_changed():
    from services.pipeline.closed_loop import decide_institution_profile_action

    decision = decide_institution_profile_action(
        holders_changed=True,
        holders_notice_frontier="20260721",
        previous_as_of="20260721",
    )
    assert decision["action"] == "run"
    assert decision["reason"] == "holders_state_changed"


def test_plan_process_steps_includes_institution_profile(monkeypatch, tmp_path):
    from services.pipeline import closed_loop as cl
    from services.pipeline.delta_manifest import decide_dc_action, plan_process_steps

    marker = tmp_path / "inst_as_of.json"
    cl.write_institution_as_of("20260721", path=marker)
    monkeypatch.setattr(cl, "INST_AS_OF_PATH", marker)
    monkeypatch.setattr(
        "services.pipeline.delta_manifest.read_institution_as_of",
        lambda path=None: cl.read_institution_as_of(path=marker),
    )

    decision = decide_dc_action(
        current_frontier="20260721",
        previous_frontier="20260721",
        advanced_partitions=[],
    )
    plan = plan_process_steps(
        dc_decision=decision,
        state_changes={
            "holders": {
                "changed": False,
                "as_of": "20260721",
            }
        },
    )
    assert "institution_profile" in plan
    assert plan["institution_profile"]["action"] == "skip"

    plan2 = plan_process_steps(
        dc_decision=decision,
        state_changes={
            "holders": {
                "changed": True,
                "as_of": "20260723",
            }
        },
    )
    assert plan2["institution_profile"]["action"] == "run"


def test_org_population_canary_under_populated():
    from services.pipeline.closed_loop import evaluate_org_population

    pop = evaluate_org_population(accepted_stocks=2, raw_stocks=5520)
    assert pop["under_populated"] is True
    assert any("accepted_stocks" in r for r in pop["reasons"])


def test_org_population_healthy():
    from services.pipeline.closed_loop import evaluate_org_population

    pop = evaluate_org_population(accepted_stocks=5000, raw_stocks=5520)
    assert pop["under_populated"] is False


def test_wired_process_steps_include_institution():
    from services.pipeline.closed_loop import wired_process_steps

    steps = wired_process_steps()
    assert "institution_profile" in steps
    assert "market_pulse" in steps


def test_decide_org_gap_repair_when_dense_raw():
    from services.org_holding_population import decide_org_gap_action

    action, status = decide_org_gap_action(
        accepted_has=True,
        local_has=True,
        population={
            "under_populated": True,
            "raw_stocks": 5520,
            "accepted_stocks": 2,
        },
    )
    assert action == "repair_accept_from_local_raw"
    assert status == "under_populated_accepted"


def test_decide_org_gap_repair_fetch_when_raw_thin():
    from services.org_holding_population import decide_org_gap_action

    action, status = decide_org_gap_action(
        accepted_has=True,
        local_has=True,
        population={
            "under_populated": True,
            "raw_stocks": 10,
            "accepted_stocks": 2,
        },
    )
    assert action == "repair_fetch_period"
    assert status == "under_populated_raw_thin"


def test_seed_institution_as_of(tmp_path, monkeypatch):
    from services.pipeline import closed_loop as cl

    class _Conn:
        def execute(self, sql, *a, **k):
            class _Cur:
                def fetchone(_self):
                    return ("20260723",)

            return _Cur()

        def close(self):
            return None

    monkeypatch.setattr(
        "services.database_manifest.get_database_manifest",
        lambda: type("M", (), {"path_for": lambda self, _a: tmp_path / "x.duckdb"})(),
    )
    monkeypatch.setattr(
        "services.duck_adapter.connect",
        lambda *a, **k: _Conn(),
    )
    marker = tmp_path / "inst_as_of.json"
    out = cl.seed_institution_as_of_from_holders(path=marker)
    assert out["status"] == "seeded"
    assert cl.read_institution_as_of(path=marker) == "20260723"

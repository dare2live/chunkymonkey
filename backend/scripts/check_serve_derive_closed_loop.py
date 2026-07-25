#!/usr/bin/env python3
"""Static check: closed-loop inventory process_steps are planned + executed.

Authority: analysis/serve_derive_closed_loop_law_20260723.md
Exit 0 PASS; 1 FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))


def main() -> int:
    from services.pipeline.closed_loop import load_closed_loop_config, wired_process_steps

    cfg = load_closed_loop_config()
    steps = wired_process_steps(cfg)
    plan_src = (REPO / "backend/services/pipeline/delta_manifest.py").read_text(
        encoding="utf-8"
    )
    process_src = (REPO / "backend/services/pipeline/process.py").read_text(
        encoding="utf-8"
    )
    acquire_src = (REPO / "backend/services/pipeline/acquire.py").read_text(
        encoding="utf-8"
    )
    # process_step → which phase owns the wiring string (Type-B is acquire-phase).
    surface_by_step = {
        str(s.get("process_step")): s
        for s in (cfg.get("surfaces") or [])
        if s.get("process_step")
    }
    gaps: list[str] = []
    for step in steps:
        if step == "dc_industry_view":
            token = "dc_industry_view"
        else:
            token = step
        surf = surface_by_step.get(step) or {}
        binding = str(surf.get("binding") or "")
        in_plan = (
            f'"{token}"' in plan_src
            or f"'{token}'" in plan_src
            or token in plan_src
        )
        in_acquire = (
            token in acquire_src
            or "type_b_fact_publish" in acquire_src
            or "run_acquire_type_b_publish_catchup" in acquire_src
        )
        if binding == "daily_acquire":
            if not in_acquire:
                gaps.append(f"acquire.py missing wiring for {token}")
        elif not in_plan:
            gaps.append(f"plan_process_steps missing {token}")
        if token == "institution_profile":
            if "institution_profile" not in process_src or "rebuild_all" not in process_src:
                gaps.append("process.py missing institution_profile rebuild wiring")
        elif token == "market_pulse":
            if "market_pulse" not in process_src:
                gaps.append("process.py missing market_pulse")
        elif token == "segments":
            if "segments" not in process_src:
                gaps.append("process.py missing segments")
        elif token == "technical_states":
            if "technical_states" not in process_src:
                gaps.append("process.py missing technical_states")
    org_src = (REPO / "backend/services/org_holding_aif10.py").read_text(
        encoding="utf-8"
    )
    pop_src = (REPO / "backend/services/org_holding_population.py").read_text(
        encoding="utf-8"
    )
    if "repair_accept_from_local_raw" not in org_src and "repair_accept_from_local_raw" not in pop_src:
        gaps.append("org population repair action missing")
    if "min_org_accepted_stocks" not in (
        REPO / "backend/config/foundation_done.yaml"
    ).read_text(encoding="utf-8"):
        gaps.append("F6 min_org_accepted_stocks missing")
    if "seed_institution_as_of_from_holders" not in (
        REPO / "backend/services/pipeline/closed_loop.py"
    ).read_text(encoding="utf-8"):
        gaps.append("institution as_of seed helper missing")
    if "integrity_observe" not in (
        REPO / "backend/services/pipeline/run_outcome.py"
    ).read_text(encoding="utf-8"):
        gaps.append("run_outcome missing integrity_observe")
    if gaps:
        print("FAIL serve_derive_closed_loop:")
        for g in gaps:
            print(f"  - {g}")
        return 1
    print(
        "PASS serve_derive_closed_loop: "
        f"wired_steps={steps}; org population + integrity_observe present"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

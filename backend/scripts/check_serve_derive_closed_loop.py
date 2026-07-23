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
    gaps: list[str] = []
    for step in steps:
        if step == "dc_industry_view":
            token = "dc_industry_view"
        else:
            token = step
        if f'"{token}"' not in plan_src and f"'{token}'" not in plan_src:
            # institution_profile key must appear in plan_process_steps return
            if token not in plan_src:
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
    if "under_populated_accepted" not in (
        REPO / "backend/services/org_holding_aif10.py"
    ).read_text(encoding="utf-8"):
        gaps.append("org_holding missing under_populated_accepted")
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

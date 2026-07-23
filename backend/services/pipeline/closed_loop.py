"""Serve→derive closed-loop helpers (single compute for process plan + gates).

Authority: analysis/serve_derive_closed_loop_law_20260723.md
Config: backend/config/serve_derive_closed_loop.yaml
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

REPO = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO / "backend/config/serve_derive_closed_loop.yaml"
INST_AS_OF_PATH = REPO / "data/reports/institution_profile_as_of.json"


def load_closed_loop_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return dict(raw)


def read_institution_as_of(path: Path | None = None) -> str | None:
    marker = path or INST_AS_OF_PATH
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    frontier = data.get("holders_notice_frontier")
    return str(frontier) if frontier else None


def write_institution_as_of(
    holders_notice_frontier: str,
    *,
    path: Path | None = None,
    rebuild: dict[str, Any] | None = None,
) -> None:
    marker = path or INST_AS_OF_PATH
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holders_notice_frontier": str(holders_notice_frontier),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    if rebuild:
        keep = ("period_windows", "episodes", "profiles", "open", "closed")
        payload["rebuild"] = {
            k: rebuild[k] for k in keep if k in rebuild and rebuild[k] is not None
        }
    marker.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def decide_institution_profile_action(
    *,
    holders_changed: bool,
    holders_notice_frontier: str | None,
    previous_as_of: str | None,
    force_run: bool = False,
) -> dict[str, Any]:
    """Delta-gated institution L2 rebuild decision for process_plan."""
    if force_run:
        return {"action": "run", "reason": "force_run"}
    if holders_changed:
        return {"action": "run", "reason": "holders_state_changed"}
    if previous_as_of is None:
        return {"action": "run", "reason": "inst_as_of_missing"}
    if holders_notice_frontier and str(holders_notice_frontier) != str(previous_as_of):
        return {
            "action": "run",
            "reason": "holders_frontier_ahead_of_inst",
            "holders_notice_frontier": str(holders_notice_frontier),
            "previous_as_of": str(previous_as_of),
        }
    return {
        "action": "skip",
        "reason": "inst_frontier_unchanged",
        "holders_notice_frontier": holders_notice_frontier,
        "previous_as_of": previous_as_of,
    }


def org_population_thresholds(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = (cfg or load_closed_loop_config()).get("org_population") or {}
    return {
        "min_accepted_stocks": int(raw.get("min_accepted_stocks", 500)),
        "min_raw_stocks_for_ratio": int(raw.get("min_raw_stocks_for_ratio", 1000)),
        "min_accepted_over_raw_ratio": float(
            raw.get("min_accepted_over_raw_ratio", 0.5)
        ),
    }


def evaluate_org_population(
    *,
    accepted_stocks: int,
    raw_stocks: int,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Existence≠population: canary accept must not look like ok skip."""
    thr = org_population_thresholds(cfg)
    accepted_n = int(accepted_stocks or 0)
    raw_n = int(raw_stocks or 0)
    ratio = (accepted_n / raw_n) if raw_n > 0 else None
    under = False
    reasons: list[str] = []
    if accepted_n < thr["min_accepted_stocks"]:
        under = True
        reasons.append(
            f"accepted_stocks={accepted_n}<{thr['min_accepted_stocks']}"
        )
    if (
        raw_n >= thr["min_raw_stocks_for_ratio"]
        and ratio is not None
        and ratio < thr["min_accepted_over_raw_ratio"]
    ):
        under = True
        reasons.append(
            f"accepted/raw={ratio:.4f}<{thr['min_accepted_over_raw_ratio']}"
        )
    return {
        "under_populated": under,
        "accepted_stocks": accepted_n,
        "raw_stocks": raw_n,
        "accepted_over_raw_ratio": ratio,
        "reasons": reasons,
        "thresholds": thr,
    }


def wired_process_steps(cfg: dict[str, Any] | None = None) -> list[str]:
    """Process step names that must appear in plan_process_steps for wired surfaces."""
    data = cfg or load_closed_loop_config()
    out: list[str] = []
    for surf in data.get("surfaces") or []:
        if str(surf.get("status") or "").startswith("wired") and surf.get(
            "process_step"
        ):
            out.append(str(surf["process_step"]))
    return out


def seed_institution_as_of_from_holders(
    *,
    holders_conn: Optional[Any] = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Seed as_of from live holders notice frontier so process won't surprise-rebuild.

    Does not rebuild episodes/profiles — only writes the frontier marker when a
    holders notice date is readable.
    """
    from services.duck_adapter import connect
    from services.database_manifest import get_database_manifest

    own = holders_conn is None
    conn = holders_conn
    if conn is None:
        db = get_database_manifest().path_for("smartmoney")
        conn = connect(str(db), read_only=True)
    try:
        row = conn.execute(
            "SELECT MAX(notice_date) FROM canonical_top10_float_holders_period"
        ).fetchone()
    finally:
        if own and conn is not None:
            conn.close()
    frontier = str(row[0]) if row and row[0] else None
    if not frontier:
        return {"status": "skipped", "reason": "no_holders_notice"}
    write_institution_as_of(frontier, path=path)
    return {"status": "seeded", "holders_notice_frontier": frontier}

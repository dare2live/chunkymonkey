"""S5 derive surface — qfq/form rebuild independent of acquire/accept.

Public boundary for ``chunkyctl derive``. Reads accepted canonical (+ authorized
adj_factor / stk_limit inputs) and writes derived analysis tables. Never calls
provider fetch, land, or accept fused helpers.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

DERIVE_TARGETS = ("qfq", "form")

_REPO = Path(__file__).resolve().parents[2]
_QFQ_SCRIPT = _REPO / "backend" / "scripts" / "build_price_kline_qfq_tushare.py"


def run_derive(
    target: str,
    *,
    from_accepted: bool = False,
    rebuild: bool = False,
    check_only: bool = False,
) -> dict[str, Any]:
    """Run one derive target. Raises ValueError for unknown targets."""

    name = str(target or "").strip().lower()
    if name not in DERIVE_TARGETS:
        raise ValueError(
            f"unknown derive target {target!r}; allowed={list(DERIVE_TARGETS)}"
        )
    if name == "qfq":
        return _run_qfq(from_accepted=from_accepted, check_only=check_only)
    return _run_form(from_accepted=from_accepted, rebuild=rebuild)


def _load_qfq_module():
    spec = importlib.util.spec_from_file_location(
        "build_price_kline_qfq_tushare", _QFQ_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load qfq builder at {_QFQ_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_qfq(*, from_accepted: bool, check_only: bool) -> dict[str, Any]:
    mod = _load_qfq_module()
    argv: list[str] = []
    if from_accepted:
        argv.append("--from-accepted")
    if check_only:
        argv.append("--check-only")
    rc = int(mod.main(argv))
    return {
        "target": "qfq",
        "from_accepted": bool(from_accepted),
        "check_only": bool(check_only),
        "returncode": rc,
        "mode": "from_accepted" if from_accepted else "canonical_plus_legacy_fill",
    }


def _run_form(*, from_accepted: bool, rebuild: bool) -> dict[str, Any]:
    from services import technical_states as ts

    if rebuild:
        out = ts.rebuild_all(from_accepted=from_accepted)
        mode = "rebuild_all"
    else:
        out = ts.build_latest(from_accepted=from_accepted)
        mode = str(out.get("mode") or "build_latest")
    return {
        "target": "form",
        "from_accepted": bool(from_accepted),
        "mode": mode,
        "result": out,
    }

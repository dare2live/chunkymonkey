"""Typed daily_update run_outcome — single compute point for exit/wrapper/notify/UI.

Authority: analysis/architecture_fix_treadmill_first_principles_20260722.md §C2.

RunOutcome ∈ {success, soft_waiting_clock, hard_fail}

Downstream (exit code, Script Editor wrapper, macOS notifications, workbench)
MUST render this field — they must not re-infer FAIL from nonzero rc alone.
"""
from __future__ import annotations

import re
from typing import Any, Literal

RunOutcome = Literal["success", "soft_waiting_clock", "hard_fail"]
MsgClass = Literal["hard", "soft", "other"]

OUTCOME_SUCCESS: RunOutcome = "success"
OUTCOME_SOFT_WAITING: RunOutcome = "soft_waiting_clock"
OUTCOME_HARD_FAIL: RunOutcome = "hard_fail"

# Hard blocks — actionable now (auth / preflight / Tier0 / writer lock).
_HARD_RE = re.compile(
    r"(AUTH\s+BLOCK|PREFLIGHT\s+BLOCK|TIER0\s+BLOCK|WRITER\s+BLOCK)",
    re.IGNORECASE,
)

# Soft clock-wait / same-day vacuum — not a defect (plan §C2 examples).
_SOFT_RE = re.compile(
    r"("
    r"pending_publish"
    r"|pre_available_after_zero_rows"
    r"|same_day_vendor_vacuum"
    r"|still_failed\s*=\s*\[([^\]]*)\]"
    r"|sync_registry\s+drain\s+有残余缺口"
    r")",
    re.IGNORECASE,
)

# Map hard subtype → shell exit (preserve existing run.py contract).
_HARD_EXIT = {
    "writer": 2,
    "auth": 3,
    "preflight": 4,
    "tier0": 5,
}

_OUTCOME_RANK = {
    OUTCOME_SUCCESS: 0,
    OUTCOME_SOFT_WAITING: 1,
    OUTCOME_HARD_FAIL: 2,
}

_LABEL_ZH = {
    OUTCOME_SUCCESS: "成功",
    OUTCOME_SOFT_WAITING: "等时钟 / 软观测",
    OUTCOME_HARD_FAIL: "硬失败",
}


def classify_msg(msg: str) -> MsgClass:
    text = str(msg or "")
    if _HARD_RE.search(text):
        return "hard"
    if _SOFT_RE.search(text):
        return "soft"
    return "other"


def _hard_subtype(msgs: list[str]) -> str | None:
    blob = "\n".join(msgs)
    if re.search(r"WRITER\s+BLOCK", blob, re.I):
        return "writer"
    if re.search(r"AUTH\s+BLOCK", blob, re.I):
        return "auth"
    if re.search(r"PREFLIGHT\s+BLOCK", blob, re.I):
        return "preflight"
    if re.search(r"TIER0\s+BLOCK", blob, re.I):
        return "tier0"
    return None


def derive_run_outcome(
    degraded_msgs: list[str] | None,
    *,
    hard_exit_code: int | None = None,
) -> dict[str, Any]:
    """Single compute point: msgs (+ optional hard exit) → typed outcome + exit.

    Rollup (plan §C2): any hard → hard_fail; else any soft/other degraded →
    soft_waiting_clock; else success.

    Adversarial note (Phase 1): non-hard degraded that is not a named clock
    pattern (continuity / SLA / data_audit) still rolls to soft_waiting_clock
    so UI/notify cannot paint honest ops degrade as FAIL. Name is the plan's
    soft bucket; not a claim that every msg is literally pending_publish.
    """
    msgs = [str(m) for m in (degraded_msgs or []) if str(m).strip()]
    classified = [{"msg": m, "class": classify_msg(m)} for m in msgs]

    has_hard = any(c["class"] == "hard" for c in classified) or (
        hard_exit_code is not None and hard_exit_code in {2, 3, 4, 5}
    )
    has_soft_named = any(c["class"] == "soft" for c in classified)
    has_other = any(c["class"] == "other" for c in classified)

    if has_hard:
        outcome: RunOutcome = OUTCOME_HARD_FAIL
    elif has_soft_named or has_other or msgs:
        outcome = OUTCOME_SOFT_WAITING
    else:
        outcome = OUTCOME_SUCCESS

    if outcome == OUTCOME_SUCCESS:
        exit_code = 0
        reason = "clean_success"
    elif outcome == OUTCOME_SOFT_WAITING:
        exit_code = 1
        if has_soft_named and not has_other:
            reason = "soft_waiting_clock"
        elif has_soft_named:
            reason = "soft_waiting_clock_with_ops_observe"
        else:
            reason = "ops_observe_non_hard_degraded"
    else:
        subtype = _hard_subtype(msgs)
        if subtype is None and hard_exit_code in _HARD_EXIT.values():
            # Invert map for explicit hard exits without msg text (writer lock).
            for name, code in _HARD_EXIT.items():
                if code == hard_exit_code:
                    subtype = name
                    break
        subtype = subtype or "tier0"
        exit_code = (
            hard_exit_code
            if hard_exit_code in _HARD_EXIT.values()
            else _HARD_EXIT.get(subtype, 5)
        )
        reason = f"hard_{subtype}"

    return {
        "run_outcome": outcome,
        "run_outcome_label": _LABEL_ZH[outcome],
        "run_outcome_reason": reason,
        "exit_code": int(exit_code),
        "classified": classified,
        "degraded_total": len(msgs),
    }


def outcome_rank(outcome: str | None) -> int:
    return _OUTCOME_RANK.get(str(outcome or ""), -1)


def label_for(outcome: str | None) -> str:
    return _LABEL_ZH.get(str(outcome or ""), str(outcome or "unknown"))


def is_soft_waiting(outcome: str | None) -> bool:
    return str(outcome or "") == OUTCOME_SOFT_WAITING


def is_hard_fail(outcome: str | None) -> bool:
    return str(outcome or "") == OUTCOME_HARD_FAIL

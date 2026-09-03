"""Typed daily_update run_outcome — single compute point for exit/wrapper/notify/UI.

Authority: 本文件 (系统语义, owner 级法条 —— 运行时必须成立, 不是开发纪律; 2026-08-11
P4.1 孤儿法条归位, 此前四态被 8 个代码文件依赖而三份旧 owner contract 零提及;
2026-09 文档大刀后法条正文从旧版顶层设计文档 §5.4 原样搬入本处 (git log --grep
run_outcome_four_states)，不再靠外部文档持有；单一计算点就是本文件)。

四态，穷尽且互斥：

  success              该做的都做了——无任何 degraded                          exit 0
  soft_waiting_clock   在等时钟，不是缺陷——只有**具名**软态                    exit 1
                        (pending_publish / pre_available_after_zero_rows /
                        same_day_vendor_vacuum / drain 残余缺口…)
  integrity_observe    真实的数据/派生洞，**不是**等时钟——有完整性类           exit 1
                        degraded (continuity / residual_hygiene /
                        system_health 自检…)，**或**任何无法归类的 degraded
  hard_fail            现在就得处理，链路已断——AUTH / PREFLIGHT / TIER0 /     exit 2·3·4·5
                        WRITER BLOCK (依次: writer · auth · preflight · tier0)

四条不可放宽的规则：

  1. 归类不明 ≠ 等时钟。认不出的 degraded 归 integrity_observe，不是
     soft_waiting_clock。「等时钟」是需要被证明的具名状态，不是兜底桶——
     反向兜底等于把未知问题渲染成「正常等待」。
  2. 完整性 ≠ 时钟。数据有洞和「今天数据还没发布」是两件事，不许合并成一个
     琥珀色。这条判断同时是 §5.8 判断法典的 L3 (backend/services/pipeline/
     closed_loop.py)；二者是同一条法的两个入口。
  3. 下游只渲染，不重新推断。exit code、Script Editor wrapper、macOS 通知、
     workbench 一律读 run_outcome 字段；**禁止**从「rc != 0」反推 FAIL——
     软态与完整性观测的 rc 都是 1，按 rc 推断会把观测渲染成失败，把
     「日更红了」变成噪音。
  4. 报告 JSON 是真相源，exit 是渲染器。data/reports/daily_*.json 里的
     run_outcome / run_outcome_label / run_outcome_reason /
     run_outcome_exit_code / run_outcome_classified 才是对象；进程退出码
     只是它的一个投影。

Rollup 顺序 (任一命中即定，不再下推)：任何 hard → hard_fail；否则有完整性或
不可归类 → integrity_observe；否则有具名软态 → soft_waiting_clock；否则 success。

Origin: 2026-08-11 P4.1 并入原 engineering_governance.md §3.1「何时不该开刀」§C2
讨论的落点。

RunOutcome ∈ {success, soft_waiting_clock, integrity_observe, hard_fail}

Downstream (exit code, Script Editor wrapper, macOS notifications, workbench)
MUST render this field — they must not re-infer FAIL from nonzero rc alone.
"""
from __future__ import annotations

import re
from typing import Any, Literal

RunOutcome = Literal[
    "success", "soft_waiting_clock", "integrity_observe", "hard_fail"
]
MsgClass = Literal["hard", "soft", "integrity", "other"]

OUTCOME_SUCCESS: RunOutcome = "success"
OUTCOME_SOFT_WAITING: RunOutcome = "soft_waiting_clock"
OUTCOME_INTEGRITY: RunOutcome = "integrity_observe"
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

# Integrity observe — real data/derive holes; not "等时钟".
# system_health = governance_gates.yaml 的运行时自检组前缀 (goal.md 治理重构 P1.2);
# 它报的都是库存/声明生效性问题，属于完整性观测，不是「等时钟」。
_INTEGRITY_RE = re.compile(
    r"("
    r"continuity/integrity"
    r"|system_health"
    r"|residual_hygiene"
    r"|data_audit"
    r"|库存断流"
    r"|under_populated_accepted"
    r"|institution_profile"
    r"|机构档案"
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
    OUTCOME_INTEGRITY: 2,
    OUTCOME_HARD_FAIL: 3,
}

_LABEL_ZH = {
    OUTCOME_SUCCESS: "成功",
    OUTCOME_SOFT_WAITING: "等时钟 / 软观测",
    OUTCOME_INTEGRITY: "完整性观测（非时钟）",
    OUTCOME_HARD_FAIL: "硬失败",
}


def classify_msg(msg: str) -> MsgClass:
    text = str(msg or "")
    if _HARD_RE.search(text):
        return "hard"
    if _SOFT_RE.search(text):
        return "soft"
    if _INTEGRITY_RE.search(text):
        return "integrity"
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

    Rollup: any hard → hard_fail; else any integrity (+ optional soft) →
    integrity_observe; else named soft clock → soft_waiting_clock; else other
    degraded → integrity_observe (unknown degrade is not "等时钟"); else success.
    """
    msgs = [str(m) for m in (degraded_msgs or []) if str(m).strip()]
    classified = [{"msg": m, "class": classify_msg(m)} for m in msgs]

    has_hard = any(c["class"] == "hard" for c in classified) or (
        hard_exit_code is not None and hard_exit_code in {2, 3, 4, 5}
    )
    has_soft_named = any(c["class"] == "soft" for c in classified)
    has_integrity = any(c["class"] == "integrity" for c in classified)
    has_other = any(c["class"] == "other" for c in classified)

    if has_hard:
        outcome: RunOutcome = OUTCOME_HARD_FAIL
    elif has_integrity or has_other:
        outcome = OUTCOME_INTEGRITY
    elif has_soft_named:
        outcome = OUTCOME_SOFT_WAITING
    else:
        outcome = OUTCOME_SUCCESS

    if outcome == OUTCOME_SUCCESS:
        exit_code = 0
        reason = "clean_success"
    elif outcome == OUTCOME_SOFT_WAITING:
        exit_code = 1
        reason = "soft_waiting_clock"
    elif outcome == OUTCOME_INTEGRITY:
        exit_code = 1
        if has_integrity and has_soft_named:
            reason = "integrity_observe_with_soft_clock"
        elif has_integrity:
            reason = "integrity_observe"
        else:
            reason = "ops_observe_non_hard_degraded"
    else:
        subtype = _hard_subtype(msgs)
        if subtype is None and hard_exit_code in _HARD_EXIT.values():
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


def is_integrity_observe(outcome: str | None) -> bool:
    return str(outcome or "") == OUTCOME_INTEGRITY


def is_hard_fail(outcome: str | None) -> bool:
    return str(outcome or "") == OUTCOME_HARD_FAIL

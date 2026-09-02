"""Attest structure products after skeleton recon. Does not cut primaries.

Laws (already in yaml; this recon fails closed if they drift):
- formulas: frozen five × form filter × ``next_tradable_open`` (not same-day close,
  not cyq winner_rate).
- main_rally: setup paper only (B0 bare K / B1 form). Full episode is a stub.
  B3+ / Optuna are not required and not a StrategyRelease.
- institution_follow: PIT = notice/announcement availability, not report-period
  end and not the trade date of the filing.

Paper fills are not product claims. ``claimable=false``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


from services.strategy_spec import (
    FROZEN_FORMULA_IDS,
    REPO,
    load_strategy_package,
    load_strategy_spec,
)

FOLLOW_PIT_OK = frozenset({"notice_available_at", "ann_date", "notice_date"})
FOLLOW_PIT_BANNED = frozenset(
    {"end_date", "REPORT_DATE", "report_date", "trade_date", "close"}
)
CLOSE_ENTRIES = frozenset(
    {"same_day_close", "close", "vwap", "same_bar_close", "decision_close"}
)
RALLY_FORBIDDEN_REQUIRED = frozenset(
    {"B3", "B4", "B5", "b3", "b4", "b5", "optuna", "phase_n_optuna"}
)


def reject_same_day_close_entry(entry_kind: str) -> str:
    kind = str(entry_kind or "").strip()
    if kind in CLOSE_ENTRIES:
        raise ValueError(f"same-day close entry forbidden: {entry_kind!r}")
    return kind


def reject_follow_report_end(anchor: str) -> str:
    raw = str(anchor or "").strip()
    if raw in FOLLOW_PIT_BANNED:
        raise ValueError("follow PIT is announcement/notice, not report-period end")
    if raw not in FOLLOW_PIT_OK:
        raise ValueError(f"follow PIT must be notice/announcement, got {anchor!r}")
    return raw


def reject_rally_requires_b3(required: Sequence[str] | None) -> tuple[str, ...]:
    items = tuple(str(x) for x in (required or ()))
    for item in items:
        token = item.split(":")[0].strip()
        if token in RALLY_FORBIDDEN_REQUIRED or token.upper().startswith("B3"):
            raise ValueError("rally must not require B3+")
    return items


def attest_structure_products(*, repo: Path | None = None) -> dict[str, Any]:
    formulas = load_strategy_package("formulas", repo=repo)[0]
    follow = load_strategy_spec("institution_follow_v1", repo=repo)
    rally = load_strategy_spec("main_rally_v1", repo=repo)
    reject_same_day_close_entry(formulas.entry_kind)
    reject_same_day_close_entry(follow.entry_kind)
    reject_follow_report_end(follow.entry_after)
    reject_rally_requires_b3(rally.applicable_states)
    if formulas.entry_kind != "next_tradable_open":
        raise ValueError("formulas must enter next tradable open")
    if rally.paper_status != "setup_signal_only":
        raise ValueError("main_rally must stay setup_signal_only")
    if rally.exit_kind != "not_implemented_full_episode":
        raise ValueError("main_rally full episode must stay a stub")
    # 2026-09-02 拆锁: 原此处读 strategy_lab.yaml["authorizations"]["phase_n_optuna"]
    # 并在非空时 raise。该配置块已随双钥机制退役, 读回来恒为空 -> 守卫恒不触发。
    # 留着是一道"看起来在把关、实际恒真"的门, 按项目 §15 判据自我验证清单删除而非留死码。
    return {
        "status": "attested",
        "identity": False,
        "claimable": False,
        "primary_cut": False,
        "strategy_release": False,
        "optuna": False,
        "formula_winner_rate": False,
        "formulas": {
            "entry_kind": formulas.entry_kind,
            "entry_after": formulas.entry_after,
            "paper_status": formulas.paper_status,
            "formula_ids": list(FROZEN_FORMULA_IDS),
        },
        "follow": {
            "entry_kind": follow.entry_kind,
            "entry_after": follow.entry_after,
            "paper_status": follow.paper_status,
        },
        "rally": {
            "entry_kind": rally.entry_kind,
            "paper_status": rally.paper_status,
            "exit_kind": rally.exit_kind,
            "b0_b1_default": True,
            "b3_required": False,
        },
        "relation": "paper_is_not_a_product_claim",
    }

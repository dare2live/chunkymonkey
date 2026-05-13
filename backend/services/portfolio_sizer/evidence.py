"""Phase η+++++ — 参数 evidence 追溯框架.

**核心原则**: 任何进入 RiskProfile 的参数都必须标注 ParamEvidence.
- validated  = True  : 有实测脚本支撑 (filled by validate_*.py 报告)
- validated  = False : 拍脑袋 / 用户偏好 / 经典默认 (允许暂存, 但必须标注)

任何 validated=False 的参数都是 **TODO** — 必须列入 todo 跑回测.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EvidenceKind = Literal[
    "validated",       # 实测回测得出
    "weak_proxy",      # 用代理变量验证 (不完美)
    "user_preference", # 用户明确偏好 (业务约束, 不调)
    "classic_default", # 文献/经典 (Kelly 0.5, 2% 止损 etc.)
    "unverified",      # 拍脑袋 — 必须回测
]


@dataclass(frozen=True)
class ParamEvidence:
    """单一参数的实测证据."""
    kind: EvidenceKind
    proof_script: str | None = None      # 验证脚本路径
    proof_date: str | None = None        # 验证日期 (YYYY-MM-DD)
    proof_summary: str | None = None     # 一句话结论
    proof_metric: str | None = None      # 关键指标 (e.g. "IC 60d=0.086")
    follow_up_todo: str | None = None    # 如果未验证, 计划怎么验证

    @property
    def trustworthy(self) -> bool:
        """是否可信进入生产策略."""
        return self.kind in ("validated", "weak_proxy", "user_preference", "classic_default")

    def __repr__(self) -> str:
        emoji = {
            "validated": "🟢",
            "weak_proxy": "🟡",
            "user_preference": "🔵",
            "classic_default": "⚪",
            "unverified": "🔴",
        }.get(self.kind, "?")
        s = self.proof_summary or self.follow_up_todo or "no info"
        return f"{emoji} {self.kind}: {s[:60]}"


# ─────────────────────────────────────────────────────────────────────
# 预定义常用 evidence (避免重复写)
# ─────────────────────────────────────────────────────────────────────

UNVERIFIED = ParamEvidence(kind="unverified",
    follow_up_todo="待 validate_profile_params.py 回测寻优")

USER_PREF = ParamEvidence(kind="user_preference",
    proof_summary="用户业务偏好, 非数据决定")

CLASSIC_KELLY = ParamEvidence(kind="classic_default",
    proof_summary="fractional Kelly 经典实践 (Kelly 1956, Thorp 1962)")

CLASSIC_STOP_LOSS = ParamEvidence(kind="classic_default",
    proof_summary="2% 止损经典 trader 实践")

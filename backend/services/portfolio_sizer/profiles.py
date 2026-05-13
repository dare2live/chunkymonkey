"""3 个 risk profile 参数表 — 短期/中期/长期组合.

策略矩阵 (Phase η+++++: 全部参数加 ParamEvidence 追溯).

⚠ 规则: 任何 evidence.kind='unverified' 的参数都是临时值, 必须列入回测 todo.
⚠ 砍掉 fundamental_stage 排除规则 — 因为派生函数依赖 _latest 表, 历史不可重建,
    无法回测验证. 风控改用可历史化的 technical_stage (代理) + valuation_pe_pctile (待接).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.portfolio_sizer.evidence import (
    CLASSIC_KELLY, CLASSIC_STOP_LOSS, ParamEvidence, UNVERIFIED, USER_PREF,
)


@dataclass(frozen=True)
class RiskProfile:
    profile_id: str
    label: str                       # 中文展示
    max_positions: int               # 持仓数上限
    stock_cap_pct: float             # 单股仓位上限 (0-1)
    kelly_fraction: float            # Kelly 系数
    holding_days: tuple[int, ...]    # 适用 hp 范围
    min_wilson_win: float            # 最低 Wilson 下界胜率
    min_n_signals: int               # 最低样本数
    trailing_pct_min: float          # 最小 trailing 幅度
    trailing_ratio: float            # trailing = max(min, target_ret × ratio)

    # Phase η+++++ 砍掉 fundamental_stage 排除 — 改用可历史化的 technical_stage 风控
    # 旧字段 exclude_fund_stages 仍保留以保持向后兼容, 但置空 (= 不排除任何)
    exclude_fund_stages: tuple[str, ...] = ()  # 不再使用, 见 exclude_tech_stages
    exclude_tech_stages: tuple[str, ...] = ()  # 可历史化代理 (e.g. "4" = 顶部分布)

    # ── evidence 追溯 (每个核心参数一条) ────────────────────────────────
    evidence: dict[str, ParamEvidence] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────
# 共享 evidence
# ─────────────────────────────────────────────────────────────────────

WILSON_60_EV = ParamEvidence(
    kind="unverified",
    follow_up_todo="跑 grid search: Wilson 阈值 [0.50,0.55,0.60,0.65,0.70] × profile, "
                   "看每档阈值的 forward_ret 实测",
)

KELLY_FRAC_EV = ParamEvidence(
    kind="classic_default",
    proof_summary="fractional Kelly 0.5/0.35/0.25 = 全 Kelly × {1/2, 1/3, 1/4}, "
                  "经典风控阶梯 (Thorp & MacLean 2010)",
)

HP_RANGE_EV = ParamEvidence(
    kind="user_preference",
    proof_summary="短/中/长期 = 用户业务定义, 非数据决定",
)

CAP_PCT_EV = ParamEvidence(
    kind="user_preference",
    proof_summary="单股仓位上限 = 用户风险偏好 (短 20% / 中 10% / 长 7%)",
)

MAX_POS_EV = ParamEvidence(
    kind="user_preference",
    proof_summary="持仓数上限 5/10/15 = 用户偏好",
)

MIN_N_EV = ParamEvidence(
    kind="unverified",
    follow_up_todo="跑 grid: min_n_signals ∈ [3,5,8,10,15], "
                   "看不同 n 下 Wilson 过门的实际 win_rate 偏差",
)

TRAIL_MIN_EV = CLASSIC_STOP_LOSS
TRAIL_RATIO_EV = ParamEvidence(
    kind="unverified",
    follow_up_todo="trailing_ratio 0.20 = 拍脑袋, 应跑 backtest 看 trailing 触发频率 vs 收益",
)


# ─────────────────────────────────────────────────────────────────────
# 3 profile
# ─────────────────────────────────────────────────────────────────────

PROFILES = {
    "short": RiskProfile(
        profile_id="short",
        label="短期 (5-15d)",
        max_positions=5,
        stock_cap_pct=0.20,
        kelly_fraction=0.5,
        holding_days=(5, 10, 15),
        min_wilson_win=0.60,
        min_n_signals=5,
        trailing_pct_min=0.02,
        trailing_ratio=0.20,
        exclude_fund_stages=(),    # 砍, 见 file docstring
        exclude_tech_stages=(),    # 待 validate_exclusion_rules.py 决定
        evidence={
            "max_positions": MAX_POS_EV,
            "stock_cap_pct": CAP_PCT_EV,
            "kelly_fraction": KELLY_FRAC_EV,
            "holding_days": HP_RANGE_EV,
            "min_wilson_win": WILSON_60_EV,
            "min_n_signals": MIN_N_EV,
            "trailing_pct_min": TRAIL_MIN_EV,
            "trailing_ratio": TRAIL_RATIO_EV,
            "exclude_tech_stages": ParamEvidence(
                kind="unverified",
                follow_up_todo="跑 (technical_stage × forward_ret) 矩阵, "
                               "看哪些 stage 实测负 alpha 应排除",
            ),
        },
    ),
    "mid": RiskProfile(
        profile_id="mid",
        label="中期 (15-30d)",
        max_positions=10,
        stock_cap_pct=0.10,
        kelly_fraction=0.35,
        holding_days=(15, 20, 30),
        min_wilson_win=0.65,
        min_n_signals=8,
        trailing_pct_min=0.03,
        trailing_ratio=0.20,
        exclude_fund_stages=(),
        exclude_tech_stages=(),
        evidence={
            "max_positions": MAX_POS_EV,
            "stock_cap_pct": CAP_PCT_EV,
            "kelly_fraction": KELLY_FRAC_EV,
            "holding_days": HP_RANGE_EV,
            "min_wilson_win": WILSON_60_EV,
            "min_n_signals": MIN_N_EV,
            "trailing_pct_min": TRAIL_MIN_EV,
            "trailing_ratio": TRAIL_RATIO_EV,
        },
    ),
    "long": RiskProfile(
        profile_id="long",
        label="长期 (30-90d)",
        max_positions=15,
        stock_cap_pct=0.07,
        kelly_fraction=0.25,
        holding_days=(30, 60, 90),
        min_wilson_win=0.70,
        min_n_signals=10,
        trailing_pct_min=0.05,
        trailing_ratio=0.20,
        exclude_fund_stages=(),    # ← Phase η+++++ 砍, 因 fundamental_stage 历史不可重建
        exclude_tech_stages=(),    # ← 待 validate 后决定
        evidence={
            "max_positions": MAX_POS_EV,
            "stock_cap_pct": CAP_PCT_EV,
            "kelly_fraction": KELLY_FRAC_EV,
            "holding_days": HP_RANGE_EV,
            "min_wilson_win": WILSON_60_EV,
            "min_n_signals": MIN_N_EV,
            "trailing_pct_min": TRAIL_MIN_EV,
            "trailing_ratio": TRAIL_RATIO_EV,
            "exclude_fund_stages": ParamEvidence(
                kind="unverified",
                proof_summary="砍 — fundamental_stage 派生依赖 _latest 表, 历史不可重建, 无法 backtest",
                follow_up_todo="改为 exclude_tech_stages, 用可历史化 technical_stage 验证后启用",
            ),
            "exclude_tech_stages": ParamEvidence(
                kind="unverified",
                follow_up_todo="跑 (technical_stage × forward_ret_60d) 验证哪些 stage 应排除",
            ),
        },
    ),
}


def get_profile(profile_id: str) -> RiskProfile:
    if profile_id not in PROFILES:
        raise ValueError(f"unknown profile: {profile_id} (valid: {list(PROFILES.keys())})")
    return PROFILES[profile_id]


def list_profiles() -> list[dict]:
    """前端展示用 (dict 序列化, 含 evidence trace)."""
    out = []
    for p in PROFILES.values():
        ev_summary = {k: repr(v) for k, v in p.evidence.items()}
        unverified_count = sum(1 for v in p.evidence.values() if v.kind == "unverified")
        out.append({
            "profile_id": p.profile_id,
            "label": p.label,
            "max_positions": p.max_positions,
            "stock_cap_pct": p.stock_cap_pct,
            "kelly_fraction": p.kelly_fraction,
            "holding_days": list(p.holding_days),
            "min_wilson_win": p.min_wilson_win,
            "min_n_signals": p.min_n_signals,
            "trailing_pct_min": p.trailing_pct_min,
            "trailing_ratio": p.trailing_ratio,
            "exclude_fund_stages": list(p.exclude_fund_stages),
            "exclude_tech_stages": list(p.exclude_tech_stages),
            "evidence": ev_summary,
            "unverified_count": unverified_count,
        })
    return out

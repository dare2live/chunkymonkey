"""Phase η+++++ — 6 个因子的 0-1 标准化打分 (纯函数, 单一职责).

⚠ 输入: 每股每公式的当日多源数据 (dict)
⚠ 输出: 6 个 0-1 因子分数 (FactorScores)
⚠ 不读 DB, 不写 DB (entry script 负责 I/O)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.buy_signal.configs import (
    FORMULA_ARCHETYPE_PREF, FORMULA_TECH_STAGE_PREF, FUND_STAGE_RISK,
    HISTORICAL_GATES, PRIMARY_TYPE_SCORE,
)


@dataclass(frozen=True)
class FactorScores:
    """8 个 factor 的标准化分数 (各 ∈ [0, 1])."""
    trigger:          float   # 1. 公式当日触发 (0 / 1)
    bucket_match:     float   # 2. 5 维桶吻合度
    historical_alpha: float   # 3. Optuna 寻优 sharpe + win 综合
    stage_fitness:    float   # 4. (fund_stage × tech_stage × formula) 数据驱动适配
    fundamental_stage: float  # 5. 基本面阶段风险 (失效/已充分演绎)
    sentiment:        float   # 6. 调研热度 (热/狂 = 高)
    stock_archetype:  float   # 7. 股票原型 × 公式偏好
    primary_type:     float   # 8. 股票类型 (业绩/价值/周期/事件/技术)


def _normalize(v: float, lo: float, hi: float) -> float:
    """[lo, hi] → [0, 1] 线性裁剪."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


# ─────────────────────────────────────────────────────────────────────
# 各 factor 评分函数 (纯函数)
# ─────────────────────────────────────────────────────────────────────

def score_trigger(triggered_today: bool) -> float:
    """因子 1: 当日公式触发? 0/1 (不触发 = 0, 没什么可议).

    注: 这是 hard gate — 不触发的 (stock × formula) 不会进 buy_signal 表.
    """
    return 1.0 if triggered_today else 0.0


def score_bucket_match(
    today_bucket: Optional[tuple[str, str, str, str]],   # (vol, amt, p60, stage)
    is_best_bucket_in_history: bool,
    historical_n_signals: int,
) -> float:
    """因子 2: 今日 5 维桶 是否在该 (stock × variant) 的历史最佳桶集合内.

    Logic:
        - is_best_bucket = True 且历史 n ≥ 10 → 1.0 (强吻合)
        - is_best_bucket = True 且历史 n ≥ 5 → 0.7
        - is_best_bucket = True 但 n < 5 → 0.4 (样本太少)
        - 不在最佳桶 → 0.0
    """
    if today_bucket is None or not is_best_bucket_in_history:
        return 0.0
    if historical_n_signals >= 10:
        return 1.0
    if historical_n_signals >= 5:
        return 0.7
    return 0.4


def score_historical_alpha(
    sharpe: Optional[float],
    win_rate: Optional[float],
    n_traded: Optional[int],
    gates=HISTORICAL_GATES,
) -> float:
    """因子 3: Optuna 寻优出的历史 sharpe + win_rate 综合.

    score = 0.5 × normalize(sharpe) + 0.5 × normalize(win_rate)
    样本不足 (n_traded < n_traded_min) → 0
    """
    if n_traded is None or n_traded < gates.n_traded_min:
        return 0.0
    if sharpe is None or win_rate is None:
        return 0.0
    s_norm = _normalize(sharpe, gates.sharpe_min, gates.sharpe_max)
    w_norm = _normalize(win_rate, gates.win_rate_min, gates.win_rate_max)
    return 0.5 * s_norm + 0.5 * w_norm


def score_stage_fitness(
    today_fund_stage: Optional[str],
    today_tech_stage: Optional[str],
    formula_variant: str,
    fitness_lookup: Optional[dict] = None,
) -> float:
    """因子 4 (重写): (fund_stage × tech_stage × formula) 数据驱动适配度.

    Phase η+++++ 修正: 用 mart_stage_formula_fitness 实测数据替代硬编码 dict.
        - fitness_lookup[(fund_stage, tech_stage, formula)] = sharpe (越高越好)
        - normalize sharpe ∈ [-0.5, +1.5] → [0, 1]
        - 缺失数据 → fallback 到 FORMULA_TECH_STAGE_PREF (旧硬编码 dict, 兜底)

    Args:
        today_fund_stage: 当日基本面阶段
        today_tech_stage: 当日技术阶段
        formula_variant: 公式变体
        fitness_lookup: {(fund_stage, tech_stage, variant): sharpe} 由 entry script 注入
    """
    # Tier A: 数据驱动 (mart_stage_formula_fitness)
    if fitness_lookup is not None:
        sharpe = fitness_lookup.get((today_fund_stage, today_tech_stage, formula_variant))
        if sharpe is not None:
            # sharpe ∈ [-1.0, +1.0] → [0, 1] 标准化
            # 锚点: sharpe=0 → 0.5 (中性), sharpe=1.0 → 1.0 (强), sharpe=-1.0 → 0.0
            return max(0.0, min(1.0, (sharpe + 1.0) / 2.0))

    # Tier B: fallback 到旧硬编码 (数据缺失时兜底)
    if not today_tech_stage:
        return 0.3
    pref = FORMULA_TECH_STAGE_PREF.get(formula_variant, ())
    if today_tech_stage in pref:
        return 1.0
    if today_tech_stage == "4":   # 顶部分布对所有趋势公式不利
        return 0.0
    stage_order = ("1", "1.5", "2", "3", "4")
    if today_tech_stage in stage_order:
        idx = stage_order.index(today_tech_stage)
        for p in pref:
            if p in stage_order:
                pidx = stage_order.index(p)
                if abs(idx - pidx) == 1:
                    return 0.6
    return 0.3


def score_stock_archetype(
    archetype: Optional[str],
    formula_variant: str,
) -> float:
    """因子 7: 股票原型 (高质量稳健 / 成长兑现 / 周期事件) × 公式偏好.

    Logic: 查 FORMULA_ARCHETYPE_PREF[variant][archetype].
    未知 archetype → 0.5 (中性).
    """
    if not archetype:
        return 0.5
    pref_map = FORMULA_ARCHETYPE_PREF.get(formula_variant, {})
    return pref_map.get(archetype, 0.5)


def score_primary_type(primary_type: Optional[str]) -> float:
    """因子 8: 股票类型 (业绩驱动/价值修复/周期复苏/事件驱动/技术突破).

    Logic: 查 PRIMARY_TYPE_SCORE 表.
    数据缺失 → 0.4 (略低于中性, 因 72% 股票 primary_type 为空, 略扣分激励派生).
    """
    if not primary_type or primary_type == "—":
        return 0.40
    return PRIMARY_TYPE_SCORE.get(primary_type, 0.40)


def score_fundamental_stage(
    fundamental_stage: Optional[str],
) -> float:
    """因子 5: 基本面阶段风险评分 (映射到 [0, 1], 负值视为 0).

    使用 FUND_STAGE_RISK 表:
      失效破坏 -1.0  → 0.0 (硬扣分)
      已充分演绎 -0.5 → 0.0 (软扣分; 注: 因子 5 weight 仅 5%, 不会一票否决)
      周期复苏 +1.0
      ...
    """
    if not fundamental_stage:
        return 0.5  # 中性 (不可考据时不扣分)
    raw = FUND_STAGE_RISK.get(fundamental_stage, 0.5)
    return max(0.0, raw)   # 负值 clamp 到 0


def score_sentiment(
    survey_bin: Optional[str],
    profile_id: str,
) -> float:
    """因子 6: 调研热度 (sentiment) — 仅长期 profile 启用.

    长期 profile:
      狂 → 1.0
      热 → 0.7
      温 → 0.4
      冷 → 0.2
    短期/中期: 0.5 (中性, 不参与加权)
    """
    if profile_id != "long":
        return 0.5
    if not survey_bin:
        return 0.2
    mapping = {"狂": 1.0, "热": 0.7, "温": 0.4, "冷": 0.2}
    return mapping.get(survey_bin, 0.2)


# ─────────────────────────────────────────────────────────────────────
# 聚合函数 (orchestrator)
# ─────────────────────────────────────────────────────────────────────

def aggregate_factors(
    *,
    triggered_today: bool,
    today_bucket: Optional[tuple[str, str, str, str]] = None,
    is_best_bucket: bool = False,
    historical_n_signals: int = 0,
    sharpe: Optional[float] = None,
    win_rate: Optional[float] = None,
    n_traded: Optional[int] = None,
    today_technical_stage: Optional[str] = None,
    formula_variant: str = "",
    fundamental_stage: Optional[str] = None,
    survey_bin: Optional[str] = None,
    profile_id: str = "mid",
    # Phase η+++++ 修正: 新接已派生的形态字段
    stock_archetype: Optional[str] = None,
    primary_type: Optional[str] = None,
    fitness_lookup: Optional[dict] = None,
) -> FactorScores:
    """8 个 factor 一次计算 (Phase η+++++ 修正: 加 stage_fitness 数据驱动 + archetype + primary_type)."""
    return FactorScores(
        trigger=score_trigger(triggered_today),
        bucket_match=score_bucket_match(today_bucket, is_best_bucket, historical_n_signals),
        historical_alpha=score_historical_alpha(sharpe, win_rate, n_traded),
        stage_fitness=score_stage_fitness(
            fundamental_stage, today_technical_stage, formula_variant, fitness_lookup,
        ),
        fundamental_stage=score_fundamental_stage(fundamental_stage),
        sentiment=score_sentiment(survey_bin, profile_id),
        stock_archetype=score_stock_archetype(stock_archetype, formula_variant),
        primary_type=score_primary_type(primary_type),
    )

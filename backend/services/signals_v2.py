"""
signals_v2.py — 极简跟随信号引擎

设计原则（第一性原理）：
    真相只有一张表：fact_institution_event.gain_60d（T+1 开盘买入 持有 60 交易日）
    决策只问一个问题：该机构历史类似事件跟过后的平均收益是多少、胜率多少、样本够吗
    其他一切（风格标签、行业评分、质地评分、qlib）都是 KNN 的特征，不是独立评分

四个可配超参（全部在 app_settings 以 signals.v2.* 前缀）：
    horizon_days          — 持有期，默认 60（对齐现有 gain_60d 列）
    min_sample            — 最小可信样本，默认 10
    ev_threshold_pct      — 跟随门槛：历史 EV% 最低值，默认 5.0
    win_threshold         — 跟随门槛：历史胜率最低值，默认 0.55

不做的事（明确写出来防止以后被加回）：
    ✗ 不合成多维分（composite_priority / pool / setup_priority / gate 等）
    ✗ 不显式打"机构风格"标签（风格由 EV 自然体现）
    ✗ 不加 qlib / 财务 / 行业动量作为独立子分
    ✗ 不加封顶规则 / 拥挤度惩罚 / 外部关注度 boost

命名空间：所有 v2 相关表/配置统一 signals_v2_* / signals.v2.* 前缀，
与 legacy scoring.py 物理隔离，可并存过渡。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from services.utils import safe_float as _safe_float

logger = logging.getLogger("cm-api")


# ─────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────

CONFIG_PREFIX = "signals.v2"

DEFAULT_CONFIG: dict = {
    "horizon_days": 60,
    "min_sample": 10,
    "ev_threshold_pct": 5.0,
    "win_threshold": 0.55,
    "prefer_same_industry_min_sample": 10,
    "signal_freshness_days": 90,
    # 严谨左切：一个事件的 gain_Xd 从 notice_date 起需要 X 个交易日后才能确认。
    # 60 交易日 ≈ 90 日历日。决策时只能用已成熟的历史样本，否则构成 look-ahead bias。
    # 用户可调；设为 0 等同于不严格（对齐老逻辑）。
    "cooldown_days": 90,
    # 双口径 KNN：短期窗口 + 长期全样本并排。分歧触发警示档。
    "short_window_days": 365,
    "short_min_sample": 5,
    # 多维度硬规则过滤（基于健康检查的实证发现）
    # 溢价硬顶：超过此值直接 skip（实证：≤15% 把 OOS edge 从 +0.82 推到 +2.06pp）
    "max_premium_pct": 15.0,
    # 持仓占流通股 % 下限（实证：<0.5% 的胜率仅 53%）
    "min_hold_ratio": 0.3,
    # 机构类型黑名单（实证：这些类型负 alpha）
    "inst_type_blacklist": "基金,国家队",
    # 机构类型硬性加分（这些类型历史上显著 beat blind）
    "inst_type_preferred": "牛散,券商,社保,QFII,北向",
}


@dataclass(frozen=True)
class PolicyConfig:
    horizon_days: int = 60
    min_sample: int = 10
    ev_threshold_pct: float = 5.0
    win_threshold: float = 0.55
    prefer_same_industry_min_sample: int = 10
    signal_freshness_days: int = 90
    cooldown_days: int = 90
    short_window_days: int = 365
    short_min_sample: int = 5
    max_premium_pct: float = 15.0
    min_hold_ratio: float = 0.3
    inst_type_blacklist: str = "基金,国家队"
    inst_type_preferred: str = "牛散,券商,社保,QFII,北向"

    @property
    def blacklist_set(self) -> set[str]:
        return {t.strip() for t in (self.inst_type_blacklist or "").split(",") if t.strip()}

    @property
    def preferred_set(self) -> set[str]:
        return {t.strip() for t in (self.inst_type_preferred or "").split(",") if t.strip()}

    @property
    def gain_column(self) -> str:
        """当前 horizon 对应的 gain_*d 列。"""
        h = self.horizon_days
        if h in (10, 30, 60, 90, 120):
            return f"gain_{h}d"
        raise ValueError(f"horizon_days={h} 没有对应的 gain 列（支持 10/30/60/90/120）")


def load_config(conn) -> PolicyConfig:
    """从 app_settings 读配置，缺省回退到 DEFAULT_CONFIG。"""
    merged = dict(DEFAULT_CONFIG)
    try:
        rows = conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE ?",
            (f"{CONFIG_PREFIX}.%",),
        ).fetchall()
        for row in rows:
            short_key = row["key"][len(CONFIG_PREFIX) + 1:]
            if short_key not in DEFAULT_CONFIG:
                continue
            try:
                default_val = DEFAULT_CONFIG[short_key]
                if isinstance(default_val, str):
                    merged[short_key] = str(row["value"])
                elif isinstance(default_val, int):
                    merged[short_key] = int(float(row["value"]))
                else:
                    merged[short_key] = float(row["value"])
            except (ValueError, TypeError):
                logger.warning(f"[signals_v2] 无效配置 {row['key']}={row['value']}，使用默认值")
    except Exception as exc:
        logger.warning(f"[signals_v2] 读配置失败: {exc}，使用默认值")
    return PolicyConfig(**merged)


def save_config(conn, config: dict) -> None:
    """保存配置到 app_settings，只接受 DEFAULT_CONFIG 里已有的 key。"""
    now = datetime.now().isoformat()
    for key, value in config.items():
        if key not in DEFAULT_CONFIG:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (f"{CONFIG_PREFIX}.{key}", str(value), now),
        )
    conn.commit()
    logger.info(f"[signals_v2] 配置已保存: {config}")


# ─────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────

@dataclass
class EvStats:
    """对一组历史收益样本的聚合统计。"""
    n: int
    ev_pct: Optional[float]          # 平均收益
    win_rate: Optional[float]        # 胜率 0-1
    median_pct: Optional[float]
    p10_pct: Optional[float]
    p90_pct: Optional[float]
    avg_drawdown_pct: Optional[float]  # 同期 max_drawdown 均值

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WindowDecision:
    """一个窗口（短或长）的决策细节。"""
    action: str                # "follow" | "watch" | "skip" | "insufficient"
    scope: str                 # "inst_industry" | "inst_all" | "insufficient"
    stats: EvStats

    def to_dict(self) -> dict:
        return {"action": self.action, "scope": self.scope, "stats": self.stats.to_dict()}


@dataclass
class Recommendation:
    """单个事件的推荐结果（双口径版）。"""
    event_id: str                    # institution_id|stock_code|report_date
    institution_id: str
    institution_name: str
    stock_code: str
    stock_name: str
    industry: Optional[str]
    notice_date: str
    event_type: str
    premium_pct: Optional[float]
    # 最终合并后的决策
    action: str                      # "follow" | "watch" | "skip"
    reason: str                      # 可读说明
    reason_label: str                # 机器可读的分歧标签（如 "both_follow" / "short_follow_long_diverge"）
    # 双口径展开
    short: Optional[WindowDecision] = None   # 短窗口（近 365 天）
    long: Optional[WindowDecision] = None    # 长窗口（全部历史）
    # 兼容旧版：ev_stats/scope 指向 "当前主档" 对应口径的统计
    # 对 follow/watch/skip 合并档来说，默认指向 long 口径展示
    ev_stats: Optional[EvStats] = None
    scope: Optional[str] = None
    # 这个事件本身的 gain（如果已 matured，用于反馈分析）
    realized_return_pct: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "event_id": self.event_id,
            "institution_id": self.institution_id,
            "institution_name": self.institution_name,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "industry": self.industry,
            "notice_date": self.notice_date,
            "event_type": self.event_type,
            "premium_pct": self.premium_pct,
            "action": self.action,
            "reason": self.reason,
            "reason_label": self.reason_label,
            "short": self.short.to_dict() if self.short else None,
            "long": self.long.to_dict() if self.long else None,
            "ev_stats": self.ev_stats.to_dict() if self.ev_stats else None,
            "scope": self.scope,
            "realized_return_pct": self.realized_return_pct,
        }
        return d


# ─────────────────────────────────────────────────────────────────────
# 核心：相似历史事件查询
# ─────────────────────────────────────────────────────────────────────

BUY_EVENT_TYPES: tuple[str, ...] = ("new_entry", "increase")


def fetch_institution_history(
    conn,
    institution_id: str,
    *,
    gain_column: str,
    as_of_date: Optional[str] = None,
    event_types: tuple[str, ...] = BUY_EVENT_TYPES,
    cooldown_days: int = 0,
    limit: Optional[int] = None,
) -> list[dict]:
    """
    拉取一个机构的全历史 buy 事件 + 对应 gain。

    as_of_date: 决策时点（YYYYMMDD 或 YYYY-MM-DD）
    cooldown_days: 成熟延迟。只取 notice_date + cooldown_days < as_of_date 的样本
                   （保证其 gain_Xd 在决策时已完全确认，避免 look-ahead bias）
                   默认 0 = 不严格（对齐老逻辑）；推荐 90（60 trade days）。
    """
    where_parts = [
        "institution_id = ?",
        f"{gain_column} IS NOT NULL",
        f"event_type IN ({','.join('?' for _ in event_types)})",
    ]
    params: list = [institution_id, *event_types]

    if as_of_date:
        if cooldown_days > 0:
            # 严格：past.notice_date + cooldown < as_of_date
            # = past.notice_date < as_of_date - cooldown
            cutoff_date = _shift_date(as_of_date, -cooldown_days)
            where_parts.append("notice_date < ?")
            params.append(cutoff_date or as_of_date)
        else:
            where_parts.append("notice_date < ?")
            params.append(as_of_date)

    sql = f"""
        SELECT
            e.institution_id, e.stock_code, e.stock_name,
            e.report_date, e.notice_date, e.event_type,
            e.premium_pct, e.{gain_column} AS gain,
            e.max_drawdown_30d, e.max_drawdown_60d,
            i.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry i ON i.stock_code = e.stock_code
        WHERE {' AND '.join(where_parts)}
        ORDER BY e.notice_date DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _shift_date(date_str: Optional[str], delta_days: int) -> Optional[str]:
    """YYYYMMDD 或 YYYY-MM-DD 加减 N 日历日。保持输入格式。"""
    if not date_str:
        return None
    raw = str(date_str)
    digits = raw.replace("-", "").replace("/", "")
    if len(digits) < 8 or not digits[:8].isdigit():
        return None
    try:
        dt = datetime.strptime(digits[:8], "%Y%m%d")
    except ValueError:
        return None
    shifted = dt + timedelta(days=delta_days)
    return shifted.strftime("%Y%m%d") if "-" not in raw else shifted.strftime("%Y-%m-%d")


def _filter_history_for_decision(
    timeline: list[dict],
    as_of_date: Optional[str],
    *,
    cooldown_days: int = 0,
    min_notice_date: Optional[str] = None,
) -> list[dict]:
    """
    内存版左切 + cooldown 过滤。用于 backtest / build_today_signals。

    规则：
      - past.notice_date 必须严格早于 as_of_date - cooldown_days
      - 如果 min_notice_date 非空，past.notice_date 还必须晚于等于它（用于短期窗口）

    timeline 需按 notice_date 升序。
    """
    if not as_of_date:
        return list(timeline)
    cutoff = _shift_date(as_of_date, -cooldown_days) or as_of_date
    history = []
    for past in timeline:
        nd = past["notice_date"]
        if nd >= cutoff:
            break  # timeline ASC 排序，后面都更晚
        if min_notice_date is not None and nd < min_notice_date:
            continue
        history.append(past)
    return history


def compute_ev_stats(history: list[dict], *, drawdown_col: str = "max_drawdown_60d") -> EvStats:
    """对一组历史事件计算 EV 统计。"""
    if not history:
        return EvStats(n=0, ev_pct=None, win_rate=None, median_pct=None,
                       p10_pct=None, p90_pct=None, avg_drawdown_pct=None)

    gains = [_safe_float(h.get("gain")) for h in history]
    gains = [g for g in gains if g is not None]
    n = len(gains)
    if n == 0:
        return EvStats(n=0, ev_pct=None, win_rate=None, median_pct=None,
                       p10_pct=None, p90_pct=None, avg_drawdown_pct=None)

    sorted_gains = sorted(gains)
    mean = sum(gains) / n
    win_rate = sum(1 for g in gains if g > 0) / n
    median = sorted_gains[n // 2] if n else None
    p10 = sorted_gains[max(0, int(n * 0.10) - 1)]
    p90 = sorted_gains[min(n - 1, int(n * 0.90))]

    dd_values = [_safe_float(h.get(drawdown_col)) for h in history]
    dd_values = [d for d in dd_values if d is not None]
    avg_dd = sum(dd_values) / len(dd_values) if dd_values else None

    return EvStats(
        n=n,
        ev_pct=round(mean, 2),
        win_rate=round(win_rate, 3),
        median_pct=round(median, 2) if median is not None else None,
        p10_pct=round(p10, 2),
        p90_pct=round(p90, 2),
        avg_drawdown_pct=round(avg_dd, 2) if avg_dd is not None else None,
    )


# ─────────────────────────────────────────────────────────────────────
# 决策函数
# ─────────────────────────────────────────────────────────────────────

def recommend_for_event(
    conn,
    event: dict,
    *,
    config: PolicyConfig,
    as_of_date: Optional[str] = None,
) -> Recommendation:
    """
    对一个事件产出推荐。

    event 必须包含：
        institution_id, institution_name, stock_code, stock_name,
        industry, report_date, notice_date, event_type, premium_pct
        （可选）realized_return_pct

    相似事件查询策略：
        1. 同机构的全历史 buy 事件（as_of_date 之前）
        2. 如果同机构×同行业样本 ≥ prefer_same_industry_min_sample，优先用这个子集
    """
    cutoff = as_of_date or event.get("notice_date")
    # 长窗口：全部历史，cooldown 过滤
    history_long = fetch_institution_history(
        conn,
        event["institution_id"],
        gain_column=config.gain_column,
        as_of_date=cutoff,
        cooldown_days=config.cooldown_days,
    )
    # 短窗口：从 history_long 里再按 short_window_days 截近期
    if config.short_window_days and cutoff:
        short_start = _shift_date(cutoff, -config.short_window_days)
        history_short = [h for h in history_long if h["notice_date"] >= (short_start or "")]
    else:
        history_short = history_long

    decision = _decide_dual_window(event, history_long, history_short, config)
    stats, scope = _pick_display_window(decision)
    reason = _build_reason(decision, config)

    return Recommendation(
        event_id=_event_id(event),
        institution_id=event["institution_id"],
        institution_name=event.get("institution_name") or event["institution_id"],
        stock_code=event["stock_code"],
        stock_name=event.get("stock_name") or "",
        industry=event.get("industry"),
        notice_date=event.get("notice_date") or "",
        event_type=event.get("event_type") or "",
        premium_pct=_safe_float(event.get("premium_pct")),
        action=decision["action"],
        reason=reason,
        reason_label=decision["reason_label"],
        short=WindowDecision(**decision["short"]),
        long=WindowDecision(**decision["long"]),
        ev_stats=stats,
        scope=scope,
        realized_return_pct=_safe_float(event.get("realized_return_pct")),
    )


def _event_id(event: dict) -> str:
    return f"{event.get('institution_id','')}|{event.get('stock_code','')}|{event.get('report_date','')}"


_REASON_LABEL_CN = {
    "both_follow": "短期与长期一致可跟（高置信）",
    "short_follow_long_diverge": "近期表现好但长期不支持，警惕均值回归",
    "long_follow_short_diverge": "长期可跟但近期走弱，警惕策略失效",
    "both_watch": "两个口径都处于边缘档",
    "both_below": "两个口径都低于阈值",
    "long_follow_short_insufficient": "长期可跟，近期样本不足降档观察",
    "short_follow_long_insufficient": "近期可跟但长期样本不足，降档观察",
    "long_only_watch": "仅长期数据有结论",
    "long_only_skip": "仅长期数据有结论",
    "short_only_watch": "仅近期数据有结论",
    "short_only_skip": "仅近期数据有结论",
    "both_insufficient": "短长样本都不足，无法判断",
}


def _build_reason(decision: dict, config: PolicyConfig) -> str:
    """
    根据 _decide_dual_window 输出构造可读 reason 文案。
    """
    label_cn = _REASON_LABEL_CN.get(decision["reason_label"], decision["reason_label"])
    short_stats = decision["short"]["stats"]
    long_stats = decision["long"]["stats"]
    short_txt = (
        f"近{config.short_window_days//30 or 1}月 n={short_stats.n} "
        f"EV {short_stats.ev_pct:+.1f}%" if short_stats.ev_pct is not None else
        f"近{config.short_window_days//30 or 1}月 n={short_stats.n}"
    )
    long_txt = (
        f"全期 n={long_stats.n} EV {long_stats.ev_pct:+.1f}%"
        if long_stats.ev_pct is not None else f"全期 n={long_stats.n}"
    )
    return f"[{label_cn}] {short_txt} · {long_txt}"


def _pick_display_window(decision: dict) -> tuple[Optional[EvStats], Optional[str]]:
    """
    选展示用的主统计（兼容旧版 ev_stats / scope 字段）。
    规则：**永远用长口径**——样本更全、噪音更低。
    短口径信息在 decision["short"] 里单独展示。
    """
    l = decision["long"]
    return l["stats"], l["scope"]


def _decide_single_window(
    event: dict,
    history: list[dict],
    config: PolicyConfig,
    *,
    min_sample_override: Optional[int] = None,
) -> tuple[str, str, EvStats]:
    """
    单一窗口决策（长或短都走这个函数）。
    返回 (action, scope, stats)。
    """
    min_sample = min_sample_override if min_sample_override is not None else config.min_sample
    if len(history) < min_sample:
        return ("skip", "insufficient", compute_ev_stats(history))

    # 优先同行业
    scope = "inst_all"
    filtered = history
    event_industry = event.get("industry")
    if event_industry:
        same_industry = [h for h in history if h.get("industry") == event_industry]
        if len(same_industry) >= config.prefer_same_industry_min_sample:
            filtered = same_industry
            scope = "inst_industry"

    stats = compute_ev_stats(filtered)
    if stats.n == 0 or stats.ev_pct is None:
        return ("skip", "no_valid_gain", stats)

    ev = stats.ev_pct
    wr = stats.win_rate or 0.0

    if ev < config.ev_threshold_pct or wr < config.win_threshold:
        # 边缘档：EV 到门槛的 60% 或胜率到 90% 算 watch
        if ev >= config.ev_threshold_pct * 0.6 or wr >= config.win_threshold * 0.9:
            return ("watch", scope, stats)
        return ("skip", scope, stats)
    return ("follow", scope, stats)


def _merge_double_window_actions(short_action: str, long_action: str) -> tuple[str, str]:
    """
    合并短窗口 + 长窗口两个决策。

    规则：
      - 都 follow → follow (高置信：近期和历史都支持)
      - 短 follow + 长 skip/watch → watch (近期好但历史不一致，警惕均值回归)
      - 短 skip/watch + 长 follow → watch (历史好但近期走弱，警惕失效)
      - 两者都 watch → watch
      - 至少一个 skip 且都不 follow → skip
      - insufficient 按保守处理：任一方 insufficient，另一方决定

    第二个返回是 reason 标签，用于前端解释。
    """
    def _rank(a: str) -> int:
        return {"follow": 3, "watch": 2, "skip": 1, "insufficient": 0}.get(a, 0)

    # 任一为 insufficient（短样本不足很常见），用另一方的结果但降档
    if short_action == "insufficient" and long_action == "insufficient":
        return ("skip", "both_insufficient")
    if short_action == "insufficient":
        # 长有结论，短样本不够 → 按长的，但如是 follow 降到 watch
        if long_action == "follow":
            return ("watch", "long_follow_short_insufficient")
        return (long_action, f"long_only_{long_action}")
    if long_action == "insufficient":
        if short_action == "follow":
            return ("watch", "short_follow_long_insufficient")
        return (short_action, f"short_only_{short_action}")

    # 两者都有结论
    if short_action == "follow" and long_action == "follow":
        return ("follow", "both_follow")
    if short_action == "follow" and long_action != "follow":
        return ("watch", "short_follow_long_diverge")
    if short_action != "follow" and long_action == "follow":
        return ("watch", "long_follow_short_diverge")
    # 没有一方是 follow：取更严的那个
    if short_action == "skip" or long_action == "skip":
        return ("skip", "both_below")
    return ("watch", "both_watch")


def _decide_from_history(
    event: dict,
    history: list[dict],
    config: PolicyConfig,
) -> tuple[str, str]:
    """
    单口径接口（兼容旧调用点）：只用长窗口决策。
    新代码应直接用 _decide_dual_window 拿到短+长两套统计。
    """
    long_action, long_scope, _ = _decide_single_window(event, history, config)
    return (long_action, long_scope)


def _apply_hard_rules(event: dict, config: PolicyConfig) -> tuple[Optional[str], Optional[str]]:
    """
    应用基于 cohort 实证的硬规则过滤。
    返回 (action, reason_label) 若命中硬规则，否则 (None, None) 让后续 KNN 决定。

    硬规则（都来自 health check 的数据）：
      - 机构类型黑名单 (基金/国家队) → skip
      - premium > max_premium_pct → skip
      - hold_ratio < min_hold_ratio → skip
    """
    inst_type = event.get("inst_type")
    if inst_type and inst_type in config.blacklist_set:
        return ("skip", "inst_type_blacklisted")

    premium = event.get("premium_pct")
    if premium is not None and premium > config.max_premium_pct:
        return ("skip", "premium_too_high")

    hold_ratio = event.get("hold_ratio")
    if hold_ratio is not None and hold_ratio < config.min_hold_ratio:
        return ("skip", "hold_ratio_too_low")

    return (None, None)


def _decide_dual_window(
    event: dict,
    history_long: list[dict],
    history_short: list[dict],
    config: PolicyConfig,
) -> dict:
    """
    双口径决策（短期 + 长期）+ 硬规则前筛。

    流程：
      1. 硬规则过滤（基于实证：机构类型/溢价/持仓比例）
      2. 双口径 KNN (short + long)
      3. 合并两个窗口的 action

    Returns:
        {
          "action": "follow" | "watch" | "skip",
          "reason_label": ...,
          "short": {"action": ..., "scope": ..., "stats": EvStats},
          "long":  {"action": ..., "scope": ..., "stats": EvStats},
        }
    """
    # Step 1: 硬规则前筛
    hard_action, hard_label = _apply_hard_rules(event, config)

    # Step 2: 计算双口径 KNN（即使命中硬规则也计算，用于展示）
    short_action, short_scope, short_stats = _decide_single_window(
        event, history_short, config, min_sample_override=config.short_min_sample,
    )
    long_action, long_scope, long_stats = _decide_single_window(
        event, history_long, config,
    )
    knn_action, knn_label = _merge_double_window_actions(short_action, long_action)

    # Step 3: 硬规则优先（比 KNN 更保守时覆盖）
    if hard_action == "skip":
        action, reason_label = hard_action, hard_label
    else:
        action, reason_label = knn_action, knn_label

    return {
        "action": action,
        "reason_label": reason_label,
        "short": {"action": short_action, "scope": short_scope, "stats": short_stats},
        "long":  {"action": long_action,  "scope": long_scope,  "stats": long_stats},
        "hard_rule_hit": hard_label if hard_action else None,
    }


# ─────────────────────────────────────────────────────────────────────
# 今日信号生成
# ─────────────────────────────────────────────────────────────────────

def build_today_signals(
    conn,
    *,
    config: Optional[PolicyConfig] = None,
    freshness_days: Optional[int] = None,
) -> list[Recommendation]:
    """
    生成"今日信号"列表：最近 freshness_days 天内新公告的 buy 事件，每个事件跑 recommend。

    这是 legacy fact_setup_snapshot / mart_stock_trend 的替代方案。
    """
    cfg = config or load_config(conn)
    fresh_days = freshness_days or cfg.signal_freshness_days

    # 最近 N 天新 buy 事件。notice_date 在库里是 YYYYMMDD 无分隔符，
    # SQLite date() 函数对这种格式无法解析，需在查询端重新拼接。
    rows = conn.execute(f"""
        SELECT
            e.institution_id, i.display_name AS institution_name, i.type AS inst_type,
            e.stock_code, e.stock_name,
            e.report_date, e.notice_date, e.event_type,
            e.premium_pct, e.{cfg.gain_column} AS realized_return_pct,
            ind.sw_level1 AS industry,
            h.holder_rank, h.hold_ratio
        FROM fact_institution_event e
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        LEFT JOIN inst_holdings h ON h.institution_id = e.institution_id
               AND h.stock_code = e.stock_code AND h.report_date = e.report_date
        WHERE e.event_type IN ('new_entry', 'increase')
          AND e.notice_date IS NOT NULL
          AND (
              CASE
                  WHEN length(e.notice_date) = 8 AND instr(e.notice_date, '-') = 0
                      THEN substr(e.notice_date,1,4) || '-' || substr(e.notice_date,5,2) || '-' || substr(e.notice_date,7,2)
                  ELSE e.notice_date
              END
          ) >= date('now', ?)
        ORDER BY e.notice_date DESC, e.institution_id
    """, (f"-{fresh_days} days",)).fetchall()

    if not rows:
        return []

    # 性能优化：一次 SQL 拉全部相关机构的历史 buy 事件，内存做 KNN 查询
    # 避免每个事件独立 SQL (1307 事件 * 30ms JOIN = 40 秒)
    institution_ids = {row["institution_id"] for row in rows}
    placeholders = ",".join("?" * len(institution_ids))
    all_history_rows = conn.execute(f"""
        SELECT e.institution_id, e.notice_date,
               e.{cfg.gain_column} AS gain,
               e.max_drawdown_30d, e.max_drawdown_60d,
               ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry','increase')
          AND e.{cfg.gain_column} IS NOT NULL
          AND e.institution_id IN ({placeholders})
        ORDER BY e.institution_id, e.notice_date ASC
    """, list(institution_ids)).fetchall()

    inst_timeline: dict[str, list[dict]] = {}
    for r in all_history_rows:
        inst_timeline.setdefault(r["institution_id"], []).append(dict(r))

    signals = []
    for row in rows:
        event = dict(row)
        timeline = inst_timeline.get(event["institution_id"], [])
        # 长窗口：严格左切 + cooldown
        history_long = _filter_history_for_decision(
            timeline, event["notice_date"], cooldown_days=cfg.cooldown_days,
        )
        # 短窗口：长窗口的最近 short_window_days 段
        short_start = _shift_date(event["notice_date"], -cfg.short_window_days) or ""
        history_short = [h for h in history_long if h["notice_date"] >= short_start]

        decision = _decide_dual_window(event, history_long, history_short, cfg)
        stats, scope = _pick_display_window(decision)
        reason = _build_reason(decision, cfg)

        signals.append(Recommendation(
            event_id=_event_id(event),
            institution_id=event["institution_id"],
            institution_name=event.get("institution_name") or event["institution_id"],
            stock_code=event["stock_code"],
            stock_name=event.get("stock_name") or "",
            industry=event.get("industry"),
            notice_date=event.get("notice_date") or "",
            event_type=event.get("event_type") or "",
            premium_pct=_safe_float(event.get("premium_pct")),
            action=decision["action"],
            reason=reason,
            reason_label=decision["reason_label"],
            short=WindowDecision(**decision["short"]),
            long=WindowDecision(**decision["long"]),
            ev_stats=stats,
            scope=scope,
            realized_return_pct=_safe_float(event.get("realized_return_pct")),
        ))

    # 按 action 分组，follow > watch > skip，同组按 long EV 降序
    action_rank = {"follow": 0, "watch": 1, "skip": 2}
    def _sort_key(r: Recommendation):
        ev = r.ev_stats.ev_pct if r.ev_stats else None
        n = r.ev_stats.n if r.ev_stats else 0
        return (action_rank.get(r.action, 9), -(ev or -999), -(n or 0))
    signals.sort(key=_sort_key)
    return signals


# ─────────────────────────────────────────────────────────────────────
# 机构 track record（不计算 quality_score，只展示真相）
# ─────────────────────────────────────────────────────────────────────

def institution_track_record(
    conn,
    institution_id: str,
    *,
    config: Optional[PolicyConfig] = None,
) -> dict:
    """
    展示一个机构"自己打过的"完整成绩单，不做评分，只呈现原始数字。
    """
    cfg = config or load_config(conn)
    history = fetch_institution_history(
        conn,
        institution_id,
        gain_column=cfg.gain_column,
    )
    overall = compute_ev_stats(history)

    # 分行业
    industry_stats = {}
    for h in history:
        ind = h.get("industry") or "(无行业)"
        industry_stats.setdefault(ind, []).append(h)
    industry_breakdown = [
        {
            "industry": ind,
            **compute_ev_stats(events).to_dict(),
        }
        for ind, events in industry_stats.items()
        if len(events) >= cfg.min_sample  # 至少够一个可信样本量
    ]
    industry_breakdown.sort(key=lambda x: -(x["ev_pct"] or 0))

    # 多周期对比（与 overall 同机构的买入事件，只是换 horizon）
    multi_horizon = institution_multi_horizon(conn, institution_id)

    return {
        "institution_id": institution_id,
        "horizon_days": cfg.horizon_days,
        "overall": overall.to_dict(),
        "by_industry": industry_breakdown,
        "by_horizon": multi_horizon["horizons"],
        "recent_events_sample": [_history_row_public(h) for h in history[:20]],
    }


def _history_row_public(row: dict) -> dict:
    """history row 精简为前端需要的字段。"""
    return {
        "stock_code": row.get("stock_code"),
        "stock_name": row.get("stock_name"),
        "industry": row.get("industry"),
        "notice_date": row.get("notice_date"),
        "event_type": row.get("event_type"),
        "premium_pct": row.get("premium_pct"),
        "gain": row.get("gain"),
    }


def fetch_similar_for_event(
    conn,
    event_id: str,
    *,
    config: Optional[PolicyConfig] = None,
    limit: int = 50,
) -> dict:
    """
    详情页抽屉用：给定一个 event_id，拉出它 recommendation 时用到的历史样本。
    """
    cfg = config or load_config(conn)
    try:
        inst_id, stock_code, report_date = event_id.split("|")
    except ValueError:
        return {"error": f"invalid event_id: {event_id}"}

    row = conn.execute("""
        SELECT e.institution_id, e.stock_code, e.report_date, e.notice_date, e.event_type,
               e.premium_pct, ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        WHERE e.institution_id = ? AND e.stock_code = ? AND e.report_date = ?
    """, (inst_id, stock_code, report_date)).fetchone()

    if not row:
        return {"error": "event_not_found"}

    event = dict(row)
    cutoff = event["notice_date"]
    history = fetch_institution_history(
        conn,
        inst_id,
        gain_column=cfg.gain_column,
        as_of_date=cutoff,
    )

    # 优先同行业
    event_industry = event.get("industry")
    scope = "inst_all"
    filtered = history
    if event_industry:
        same_industry = [h for h in history if h.get("industry") == event_industry]
        if len(same_industry) >= cfg.prefer_same_industry_min_sample:
            filtered = same_industry
            scope = "inst_industry"

    # 截最近 N 条
    filtered_public = [_history_row_public(h) for h in filtered[:limit]]
    stats = compute_ev_stats(filtered)

    return {
        "event_id": event_id,
        "scope": scope,
        "as_of_date": cutoff,
        "stats": stats.to_dict(),
        "samples": filtered_public,
    }


# ─────────────────────────────────────────────────────────────────────
# 历史回测（baseline P&L）
# ─────────────────────────────────────────────────────────────────────

def backtest_historical(
    conn,
    *,
    config: Optional[PolicyConfig] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    对所有历史 buy 事件跑 recommend()，分组汇总。

    性能优化：一次性 SQL 拉全量，全部在内存做 KNN 查找，避免 N 次 SQL。
    30K 事件约 3-5 秒完成。

    返回：
        {
          "config": {...},
          "coverage": {"total_events": N, "follow": Nf, "watch": Nw, "skip": Ns},
          "follow_policy": {"n", "ev_pct", "win_rate", "median_pct", "avg_drawdown_pct"},
          "watch_policy":  {...},
          "skip_policy":   {...},
          "blind_buy":     {...},   # 不筛选盲跟所有 buy 事件作对照
          "equity_curve":  [{"date":..., "follow_equity":..., "blind_equity":...}, ...]
        }
    """
    cfg = config or load_config(conn)

    where = [
        f"e.{cfg.gain_column} IS NOT NULL",
        "e.event_type IN ('new_entry','increase')",
        "e.notice_date IS NOT NULL",
    ]
    params: list = []
    if start_date:
        where.append("e.notice_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("e.notice_date <= ?")
        params.append(end_date)

    sql = f"""
        SELECT
            e.institution_id,
            e.stock_code, e.stock_name, e.report_date, e.notice_date, e.event_type,
            e.premium_pct, e.{cfg.gain_column} AS gain,
            ind.sw_level1 AS industry,
            i.type AS inst_type,
            h.holder_rank, h.hold_ratio
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN inst_holdings h ON h.institution_id = e.institution_id
               AND h.stock_code = e.stock_code AND h.report_date = e.report_date
        WHERE {' AND '.join(where)}
        ORDER BY e.notice_date ASC
    """
    events = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not events:
        return {"error": "no_events", "config": asdict(cfg)}

    # 按机构聚合全历史（已按 notice_date ASC 排序），内存中做 KNN 查找
    inst_timeline: dict[str, list[dict]] = {}
    for ev in events:
        inst_timeline.setdefault(ev["institution_id"], []).append(ev)

    buckets = {"follow": [], "watch": [], "skip": []}
    for ev in events:
        inst_id = ev["institution_id"]
        notice_date = ev["notice_date"]
        timeline = inst_timeline[inst_id]
        # 长窗口：cooldown 严格左切
        history_long = _filter_history_for_decision(
            timeline, notice_date, cooldown_days=cfg.cooldown_days,
        )
        # 短窗口
        short_start = _shift_date(notice_date, -cfg.short_window_days) or ""
        history_short = [h for h in history_long if h["notice_date"] >= short_start]

        decision = _decide_dual_window(ev, history_long, history_short, cfg)
        action = decision["action"]
        gain = _safe_float(ev.get("gain"))
        if gain is None:
            continue
        buckets[action].append({
            "notice_date": notice_date,
            "gain": gain,
            "institution_id": inst_id,
            "stock_code": ev["stock_code"],
            "reason_label": decision["reason_label"],
        })

    summary = {
        "config": asdict(cfg),
        "coverage": {
            "total_events": len(events),
            "follow": len(buckets["follow"]),
            "watch": len(buckets["watch"]),
            "skip": len(buckets["skip"]),
        },
    }

    def _pack(bucket_name: str, rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        gains = [r["gain"] for r in rows]
        return {
            "n": len(gains),
            "ev_pct": round(sum(gains) / len(gains), 2),
            "win_rate": round(sum(1 for g in gains if g > 0) / len(gains), 3),
            "median_pct": round(sorted(gains)[len(gains)//2], 2),
            "min_pct": round(min(gains), 2),
            "max_pct": round(max(gains), 2),
        }

    summary["follow_policy"] = _pack("follow", buckets["follow"])
    summary["watch_policy"] = _pack("watch", buckets["watch"])
    summary["skip_policy"] = _pack("skip", buckets["skip"])

    # 盲跟（对照组）：所有 buy 事件不筛
    all_rows = [{"gain": ev["gain"], "notice_date": ev["notice_date"]} for ev in events]
    summary["blind_buy"] = _pack("all", all_rows)

    # 季度趋势：按事件入场季度汇总（而非"权益曲线"，避免重叠持仓的误导）
    summary["quarterly_trend"] = _build_quarterly_trend(buckets["follow"], all_rows)

    # Top / Bottom 事件（用于调试）
    sorted_follow = sorted(buckets["follow"], key=lambda r: -r["gain"])
    summary["top_follow_samples"] = sorted_follow[:5]
    summary["bottom_follow_samples"] = sorted_follow[-5:] if len(sorted_follow) > 5 else []

    # 分机构表现（只看入 follow 档的）
    inst_agg = {}
    for r in buckets["follow"]:
        inst_agg.setdefault(r["institution_id"], []).append(r["gain"])
    inst_breakdown = []
    for inst, gains in inst_agg.items():
        if len(gains) < 3:
            continue
        inst_breakdown.append({
            "institution_id": inst,
            "n": len(gains),
            "ev_pct": round(sum(gains) / len(gains), 2),
            "win_rate": round(sum(1 for g in gains if g > 0) / len(gains), 3),
        })
    inst_breakdown.sort(key=lambda x: -x["ev_pct"])
    summary["top_institutions_in_follow"] = inst_breakdown[:20]

    return summary


def _normalize_ymd_key(date_str: Optional[str]) -> Optional[str]:
    """把 notice_date（YYYYMMDD 或 YYYY-MM-DD）归一为 YYYY-MM-DD，用于分组 key。"""
    if not date_str:
        return None
    digits = str(date_str).replace("-", "").replace("/", "")
    if len(digits) >= 8 and digits[:8].isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return None


def _quarter_key(date_str: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD → YYYY-Q1/Q2/Q3/Q4"""
    norm = _normalize_ymd_key(date_str)
    if not norm:
        return None
    month = int(norm[5:7])
    q = (month - 1) // 3 + 1
    return f"{norm[:4]}-Q{q}"


def _build_quarterly_trend(follow_rows: list[dict], all_rows: list[dict]) -> list[dict]:
    """
    按事件入场季度汇总 EV 与胜率。
    纯统计视角——不假装是"账户权益曲线"（60 天持仓本质上会跨季度重叠）。
    给用户看"筛选 vs 不筛"在每个季度的差距。
    """
    def _group(rows):
        g = {}
        for r in rows:
            k = _quarter_key(r.get("notice_date"))
            if k:
                g.setdefault(k, []).append(r["gain"])
        return g

    follow_q = _group(follow_rows)
    blind_q = _group(all_rows)

    all_quarters = sorted(set(follow_q.keys()) | set(blind_q.keys()))
    trend = []
    for q in all_quarters:
        f_gains = follow_q.get(q, [])
        b_gains = blind_q.get(q, [])
        trend.append({
            "quarter": q,
            "follow_n": len(f_gains),
            "follow_ev_pct": round(sum(f_gains)/len(f_gains), 2) if f_gains else None,
            "follow_win_rate": round(sum(1 for g in f_gains if g > 0)/len(f_gains), 3) if f_gains else None,
            "blind_n": len(b_gains),
            "blind_ev_pct": round(sum(b_gains)/len(b_gains), 2) if b_gains else None,
            "blind_win_rate": round(sum(1 for g in b_gains if g > 0)/len(b_gains), 3) if b_gains else None,
        })
    return trend


# ─────────────────────────────────────────────────────────────────────
# 反馈闭环：最近已成熟 cohort 的实际表现
# ─────────────────────────────────────────────────────────────────────

def cohort_recent_matured(
    conn,
    *,
    lookback_days: int = 180,
    config: Optional[PolicyConfig] = None,
) -> dict:  # noqa: C901 — 复杂度可接受
    """
    最近 N 天内已成熟（gain_60d 非空）的事件 cohort 的实际表现。

    意义：打开页面时就能看到"过去系统推荐的 follow 档，实际跑出来了吗"——
    不是拿回测在自己的数据上吹，而是看真实走完 60 天的事件结果。

    Returns:
        {
          "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "lookback_days": N},
          "cohort_size": int,
          "by_bucket": {
              "follow": {"n", "ev_pct", "win_rate", "median_pct"},
              "watch":  {...},
              "skip":   {...},
              "blind":  {...},
          },
          "edge_vs_blind": {
              "follow": {"ev_diff_pct", "win_diff_pp"},
              ...
          }
        }
    """
    cfg = config or load_config(conn)

    # 计算窗口：只看已成熟（有 gain_60d 的）的事件
    # gain_60d 需要 60 交易日 → 约 90 日历日后才可用
    end_date = datetime.now() - timedelta(days=90)
    start_date = end_date - timedelta(days=lookback_days)
    # 转成 YYYYMMDD 与 DB 格式一致
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    rows = conn.execute(f"""
        SELECT
            e.institution_id, e.stock_code, e.stock_name,
            e.report_date, e.notice_date, e.event_type,
            e.premium_pct, e.{cfg.gain_column} AS gain,
            ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry','increase')
          AND e.{cfg.gain_column} IS NOT NULL
          AND e.notice_date >= ?
          AND e.notice_date <= ?
        ORDER BY e.notice_date ASC
    """, (start_str, end_str)).fetchall()

    events = [dict(r) for r in rows]
    if not events:
        return {
            "window": {
                "start": start_str, "end": end_str, "lookback_days": lookback_days,
            },
            "cohort_size": 0,
            "by_bucket": {}, "edge_vs_blind": {},
            "note": "窗口内无已成熟事件",
        }

    # 预加载所有历史 buy 事件，供 KNN 左切查询
    all_events_rows = conn.execute(f"""
        SELECT e.institution_id, e.notice_date, e.{cfg.gain_column} AS gain,
               ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry','increase')
          AND e.{cfg.gain_column} IS NOT NULL
        ORDER BY e.notice_date ASC
    """).fetchall()

    timeline: dict[str, list[dict]] = {}
    for ev in all_events_rows:
        timeline.setdefault(ev["institution_id"], []).append(dict(ev))

    # 分档（cohort 验证走双口径 + cooldown）
    buckets: dict[str, list[float]] = {"follow": [], "watch": [], "skip": []}
    for ev in events:
        timeline_i = timeline.get(ev["institution_id"], [])
        history_long = _filter_history_for_decision(
            timeline_i, ev["notice_date"], cooldown_days=cfg.cooldown_days,
        )
        short_start = _shift_date(ev["notice_date"], -cfg.short_window_days) or ""
        history_short = [h for h in history_long if h["notice_date"] >= short_start]
        decision = _decide_dual_window(ev, history_long, history_short, cfg)
        gain = _safe_float(ev.get("gain"))
        if gain is None:
            continue
        buckets[decision["action"]].append(gain)

    all_gains = [float(e["gain"]) for e in events if e.get("gain") is not None]

    def _pack(gains: list[float]) -> dict:
        if not gains:
            return {"n": 0}
        n = len(gains)
        ev_pct = sum(gains) / n
        wr = sum(1 for g in gains if g > 0) / n
        median = sorted(gains)[n // 2]
        return {
            "n": n,
            "ev_pct": round(ev_pct, 2),
            "win_rate": round(wr, 3),
            "median_pct": round(median, 2),
        }

    packed = {
        "follow": _pack(buckets["follow"]),
        "watch": _pack(buckets["watch"]),
        "skip": _pack(buckets["skip"]),
        "blind": _pack(all_gains),
    }

    # Edge vs blind
    blind_ev = packed["blind"].get("ev_pct", 0) or 0
    blind_wr = packed["blind"].get("win_rate", 0) or 0
    edge = {}
    for b in ("follow", "watch", "skip"):
        pb = packed[b]
        if pb.get("ev_pct") is None:
            continue
        edge[b] = {
            "ev_diff_pct": round(pb["ev_pct"] - blind_ev, 2),
            "win_diff_pp": round((pb["win_rate"] - blind_wr) * 100, 1),
        }

    return {
        "window": {
            "start": f"{start_str[:4]}-{start_str[4:6]}-{start_str[6:8]}",
            "end": f"{end_str[:4]}-{end_str[4:6]}-{end_str[6:8]}",
            "lookback_days": lookback_days,
        },
        "cohort_size": len(events),
        "by_bucket": packed,
        "edge_vs_blind": edge,
    }


# ─────────────────────────────────────────────────────────────────────
# 机构多周期 track record（30/60/90/120 天对比）
# ─────────────────────────────────────────────────────────────────────

def institution_multi_horizon(conn, institution_id: str) -> dict:
    """
    展示机构在不同持有期的 EV / 胜率对比。
    让用户看出"这个机构的 edge 是短线还是长线"。
    """
    horizons = [30, 60, 90, 120]
    out = {"institution_id": institution_id, "horizons": []}

    for h in horizons:
        col = f"gain_{h}d"
        try:
            rows = conn.execute(f"""
                SELECT e.{col} AS gain
                FROM fact_institution_event e
                WHERE e.institution_id = ?
                  AND e.event_type IN ('new_entry','increase')
                  AND e.{col} IS NOT NULL
            """, (institution_id,)).fetchall()
        except Exception:
            rows = []

        gains = [float(r["gain"]) for r in rows if r["gain"] is not None]
        if not gains:
            out["horizons"].append({"horizon_days": h, "n": 0})
            continue

        n = len(gains)
        out["horizons"].append({
            "horizon_days": h,
            "n": n,
            "ev_pct": round(sum(gains) / n, 2),
            "win_rate": round(sum(1 for g in gains if g > 0) / n, 3),
            "median_pct": round(sorted(gains)[n // 2], 2),
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# 模块 init（app_settings 初始化默认值）
# ─────────────────────────────────────────────────────────────────────

def ensure_defaults(conn) -> None:
    """确保 app_settings 里有默认配置（只在缺失时插入，不覆盖用户值）。"""
    now = datetime.now().isoformat()
    for key, value in DEFAULT_CONFIG.items():
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (f"{CONFIG_PREFIX}.{key}", str(value), now),
        )
    conn.commit()

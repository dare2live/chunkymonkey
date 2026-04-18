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
from datetime import datetime
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
}


@dataclass(frozen=True)
class PolicyConfig:
    horizon_days: int = 60
    min_sample: int = 10
    ev_threshold_pct: float = 5.0
    win_threshold: float = 0.55
    prefer_same_industry_min_sample: int = 10
    signal_freshness_days: int = 90

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
                if isinstance(default_val, int):
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
class Recommendation:
    """单个事件的推荐结果。"""
    event_id: str                    # institution_id|stock_code|report_date
    institution_id: str
    institution_name: str
    stock_code: str
    stock_name: str
    industry: Optional[str]
    notice_date: str
    event_type: str
    premium_pct: Optional[float]
    # 决策
    action: str                      # "follow" | "watch" | "skip"
    reason: str                      # 可读说明
    scope: str                       # similarity scope used: "inst_industry" | "inst_all" | "insufficient"
    ev_stats: EvStats                # 历史样本聚合
    # 这个事件本身的 gain（如果已 matured，用于反馈分析）
    realized_return_pct: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ev_stats"] = self.ev_stats.to_dict()
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
    limit: Optional[int] = None,
) -> list[dict]:
    """
    拉取一个机构的全历史 buy 事件 + 对应 gain。

    as_of_date: 严格左切——只取 notice_date < as_of_date 的事件
               （避免 look-ahead bias，回测必须传）
    """
    where_parts = [
        "institution_id = ?",
        f"{gain_column} IS NOT NULL",
        f"event_type IN ({','.join('?' for _ in event_types)})",
    ]
    params: list = [institution_id, *event_types]

    if as_of_date:
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
    history = fetch_institution_history(
        conn,
        event["institution_id"],
        gain_column=config.gain_column,
        as_of_date=cutoff,
    )

    action, scope = _decide_from_history(event, history, config)

    # 根据 scope 选择用于展示的 stats（follow/watch 展示 filtered 子集；skip 展示全集）
    event_industry = event.get("industry")
    if scope == "inst_industry" and event_industry:
        filtered = [h for h in history if h.get("industry") == event_industry]
        stats = compute_ev_stats(filtered)
    else:
        stats = compute_ev_stats(history)

    ev = stats.ev_pct if stats.ev_pct is not None else 0.0
    wr = stats.win_rate if stats.win_rate is not None else 0.0

    # 构造可读 reason
    if action == "skip" and scope == "insufficient":
        reason = f"样本不足(n={stats.n})，无法判断"
    elif action == "follow":
        scope_label = {"inst_industry": "同机构·同行业", "inst_all": "同机构·全行业"}.get(scope, scope)
        reason = f"[{scope_label} n={stats.n}] 历史 EV {ev:.1f}% / 胜率 {wr*100:.0f}%"
    elif action == "watch":
        reason = f"历史 EV {ev:.1f}% / 胜率 {wr*100:.0f}%，边缘档（阈值 {config.ev_threshold_pct:.0f}% / {config.win_threshold*100:.0f}%）"
    else:
        reason = f"历史 EV {ev:.1f}% / 胜率 {wr*100:.0f}%，低于阈值"

    return Recommendation(
        event_id=_event_id(event),
        institution_id=event["institution_id"],
        institution_name=event.get("institution_name") or event["institution_id"],
        stock_code=event["stock_code"],
        stock_name=event.get("stock_name") or "",
        industry=event_industry,
        notice_date=event.get("notice_date") or "",
        event_type=event.get("event_type") or "",
        premium_pct=_safe_float(event.get("premium_pct")),
        action=action,
        reason=reason,
        scope=scope,
        ev_stats=stats,
        realized_return_pct=_safe_float(event.get("realized_return_pct")),
    )


def _event_id(event: dict) -> str:
    return f"{event.get('institution_id','')}|{event.get('stock_code','')}|{event.get('report_date','')}"


def _decide_from_history(
    event: dict,
    history: list[dict],
    config: PolicyConfig,
) -> tuple[str, str]:
    """
    内部：根据一个已经 in-memory 的 history 列表做决策。
    返回 (action, scope)。
    供 backtest 和 recommend_for_event 共用，避免重复决策逻辑。
    """
    if len(history) < config.min_sample:
        return ("skip", "insufficient")

    # 优先同行业
    scope = "inst_all"
    filtered = history
    event_industry = event.get("industry")
    if event_industry:
        same_industry = [h for h in history if h.get("industry") == event_industry]
        if len(same_industry) >= config.prefer_same_industry_min_sample:
            filtered = same_industry
            scope = "inst_industry"

    gains = [_safe_float(h.get("gain")) for h in filtered]
    gains = [g for g in gains if g is not None]
    if not gains:
        return ("skip", "no_valid_gain")
    ev = sum(gains) / len(gains)
    wr = sum(1 for g in gains if g > 0) / len(gains)

    if ev < config.ev_threshold_pct or wr < config.win_threshold:
        if ev >= config.ev_threshold_pct * 0.6 or wr >= config.win_threshold * 0.9:
            return ("watch", scope)
        return ("skip", scope)
    return ("follow", scope)


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
            e.institution_id, i.display_name AS institution_name,
            e.stock_code, e.stock_name,
            e.report_date, e.notice_date, e.event_type,
            e.premium_pct, e.{cfg.gain_column} AS realized_return_pct,
            ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
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

    signals = []
    for row in rows:
        event = dict(row)
        # 以事件自己的 notice_date 为左切点，避免用未来信息
        rec = recommend_for_event(conn, event, config=cfg, as_of_date=event["notice_date"])
        signals.append(rec)

    # 按 action 分组，follow > watch > skip，同组按 EV 降序
    action_rank = {"follow": 0, "watch": 1, "skip": 2}
    signals.sort(key=lambda r: (
        action_rank.get(r.action, 9),
        -(r.ev_stats.ev_pct or -999),
        -(r.ev_stats.n or 0),
    ))
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

    return {
        "institution_id": institution_id,
        "horizon_days": cfg.horizon_days,
        "overall": overall.to_dict(),
        "by_industry": industry_breakdown,
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
            ind.sw_level1 AS industry
        FROM fact_institution_event e
        LEFT JOIN dim_stock_industry ind ON ind.stock_code = e.stock_code
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
        # 同机构严格早于本次的事件（timeline 已排序）
        timeline = inst_timeline[inst_id]
        history = []
        for past in timeline:
            if past["notice_date"] < notice_date:
                history.append(past)
            else:
                break  # ASC 排序后遇到 >= 就停

        action, scope = _decide_from_history(ev, history, cfg)
        gain = _safe_float(ev.get("gain"))
        if gain is None:
            continue
        buckets[action].append({
            "notice_date": notice_date,
            "gain": gain,
            "institution_id": inst_id,
            "stock_code": ev["stock_code"],
            "scope": scope,
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

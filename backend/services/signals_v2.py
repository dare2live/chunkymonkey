"""
signals_v2.py — 极简跟随信号引擎

设计原则（第一性原理）：
    真相只有一张表：fact_institution_event.gain_60d（可执行日 VWAP 跟随 持有 60 交易日）
    决策只问一个问题：该机构历史类似事件跟过后的平均收益是多少、胜率多少、样本够吗
    其他一切（风格标签、行业评分、质地评分）都是 KNN 的特征，不是独立评分

四个可配超参（全部在 app_settings 以 signals.v2.* 前缀）：
    horizon_days          — 持有期，默认 60（对齐现有 gain_60d 列）
    min_sample            — 最小可信样本，默认 10
    ev_threshold_pct      — 跟随门槛：历史 EV% 最低值，默认 5.0
    win_threshold         — 跟随门槛：历史胜率最低值，默认 0.55

不做的事（明确写出来防止以后被加回）：
    ✗ 不合成多维分（composite_priority / pool / setup_priority / gate 等）
    ✗ 不显式打"机构风格"标签（风格由 EV 自然体现）
    ✗ 不加财务 / 行业动量作为独立子分
    ✗ 不加封顶规则 / 拥挤度惩罚 / 外部关注度 boost

命名空间：所有 v2 相关表/配置统一 signals_v2_* / signals.v2.* 前缀，
与 legacy scoring.py 物理隔离，可并存过渡。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from services.utils import safe_float as _safe_float
from services.industry import INDUSTRY_TABLE  # 2026-06-16 S3: 行业 JOIN 走单一常量 (申万切换不漏改, no-hardcode)

logger = logging.getLogger("cm-api")


def _table_columns(conn, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:
        return set()
    columns: set[str] = set()
    for row in rows:
        if hasattr(row, "keys"):
            columns.add(str(row["name"]))
        else:
            columns.add(str(row[1]))
    return columns


TODAY_SIGNAL_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS mart_today_signal_cache (
    cache_key TEXT PRIMARY KEY,
    freshness_days INTEGER NOT NULL,
    policy_hash TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    signals_json TEXT NOT NULL,
    signal_count INTEGER NOT NULL,
    source_max_notice_date TEXT,
    built_at TEXT NOT NULL
);
"""

TODAY_SIGNAL_CACHE_SIGNAL_DDL = """
CREATE TABLE IF NOT EXISTS mart_today_signal_cache_signal (
    cache_key TEXT NOT NULL,
    signal_rank INTEGER NOT NULL,
    policy_hash TEXT NOT NULL,
    stock_code TEXT,
    action TEXT,
    signal_json TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (cache_key, signal_rank)
);
"""


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
    # D1 · 股东人数 YoY（筹码集中度）
    # 实证：max_yoy=30 把 OOS edge 从 +3.29pp 推到 +5.22pp (WR 70%)
    # 99999 = 不启用；数值越小越严（0 = 只留筹码集中的）
    "max_holder_yoy_pct": 30.0,
    # D3 · 业绩预告利润同比（取 low/high 中值）
    # 实证：+D1 +D3(fc≥20) 组合 OOS edge +14.44pp, EV+21.77%, WR 80%
    # min_forecast_profit_yoy: 最低预告中值（%）；-9999 = 不启用
    # 说明：<20% 的"微增"事件 EV 仅 +5.63%，>20% 开始才是真"增长"
    "min_forecast_profit_yoy": 20.0,
    # D5 · 180 天解禁比例（风险规避）
    # 实证：>5% 解禁 n=3 全负 (样本小但方向对)
    # max_unlock_ratio_180d: 上限（%）；99999 = 不启用
    "max_unlock_ratio_180d": 5.0,
    # D8 · 机构调研活跃度（近 90 日）
    # 实证：调研次数 = 近期机构关注度的客观指标；0=不启用（默认），待 cohort 验证后调至 1 或 2
    "min_survey_count_90d": 0,
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
    max_holder_yoy_pct: float = 30.0  # D1: 股东人数 YoY 上限；99999=不启用
    min_forecast_profit_yoy: float = 20.0  # D3: 业绩预告利润 YoY 下限（%）；-9999=不启用
    max_unlock_ratio_180d: float = 5.0     # D5: 180 天解禁上限（%）；99999=不启用
    min_survey_count_90d: int = 0          # D8: 近 90d 调研次数下限；0=不启用

    @property
    def blacklist_set(self) -> set[str]:
        return {t.strip() for t in (self.inst_type_blacklist or "").split(",") if t.strip()}

    @property
    def gain_column(self) -> str:
        """当前 horizon 对应的 gain_*d 列。"""
        h = self.horizon_days
        if h in (10, 30, 60, 90, 120):
            return f"gain_{h}d"
        raise ValueError(f"horizon_days={h} 没有对应的 gain 列（支持 10/30/60/90/120）")

    @property
    def drawdown_column(self) -> str:
        """当前 horizon 对应的回撤列。

        fact_institution_event 仅有 max_drawdown_30d/60d 两列（schema 限制），
        按 horizon 映射到最接近的列：≤30d → dd30; ≥60d → dd60。
        这比原来所有 horizon 都固定 dd60 要一致。
        """
        return "max_drawdown_30d" if self.horizon_days <= 30 else "max_drawdown_60d"


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


def ensure_today_signal_cache(conn) -> None:
    ddl = TODAY_SIGNAL_CACHE_DDL + "\n" + TODAY_SIGNAL_CACHE_SIGNAL_DDL
    if hasattr(conn, "executescript"):
        conn.executescript(ddl)
        return
    for stmt in ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def _policy_hash(config: PolicyConfig) -> str:
    raw = json.dumps(asdict(config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _today_signal_cache_key(*, freshness_days: int, config: PolicyConfig) -> tuple[str, str]:
    policy_hash = _policy_hash(config)
    raw = json.dumps(
        {
            "cache": "today_signals_v1",
            "freshness_days": int(freshness_days),
            "policy_hash": policy_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), policy_hash


def _source_max_notice_date(conn) -> str | None:
    try:
        # PIT 上界走交易日历真相源 (2026-06-22 P0-2): 非 wall-clock CURRENT_DATE — 周末/盘中
        # CURRENT_DATE admit 未收盘日 → live 信号 cache 新鲜度判定混入未来公告。
        from services.calendar import latest_closed_or_raise
        cutoff = latest_closed_or_raise()
        row = conn.execute(
            """
            WITH normalized AS (
                SELECT CASE
                           WHEN length(notice_date) = 8 AND instr(notice_date, '-') = 0
                               THEN substr(notice_date,1,4) || '-' || substr(notice_date,5,2) || '-' || substr(notice_date,7,2)
                           ELSE notice_date
                       END AS notice_iso
                  FROM fact_institution_event
                 WHERE event_type IN ('new_entry', 'increase')
                   AND notice_date IS NOT NULL
            )
            SELECT MAX(notice_iso) AS max_notice_date
              FROM normalized
             WHERE TRY_CAST(notice_iso AS DATE) <= ?
            """,
            [cutoff],
        ).fetchone()
        return str(row["max_notice_date"]) if row and row["max_notice_date"] is not None else None
    except Exception:
        return None


def _today_signal_summary(
    *,
    signals: list[dict],
    freshness_days: int,
    cache_status: dict | None = None,
) -> dict:
    counts = {"follow": 0, "watch": 0, "skip": 0}
    for signal in signals:
        action = signal.get("action")
        counts[action] = counts.get(action, 0) + 1
    out = {
        "total": len(signals),
        "by_action": counts,
        "freshness_days": int(freshness_days),
    }
    if cache_status:
        out["cache"] = cache_status
    return out


def _load_today_signal_cache_items(conn, cache_key: str) -> list[dict] | None:
    if "signal_json" not in _table_columns(conn, "mart_today_signal_cache_signal"):
        return None
    rows = conn.execute(
        """
        SELECT signal_json
          FROM mart_today_signal_cache_signal
         WHERE cache_key = ?
         ORDER BY signal_rank
        """,
        (cache_key,),
    ).fetchall()
    return [json.loads(row["signal_json"] or "{}") for row in rows]


def _replace_today_signal_cache_items(
    conn,
    *,
    cache_key: str,
    policy_hash: str,
    built_at: str,
    signals: list[dict],
) -> None:
    conn.execute("DELETE FROM mart_today_signal_cache_signal WHERE cache_key = ?", (cache_key,))
    if not signals:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO mart_today_signal_cache_signal (
            cache_key, signal_rank, policy_hash, stock_code, action, signal_json, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                cache_key,
                idx,
                policy_hash,
                signal.get("stock_code"),
                signal.get("action"),
                json.dumps(signal, ensure_ascii=False, sort_keys=True),
                built_at,
            )
            for idx, signal in enumerate(signals)
        ],
    )


def load_today_signal_cache(
    conn,
    *,
    config: PolicyConfig,
    freshness_days: int,
) -> dict | None:
    ensure_today_signal_cache(conn)
    cache_key, policy_hash = _today_signal_cache_key(
        freshness_days=int(freshness_days),
        config=config,
    )
    row = conn.execute(
        """
        SELECT summary_json,
               signals_json,
               signal_count,
               source_max_notice_date,
               built_at
          FROM mart_today_signal_cache
         WHERE cache_key = ?
           AND policy_hash = ?
         LIMIT 1
        """,
        (cache_key, policy_hash),
    ).fetchone()
    if not row:
        return None
    detail_signals = _load_today_signal_cache_items(conn, cache_key)
    signal_count = int(row["signal_count"] or 0)
    if detail_signals is not None and (detail_signals or signal_count == 0):
        signals = detail_signals
    else:
        signals = json.loads(row["signals_json"] or "[]")
    summary = json.loads(row["summary_json"] or "{}")
    current_source_max = _source_max_notice_date(conn)
    cache_status = {
        "status": "hit",
        "cache_key": cache_key,
        "policy_hash": policy_hash,
        "built_at": row["built_at"],
        "signal_count": signal_count,
        "source_max_notice_date": row["source_max_notice_date"],
        "current_source_max_notice_date": current_source_max,
        "stale": bool(
            current_source_max
            and row["source_max_notice_date"]
            and current_source_max != row["source_max_notice_date"]
        ),
    }
    summary["cache"] = cache_status
    return {
        "summary": summary,
        "signals": signals,
        "cache": cache_status,
    }


def describe_today_signal_cache(
    conn,
    *,
    config: PolicyConfig | None = None,
    freshness_days: int | None = None,
) -> dict:
    cfg = config or load_config(conn)
    fresh_days = int(freshness_days or cfg.signal_freshness_days)
    ensure_today_signal_cache(conn)
    cache_key, policy_hash = _today_signal_cache_key(
        freshness_days=fresh_days,
        config=cfg,
    )
    current_source_max = _source_max_notice_date(conn)
    row = conn.execute(
        """
        SELECT signal_count,
               source_max_notice_date,
               built_at
          FROM mart_today_signal_cache
         WHERE cache_key = ?
           AND policy_hash = ?
         LIMIT 1
        """,
        (cache_key, policy_hash),
    ).fetchone()
    if not row:
        return {
            "status": "miss",
            "cache_key": cache_key,
            "policy_hash": policy_hash,
            "freshness_days": fresh_days,
            "signal_count": 0,
            "source_max_notice_date": None,
            "current_source_max_notice_date": current_source_max,
            "built_at": None,
            "stale": True,
            "requires_refresh": True,
        }
    source_max = row["source_max_notice_date"]
    stale = bool(current_source_max and source_max and current_source_max != source_max)
    return {
        "status": "hit",
        "cache_key": cache_key,
        "policy_hash": policy_hash,
        "freshness_days": fresh_days,
        "signal_count": int(row["signal_count"] or 0),
        "source_max_notice_date": source_max,
        "current_source_max_notice_date": current_source_max,
        "built_at": row["built_at"],
        "stale": stale,
        "requires_refresh": stale,
    }


def materialize_today_signal_cache(
    conn,
    *,
    config: PolicyConfig | None = None,
    freshness_days: int | None = None,
) -> dict:
    cfg = config or load_config(conn)
    fresh_days = int(freshness_days or cfg.signal_freshness_days)
    cache_key, policy_hash = _today_signal_cache_key(
        freshness_days=fresh_days,
        config=cfg,
    )
    signal_dicts = [
        signal.to_dict()
        for signal in build_today_signals(conn, config=cfg, freshness_days=fresh_days)
    ]
    source_max = _source_max_notice_date(conn)
    built_at = datetime.now().isoformat(timespec="seconds")
    cache_status = {
        "status": "refreshed",
        "cache_key": cache_key,
        "policy_hash": policy_hash,
        "built_at": built_at,
        "signal_count": len(signal_dicts),
        "source_max_notice_date": source_max,
        "current_source_max_notice_date": source_max,
        "stale": False,
    }
    summary = _today_signal_summary(
        signals=signal_dicts,
        freshness_days=fresh_days,
        cache_status=cache_status,
    )
    ensure_today_signal_cache(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_today_signal_cache (
            cache_key, freshness_days, policy_hash, summary_json, signals_json,
            signal_count, source_max_notice_date, built_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cache_key,
            fresh_days,
            policy_hash,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            "[]",
            len(signal_dicts),
            source_max,
            built_at,
        ),
    )
    _replace_today_signal_cache_items(
        conn,
        cache_key=cache_key,
        policy_hash=policy_hash,
        built_at=built_at,
        signals=signal_dicts,
    )
    try:
        from services.schema_versions import record_actual_version  # noqa: WPS433

        record_actual_version(conn, "mart_today_signal_cache")
        record_actual_version(conn, "mart_today_signal_cache_signal")
    except Exception:
        logger.debug("[signals_v2] mart_today_signal_cache schema version record skipped", exc_info=True)
    conn.commit()
    return {
        "summary": summary,
        "signals": signal_dicts,
        "cache": cache_status,
    }


def migrate_today_signal_cache_payload(conn, *, execute: bool = False) -> dict:
    """Move legacy whole-cache JSON blobs into the bounded detail table."""
    if execute:
        ensure_today_signal_cache(conn)
    elif "signals_json" not in _table_columns(conn, "mart_today_signal_cache"):
        return {
            "execute": False,
            "rows_scanned": 0,
            "rows_to_migrate": 0,
            "signals_to_migrate": 0,
            "payload_bytes_before": 0,
            "payload_bytes_after": 0,
        }
    rows = conn.execute(
        """
        SELECT cache_key, policy_hash, signals_json, signal_count, built_at
          FROM mart_today_signal_cache
         WHERE COALESCE(length(signals_json), 0) > 2
         ORDER BY built_at DESC
        """
    ).fetchall()
    payload_bytes_before = 0
    rows_to_migrate = 0
    signals_to_migrate = 0
    for row in rows:
        payload_text = row["signals_json"] or "[]"
        payload_bytes_before += len(payload_text.encode("utf-8"))
        signals = json.loads(payload_text)
        if not isinstance(signals, list) or not signals:
            continue
        rows_to_migrate += 1
        signals_to_migrate += len(signals)
        if execute:
            _replace_today_signal_cache_items(
                conn,
                cache_key=row["cache_key"],
                policy_hash=row["policy_hash"],
                built_at=row["built_at"],
                signals=signals,
            )
            conn.execute(
                """
                UPDATE mart_today_signal_cache
                   SET signals_json = '[]',
                       signal_count = ?
                 WHERE cache_key = ?
                """,
                (int(row["signal_count"] or len(signals)), row["cache_key"]),
            )
    if execute:
        try:
            from services.schema_versions import record_actual_version  # noqa: WPS433

            record_actual_version(conn, "mart_today_signal_cache")
            record_actual_version(conn, "mart_today_signal_cache_signal")
        except Exception:
            logger.debug("[signals_v2] today signal cache migration version record skipped", exc_info=True)
        conn.commit()
    return {
        "execute": bool(execute),
        "rows_scanned": len(rows),
        "rows_to_migrate": rows_to_migrate,
        "signals_to_migrate": signals_to_migrate,
        "payload_bytes_before": payload_bytes_before,
        "payload_bytes_after": 2 * rows_to_migrate if execute else payload_bytes_before,
    }


def save_config(conn, config: dict) -> None:
    """保存配置到 app_settings，只接受 DEFAULT_CONFIG 里已有的 key。"""
    now = datetime.now().isoformat()
    rows = [
        (f"{CONFIG_PREFIX}.{key}", str(value), now)
        for key, value in config.items()
        if key in DEFAULT_CONFIG
    ]
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            rows,
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
    notice_date_source: Optional[str]
    source_notice_date: Optional[str]
    availability_deadline: Optional[str]
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
    # 硬规则 breakdown：7 维检查清单，告诉用户"为什么是 skip"
    rule_breakdown: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {
            "event_id": self.event_id,
            "institution_id": self.institution_id,
            "institution_name": self.institution_name,
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "industry": self.industry,
            "notice_date": self.notice_date,
            "notice_date_source": self.notice_date_source,
            "source_notice_date": self.source_notice_date,
            "availability_deadline": self.availability_deadline,
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
            "rule_breakdown": self.rule_breakdown,
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
            i.tdx_l1 AS industry
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} i ON i.stock_code = e.stock_code
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
    rule_breakdown = _build_rule_breakdown(event, config, decision.get("hard_rule_hit"))

    return Recommendation(
        event_id=_event_id(event),
        institution_id=event["institution_id"],
        institution_name=event.get("institution_name") or event["institution_id"],
        stock_code=event["stock_code"],
        stock_name=event.get("stock_name") or "",
        industry=event.get("industry"),
        notice_date=event.get("notice_date") or "",
        notice_date_source=event.get("notice_date_source"),
        source_notice_date=event.get("source_notice_date"),
        availability_deadline=event.get("availability_deadline"),
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
        rule_breakdown=rule_breakdown,
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
    dd_col = config.drawdown_column  # 审计 4.3: 回撤列跟随 horizon
    if len(history) < min_sample:
        return ("skip", "insufficient", compute_ev_stats(history, drawdown_col=dd_col))

    # 优先同行业
    scope = "inst_all"
    filtered = history
    event_industry = event.get("industry")
    if event_industry:
        same_industry = [h for h in history if h.get("industry") == event_industry]
        if len(same_industry) >= config.prefer_same_industry_min_sample:
            filtered = same_industry
            scope = "inst_industry"

    stats = compute_ev_stats(filtered, drawdown_col=dd_col)
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

    硬规则（都来自 health check 的数据，所有阈值参数化）：
      - 机构类型黑名单 → skip
      - premium > max_premium_pct → skip
      - hold_ratio < min_hold_ratio → skip
      - holder_count_yoy > max_holder_yoy_pct → skip (D1 股东散户化)
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

    # D1: 股东人数 YoY（数值 = 人数变化率，>0 表示散户化，<0 表示筹码集中）
    hc_yoy = event.get("holder_count_yoy")
    if hc_yoy is not None and hc_yoy > config.max_holder_yoy_pct:
        return ("skip", "holder_dispersing")

    # D3: 业绩预告利润同比中值
    fc_mid = event.get("forecast_profit_yoy_mid")
    if fc_mid is not None and fc_mid < config.min_forecast_profit_yoy:
        return ("skip", "forecast_too_weak")

    # D5: 未来 180 天解禁比例（风险规避）
    unlock = event.get("future_unlock_ratio_180d")
    if unlock is not None and unlock > config.max_unlock_ratio_180d:
        return ("skip", "unlock_risk")

    # D8: 近 90 日机构调研次数（活跃度）
    survey_cnt = event.get("survey_count_90d")
    if config.min_survey_count_90d > 0 and survey_cnt is not None and survey_cnt < config.min_survey_count_90d:
        return ("skip", "survey_too_quiet")

    return (None, None)


def _build_rule_breakdown(
    event: dict,
    config: PolicyConfig,
    hard_rule_hit: Optional[str],
) -> dict:
    """
    构造 7 维硬规则检查清单，供前端展示"为什么是 skip"。

    每维状态：
      - pass：原始值已知且通过门槛
      - fail：原始值已知且不通过
      - unknown：原始值缺失（未入库 / 无对应报告）—— 不算 skip 理由
    """
    inst_type = event.get("inst_type")
    premium = event.get("premium_pct")
    hold_ratio = event.get("hold_ratio")
    hc_yoy = event.get("holder_count_yoy")
    fc_mid = event.get("forecast_profit_yoy_mid")
    unlock = event.get("future_unlock_ratio_180d")
    survey_cnt = event.get("survey_count_90d")

    def _status(raw, pass_cond) -> str:
        if raw is None:
            return "unknown"
        return "pass" if pass_cond else "fail"

    checks = [
        {
            "key": "inst_type",
            "label": "机构类型",
            "raw": inst_type,
            "threshold_display": f"非黑名单({config.inst_type_blacklist or '-'})",
            "status": _status(inst_type, inst_type not in config.blacklist_set)
                        if inst_type else "unknown",
            "rule_id": "inst_type_blacklisted",
        },
        {
            "key": "premium_pct",
            "label": "溢价 (%)",
            "raw": premium,
            "threshold_display": f"≤ {config.max_premium_pct}%",
            "status": _status(premium, premium is not None and premium <= config.max_premium_pct),
            "rule_id": "premium_too_high",
        },
        {
            "key": "hold_ratio",
            "label": "持仓占比 (%)",
            "raw": hold_ratio,
            "threshold_display": f"≥ {config.min_hold_ratio}%",
            "status": _status(hold_ratio, hold_ratio is not None and hold_ratio >= config.min_hold_ratio),
            "rule_id": "hold_ratio_too_low",
        },
        {
            "key": "holder_count_yoy",
            "label": "D1 股东 YoY (%)",
            "raw": hc_yoy,
            "threshold_display": f"≤ {config.max_holder_yoy_pct}%",
            "status": _status(hc_yoy, hc_yoy is not None and hc_yoy <= config.max_holder_yoy_pct),
            "rule_id": "holder_dispersing",
        },
        {
            "key": "forecast_profit_yoy_mid",
            "label": "D3 预告利润 YoY (%)",
            "raw": fc_mid,
            "threshold_display": f"≥ {config.min_forecast_profit_yoy}%",
            "status": _status(fc_mid, fc_mid is not None and fc_mid >= config.min_forecast_profit_yoy),
            "rule_id": "forecast_too_weak",
        },
        {
            "key": "future_unlock_ratio_180d",
            "label": "D5 180d 解禁 (%)",
            "raw": unlock,
            "threshold_display": f"≤ {config.max_unlock_ratio_180d}%",
            "status": _status(unlock, unlock is not None and unlock <= config.max_unlock_ratio_180d),
            "rule_id": "unlock_risk",
        },
        {
            "key": "survey_count_90d",
            "label": "D8 近90d调研",
            "raw": survey_cnt,
            "threshold_display": (
                f"≥ {config.min_survey_count_90d} 次"
                if config.min_survey_count_90d > 0 else "未启用"
            ),
            "status": (
                "unknown" if survey_cnt is None
                else "pass" if config.min_survey_count_90d == 0 or survey_cnt >= config.min_survey_count_90d
                else "fail"
            ),
            "rule_id": "survey_too_quiet",
        },
    ]
    return {
        "triggered": hard_rule_hit,
        "checks": checks,
    }


def _load_gpcw_feature_maps(conn) -> dict:
    """
    一次性预加载 GPCW 里我们要的所有字段，key = (stock, report_date_iso)。

    返回 dict: {feature_name: {(stock, date): value}}
    """
    # 2026-06-26 通达信全删: gpcw 源 (raw_gpcw_detail) 退役 → 不再读。D1 holder_count_yoy / D3
    # forecast_profit_yoy_mid 返空 map → _apply_hard_rules 的 `if X is not None` 守卫使两过滤器自动 no-op
    # (优雅降级, 不崩)。用户决议"信号重做不用旧的": signals_v2 是 reset 前过渡引擎, gpcw 过滤器不保留;
    # 重做信号时 D1/D3 若需要, 从 tushare 重接 (holder→stk_holdernumber / forecast→raw_tushare_forecast)。
    return {
        "holder_count": {},           # D1 (gpcw 退役, 空 → 过滤器 no-op)
        "forecast_profit_yoy_mid": {}, # D3 (同上)
    }


def _load_gpcw_holder_count_map(conn) -> dict:
    """向后兼容的 wrapper。"""
    return _load_gpcw_feature_maps(conn)["holder_count"]


def _compute_holder_count_yoy(
    stock_code: str,
    event_report_date: str,
    gpcw_map: dict,
) -> Optional[float]:
    """
    算某事件对应的股东人数 YoY（%）。
    event_report_date 格式 YYYYMMDD 或 YYYY-MM-DD；
    GPCW map 里是 YYYY-MM-DD。
    """
    # 归一化 event_report_date 到 YYYY-MM-DD
    digits = str(event_report_date).replace("-", "")
    if len(digits) < 8:
        return None
    rd_iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"

    cur_hc = gpcw_map.get((stock_code, rd_iso))
    if cur_hc is None or cur_hc <= 0:
        return None

    # 4 季度前同 mmdd
    try:
        prev_iso = f"{int(rd_iso[:4])-1}{rd_iso[4:]}"
    except Exception:
        return None
    prev_hc = gpcw_map.get((stock_code, prev_iso))
    if prev_hc is None or prev_hc <= 0:
        return None

    return round((cur_hc - prev_hc) / prev_hc * 100, 2)


def _load_survey_by_stock(conn, stock_codes: set[str]) -> dict[str, list[tuple[str, str]]]:
    """
    一次性批量加载 raw_institution_surveys，返回 {stock_code: [(survey_date, notice_date), ...]}

    as-of 过滤放在调用端：按 event.notice_date 筛选 survey.notice_date <= 事件公告日 + 90d 窗口。
    """
    if not stock_codes:
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        # 2026-06-23 P1-1 SERVE conformance: 内联 raw_institution_surveys → data_access 单读路
        # (institution_survey entity, notice_date ISO PIT 锚; lineage 声明)。传 conn 避同库重开冲突。
        from services.data_access import get_data_access
        rows = get_data_access().get("institution_survey", codes=list(stock_codes), conn=conn).rows
        for r in rows:
            sc = r["stock_code"]
            sd = r["survey_date"]
            nd = r["notice_date"]
            if sc and sd and nd:
                out.setdefault(sc, []).append((sd, nd))
    except Exception as exc:
        logger.warning(f"[signals_v2] 加载 survey 失败: {exc}")
    return out


def _load_survey_coverage_start(conn) -> Optional[str]:
    """
    返回 institution_survey 数据覆盖最早 notice_date（YYYY-MM-DD）。
    早于此日期的事件 D8 视为 unknown（数据未覆盖，不算冷门）。
    2026-06-23 不变量4: 内联 MIN FROM raw_institution_surveys → DataAccess.coverage_start 元数据原语 (SERVE 单读路)。
    """
    try:
        return get_data_access().coverage_start("institution_survey", conn=conn)
    except Exception:
        return None


def _event_notice_iso(notice_date) -> Optional[str]:
    """事件 notice_date 归一化成 YYYY-MM-DD。入库里有 YYYYMMDD 和 YYYY-MM-DD 混用。"""
    if notice_date is None:
        return None
    digits = str(notice_date).replace("-", "").replace("/", "")
    if len(digits) < 8 or not digits[:8].isdigit():
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _count_surveys_as_of(
    surveys: list[tuple[str, str]],
    event_nd_iso: str,
    window_days: int = 90,
) -> int:
    """
    在 event_nd_iso 这个时点（as-of），过去 window_days 天内被调研的次数。
    survey (survey_date, notice_date) 二者都是 YYYY-MM-DD。
    约束：survey.notice_date <= event_nd_iso（避免 look-ahead）
          survey.survey_date in [event_nd_iso - window_days, event_nd_iso]
    """
    if not surveys or not event_nd_iso:
        return 0
    dt = datetime.strptime(event_nd_iso, "%Y-%m-%d")
    lower = (dt - timedelta(days=window_days)).strftime("%Y-%m-%d")
    return sum(
        1 for (sd, nd) in surveys
        if nd <= event_nd_iso and lower <= sd <= event_nd_iso
    )


def _enrich_events_with_gpcw(conn, events: list[dict]) -> None:
    """
    给 events 列表 in-place 添加特征：
      - holder_count_yoy         D1 股东人数同比变化率（%）
      - forecast_profit_yoy_mid  D3 业绩预告利润同比中值（%）
      - future_unlock_ratio_180d D5 180 天解禁比例（%）
      - survey_count_90d         D8 近 90 日机构调研次数（point-in-time，避免 look-ahead）

    一次性预加载所有维度的 map 避免重复扫表。
    """
    if not events:
        return
    maps = _load_gpcw_feature_maps(conn)
    hc_map = maps["holder_count"]
    fc_map = maps["forecast_profit_yoy_mid"]

    # D5: 解禁数据从 dim_capital_behavior_latest 加载
    unlock_map = {}
    try:
        for r in conn.execute(
            "SELECT stock_code, future_unlock_ratio_180d "
            "FROM dim_capital_behavior_latest WHERE future_unlock_ratio_180d IS NOT NULL"
        ).fetchall():
            unlock_map[r["stock_code"]] = float(r["future_unlock_ratio_180d"])
    except Exception as exc:
        logger.warning(f"[signals_v2] 加载 unlock 失败: {exc}")

    # D8: 批量加载调研数据 + 覆盖区间
    stock_codes = {ev.get("stock_code") for ev in events if ev.get("stock_code")}
    survey_by_stock = _load_survey_by_stock(conn, stock_codes)
    coverage_start = _load_survey_coverage_start(conn)  # YYYY-MM-DD 最早 notice_date

    for ev in events:
        rd = str(ev.get("report_date", ""))
        digits = rd.replace("-", "")
        if len(digits) >= 8:
            rd_iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        else:
            rd_iso = rd
        stock = ev.get("stock_code", "")
        ev["holder_count_yoy"] = _compute_holder_count_yoy(stock, rd, hc_map)
        ev["forecast_profit_yoy_mid"] = fc_map.get((stock, rd_iso))
        ev["future_unlock_ratio_180d"] = unlock_map.get(stock)

        event_nd_iso = _event_notice_iso(ev.get("notice_date"))
        # 数据未覆盖的老事件 → unknown（避免把数据缺失误判成"调研冷门"）
        if not event_nd_iso or not stock:
            ev["survey_count_90d"] = None
        elif coverage_start and event_nd_iso < coverage_start:
            ev["survey_count_90d"] = None
        else:
            ev["survey_count_90d"] = _count_surveys_as_of(
                survey_by_stock.get(stock, []), event_nd_iso, window_days=90,
            )


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
    # PIT 窗口锚交易日历真相源 (2026-06-22 P0-1): 上界=最新完成交易日 (非 wall-clock CURRENT_DATE,
    # 周末/盘中会 admit 未收盘日); 近 fresh_days 窗口下界从该锚回推 (日历天 recency 窗)。
    from services.calendar import latest_closed_or_raise
    from datetime import date as _d, timedelta as _td
    _latest_closed = latest_closed_or_raise()
    _window_start = (_d.fromisoformat(_latest_closed) - _td(days=int(fresh_days))).isoformat()
    event_columns = _table_columns(conn, "fact_institution_event")
    notice_source_select = (
        "e.notice_date_source" if "notice_date_source" in event_columns else "NULL"
    )
    source_notice_select = (
        "e.source_notice_date" if "source_notice_date" in event_columns else "NULL"
    )
    deadline_select = (
        "e.availability_deadline" if "availability_deadline" in event_columns else "NULL"
    )

    # 最近 N 天新 buy 事件。notice_date 在库里是 YYYYMMDD 无分隔符，
    # 查询端统一重拼成 YYYY-MM-DD 后再做日期比较。
    rows = conn.execute(f"""
        SELECT
            e.institution_id, i.display_name AS institution_name, i.type AS inst_type,
            e.stock_code, e.stock_name,
            e.report_date, e.notice_date, e.event_type,
            {notice_source_select} AS notice_date_source,
            {source_notice_select} AS source_notice_date,
            {deadline_select} AS availability_deadline,
            e.premium_pct, e.{cfg.gain_column} AS realized_return_pct,
            ind.tdx_l1 AS industry,
            h.holder_rank, h.hold_ratio
        FROM fact_institution_event e
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
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
          ) >= ?
          AND (
              CASE
                  WHEN length(e.notice_date) = 8 AND instr(e.notice_date, '-') = 0
                      THEN substr(e.notice_date,1,4) || '-' || substr(e.notice_date,5,2) || '-' || substr(e.notice_date,7,2)
                  ELSE e.notice_date
              END
          ) <= ?
        ORDER BY e.notice_date DESC, e.institution_id
    """, (_window_start, _latest_closed)).fetchall()

    if not rows:
        return []

    # 补 GPCW 特征（D1 股东人数 YoY 等）
    rows_list = [dict(r) for r in rows]
    _enrich_events_with_gpcw(conn, rows_list)
    rows = rows_list

    # 性能优化：一次 SQL 拉全部相关机构的历史 buy 事件，内存做 KNN 查询
    # 避免每个事件独立 SQL (1307 事件 * 30ms JOIN = 40 秒)
    institution_ids = {row["institution_id"] for row in rows}
    placeholders = ",".join("?" * len(institution_ids))
    all_history_rows = conn.execute(f"""
        SELECT e.institution_id, e.notice_date,
               e.{cfg.gain_column} AS gain,
               e.max_drawdown_30d, e.max_drawdown_60d,
               ind.tdx_l1 AS industry
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
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
        rule_breakdown = _build_rule_breakdown(event, cfg, decision.get("hard_rule_hit"))

        signals.append(Recommendation(
            event_id=_event_id(event),
            institution_id=event["institution_id"],
            institution_name=event.get("institution_name") or event["institution_id"],
            stock_code=event["stock_code"],
            stock_name=event.get("stock_name") or "",
            industry=event.get("industry"),
            notice_date=event.get("notice_date") or "",
            notice_date_source=event.get("notice_date_source"),
            source_notice_date=event.get("source_notice_date"),
            availability_deadline=event.get("availability_deadline"),
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
            rule_breakdown=rule_breakdown,
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
    dd_col = cfg.drawdown_column  # 审计 4.3: 回撤跟随 horizon
    overall = compute_ev_stats(history, drawdown_col=dd_col)

    # 分行业
    industry_stats = {}
    for h in history:
        ind = h.get("industry") or "(无行业)"
        industry_stats.setdefault(ind, []).append(h)
    industry_breakdown = [
        {
            "industry": ind,
            **compute_ev_stats(events, drawdown_col=dd_col).to_dict(),
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

    row = conn.execute(f"""
        SELECT e.institution_id, e.stock_code, e.report_date, e.notice_date, e.event_type,
               e.premium_pct, ind.tdx_l1 AS industry
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
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
    stats = compute_ev_stats(filtered, drawdown_col=cfg.drawdown_column)

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
            ind.tdx_l1 AS industry,
            i.type AS inst_type,
            h.holder_rank, h.hold_ratio
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN inst_holdings h ON h.institution_id = e.institution_id
               AND h.stock_code = e.stock_code AND h.report_date = e.report_date
        WHERE {' AND '.join(where)}
        ORDER BY e.notice_date ASC
    """
    events = [dict(r) for r in conn.execute(sql, params).fetchall()]
    if not events:
        return {"error": "no_events", "config": asdict(cfg)}

    # 补 GPCW 特征 (D1: holder_count_yoy)
    _enrich_events_with_gpcw(conn, events)

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
            ind.tdx_l1 AS industry,
            i.type AS inst_type,
            h.holder_rank, h.hold_ratio
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
        LEFT JOIN inst_institutions i ON i.id = e.institution_id
        LEFT JOIN inst_holdings h ON h.institution_id = e.institution_id
               AND h.stock_code = e.stock_code AND h.report_date = e.report_date
        WHERE e.event_type IN ('new_entry','increase')
          AND e.{cfg.gain_column} IS NOT NULL
          AND e.notice_date >= ?
          AND e.notice_date <= ?
        ORDER BY e.notice_date ASC
    """, (start_str, end_str)).fetchall()

    events = [dict(r) for r in rows]
    # 补 GPCW 特征 (D1 等)
    _enrich_events_with_gpcw(conn, events)
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
               ind.tdx_l1 AS industry
        FROM fact_institution_event e
        LEFT JOIN {INDUSTRY_TABLE} ind ON ind.stock_code = e.stock_code
        WHERE e.event_type IN ('new_entry','increase')
          AND e.{cfg.gain_column} IS NOT NULL
        ORDER BY e.notice_date ASC
    """).fetchall()

    timeline: dict[str, list[dict]] = {}
    for ev in all_events_rows:
        timeline.setdefault(ev["institution_id"], []).append(dict(ev))

    # 分档（cohort 验证走双口径 + cooldown）
    # 存结构化 dict 方便做季度拆分（诚实披露 V6 alpha 是否集中在某个季度）
    buckets: dict[str, list[dict]] = {"follow": [], "watch": [], "skip": []}
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
        buckets[decision["action"]].append({
            "gain": gain,
            "notice_date": ev.get("notice_date"),
        })

    all_rows = [
        {"gain": float(e["gain"]), "notice_date": e.get("notice_date")}
        for e in events if e.get("gain") is not None
    ]

    def _pack(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0}
        gains = [r["gain"] for r in rows]
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

    def _pack_with_quarters(rows: list[dict]) -> dict:
        """follow/watch/skip 档的 _pack 扩展：额外拆季度，揭示样本是否集中在单季。"""
        base = _pack(rows)
        if not rows:
            return base
        quarters: dict[str, list[float]] = {}
        for r in rows:
            q = _quarter_key(r.get("notice_date"))
            if q:
                quarters.setdefault(q, []).append(r["gain"])
        breakdown = []
        for q in sorted(quarters.keys()):
            gs = quarters[q]
            breakdown.append({
                "quarter": q,
                "n": len(gs),
                "ev_pct": round(sum(gs) / len(gs), 2),
            })
        base["quarterly"] = breakdown
        return base

    packed = {
        "follow": _pack_with_quarters(buckets["follow"]),
        "watch": _pack_with_quarters(buckets["watch"]),
        "skip": _pack(buckets["skip"]),
        "blind": _pack(all_rows),
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
    gain_cols = [f"gain_{h}d" for h in horizons]
    try:
        rows = conn.execute(
            f"""
            SELECT {", ".join(gain_cols)}
              FROM fact_institution_event
             WHERE institution_id = ?
               AND event_type IN ('new_entry','increase')
               AND ({" OR ".join(f"{col} IS NOT NULL" for col in gain_cols)})
            """,
            (institution_id,),
        ).fetchall()
    except Exception:
        rows = []

    for h in horizons:
        col = f"gain_{h}d"
        col_idx = gain_cols.index(col)
        gains = [
            float(value)
            for r in rows
            for value in [r[col] if hasattr(r, "keys") else r[col_idx]]
            if value is not None
        ]
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
    conn.executemany(
        "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
        [
            (f"{CONFIG_PREFIX}.{key}", str(value), now)
            for key, value in DEFAULT_CONFIG.items()
        ],
    )
    conn.commit()

"""P4c champion model 复盘闭环 — mart_champion_model 注册 + champion/challenger 对比.

PLAN_V3 v3.2 P4c:
- paper_sim KPI → mlflow / mart_walkforward_eval (已有)
- champion → mart_champion_model (新)
- Gate: champion 必须记录 RankIC, ann, mdd, turnover, cost, capacity
- 可复现任一历史 run; 可比较 champion/challenger

mart_champion_model:
- 一行 = 一个 candidate champion (model_id × feature_version × seed)
- 含完整 PLAN_V3 KPI: rank_ic, ann_ret, max_dd, monthly_win_rate, excess_vs_hs300,
  turnover, tx_cost_pct, capacity_concentration
- is_current_champion 布尔字段, 每次更新只一个 True
- promoted_at + reason 字段追溯
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc
from typing import Optional

log = logging.getLogger("portfolio.champion")


CHAMPION_DDL = """
CREATE TABLE IF NOT EXISTS mart_champion_model (
    champion_id        TEXT NOT NULL,
    model_id           TEXT NOT NULL,
    model_version      TEXT NOT NULL,
    feature_version    TEXT NOT NULL,
    label_version      TEXT NOT NULL,
    seed               INTEGER,
    -- PLAN_V3 P4c gate: 必填 KPI 全部
    rank_ic            DOUBLE,
    ann_ret            DOUBLE,
    max_dd             DOUBLE,
    monthly_win_rate   DOUBLE,
    excess_vs_hs300    DOUBLE,
    turnover           DOUBLE,
    tx_cost_pct        DOUBLE,
    capacity_concentration DOUBLE,
    -- Final holdout 范围 (P3)
    final_period_start TEXT,
    final_period_end   TEXT,
    n_oos_months       INTEGER,
    -- 注册状态
    is_current_champion BOOLEAN NOT NULL DEFAULT FALSE,
    promoted_at        TEXT,
    promoted_reason    TEXT,
    -- 元数据
    composite_score    DOUBLE,
    p3_passed          BOOLEAN,
    p3_failures_json   TEXT,
    built_at           TEXT NOT NULL,
    PRIMARY KEY (champion_id)
);
"""


@dataclass(frozen=True)
class ChampionRecord:
    """champion model 注册记录, 跟 mart_champion_model 字段对齐."""
    champion_id: str
    model_id: str
    model_version: str
    feature_version: str
    label_version: str
    rank_ic: float
    ann_ret: float
    max_dd: float
    monthly_win_rate: float
    excess_vs_hs300: float
    turnover: float = 0.0
    tx_cost_pct: float = 0.0
    capacity_concentration: float = 0.0
    seed: int = 42
    final_period_start: Optional[str] = None
    final_period_end: Optional[str] = None
    n_oos_months: int = 0
    composite_score: Optional[float] = None
    p3_passed: bool = False
    p3_failures: list[str] = None


def validate_champion_kpi_completeness(rec: ChampionRecord) -> list[str]:
    """PLAN_V3 §4c Gate 检 KPI 完整性 (champion 入库必填).

    Returns:
        list of missing field names; 空 = 完整.
    """
    missing = []
    required_fields = ("rank_ic", "ann_ret", "max_dd", "monthly_win_rate",
                       "excess_vs_hs300", "turnover", "tx_cost_pct",
                       "capacity_concentration")
    for f in required_fields:
        v = getattr(rec, f, None)
        if v is None or (isinstance(v, float) and v != v):  # NaN check
            missing.append(f)
    return missing


def register_champion(conn, rec: ChampionRecord, *, promote: bool = False, reason: str = "") -> bool:
    """注册 champion record (idempotent INSERT OR REPLACE).

    Args:
        conn: smartmoney.duckdb 连接.
        rec: ChampionRecord.
        promote: True 时把此 record 设为 is_current_champion=TRUE,
                 并把其他 record is_current_champion=FALSE (单冠军).
        reason: promoted_reason 文字.

    Returns:
        True 当 register 成功. False 当 KPI 不完整 (Gate 拒绝).
    """
    missing = validate_champion_kpi_completeness(rec)
    if missing:
        log.error(f"Champion KPI 不完整, 缺: {missing}; 拒绝注册 (PLAN_V3 §4c Gate)")
        return False

    import json
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    promoted_at = built_at if promote else None
    p3_failures_json = json.dumps(rec.p3_failures or [])

    if promote:
        # 单冠军原则: 其他全 FALSE
        conn.execute("UPDATE mart_champion_model SET is_current_champion = FALSE")

    conn.execute(
        """
        INSERT OR REPLACE INTO mart_champion_model (
            champion_id, model_id, model_version, feature_version, label_version, seed,
            rank_ic, ann_ret, max_dd, monthly_win_rate, excess_vs_hs300,
            turnover, tx_cost_pct, capacity_concentration,
            final_period_start, final_period_end, n_oos_months,
            is_current_champion, promoted_at, promoted_reason,
            composite_score, p3_passed, p3_failures_json, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            rec.champion_id, rec.model_id, rec.model_version,
            rec.feature_version, rec.label_version, rec.seed,
            rec.rank_ic, rec.ann_ret, rec.max_dd, rec.monthly_win_rate, rec.excess_vs_hs300,
            rec.turnover, rec.tx_cost_pct, rec.capacity_concentration,
            rec.final_period_start, rec.final_period_end, rec.n_oos_months,
            promote, promoted_at, reason if promote else "",
            rec.composite_score, rec.p3_passed, p3_failures_json, built_at,
        ]
    )
    log.info(f"Champion registered: {rec.champion_id} "
             f"(promoted={promote}, reason={reason!r})")
    return True


def get_current_champion(conn) -> dict | None:
    """返回当前 is_current_champion=TRUE 的 record (单冠军), 或 None."""
    cur = conn.execute("SELECT * FROM mart_champion_model WHERE is_current_champion = TRUE")
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    if not row:
        return None
    return dict(zip(cols, row))


def compare_challenger(conn, challenger: ChampionRecord) -> dict:
    """对比 challenger vs current champion. 报每个 KPI 的 Δ."""
    champion = get_current_champion(conn)
    if not champion:
        return {"verdict": "no_champion_yet",
                "challenger_rank_ic": challenger.rank_ic,
                "challenger_ann_ret": challenger.ann_ret}
    out = {"verdict": "compare",
           "current_champion_id": champion["champion_id"]}
    for f in ("rank_ic", "ann_ret", "max_dd", "monthly_win_rate", "excess_vs_hs300"):
        c_val = champion.get(f)
        h_val = getattr(challenger, f, None)
        out[f"{f}_champion"] = c_val
        out[f"{f}_challenger"] = h_val
        if c_val is not None and h_val is not None:
            out[f"{f}_delta"] = h_val - c_val
    return out

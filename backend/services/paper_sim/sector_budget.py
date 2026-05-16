"""Phase 2 sector budget (Codex round 5 MAJOR verdict).

设计目标: 防 Top-K=5 单事件放大 (v8 -22% dd 已暴露), 加 sector concentration limit.

Codex round 5 设计:
- 12 supersector (Phase 2 hierarchical 24池的 12 个, 比 CITIC L1 稳定)
- 硬上限 40% NAV (Top-5 单行业最多 2 只)
- 软目标 30% (rank penalty, v1 暂不实施)
- sector source: mart_stock_industry_pit (cutoff_date PIT-safe, 87.6% observed_snapshot)
- reclassify: 持仓不强卖, 冻结超额 sector 新买入 + 记录 breach

切入点: driver.py BUY 时调 check_sector_quota(positions, candidate, sector_budget, signal_date).
selector.py 保持 alpha rank.

跟 Phase 1c tiered orthogonal (sector_budget 是 portfolio 层硬约束).

API:
    load_industry_at_cutoff(conn, signal_date) -> dict[stock_code, sector]
    check_sector_quota(positions, candidate, sector_map, nav, hard_cap_pct) -> bool
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("paper_sim.sector_budget")


def load_industry_pit(conn, signal_date: str,
                      level: str = "tdx_l1",
                      confidence_filter: str = "observed_snapshot") -> dict[str, str]:
    """从 mart_stock_industry_pit ASOF 拿 signal_date 时点的 stock → sector 映射.

    Args:
        signal_date: 'YYYY-MM-DD' PIT cutoff
        level: 'tdx_l1' (12-15 一级行业) / 'tdx_l2' / 'tdx_l3'
        confidence_filter: 'observed_snapshot' 跳过 12.4% fallback (Codex round 5 PIT-safe)

    Returns:
        dict {stock_code: sector_name or sector_code}
    """
    # Codex round 13 MAJOR fix: effective_to 是闭区间 (PIT 表 builder 写 inclusive),
    # 改 >= signal_date 不漏末日.
    sql = f"""
    SELECT stock_code, {level} AS sector
    FROM mart_stock_industry_pit
    WHERE confidence_level = ?
      AND effective_from <= ?
      AND (effective_to IS NULL OR effective_to >= ?)
      AND {level} IS NOT NULL
    """
    rows = conn.execute(sql, [confidence_filter, signal_date, signal_date]).fetchall()
    return {r[0]: str(r[1]) for r in rows}


def compute_current_sector_exposure(positions: list, current_prices: dict[str, float],
                                      sector_map: dict[str, str],
                                      nav: float) -> dict[str, float]:
    """计算当前每个 sector 在 NAV 中的占比.

    Args:
        positions: list of _OpenPosition (driver.py 的 dataclass)
        current_prices: {stock_code: close_today}
        sector_map: {stock_code: sector}
        nav: total portfolio NAV

    Returns:
        dict {sector: nav_pct}
    """
    if nav <= 0:
        return {}
    sector_value: dict[str, float] = {}
    for p in positions:
        if not getattr(p, "is_open", True):
            continue
        sc = p.stock_code
        sector = sector_map.get(sc, "UNKNOWN")
        price = current_prices.get(sc, p.open_price)
        value = price * p.shares
        sector_value[sector] = sector_value.get(sector, 0.0) + value
    return {s: v / nav for s, v in sector_value.items()}


def check_sector_quota(candidate_stock: str,
                        candidate_target_value: float,
                        sector_map: dict[str, str],
                        current_exposure: dict[str, float],
                        nav: float,
                        hard_cap_pct: float = 0.40) -> tuple[bool, Optional[str]]:
    """check 加 candidate 后是否超 sector hard cap.

    Codex round 13 MAJOR fix: 缺映射 stock (sector_map miss) 不入 'UNKNOWN' 桶,
    直接 allow (不参与 sector cap check). 否则缺映射股汇聚到 UNKNOWN 误触 cap.

    Returns:
        (allowed, reason) — allowed=True 通过, allowed=False reason 说明拒绝
    """
    sector = sector_map.get(candidate_stock)
    if sector is None:
        # 缺映射 stock 不参与 sector cap (避免汇聚 UNKNOWN 桶误挡)
        return True, None
    current_pct = current_exposure.get(sector, 0.0)
    added_pct = candidate_target_value / nav if nav > 0 else 0
    projected_pct = current_pct + added_pct
    if projected_pct > hard_cap_pct:
        return False, f"sector {sector} 超 {hard_cap_pct*100:.0f}% NAV cap (current={current_pct*100:.1f}% + add={added_pct*100:.1f}% > {hard_cap_pct*100:.0f}%)"
    return True, None


def log_sector_breach(sim_run_id: str, signal_date: str,
                       stock_code: str, sector: str, reason: str,
                       conn=None) -> None:
    """记录 sector budget breach (denied candidate). 用于 audit + Phase 1b decision.

    v1 只 log warning, v2 可入 mart_paper_sim_sector_breach table.
    """
    log.warning(f"[sector_budget] BREACH sim={sim_run_id} date={signal_date} "
                f"stock={stock_code} sector={sector}: {reason}")

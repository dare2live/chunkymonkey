"""Paper Sim — portfolio_dd hard stop (v3 实验 2026-05-15).

Codex aa2d79d2 C-D MARGINAL: max_dd -27% > target -20%. Path A v2 (cash 0.30 only) 维持 alpha
但 max_dd 仅 -26.7% 微改善. 真正的 dd 控制必须在 portfolio 层 (per-position stop_pct 不够).

机制 (借用 RiskConfig.max_dd_hard_stop_pct):
  - 跟踪 sim 起 peak_nav (recorded daily in mart_paper_sim_nav)
  - 每日 NAV 更新后算 current_dd = (today_nav / peak_nav) - 1
  - 若 current_dd <= max_dd_hard_stop_pct (e.g. -0.20 → 跌 20%): 全清仓位
  - 冻结新 buy `hard_stop_freeze_days` 天 (恢复期, 避免连续抓底)

PIT-safe: 用历史 NAV 序列, 不读未来.

API:
    `compute_portfolio_dd(today_nav, peak_nav) -> float` — current dd
    `should_hard_stop(current_dd, cfg) -> bool` — 是否触发
    `is_frozen(today, freeze_until) -> bool` — 在冻结期不能 new buy

Note: 不强 sell at stop_hit level (那是 per-position rule). hard_stop 是 portfolio-level
最后防线: 信号失效 / 系统性风险 / 黑天鹅时全清.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


def compute_portfolio_dd(today_nav: float, peak_nav: float) -> float:
    """当前 dd ratio. Returns negative (e.g. -0.15 = 15% dd)."""
    if peak_nav <= 0 or today_nav <= 0:
        return 0.0
    return (today_nav / peak_nav) - 1.0


def should_hard_stop(current_dd: float, max_dd_hard_stop_pct: float) -> bool:
    """current_dd <= max_dd_hard_stop_pct (both negative) → 触发全清.

    e.g. current_dd=-0.21, max_dd_hard_stop_pct=-0.20 → True (跌穿)
    """
    return current_dd <= max_dd_hard_stop_pct


def is_buy_frozen(today: str, freeze_until: Optional[str]) -> bool:
    """是否在 hard-stop 冻结期 (今日 < freeze_until → frozen, 不能新 buy)."""
    if not freeze_until:
        return False
    return today < freeze_until


def compute_freeze_until(today: str, freeze_days: int) -> str:
    """计算冻结到哪天 (含 today + freeze_days 自然日)."""
    dt = datetime.strptime(today, "%Y-%m-%d").date() + timedelta(days=freeze_days)
    return dt.strftime("%Y-%m-%d")

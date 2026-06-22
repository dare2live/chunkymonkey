"""Market perception service package — RETIRED (2026-06-14 地基-reset commit 639e0dfb).

子系统已退役 (main.py 注释 + perception_absorbed/README 佐证)。reset 删了全部 engine
(emotion/leader_follower/style_rotation/stock_context/theme_lifecycle/under_reaction)
**以及 .utils** — 残存的 regime_engine.py 仍 `from .utils import` = 自身亦 broken。
全仓 0 外部消费者 (2026-06-22 conformance 审计 P0-7 实测 rg 确认)。

__init__ 改 import-safe 空壳: 不 re-export 任何已坏子模块, 使 `import services.market_perception`
不再 ModuleNotFoundError。regime_engine.py/router_serialize.py 死码留盘 (0 引用, 无害),
全包物删归 P2 cleanup (单独 impact 审计后)。
"""

__all__: list[str] = []

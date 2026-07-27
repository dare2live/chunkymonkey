# Factor-family K3 live frontier gate (2026-07-27)

> Lifecycle: evidence-only · Label: **FIXED**

本刀只证明 K3 frontier 投影的 live artifact 可验证；它不把 defer 因子族升级为
READY，也不授权 formal RX。

## Closed

- 投影记录 inventory hash、生成时间、probe 状态和 typed defer reason。
- raw moneyflow、smartmoney fact、raw margin、smartmoney org 使用各自真实数据库
  探针，不再借错库获得假绿。
- `check_factor_family_frontier_live.py` 对缺库、查询失败、`UNVERIFIED`、
  inventory hash 漂移和 stale artifact 一律非零退出。
- live artifact 保留 defer/blocked 语义；未知或未测量值不补 0。

## Verification

- inventory structural gate: PASS
- frequency continuity gate: PASS
- live frontier projection gate: PASS
- projection test suite: PASS

## Residual

K3 PASS 只表示投影本身 fresh、可追溯、fail-closed。因子族是否可用于 B3/B4
仍由各自 readiness/continuity gate 决定；formal RX / Optuna /
StrategyRelease 仍为 **BLOCKED**。

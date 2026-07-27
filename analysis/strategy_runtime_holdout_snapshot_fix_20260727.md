# Strategy runtime holdout / snapshot remediation (2026-07-27)

> Lifecycle: evidence-only · Label: **PARTIAL**

本刀只整改 strategy runtime 的冻结、预注册和 holdout 消费契约；不授权
formal RX、Optuna 或 StrategyRelease。

## Closed

- B0 在消费 holdout 前先冻结真实 walk-forward plan，并绑定 snapshot、
  strategy、universe、protocol 与 governed policy 组成的稳定 scope。
- 正式顺序固定为
  `plan → prereg → pointer preflight → consume → canonical load/hash → measure`；
  consume 前不读取 OHLCV outcome。
- snapshot/pointer 的 batch、row count、contract/config/content hash 必须一致，
  canonical nominal rows 只加载一次并重算内容 hash；holdout 后数据直接拒绝。
- 裸 `bars_by_day` 不再冒充正式输入；typed offline fixture 不写正式 prereg、
  不消耗 holdout，且最终 verdict 强制 non-claimable。
- downstream B1/B2/B4 必须继承 formal B0 evidence；fixture B0 不得提升后续结论。
- prereg marker 采用原子 first-publish；并发与 record 写失败不能删除已消费证据。
- 上述 strategy runtime 测试已从 nightly/optional 提升到 blocking CI。

## Residual blockers

- disclosure freeze 尚无合格 `nominal_ohlcv.accepted[]`。
- 当前 main-rally freeze 的 nominal 范围越过 `20250601` holdout，必须重建为
  严格截止 `20250531` 的 snapshot。
- 文件 ledger 只证明单节点 fail-closed；跨节点正式发布仍需 CAS/唯一约束 owner。
- `goal.md` 尚未显式 schedule RX，StrategyRelease 其余门也未闭合。

结论：可继续离线 freeze 重建和 preflight；formal RX / Optuna /
StrategyRelease 仍为 **BLOCKED**。

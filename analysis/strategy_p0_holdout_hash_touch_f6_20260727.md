# Foundation + strategy P0 remediation integration audit (2026-07-27)

> Lifecycle: evidence-only · Label: **PARTIAL**（代码契约已整改；formal RX 仍 BLOCKED）

Status: **PARTIAL**；整改已按三个独立 L3 刀口提交，当前不存在可用于
formal RX 的合格 freeze，且 owner 尚未 schedule RX。

## Delivery slices

| Scope | Commit | Result |
|---|---|---|
| org accepted pointer / F6 | `273d7b6be` | committed, no push |
| strategy snapshot / prereg / holdout runtime | `4cbe50c06` | committed, no push |
| factor-family K3 live frontier gate | `95cfd2697` | committed, no push |

## Fixes

| ID | Fix |
|---|---|
| P0-1 | `main_rally_b0` binds `actual_data_end` from snapshot nominal max; spill → `HoldoutBoundaryViolation`. |
| P0-2 | nominal bind 同时校验 snapshot/pointer 的 batch、count、contract/config/content hash，并重算 canonical 内容 hash；裸 `bars_by_day` 被拒，离线 fixture 必须显式 typed。 |
| P0-3 | prereg 先冻结真实 WF plan；one-touch marker 改为稳定 `holdout_scope_id`，锚定 snapshot + strategy + universe + protocol + governed policy（新 UUID、block、fold/holdout date 不能重置）；load/consume 重算 param hash 并检查 token/marker，marker 完整 JSON 原子发布。 |
| P0-4 | 正式路径顺序为 **plan → prereg → pointer metadata preflight → consume → canonical load/hash → measure**；consume 前不读 OHLCV outcome；synthetic fixture 不落 formal prereg、不消耗 holdout且 verdict 强制 non-claimable。 |
| P1-1 | F6 FULL OUTER JOIN + canonical content hash；accepted batch 在 sibling 更新 partition pointer 后仍可幂等重放。 |
| P1-2 | repair 对全部可修项单事务更新、post-verify 后提交；missing-side / 中途失败均 rollback / 非零。 |
| P1-3 | `--skip-live` 只能得到 `PARTIAL`，不得再产生 `phase_closure_ready=true`。 |
| K3 | raw moneyflow、smartmoney fact、raw margin、smartmoney org 分库探针修正；新增 freshness/hash/error fail-closed live gate。 |

## Problem type (both audit rounds)

Not random bugs — **incomplete fan-out**, **shallow freeze**, **API-without-wiring / wrong lock order**, **verifier–claim mismatch (false-green)**.

## Why process missed them

Gates check scoped knife correctness, not sibling-contract completeness. First holdout knife fixed institution_follow only; freeze tested membership not generation; store shipped without consume-before-observe; F6 text overclaimed vs SQL; `APPROVE_WITH_NOTES` soft-landed blockers.

## Verification

- final blocking CI：`1317/1317 passed`
- Moth exact-staged assertions：`35/35 PASS`
- lineage exact-staged projection：`448 nodes / 1259 edges`, fresh
- live K3：`PASS`，moneyflow raw tip `20260724`、fact tip `20260724`、margin accepted `1828`、org accepted `22`
- live foundation：`PASS / phase_closure_ready=true`；完整 F6 hash 后总耗时 `41.45s`
- offline foundation：`PARTIAL / phase_closure_ready=false`
- continuity audit：overall `PASS`，但 live readiness 仍为 `UNVERIFIED`
  （3 项 typed skip）；提交允许不等于 Tier0 consumption/release READY。

## Residuals / formal blocker

- disclosure freeze 尚无 `nominal_ohlcv.accepted[]`，不能运行正式 institution RX；
- 当前 main-rally freeze nominal 范围越过 `20250601` holdout，必须重建严格截止 `20250531` 的 freeze；
- 文件型 one-touch ledger 是单节点 fail-closed evidence；跨节点/正式 Release 仍需唯一约束或 CAS owner；
- `goal.md` 未显式 schedule RX，且 StrategyRelease §9 其余门未完成。

因此结论是：**foundation owner gate 当前闭合，可继续做离线策略地基和
freeze 重建；但连续性 live readiness 仍须保留 `UNVERIFIED`，formal RX /
Optuna / StrategyRelease 仍 BLOCKED。**

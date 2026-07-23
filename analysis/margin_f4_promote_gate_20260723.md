# Margin F4 / 1c — pulse serve→accepted cutover（2026-07-23）

> evidence-only；FOUNDATION F4。禁 fake TRUSTED / silent thaw / Continuity mute。

## Root cause（owner: 不够应补，不是关）

| 症状 | 根因 |
|---|---|
| gate=`PENDING_SERVE_CUTOVER` | builder/serve 仍读 `raw_tushare_margin` |
| mart rzrqye NULL on 20260717–22 | **raw 空**；accepted v3 SSE+SZSE **有数** |
| 假「证据不足」 | 证据在 accepted；路径未切 — fail-closed 被当成终点 |

## Before → after

| | builder source | shadow input | promote | rzrqye field |
|---|---|---|---|---|
| before | raw (可含 BSE；v3 日空) | raw → BLOCKED | PENDING_SERVE_CUTOVER | 永久 UNTRUSTED |
| after | **accepted SSE+SZSE**（prefer v3，缺则回退有双所的更高/其它 generation） | **accepted** | **PROMOTED** when day has accepted | **READY** as `external_aggregate` |

缺 accepted 的日：仍 UNTRUSTED + typed remaining（fail closed）；不回退 raw 假填。Rebuild 不抹 v2 历史。

## Config

`backend/config/margin_pulse_promote.yaml`:
- `pulse_source_accepted: true`
- `promote_allowed: true`（router 仅当 runtime accepted 在场才生效）
- `contract_version: "3"`（preferred；非 exclusive pin）

## Not TRUSTED

READY ≠ project_universe_pit；`refuse_project_universe_claim` 仍硬墙。breadth 仍 UNTRUSTED → overall_status 可仍 UNTRUSTED。

## Tests

`test_margin_pulse_promote_gate` + scope + sentiment API + market_pulse builder fixtures seed canonical.

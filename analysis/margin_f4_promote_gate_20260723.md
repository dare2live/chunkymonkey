# Margin F4 / 1c — rzrqye promote gate（2026-07-23）

> evidence-only；FOUNDATION F4。禁 fake TRUSTED / silent thaw。

## Before → after

| | product rzrqye | shadow | promote path |
|---|---|---|---|
| before | UNTRUSTED（永久停） | raw venue shadow only | optional / 不动 |
| after | **仍 UNTRUSTED**（诚实） | + accepted SSE+SZSE loader | **typed `promote_gate` 产品可见** |

Live sentiment sidecar now returns `promote_gate` with status ∈
`CRITERIA_PENDING | SHADOW_EXTERNAL_HONEST | PENDING_SERVE_CUTOVER | READY_TO_PROMOTE | BLOCKED`.

## What moved toward trust

1. `load_accepted_margin_rows_for_shadow` — canonical v3 SSE+SZSE
2. `evaluate_margin_pulse_promote_gate` — explicit criteria checklist
3. `/api/v3/pulse/sentiment` exposes `promote_gate`（不改 mart 数值）
4. Gate **never** sets `product_trust_would_be=TRUSTED`；READY_TO_PROMOTE 仍 UNTRUSTED until separate cutover knife

## Remaining（next promote cutover knife — not this commit）

1. Pulse serve/builder 切到 accepted SSE+SZSE（`pulse_source_accepted=True`）— 今日仍读 raw（可含 BSE）
2. Explicit `promote_allowed` config/yaml（默认 False）
3. Only then typed field status READY **as external_aggregate**（仍拒绝 project_universe_pit）

## Tests

`test_margin_pulse_promote_gate` + sentiment API promote_gate assertions.

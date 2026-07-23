# Continuity F1 — typed hk_holidays + event_sparse（2026-07-23）

> evidence-only；owner: 不为刷绿清掉，但要修复。禁 Continuity READY cosmetics / mute checker。

## Before → after

| | overall | warn | fail | 机制 |
|---|---|---|---|---|
| before | WARN | 2（dividend / moneyflow_hsgt annotate） | 0 | `gap_tolerance: annotate` 永久 WARN |
| after | **PASS** | **0** | 0 | typed calendars；非假期/真缺仍 FAIL |

## Fixes

1. **moneyflow_hsgt** → `gap_tolerance: hk_holidays` + `backend/config/hk_northbound_closed_days.yaml`（90 日；R4/Knife4 vendor-0 实测）。日历外空洞 = **FAIL**（不 mute）。
2. **dividend** → `gap_tolerance: event_sparse`：事件稀疏中间空日 typed pass；**尾部 SLA 仍 FAIL**。
3. `continuity_guard` 对齐 typed 豁免（hk 须对日历）。

## Not done / residual

- 其它仍 `annotate` 的域（stk_surv / forecast 等）未在本刀改；当前 live 未贡献 WARN。
- 新港股假期日：须 probe vendor→追加 yaml；禁止直接 annotate 洗。
- 近端 frontier 已对齐；hsgt/dividend 在 `--all-due`（非 on_demand），无需 margin 式 catchup 刀。

## Tests

`test_check_continuity_integrity` 47 passed（含 hk residual FAIL + event_sparse tail FAIL）。
Live: `overall=PASS warn=0`（latest_expected=20260722）。

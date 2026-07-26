# Residual hygiene F9 — evidence (2026-07-26)

> Lifecycle: evidence-only · Label: **FIXED path** (mechanism)
> Authority: `analysis/FOUNDATION_EXECUTION_PLAN.md` F9 · `goal.md` 护栏
> Scope: YAML SLA + checker + daily_update wire；**no** mass A2/A3 drain；**no** Continuity READY wash；STRATEGY still BLOCKED

## 0. Why

Owner concern: non-blocking residuals (A2/A3) must not drift forever under repeated updates.
Cure = **process gate with trading-day SLA**, organic to store/continuity/type_b — not a second dashboard and not "zero WARN".

## 1. What shipped

| Piece | Path |
|---|---|
| Policy | `backend/config/residual_hygiene.yaml` |
| Single compute | `backend/services/residual_hygiene.py` |
| CLI | `backend/scripts/check_residual_hygiene.py` |
| Wire acquire | `type_b_fact_publish_catchup.run_acquire_*` → `evaluate_type_b_after_catchup` → `delta_manifest.acquire_summary.residual_hygiene_type_b` |
| Wire store | Step **2.985** after continuity; ALERT `/tmp/chunkymonkey_ALERT_residual_hygiene.flag` |
| Outcome class | `run_outcome` integrity regex includes `residual_hygiene` |
| Backlog | FOUNDATION F9 + §3b A2/A3 → F9 jurisdiction |

Thresholds (trading days):

- Type-B publish: warn `>1`, fail `>2`
- ann tip (`stk_holdernumber`): warn `>5`, fail `>15`

## 2. Tests

`pytest backend/tests/services/test_residual_hygiene.py` (+ type_b wire / run_outcome) → **pass**.

## 3. Live probe (read-only)

Command:

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/check_residual_hygiene.py \
  --json --json-out analysis/residual_hygiene_f9_live_20260726.json
```

Result (`as_of` 2026-07-26T10:17:50): **overall=PASS** · fail=0 warn=0 pass=7

| check | domain | lag_trading_days | status |
|---|---|---:|---|
| type_b_publish_lag | moneyflow…top_inst_seat (6) | 0 | pass |
| ann_tip_lag | stk_holdernumber | 1 (`local=20260723` vs `eligible=20260724`) | pass |

Artifact: `analysis/residual_hygiene_f9_live_20260726.json`

## 4. Honesty / residual

- Mechanism FIXED path; live currently inside SLA — no drain knife required this session.
- If tip/publish later exceeds fail SLA → daily_update **degraded** + ALERT; catchup/drain paths already exist to close lag.
- Does **not** claim Continuity READY; does **not** open RX/STRATEGY.

## 5. Label

**FIXED** (F9 mechanism) · residual owner = ops when ALERT fires · next verify = next `daily_update` store step 2.985.

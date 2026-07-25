# QFII period ops drain (2026-07-25)

> Status: evidence-only · Label: **FIXED** (missing report periods 22→0)

## Scope

`raw_qfii_holding_quarterly` vs plannable calendar `2018-12-31`→`2026-03-31` (30 quarters).
Daily `sync_qfii_incremental` only fills latest plannable; historical holes needed explicit ops.

## Before → after

| Metric | Before | After |
|---|---:|---:|
| Distinct `report_date` | 8 | **30** |
| Missing quarters | **22** (`2018-12-31`…`2024-03-31`) | **0** |
| Row range | 815–2508 (recent) | 308–2508 (full span; early quarters thinner = provider, not page-cap) |

## Command

```bash
PYTHONPATH=backend .venv/bin/python3 backend/scripts/qfii_period_drain.py --max-partitions 22
```

Log: `/tmp/qfii_drain_20260725.log` · Entrypoint: `backend/scripts/qfii_period_drain.py`

## Notes

- Each period: aif10 `RPT_DMSK_HOLDERS` × 4 change_type symbols; skip periods with local rows (no mass refresh).
- Not the holders notice_date / 公告标题 path — QFII is **report_period** grain.
- Daily auto unchanged (latest plannable only).

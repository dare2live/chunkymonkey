# Holders landing retention F3（2026-07-23）

> evidence-only；FOUNDATION F3 — real reclaim, not optional forever.

## Before → after

| | landing rows | uniq row_hash | avg copies | smartmoney file |
|---|---:|---:|---:|---:|
| before | 7,171,617 | 225,099 | **~31.9×** | 6.7 GiB |
| after | **235,821** | 224,910 | **~1.05×** | **4.3 GiB**（compact −2.5 GiB） |

- Archived `6,935,796` rows → `data/archive/holders_landing_retention/holders_landing_retention_20260723T075446Z.parquet`（~99 MiB ZSTD）
- Kept: latest ACCEPTED per partition + inflight non-ACCEPTED（638 batches）
- Canonical unchanged (~224,973)
- `mart_data_deletion_record` run_id=`holders_landing_retention_20260723T075446Z`

## Recurrence

- Skip-land（Knife2）已关同 `payload_hash` 风暴；本刀清历史堆
- Retention 可再跑（noop if no archive candidates）
- **禁** bare DELETE/DROP landing 当去重

## Code

- `backend/services/holders_landing_retention.py`
- `backend/scripts/db_holders_landing_retention.py`（dry-run / `--execute`）
- tests + `ci_pytest_surface` blocking path

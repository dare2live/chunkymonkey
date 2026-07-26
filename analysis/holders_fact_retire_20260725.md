# Holders fact retire — 2026-07-26
>
> 状态：evidence-only

## Verdict: FIXED

`fact_top10_holder_period` fully retired and DROPped. Formal SSOT remains:

`landing_miaoxiang_holders_top10` → `canonical_top10_float_holders_period` → `accepted_partition`.

## Binding (executed)

| Decision | Result |
|---|---|
| Names | `dim_active_a_stock` via `security_master.active_stock_name_map` |
| Episode rebuild | canonical-only (`disclosure_enrichment_projection`) |
| From-fact catchup | retired; provider `land_holders_notice_partitions_forward` kept |
| Legacy mirror / land-from-legacy | fail-closed `holders_compat_retired` |
| Live DROP | yes + `mart_data_deprecation_record` |

## Pre-DROP evidence

- Canonical enrichment fill (live): change_status/shares/holder_type/share_class = 100%; hold_change_num ≈ 80.6% (null OK for 新进)
- Fact tip frozen `notice=20260717` / `report=20260715`; canonical tip `notice=20260725` / `report=20260723`
- Fact rows at DROP: 1,726,573

## Live post-DROP

- `duckdb_tables`: `fact_top10_holder_period` absent
- Deprecation: `holders_fact_retire_20260726` → replacement `canonical_top10_float_holders_period`, status `DROPPED`

## Code / config

- Readers: dossier, screener, institution_profile → dim/canonical
- Writers: `_write_legacy_direct` / `accept_*_from_legacy` raise retired
- Transport: holders land-from-legacy raises `holders_compat_retired`
- Shadow: holders MATCH on canonical tip only (no legacy plane)
- Contract: `CONTRACT_VERSION=3`, `COMPATIBILITY_RETIRED=True`
- schema_core: no CREATE; schema_migrations: `DROP TABLE IF EXISTS`
- data_layers / data_access / moth claim / db_health / source_watermarks cleaned

## Tests

Targeted suite (dossier/screener/db_health/dual_plane/watermarks/enrichment/institution/holders_aif10/disclosure_*): **109 passed**.

## Non-goals (kept)

- org/stk compatibility tables retained
- No fact backfill / no RX

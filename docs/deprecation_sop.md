# Data Source Deprecation SOP

Policy: delete retired data instead of archiving it in production DuckDB files. A source is retired when a higher-tier source covers the same production capability or when the source has no contracted unit/schema semantics.

## Step 1 - Decision Record

Deliverables:

| Deliverable | Required content |
|---|---|
| Source decision | Source name, capability, tier, replacement source, owner, decision date |
| Coverage proof | Row count, code count, min/max date, overlap query, missing-key count |
| Risk statement | Downstream tables that read the source and the exact breakage if not removed |
| Approval note | Link or text reference to the owner decision |

Minimum SQL for stock K-line retirement:

```sql
ATTACH 'data/market.duckdb' AS market;

WITH retired_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline
    WHERE source = 'akshare_sina'
      AND freq = 'daily'
      AND adjust = 'qfq'
),
trusted_codes AS (
    SELECT DISTINCT code
    FROM market.price_kline_tdxhub
    WHERE freq = 'daily'
      AND adjust = 'qfq'
)
SELECT
    (SELECT COUNT(*) FROM retired_codes) AS retired_codes,
    (SELECT COUNT(*) FROM trusted_codes) AS trusted_codes,
    (SELECT COUNT(*) FROM retired_codes r JOIN trusted_codes t USING (code)) AS overlap_codes,
    (SELECT COUNT(*) FROM retired_codes r LEFT JOIN trusted_codes t USING (code) WHERE t.code IS NULL) AS retired_only_codes;
```

## Step 2 - Read Path Removal

Deliverables:

| Deliverable | Required content |
|---|---|
| Code search evidence | `rg -n "<source>|price_kline|upsert_price_rows"` before and after |
| Reader patch | Canonical readers use tier-1 relation or an allowlisted benchmark relation only |
| Writer patch | Production writer rejects retired source labels before database write |
| Test evidence | Unit test or script output proving retired source no longer reaches canonical marts |

Rules:

| Rule | Enforcement |
|---|---|
| Tier-3 sources cannot appear in canonical stock K-line views | Block downstream build |
| Tier-2 fallback must be allowlisted by code and capability | Reject rows outside allowlist |
| Raw source labels must not be normalized into trusted labels | Preserve source label and tier |

## Step 3 - Physical Delete

Deliverables:

| Deliverable | Required content |
|---|---|
| Pre-delete count | Source, rows, codes, min date, max date |
| Delete SQL | Exact `DELETE` or `DROP TABLE` statement |
| Post-delete count | Retired row count equals 0 |
| Vacuum plan | DuckDB maintenance command if file size matters |

Template:

```sql
ATTACH 'data/market.duckdb' AS market;

SELECT source, COUNT(*) AS rows, COUNT(DISTINCT code) AS codes, MIN(date), MAX(date)
FROM market.price_kline
WHERE source IN (
    'akshare_sina',
    'akshare_eastmoney',
    'akshare_tx',
    'akshare_mootdx',
    'chatgpt_import',
    'mootdx',
    'eastmoney_direct',
    'derived_from_daily'
)
GROUP BY source;

DELETE FROM market.price_kline
WHERE source IN (
    'akshare_sina',
    'akshare_eastmoney',
    'akshare_tx',
    'akshare_mootdx',
    'chatgpt_import',
    'mootdx',
    'eastmoney_direct',
    'derived_from_daily'
);

SELECT COUNT(*) AS retired_rows_remaining
FROM market.price_kline
WHERE source IN (
    'akshare_sina',
    'akshare_eastmoney',
    'akshare_tx',
    'akshare_mootdx',
    'chatgpt_import',
    'mootdx',
    'eastmoney_direct',
    'derived_from_daily'
);
```

## Step 4 - Rebuild Gate

Deliverables:

| Deliverable | Required content |
|---|---|
| Audit report | `backend/scripts/nightly_data_audit.py` output with severity `ok` |
| Rebuild manifest | Label build id, feature build id, model id, prediction build id |
| KPI invalidation | Corrupt downstream marts marked stale before rebuild |
| Regression guard | CI or nightly cron command that fails when retired source rows reappear |

Required verification:

```bash
python backend/scripts/check_sina_tdxhub_overlap.py
python backend/scripts/nightly_data_audit.py --lookback-days 30
rg -n "akshare_sina|akshare_eastmoney|akshare_tx|akshare_mootdx|chatgpt_import|mootdx|eastmoney_direct|derived_from_daily" backend/routers backend/scripts backend/services
```

The grep is allowed to return documentation, migration records, and explicit deprecation tests only. It must not return production K-line writers or model/label readers.

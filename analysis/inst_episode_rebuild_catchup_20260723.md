# Institution episode rebuild catch-up (2026-07-23)

> Status: evidence-only / **FIXED** (ops rebuild; local DuckDB artifact)  
> Scope: `institution_profile.rebuild_all()` — `period_windows` → `build_episodes` → `build_profiles`  
> Related: residual 55 in `inst_profile_coverage_lift_20260723.md`; diagnosis [Explain 55 no-episode holders](93b82e24-dca4-4723-8fdb-460651eaf788)  
> Bans honored: serialize DuckDB writes; **no** org mass refresh; **no** invent-from-top10

---

## Root cause (pre-rebuild)

Latest HS-A top10 holders missing episode were **not** a profile-filter bug.
`period_windows` / `fact_inst_episode` lagged holders canonical:

| Plane | Before |
|---|---|
| `period_windows.max(report_date)` | `20260630` |
| `fact_inst_episode.max(open_date)` | `20260629` |
| `canonical_top10_float_holders_period` (share_class A) max report | `20260721` |
| Latest HS-A top10 distinct holders | 32,191 |
| → with episode / profile | 32,136 / 32,136 |
| **no episode** | **55** (0.17%) |

55/55 had VWAP-capable prices; join drop was stale materialized windows only.

---

## Action

```bash
cd /Users/dp/Documents/M/stock/chunkymonkey
PYTHONPATH=backend .venv/bin/python - <<'PY'
from services.institution_profile import rebuild_all
print(rebuild_all())
PY
```

Live run: **2026-07-23T10:16:14 → 10:18:58** (+08), **163.76s**, single writer on `feature_store.duckdb` (sm/mk/tr attached `READ_ONLY`).

```text
period_windows=139405
opened=368907 closed=317394 seeded=32537
no_price_skip=36530 unpriced_close=42 superseded=14
episodes=368907 profiles=122519 profile_dims=416618
```

---

## Before / after (measured live)

| Metric | Before | After |
|---|---:|---:|
| `period_windows` n / max report | 139,255 / `20260630` | 139,405 / **`20260721`** |
| `fact_inst_episode` n / max open / holders | 368,662 / `20260629` / 122,464 | 368,907 / **`20260721`** / 122,519 |
| `mart_inst_profile` n | 122,464 | 122,519 |
| Latest HS-A top10 holders | 32,191 | 32,191 |
| → with episode | 32,136 | **32,191** |
| → with profile | 32,136 | **32,191** |
| **no episode residual** | **55** | **0** |
| episode without profile (top10) | 0 | **0** |
| all episode holders → profile | 122,464 / 122,464 | **122,519 / 122,519** |

Deep-link gate: **profile coverage ≥ episode coverage** (top10 and global) — PASS.

Sample previously residual names now present with thin display rows:

| holder | episodes | metrics_status |
|---|---:|---|
| 何丽琼 | 1 | holding_only |
| 儒意电影娱乐股份有限公司回购专用证券账户 | 1 | holding_only |

---

## Residual / failures

| Item | Result |
|---|---|
| Residual no-episode (latest HS-A top10) | **0** |
| Rebuild failures | **none** |
| Org refresh / invent-from-top10 | **not run** |

---

## Re-run notes

- DB files under `data/*.duckdb` are **local artifacts** (not committed). Re-run the snippet above on any machine whose holders canonical ahead of `period_windows`.
- Safe when: no concurrent writer on `feature_store.duckdb`; do **not** couple to org_holding mass refresh.
- After holders catch-up past episode frontier, expect the same lag until next `rebuild_all()`.
- Ranking honesty unchanged (`MIN_EPISODES` + rankable alpha); new rows may be `holding_only` / `low_sample`.

Label: **FIXED** — residual 55 cleared by episode catch-up rebuild.

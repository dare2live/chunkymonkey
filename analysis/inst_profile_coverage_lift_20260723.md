# Institution profile coverage lift (2026-07-23)

> Status: evidence-only / **FIXED** (display deep-link) / ranking honesty unchanged  
> Scope: `build_profiles` → `mart_inst_profile` display rows for every non-empty holder with episodes  
> Live rebuild: feature_store profiles only (episodes unchanged)  
> Related: `holders_stock_dossier_lineage_audit_20260721.md` §2.1 (prior ~54% profile join)

---

## Owner ask

Latest HS-A top10 holders had ~99% episode coverage but only ~half a `mart_inst_profile` row → dossier could not deep-link 机构档案. Owner: **这应该补上啊** — every top10 `holder_name_norm` with episodes should get a **displayable** profile (thin/low_sample OK); do not invent alpha; fail-closed.

---

## Diagnosis (exact drop filters)

Old `build_profiles` hard-gated the mart on:

```text
status='closed' AND NOT seeded AND NOT is_passive AND alpha_c1 IS NOT NULL
```

Holders with episodes but **zero** rows matching that predicate never got a mart row.

Live drop buckets among episode holders **missing** profile (before lift):

| Drop reason | Count (all episode holders) | Latest top10 missing |
|---|---:|---:|
| `holding_only` | ~18,054 | 17,972 |
| `closed_but_not_rankable` (seeded / no alpha) | ~9,842 | 1,029 |
| `all_passive` (ETF/指数/联接) | 479 | 186 |
| other | 5 | — |
| no episode at all | — | 55 |

`MIN_EPISODES=10` / `low_sample` was **not** the missing-row cause — it only flagged rows that already existed. Ranking list already filtered `n_closed >= 10`.

---

## Filter change

| Surface | Before | After |
|---|---|---|
| `mart_inst_profile` membership | only holders with ≥1 rankable closed episode | **every** non-empty `holder` with ≥1 episode |
| Rankable metrics (`n_closed`, `median_alpha`, …) | same predicate as membership | **unchanged predicate** via `FILTER (WHERE rankable)`; else **NULL** |
| Passive products | excluded from mart entirely | **display row** kept; `metrics_status=passive_product`; `n_closed=0` → not ranked |
| Empty / null holder name | N/A | **dropped** (invalid) |
| `mart_inst_profile_dim` | rankable only | **unchanged** (dims without alpha stay empty) |
| `list_profiles` ranking | `n_closed >= min_episodes` | + `median_alpha IS NOT NULL` (never sort NULL as skill) |

New honesty columns: `n_episodes`, `n_holding`, `is_passive_holder`, `metrics_status`
∈ `{ranked, low_sample, holding_only, no_closed_alpha, passive_product}`.

**Passive product policy (documented):** keep displayable deep-link; ban from skill ranking (申赎驱动 ≠ 选股观点). Not a silent drop.

---

## Before / after (measured live)

| Metric | Before | After |
|---|---:|---:|
| Latest HS-A top10 distinct holders | 32,191 | 32,191 |
| → with `fact_inst_episode` | 32,136 (**99.83%**) | 32,136 (**99.83%**) |
| → with `mart_inst_profile` | 12,949 (**40.23%**) | 32,136 (**99.83%**) |
| All episode holders → profile | 94,084 / 122,464 (**76.83%**) | 122,464 / 122,464 (**100%**) |
| Ranked-eligible (`n_closed≥10` + alpha) | (subset of old mart) | **3,294** (unchanged skill gate) |

Prior audit (2026-07-21) on a slightly different holder universe: episode **99.3%** / profile **54.2%**. Same root cause; live pre-lift top10 was **40.23%**.

Rebuild: `build_profiles` only, ~0.54s; `profiles=122464`, `profile_dims=416366`.

### metrics_status mix (after)

| status | n |
|---|---:|
| low_sample | 90,790 |
| holding_only | 19,080 |
| no_closed_alpha | 8,821 |
| ranked | 3,294 |
| passive_product | 479 |

---

## Frontend honesty

- Dossier holder chip deep-links when profile row exists; labels `·低样本` / `·持有中` / `·被动` from `metrics_status`.
- Institution detail banners for `passive_product` / `holding_only` / `no_closed_alpha` / `low_sample`; KPI shows `—` for NULL alpha (no fake %).

---

## Residual

| Residual | Count | Owner |
|---|---:|---|
| Latest top10 holders with **no episode** | **55** (0.17%) | episode pipeline / share_class / price-window skip — not profile mart |
| Episode holders without profile | **0** | — |
| Thin profiles (low_sample / holding_only / no_closed_alpha) | majority of mart | honest display; ranking still gated |
| Ranked skill list | 3,294 | intentional `MIN_EPISODES` + rankable filter |

Label: **FIXED** for deep-linkable coverage ≈ episode coverage on HS-A latest top10. Residual 55 no-episode names stay out of scope for this knife.

---

## Tests

```text
pytest tests/test_institution_profile.py tests/test_institution_profile_api.py tests/test_stock_dossier_api.py
→ 24 passed
```

New: `test_build_profiles_includes_holding_only_and_keeps_metrics_null`.

---

## Files

- `backend/services/institution_profile.py` — `build_profiles` / read API
- `backend/routers/stock_dossier.py` — metrics_status honesty
- `frontend/src/pages/{InstitutionDetailPage,StockDossierPage}.tsx` + api types
- `backend/tests/test_institution_profile{,_api}.py`

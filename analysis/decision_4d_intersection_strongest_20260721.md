# Decision log — Cap 4D 交集最强股 scope/definition (2026-07-21)

> Status: **DECIDED** (self-adversarial synthesis) / evidence-only
> Mandate: ambiguous → 2 adversarial positions → synthesize per north star, log decision.
> Tooling note: this successor session has no Task-subagent tool available (checked
> tool catalog — no spawn/Task tool present). Per §1 exception ("requested by ...
> instructions"), the mandate itself directs this adversarial process, so it is
> carried out as a self-authored two-position debate (labeled Advocate A / B) rather
> than two separately-invoked model instances like `decision_3a` did. This is a
> disclosed degradation, not a silent shortcut.

## Question

What counts as "交集最强" (intersection-strongest) for Cap 4D, and how far should
tonight's slice reach: which chains intersect, what "strong" means, how strict the
freshness gate is, and whether the per-stock lookup shares one source of truth with
the board?

## Adversarial positions

| | Advocate A (broader/richer) | Advocate B (narrower/ship-safe) |
|---|---|---|
| Chain scope | 3-way: dc_industry ∩ dc_concept ∩ sw_industry — matches plan wording "sectors/concepts" most literally | 2-way: dc_industry ∩ dc_concept only — sw membership keys off `index_member_all.l3_code` (leaf), not directly joinable against the L1 board rows without new leaf→L1 rollup logic; bolting that on tonight risks a rushed, untested aggregation bug |
| "Strong" definition | Rank sectors by raw `relative_ratio_pct` magnitude (top-K "hottest money") — simple, intuitive | Reuse `moneyflow_assist.behavior_from_regime` labels (chase/latent) — already carries the reviewed price-response honesty guards (never forces a label on an incomplete window); a second "strong" definition would fork honesty logic |
| Freshness gate | Query `dim_trading_calendar` for exact trading-day gap — precise SLA semantics | Plain calendar-day arithmetic — no extra query, and strictly conservative (calendar days ≥ trading days, so it can only be *more* eager to flag stale, never silently permissive) |
| Per-stock lookup | Dedicated single-stock SQL — avoids recomputing the whole board per dossier open | Reuse the same unsliced `_compute_intersection` the board uses — guarantees the board and the per-stock dossier tab can never diverge on what counts as a hit; traffic tier (decision-assist, not hot path) makes the recompute cost acceptable |

## North-star synthesis

Plan §3.5 asks for **input honesty** (membership + strength share a serve as-of;
UNTRUSTED/stale → unknown) and an **output that is a decision list + why-sentence**,
not a raw rank dump. Shipping something that quietly forks the reviewed Cap A
behavior-honesty guards, or that ships an untested sw leaf-rollup on a single knife,
would violate "don't invent a second definition of strong" more than it would gain
literal chain-count completeness.

**DECIDE:**

1. **Chain scope (tonight): 2-way** — `dc_industry ∩ dc_concept`. Both share the
   `fact_dc_member_daily` (`dc_member`) schema and the same `moneyflow_assist`
   sector-board honesty path, so the intersection is genuine (not a shortcut) and
   the fan-out cost is bounded. `sw_industry` intersection is a **documented
   residual**, not a silent drop — next knife should add an explicit L3→board
   leaf-rollup before joining it in.
2. **"Strong" definition: reuse behavior labels** (`chase`/`latent` from
   `moneyflow_assist.behavior_from_regime`) rather than re-ranking by raw ratio.
   This directly satisfies "reuse pulse `/strongest` honesty" read as *reuse,
   don't reinvent* — the incomplete-window → `unknown` guard and the
   price-response asymmetry fix already reviewed for Cap A carry over for free.
3. **Freshness gate: calendar-day lag + chain as-of equality.** Chains must report
   the *same* as-of trade_date (mismatch → stale) and that as-of must not lag the
   latest completed trading day (from `services.calendar`) by more than
   `sla_max_lag_calendar_days` (config, default 1). Calendar-day arithmetic is a
   conservative (never permissive) approximation of the plan's fail-closed intent;
   promote to an exact trading-day count later if false-positive staleness is
   observed in production.
4. **Per-stock lookup shares the board's computation** (`_compute_intersection`,
   unsliced) — the board slices to `limit` for display; the per-stock dossier tab
   searches the full unsliced result so a real hit ranked outside the display
   `limit` is never mis-reported as "not in intersection".
5. **Output shape**: ranked decision list with a `why` sentence per row (named
   industry + concept sector, both behaviors) — never a bare rank table. Empty/­
   stale states return explicit `reason` codes, not silence.

## NON-goals

- sw_industry intersection this knife (documented residual, §2 above)
- A second/forked behavior-strength taxonomy diverging from `moneyflow_assist`
- Trading-day-exact SLA computation (calendar-day approximation accepted for v0)
- Fusing intersection labels into Tier0/Tier2 accepted state
- Optuna / StrategyRelease / mass org / margin thaw

# Architecture Reform Context

This document explains why the project is in a governance-first phase. It is
historical context and stable rationale, not the live status ledger. Current
FAIL/WARN counts, exact file progress, GCP state, and next commands belong in
`../goal.md`.

## Why This Exists

ChunkyMonkey is a real-money A-share quantitative system. The project goal is
not a beautiful backtest; it is a trustworthy candidate and monitoring system
that survives production constraints.

The reform started when the 300616 sentinel case exposed that a single stock
could pass through data, formula, signal, strategy, and backtest layers and
surface system-wide defects. The conclusion was not "fix 300616"; it was "fix
the architecture that made 300616 impossible to trust."

## Sentinel Case

300616 was useful because the user could reason about three visible rally waves
and ask the system to find the buy points. That forced the whole pipeline to
answer a concrete question:

| Layer | Question exposed by 300616 |
|---|---|
| Data | Is this stock even in the tradable universe? |
| Formula | Can the formula produce a signal without future data? |
| Sampling | Did validation cover 创业板, 科创板, 沪主板, and 深主板? |
| Search | Did Optuna actually search parameters or just rerun defaults? |
| Strategy | Did the backtest respect costs, T+1, limit-up buyability, and excluded stocks? |
| Operations | Did daily sync keep required facts fresh? |

## Failures Exposed

| Failure | Root lesson |
|---|---|
| Active universe came from snapshot-derived tables | K-line data is tradeability truth; cache tables are not truth |
| Code-sorted stock samples produced board bias | Runtime sample coverage matters more than DB-wide preconditions |
| Formulas without search spaces were sent to Optuna/GCP | A runnable plan is not necessarily a useful plan |
| Limit-up percentage was treated as a global parameter | Board-specific trading rules are runtime attributes from config |
| Several formulas used future information | PIT and leakage checks are zero-tolerance |
| `dim_active_a_stock` was used as active-universe truth | It is only code-to-name/cache/schema support |
| Data sync was not reliably part of the workflow | Freshness gates must be integrated with update ownership |

## First Principles

| Principle | Consequence |
|---|---|
| Real-money decisions need observable facts | Unmeasured metrics are `unknown`, not estimated |
| Every business judgment needs one truth source | K-line, calendar, and YAML rule ownership must be explicit |
| Fewer moving parts reduce failure points | Add modules/tables/config only when they remove real ambiguity or duplication |
| Runtime evidence beats static assumptions | Validate what the runner actually loads and uses |
| Research scaffolding is not production evidence | God-view signals, proxy data, and warn-only gates must stay labeled |

## Durable Architecture Direction

| Area | Direction |
|---|---|
| Universe | Use `services.universe` and K-line truth; never infer active stocks from `dim_active_a_stock` |
| Rules and thresholds | Put business rules in YAML/config or governed tables; code literals need a documented exception |
| Data needs | Register consumer, source priority, grain, PIT key, freshness SLA, evidence status, and production eligibility |
| Updater | Keep it as supervisor: plan, schedule, state, locks, evidence aggregation |
| Domain modules | Each source/calculation module owns its writes, validation, watermark, and StepResult evidence |
| Audit tools | Gates must be executable, current, and reviewed before results are used as evidence |
| Docs | Current state in `goal.md`; durable rules in active docs; dated evidence in `analysis/` |

## What This Context Does Not Authorize

| Temptation | Decision |
|---|---|
| Resume 300616 formulas because the story started there | Blocked until governance gates pass |
| Treat 主升浪猎手 research numbers as production | Blocked until revalidated under PIT, cost, walk-forward, and paper/forward gates |
| Use stale fund-flow or market-perception data as a signal | Blocked or `unknown` until freshness and lineage pass |
| Start GCP because a prior run existed | Blocked until the current GCP controlled-use plan is stated |
| Add a new doc for every idea | Blocked unless another active doc is merged, archived, or deleted in the same slice |

## Post-Governance Mainline

After architecture/docs/test/data/tooling gates pass, the project returns to
business validation in this order:

| Order | Work |
|---:|---|
| 0 | 主升浪猎手 serious research and validation |
| 1 | BestChoice artifact freeze and challenger import |
| 2 | 300616 original formula replay |
| 3 | 300616 derived formulas and search spaces |
| 4 | Main-project backtest and paper simulation |
| 5 | Candidate, holding, profile, and frontend workflow |

The north star remains "buy before the main move when evidence is trustworthy."
The method is governance first, measured validation second, frontend last.

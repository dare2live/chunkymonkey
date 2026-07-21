# Cap 3A + 3C — moneyflow decision assist + tabs (2026-07-21)

> Status: evidence-only / product knife
> Label: **FIXED** subset (sector board + stock dossier 资金 + C tabs)
> Decision: `analysis/decision_3a_moneyflow_assist_20260721.md` (adversarial synthesis)
> Authority: product_plan_reeval §2/§3.4; backlog Cap A/C; goal.md bans intact

## Scope shipped

| Piece | Delivery |
|---|---|
| Host | `#/market` page tabs「市场感知｜资金决策辅助」; dossier `资金` tab enabled |
| API | `GET /api/v3/decision/moneyflow/board` + `/moneyflow/stock/{code}` — **not** under `/pulse` |
| Horizons | 1/3/5/10/20/30/60 chips; incomplete window → `unknown` |
| Relative ratio | sector: implied mv from published `cum_ratio_20d`; stock: `circ_mv` (万元→元); never invent |
| Behavior | Tier3 map `flow_regime` → 潜伏/抢筹/出货迹象 (`moneyflow_behavior_v0`); price-response guards; disclaimer |
| C tabs | Market page dual tab + dossier moneyflow lazy fetch |

## Honesty / NON-goals held

- Sensing cards keep「零买卖暗示」; assist is a separate Tier3 surface
- No pulse engine / `market_pulse.yaml` edits; labels not fused into Tier0
- DC vs tushare stock planes separately labeled; no cross-chain ranking
- HS-A gate on stock endpoint (`classify_exclusion`)
- Incomplete horizon → behavior **unknown** (chase/distribute both require known window return)
- No Optuna / Release / mass org / margin thaw / 4D

## Tests

`tests/test_moneyflow_assist.py` (blocking): behavior guards (incl. distribute+None),
incomplete horizon board unknown, 20d ratio consistency, board+stock API + BJ 404.

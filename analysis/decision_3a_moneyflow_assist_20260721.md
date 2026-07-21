# Decision log — Cap 3A moneyflow assist host/taxonomy (2026-07-21)

> Status: **DECIDED** (adversarial synthesis) / evidence-only
> Mandate: ambiguous → 2 Task agents (sonnet vs gpt-5.6-sol) → synthesize per north star
> Agents: [Advocate A](212e8255-7b3d-4e79-83e6-9ffbb1fba151) dossier-only+new taxonomy;
> [Advocate B](2bd01ad3-eb8d-43d4-87bd-88c9f0ca6865) market-adjacent+regime-map

## Question

Where does Cap A live tonight, and is 潜伏/抢筹/出货 a new taxonomy or a map from `flow_regime`?

## Adversarial positions

| | A (sonnet) | B (gpt) |
|---|---|---|
| Host | Dossier `资金` tab only; zero MarketPage touch | `#/market` Tier3 tab reusing pulse boards |
| API | New `/stock/{code}/moneyflow` | Extend `/pulse/flow_assist` |
| Taxonomy | New stock-grain `behavior_label_v0` | Map existing `flow_regime` → product labels |
| Denom | `dim_stock_segment_daily.circ_mv` | Existing sector_mv / `cum_ratio_*` |

## North-star synthesis

Cap A asks for **industry/sector/concept conclusions** *and* stock horizons. Plan §3.4 allows **market tab OR dossier 资金**. Sensing contract **"零买卖暗示"** is load-bearing — must not put decision labels on sensing cards.

**DECIDE:**

1. **Host (both, C tabs):** `#/market` page-level tab「资金决策辅助」**separate from**「市场感知」; enable dossier `资金` tab for this-stock horizons + sector read-through. Sensing cards stay descriptive-only.
2. **API:** new `/api/v3/decision/*` consumer router — **reads** pulse marts + stock moneyflow facts; **does not** edit `market_pulse.yaml` / pulse engine (A's blast-radius win + B's reuse win).
3. **Taxonomy:** versioned Tier3 **map** from `flow_regime` (+ price response) → `潜伏迹象|抢筹迹象|出货迹象|unknown` — reuse measured bricks; labels are product, never written back to Tier0/2; disclaimer required.
4. **Denominator:** sector = existing cum_ratio / sector_mv pattern generalized to horizons (unknown if window incomplete); stock = `circ_mv` ratio same discipline. No cross-chain ranking.
5. **Scope tonight:** sector board assist + stock moneyflow panel + tabs; no 4D; no Optuna; no pulse rewrite.

## NON-goals

- Buy/sell copy on sensing widgets
- Second flow_regime engine / invented denominators
- Fusing labels into Tier0 accepted
- Claiming measured edge for behavior labels

# Formula Local Optuna Merge Plan

- input: `/Users/dp/Documents/M/stock/bestchoice/analysis/formula_local_optuna_adoption_candidates.csv`
- stock_best: `/Users/dp/Documents/M/stock/bestchoice/analysis/stock_formula_best.csv`
- plan_output: `/Users/dp/Documents/M/stock/bestchoice/analysis/formula_local_optuna_merge_plan.csv`
- replacement_output: `/Users/dp/Documents/M/stock/bestchoice/analysis/formula_local_optuna_stock_best_replacements.csv`
- rows: `20`
- replacements: `2`

## Replacements

| stock | formula | old_variant | new_variant | old_score | new_score | delta | val_delta |
|---|---|---|---|---:|---:|---:|---:|
| `002718` | `activity_breakout` | `default` | `local_optuna_t24_vsplit` | 79.45 | 83.97 | 4.52 | 0.77 |
| `688700` | `activity_breakout` | `classic_capped` | `local_optuna_t24_vsplit` | 65.02 | 68.26 | 3.25 | 4.75 |

## Policy

- This script is dry-run only and does not modify `analysis/stock_formula_best.csv`.
- Replacement rows preserve the production schema and use `local_optuna_t<trials>_vsplit` as `variant_id`.
- Only rows passing the adoption guardrails are emitted as replacements.

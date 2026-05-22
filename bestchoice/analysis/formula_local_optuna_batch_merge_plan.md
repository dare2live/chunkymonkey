# Formula Local Optuna Merge Plan

- input: `analysis/formula_local_optuna_batch_adoption.csv`
- stock_best: `/Users/dp/Documents/M/stock/bestchoice/analysis/stock_formula_best.csv`
- plan_output: `analysis/formula_local_optuna_batch_merge_plan.csv`
- replacement_output: `analysis/formula_local_optuna_batch_stock_best_replacements.csv`
- rows: `26005`
- replacements: `1146`

## Replacements

| stock | formula | old_variant | new_variant | old_score | new_score | delta | val_delta |
|---|---|---|---|---:|---:|---:|---:|
| `301087` | `gs_pullback_confirm` | `default` | `local_optuna_t24_vsplit` | 13.93 | 78.59 | 64.66 | 84.56 |
| `002320` | `gs_pullback_confirm` | `loose` | `local_optuna_t24_vsplit` | 32.56 | 95.00 | 62.44 | 52.26 |
| `688266` | `gs_pullback_confirm` | `default` | `local_optuna_t24_vsplit` | 23.47 | 84.82 | 61.35 | 100.70 |
| `301186` | `gs_pullback_confirm` | `default` | `local_optuna_t24_vsplit` | 1.78 | 57.30 | 55.52 | 75.48 |
| `000596` | `activity_breakout` | `default` | `local_optuna_t24_vsplit` | 38.88 | 92.06 | 53.18 | 77.52 |
| `300007` | `gs_pullback_confirm` | `loose` | `local_optuna_t24_vsplit` | 16.37 | 65.95 | 49.58 | 40.97 |
| `600993` | `gs_pullback_confirm` | `loose` | `local_optuna_t24_vsplit` | 37.54 | 86.14 | 48.61 | 38.65 |
| `000786` | `activity_breakout` | `strict_capped` | `local_optuna_t24_vsplit` | 32.80 | 80.96 | 48.16 | 33.98 |
| `601326` | `volume_base_breakout` | `case_301511_broad` | `local_optuna_t24_vsplit` | 38.72 | 86.77 | 48.05 | 21.96 |
| `603429` | `volume_base_breakout` | `case_301511_broad` | `local_optuna_t24_vsplit` | 31.30 | 78.34 | 47.05 | 15.45 |
| `000623` | `gs_pullback_confirm` | `default` | `local_optuna_t24_vsplit` | 49.46 | 95.00 | 45.54 | 5.17 |
| `002515` | `gs_pullback_confirm` | `default` | `local_optuna_t24_vsplit` | 49.85 | 95.00 | 45.15 | 65.22 |
| `003011` | `activity_breakout` | `default` | `local_optuna_t24_vsplit` | 44.11 | 88.03 | 43.92 | 25.67 |
| `605122` | `volume_base_breakout` | `case_301511_cooldown` | `local_optuna_t24_vsplit` | 41.91 | 85.78 | 43.88 | 35.69 |
| `603617` | `activity_breakout` | `default` | `local_optuna_t24_vsplit` | 35.95 | 79.45 | 43.50 | 13.21 |
| `300075` | `volume_base_breakout` | `case_301511_broad` | `local_optuna_t24_vsplit` | 38.58 | 80.85 | 42.27 | 6.68 |
| `002558` | `gs_pullback_confirm` | `loose` | `local_optuna_t24_vsplit` | 52.66 | 93.53 | 40.87 | 14.51 |
| `002887` | `activity_breakout` | `strict_capped` | `local_optuna_t24_vsplit` | 37.60 | 77.99 | 40.40 | 28.25 |
| `601669` | `gs_pullback_confirm` | `loose` | `local_optuna_t24_vsplit` | 46.33 | 86.64 | 40.31 | 54.67 |
| `300404` | `volume_base_breakout` | `case_301511_cooldown` | `local_optuna_t24_vsplit` | 36.50 | 76.19 | 39.68 | 35.86 |

## Policy

- This script is dry-run only and does not modify `analysis/stock_formula_best.csv`.
- Replacement rows preserve the production schema and use `local_optuna_t<trials>_vsplit` as `variant_id`.
- Only rows passing the adoption guardrails are emitted as replacements.

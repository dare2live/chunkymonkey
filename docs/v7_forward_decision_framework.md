# v7 Forward Deploy Decision Framework

> 2026-05-23 created per Option 4 forward deploy. Avoids endless retrain pattern.

## 当前 state (2026-05-23 deploy 起点)

- v7 model: `lgbm_phase5_v7_20260523T010000Z` (panel v5c clean universe, true train-log)
- Registry: `v7_clean_panel_v5c_20260523` production_status=`candidate_forward_monitor`
- Decision: `hold_challenger`
- Capital allocation: 5%
- Window: 6 weeks (2026-05-23 → 2026-07-04)

## 主要 KPI baseline (paper_sim v7)

| Metric | paper_sim | Forward expectation |
|---|---|---|
| Sharpe | 0.87 | 0.5-0.8 (real-world slippage haircut) |
| Ann ret | 21.7% | 15-25% |
| Max DD | -19.0% | -20% to -25% |
| Win rate | 40% | 35-45% |
| Phase 4 PBO | 0.094 PASS | - |
| Phase 4 IS-OOS drop | 63.5% | - |

## Decision tree (每周 review)

### Week 1-2 (early phase)

| Condition | Action |
|---|---|
| 实盘 sharpe ≥ 0.5 (paper_sim 一致) | **HOLD continue** |
| 实盘 sharpe 0.3 - 0.5 (低于 paper) | **HOLD with caution** monitor closely |
| 实盘 sharpe < 0.3 | **ALARM** investigate (regime / slippage / data) |
| max_dd worse than -25% | **ABORT** stop trading, revert V4 |
| picks 含 contamination > 5% (ST/退市/BSE) | **ABORT** data integrity issue |

### Week 3-4 (mid phase)

| Condition | Action |
|---|---|
| Cumulative sharpe ≥ 0.6 | **HOLD continue, consider scale to 10% capital** |
| Sharpe 0.3-0.6 | **HOLD as-is** |
| Win rate < 35% for 3+ consecutive weeks | **ABORT** strategy quality concern |
| Sharpe < 0.3 for 4+ consecutive weeks | **ABORT** confirmed underperformance |

### Week 5-6 (decision phase)

| Condition | Action |
|---|---|
| Cumulative sharpe ≥ 0.8 + max_dd ≥ -20% | **PROMOTE to production champion** (auto-rotate registry production_status=production, V4 → previous_champion) |
| Cumulative sharpe 0.5-0.8 | **EXTEND monitor** 6 → 12 weeks for more evidence |
| Cumulative sharpe < 0.5 | **REJECT** revert to V4, retry with different model class (linear/factor) |

## Capital scaling rule (if HOLD continues)

- Week 1-2: **5%** (initial)
- Week 3 (if sharpe ≥ 0.5): **10%**
- Week 5 (if cumulative sharpe ≥ 0.6): **15%**
- Post-promote: **20-30%** with V4 ensemble fallback

## Abort recovery plan

If ABORT triggered:
1. Stop v7 trading immediately
2. Capital reverts to V4 production champion
3. Log abort reason in registry decision_reason
4. Post-mortem: what diverged from paper_sim?
   - Regime change?
   - Slippage > expected?
   - 14% contamination 真 impacts? (panel v5c was clean but BC picks not yet clean)
   - Survivorship bias bigger than estimated?
5. Decide whether retry with different approach OR park retry

## Non-emergency review schedule

- **Daily** (auto via cron): `monitor_v7_forward.py` runs 8:30 AM, log contamination + paper_sim sanity
- **Weekly** (manual): aggregate week's forward picks ret, compare to paper_sim baseline
- **Mid-phase (Week 3)**: full evidence review + scaling decision
- **End of phase (Week 6)**: promote / extend / reject decision

## Operational decisions NEEDED from user

| Decision | Default if no response | User input needed |
|---|---|---|
| Phase 4 IS-OOS strict 0.30 vs tree-model 0.50 | Keep strict 0.30 (no game) | Y/N |
| If v7 promote, auto-rotate registry? | Manual approval | Y/N |
| Forward capital cap | $X per week per stock | $X |
| Real broker integration (paper account first?) | Paper account default | Y/N |

## Linked artifacts

- v7 model: `mart_p0b_lambdamart_v6_predictions` model_id=lgbm_phase5_v7_20260523T010000Z
- Registry: `mart_strategy_result_registry` result_id=v7_clean_panel_v5c_20260523
- Monitor: `backend/scripts/monitor_v7_forward.py`
- Daily report: `data/reports/v7_forward_monitor.json`
- Cron: 30 8 * * * local
- Daily pipeline: scripts/daily_update.sh Step 5d

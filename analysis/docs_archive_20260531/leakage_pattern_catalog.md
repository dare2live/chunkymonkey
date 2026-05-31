# Leakage Pattern Catalog

> 2026-05-22 创建. 触发: 用户 push back "同样问题总发生, 反思一下".
> 此 catalog 是 **systematic enumeration**, 不是按反例一类一类补的.
> 每个 pattern: 描述 + 反例 + 检测方法 + audit tool check # + status.

## 设计原则

1. **Catalog 优先**: 先 enumerate 所有 known leakage modes, 后建检测.
2. **Test fixture**: 每个 pattern 有历史反例 (known-leaky panel/model/feature) 作 ground truth.
3. **Audit tool 必须 cover ALL patterns**: 不止 catch 我们当下想到的.
4. **新发现 leakage**: 加 catalog + 加 tool check + 加 test fixture (3 step lockstep).
5. **Reference**: CLAUDE.md §4.1 (8 patterns) + AI Quant 文章 (5 patterns) + López de Prado.

## 10 类已知 leakage patterns

### Pattern 1: Optuna 全段 in-sample search

**描述**: Optuna 用 full-period data 跑 search, 选 best params 看 full-period sharpe. params 知道 full period 信息.

**反例**: 主项目早期 stage_opt_per_stock walk_forward_mode='none' → in-sample fit. 见 CLAUDE.md §4.5.

**检测**:
- `services/optimization/governance.py::enforce_pre_insert` 拒 `walk_forward_mode='none'`
- `walkforward.split_dispatch` mandatory expanding_monthly

**Tool check**: ✓ (governance)

**Status**: ✓ done

---

### Pattern 2: Selector ORDER BY full-period sharpe

**描述**: candidates 写完 ranker 选 ORDER BY full-period sharpe DESC, 选了 lucky in-sample winners.

**反例**: paper_sim_v2 selector_top10 ORDER BY sharpe → +312% paper_sim fake.

**检测**:
- selector 必须 ORDER BY `COALESCE(oos_sharpe, sharpe)` 或 walk-forward 派生
- governance check `selector_order_by` field

**Tool check**: ✓ (governance + manual review)

**Status**: ✓ done

---

### Pattern 3: 特征用未来 K 线 (forward index)

**描述**: `bars[sig_i+1:]` / `pd.shift(-N)` / `df.iloc[i+10]['close']` — 直接访问未来 row.

**反例**: 不 shift(1) → 假 +190% PnL (AI Quant 文章).

**检测**:
- grep panel build script for `\[i\+`, `shift\(-`, `bars[sig_i\+1:`, `.iloc[i\+`
- AST static analysis on feature engineering files

**Tool check**: [MISS] **MISSING — 需 check 7**

**Status**: [MISS] pending

---

### Pattern 4: Label 跨期 无 purge + embargo

**描述**: fwd_20d label, 用 20 天 forward return as y. Train period 必须 + 20 天 embargo 防 train data overlap test forward window.

**反例**: 早期 backtest 没 purge → 跨期 label overlap.

**检测**:
- Walk-forward window: train_end + embargo ≤ test_start
- `services/optimization/walk_forward.py::assert_no_temporal_leak` (已有部分)

**Tool check**: ✓ partial (walk_forward assert)

**Status**: ⚠ partial — 加 explicit `--embargo-days N` 严格检查

---

### Pattern 5: JOIN missing PIT predicate

**描述**: fact_/dim_ JOIN 缺 `built_at ≤ signal_date` 或 `announce_date ≤ signal_date` → 用未来数据.

**反例**: chain v4 inst_path_a 5 cols 用 latest snapshot retrospective (CLAUDE.md §4.5).

**检测**: audit_panel_leakage.py check 2 — grep JOIN 上下文 PIT predicate.

**Tool check**: ✓ check 2

**Status**: ✓ done

---

### Pattern 6: 宇宙 retrospective (PIT-fail universe)

**描述**: 用今天 HS300/CSI500 成分 回测 2018 → 当时不在成分股. 生存者偏差.

**反例**: 早期 backtest 用 `dim_active_a_stock` 当前列表过滤 historical.

**检测**:
- `dim_index_member_history` 用 `as_of_date ≤ signal_date`
- audit script grep panel build for universe filter without `as_of`

**Tool check**: [MISS] **MISSING — 需 check 8**

**Status**: [MISS] pending

---

### Pattern 7: 复权用最新 qfq factor

**描述**: 当今的 qfq factor (含历史所有 split/dividend) apply 到 2018 数据 → 2018 价位是 retrospective adjusted.

**反例**: BC daily feed 用 `v_price_kline_qfq` view — 静态 qfq factor.

**检测**:
- PIT 复权 = rebalance 时点用当时 factor
- 但 ratio-based features (BC formulas, alpha158 mostly) scale-invariant, 受影响小
- 检测: panel cols 用 absolute price level (vs ratio) → 标 PIT-affected

**Tool check**: [MISS] **MISSING — 需 check 9** (低 risk, BC formula 已 verify scale-invariant)

**Status**: ⚠ low-priority (大部分 features ratio-based)

---

### Pattern 8: 生存者偏差 (Survivorship bias)

**描述**: 只用现存上市股, 退市股不在 training. Test 实盘可能买退市股.

**反例**: panel 用 `WHERE listed_today = 1`.

**检测**:
- Panel 必须含已退市股直到 delist_date
- `dim_listing_status` PIT
- audit grep universe selection 看 `listed_today`/`active`/类似 retrospective filter

**Tool check**: [MISS] **MISSING — 需 check 10**

**Status**: [MISS] pending

---

### Pattern 9: PARTITION BY flat current-mapping (Phase D 反例)

**描述**: `PARTITION BY date, tdx_l1` — tdx_l1 from `dim_stock_tdx_industry` flat NON-PIT mapping → retrospective industry assignment.

**反例**: panel v3/v4 sector_*_tdx_l1_rel features (Phase D 2026-05-22). 92.43% IS-OOS drop.

**检测**: audit_panel_leakage.py **check 3** — detect PARTITION BY with mapping cols + verify source table has PIT marker.

**Tool check**: ✓ check 3

**Status**: ✓ done

---

### Pattern 10: NULL year gradient (time-availability leak)

**描述**: Feature column NULL 比例 by year 单调下降 (老年 100% NULL → 新年 low NULL) → ML 学 'non-NULL = recent regime'.

**反例**: panel v4 inst_holder_cnt 100/100/54/7%, beta_60d 100/3/2/18% (v6 retrain 2026-05-22). 60.8% IS-OOS drop.

**检测**: audit_panel_leakage.py **check 6** — per-feature NULL gradient > 50% = HIGH.

**Tool check**: ✓ check 6

**Status**: ✓ done

---

## Tool check coverage

| Check | Pattern | Status |
|---|---|---|
| 1 PIT markers | source pattern (P5 prerequisite) | ✓ |
| 2 JOIN PIT-strict | Pattern 5 | ✓ |
| 3 Flat current-mapping PARTITION BY | Pattern 9 | ✓ |
| 4 Mapping fallback ratio | Pattern 5 variant | ✓ |
| 5 Feature temporal variance | Pattern 5 variant (constant features) | ✓ |
| 6 NULL year gradient | Pattern 10 | ✓ |
| **7** | **Forward index in feature code** (Pattern 3) | **[MISS] pending** |
| **8** | **Universe PIT (Pattern 6)** | **[MISS] pending** |
| **9** | qfq retrospective (Pattern 7, low priority) | [MISS] pending |
| **10** | Survivorship bias (Pattern 8) | **[MISS] pending** |
| governance | Pattern 1 + 2 | ✓ |
| walk_forward.assert_no_temporal_leak | Pattern 4 | ✓ partial |

**Coverage: 6/10 patterns auto-detected**. 4 patterns 需补.

## Next implementation

1. Check 7: AST grep panel build files for `\[i+`, `shift(-)`, `iloc[i+` patterns
2. Check 8: grep universe filter for missing `as_of_date` / `effective_from` predicates
3. Check 10: grep `WHERE listed`/`active`/`existing` patterns + verify `dim_listing_status` JOIN PIT

## Test fixtures (待 build)

- `data/leakage_fixtures/`: known-leaky panels (stability / v5 / v6) + verdict labels
- `backend/tests/test_audit_panel_leakage.py`: unit test 每个 pattern fixture 必须被对应 check catch
- 加 CI gate: pre-commit hook + each PR 必 pass audit on fixtures

## 历史反例 cross-ref to catalog

| 反例 | Pattern # | When found | Tool check |
|---|---|---|---|
| `mart_stock_industry_pit` 99.978% fallback | 9 | 2026-05-15 v3 chain audit | check 3 partial (need extend) |
| chain v4 inst_path_a latest snapshot | 5 | 2026-05-15 Codex review | check 2 |
| sector_*_tdx_l1_rel retrospective | 9 | 2026-05-22 Phase D | check 3 |
| stability IS-OOS drop 92% | mixed (pattern 9 + 10) | 2026-05-22 Phase 4 gate | check 3 + 6 |
| **v6 IS-OOS drop 60%** | **10** (time-availability) | 2026-05-22 evening | **check 6 (NEW)** |
| BC selection bias MILD | not strict leakage, selection 偏 | 2026-05-22 Phase 5 audit | walk-forward audit 跨 repo (defer) |

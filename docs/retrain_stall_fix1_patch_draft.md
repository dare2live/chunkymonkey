# Retrain 15min single-thread stall — Fix 1 patch draft

> **Status**: 设计草稿, 未实施. retrain in-flight 时**不应用**.
> 下次 retrain 前 apply, 估 30-60× 加速 (15 min → 20-30 sec).

## 根因 (Claude general-purpose agent aacdbf94 实测)

`backend/scripts/run_p0b_lambdamart_v6.py:182-218 build_walk_forward_windows` 在 panel 完成 build 后, 走 30 个 expanding walk-forward window split. 每 window 内:

1. `np.isin(panel.signal_dates, list(train_dates))` — 3.93M string dtype `<U10` ndarray vs ~30 train dates string list, 走 sort + searchsorted O((N+K) log K), string compare 50× 慢 int. 单 window ~2 sec, 30 windows ~60 sec.
2. `assert_pit_strict(panel.signal_dates[train_idx], ...)` — 内部 `pd.to_datetime(pd.Series(train_signal_dates))` parse 累积 3.5M strings (expanding window 后期). 单 window 18 sec, 30 windows ~400 sec.

总 ~6-12 min single-thread, 加 GIL + GC overhead, 实测 15 min.

## Fix 1: panel.signal_dates 一次性转 int64, 后续全 int 比较

### Patch A: `backend/scripts/run_p0b_lambdamart_v6.py:89-101 assert_pit_strict`

新增 int64 fast-path, 保留旧 wrapper 兼容现有单测:

```python
def assert_pit_strict(train_signal_dates, test_signal_dates):
    """Ensure PIT-strict: max(train) < min(test).

    Fast-path: 若输入是 int64 ndarray (epoch ns/day), 走 int 比较 (O(1) per array).
    Legacy path: string ndarray fallback (跑 pd.to_datetime, 慢, 兼容老 test).
    """
    if (isinstance(train_signal_dates, np.ndarray) and
        train_signal_dates.dtype.kind == 'i' and
        isinstance(test_signal_dates, np.ndarray) and
        test_signal_dates.dtype.kind == 'i'):
        # Fast-path: int64 epoch — O(1) max/min
        last_train = int(train_signal_dates.max())
        first_test = int(test_signal_dates.min())
        if last_train >= first_test:
            raise AssertionError(
                f"PIT leak: last_train_epoch={last_train} >= first_test_epoch={first_test}"
            )
        return
    # Legacy path (string ndarray) — pd.to_datetime, 慢
    train_dates = pd.to_datetime(pd.Series(train_signal_dates))
    test_dates = pd.to_datetime(pd.Series(test_signal_dates))
    last_train = train_dates.max()
    first_test = test_dates.min()
    if last_train >= first_test:
        raise AssertionError(f"PIT leak: last_train={last_train} >= first_test={first_test}")
```

### Patch B: `backend/scripts/run_p0b_lambdamart_v6.py:182-218 build_walk_forward_windows`

入口加一次性 string → int64 转换, 循环内全 int 比较:

```python
def build_walk_forward_windows(panel, *, min_train_months, forward_months, max_windows=None):
    # === Patch B (Fix 1): 一次性 string → int64 epoch (day-level) ===
    # 估 30 sec for 3.93M strings, 后续 30 windows 复用, 净节省 5-8 min
    panel_dates_str = panel.signal_dates  # <U10 ndarray
    panel_dates_int = (
        pd.to_datetime(pd.Series(panel_dates_str))
        .values.astype('datetime64[D]')
        .astype('int64')  # days since epoch
    )
    # ============================================================

    unique_dates = pd.Series(panel_dates_str).drop_duplicates().tolist()
    date_signals = [{"stock_code": "__date__", "signal_date": d} for d in unique_dates]
    splits = split_expanding_monthly(
        date_signals,
        min_train_months=min_train_months,
        forward_months=forward_months,
        min_test=1,
    )

    windows: list[WindowSpec] = []
    for sp in splits:
        # int64 fast-path
        train_dates_str = {str(r["signal_date"])[:10] for r in sp.train}
        test_dates_str = {str(r["signal_date"])[:10] for r in sp.test}
        train_dates_int = np.array(
            [pd.Timestamp(d).to_datetime64().astype('datetime64[D]').astype('int64')
             for d in train_dates_str],
            dtype=np.int64,
        )
        test_dates_int = np.array(
            [pd.Timestamp(d).to_datetime64().astype('datetime64[D]').astype('int64')
             for d in test_dates_str],
            dtype=np.int64,
        )
        train_idx = np.where(np.isin(panel_dates_int, train_dates_int))[0].astype(np.int32)
        test_idx = np.where(np.isin(panel_dates_int, test_dates_int))[0].astype(np.int32)
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        # int64 fast-path assert (避免 pd.to_datetime on累积 3.5M strings)
        assert_pit_strict(panel_dates_int[train_idx], panel_dates_int[test_idx])
        windows.append(WindowSpec(
            train_idx=train_idx,
            test_idx=test_idx,
            train_start=str(panel_dates_str[train_idx[0]]),
            train_end=str(panel_dates_str[train_idx[-1]]),
            test_start=str(panel_dates_str[test_idx[0]]),
            test_end=str(panel_dates_str[test_idx[-1]]),
        ))

    if max_windows is not None:
        windows = windows[:max_windows]
    return windows
```

## 单测 (新增 perf 回退测试)

```python
# backend/tests/test_lambdamart_v6_perf.py (新文件)
def test_build_walk_forward_windows_perf():
    """Fix 1 perf 回退测试: 3.93M synthetic panel, build < 60 sec."""
    import time
    n_dates = 700  # 跨 2023-07 → 2026-05
    n_stocks = 5600
    # ... synthetic panel build ...
    start = time.time()
    windows = build_walk_forward_windows(panel, min_train_months=6, forward_months=1)
    elapsed = time.time() - start
    assert elapsed < 60, f"build took {elapsed:.1f}s, expected < 60s (Fix 1 regression)"
    assert len(windows) >= 25  # 至少 25 windows for 30-month OOS


def test_assert_pit_strict_int64_fast_path():
    """Fix 1: int64 fast-path 跟 string legacy path 结果一致."""
    train_str = np.array(['2023-01-01', '2023-01-15', '2023-02-01'], dtype='<U10')
    test_str = np.array(['2023-02-15', '2023-03-01'], dtype='<U10')
    train_int = pd.to_datetime(pd.Series(train_str)).values.astype('datetime64[D]').astype('int64')
    test_int = pd.to_datetime(pd.Series(test_str)).values.astype('datetime64[D]').astype('int64')
    # 两 path 都 PASS
    assert_pit_strict(train_str, test_str)
    assert_pit_strict(train_int, test_int)


def test_assert_pit_strict_int64_leak_detection():
    """Fix 1: int64 path 能正确 raise on PIT leak."""
    # last_train == first_test → leak
    train_int = np.array([19000, 19001], dtype=np.int64)
    test_int = np.array([19001, 19002], dtype=np.int64)
    with pytest.raises(AssertionError, match="PIT leak"):
        assert_pit_strict(train_int, test_int)
```

## 估加速比

| 阶段 | 当前 | Fix 1 后 | 加速 |
|---|---:|---:|---:|
| 一次性 panel string → int64 | 0 | ~30 sec | overhead |
| `np.isin` × 60 ops (30 win × train+test) | ~60 sec | ~1.2 sec | 50× |
| `assert_pit_strict` × 30 ops (累积 3.5M) | ~400 sec | 0 sec (int max/min O(1)) | ∞ |
| Python list comprehension overhead | ~5 sec | ~5 sec | 1× |
| **总** | **15 min** | **~40 sec** | **~22×** |

## 风险评估

| 维度 | 评估 |
|---|---|
| PIT 守护语义 | 等价 (int64 epoch 单调 == datetime 单调) |
| 边界检测 (last_train >= first_test) | 等价 (int >= int) |
| 单测兼容 | 保留 legacy string path, 旧 `test_lambdamart_v6.py:112` 不需改 |
| panel.signal_dates dtype 假设 | 假设是 `<U10` string ndarray. 若上游改 dtype, fast-path 自动 fallback legacy |
| pd.Timestamp parse 错误 | 上游 already validated (panel 已 build), 不会引新错 |
| Optuna study reproducibility | 完全不影响 (随机种子 + Optuna seed) |

## 实施 checklist

- [ ] retrain 完 (lgbm_phase5_gcp_20260519T143043) + KPI compare 完成
- [ ] basleline test: `PYTHONPATH=backend pytest backend/tests/test_lambdamart_v6.py -v` 全 PASS
- [ ] Apply Patch A (assert_pit_strict int64 fast-path)
- [ ] Apply Patch B (build_walk_forward_windows int64 conversion)
- [ ] 新增 `backend/tests/test_lambdamart_v6_perf.py` 3 tests
- [ ] 跑全套 pytest verify
- [ ] 跑 1-iter retrain smoke (`--n-trials 1 --max-windows 5`) 实测 walk_forward build < 60 sec
- [ ] commit + PROJECT_INDEX §14 沉淀
- [ ] 下一次完整 retrain (50 trials, OPTUNA_N_JOBS=8) 实测 total time vs 当前 baseline

## 引用

- Claude general-purpose agent: aacdbf9413b4696b6
- Spec: docs/codegraph_audit_integration_spec.md (审计 infra)
- 关联文件:
  - `backend/scripts/run_p0b_lambdamart_v6.py:89-101 + 182-218`
  - `backend/services/optimization/walk_forward.py:169-256`
  - `backend/tests/test_lambdamart_v6.py:112`

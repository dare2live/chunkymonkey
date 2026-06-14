"""PIT 行为门 — 公式无关的前瞻泄漏检测 (L0 防泄露固化, owner=l0_bare_kline_baseline_spec §2)。

黄金标准 (优于静态名模式扫描): feature[i] 在 bars[:n] 上算的值, 必须 == 在 bars[:n+k] (追加未来 bar)
上算的同一个 i 的值。若变 = 该特征用了 t 之后信息 (lookahead) = 泄漏死。任何 rolling/EMA/未来引用 bug
都会被这个行为门抓到, 不靠开发者自觉 (mythos §14: 工具不强制=孤儿; §3 防回退三件套之行为断言)。

用法 (固化进 L0 驱动 + 测试):
  rep = assert_pit_clean(lambda b: extract_feature("macd_golden_cross", b), bars)
  if not rep["clean"]: raise SystemExit(f"PIT 泄漏: {rep}")
"""
from __future__ import annotations

import math


def assert_pit_clean(
    feature_fn, bars: dict[str, list[float]], *, probe_points: int = 24,
    future_pad: int = 5, tol: float = 1e-9,
) -> dict:
    """对 feature_fn 做行为级 PIT 核证。

    feature_fn(bars_dict) -> list[float|None] 对齐 bars。在 probe_points 个截断点 n 截 bars[:n],
    与 bars[:n+future_pad] (追加未来) 比同一前缀的特征是否逐点相等。任一不等 = lookahead 泄漏。
    返回 {clean: bool, n_checked: int, violations: [...]}。
    """
    cols = list(bars)
    total = len(bars[cols[0]])
    if total < future_pad + 3:
        return {"clean": True, "n_checked": 0, "violations": [], "note": "样本过短跳过"}

    # 截断点: 在 [future_pad+2, total-future_pad] 均匀取 probe_points 个
    lo, hi = future_pad + 2, total - future_pad
    if hi <= lo:
        return {"clean": True, "n_checked": 0, "violations": [], "note": "区间过窄跳过"}
    step = max(1, (hi - lo) // probe_points)
    cuts = list(range(lo, hi, step))[:probe_points]

    violations: list[dict] = []
    n_checked = 0
    for n in cuts:
        short = {c: bars[c][:n] for c in cols}
        long = {c: bars[c][:n + future_pad] for c in cols}
        fs = feature_fn(short)
        fl = feature_fn(long)
        # 比前 n 个 (短序列的全部) — 长序列追加未来 bar 后, 这 n 个不该变
        for i in range(n):
            a, b = fs[i], fl[i]
            n_checked += 1
            if a is None and b is None:
                continue
            if a is None or b is None or not math.isclose(a, b, rel_tol=tol, abs_tol=tol):
                violations.append({"cut": n, "idx": i, "short_val": a, "long_val": b})
                break  # 该截断点一处违例即足证泄漏, 不刷屏
    return {"clean": not violations, "n_checked": n_checked, "violations": violations}

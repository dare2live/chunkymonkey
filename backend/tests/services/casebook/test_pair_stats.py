"""有效样本量与分层判据的测试。

判据算错不会报错, 只会让每个格子的可信度标错一档 —— 而档位正是 goal.md 两条底线之一
(「格内先例不足时报先例不足, 而不是给数字」) 的执行者。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from services.casebook import pair_stats as ps


# ── A1 贪心不重叠 ────────────────────────────────────────────────────────────

def test_greedy_takes_first_then_skips_within_h() -> None:
    """H=10: 取 1 之后, 直到 12 (>1+10) 才能再取。11 恰好等于 1+10, 不取。"""
    assert ps.greedy_nonoverlap(np.array([1, 5, 11, 12, 30]), 10) == 3  # 1, 12, 30


def test_greedy_dense_signals_collapse() -> None:
    """每天都触发时, n_eff 退化成 A2 的闭式 —— 两条算法必须给同一个数。"""
    n_days = 100
    h = 10
    dense = np.arange(1, n_days + 1)
    assert ps.greedy_nonoverlap(dense, h) == ps.baseline_n_eff(n_days, h)


def test_greedy_sparse_signals_keep_all() -> None:
    """间距都大于 H 时一个不丢 —— 这是「n/H 或减半」那类近似会算错的情形。"""
    sparse = np.array([1, 100, 200, 300])
    assert ps.greedy_nonoverlap(sparse, 10) == 4


def test_greedy_clustered_signals_lose_most() -> None:
    """成簇信号即使平均间距很大, n_eff 也很低 —— gs_pullback 实测 n_eff/n=0.27 就是这个形态。"""
    clustered = np.array([1, 2, 3, 4, 5, 200, 201, 202])
    assert ps.greedy_nonoverlap(clustered, 10) == 2  # 1, 200


def test_greedy_empty() -> None:
    assert ps.greedy_nonoverlap(np.array([], dtype=int), 10) == 0


# ── A2 基线闭式 ──────────────────────────────────────────────────────────────

def test_baseline_n_eff_matches_measured_ceiling() -> None:
    """全市场最长 1,847 天 / H=10 ⇒ 168。设计文档独立算过同一个数。"""
    assert ps.baseline_n_eff(1847, 10) == 168


def test_baseline_n_eff_edges() -> None:
    assert ps.baseline_n_eff(0, 10) == 0
    assert ps.baseline_n_eff(1, 10) == 1
    assert ps.baseline_n_eff(11, 10) == 1     # 第 12 天才够开第二个不重叠窗
    assert ps.baseline_n_eff(12, 10) == 2


# ── A3 n_pair ────────────────────────────────────────────────────────────────

def test_n_pair_is_below_both_inputs() -> None:
    """1/(1/a+1/b) < min(a,b) 恒成立 —— 这是「本股层可比较不可达」的结构性证明:
    n_pair < n_eff_base <= 168 < 200, 与哪条策略无关。"""
    for a, b in [(5, 168), (168, 168), (1, 1), (400, 168)]:
        assert ps.n_pair(a, b) < min(a, b) or a == b == 1


def test_n_pair_zero_when_either_side_empty() -> None:
    assert ps.n_pair(0, 168) == 0.0
    assert ps.n_pair(50, 0) == 0.0


# ── A5 分层: 本股格按 n_pair, 不按半宽 ────────────────────────────────────────

def test_tier_thresholds() -> None:
    t = (10.0, 30.0, 200.0)
    assert ps.tier_of(9.9, t) == "insufficient"
    assert ps.tier_of(10.0, t) == "thin"
    assert ps.tier_of(29.9, t) == "thin"
    assert ps.tier_of(30.0, t) == "referable"
    assert ps.tier_of(199.9, t) == "referable"
    assert ps.tier_of(200.0, t) == "comparable"
    assert ps.tier_of(math.nan, t) == "insufficient"


def test_degenerate_win_rate_must_not_read_as_high_precision() -> None:
    """**回归测试**: p=0 或 p=1 时正态方差 p(1−p) 塌成 0, 半宽跟着塌成 0。

    2026-09-04 第一版按半宽判, 于是三个只有 9-19 天数据、每天全赢或全输的格子
    被判成「可比较」—— **信息最少的地方被判成最确定**, 与判据想表达的正好相反。
    改按 n_pair 后它们回到 insufficient。这条测试钉住那个边界。
    """
    # 那三个真实格子的形状: n_eff_sig=1, n_eff_base=1~2
    for n_s, n_b in [(1, 1), (1, 2), (2, 2)]:
        hw = ps.excess_halfwidth(0.0, n_s, 0.0, n_b)
        assert hw == 0.0, "退化 p 下半宽确实塌成 0 —— 这正是不能用它判档的原因"
        assert ps.tier_of(ps.n_pair(n_s, n_b), (10.0, 30.0, 200.0)) == "insufficient"


def test_wilson_does_not_fake_certainty_at_extremes() -> None:
    """Wilson 在 p=0/1 时**不塌** —— 它是给胜率用的, 所以那里用它而不是正态。"""
    lo, hi = ps.wilson(0.0, 5)
    assert lo == pytest.approx(0.0, abs=1e-9) and hi > 0.3, f"p=0,n=5 的上界应仍很宽, 实得 {hi}"
    lo2, hi2 = ps.wilson(1.0, 5)
    assert lo2 < 0.7 and hi2 == pytest.approx(1.0, abs=1e-9)


def test_wilson_zero_n_is_nan_not_full_range() -> None:
    """n=0 返回 nan, **不返回 (0,1) 冒充「无信息」** —— (0,1) 会被下游当成一个真区间。"""
    lo, hi = ps.wilson(0.5, 0)
    assert math.isnan(lo) and math.isnan(hi)


# ── loader fail-closed ───────────────────────────────────────────────────────

def test_real_config_loads() -> None:
    cfg = ps.load_effective_n()
    assert cfg["rule"] == "greedy_nonoverlap" and cfg["pair"] == "harmonic"
    assert ps.load_sample_tiers() == (10.0, 30.0, 200.0)


def _write(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(ps._CONFIG.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "casebook.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "mutate,hit",
    [
        (lambda r: r["effective_n"].__setitem__("rule", "raw_n"), "rule"),
        (lambda r: r["effective_n"].__setitem__("pair", "min"), "pair"),
        (lambda r: r["effective_n"].__setitem__("tiers_pp", [1, 2]), "tiers_pp"),
        (lambda r: r["effective_n"].__setitem__("tiers_pp", [30, 10, 20]), "tiers_pp"),
        (lambda r: r.pop("effective_n"), "effective_n"),
    ],
)
def test_effective_n_fail_closed(tmp_path: Path, mutate, hit: str) -> None:
    """换算法 = 换全部判定。特别是 `rule: raw_n` —— 原始 n 在 1,845 日密集抽样下
    把方差低估 2.4x, 静默回退等于把所有格子判宽一档。"""
    with pytest.raises(ValueError, match=hit):
        ps.load_effective_n(_write(tmp_path, mutate))


@pytest.mark.parametrize(
    "mutate,hit",
    [
        (lambda r: r["sample_tiers"].__setitem__("referable", 500), "必须"),
        (lambda r: r["sample_tiers"].pop("comparable"), "缺键"),
        (lambda r: r.pop("sample_tiers"), "sample_tiers"),
    ],
)
def test_sample_tiers_fail_closed(tmp_path: Path, mutate, hit: str) -> None:
    with pytest.raises(ValueError, match=hit):
        ps.load_sample_tiers(_write(tmp_path, mutate))

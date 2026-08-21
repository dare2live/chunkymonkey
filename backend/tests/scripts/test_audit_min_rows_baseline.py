"""min_rows 校准工具的判据测试。

工具存在的理由(2026-08-18 实测): registry 多个域的底线注释写着"照 histmin 校准",
但没人验证过 histmin 那天本身健康 —— dc_member 的底线 7000 就是照一次分页截断日
(库 7,919 行 vs vendor 40,000)设的, 缺陷被固化成了基准。
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_min_rows_baseline import analyse  # noqa: E402


def _table(conn, name: str, per_day: dict[str, int]) -> None:
    conn.execute(f'create table "{name}" (trade_date VARCHAR)')
    for day, n in per_day.items():
        for _ in range(n):
            conn.execute(f'insert into "{name}" values (?)', [day])


def test_a_collapsed_day_is_not_taken_as_the_calibration_basis():
    """塌陷日不能当基准 —— 否则底线照缺陷设, 永远拦不住同类缺陷。"""
    conn = duckdb.connect(":memory:")
    days = {f"2026{m:02d}{d:02d}": 100 for m in (1,) for d in range(1, 22)}
    days["20260110"] = 3          # 一次塌陷: 3 行 vs 邻域 100
    _table(conn, "t", days)

    got = analyse(conn, "t", "trade_date", healthy_ratio=0.5)

    assert got["hist_min"] == 3, got                    # 原始最小值就是那天
    assert got["hist_min_day"] == "20260110"
    assert got["basis_is_suspect"] is True              # 且被判为"待核证"
    assert got["healthy_hist_min"] == 100, got          # 健康基准跳过了它


def test_a_genuine_low_day_stays_usable_as_basis():
    """真实低值日(未低于阈值)仍可当基准 —— 判据不能把正常波动一律打成缺陷。"""
    conn = duckdb.connect(":memory:")
    days = {f"202602{d:02d}": 100 for d in range(1, 22)}
    days["20260210"] = 70         # 比值 0.7, 高于 0.5 阈值
    _table(conn, "t2", days)

    got = analyse(conn, "t2", "trade_date", healthy_ratio=0.5)

    assert got["basis_is_suspect"] is False
    assert got["healthy_hist_min"] == 70, got


def test_ratio_threshold_is_a_coarse_filter_not_a_verdict():
    """阈值只是粗筛: 调高阈值会把更多真实低值日划进"待核证", 这是有意的保守。

    实测反例说明为什么不能用它定案: dc_member 20251029 比值 0.32(远低于 0.5),
    但 vendor 逐页核证全量就是 20,748 行 —— 那天是源端真实低值, 不是缺陷。
    所以工具输出的是"待核证"而不是"缺陷", 最终裁决必须向 vendor 求证。
    """
    conn = duckdb.connect(":memory:")
    days = {f"202603{d:02d}": 100 for d in range(1, 22)}
    days["20260310"] = 70
    _table(conn, "t3", days)

    lenient = analyse(conn, "t3", "trade_date", healthy_ratio=0.5)
    strict = analyse(conn, "t3", "trade_date", healthy_ratio=0.8)

    assert lenient["basis_is_suspect"] is False
    assert strict["basis_is_suspect"] is True
    assert strict["healthy_hist_min"] == 100


def test_era_segmented_domains_are_measured_within_the_current_era_only():
    """声明了 min_rows_since 的域, 只在当前时代内求 histmin。

    2026-08-21 实测教训(本工具初版的真 bug): 没读 min_rows_since 就拿全历史 histmin
    去比今日底线, 把 margin_detail(since=20220104 / before=800) 与
    moneyflow_ind_dc(since=20260101 / before=80) 双双误报成"底线偏紧" ——
    而这两个域早在 2026-07-09 就用时代分段根治过, registry 注释里连
    "594 个 2019-2021 真实完整日成幻影缺口"都写着。
    工具自己的 docstring 警告"基准必须先被验证", 第一版却栽在同一件事上。
    """
    conn = duckdb.connect(":memory:")
    days = {}
    days.update({f"20190{m}{d:02d}": 900 for m in (1,) for d in range(1, 16)})   # 旧时代: 低行数
    days.update({f"20260{m}{d:02d}": 3500 for m in (1,) for d in range(1, 16)})  # 当前时代: 高行数
    _table(conn, "t_era", days)

    whole = analyse(conn, "t_era", "trade_date", healthy_ratio=0.5)
    current = analyse(conn, "t_era", "trade_date", healthy_ratio=0.5, since="20220104")

    # 不分段: histmin 落在 2019 的低行数时代
    assert whole["healthy_hist_min"] == 900, whole
    # 分段后: 只看当前时代, 旧时代交给 min_rows_before 负责
    assert current["healthy_hist_min"] == 3500, current
    assert current["hist_min_day"].startswith("2026"), current

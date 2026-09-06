"""判例引擎地基的口径测试。

**为什么要手算 fixture 而不是只验总数**: 全量重建的行数对上, 只证明它跑完了, 不证明
入场出场取的是正确那根 K 线。差一根 = 全部历史结论差一天, 而且不报错。所以这里用一只
自造的 5 根 K 线的股票, 每个数字都能手算核对。
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
import yaml

from services.casebook import outcome as oc


def _fixture_conn() -> duckdb.DuckDBPyConnection:
    """两只股票, 手写 OHLC。A 有 5 根, B 有 3 根 (用来验截尾)。"""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SCHEMA mkt")
    conn.execute("""
CREATE TABLE mkt.v_price_kline_qfq (code VARCHAR, date VARCHAR, open DOUBLE, close DOUBLE)
""")
    conn.execute("""
INSERT INTO mkt.v_price_kline_qfq VALUES
  ('A', '2026-01-05', 10.0, 11.0),
  ('A', '2026-01-06', 12.0, 13.0),
  ('A', '2026-01-07', 14.0, 15.0),
  ('A', '2026-01-08', 16.0, 17.0),
  ('A', '2026-01-09', 18.0, 19.0),
  ('B', '2026-01-05', 20.0, 21.0),
  ('B', '2026-01-06', 22.0, 23.0),
  ('B', '2026-01-07', 24.0, 25.0)
""")
    return conn


def test_entry_is_next_open_not_signal_day_close() -> None:
    """入场必须是**下一根**开盘 —— 信号日收盘后才知道信号, 当日不可成交 (PIT 红线)。

    A 在 2026-01-05 的 entry_open 必须是 01-06 的 open=12.0, 不是 01-05 的 close=11.0
    也不是 01-05 的 open=10.0。
    """
    conn = _fixture_conn()
    conn.execute(oc._outcome_sql((1,)))
    row = conn.execute(
        "SELECT entry_open FROM casebook_outcome_day WHERE code='A' AND date='2026-01-05'"
    ).fetchone()
    assert row[0] == 12.0, "entry 取错了 K 线 —— 必须是下一根的 open"


def test_exit_is_close_h_bars_after_entry() -> None:
    """出场 = 入场后第 H 根收盘 = LEAD(close, H+1)。

    A 在 01-05 触发, H=1: 入场 01-06 open=12.0, 出场应是 01-07 的 close=15.0。
    收益 = 15.0/12.0 − 1 = 0.25。逐个手算, 不用代码算给自己看。
    """
    conn = _fixture_conn()
    conn.execute(oc._outcome_sql((1, 2)))
    r = conn.execute(
        "SELECT entry_open, exit_1, exit_2 FROM casebook_outcome_day "
        "WHERE code='A' AND date='2026-01-05'"
    ).fetchone()
    assert r == (12.0, 15.0, 17.0), (
        f"H=1 应出在 01-07 close=15.0, H=2 应出在 01-08 close=17.0; 实得 {r}"
    )
    assert abs(r[1] / r[0] - 1 - 0.25) < 1e-12


def test_censored_is_null_never_zero() -> None:
    """窗口越过最后一根 → NULL + censored, **不是 0**。

    这是红线 3 在本层的形态: 退市股的最后 H 根就是这种情形, 填 0 会把"没数据"
    伪装成"收益为零", 而 0 在胜率里算输 —— 等于凭空给每只退市股塞一串败仗。
    """
    conn = _fixture_conn()
    conn.execute(oc._outcome_sql((2,)))
    rows = conn.execute(
        "SELECT date, exit_2, censored_2 FROM casebook_outcome_day "
        "WHERE code='B' ORDER BY date"
    ).fetchall()
    # B 只有 3 根: 01-05 入场 01-06, H=2 要 01-08 的 close → 不存在
    assert all(r[1] is None for r in rows), f"B 全部应为 NULL, 实得 {rows}"
    assert all(r[2] for r in rows), f"B 全部应标 censored, 实得 {rows}"
    assert not any(r[1] == 0 for r in rows), "censored 被填成了 0 —— 违红线 3"


def test_last_bar_has_no_entry() -> None:
    """最后一根没有下一根, entry_open 必须是 NULL 而不是自己的 open。"""
    conn = _fixture_conn()
    conn.execute(oc._outcome_sql((1,)))
    r = conn.execute(
        "SELECT entry_open FROM casebook_outcome_day WHERE code='A' AND date='2026-01-09'"
    ).fetchone()
    assert r[0] is None


def test_horizons_do_not_leak_into_each_other() -> None:
    """多个 H 同时算时互不串味 —— 每个 H 的 exit 都必须是它自己的偏移。"""
    conn = _fixture_conn()
    conn.execute(oc._outcome_sql((1, 2, 3)))
    r = conn.execute(
        "SELECT exit_1, exit_2, exit_3 FROM casebook_outcome_day "
        "WHERE code='A' AND date='2026-01-05'"
    ).fetchone()
    assert r == (15.0, 17.0, 19.0), f"三个 H 应分别出在 01-07/01-08/01-09 的 close; 实得 {r}"


# ── loader fail-closed ────────────────────────────────────────────────────────

def _write_cfg(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(oc._CONFIG.read_text(encoding="utf-8"))
    mutate(raw)
    p = tmp_path / "casebook.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return p


def test_real_config_loads() -> None:
    w = oc.load_window()
    assert w.horizons and all(h > 0 for h in w.horizons)
    assert w.entry == "next_open" and w.censored_policy == "null_never_zero"


@pytest.mark.parametrize(
    "mutate,hit",
    [
        (lambda r: r["window"].__setitem__("entry", "signal_day_close"), "window.entry"),
        (lambda r: r["window"].__setitem__("censored_policy", "fill_zero"), "censored_policy"),
        (lambda r: r["window"].__setitem__("brand_new_key", 1), "未知键"),
        (lambda r: r.__setitem__("horizons", []), "horizons"),
        (lambda r: r.__setitem__("horizons", [0]), "horizons"),
        (lambda r: r.pop("window"), "缺 window"),
    ],
)
def test_loader_is_fail_closed(tmp_path: Path, mutate, hit: str) -> None:
    """口径读错一个字, 全部历史结论都错且不报错 —— 所以宁可起不来。

    特别是 `entry: signal_day_close` 与 `censored_policy: fill_zero`: 这两个值分别
    对应「用信号日收盘价入场」(泄漏) 与「截尾填 0」(违红线 3), 它们必须被 loader 挡住,
    不能靠人记得别写。
    """
    p = _write_cfg(tmp_path, mutate)
    with pytest.raises(ValueError, match=hit):
        oc.load_window(p)


def test_adding_a_longer_horizon_must_not_shift_the_baseline(tmp_path: Path) -> None:
    """往 horizons 里加一个更长的 H, 基线 H 必须纹丝不动。

    原实现是 `horizons[len//2]` 位置取中位: (5,10,20) 给 10, 加个 60 变成 (5,10,20,60) 就给 20 ——
    **基线 H 一变全部历史结论跟着变且不报错**。加一个可选窗口是扩展, 换基线是口径变更, 两件事。
    """
    raw = yaml.safe_load(oc._CONFIG.read_text(encoding="utf-8"))
    raw["horizons"] = sorted(set(raw["horizons"]) | {60})
    p = tmp_path / "casebook.yaml"
    p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert oc.load_window(p).baseline_horizon == oc.load_window().baseline_horizon


def test_baseline_horizon_must_be_declared_and_in_horizons(tmp_path: Path) -> None:
    for bad in (None, 7, "10"):
        raw = yaml.safe_load(oc._CONFIG.read_text(encoding="utf-8"))
        if bad is None:
            raw.pop("baseline_horizon")
        else:
            raw["baseline_horizon"] = bad
        p = tmp_path / "cb.yaml"
        p.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValueError, match="baseline_horizon"):
            oc.load_window(p)

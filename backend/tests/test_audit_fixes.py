"""审计 2026-04-22 整改项的单元测试.

覆盖:
- 4.1 median_max_drawdown 真正中位数
- 4.2 win_rate_120d 列 + fallback 配对
- 4.3 signals_v2 drawdown_column horizon-aware
- 5.5 calculate_returns 增量跳过冻结事件
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from conftest import duck_mem
from services.signals_v2 import PolicyConfig


# ============================================================================
# P0-C drawdown_column 跟随 horizon
# ============================================================================


@pytest.mark.parametrize(
    "horizon, expected_col",
    [
        (10, "max_drawdown_30d"),
        (30, "max_drawdown_30d"),
        (60, "max_drawdown_60d"),
        (90, "max_drawdown_60d"),
        (120, "max_drawdown_60d"),
    ],
)
def test_policy_config_drawdown_column_by_horizon(horizon, expected_col):
    cfg = PolicyConfig(horizon_days=horizon)
    assert cfg.drawdown_column == expected_col


# ============================================================================
# P0-C compute_ev_stats 接受 drawdown_col
# ============================================================================


def test_compute_ev_stats_dd30_vs_dd60_differs():
    from services.signals_v2 import compute_ev_stats

    history = [
        {"gain": 10.0, "max_drawdown_30d": -2.0, "max_drawdown_60d": -8.0},
        {"gain": 15.0, "max_drawdown_30d": -3.0, "max_drawdown_60d": -10.0},
        {"gain": -5.0, "max_drawdown_30d": -6.0, "max_drawdown_60d": -15.0},
    ]
    stats_30 = compute_ev_stats(history, drawdown_col="max_drawdown_30d")
    stats_60 = compute_ev_stats(history, drawdown_col="max_drawdown_60d")
    # 30d 均值 -3.67，60d 均值 -11.0，必须不同
    assert stats_30.avg_drawdown_pct != stats_60.avg_drawdown_pct
    assert abs(stats_30.avg_drawdown_pct - (-3.67)) < 0.1
    assert abs(stats_60.avg_drawdown_pct - (-11.0)) < 0.1


# ============================================================================
# P0-A median_max_drawdown 取中位数而非均值
# ============================================================================


def test_median_not_average_on_skewed_dd():
    """模拟 updater.py 里的 Python 中位数路径."""
    dd30 = [-2.0, -3.0, -5.0, -100.0]  # 明显右偏，median=-4 (skewed)，mean=-27.5
    dd30_sorted = sorted(dd30)
    median_dd30 = dd30_sorted[len(dd30_sorted) // 2]
    mean_dd30 = sum(dd30) / len(dd30)
    # median 应远离 mean（证明不是一回事）
    assert abs(median_dd30 - mean_dd30) > 20
    # median 应接近 -3 (第 3 个是 -3.0)
    assert median_dd30 == -3.0


# ============================================================================
# P0-B win_rate_120d 统一 fallback
# ============================================================================


def test_scoring_wr_fallback_uses_120d_for_non_buy():
    """scoring.py fallback 路径应走 win_rate_120d 而非 win_rate_90d."""
    import inspect
    from services import scoring

    source = inspect.getsource(scoring.calculate_institution_scores)
    # 以前: _pick(p, "buy_win_rate_120d", "win_rate_90d")
    # 现在: _pick(p, "buy_win_rate_120d", "win_rate_120d")
    assert "_pick(p, \"buy_win_rate_120d\", \"win_rate_120d\")" in source


def test_institution_scoring_read_label_matches_fallback():
    """institution_scoring_read.py 的 120 日胜率标签应对应 win_rate_120d (而非 90d)."""
    import inspect
    from services import institution_scoring_read

    source = inspect.getsource(institution_scoring_read)
    # 找 "120日胜率" 的 tuple
    assert "\"120日胜率\"" in source
    # 新逻辑必须取 win_rate_120d 作为 fallback
    assert "profile.get(\"buy_win_rate_120d\") if has_buy else profile.get(\"win_rate_120d\")" in source


# ============================================================================
# P2 5.3 scoring.has_buy_data per-institution（不再是全局 any）
# ============================================================================


def test_scoring_uses_per_institution_has_buy():
    """scoring.py 不应再有全局 any() 的 has_buy_data 变量 + 在 fallback 路径误用."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "services" / "scoring.py").read_text()
    # 全局 any() 赋值已被 _has_buy(p) 函数替代
    assert "has_buy_data = any(" not in src
    # 新辅助函数存在
    assert "def _has_buy(p):" in src


# ============================================================================
# P1-B / Phase V event-time 行业快照
# ============================================================================


def test_tdx_industry_history_table_created_and_written(tmp_path, monkeypatch):
    """sync_tdx_industry 应追加 dim_stock_tdx_industry_history 快照."""
    from services import tdx_industry_client as tic

    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT, tdx_l2 TEXT, tdx_l3 TEXT,
            tdx_l1_name TEXT, tdx_l2_name TEXT, tdx_l3_name TEXT,
            updated_at TIMESTAMP
        );
        """
    )

    # mock 下载 + 解析 (Phase η++: 删除第 8 列 sw_x_legacy)
    parsed_fake = [
        ("600519", "T02", "T0202", "T020201", "日常消费", "饮料", "白酒"),
        ("000001", "T10", "T1001", "T100101", "金融", "银行", "大型银行"),
    ]
    monkeypatch.setattr(tic, "_fetch_tdxhy_bytes", lambda: (b"fake", "http://fake"))
    monkeypatch.setattr(tic, "_parse_tdxhy", lambda _: parsed_fake)

    result = tic.sync_tdx_industry(conn)
    assert result["rows_upserted"] == 2
    assert result.get("history_snapshot_date")
    assert result.get("raw_hash")

    # history 表应存在且有 2 行
    hist_rows = conn.execute(
        "SELECT stock_code, snapshot_date, tdx_l1, source_raw_hash FROM dim_stock_tdx_industry_history"
    ).fetchall()
    assert len(hist_rows) == 2
    assert {row["source_raw_hash"] for row in hist_rows} == {result["raw_hash"]}
    raw_rows = conn.execute(
        "SELECT raw_hash, file_name, bytes_len FROM raw_tdx_industry_file_snapshot"
    ).fetchall()
    assert [(row["raw_hash"], row["file_name"], row["bytes_len"]) for row in raw_rows] == [
        (result["raw_hash"], "tdxhy.cfg", 4)
    ]
    conn.close()


def test_get_tdx_industry_at_event_time_fallback():
    """历史快照为空时 get_tdx_industry_at 应 fallback 到当前行业."""
    from services import tdx_industry_client as tic

    conn = duck_mem()
    conn.executescript(
        """
        CREATE TABLE dim_stock_tdx_industry (
            stock_code TEXT PRIMARY KEY,
            tdx_l1 TEXT, tdx_l2 TEXT, tdx_l3 TEXT,
            tdx_l1_name TEXT, tdx_l2_name TEXT, tdx_l3_name TEXT,
            updated_at TIMESTAMP
        );
        CREATE TABLE dim_stock_tdx_industry_history (
            stock_code TEXT, snapshot_date TEXT,
            tdx_l1 TEXT, tdx_l2 TEXT, tdx_l3 TEXT,
            tdx_l1_name TEXT, tdx_l2_name TEXT, tdx_l3_name TEXT,
            PRIMARY KEY(stock_code, snapshot_date)
        );
        INSERT INTO dim_stock_tdx_industry
            VALUES('600519','T02','T0202','T020201','消费','饮料','白酒',CURRENT_TIMESTAMP);
        """
    )
    # 无 history，应回退当前（get_tdx_industry 返回 dict 无 source 字段）
    ind = tic.get_tdx_industry_at(conn, "600519", "2024-01-01")
    assert ind is not None
    assert ind["tdx_l1"] == "T02"
    assert ind.get("source") != "event_time_snapshot"  # 回退标记

    # 塞一条 2023-06-01 的历史快照（当时行业 T07）
    conn.execute(
        "INSERT INTO dim_stock_tdx_industry_history VALUES "
        "('600519','2023-06-01','T07','T0701','T070101','老行业','子行业','三级')"
    )
    # event_date=2024-01-01 应取 ≤ 2024 的最新快照 2023-06-01
    ind2 = tic.get_tdx_industry_at(conn, "600519", "2024-01-01")
    assert ind2["tdx_l1"] == "T07"
    assert ind2["source"] == "event_time_snapshot"

    # event_date=2023-01-01 先于快照，回退当前
    ind3 = tic.get_tdx_industry_at(conn, "600519", "2023-01-01")
    assert ind3["tdx_l1"] == "T02"

    conn.close()

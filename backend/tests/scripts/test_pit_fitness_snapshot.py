"""PIT 防回退 — fitness 分桶 + buy_signal picture JOIN 不得注入未来快照.

体检 2026-06-11 HIGH (inst_path_a 同款 latest-snapshot):
  - rebuild_stage_formula_fitness.py 旧版用 MAX(snapshot_date) 给全历史聚合分桶
  - build_stock_formula_buy_signal_daily.py 旧版历史 --date 也贴 MAX 快照

红→绿断言: 历史 signal_date / eval_end_date 只能看到 <= 该日的 as-of 画像快照.
"""
from __future__ import annotations


def _setup_picture(conn):
    """3 张快照: 某股 fundamental_stage 随时间变化, 制造 as-of vs latest 分歧."""
    conn.execute(
        """
        CREATE TABLE mart_stock_picture_daily (
            stock_code TEXT,
            snapshot_date TEXT,
            fundamental_stage TEXT,
            stock_archetype TEXT,
            primary_type TEXT
        )
        """
    )
    conn.executemany(
        """INSERT INTO mart_stock_picture_daily
           (stock_code, snapshot_date, fundamental_stage, stock_archetype, primary_type)
           VALUES (?, ?, ?, ?, ?)""",
        [
            # 600000: 历史是 周期复苏, 最新(未来)快照变成 已充分演绎
            ("600000", "2026-01-31", "周期复苏", "archA", "typeA"),
            ("600000", "2026-05-12", "周期复苏", "archA", "typeA"),
            ("600000", "2026-06-04", "已充分演绎", "archZ", "typeZ"),  # 未来快照 — 不许泄漏
        ],
    )


# ---------------------------------------------------------------------------
# build_stock_formula_buy_signal_daily.load_today_rows — picture as-of
# ---------------------------------------------------------------------------

def test_buy_signal_historical_date_uses_asof_not_latest_snapshot():
    """历史 signal_date 必须拿 <= 该日的 as-of 画像, 不是 MAX(snapshot_date) 未来快照."""
    from scripts.build_stock_formula_buy_signal_daily import load_today_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        _setup_picture(conn)
        conn.execute(
            """CREATE TABLE fact_technical_trigger (
                   date TEXT, stock_code TEXT, formula_id TEXT, formula_variant TEXT)"""
        )
        conn.execute(
            """CREATE TABLE fact_signal_context (
                   stock_code TEXT, date TEXT, vol_r20 REAL, amt_r20 REAL,
                   price_pos_60d REAL, technical_stage TEXT)"""
        )
        conn.execute(
            """CREATE TABLE mart_stock_survey_features (
                   stock_code TEXT, as_of_date TEXT, survey_bin TEXT, survey_count_60d INTEGER)"""
        )
        conn.execute(
            """CREATE TABLE mart_per_stock_strategy_optimal (
                   stock_code TEXT, formula_id TEXT, formula_variant TEXT,
                   optimal_hp INTEGER, optimal_stop_pct REAL, optimal_target_pct REAL,
                   optimal_trailing_pct REAL, avg_ret REAL, avg_max_dd REAL,
                   sharpe REAL, win_rate REAL, n_traded INTEGER)"""
        )

        signal_date = "2026-05-12"
        conn.execute(
            """INSERT INTO fact_technical_trigger (date, stock_code, formula_id, formula_variant)
               VALUES (?, ?, ?, ?)""",
            [signal_date, "600000", "macd_golden_cross", "macd_golden_cross"],
        )
        conn.execute(
            """INSERT INTO fact_signal_context
               (stock_code, date, vol_r20, amt_r20, price_pos_60d, technical_stage)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ["600000", signal_date, 1.0, 1.0, 0.9, "2"],
        )

        rows = load_today_rows(conn, signal_date)
        assert len(rows) == 1
        # index 7 = fundamental_stage, 8 = stock_archetype, 9 = primary_type
        fund, arch, ptype = rows[0][7], rows[0][8], rows[0][9]
        assert fund == "周期复苏", f"as-of 应取 2026-05-12 的 周期复苏, 实得 {fund} (未来快照泄漏!)"
        assert arch == "archA", f"archetype 未来快照泄漏: {arch}"
        assert ptype == "typeA", f"primary_type 未来快照泄漏: {ptype}"
    finally:
        conn.close()


def test_buy_signal_date_before_any_snapshot_marks_unknown_not_future():
    """signal_date 早于所有快照 → fund/archetype 标 unknown(None), 绝不注入未来快照."""
    from scripts.build_stock_formula_buy_signal_daily import load_today_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        _setup_picture(conn)
        conn.execute(
            """CREATE TABLE fact_technical_trigger (
                   date TEXT, stock_code TEXT, formula_id TEXT, formula_variant TEXT)"""
        )
        conn.execute(
            """CREATE TABLE fact_signal_context (
                   stock_code TEXT, date TEXT, vol_r20 REAL, amt_r20 REAL,
                   price_pos_60d REAL, technical_stage TEXT)"""
        )
        conn.execute(
            """CREATE TABLE mart_stock_survey_features (
                   stock_code TEXT, as_of_date TEXT, survey_bin TEXT, survey_count_60d INTEGER)"""
        )
        conn.execute(
            """CREATE TABLE mart_per_stock_strategy_optimal (
                   stock_code TEXT, formula_id TEXT, formula_variant TEXT,
                   optimal_hp INTEGER, optimal_stop_pct REAL, optimal_target_pct REAL,
                   optimal_trailing_pct REAL, avg_ret REAL, avg_max_dd REAL,
                   sharpe REAL, win_rate REAL, n_traded INTEGER)"""
        )

        # 早于任何快照 (最早快照 2026-01-31)
        signal_date = "2025-12-01"
        conn.execute(
            """INSERT INTO fact_technical_trigger (date, stock_code, formula_id, formula_variant)
               VALUES (?, ?, ?, ?)""",
            [signal_date, "600000", "macd_golden_cross", "macd_golden_cross"],
        )

        rows = load_today_rows(conn, signal_date)
        assert len(rows) == 1
        fund, arch, ptype = rows[0][7], rows[0][8], rows[0][9]
        assert fund is None and arch is None and ptype is None, (
            f"该日之前无快照应为 unknown, 实得 fund={fund} arch={arch} ptype={ptype} (未来泄漏!)"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rebuild_stage_formula_fitness — fitness 分桶 as-of <= eval_end_date
# ---------------------------------------------------------------------------

def test_fitness_buckets_by_asof_eval_end_not_latest_snapshot():
    """fitness 分桶用 <= eval_end_date 的 as-of fund_stage, 不是 MAX(snapshot_date)."""
    from scripts.rebuild_stage_formula_fitness import FITNESS_AGG_SQL
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        _setup_picture(conn)
        conn.execute(
            """CREATE TABLE mart_stock_formula_optuna_v2 (
                   stock_code TEXT, formula_id TEXT, formula_variant TEXT,
                   holding_days INTEGER, stage_bin TEXT, n_signals INTEGER,
                   win_rate REAL, avg_ret REAL, avg_max_dd REAL, sharpe REAL,
                   eval_start_date TEXT, eval_end_date TEXT)"""
        )
        # eval window 结束于 2026-05-12 → 应取 周期复苏 (as-of), 不是 已充分演绎 (未来 06-04)
        conn.execute(
            """INSERT INTO mart_stock_formula_optuna_v2
               (stock_code, formula_id, formula_variant, holding_days, stage_bin,
                n_signals, win_rate, avg_ret, avg_max_dd, sharpe,
                eval_start_date, eval_end_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["600000", "macd_golden_cross", "macd_golden_cross", 20, "2",
             10, 0.6, 0.05, -0.08, 1.2, "2024-01-01", "2026-05-12"],
        )

        rows = conn.execute(FITNESS_AGG_SQL).fetchall()
        assert len(rows) == 1
        fund_stage = rows[0][0]  # SELECT 第 1 列 = fund_stage
        assert fund_stage == "周期复苏", (
            f"fitness 应按 as-of(<=2026-05-12) 的 周期复苏 分桶, 实得 {fund_stage} (未来快照泄漏!)"
        )
    finally:
        conn.close()


def test_fitness_ddl_has_walk_forward_mode_marker():
    """fitness 表显式标 walk_forward_mode='none' (window-level 归因, 非 per-signal OOS)."""
    from scripts.rebuild_stage_formula_fitness import DDL
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(DDL)
        cols = {r[1] for r in conn.execute("PRAGMA table_info('mart_stage_formula_fitness')").fetchall()}
        assert "walk_forward_mode" in cols, "DDL 必须含 walk_forward_mode PIT marker 列"
        # 默认值应为 'none'
        conn.execute(
            """INSERT INTO mart_stage_formula_fitness
               (fundamental_stage, technical_stage, formula_id, formula_variant, holding_days, n_signals)
               VALUES ('中性', '2', 'f', 'f', 20, 30)"""
        )
        wfm = conn.execute(
            "SELECT walk_forward_mode FROM mart_stage_formula_fitness LIMIT 1"
        ).fetchone()[0]
        assert wfm == "none", f"walk_forward_mode 默认应为 'none', 实得 {wfm}"
    finally:
        conn.close()

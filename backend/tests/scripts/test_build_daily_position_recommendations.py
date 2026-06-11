from __future__ import annotations


def test_daily_position_recommendation_ddl_preserves_existing_history():
    """DDL must not drop historical recommendation dates before a new date refresh."""
    from scripts.build_daily_position_recommendations import DDL
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(DDL)
        conn.execute(
            """
            INSERT INTO mart_daily_position_recommendation
              (signal_date, buy_date, profile_id, rank_in_profile, stock_code,
               formula_id, formula_variant)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "2026-05-19",
                "2026-05-20",
                "short",
                1,
                "600519",
                "macd_golden_cross",
                "macd_golden_cross",
            ],
        )

        conn.executescript(DDL)

        rows = conn.execute(
            """
            SELECT signal_date, buy_date, profile_id, stock_code, formula_id, formula_variant
              FROM mart_daily_position_recommendation
            """
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "2026-05-19",
                "2026-05-20",
                "short",
                "600519",
                "macd_golden_cross",
                "macd_golden_cross",
            )
        ]
    finally:
        conn.close()


def test_pit_diagnostic_rows_include_governance_reason():
    """Missing PIT rows should surface the latest governance rejection reason."""
    from scripts.build_daily_position_recommendations import DDL, _build_pit_diagnostic_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(DDL)
        conn.execute(
            """
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                stage_filter TEXT,
                cutoff_date TEXT,
                oos_n_traded INTEGER,
                n_traded INTEGER,
                oos_avg_ret DOUBLE,
                avg_ret DOUBLE,
                optimal_stop_pct DOUBLE,
                oos_sharpe DOUBLE,
                sharpe DOUBLE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE fact_optuna_governance_log (
                run_id TEXT,
                rejected_at TIMESTAMP,
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                stage_filter TEXT,
                reason TEXT,
                record_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO fact_optuna_governance_log
              (run_id, rejected_at, stock_code, formula_id, formula_variant,
               stage_filter, reason, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-1",
                "2026-05-15 19:43:34",
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                "1.5",
                "oos_n_traded=1 < min_test_signals=3 (OOS 样本太少, 不可信)",
                None,
            ],
        )
        conn.execute(
            """
            INSERT INTO fact_optuna_governance_log
              (run_id, rejected_at, stock_code, formula_id, formula_variant,
               stage_filter, reason, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "run-2",
                "2026-05-16 09:10:11",
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                "1.5",
                "oos_win_rate=1.00 超出现实区间 [0, 0.95]",
                None,
            ],
        )

        all_rows = [
            (
                "2026-05-29",
                "2026-05-30",
                "short",
                1,
                "605580",
                "turtle_breakout_55",
                "turtle_breakout_55",
                None,
                None,
                None,
                "1.5",
                None,
                "cross_stage_fallback",
            )
        ]

        rows = _build_pit_diagnostic_rows(conn, "2026-05-29", all_rows)
        assert len(rows) == 1

        row = rows[0]
        assert row[12] == "stock_missing_pit"
        assert row[13] == 2
        assert row[14] == "oos_win_rate=1.00 超出现实区间 [0, 0.95]"
        assert str(row[15]).startswith("2026-05-16 09:10:11")
    finally:
        conn.close()


def test_pit_diagnostic_columns_are_added_to_existing_table():
    """Migration helper must backfill governance columns onto legacy diagnostic tables."""
    from scripts.build_daily_position_recommendations import _ensure_pit_diagnostic_columns
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE mart_daily_position_recommendation_pit_diagnostic (
                signal_date TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                rank_in_profile INTEGER NOT NULL,
                stock_code TEXT NOT NULL,
                formula_id TEXT,
                formula_variant TEXT,
                stage_bin TEXT,
                match_tier TEXT,
                pit_exact_stage_rows INTEGER NOT NULL DEFAULT 0,
                pit_same_formula_rows INTEGER NOT NULL DEFAULT 0,
                pit_same_stock_rows INTEGER NOT NULL DEFAULT 0,
                latest_pit_cutoff_date TEXT,
                missing_reason TEXT NOT NULL,
                built_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (signal_date, profile_id, stock_code, formula_id, formula_variant)
            )
            """
        )

        _ensure_pit_diagnostic_columns(conn)

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info('mart_daily_position_recommendation_pit_diagnostic')"
            ).fetchall()
        }
        assert {"governance_reject_count", "governance_latest_reason", "governance_latest_rejected_at"}.issubset(columns)
    finally:
        conn.close()


def test_load_candidate_rows_includes_macd_state_history_and_stage_formula_fallback():
    """Recommendation candidate pool must include MACD state history and PIT stage-fallback rows.

    PIT 红线 (CLAUDE.md §5): 候选只来自 mart_per_stock_stage_strategy_optimal_pit (有 oos_*).
      - 000001: PIT exact stage   -> stage_pit
      - 000002: MACD holding + PIT same stock+formula (不同 stage) -> stage_pit_formula_fallback (= cross-stage)
      - 000003: PIT formula fallback -> stage_pit_formula_fallback
    legacy mart_per_stock_strategy_optimal (无 oos_*, 全期 in-sample) 永不进候选 (见
    test_legacy_in_sample_table_never_enters_candidates).
    """
    from scripts.build_daily_position_recommendations import _load_candidate_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE fact_technical_trigger (
                date TEXT,
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                strength REAL,
                state TEXT
            );
            CREATE TABLE mart_macd_state_history (
                date TEXT,
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                strength REAL,
                state TEXT
            );
            CREATE TABLE fact_signal_context (
                stock_code TEXT,
                date TEXT,
                vol_r20 REAL,
                amt_r20 REAL,
                price_pos_60d REAL,
                technical_stage TEXT
            );
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                stage_filter TEXT,
                cutoff_date TEXT,
                oos_n_traded INTEGER,
                n_traded INTEGER,
                oos_win_rate REAL,
                win_rate REAL,
                oos_avg_ret REAL,
                avg_ret REAL,
                optimal_stop_pct REAL,
                optimal_target_pct REAL,
                optimal_trailing_pct REAL,
                oos_sharpe REAL,
                sharpe REAL,
                holding_days INTEGER
            );
            CREATE TABLE mart_per_stock_strategy_optimal (
                stock_code TEXT,
                formula_id TEXT,
                formula_variant TEXT,
                optimal_hp INTEGER,
                n_traded INTEGER,
                win_rate REAL,
                avg_ret REAL,
                avg_max_dd REAL,
                sharpe REAL,
                calmar REAL,
                optimal_stop_pct REAL,
                optimal_target_pct REAL,
                optimal_trailing_pct REAL
            );
            CREATE TABLE mart_stock_picture_daily (
                stock_code TEXT,
                snapshot_date TEXT,
                fundamental_stage TEXT,
                latest_close REAL
            );
            CREATE TABLE mart_stock_survey_features (
                stock_code TEXT,
                as_of_date TEXT,
                survey_bin TEXT,
                survey_count_60d INTEGER
            );
            """
        )
        signal_date = "2026-05-29"
        conn.executemany(
            """
            INSERT INTO fact_signal_context
              (stock_code, date, vol_r20, amt_r20, price_pos_60d, technical_stage)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("000001", signal_date, 1.0, 1.0, 0.90, "1"),
                ("000002", signal_date, 1.0, 1.0, 0.90, "3"),
                ("000003", signal_date, 1.0, 1.0, 0.90, "2"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO mart_stock_picture_daily
              (stock_code, snapshot_date, fundamental_stage, latest_close)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("000001", signal_date, "正常", 10.0),
                ("000002", signal_date, "正常", 20.0),
                ("000003", signal_date, "正常", 30.0),
            ],
        )
        conn.execute(
            """
            INSERT INTO fact_technical_trigger
              (date, stock_code, formula_id, formula_variant, strength, state)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [signal_date, "000001", "macd_golden_cross", "macd_golden_cross", 1.0, "just_crossed"],
        )
        conn.execute(
            """
            INSERT INTO fact_technical_trigger
              (date, stock_code, formula_id, formula_variant, strength, state)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [signal_date, "000003", "macd_golden_cross", "macd_golden_cross", 1.0, None],
        )
        conn.execute(
            """
            INSERT INTO mart_macd_state_history
              (date, stock_code, formula_id, formula_variant, strength, state)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [signal_date, "000002", "reversal_1m_deep", "reversal_1m_deep", 1.0, "holding"],
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal_pit
              (stock_code, formula_id, formula_variant, stage_filter, cutoff_date,
               oos_n_traded, n_traded, oos_win_rate, win_rate, oos_avg_ret, avg_ret,
               optimal_stop_pct, optimal_target_pct, optimal_trailing_pct, oos_sharpe, sharpe,
               holding_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["000001", "macd_golden_cross", "macd_golden_cross", "1", signal_date,
             8, 8, 0.62, 0.62, 0.08, 0.08, -0.08, 0.16, 0.05, 1.4, 1.4, 20],
        )
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal_pit
              (stock_code, formula_id, formula_variant, stage_filter, cutoff_date,
               oos_n_traded, n_traded, oos_win_rate, win_rate, oos_avg_ret, avg_ret,
               optimal_stop_pct, optimal_target_pct, optimal_trailing_pct, oos_sharpe, sharpe,
               holding_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["000003", "macd_golden_cross", "macd_golden_cross", "9", signal_date,
             8, 8, 0.60, 0.60, 0.07, 0.07, -0.07, 0.15, 0.04, 1.3, 1.3, 20],
        )
        # 000002: MACD holding 信号 stage='3'，PIT 行 stage_filter='7' (不同 stage)
        # -> 经 stage_pit_formula CTE 走 cross-stage PIT fallback (有 oos_*，干净)
        conn.execute(
            """
            INSERT INTO mart_per_stock_stage_strategy_optimal_pit
              (stock_code, formula_id, formula_variant, stage_filter, cutoff_date,
               oos_n_traded, n_traded, oos_win_rate, win_rate, oos_avg_ret, avg_ret,
               optimal_stop_pct, optimal_target_pct, optimal_trailing_pct, oos_sharpe, sharpe,
               holding_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["000002", "reversal_1m_deep", "reversal_1m_deep", "7", signal_date,
             9, 9, 0.64, 0.64, 0.09, 0.09, -0.06, 0.14, 0.03, 1.5, 1.5, 20],
        )
        # legacy in-sample 行 (无 oos_*) 也存在 -> 必须被忽略，不能影响 000002 的指标
        conn.execute(
            """
            INSERT INTO mart_per_stock_strategy_optimal
              (stock_code, formula_id, formula_variant, optimal_hp, n_traded, win_rate,
               avg_ret, avg_max_dd, sharpe, calmar, optimal_stop_pct, optimal_target_pct,
               optimal_trailing_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ["000002", "reversal_1m_deep", "reversal_1m_deep", 20, 99, 0.99, 0.99, -0.01, 9.9, 9.9, -0.06, 0.14, 0.03],
        )

        rows = _load_candidate_rows(conn, signal_date)
        assert len(rows) == 3

        by_key = {
            (row[0], row[1], row[2]): row
            for row in rows
        }
        assert by_key[("000001", "macd_golden_cross", "macd_golden_cross")][14] == "stage_pit"
        # 000002 走 PIT cross-stage fallback (不再是 legacy cross_stage_fallback)
        r002 = by_key[("000002", "reversal_1m_deep", "reversal_1m_deep")]
        assert r002[14] == "stage_pit_formula_fallback"
        # 指标必须来自 PIT oos_* (win=0.64/avg_ret=0.09/sharpe=1.5)，
        # 不能是 legacy in-sample (win=0.99/avg_ret=0.99/sharpe=9.9)
        # tuple idx: 8=n_signals 9=win_rate 10=avg_ret 12=sharpe 13=calmar
        assert round(r002[9], 4) == 0.64
        assert round(r002[10], 4) == 0.09
        assert round(r002[12], 4) == 1.5
        assert by_key[("000003", "macd_golden_cross", "macd_golden_cross")][14] == "stage_pit_formula_fallback"
        assert {row[22] for row in rows} == {"just_crossed", "holding"}
    finally:
        conn.close()


def test_legacy_in_sample_table_never_enters_candidates():
    """REGRESSION (PIT 红线, §4.5 反例): 仅有 legacy mart_per_stock_strategy_optimal
    (无 oos_*, 全期 in-sample sharpe) 覆盖的 (stock,formula,variant) 必须被丢弃,
    绝不用 in-sample 值生成推荐。PIT 无覆盖 = score=unknown = 不进候选。"""
    from scripts.build_daily_position_recommendations import _load_candidate_rows
    from services.duck_adapter import connect as duck_connect

    conn = duck_connect(":memory:")
    try:
        conn.executescript(
            """
            CREATE TABLE fact_technical_trigger (
                date TEXT, stock_code TEXT, formula_id TEXT, formula_variant TEXT,
                strength REAL, state TEXT
            );
            CREATE TABLE mart_macd_state_history (
                date TEXT, stock_code TEXT, formula_id TEXT, formula_variant TEXT,
                strength REAL, state TEXT
            );
            CREATE TABLE fact_signal_context (
                stock_code TEXT, date TEXT, vol_r20 REAL, amt_r20 REAL,
                price_pos_60d REAL, technical_stage TEXT
            );
            CREATE TABLE mart_per_stock_stage_strategy_optimal_pit (
                stock_code TEXT, formula_id TEXT, formula_variant TEXT, stage_filter TEXT,
                cutoff_date TEXT, oos_n_traded INTEGER, n_traded INTEGER,
                oos_win_rate REAL, win_rate REAL, oos_avg_ret REAL, avg_ret REAL,
                optimal_stop_pct REAL, optimal_target_pct REAL, optimal_trailing_pct REAL,
                oos_sharpe REAL, sharpe REAL, holding_days INTEGER
            );
            CREATE TABLE mart_per_stock_strategy_optimal (
                stock_code TEXT, formula_id TEXT, formula_variant TEXT, optimal_hp INTEGER,
                n_traded INTEGER, win_rate REAL, avg_ret REAL, avg_max_dd REAL,
                sharpe REAL, calmar REAL, optimal_stop_pct REAL, optimal_target_pct REAL,
                optimal_trailing_pct REAL
            );
            CREATE TABLE mart_stock_picture_daily (
                stock_code TEXT, snapshot_date TEXT, fundamental_stage TEXT, latest_close REAL
            );
            CREATE TABLE mart_stock_survey_features (
                stock_code TEXT, as_of_date TEXT, survey_bin TEXT, survey_count_60d INTEGER
            );
            """
        )
        signal_date = "2026-05-29"
        conn.execute(
            "INSERT INTO fact_signal_context VALUES (?, ?, ?, ?, ?, ?)",
            ["000900", signal_date, 1.0, 1.0, 0.90, "1"],
        )
        conn.execute(
            "INSERT INTO mart_stock_picture_daily VALUES (?, ?, ?, ?)",
            ["000900", signal_date, "正常", 12.0],
        )
        conn.execute(
            """INSERT INTO fact_technical_trigger
               (date, stock_code, formula_id, formula_variant, strength, state)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [signal_date, "000900", "macd_golden_cross", "macd_golden_cross", 1.0, "just_crossed"],
        )
        # 仅 legacy in-sample 行 (高 sharpe/calmar 诱人，但是 in-sample = leakage)，无任何 PIT 行
        conn.execute(
            """INSERT INTO mart_per_stock_strategy_optimal
               (stock_code, formula_id, formula_variant, optimal_hp, n_traded, win_rate,
                avg_ret, avg_max_dd, sharpe, calmar, optimal_stop_pct, optimal_target_pct,
                optimal_trailing_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["000900", "macd_golden_cross", "macd_golden_cross", 20, 50, 0.90, 0.20, -0.03, 4.5, 6.0, -0.05, 0.12, 0.02],
        )

        rows = _load_candidate_rows(conn, signal_date)
        # PIT 无覆盖 -> 该股不进候选，绝不用 in-sample sharpe=4.5 强推
        assert rows == []
    finally:
        conn.close()


def test_candidate_sql_does_not_join_legacy_in_sample_table():
    """STATIC GUARD: _load_candidate_rows 的 SQL 不得 JOIN/排序 legacy in-sample 表。
    防止有人重新引入 mart_per_stock_strategy_optimal (无 oos_*) 到 fallback 链。"""
    import inspect
    from scripts.build_daily_position_recommendations import _load_candidate_rows

    src = inspect.getsource(_load_candidate_rows)
    # 候选 SQL 部分 (从 WITH 到 fetchall 之前)
    sql_raw = src[src.index("WITH today_signals"):src.index(".fetchall()")]
    # 剔除 -- 注释行 (注释里会出现表名作为警告，不算实际 JOIN)
    sql = "\n".join(
        line.split("--", 1)[0] for line in sql_raw.splitlines()
    )
    # legacy in-sample 表绝不能出现 (它没有 oos_* 列)
    assert "mart_per_stock_strategy_optimal" not in sql, (
        "legacy in-sample 表 mart_per_stock_strategy_optimal 不得进推荐候选 SQL"
    )
    # 候选只能来自 PIT 表
    assert "mart_per_stock_stage_strategy_optimal_pit" in sql
    # 不得引用裸 legacy 别名 opt.* (sopt./sfopt. 是 PIT 别名，用 \b 词边界避免误伤)
    import re as _re
    assert not _re.search(r"\bopt\.sharpe\b", sql)
    assert not _re.search(r"\bopt\.avg_ret\b", sql)
    assert not _re.search(r"\bopt\.win_rate\b", sql)
    assert not _re.search(r"\bopt\.n_traded\b", sql)
    assert not _re.search(r"\bopt\.calmar\b", sql)

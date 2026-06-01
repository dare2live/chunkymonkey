"""Tests for build_formula_signals_history + build_stage_formula_fitness scripts."""
from __future__ import annotations

import numpy as np
import pytest


class TestEnsureFormulaTables:
    """DDL helpers in services/formula_engine/ddl.py."""

    def test_ensure_creates_four_tables(self):
        import duckdb
        from services.formula_engine.ddl import ensure_formula_tables

        from services.duck_adapter import connect as duck_connect
        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
            names = {t[0] for t in tables}
            assert "fact_technical_trigger" in names
            assert "mart_formula_horizon_evidence" in names
            assert "fact_stock_technical_stage" in names
            assert "mart_stage_formula_fitness" in names
        finally:
            conn.close()

    def test_ensure_is_idempotent(self):
        import duckdb
        from services.formula_engine.ddl import ensure_formula_tables

        from services.duck_adapter import connect as duck_connect
        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            ensure_formula_tables(conn)  # 第二次不报错
            ensure_formula_tables(conn)  # 第三次不报错
            n = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='fact_technical_trigger'"
            ).fetchone()[0]
            assert n == 1
        finally:
            conn.close()


class TestBuildFormulaSignalsHistoryHelpers:
    """build_formula_signals_history.py 辅助函数测试."""

    def test_bootstrap_registers_all_current_formulas(self):
        from scripts.build_formula_signals_history import REGISTRY

        assert {
            "macd_golden_cross",
            "turtle_breakout_20",
            "turtle_breakout_55",
            "dynamic_ma_iterative_cross",
            "reversal_1m_mild",
            "reversal_1m_deep",
            "reversal_1w",
        }.issubset(REGISTRY.keys())

    def test_load_all_kline_grouped_basic(self):
        """直接构造小 K 线表测试 groupby 逻辑 (用原生 duckdb 因为需要 fetchnumpy)."""
        import duckdb
        from scripts.build_formula_signals_history import load_all_kline_grouped

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("""
                CREATE VIEW v_price_kline_qfq AS
                SELECT * FROM (VALUES
                    ('000001', '2024-01-02', 10.0, 10.5, 9.8, 10.3, 1000.0, 100000.0, 'daily', 'qfq'),
                    ('000001', '2024-01-03', 10.3, 10.6, 10.2, 10.5, 1100.0, 110000.0, 'daily', 'qfq'),
                    ('000001', '2024-01-04', 10.5, 10.7, 10.4, 10.6, 1200.0, 120000.0, 'daily', 'qfq'),
                    ('000002', '2024-01-02', 20.0, 20.5, 19.8, 20.3, 2000.0, 200000.0, 'daily', 'qfq'),
                    ('000002', '2024-01-03', 20.3, 20.6, 20.2, 20.5, 2100.0, 210000.0, 'daily', 'qfq')
                ) t(code, date, open, high, low, close, volume, amount, freq, adjust)
            """)
            result = load_all_kline_grouped(conn, "2024-01-02", "2024-01-04")
            assert set(result.keys()) == {"000001", "000002"}
            assert len(result["000001"]["dates"]) == 3
            assert len(result["000002"]["dates"]) == 2
            # 检查字段
            assert "closes" in result["000001"]
            assert result["000001"]["closes"][0] == pytest.approx(10.3)
        finally:
            conn.close()

    def test_load_all_kline_groupby_preserves_order(self):
        """数据按 (code, date) 排序后, numpy groupby 各股内部仍按 date 升序."""
        import duckdb
        from scripts.build_formula_signals_history import load_all_kline_grouped

        conn = duckdb.connect(":memory:")
        try:
            conn.execute("""
                CREATE VIEW v_price_kline_qfq AS
                SELECT * FROM (VALUES
                    ('000001', '2024-01-03', 11.0, 11.0, 11.0, 11.0, 1000.0, 11000.0, 'daily', 'qfq'),
                    ('000001', '2024-01-02', 10.0, 10.0, 10.0, 10.0, 1000.0, 10000.0, 'daily', 'qfq'),
                    ('000001', '2024-01-04', 12.0, 12.0, 12.0, 12.0, 1000.0, 12000.0, 'daily', 'qfq')
                ) t(code, date, open, high, low, close, volume, amount, freq, adjust)
            """)
            result = load_all_kline_grouped(conn, "2024-01-02", "2024-01-04")
            d = result["000001"]["dates"]
            assert list(d) == ["2024-01-02", "2024-01-03", "2024-01-04"]
            c = result["000001"]["closes"]
            assert list(c) == [10.0, 11.0, 12.0]
        finally:
            conn.close()


class TestComputeAllSignalsSmokeRun:
    """compute_all_signals 集成测试 - 用合成 K 线验证端到端."""

    def test_smoke_macd_on_uptrend(self):
        from scripts.build_formula_signals_history import compute_all_signals
        # 注册公式
        from services.formula_engine import macd_golden_cross  # noqa: F401

        np.random.seed(7)
        n = 200
        trend = np.linspace(100, 150, n)
        noise = 5 * np.sin(np.arange(n) / 8) + np.random.randn(n) * 2
        closes = trend + noise
        dates = np.array([f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)])

        grouped = {
            "TEST": {
                "dates": dates,
                "opens": closes,
                "highs": closes * 1.01,
                "lows": closes * 0.99,
                "closes": closes,
                "volumes": np.ones(n) * 1000,
                "amounts": closes * 1000,
            }
        }
        signals = compute_all_signals(grouped, formula_ids=("macd_golden_cross",))
        assert len(signals) >= 1
        # 所有信号都是 MACD 类
        for s in signals:
            assert s.formula_id == "macd_golden_cross"
            assert s.stock_code == "TEST"


class TestFormulaSignalWindowPlanning:
    """公式信号刷新 CLI 的 compute window / write window 拆分。"""

    def test_filter_signals_to_write_window(self):
        from scripts.build_formula_signals_history import _filter_signals_to_window
        from services.formula_engine.base import FormulaSignal

        signals = [
            FormulaSignal("000001", "2026-05-19", "macd_golden_cross",
                          "macd_golden_cross", 0.5, "old", ()),
            FormulaSignal("000001", "2026-05-20", "macd_golden_cross",
                          "macd_golden_cross", 0.6, "in", ()),
            FormulaSignal("000001", "2026-05-29", "macd_golden_cross",
                          "macd_golden_cross", 0.7, "in", ()),
            FormulaSignal("000001", "2026-06-01", "macd_golden_cross",
                          "macd_golden_cross", 0.8, "future", ()),
        ]

        filtered = _filter_signals_to_window(signals, start="2026-05-20", end="2026-05-29")

        assert [signal.date for signal in filtered] == ["2026-05-20", "2026-05-29"]

    def test_resolve_write_window_rejects_window_before_compute_start(self):
        from scripts.build_formula_signals_history import _resolve_write_window

        with pytest.raises(ValueError, match="compute start"):
            _resolve_write_window("2026-05-20", "2026-05-29", "2026-05-19")

    def test_resolve_write_window_rejects_window_after_end(self):
        from scripts.build_formula_signals_history import _resolve_write_window

        with pytest.raises(ValueError, match="must be <= end"):
            _resolve_write_window("2026-05-01", "2026-05-29", "2026-06-01")


class TestTechnicalStageHistorical:
    """build_stage_formula_fitness 的 technical_stage 历史回算逻辑."""

    def test_classify_pure_function_consistency(self):
        """同一输入,classify 函数必须每次返回相同结果(纯函数)."""
        from services.formula_engine.technical_stage import classify_technical_stage

        np.random.seed(42)
        n = 400
        closes = 10 + np.cumsum(np.random.randn(n) * 0.05)
        volumes = np.abs(np.random.randn(n)) * 1000 + 100
        out1 = classify_technical_stage(closes, volumes)
        out2 = classify_technical_stage(closes, volumes)
        assert list(out1) == list(out2), "classify 必须是纯函数,两次调用结果一致"

    def test_stage_window_replacement_uses_separate_compute_and_write_start(self):
        """stage 增量刷新要用 compute_start 预热,但只替换 write_start 之后。"""
        from datetime import date, timedelta

        import duckdb

        from scripts.build_stage_formula_fitness import build_technical_stage_history
        from services.duck_adapter import connect as duck_connect
        from services.formula_engine.ddl import ensure_formula_tables

        mkt_conn = duckdb.connect(":memory:")
        conn = duck_connect(":memory:")
        try:
            start_day = date(2026, 1, 1)
            rows = []
            for i in range(420):
                day = (start_day + timedelta(days=i)).isoformat()
                close = 10.0 + i * 0.05
                rows.append(("000001", day, close, 1000.0, "daily", "qfq"))
            mkt_conn.execute(
                """
                CREATE TABLE kline (
                    code TEXT, date TEXT, close DOUBLE, volume DOUBLE, freq TEXT, adjust TEXT
                )
                """
            )
            mkt_conn.executemany("INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?)", rows)
            mkt_conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM kline")
            ensure_formula_tables(conn)
            conn.execute(
                """
                INSERT INTO fact_stock_technical_stage (stock_code, date, stage)
                VALUES ('000001', '2026-02-01', 'OLD'), ('000001', '2026-12-20', 'DIRTY')
                """
            )

            written = build_technical_stage_history(
                mkt_conn,
                conn,
                "2026-01-01",
                "2027-02-24",
                write_start="2026-12-20",
            )

            assert written > 0
            preserved = conn.execute(
                "SELECT stage FROM fact_stock_technical_stage WHERE stock_code='000001' AND date='2026-02-01'"
            ).fetchone()[0]
            replaced = conn.execute(
                "SELECT stage FROM fact_stock_technical_stage WHERE stock_code='000001' AND date='2026-12-20'"
            ).fetchone()[0]
            assert preserved == "OLD"
            assert replaced != "DIRTY"
        finally:
            conn.close()
            mkt_conn.close()

    def test_stage_empty_window_refuses_to_delete_existing_rows(self):
        from datetime import date, timedelta

        import duckdb

        from scripts.build_stage_formula_fitness import build_technical_stage_history
        from services.duck_adapter import connect as duck_connect
        from services.formula_engine.ddl import ensure_formula_tables

        mkt_conn = duckdb.connect(":memory:")
        conn = duck_connect(":memory:")
        try:
            start_day = date(2026, 1, 1)
            rows = []
            for i in range(10):
                day = (start_day + timedelta(days=i)).isoformat()
                rows.append(("000001", day, 10.0 + i, 1000.0, "daily", "qfq"))
            mkt_conn.execute(
                """
                CREATE TABLE kline (
                    code TEXT, date TEXT, close DOUBLE, volume DOUBLE, freq TEXT, adjust TEXT
                )
                """
            )
            mkt_conn.executemany("INSERT INTO kline VALUES (?, ?, ?, ?, ?, ?)", rows)
            mkt_conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM kline")
            ensure_formula_tables(conn)
            conn.execute(
                """
                INSERT INTO fact_stock_technical_stage (stock_code, date, stage)
                VALUES ('000001', '2026-01-05', 'KEEP')
                """
            )

            with pytest.raises(RuntimeError, match="produced 0 rows"):
                build_technical_stage_history(
                    mkt_conn,
                    conn,
                    "2026-01-01",
                    "2026-01-10",
                    write_start="2026-01-05",
                )

            kept = conn.execute(
                "SELECT stage FROM fact_stock_technical_stage WHERE stock_code='000001' AND date='2026-01-05'"
            ).fetchone()[0]
            assert kept == "KEEP"
        finally:
            conn.close()
            mkt_conn.close()


class TestSignalContextWindowRefresh:
    """build_signal_context 的 lookback-safe 窗口刷新。"""

    @staticmethod
    def _seed_market(path, *, days: int) -> list[str]:
        from datetime import date, timedelta

        import duckdb

        start_day = date(2026, 1, 1)
        rows = []
        dates = []
        for i in range(days):
            day = (start_day + timedelta(days=i)).isoformat()
            dates.append(day)
            close = 10.0 + i * 0.1
            rows.append((
                "000001",
                day,
                close - 0.1,
                close + 0.2,
                close - 0.3,
                close,
                1000.0 + i,
                100000.0 + i * 100,
                "daily",
                "qfq",
            ))
        conn = duckdb.connect(str(path))
        try:
            conn.execute(
                """
                CREATE TABLE price_kline_tdxhub (
                    code TEXT, date TEXT, open DOUBLE, high DOUBLE, low DOUBLE,
                    close DOUBLE, volume DOUBLE, amount DOUBLE, freq TEXT, adjust TEXT
                )
                """
            )
            conn.executemany("INSERT INTO price_kline_tdxhub VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.execute("CREATE VIEW v_price_kline_qfq AS SELECT * FROM price_kline_tdxhub")
        finally:
            conn.close()
        return dates

    @staticmethod
    def _seed_smart(path, dates: list[str]) -> None:
        from services.duck_adapter import connect as duck_connect
        from services.formula_engine.ddl import ensure_formula_tables
        from services.formula_engine.signal_context_ddl import ensure_signal_context_table

        conn = duck_connect(str(path))
        try:
            ensure_formula_tables(conn)
            ensure_signal_context_table(conn)
            conn.executemany(
                "INSERT INTO fact_stock_technical_stage (stock_code, date, stage) VALUES (?, ?, ?)",
                [("000001", day, "2") for day in dates],
            )
        finally:
            conn.close()

    def test_signal_context_preserves_history_and_replaces_declared_window(self, tmp_path):
        import duckdb

        from scripts.build_signal_context import build_signal_context
        from services.duck_adapter import connect as duck_connect

        market_db = tmp_path / "market.duckdb"
        smart_db = tmp_path / "smart.duckdb"
        dates = self._seed_market(market_db, days=140)
        self._seed_smart(smart_db, dates)
        outside_date = dates[124]
        write_start = dates[125]
        end = dates[134]

        conn = duckdb.connect(str(smart_db))
        try:
            conn.execute(
                """
                INSERT INTO fact_signal_context
                  (stock_code, date, vol_r20, amt_r20, amount_20d_avg,
                   price_pos_60d, price_pos_120d, drawdown_60d, technical_stage)
                VALUES
                  ('000001', ?, 777, 1, 1, 1, 1, 0, 'OLD'),
                  ('000001', ?, 999, 1, 1, 1, 1, 0, 'DIRTY')
                """,
                [outside_date, write_start],
            )
        finally:
            conn.close()

        summary = build_signal_context(
            "2026-01-01",
            end,
            write_start=write_start,
            market_db=market_db,
            smart_db=smart_db,
            connect_smart=lambda: duck_connect(str(smart_db)),
        )

        assert summary["rows"] > 0
        conn = duckdb.connect(str(smart_db), read_only=True)
        try:
            preserved = conn.execute(
                "SELECT vol_r20 FROM fact_signal_context WHERE stock_code='000001' AND date=?",
                [outside_date],
            ).fetchone()[0]
            replaced = conn.execute(
                "SELECT vol_r20, technical_stage FROM fact_signal_context WHERE stock_code='000001' AND date=?",
                [write_start],
            ).fetchone()
            assert preserved == pytest.approx(777.0)
            assert replaced[0] != pytest.approx(999.0)
            assert replaced[1] == "2"
        finally:
            conn.close()

    def test_signal_context_empty_window_refuses_to_delete_existing_rows(self, tmp_path):
        import duckdb

        from scripts.build_signal_context import build_signal_context
        from services.duck_adapter import connect as duck_connect

        market_db = tmp_path / "market.duckdb"
        smart_db = tmp_path / "smart.duckdb"
        dates = self._seed_market(market_db, days=10)
        self._seed_smart(smart_db, dates)
        dirty_date = dates[4]
        conn = duckdb.connect(str(smart_db))
        try:
            conn.execute(
                """
                INSERT INTO fact_signal_context
                  (stock_code, date, vol_r20, amt_r20, amount_20d_avg,
                   price_pos_60d, price_pos_120d, drawdown_60d, technical_stage)
                VALUES ('000001', ?, 999, 1, 1, 1, 1, 0, 'DIRTY')
                """,
                [dirty_date],
            )
        finally:
            conn.close()

        with pytest.raises(RuntimeError, match="produced 0 rows"):
            build_signal_context(
                dates[0],
                dates[-1],
                write_start=dirty_date,
                market_db=market_db,
                smart_db=smart_db,
                connect_smart=lambda: duck_connect(str(smart_db)),
            )

        conn = duckdb.connect(str(smart_db), read_only=True)
        try:
            kept = conn.execute(
                "SELECT technical_stage FROM fact_signal_context WHERE stock_code='000001' AND date=?",
                [dirty_date],
            ).fetchone()[0]
            assert kept == "DIRTY"
        finally:
            conn.close()


class TestFormulaSignalCanGoThroughWriteCycle:
    """完整端到端: 信号 → 写库 → 读出 → 字段保真."""

    def test_round_trip_signal_to_db(self):
        import duckdb
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables

        from services.duck_adapter import connect as duck_connect
        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            sigs = [
                FormulaSignal(
                    stock_code="600519",
                    date="2024-01-15",
                    formula_id="macd_golden_cross",
                    formula_variant="macd_golden_cross",
                    strength=0.75,
                    state="just_crossed",
                    reason_codes=("dif_above_dea:0.5",),
                ),
                FormulaSignal(
                    stock_code="000001",
                    date="2024-01-16",
                    formula_id="macd_golden_cross",
                    formula_variant="macd_golden_cross",
                    strength=0.55,
                    state="just_crossed",
                    reason_codes=("dif_above_zero:0.3",),
                ),
            ]
            n = write_signals_to_db(conn, sigs)
            assert n == 2
            rows = conn.execute(
                "SELECT stock_code, date, formula_id, strength, state, reason_codes_json FROM fact_technical_trigger ORDER BY stock_code"
            ).fetchall()
            assert rows[0][0] == "000001"
            assert rows[1][0] == "600519"
            assert rows[0][3] == pytest.approx(0.55)
            assert rows[1][3] == pytest.approx(0.75)
            # reason_codes_json 是 JSON 数组字符串
            import json
            assert json.loads(rows[1][5]) == ["dif_above_dea:0.5"]
        finally:
            conn.close()

    def test_write_signals_to_db_idempotent_replace(self):
        """同一 formula_id 第二次写入应 DELETE+INSERT (不累加)."""
        import duckdb
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables

        from services.duck_adapter import connect as duck_connect
        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            sigs1 = [
                FormulaSignal("600519", "2024-01-15", "macd_golden_cross",
                              "macd_golden_cross", 0.5, "just_crossed", ()),
                FormulaSignal("600519", "2024-02-15", "macd_golden_cross",
                              "macd_golden_cross", 0.6, "just_crossed", ()),
            ]
            write_signals_to_db(conn, sigs1)
            n1 = conn.execute("SELECT COUNT(*) FROM fact_technical_trigger").fetchone()[0]
            assert n1 == 2

            # 第二次写入 1 条新信号 (同 formula_id),应替换之前所有,不累加
            sigs2 = [
                FormulaSignal("000001", "2024-03-15", "macd_golden_cross",
                              "macd_golden_cross", 0.7, "just_crossed", ()),
            ]
            write_signals_to_db(conn, sigs2)
            n2 = conn.execute("SELECT COUNT(*) FROM fact_technical_trigger").fetchone()[0]
            assert n2 == 1  # 只剩第二次的 1 条
        finally:
            conn.close()

    def test_write_signals_to_db_scoped_replace_preserves_outside_window(self):
        """显式窗口刷新只替换该 formula/date 窗口,不能误删历史或其他公式。"""
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            seed = [
                FormulaSignal("600519", "2024-01-15", "macd_golden_cross",
                              "macd_golden_cross", 0.5, "just_crossed", ()),
                FormulaSignal("600519", "2024-02-15", "macd_golden_cross",
                              "macd_golden_cross", 0.6, "just_crossed", ()),
                FormulaSignal("600519", "2024-03-15", "macd_golden_cross",
                              "macd_golden_cross", 0.7, "just_crossed", ()),
                FormulaSignal("000001", "2024-02-10", "turtle_breakout",
                              "turtle_breakout_20", 0.8, "breakout", ()),
            ]
            write_signals_to_db(conn, seed)

            replacement = [
                FormulaSignal("000002", "2024-02-20", "macd_golden_cross",
                              "macd_golden_cross", 0.9, "just_crossed", ()),
            ]
            write_signals_to_db(
                conn,
                replacement,
                formula_ids=("macd_golden_cross",),
                start="2024-02-01",
                end="2024-02-28",
            )

            rows = [tuple(row) for row in conn.execute(
                """
                SELECT stock_code, date, formula_id
                  FROM fact_technical_trigger
                 ORDER BY formula_id, date, stock_code
                """
            ).fetchall()]
            assert rows == [
                ("600519", "2024-01-15", "macd_golden_cross"),
                ("000002", "2024-02-20", "macd_golden_cross"),
                ("600519", "2024-03-15", "macd_golden_cross"),
                ("000001", "2024-02-10", "turtle_breakout"),
            ]
        finally:
            conn.close()

    def test_write_signals_to_db_scoped_zero_signals_clears_stale_window(self):
        """窗口内本次 0 信号也要删除旧窗口行,避免 stale signal 冒充新鲜。"""
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            seed = [
                FormulaSignal("600519", "2024-01-15", "macd_golden_cross",
                              "macd_golden_cross", 0.5, "just_crossed", ()),
                FormulaSignal("600519", "2024-02-15", "macd_golden_cross",
                              "macd_golden_cross", 0.6, "just_crossed", ()),
            ]
            write_signals_to_db(conn, seed)

            n = write_signals_to_db(
                conn,
                [],
                formula_ids=("macd_golden_cross",),
                start="2024-02-01",
                end="2024-02-28",
            )

            assert n == 0
            rows = [tuple(row) for row in conn.execute(
                "SELECT stock_code, date FROM fact_technical_trigger ORDER BY date"
            ).fetchall()]
            assert rows == [("600519", "2024-01-15")]
        finally:
            conn.close()

    def test_write_signals_to_db_rejects_rows_outside_declared_scope(self):
        """显式窗口刷新必须拒绝公式或日期越界的 signals,避免误写未删除区间。"""
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            out_of_window = [
                FormulaSignal("600519", "2024-03-15", "macd_golden_cross",
                              "macd_golden_cross", 0.5, "just_crossed", ()),
            ]
            with pytest.raises(ValueError, match="outside scoped refresh window"):
                write_signals_to_db(
                    conn,
                    out_of_window,
                    formula_ids=("macd_golden_cross",),
                    start="2024-02-01",
                    end="2024-02-28",
                )

            wrong_formula = [
                FormulaSignal("600519", "2024-02-15", "turtle_breakout",
                              "turtle_breakout_20", 0.5, "breakout", ()),
            ]
            with pytest.raises(ValueError, match="outside delete scope"):
                write_signals_to_db(
                    conn,
                    wrong_formula,
                    formula_ids=("macd_golden_cross",),
                    start="2024-02-01",
                    end="2024-02-28",
                )
        finally:
            conn.close()

    def test_write_signals_rollback_on_executemany_error(self):
        """模拟 SIGPIPE/中断 = DELETE 已执行但 INSERT 抛错; 表必须保持原状 (事务回滚)。

        历史 bug: DuckDB 默认 auto-commit, DELETE 立即生效;
        若 INSERT 阶段挂掉, fact_technical_trigger 留下空表 + 半成品索引,
        下次 DELETE 触发 FATAL Error → macOS 'Python quit unexpectedly'。
        本测试保证 BEGIN/COMMIT 事务回滚生效。
        """
        from unittest.mock import patch
        from scripts.build_formula_signals_history import write_signals_to_db
        from services.formula_engine.base import FormulaSignal
        from services.formula_engine.ddl import ensure_formula_tables
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        try:
            ensure_formula_tables(conn)
            # 预先填入 3 行 (假设上次成功跑过)
            seed = [
                FormulaSignal("600519", "2024-01-15", "macd_golden_cross",
                              "macd_golden_cross", 0.5, "just_crossed", ()),
                FormulaSignal("600519", "2024-02-15", "macd_golden_cross",
                              "macd_golden_cross", 0.6, "just_crossed", ()),
                FormulaSignal("000001", "2024-03-15", "macd_golden_cross",
                              "macd_golden_cross", 0.7, "just_crossed", ()),
            ]
            write_signals_to_db(conn, seed)
            assert conn.execute("SELECT COUNT(*) FROM fact_technical_trigger").fetchone()[0] == 3

            # 第二次写: mock executemany 抛错 (模拟 SIGPIPE / 内存不足 / 索引坏)
            new_sigs = [
                FormulaSignal("000002", "2024-04-15", "macd_golden_cross",
                              "macd_golden_cross", 0.8, "just_crossed", ()),
            ]
            original_executemany = conn.executemany
            def explode(*a, **kw):
                raise RuntimeError("simulated SIGPIPE during INSERT batch")
            with patch.object(conn, "executemany", side_effect=explode):
                try:
                    write_signals_to_db(conn, new_sigs)
                    raise AssertionError("应该抛 RuntimeError")
                except RuntimeError as e:
                    assert "simulated SIGPIPE" in str(e)

            # 关键断言: 原始 3 行必须还在 (ROLLBACK 生效), 不会被 DELETE 吞掉
            n_after = conn.execute("SELECT COUNT(*) FROM fact_technical_trigger").fetchone()[0]
            assert n_after == 3, f"事务未回滚! 实际行数 {n_after}, 应为 3"
            # 而且新 stock 000002 没被插入
            codes = {r[0] for r in conn.execute(
                "SELECT DISTINCT stock_code FROM fact_technical_trigger").fetchall()}
            assert "000002" not in codes
        finally:
            conn.close()

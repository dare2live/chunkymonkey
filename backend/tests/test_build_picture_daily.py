"""Phase γ D3 — build_picture_daily e2e 集成测试。

用最小 in-memory fixtures 验证:
  - 全管道跑通不报错
  - 4 张输出表都有数据
  - 关键字段非空 (fundamental_stage, primary_type, latest_close, institution_score)
  - 幂等: 第二次跑 (同日) 行数不增加
"""
from __future__ import annotations

import pytest


def _seed_fixtures(conn, mkt_conn, target_date="2026-05-12"):
    """种入最小可跑通管道的 fixtures。"""
    # 主 conn 上
    conn.executescript("""
        CREATE TABLE dim_active_a_stock (stock_code TEXT PRIMARY KEY);
        INSERT INTO dim_active_a_stock VALUES ('600519'), ('000001'), ('300750');

        CREATE TABLE dim_stock_archetype_latest (
          stock_code TEXT, stock_archetype TEXT, pe_ttm DOUBLE,
          latest_revenue_yoy DOUBLE, latest_profit_yoy DOUBLE
        );
        INSERT INTO dim_stock_archetype_latest VALUES
          ('600519', '高质量稳健型', 32.5, 0.10, 0.15),
          ('000001', '周期/事件驱动型', 8.0, 0.20, 0.18),
          ('300750', '成长兑现型', 50.0, 0.35, 0.45);

        CREATE TABLE dim_stock_stage_latest (
          stock_code TEXT, stage_reason TEXT, stage_score_v1 DOUBLE,
          stock_gate TEXT, return_3m DOUBLE
        );
        INSERT INTO dim_stock_stage_latest VALUES
          ('600519', '稳健型基本面续航与趋势健康较好', 65.0, 'follow', 0.08),
          ('000001', '周期/事件型处于修复展开阶段', 55.0, 'watch', 0.15),
          ('300750', '稳健型短期存在过热迹象', 35.0, 'observe', 0.25);

        CREATE TABLE fact_stock_technical_stage (
          stock_code TEXT, date TEXT, stage TEXT, built_at TEXT
        );
        INSERT INTO fact_stock_technical_stage VALUES
          ('600519', '2026-05-11', '2', '2026-05-11'),
          ('600519', '2026-05-12', '2', '2026-05-12'),
          ('000001', '2026-05-11', '1.5', '2026-05-11'),
          ('000001', '2026-05-12', '2', '2026-05-12'),
          ('300750', '2026-05-12', '3', '2026-05-12');

        CREATE TABLE fact_technical_trigger (
          stock_code TEXT, date TEXT, formula_id TEXT
        );
        INSERT INTO fact_technical_trigger VALUES
          ('300750', '2026-05-10', 'macd_golden_cross'),
          ('300750', '2026-05-12', 'turtle_breakout_20');

        CREATE TABLE raw_aif10_valuation_quantile (
          security_code TEXT, index_type TEXT, statistics_cycle TEXT,
          percentile_thirty DOUBLE, percentile_fifty DOUBLE, percentile_seventy DOUBLE
        );
        INSERT INTO raw_aif10_valuation_quantile VALUES
          ('600519', '1', '4', 20.0, 30.0, 45.0),
          ('300750', '1', '4', 40.0, 60.0, 90.0);

        CREATE TABLE raw_aif10_peer_valuation (
          security_code TEXT, report_date TEXT,
          industry_pe_median DOUBLE, stock_pe DOUBLE
        );
        INSERT INTO raw_aif10_peer_valuation VALUES
          ('600519', '2025-12-31', 28.0, 32.5),
          ('300750', '2025-12-31', 35.0, 50.0);

        CREATE TABLE inst_institutions (
          id TEXT, name TEXT, display_name TEXT, type TEXT, enabled BIGINT
        );
        INSERT INTO inst_institutions VALUES
          ('inst_huijin', '中央汇金', '中央汇金', 'sovereign', 1),
          ('inst_gaoling', '高瓴资本', '高瓴资本', 'fund', 1);

        CREATE TABLE mart_institution_profile (
          institution_id TEXT, institution_name TEXT, win_rate_60d DOUBLE
        );
        INSERT INTO mart_institution_profile VALUES
          ('inst_huijin', '中央汇金', 65.0),
          ('inst_gaoling', '高瓴资本', 72.5);

        CREATE TABLE fact_top10_holder_period (
          stock_code TEXT, report_date TEXT, holder_name TEXT, holder_name_norm TEXT,
          hold_ratio_total DOUBLE, hold_change_num DOUBLE, is_exit_row BOOLEAN
        );
        INSERT INTO fact_top10_holder_period VALUES
          ('600519', '20260331', '中央汇金', '中央汇金', 4.0, 0.5, FALSE),
          ('600519', '20260331', '高瓴资本', '高瓴资本', 3.0, -0.2, FALSE),
          ('000001', '20260331', '中央汇金', '中央汇金', 5.0, 0.8, FALSE);
    """)
    conn.commit()

    # market conn 上
    mkt_conn.executescript("""
        CREATE VIEW v_price_kline_qfq AS
        SELECT * FROM (VALUES
          ('600519', '2026-05-11', 0.0, 0.0, 0.0, 1780.0, 1000.0, 1000000.0, 'daily', 'qfq'),
          ('600519', '2026-05-12', 0.0, 0.0, 0.0, 1800.0, 1100.0, 1100000.0, 'daily', 'qfq'),
          ('000001', '2026-05-11', 0.0, 0.0, 0.0, 12.0,   1000.0, 12000.0,   'daily', 'qfq'),
          ('000001', '2026-05-12', 0.0, 0.0, 0.0, 12.5,   1100.0, 13750.0,   'daily', 'qfq'),
          ('300750', '2026-05-11', 0.0, 0.0, 0.0, 200.0,  1000.0, 200000.0,  'daily', 'qfq'),
          ('300750', '2026-05-12', 0.0, 0.0, 0.0, 210.0,  1100.0, 231000.0,  'daily', 'qfq')
        ) t(code, date, open, high, low, close, volume, amount, freq, adjust);
    """)
    mkt_conn.commit()


class TestBuildPictureDailyE2E:
    def test_full_pipeline_writes_all_four_tables(self):
        from scripts.build_picture_daily import build_picture_daily
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        mkt_conn = duck_connect(":memory:")
        try:
            _seed_fixtures(conn, mkt_conn)
            result = build_picture_daily(
                target_date="2026-05-12", conn=conn, mkt_conn=mkt_conn,
            )
            # 3 股票全部进 4 张表
            assert result["fact_stock_fundamental_stage_daily"] == 3
            assert result["fact_stock_type_daily"] == 3
            assert result["dim_stock_stage_days"] == 3
            assert result["mart_stock_picture_daily"] == 3

            # 关键字段非空
            rows = conn.execute(
                "SELECT stock_code, fundamental_stage, primary_type, latest_close, "
                "valuation_pe, institution_score "
                "FROM mart_stock_picture_daily ORDER BY stock_code"
            ).fetchall()
            assert len(rows) == 3
            row_by_code = {r[0]: r for r in rows}

            # 600519 — 稳健型续航 → 温和验证, latest_close=1800
            r1 = row_by_code["600519"]
            assert r1[1] == "温和验证"
            assert r1[3] == 1800.0
            assert r1[4] == 32.5  # pe_ttm

            # 000001 — 周期型修复展开 → 周期复苏
            r2 = row_by_code["000001"]
            assert r2[1] == "周期复苏"
            assert r2[3] == 12.5

            # 300750 — 稳健型过热 → 已充分演绎, 命中技术突破规则(2 hits + vol_ratio TBD)
            r3 = row_by_code["300750"]
            assert r3[1] == "已充分演绎"
        finally:
            conn.close()
            mkt_conn.close()

    def test_idempotent_same_date(self):
        """跑两次同日, 行数不变 (DELETE+INSERT 原子替换)。"""
        from scripts.build_picture_daily import build_picture_daily
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        mkt_conn = duck_connect(":memory:")
        try:
            _seed_fixtures(conn, mkt_conn)
            r1 = build_picture_daily("2026-05-12", conn=conn, mkt_conn=mkt_conn)
            r2 = build_picture_daily("2026-05-12", conn=conn, mkt_conn=mkt_conn)
            assert r1 == r2
            n = conn.execute("SELECT COUNT(*) FROM mart_stock_picture_daily").fetchone()[0]
            assert n == 3
        finally:
            conn.close()
            mkt_conn.close()

    def test_institution_signal_computed(self):
        from scripts.build_picture_daily import build_picture_daily
        from services.duck_adapter import connect as duck_connect
        import json

        conn = duck_connect(":memory:")
        mkt_conn = duck_connect(":memory:")
        try:
            _seed_fixtures(conn, mkt_conn)
            build_picture_daily("2026-05-12", conn=conn, mkt_conn=mkt_conn)

            # 600519 有 2 个 holders (中央汇金 + 高瓴资本), 都是 tracked
            r = conn.execute(
                "SELECT institution_score, institution_n_insts, institution_top_json "
                "FROM mart_stock_picture_daily WHERE stock_code='600519'"
            ).fetchone()
            assert r[1] == 2  # n_insts
            assert r[0] > 0   # score
            top = json.loads(r[2])
            assert len(top) == 2
            # 按 share_pct desc: 中央汇金 4.0 > 高瓴资本 3.0
            assert top[0]["name"] == "中央汇金"
        finally:
            conn.close()
            mkt_conn.close()

    def test_atomic_rollback_on_error(self):
        """模拟 _write_atomic 失败, 之前的种子表保持不变。"""
        from unittest.mock import patch
        from scripts.build_picture_daily import build_picture_daily
        from services.duck_adapter import connect as duck_connect

        conn = duck_connect(":memory:")
        mkt_conn = duck_connect(":memory:")
        try:
            _seed_fixtures(conn, mkt_conn)
            # 先成功跑一次
            build_picture_daily("2026-05-11", conn=conn, mkt_conn=mkt_conn)
            n_before = conn.execute(
                "SELECT COUNT(*) FROM mart_stock_picture_daily"
            ).fetchone()[0]
            assert n_before == 3

            # 注入故障 (mock executemany 抛错)
            real_executemany = conn.executemany
            call_count = [0]
            def maybe_explode(*args, **kwargs):
                call_count[0] += 1
                # 第 3 次 executemany 时炸 (即 dim_stock_stage_days 插入时)
                if call_count[0] == 3:
                    raise RuntimeError("模拟 SIGPIPE in INSERT batch")
                return real_executemany(*args, **kwargs)

            with patch.object(conn, "executemany", side_effect=maybe_explode):
                try:
                    build_picture_daily("2026-05-12", conn=conn, mkt_conn=mkt_conn)
                    raise AssertionError("应该抛 RuntimeError")
                except RuntimeError as e:
                    assert "模拟 SIGPIPE" in str(e)

            # 关键: 2026-05-11 的数据不会被 2026-05-12 的部分写入污染
            n_after = conn.execute(
                "SELECT COUNT(*) FROM mart_stock_picture_daily WHERE snapshot_date='2026-05-11'"
            ).fetchone()[0]
            assert n_after == 3, "2026-05-11 数据被错误删了"
            n_2026_05_12 = conn.execute(
                "SELECT COUNT(*) FROM mart_stock_picture_daily WHERE snapshot_date='2026-05-12'"
            ).fetchone()[0]
            assert n_2026_05_12 == 0, f"2026-05-12 半成品没回滚, 残留 {n_2026_05_12} 行"
        finally:
            conn.close()
            mkt_conn.close()

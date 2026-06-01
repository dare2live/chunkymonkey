"""Unit tests for the research-side fund-flow rank snapshot builder."""
from __future__ import annotations


def _fake_snapshot_rows() -> list[dict[str, object]]:
    return [
        {
            "序号": 1,
            "股票代码": "600519",
            "股票简称": "贵州茅台",
            "最新价": 1234.5,
            "涨跌幅": 1.2,
            "换手率": 0.56,
            "流入资金": 999.0,
            "流出资金": 123.0,
            "净额": 876.0,
            "成交额": 2345.0,
        },
        {
            "序号": 2,
            "股票代码": "000001",
            "股票简称": "平安银行",
            "最新价": 12.34,
            "涨跌幅": -0.8,
            "换手率": 1.23,
            "流入资金": 100.0,
            "流出资金": 40.0,
            "成交额": 456.0,
        },
    ]


def test_build_snapshot_daily_writes_rows_and_watermark(monkeypatch) -> None:
    from conftest import duck_mem
    from scripts import build_fund_flow_rank_snapshot_daily as builder

    conn = duck_mem()
    try:
        def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
            assert capability == "individual_fund_flow_rank_snapshot"
            assert prefer_source == "akshare"
            assert kwargs == {"symbol": "即时"}
            return (_fake_snapshot_rows(), "akshare")

        monkeypatch.setattr(builder, "resolve", fake_resolve)

        result = builder.build_fund_flow_rank_snapshot_daily(
            snapshot_date="2026-05-29",
            snapshot_symbol="即时",
            conn=conn,
        )

        assert result["dry_run"] is False
        assert result["row_count"] == 2
        assert result["source_used"] == "akshare"
        assert result["min_rank_seq"] == 1
        assert result["max_rank_seq"] == 2

        rows = conn.execute(
            """
            SELECT snapshot_date, snapshot_symbol, rank_seq, stock_code, stock_name,
                   latest_price, change_pct, turnover_rate, inflow_amount,
                   outflow_amount, net_amount, turnover_amount, source_used,
                   source_capability
              FROM mart_stock_fund_flow_rank_snapshot_daily
             ORDER BY rank_seq
            """
        ).fetchall()
        assert len(rows) == 2
        first, second = rows
        assert str(first["snapshot_date"]) == "2026-05-29"
        assert first["snapshot_symbol"] == "即时"
        assert first["rank_seq"] == 1
        assert first["stock_code"] == "600519"
        assert first["stock_name"] == "贵州茅台"
        assert first["net_amount"] == 876.0
        assert str(second["snapshot_date"]) == "2026-05-29"
        assert second["snapshot_symbol"] == "即时"
        assert second["rank_seq"] == 2
        assert second["stock_code"] == "000001"
        assert second["stock_name"] == "平安银行"
        assert second["net_amount"] == 60.0  # computed from inflow - outflow

        watermark = conn.execute(
            """
            SELECT data_domain, source_name, source_tier, last_data_date, row_count, parser_version
              FROM mart_data_source_watermark
             WHERE data_domain = 'stock_fund_flow_rank_snapshot'
            """
        ).fetchone()
        assert tuple(watermark) == (
            "stock_fund_flow_rank_snapshot",
            "akshare",
            3,
            "2026-05-29",
            2,
            "akshare_stock_fund_flow_individual_snapshot_v1",
        )
    finally:
        conn.close()


def test_build_snapshot_daily_preserves_other_dates(monkeypatch) -> None:
    from conftest import duck_mem
    from scripts import build_fund_flow_rank_snapshot_daily as builder

    conn = duck_mem()
    try:
        def fake_resolve(capability: str, *, prefer_source=None, **kwargs):
            return (_fake_snapshot_rows(), "akshare")

        monkeypatch.setattr(builder, "resolve", fake_resolve)

        builder.build_fund_flow_rank_snapshot_daily(
            snapshot_date="2026-05-28",
            snapshot_symbol="即时",
            conn=conn,
        )
        builder.build_fund_flow_rank_snapshot_daily(
            snapshot_date="2026-05-29",
            snapshot_symbol="即时",
            conn=conn,
        )

        counts = conn.execute(
            """
            SELECT snapshot_date, COUNT(*) AS n
              FROM mart_stock_fund_flow_rank_snapshot_daily
             GROUP BY snapshot_date
             ORDER BY snapshot_date
            """
        ).fetchall()
        assert [(str(row["snapshot_date"]), row["n"]) for row in counts] == [
            ("2026-05-28", 2),
            ("2026-05-29", 2),
        ]
    finally:
        conn.close()

import sys
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import capital_client


def _make_conn():
    conn = duck_mem()
    capital_client.ensure_tables(conn)
    return conn


def test_capital_store_functions_batch_rows_and_preserve_counts():
    conn = _make_conn()
    try:
        snapshot_date = "2026-05-21"
        created_at = "2026-05-21T04:20:00"

        dividend_count = capital_client._store_dividend_summary(
            conn,
            snapshot_date,
            created_at,
            pd.DataFrame([
                {"代码": "1", "名称": "A", "上市日期": "20200101", "累计股息": "1.5", "年均股息": "0.3", "分红次数": "5", "融资总额": "10", "融资次数": "2"},
                {"代码": "600002", "名称": "B", "上市日期": "2020-02-03", "累计股息": "--", "年均股息": "0.2", "分红次数": "3", "融资总额": "8", "融资次数": "1"},
            ]),
        )
        repurchase_count = capital_client._store_repurchase(
            conn,
            snapshot_date,
            created_at,
            pd.DataFrame([
                {"股票代码": "600001", "股票简称": "A", "最新价": "12.3", "计划回购价格区间": "15", "计划回购金额区间-下限": "100", "计划回购金额区间-上限": "200", "占公告前一日总股本比例-下限": "1.1", "占公告前一日总股本比例-上限": "2.2", "回购起始时间": "20260501", "实施进度": "实施中", "已回购股份数量": "10", "已回购金额": "120", "最新公告日期": "20260520"},
            ]),
        )
        unlock_count = capital_client._store_unlock(
            conn,
            snapshot_date,
            created_at,
            pd.DataFrame([
                {"股票代码": "600001", "股票简称": "A", "解禁时间": "20260601", "限售股类型": "首发", "解禁数量": "100", "实际解禁数量": "80", "实际解禁市值": "1000", "占解禁前流通市值比例": "3.5", "解禁前一交易日收盘价": "10", "解禁前20日涨跌幅": "5", "解禁后20日涨跌幅": "-2"},
            ]),
        )

        assert dividend_count == 2
        assert repurchase_count == 1
        assert unlock_count == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_capital_dividend_summary").fetchone()[0] == 2
        assert conn.execute("SELECT stock_code FROM raw_capital_dividend_summary ORDER BY stock_code").fetchall()[0][0] == "000001"
        assert conn.execute("SELECT COUNT(*) FROM raw_capital_repurchase").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_capital_unlock").fetchone()[0] == 1
    finally:
        conn.close()


def test_capital_detail_store_functions_filter_missing_notice_dates():
    conn = _make_conn()
    try:
        created_at = "2026-05-21T04:20:00"
        dividend_count = capital_client._store_dividend_detail(
            conn,
            "600001",
            created_at,
            pd.DataFrame([
                {"公告日期": "20260520", "进度": "实施", "送股": "1", "转增": "2", "派息": "0.5", "除权除息日": "20260601", "股权登记日": "20260531", "红股上市日": "20260602"},
                {"公告日期": None, "进度": "忽略"},
            ]),
        )
        allotment_count = capital_client._store_allotment_detail(
            conn,
            "600001",
            created_at,
            pd.DataFrame([
                {"公告日期": "20260521", "配股方案": "3", "配股价格": "4.5", "基准股本": "100", "除权日": "20260603", "股权登记日": "20260602", "缴款起始日": "20260604", "缴款终止日": "20260608", "配股上市日": "20260610", "募集资金合计": "500"},
                {"公告日期": "", "配股方案": "忽略"},
            ]),
        )

        assert dividend_count == 1
        assert allotment_count == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_capital_dividend_detail").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM raw_capital_allotment_detail").fetchone()[0] == 1
    finally:
        conn.close()

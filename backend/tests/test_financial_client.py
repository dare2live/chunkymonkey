"""financial_client.calc_financial_derived 单测 (tushare 周期模型派生)。

2026-06-27 通达信全删: gpcw sina/akshare 同步路径全退役 (sync_financial_data 等 0 live caller),
原 10 个 sync 测试随 raw_gpcw_financial 物删一并移除; 本文件只保留迁移后 calc_financial_derived 的守门测试。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import duck_mem
from services import financial_client


# ============================================================
# calc_financial_derived — tushare 周期模型派生 (2026-06-26 通达信全删 单元4 迁移)
# ============================================================

def _seed_tushare_financial(conn):
    """注合成 tr.* tushare 源 (attach=False 时 tr=schema)。
    600519: 2 期 (FY 20251231 + Q1 20260331); 故意制造各陷阱供单测守:
      - fina 20260331 有 update_flag 0/1 双版本 → 验去重选 update_flag=1 (非旧 0 版的错值)
      - gross_margin 列填【金额】巨值 (陷阱), grossprofit_margin 填毛利率% → 验用对列
      - roe(季报累计) 与 roe_yearly(年化) 不同 → 验用 roe_yearly
      - income 有 FY+Q1 → 验 contract 锁年报期(FY) 不被 Q1 部分年度污染
      - stk_holdernumber 20260331 有双 ann_date → 验 holder 去重 ann_date DESC
    """
    conn.execute("CREATE SCHEMA tr")
    conn.execute("""
        CREATE TABLE tr.raw_tushare_fina_indicator (
            ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR,
            roe DOUBLE, roe_yearly DOUBLE, debt_to_assets DOUBLE, current_ratio DOUBLE,
            grossprofit_margin DOUBLE, gross_margin DOUBLE, netprofit_margin DOUBLE,
            tr_yoy DOUBLE, netprofit_yoy DOUBLE, ocf_to_profit DOUBLE)
    """)
    conn.executemany(
        "INSERT INTO tr.raw_tushare_fina_indicator VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # FY 20251231
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", 34.46, 34.46, 16.42, 5.09, 91.18, 1.539e11, 50.53, -1.20, -4.53, 53.59),
            # Q1 20260331 — 正确版 (update_flag=1)
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", 10.57, 42.27, 12.12, 7.06, 89.76, 4.84e10, 52.22, 6.34, 1.47, 71.69),
            # Q1 20260331 — 旧错版 (update_flag=0, 全是错值; 去重必须丢弃它)
            ("600519.SH", "20260331", "20260425", "0", "2026-04-20", 99.99, 99.99, 99.99, 0.01, 1.00, 1.0, 1.0, 9.99, 9.99, 9.99),
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_balancesheet (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR, contract_liab VARCHAR)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_balancesheet VALUES (?,?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", "100"),
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", "120"),  # bs 有 Q1 但锁FY期不用它
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_income (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, update_flag VARCHAR, built_at VARCHAR, revenue DOUBLE)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_income VALUES (?,?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "1", "2026-04-17", 2000.0),  # FY (12个月)
            ("600519.SH", "20260331", "20260425", "1", "2026-04-25", 500.0),   # Q1 (3个月, 故意给 — 验 contract 非FY期必 NULL 不被它污染)
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_daily_basic (ts_code VARCHAR, trade_date VARCHAR, float_share DOUBLE, total_share DOUBLE)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_daily_basic VALUES (?,?,?,?)",
        [
            ("600519.SH", "20260620", 12.50, 12.60),
            ("600519.SH", "20260623", 12.51, 12.61),  # 最新日
        ],
    )
    conn.execute("CREATE TABLE tr.raw_tushare_stk_holdernumber (ts_code VARCHAR, end_date VARCHAR, ann_date VARCHAR, built_at VARCHAR, holder_num VARCHAR)")
    conn.executemany(
        "INSERT INTO tr.raw_tushare_stk_holdernumber VALUES (?,?,?,?,?)",
        [
            ("600519.SH", "20251231", "20260417", "2026-04-17", "255892"),
            ("600519.SH", "20260331", "20260417", "2026-04-17", "243159"),  # 真值 (ann 更晚)
            ("600519.SH", "20260331", "20260410", "2026-04-10", "999999"),  # 旧 ann, 去重必丢
        ],
    )


def test_calc_financial_derived_tushare_period_model_mapping():
    """守迁移核心映射 + 两处真金白银修复 (roe_yearly / grossprofit_margin) + 去重 + 单位 + FY-restriction。"""
    conn = duck_mem()
    try:
        _seed_tushare_financial(conn)
        fact_count = financial_client.calc_financial_derived(conn, attach=False)

        dim = conn.execute("""
            SELECT roe, debt_ratio, current_ratio, gross_margin, net_margin,
                   revenue_yoy, profit_yoy, ocf_to_profit, contract_to_revenue,
                   holder_count, holder_count_change_pct, float_shares, total_shares,
                   latest_report_date, history_rows
            FROM dim_financial_latest WHERE stock_code='600519'
        """).fetchone()
        d = dict(zip(
            ["roe","debt_ratio","current_ratio","gross_margin","net_margin","revenue_yoy",
             "profit_yoy","ocf_to_profit","contract_to_revenue","holder_count",
             "holder_count_change_pct","float_shares","total_shares","latest_report_date","history_rows"],
            dim,
        ))

        # 修复1: roe 用 roe_yearly(42.27) 非季报累计 roe(10.57) 也非旧错版(99.99)
        assert abs(d["roe"] - 0.4227) < 1e-4, f"roe 应=roe_yearly/100=0.4227, 实={d['roe']}"
        # 修复2: gross 用 grossprofit_margin(89.76%) 非 gross_margin【金额】陷阱
        assert abs(d["gross_margin"] - 0.8976) < 1e-4, f"gross 应=grossprofit_margin/100=0.8976, 实={d['gross_margin']}"
        # current_ratio 不除 (倍数)
        assert abs(d["current_ratio"] - 7.06) < 1e-4, f"current_ratio 不除, 应=7.06, 实={d['current_ratio']}"
        assert abs(d["debt_ratio"] - 0.1212) < 1e-4
        assert abs(d["net_margin"] - 0.5222) < 1e-4
        assert abs(d["revenue_yoy"] - 0.0634) < 1e-4
        assert abs(d["profit_yoy"] - 0.0147) < 1e-4
        assert abs(d["ocf_to_profit"] - 0.7169) < 1e-4
        # contract 锁最新年报期(FY/1231): 即使 Q1(20260331) bs+income 都有, 也只用 FY 期 contract_liab(100)/FY revenue(2000)=0.05
        # (FY-restriction 修复期间口径混合 BLOCKER: 不会用 Q1 的 contract_liab(120)/Q1 revenue(500)=0.24 那种虚高跨期不可比值)
        assert abs(d["contract_to_revenue"] - 0.05) < 1e-4, f"contract 应锁FY期=0.05, 实={d['contract_to_revenue']}"
        # holder 去重(ann DESC 取 243159 非旧 999999) + 环比方向 (243159-255892)/255892
        assert d["holder_count"] == 243159
        assert abs(d["holder_count_change_pct"] - ((243159 - 255892) / 255892)) < 1e-6
        # float/total ×10000 (万股→股), 取最新日 20260623
        assert abs(d["float_shares"] - 125100.0) < 1e-3, f"float 应=12.51×10000, 实={d['float_shares']}"
        assert abs(d["total_shares"] - 126100.0) < 1e-3
        # 最新期格式 + history_rows
        assert d["latest_report_date"] == "2026-03-31"
        assert d["history_rows"] == 2  # 20251231 + 20260331 (去重后 distinct end_date)

        # fact 周期历史: 2 期 (去重后), float/total/holder_change 故意 NULL
        facts = conn.execute("""
            SELECT report_date, report_season, roe, gross_margin, contract_to_revenue,
                   holder_count_change_pct, float_shares, total_shares
            FROM fact_financial_derived WHERE stock_code='600519' ORDER BY report_date
        """).fetchall()
        assert fact_count == 2 and len(facts) == 2, f"fact 应 2 期 (去重), 实={fact_count}"
        fact_q1 = [f for f in facts if f[0] == "2026-03-31"][0]
        assert fact_q1[1] == "Q1"
        assert abs(fact_q1[2] - 0.4227) < 1e-4  # fact roe 也用 roe_yearly
        # fact Q1 contract: 即使 income 有 20260331(revenue=500), 非 FY 期也必 NULL (FY-restriction BLOCKER 修复)
        assert fact_q1[4] is None, "fact Q1(非年报期) contract 应 NULL — FY-restriction, 不算部分年度比率"
        # fact 的 point-in-time 列故意 NULL (无消费方)
        assert fact_q1[5] is None and fact_q1[6] is None and fact_q1[7] is None
        fact_fy = [f for f in facts if f[0] == "2025-12-31"][0]
        assert fact_fy[1] == "Q4"
        assert abs(fact_fy[4] - 0.05) < 1e-4  # FY contract = 100/2000
    finally:
        conn.close()


def test_calc_financial_derived_shadow_suffix_isolates_live():
    """write_suffix='_shadow' 只写影子表, 不碰 live dim/fact (promote 前验证隔离)。"""
    conn = duck_mem()
    try:
        _seed_tushare_financial(conn)
        financial_client.calc_financial_derived(conn, attach=False, write_suffix="_shadow")
        # 影子表有数据
        assert conn.execute("SELECT COUNT(*) FROM dim_financial_latest_shadow").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fact_financial_derived_shadow").fetchone()[0] == 2
        # live 表未被写 (ensure_tables 建了空表)
        assert conn.execute("SELECT COUNT(*) FROM dim_financial_latest").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_financial_derived").fetchone()[0] == 0
    finally:
        conn.close()

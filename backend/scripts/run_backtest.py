#!/usr/bin/env python3
"""运行全量历史回测并生成报告"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))

from services.db import get_conn, init_db
from services.market_db import get_market_conn, init_market_db
from services.backtest_engine import run_full_backtest


def generate_report(conn):
    """从研究表生成报告"""
    lines = []
    lines.append("# 历史回测报告")
    lines.append(f"\n生成时间: {__import__('datetime').datetime.now().isoformat()}")
    lines.append(f"\n---\n")

    # === 表 1 摘要 ===
    lines.append("## 1. 机构三层行业表现\n")

    # 各层级总览
    overview_rows = conn.execute("""
        SELECT industry_level,
               COUNT(*) as rows,
               COUNT(DISTINCT institution_id) as insts,
               COUNT(DISTINCT industry_name) as industries,
               AVG(avg_gain_30d) as g30,
               AVG(win_rate_30d) as wr30
        FROM research_inst_industry_performance
        WHERE industry_level IN ('L1', 'L2', 'L3') AND buy_event_count>=3
        GROUP BY industry_level
    """).fetchall()
    overview_by_level = {r["industry_level"]: r for r in overview_rows}
    for level in ["L1", "L2", "L3"]:
        r = overview_by_level.get(level)
        if r:
            rows = r["rows"]
            insts = r["insts"]
            industries = r["industries"]
            g30 = r["g30"] or 0.0
            wr30 = r["wr30"] or 0.0
        else:
            rows = insts = industries = 0
            g30 = wr30 = 0.0
        lines.append(f"**{level}**: {rows} 条 ({insts} 机构 × {industries} 行业), "
                      f"平均30d收益 {g30:.2f}%, 平均30d胜率 {wr30:.1f}%")

    # Top 行业高手 (L3, 买入>=5)
    lines.append("\n### L3 行业高手 Top 15 (买入>=5, 按30d胜率)\n")
    lines.append("| 机构 | 类型 | 行业(L3) | 买入事件 | 30d胜率 | 30d均收 | 30d回撤 | 边际优势 | 低溢价胜率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    tops = conn.execute("""
        SELECT inst_name, inst_type, industry_name, buy_event_count,
               win_rate_30d, avg_gain_30d, avg_max_drawdown_30d,
               industry_edge_30d, low_premium_win_rate_30d
        FROM research_inst_industry_performance
        WHERE industry_level='L3' AND buy_event_count>=5
        ORDER BY win_rate_30d DESC LIMIT 15
    """).fetchall()
    for t in tops:
        lines.append(f"| {t['inst_name'][:15]} | {t['inst_type'] or '-'} | {t['industry_name']} | "
                      f"{t['buy_event_count']} | {t['win_rate_30d']:.1f}% | {t['avg_gain_30d']:+.2f}% | "
                      f"{t['avg_max_drawdown_30d']:.1f}% | {t['industry_edge_30d']:+.2f}% | "
                      f"{t['low_premium_win_rate_30d']:.0f}% |")

    # === 表 2 摘要 ===
    lines.append("\n\n## 2. 持仓链条\n")
    chain_stats = conn.execute("""
        SELECT chain_status, COUNT(*) as cnt,
               AVG(chain_days) as avg_days,
               AVG(chain_inst_gain_pct) as avg_gain
        FROM research_holding_chains
        GROUP BY chain_status
    """).fetchall()
    for cs in chain_stats:
        text = (
            f"**{cs['chain_status']}**: {cs['cnt']} 条, 平均 {cs['avg_days']:.0f} 天"
            if cs["avg_days"]
            else f"**{cs['chain_status']}**: {cs['cnt']} 条"
        )
        if cs["avg_gain"] is not None:
            text += f", 机构平均收益 {cs['avg_gain']:+.2f}%"
        lines.append(text)

    # 闭合链条按机构类型
    lines.append("\n### 闭合链条按机构类型\n")
    lines.append("| 类型 | 链条数 | 平均天数 | 机构平均收益 | 跟随30d | 入场溢价 |")
    lines.append("|---|---|---|---|---|---|")
    ct = conn.execute("""
        SELECT i.type, COUNT(*) as cnt,
               AVG(c.chain_days) as days,
               AVG(c.chain_inst_gain_pct) as ig,
               AVG(c.follow_gain_30d) as fg30,
               AVG(c.entry_premium_pct) as prem
        FROM research_holding_chains c
        JOIN inst_institutions i ON c.institution_id=i.id
        WHERE c.chain_status='closed' AND c.chain_days IS NOT NULL
        GROUP BY i.type HAVING cnt>=5
        ORDER BY ig DESC
    """).fetchall()
    for r in ct:
        lines.append(f"| {r['type'] or '-'} | {r['cnt']} | {r['days']:.0f} | "
                      f"{r['ig']:+.1f}% | {r['fg30']:+.1f}% | {r['prem']:+.1f}% |" if r['ig'] else "")

    # === 表 3 摘要 ===
    lines.append("\n\n## 3. 交叉分析关键发现\n")

    # 机构类型 × 行业 top 组合
    lines.append("### 机构类型 × 行业 Top 10 (样本>=20, 按30d收益)\n")
    lines.append("| 机构类型 | 行业 | 样本 | 30d均收 | 30d胜率 | 30d回撤 | vs基线 |")
    lines.append("|---|---|---|---|---|---|---|")
    cf1 = conn.execute("""
        SELECT factor_a_value, factor_b_value, sample_count,
               avg_gain_30d, win_rate_30d, avg_drawdown_30d, uplift_vs_baseline
        FROM research_cross_factor
        WHERE factor_a='inst_type' AND factor_b='industry_l1'
            AND sample_count>=20
        ORDER BY avg_gain_30d DESC LIMIT 10
    """).fetchall()
    for r in cf1:
        lines.append(f"| {r['factor_a_value']} | {r['factor_b_value']} | {r['sample_count']} | "
                      f"{r['avg_gain_30d']:+.2f}% | {r['win_rate_30d']:.1f}% | {r['avg_drawdown_30d']:.1f}% | "
                      f"{r['uplift_vs_baseline']:+.2f}% |")

    # 共识度 × 溢价
    lines.append("\n### 共识度 × 溢价\n")
    lines.append("| 共识度 | 溢价 | 样本 | 30d均收 | 30d胜率 | 30d回撤 |")
    lines.append("|---|---|---|---|---|---|")
    cf2 = conn.execute("""
        SELECT factor_a_value, factor_b_value, sample_count,
               avg_gain_30d, win_rate_30d, avg_drawdown_30d
        FROM research_cross_factor
        WHERE factor_a='consensus' AND factor_b='premium_bucket'
        ORDER BY avg_gain_30d DESC
    """).fetchall()
    for r in cf2:
        lines.append(f"| {r['factor_a_value']} | {r['factor_b_value']} | {r['sample_count']} | "
                      f"{r['avg_gain_30d']:+.2f}% | {r['win_rate_30d']:.1f}% | {r['avg_drawdown_30d']:.1f}% |")

    # === 表 4 摘要 ===
    lines.append("\n\n## 4. 信号传递效率\n")
    st = conn.execute("""
        SELECT COUNT(*) as rows,
               AVG(signal_capture_30d) as avg_cap
        FROM research_signal_transfer
        WHERE signal_capture_30d IS NOT NULL
    """).fetchone()
    lines.append(f"有效记录: {st['rows']}, 平均 30d 信号捕获率: {st['avg_cap']:.3f}" if st['avg_cap'] else "信号传递数据不足")

    # 写入文件
    report_path = _ROOT / "docs" / "BACKTEST_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    return str(report_path)


def main():
    print("=" * 60)
    print("全量历史回测")
    print("=" * 60)

    init_db()
    init_market_db()
    conn = get_conn(timeout=600)
    mkt = get_market_conn()

    results = run_full_backtest(conn, mkt)
    print(f"\n回测结果: {results}")

    print("\n生成报告...")
    path = generate_report(conn)
    print(f"报告已写入: {path}")

    conn.close()
    mkt.close()


if __name__ == "__main__":
    main()

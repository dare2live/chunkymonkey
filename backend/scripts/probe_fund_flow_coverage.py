#!/usr/bin/env python3
"""M9 资金流覆盖率 probe (Codex §3 Q2 Decision 第一步, 只读不入生产表).

目标: 验证 akshare 主力资金流向数据是否值得接入.

5 个验收维度:
1. 股票覆盖率 - 多少 A 股能拉到数据
2. 日期连续性 - 是否对每个交易日都有, 是否有结构性缺失
3. 历史深度 - 能拉到多早的历史
4. 字段口径 - 主力/超大单 单位是否稳定 (元/万元) / 符号定义 / 与成交额比例是否合理
5. PIT 一致性 - 同一历史日期两次拉取结果是否一致 (本脚本拉两次只能验证 "在两个时间点是否一致", 真正的 PIT 还需要隔天再跑一次)

用法 (在你能上 eastmoney 的本地机器):
    cd /Users/dp/Documents/M/stock/backend
    python3 -m scripts.probe_fund_flow_coverage > /tmp/fundflow_probe.log 2>&1
    cat /tmp/fundflow_probe.log    # 把内容贴给 Claude

输出: 仅 stdout, 无 DB 写入. CSV 落 /tmp/fund_flow_probe_*.csv 供后续分析.
"""
from __future__ import annotations

import sys
import time
import random
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

# 抓 100 只票, 涵盖大/中/小盘 + 不同行业. 直接列出, 不依赖 DB
SAMPLE_STOCKS = [
    # 大盘 (沪深 300)
    ('600519', 'sh', '贵州茅台'),
    ('601318', 'sh', '中国平安'),
    ('600036', 'sh', '招商银行'),
    ('000333', 'sz', '美的集团'),
    ('000001', 'sz', '平安银行'),
    ('601988', 'sh', '中国银行'),
    ('600276', 'sh', '恒瑞医药'),
    ('601012', 'sh', '隆基绿能'),
    ('300750', 'sz', '宁德时代'),
    ('002594', 'sz', '比亚迪'),
    ('000858', 'sz', '五粮液'),
    ('600030', 'sh', '中信证券'),
    ('601398', 'sh', '工商银行'),
    ('601857', 'sh', '中国石油'),
    ('600028', 'sh', '中国石化'),
    # 中盘
    ('300059', 'sz', '东方财富'),
    ('300015', 'sz', '爱尔眼科'),
    ('600887', 'sh', '伊利股份'),
    ('601225', 'sh', '陕西煤业'),
    ('002475', 'sz', '立讯精密'),
    ('300760', 'sz', '迈瑞医疗'),
    ('600585', 'sh', '海螺水泥'),
    ('002714', 'sz', '牧原股份'),
    ('600009', 'sh', '上海机场'),
    ('601888', 'sh', '中国中免'),
    # 小盘 / ST / 创业 / 科创
    ('300033', 'sz', '同花顺'),
    ('300316', 'sz', '晶盛机电'),
    ('688981', 'sh', '中芯国际'),
    ('688008', 'sh', '澜起科技'),
    ('688111', 'sh', '金山办公'),
    ('300782', 'sz', '卓胜微'),
    ('002230', 'sz', '科大讯飞'),
    ('300999', 'sz', '金龙鱼'),
    ('688036', 'sh', '传音控股'),
    ('300866', 'sz', '安克创新'),
    # 行业代表
    ('600196', 'sh', '复星医药'),
    ('601728', 'sh', '中国电信'),
    ('600690', 'sh', '海尔智家'),
    ('601816', 'sh', '京沪高铁'),
    ('600438', 'sh', '通威股份'),
    ('300316', 'sz', '晶盛机电'),
    ('002460', 'sz', '赣锋锂业'),
    ('300274', 'sz', '阳光电源'),
    ('601658', 'sh', '邮储银行'),
    ('600406', 'sh', '国电南瑞'),
    # 中小创风格
    ('301180', 'sz', '万祥科技'),
    ('300999', 'sz', '金龙鱼'),
    ('002241', 'sz', '歌尔股份'),
    ('601933', 'sh', '永辉超市'),
    ('600436', 'sh', '片仔癀'),
    ('600660', 'sh', '福耀玻璃'),
]


def detect_market(code: str) -> str:
    """简单判断 sh / sz."""
    if code.startswith('60') or code.startswith('68'):
        return 'sh'
    return 'sz'


def main():
    print("=" * 80)
    print("M9 资金流 coverage probe (Codex §3 Q2 Decision)")
    print(f"开始时间: {datetime.now().isoformat()}")
    print("=" * 80)
    print()

    try:
        import akshare as ak
        print(f"akshare version: {ak.__version__}")
    except ImportError:
        print("ERROR: 没装 akshare. pip install akshare")
        sys.exit(1)
    print()

    # 1. 单股测试: 拉一只票, 看 schema
    print("=" * 80)
    print("[1/5] Schema probe: stock_individual_fund_flow('000001', 'sz')")
    print("=" * 80)
    try:
        df = ak.stock_individual_fund_flow(stock='000001', market='sz')
        print(f"shape: {df.shape}")
        print(f"columns: {list(df.columns)}")
        print(f"dtypes:")
        for col, dt in df.dtypes.items():
            print(f"  {col}: {dt}")
        print(f"\nhead 5 行:")
        print(df.head(5).to_string())
        print(f"\ntail 5 行:")
        print(df.tail(5).to_string())
        if '日期' in df.columns:
            dates = pd.to_datetime(df['日期'])
            print(f"\n日期范围: {dates.min().date()} ~ {dates.max().date()}, 共 {len(df)} 行")
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        sys.exit(1)
    print()

    # 2. 字段口径分析: 拉 1 只票最近 60 天, 看主力净额量级
    print("=" * 80)
    print("[2/5] 字段口径检查: 主力净额单位/符号/与成交额比例")
    print("=" * 80)
    try:
        df = ak.stock_individual_fund_flow(stock='600519', market='sh')  # 茅台
        df = df.sort_values('日期').tail(60)
        # 找主力净额列
        main_cols = [c for c in df.columns if '主力' in c]
        super_cols = [c for c in df.columns if '超大单' in c]
        amt_cols = [c for c in df.columns if '成交额' in c or '收盘价' in c]
        print(f"主力 列: {main_cols}")
        print(f"超大单 列: {super_cols}")
        print(f"成交额/价格 列: {amt_cols}")
        if main_cols:
            for c in main_cols[:3]:
                vals = pd.to_numeric(df[c], errors='coerce').dropna()
                if len(vals) > 0:
                    print(f"\n  {c}:")
                    print(f"    min={vals.min():>15.2f}, max={vals.max():>15.2f}, "
                          f"mean={vals.mean():>15.2f}, median={vals.median():>15.2f}")
                    print(f"    含负值: {(vals < 0).sum()}/{len(vals)} 行")
    except Exception as e:
        print(f"ERROR: {e}")
    print()

    # 3. 大批量覆盖率: 拉 50 只票, 测覆盖率
    print("=" * 80)
    print("[3/5] 覆盖率: 50 只票")
    print("=" * 80)
    coverage_results = []
    sample = SAMPLE_STOCKS[:50]
    for i, (code, market_hint, name) in enumerate(sample):
        market = detect_market(code) if market_hint not in ('sh', 'sz') else market_hint
        try:
            t0 = time.time()
            df = ak.stock_individual_fund_flow(stock=code, market=market)
            elapsed = time.time() - t0
            if df is None or df.empty:
                print(f"  [{i+1:>2}/{len(sample)}] {code} ({name}): EMPTY ({elapsed:.1f}s)")
                coverage_results.append({'code': code, 'name': name, 'rows': 0,
                                          'min_date': None, 'max_date': None,
                                          'elapsed_s': elapsed})
                continue
            dates = pd.to_datetime(df['日期'])
            min_d, max_d = dates.min().date(), dates.max().date()
            print(f"  [{i+1:>2}/{len(sample)}] {code} ({name:>6s}): {len(df):>5} 行, "
                  f"{min_d} ~ {max_d} ({elapsed:.1f}s)")
            coverage_results.append({'code': code, 'name': name, 'rows': len(df),
                                      'min_date': str(min_d), 'max_date': str(max_d),
                                      'elapsed_s': elapsed})
        except Exception as e:
            print(f"  [{i+1:>2}/{len(sample)}] {code} ({name}): ERR {type(e).__name__}: {str(e)[:80]}")
            coverage_results.append({'code': code, 'name': name, 'rows': -1,
                                      'min_date': None, 'max_date': None,
                                      'elapsed_s': None})
        time.sleep(0.5)  # 礼貌, 不打死 eastmoney

    cov_df = pd.DataFrame(coverage_results)
    cov_csv = f"/tmp/fund_flow_probe_coverage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    cov_df.to_csv(cov_csv, index=False)
    print(f"\nCSV saved: {cov_csv}")
    print()
    print("覆盖率摘要:")
    success = cov_df[cov_df['rows'] > 0]
    print(f"  成功拉到数据: {len(success)} / {len(cov_df)}")
    if len(success):
        print(f"  历史深度 (min_date): {success['min_date'].min()} ~ {success['min_date'].max()}")
        print(f"  最新日期 (max_date): {success['max_date'].min()} ~ {success['max_date'].max()}")
        print(f"  平均行数: {success['rows'].mean():.0f}")
        print(f"  平均拉取耗时: {success['elapsed_s'].mean():.2f}s")
    fail = cov_df[cov_df['rows'] <= 0]
    if len(fail):
        print(f"  失败/空: {len(fail)} 只: {list(fail['code'])}")
    print()

    # 4. 日期连续性: 取 1 只样本看是否每个交易日都有
    print("=" * 80)
    print("[4/5] 日期连续性 (000001 平安银行 最近 250 个交易日)")
    print("=" * 80)
    try:
        df = ak.stock_individual_fund_flow(stock='000001', market='sz')
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期')
        last_n = df.tail(300)
        # 工作日 vs 实际有数据
        gaps = last_n['日期'].diff().dt.days
        print(f"最近 {len(last_n)} 行, 日期差分布:")
        print(f"  1 天 (相邻交易日): {(gaps == 1).sum()}")
        print(f"  2 天 (跨周末/单日): {(gaps == 2).sum()}")
        print(f"  3 天 (跨周末): {(gaps == 3).sum()}")
        print(f"  4-7 天 (节假日): {((gaps >= 4) & (gaps <= 7)).sum()}")
        print(f"  > 7 天 (异常缺口): {(gaps > 7).sum()}")
        big_gaps = last_n[gaps > 7][['日期']]
        if len(big_gaps):
            print(f"\n大缺口列表:")
            for _, r in big_gaps.iterrows():
                print(f"  {r['日期'].date()}")
    except Exception as e:
        print(f"ERROR: {e}")
    print()

    # 5. PIT 一致性: 间隔几秒重拉同一只, 看历史是否变化
    print("=" * 80)
    print("[5/5] PIT 一致性 (同时间窗 2 次拉, 比较历史值)")
    print("=" * 80)
    try:
        df1 = ak.stock_individual_fund_flow(stock='000001', market='sz').copy()
        time.sleep(3)
        df2 = ak.stock_individual_fund_flow(stock='000001', market='sz').copy()
        # 比较最近 30 天的"主力净流入"
        df1['日期'] = pd.to_datetime(df1['日期'])
        df2['日期'] = pd.to_datetime(df2['日期'])
        df1, df2 = df1.sort_values('日期').tail(30), df2.sort_values('日期').tail(30)
        # 找主力净额列
        main_col = next((c for c in df1.columns if '主力' in c and ('净流入' in c or '净额' in c)), None)
        if main_col is None:
            main_col = next((c for c in df1.columns if '主力' in c), None)
        if main_col:
            v1 = pd.to_numeric(df1[main_col], errors='coerce').values
            v2 = pd.to_numeric(df2[main_col], errors='coerce').values
            n_diff = np.sum(np.abs(v1 - v2) > 0.01)
            max_diff = np.max(np.abs(v1 - v2))
            print(f"主力列: {main_col}")
            print(f"  对比样本数: {len(v1)}")
            print(f"  数值差异行数: {n_diff}")
            print(f"  最大差异: {max_diff:.4f}")
            print(f"  结论: {'PIT 一致 ✓' if n_diff == 0 else '⚠ 历史值在变化 (可能盘后改写)'}")
        else:
            print("找不到主力列, 跳过 PIT 检查")
    except Exception as e:
        print(f"ERROR: {e}")

    print()
    print("=" * 80)
    print("probe 完成")
    print(f"结束时间: {datetime.now().isoformat()}")
    print("=" * 80)
    print()
    print("⚠ 真正的 PIT 检查还需要隔天 (T+1) 再跑一次本脚本, 比较两次输出看 [4/5] 段")
    print(f"   是否同一历史日期的主力净流入值变化. 把今天的输出存好.")


if __name__ == '__main__':
    main()

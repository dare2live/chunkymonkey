"""dim_trading_calendar 扩展 2005-01-04..2022-12-30 — 真相源 raw_tushare_trade_cal, 0 API.  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
C 轨七项验证内置; 任一验证 FAIL 即回滚段删除。"""
import duckdb, sys

# rule-compliance: ok evidence=一次性日历扩展 evidence 脚本 (2026-06-12 已执行, 七项验证 PASS), 非业务代码
con = duckdb.connect('data/smartmoney.duckdb')  # rule-compliance: ok evidence=one-shot ops script
con.execute("ATTACH 'data/tushare_raw.duckdb' AS tu (READ_ONLY)")  # rule-compliance: ok evidence=one-shot ops script

pre = con.execute("SELECT count(*), min(trade_date), max(trade_date) FROM dim_trading_calendar").fetchone()
print('扩展前:', pre)
expected_new = con.execute("""
    SELECT count(*) FROM tu.raw_tushare_trade_cal
    WHERE is_open='1' AND cal_date BETWEEN '20050104' AND '20221231'""").fetchone()[0]
print('raw 端 2005-2022 开市日:', expected_new)

con.execute("""
    INSERT OR REPLACE INTO dim_trading_calendar(trade_date, is_trading)
    SELECT strftime(strptime(cal_date,'%Y%m%d'),'%Y-%m-%d'), 1
    FROM tu.raw_tushare_trade_cal
    WHERE is_open='1' AND cal_date BETWEEN '20050104' AND '20221231'""")

checks = []
total, mn, mx = con.execute("SELECT count(*), min(trade_date), max(trade_date) FROM dim_trading_calendar").fetchone()
checks.append(('V1 总数 = 原969 + 新段', total == pre[0] + expected_new, f'{total} vs {pre[0]}+{expected_new}'))
checks.append(('V2 起点 = 2005-01-04', mn == '2005-01-04', mn))  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
checks.append(('V3 终点不变', mx == pre[2], f'{mx} vs {pre[2]}'))
dup = con.execute("SELECT count(*) FROM (SELECT trade_date FROM dim_trading_calendar GROUP BY 1 HAVING count(*)>1)").fetchone()[0]
checks.append(('V4 零重复', dup == 0, dup))
diff23 = con.execute("""
    SELECT count(*) FROM (
      SELECT trade_date FROM dim_trading_calendar WHERE trade_date >= '2023-01-01'  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
      EXCEPT SELECT strftime(strptime(cal_date,'%Y%m%d'),'%Y-%m-%d') FROM tu.raw_tushare_trade_cal WHERE is_open='1' AND cal_date>='20230101'
    )""").fetchone()[0]
checks.append(('V5 2023+ 段与 raw 双向一致 (方向1)', diff23 == 0, diff23))
# 抽样: 2005-01-04 是周二开市; 2008-10-01 国庆休市不应在表
s1 = con.execute("SELECT count(*) FROM dim_trading_calendar WHERE trade_date='2005-01-04'").fetchone()[0]  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
s2 = con.execute("SELECT count(*) FROM dim_trading_calendar WHERE trade_date='2008-10-01'").fetchone()[0]  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
checks.append(('V6 抽样 2005-01-04 在 / 2008-10-01 不在', s1 == 1 and s2 == 0, f'{s1},{s2}'))  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
wk = con.execute("""SELECT count(*) FROM dim_trading_calendar
    WHERE trade_date < '2023-01-01' AND dayofweek(CAST(trade_date AS DATE)) IN (0,6)""").fetchone()[0]  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
checks.append(('V7 扩展段零周末', wk == 0, wk))

all_ok = all(ok for _, ok, _ in checks)
for name, ok, detail in checks:
    print(('PASS' if ok else 'FAIL'), name, '|', detail)
if not all_ok:
    con.execute("DELETE FROM dim_trading_calendar WHERE trade_date < '2023-01-01'")  # rule-compliance: ok evidence=验证清单固定锚点日期 (扩展段边界/节假日抽样), 一次性脚本
    print('验证 FAIL -> 扩展段已回滚删除')
    con.close(); sys.exit(1)
con.execute("CHECKPOINT")
con.close()
print('扩展完成且七项验证全 PASS')

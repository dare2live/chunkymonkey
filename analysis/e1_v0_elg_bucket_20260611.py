#!/usr/bin/env python3
"""E1 B-V0: 特大单资金流 × reversal 信号 条件分桶 (0 新数据, akshare 口径仅方向参考).

问题: reversal 信号日, 此前 5 日特大单净流入(洗盘?)vs 净流出(出货?) 的 forward 表现差异.
PIT: fund_flow 盘后数据 -> 信号日 t 只能用 <= t-1 的资金流 (JOIN 严格 < t).
预注册判据 (FINAL E1): 仅作方向性 go/no-go; akshare 口径永不入生产决策.
"""
import duckdb

con = duckdb.connect("data/smartmoney.duckdb", read_only=True)  # rule-compliance: ok evidence=analysis-oneoff-readonly
con.execute("ATTACH 'data/market.duckdb' AS market (READ_ONLY)")  # rule-compliance: ok evidence=analysis-oneoff-readonly

sql = """
WITH ff AS (  -- 个股日度特大单净流入 (akshare, 元)
    SELECT stock_code, CAST(trade_date AS DATE) AS d, super_large_net_amount AS elg
    FROM raw_fund_flow_daily
),
ff5 AS (      -- 滚动 5 日特大单净流入累计 (截至 d 当日, 含 d)
    SELECT stock_code, d,
           SUM(elg) OVER (PARTITION BY stock_code ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS elg5
    FROM ff
),
sig AS (      -- reversal 信号 (信号窗口与 fund_flow 重叠期)
    SELECT t.stock_code, CAST(t.date AS DATE) AS sig_d, t.formula_id
    FROM fact_technical_trigger t
    WHERE t.formula_id IN ('reversal_1m_deep', 'reversal_1m_mild')
      AND CAST(t.date AS DATE) BETWEEN DATE '2025-08-28' AND DATE '2026-04-17'  -- rule-compliance: ok evidence=akshare-fundflow-coverage-window-measured
),
joined AS (   -- PIT: 取信号日前一交易日 (<= t-1) 的最近 elg5
    SELECT s.stock_code, s.sig_d, s.formula_id,
           (SELECT f.elg5 FROM ff5 f
             WHERE f.stock_code = s.stock_code AND f.d < s.sig_d
             ORDER BY f.d DESC LIMIT 1) AS elg5_pre
    FROM sig s
),
px AS (       -- forward 收益 (T+1 open 买入口径: 用 t+1 与 t+11/t+21 的收盘近似 — V0 方向参考)
    SELECT j.*, k0.close AS c0,
           (SELECT k.close FROM market.price_kline_tdxhub k
             WHERE k.code = j.stock_code AND k.freq='daily' AND CAST(k.date AS DATE) > j.sig_d
             ORDER BY k.date LIMIT 1 OFFSET 9)  AS c10,
           (SELECT k.close FROM market.price_kline_tdxhub k
             WHERE k.code = j.stock_code AND k.freq='daily' AND CAST(k.date AS DATE) > j.sig_d
             ORDER BY k.date LIMIT 1 OFFSET 19) AS c20
    FROM joined j
    JOIN market.price_kline_tdxhub k0
      ON k0.code = j.stock_code AND k0.freq='daily' AND CAST(k0.date AS DATE) = j.sig_d
    WHERE j.elg5_pre IS NOT NULL
),
mv AS (       -- 市值代理: 信号日成交额 20 日均 (无市值表用流动性分桶)
    SELECT p.*, p.c10 / p.c0 - 1 AS fwd10, p.c20 / p.c0 - 1 AS fwd20,
           NTILE(3) OVER (PARTITION BY p.formula_id ORDER BY p.c0) AS px_bucket,
           CASE WHEN p.elg5_pre > 0 THEN 'inflow' ELSE 'outflow' END AS elg_side,
           NTILE(5) OVER (PARTITION BY p.formula_id ORDER BY p.elg5_pre) AS elg_q
    FROM px p
    WHERE p.c10 IS NOT NULL
)
SELECT formula_id, elg_side,
       COUNT(*) AS n,
       ROUND(AVG(CASE WHEN fwd10 > 0 THEN 1.0 ELSE 0 END), 4) AS win10,
       ROUND(AVG(fwd10), 5) AS avg10,
       ROUND(AVG(CASE WHEN fwd20 > 0 THEN 1.0 ELSE 0 END), 4) AS win20,
       ROUND(AVG(fwd20), 5) AS avg20
FROM mv
GROUP BY 1, 2 ORDER BY 1, 2
"""
print("=== 主表: 特大单 5 日净流向 (信号前, PIT t-1) × forward ===")
print(con.execute(sql).df().to_string(index=False))

# 五分位细化
sql_q = sql.replace(
    "SELECT formula_id, elg_side,",
    "SELECT formula_id, elg_q,").replace("GROUP BY 1, 2 ORDER BY 1, 2", "GROUP BY 1, 2 ORDER BY 1, 2")
print("\n=== 五分位 (elg_q 1=最大流出 5=最大流入) ===")
print(con.execute(sql_q).df().to_string(index=False))
con.close()

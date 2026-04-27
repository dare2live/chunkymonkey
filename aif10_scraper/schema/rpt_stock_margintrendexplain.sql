-- ============================================================
-- RPT_STOCK_MARGINTRENDEXPLAIN
-- trading/融资融券趋势解读 (daily)
-- 样本行数: 100, 字段数: 5
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_stock_margintrendexplain (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  SECURITY_INNER_CODE VARCHAR NOT NULL,
  SECURITY_NAME_ABBR VARCHAR NOT NULL,
  EXPLAIN VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE)
);

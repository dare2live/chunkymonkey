-- ============================================================
-- RPT_F10_CAPITAL_RAISE
-- capital_ops/募集资金来源 (event)
-- 样本行数: 100, 字段数: 10
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_capital_raise (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  NOTICE_DATE TIMESTAMP NOT NULL,
  FINANCE_TYPE VARCHAR NOT NULL,
  FINANCE_TYPEE VARCHAR NOT NULL,
  NET_RAISE_FUNDS DOUBLE,
  START_DATE TIMESTAMP,
  SECURITY_NAME_ABBR VARCHAR NOT NULL,
  SECURITY_TYPE VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE, NOTICE_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_f10_capital_raise_notice_date ON rpt_f10_capital_raise(NOTICE_DATE);

-- ============================================================
-- RPT_F10_DIVIDEND_EFFECT
-- dividend/分红影响 (daily)
-- 样本行数: 100, 字段数: 6
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_dividend_effect (
  SECURITY_CODE VARCHAR NOT NULL,
  SECUCODE VARCHAR NOT NULL,
  TRADE_DATE TIMESTAMP NOT NULL,
  CLOSE_PRICE DOUBLE NOT NULL,
  PE_TTM DOUBLE NOT NULL,
  PB_MRQ DOUBLE NOT NULL,
  PRIMARY KEY (SECUCODE)
);

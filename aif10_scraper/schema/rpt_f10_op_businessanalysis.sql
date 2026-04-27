-- ============================================================
-- RPT_F10_OP_BUSINESSANALYSIS
-- business/经营评述 (NLP, 全文) (quarterly)
-- 样本行数: 100, 字段数: 10
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_op_businessanalysis (
  SECURITY_INNER_CODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  SECUCODE VARCHAR NOT NULL,
  SECURITY_NAME_ABBR VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  REPORT_DATE TIMESTAMP NOT NULL,
  REPORT_NAME VARCHAR NOT NULL,
  BUSINESS_REVIEW VARCHAR NOT NULL,
  FUTURE_EXPECT VARCHAR,
  SECURITY_TYPE VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE, REPORT_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_f10_op_businessanalysis_report_date ON rpt_f10_op_businessanalysis(REPORT_DATE);

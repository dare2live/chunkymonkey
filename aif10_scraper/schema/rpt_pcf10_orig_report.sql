-- ============================================================
-- RPT_PCF10_ORIG_REPORT
-- financial/原始财报披露 (event)
-- 样本行数: 100, 字段数: 7
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_pcf10_orig_report (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  YEAR INTEGER NOT NULL,
  REPORT_TYPE VARCHAR NOT NULL,
  REPORT_DATE TIMESTAMP NOT NULL,
  PUBLISH_SITUATIONS VARCHAR NOT NULL,
  OPINION_TYPE VARCHAR,
  PRIMARY KEY (SECUCODE, REPORT_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_pcf10_orig_report_report_date ON rpt_pcf10_orig_report(REPORT_DATE);

-- ============================================================
-- RPT_F10_EH_FREEHOLDERSDATE
-- shareholder/十大流通股东 报告期列表 (quarterly)
-- 样本行数: 100, 字段数: 8
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_eh_freeholdersdate (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  END_DATE TIMESTAMP NOT NULL,
  IS_DEFAULT VARCHAR NOT NULL,
  IS_REPORTDATE VARCHAR NOT NULL,
  IS_MAX_REPORTDATE VARCHAR NOT NULL,
  REPORT_DATE_NAME VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE, END_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_f10_eh_freeholdersdate_end_date ON rpt_f10_eh_freeholdersdate(END_DATE);

-- ============================================================
-- RPT_F10_EH_HOLDERSDATE
-- shareholder/十大股东 报告期列表 (quarterly)
-- 样本行数: 100, 字段数: 8
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_eh_holdersdate (
  SECUCODE VARCHAR NOT NULL,
  END_DATE TIMESTAMP NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  IS_DEFAULT VARCHAR NOT NULL,
  IS_REPORTDATE VARCHAR NOT NULL,
  IS_MAX_REPORTDATE VARCHAR NOT NULL,
  REPORT_DATE_NAME DATE NOT NULL,
  PRIMARY KEY (SECUCODE, END_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_f10_eh_holdersdate_end_date ON rpt_f10_eh_holdersdate(END_DATE);

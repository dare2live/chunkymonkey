-- ============================================================
-- RPT_ORG_COURSECHANGE
-- profile/发展历程 (event)
-- 样本行数: 100, 字段数: 9
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_org_coursechange (
  SECURITY_INNER_CODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  SECUCODE VARCHAR NOT NULL,
  CHANGE_AFTER VARCHAR NOT NULL,
  CHANGE_DATE TIMESTAMP NOT NULL,
  CHANGE_BEFORE VARCHAR NOT NULL,
  CHANGE_NAME VARCHAR NOT NULL,
  CHANGE_REASON VARCHAR NOT NULL,
  CHANGE_EXPLAIN VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE, CHANGE_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpt_org_coursechange_change_date ON rpt_org_coursechange(CHANGE_DATE);

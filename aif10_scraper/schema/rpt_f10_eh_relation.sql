-- ============================================================
-- RPT_F10_EH_RELATION
-- shareholder/实控人 (static)
-- 样本行数: 100, 字段数: 6
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_eh_relation (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  HOLDER_NAME VARCHAR,
  RELATED_RELATION VARCHAR NOT NULL,
  HOLD_RATIO DOUBLE,
  PRIMARY KEY (SECUCODE)
);

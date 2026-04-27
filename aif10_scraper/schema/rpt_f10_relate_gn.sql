-- ============================================================
-- RPT_F10_RELATE_GN
-- peer/行业归属 (static)
-- 样本行数: 100, 字段数: 7
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_relate_gn (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  SECURITY_NAME_ABBR VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  BOARD_CODE VARCHAR NOT NULL,
  BOARD_NAME VARCHAR NOT NULL,
  BOARD_TYPE_NEW VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE, BOARD_CODE)
);

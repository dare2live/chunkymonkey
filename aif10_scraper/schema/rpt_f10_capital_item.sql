-- ============================================================
-- RPT_F10_CAPITAL_ITEM
-- capital_ops/项目进度 (event)
-- 样本行数: 100, 字段数: 10
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_capital_item (
  SECURITY_CODE VARCHAR NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  ITEM_NAME VARCHAR NOT NULL,
  NOTICE_DATE TIMESTAMP NOT NULL,
  PLAN_INVEST_AMT DOUBLE NOT NULL,
  ACTUAL_INPUT_RF DOUBLE,
  BUILD_PERIOD DOUBLE,
  YIELD DOUBLE,
  INVEST_RECOVERY_PERIOD DOUBLE,
  RANK VARCHAR NOT NULL
);

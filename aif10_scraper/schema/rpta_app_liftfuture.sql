-- ============================================================
-- RPTA_APP_LIFTFUTURE
-- events/限售解禁 (event)
-- 替代 ak.stock_restricted_release_detail_em
-- 样本行数: 100, 字段数: 11
-- ============================================================

CREATE TABLE IF NOT EXISTS rpta_app_liftfuture (
  SECUCODE VARCHAR NOT NULL,
  SECURITY_CODE VARCHAR NOT NULL,
  LIFT_DATE TIMESTAMP NOT NULL,
  LIFT_HOLDER_NUM INTEGER NOT NULL,
  LIFT_NUM INTEGER NOT NULL,
  ORG_CODE VARCHAR NOT NULL,
  LIFT_TYPE VARCHAR NOT NULL,
  TOTAL_SHARES_RATIO DOUBLE NOT NULL,
  UNLIMITED_A_SHARES_RATIO DOUBLE NOT NULL,
  SECURITY_TYPE_CODE VARCHAR NOT NULL,
  FACTOR_COST DOUBLE NOT NULL,
  PRIMARY KEY (SECUCODE, LIFT_DATE)
);

CREATE INDEX IF NOT EXISTS idx_rpta_app_liftfuture_lift_date ON rpta_app_liftfuture(LIFT_DATE);

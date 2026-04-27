-- ============================================================
-- RPT_F10_REMIND_RELATIONSHIP
-- events/大事提醒 (字典) (event)
-- 样本行数: 46, 字段数: 4
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_remind_relationship (
  EVENT_TYPE VARCHAR NOT NULL,
  EVENT_TYPE_CODE VARCHAR NOT NULL,
  BELONG_CLASSIF VARCHAR NOT NULL,
  BELONG_CLASSIF_CODE VARCHAR NOT NULL,
  PRIMARY KEY (EVENT_TYPE)
);

-- ============================================================
-- RPT_F10_PUBLIC_COMPANYTPYE
-- financial/⭐ 公司类型 (财报模板) (static)
-- 入库前必查, 决定字段集 (一般/银行/保险/券商)
-- 样本行数: 100, 字段数: 2
-- ============================================================

CREATE TABLE IF NOT EXISTS rpt_f10_public_companytpye (
  SECUCODE VARCHAR NOT NULL,
  COMPANY_TYPE VARCHAR NOT NULL,
  PRIMARY KEY (SECUCODE)
);

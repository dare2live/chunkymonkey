# smartmoney 34G 清理即拆分 Runbook (G2 执行计划)

> 2026-06-12 | 用户催办。现状实测: smartmoney.duckdb **34G / 345 表**, 磁盘余 48G (拆分可行)。
> 原则 (既有决策): DuckDB DELETE/DROP **不回收空间** → 唯一回收路径 = 活表导出重建新库。
> Gate (goal.md): 执行前需 copied-DuckDB validation + Codex review; 本文即 review 输入。

## 已执行 (2026-06-12 凌晨, 低风险先行)

| 动作 | 证据 |
|---|---|
| 2 个 hash 命名 cache 死表 (rg 全仓 0 非测试引用) EXPORT parquet 存档 → DROP → CHECKPOINT | `data/archive/dead_tables_20260612/` 985MB 可回滚; 各 4,052,975 行 |

## 体量大头 (est_rows top, 实测)

| 表族 | 量级 | 处置判定 |
|---|---|---|
| fact_tdx_gpcw_auto_feature_quarterly | 47.1M 行 | 活 (财务特征); W-B income 全史落地后评估替代退役 |
| panel 家族 8 变体 (fact_feature_panel / candidate(205列) / tdx_keep_challenger / mart_p0a_*_panel{,_v3,_v4,_v5,_unified_v1}) | ~30M 行累计 | **G4 收敛工程**: 各有 12-29 个消费文件, 不可直接清; 收敛决议 (留 v5+unified?) 后拆分时只搬幸存者 |
| 预测/信号历史 (lambdamart_v6 24.6M / technical_trigger 26.9M / signal_context 17.4M) | ~69M 行 | 活 (生产链路) |
| temporal/shareholder 研究 panel | 7.1M 行 | 研究产物, 拆分时评估转 parquet 归档 |

## 拆分步骤 (执行窗口: 任意交易日 03:00-16:00 避开 daily_update, 预计 30-60min)

1. **前置**: G4 panel 收敛决议 (Codex 参与: 哪些变体随迁/降archive/弃) — 这是拆分收益的大头
2. 冻结写入: 确认无 chain/daily_update 进程持锁
3. 新库 `smartmoney_v2.duckdb`: ATTACH 旧库 read_only → 按"幸存者清单"逐表 `CREATE TABLE AS SELECT`
4. **copied-DB validation** (gate 要求): 逐表行数比对 + 每表 3 行随机抽样字段级比对 + 关键表 (kline 视图依赖/watermark/决策 mart) 专项断言 — 脚本化, FAIL 即弃新库
5. 原子换名: 旧库 → `smartmoney_v1_retired_<date>.duckdb` (保留 14 天), 新库顶位; `database_manifest.yaml` 同步
6. doctor + data-status + 全量测试回归; 14 天无异常后删旧库文件 (真正回收 ~10-15G)

## 风险与回滚

- 任何 validation FAIL → 直接弃新库, 旧库零改动 (步骤 3-4 全程只读旧库)
- 换名后异常 → 改回 manifest 指旧库 (秒级回滚)
- 预期回收: 死表 (已 DROP 的不再搬) + panel 收敛弃置 + 重建紧缩 ≈ **10-15G** (实数以拆分后为准, 不臆造)

## 2026-06-12 执行后回归与修复 (重要反例)

**回归**: `COPY FROM DATABASE` 只搬数据+视图, **不搬 PK/UNIQUE 约束与索引** —
新库 1 约束/2 索引 vs 旧库 315/348。后果: `upsert ... ON CONFLICT` 全部
Binder Error (drain 转正 6 域连环失败), 查询性能受损。初版 validation
只比行数+抽样值, 没比 schema 元素 = 验收尺自身有盲区。

**修复 (混合方案, grill 后选型)**: ON CONFLICT 真实写入面仅 4 表
(watermark/schema_version/gpcw_financial/forecast_upside_live, 全小表) →
旧库原 DDL 带 PK 重建 (秒级) + upsert 冒烟 PASS; 348 索引从旧库 DDL 全量
重放 (IF NOT EXISTS 幂等)。**不为 3 个写路径重建 315 张表** (奥卡姆)。
其余表 PK 不重建的代价显式接受: 新 ON CONFLICT 写路径会显式报错 (可见,
非静默), 届时按需单表重建。

**validation v2 维度 (下次拆分必查)**: 行数 + 抽样值 + **约束计数 +
索引计数 + ON CONFLICT 写路径冒烟** 五件套。
**工具选型修正**: 整库迁移要保真用 `EXPORT DATABASE`/`IMPORT DATABASE`
(schema.sql 含约束索引); `COPY FROM DATABASE` 仅适合纯数据搬运。

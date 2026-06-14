# smartmoney 34G 清理即拆分 Runbook (G2 执行计划)

> **状态: 已偏离 (2026-06-14 地基-reset 取代)** — 本文档(计划/设计/handoff层)针对已删的模型/特征/寻优/serving层, 已过时; 保留作历史叙事/measured证据参考。当前态见 `goal.md` + `docs/data_management_framework.md`。


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

## 2026-06-12 15:34 第二轮整改 — 写入面"仅 4 表"声称证伪, PK 全量恢复

**证伪**: 全仓静态扫描 (INSERT OR REPLACE + ON CONFLICT, backend/+scripts/) 实测
upsert 目标表 184 个, 其中存在于 smartmoney 且无 PK/UNIQUE 的 = **165 张**, 不是 4 张 —
首轮修复只覆盖了 drain 路径撞到的表。当晚 17:00 daily_update 链路传递闭包内至少 11 张
(mart_p0a_label_panel 4.2M / fact_risk_factors 4.85M / sniper+institution_score 各 2.4M 等)
会再撞 Binder Error (被 step_degraded 吞成降级, 不致整链死但污染当晚实弹观察)。

**整改 (16:50 前完成)**: 放弃 call-graph 猜热路径 (import 解析有盲区, 实测漏报
mart_prediction_outcome 等), 改恒等式规则 "凡 upsert 目标表必有 PK": 164 张全部按旧库
DDL 原样重建 (逐表事务: drop 表上索引 -> RENAME -> CREATE 旧 DDL -> INSERT SELECT ->
行数核验 -> DROP tmp -> 重放旧库该表索引), 79 张 7.2s + 85 张 100.6s 完成, 0 失败。

**终态实测**: PK/UNIQUE 约束表 5->169; 索引 348 不变; 表 343 不变; 0 tmp 残留;
upsert 冒烟 6/6 PASS (pipeline_lock/prediction_outcome/quality_gate/sector_momentum/
paper_sim_kpi/financial_sync_state, 事务内 INSERT OR REPLACE 回滚验证); 库文件
23.7G -> 24G (+0.3G PK ART 索引)。

**显式残余 (1 张, 非本回归)**: fact_fundamental_quarterly 旧库本就无 PK —
写入器 build_fundamental_quarterly.py 的 upsert 在拆分前同样会报错, 属既有缺陷,
单独立案不混入本次回归。

**RENAME 依赖坑 (validation v2 六件套补充)**: DuckDB 表上有索引时 ALTER RENAME 报
Dependency Error — 重建顺序必须 先 DROP 该表索引再 RENAME。五件套升级:
行数 + 抽样值 + 约束计数 + 索引计数 + upsert 冒烟 + **写入面全仓扫描对账**
(声称写入面 N 表必须给静态扫描证据, 不许拍)。

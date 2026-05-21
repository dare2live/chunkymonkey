# 全系统架构 Audit — 2026-05-21

> 触发: 用户 2026-05-21 21:00 push back "对整个系统的交互连通性 + 代码复杂度 + 模块化管理 + 可维护可扩展 + 代码冗余 audit, 本着第一性原理 + 奥卡姆剃刀".
> 方法: codegraph (status / query / context) + complexity-optimizer + 4 个并行 agents (Explore × 2 + Plan × 1 + general-purpose × 1).
> 范围: 主项目 chunkymonkey + 子模块 market_perception + 物理隔离 sibling BestChoice.

## 0. TL;DR

**健康度: 良好但有改进空间**. 项目无循环依赖 / 反向依赖 / 物理边界清晰, 是工程上"还能往前走"的状态. 但累积了 5 个 god-modules (top: `updater.py` 5136 LOC) 和 ~6.9% 冗余 (~7.3K LOC, 主要 DDL/SQL 重复). 按奥卡姆: 不为拆而拆, 但 `updater.py` + `data_quality.py` 拆有真 ROI.

**主要 verdict (按用户问的维度)**:

| 维度 | Verdict | 关键数字 |
|---|---|---|
| 交互连通性 | [PASS] | 0 循环依赖 / 0 services→routers 反向 / 三子系统物理隔离 |
| 代码复杂度 | [WARN] | 80 HIGH 全集中 legacy `assets/js/app.js` + 5 god-modules backend |
| 模块化管理 | [WARN] | services 368 files OK, 但 god-modules 内部职责不清 |
| 可维护性 | [WARN] | 233 scripts 中 ~70 个可能死代码 + DDL 158 file 散落 |
| 可扩展性 | [PASS] | 加新 engine / 新公式 / 新模型 都有明确 5 步模板, 估时 4h-2w |
| 代码冗余度 | [NOTE] 6.9% (~7.3K LOC) | DDL 重复 8% / SQL 重复 4% / 脚本族 6% / helper 3% / 死代码 1% |

## 1. 项目全貌 (codegraph + LOC 实测)

### 主项目 chunkymonkey
| 指标 | 值 |
|---|---:|
| 总文件 | 920 (Python 874 / JSX 24 / JS 22) |
| Codegraph nodes | 14,189 |
| Codegraph edges | 162,651 |
| 总 LOC (backend) | ~192,427 |
| Functions / Methods | 5,872 / 931 |
| Classes / Imports | 408 / 4,403 |
| **services** | 368 files |
| **scripts** | 233 files |
| **routers** | 18 files |
| **tests** | 332 files |

### BestChoice (物理隔离)
| 指标 | 值 |
|---|---:|
| 总文件 | 25 (Python + JSX) |
| Codegraph nodes | 749 |
| Codegraph edges | 2,615 |
| LOC | ~13,385 |
| **research_cache.duckdb** | 973 MB |
| **HIGH complexity** | 80 (全在 BestChoice 内: `compute.py:18` / `list-panel.jsx:25` / `tweaks-panel.jsx:8` / `detail-pane.jsx:6` / `main.py:7`) |

## 2. 交互连通性 (verdict: [PASS])

### 2.1 主项目内 import 拓扑

| 检查 | 结果 |
|---|---|
| services 内部循环依赖 | **0** (164 files 有向依赖无环) |
| services → routers 反向 | **0** |
| services → scripts 反向 | 3 处 lazy import (`update_tasks.py` → holders/profile/auto_features), 设计可接受 |
| 入口集中度 | 1 entry (`backend/main.py`) → 18 routers → 368 services |

### 2.2 子系统边界

| 边界 | 验证方法 | 结果 |
|---|---|---|
| chunkymonkey ↔ market_perception | `from services.market_perception import ...` 直接 import (同 monorepo) | [OK] 标准 sub-package |
| chunkymonkey ↔ BestChoice | `grep "import bestchoice\|from bestchoice" backend/` | **0 hit** [OK] 完全物理隔离 |
| BestChoice → chunkymonkey | `grep "from backend\|chunkymonkey" /Users/dp/Documents/M/stock/bestchoice/` | **0 hit** [OK] 完全物理隔离 |
| market_perception ↔ BestChoice | 双向 grep | **0 hit** [OK] |

### 2.3 子系统跟主 ML pipeline 关系

| 关系 | 详情 |
|---|---|
| market_perception → LambdaMART v6 panel | **0 hit** (`grep "market_perception" backend/scripts/build_*panel*.py`). market_perception 是**独立 lane**, 不灌进 model panel, 只服务 UI/router |
| BestChoice → 主项目 | 设计走 mart 表 import (plan §5), 当前未实施. 主项目仅 4 处 docstring 提"借鉴 bestchoice" |

## 3. 代码复杂度 (verdict: [WARN])

### 3.1 主项目 80 HIGH 全 legacy frontend

```
80 HIGH = 75 nested-or-callback-loop + 4 sort-in-loop + 1 io-or-query-in-loop
全部集中: assets/js/app.js
```

这是历史遗留 god-module, backend Python 当前 0 hotspot 残留 (Codex 拆 workbench 后清理干净).

### 3.2 Backend 5 大 god-modules

| 文件 | LOC | 主要痛点 | 优先级 |
|---|---:|---|---|
| `backend/routers/updater.py` | 5136 | 32 step + 16 endpoint + ~40 helper 混堆; `_step_sync_market_data` 单函数 700 LOC; 改一个 step 全文 lock | **P0** |
| `backend/services/data_quality.py` | 4276 | 69 funcs 全 module-level; seed/contract/check/null-policy 5 concern 混叠 | **P0** |
| `backend/services/scoring.py` | 2712 | 2 大算法 + grade helper + setup 评估混 | P1 |
| `backend/scripts/build_feature_panel_duck.py` | 2291 | script 但被 8 tests 当 library import | P2 |
| `backend/services/signals_v2.py` | 2013 | cache + policy + EV + decision + GPCW 5 concern | P1 |

### 3.3 BestChoice complexity

| File | LOC | HIGH | 备注 |
|---|---:|---:|---|
| `compute.py` | 3302 | 18 | god-module: cache / formula / Optuna / dump 混 |
| `main.py` | 786 | 7 | FastAPI 入口 |
| `design/list-panel.jsx` | 385 | 25 | JSX god-module: hook + filter + sort |
| `design/tweaks-panel.jsx` | 568 | 8 | UI |

## 4. 模块化管理 (verdict: [WARN])

### 4.1 已成功拆分经验 (Codex + Claude 2026-05-20/21)

| 拆分 | 结果 |
|---|---|
| `workbench.py` → ~30 个 `workbench_*_read.py` services | god-module → read-only sub-services |
| `market_db.py` → `market_schema.py` | DDL 集中 |
| `v3_market_perception.py 807→577 LOC` | serialize 抽到 `router_serialize.py 271 LOC` |

### 4.2 market_perception 子模块

| Engine | LOC | 共享工具 |
|---|---:|---|
| `regime_engine.py` | 752 | 含 `_table_exists` / `_fetchall` / `_to_date` / `_attach_market_if_available` 共享枢纽 |
| 其他 6 engines | 238-389 each | 全部 `from .regime_engine import ...` 复用工具 |

[WARN] **P1**: 6 engines 都 import regime_engine 工具函数 → 违反水平分层. 应抽 `market_perception/utils.py`.

### 4.3 233 scripts 治理度

- 仅 3 个被 services 显式引用 (`ingest_holders_tdxhub` / `profile_tdx_gpcw_fields` / `build_tdx_gpcw_auto_features`)
- 其他 230 通过 CLI / daily_update.sh / lazy import 调用, 缺统一 entry point
- [NOTE] **P2**: 可疑死 scripts 约 70 个 (need git log + grep 确认未用)

## 5. 可维护性 (verdict: [WARN])

### 5.1 DDL 散落 (最大维护成本)

| 表 | CREATE TABLE 重复次数 | 风险 |
|---|---:|---|
| `fact_feature_panel` | **37** | schema drift |
| `dim_trading_calendar` | **34** | 同上 |
| `fact_feature_panel_candidate` | **28** | 同上 |
| 整体 `CREATE TABLE IF NOT EXISTS` | 158 files 散落 | 改 schema 要扫 158 处 |

[OK] **修法已有先例**: market_schema.py (Codex 拆出) + ensure_market_schema().

### 5.2 跟 SESSION_HANDOFF / workflow_checkpoint 同步度

| 三件套 | 现状 |
|---|---|
| goal.md | [OK] 实时更新 (本 audit 已落档) |
| SESSION_HANDOFF.md | [OK] cron 5min 自动更新 |
| analysis/workflow_checkpoint.md | [OK] 实时更新 |

## 6. 可扩展性 (verdict: [PASS]) — stress test

| 假设场景 | 改动量 | 估时 | 阻碍 god-module? |
|---|---|---|---|
| 加第 8 个 market_perception engine | 5 处 (engine + __init__ + build + serialize + router) | 4-6h | 0 (模式成熟) |
| BestChoice 1146 candidates import (Phase 1) | 1 新 script + 1 新 mart + DDL + panel JOIN | 1-2 天 | 0 (但 PIT 边界要 audit) |
| 加第 6 个 BestChoice 公式 | `formula_engine.py:546` 加 1 项 + 公式 fn (~50 LOC) | 2-3h | compute.py 不阻碍单加公式, 阻碍 = 改 cache 调度 |
| LambdaMART v6 → v7 | 4 god-modules 必动 (build_p0a_feature_panel_v4 + build_hybrid + db.py / schema_marts + Optuna runner) | 1-2 周 | market_perception + BestChoice 0 影响 |

**结论**: 添加新东西的成本可预测, 改 ML 核心还是依赖 god-modules — 拆 god-modules 能降低这个成本.

## 7. 代码冗余度 (verdict: 6.9% / ~7.3K LOC, 可缩 6.8K)

| 类别 | 冗余度 | 规模 | 修法 | 风险 |
|---|---:|---:|---|---|
| **DDL 重复** | 8% | 2.1K LOC | 建 `services/schema/` 集中 158 个 CREATE TABLE | 低 |
| **SQL JOIN 重复** | 4% | 1.2K LOC | `services/sql_templates.py` 提取 PIT JOIN factory | 低 |
| **脚本族** | 6% | 3.2K LOC | 26 audit / 5 paper_sim / 2 retrain 用 base class + YAML | 中 (需测试) |
| **helper 函数** | 3% | 0.5K LOC | `services/utils/numeric.py` 集中 `_finite_float` (10 次) | 低 |
| **死代码** | 1% | 0.3K LOC | git log + grep 验证后删 | 低 |
| **unused imports** | 2% | <200 LOC | 采样手工查 | 低 |
| **总冗余** | **~6.9%** | **~7.3K LOC** | — | — |

## 8. 第一性原理 push back (奥卡姆剃刀)

按 plan agent + 我自己判断, **以下不该拆**:

| 不拆项 | 理由 |
|---|---|
| `build_feature_panel_duck.py 主体` | script 不是 service, 单次跑批 orchestration, 拆完反破坏"一文件管一次 panel build"可读性. 只抽 SQL helper |
| `scoring.calculate_institution_scores` / `calculate_stock_scores` 本身 | 长但单 pipeline, 拆内部 step 破坏 SQL 事务边界 |
| `signals_v2.build_today_signals` | 顶层 orchestrator, 跟 endpoint 一一对应 |
| `updater.py` 的 16 endpoint | 路由本职, 别为 LOC 拆 endpoint |
| `v3_market_perception.py` 继续拆 7 helpers (cosmetic) | 已 577 LOC, helpers 全本地 caller, 抽出无真复用 |

## 9. Top 3 推荐修复 (按 ROI)

| 优先级 | 工作 | 投入 | 收益 |
|---|---|---|---|
| **P0** | DDL 集中: 158 files 散 CREATE TABLE → `services/schema/` | 2-3 天 | 省 2.1K LOC + 消除 schema drift 风险 |
| **P0** | 拆 `data_quality.py` 4276 LOC (低风险 dry run) | 2-3 天 | 拆分模板验证 + 为 updater 探路 |
| **P0** | 拆 `updater.py` 5136 LOC (最大 god-module) | 3-4 天 | 主项目最大维护性收益 |

中等优先级 (P1-P2 留 follow-up): scoring / signals_v2 / build_feature_panel / market_perception utils 抽出 / 脚本族 base class / 70 个死 scripts 清理.

## 10. 跟用户终极目标的关系

**用户终极目标** (年化≥30% / max_dd≥-20% / 月胜率≥55% / 超额 HS300>0):

- 这次 audit **不直接产 alpha**, 但**降低 future iteration 成本**
- 主项目当前 Phase4 verdict=block (lm735/sniper265 relative_drop 81.36%) - 需要换思路 / 加 alpha / 改 retrain. 这些都要动 god-modules
- BestChoice Phase 1+ 要新加 1 新 import script + 1 mart 表 + panel JOIN — 走 plan §5 路径
- Wait GCP stability retrain COMPLETE (估 8h+ 后) → 走 `post_retrain_pipeline.sh` 测下一个 model

## 引用

- `/tmp/cm_complexity_full.md` (402 行 complexity scan)
- `/tmp/bc_complexity.md` (BestChoice 80 HIGH)
- codegraph stats: 14,189 nodes / 162,651 edges
- Agent 1-4 raw reports (本 session)
- CLAUDE.md §7.4 codegraph + complexity 双扫规则
- bestchoice/analysis/bestchoice_chunkymonkey_validation_plan.md
- analysis/bestchoice_phase0_freeze_20260521.md
- analysis/data_integrity_audit_20260521.md

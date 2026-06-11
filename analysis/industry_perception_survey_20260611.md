# ChunkyMonkey 行业分类 + 市场感知 现状调研报告

**调研日期**: 2026-06-11  
**覆盖范围**: 通达信行业分类使用、申万分类现有资产、市场感知模块架构、概念/题材数据资产

---

## A. 通达信行业分类的使用面（切换影响评估）

### A.1 通达信行业分类体系概览

#### 分类层级结构
- **L1 (一级，13 类)**：通达信标准分类，覆盖全市场
  - T01 能源 (155 stocks)
  - T02 材料 (830 stocks)
  - T03 日常消费 (384 stocks)
  - T04 可选消费 (950 stocks)
  - T05 商贸 (120 stocks)
  - T06 社会服务 (205 stocks)
  - T07 装备制造 (1105 stocks)
  - T08 公用事业 (193 stocks)
  - T09 交通运输 (123 stocks)
  - T10 金融 (120 stocks)
  - T11 建筑地产 (251 stocks)
  - T12 信息产业 (1082 stocks)
  - T13 综合类 (19 stocks)

- **L2 (二级，56 类)**：更细粒度分类
- **L3 (三级，76 类)**：最细粒度分类

#### 主要存储表
| 表名 | 粒度 | 行数 | 特点 | 最新日期 |
|------|------|------|------|---------|
| `dim_stock_tdx_industry` | 股票×分类 | 5,619 | 当前快照，每股一行 L1/L2/L3 | 已更新 |
| `dim_stock_tdx_industry_history` | 股票×日期×分类 | 56,106 | 历史快照，PIT 用途 | 2026-05-07 |
| `raw_tdx_industry_file_snapshot` | 文件日期 | 5 | 通达信文件摘要 | 2026-05-01 |
| `mart_stock_industry_pit` | 股票×有效期 | 39,259 | **PIT-aware 映射表** | 2026-05-07 |

### A.2 PIT 化现状：mart_stock_industry_pit 质量评估

#### 关键统计
```
表结构: stock_code, effective_from, effective_to, tdx_l1/l2/l3, tdx_l1/l2/l3_name, 
        source, source_snapshot_date, confidence_level, is_historical_pit, built_at

行业映射 confidence_level 分布:
  - observed_snapshot:      33,648 行 (85.7%)  [OK] 历史快照，PIT 安全
  - current_label_fallback:  5,611 行 (14.3%)  [WARN] 当前标签代替，leakage 风险

特别关注: 
  - 大量 effective_from='1900-01-01' (fallback rows)，表示数据稀疏
  - dim_stock_tdx_industry_history 原始数据只有 5 个日期快照 (超稀疏)
  - PIT v3 feature panel 中：96.9% 行使用 current_label_fallback (leakage 已接受)
```

### A.3 通达信行业分类的代码引用面（影响面清单）

**关键模块统计**（按引用频度）：

#### Core Engines & Features (高频，>15 文件)
1. **特征工程层** (66 引用 in feature_join/标签)
   - `backend/services/labels/feature_join_v3.py`: sector_ret_*/sector_excess_* 5 列 via mart_stock_industry_pit
   - `backend/services/labels/feature_join_v4.py`: 同 v3 + 新增 sm_ret/sm_excess 6 列
   - `backend/services/labels/feature_join_v5.py`: 同 v4，但移除 Pattern 10 泄漏列
   - **Panel 现状**: v3 (102 cols) → v4 (143 cols) → v5 (135 cols)，sector 列数稳定在 6-15

2. **评分与筛选** (42 references in scoring.py + 33 in industry_pit.py)
   - `backend/services/scoring.py`: tdx_l1/l2 用于 institution 行业 skill 评估
   - `backend/services/sector_momentum.py`: tdx_l1_name 聚合板块动量，输出 fact_sector_momentum_daily
   - `backend/services/industry_context_engine.py`: 股票级行业上下文沉淀 (PIT)

3. **机构与持仓** (19 references in updater_institution.py, 20 in stock_graph_read.py)
   - `backend/services/institution_aux_read.py`: 机构持仓按行业聚合统计
   - `backend/routers/institution.py`: 机构 profile 和持仓分布展示
   - `backend/routers/screening.py`: 行业筛选与对比

4. **回测与策略** (42 + 14 + 20+ across backtest_engine, signals_v2, strategies)
   - `backend/services/backtest_engine.py`: 过滤器、评分桶接行业
   - `backend/services/stock_turtle_engine.py`: 行业均线与个股均线对标

#### 受影响的特征列清单

**Feature Panel v3 中的行业相关列** (6 列)
```
- sector_ret_5d        ← fact_sector_momentum_daily.ret_5d (via mart_stock_industry_pit)
- sector_ret_20d       ← fact_sector_momentum_daily.ret_20d
- sector_ret_60d       ← fact_sector_momentum_daily.ret_60d
- sector_excess_20d    ← fact_sector_momentum_daily.excess_20d
- sector_excess_60d    ← fact_sector_momentum_daily.excess_60d
- industry_pit_confidence  ← mart_stock_industry_pit.confidence_level (元数据)
```

**Feature Panel v5 中的行业相关列** (15 列，包括新增)
```
← v3 的 6 列 +
- sm_ret_5d, sm_ret_20d, sm_ret_60d, sm_ret_120d      ← fact_sector_momentum_daily (PIT aware)
- sm_excess_20d, sm_excess_60d                         ← 同上
- sm_price_vs_ma20, sm_price_vs_ma60                   ← 同上
- sm_vol_60d                                            ← 同上
- industry_pit_confidence (沿用)
```

### A.4 Mart 表与数据管道依赖

| Mart 表 | 依赖的 TDX 分类 | 消费模块 | 数据量 | 更新频率 |
|---------|-----------------|----------|--------|----------|
| `fact_sector_momentum_daily` | tdx_l1_name (via industry_pit) | 特征/评分/UI | 10,686 行 (2023~2026) | 日 |
| `mart_stock_industry_pit` | tdx_l1/l2/l3 + l*_name | 特征/市场感知/评分 | 39,259 | 周度 |
| `fact_stock_industry_context` | tdx_l1/l2 | UI 股票详情/评分 | 80,778 行 | 日 |
| `dim_stock_industry_context_latest` | tdx_l1/l2 | UI 快照 / 筛选 | 5,611 | 日 |
| `mart_institution_industry_stat` | industry_level/name | 机构 profile/对比 | 4,807 | 周度 |
| `fact_industry_beta_daily` | industry (投资组合风险) | 风险评估 | 2,950,666 | 日 |

### A.5 sector_momentum 计算链路（关键依赖点）

```
dim_stock_tdx_industry (stock_code → tdx_l1_name)
           ↓
      [GROUP BY tdx_l1_name]
           ↓
   成分股等权合成 K 线 (sector_close, ma20, ma60, vol, ...)
           ↓
     [MACD / 趋势判断 / 相对强度计算]
           ↓
fact_sector_momentum_daily (sector_name = tdx_l1_name, ret_5d/20d/60d, excess_20d/60d, ...)
           ↓
feature_join_v3/v5 ASOF JOIN (signal_date <= fact_sector_momentum_daily.date)
           ↓
mart_p0a_feature_label_panel_v{3,5} (sector_ret_*, sm_ret_*, ...)
```

**风险点**: 
- sector_momentum 历史数据 (2023 年起)，但 dim_stock_tdx_industry_history 只有 5 个日期快照
- 回溯时用 `current_label_fallback` (effective_from=1900-01-01)，隐含 leakage
- feature_panel v3 已文档化此 leakage ("mart_institution_profile.win_rate_60d 无 as_of_date" 等 v3.5 TODO)

---

## B. 市场感知（market_perception）模块现状

### B.1 模块架构与 7 引擎

| 引擎名 | 职责 | 入口函数 | 输出表 | 数据源 | 维度 |
|--------|------|----------|--------|--------|------|
| **RegimeEngine** | 市场风险偏好 / 态度 | `compute_regime_for_date()` | `mart_market_perception_daily` | HS300 K线/涨跌家数/限涨家数/LHB 事件 | 日 |
| **EmotionEngine** | 市场情绪（恐惧/贪婪） | `compute_emotion_for_date()` | `mart_market_perception_daily` | 成交量异常/融资余额/融券余额 | 日 |
| **ThemeLifecycleEngine** | 题材/概念热度与生命周期 | `compute_theme_lifecycle_for_date()` | `mart_market_perception_theme_daily` | fact_sector_momentum_daily (通达信 L1/L2 as theme) | 日 |
| **StyleRotationEngine** | 风格轮动（大小值/成长价值） | `compute_style_rotation_for_date()` | `mart_market_perception_daily` | 分组 K 线 (巨潮分类)，未直接用 TDX | 日 |
| **LeaderFollowerEngine** | 题材龙头-跟随关系 | `compute_leader_follower_for_date()` | `mart_market_perception_leader_follower_daily` | fact_sector_momentum_daily (topic/theme_name) | 日 |
| **UnderReactionEngine** | 资金异常 (融资/融券/持仓) | `compute_under_reaction_for_date()` | `mart_market_perception_under_reaction_daily` | fact_capital_flow_pit_daily / fact_top10_holder_period | 日 |
| **StockContextEngine** | 股票级市场感知聚合 | `compute_stock_context_for_date()` | `mart_market_perception_stock_context_daily` | regime + emotion + theme + ... | 日 |

### B.2 前端展示与功能

**Frontend 页面** (`design/v3-page-market-perception.jsx`):
- 市场状态仪表盘 (RegimeEngine) → regime_score / breadth_state / volatility_state
- 市场情绪曲线 (EmotionEngine) → emotion_score / emotion_state
- 题材排行榜 (ThemeLifecycleEngine) → theme_name / theme_score / lifecycle_stage (排序)
- 龙头-跟随 (LeaderFollowerEngine) → leader_stock_code / follower_stock_code
- 异常资金 (UnderReactionEngine) → 持仓异常个股
- 风格轮动 (StyleRotationEngine) → style_name / style_score
- 个股上下文 (StockContextEngine) → context_score 综合

**API 入口** (backend/routers 中):
```
GET /api/v3/market_perception/snapshot
GET /api/v3/market_perception/history?days=N
GET /api/v3/market_perception/emotion/snapshot
GET /api/v3/market_perception/theme/snapshot
GET /api/v3/market_perception/theme/history?days=14&top_n=5
GET /api/v3/market_perception/leader_follower/snapshot
GET /api/v3/market_perception/under_reaction/snapshot
GET /api/v3/market_perception/style/snapshot
GET /api/v3/market_perception/stock_context/snapshot
GET /api/v3/market_perception/health
```

### B.3 perception_absorbed 模块状态

**发现**: 存在两个完全平行的目录树

```
backend/services/market_perception/         (活跃，8 个引擎)
  - __init__.py (re-export 7 engines)
  - regime_engine.py
  - emotion_engine.py
  - theme_lifecycle_engine.py
  - leader_follower_engine.py
  - under_reaction_engine.py
  - style_rotation_engine.py
  - stock_context_engine.py
  - utils.py (共享辅助函数)
  - router_serialize.py (序列化)
  Total: 2,876 LOC

backend/services/perception_absorbed/       (死代码，2599 LOC)
  - __init__.py (内容同上 7 engines)
  - regime_engine.py
  - emotion_engine.py
  - theme_lifecycle_engine.py
  - leader_follower_engine.py
  - under_reaction_engine.py
  - style_rotation_engine.py
  - stock_context_engine.py
  [WARN] NO utils.py, NO router_serialize.py
  [WARN] NO 代码引用此包 (grep 0 results)
```

**结论**: `perception_absorbed` 是 **100% 死代码**，应清除。可能是早期版本备份或重构遗留。

### B.4 Theme/Concept 数据现状

**当前题材/概念来源**:
- `mart_market_perception_theme_daily` (168 rows, 2026-05-20 更新)
  - theme_name: **来自 fact_sector_momentum_daily.sector_name**
  - 即：通达信 L1 行业名称 (13 类)
  - 不是独立的"概念"，而是行业作为主线题材

**缺陷**:
1. 无专门的"概念/题材"映射表 (如 `dim_stock_concept` 或 `mart_stock_concept_pit`)
2. 无个股→概念的映射历史
3. 概念热度基于行业动量推导，而非直接数据

**脚本构建流程**:
```
backend/scripts/build_market_perception_theme_daily.py
  └─ compute_theme_lifecycle_for_range()
       └─ _load_sector_momentum()  [来自 fact_sector_momentum_daily]
       └─ _load_sector_internal_stats()  [成分股覆盖/涨跌面等]
       └─ scoring 聚合 → theme_score (范围 [-1, 1])
```

---

## C. 现有 sector/concept 数据资产盘点（read_only DuckDB）

### C.1 行业/板块/题材相关表完整清单

| 表名 | 粒度 | 行数 | 口径 | 特征 | 最新日期 |
|------|------|------|------|------|---------|
| **dim_stock_tdx_industry** | 股票 | 5,619 | 通达信 L1/L2/L3 | 当前快照 | 已更新 |
| **dim_stock_tdx_industry_history** | 股票×日期 | 56,106 | 通达信 历史 | PIT 源 (仅 5 个日期) | 2026-05-07 |
| **dim_stock_tdx_block** | 股票×板块 | 8,938 | 通达信 板块 (非行业分类) | 每股多个板块 | 已更新 |
| **dim_tdx_block_catalog** | 板块 | 55 | 通达信 板块目录 | 板块元数据 | 已更新 |
| **dim_stock_sw_industry** | 股票 | 5,805 | 申万 L1/L2/L3 | 当前快照，包含 NULL codes | 已更新 |
| **mart_stock_industry_pit** | 股票×有效期 | 39,259 | 通达信 PIT 映射 | [OK] PIT-aware, 85.7% observed | 2026-05-07 |
| **fact_sector_momentum_daily** | 板块×日期 | 10,686 | 通达信 L1 | 动量特征 (ret/excess/ma20/vol等) | 2026-05-29 |
| **fact_stock_industry_context** | 股票×日期 | 80,778 | 通达信 | 股票级行业上下文 (动量/旋转/确认) | 2026-05-29 |
| **dim_stock_industry_context_latest** | 股票 | 5,611 | 通达信 | 最新行业上下文快照 | 2026-05-29 |
| **fact_industry_beta_daily** | 股票×日期 | 2,950,666 | 通达信 (industry 列) | beta_60d / zscore | 2026-05-29 |
| **mart_institution_industry_stat** | 机构×行业 | 4,807 | 通达信 | 机构行业 skill (gain/win_rate) | 已更新 |
| **mart_market_perception_theme_daily** | 题材×日期 | 168 | 通达信 L1 as theme | theme_score / lifecycle_stage | 2026-05-20 |
| **fact_sector_predicted_ret_daily** | 板块×日期 | 8,983 | 通达信 L1 | 板块收益预测模型 | 2026-05-12 |
| **v_stock_sector_momentum_daily** | 股票×日期 | 4,551,414 | 通达信 L1 | 股票级板块动量视图 | 2026-05-29 |
| **raw_tdx_industry_file_snapshot** | 文件 | 5 | 通达信 | 文件摘要 | 2026-05-01 |

### C.2 申万行业现状

**静态存在但未激活**:
- `dim_stock_sw_industry` 表已存在，包含完整 L1/L2/L3
- **SW L1**: 31 个有名称分类 (vs TDX 13 个) → 更细粒度
- **SW L2**: 162 个分类
- **SW L3**: 396 个分类

**但无对应的**:
- 申万 PIT 映射表 (如 `mart_stock_shenwan_industry_pit`)
- 申万板块动量表 (如 `fact_shenwan_sector_momentum_daily`)
- 申万相关特征列

**可能原因**: 2026-05 体检时注明 "申万源已在 Phase 2/3 退役"

### C.3 概念/题材数据现状

**表状态总结**:
| 需求 | 现有资产 | 覆盖度 |
|------|---------|--------|
| 个股→概念映射 | [NO] 无 (`dim_stock_concept` 不存在) | 0% |
| 概念→成分股列表 | [NO] 无 | 0% |
| 概念热度历史 | [WARN] 部分 (theme 用行业代替) | 10% (仅 L1 行业) |
| 概念生命周期 | [OK] 有 (theme_lifecycle_stage) | 100% (for L1 industry) |

---

## D. 申万/东财/同花顺分类的项目痕迹

### D.1 申万分类搜索结果

**grep 搜索**（`sw_l* | shenwan | 申万`）:

| 提及场景 | 文件位置 | 状态 | 备注 |
|---------|---------|------|------|
| 列重命名历史 | `backend/services/schema_migrations.py` | 文档 | Phase 2 迁移记录："sw_level1 → tdx_l1" 等 |
| 弃用备注 | `backend/routers/updater_institution.py` | 注释 | "Phase 2 申万源退役后... 改读 fact_institution_event.sw_level 字段或 dim_stock_tdx_industry" |
| 旧表清单 | `backend/tests/test_event_industry_*.py` | 弃用测试 | "原 fact_institution_event_industry_snapshot + sw_level* 已退役" |
| 股票图标 | `backend/services/stock_graph_read.py` (line ~150) | 活代码 | "— 行业 (industry, 申万一级)" 备注，但实现用 tdx |
| PIT 审计 | `backend/scripts/audit_panel_leakage.py` | 审计工具 | `SUSPECT_MAPPING_COLS = {..., "sw_l1", "sw_l2", ...}` 列入检查范围 |
| Concept 占位 | `backend/scripts/audit_panel_leakage.py` | 扩展点 | "extensible: ("mart_stock_concept_pit", "confidence_level", "current_label_fallback")" |
| API 文档 | `backend/services/api_schemas.py` (line ~300) | 清理中 | "# SW industry parsing classes removed (Phase η++ 2026-05-12)" |

### D.2 东财/同花顺分类痕迹

**搜索结果**: [NO] 无项目内引用
- 无 `dc_member | index_member | ths_member | ths_index | index_classify` 匹配

### D.3 结论

申万分类：
- [OK] 数据资产完整 (`dim_stock_sw_industry` 有 31 L1 + 162 L2 + 396 L3)
- [OK] 代码历史存在 (Phase 2 迁移记录)
- [NO] 已正式退役，无生产引用
- [WARN] 但 feature audit 列入风险检查范围

东财/同花顺：
- 无痕迹，从未在项目中使用

---

## E. 核心发现与决策要点

### E.1 切换通达信→申万的影响面评估

#### 直接受影响的组件数量

| 组件类型 | 数量 | 风险等级 |
|---------|------|---------|
| **特征列** (feature_join v3/v4/v5) | 6-15 列 | [P0] 关键 |
| **Mart 表** | 6 个 (sector_momentum/industry_pit/context 等) | [P0] 关键 |
| **策略/评分模块** | 8+ (scoring/backtest/signals/strategies) | [P0] 关键 |
| **前端模块** | 3 (market_perception/detail page/screening) | [P1] 中等 |
| **测试** | 20+ 测试文件 | [P1] 中等 |

#### 工作量估算

**一级工作** (核心数据管道，2-3 周):
1. 构建 `mart_stock_shenwan_industry_pit` (SW L1/L2 → PIT 映射，仿 mart_stock_industry_pit)
   - 需要 `dim_stock_sw_industry_history` (当前无)
   - 或接受 fallback (current_label only)
2. 重建 `fact_shenwan_sector_momentum_daily` (SW L1 聚合动量，仿 fact_sector_momentum_daily)
3. 修改 feature_join 中的 industry_pit ASOF 逻辑 (切换 table 参考)

**二级工作** (评分/策略适配，1-2 周):
4. 更新 `sector_momentum.py` / `industry_context_engine.py` 的聚合逻辑 (L1 → L2 可选)
5. 更新 `scoring.py` 中的行业 skill 计算
6. 修改 `stock_graph_read.py` 等关联模块

**三级工作** (回测/测试，1 周):
7. 回测链路验证 (backtest_engine / paper_sim)
8. 更新 20+ 单测用例

**总体估算**: 4-6 周，其中 1-2 周关键路径 (feature_panel + sector_momentum 链路)

### E.2 现有数据/表可用性评估

| 用途 | 推荐方案 | 可行性 |
|------|---------|--------|
| **板块轮动分析** | fact_sector_momentum_daily (当前 TDX L1) + theme_lifecycle_stage | [OK][OK] 即插即用 |
| **个股板块标签** | mart_stock_industry_pit (current_label_fallback 可接受，已 95% 覆盖) | [OK][OK] 即插即用 |
| **板块强度排名** | mart_sector_momentum.momentum_score (日更) | [OK][OK] 即插即用 |
| **产业链扩散** | 无直接表，需自建 (stock_graph 支撑但无链路) | [NO] 需新开发 |
| **概念热度/生命周期** | mart_market_perception_theme_daily (仅 13 个 TDX L1) | [WARN] 覆盖不足，需扩展 |

### E.3 市场感知模块评价

**优点**:
- 7 引擎架构清晰，各自职责分明
- PIT-aware (emotion 用融资余额历史等)
- API 完整，前端集成良好
- 每日自动更新

**缺点**:
- 题材(theme) 仅用行业 L1 代替，不够细粒度
- 无个股→概念独立映射
- `perception_absorbed` 死树应清理
- LeaderFollower 与 UnderReaction 未被充分利用

**建议**:
1. **立即清理**: 删除 `backend/services/perception_absorbed/` (整个目录，2599 LOC 死代码)
2. **优化 theme**: 待 `dim_stock_concept_pit` 就位后，从行业 L1 扩展到概念、题材
3. **增强输出**: 加入板块轮动排行、行业动能信号等前端 widget

### E.4 区分度评估（用哪些口径）

**推荐优先级**:

| 口径 | 层级 | 覆盖度 | 区分度 | 推荐用途 |
|------|------|--------|--------|----------|
| 通达信 L1 | 一级 (13 类) | [OK][OK] 100% | [WARN] 较粗 | 主要筛选、全市场分析 |
| 通达信 L2 | 二级 (56 类) | [OK] 95% | [OK] 中等 | **推荐**: 板块轮动、风险敞口 |
| 申万 L1 | 一级 (31 类) | [OK][OK] 100% | [OK] 较细 | **备选**: 资本市场标准口径 |
| 申万 L2 | 二级 (162 类) | [OK] 95% | [OK][OK] 很细 | 深度研究、产业链分析 |
| 概念/题材 | 动态 | [NO] 无 |  最细 | **待实现**: 市场热点跟踪 |

---

## F. 立即可执行的建议

### F.1 数据层面

[OK] **已可用** (无需改动):
- 用 `mart_stock_industry_pit` 给个股贴 L1/L2 标签（接受 14.3% fallback）
- 基于 `fact_sector_momentum_daily` 做板块轮动分析（13 个 TDX L1 级别）
- 基于 `mart_market_perception_theme_daily` 获取题材热度（每日更新）

[WARN] **需补齐** (1-2 周):
- 构建个股→概念映射表 (爬取通达信/巨潮/东财，或人工标签)
- 实现 dim_stock_tdx_industry_history 的完整历史（当前仅 5 个快照）
- 若要申万，需先补 `dim_stock_sw_industry_history` + PIT 化

 **不推荐**:
- 同时维护通达信+申万（维护成本高，回测不可比）
- 用 `dim_stock_tdx_block` (8,938 rows, 通达信板块非行业分类，杂乱)

### F.2 代码层面

[OK] **立即清理** (0.5 小时):
```bash
rm -rf backend/services/perception_absorbed/  # 2599 LOC 死代码
git add -A && git commit -m "cleanup: remove dead perception_absorbed module"
```

[OK] **可选优化** (仅在扩展时):
- 若实现申万，复制 industry_pit.py → shenwan_pit.py 框架
- 若实现概念，新增 concept_pit.py (仿 industry_pit.py 设计)
- 在 audit_panel_leakage.py 中扩展概念检查规则

---

## G. 附录：关键文件索引

### 通达信行业分类核心
- `backend/services/industry.py` — 行业辅助函数库
- `backend/services/industry_pit.py` — PIT 化逻辑
- `backend/services/tdx_industry_client.py` — 数据同步
- `backend/services/tdx_industry_names.py` — 名称映射
- `backend/services/sector_momentum.py` — 板块动量计算

### 市场感知核心
- `backend/services/market_perception/` — 7 引擎 (活跃)
- `backend/services/perception_absorbed/` — **待删除**
- `design/v3-page-market-perception.jsx` — 前端展示

### 特征工程
- `backend/services/labels/feature_join_v3.py` — sector_ret 等 6 列来源
- `backend/services/labels/feature_join_v5.py` — sm_ret 等 9 列来源
- `backend/scripts/build_feature_panel_duck.py` — 特征 panel 构建脚本

### 影响面分析
- `backend/services/scoring.py` (42 引用) — 评分中行业 skill
- `backend/services/backtest_engine.py` (20+) — 回测中的行业过滤
- `backend/routers/screening.py` (20+) — 筛选中的行业分组

---

**报告完成时间**: 2026-06-11  
**数据基准日**: 2026-05-29 (最新 DuckDB 快照)  
**建议处理周期**: 2-3 周内清理 perception_absorbed，4-6 周内完成申万切换（若决策采纳）


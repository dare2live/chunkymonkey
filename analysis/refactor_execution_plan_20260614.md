## 重构执行方案 (Phase A 底座收口) — 2026-06-14

> 状态: live (执行中)。owner=本文件 (执行清单) + `docs/MASTER_TOPLEVEL_DESIGN.md` §9-10 (骨架)。
> 缘起: 用户指令"daily_update 保持手动 + 其余数据底座问题制定系统方案并开始"。本文件 = Phase A
> 的可执行 checklist + 决策记录 + 战略重排理由, 防压缩丢失。

### 战略重排 (必读, 决定 B-F 排序)
两份独立结论同指一点: **瓶颈在 base-edge, 不在架构、不在多抓数据**。
- `multidim_strategy_architecture_20260613`: cube 立法完成但**实例化 BLOCK** — 板块维实测失效 (p=0.157-0.803, OOS 本身为负)。
- `tushare_alpha_potential_menu_20260614`: 137 接口**无一 high-edge** (诚实)。
→ 不先建 cube 架构、不先为没证明的数据建保鲜管道 (architect rule6)。最该先答 = "edge 存不存在"。

### 用户决策 (2026-06-14)
1. 执行起点 = **先 A 底座收口 (把好关)**, 再 alpha。
2. 退役方式 = **直接删 (git 留历史)** — 文档 git rm; bloat 数据表 DROP / 孤儿 .duckdb 文件删。
3. routers = **一起退役 (gate 全绿)** — 删孤儿 router + 改 main.py。(已核: 无 spawned worktree 冲突。)

### Phase A 执行清单 (按风险从低到高, 高频 commit)
| # | 步骤 | gate | 风险 | 状态 |
|---|---|---|---|---|
| A2 | 3 表加 retention (dim_stock_tdx_industry_history / raw_profit_forecast_snapshot_daily / raw_tdx_industry_file_snapshot) | C3 绿 | 纯 config, dry_run, 0 数据动 | 待 |
| A1 | 重建 `scripts/daily_update.sh` 新手动流 (留 preflight+sync+L1k macd+retention+audit; 删 Step4-8 model/paper_sim/champion + 4 L2 builder + missing panel) | C1 绿 | 数据管线, 须逐步验 | 待 |
| A3a | 退役孤儿 config (model_search.yaml / feature_registry.yaml / champion_registry.yaml / market_perception.yaml — L2/L3 wiped 的死配置) git rm | C2 部分 | config 可逆 | 待 |
| A3d | 修 gate: C2 排除 __pycache__/binary 匹配 (mythos §13 派生工具缺陷) | gate 质量 | 低 | 待 |
| A1b | 切 ops_manual_run / launchd_job_wrapper 对新 flow 的引用对齐 | — | 低 | 待 |
| A3b | 退役孤儿 routers (v3_market_perception/recommendation/institution/updater_*/v3_meta/v3_views/v3_perception_legacy/screening) + 改 main.py | C2 大部分 | **改 app, CI 风险** | 待 |
| A3c | schema_versions.py 删 wiped 表 DDL (防重建循环, 27 处) | C2 收尾 | 中 (schema-init) | 待 |
| A4 | 8 散落 service ensure_tables() 包 layer-gate (assert_active_layer) | 防 alpha158 类循环 | 中 | 待 |
| A5 | bloat 回收: phase5_predictions 57M .duckdb 删 + ARCHIVE_FIRST 10 表 DROP (停 fact_stock_attention_snapshot write 路径后) | 省盘 | **破坏性数据** | 待 |
| A6 | 文档收口: git rm 5 已偏离 analysis (first_principles_diagnosis_20260517 / chunkymonkey_architecture_audit_20260517 / multi_wave_strategy_300616 / system_architecture_audit_20260521 / implementation_plan_20260611) + 2 docs (zhushenglang_hunter_research_log_20260528 / architecture_reform_context) ; CLAUDE.md 瘦身 ~70 行迁 skill | docs<=10 | 控制面, 用户审 | 待 |
| Z | 全 gate 绿 + moth assert + doctor + commit/push + 更新 goal/INDEX/HANDOFF | 验收 | — | 待 |

### Phase B-F (Phase A 后, owner=MASTER §10)
- B 证 base-edge: B1 per-stage L0 IC (reuse oos_ic+technical_stage) · B2 Alpha158 干净重算 (>+0.064?)
- C 可靠性阶梯+Tier-2: Gate2 MC截面置换 · Tier-2 backtest 引擎 (reset 删, 重建) · Gate4 PBO 恢复 · Gate5 块自助
- D 逐数据 alpha 验证 (cashflow/block_trade/资金流/筹码, 超 +0.064 才入)
- E 策略立方体逐维解锁 (BLOCK 直到 B/D 出正 edge)
- F 含成本 paper_sim → KPI

### 执行中发现 (2026-06-14, 改写 A1/A3b 顺序)
1. **gate 双精度 bug 已修 (A3d done)**: 238 含 ~59 噪声 = __pycache__ 二进制 + substring 假阳性
   (wiped `fact_shareholder_plan` 裸 grep 误匹配活表 `fact_shareholder_plan_tdx_f10` L1/30769行)。
   修后真实 C2=179; 删 2 真孤儿 config (model_search/champion_registry)→**149**。
2. **config 不全是孤儿 (agent 粗标纠偏)**: `feature_registry.yaml`(被 4 live service load)+
   `market_perception.yaml`(被 regime_engine+schema_marts+schema_migrations+stock_graph_read+main.py 用)
   **不可删**; feature_registry 的 8 处"stale"全是 substring 假阳性 (修 gate 后消失)。
3. **A1↔A3b 深度纠缠 (关键)**: daily_update.sh 对 routers 只有 **2 个函数**耦合 —
   `routers.updater._step_sync_lhb`(Step2d)+`_step_sync_industry`(Step2g); 其余 sync 全走干净
   `services.*`。→ 退役 routers 前必须先把这 2 helper 抽进 service, 否则断 sync 管线 (真金白银)。
   **refined 子序列** (动 live 管线+app boot, 不 big-bang):
   - A3b-0 抽 `_step_sync_lhb`/`_step_sync_industry` → services/ (核无 FastAPI 依赖), daily_update 改调 service
   - A1   重写 daily_update.sh (调 service; 删 Step0/4-8 + missing L2 builder; 加 retention+report) → C1 绿
   - A3b  退役死 serving routers (updater_*/institution/recommendation/screening/v3_meta/v3_views/
          v3_market_perception/v3_perception_legacy) + main.py 注册 + updater.py serving 部分
   - A3c  schema_versions.py 删 wiped DDL(23) + market_perception/storage_retention/杂 config 外科清
   每步: app `import main` 冒烟 + CI/moth 验, red→green。

### daily_update.sh 重建底本 (A1)
幸存脚本 (6): update_watermark_sla / build_price_kline_tdxhub / sync_hs300_benchmark_kline /
ingest_profit_forecast_snapshot / refresh_source_watermarks / build_macd_state_history。
幸存内联 sync (heredoc): xdxr / LHB / risk_factors / institution_survey / tdx_industry / external_attention / sync_runner --all-due --drain。
删: Step0 experiment contract (走 experiment_jobs) · Step3 missing panel/label/signal builder (L2 wiped) ·
Step2 4 个 missing builder (sector_momentum/capital_flow/sniper/institution_score, L2) · Step4-8 model/paper_sim/gate/champion (L3/L4 wiped)。
新增: retention enforce step (storage_retention dry_run) + 精简 SLA/health report + notification。

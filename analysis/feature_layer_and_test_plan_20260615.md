## L2 特征层落地 + 多因子测试计划 (2026-06-15)

> 状态: live。owner=本文件。缘起: 用户"抓回来数据按分层存(避写锁/解耦/可清), 应用同分层; 看 L2-bypass 怎么产生
> 的、移到正确位置、从流程杜绝; 制定测试计划探索各种组合含 Optuna+Modal, 做好万全准备"。
> 上承 data_layers.yaml (8层) + db_management_design (DB分区写锁边界) + tushare_migration_program (M0-M4)。

### 1. 根因: L2-bypass 怎么产生的 (要从流程杜绝的问题)

| 因 | 说明 |
|---|---|
| L2 被 wipe | 2026-06-14 reset wipe L2_feature, feature_store.duckdb 根本没建 (空) |
| 内联算因子 | Phase D 实验 (mf_trend/momentum/quality) 内联算因子**直读 L0** (tushare_raw+market), 因 L2 空=最省力路径 |
| 框架只声明不执法 | data_layers 声明每表 layer, 但**没强制"实验消费 L2 不读 L0 raw 算因子"** → 声明式 layering 在消费侧无执法 |

后果 (正是分层要避免的 3 件事):
- **写锁**: 实验读 L0 tushare_raw 算因子 -> 撞 moneyflow 回补的写锁 (单writer), 拉数据与探索互堵
- **耦合**: 实验↔L0 raw 直接耦合, 因子逻辑散在各实验, 不可复用
- **不可清**: 因子没物化进 L2, 无法干净 wipe/重建; 无效实验数据散落

### 2. 修复: 移到正确位置 + 从流程杜碎

**移 (build L2)**: 建 `feature_store.duckdb` (独立库=写锁隔离) + `fact_feature_panel` (PIT: code×date + 因子列, 分模块);
  `build_feature_panel.py` 按模块 builder 读 L0/L1/market → 物化因子列。模块解耦, 各独立, wipeable。

**杜绝 (process gate, 从流程)**:
- moth `feature-layer-no-l0-bypass`: `experiment_*.py` 算 IC/backtest 的, 不许直接 `duck_connect('data/tushare_raw')` 算因子
  (应读 feature_store L2 panel); 命中 = FAIL。
- `check_strategy_validation_integrity` 加 `feature_from_l2` 检查。
- data_layers 框架补"消费侧 layering"执法 (L4 实验读 L2, 不读 L0 raw 算特征)。

### 3. L2 模块分解 (因子按域, 各独立 builder, 单向 L0→L2)

| 模块 | 因子列 | 源 (L0/L1/market) | PIT 锚 |
|---|---|---|---|
| 技术 technical | mom_20/60/120, reversal_20, vol_20 | market price_kline_qfq_tushare | bars[:t+1] |
| 资金流 moneyflow | mf_net_trend_20/60, 北向 hk_trend | L0 raw_tushare_moneyflow / hk_hold | 盘后 t-1 |
| 质量 quality | roe_dt_asof, netprofit_margin_asof | L0 raw_tushare_fina_indicator | ann_date<=t |
| 筹码 chip | winner_rate, cost_pctile | L0 raw_tushare_cyq_perf (待质量修) | 盘后 t-1 |
| 估值 valuation | pe_pctile, pb_pctile, turnover | L0 raw_tushare_daily_basic 自算分位 | t (盘后) |

### 4. 测试计划: 多因子组合探索 (Optuna + Modal, 含成本 R1 裁决)

**目标**: 主升浪猎手 + 各公式 × 多因子组合, 找含成本 OOS 绝对收益最优 (R1, 非 IC); 2019/2020+ 多regime; train/holdout disjoint; DSR 去偏。

**搜索空间** (config 驱动, plan_validator 非空门): 因子子集选择 (哪些模块组合) × 因子权重 × regime 门 (MA/阈值) × top_k × rebal × sizing × horizon。

**Optuna**: TPE 搜上述; 目标=含成本 execution-aware backtest 年化+max_dd 惩罚 (复用 phaseD_signal_eval/execbacktest); 读 L2 panel (不读 L0 raw → 无锁)。

**Modal** (已登录验证 dare2live): 组合空间大 (因子子集 2^5 × 权重 × 参数 = 数千组合 × 多regime backtest) → Modal worker 并行跑 (本地单机串行太慢)。每 trial = 1 backtest, Modal map 并行; 结果聚合回 L4 experiment_store。modal token 已 set (~/.modal.toml profile dare2live), smoke add.remote(2,3)=5 端到端通。

**裁决**: 每组合 tradability_verdict + kpi_verdict + trailing 多窗 (近期是否衰减); DSR(n_trials) 防过拟合 (P3 实证 anti-overfit 框架挡 +8.94% 幻觉)。confirmed_by_owner 须含成本绝对收益 (C-R1)。

### 5. 执行序 (万全准备)
1. [DONE] Modal token set + 端到端验证
2. build feature_store L2 + 模块 builder (移因子到 L2, 2019/2020+) — 写锁隔离
3. process gate (moth + integrity gate) 杜绝 L0-bypass
4. 多因子 Optuna 搜索 runner (读 L2, R1 目标, Modal map 并行) + search space yaml
5. 主升浪 + 公式 × 多因子组合探索 RUN (Modal); 裁决 + trailing + DSR

# ChunkyMonkey 架构重构 + 业务交付 — 落地执行计划
Date: 2026-05-26 (Claude + Codex 讨论确认版)

## 执行顺序 (按依赖关系)

```
M0 300616五公式 → M4 前端公式视图 → M5 GCP全量跑批
                                   ↗
M3 data_audit集成 daily_update ──→
M6 data_audit 配置化 ──→

(独立) S1-S4 技术债 (God module拆/YAML合并)
(独立) M2 dim_all_ever_listed 清理
```

## M0: 300616 五公式优化 (1 天, 最高优先)

```
方法: 上帝视角 → 全A股验证 → 去leakage调参
代码: backend/services/bc_absorbed/derived_formulas.py
数据: 300616 三波 W1(12-29→02-14) W3(04-20→05-20)
关键特征: 量比 0.5x→1.4x 回升 + below MA120 + pos250低
已有: 上帝视角35笔+21265% + T时刻特征分析 + gs_raw_buy在理想日命中

步骤:
  1. 用未来函数写300616精确买卖 (已有god_view_signals基础)
  2. 全A股跑看能否选出300616 (universe 4969已修OK, 300616已入选)
  3. 逐步去掉未来函数, 用VWAP+成本验证
  4. 五公式各自验证: 信号日 vs 理想日偏差 ≤ 1天

验证: 300616三波每波买入偏差≤1天, 含成本收益>0
Owner: Claude (主) + Codex (review)
```

## M3: data_audit 集成到 daily_update (0.25 天)

```
文件: scripts/daily_update.sh L341-362
改法: Step 3 label/panel rebuild 后加:
  PYTHONPATH=backend python -c "
  from services.data_audit import run_post_sync_audit
  result = run_post_sync_audit('step3_label_panel', strict=False)
  print(result.get('overall', 'unknown'))
  "
验证: bash scripts/daily_update.sh --dry 日志含 data_audit
Owner: Claude
```

## M4: 前端公式视图 (0.5 天)

```
后端:
  backend/routers/v3_meta.py — /api/v3/formulas 接入 bc_absorbed FORMULA_DEFINITIONS
  返回 59 个公式 (id + display_name + description + type)

前端:
  v3/v3-page-formula-view.jsx — 渲染公式列表

验证: curl /api/v3/formulas | jq length >= 59
      浏览器打开公式视图能看到公式
Owner: Claude (后端) + Codex (前端 review)
```

## M5: GCP 全量 Optuna 跑批 (0.5-1 天)

```
脚本: gcp/gcp_formula_optuna_batch.sh (不是 gcp_stability_retrain.sh)
前提: grill_stamp + preflight_gcp_launch 7/7 PASS
参数: 全量 4541+ stocks, tiered trials (100/60/30/1), walk-forward 70/30
成本: ~$13 (34h), 预算 $35 够

验证: 34 公式全 complete + score > 0 + 0 FAIL
Owner: Claude (启动+监控)
```

## M6: data_audit 配置化 (0.5 天, SHOULD)

```
现状: 检查项硬编码在 data_audit.py
目标: 检查项从 YAML 读 (config/data_audit_rules.yaml)
改法: 新建 YAML, data_audit.py 读 YAML 定义检查项
验证: 改 YAML 参数不改代码, 审计行为变化
Owner: Codex
```

## M2: dim_all_ever_listed 清理 (0.5 天, SHOULD)

```
现状: 12 文件引用, 但无 runtime 阻塞 (get_active_universe 已不依赖)
改法: 逐个替换为 K线查询或 dim_listing_status, 审计脚本保留
验证: grep dim_all_ever_listed 只剩 schema/audit/build_dim 文件
Owner: Codex
```

## S1-S4: 技术债 (独立, 不阻塞业务)

| Task | 估时 | 内容 |
|---|---|---|
| S1 updater.py 拆分 | 2d | 5136行→5模块 |
| S2 data_quality.py 拆分 | 2d | 4276行→3模块 |
| S3 compute.py 拆分 | 3d | 3303行→3模块 |
| S4 paper_sim YAML 合并 | 1.5d | 16个→基础+覆盖 |

## 总时间

| 类别 | 估时 | 状态 |
|---|---|---|
| M0 300616 公式 | 1d | 待做 |
| M3 data_audit 集成 | 0.25d | 待做 |
| M4 前端公式视图 | 0.5d | 待做 |
| M5 GCP 全量跑批 | 0.5-1d | 待做 (含等待时间) |
| M6 data_audit 配置化 | 0.5d | 待做 |
| M2 dim清理 | 0.5d | 待做 |
| **MUST 合计** | **3-3.5d** | |
| S1-S4 技术债 | 8.5d | 后续排 |

## 已完成 (本 session)

- get_active_universe → K线优先 ✓
- LIMIT_THRESHOLD → YAML ✓
- tx_cost → config ✓
- dim_all_ever_listed 573误标 → 修 ✓
- universe_rules.yaml ✓
- check_universe_filter lint CLEAN ✓
- PROJECT_CONSTITUTION 9条 ✓
- engineering-discipline skill ✓
- data_audit.py 7项检查 ✓
- 全部审计工具体系设计 ✓

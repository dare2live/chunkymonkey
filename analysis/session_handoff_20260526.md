# Session Handoff — 2026-05-26 公式工厂 + 分层架构 + GCP

## 下一个 session 的 next action

**P0: 300616 五公式用上帝视角重做**

方法:
1. 用未来函数精确抓 300616 三波买卖点
2. 全 A 股跑看能否选出 300616
3. 去掉未来函数, 调参争取最大胜率

五公式: pullback_doji / wave1_base_breakout / wave2_pullback_buy / wave3_rapid_doji / full_rally_rider
代码在: `backend/services/bc_absorbed/derived_formulas.py`
验证数据: `analysis/codex_multi_wave_300616.md`
关键特征: 量比从 0.5x 回升到 1.4x 是起涨前兆

## 今日完成

| 类别 | 项目 |
|---|---|
| 公式整改 Phase 1-3 | 6 core YAML + 49 bank 接入 + SmartMoney adapter 接通 (10 公式可喂外部数据) |
| 审计工具 | preflight 8 + plan_validator 8 + data_audit 7 + grill gate + preflight_gcp 7 |
| Bug 修复 | multi_tf PIT / macd_divergence / mfi roll / dividend future / LHB executescript |
| 四层架构 | profiler / ranker / pool / picks + config |
| GCP | 跑了 2 轮都有问题 (29 无 search space + 200 只抽样偏差), 已修但未重跑 |
| 数据 sync | K 线/risk/sector/capital_flow/exec/active/industry/LHB 到 05-26 |
| Codex review | 4 findings 全修 (OOS 选参 / shell 展开 / code scan / PIT 异常) |
| Skills 安装 | 8 个 mattpocock skills (grill/diagnose/tdd/to-issues/handoff 等) |

## 待续

| 优先级 | 任务 |
|---|---|
| P0 | 300616 五公式上帝视角→去未来函数优化 |
| P0 | Bank 49 按 Codex 评估整理 (11 KEEP / 25 REWORK / 13 DROP) |
| P1 | 前端公式视图接 bc_absorbed (当前返回 0 个公式) |
| P1 | 前端更新按钮检查 + 增量更新缓存 |
| P1 | GCP 全量 4541 stocks 重跑 (grill → preflight → 启动) |
| P1 | dim_stock_stage_latest / quality_latest 更新路径修复 |
| P1 | data_audit 集成到 daily_update.sh |
| P2 | REWORK 25 个 bank 公式补参数 |

## 教训

| 教训 | 根因 |
|---|---|
| 29/34 公式白跑 GCP | 没验证 search space 非空 → plan_validator 已修 |
| 200 只全深主板 | max_stocks=200 按 code 排序 → 板块覆盖检查已加 |
| pullback_doji -999 | limit_up_pct 不按板块 → 改为 per-stock 自动取 |
| 数据 sync 静默失败 | daily_update 从未定时跑 + 多表不在 sync 步骤 |
| 衍生公式抓不住 300616 | 拍参数不行, 应该先上帝视角再去未来函数 |

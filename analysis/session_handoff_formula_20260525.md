# Session Handoff — 回调十字星 + 公式工厂整改 2026-05-25

## 下一个 session 的 next action

**Phase 1 开始**: GS 系列 (gs_raw_buy + gs_pullback_confirm) 接入 preflight + 配置化

具体步骤:
1. `git stash list` 确认无残留 stash
2. 读 `analysis/audit_bestchoice_preflight_20260525.md` 了解审计结果
3. 改 gs_raw_buy: 在调用方加 `enforce_backtest_preflight()` + 从 YAML 读参数
4. 改 gs_pullback_confirm: 同上
5. 跑全量回测验证 7/7 PASS
6. Codex review → commit → push
7. 继续 Phase 2 (ma_base_breakout + activity_breakout)

## 已完成

### 回调十字星公式
- 从通达信公式解析 → Python 实现 → 注册 formula_engine 第 6 个公式
- 原图股票识别: 300616 (创业板), 三波精确匹配
- 回测: 11,089 信号 (universe 过滤前), S 级 78.9% 胜率 +8.09% 3d
- Optuna R1: 4×100 trials, offset=2 优于 offset=1 (收益 +6.49%)
- PIT audit: PASS (核心干净, verified 为事后标签)
- 真实成本验证: 佣金+印花税+滑点+涨停封板, 数字基本不变

### 审计基础设施
- backtest_preflight.py: 7 维 fail-closed gate (与交易日历同强度)
- load_clean_backtest_data(): 一行统一入口
- universe.py: get_limit_up_pct 按板块 + get_active_universe
- exits.py: is_limit_up/down_day 加 limit_pct 参数
- BestChoice 全公式审计: 0/35 通过, P0 整改清单已出

### Calendar gate
- 发现 sync 跨 15:05 阈值导致覆盖不一致
- 修复: filter_kline_rows_by_calendar 加 max_date_override
- build_price_kline_tdxhub.py 改动在工作区但未 commit (L8 hook 阻断, 需单独处理)

### K 线数据
- 全量同步到 2026-05-25
- 卫星数据 (alpha158/LHB/risk/sector/capital_flow) 全部更新

## 待续清单

| 优先级 | 任务 | 位置 |
|---|---|---|
| P0 | Phase 1: GS 系列接入 preflight | bc_absorbed/formula_engine.py |
| P0 | Phase 2: 均线+活跃度接入 | 同上 |
| P0 | Phase 3: 巨量+54 bank + GCP Optuna | 同上 + GCP $1.88 |
| P1 | 回调十字星公式 v2 重写 | 基于 300616 三波: breakout 连续大涨取第一天 |
| P1 | Optuna R2 综合寻优 200 trials | optuna_pullback_doji.py |
| P1 | 每日选股器 | 输出明日买入列表 + 历史同模式推荐 |
| P2 | calendar gate commit | build_price_kline_tdxhub.py (L8 hook 冲突) |
| P2 | Codex review 补做 | 之前 skipped 的 5 次 commit |
| P2 | 全局硬编码清理 | LIMIT_THRESHOLD=0.097 等 |

## 用户关键指令

- "全局不硬编码, 用模块/数据表/配置文件"
- "审计工具做成强规则, 像交易日历那样, 前置在其他动作之前"
- "BestChoice 全部公式按十字星标准重做"
- "四个公式各自独立测试, 最后才整合"
- "codex review 不要 bypass"
- "leakage 和未来函数放在审计工具里做"
- "按板块分涨停阈值, 不用统一值"

## 关键文件索引

| 文件 | 用途 |
|---|---|
| goal.md §2026-05-25 | 公式工厂整改计划 |
| backend/services/backtest_preflight.py | 7 维审计 gate |
| backend/services/universe.py | universe + 板块涨停 |
| backend/services/bc_absorbed/formula_engine.py | 6 个注册公式 |
| backend/scripts/formula_limit_up_pullback.py | 回调十字星主脚本 |
| backend/scripts/optuna_pullback_doji.py | Optuna 寻优 |
| backend/config/formula_limit_up_pullback.yaml | 公式 YAML 模板 |
| analysis/audit_bestchoice_preflight_20260525.md | BestChoice 审计报告 |
| analysis/optuna_pullback_doji_*.json | Optuna R1 结果 |
| analysis/formula_limit_up_pullback_full_v2_*.json | 全量回测结果 |

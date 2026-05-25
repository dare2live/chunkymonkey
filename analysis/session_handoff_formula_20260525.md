# Session Handoff — 回调十字星公式开发 2026-05-25

## 当前状态

### 已完成
1. **裸公式 + 回测** — 11K 信号, S 级 78.9% 胜率 +8.09% (universe 过滤前)
2. **PIT audit** — 核心 PIT 干净, verified 为事后标签
3. **Optuna R1 四轮** — offset=2 优于 offset=1 (收益 +6.49%)
4. **原图股票识别** — 300616 (创业板), 三波精确匹配
5. **Universe 强规则** — get_active_universe() 排除 ST/退市/北交所
6. **板块涨停适配** — get_limit_up_pct() 主板10%/创业板20%/科创板20%
7. **回测前置审计** — backtest_preflight.py, 4 维审计 fail-closed gate
8. **Calendar gate 修复** — batch_max_date 锁定 (代码改了, 未 commit 到 build_price_kline_tdxhub)
9. **K 线 + 卫星数据同步** — 全量到 5/25

### 待 commit (在 git 工作区)
- `backend/services/backtest_preflight.py` (新) — 回测前置审计
- `backend/services/paper_engine/exits.py` — limit_pct 参数
- `backend/scripts/optuna_pullback_doji.py` — 按板块 limit_pct
- `PROJECT_INDEX.md` — 更新

等 Codex review (task bc8v7zejs) 回来后 commit.

### 待续
1. **Codex 设计讨论** — 四公式框架 (task bqybcfmku 可能超时)
2. **公式 v2 重写** — 基于 300616 三波: breakout 检测改为连续大涨取第一天, 回调评分机制
3. **Optuna R2** — 综合寻优 200 trials, 缩窄空间
4. **硬编码全局清理** — LIMIT_THRESHOLD=0.097 等全部改为读配置/数据表
5. **每日选股器** — 输出明日买入列表 + 历史同模式推荐
6. **四公式独立回测** — F1 底部蓄势 / F2 横盘突破 / F3 回调十字星 / F4 卖出

## 关键文件
| 文件 | 用途 |
|---|---|
| backend/scripts/formula_limit_up_pullback.py | 回调十字星公式主脚本 |
| backend/scripts/optuna_pullback_doji.py | Optuna 寻优 |
| backend/config/formula_limit_up_pullback.yaml | 公式参数配置 |
| backend/services/backtest_preflight.py | 回测前置审计 gate |
| backend/services/universe.py | universe + 板块涨停阈值 |
| analysis/formula_limit_up_pullback_full_v2_*.json | 回测结果 |
| analysis/optuna_pullback_doji_*.json | Optuna 结果 |

## 用户关键指令
- "全局不硬编码, 用模块/数据表/配置文件"
- "审计工具做成强规则, 像交易日历那样"
- "BestChoice 全部公式也要适配板块涨停阈值"
- "四个公式各自独立测试, 最后才整合"
- "codex review 不要 bypass"

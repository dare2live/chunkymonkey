"""Paper Sim v2 — Phase ψ 自动模拟实盘.

设计原则 (用户原话 + CLAUDE.md):
  - 100 万本金, 最多 5 只持仓, 自动买/卖/换股, 不人工干预.
  - 时间维度走每股 Optuna optimal_hp, 不全局硬编码.
  - Swap 触发 = 达成率 (盈利进度 / 时间进度) < 0.5, 且候选能补上落后.
  - 所有 hyperparam 在 backend/config/paper_sim_config.yaml 集中, 调参 ≠ 改代码.
  - 走 walk-forward 历史回放 + 6 类 20 KPI 验证 (含 anti-churn + ablation +
    sensitivity + reality-check), 通过才上 live.

模块组织:
  config.py       — 加载 yaml + 校验
  ddl.py          — paper_sim 专用 4 张表 (跟旧 paper_engine mart_paper_nav 等分离)
  tx_cost.py      — 印花/佣金/过户/滑点 计算
  selector.py     — 每日候选选股 + 流动性过滤
  sizer.py        — Wilson lower-bound + Kelly fraction 仓位
  exit_rules.py   — target/stop/trailing/hp/stage_deterioration 触发
  swap_rules.py   — 达成率 + 候选能补差 + STRONG_BUY 门槛
  driver.py       — 单日主循环 (退出 → swap 评估 → 入新 → NAV)
  reporter.py     — NAV 写库 + KPI 计算 + 决策树阻断
"""

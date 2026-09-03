"""daily_update 数据管线 — 获取/清洗/加工/存储 各司其职 (2026-06-23 重设计)。

旧 scripts/daily_update.sh (469 行 bash 套 python heredoc) 重组为四阶段 Python 管线:
  preflight (gate) → acquire (获取→L0) → clean (清洗 L0→L1) → process (加工 L1→L2) → store (存储/治理)

每阶段一个模块, 单一职责, 可单测; bash 退化为只设 env + 调 `python -m services.pipeline.run`。
逻辑零改 (faithful port), 只重组结构。owner=本包。
"""

"""Signal Evolution Framework (SEF).

6 层架构（见 docs/SEF_MASTER_PLAN.md）：
- Layer 0 · Data Lake（已有）
- Layer 1 · Alpha Decay Survival Model
- Layer 2 · Stock Factor Response + Sharpe Style Analysis
- Layer 3 · Bayesian Signal Updater + Meta-Labeling
- Layer 4 · Portfolio Optimizer (Black-Litterman)
- Layer 5 · Exploration Bandit (Thompson Sampling)
- Layer 6 · Online Feedback + Counterfactual Eval

Phase I 交付（本模块）:
- schema.py           新表 + ALTER 扩列（幂等迁移）
- chain_alpha.py      chain 真相层回填（15009 chain → fact_chain_alpha_truth）
- triple_barrier.py   Triple Barrier labels (upper=2ATR / lower=1ATR / time=120d)
- purged_cv.py        Purged K-Fold + Embargo 工具
- survivorship.py     dim_all_ever_listed（含退市股，防幸存者偏差）
- qlib_alpha158.py    Alpha158 批量生成 + 覆盖率验证
"""

from .schema import migrate_phase1, SCHEMA_VERSION

__all__ = ["migrate_phase1", "SCHEMA_VERSION"]

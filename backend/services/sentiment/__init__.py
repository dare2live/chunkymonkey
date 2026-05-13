"""Phase η++++ — sentiment 包.

模块化设计:
  - configs       : 参数唯一源 (frozen dataclass)
  - factor_registry: 因子中央注册表
  - bin_assigner  : 数值 → 桶 (纯函数)
  - window_calculator: 滚动窗口 (纯函数)
  - validators    : 数据契约 (raise 不静默)
  - ddl           : schema 一处
  - survey_builder: orchestrator (纯函数, 无 I/O)

外部接口示例:
    from services.sentiment.factor_registry import get_eligible_factors, get_bucket_dims
    from services.sentiment.survey_builder import build_survey_features
    from services.sentiment.configs import SURVEY_BIN, PROFILE_POLICY
"""
from services.sentiment.factor_registry import (
    factor_summary, get_bucket_dims, get_eligible_factors, get_factor,
    is_factor_eligible, list_factor_ids,
)

"""technical_states — 技术形态识别工具 (形态地基, 后续因子叠加的基础)。

给任一股票任一时点 (日/周/月) 识别技术状态 (主态+子态) + 量化特征。config 驱动 (technical_states.yaml), PIT, 多时间框架。
用法:
  from services.technical_states import compute, classify_series, classify_multi_timeframe, load_config
  feats = compute(dates, o, h, l, c, v, timeframe="daily")        # 日线特征
  states = classify_series(feats)                                  # {date: {dominant, sub_state, membership, entropy}}
  wf = compute(dates, o, h, l, c, v, resample_rule="W", warmup=60) # 周线
  mtf = classify_multi_timeframe(feats_d, feats_w, feats_m)         # 日/周/月聚合 + mtf_aligned
"""
from services.technical_states.classifier import (  # noqa: F401
    classify_bar,
    classify_multi_timeframe,
    classify_series,
    classify_stock,
    load_config,
    state_scores,
)
from services.technical_states.context import apply_context  # noqa: F401
from services.technical_states.coupling import (  # noqa: F401
    apply_coupling,
    list_tunables,
    with_overrides,
)
from services.technical_states.features import FEATURE_KEYS, compute, resample  # noqa: F401
from services.technical_states.limits import (  # noqa: F401
    code_to_ts_code,
    compute_limit_flags,
    enrich_features,
)

"""technical_states — 技术形态识别工具 (档案系统维度①, 形态地基)。

给任一股任一时点(日/周/月)识别技术状态(主态+子态)+命名形态+量化特征。**全 config 驱动**(technical_states.yaml),
PIT, 多时间框架, A股涨停修正, 上下文消歧。owner=docs/stock_dossier_master_design.md。

## 模块架构 (单一职责, 各读同一 config; 加态/子态/事件/命名=改 config 不动代码)
  features.py   多TF PIT 特征 (compute/resample)
  classifier.py L1派生 / L2软隶属态 / L3子态 (声明式config evaluator) = orchestrator
  coupling.py   边界耦合 resolver (apply_coupling/with_overrides/list_tunables)
  limits.py     A股涨跌停修正 (compute_limit_flags/enrich_features; stk_limit真相源)
  context.py    上下文层两遍架构 (apply_context; 缩量回踩复活+prior_trend, PIT三时点)
  candles.py    单日K线形态 (candle_pattern; 位置消歧+A股一字板特判)
  patterns.py   命名形态模板 (match_named_patterns; 老鸭头等, 派生纯函数+PIT不回贴)

## 用法 (高层入口 = classify_stock 多TF; 单层 = compute+classify_series)
  from services.technical_states import compute, classify_series, classify_multi_timeframe
  feats = compute(dates, o, h, l, c, v, timeframe="daily")
  states = classify_series(feats)                  # {date: {dominant, sub_state, membership, entropy, covered}}
  mtf = classify_multi_timeframe(feats_d, feats_w, feats_m)
  # 上下文消歧(缩量回踩复活): apply_context(states, feats, cfg)
  # 命名形态: match_named_patterns([(date, refined_dominant), ...], cfg)
"""
from services.technical_states.candles import candle_pattern  # noqa: F401
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
from services.technical_states.patterns import match_named_patterns  # noqa: F401
from services.technical_states.capital import capital_signals, mainforce_net, capital_intent, zhuli_intent  # noqa: F401
from services.technical_states.chips import chip_signals  # noqa: F401
from services.technical_states.vol import volume_signals  # noqa: F401
from services.technical_states.rs import relative_strength  # noqa: F401

__all__ = [
    "compute", "resample", "FEATURE_KEYS", "load_config",
    "classify_bar", "classify_series", "classify_stock", "classify_multi_timeframe", "state_scores",
    "apply_coupling", "with_overrides", "list_tunables",
    "compute_limit_flags", "enrich_features", "code_to_ts_code",
    "apply_context", "candle_pattern", "match_named_patterns", "relative_strength",
    "capital_signals", "mainforce_net", "capital_intent", "zhuli_intent", "chip_signals", "volume_signals",
]

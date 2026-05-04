"""模型生命周期 (champion / challenger / retired) — W4.

API:
  - get_champion() / get_default_champion_model_id() / list_models() / get_model(model_id)
  - propose_challenger(model_id, training_config) — 标记新模型为 challenger
  - promote(model_id) — 把 challenger 提升为 champion (旧 champion 自动 retired)
  - check_deploy_gate(model_id) — 部署闸门 (IC + drift 双重检查)
"""
from .registry import (
    get_champion,
    get_default_champion_model_id,
    select_default_model_id,
    get_model_status,
    list_models,
    get_model,
    propose_challenger,
    promote,
    retire,
    check_deploy_gate,
)
from .drift import compute_psi, compute_feature_drift, write_drift_snapshot

__all__ = [
    "get_champion",
    "get_default_champion_model_id",
    "select_default_model_id",
    "get_model_status",
    "list_models",
    "get_model",
    "propose_challenger",
    "promote",
    "retire",
    "check_deploy_gate",
    "compute_psi",
    "compute_feature_drift",
    "write_drift_snapshot",
]

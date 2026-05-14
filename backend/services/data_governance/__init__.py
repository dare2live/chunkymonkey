"""Phase ψ.γ.dict.2 — 数据治理 runtime enforce 框架.

⚠ Rule 5 (Root Cause) + Rule 6 (Measured) + Rule 9.5 (Discipline):
    Phase ψ.γ.dict.1 写了 backend/config/field_dictionary.yaml 是 declarative,
    但 ETL/sync/mart 入库**没有任何代码读字典做 runtime check** — 字典是文档不是治理.

⚠ 本模块: 把字典升级为 runtime enforce, ETL 入库前必 call `enforce_dictionary(record, table_name)`.
    违反 → raise DictionaryViolation, 不 silent fallback (Rule 5).

参考: services/optimization/governance.py 同款风格 (Optuna 治理), 本模块通用 ETL 入库治理.
"""
from services.data_governance.config import (
    FieldDictionary,
    FieldSpec,
    TableSpec,
    get_field_dictionary,
    load_field_dictionary,
    reload_field_dictionary,
)
from services.data_governance.enforcer import (
    DictionaryViolation,
    enforce_dictionary,
    enforce_dictionary_batch,
)
from services.data_governance.etl_hook import validate_rows_before_insert

__all__ = [
    "FieldDictionary",
    "FieldSpec",
    "TableSpec",
    "DictionaryViolation",
    "get_field_dictionary",
    "load_field_dictionary",
    "reload_field_dictionary",
    "enforce_dictionary",
    "enforce_dictionary_batch",
    "validate_rows_before_insert",
]

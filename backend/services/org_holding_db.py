"""Org-holding database routing.

Physical file is the ``org_holding`` manifest alias (write-lock split from
smartmoney). Low-cadence event/detail store; land→canonical accept stays in
this file (MASTER §5.6: cadence/write-lock, not a strategy-owned database).
"""
from __future__ import annotations

from pathlib import Path

from services.database_manifest import get_database_manifest
from services.duck_adapter import connect as duck_connect

ALIAS = "org_holding"


def org_holding_db_path() -> Path:
    return get_database_manifest().path_for(ALIAS)


def connect_org_holding(*, read_only: bool = False, timeout: int = 30):
    path = org_holding_db_path()
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duck_connect(str(path), timeout=timeout, read_only=read_only)


__all__ = ["ALIAS", "connect_org_holding", "org_holding_db_path"]

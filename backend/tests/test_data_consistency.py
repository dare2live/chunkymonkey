"""Static data-contract tests for retired tables and canonical K-line usage.

Production DB content checks live in ``backend/tests/realdb`` and are opt-in.
This default suite must be deterministic and must not open local production DBs.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.contract

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _python_files(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend((BACKEND_DIR / root).rglob("*.py"))
    return sorted(path for path in files if "__pycache__" not in path.parts)


def _source_without_comments(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        code = line.split("#", 1)[0]
        if code.strip():
            lines.append(code)
    return "\n".join(lines)


def _python_string_literals_excluding_docstrings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstring_nodes.add(body[0].value)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node not in docstring_nodes
    ]


def test_retired_event_return_table_is_not_read_or_written_by_business_code():
    offenders = []
    patterns = [
        "FROM fact_event_return",
        "JOIN fact_event_return",
        "INTO fact_event_return",
        "UPDATE fact_event_return",
        "DELETE FROM fact_event_return",
    ]
    for path in _python_files("services", "scripts", "routers"):
        text = _source_without_comments(path)
        if any(pattern in text for pattern in patterns):
            offenders.append(path.relative_to(BACKEND_DIR).as_posix())

    assert offenders == []


def test_retired_stock_kline_table_is_not_read_or_written_by_business_code():
    offenders = []
    patterns = [
        "FROM stock_kline",
        "JOIN stock_kline",
        "INTO stock_kline",
        "UPDATE stock_kline",
        "DELETE FROM stock_kline",
    ]
    for path in _python_files("services", "scripts", "routers"):
        text = _source_without_comments(path)
        if any(pattern in text for pattern in patterns):
            offenders.append(path.relative_to(BACKEND_DIR).as_posix())

    assert offenders == []


def test_retired_sw_industry_table_access_is_allowlisted_only_for_migration_and_cleanup():
    allowed = {
        "services/db.py",
        "scripts/audit_stale_references.py",
        "services/data_deprecation.py",
    }
    offenders = []
    for path in _python_files("services", "scripts", "routers"):
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel in allowed:
            continue
        literals = "\n".join(_python_string_literals_excluding_docstrings(path))
        if re.search(r"\bdim_stock_industry\b", literals):
            offenders.append(rel)

    assert offenders == []


def test_core_daily_stock_price_readers_use_canonical_kline_relation():
    required = {
        "scripts/build_feature_panel_duck.py",
        "scripts/run_daily_topk.py",
        "scripts/backtest_model_portfolio.py",
        "scripts/backtest_walkforward_portfolio.py",
        "scripts/build_alpha158_duck.py",
        "scripts/build_executive_trade_events.py",
        "scripts/build_lhb_events.py",
        "services/return_engine.py",
        "services/stock_stage_engine.py",
        "services/stock_turtle_engine.py",
        "services/screening_engine.py",
        "services/sector_momentum.py",
        "services/risk_factors.py",
        "services/prediction_outcome.py",
        "services/event_simulator.py",
        "services/gap_queue.py",
    }
    missing = []
    for rel in sorted(required):
        text = (BACKEND_DIR / rel).read_text(encoding="utf-8")
        if (
            "CANONICAL_KLINE_QFQ_RELATION" not in text
            and "KLINE_DAILY_QFQ_RELATION" not in text
            and "get_canonical_kline_qfq_relation" not in text
            and "canonical_kline_daily_qfq_sql" not in text
        ):
            missing.append(rel)

    assert missing == []


def test_default_suite_does_not_open_production_db_connections():
    """Guard this file against slipping back into runtime data audit behavior."""

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    banned_calls = {"get_conn", "get_market_conn", "init_db", "init_market_db"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in banned_calls:
                offenders.append(func.id)
            elif isinstance(func, ast.Attribute) and func.attr in banned_calls:
                offenders.append(func.attr)

    assert offenders == []

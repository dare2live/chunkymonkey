from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_architecture_inventory as subject


pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[3]


def test_frontend_api_calls_are_backed_by_backend_routes():
    backend = subject.scan_backend_assets(REPO)
    frontend = subject.scan_frontend_assets(REPO)

    violations = subject.frontend_route_contract_violations(
        frontend_assets=frontend.assets,
        backend_assets=backend.assets,
    )

    assert violations == []


def test_production_code_does_not_depend_on_deprecated_assets():
    backend = subject.scan_backend_assets(REPO)

    violations = subject.deprecated_dependency_violations(
        assets=backend.assets,
        edges=backend.edges,
    )

    assert violations == []

#!/usr/bin/env python3
"""Build a repo and DuckDB architecture inventory.

The inventory is intentionally heuristic. It gives cleanup and refactor work a
queryable starting point: assets, table reads/writes, API contracts, frontend
call sites, and dependency edges are persisted before anything is removed.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import chain
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import get_conn  # noqa: E402
from services.pipeline_manifest import git_commit_sha, record_pipeline_run, utc_now_iso  # noqa: E402
from services.schema_versions import record_actual_version  # noqa: E402


REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_REPORT = REPO.parent / "architecture_inventory.md"

INVENTORY_TABLES = [
    "mart_architecture_inventory_asset",
    "mart_architecture_dependency_edge",
    "mart_architecture_inventory_summary",
]

DDL = """
CREATE TABLE IF NOT EXISTS mart_architecture_inventory_asset (
    run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    path TEXT NOT NULL,
    module_area TEXT NOT NULL,
    classification TEXT NOT NULL,
    owner_module TEXT,
    current_call_paths_json TEXT,
    read_tables_json TEXT,
    write_tables_json TEXT,
    api_routes_json TEXT,
    frontend_api_calls_json TEXT,
    model_artifacts_json TEXT,
    blockers_json TEXT,
    notes TEXT,
    built_at TEXT NOT NULL,
    PRIMARY KEY (run_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_arch_inventory_classification
    ON mart_architecture_inventory_asset(run_id, classification, module_area);
CREATE INDEX IF NOT EXISTS idx_arch_inventory_path
    ON mart_architecture_inventory_asset(path);

CREATE TABLE IF NOT EXISTS mart_architecture_dependency_edge (
    run_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    target_asset_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    target TEXT NOT NULL,
    evidence TEXT,
    built_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arch_dependency_source
    ON mart_architecture_dependency_edge(run_id, source_asset_id);
CREATE INDEX IF NOT EXISTS idx_arch_dependency_target
    ON mart_architecture_dependency_edge(run_id, target_asset_id);
CREATE INDEX IF NOT EXISTS idx_arch_dependency_type
    ON mart_architecture_dependency_edge(run_id, dependency_type);

CREATE TABLE IF NOT EXISTS mart_architecture_inventory_summary (
    run_id TEXT PRIMARY KEY,
    backend_asset_count INTEGER NOT NULL,
    frontend_asset_count INTEGER NOT NULL,
    duckdb_asset_count INTEGER NOT NULL,
    dependency_edge_count INTEGER NOT NULL,
    deletion_candidate_count INTEGER NOT NULL,
    classification_counts_json TEXT NOT NULL,
    module_counts_json TEXT NOT NULL,
    built_at TEXT NOT NULL
);
"""

SKIP_DIR_NAMES = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

BACKEND_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".ini", ".txt"}
FRONTEND_SUFFIXES = {".html", ".js", ".css"}
ACTIVE_DEPENDENCY_CLASSIFICATIONS = {"compatibility_shim", "deprecated_pending_cleanup", "delete_after_tests"}

SQL_READ_RE = re.compile(r"\b(?:FROM|JOIN|ASOF\s+JOIN)\s+([A-Za-z_\"`][\w\.\"`]*)", re.IGNORECASE)
SQL_WRITE_RE = re.compile(
    r"\b(?:"
    r"INSERT\s+(?:OR\s+REPLACE\s+)?INTO|"
    r"UPDATE|"
    r"DELETE\s+FROM|"
    r"DROP\s+TABLE(?:\s+IF\s+EXISTS)?|"
    r"ALTER\s+TABLE|"
    r"CREATE(?:\s+OR\s+REPLACE)?\s+(?:TABLE|VIEW)(?:\s+IF\s+NOT\s+EXISTS)?"
    r")\s+([A-Za-z_\"`][\w\.\"`]*)",
    re.IGNORECASE,
)
APP_ROUTE_RE = re.compile(r"@app\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)", re.IGNORECASE)
ROUTER_ROUTE_RE = re.compile(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)", re.IGNORECASE)
API_ROUTER_PREFIX_RE = re.compile(r"APIRouter\((?P<args>[^)]*)\)", re.DOTALL)
PREFIX_ARG_RE = re.compile(r"prefix\s*=\s*[\"']([^\"']*)")
ROUTER_IMPORT_RE = re.compile(r"from\s+routers\.([A-Za-z_]\w*)\s+import\s+router\s+as\s+([A-Za-z_]\w*)")
INCLUDE_ROUTER_CALL_RE = re.compile(
    r"(?:app|router)\.include_router\(\s*(?P<alias>[A-Za-z_]\w*)(?P<args>[^)]*)\)",
    re.DOTALL,
)
FRONTEND_API_RE = re.compile(r"[\"'](/api/[^\"'\s)`]+)")
MODEL_ARTIFACT_RE = re.compile(r"[\"']((?:models|data)/[^\"']+\.(?:pkl|joblib|json|duckdb|parquet|csv))[\"']")
LEGACY_FRONTEND_SURFACE_RE = re.compile(r"data-legacy-surface\s*=\s*[\"']([^\"']+)[\"']")
FRONTEND_ASSET_REF_RE = re.compile(r"[\"'](assets/[^\"']+\.(?:js|css))(?:\?[^\"']*)?[\"']")


@dataclass
class Asset:
    asset_id: str
    asset_type: str
    path: str
    module_area: str
    classification: str
    owner_module: str | None = None
    current_call_paths: list[str] = field(default_factory=list)
    read_tables: list[str] = field(default_factory=list)
    write_tables: list[str] = field(default_factory=list)
    api_routes: list[str] = field(default_factory=list)
    frontend_api_calls: list[str] = field(default_factory=list)
    model_artifacts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass(frozen=True)
class Edge:
    source_asset_id: str
    target_asset_id: str
    dependency_type: str
    target: str
    evidence: str = ""


@dataclass
class Inventory:
    assets: list[Asset]
    edges: list[Edge]


def _execute_script(conn: Any, sql: str) -> None:
    if hasattr(conn, "executescript"):
        conn.executescript(sql)
        return
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            _execute_statement(conn, stmt)


def _execute_statement(conn: Any, sql: str) -> None:
    conn.execute(sql)


def ensure_tables(conn: Any) -> None:
    _execute_script(conn, DDL)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _rel(path: Path, repo: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _normalize_table_token(token: str) -> str | None:
    cleaned = token.strip().strip(";,()[]{}").replace('"', "").replace("`", "")
    cleaned = cleaned.split()[0] if cleaned.split() else cleaned
    cleaned = cleaned.rstrip(",;")
    if not cleaned:
        return None
    ignored = {
        "select",
        "values",
        "unnest",
        "read_csv_auto",
        "read_parquet",
        "range",
        "json_each",
        "if",
        "not",
    }
    if cleaned.lower() in ignored or cleaned.startswith("?"):
        return None
    if cleaned.split(".", 1)[0] in {"services", "scripts", "routers", "fastapi", "typing"}:
        return None
    return cleaned


def _unique_sorted(items: Iterable[str | None]) -> list[str]:
    return sorted({item for item in items if item})


def extract_sql_tables(text: str) -> tuple[list[str], list[str]]:
    reads = [_normalize_table_token(match.group(1)) for match in SQL_READ_RE.finditer(text)]
    writes = [_normalize_table_token(match.group(1)) for match in SQL_WRITE_RE.finditer(text)]
    return _unique_sorted(reads), _unique_sorted(writes)


def _join_api_path(*parts: str) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part if part.startswith("/") else f"/{part}"
            continue
        out = out.rstrip("/") + "/" + part.lstrip("/")
    return out or "/"


def _router_prefix(text: str) -> str:
    match = API_ROUTER_PREFIX_RE.search(text)
    if not match:
        return ""
    prefix = PREFIX_ARG_RE.search(match.group("args"))
    return prefix.group(1) if prefix else ""


def _router_import_aliases(text: str) -> dict[str, str]:
    return {
        alias: f"routers.{module}"
        for module, alias in ROUTER_IMPORT_RE.findall(text)
    }


def _included_router_prefixes(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for match in INCLUDE_ROUTER_CALL_RE.finditer(text):
        prefix = PREFIX_ARG_RE.search(match.group("args"))
        out.append((match.group("alias"), prefix.group(1) if prefix else ""))
    return out


def _router_prefixes_from_main(main_text: str) -> dict[str, str]:
    alias_to_module = _router_import_aliases(main_text)
    out: dict[str, str] = {}
    for alias, prefix in _included_router_prefixes(main_text):
        module = alias_to_module.get(alias)
        if module:
            out[module] = prefix
    return out


def _router_child_app_prefixes(
    *,
    module_text: str,
    parent_prefix: str,
) -> list[tuple[str, str]]:
    alias_to_module = _router_import_aliases(module_text)
    return [
        (child_module, _join_api_path(parent_prefix, include_prefix))
        for alias, include_prefix in _included_router_prefixes(module_text)
        for child_module in [alias_to_module.get(alias)]
        if child_module
    ]


def _resolve_router_app_prefixes(module_to_text: dict[str, str], main_text: str) -> dict[str, str]:
    out = _router_prefixes_from_main(main_text)
    pending = list(out.items())
    while pending:
        parent_module, parent_prefix = pending.pop(0)
        children = [
            (child_module, child_prefix)
            for child_module, child_prefix in _router_child_app_prefixes(
                module_text=module_to_text.get(parent_module, ""),
                parent_prefix=parent_prefix,
            )
            if child_module not in out
        ]
        out.update(children)
        pending.extend(children)
    return out


def extract_api_routes(text: str, *, app_prefix: str = "", router_prefix: str = "") -> list[str]:
    routes: list[str] = []
    for method, path in APP_ROUTE_RE.findall(text):
        routes.append(f"{method.upper()} {_join_api_path(app_prefix, path)}")
    for method, path in ROUTER_ROUTE_RE.findall(text):
        routes.append(f"{method.upper()} {_join_api_path(app_prefix, router_prefix, path)}")
    return sorted(set(routes))


def extract_frontend_api_calls(text: str) -> list[str]:
    text = _strip_js_comments(text)
    return sorted({match.group(1).rstrip(";,") for match in FRONTEND_API_RE.finditer(text)})


def extract_model_artifacts(text: str) -> list[str]:
    return sorted({match.group(1) for match in MODEL_ARTIFACT_RE.finditer(text)})


def extract_frontend_asset_refs(text: str) -> list[str]:
    text = _strip_js_comments(text)
    return sorted({match.group(1) for match in FRONTEND_ASSET_REF_RE.finditer(text)})


def legacy_frontend_surface_paths(index_text: str) -> set[str]:
    paths: set[str] = set()
    for surface in LEGACY_FRONTEND_SURFACE_RE.findall(index_text):
        normalized = surface.strip().replace("_", "-")
        if normalized:
            paths.add(f"assets/js/{normalized}-view.js")
    return paths


def _strip_js_comments(text: str) -> str:
    """Remove JavaScript comments while preserving string literals."""

    out: list[str] = []
    i = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if line_comment:
            if ch == "\r" or ch == "\n":
                out.append(ch)
                line_comment = False
            i += 1
            continue
        if block_comment:
            if ch == "*" and nxt == "/":
                block_comment = False
                i += 2
                continue
            out.append("\n" if ch == "\r" or ch == "\n" else " ")
            i += 1
            continue
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch == "'" or ch == '"' or ch == "`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            block_comment = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _iter_files(root: Path, suffixes: set[str]) -> Iterable[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name == ".DS_Store":
            continue
        if path.suffix.lower() in suffixes:
            files.append(path)
    return sorted(files)


def python_module_for_path(path: Path, repo: Path) -> str | None:
    rel = Path(_rel(path, repo))
    if not rel.parts or rel.parts[0] != "backend" or path.suffix != ".py":
        return None
    parts = list(rel.parts[1:])
    if not parts:
        return None
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def asset_type_for_path(path: Path, repo: Path) -> str:
    rel = _rel(path, repo)
    if rel == "backend/main.py":
        return "backend_entry"
    if rel.startswith("backend/routers/"):
        return "router"
    if rel.startswith("backend/services/"):
        return "service"
    if rel.startswith("backend/scripts/"):
        return "script"
    if rel.startswith("backend/config/"):
        return "config"
    if rel.startswith("backend/tests/"):
        return "test"
    if rel == "index.html":
        return "frontend_page"
    if rel.startswith("assets/js/"):
        return "frontend_js"
    if rel.startswith("assets/css/"):
        return "frontend_css"
    return "other"


def module_area_for_path(path: Path, repo: Path, text: str = "") -> str:
    rel = _rel(path, repo).lower()
    probe = f"{rel}\n{text[:2000].lower()}"
    if rel.startswith("assets/") or rel == "index.html":
        return "frontend"
    if rel.startswith("backend/routers/") or rel == "backend/main.py":
        return "api_workbench"
    if rel.startswith("backend/tests/"):
        return "test"
    if "trading_calendar" in probe or "calendar" in probe:
        return "calendar_gate"
    if any(key in probe for key in ("tdxhub", "tdx_source", "kline_source", "build_price_kline", "source_policy")):
        return "market_data_ingestion"
    if any(key in probe for key in ("data_sources", "source_watermark", "gap_queue", "failure_queue")):
        return "source_policy"
    if any(key in probe for key in ("feature_panel", "alpha158", "feature_registry", "feature_", "tdx_gpcw", "tdx_keep")):
        return "feature_factory"
    if any(key in probe for key in ("optuna", "champion", "walkforward", "model_", "ranker", "lightgbm", "lgbm")):
        return "model_research"
    if any(key in probe for key in ("storage_retention", "deprecation", "cleanup", "orphan")):
        return "storage_retention"
    if any(key in probe for key in ("duckdb", "schema_version", "pipeline_manifest", "db.py")):
        return "storage_core"
    return "other"


def classify_code_asset(path: Path, repo: Path, text: str) -> tuple[str, str]:
    rel = _rel(path, repo).lower()
    probe = f"{rel}\n{text[:4000].lower()}"
    if (
        rel.endswith("runtime_patches.py")
        or "/compat" in rel
        or "/shim" in rel
        or "compatibility_shim" in rel
    ):
        return "compatibility_shim", "compatibility marker detected"
    if rel.endswith("services/data_deprecation.py") or rel.endswith("scripts/record_data_deprecations.py"):
        return "production", "active data asset deprecation registry"
    if any(key in rel for key in ("cleanup_orphan", "cleanup_legacy", "mark_deprecated")):
        return "deprecated_pending_cleanup", "cleanup/deprecation tool; keep until call paths and replacements are verified"
    if rel.startswith("backend/tests/"):
        return "production", "test coverage path; fixture literals are not cleanup markers"
    if any(key in probe for key in ("legacy", "deprecated", "退役")) and "replacement" in probe:
        return "deprecated_pending_cleanup", "legacy/deprecated replacement marker detected"
    if any(key in probe for key in ("candidate", "challenger", "tdx_keep", "gpcw", "hybrid")):
        return "candidate", "candidate/challenger workflow marker detected"
    if any(
        key in probe
        for key in (
            "optuna",
            "ablation",
            "drift_safe",
            "feature_association",
            "feature_search",
            "stability_search",
            "benchmark",
            "profile_",
            "walkforward_feature_eval",
        )
    ):
        return "research", "research/model exploration marker detected"
    return "production", "active code path by default"


def _resolve_relative_import(current_module: str | None, module: str | None, level: int) -> str | None:
    if level <= 0:
        return module
    if not current_module:
        return module
    parts = current_module.split(".")
    package = parts if parts[-1] == "__init__" else parts[:-1]
    keep = max(len(package) - (level - 1), 0)
    resolved = package[:keep]
    if module:
        resolved.extend(module.split("."))
    return ".".join(part for part in resolved if part)


def extract_python_imports(text: str, current_module: str | None = None) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative_import(current_module, node.module, node.level)
            if resolved:
                imports.add(resolved)
    prefixes = ("services", "scripts", "routers", "backend.services", "backend.scripts", "backend.routers")
    return sorted(item for item in imports if item.startswith(prefixes))


def _backend_table_edges(asset_id: str, reads: Iterable[str], writes: Iterable[str]) -> set[Edge]:
    edges: set[Edge] = set()
    for table in reads:
        edges.add(Edge(asset_id, f"duckdb:{table}", "table_read", table, "SQL FROM/JOIN"))
    for table in writes:
        edges.add(Edge(asset_id, f"duckdb:{table}", "table_write", table, "SQL write DDL/DML"))
    return edges


def _backend_route_edges(asset_id: str, routes: Iterable[str]) -> set[Edge]:
    return {
        Edge(asset_id, f"api:{route.split(' ', 1)[-1]}", "api_route", route, "FastAPI decorator")
        for route in routes
    }


def _resolve_import_asset(imported: str, module_to_asset: dict[str, str]) -> str | None:
    target = imported
    while target and target not in module_to_asset and "." in target:
        target = target.rsplit(".", 1)[0]
    return module_to_asset.get(target) if target else None


def _backend_import_edges(
    asset_id: str,
    imports: Iterable[str],
    module_to_asset: dict[str, str],
) -> set[Edge]:
    return {
        Edge(
            asset_id,
            _resolve_import_asset(imported, module_to_asset) or f"module:{imported}",
            "python_import",
            imported,
            "AST import",
        )
        for imported in imports
    }


def _frontend_edges(asset_id: str, calls: Iterable[str], asset_refs: Iterable[str]) -> set[Edge]:
    edges: set[Edge] = set()
    for call in calls:
        edges.add(Edge(asset_id, f"api:{call.split('?', 1)[0]}", "frontend_api_call", call, "frontend /api string"))
    for ref in asset_refs:
        edges.add(Edge(asset_id, f"frontend:{ref}", "frontend_asset_ref", ref, "frontend asset reference"))
    return edges


def scan_backend_assets(repo: Path) -> Inventory:
    backend = repo / "backend"
    main_text = _read_text(backend / "main.py") if (backend / "main.py").exists() else ""
    files = list(_iter_files(backend, BACKEND_SUFFIXES))
    module_to_asset: dict[str, str] = {}
    module_to_text: dict[str, str] = {}
    path_to_text: dict[Path, str] = {}

    for path in files:
        text = _read_text(path)
        path_to_text[path] = text
        module = python_module_for_path(path, repo)
        if module:
            module_to_asset[module] = f"code:{_rel(path, repo)}"
            module_to_text[module] = text

    app_prefix_by_module = _resolve_router_app_prefixes(module_to_text, main_text)
    assets: list[Asset] = []
    edges: set[Edge] = set()
    for path in files:
        text = path_to_text[path]
        rel = _rel(path, repo)
        asset_id = f"code:{rel}"
        module = python_module_for_path(path, repo)
        reads, writes = extract_sql_tables(text)
        classification, notes = classify_code_asset(path, repo, text)
        router_prefix = _router_prefix(text) if rel.startswith("backend/routers/") else ""
        app_prefix = app_prefix_by_module.get(module or "", "")
        routes = extract_api_routes(text, app_prefix=app_prefix, router_prefix=router_prefix)
        asset = Asset(
            asset_id=asset_id,
            asset_type=asset_type_for_path(path, repo),
            path=rel,
            module_area=module_area_for_path(path, repo, text),
            classification=classification,
            owner_module=module or module_area_for_path(path, repo, text),
            read_tables=reads,
            write_tables=writes,
            api_routes=routes,
            model_artifacts=extract_model_artifacts(text),
            notes=notes,
        )
        if rel.startswith("backend/scripts/"):
            asset.current_call_paths.append(f"cli:{rel}")
        if rel.startswith("backend/routers/") or rel == "backend/main.py":
            asset.current_call_paths.append("fastapi")
        assets.append(asset)

        edges.update(_backend_table_edges(asset_id, reads, writes))
        edges.update(_backend_route_edges(asset_id, routes))
        if path.suffix == ".py":
            edges.update(_backend_import_edges(asset_id, extract_python_imports(text, module), module_to_asset))

    return Inventory(assets=assets, edges=sorted(edges, key=lambda e: (e.source_asset_id, e.dependency_type, e.target)))


def scan_frontend_assets(repo: Path) -> Inventory:
    roots = [repo / "index.html", repo / "assets"]
    index_text = _read_text(roots[0]) if roots[0].exists() else ""
    legacy_paths = legacy_frontend_surface_paths(index_text)
    files: list[Path] = []
    if roots[0].exists():
        files.append(roots[0])
    files.extend(_iter_files(roots[1], FRONTEND_SUFFIXES))
    unique_files = sorted(set(files))
    assets: list[Asset] = []
    edges: set[Edge] = set()
    for path in unique_files:
        text = _read_text(path)
        rel = _rel(path, repo)
        calls = extract_frontend_api_calls(text)
        asset_refs = extract_frontend_asset_refs(text)
        if rel in legacy_paths:
            classification = "compatibility_shim"
            current_call_paths = ["browser:legacy"]
            notes = "legacy frontend surface isolated behind workbench replacement"
        else:
            classification = "production"
            current_call_paths = ["browser"]
            notes = "active frontend surface by default"
        asset = Asset(
            asset_id=f"frontend:{rel}",
            asset_type=asset_type_for_path(path, repo),
            path=rel,
            module_area="frontend",
            classification=classification,
            owner_module="frontend",
            current_call_paths=current_call_paths,
            frontend_api_calls=calls,
            model_artifacts=extract_model_artifacts(text),
            notes=notes,
        )
        assets.append(asset)
        edges.update(_frontend_edges(asset.asset_id, calls, asset_refs))
    return Inventory(assets=assets, edges=sorted(edges, key=lambda e: (e.source_asset_id, e.target)))


def _duckdb_table_asset_id(table_catalog: str, table_schema: str, table_name: str) -> str:
    if table_schema == "main":
        return f"duckdb:{table_name}"
    return f"duckdb:{table_schema}.{table_name}"


def _quote_table_ref(table_catalog: str, table_schema: str, table_name: str) -> str:
    return ".".join(_quote_ident(part) for part in (table_catalog, table_schema, table_name))


def _table_columns(conn: Any, table_catalog: str, table_schema: str, table_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_catalog = ?
           AND table_schema = ?
           AND table_name = ?
         ORDER BY ordinal_position
        """,
        (table_catalog, table_schema, table_name),
    ).fetchall()
    return [str(row["column_name"]) for row in rows]


def _safe_count(conn: Any, table_ref: str) -> int | None:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table_ref}").fetchone()
        return int(row["n"]) if row else None
    except Exception:
        return None


def _safe_latest(conn: Any, table_ref: str, columns: list[str]) -> tuple[str | None, str | None]:
    candidates = (
        "date",
        "trade_date",
        "report_date",
        "built_at",
        "updated_at",
        "created_at",
        "validated_at",
        "started_at",
        "ended_at",
    )
    lower_to_actual = {col.lower(): col for col in columns}
    available = [(candidate, lower_to_actual[candidate]) for candidate in candidates if candidate in lower_to_actual]
    if not available:
        return None, None
    select_exprs = ", ".join(
        f"CAST(MAX({_quote_ident(actual)}) AS VARCHAR) AS {_quote_ident(f'latest_{idx}')}"
        for idx, (_candidate, actual) in enumerate(available)
    )
    try:
        row = conn.execute(f"SELECT {select_exprs} FROM {table_ref}").fetchone()
    except Exception:
        return None, None
    if not row:
        return None, None
    for idx, (_candidate, actual) in enumerate(available):
        latest = row[f"latest_{idx}"]
        if latest is not None:
            return actual, str(latest)
    return None, None


def classify_duckdb_asset(table_name: str, table_type: str) -> tuple[str, str]:
    probe = table_name.lower()
    if any(key in probe for key in ("deprecated", "legacy", "orphan")):
        return "deprecated_pending_cleanup", "legacy/deprecated table marker detected"
    if any(key in probe for key in ("candidate", "challenger", "tdx_keep", "gpcw", "hybrid")):
        return "candidate", "candidate/challenger table marker detected"
    if any(
        key in probe
        for key in (
            "optuna",
            "ablation",
            "drift_safe",
            "feature_association",
            "feature_search",
            "stability_search",
            "walkforward",
            "holding_topk_eval",
        )
    ):
        return "research", "research/model exploration table marker detected"
    if table_type.upper() == "VIEW":
        return "compatibility_shim", "view is a compatibility/read abstraction until consumers are verified"
    return "production", "active storage object by default"


def module_area_for_table(table_name: str) -> str:
    probe = table_name.lower()
    if "calendar" in probe:
        return "calendar_gate"
    if any(key in probe for key in ("kline", "tdx", "source", "watermark", "failure_queue")):
        return "market_data_ingestion"
    if "feature" in probe or "alpha" in probe:
        return "feature_factory"
    if any(key in probe for key in ("model", "champion", "optuna", "walkforward", "prediction", "portfolio")):
        return "model_research"
    if any(key in probe for key in ("deprecation", "retention", "cleanup")):
        return "storage_retention"
    if table_name.startswith("mart_architecture_") or table_name.startswith("dim_schema"):
        return "storage_core"
    return "storage_core"


def scan_duckdb_assets(conn: Any) -> Inventory:
    rows = conn.execute(
        """
        SELECT table_catalog, table_schema, table_name, table_type
          FROM information_schema.tables
         WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
         ORDER BY table_catalog, table_schema, table_name
        """
    ).fetchall()
    assets: list[Asset] = []
    for row in rows:
        table_catalog = str(row["table_catalog"])
        table_schema = str(row["table_schema"])
        table_name = str(row["table_name"])
        table_type = str(row["table_type"])
        if table_name.startswith("sqlite_"):
            continue
        table_ref = _quote_table_ref(table_catalog, table_schema, table_name)
        columns = _table_columns(conn, table_catalog, table_schema, table_name)
        row_count = _safe_count(conn, table_ref)
        latest_column, latest_value = _safe_latest(conn, table_ref, columns)
        classification, notes = classify_duckdb_asset(table_name, table_type)
        details = {
            "table_type": table_type,
            "row_count": row_count,
            "column_count": len(columns),
            "latest_column": latest_column,
            "latest_value": latest_value,
        }
        assets.append(
            Asset(
                asset_id=_duckdb_table_asset_id(table_catalog, table_schema, table_name),
                asset_type="duckdb_view" if table_type.upper() == "VIEW" else "duckdb_table",
                path=f"{table_catalog}.{table_schema}.{table_name}",
                module_area=module_area_for_table(table_name),
                classification=classification,
                owner_module=module_area_for_table(table_name),
                notes=f"{notes}; {_json(details)}",
            )
        )
    return Inventory(assets=assets, edges=[])


def _route_lookup(assets: list[Asset]) -> dict[str, str]:
    return dict(chain.from_iterable(_route_lookup_rows(asset) for asset in assets))


def _route_lookup_rows(asset: Asset) -> list[tuple[str, str]]:
    return [(route.split(" ", 1)[1], asset.asset_id) for route in asset.api_routes]


def _table_lookup(assets: list[Asset]) -> dict[str, str]:
    out: dict[str, str] = {}
    for asset in assets:
        if not asset.asset_id.startswith("duckdb:"):
            continue
        path_parts = asset.path.split(".")
        simple = path_parts[-1]
        out[simple] = asset.asset_id
        if len(path_parts) >= 2:
            out[".".join(path_parts[-2:])] = asset.asset_id
    return out


def _resolve_edges(edges: Iterable[Edge], assets: list[Asset]) -> list[Edge]:
    route_by_path = _route_lookup(assets)
    table_by_name = _table_lookup(assets)
    resolved: set[Edge] = set()
    for edge in edges:
        target_asset = edge.target_asset_id
        if edge.dependency_type in {"table_read", "table_write"}:
            target_asset = table_by_name.get(edge.target, edge.target_asset_id)
        elif edge.dependency_type == "frontend_api_call":
            path = edge.target.split("?", 1)[0]
            target_asset = route_by_path.get(path, edge.target_asset_id)
        resolved.add(
            Edge(
                source_asset_id=edge.source_asset_id,
                target_asset_id=target_asset,
                dependency_type=edge.dependency_type,
                target=edge.target,
                evidence=edge.evidence,
            )
        )
    return sorted(resolved, key=lambda e: (e.source_asset_id, e.dependency_type, e.target_asset_id, e.target))


def _apply_dependency_context(assets: list[Asset], edges: list[Edge]) -> None:
    by_id = {asset.asset_id: asset for asset in assets}
    incoming: dict[str, set[str]] = defaultdict(set)
    source_blockers: dict[str, set[str]] = defaultdict(set)
    target_blockers: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        incoming[edge.target_asset_id].add(edge.source_asset_id)
        source = by_id.get(edge.source_asset_id)
        target = by_id.get(edge.target_asset_id)
        if not source or not target:
            continue
        if source.asset_type == "test":
            continue
        if target.classification in ACTIVE_DEPENDENCY_CLASSIFICATIONS:
            if source.classification == "production":
                source_blockers[source.asset_id].add(
                    f"production asset depends on {target.classification}: {target.path}"
                )
            target_blockers[target.asset_id].add(
                f"has active dependency from {source.path}"
            )
    call_paths_by_asset = {
        asset.asset_id: sorted(set(asset.current_call_paths) | incoming.get(asset.asset_id, set()))
        for asset in assets
    }
    blockers_by_asset = {
        asset.asset_id: sorted(
            set(asset.blockers)
            | source_blockers.get(asset.asset_id, set())
            | target_blockers.get(asset.asset_id, set())
        )
        for asset in assets
    }
    for asset in assets:
        asset.current_call_paths = call_paths_by_asset[asset.asset_id]
        asset.blockers = blockers_by_asset[asset.asset_id]


def _path_segments(path: str) -> list[str]:
    clean = path.split("?", 1)[0].strip()
    return [part for part in clean.strip("/").split("/") if part]


def _route_segment_matches(route_segment: str, call_segment: str) -> bool:
    return (route_segment.startswith("{") and route_segment.endswith("}")) or route_segment == call_segment


def frontend_call_matches_route(call: str, route_path: str) -> bool:
    """Return True when a frontend API string is covered by a backend route path.

    Frontend strings are often dynamic prefixes such as ``/api/foo/`` followed
    by concatenated IDs. A trailing slash means the static prefix must match the
    route's leading segments, with ``{param}`` segments acting as wildcards.
    """

    raw_call = call.split("?", 1)[0].strip()
    call_segments = _path_segments(raw_call)
    route_segments = _path_segments(route_path)
    if raw_call.rstrip("/") == route_path.rstrip("/"):
        return True
    if len(call_segments) == len(route_segments):
        return all(
            _route_segment_matches(route_segment, call_segment)
            for route_segment, call_segment in zip(route_segments, call_segments)
        )
    if raw_call.endswith("/") and len(call_segments) < len(route_segments):
        return all(
            _route_segment_matches(route_segment, call_segment)
            for route_segment, call_segment in zip(route_segments, call_segments)
        )
    return False


def _backend_route_paths(backend_assets: list[Asset]) -> list[str]:
    def route_paths(asset: Asset) -> list[str]:
        if not (asset.path == "backend/main.py" or asset.path.startswith("backend/routers/")):
            return []
        return [route.split(" ", 1)[1] for route in asset.api_routes]

    return list(chain.from_iterable(route_paths(asset) for asset in backend_assets))


def _route_match_index(route_paths: Iterable[str]) -> tuple[set[str], list[str], list[str]]:
    static_routes: set[str] = set()
    pattern_routes: list[str] = []
    prefix_routes: list[str] = []
    for route_path in route_paths:
        prefix_routes.append(route_path)
        if "{" in route_path and "}" in route_path:
            pattern_routes.append(route_path)
        else:
            static_routes.add(route_path)
    return static_routes, pattern_routes, prefix_routes


def _frontend_api_call_rows(frontend_assets: list[Asset]) -> list[tuple[str, str]]:
    return list(
        chain.from_iterable(
            ((asset.path, call) for call in asset.frontend_api_calls)
            for asset in frontend_assets
        )
    )


def _frontend_call_is_backed(
    call: str,
    *,
    static_routes: set[str],
    pattern_routes: list[str],
    prefix_routes: list[str],
) -> bool:
    raw_call = call.split("?", 1)[0].strip()
    if raw_call in static_routes:
        return True
    if any(frontend_call_matches_route(raw_call, route_path) for route_path in pattern_routes):
        return True
    if raw_call.endswith("/"):
        return any(frontend_call_matches_route(raw_call, route_path) for route_path in prefix_routes)
    return False


def frontend_route_contract_violations(
    *,
    frontend_assets: list[Asset],
    backend_assets: list[Asset],
) -> list[dict[str, str]]:
    static_routes, pattern_routes, prefix_routes = _route_match_index(_backend_route_paths(backend_assets))
    out: list[dict[str, str]] = []
    for path, call in _frontend_api_call_rows(frontend_assets):
        if not _frontend_call_is_backed(
            call,
            static_routes=static_routes,
            pattern_routes=pattern_routes,
            prefix_routes=prefix_routes,
        ):
            out.append({"path": path, "api_call": call})
    return sorted(out, key=lambda item: (item["path"], item["api_call"]))


def deprecated_dependency_violations(
    *,
    assets: list[Asset],
    edges: list[Edge],
    source_classification: str = "production",
) -> list[dict[str, str]]:
    by_id = {asset.asset_id: asset for asset in assets}
    out: list[dict[str, str]] = []
    for edge in edges:
        source = by_id.get(edge.source_asset_id)
        target = by_id.get(edge.target_asset_id)
        if not source or not target:
            continue
        if source.asset_type == "test":
            continue
        if source.classification != source_classification:
            continue
        if target.classification not in {"deprecated_pending_cleanup", "delete_after_tests"}:
            continue
        out.append(
            {
                "source_path": source.path,
                "target_path": target.path,
                "dependency_type": edge.dependency_type,
                "target": edge.target,
            }
        )
    return sorted(out, key=lambda item: (item["source_path"], item["target_path"], item["target"]))


def collect_inventory(conn: Any, repo: Path) -> Inventory:
    backend = scan_backend_assets(repo)
    frontend = scan_frontend_assets(repo)
    duckdb = scan_duckdb_assets(conn)
    assets = backend.assets + frontend.assets + duckdb.assets
    edges = _resolve_edges(backend.edges + frontend.edges + duckdb.edges, assets)
    _apply_dependency_context(assets, edges)
    return Inventory(assets=sorted(assets, key=lambda a: a.asset_id), edges=edges)


def _summary(assets: list[Asset], edges: list[Edge], run_id: str, built_at: str) -> dict[str, Any]:
    classification_counts = Counter(asset.classification for asset in assets)
    module_counts = Counter(asset.module_area for asset in assets)
    return {
        "run_id": run_id,
        "backend_asset_count": sum(1 for asset in assets if asset.path.startswith("backend/")),
        "frontend_asset_count": sum(
            1 for asset in assets if asset.path == "index.html" or asset.path.startswith("assets/")
        ),
        "duckdb_asset_count": sum(1 for asset in assets if asset.asset_id.startswith("duckdb:")),
        "dependency_edge_count": len(edges),
        "deletion_candidate_count": sum(
            1
            for asset in assets
            if asset.classification in {"deprecated_pending_cleanup", "delete_after_tests"}
        ),
        "classification_counts": dict(sorted(classification_counts.items())),
        "module_counts": dict(sorted(module_counts.items())),
        "built_at": built_at,
    }


def persist_inventory(conn: Any, inventory: Inventory, *, run_id: str, built_at: str) -> dict[str, Any]:
    ensure_tables(conn)
    conn.execute("DELETE FROM mart_architecture_inventory_asset WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_architecture_dependency_edge WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM mart_architecture_inventory_summary WHERE run_id = ?", (run_id,))
    conn.executemany(
        """
        INSERT INTO mart_architecture_inventory_asset (
            run_id, asset_id, asset_type, path, module_area, classification,
            owner_module, current_call_paths_json, read_tables_json, write_tables_json,
            api_routes_json, frontend_api_calls_json, model_artifacts_json,
            blockers_json, notes, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                asset.asset_id,
                asset.asset_type,
                asset.path,
                asset.module_area,
                asset.classification,
                asset.owner_module,
                _json(asset.current_call_paths),
                _json(asset.read_tables),
                _json(asset.write_tables),
                _json(asset.api_routes),
                _json(asset.frontend_api_calls),
                _json(asset.model_artifacts),
                _json(asset.blockers),
                asset.notes,
                built_at,
            )
            for asset in inventory.assets
        ],
    )
    conn.executemany(
        """
        INSERT INTO mart_architecture_dependency_edge (
            run_id, source_asset_id, target_asset_id, dependency_type,
            target, evidence, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                edge.source_asset_id,
                edge.target_asset_id,
                edge.dependency_type,
                edge.target,
                edge.evidence,
                built_at,
            )
            for edge in inventory.edges
        ],
    )
    summary = _summary(inventory.assets, inventory.edges, run_id, built_at)
    conn.execute(
        """
        INSERT OR REPLACE INTO mart_architecture_inventory_summary (
            run_id, backend_asset_count, frontend_asset_count, duckdb_asset_count,
            dependency_edge_count, deletion_candidate_count,
            classification_counts_json, module_counts_json, built_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            summary["backend_asset_count"],
            summary["frontend_asset_count"],
            summary["duckdb_asset_count"],
            summary["dependency_edge_count"],
            summary["deletion_candidate_count"],
            _json(summary["classification_counts"]),
            _json(summary["module_counts"]),
            built_at,
        ),
    )
    record_actual_version(conn, "mart_architecture_inventory_asset")
    record_actual_version(conn, "mart_architecture_dependency_edge")
    record_actual_version(conn, "mart_architecture_inventory_summary")
    conn.commit()
    return summary


def _top_frontend_api_calls(assets: list[Asset]) -> list[tuple[str, int]]:
    calls = (
        call.split("?", 1)[0]
        for asset in assets
        for call in asset.frontend_api_calls
    )
    return Counter(calls).most_common(30)


def _largest_duckdb_assets(assets: list[Asset]) -> list[tuple[str, int | None, str | None]]:
    rows: list[tuple[str, int | None, str | None]] = []
    for asset in assets:
        if not asset.asset_id.startswith("duckdb:"):
            continue
        try:
            details = json.loads(asset.notes.split("; ", 1)[1])
        except Exception:
            details = {}
        rows.append((asset.path, details.get("row_count"), details.get("latest_value")))
    return sorted(rows, key=lambda item: -1 if item[1] is None else -int(item[1]))[:30]


def _module_report_asset_key(asset: Asset) -> tuple[str, bool, bool, str]:
    return (
        asset.module_area,
        asset.asset_type == "test",
        asset.classification != "production",
        asset.path,
    )


def _classification_counts_dict(assets: Iterable[Asset]) -> dict[str, int]:
    counts = Counter(asset.classification for asset in assets)
    return dict(sorted(counts.items()))


def _module_cut_line_rows(assets: list[Asset]) -> list[dict[str, Any]]:
    by_module: dict[str, list[Asset]] = defaultdict(list)
    sorted_assets = sorted(assets, key=_module_report_asset_key)
    module_names: list[str] = []
    last_module: str | None = None
    for asset in sorted_assets:
        if asset.module_area != last_module:
            module_names.append(asset.module_area)
            last_module = asset.module_area
        by_module[asset.module_area].append(asset)
    rows: list[dict[str, Any]] = []
    for module in module_names:
        module_assets = by_module[module]
        representative = [asset.path for asset in module_assets[:8]]
        rows.append(
            {
                "module_area": module,
                "total": len(module_assets),
                "counts": _classification_counts_dict(module_assets),
                "representative_paths": representative,
            }
        )
    return rows


def write_markdown_report(
    path: Path,
    *,
    summary: dict[str, Any],
    inventory: Inventory,
    repo: Path,
) -> None:
    deprecated = [
        asset
        for asset in inventory.assets
        if asset.classification in {"deprecated_pending_cleanup", "delete_after_tests", "compatibility_shim"}
    ]
    lines = [
        "# Chunky Monkey Architecture Inventory",
        "",
        f"- run_id: `{summary['run_id']}`",
        f"- built_at: `{summary['built_at']}`",
        f"- repo: `{repo}`",
        "",
        "## Summary",
        "",
        f"- backend assets: {summary['backend_asset_count']}",
        f"- frontend assets: {summary['frontend_asset_count']}",
        f"- DuckDB assets: {summary['duckdb_asset_count']}",
        f"- dependency edges: {summary['dependency_edge_count']}",
        f"- deletion/deprecation candidates: {summary['deletion_candidate_count']}",
        "",
        "## Classification Counts",
        "",
    ]
    for key, count in summary["classification_counts"].items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Module Counts", ""])
    for key, count in summary["module_counts"].items():
        lines.append(f"- {key}: {count}")

    lines.extend(["", "## Target Module Map", ""])
    for row in _module_cut_line_rows(inventory.assets):
        counts = ", ".join(f"{key}={value}" for key, value in row["counts"].items())
        paths = "; ".join(f"`{path}`" for path in row["representative_paths"])
        lines.append(
            f"- `{row['module_area']}`: total={row['total']}; {counts}; cut-line sample: {paths}"
        )

    lines.extend(["", "## Deprecated Or Compatibility Candidates", ""])
    deprecated_candidates = sorted(deprecated, key=lambda a: (a.classification, a.path))[:60]
    for asset in deprecated_candidates:
        blocker = "; ".join(asset.blockers[:3]) if asset.blockers else "no active blocker detected by static scan"
        lines.append(f"- `{asset.classification}` `{asset.path}`: {blocker}")

    lines.extend(["", "## Frontend API Calls", ""])
    for call, count in _top_frontend_api_calls(inventory.assets):
        lines.append(f"- `{call}`: {count} call site(s)")

    lines.extend(["", "## Largest DuckDB Objects", ""])
    for table_path, row_count, latest in _largest_duckdb_assets(inventory.assets):
        lines.append(f"- `{table_path}`: rows={row_count}, latest={latest}")

    lines.extend(
        [
            "",
            "## Next Actions",
            "",
            "- Confirm every `deprecated_pending_cleanup` asset has no production runtime dependency before deletion.",
            "- Use `mart_architecture_dependency_edge` to replace hard-coded imports with configurable module boundaries.",
            "- Convert high-fanout frontend API calls into explicit workbench modules before UI refactor.",
            "- Treat row counts and latest dates as the storage baseline before retention cleanup.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def build_architecture_inventory(
    conn: Any,
    *,
    repo: Path = REPO,
    run_id: str | None = None,
    output_path: Path | None = DEFAULT_REPORT,
) -> dict[str, Any]:
    started = utc_now_iso()
    started_monotonic = time.monotonic()
    run_id = run_id or f"architecture_inventory_{started.replace(':', '').split('.')[0]}"
    inventory = collect_inventory(conn, repo)
    built_at = utc_now_iso()
    summary = persist_inventory(conn, inventory, run_id=run_id, built_at=built_at)
    if output_path:
        write_markdown_report(output_path, summary=summary, inventory=inventory, repo=repo)
    ended = utc_now_iso()
    record_pipeline_run(
        conn,
        run_id=run_id,
        pipeline_name="build_architecture_inventory",
        status="success",
        started_at=started,
        ended_at=ended,
        duration_s=round(time.monotonic() - started_monotonic, 3),
        commit_sha=git_commit_sha(repo),
        cwd=str(repo),
        output_tables=INVENTORY_TABLES,
        perf_summary={
            "backend_asset_count": summary["backend_asset_count"],
            "frontend_asset_count": summary["frontend_asset_count"],
            "duckdb_asset_count": summary["duckdb_asset_count"],
            "dependency_edge_count": summary["dependency_edge_count"],
            "report_path": str(output_path) if output_path else None,
        },
    )
    summary["report_path"] = str(output_path) if output_path else None
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", default=str(DEFAULT_REPORT))
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    output_path = None if args.no_report else Path(args.output).expanduser().resolve()
    with get_conn() as conn:
        summary = build_architecture_inventory(
            conn,
            repo=Path(args.repo).expanduser().resolve(),
            run_id=args.run_id,
            output_path=output_path,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

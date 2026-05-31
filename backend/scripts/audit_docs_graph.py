"""Audit markdown documentation ownership, live refs, and authority cycles."""

from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parent.parent.parent

CORE_SOURCES = (
    "goal.md",
    "SESSION_HANDOFF.md",
    "analysis/workflow_checkpoint.md",
)
CONTEXT_ONLY_SOURCES = {
    "SESSION_HANDOFF.md",
    "analysis/workflow_checkpoint.md",
}
DOCS_MAP = "docs/README.md"
CLEANUP_LEDGER_HEADING = "## Recent Cleanup Ledger"
DOCS_TARGET_MAX = 10
DOCS_HARD_MAX = 10
ARCHIVE_TARGET_RE = re.compile(r"Archived (?:as|under) `([^`]+)`")
LEDGER_ROW_RE = re.compile(r"^\|\s*`([^`]+)`[^|]*\|\s*(.*?)\s*\|", re.MULTILINE)
DATED_CONTEXT_RE = re.compile(
    r"^analysis/(?:handoff|codex_bootstrap|next_session_prompt)_\d{8}\.md$"
)

ALLOWED_CURRENT_AUTHORITY_SCC = {
    "goal.md",
    "docs/PROJECT_CONSTITUTION.md",
    "docs/README.md",
    "docs/architecture_reform_context.md",
    "docs/chunkyctl_session_quickstart.md",
    "docs/data_product_contract.md",
    "docs/engineering_governance.md",
    "docs/implementation_plan.md",
    "docs/strategy_validation_contract.md",
}

MARKDOWN_REF_RE = re.compile(
    r"(?<![\w./-])"
    r"((?:\.\./|\./|~/|/)?"
    r"(?:docs/|analysis/|Users/|Documents/)?"
    r"[\w./\-\u4e00-\u9fff]+\.md)"
)


def _rel(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _source_paths(repo: Path) -> list[Path]:
    sources = [repo / rel for rel in CORE_SOURCES]
    sources.extend(sorted((repo / "docs").glob("*.md")))
    return [path for path in sources if path.exists()]


def _candidate_paths(repo: Path, source: Path, ref: str) -> list[Path]:
    if ref.startswith("~/"):
        return [(Path.home() / ref[2:]).resolve()]
    if ref.startswith("/"):
        return [Path(ref).resolve()]
    if ref.startswith(("../", "./")):
        return [(source.parent / ref).resolve()]
    if ref.startswith(("docs/", "analysis/")):
        return [(repo / ref).resolve()]
    if ref in {"goal.md", "SESSION_HANDOFF.md", "AGENTS.md", "CLAUDE.md"}:
        return [(repo / ref).resolve()]
    return [
        (source.parent / ref).resolve(),
        (repo / ref).resolve(),
        (repo / "docs" / ref).resolve(),
        (repo / "analysis" / ref).resolve(),
    ]


def _known_markdown_paths(repo: Path) -> set[Path]:
    known = {path.resolve() for path in repo.glob("*.md")}
    known.update(path.resolve() for path in (repo / "docs").glob("*.md"))
    known.update(path.resolve() for path in (repo / "analysis").glob("*.md"))
    return known


def _line_starts(text: str) -> list[int]:
    return [0] + [match.end() for match in re.finditer("\n", text)]


def _line_text(text: str, starts: list[int], line_no: int) -> str:
    start = starts[line_no - 1]
    end = text.find("\n", start)
    return text[start:] if end == -1 else text[start:end]


def _resolve_ref(repo: Path, source: Path, ref: str, known_paths: set[Path]) -> Path | None:
    for path in _candidate_paths(repo, source, ref):
        if path in known_paths or (path.is_absolute() and path.exists()):
            return path
    return None


def _scan_source_refs(
    repo: Path,
    source: Path,
    docs_map: Path,
    known_paths: set[Path],
) -> tuple[list[dict], list[dict], int]:
    edges: list[dict] = []
    unresolved: list[dict] = []
    cleanup_ledger_unresolved = 0
    text = _read_text(source)
    starts = _line_starts(text)
    cleanup_start = text.find(CLEANUP_LEDGER_HEADING) if source.resolve() == docs_map else -1
    source_rel = _rel(repo, source)
    for match in MARKDOWN_REF_RE.finditer(text):
        ref = match.group(1).strip("` ,;:()[]")
        if ref.startswith("http"):
            continue
        line_no = bisect.bisect_right(starts, match.start())
        target = _resolve_ref(repo, source, ref, known_paths)
        if target is not None:
            edges.append(
                {
                    "source": source_rel,
                    "target": _rel(repo, target),
                    "line": line_no,
                    "ref": ref,
                }
            )
        elif cleanup_start != -1 and match.start() > cleanup_start:
            cleanup_ledger_unresolved += 1
        else:
            unresolved.append(
                {
                    "source": source_rel,
                    "line": line_no,
                    "ref": ref,
                    "text": _line_text(text, starts, line_no).strip(),
                }
            )
    return edges, unresolved, cleanup_ledger_unresolved


def _scan_markdown_refs(repo: Path, sources: Iterable[Path]) -> tuple[list[dict], list[dict], int]:
    docs_map = (repo / DOCS_MAP).resolve()
    known_paths = _known_markdown_paths(repo)
    all_edges: list[dict] = []
    all_unresolved: list[dict] = []
    cleanup_ledger_unresolved = 0
    for source in sources:
        edges, unresolved, cleanup_unresolved = _scan_source_refs(
            repo,
            source,
            docs_map,
            known_paths,
        )
        all_edges.extend(edges)
        all_unresolved.extend(unresolved)
        cleanup_ledger_unresolved += cleanup_unresolved
    return all_edges, all_unresolved, cleanup_ledger_unresolved


def _resolve_cleanup_target(repo: Path, docs_map: Path, ref: str) -> Path:
    if ref.startswith("/"):
        return Path(ref)
    if ref.startswith("~/"):
        return Path.home() / ref[2:]
    if ref.startswith(("../", "./")):
        return docs_map.parent / ref
    return repo / ref


def _scan_cleanup_archive_targets(repo: Path, docs_map: Path) -> list[dict]:
    if not docs_map.exists():
        return []
    text = _read_text(docs_map)
    cleanup_start = text.find(CLEANUP_LEDGER_HEADING)
    if cleanup_start == -1:
        return []
    starts = _line_starts(text)
    missing: list[dict] = []
    for match in ARCHIVE_TARGET_RE.finditer(text, cleanup_start):
        ref = match.group(1)
        target = _resolve_cleanup_target(repo, docs_map, ref).resolve()
        if target.exists():
            continue
        line_no = bisect.bisect_right(starts, match.start())
        missing.append(
            {
                "source": _rel(repo, docs_map),
                "line": line_no,
                "ref": ref,
            }
        )
    return missing


def _git_show_head(repo: Path, ref: str) -> bytes | None:
    if ref.startswith(("../", "./", "/", "~")):
        return None
    result = subprocess.run(
        ["git", "show", f"HEAD:{ref}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _blank_archive_content_report() -> dict:
    return {
        "checked": 0,
        "exact_match": 0,
        "changed": 0,
        "no_head_baseline": 0,
        "skipped": 0,
        "target_missing": 0,
        "changed_items": [],
        "no_head_baseline_items": [],
        "target_missing_items": [],
    }


def _scan_cleanup_archive_content(repo: Path, docs_map: Path) -> dict:
    report = _blank_archive_content_report()
    if not docs_map.exists():
        return report
    text = _read_text(docs_map)
    cleanup_start = text.find(CLEANUP_LEDGER_HEADING)
    if cleanup_start == -1:
        return report
    starts = _line_starts(text)
    for match in LEDGER_ROW_RE.finditer(text, cleanup_start):
        former_ref = match.group(1)
        archive_match = ARCHIVE_TARGET_RE.search(match.group(2))
        if not archive_match:
            continue
        target_ref = archive_match.group(1)
        if "*" in former_ref or not former_ref.endswith(".md") or not target_ref.endswith(".md"):
            report["skipped"] += 1
            continue
        line_no = bisect.bisect_right(starts, match.start())
        target = _resolve_cleanup_target(repo, docs_map, target_ref).resolve()
        item = {
            "source": _rel(repo, docs_map),
            "line": line_no,
            "former": former_ref,
            "target": target_ref,
        }
        if not target.exists():
            report["target_missing"] += 1
            report["target_missing_items"].append(item)
            continue
        old_bytes = _git_show_head(repo, former_ref)
        if old_bytes is None:
            report["no_head_baseline"] += 1
            report["no_head_baseline_items"].append(item)
            continue
        report["checked"] += 1
        if old_bytes == target.read_bytes():
            report["exact_match"] += 1
        else:
            report["changed"] += 1
            report["changed_items"].append(item)
    return report


def _is_registry_edge(edge: dict) -> bool:
    """Docs map registration edges are ownership metadata, not authority links."""
    return edge["source"] == DOCS_MAP and edge["target"].startswith("docs/")


def _is_context_only_edge(edge: dict) -> bool:
    """Runtime snapshots and dated handoffs are evidence, not authority links."""
    return (
        edge["source"] in CONTEXT_ONLY_SOURCES
        or edge["target"] in CONTEXT_ONLY_SOURCES
        or DATED_CONTEXT_RE.match(edge["target"]) is not None
    )


def _is_authority_edge(edge: dict) -> bool:
    return not _is_registry_edge(edge) and not _is_context_only_edge(edge)


def _strongly_connected_components(nodes: set[str], edges: list[dict]) -> list[list[str]]:
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        graph[edge["source"]].append(edge["target"])

    index: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = len(index)
        lowlink[node] = index[node]
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, []):
            if target not in index:
                visit(target)
                lowlink[node] = min(lowlink[node], lowlink[target])
            elif target in on_stack:
                lowlink[node] = min(lowlink[node], index[target])
        if lowlink[node] == index[node]:
            component: list[str] = []
            while True:
                item = stack.pop()
                on_stack.remove(item)
                component.append(item)
                if item == node:
                    break
            components.append(component)

    sorted_nodes = sorted(nodes)
    for node in sorted_nodes:
        if node not in index:
            visit(node)
    return components


def _canonical_components(components: list[list[str]]) -> list[list[str]]:
    return sorted(sorted(component) for component in components)


def build_docs_graph_report(repo: Path = REPO) -> dict:
    docs_dir = repo / "docs"
    docs_map = repo / DOCS_MAP
    docs_files = sorted(docs_dir.glob("*.md"))
    docs_map_text = _read_text(docs_map) if docs_map.exists() else ""
    unmentioned = sorted(path.name for path in docs_files if path.name not in docs_map_text)
    missing_archive_targets = _scan_cleanup_archive_targets(repo, docs_map)
    archive_content = _scan_cleanup_archive_content(repo, docs_map)

    sources = _source_paths(repo)
    edges, unresolved, cleanup_unresolved = _scan_markdown_refs(repo, sources)
    authority_edges = [edge for edge in edges if _is_authority_edge(edge)]
    context_only_edges = [edge for edge in edges if _is_context_only_edge(edge)]
    nodes = {_rel(repo, path) for path in sources}
    nodes.update(edge["target"] for edge in authority_edges)
    scc = _canonical_components([
        component
        for component in _strongly_connected_components(nodes, authority_edges)
        if len(component) > 1
    ])
    forbidden_scc = [
        component
        for component in scc
        if not set(component).issubset(ALLOWED_CURRENT_AUTHORITY_SCC)
    ]
    docs_count = len(docs_files)
    warnings = []
    hard_failures = []
    if docs_count > DOCS_TARGET_MAX:
        warnings.append(
            f"docs_count {docs_count} exceeds steady-state target {DOCS_TARGET_MAX}; "
            "merge, archive, or delete stale docs before adding more."
        )
    if docs_count > DOCS_HARD_MAX:
        hard_failures.append(
            f"docs_count {docs_count} exceeds hard max {DOCS_HARD_MAX}; "
            "new docs are blocked until existing docs are merged, archived, or deleted."
        )
    verdict = (
        "PASS"
        if not unmentioned
        and not unresolved
        and not missing_archive_targets
        and not forbidden_scc
        and not hard_failures
        else "FAIL"
    )
    return {
        "verdict": verdict,
        "docs_count": docs_count,
        "docs_target_max": DOCS_TARGET_MAX,
        "docs_hard_max": DOCS_HARD_MAX,
        "docs_count_over_target": max(0, docs_count - DOCS_TARGET_MAX),
        "sources_scanned": len(sources),
        "edge_count": len(edges),
        "authority_edge_count": len(authority_edges),
        "context_only_edge_count": len(context_only_edges),
        "unmentioned_docs": unmentioned,
        "unresolved_live_refs": unresolved,
        "cleanup_ledger_unresolved_labels": cleanup_unresolved,
        "missing_cleanup_archive_targets": missing_archive_targets,
        "archive_content": archive_content,
        "scc_count": len(scc),
        "largest_scc": max((len(component) for component in scc), default=0),
        "forbidden_scc": forbidden_scc,
        "warnings": warnings,
        "hard_failures": hard_failures,
        "allowed_current_authority_scc": [
            component for component in scc if set(component).issubset(ALLOWED_CURRENT_AUTHORITY_SCC)
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Docs Graph Audit",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Verdict | {report['verdict']} |",
        f"| docs_count | {report['docs_count']} |",
        f"| docs_target_max | {report.get('docs_target_max', 'unknown')} |",
        f"| docs_hard_max | {report.get('docs_hard_max', 'unknown')} |",
        f"| docs_count_over_target | {report.get('docs_count_over_target', 0)} |",
        f"| sources_scanned | {report['sources_scanned']} |",
        f"| edge_count | {report['edge_count']} |",
        f"| authority_edge_count | {report.get('authority_edge_count', 'unknown')} |",
        f"| context_only_edge_count | {report.get('context_only_edge_count', 'unknown')} |",
        f"| unmentioned_docs | {len(report['unmentioned_docs'])} |",
        f"| unresolved_live_refs | {len(report['unresolved_live_refs'])} |",
        f"| cleanup_ledger_unresolved_labels | {report['cleanup_ledger_unresolved_labels']} |",
        f"| missing_cleanup_archive_targets | {len(report.get('missing_cleanup_archive_targets', []))} |",
        f"| archive_content_checked | {report.get('archive_content', {}).get('checked', 0)} |",
        f"| archive_content_exact_match | {report.get('archive_content', {}).get('exact_match', 0)} |",
        f"| archive_content_changed | {report.get('archive_content', {}).get('changed', 0)} |",
        f"| archive_content_no_head_baseline | {report.get('archive_content', {}).get('no_head_baseline', 0)} |",
        f"| archive_content_skipped | {report.get('archive_content', {}).get('skipped', 0)} |",
        f"| scc_count | {report['scc_count']} |",
        f"| largest_scc | {report['largest_scc']} |",
        f"| forbidden_scc | {len(report['forbidden_scc'])} |",
        f"| warnings | {len(report.get('warnings', []))} |",
        f"| hard_failures | {len(report.get('hard_failures', []))} |",
    ]
    if report.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {item}" for item in report["warnings"])
    if report.get("hard_failures"):
        lines.extend(["", "## Hard Failures"])
        lines.extend(f"- {item}" for item in report["hard_failures"])
    if report["unmentioned_docs"]:
        lines.extend(["", "## Unmentioned Docs"])
        lines.extend(f"- `{item}`" for item in report["unmentioned_docs"])
    if report["unresolved_live_refs"]:
        lines.extend(["", "## Unresolved Live Refs"])
        for item in report["unresolved_live_refs"]:
            lines.append(f"- `{item['source']}:{item['line']}` -> `{item['ref']}`")
    if report.get("missing_cleanup_archive_targets"):
        lines.extend(["", "## Missing Cleanup Archive Targets"])
        for item in report["missing_cleanup_archive_targets"]:
            lines.append(f"- `{item['source']}:{item['line']}` -> `{item['ref']}`")
    archive_content = report.get("archive_content", {})
    if archive_content.get("changed_items"):
        lines.extend(["", "## Archive Content Changed"])
        for item in archive_content["changed_items"]:
            lines.append(
                f"- `{item['source']}:{item['line']}` `{item['former']}` -> `{item['target']}`"
            )
    if archive_content.get("no_head_baseline_items"):
        lines.extend(["", "## Archive Content Without HEAD Baseline"])
        for item in archive_content["no_head_baseline_items"]:
            lines.append(
                f"- `{item['source']}:{item['line']}` `{item['former']}` -> `{item['target']}`"
            )
    if report["forbidden_scc"]:
        lines.extend(["", "## Forbidden SCC"])
        for component in report["forbidden_scc"]:
            lines.append("- " + ", ".join(f"`{item}`" for item in component))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()

    report = build_docs_graph_report(REPO)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_docs_graph.py"
SPEC = importlib.util.spec_from_file_location("audit_docs_graph", SCRIPT_PATH)
audit_docs_graph = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = audit_docs_graph
SPEC.loader.exec_module(audit_docs_graph)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_docs_graph_allows_cleanup_ledger_former_filenames(tmp_path: Path) -> None:
    _write(tmp_path / "goal.md", "see docs/README.md\n")
    _write(tmp_path / "SESSION_HANDOFF.md", "handoff\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "checkpoint\n")
    _write(
        tmp_path / "docs/README.md",
        "\n".join(
            [
                "# Map",
                "`README.md` `note.md`",
                "See ../goal.md",
                "## Recent Cleanup Ledger",
                "| `OLD_PLAN.md` | moved |",
            ]
        ),
    )
    _write(tmp_path / "docs/note.md", "see ../goal.md\n")

    report = audit_docs_graph.build_docs_graph_report(tmp_path)

    assert report["verdict"] == "PASS"
    assert report["docs_count_over_target"] == 0
    assert report["authority_edge_count"] == 3
    assert report["unmentioned_docs"] == []
    assert report["unresolved_live_refs"] == []
    assert report["cleanup_ledger_unresolved_labels"] == 1
    assert report["missing_cleanup_archive_targets"] == []


def test_docs_graph_blocks_missing_cleanup_archive_targets(tmp_path: Path) -> None:
    _write(tmp_path / "goal.md", "see docs/README.md\n")
    _write(tmp_path / "SESSION_HANDOFF.md", "handoff\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "checkpoint\n")
    _write(tmp_path / "analysis/existing.md", "archived\n")
    _write(
        tmp_path / "docs/README.md",
        "\n".join(
            [
                "# Map",
                "`README.md`",
                "## Recent Cleanup Ledger",
                "| `OLD.md` | Archived as `../analysis/existing.md` |",
                "| `MISSING.md` | Archived as `../analysis/missing.md` |",
            ]
        ),
    )

    report = audit_docs_graph.build_docs_graph_report(tmp_path)

    assert report["verdict"] == "FAIL"
    assert report["missing_cleanup_archive_targets"] == [
        {
            "source": "docs/README.md",
            "line": 5,
            "ref": "../analysis/missing.md",
        }
    ]


def test_docs_graph_reports_archive_content_statuses(tmp_path: Path) -> None:
    _write(tmp_path / "goal.md", "see docs/README.md\n")
    _write(tmp_path / "SESSION_HANDOFF.md", "handoff\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "checkpoint\n")
    _write(tmp_path / "docs/exact.md", "same\n")
    _write(tmp_path / "docs/changed.md", "old\n")
    _write(
        tmp_path / "docs/README.md",
        "\n".join(
            [
                "# Map",
                "`README.md`",
                "## Recent Cleanup Ledger",
                "| `docs/exact.md` | Archived as `../analysis/exact.md` |",
                "| `docs/changed.md` | Archived as `../analysis/changed.md` |",
                "| `docs/new.md` | Archived as `../analysis/new.md` |",
                "| `docs/paper_sim_*` | Archived under `../analysis/docs_archive/` |",
            ]
        ),
    )
    _git(tmp_path, "init")
    _git(tmp_path, "add", "goal.md", "SESSION_HANDOFF.md", "analysis/workflow_checkpoint.md", "docs")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init")
    (tmp_path / "docs/exact.md").unlink()
    (tmp_path / "docs/changed.md").unlink()
    _write(tmp_path / "analysis/exact.md", "same\n")
    _write(tmp_path / "analysis/changed.md", "new\n")
    _write(tmp_path / "analysis/new.md", "new\n")
    (tmp_path / "analysis/docs_archive").mkdir()

    report = audit_docs_graph.build_docs_graph_report(tmp_path)
    archive_content = report["archive_content"]

    assert report["verdict"] == "PASS"
    assert archive_content["checked"] == 2
    assert archive_content["exact_match"] == 1
    assert archive_content["changed"] == 1
    assert archive_content["no_head_baseline"] == 1
    assert archive_content["skipped"] == 1
    assert archive_content["changed_items"][0]["former"] == "docs/changed.md"
    assert archive_content["no_head_baseline_items"][0]["former"] == "docs/new.md"


def test_docs_graph_blocks_unmentioned_unresolved_and_forbidden_cycles(tmp_path: Path) -> None:
    _write(tmp_path / "goal.md", "see docs/a.md\n")
    _write(tmp_path / "SESSION_HANDOFF.md", "handoff\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "checkpoint\n")
    _write(tmp_path / "docs/README.md", "`README.md` `a.md`\n")
    _write(tmp_path / "docs/a.md", "see docs/b.md and docs/missing.md\n")
    _write(tmp_path / "docs/b.md", "see docs/a.md\n")

    report = audit_docs_graph.build_docs_graph_report(tmp_path)

    assert report["verdict"] == "FAIL"
    assert report["unmentioned_docs"] == ["b.md"]
    assert [item["ref"] for item in report["unresolved_live_refs"]] == ["docs/missing.md"]
    assert report["forbidden_scc"] == [["docs/a.md", "docs/b.md"]]


def test_docs_graph_treats_runtime_snapshots_as_context_edges(tmp_path: Path) -> None:
    _write(
        tmp_path / "goal.md",
        "see docs/README.md plus SESSION_HANDOFF.md and analysis/workflow_checkpoint.md\n",
    )
    _write(tmp_path / "SESSION_HANDOFF.md", "runtime facts see docs/README.md\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "pipeline facts see goal.md\n")
    _write(
        tmp_path / "analysis/handoff_20260527.md",
        "dated handoff\n",
    )
    _write(
        tmp_path / "docs/README.md",
        "`README.md`\n"
        "context SESSION_HANDOFF.md analysis/workflow_checkpoint.md "
        "analysis/handoff_20260527.md\n",
    )

    report = audit_docs_graph.build_docs_graph_report(tmp_path)

    assert report["verdict"] == "PASS"
    assert report["forbidden_scc"] == []
    assert report["context_only_edge_count"] == 7
    assert report["authority_edge_count"] == 1


def test_render_markdown_includes_failure_sections() -> None:
    report = {
        "verdict": "FAIL",
        "docs_count": 2,
        "docs_target_max": 10,
        "docs_hard_max": 10,
        "docs_count_over_target": 0,
        "sources_scanned": 5,
        "edge_count": 3,
        "authority_edge_count": 3,
        "context_only_edge_count": 0,
        "unmentioned_docs": ["b.md"],
        "unresolved_live_refs": [{"source": "docs/a.md", "line": 7, "ref": "docs/missing.md"}],
        "cleanup_ledger_unresolved_labels": 0,
        "missing_cleanup_archive_targets": [
            {"source": "docs/README.md", "line": 9, "ref": "../analysis/missing.md"}
        ],
        "archive_content": {
            "checked": 2,
            "exact_match": 1,
            "changed": 1,
            "no_head_baseline": 1,
            "skipped": 0,
            "target_missing": 0,
            "changed_items": [
                {
                    "source": "docs/README.md",
                    "line": 10,
                    "former": "docs/changed.md",
                    "target": "../analysis/changed.md",
                }
            ],
            "no_head_baseline_items": [
                {
                    "source": "docs/README.md",
                    "line": 11,
                    "former": "docs/new.md",
                    "target": "../analysis/new.md",
                }
            ],
            "target_missing_items": [],
        },
        "scc_count": 1,
        "largest_scc": 2,
        "forbidden_scc": [["docs/a.md", "docs/b.md"]],
        "warnings": [],
        "hard_failures": [],
    }

    markdown = audit_docs_graph.render_markdown(report)

    assert "| Verdict | FAIL |" in markdown
    assert "## Unmentioned Docs" in markdown
    assert "`docs/a.md:7` -> `docs/missing.md`" in markdown
    assert "## Missing Cleanup Archive Targets" in markdown
    assert "`docs/README.md:9` -> `../analysis/missing.md`" in markdown
    assert "## Archive Content Changed" in markdown
    assert "`docs/README.md:10` `docs/changed.md` -> `../analysis/changed.md`" in markdown
    assert "## Archive Content Without HEAD Baseline" in markdown
    assert "`docs/README.md:11` `docs/new.md` -> `../analysis/new.md`" in markdown
    assert "## Forbidden SCC" in markdown


def test_docs_graph_warns_above_target_and_fails_above_hard_cap(tmp_path: Path) -> None:
    _write(tmp_path / "goal.md", "see docs/README.md\n")
    _write(tmp_path / "SESSION_HANDOFF.md", "handoff\n")
    _write(tmp_path / "analysis/workflow_checkpoint.md", "checkpoint\n")
    docs_names = ["README.md"] + [f"doc_{idx}.md" for idx in range(audit_docs_graph.DOCS_HARD_MAX)]
    _write(tmp_path / "docs/README.md", " ".join(f"`{name}`" for name in docs_names))
    for name in docs_names:
        if name != "README.md":
            _write(tmp_path / "docs" / name, "registered\n")

    report = audit_docs_graph.build_docs_graph_report(tmp_path)

    assert report["docs_count"] == audit_docs_graph.DOCS_HARD_MAX + 1
    assert report["docs_count_over_target"] > 0
    assert report["warnings"]
    assert report["hard_failures"]
    assert report["verdict"] == "FAIL"

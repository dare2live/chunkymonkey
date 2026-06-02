from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "services" / "codegraph_status.py"
SPEC = importlib.util.spec_from_file_location("codegraph_status", SCRIPT_PATH)
codegraph_status = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = codegraph_status
SPEC.loader.exec_module(codegraph_status)


def test_parse_codegraph_status_outputs_pending_json() -> None:
    text = """
\x1b[1mCodeGraph Status\x1b[0m
\x1b[36mProject:\x1b[0m /repo

\x1b[1mIndex Statistics:\x1b[0m
  Files:     1,041
  Nodes:     16,666
  Edges:     300,838
  DB Size:   171.47 MB

\x1b[1mNodes by Kind:\x1b[0m
  function        6,897
  class           449

\x1b[1mFiles by Language:\x1b[0m
  python          995
  javascript      22

\x1b[1mPending Changes:\x1b[0m
  Added:     44 files
  Modified:  2 files
"""

    status = codegraph_status.parse_codegraph_status(text)

    assert status["project"] == "/repo"
    assert status["index"]["files"] == 1041
    assert status["index"]["db_size"] == "171.47 MB"
    assert status["nodes_by_kind"] == {"function": 6897, "class": 449}
    assert status["files_by_language"]["python"] == 995
    assert status["pending"]["added"] == 44
    assert status["pending"]["modified"] == 2
    assert status["pending"]["total"] == 46
    assert status["pending"]["sync_required"] is True


def test_parse_codegraph_status_does_not_count_clean_marker_as_pending() -> None:
    status = codegraph_status.parse_codegraph_status("Project: /repo\nPending Changes:\n  No pending changes\n")

    assert status["pending"]["total"] == 0
    assert status["pending"]["sync_required"] is False

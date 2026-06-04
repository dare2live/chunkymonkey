from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "scripts" / "chunkyctl"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(WRAPPER, repo / "scripts" / "chunkyctl")
    _write(
        repo / "backend" / "scripts" / "chunkyctl.py",
        "from __future__ import annotations\n"
        "import json\n"
        "import sys\n"
        "print(json.dumps(sys.argv[1:]))\n",
    )
    return repo


def _run_wrapper(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/chunkyctl", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def test_preflight_wrapper_forwards_positional_scopes_and_agent_evidence(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)

    result = _run_wrapper(
        repo,
        "preflight",
        "审计 data_health",
        "goal.md",
        "--agent-dispatch",
        "Ptolemy read-only triage",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == [
        "preflight",
        "--scope",
        "goal.md",
        "--agent-dispatch",
        "Ptolemy read-only triage",
        "--task",
        "审计 data_health",
    ]


def test_preflight_wrapper_forwards_flag_task_scope_and_skip_reason(tmp_path: Path) -> None:
    repo = _make_fake_repo(tmp_path)

    result = _run_wrapper(
        repo,
        "preflight",
        "--task=审计 data_health",
        "--scope=goal.md",
        "--agent-skip-reason=tool unavailable",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == [
        "preflight",
        "--scope",
        "goal.md",
        "--agent-skip-reason",
        "tool unavailable",
        "--task",
        "审计 data_health",
    ]

"""CI paths-ignore must stay a strict subset of the L1 commit-tier surface.

`.github/workflows/ci.yml` skips CI for docs/board-only pushes. The policy
owner for "what is docs-only" is `backend/config/commit_tiers.yaml` (L1);
the workflow list is a projection. Unsafe drift direction: a path that is
(or becomes) L2/L3 sits in paths-ignore → risky code pushes silently skip
server-side CI gates. This test turns that drift red.

Safe drift (L1 gains a prefix that CI still runs) is allowed — CI merely
stays slower than necessary.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
COMMIT_TIERS = REPO / "backend" / "config" / "commit_tiers.yaml"


def _load_workflow_on() -> dict:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 parses the bare `on:` key as boolean True.
    trigger = workflow.get("on") or workflow.get(True)
    assert isinstance(trigger, dict), "ci.yml has no push/pull_request trigger map"
    return trigger


def _load_l1_surface() -> tuple[list[str], list[str]]:
    policy = yaml.safe_load(COMMIT_TIERS.read_text(encoding="utf-8"))
    prefixes = policy["l1_prefixes"]
    files = policy["l1_files"]
    assert prefixes and files, "commit_tiers.yaml L1 surface is empty"
    return prefixes, files


def _ignored_paths(trigger: dict, event: str) -> list[str]:
    section = trigger.get(event)
    assert isinstance(section, dict), f"ci.yml missing {event} trigger"
    ignored = section.get("paths-ignore")
    assert isinstance(ignored, list) and ignored, f"{event} paths-ignore missing/empty"
    return ignored


def test_paths_ignore_is_subset_of_l1_surface() -> None:
    trigger = _load_workflow_on()
    l1_prefixes, l1_files = _load_l1_surface()

    for event in ("push", "pull_request"):
        for pattern in _ignored_paths(trigger, event):
            if pattern.endswith("/**"):
                prefix = pattern[: -len("**")]  # 'docs/**' -> 'docs/'
                assert prefix in l1_prefixes, (
                    f"{event} paths-ignore entry {pattern!r} is not an L1 prefix; "
                    "CI would skip a non-docs-only surface (fix ci.yml, not this test)"
                )
            else:
                assert "*" not in pattern, (
                    f"{event} paths-ignore entry {pattern!r} uses an unaudited glob; "
                    "only '<l1_prefix>/**' and exact L1 files are allowed"
                )
                assert pattern in l1_files, (
                    f"{event} paths-ignore entry {pattern!r} is not an L1 file; "
                    "CI would skip a non-docs-only surface (fix ci.yml, not this test)"
                )


def test_push_and_pull_request_ignore_lists_agree() -> None:
    trigger = _load_workflow_on()
    assert sorted(_ignored_paths(trigger, "push")) == sorted(
        _ignored_paths(trigger, "pull_request")
    ), "push and pull_request paths-ignore drifted apart"


def test_red_case_l2_l3_prefix_would_fail() -> None:
    """Representative violation: backend code prefixes must never be ignorable."""
    l1_prefixes, l1_files = _load_l1_surface()
    for risky in ("backend/", "backend/services/", "scripts/", ".github/"):
        assert risky not in l1_prefixes, f"{risky!r} must not be classified L1"
    for risky_file in ("backend/main.py", "scripts/safe_commit.sh"):
        assert risky_file not in l1_files, f"{risky_file!r} must not be classified L1"

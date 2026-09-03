from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SAFE_COMMIT = REPO_ROOT / "scripts" / "safe_commit.sh"
MOTH_INVARIANTS_GATE = REPO_ROOT / "backend" / "scripts" / "check_moth_invariants.py"


def _run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **kwargs,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _continuity_report(
    *, overall: str = "PASS", statuses: tuple[str, ...] = ("pass",)
) -> dict:
    checks = []
    counts = {
        "pass": 0, "warn": 0, "fail": 0, "observe": 0,
        "skipped": 0, "db_unreachable": 0,
    }
    for status in statuses:
        checks.append({
            "status": status,
            "check": "calendar_gaps",
            "domain": "margin_detail",
            "table": "raw_tushare_margin_detail",
            "detail": "fixture detail",
            "fix_hint": "fixture hint",
        })
        category = (
            "fail" if status.startswith("fail")
            else "warn" if status.startswith("warn")
            else "pass" if status == "pass"
            else "observe" if status.startswith("observe_")
            else "db_unreachable" if status == "db_unreachable"
            else "skipped"
        )
        counts[category] += 1
    return {
        "overall": overall,
        "latest_expected": "20260716",
        "checks": checks,
        "summary": {"counts": counts, "by_check": {"calendar_gaps": counts.copy()}},
    }


def _write_continuity_stub(repo: Path, payload: dict, *, rc: int) -> None:
    rendered = json.dumps(payload, ensure_ascii=False)
    _write(
        repo / "backend" / "scripts" / "check_continuity_integrity.py",
        f"print({rendered!r})\nraise SystemExit({rc})\n",
    )


def _make_repo_with_staged_python(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(SAFE_COMMIT, repo / "scripts" / "safe_commit.sh")
    (repo / "backend" / "scripts").mkdir(parents=True)
    (repo / "backend" / "config").mkdir(parents=True)
    # 拷**真**脚本而非 stub: 本文件有一例专门验证 blocking 分流真的会阻断,
    # 用 stub 就变成「测 stub」而不是测分流逻辑 (语料 D 族原样复现)。
    shutil.copy2(MOTH_INVARIANTS_GATE, repo / "backend" / "scripts" / "check_moth_invariants.py")
    # WP1: fixture without classifier → safe_commit fail-closes to L3 full gates
    # (explicit stub keeps classify import path available for L1/L2 tier checks).
    _write(
        repo / "backend" / "scripts" / "classify_commit_tier.py",
        "import json\n"
        "print(json.dumps({'tier':'L3','gates':["
        "'staged_worktree_parity','doc_allowlist','brick_registry','legacy_raw_plane','moth','moth_invariants','rule_compliance',"
        "'sandbox_isolation','serve_read_layer','calendar_usage',"
        "'population_contract','lineage_drift','dead_references',"
        "'grain_uniqueness','continuity','no_emoji','config_refs','tushare_sunset'],"
        "'reasons':['fixture_l3'],'paths':[]}))\n",
    )
    # 2026-08-11: agent_board 门随 BOARD.md 退役(P2.3); no_emoji / doc_runtime_state 新登记。
    # always-on 的 ci-surface-drift 不看 tier, 沙箱必须真有这个测试文件, 否则 Step 3.35
    # 直接 exit 3 —— 这正是本文件长期 25 例全红的根因(全死在门体系之前, 与被测逻辑无关)。
    _write(
        repo / "backend" / "tests" / "scripts" / "test_ci_pytest_surface_drift.py",
        "def test_stub():\n    assert True\n",
    )
    _write(repo / "backend" / "scripts" / "check_no_emoji.py", "raise SystemExit(0)\n")
    # gate_policy 不可用时 safe_commit 走 fail-closed(全阻断) —— 沙箱给最小实现, 让
    # 分组语义(scaffold=warn / system_health=skip)在测试里也真的生效。
    _write(
        repo / "backend" / "scripts" / "gate_policy.py",
        "import sys\n"
        "g = sys.argv[sys.argv.index('--names')+1] if '--names' in sys.argv else ''\n"
        "print({'scaffold':'moth tushare_sunset',"
        "'system_health':'grain_uniqueness continuity'}.get(g,''))\n",
    )
    _write(repo / "backend" / "config" / "commit_tiers.yaml", "version: 1\n")
    _write(repo / "backend" / "scripts" / "check_rule_compliance.py", "raise SystemExit(0)\n")
    # 2026-07-02 沙箱跟上 safe_commit 新门 (3.8 sandbox/3.9 serve/3.95 calendar/3.97 dead-references;
    #   pre-existing 6失败根因=沙箱缺这些脚本 → python 非0 → exit 5)
    _write(repo / "backend" / "scripts" / "check_sandbox_isolation.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_serve_read_layer.py", "raise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_calendar_usage.py", "raise SystemExit(0)\n")
    _write(
        repo / "backend" / "scripts" / "check_universe_filter.py",
        "import json\n"
        "print(json.dumps({'verdict':'PASS','formal_dataset_count':1,"
        "'live_readiness':'NOT_EVALUATED'}))\n",
    )
    _write(repo / "backend" / "scripts" / "check_dead_references.py", "print('[dead-references] PASS')\nraise SystemExit(0)\n")
    # 2026-07-04 沙箱跟上 Step 3.98 (grain-uniqueness 门, 本 session R1 新 wire): 沙箱无真实
    #   sync_registry/数据库, 未 stub 会真跑该脚本报错退出非0 (与上条同款坑, 非本次逻辑改动引入)。
    _write(repo / "backend" / "scripts" / "check_grain_uniqueness.py", "print('grain-uniqueness: stub PASS')\nraise SystemExit(0)\n")
    # 2026-07-06 沙箱跟上 Step 3.99 (continuity-integrity 门, 全面数据审计根因根治新 wire):
    #   同款坑, 沙箱无真实 sync_registry/数据库不 stub 会真跑报错退出非0。
    _write_continuity_stub(repo, _continuity_report(), rc=0)
    # Step 3.99 (tushare-sunset 门, scaffold 组): 沙箱提供最小 yaml + 默认 PASS stub,
    # 否则 test_scaffold_gate_failure_warns_but_never_goes_silent[tushare_sunset] 会在
    # "缺 tushare_sunset.yaml" 分支就已经 WARN, 测不到脚本本身失败这条路径。
    _write(repo / "backend" / "config" / "tushare_sunset.yaml", "version: 1\n")
    _write(repo / "backend" / "config" / "sync_registry.yaml", "version: 1\n")
    _write(repo / "backend" / "scripts" / "check_tushare_sunset.py", "raise SystemExit(0)\n")
    # Step 3.991/3.992/3.993/3.994 都是硬闸；空测试仓库必须提供确定性 stub。
    _write(repo / "backend" / "scripts" / "check_config_refs.py",
           "print('[config-refs] PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_tracked_allowlist.py",
           "print('[doc-allowlist] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_brick_registry.py",
           "print('[brick-registry] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_legacy_raw_plane.py",
           "print('[legacy-raw-plane] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "backend" / "scripts" / "check_lineage_drift.py",
           "print('[lineage-drift] stub PASS')\nraise SystemExit(0)\n")
    _write(repo / "README.md", "seed\n")

    assert _run(["git", "init"], repo).returncode == 0
    assert _run(["git", "config", "user.email", "test@example.com"], repo).returncode == 0
    assert _run(["git", "config", "user.name", "Test User"], repo).returncode == 0
    assert _run(["git", "add", "."], repo).returncode == 0
    assert _run(["git", "commit", "-m", "initial"], repo).returncode == 0

    _write(repo / "backend" / "sample.py", "print('sample')\n")
    assert _run(["git", "add", "backend/sample.py"], repo).returncode == 0
    return repo


def _success_codegraph_env(repo: Path, **extra: str) -> dict[str, str]:
    fake_bin = repo.parent / "fake-codegraph-success"
    fake_bin.mkdir(exist_ok=True)
    fake_codegraph = fake_bin / "codegraph"
    _write(
        fake_codegraph,
        """#!/usr/bin/env bash
set -eu
case "${1:-}" in
  init)
    touch "$2/.fake-codegraph-initialized"
    ;;
  sync)
    if [[ -n "${2:-}" && ! -f "$2/.fake-codegraph-initialized" ]]; then
      exit 9
    fi
    ;;
  *) exit 9 ;;
esac
""",
    )
    fake_codegraph.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        **extra,
    }


def _safe_commit(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = _success_codegraph_env(repo, SAFE_COMMIT_DRY_RUN="1")
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def _safe_commit_no_push(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    env = _success_codegraph_env(repo, SAFE_COMMIT_NO_PUSH="1")
    return _run(["bash", "scripts/safe_commit.sh", message], repo, env=env)


def _stage_lineage_fixture(repo: Path, *, source: str, rendered: str) -> None:
    _write(
        repo / "backend" / "scripts" / "check_lineage_drift.py",
        """from pathlib import Path

root = Path(__file__).resolve().parents[2]
expected = f"lineage:{(root / 'LINEAGE_SOURCE.txt').read_text(encoding='utf-8').strip()}\\n"
actual = (root / 'data' / 'lineage' / 'graph.json').read_text(encoding='utf-8')
raise SystemExit(0 if actual == expected else 1)
""",
    )
    _write(repo / "LINEAGE_SOURCE.txt", source)
    _write(repo / "data" / "lineage" / "graph.json", rendered)
    assert _run(
        [
            "git",
            "add",
            "backend/scripts/check_lineage_drift.py",
            "LINEAGE_SOURCE.txt",
            "data/lineage/graph.json",
        ],
        repo,
    ).returncode == 0


@pytest.mark.parametrize(
    "gate,script",
    [
        ("tushare_sunset", "check_tushare_sunset.py"),
    ],
)
def test_scaffold_gate_failure_warns_but_never_goes_silent(
    tmp_path: Path, gate: str, script: str
) -> None:
    """B3: 降级只准去掉**阻断力**, 不准连**检测**一起丢。

    warn-only 最容易悄悄退化成 warn-nothing —— 失败被 `|| true` 吞掉、输出被 `2>/dev/null`
    盖住, 表面全绿实则这道门已经死了, 而且**没有任何信号**说它死了(比它直接报红更危险)。
    所以每道 scaffold 门都必须证明两件事同时成立: 点名自己 + 不阻断。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    _write(repo / "backend" / "scripts" / script, "raise SystemExit(1)\n")
    assert _run(["git", "add", f"backend/scripts/{script}"], repo).returncode == 0
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert f"WARN-ONLY [{gate}]" in result.stdout, "降级后必须仍然点名报出来"
    assert result.returncode == 0, "scaffold 门不得阻断提交"

def test_system_health_gates_are_not_run_at_commit(tmp_path: Path) -> None:
    """P1: continuity / grain 查的是 live 数据, 与本次 diff 无关 —— commit 路径不跑。

    原先这里有 13 个用例断言 continuity 在 commit 路径的各种降级语义; 那套行为已整体
    归位 `daily_update` 的 system_health 自检(见 governance_gates.yaml)。留着它们就是
    在测一个不再存在的执法点。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert result.returncode == 0
    for gate in ("grain_uniqueness", "continuity"):
        assert f"skip {gate}" in result.stdout, f"{gate} 应在 commit 路径被跳过"
    assert "LIVE DATA READINESS" not in result.stdout, "commit 不再产生任何 readiness 声明"

def test_stale_staged_lineage_graph_blocks_commit(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_lineage_fixture(repo, source="v2\n", rendered="lineage:v1\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 5
    assert "staged 血缘图" in result.stdout


def test_lineage_gate_ignores_unstaged_worktree_change(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _stage_lineage_fixture(repo, source="v2\n", rendered="lineage:v2\n")
    _write(repo / "LINEAGE_SOURCE.txt", "v3-unstaged\n")

    result = _safe_commit(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "staged snapshot PASS" in result.stdout
    assert "SAFE_COMMIT_DRY_RUN=1" in result.stdout


def test_moth_gate_runs_from_exported_staged_snapshot(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    _write(repo / ".moth" / "profile.yaml", "kind: profile\nname: fixture\nrepo_path: .\n")
    assert _run(["git", "add", ".moth/profile.yaml"], repo).returncode == 0

    env = _success_codegraph_env(repo, SAFE_COMMIT_DRY_RUN="1")
    fake_bin = Path(env["PATH"].split(os.pathsep, 1)[0])
    calls = tmp_path / "moth-calls.txt"
    fake_moth = fake_bin / "moth"
    _write(
        fake_moth,
        """#!/usr/bin/env bash
set -eu
[[ "$PWD" == /tmp/chunkymonkey-staged-index.* || "$PWD" == /private/tmp/chunkymonkey-staged-index.* ]]
[[ -f .moth/profile.yaml ]]
[[ "${2:-}" == "--repo" ]]
[[ "${3:-}" == "." ]]
printf '%s\n' "${1:-}" >> "$MOTH_CALLS"
""",
    )
    fake_moth.chmod(0o755)
    env["MOTH_CALLS"] = str(calls)

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env=env,
    )

    assert result.returncode == 0
    assert "[moth] staged snapshot PASS" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == ["assert", "coupling"]


def test_safe_commit_no_push_commits_without_remote_push(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)

    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")

    assert result.returncode == 0
    assert "SAFE_COMMIT_NO_PUSH=1: skipping git push." in result.stdout
    assert "DONE: commit + no-push + codegraph sync 完成" in result.stdout
    log = _run(["git", "log", "-1", "--format=%B"], repo)
    assert "Codex-Reviewed: APPROVE" in log.stdout


def test_codegraph_sync_failure_is_reported_after_commit(tmp_path: Path) -> None:
    repo = _make_repo_with_staged_python(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codegraph = fake_bin / "codegraph"
    _write(
        fake_codegraph,
        """#!/usr/bin/env bash
if [[ "${1:-}" == "init" ]]; then exit 0; fi
if [[ "${1:-}" == "sync" && -n "${2:-}" ]]; then exit 0; fi
exit 9
""",
    )
    fake_codegraph.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "SAFE_COMMIT_NO_PUSH": "1",
    }

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env=env,
    )

    assert result.returncode == 7
    assert "commit 已创建，但 CodeGraph sync 失败" in result.stdout
    assert "Codex-Reviewed: APPROVE" in _run(
        ["git", "log", "-1", "--format=%B"], repo
    ).stdout


def test_moth_gate_runs_coupling_even_when_assert_fails(tmp_path: Path) -> None:
    """B3: assert 挂掉不得吞掉 coupling —— 两项独立跑, 不许 elif 短路。

    warn-only 把 gate_fail 从 `exit` 变成 `return 0`, 于是原来无害的 `if A; then …
    elif B; then …` 变成了「A 一挂 B 永远不跑」。表面上门还在, 实际少查一半, 且没有信号。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    (repo / ".moth").mkdir(exist_ok=True)
    _write(repo / ".moth" / "profile.yaml", "version: 1\n")
    bin_dir = repo / "fakebin"
    bin_dir.mkdir()
    log = repo / "moth_calls.log"
    # assert 必失败, coupling 必成功; 若被短路吞掉, log 里就不会有 coupling。
    stub = bin_dir / "moth"
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$1" >> "{log}"\n'
        'if [ "$1" = "assert" ]; then exit 1; fi\nexit 0\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    assert _run(["git", "add", "-A"], repo).returncode == 0

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env={**os.environ, "SAFE_COMMIT_NO_PUSH": "1", "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    calls = log.read_text(encoding="utf-8").split() if log.exists() else []
    assert "assert" in calls, "前置条件: assert 应被调用"
    assert "coupling" in calls, "assert 失败后 coupling 仍必须跑 —— 不许 elif 短路"
    assert "WARN-ONLY [moth]" in result.stdout


def test_staged_worktree_drift_blocks_commit(tmp_path: Path) -> None:
    """`git add` 之后又编辑 —— 你测的和你要提交的不是同一份。

    ci_pytest 门刻意拿 live worktree 跑(测试需要 repo 的 pytest.ini 与 fixture), 所以它对
    这种漂移**结构性失明**: 跑的是手上的版本, 提交的是索引里的版本。2026-08-11 同一天咬两次
    (一次提交声明落空, 一次带着本地已修好的红测试上线导致 CI 红), 故立此门。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    # backend/sample.py 已 staged (见 fixture); 现在只改工作树, 不 git add。
    _write(repo / "backend" / "sample.py", "print('edited after git add')\n")

    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert result.returncode != 0, "staged 与工作树不一致必须阻断"
    assert "backend/sample.py" in result.stdout, "必须点名是哪个文件漂移了"
    assert "staged" in result.stdout.lower()


def test_staged_worktree_parity_passes_when_index_matches(tmp_path: Path) -> None:
    """反向: 没有漂移时这道门必须放行, 不能变成拦一切的噪音门。"""
    repo = _make_repo_with_staged_python(tmp_path)
    result = _safe_commit_no_push(repo, "test audit\nCodex-Reviewed: APPROVE")
    assert result.returncode == 0
    assert "[staged-worktree-parity] PASS" in result.stdout


def test_moth_blocking_invariant_failure_blocks_commit(tmp_path: Path) -> None:
    """数据不变量失败必须**阻断**, 哪怕 moth 门本身只是 warn-only。

    2026-08-14 分流的全部意义就在这里: 此前 38 条断言按整体继承 moth 的 scaffold 分组,
    于是「日历起点回退」这类**数据此刻就是错的**也只是 warn。本例用一个会吐 JSON 的 stub moth
    模拟 calendar-floor 失败, 断言提交被拦 —— 若哪天分流被抹平, 这条必红。
    """
    repo = _make_repo_with_staged_python(tmp_path)
    (repo / ".moth" / "assertions").mkdir(parents=True, exist_ok=True)
    _write(repo / ".moth" / "profile.yaml", "version: 1\n")
    _write(
        repo / ".moth" / "assertions" / "claims.yaml",
        "assertions:\n  - id: calendar-floor\n    severity: blocking\n",
    )
    bin_dir = repo / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "moth"
    # assert 自身 exit 0(即 moth 门不报错), 但 JSON 里 calendar-floor 是 fail ——
    # 这样才能证明**分流**在起作用, 而不是搭了 moth 门的便车。
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "assert" ]; then\n'
        '  echo \'{"packs":[{"results":['
        '{"id":"calendar-floor","status":"fail","detail":"模拟回退"}]}]}\'\n'
        "  exit 0\n"
        "fi\nexit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    assert _run(["git", "add", "-A"], repo).returncode == 0

    result = _run(
        ["bash", "scripts/safe_commit.sh", "test audit\nCodex-Reviewed: APPROVE"],
        repo,
        env={**os.environ, "SAFE_COMMIT_NO_PUSH": "1", "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0, "blocking 不变量失败必须阻断提交"
    assert "calendar-floor" in result.stdout, "必须点名是哪条不变量"


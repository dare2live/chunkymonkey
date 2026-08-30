from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from scripts import check_universe_filter as gate


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _source(repo: Path, name: str, text: str = "VALUE = 1\n") -> Path:
    path = repo / "backend" / "services" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _live_margin_registry() -> dict:
    live = yaml.safe_load(
        (gate.REPO_ROOT / gate.REGISTRY_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    # sources 段 (2026-08-30 新增: target_db 等源级参数从 defaults 移入) 必须随 defaults/domains
    # 一并搬进这份合成 registry, 否则 domain_spec() 在这里解不到 margin 域的 target_db。
    return {
        "defaults": live["defaults"],
        "sources": live.get("sources", {}),
        "domains": {"margin": live["domains"]["margin"]},
    }


def _write_inputs(repo: Path, registry: dict) -> Path:
    registry_path = repo / gate.REGISTRY_RELATIVE_PATH
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
    policy = repo / gate.POLICY_RELATIVE_PATH
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        (gate.REPO_ROOT / gate.POLICY_RELATIVE_PATH).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return registry_path


def test_empty_source_snapshot_fails_closed(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    report = gate.audit_repository(tmp_path)
    assert report["verdict"] == "FAIL"
    assert report["source_count"] == 0
    assert any(issue["code"] == "no_source" for issue in report["issues"])


def test_worktree_includes_untracked_source_and_omits_deleted_file(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    deleted = _source(tmp_path, "deleted.py")
    _git(tmp_path, "add", deleted.relative_to(tmp_path).as_posix())
    deleted.unlink()
    _source(tmp_path, "untracked.py")
    registry_path = _write_inputs(tmp_path, _live_margin_registry())

    report = gate.audit_repository(tmp_path, registry_path=registry_path)
    assert report["verdict"] == "PASS"
    assert report["source_mode"] == "worktree"
    assert report["source_count"] == 1
    assert report["formal_dataset_count"] == 1
    assert report["scope_counts"] == {"external_aggregate": 1}
    # live_readiness must be evaluated via observation loaders, not a dead constant.
    # Hosts with an accepted eligible-frontier canary may honestly report READY;
    # offline/missing hosts stay NOT_EVALUATED/BLOCKED with explicit reasons.
    assert report["live_readiness"] in {"NOT_EVALUATED", "BLOCKED", "DEGRADED", "READY"}
    detail = report["live_readiness_detail"]
    assert detail["status"] == report["live_readiness"]
    assert detail.get("reasons")
    if report["live_readiness"] == "READY":
        assert detail.get("observation_date")
        assert any("accepted_calendar_kline_st" in reason for reason in detail["reasons"])
    else:
        # 只要求 reasons 点名**它实际检查到的那个**数据集, 不要求两个都出现:
        # readiness 在第一个缺失的数据集处短路返回是正确行为(缺 K 线时不必再查 ST),
        # 于是 reasons 可能只有一条。2026-08-21 实测: 补齐 stock_st 到 20260821 后
        # 该域已就绪、K 线因 available_after=18:00 尚未发布, reasons 只剩 nominal_ohlcv
        # 一条 —— 原断言要求两条同时出现, 等于断言宿主数据处于某个特定残缺状态。
        assert any(
            ds in reason
            for reason in detail["reasons"]
            for ds in ("nominal_ohlcv", "stock_st", "trading_calendar")
        ), detail["reasons"]


def test_index_mode_uses_staged_inventory_not_clean_worktree(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    tracked = _source(tmp_path, "tracked.py")
    registry_path = _write_inputs(tmp_path, _live_margin_registry())
    _git(
        tmp_path,
        "add",
        tracked.relative_to(tmp_path).as_posix(),
        registry_path.relative_to(tmp_path).as_posix(),
        gate.POLICY_RELATIVE_PATH.as_posix(),
    )
    tracked.write_text("WORKTREE_ONLY = 2\n", encoding="utf-8")

    report = gate.audit_repository(tmp_path, source_mode="index")
    assert report["verdict"] == "PASS"
    assert report["source_mode"] == "index"
    assert report["source_count"] == 1


def test_invalid_staged_policy_returns_structured_failure(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    tracked = _source(tmp_path, "tracked.py")
    registry_path = _write_inputs(tmp_path, _live_margin_registry())
    policy_path = tmp_path / gate.POLICY_RELATIVE_PATH
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["eligibility"]["rule"] = "future_window"
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    _git(
        tmp_path,
        "add",
        tracked.relative_to(tmp_path).as_posix(),
        registry_path.relative_to(tmp_path).as_posix(),
        gate.POLICY_RELATIVE_PATH.as_posix(),
    )

    report = gate.audit_repository(tmp_path, source_mode="index")
    assert report["verdict"] == "FAIL"
    assert any(item["code"] == "contract_inputs_unreadable" for item in report["issues"])


def test_disabled_formal_dataset_still_requires_population_scope(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _source(tmp_path, "example.py")
    registry = _live_margin_registry()
    registry["domains"]["margin"].pop("population_scope")
    registry_path = _write_inputs(tmp_path, registry)

    report = gate.audit_repository(tmp_path, registry_path=registry_path)
    assert report["verdict"] == "FAIL"
    issue = next(item for item in report["issues"] if item["code"] == "population_scope_invalid")
    assert issue["domain"] == "margin"


def test_exchange_aggregate_cannot_masquerade_as_project_security_scope(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _source(tmp_path, "example.py")
    registry = _live_margin_registry()
    scope = registry["domains"]["margin"]["population_scope"]
    scope.clear()
    scope.update(
        {
            "kind": "project_universe_pit",
            "universe_policy_id": "active_a_share_trading_universe",
            "security_field": "exchange_id",
            "as_of_field": "trade_date",
            "as_of_role": "observation_time",
        }
    )
    registry_path = _write_inputs(tmp_path, registry)
    report = gate.audit_repository(tmp_path, registry_path=registry_path)
    assert report["verdict"] == "FAIL"
    assert "security_field must be 'ts_code'" in report["issues"][0]["message"]


def test_skip_live_readiness_keeps_static_contract_offline(
    tmp_path: Path, monkeypatch
) -> None:
    """CI path: static population contract must not require live DuckDB."""

    _git(tmp_path, "init", "-q")
    _source(tmp_path, "example.py")
    registry_path = _write_inputs(tmp_path, _live_margin_registry())

    calls: list[str] = []

    def _boom(_policy):
        calls.append("evaluated")
        raise OSError('Cannot open database "data/tushare_raw.duckdb"')

    monkeypatch.setattr(gate, "evaluate_observation_population_readiness", _boom)
    report = gate.audit_repository(
        tmp_path, registry_path=registry_path, skip_live_readiness=True
    )
    assert report["verdict"] == "PASS"
    assert report["live_readiness"] == "NOT_EVALUATED"
    assert report["live_readiness_detail"]["reasons"] == ["live_readiness_skipped"]
    assert calls == []


def test_live_readiness_io_failure_is_typed_not_evaluated(tmp_path: Path, monkeypatch) -> None:
    """Missing DB must not escape as bare IO / kill the static gate."""

    _git(tmp_path, "init", "-q")
    _source(tmp_path, "example.py")
    registry_path = _write_inputs(tmp_path, _live_margin_registry())

    def _boom(_policy):
        raise OSError('Cannot open database "data/tushare_raw.duckdb"')

    monkeypatch.setattr(gate, "evaluate_observation_population_readiness", _boom)
    report = gate.audit_repository(tmp_path, registry_path=registry_path)
    assert report["verdict"] == "PASS"
    assert report["live_readiness"] == "NOT_EVALUATED"
    assert any(
        "live_readiness_not_evaluated" in reason
        for reason in report["live_readiness_detail"]["reasons"]
    )


def test_main_skip_live_readiness_flag(monkeypatch, capsys) -> None:
    seen: dict[str, object] = {}

    def _capture(**kwargs):
        seen.update(kwargs)
        return {
            "verdict": "PASS",
            "source_mode": "worktree",
            "source_count": 1,
            "formal_dataset_count": 1,
            "scope_counts": {},
            "issues": [],
            "live_readiness": "NOT_EVALUATED",
            "live_readiness_detail": {"status": "NOT_EVALUATED", "reasons": ["live_readiness_skipped"]},
        }

    monkeypatch.setattr(gate, "audit_repository", _capture)
    assert gate.main(["--format", "json", "--skip-live-readiness"]) == 0
    assert seen.get("skip_live_readiness") is True
    assert json.loads(capsys.readouterr().out)["live_readiness"] == "NOT_EVALUATED"


def test_main_json_failure_is_nonzero(monkeypatch, capsys) -> None:
    report = {
        "verdict": "FAIL",
        "source_mode": "index",
        "source_count": 7,
        "formal_dataset_count": 2,
        "scope_counts": {},
        "issues": [{"code": "example", "message": "bad"}],
        "live_readiness": "NOT_EVALUATED",
    }
    monkeypatch.setattr(gate, "audit_repository", lambda **_kwargs: report)
    assert gate.main(["--format", "json", "--staged"]) == 1
    assert json.loads(capsys.readouterr().out) == report

"""门分布策略 (goal.md「治理体系重构」P1) 的机械锁。

覆盖三件事，都是「说了就得算数」的不变量：

1. 登记表本身合法，且三处门名副本 (registry / classify_commit_tier /
   safe_commit.sh 兜底串) 不漂移；
2. ``system_health`` 组从 commit 路径摘掉之后**真的有人接手** —— 每道都挂在
   ``runtime_checks`` 上，且 daily_update 的 store 阶段真的会跑；
3. ``scaffold`` 组在 safe_commit 里走 ``gate_fail`` (由分组决定后果)，而不是
   继续硬编码 ``exit``；always-on 的 ci-surface-drift 不受分组影响仍然阻断。

全部离线：b_pit / tier12 resolver 只读 config + lineage artifact，不连 DB；
需要交易日历的那一条用 monkeypatch 顶掉。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from pathlib import Path

import pytest
import yaml

from scripts import check_cutover_effective as cce
from scripts.classify_commit_tier import ALL_GATES_ORDERED
from services import governance_gates as gg


REPO = Path(__file__).resolve().parents[3]
SAFE_COMMIT = REPO / "scripts" / "safe_commit.sh"
STORE = REPO / "backend" / "services" / "pipeline" / "store.py"


# ── 1. 登记表合法性与三处一致 ───────────────────────────────────────────
def test_live_registry_loads() -> None:
    reg = gg.load_registry()
    assert len(reg.gates) == len(ALL_GATES_ORDERED)
    assert set(reg.gate_names) == set(ALL_GATES_ORDERED)


def test_every_gate_has_a_known_group() -> None:
    reg = gg.load_registry()
    covered = sum(len(reg.names_in_group(g)) for g in gg.KNOWN_GROUPS)
    assert covered == len(reg.gates), "分组必须是全覆盖且互斥的划分"


def test_safe_commit_fallback_list_matches_registry() -> None:
    """safe_commit.sh 的 fail-closed 兜底串是最后一道防线，不能与登记表漂移。"""
    text = SAFE_COMMIT.read_text(encoding="utf-8")
    match = re.search(r'^COMMIT_TIER_GATES="([^"]*)"', text, re.M)
    assert match, "safe_commit.sh 必须保留 fail-closed 兜底串"
    assert set(match.group(1).split()) == set(gg.load_registry().gate_names)


def test_commit_behavior_is_owned_by_the_group_not_the_gate() -> None:
    reg = gg.load_registry()
    assert reg.groups["diff_correctness"]["commit_behavior"] == "block"
    assert reg.groups["system_health"]["commit_behavior"] == "not_run"
    assert reg.groups["scaffold"]["commit_behavior"] == "warn"


def test_registry_is_fail_closed_on_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "governance_gates.yaml"
    bad.write_text("version: 1\ngroups: {}\n", encoding="utf-8")
    with pytest.raises(gg.GatePolicyError):
        gg.load_registry(bad)
    missing = tmp_path / "nope.yaml"
    with pytest.raises(gg.GatePolicyError):
        gg.load_registry(missing)


# ── R1/R6 死亡条件字段 (object/invariant/kill_when/paradigm, 2026-09-03) ──────
# mio #7 实证边界: 40 道门 8 事故 32 计划催生, 维护占比 5 月 2% → 9 月 43%。每道门
# 现在必须自带「守谁/守什么/对象消失时怎么死/哪个范式」四个字段, 缺一不可 (与既有
# group/checks/why 同一处 fail-closed); object 命中 0 不是 loader 错误, 是 DEAD_GATE
# 信号 —— 门自己红要求删门, 不阻断提交 (见 gate_policy.py --check / safe_commit.sh §1.65)。
def _raw_registry() -> dict:
    return yaml.safe_load(
        (REPO / "backend" / "config" / "governance_gates.yaml").read_text(encoding="utf-8")
    )


def _write_mutated(tmp_path: Path, mutate) -> Path:
    raw = _raw_registry()
    mutate(raw)
    path = tmp_path / "governance_gates.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return path


def test_every_gate_declares_all_four_death_fields() -> None:
    reg = gg.load_registry()
    for spec in reg.gates:
        assert spec.object.strip(), f"{spec.name}.object 不能为空"
        assert spec.invariant.strip(), f"{spec.name}.invariant 不能为空"
        assert spec.kill_when.strip(), f"{spec.name}.kill_when 不能为空"
        assert spec.paradigm in gg.KNOWN_PARADIGMS, f"{spec.name}.paradigm 未知: {spec.paradigm!r}"


def test_no_dead_gate() -> None:
    """全部 object 至少命中一个真实路径 —— 0 个 DEAD_GATE 是现状, 不是巧合。

    红了只有一种修法: 删那道门 (YAML 条目 + safe_commit.sh Step + 测试), 不是把
    object 改到能凑合命中的地方 —— 那正是这道门试图揭穿的 「declared vs actual」漂移。
    """
    reg = gg.load_registry()
    dead = gg.dead_gate_report(reg)
    assert not dead, (
        "DEAD_GATE (object 命中 0 个路径, 删这道门): "
        + "; ".join(f"{name}: {items}" for name, items in sorted(dead.items()))
    )


@pytest.mark.parametrize("field", ["object", "invariant", "kill_when", "paradigm"])
def test_missing_death_field_raises(tmp_path: Path, field: str) -> None:
    path = _write_mutated(tmp_path, lambda raw: raw["commit_gates"]["dead_references"].pop(field))
    with pytest.raises(gg.GatePolicyError, match=re.escape(f"commit_gates.dead_references.{field}")):
        gg.load_registry(path)


def test_kill_when_bad_prefix_raises(tmp_path: Path) -> None:
    def mutate(raw: dict) -> None:
        raw["commit_gates"]["dead_references"]["kill_when"] = "someday, maybe, who knows"

    path = _write_mutated(tmp_path, mutate)
    with pytest.raises(gg.GatePolicyError, match="delete / move_to:<group> / keep_forever:"):
        gg.load_registry(path)


def test_keep_forever_without_reason_raises(tmp_path: Path) -> None:
    def mutate(raw: dict) -> None:
        raw["commit_gates"]["dead_references"]["kill_when"] = "keep_forever:"

    path = _write_mutated(tmp_path, mutate)
    with pytest.raises(gg.GatePolicyError, match="keep_forever: 后必须带理由"):
        gg.load_registry(path)


def test_paradigm_unknown_enum_raises(tmp_path: Path) -> None:
    def mutate(raw: dict) -> None:
        raw["commit_gates"]["dead_references"]["paradigm"] = "vibes"

    path = _write_mutated(tmp_path, mutate)
    with pytest.raises(gg.GatePolicyError, match="unknown paradigm"):
        gg.load_registry(path)


def test_object_zero_glob_hits_is_dead_gate_not_a_load_error() -> None:
    """DEAD_GATE 是 --check 时机器强制的信号, 不是 load_registry 本身的 fail-closed 错误——

    否则一道门守的文件被删掉的瞬间, 它会把**加载整份策略**都炸掉 (全部门连带阻断),
    这比它本来该做的事 (提示删自己) 严重得多。
    """
    reg = gg.load_registry()
    gates = list(reg.gates)
    for i, spec in enumerate(gates):
        if spec.name == "dead_references":
            gates[i] = dataclasses.replace(spec, object="backend/this_path_definitely_absent_xyz/**/*.py")
    mutated = dataclasses.replace(reg, gates=tuple(gates))
    dead = gg.dead_gate_report(mutated)
    assert dead == {"dead_references": ["backend/this_path_definitely_absent_xyz/**/*.py"]}


def test_table_and_rule_prefixed_objects_skip_filesystem_check() -> None:
    """`table:`/`rule:` 条目不查文件系统 —— 不误报 DEAD_GATE。"""
    reg = gg.load_registry()
    gates = list(reg.gates)
    for i, spec in enumerate(gates):
        if spec.name == "dead_references":
            gates[i] = dataclasses.replace(
                spec, object="table:no_such_db.no_such_table, rule:no_such_rule_id"
            )
    mutated = dataclasses.replace(reg, gates=tuple(gates))
    assert gg.dead_gate_report(mutated) == {}


def test_system_health_gate_removed_from_registry_must_break_loudly(tmp_path: Path) -> None:
    """把一道门标成 system_health 却不挂运行时自检 = 静默删检查，必须抛错。"""
    raw = yaml.safe_load(
        (REPO / "backend" / "config" / "governance_gates.yaml").read_text(encoding="utf-8")
    )
    raw["commit_gates"]["moth"]["group"] = "system_health"
    path = tmp_path / "governance_gates.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(gg.GatePolicyError, match="not wired into runtime_checks"):
        gg.load_registry(path)


# ── 2. system_health 组真的挂在 daily_update 上 ─────────────────────────
def test_system_health_gates_are_all_wired_to_runtime_checks() -> None:
    reg = gg.load_registry()
    wired = {c.from_gate for c in reg.runtime_checks if c.from_gate}
    for name in reg.names_in_group("system_health"):
        assert name in wired, f"{name} 从 commit 路径摘掉了却没人接手"


def test_store_runs_the_registry_not_a_hand_list() -> None:
    """store.py 必须消费登记表；再出现第二份手写清单就等于分家失败。"""
    text = STORE.read_text(encoding="utf-8")
    assert "run_system_health_checks" in text
    assert "run_runtime_checks" in text
    for legacy in ("check_continuity_integrity.py", "check_residual_hygiene.py"):
        assert legacy not in text, f"{legacy} 应该只登记在 governance_gates.yaml 里"


def test_runtime_check_rendering_and_dry_skip() -> None:
    reg = gg.load_registry()
    calls: list[tuple[str, list[str]]] = []

    def runner(spec: gg.RuntimeCheckSpec, args: list[str]) -> int:
        calls.append((spec.id, args))
        return 0

    rows = gg.run_runtime_checks(runner, date="20260811", registry=reg)
    assert len(rows) == len(reg.runtime_checks)
    assert all(r["status"] == "pass" for r in rows)
    assert not any("{date}" in a for _, args in calls for a in args)
    assert any("20260811" in a for _, args in calls for a in args)
    assert not any("{date}" in r["degraded_msg"] for r in rows)

    calls.clear()
    dry_rows = gg.run_runtime_checks(runner, date="20260811", dry=True, registry=reg)
    skipped = {r["id"] for r in dry_rows if r["status"] == "skipped_dry"}
    assert skipped == {c.id for c in reg.runtime_checks if c.skip_when_dry}
    assert skipped and skipped != {c.id for c in reg.runtime_checks}, (
        "dry-run 该跳过写库类自检，但不该把整组都跳掉"
    )


def test_runtime_check_failure_is_reported_not_swallowed() -> None:
    rows = gg.run_runtime_checks(lambda spec, args: 1, date="20260811")
    assert rows and all(r["status"] == "fail" for r in rows)
    assert all(r["degraded_msg"] for r in rows)


def test_degraded_messages_classify_as_integrity_observe() -> None:
    """自检的 degraded 文案必须被 run_outcome 认成完整性观测，不是「等时钟」。"""
    from services.pipeline.run_outcome import classify_msg

    for spec in gg.load_registry().runtime_checks:
        msg = spec.rendered_degraded_msg(date="20260811")
        assert classify_msg(msg) == "integrity", f"{spec.id} 的文案会被误分类"


# ── 3. scaffold 组在 commit 路径的后果由分组决定 ────────────────────────
def test_gates_use_gate_fail_instead_of_hardcoded_exit() -> None:
    # 只扫**调用点**: 注释里解释 gate_fail 语义的散文(如「gate_fail 返回 0」)不是调用,
    # 扫进来会把中文词当成门名。剥掉整行注释再匹配。
    text = "\n".join(
        ln for ln in SAFE_COMMIT.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    called = set(re.findall(r"gate_fail (\w+) ", text))
    reg = gg.load_registry()
    # commit_msg 没有失败路径 (它本来就只打印 WARNING)。
    expected = set(reg.gate_names) - {"commit_msg"}
    assert expected <= called, f"仍在硬编码 exit 的门: {sorted(expected - called)}"
    assert called <= set(reg.gate_names)


def test_always_on_ci_surface_drift_stays_blocking() -> None:
    """它不在 19 门里，也不受分组影响 —— 是 diff 正确性的 always-on 底线。"""
    text = SAFE_COMMIT.read_text(encoding="utf-8")
    block = text.split("Step 3.35")[1].split("Step 3.4")[0]
    assert "gate_fail" not in block
    assert "exit 3" in block


def test_scaffold_fix_entrypoint_exists_for_every_regenerable_artifact() -> None:
    reg = gg.load_registry()
    fixes = {f.from_gate: f for f in reg.scaffold_fixes}
    # agent_board 于 P2.3 随 BOARD.md 退役 —— board 已是现查投影，没有产物可重生。
    assert fixes["feature_map"].kind == "regenerate"
    assert all(f.from_gate in reg.names_in_group("scaffold") for f in reg.scaffold_fixes)
    chunkyctl = (REPO / "scripts" / "chunkyctl").read_text(encoding="utf-8")
    assert "scaffold-fix)" in chunkyctl

def test_tier12_structural_reason_is_fail_not_warn(monkeypatch) -> None:
    """2026-08-11 独立审查 finding #3：只有「当天没 accepted」才配叫预期回落。

    `config_hash_mismatch` 这类是 config 与已发布 payload 的结构性不一致，跑多少次
    日更都不会自愈 —— 与 b_pit 同级，判 FAIL。首版一律判 WARN，等于给一个永久坏掉的
    cutover 永远贴上「这是预期」的标签。
    """
    from types import SimpleNamespace

    def _fake(day, **kwargs):
        return SimpleNamespace(
            cutover_allowed=False,
            status="BLOCKED",
            source="legacy_scaffold",
            reasons=("config_hash_mismatch",),
        )

    monkeypatch.setattr(
        "services.tier12_consumer_cutover.resolve_tier12_consumer_cutover", _fake
    )
    finding = cce._tier12_finding("20260810")
    assert finding["status"] == cce.STATUS_FAIL
    assert finding["transient_reason"] is False
    assert "不会自愈" in finding["detail"]


def test_tier12_missing_accept_stays_warn(monkeypatch) -> None:
    """逐日回落仍必须是 WARN —— 否则天天 FAIL 就是 cry wolf。"""
    from types import SimpleNamespace

    def _fake(day, **kwargs):
        return SimpleNamespace(
            cutover_allowed=False,
            status="BLOCKED",
            source="legacy_scaffold",
            reasons=("missing_accept", "no_accepted_partition_for_day"),
        )

    monkeypatch.setattr(
        "services.tier12_consumer_cutover.resolve_tier12_consumer_cutover", _fake
    )
    finding = cce._tier12_finding("20260810")
    assert finding["status"] == cce.STATUS_WARN
    assert finding["transient_reason"] is True


def test_cutover_report_is_unverified_when_calendar_is_unreachable(monkeypatch) -> None:
    monkeypatch.setattr(cce, "_latest_trade_date", lambda: (None, "calendar_unreachable:X"))
    report = cce.evaluate()
    assert report["overall"] == "WARN"
    assert report["findings"][0]["status"] == cce.STATUS_UNVERIFIED


def test_cutover_check_is_registered_as_a_runtime_check() -> None:
    ids = {c.id for c in gg.load_registry().runtime_checks}
    assert "cutover_effective" in ids


# ── 5. 两条执法路径同源 (2026-08-11 P3 收口发现的 P1 漏洞) ──────────────
def test_git_hook_is_versioned_and_group_aware() -> None:
    """`git commit` 直调走 hook，走 safe_commit 走门 —— 后果必须由同一份分组决定。

    实测反例：P1 把 project_index_sync 降为 warn-only，而 hook 仍硬阻断，且 hook 当时
    只存在于各自机器的 `.git/hooks/`（不入版本、不被审查、新克隆根本没有这道防线）。
    """
    hook = REPO / "configs" / "git-hooks" / "pre-commit"
    assert hook.is_file(), "hook 必须入 git，否则新克隆没有兜底且无人能审"
    text = hook.read_text(encoding="utf-8")
    assert "gate_policy.py --names scaffold" in text, "hook 必须消费同一份分组策略"
    assert "WARN-ONLY" in text, "scaffold 组在 hook 路径也必须只 warn"
    # fail-closed: 取不到名单时不得放行
    assert "|| true" in text and 'case " $SCAFFOLD "' in text
